from __future__ import annotations

import codecs
import json
import re
from dataclasses import dataclass, field
from typing import AsyncIterable, AsyncIterator, Iterator, Mapping

#: 终态事件 —— 收到它就该收工。
TERMINAL_TYPES = frozenset({"response.completed", "response.incomplete", "response.failed"})

#: 已知的事件类型。**不在这儿的一律抛。**
#:
#: ⚠️ 带 *(实测)* 的那六个是 2026-09-26 从**跑着的** NInfer 引擎上实测出来的
#: （原始流字节级冻在 `tests/fixtures/ninfer_responses_stream.sse`，抓法见 HANDOFF「抓真流」）。
#: 它们**每一个**都曾让 `feed()` 抛 `ResponseProtocolError`、整条流当场断 ——
#: 真流是在**第 2 个事件** `response.in_progress` 上断的。
#: ⇒ **一个都不是"顺手收下"的**：每个都写明它为什么不改变累加。
KNOWN_TYPES = frozenset({
    "response.created",
    "response.output_item.added",
    "response.output_item.done",
    "response.output_text.delta",
    # (实测 9~11x) 思考增量的**真名** —— 它累进 `acc.reasoning`。
    "response.reasoning_text.delta",
    # (实测 1x) 带**完整**思考文本 ⇒ **不累加**（累加就双倍，判据 6 立刻对不上）。
    "response.reasoning_text.done",
    # (实测 1x) `text` 是**完整正文**（与增量拼出来的逐字相同）⇒ **不累加**，只标记完成。
    "response.output_text.done",
    # (实测 2x) 宣布"接下来往这个 part 写"；实测 `part["text"]` 是**空串** ⇒ 不改变累加。
    "response.content_part.added",
    # (实测 2x) 带**完整 part 文本** ⇒ **不累加**（同 `output_text.done`）。
    "response.content_part.done",
    # (实测 1x，紧跟 `created`) 只是"引擎开始干活了"的心跳；正文仍只由 delta 累加。
    "response.in_progress",
    # Observed on the subscription stream (2026-10-25): advisory metadata.
    # No text/tool/usage accumulation and never a terminal/success signal.
    # It still passes the same sequence and response-identity checks below.
    "response.metadata",
    "error",
    # ⚠️ 这里**曾经**写着 `response.reasoning.delta` —— 那个名字是**我们发明的，引擎实测从不发**
    # （真名是上面的 `reasoning_text.delta`）。2026-09-26 已删：
    # 留一个永不出现的名字会让读代码的人以为它会出现，而它**已经骗过一次**
    # （我们照文档写下它，真流在**第 2 个事件**上就断了）。
    # `tests/test_stream.py::test_the_invented_reasoning_delta_name_is_rejected` 钉着"删干净了"。
    #
    # 设计稿 07 §7.3 点名的不可逆三之一；本轮两次抓流都没触发（引擎没触发拒绝），保留。
    "response.refusal.delta",
    # 拒绝的收尾事件（与 `output_text.done` 同规矩：**带完整文本 ⇒ 不累加**）。
    # ⚠️ 它的**载荷形状没有实测过** —— 引擎从没拒绝过（两次抓流都是正常答复）。
    # 但它与 `refusal.delta` 是兄弟：不登记的话，**第一次真拒绝会以协议错死掉**，
    # 而那时已经不可逆、不会重试。⇒ 登记它、且**只登记不解析**（不累加 ⇒ 形状猜错也无害）。
    "response.refusal.done",
    # ⚠️ 下面两个**没有实测过** —— 引擎从没发过 `function_call`（工具还不存在）。
    # 它们是照上游文档写下的名字，**只为让流不在这里断掉**：
    # 不登记的话，第一次真调用会以协议错死掉（与 `refusal.done` 同一个理由）。
    # 累加那边的立场是"**只记我们看到的东西**"（见 `ToolCall`）。
    "response.function_call_arguments.delta",
    "response.function_call_arguments.done",
    "response.completed",
    "response.incomplete",
    "response.failed",
})

#: 只有这两种**事件**一出现就不可逆 —— 07 §7.3「不可逆只认三种」里的那两种 delta。
#:
#: ⚠️ `response.output_item.added` **不在**这里了（它曾经在）：
#: 实测真流的**第 2 个事件**就是它，而 item 是 **`reasoning`** ⇒
#: 那个标志会在**还没有任何用户可见文本**的时候就置位，
#: 「思考阶段可以重试」（07 §7.3）当场作废。item 的那一半改由下面 `VISIBLE_ITEM_TYPES` 判。
EXTERNALIZING_TYPES = frozenset({
    "response.output_text.delta",
    "response.refusal.delta",
})

#: `output_item.added` 里**算用户可见**的 item 类型 —— 07 §7.3 点名的 `function_call`。
#:
#: 其余类型在 added 那一刻**都还没有正文**：实测 `message` / `reasoning` 的 `content` 都是 `[]`
#: （正文靠 delta 来），而 reasoning **明确不算**（07 §7.3：思考阶段可以重试）。
#:
#: ⚠️ **认不出的类型（含缺失）也不置位** —— 判据是"有用户可见的东西出去了"，
#: 而 07 给的是**枚举**，不是"默认不可逆"。
#:
#: **反面是一行翻转**：把上面那行改成"只要 `raw_item` 存在就置位"（或干脆把
#: `output_item.added` 放回 `EXTERNALIZING_TYPES`）。**它的代价是量过的**：
#: 真流的**第 2 个事件**就是个 reasoning item ⇒ 翻过去 = **思考阶段永远不能重试**，
#: 每一条真流都会在"还没有任何用户可见文本"的时候变成不可逆。
#: 最终审选了"枚举"这一侧（Task 7 报告 A.4 / A.11）。
VISIBLE_ITEM_TYPES = frozenset({"function_call"})

#: ⚠️ **三个 `.done` 载体都不置位**（这一条只写在注释里，**故意不写成测试** ——
#: 一条测试会**冻结一个引擎从未被观测过的流形状**，而那正是这个任务家族付过三次代价的错误）：
#: `response.output_text.done` / `response.content_part.done` /
#: message 的 `response.output_item.done`
#: 都不进 `EXTERNALIZING_TYPES`，也不是 `VISIBLE_ITEM_TYPES`。
#:
#: ⇒ **一条只经由它们承载正文、然后中途断掉的流会被重试。** 代价是**量过的**（2026-09-26 探针）：
#: · `calls=2 attempts=2`，第二次正常收尾 ⇒ `text='好'`，**没有重复输出**
#:   （`generate()` 只在最后返回一次，半截的那次从不抵达调用方）；
#: · 若它**走完**到 `completed`（终态宣布了正文、而增量一个都没有）⇒
#:   **判据 6 当场抓住**：`ResponseProtocolError: terminal text disagrees with deltas: 1 vs 0 chars`，
#:   而那时 `externalized=False` ⇒ **不会**被 wrap 成 `IrreversibleError`（不用假装它不可逆）。
#:
#: ⇒ 结论：**这条腿是"看起来漏、实际有兜底"** —— 兜底是判据 6，不是不可逆判据。

#: 终态里 `status` 允许的取值。**三种都合法** ——
#: `failed` 是上游认输，不是协议违规（调用方看 `status` 决定怎么办）。
TERMINAL_STATUSES = frozenset({"completed", "incomplete", "failed"})


class ResponseProtocolError(RuntimeError):
    """上游的流违反协议。抛出去 —— 它不该被当成可重试的错误。"""


@dataclass(frozen=True)
class ToolCall:
    """一次工具调用 —— **从上游事件里抽出来的轻量视图**（与 `note.Record` 同一手法）。

    ⚠️ 它**不是**那个 item 的原样 dict：字段名跟着**本仓**的叫法走，
       免得上游改个名字就沿着下游一路漂。
    ⚠️ 三个字段**都有默认空串** —— 因为我们**没见过真形状**：
       "缺了的那一半算不算一个完整的调用"是 **S6** 的判断，不是这一层的。
    """

    call_id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class ResponseAccumulator:
    """把一条 SSE 流收成一段文本，同时**逐事件校验**。"""

    text: str = ""
    reasoning: str = ""
    #: 拒绝文字 —— ⚠️ **与 `text` 分开装**：那是"模型明确说不做"这个信号，
    #: 处置与正文完全不同（正文可以转述，拒绝要原样交给调用方）。
    #: 它是**用户可见**的，所以它出现时 `externalized` 就会置位（07 §7.3 的三条之一）。
    refusal: str = ""
    #: 收下来的工具调用 —— **public**，Task 2 与 S6 都读它。
    calls: list[ToolCall] = field(default_factory=list)
    response_id: str | None = None
    status: str | None = None
    _last_seq: int = -1
    _created_seen: bool = False
    _terminal: dict | None = None
    _done_items: set[str] = field(default_factory=set)
    _done_text_chars: int = 0  # diagnostic only; never substitutes terminal text
    #: 累加中的那一份：`item_id` → `{"call_id":…, "name":…, "arguments":…}`。
    #  ⚠️ 它**不 pop** —— 它要留到流结束（两个来源的核对需要同一个基准，见 `_merge_arguments`）。
    _pending: dict[str, dict] = field(default_factory=dict)
    #: 已经收进 `calls` 的那些 `item_id` —— 定案的**幂等**靠它
    #  （同一个 item 可能从 `output_item.done`、`function_call_arguments.done`、
    #   以及终态那一趟各来一次）。
    _settled: set[str] = field(default_factory=set)
    externalized: bool = False
    #: ⚠️ **那个 `function_call` item 真的出现在流里过吗？** —— item 的类型是**猜的**
    #: （照上游文档写下来、从没被观测过，见 `KNOWN_TYPES` 上方那段），
    #: 而**猜错的唯一可观测后果**就是这一位。
    #: 它把"**那个 item 从没被观测到**"与"**见了它、但它没名字**"分开：
    #: 后者那格 `ToolCall('', '', '{}')` 由 `test_missing_fields_do_not_raise` 钉着，
    #: 而**两者今天长得逐字节一样**（都是空名字、都不抛）。
    #: ⚠️ 两位都是**真的**：`output_item.added` 与 `output_item.done` 上匹配到都算。
    #: ⚠️ 它**只记观察、不做判断** —— `calls` 照样照收（不假设形状），
    #: 而"**没名字的调用算不算一次调用**"是 **S6** 的判断。**本层等 S6。**
    saw_function_call_item: bool = False

    @property
    def usage(self) -> dict | None:
        """ **终态那一份里的 `usage`**（2026-10-05）—— 引擎报了它，而**从前没有一处读**。

        ⚠️ **它在 `response` 里面**（**实测**，2026-10-05：给引擎发一条流式请求，
           把终态事件的结构打出来 —— 顶层键只有 `['response', 'sequence_number', 'type']`
           ⇒ **顶层没有** `usage` ）：
               `{"type":"response.completed","response":{"usage":{…}}}`
            我第一版读的是**顶层** ⇒ 判据当场红（"终态里的用量没被读出来" ）⇒ 按实测改 。
        ⚠️ 形状**照原样**交出去（`input_tokens` ／ `input_tokens_details.cached_tokens` …）：
           这一层**不解释**它 —— 解释是上面那几层与页面的事 。
        ⚠️ 没有 ⇒ `None`（**不编一个 0** —— 与"不许把不知道画成零"同一条口径 ）。
        """
        if not isinstance(self._terminal, Mapping):
            return None
        response = self._terminal.get("response")
        if not isinstance(response, Mapping):
            return None
        got = response.get("usage")
        return dict(got) if isinstance(got, Mapping) else None

    @property
    def error(self) -> dict | None:
        """Keep the upstream failure object without guessing its cause."""
        response = (self._terminal or {}).get("response")
        value = response.get("error") if isinstance(response, Mapping) else None
        return dict(value) if isinstance(value, Mapping) else None

    @property
    def incomplete_details(self) -> dict | None:
        """Keep truncation details separate from upstream failures."""
        response = (self._terminal or {}).get("response")
        value = response.get("incomplete_details") if isinstance(response, Mapping) else None
        return dict(value) if isinstance(value, Mapping) else None

    @property
    def terminated(self) -> bool:
        """终态事件**已经到过** —— 收到它就该收工，**别再往下读**。

        ⚠️ 它是给消费者的**早退信号**，不是"反正流会自己结束"的注释。
        流的契约在终态事件那一刻就结束了；再往下读，读到的只可能是
        **不产生事件的字节**（引擎 prefill 期发的 `: ping` 就是这种，**每 2.000 s 一个**，
        实测）—— 而每来一个字节都会**重置 httpx 的 read timeout**
        ⇒ 那种等待**不是**有界于 `frame_idle_ms` 的，是**没有上界**的。

        ⚠️ 诚实起见：**今天**引擎在终态帧之后**一个字节都不发**
        （2026-09-26 实测：全部 7548 字节在 t=0.156 s 前到齐，`iter_bytes()` 在
        "最后一个字节"之后花了 **0.000 s** 返回。⚠️ 那次量的是**同步 `iter_bytes()` 路径**；
        异步化之后这条链走 `aiter_bytes()`，**没有重测** —— 数还是那次同步实测的数）⇒
        早退现在**不省任何时间**。
        它的价值是**结构上的**：把"读完就停"从"取决于上游行为"变成**我们自己说了算**。

        ⚠️ 它是**只读的**：置位只在 `feed()` 里、且一个流只许置一次
        （重复终态是协议错）。
        """
        return self._terminal is not None

    def feed(self, event: dict) -> None:
        # ⚠️ `iter_sse_events` 会原样产出 `json.loads` 的结果 —— `data: 5` 就是个整数。
        # 那也要抛**协议错**，别漏成 AttributeError（调用方只捕前者）。
        if not isinstance(event, dict):
            raise ResponseProtocolError(f"event is not an object: {event!r}")
        _validate_event_shapes(event)
        t = event.get("type")
        if not isinstance(t, str) or t not in KNOWN_TYPES:
            label = (t if isinstance(t, str) and len(t) <= 80
                     and re.fullmatch(r"response(?:[.][a-z][a-z_]{0,31}){1,4}", t) else "unclassified")
            error = ResponseProtocolError("unknown event type: " + label)
            error.event_type = label
            raise error

        seq = event.get("sequence_number")
        if not isinstance(seq, int):
            raise ResponseProtocolError("sequence_number missing or not an int")
        if seq <= self._last_seq:
            prev = self._last_seq
            raise ResponseProtocolError(
                f"sequence_number not increasing: {seq} <= {prev}")
        self._last_seq = seq

        if t == "error":
            from .transport import stream_failure
            raise stream_failure(event.get("error") or event)

        # 终态事件**三种都认** —— 只认 `completed` 会把"被截断"误判成协议违规，
        # 而短额度路（被 `max_output_tokens` 截断）是**正常结果**。
        if t in TERMINAL_TYPES:
            if self._terminal is not None:
                raise ResponseProtocolError("duplicate terminal event")
            self._terminal = event

        if t == "response.created":
            if self._created_seen:
                raise ResponseProtocolError("duplicate response.created")
            self._created_seen = True

        # identity 一旦定下就不许变
        rid = (event.get("response") or {}).get("id")
        if rid:
            if self.response_id is None:
                self.response_id = rid
            elif rid != self.response_id:
                known = self.response_id
                raise ResponseProtocolError(
                    f"response identity changed: {known} -> {rid}")

        # item 的 id 允许两种写法：真协议是 `item={"id": ...}`，简化写法是裸 `item_id`。
        # ⚠️ `item` 可能不是字典 —— 那种畸形输入也要抛 ResponseProtocolError，
        # 别漏成 AttributeError（调用方按设计只捕前者）。
        raw_item = event.get("item")
        if raw_item is not None and not isinstance(raw_item, dict):
            raise ResponseProtocolError(f"item is not an object: {raw_item!r}")
        item_id = event.get("item_id") or (raw_item or {}).get("id")
        if t == "response.output_item.done" and item_id:
            if item_id not in self._done_items:
                content = (raw_item or {}).get("content")
                if isinstance(content, list):
                    self._done_text_chars += sum(len(seg["text"]) for seg in content
                        if isinstance(seg, dict) and seg.get("type") == "output_text"
                        and isinstance(seg.get("text"), str))
            self._done_items.add(item_id)
        elif t in ("response.output_text.delta",
                   "response.reasoning_text.delta",
                   "response.refusal.delta",
                   "response.function_call_arguments.delta") and item_id:
            # ⚠️ 四个 delta 一起守：**reasoning 与 refusal 是有意收进来的**
            # （见 Task 7 报告 M5）—— 一个 item 收工之后还来它的 delta，
            # 一律是**不可重试**的协议错（那时连接层已经不可信了）。
            # ⚠️ `function_call_arguments.delta` 是**第四个**，2026-09-30 补进来的：
            # 它是**一个类型名加进这条既有规矩** —— 晚到的那一条会落进**不 pop** 的
            # 累加位 ⇒ 随后一条合理的 `arguments.done` 会撞上**假的** `arguments disagree`。
            if item_id in self._done_items:
                raise ResponseProtocolError(f"delta after item.done: {item_id}")

        if t in EXTERNALIZING_TYPES:
            self.externalized = True
        elif (t == "response.output_item.added"
              and (raw_item or {}).get("type") in VISIBLE_ITEM_TYPES):
            # ⚠️ **有条件的**：只有 07 §7.3 点名的那种 item 算"用户可见"。
            # 无条件收 `output_item.added` 会在真流的**第 2 个事件**（reasoning item）上置位。
            self.externalized = True

        if t in ("response.output_text.delta", "response.reasoning_text.delta",
                 "response.refusal.delta"):
            delta = event.get("delta")
            # `null` 当空串（与判据 6 的 `text: null` 同一处理）；
            # 但 **falsy 非串**（`0` / `False` / `[]`）要抛 —— `or ""` 会把它们静默当空，
            # 那还是**静默丢数据**，与这一轮的契约方向相反。
            if delta is not None and not isinstance(delta, str):
                raise ResponseProtocolError(f"delta is not a string: {delta!r}")
            if t == "response.output_text.delta":
                self.text += delta or ""
            elif t == "response.refusal.delta":
                # ⚠️ **独立字段**，不并进 `text`：拒绝要能被调用方单独认出来。
                # （不累加它 = 一条已经"不可逆"的答复，内容却永远到不了调用方 ——
                #   那是 Task 7 I1 抓到的形状。）
                self.refusal += delta or ""
            else:
                self.reasoning += delta or ""

        # ⚠️ 这一段**在既有检查之后** —— 顺序那两条（`int`、递增）不许被绕过。
        if t in ("response.output_item.added", "response.output_item.done"):
            if (raw_item or {}).get("type") == "function_call":
                # 顺带记下**这一次匹配**（见字段的说明）：类型是猜的，
                #   而"猜没猜中"是这一位唯一能回答的事。
                self.saw_function_call_item = True
                self._note_a_call(item_id, raw_item,
                                  settled=t == "response.output_item.done")
        elif t == "response.function_call_arguments.delta":
            # 照抄本文件 `:256-260` 那条规矩，别用 `or ""`：
            #   `or ""` 把 `0` / `False` / `[]` **静默当空**，而 `+= 5` 这种会漏出
            #   一个 **`TypeError`** —— `generate.py:279-281` 只认
            #   `(TransportError, LLMError, ResponseProtocolError, RuntimeError)`
            #   ⇒ 漏出去的那个**跳过 `IrreversibleError` 的分类**（调用方只捕协议错）。
            delta = event.get("delta")
            if delta is not None and not isinstance(delta, str):
                raise ResponseProtocolError(f"delta is not a string: {delta!r}")
            self._note_arguments(item_id, delta or "", settled=False)
        elif t == "response.function_call_arguments.done":
            # 同上：`arguments` 也**只收字符串**（`None` 当"没给" —— 见 `_note_arguments`）。
            arguments = event.get("arguments")
            if arguments is not None and not isinstance(arguments, str):
                raise ResponseProtocolError(f"arguments is not a string: {arguments!r}")
            self._note_arguments(item_id, arguments, settled=True)

        # **终态那一趟**：还挂着的那些照收 —— 否则"只来了 delta、流就断了"那一条
        #   **永远进不了 `calls`**（而那条恰恰是我们唯一见过的形状的两个可能之一）。
        #   ⚠️ 它必须**放在最后** —— 且由 `_settled` 保证幂等。
        if t in TERMINAL_TYPES:
            for pending_id in list(self._pending):
                self._settle_call(pending_id)

    def _slot(self, item_id):
        """那一格累加位（**没有就建**，且**永不 pop**）。"""
        return self._pending.setdefault(item_id or "", {"call_id": "", "name": "",
                                                        "arguments": ""})

    def _note_a_call(self, item_id, item, *, settled):
        """item 那一半 —— `added` 与 `done` 都可能带，**先到先用**，两边都不硬取。

        ⚠️ **错类型的字段要抛，不许静默跳过** —— 与 `:256-260` / `:290-293` 那两条
           事件载荷的守卫是**同一条规矩**：那里写着"**falsy 非串**（`0` / `False` / `[]`）
           **要抛** —— `or ""` 会把它们静默当空，**那还是静默丢数据**"，
           而 `:290-293` 对 `arguments` 那句是"**同上**"（`null` 当"没给"）。
           写成 `isinstance(value, str) and value` ⇒ `call_id: 5` 被**当没看见**
           ⇒ 收下来的 `ToolCall(call_id='', name='')` 与"引擎真的没给"
           **分不出来**；而 `calls` 非空又会在 `generate.py` 那边把空答复判据豁免掉。
        ⚠️ **`None` 仍然合法** —— 它就是"没给"（缺字段与显式 `null` 都走 `item.get`
           返回 `None`），与那两条事件守卫**逐字一致**（`null` 算空）。
        ⚠️ 守卫在 `_slot` **之前** —— 抛的时候那一格**没被建**，状态没被改。
        """
        for key in ("call_id", "name", "arguments"):
            raw = item.get(key)
            if raw is not None and not isinstance(raw, str):
                raise ResponseProtocolError(f"{key} is not a string: {raw!r}")
        slot = self._slot(item_id)
        for key in ("call_id", "name"):
            value = item.get(key)
            if isinstance(value, str) and value and not slot[key]:
                slot[key] = value
        # ⚠️ item 上带的 `arguments` 也要看（`output_item.done` 上**可能**有它）
        provided = item.get("arguments")
        if isinstance(provided, str) and provided:
            self._merge_arguments(item_id, provided)
        if settled:
            self._settle_call(item_id)

    def _note_arguments(self, item_id, arguments, *, settled):
        """`function_call_arguments.*` 那一半。`delta` 往里加；`done` 与累加值**若都有才比**。"""
        slot = self._slot(item_id)
        if settled:
            if isinstance(arguments, str) and arguments:
                self._merge_arguments(item_id, arguments)
            self._settle_call(item_id)
        else:
            slot["arguments"] += arguments

    def _merge_arguments(self, item_id, provided):
        """ spec §五 ③：**若**两个来源都给了就核对 —— **不**是"某处必须有"。

        ⚠️ **基准是那一格累加位，而它不 pop** —— 所以第二个来源来的时候
           核对**仍然比得到**（这正是本条存在的理由）。
        """
        slot = self._slot(item_id)
        if slot["arguments"] and slot["arguments"] != provided:
            raise ResponseProtocolError(
                f"function arguments disagree: {slot['arguments']!r} != {provided!r}")
        if not slot["arguments"]:
            slot["arguments"] = provided

    def _settle_call(self, item_id):
        """定案：把那一格收进 `calls`。 **幂等**（同一个 item 只收一次），**不 pop**。

        ⚠️ **不 pop** 有两个理由：① 核对要用同一个基准（上面那条）；
           ② 终态那一趟还会再叫一次 —— `_settled` 挡住重复。
        ⚠️ **缺字段就留空串** —— **绝不拿 `item_id` 去填 `call_id`**：
           那是个**形状假设**（假定 item 的 id 就是 call 的 id），而整份 spec
           的立场恰恰是"**不假设**"。"补不上怎么办"是 **S6** 的判断。
        """
        key = item_id or ""
        if key in self._settled:
            return
        slot = self._pending.get(key)
        if slot is None:
            return
        self._settled.add(key)
        self.calls.append(ToolCall(**slot))

    def finish(self) -> "ResponseAccumulator":
        if self._terminal is None:
            raise ResponseProtocolError("stream ended with no terminal event")

        resp = self._terminal.get("response") or {}
        status = resp.get("status")
        if status not in TERMINAL_STATUSES:
            raise ResponseProtocolError(f"terminal status not terminal: {status!r}")

        # ⚠️ 判据 6/8 **只在 `completed` 时判**。
        # 被截断（`incomplete`）或上游认输（`failed`）时，终态对不上是**正常**的 ——
        # 少一个 `item.done`、正文只流了一半，都是截断应有的形态。
        # 对它们施加对账 = 把"正常结果"误判成"不可重试的协议违规"（Ruling E 要消灭的那个）。
        if status != "completed":
            self.status = status
            return self

        # ⚠️ 终态 `output` 里的非字典项**不许被静默过滤掉** ——
        # 静默丢数据是最难查的一类（同 Ruling I 的理由）。
        raw_output = resp.get("output") or []
        for it in raw_output:
            if not isinstance(it, dict):
                raise ResponseProtocolError(f"terminal output item is not an object: {it!r}")
        announced = list(raw_output)

        # 判据 8：终态宣布的每个 item，都必须 **done 过**。
        for it in announced:
            iid = it.get("id")
            if iid and iid not in self._done_items:
                raise ResponseProtocolError(f"terminal announces item never done: {iid}")

        # 判据 6：终态宣布的正文，必须和增量拼出来的**一致**。
        # ⚠️ 它是**纯对账**，**不是非空断言** —— 两边都空时它**放行**。
        # 「诚实地空」（思考吃掉全部额度）由 T5 自己判，见 07 那一节。
        final_text = _output_text(announced)
        if final_text != self.text:
            raise ResponseProtocolError(
                f"terminal text disagrees with deltas: "
                f"{len(final_text)} vs {len(self.text)} chars; "
                f"output={_output_shape(resp)}; done_chars={self._done_text_chars}")

        # 状态**最后**才写 —— 上面任何一条抛了，调用方 catch 后读到的仍是 None，
        # 不会留下"抛了却带着半截状态"的污染。
        self.status = status
        return self


def _output_shape(response: Mapping) -> str:
    """Bounded structural diagnostics only: never emit text, IDs or metadata."""
    if "output" not in response:
        return "missing"
    output = response["output"]
    if output is None:
        return "null"
    if not isinstance(output, list):
        return "non-list"
    if not output:
        return "empty-list"
    # Recognized type labels only; upstream strings may contain private content.
    labels = []
    for item in output[:3]:
        kind = item.get("type") if isinstance(item, dict) else None
        if kind == "message":
            content = item.get("content")
            if not isinstance(content, list):
                labels.append("message/no-content-list")
            else:
                types = {seg.get("type") for seg in content if isinstance(seg, dict)
                         and isinstance(seg.get("type"), str)}
                labels.append("message/" + ("output_text" if "output_text" in types
                              else "refusal" if "refusal" in types else "other-content"))
        else:
            labels.append(kind if kind in ("reasoning", "function_call") else "other-item")
    return ",".join(labels)


def _validate_event_shapes(event):
    def object_or_none(value, label):
        if value is not None and not isinstance(value, dict):
            raise ResponseProtocolError(label + " must be an object")
    def string_or_none(value, label):
        if value is not None and not isinstance(value, str):
            raise ResponseProtocolError(label + " must be a string")
    response = event.get("response")
    object_or_none(response, "response")
    if response is not None:
        string_or_none(response.get("id"), "response id")
        string_or_none(response.get("status"), "response status")
        for key in ("usage", "error", "incomplete_details"):
            object_or_none(response.get(key), "response " + key)
        output = response.get("output")
        if output is not None:
            if not isinstance(output, list):
                raise ResponseProtocolError("output must be a list")
            for item in output:
                if not isinstance(item, dict):
                    raise ResponseProtocolError("output item must be an object")
                string_or_none(item.get("type"), "output item type")
                string_or_none(item.get("id"), "output item id")
                content = item.get("content")
                if content is not None:
                    if not isinstance(content, list):
                        raise ResponseProtocolError("content must be a list")
                    for part in content:
                        if not isinstance(part, dict):
                            raise ResponseProtocolError("content part must be an object")
                        string_or_none(part.get("type"), "content type")
                        string_or_none(part.get("text"), "content text")
    item = event.get("item")
    object_or_none(item, "item")
    if item is not None:
        string_or_none(item.get("type"), "item type")
        string_or_none(item.get("id"), "item id")
    string_or_none(event.get("item_id"), "item id")


def _output_text(items: list) -> str:
    """从终态的 `output` 里把 `output_text` 段拼起来，用来和增量对账。

    ⚠️ 上游可能给 `"text": null` —— 当空串算，别让它炸成 `TypeError`（那会滑出协议错误契约）。
    """
    parts: list[str] = []
    for item in items:
        for seg in ((item or {}).get("content") or []):
            if isinstance(seg, dict) and seg.get("type") == "output_text":
                parts.append(seg.get("text") or "")
    return "".join(parts)


async def iter_sse_events(chunks: AsyncIterable[bytes]) -> AsyncIterator[dict]:
    """把字节块切成 SSE 事件。

    ⚠️ **四条都要守住**：
    · **帧会跨块** —— 必须按 `\\n\\n` 缓冲，不能按块边界切；
    · **字符也会跨块** —— **不许每块各自 `decode()`**：一个汉字是 3 字节，
      块边界落在中间时，分块解码会把**一个字符**变成两三个 `\\ufffd`。
      （实测：7400 B 的真流按 97 B 切，**6 处**边界落在汉字中间、多出 **9 个**替换符，
      正文被啃成 `'���好！'` —— 判据 6 随后把它判成"终态对不上"（3 vs 5 字符），
      离现场已经很远。）⇒ 用**增量解码器**：不完整的序列留在里面，等下一块。
    · **行尾可能是 CRLF** —— 先规范化成 `\\n`，否则**一个事件都读不出来**（静默返回空）；
    · **末尾可能没有空行收尾** —— 循环退出后要 **flush 残留**。不 flush 的话，
      最后一帧（**很可能正好是终态事件**）会被静默丢掉，下游只看到"没有终态事件"。

    ⚠️ 它**本身是异步的**（上游给的是 `stream_chat` 的异步字节流），但**里面全是纯计算**：
    唯一的 `await` 是那个 `async for` 取块。⇒ 它不碰 I/O，也就没有"顺手把文件 I/O 也异步化"
    那一层（`log.py`／`events.py` 保持同步，不在这里）。
    """
    buf = ""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    async for raw in chunks:
        text = decoder.decode(raw)          # 残缺序列留在解码器里，不在这里变替换符
        buf += text.replace("\r\n", "\n").replace("\r", "\n")
        while "\n\n" in buf:
            frame, buf = buf.split("\n\n", 1)
            for event in _frame_events(frame):
                yield event

    # ⚠️ 收尾要把解码器里最后那点也**问出来**（`final=True`）——
    # 不然一个"流末尾正好停在半个字符上"的响应会**静默少几个字符**。
    tail = decoder.decode(b"", final=True)
    buf += tail.replace("\r\n", "\n").replace("\r", "\n")
    if buf.strip():
        for event in _frame_events(buf):
            yield event


def _frame_events(frame: str) -> Iterator[dict]:
    """⚠️ **它是纯的，保持同步** —— 只在异步函数里被迭代，一行都不用改。"""
    for line in frame.split("\n"):
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            yield {"__done__": True}
        elif payload:
            # ⚠️ 坏 JSON 要抛**协议错**，不是 `JSONDecodeError` —— 调用方只捕前者。
            try:
                obj = json.loads(payload)
            except ValueError as exc:
                raise ResponseProtocolError(f"malformed SSE payload: {exc}") from exc
            yield obj
