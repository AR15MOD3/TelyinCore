# telyincore/generate.py
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .config import Config
from .stream import ResponseAccumulator, ResponseProtocolError, ToolCall, iter_sse_events
from .transport import LLMError, Transport, TransportError, PreOpenCancelled, OpenStreamCancelled

#: runaway guard：输出超过 max_output_tokens × 这个数就硬中止。
RUNAWAY_CHARS_PER_TOKEN = 12


class EmptyResponseError(RuntimeError):
    """上游回了**一段既没有正文、也没有拒绝文字的答复**。

    校验器的判据 6/8 是**纯对账** —— 终态说空、增量也空，两边对得上，它放行。
    但"模型没说出话来"是个真该知道的状态：

    · **不是协议错** —— 上游没说谎；
    · **重试没用** —— 再问一遍不会变；
    · 处置由调用方**看 `status`** 决定 —— ⚠️ **原因不止一种**：
      `completed` ⇒ 多半是思考吃掉了额度（那时才该换额度或关思考）；
      `failed` / `incomplete` ⇒ 上游认输或被截断，**跟思考额度无关**。
    · ⚠️ **只回拒绝的流不走这里** —— 拒绝文字是**用户可见**的输出（而且不可逆），
      它落在 `Result.refusal` 上，`generate()` 照常返回（见 `Result.refusal` 的说明）。

    ⚠️ `status` 是**属性**，不是消息里的一段文字（07 §7.5 / §7.2 要求调用方就按这个值分支）。
    写进消息串里的话，调用方只能**解析文本** —— 而措辞随时会改，那种分支会在某次改字时静默失效。
    """

    def __init__(self, message: str, status: str | None = None, *,
                 error: Mapping[str, Any] | None = None,
                 incomplete_details: Mapping[str, Any] | None = None,
                 usage: Mapping[str, Any] | None = None,
                 reasoning_chars: int = 0) -> None:
        super().__init__(message)
        #: 终态的 `status`（`completed` / `incomplete` / `failed`）——
        #: 与 `ResponseAccumulator.status` 同一个值，**原样**带出来，不加工。
        self.status = status
        self.error = dict(error) if error is not None else None
        self.incomplete_details = (
            dict(incomplete_details) if incomplete_details is not None else None)
        self.usage = dict(usage) if usage is not None else None
        self.reasoning_chars = reasoning_chars


class FirstResponseTimeout(TimeoutError):
    """首响应超时 —— ⚠️ **本层（`generate()`）不重试它**。

    它**不在重试元组里**（见 `test_timeout_is_not_retried`）：要不要重试是**调用方**的决策，
    而这里刻意不让编排层自作主张 —— 超时重发一遍未必是对方想要的。
    """


class ContentIdleTimeout(TimeoutError):
    """内容空闲超时 —— ⚠️ **本层（`generate()`）不重试它**（同上）。

    ⚠️ **别把它读成"externalization 让它不可重试"** ——
    机制是**那个 `except` 子句**（两条超时不在元组里），不是 `externalized` 这个标志；
    这个标志管的是"包不包成 `IrreversibleError`"，而超时**根本不进那一段**。

    这里只是提醒**调用方**：若它已经被外部化，重发一遍请求**不会"接着上次"** ——
    引擎会**从头再生成一遍**，而上一段半成品是被**丢掉**的。
    （所以准确的动词是「**再生成一遍**」，不是"再发一遍"。）
    """


class IrreversibleError(RuntimeError):
    """已经有可见内容流出去过 —— **不许重试**（07 第六节的不可逆性判据）。"""


class RoundCancelled(RuntimeError):
    """这一轮**在开工之前**就被取消了（`signal` 已置上）—— 一个**专用类型**，不是措辞。

    ⚠️ **为什么它必须是一个类型**（这个类存在的全部理由）：
    `generate()` 里还有**两条裸 `RuntimeError`** —— runaway（`_one_attempt` 里
    `"runaway output: exceeded …"` 那一条）与重试环耗尽（`generate()` 末尾那句不可达的
    `"retry loop exhausted without recording a failure"`）。它们与"被取消"在**类型上
    一模一样** ⇒ 想分辨的调用方只剩**解析消息文本**这条路，而本仓明令**别按措辞分支**
    （措辞随时会改，那种分支会在某次改字时**静默失效** —— 与 `EmptyResponseError.status`
    ／`Result.status` 同一条纪律）。

    ⚠️ 而它是 `RuntimeError` 的**子类**：既有的 `except RuntimeError` 路径照旧有效；
    想**只接取消、放过 runaway** 的调用方先接它就行（放在 `except RuntimeError` 之前）。

    ⚠️ **它管的是"还没开工"那种取消**：两处检查都在**发出这一次请求之前**
    （`generate()` 里那两句 `if signal is not None and signal.is_set()`）。
    ⚠️ **而"也因此在退避之前"那句话的范围要收窄 —— 它只对顶上那一次成立**：
       顶上那次在 `await sleep(…)` **之前**求值 ⇒ **"不白付一次退避"是它管的**；
       而**一次失败的尝试**那条路上置上的信号，要等**退避走完**才被第二次检查读到
       （`test_a_signal_set_during_the_backoff_stops_the_next_attempt` 量的就是它：
       `slept == [1000]`、`t.calls == 1`，**然后**才是 `RoundCancelled`）——
       管那一窗的是**退避之后的那一次**检查，而它保证的只是"**下一次请求不出去**"。
    传输层那条**协作式**的取消是**另一件事**：它**不抛**，只是在下一个 chunk 边界停下、
    让这一次尝试提前收工（见 `Transport.stream_chat` 的注解）。
    """


@dataclass
class Result:
    text: str
    reasoning: str
    attempts: int
    #: 终态的 `status`（`completed` / `incomplete` / `failed`）。
    #  ⚠️ **必须带出来**：不带的话，**上游认输**与**成功但空**在调用方看来一模一样 ——
    #  而 `ResponseAccumulator` 明明已经把 status 算出来了。
    status: str | None = None
    #: 已经有可见内容流出去过 —— 不可重试。
    externalized: bool = False
    #: 模型明确表示不做的文字 —— ⚠️ **与 `text` 分开**：那是另一个信号，处置也不同
    #: （正文可以转述；拒绝要原样交给调用方）。它**用户可见**，所以它非空时
    #: `externalized` 必为 True，而 `EmptyResponseError` 不会触发。
    refusal: str = ""
    #: 这一轮模型要调的每一次工具 —— **默认空**：那样既有调用点一个字都不用改。
    #  ⚠️ 用 `tuple`（与 `session.records()` 同一条纪律：交出去的东西改不动）；
    #  累加器那边是 `list`（它要往里加），所以这里是**收口**，不是同一只容器。
    #  ⚠️ **本层只搬运**：`arguments` 是**原样的 JSON 字符串**，不解（那是 S6 的判断）。
    calls: tuple["ToolCall", ...] = ()
    #: **终态报的那一份用量**（2026-10-05）—— 原样带着走 `usage` 那几格
    #: （`input_tokens` ／ `input_tokens_details.cached_tokens` …）
    #: ⚠️ 上游没报 ⇒ `None`（**不是 0** ）⇒ 调用方那一侧照旧"没有就是没有" 。
    usage: Mapping[str, Any] | None = None
    #: **这一轮那三段有多久**（秒；2026-10-05）—— `thinking` ／ `output` ／ `total`
    #: ⚠️ 交的是**差值**（不是时间戳 ⚠️ `monotonic` 的绝对值**跨进程没有意义** ）；
    #: ⚠️ 上游没走到"有内容"那一步 ⇒ 前两格是 `None`（**不编一个 0** ），`total` 照旧有 。
    timing: Mapping[str, float | None] | None = None
    #: **"思考 ＋ 输出"**（2026-10-05 作者："显示的是思考 和输出所花费的时间"）——
    #: 那一格显示的就是它 （`timing` 里的 `total` 是**全程**，含排队那些杂项 ）。


def build_body(cfg: Config, messages: Sequence[Mapping]) -> dict:
    """`/v1/responses` 的请求体。

    ⚠️ 消息那个键叫 **`input`**，不是 `messages` —— 实测（2026-09-26）：
    往 `/v1/responses` 发 `messages` 会回
    `{"error":{"code":"unknown_parameter","message":"unknown parameter: messages","param":"input"}}`
    （原文照抄，一个字都没改）。
    数组形式（`[{"role":…,"content":…}]`）实测**被接受**，所以对上的那层抽象原样保住。
    ⚠️ **`tools` 那一格**（2026-10-05）：`cfg.tools` **空就不放** ——
      放一个空的 `[]` 等于告诉模型"你没有工具"（那是**一句假话** ）；
      而**有**的时候它是**声明**那一半（`name` ／ `description` ／ `parameters` ——
      handler **不在**这一路上 见 `tools.py` 的抬头）。
    """
    body = {
        "model": cfg.model,
        "input": [dict(m) for m in messages],
        "stream": True,
        "max_output_tokens": cfg.max_tokens,
        "chat_template_kwargs": {"enable_thinking": cfg.thinking},
    }
    tools = getattr(cfg, "tools", ())
    if tools:
        body["tools"] = [dict(one) for one in tools]
    return body


class ResponseBudgetTimeout(TimeoutError):
    """The absolute generation deadline expired; never retry this outcome."""

class UnsettledToolHistory(ValueError):
    """Do not send or redispatch a history whose tool outcomes are unconfirmed."""

def _require_settled_tool_history(messages):
    pending=set();seen=set()
    for item in messages:
        kind=item.get('type')
        if kind not in ('function_call','function_call_output'):
            continue
        call_id=item.get('call_id')
        if not isinstance(call_id,str) or not call_id:
            raise UnsettledToolHistory('tool history requires an exact call identity')
        if kind=='function_call':
            if call_id in seen:
                raise UnsettledToolHistory('duplicate tool history identity')
            pending.add(call_id);seen.add(call_id)
        elif call_id not in pending:
            raise UnsettledToolHistory('unmatched or duplicate tool outcome')
        else:pending.remove(call_id)
    if pending:
        error=UnsettledToolHistory('tool outcome unconfirmed; no model request or automatic retry')
        error.pending_call_ids=tuple(sorted(pending))
        raise error


async def generate(cfg, messages, transport, *, signal=None,
                   sleep=lambda ms: asyncio.sleep(ms / 1000), monotonic=time.monotonic):
    if signal is not None and signal.is_set():
        raise RoundCancelled('这一轮已被取消（请求历史准入前）')
    _require_settled_tool_history(messages)
    budget = getattr(cfg, 'total_response_ms', 600_000)
    if type(budget) is not int or budget <= 0:
        raise ValueError('positive integral total_response_ms required')
    deadline = asyncio.get_running_loop().time() + budget / 1000
    timer = asyncio.timeout_at(deadline)
    try:
        async with timer:
            return await _generate(cfg, messages, transport, signal=signal,
                                   sleep=sleep, monotonic=monotonic, _deadline=deadline)
    except TimeoutError as error:
        if not timer.expired():
            raise
        raise ResponseBudgetTimeout('whole generation deadline exceeded') from error


async def _generate(
    cfg: Config,
    messages: Sequence[Mapping],
    transport: Transport,
    *,
    #: 取消接缝（可选）—— 与 `Transport.stream_chat(signal=…)` 是**同一条**：
    #: 这一层在**两处**查它 —— 重试环那一层，以及透传下去由传输层在 chunk 边界查。
    #: ⚠️ 注解是 `Any`：这一侧只要求"有个 `is_set()`"，不绑定具体类型
    #: （本仓模块之间靠**鸭子类型**耦合）。
    signal: Any = None,
    sleep: Callable[[int], Awaitable[None]] = lambda ms: asyncio.sleep(ms / 1000),
    monotonic: Callable[[], float] = time.monotonic,
    _deadline: float | None = None,
) -> Result:
    body = build_body(cfg, messages)
    delays = cfg.retry_delays_ms
    last: Exception | None = None

    for attempt in range(1 + len(delays)):
        # **重试环这一层也要查** —— 只把它透传给 `stream_chat` 是不够的：
        #   一个已取消的请求会白试 4 次、退避 [1000, 4000, 10000]、多等 **15.0 秒**（实测）。
        #   ⚠️ **这个位置只管一个窗口**：它抓"**进入本轮时**就已经取消" ——
        #   第一次尝试尤其要紧（放到尝试**之后** ⇒ 第一次请求照样发出去，预扫第 4 条点的就是它）。
        #   而**退避那一窗**里置上的信号它**看不见** —— 那由下面 `await sleep(...)` 之后
        #   再查的那一次管。两次**不互相遮蔽**：`await` 之前的求值管不了 `await` 之后的时间。
        #   ⚠️ 抛的是**专用类型**（不是裸 `RuntimeError`）—— 见 `RoundCancelled` 的说明：
        #   调用方要能**只接取消**、放过 runaway 那类裸 `RuntimeError`。
        if signal is not None and signal.is_set():
            raise RoundCancelled("这一轮已被取消（信号在重试环那一层被看到）")
        if attempt:
            await sleep(delays[attempt - 1])
        # **退避之后再查一次** —— 退避要等 1000／4000／10000 ms（那 15.0 秒里的大头），
        #   而**取消完全可能就落在这个窗里**（Task 2 的 `stop()` 正会那样打断一次退避）。
        #   上面那次检查是在 `await` **之前**求值的 ⇒ 窗里置上的信号它**看不见**；
        #   不在这里重读一次，下一次请求就**照样发出去** —— 而一次关机请求会被记成
        #   一轮失败（`ResponseProtocolError` ／ `IrreversibleError`）。
        if signal is not None and signal.is_set():
            raise RoundCancelled("这一轮已被取消（信号在重试环那一层被看到）")
        try:
            return await _one_attempt(cfg, body, transport, attempt + 1,
                                      monotonic=monotonic, signal=signal, total_deadline=_deadline)
        except PreOpenCancelled as exc:
            # Only the transport's explicit pre-open outcome is cancellation.
            # Never relabel an independent protocol error merely because a
            # signal is concurrently set. No retry or synthetic completion.
            raise RoundCancelled("request cancelled before HTTP stream open") from exc
        except OpenStreamCancelled as exc:
            raise RoundCancelled("request cancelled after HTTP stream open") from exc
        except LLMError as exc:
            if not exc.retryable:
                raise
            last = exc
        except TransportError as exc:
            # ⚠️ 与上面 `LLMError` 那一支**同一个形状**：已知永久的错**不进重试桶**
            # （回评 I3）。目前唯一一类是"URL 解析不出来"——
            # 重试 4 次、退避 [1000, 4000, 10000]、多等 **15.0 秒**（实测）之后，
            # 报的还是同一个错。读断 / 连不上仍然是可重试的（默认 `retryable=True`）。
            if not exc.retryable:
                raise
            last = exc
        # ResponseProtocolError / EmptyResponseError / runaway / IrreversibleError / 两条超时
        # 直接往上抛 —— 它们不是可重试的。
        # ⚠️ 「不被重试」的保证来自**它们不在上面任何一条 `except` 里**，**不是来自类型本身**：
        # 两条超时不被重试是**这一层**的事（`test_timeout_is_not_retried` 钉着它）；
        # `IrreversibleError` 同理且更严 —— 不可逆性判据要求连一次重试都不给。

    if last is None:
        # 不可达 —— 但 `python -O` 会**剥掉 `assert`**，那时 `raise None` 会炸成 `TypeError`，
        # 又是一个"跑出契约"的类型。显式抛。
        raise RuntimeError("retry loop exhausted without recording a failure")
    raise last


async def _one_attempt(
    cfg: Config, body: Mapping, transport: Transport, attempts: int,
    *, monotonic: Callable[[], float] = time.monotonic, signal: Any = None,
    total_deadline: float | None = None,
) -> Result:
    from .voice_draft import current as current_voice_attempt
    voice_attempt = current_voice_attempt.get()
    acc = ResponseAccumulator()
    limit = cfg.max_tokens * RUNAWAY_CHARS_PER_TOKEN
    # ⚠️ 三个量都在 `stream_chat` **之前**取：它返回的是**异步生成器**，真正开工要等第一次迭代 ——
    # 起点定在它之前，才等于"从发出去那一刻算"。
    started = monotonic()
    content_at = started
    first_seen = False
    #: **第一个"有内容"的事件那一刻**（2026-10-05）—— 它就是**思考那一段的终点** 。
    #: ⚠️ **不要拿 `content_at` 当它** ：那一个**每次事件都重置**（它是"内容空闲"判据用的 ）。
    #: 判据是「`acc.text` 或 `acc.reasoning` 非空」 —— 比"第一个 chunk"准（chunk 可能只是壳 ）。
    first_token_at: float | None = None
    # ⚠️ 这里给 transport 的是 **`frame_idle_ms`（帧空闲）**，不是 `first_response_ms` ——
    # 它落在 httpx 的 read timeout 上，管的是"**连一个字节块都不来**"；
    # 而首响应 / 内容空闲那两条判据只有这一层看得见事件，由下面的循环判。
    #
    # ⚠️ **不 `await` 它**（异步生成器是"调用即返回"的，`await` 它会直接报错）：
    # 拿到的是异步迭代器，异常要等 `async for` 第一次取值时才抛。
    # ⚠️ `signal` **原样透传**（协作者，不解释、不替换）：取消是在**下一个 chunk 边界**
    # 被传输层看到的 —— 见 `transport.Transport.stream_chat` 的说明。
    chunks = transport.stream_chat(cfg.base_url, body, frame_idle_ms=cfg.frame_idle_ms,
                                   signal=signal)
    # ⚠️ 契约要求的是**异步生成器**（见 `transport.py` 的注解）：`generate()` 会 `aclose()` 它。
    # 一个只是 `AsyncIterator` 的返回物在**注解上合法**，却**没有 `.aclose()`**
    # ⇒ 它会在**正常路径**上炸出 `AttributeError`。⇒ **响亮地抛一个说清要求的错**，
    # 而不是让它那样炸。
    #
    # ⚠️ 这里**故意**用 `hasattr` 而不是 `isinstance(..., AsyncGenerator)`：前者允许更宽的合法实现
    # （任何**可 `aclose` 的异步迭代器**）；那样「`aclose()` 抛且无主错」这条路径才**可达**，
    # 而它由下面那条测试守着。收紧成 `isinstance` 会让它按构造不可达、那条测试也就没意义了。
    #
    # ⚠️ 而它在异步下**更必要**：模块之间**零 import**（靠鸭子类型耦合），
    # 形状变了**没有任何类型检查**会响 —— 只有这一条会。
    if not hasattr(chunks, "aclose"):
        raise TypeError(
            "stream_chat must return an async generator (it is aclose()d); "
            f"got {type(chunks).__name__}")
    # ⚠️ **主错要显式记，不许去问 `sys.exc_info()`。**
    # `sys.exc_info()` 是**线程级**的：若**调用方**正在处理一个异常，它会看到**那个** ——
    # 于是"没有主错"这一支照样走不到 ⇒ 清理失败又静默消失（实测：从调用方的 `except` 里调用时，
    # 它捕获的是调用方自己的异常）。显式标志只记**本次调用**的主错。
    primary: BaseException | None = None
    async def bounded_events():
        events = iter_sse_events(chunks)
        first = True
        clock = asyncio.get_running_loop().time
        deadline = clock() + cfg.first_response_ms / 1000
        try:
            while True:
                if total_deadline is not None and clock() >= total_deadline:
                    raise ResponseBudgetTimeout('whole generation deadline exceeded')
                budget = cfg.first_response_ms if first else cfg.content_idle_ms
                timer = asyncio.timeout_at(deadline)
                try:
                    async with timer:
                        event = await anext(events)
                except StopAsyncIteration:
                    return
                except TimeoutError as error:
                    if not timer.expired():
                        raise
                    kind = FirstResponseTimeout if first else ContentIdleTimeout
                    raise kind(f'event deadline exceeded after {budget}ms') from error
                if not (isinstance(event, dict) and event.get('__done__')):
                    first = False
                    deadline = clock() + cfg.content_idle_ms / 1000
                yield event
        finally:
            await events.aclose()
    bounded = bounded_events()
    try:
        async for event in bounded:
            # ⚠️ `isinstance` 守卫在前：`iter_sse_events` 会原样产出 `json.loads` 的结果，
            # 所以 `data: 5` / `null` 会到这儿来。直接 `event.get(...)` 会以 **AttributeError**
            # 逃出 —— 而 `feed()` 本来就为这一类准备了协议错（见 stream.py 的守卫注释）。
            if isinstance(event, dict) and event.get("__done__"):
                continue
            now = monotonic()
            # 判据一：**首个事件**到时还太慢 ⇒ 首响应超时。只看第一个 ——
            # 之后每个事件之间的间隔是"内容空闲"那条管的事。
            if not first_seen:
                first_seen = True
                if (now - started) * 1000 > cfg.first_response_ms:
                    raise FirstResponseTimeout(
                        f"first response timeout after {cfg.first_response_ms}ms")
            # 完整事件的真实等待截止由 bounded_events 执行，保活/半帧不续期。
            # 这里保留注入时钟的到达时复核。持续完整事件可续内容空闲预算；
            # 此合同不等于对正常持续输出另设全程硬截止。
            if (now - content_at) * 1000 > cfg.content_idle_ms:
                raise ContentIdleTimeout(
                    f"content idle timeout after {cfg.content_idle_ms}ms")
            acc.feed(event)
            if voice_attempt is not None:
                try:
                    voice_attempt.observe(acc, event)
                except Exception:
                    voice_attempt.cancel()
            # 第一个**有内容**的事件 ⇒ 记下那一刻（思考那一段到此为止 ）
            if first_token_at is None and (acc.text or acc.reasoning):
                first_token_at = now
            # ⚠️ 数的是 **text ＋ reasoning**，不是只看 text：
            # 思考开着时（默认开），额度**全进 `reasoning`** ——
            # 只数 text 等于在"服务器忽略 `max_output_tokens`"这条主通道上**没有守卫**。
            if len(acc.text) + len(acc.reasoning) > limit:
                raise RuntimeError(f"runaway output: exceeded {limit} chars")
            # ⚠️ **终态到过就收工 —— 不许再去拉下一块字节。**
            # 流的契约在终态那一刻就结束了；再往下读只能读到**不产生事件的字节**
            # （引擎 prefill 期发的 `: ping` 就是这种，实测**每 2.000 s 一个**），
            # 而每来一个字节都会重置 httpx 的 read timeout ⇒ 那种等待**没有上界**
            # ——**不是**有界于 `frame_idle_ms`。
            # ⚠️ 诚实起见：**今天**引擎在终态帧之后**一个字节都不发**
            # （实测：全部 7548 字节在 t=0.156 s 前到齐，`iter_bytes()` 在最后一个字节之后
            # 花 0.000 s 返回。⚠️ 那次量的是**同步 `iter_bytes()` 路径**；异步化之后这条链走
            # `aiter_bytes()`，**没有重测** —— 数还是那次同步实测的数）⇒ 这一行**现在不省任何
            # 时间**，它是**结构性的**：把"读完就停"从"取决于上游行为"变成**我们说了算**。
            # `finish()` 在循环**外面**、仍然是紧接着跑到的 —— 早退**不跳过**判据 6/8。
            if acc.terminated:
                break
            # ⚠️ **任何事件都重置 `content_at`** —— 「内容空闲」的意思是"**引擎不再产出**"，
            # 那与"用户可不可见"**是两件事**：`reasoning_text.delta` 是引擎**在产出**、却不外部化，
            # 而**思考默认开着** ⇒ 只认 `EXTERNALIZING_TYPES` 的话，一段超过 300 s 的推理阶段
            # 会在**连接稳定流着**的时候被判成"空闲"、抛 `ContentIdleTimeout`、且**绕过重试**
            # ⇒ **那是把判据用在正常流上。**
            # （`externalized` 是"**不可逆**"的判据，另一回事 —— 它由两处置位：
            #   `EXTERNALIZING_TYPES` 里的两个 delta，**以及** `function_call` 那种 item
            #   的 `output_item.added`；见 `stream.py` 的 `VISIBLE_ITEM_TYPES`。）
            # ⚠️ 这一行**曾经写着**"`externalized` 仍只由 `EXTERNALIZING_TYPES` 置位" ——
            #   自 `function_call` 那条支路起，那句话就不成立了。读者查不可逆规则时会看这里。
            content_at = now
        # ⚠️ `finish()` **在 try 里面**（不是紧跟 `finally` 之后）：它的判据 6/8 对账
        # 也在**流已经吐过内容之后**才判 —— 那时报协议错，同样是"内容已经漏出去了"：
        # 必须让调用方看到 `IrreversibleError` 而不是一个可以被当成"重发一次就好"的协议错
        # （`test_irreversible_error_is_not_retried` 正是这条：吐了一段可见文本然后断流）。
        acc.finish()
        if acc.status == "failed" and (acc.error or acc.text or acc.calls or acc.refusal):
            from .transport import stream_failure
            raise stream_failure(acc.error)
    except BaseException as exc:
        # ⚠️ 这一支的作用是**记录主错**：让 `finally` 知道"本次调用有主错"。
        # 它**不是**用来挡 `KeyboardInterrupt`/`SystemExit` 的 —— 那是 wrapper 那一支的事
        # （见下面包住 `chunks.aclose()` 的 `except BaseException:`）。
        # 抓 `BaseException` 而不是 `Exception`，是因为：主错是 `KeyboardInterrupt`/`SystemExit` 时，
        # 若只抓 `Exception`，**主错标志**会停在 `None` ⇒ **清理错误反过来顶掉它**
        # （实测：Ctrl-C 被吞掉、还被当成可重试的 `TransportError` 重试 4 次）。
        primary = exc
        # **不可逆性判据**（07 第六节）：`acc.externalized` 已置位 ⇒ 这次失败**不许重试**。
        # ⚠️ 理由**不是**"用户已经看见了两遍" —— 那与事实不符：`_one_attempt` **只在最后返回**、
        # `generate()` 也**没有回调** ⇒ **任何部分文本都不可能到过调用方**
        # （`output_item.added` 本身更不带正文）。
        # 真正的理由是：**不许把两段半截答案拼成一个** —— 重发会让引擎**从头发起**第二段，
        # 与已经动起来的那次拼不到一起。
        # wrap 成一个专用类型，让调用方一眼看出"这次已经动过、不能再重来"。
        #
        # ⚠️ **两条超时不在这个元组里**，尽管它们也是"流半路坏了"：它们**必须原样抵达调用方**。
        # 理由有两条：
        #  · 它们是**判据本身** —— 调用方要看的是"哪条判据判的"（首响应 / 内容空闲），
        #    换成一个笼统的 `IrreversibleError` 就把这个信息弄丢了；
        #  · 换成它**也换不来任何东西** —— `generate()` 压根不重试这两条
        #    （它的 `except` 只认 `LLMError` / `TransportError`），所以不可逆性**已经**成立。
        # （实测：不这么分开，`test_content_idle_timeout` 那条会以 `IrreversibleError` 逃出 ——
        #  那时 `delta` 已经在 seq 1 被 `feed()` 过，`acc.externalized` 早就是 True 了。）
        if isinstance(exc, OpenStreamCancelled):
            raise
        if acc.externalized and isinstance(exc, (
                TransportError, LLMError, ResponseProtocolError, RuntimeError)):
            raise IrreversibleError(str(exc)) from exc
        raise
    finally:
        # ⚠️ 必须显式关闭：上面任何一条 `raise` 都发生在**迭代中途**，
        # 传输的异步生成器（以及它背后的 `async with self._client.stream(...)`）还开着 ——
        # 不关，引擎会继续往一个没人读的 socket 里写。
        cleanup_errors = []
        for owned_stream in (bounded, chunks):
            try:
                await owned_stream.aclose()
            except BaseException as error:
                cleanup_errors.append(error)
        if cleanup_errors:
            cleanup_error = cleanup_errors[0]
            # **清理抛的错不许顶掉主错** —— 否则一个 runaway（**不可重试**）
            # 会被换成 `TransportError`（**可重试**）⇒ 信号丢了、还多发一次请求。
            # 只在**本次调用确实没有主错**时，清理的失败才是主错（那时它**必须**浮出来）。
            if primary is None:
                if len(cleanup_errors) > 1:
                    cleanup_error._additional_cleanup_errors = tuple(cleanup_errors[1:])
                raise cleanup_error
            primary._stream_cleanup_error = cleanup_error
            primary._stream_cleanup_errors = tuple(cleanup_errors)
            primary.add_note('stream cleanup failed; original failure retained')
    # 「诚实地空」要拦下 —— 见 EmptyResponseError 的说明。
    # ⚠️ 用 `.strip()`：纯空白的"回答"不算回答。
    # ⚠️ **拒绝也算"说出来了"**：它用户可见、且已置 `externalized` ⇒
    #   那时报"什么都没说"是自相矛盾的（一条已漏出去的答复被报成空）——
    #   该做的是把拒绝文字交给调用方（`Result.refusal`）。
    # ⚠️ **调用也算"说出来了"** —— 与上面那一条**同一个理由**，一个字都不用改：
    #   `function_call` 的 item **经 `output_item.added` 那条腿**同样置 `externalized`
    #   （它在 `stream.py` 的 `VISIBLE_ITEM_TYPES` 里，与 `output_text.delta` /
    #   `refusal.delta` 并列为 07 §7.3 的"不可逆三"），而且它是一个**完整的、
    #   要被执行的动作** —— `Result.calls` 装的就是那一轮要做的事。
    #   ⚠️ **而那句"置 `externalized`"只对 `added` 那条腿成立**：`stream.py` 那一条
    #      要求 `t == "response.output_item.added"`，而 `function_call_arguments.done`
    #      与 item 自己的 `output_item.done` 都**记调用、但不置位**
    #      （量过：一条只经由 done 递调用的流 ⇒ `calls` 非空、`externalized=False`）。
    #      ⇒ **豁免不靠那一句**：靠的是"**记下一个调用 ＝ 有个动作在等**"、
    #      不是"什么都没说" —— 两条腿记下的是同一个**要被执行的动作**。
    #   ⇒ 那时报"什么都没说"，报的同样是一条**已经漏出去**的答复。
    #   ⚠️ **代价是量过的**：不豁免 ⇒ `EmptyResponseError` 冒出去 ⇒ 累加器**连同 `calls`
    #   一起被丢掉** ⇒ **一个完好的工具调用被扔掉**，而 `loop.py` 把它计成一轮失败。
    #   （⚠️ **不是**"会重发一遍"：`EmptyResponseError` 不在 `generate()` 的任何一条
    #     `except` 里 ⇒ 它压根不被重试 —— 后果是"丢一次调用"，不是"发两遍"。）
    #   ⚠️ 这里**只判"有没有"**，不判"完不完整"：`calls` 里可能是个缺 `call_id` 的半截调用，
    #   而"半截的调用算不算调了"是 **S6** 的判断（见 `stream.ToolCall`）—— 本层不越界。
    if not acc.text.strip() and not acc.refusal.strip() and not acc.calls:
        # Do not log arbitrary upstream messages: they can echo private input.
        code = (acc.error or {}).get("code")
        reason = (acc.incomplete_details or {}).get("reason")
        detail = ""
        if isinstance(code, str) and code.replace("_", "").isalnum() and len(code) <= 80:
            detail += f"; upstream_code={code}"
        if isinstance(reason, str) and reason.replace("_", "").isalnum() and len(reason) <= 80:
            detail += f"; incomplete_reason={reason}"
        detail += f"; reasoning_chars={len(acc.reasoning)}"
        usage = acc.usage or {}
        for key in ("input_tokens", "output_tokens"):
            count = usage.get(key)
            if type(count) is int and count >= 0:
                detail += f"; {key}={count}"
        output_details = usage.get("output_tokens_details")
        count = (output_details.get("reasoning_tokens")
                 if isinstance(output_details, Mapping) else None)
        if type(count) is int and count >= 0:
            detail += f"; reasoning_tokens={count}"
        raise EmptyResponseError(
            f"no user-visible text (status={acc.status!r}{detail})",
            status=acc.status, error=acc.error, incomplete_details=acc.incomplete_details,
            usage=acc.usage, reasoning_chars=len(acc.reasoning))
    ended = monotonic()
    return Result(text=acc.text, reasoning=acc.reasoning, attempts=attempts,
                  status=acc.status, externalized=acc.externalized,
                  refusal=acc.refusal, calls=tuple(acc.calls),
                  usage=acc.usage,
                  timing={
                      "thinking": (None if first_token_at is None
                                   else max(0.0, first_token_at - started)),
                      "output": (None if first_token_at is None
                                 else max(0.0, ended - first_token_at)),
                      "total": max(0.0, ended - started),
                      # **思考 ＋ 输出**（那一格显示它 —— 不是"全程"：
                      # 全程还含排队／首响应那些杂项 ）
                      "answer": (None if first_token_at is None
                                 else max(0.0, ended - started)),
                  })
