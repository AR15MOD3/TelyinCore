# telyincore/round.py
"""一轮 —— `loop.py` 注入的那个执行层。

⚠️ 这一层只做五件事：**组请求 ／ 发出去 ／ 把回执重建成记录 ／ 把那一批调用交出去 ／
   判读五位**（每一件在这份文件里都有它自己那一节）。
   它**不解释**模型说的话（那是 `stream.py` 与 S6 的事），也**不判**这一批还要不要跑
   （那是 `loop.py` 的事）。

⚠️⚠️ **两条线都是"从外面递进来"**（本刀）：
   ① `run_tools` —— 形如 `async def run_tools(calls) -> DispatchResult`。递进来了就
      **真的把那一批调用交给它**（不再只收成一位 `called_tool` 就丢掉）；没递 ⇒ 与
      接线之前**逐字相同**，而那一笔欠在 `_OWED["run_tools_source"]` 上（点名了谁欠）。
   ② `signal` —— 与 `Transport.stream_chat(signal=…)` ／ `generate(signal=…)` 是
      **同一条**（检查点早就埋在 `generate` 与传输层里了）。没递 ⇒ 那一个形参
      **连加都不加**（两路调用形状不同，理由写在 `run_round` 那一处）。
       而递进来了、信号已置 ⇒ `generate` 在**发出请求之前**掷 `RoundCancelled`，
      这一层把它翻成 **"这一轮没有发生"**（五位全假 —— **不是失败**；它是什么意思、
      为什么不能并进那个通用的失败支路，写在 `run_round` 的 `except RoundCancelled` 那一节）。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping, Sequence

from .generate import RoundCancelled, EmptyResponseError, UnsettledToolHistory
from .loop import RoundOutcome
from .note import Record
from .transport import LLMError
from .chatgpt_subscription import SubscriptionError, public_subscription_error
from .protocol import ReasoningReplayMissing
from .provider_record_replay import ReplayBindingConflict
from .wake_policy import WakeModelBudgetExhausted, WakeModelAdmissionDenied


def _the_same_as_last_round(session: Any, calls: Sequence[Any]) -> bool:
    """ **这一届那几个调用，与"上一届"的是不是一模一样**（工具名 ＋ 参数 ）。

     2026-10-09 作者：「经历页是不是没做好？」⇒ 追下去量到的： 同一个工具（ 参数
      还是**空的** `{}` ）**连着被调了 12 次** —— 而 12 正是 `loop.py::hard_rounds`
      ⇒ 它**撞了上限才停** ， 中间**没有一轮**发现"你在原地打转"。
     于是这一条： 打转**就算一次失败** ⇒ `loop.py` 里那道现成的连败闸
      （`max_consecutive_failures = 2` ）自己会把它收掉 —— 它的循环一个字不用改 。

    ⚠️ **"上一届"是会话尾部去掉"本届"之后那一段** —— 这一点是这一句的全部难点：
       `_take_the_reply` 在 **本函数被叫到之前** 已经把**本届**那几条 `function_call`
      写进会话了 ⇒ 直接拿尾巴比会**永远相等** （ 那就成了一个"每轮都失败"的
      死锁 —— 比不修还糟 ）。
    ⚠️ **只比 `(名字, 参数)`** ， 不比正文 ／ 不比时间 —— 那两样每届都不一样 ，
       拿它们比就永远不相等 。
    """
    # **2026-10-10 修：不计次序** —— 作者：「丙」。
    # ⚠ **为什么非改不可** ： 原来比的是
    # **按次序的 `(名字, 参数)` 列** （ `tuple(… for one in calls)` ）
    # ⇒ 而实测到的打转是**两个工具交替**
    # （ `read_events '{}'` ⁄ `memory_search {…}` ）——
    # 每一届都是**同两个** ， 而**顺序换了**
    # ⇒ 按次序比**永远不相等** ⇒ 它从那一条判据底下**钻过去了** 。
    # 改成**不计次序**： 两届的调用集合**一样** ⇒ 打转
    # （ 同一把钥匙 ）。 而"调了一个新东西"、
    # "**参数推进了**" 两种都不会被误杀 。
    got = tuple(sorted((str(getattr(one, "name", "")), str(getattr(one, "arguments", "")))
                       for one in calls))
    if not got:
        return False # 这一届没调工具 ⇒ 谈不上打转
    # A fresh user message starts a new request; repeating a read in that request
    # is not a loop merely because the previous request used identical arguments.
    rows = list(session.records())
    boundary = next((i + 1 for i in range(len(rows) - 1, -1, -1)
                     if str(getattr(rows[i], "kind", "")) == "message"
                     and str(getattr(rows[i], "role", "")) == "user"), 0)
    seen = [(str(getattr(one, "name", "")), str(getattr(one, "text", "") or ""))
            for one in rows[boundary:]
            if str(getattr(one, "kind", "")) == "function_call"]
    if len(seen) < len(got) * 2:
        return False # 连"上一届"都凑不齐 ⇒ 不是打转
    # **同一把钥匙** ： 两边都要**不计次序**
    # （ 只给一边排序 ⇒ 它们仍然不相等 ）。
    was = tuple(sorted(seen[-len(got) * 2:-len(got)]))
    return was == got

#: **本模块欠着的那两笔**（名字 → 欠主）——成法照 `timers._OWED` ／ `app._OWED`：
#: 键是**稳定的机器名**（改措辞不会红），值里必须**点名**（空话会红），而**还清就删条目**
#: ——一条已经兑现的债留在表里就是一句假话。
#: ⚠️ 两笔听上去像同一件事（都是"装配那一层递东西进来"），然而**代价不同**：
#: 一笔欠的是"调用别丢掉"，另一笔欠的是"关机真的能中断一轮"。所以分开记。
_OWED: dict[str, str] = {
    "run_tools_source":
        "★ **已经接上了**（装配那一层就是 `console.assemble`：它把 `run_tools=` 递进 "
        "`make_run_round(...)`，见那一处调用点）。 原先写的是：**谁把「跑工具」那一个"
        "可调用对象递给 `make_run_round`** —— 那时那个调用者**不存在**（`make_run_round` "
        "只有测试在调）。⚠️ **仍然为真的一半**：它与 `loop.run_tools` 那个槽**必须是同一个"
        "对象**（配对账 `Dispatcher.pending` 与回执账 `Dispatcher.receipts` 各挂在一部"
        "派发器上，各造一份就是**两本账分家**），而 `loop.py` 那一份**从来不被调用** ⇒ "
        "在生产里分家看不出来（ 而**现在有人判**：`tests/test_app.py::"
        "test_the_rounds_runner_is_the_one_bound_to_apps_dispatcher`）。"
        " **而它从来不是一个「死结」** —— 那个环**晚绑一步就解开了**（先造派发器、再造 "
        "`run_round`、最后把两者一起递给 `build()`）—— 装配那一层就是这么做的。"
        " **真正的障碍照旧是另一件**：`loop.py` 那个**调用点已经不在了** —— 调用的材料"
        "（`Result.calls`）在接线那一刀**之前**就被折成这一层的**一位** `called_tool` ⇒ "
        "经 `loop.run_tools` 那个槽进去是**形状不对**，与构造次序无关"
        "（所以**别去找一个顺序上的修法**）。",
    "signal_source":
        "★ **已经接上了**（装配那一层：`console.assemble` 把 `self.signal` —— **同一个"
        "对象** —— 同时递给 `make_run_round(signal=…)` 与 `build(signal=…)`，见那两处；"
        "而 `App.stop()` 置的就是 `App.signal` 那一个）。 原先写的是：**谁发起一轮、谁置"
        "那个信号**。当前 Console 的停止和最终收尾会调用 `App.stop()`；"
        "生产取消必须保留同一 signal、原批身份和未完成责任。而「必须同一个对象」"
        "这一条今天**有人判**：`tests/test_app.py::"
        "test_the_signal_the_shutdown_side_sets_is_the_one_the_round_reads`。",
}


def _receipt_reference(receipt, calls, references):
    key = getattr(receipt, 'call_id', '')
    matches = [call for call in calls if call.call_id == key] if key else [
        call for call in calls if call.name == getattr(receipt, 'name', '')]
    if len(matches) != 1 or matches[0].name != getattr(receipt, 'name', ''):
        raise ValueError('receipt has no unique durable association')
    return references[id(matches[0])]


def _retain_settled_prefix(session, calls, harvest, ts, tell, run_name, primary, references=None):
    """Harvest only definitive receipts for this exact failed invocation.

    The producer retains its ledger; this is not an ack, retry or repair of
    unknown outcomes. A secondary collection/UI failure never masks cancellation
    or the original batch error. Pairing IDs remain outside session records.
    """
    if harvest is None:
        return set()
    try:
        receipts = tuple(harvest(calls))
        expected = {call.call_id: call.name for call in calls}
        if len(expected) != len(calls) or any(not key for key in expected):
            raise ValueError('unique receipt identities required')
        seen = set()
        positions = {call.call_id: index for index, call in enumerate(calls)}
        last_position = -1
        for receipt in receipts:
            if (receipt.call_id not in expected or receipt.call_id in seen
                    or receipt.name != expected[receipt.call_id]):
                raise ValueError('foreign or duplicate settled receipt')
            if positions[receipt.call_id] <= last_position:
                raise ValueError('settled receipt order changed')
            last_position = positions[receipt.call_id]
            seen.add(receipt.call_id)
        for receipt in receipts:
            got = Record(kind='function_result', role='assistant',
                         name=receipt.name, text=receipt.text, ts_ms=ts,
                         tool_ref=_receipt_reference(receipt, calls, references) if references is not None else '')
            session.append(got)
            if callable(tell):
                try:
                    tell(dict(kind='function_result', name=got.name,
                              text=got.text, run=run_name, tool_ref=got.tool_ref))
                except BaseException as error:
                    primary._receipt_notice_error = error
                    primary.add_note('settled receipt retained; notice failed')
        return seen
    except BaseException as error:
        primary._receipt_harvest_error = error
        primary.add_note('settled receipt collection incomplete; dispatcher retains responsibility')
        return None


def _retain_interruption(session, calls, settled, references, ts, tell, run_name, primary):
    """Close only this interrupted wire exchange, never business ownership.

    The host knows cancellation happened, not whether an unreceipted operation
    ran or committed. These records are not dispatcher receipts: no pending
    item is acknowledged, no external outcome is inferred, and no tool runs.
    Failed/ambiguous receipt harvest remains fail-closed instead of being hidden.
    """
    if settled is None:
        return
    try:
        for call in calls:
            if call.call_id in settled:
                continue
            got=Record(kind='function_result',role='assistant',name=call.name,
                tool_ref=references[id(call)],ts_ms=ts,
                text='〔宿主中断记录；结果未知〕本轮工具派发已被宿主中断；该调用是否执行、'
                     '是否产生副作用及是否完成均未确认。这不是工具成功或失败回执，不撤销既有副作用；'
                     '不可自动重放。请先核对原始记录与真实工具回执，再决定下一步。')
            session.append(got)
            if callable(tell):
                try:
                    tell(dict(kind='function_result',name=got.name,text=got.text,
                              run=run_name,tool_ref=got.tool_ref))
                except BaseException as error:
                    primary._receipt_notice_error=error
                    primary.add_note('host interruption retained; notice failed')
    except BaseException as error:
        primary._interruption_record_error=error
        primary.add_note('host interruption record incomplete; history remains unconfirmed')
        import sys
        print('[round] interruption record incomplete: '+type(error).__name__,file=sys.stderr,flush=True)


def make_run_round(
    *,
    session: Any,
    generate: Callable[..., Awaitable[Any]],
    config: Any,
    transport: Any,
    head: Callable[[], Sequence[Mapping]],
    hard_tokens: Callable[[], int | None],
    estimate: Callable[[Sequence[Mapping]], int],
    record_event: Callable[[Mapping], Any],
    now_ms: Callable[[], int],
    now_iso: Callable[[], str],
    run_tools: Callable[[Sequence[Any]], Awaitable[Any]] | None = None,
    settled_receipts: Callable[[Sequence[Any]], Sequence[Any]] | None = None,
    signal: Any = None,
    #: **2026-10-10 卡片第三刀（乙）**： 把**工具那两条也推出去**
    #: （ 作者：「想看看这批里调用了什么工具」）。 不递 ⇒ 与从前逐字相同 。
    tell: Callable[[Any], Any] | None = None,
    #: **每轮把"终态那份用量"交出去**（2026-10-05）—— 与上面那两格同一条成法：
    #: **不递时这一层的行为逐字相同** （`loop.py` 的 `RoundOutcome` 一个字都不动 ）。
    #: ⚠️ 它只**交出去**：存哪、算不算，是调用方的事（本层不认识"缓存"那件事 ）。
    on_usage: Callable[[Mapping[str, Any] | None], None] | None = None,
    #: **每轮把"那三段有多久"交出去**（2026-10-05）—— 与上面那两格同一条成法：
    #: **不递时这一层的行为逐字相同** 。
    on_timing: Callable[[Mapping[str, Any] | None], None] | None = None,
    on_reply: Callable[[Any], Any] | None = None,
    before_reply: Callable[[Any, Any], Any] | None = None,
    allow_empty_internal: Callable[[], bool] | None = None,
    host_interrupted: Callable[[], bool] | None = None,
) -> Callable[[int], Awaitable[RoundOutcome]]:
    """把这一轮要用的一切递进来，交回 `loop.py:43` 要的那个可调用对象。

    ⚠️ **`ended` 由谁说了算**（本刀把这一句改准了）—— 它是"模型调了 `finish_turn`
       这个工具"，而**只有那张工具表算得出来它**（表在派发器手里）⇒ 递进来的
       `run_tools` 交回的那一份 `DispatchResult.ended` 是它**唯一**的来源。
       **没递 `run_tools`** 时它恒假 —— 而那也不是空实现：**今天请求里连 `tools`
       数组都没有**（`generate.build_body` 不发它）⇒ 模型不可能"调工具收工"；
       ⇒ "没人把那一批交出去"的那些构造里，恒假是**当前正确的行为**。

    ⚠️ **两个可选面（`run_tools` ／ `signal`）都默认 `None`**，而那**不是**"用不到"，
       而是"**还没人递**"—— 两笔欠各自点名在 `_OWED` 上（谁欠它、代价是什么）。
       不递时这一层的行为与接线之前**逐字相同**：`calls` 只收成一位，而取消那条路
       **连那个形参都不加**（两处的理由见 `run_round` 里对应的注释）。

    ⚠️ **`signal` 的形状**：注解是 `Any`（与 `generate` ／ `transport` 那两处**同一句**）
       —— 这一层只要求"有个 `is_set()`"，不绑定具体类型（模块之间靠**鸭子类型**耦合）。

     **"那一批的结果交回之后归谁"（本刀的裁定，落点就在这一句里）** —— 这一层
       **不把它塞进 `RoundOutcome`**：那五位是 `loop.py` 的契约（**逐字节禁改**的那一份，
       `RoundOutcome` 就是它定义里的一个 frozen dataclass），要带上那一份就得加第六位
       ⇒ 那要动 `loop.py`，不成立。而 `loop.py` **也不需要**它：它只判"这一批还要不要
       往下跑"（八条收尾读的就是那五位），回执是**下一轮要带给模型的东西**，另一条路。
       ⇒ 那一份的落点是**已经定好的**：`Dispatcher.receipts`（`run` 在**交回之前**就记上了
          —— 一处机器看得见的账），而"把它写进会话、带给模型"归**写会话那一层**
          （`dispatch._RECEIPT_OWED_BY` 点了名）。⚠️ 所以这一层只**多送一位上去**：
          `ended` —— 那张工具表**唯一**算得出、而 `loop.py` 真的要用的一位（③）。

     **两个时钟，不是冗余** —— 两个消费者、两套约定、两套形状：
       · `now_ms` ⇒ **毫秒整数**，进会话（`note.py:33` 的 `Record.ts_ms`）；
       · `now_iso` ⇒ **本地 ISO-8601 字符串**，进事件库（`events.py:220-226` 的 `_now_iso`）。
       ⚠️ 硬把一种形状掰成另一种就是这个 bug：`str(now_ms())` 写出 `"7"`，而
          `events.py:603-614` 的 `from_ts` / `to_ts` 是**字符串比较** ⇒ `"7" < "20…"`
          ⇒ 那条事件**对一切时间窗查询静默不可见** —— 正是这一支最防的"写进去读不出来"。
       接线那一层把它接到**事件库自己那个 `clock`** 上（同一个来源）。
    """

    def report_failure(error, round_no):
        # Persist code locations, never request bodies, credentials or frame locals.
        import traceback
        from pathlib import Path
        code = "provider_record_projection_length_mismatch" if (
            type(error) is ValueError and str(error) == "provider record projection length mismatch"
        ) else type(error).__name__
        if isinstance(error, (ReplayBindingConflict, WakeModelAdmissionDenied)):
            code = error.public_code
        from .transport import http_failure_diagnostic
        from .stream import ResponseProtocolError
        from .transport import SUBSCRIPTION_LIMIT, SUBSCRIPTION_LIMIT_HINT
        http_error = http_failure_diagnostic(error)
        if http_error.get("upstream_code") == SUBSCRIPTION_LIMIT:
            code = SUBSCRIPTION_LIMIT
            print("[round] " + SUBSCRIPTION_LIMIT_HINT, flush=True)
        def event_label(exc):
            if isinstance(exc, ResponseProtocolError) and hasattr(exc, "event_type"):
                return {"event_type": exc.event_type}
            return {}
        frames = [{"file": Path(frame.filename).name, "line": frame.lineno,
                   "function": frame.name} for frame in traceback.extract_tb(error.__traceback__)[-24:]]
        # Wrapped stream failures need their cause locations, without raw messages
        # (which may contain credentials or provider response content).
        causes, seen = [], {id(error)}
        cause = error.__cause__ or error.__context__
        while cause is not None and id(cause) not in seen and len(causes) < 5:
            seen.add(id(cause))
            causes.append({"exception": type(cause).__name__, **event_label(cause), "frames": [
                {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(cause.__traceback__)[-12:]]})
            cause = cause.__cause__ or cause.__context__
        try:
            record_event({"type": "round-failure", "ts": now_iso(), "source": "host",
                          "origin": "internal", "text": code,
                          "meta": {"diagnostic": {"exception": type(error).__name__,
                                   "code": code, "round": round_no, "frames": frames, **event_label(error),
                                   **({"causes": causes} if causes else {}),
                                   **({"upstream": http_error} if http_error else {})}}})
        except Exception:
            pass  # Diagnostic recording must not replace the original failure.

    async def run_round(round_no: int) -> RoundOutcome:
        # 2026-10-11（尾巴缝）：**每轮都会变的那几句放在历史之后**（见 session.outbound）。
        # 它由装配那一侧挂在会话上（`session.the_tail`）；没挂 ⇒ 与从前逐字一样。
        _tail = getattr(session, "the_tail", None)
        # ⚠️ **没有尾巴就走老路**：那些替身会话只有 `outbound(head)` 一个形参，
        # 一律按两参调用会把它们全打红（2026-10-11 实测：29 条红，当场抓到）。
        if _tail is None:
            messages = session.outbound(head())
        else:
            messages = session.outbound(head(), _tail() if callable(_tail) else _tail)
        try:
            try:
                if signal is None:
                    # ⚠️ 不递 `signal` 时**连那个形参都不加**：那个注入面是老形状（只收三个）
                    #    —— 而"取消没被用到"就不该让发请求那一句话变形（两路**同义**：
                    #    真 `generate` 那一侧第四个形参的默认值就是 `None`）。
                    result = await generate(config, messages, transport)
                else:
                    result = await generate(config, messages, transport, signal=signal)
            except Exception as error:
                from .console_canary import CanaryStopped
                reason = (error.reason if isinstance(error, CanaryStopped)
                          else getattr(error, 'canary_terminal_reason', None))
                if type(reason) is not dict:
                    raise
                import sys
                print('[canary] terminal failure: ' + str(reason.get('kind')),
                      file=sys.stderr, flush=True)
                return RoundOutcome(failed=True, terminal_failure=True)
            # 交出去（**在这一轮别的地方动它之前** —— 那是这一轮真正的读数 ）
            if on_usage is not None:
                on_usage(getattr(result, "usage", None))
            if on_timing is not None:
                on_timing(getattr(result, "timing", None))
        except RoundCancelled as exc:
            acknowledge=getattr(generate,'acknowledge_failure',None)
            if callable(acknowledge):acknowledge(exc)
            # **一次取消不是一次失败**（本刀的裁定；钉子在 `tests/test_round.py` 那两条）：
            #   `RoundCancelled` **只在"发出这一次请求之前"**被掷出（`generate.py` 那两处
            #   检查）⇒ 这一轮**没有发生**：回执没有、记录没有、日志没有
            #   ⇒ 交回**五位全假**（`loop.py` ① 那一支读到"没调工具" ⇒ 这一轮完了）。
            #   ⚠️ **不许把它并进下面那两条 `except`**：它是 `RuntimeError` 的子类
            #      ⇒ 下面那条裸 `except Exception` 会**顺路接住它**、记成一轮失败。
            #      而 `loop.py` ⑦ 那一支**只读 `failed`** ⇒ 一次**关机**会在层 B 记一次连败、
            #      够数还为它付一次退避（`backoff_ms` 最长那一档）—— 而且从此"上游坏了"
            #      与"我们自己在关机"在账上分不出来。它必须是一个**类型**才分辨得了
            #      （`generate.RoundCancelled` 那段写着它存在的全部理由）。
            #   ⚠️ **它也不是"办完了一半"**：那两处掷出点都在请求之前。而**落在流中间**
            #      的那一次取消**不以它回来** —— `transport` 在 chunk 边界提前收工、
            #      没有终态事件 ⇒ 接着抛的是 `ResponseProtocolError`（已经外化过内容则是
            #      `IrreversibleError`）。⇒ 谁在关机之后去看那一轮的结局，都得**自己读
            #      `signal.is_set()`**：类型在这儿讲不出"取消"这件事（`App.stop()` 那段写了
            #      同一条，而它正是本层这句话的对面那一半）。
            return RoundOutcome()
        except (UnsettledToolHistory, ReasoningReplayMissing) as exc:
            report_failure(exc, round_no)
            # The history remains unresolved. Do not replay tools or ask the
            # model again; dispose only this rejected request's exact resources.
            acknowledge=getattr(generate,'acknowledge_failure',None)
            if callable(acknowledge):acknowledge(exc)
            import sys as _s
            print("   [round] 请求历史不完整 ⇒ " + type(exc).__name__
                  + "；未发送，结束本批", file=_s.stderr, flush=True)
            return RoundOutcome(failed=True,terminal_failure=True)
        except WakeModelAdmissionDenied as exc:
            report_failure(exc, round_no)
            print("   [wake] 唤醒准入未通过 · " + exc.reason + "；结束本批，不自动重试。", flush=True)
            return RoundOutcome(failed=True, terminal_failure=True)
        except WakeModelBudgetExhausted:
            print("   [wake] 本次唤醒已达到模型请求上限，结束处理。", flush=True)
            return RoundOutcome(ended=True)
        except EmptyResponseError as exc:
            if exc.status == "completed" and allow_empty_internal is not None and allow_empty_internal() is True:
                record_event({"type": "internal-wake-empty", "ts": now_iso(),
                              "source": "host", "origin": "internal",
                              "text": "内部唤醒返回空响应；宿主结束本批，不代表模型已完成任何事项。"})
                return RoundOutcome()
            report_failure(exc, round_no)
            return RoundOutcome(failed=True)
        except LLMError as exc:
            acknowledge=getattr(generate,'acknowledge_failure',None)
            if callable(acknowledge):acknowledge(exc)
            if exc.code == "context_length_exceeded":
                # 上游上下文超限不计为失败；否则失败分支先 continue，会跳过交接请求。
                return RoundOutcome(overflowed=True)
            report_failure(exc, round_no)
            # Deterministic upstream rejection must not replay the same batch.
            return RoundOutcome(failed=True, terminal_failure=not exc.retryable)
        except Exception as why:               # noqa: BLE001
            report_failure(why, round_no)
            # ⚠️ **落进这一支的是**（`LLMError` 已由上面那一支接走 —— 它自带 `code` 可判，这里判不了）：
            #   `TransportError` ／ `EmptyResponseError` ／ `ResponseProtocolError` ／
            #   `FirstResponseTimeout` ／ `ContentIdleTimeout` ／ `IrreversibleError` ／
            #   runaway 那个裸 `RuntimeError` —— 而这些**一律记成失败**（判不了的可重试性归层 B）。
            #   ⚠️ 它们**都不写会话**：`_take_the_reply` 在这后面，够不着 —— 那是**设计**，不是缺失。
            # 2026-10-07：**这一支必须出声** —— 它原来**一个字都不打**，只回一句
            # "failed" ⇒ 外面看到的就是"批推走了、然后什么都不发生"
            # （ 今晚"它一句都不回"就是被这一支瞒住的 而装一行表就定案了 ）。
            import sys as _s
            detail = "：" + public_subscription_error(why) if isinstance(why, SubscriptionError) else ""
            if isinstance(why, ReplayBindingConflict):
                detail = "：" + why.public_code
            print("   [round] 这一轮失败了 ⇒ " + type(why).__name__ + detail,
                  file=_s.stderr, flush=True)
            acknowledge=getattr(generate,'acknowledge_failure',None)
            if callable(acknowledge):acknowledge(why)
            from .transport import http_failure_diagnostic
            terminal = http_failure_diagnostic(why).get("retryable") is False
            return RoundOutcome(failed=True, terminal_failure=terminal)

        ts = now_ms()
        rows, references = _reply_records(result, ts)
        if before_reply is not None:
            try:
                await before_reply(result, rows)
            except Exception as error:
                report_failure(error, round_no)
                print("   [round] 回复未保存：" + type(error).__name__, flush=True)
                return RoundOutcome(failed=True, terminal_failure=True)
        _append_reply(session, result, rows)
        if on_reply is not None:
            try:
                import inspect
                delivered = on_reply(result)
                if inspect.isawaitable(delivered):
                    await delivered
            except Exception as exc:  # external delivery cannot replay the model turn
                import sys as _s
                print("   [round] output tap failed: " + type(exc).__name__,
                      file=_s.stderr, flush=True)
        try:
            _note_the_reasoning(record_event, result, round_no, now_iso)
        except Exception:                      # noqa: BLE001
            # **丢了一条日志，不丢结论。**
            #   ⚠️ 写日志是**取证**，判读五位是**决定** —— 而 `loop.py:43` 的契约是
            #      "调 `run_round`、读回五位" ⇒ 取证失败**不许**把那五位一起带走。
            #      逃出去就是这个形状：**会话留下了回执，而这一轮没有 `RoundOutcome`**
            #      （`_rounds()` 那边**没有任何 handler** 接得住它）。
            #   ⚠️ 为什么吞得理直气壮：**批的推进不看日志** —— `loop.py` 读的是这五位，
            #      日志是**旁路证据**（同 `:49` 那个 `except Exception` 的理由：
            #      判不了的可重试性归层 B；而这里连"判"都不需要）。
            #   ⚠️ 拒面是真的且不窄（`events.py:455` 那八条 ＋ `OSError`
            #      ＋ 多行那条裸 `ValueError`）⇒ 它不是一条理论上的路。
            #   ⚠️ **而第 ④ 刀之后多了一条**：库被封之后 `append` 会抛 **`SealedError`**
            #      —— 它是**第九条**、而且**不是 `EventError`**（是 `RuntimeError` 的子类）
            #      ⇒ 上面那个"那八条"从那一刻起**不再准确**。而它同样被本层吞掉
            #      （那条裸 `except Exception`）⇒ **行为不变，而这句注释得说准**。
            #   ⚠️ 而它**不静默**：这句话就是那唯一的可见性（本层没有日志设施，
            #      也不为它发明一个 —— 见 spec §六）。
            pass
        # **那一批调用真的交出去**（本刀的接线）—— 交在**写记录与写日志之后**。
        #   ⚠️ 次序是承重的：**交出去会抛**（`dispatch.make_run_tools` 那段点过名 ——
        #      `Dispatcher.run` 那条入批检查说的是"装配缺了一块"，不是"这条调用办砸了"），
        #      而"模型提过这条建议"这件事**不许随那次异常一起消失** ⇒ 写记录排在它前面。
        #   ⚠️ 而它**不在上面那个 `try` 里**（那是刻意的）：那一条 `except Exception`
        #      接的是 `generate(...)` 的错；把"这一批跑不动"也咽成"这一轮失败"是错的 ——
        #      `loop.py` 的 `_rounds()` 八条收尾**一条都不接异常** ⇒ 那颗异常冒出去
        #      **就是把整批带走**，而那正是 `dispatch` 那一层明写的"**它该有的样子**"
        #      （与"一次调用办砸了"分开，正是 6.5 的理由）。钉子在
        #      `tests/test_round.py::test_a_runner_that_raises_is_not_turned_into_a_failed_round`。
        #   ⚠️ **只在这一轮真的调了工具时才交**（空批不交）：一批"一条都没有"的调用不是
        #      一趟活，而 `Dispatcher.run` 会为它**落一条空账**（"真跑过的那几趟"那本账）。
        #   ⚠️ **没递 `run_tools`** ⇒ 与接线之前逐字相同（那一批仍只收成一位）——
        #      那一笔欠在 `_OWED["run_tools_source"]` 上（点名了谁欠它、代价是什么），
        #      **不是**静默丢掉（静默丢掉正是 `loop.py:60-63` 禁的那个形状）。
        #   ⚠️ **交回的那一份不许丢**：`ended` 是 `loop.py` ③ 那一位，而**只有那张工具表**
        #      算得出它 ⇒ 它是这一位唯一的来源（本刀之前它恒假）。其余那些（`receipts`）
        #      **已经落了**：`Dispatcher.run` 在交回之前就把它记进 `Dispatcher.receipts`
        #      —— 一处机器看得见的账，而"把它送到模型面前"归另一层
        #      （`dispatch._RECEIPT_OWED_BY` 点了名）。
        calls = tuple(getattr(result, "calls", ()) or ())
        # **2026-10-09：同一个调用连着来 ⇒ 这一届算失败** （ 见
        # `_the_same_as_last_round` 那一段： 盘上量到同一个工具连着调了 **12** 次 ，
        # 而 12 正是 `hard_rounds` ⇒ 撞了上限才停 、 中间**没人发现** ）。
        # 交给 `loop.py` 那道**现成**的连败闸（`max_consecutive_failures = 2` ）
        # ⇒ 它的循环一个字都不用改 。
        if calls and _the_same_as_last_round(session, calls):
            import sys as _s
            print("   [call] 异常 · 调用与上一轮完全重复，未派发", file=_s.stderr, flush=True)
            # These are host refusal receipts, NOT handler results or replayed
            # successes. The proposed calls are already durable above; each
            # needs a truthful result before another model request can be built.
            for one in calls:
                got = Record(kind="function_result", role="assistant",
                             name=getattr(one, "name", ""),
                             text="宿主已拒绝本次重复调用：与当前请求上一轮完全相同，未派发工具；没有产生新的执行结果。",
                             ts_ms=ts, tool_ref=references[id(one)])
                session.append(got)
                if callable(tell):
                    tell({"kind": "function_result", "name": got.name,
                          "text": got.text, "run": "round-%d" % round_no, "tool_ref": got.tool_ref})
            return RoundOutcome(failed=True)
        # **调用那一条也推出去**（卡片那条路 ）—— 与 `_take_the_reply`
        # 写下去的**同一批**（同一个 `calls` 同一个时刻 ）⇒ 两边不会对不上 。
        run_name = "round-%d" % round_no
        if callable(tell):
            for one in calls:
                tell({"kind": "function_call",
                      "name": getattr(one, "name", ""),
                      "text": getattr(one, "arguments", ""), "tool_ref": references[id(one)],
                      "run": run_name})
        ended = False
        if run_tools is not None and calls:
            try:
                from .async_tools import current_tool_references
                reference_token = current_tool_references.set(references)
                try:
                    handed = await run_tools(calls)
                finally:
                    current_tool_references.reset(reference_token)
            except BaseException as primary:
                settled=_retain_settled_prefix(session, calls, settled_receipts,
                                              ts, tell, run_name, primary, references)
                import asyncio
                if (isinstance(primary,asyncio.CancelledError) and callable(host_interrupted)
                        and host_interrupted()):
                    _retain_interruption(session,calls,settled,references,now_ms(),tell,run_name,primary)
                raise
            ended = bool(getattr(handed, "ended", False))
            # **把"办完的结果"也写进会话**（2026-10-05 作者："接上"）——
            #    ⚠️⚠️ **这一处就是那个缺口的落点**：从前只写"它要过一次东西"
            # （上面 `_take_the_reply` 那一条 `function_call` ），而**结果那条没人写**
            # ⇒ `dispatch._RECEIPT_OWED_BY` 那句"**模型永远看不见调用办没办**"说的就是它 。
            # 形状照**原设计**（`docs/superpowers/specs/2026-09-30-一轮-design.md:57`
            # Q2 丙）：**会话只存两条记录**（发生过的调用 ＋ 它的结果 ），
            # 上游 call_id 不充当持久身份；host tool_ref 随调用和回执进入会话。
            # ⚠️ **成对是这一处的契约**：调用在 `_take_the_reply` 里先写 ，结果紧接在这里
            # ⇒ 渲染那一侧**按次序现造一对 id** 就对得上 （见
            # `session.outbound` 里那一段 —— 引擎**只要求两边同一个 id** 实测）。
            # ⚠️ `receipts` **只装定案的那些**（进待确认队列的不在 ）⇒
            # 那些"还没定案"的调用**照旧只有那一条 `function_call`** ——
            # 而它们本来也不该有结果（结果还没回来 ）。
            for one in getattr(handed, "receipts", ()) or ():
                got = Record(kind="function_result", role="assistant",
                             name=getattr(one, "name", ""),
                             text=getattr(one, "text", ""), ts_ms=ts,
                             tool_ref=_receipt_reference(one, calls, references))
                session.append(got)
                if callable(tell):
                    # 同一条记录 ： 一面进会话、一面上线 。
                    # **2026-10-10 卡片第四刀**： 这一帧还带上
                    # **思考** （`result.reasoning` ）与**轮号**（`run-{n}` ）。
                    # ⚠ 为什么**不**违 Q3 甲（「思考不进会话」）：
                    # 那一条防的是「会话变肥」（ 会话是**要重发的那一份**）；
                    # 而**帧不是会话** —— 它只发到页上、
                    # **永远不会重发给模型** 。
                    tell({"kind": "function_result",
                          "name": got.name, "text": got.text, "tool_ref": got.tool_ref,
                          "run": run_name})
        # ⚠️ 量的**不是这一轮发出去的那一份**，而是"**下一轮会发出去的**那一份**"
        #    ⇒ 它必须在 `_take_the_reply` **之后**（回执刚 append 进会话）。
        over_budget = False
        if round_no > 1: # 首轮仍处理本批新事件，不在此判硬阈。
            hard = hard_tokens()
            if hard is not None:
                tail = getattr(session, "the_tail", None)
                next_messages = (session.outbound(head()) if tail is None else
                                 session.outbound(head(), tail() if callable(tail) else tail))
                over_budget = estimate(next_messages) > hard
        if run_tools is not None and calls:
            from .tool_log import log_dispatch
            log_dispatch(calls, handed)
        return RoundOutcome(called_tool=bool(calls), ended=ended,
                            over_budget=over_budget)

    async def observed_round(round_no):
        try:
            return await run_round(round_no)
        except Exception as error:
            report_failure(error, round_no)
            raise
    return observed_round


def _take_the_reply(session: Any, result: Any, ts: int) -> dict[int, str]:
    rows, references = _reply_records(result, ts)
    _append_reply(session, result, rows)
    return references


def _reply_records(result: Any, ts: int) -> tuple[list[Record], dict[int, str]]:
    """由 Result 的文本和调用重建会话记录。

上游 item 已被解析为平面结果；上游 call_id 不写入记录，
每次调用持久化宿主生成的 tool_ref，供回执精确配对。"""
    rows = []
    text = getattr(result, "text", "") or ""
    if text:
        # ★ **2026-10-12（作者：「五个都填」）**：这一条**就是"它说出去的那句话"** ——
        #   于是 `is_speak=True`：`note.cap_for` 那一位"恒进最近段"（说出去的那句
        #   不许因为分档被折掉）。⚠️ 工具那两条**不算**（它们是机器的手，不是话）。
        rows.append(Record(kind="message", role="assistant", text=text, ts_ms=ts,
                              is_speak=True, display_id=str(getattr(result, "display_id", "") or "")))
    # ⚠️ 正文与拒绝**两位都要收**：`generate.py` 的守卫只在**两位都空**时才抛。
    refusal = getattr(result, "refusal", "") or ""
    if refusal:
        rows.append(Record(kind="message", role="assistant", text=refusal, ts_ms=ts,
                              is_speak=True, display_id=(str(getattr(result, "display_id", "") or "") if not text.strip() else "")))
    calls = tuple(getattr(result, "calls", ()) or ())
    from uuid import uuid4
    # Each call carries the same host reference into its eventual receipt.
    references = {id(call): uuid4().hex for call in calls}
    for call in calls:
        rows.append(Record(kind="function_call", role="assistant",
                           name=call.name, text=call.arguments, ts_ms=ts,
                           tool_ref=references[id(call)]))
    return rows, references


def _append_reply(session: Any, result: Any, rows: list[Record]) -> None:
    from .tool_log import log_requests
    calls = tuple(getattr(result, "calls", ()) or ())
    log_requests(calls)
    for row in rows:
        session.append(row)
    for call in calls:
        # Keep raw argument diagnostics available to verbose consoles/log readers.
        import sys as _s
        print("   [call-detail] " + "tool" + " ／参数 "
              + "[arguments redacted]",
              file=_s.stderr, flush=True)


def _note_the_reasoning(record_event: Callable[[Mapping], Any], result: Any,
                        round_no: int, now_iso: Callable[[], str]) -> None:
    """Q3 甲：**思考进日志，不进会话。**

    ⚠️ 为什么：会话是"**要重发的那一份**"（放进去每一轮请求都变大，而思考常比答案长），
       日志是"**只写不读的那一份**"（零代价，且不为没见过的形状定契约）。
    ⚠️ 而 `type` 只是个字符串（`events.py` 的 `_REQUIRED` 里没有枚举）
       ⇒ **加这一种事件不用改 `events.py`**。
    ⚠️ **时钟是收进来的**（不是这一层自己 `time()`）：这个模块**不碰墙上时钟**
       —— 两个时钟都由调用方给，测试才钉得住"写进去的到底是哪一个"。
       而它**只在真要写那一条时才读**（推理为空就早退 ⇒ 不白读一次）。
    """
    reasoning = getattr(result, "reasoning", "") or ""
    if not reasoning:
        return
    record_event({
        "type": "reasoning",
        "ts": now_iso(),
        "source": "core",
        "origin": "round",
        "text": reasoning,
        "run": f"round-{round_no}",
    })
