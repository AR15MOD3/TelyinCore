# telyincore/bus.py
"""总线按到达顺序攒事件，再按 debounce、flush、piggyback 触发有界批次交付。

时间与定时器由调用方成对注入，必须来自同一个时钟源，避免截止时间与触发时间漂移。"""
from __future__ import annotations

from collections.abc import Callable
from collections import deque
from dataclasses import dataclass, asdict
import json
from typing import Any, Literal

from .events import EventEnvelope

# 总线队列的元素是事件信封；新增元素类型时同步调整此别名及消费者。
WakeItem = EventEnvelope

# 支持安静间隔、立即投递和搭车三种触发。
TriggerMode = Literal["debounce", "flush", "piggyback"]


def default_trigger(item):
    return "flush" if item.origin == "internal" else "debounce"


#: 定时器句柄 —— 形状由注入的 `schedule` 说了算，总线不解释它。
TimerHandle = Any


@dataclass(frozen=True)
class WakeBusOptions:
    """不可变的合批参数。构造时校验整数范围及最小批龄不超过最大批龄。

安静间隔允许大于最大批龄；此时由最大批龄触发投递。"""

    quiet_gap_ms: int = 2500
    min_batch_age_ms: int = 0
    max_batch_age_ms: int = 15000
    max_batch_size: int = 100
    max_queue_size: int = 10000
    max_queue_bytes: int = 64 * 1024 * 1024
    max_batch_bytes: int = 8 * 1024 * 1024

    # 合批配置的有效区间，由 __post_init__ 校验。
    _RANGES = (
        ("quiet_gap_ms", 100, 600_000),
        ("min_batch_age_ms", 0, 600_000),
        ("max_batch_age_ms", 1000, 3_600_000),
        ("max_batch_size", 1, 1000),
        ("max_queue_size", 1, 1_000_000),
        ("max_queue_bytes", 1, 1024 * 1024 * 1024),
        ("max_batch_bytes", 1, 1024 * 1024 * 1024),
    )

    def __post_init__(self) -> None:
        for name, lo, hi in self._RANGES:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} 必须是整数，得到 {value!r}")
            if not (lo <= value <= hi):
                raise ValueError(f"{name} 必须在 {lo}..{hi}，得到 {value}")
        # ⚠️ **范围之外还有一条跨字段的不变量**：批龄的下限不能高过它的上限。
        # 反过来的配置是**自相矛盾**的 —— "至少攒 600 秒"而"不许超过 1 秒"，
        # 那个窗口是**空的**，而 Task 2 的 deadline 数学正是吃这两个值。
        # ⚠️ 而**它拒在这儿有代价、也有理由**：本类是这四个参数**唯一**的校验层
        # （上面那段说的是"范围只在配置层"，我们还没有配置层）⇒
        # 一条静默生效的矛盾配置会让 Task 2 的上下限互相打架而没人查得动。
        if self.min_batch_age_ms > self.max_batch_age_ms:
            raise ValueError(
                f"min_batch_age_ms ({self.min_batch_age_ms}) 不能大于 "
                f"max_batch_age_ms ({self.max_batch_age_ms})")
        # ⚠️ **而 `quiet_gap_ms` 与 `max_batch_age_ms` 之间故意不约束** —— 这不是漏的：
        # 「短的 `max_batch_age_ms` ＋ 长的 `quiet_gap_ms`」是**合法配置**
        # （安静间隔还没到、上限先到了，就按上限送），Task 2 可能故意用它。
        # ⇒ 只拒「**自相矛盾**」，不拒「**少见**」。
        # `test_only_the_contradictory_batch_age_pair_is_refused` 钉着这两半。


class BusFull(RuntimeError):
    """Admission refused before any queue or timer mutation."""


class WakeBus:
    """攒与调度。交接见 Task 3。"""

    def __init__(
        self,
        opts: WakeBusOptions,
        *,
        clock: Callable[[], int],
        schedule: Callable[[int, Callable[[], None]], TimerHandle],
        cancel: Callable[[TimerHandle], None],
        on_ready: Callable[[], None] | None = None,
    ) -> None:
        self.opts = opts
        self._clock = clock
        self._schedule = schedule
        self._cancel = cancel
        self._on_ready = on_ready

        self._queue = deque()  # (item, piggyback, serialized bytes)
        self._queue_bytes = 0
        self._external_count = 0
        self._active_count = 0
        self._timer: TimerHandle | None = None
        self._timer_token = None
        self._first_at: int | None = None   # 首件到达的时刻；整个批次期间不动
        self._last_at: int = 0              # 最近一件到达的时刻；每次刷新
        self._ready: bool = False        # 「有一批就绪而没人来取」—— 置真在 Task 2 的 `_deliver`
        self._fixed_deadline: int | None = None
        self._only_fixed_windows = True
        self._closed = False
        self._taking = False
        self._close_errors: list[BaseException] = []

    def begin_shutdown(self) -> None:
        """Stop admission and the owned timer; retain unconsumed events and failures."""
        self._closed = True
        if self._timer is not None:
            timer = self._timer
            try:
                self._cancel(timer)
            except BaseException as error:
                self._close_errors.append(error)
                raise
            if self._timer is timer:
                self._timer = None
                self._timer_token = None

    def shutdown_state(self) -> dict[str, Any]:
        return {'accepting': not self._closed, 'timer_pending': self._timer is not None,
                'buffered': len(self._queue), 'buffered_bytes': self._queue_bytes, 'failed': len(self._close_errors),
                'cleanup_ok': self._closed and self._timer is None and not self._close_errors}

    # ── 可见性 ──────────────────────────────────────────────
    def pending(self) -> int:
        """队里有多少项（**含** piggyback）—— 只做可见性统计，不是投递判据。"""
        return len(self._queue)

    @property
    def ready(self) -> bool:
        """有一批**已经攒够、只等着被取走**，而还没有人来取 —— **只读**。

        ⚠️ **它是真会变的** —— `_deliver` 把它置真（到点／`flush`／溢出三条路都走它），
        `take_if_ready()` 在取走时把它清掉 ⇒ 交付到取走之间为真、取走后为假。
        它在这儿出现，是为了让「送出去了」这个信号**从一开始就是它自己**：
        拿 `pending() == 0` 去表示"送了"，等于把「清不清队」这个实现细节变成契约 ——
        而那是 Task 2／3 的形状。
        """
        return self._ready

    # ── 攒 ──────────────────────────────────────────────────
    def push(self, item: WakeItem, *, trigger: TriggerMode | None = None,
             window_ms: int | None = None) -> None:
        """接收事件。触发由产生方指定；默认内部事件 flush，外部事件 debounce。"""
        if self._closed:
            raise RuntimeError('WakeBus admission is closed')
        if self._taking:
            raise RuntimeError('WakeBus cannot accept reentrant pushes during batch handoff')
        if window_ms is not None and (type(window_ms) is not int or not 0 <= window_ms <= 10000):
            raise ValueError("window_ms must be an integer between 0 and 10000")
        if trigger is None:
            trigger = default_trigger(item)

        if trigger not in ('flush', 'debounce', 'piggyback'):
            raise ValueError('unknown bus trigger')
        size = len(json.dumps(asdict(item), ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        if (len(self._queue) >= self.opts.max_queue_size
                or self._queue_bytes + size > self.opts.max_queue_bytes):
            raise BusFull('WakeBus capacity exceeded; event was not queued')
        piggyback = trigger == "piggyback"
        self._queue.append((item, piggyback, size))
        self._queue_bytes += size
        self._active_count += not piggyback
        self._external_count += not piggyback and item.origin == 'external'

        if trigger == "flush":
            self._deliver()                 # 绕过全部计时器与年龄
            return
        if trigger == "piggyback":
            return                          # 只入队 —— 不 arm、不重算、不判上限

        # 剩下的就是 debounce：先判上限，再重算 deadline。
        now = self._clock()
        if window_ms is None:
            self._only_fixed_windows = False
        elif self._fixed_deadline is None:
            self._fixed_deadline = now + window_ms
        if self._first_at is None:
            self._first_at = now
        self._last_at = now

        if self._pending_event_count() >= self.opts.max_batch_size:
            self._deliver()
            return
        self._arm()

    def _pending_event_count(self) -> int:
        """只计外部事件，并排除 piggyback；投递闸门的溢出判断必须使用相同口径。"""
        return self._external_count

    def _arm(self) -> None:
        """重算**同一批**的 deadline。`_first_at` 全程不动。

        ```
        at = min(first_at + max_batch_age_ms,      # 上限：再吵也得送
                 max(first_at + min_batch_age_ms,  # 下限：不能送得太早
                     last_at + quiet_gap_ms))      # 安静：静了这么久就送
        ```
        """
        if self._first_at is None:
            return
        o = self.opts
        at = min(
            self._first_at + o.max_batch_age_ms,
            max(
                self._first_at + o.min_batch_age_ms,
                self._last_at + o.quiet_gap_ms,
            ),
        )
        # An adapter window replaces debounce, rather than adding a second wait.
        # Mixed producers keep the existing default timing contract.
        if self._only_fixed_windows and self._fixed_deadline is not None:
            at = min(self._first_at + o.max_batch_age_ms,
                     max(self._first_at + o.min_batch_age_ms, self._fixed_deadline))
        if self._timer is not None:
            self._cancel(self._timer)
            self._timer = None
            self._timer_token = None
        token = object()
        self._timer_token = token
        def deliver_current():
            if self._timer_token is token:
                self._deliver()
        try:
            self._timer = self._schedule(max(0, at - self._clock()), deliver_current)
        except BaseException:
            self._timer_token = None
            raise

    def _deliver(self) -> None:
        """取消定时器并标记就绪；项仍留在队列，直到同步的 take_if_ready 取走。"""
        if self._closed:
            return  # A callback already queued before cancellation cannot revive work.
        if self._timer is not None:
            self._cancel(self._timer)
            self._timer = None
        self._timer_token = None
        # 仅有 piggyback 的批不投递。
        if not self._active_count:
            return
        if not self._queue:
            return
        self._first_at = None          # ⚠️ 这一批已交付 ⇒ 下一批重新钉
        self._ready = True             # ⚠️ 它仍是**可观察的那个信号**（Task 3 从外面看的就是它）
        # ⚠️ **契约：`on_ready` 必须与事件循环同线程。**
        #    它今天的实现是 `asyncio.Event.set`（单线程、同步、不会 yield），
        #    而 `schedule` 是**注入**的 —— 若有人把它换成**线程定时器**，
        #    这一句就**跨线程**了（而它**不会报错**，只会让桥**永远不醒**）。
        # ⚠️ **另一条契约（来自同一份评审）：`AsyncClient` 不许跨 `asyncio.run()` 复用。**
        #    httpx 的 client 绑在**它被创建时的那个事件循环**上；
        #    而 `asyncio.run()` 每次**新建一个循环、结束就关** ⇒ 跨着复用会在第二次炸。
        #    ⇒ **本份不做任何"多轮复用"**（`loop.py` 是纯状态机，它不持 client）；
        #      将来谁要复用，**复用的那个东西必须活在一次 `asyncio.run` 之内。**
        if self._on_ready is not None:
            self._on_ready()      # ⚠️ 语义是「**可以试着取了**」，不是「一定有货」

    # ── 交接 ────────────────────────────────────────────────
    def take_if_ready(self) -> list[WakeItem] | None:
        """同步取走就绪批；没有则返回 None，不阻塞。

接收方只拿到事件序列；触发方式由产生方决定，不随批次传给消费者。"""
        if self._taking or not self._ready or not self._queue:
            return None
        self._taking = True
        try:
            return self._take()
        finally:
            self._taking = False

    def _take(self) -> list[WakeItem]:
        """按条数和序列化字节预算取批；超大单项独占一批，剩余已就绪项继续交付。

        ⚠️ **这两句不一样：一句承重，一句是空操作。**

        **`self._first_at = None` —— 承重。**
        交付（`_deliver`）到取走之间**可能来新件** —— `ready` 是粘的、项留在队里直到被取，
        而那段时间里 `push` 会**重钉** `_first_at`（走 `is None` 那支）⇒ 走到这里它**可能非 None**
        不复位就会让下一批拿一个**过去的**首件时刻在算：
        `min(first_at+15000, max(first_at+0, last+2500))` 整个落在过去，
        再被 `max(0, at-now)` 夹成 **0 ms** ⇒ **上限／下限／安静三层同时失效**
        （Task 3 的评审用公开 API 实测：`min_batch_age_ms=5000` 时，
        一批 t=6000 首件的批在 t=10000 就 ready ⇒ **下限被违反 1000ms**；
        变异 M1／M8 都会红。）

        **`self._last_at = 0` —— 空操作，不是修复。**
        它唯一的读者是 `_arm`，而 `_arm` 只在 `push` 里被调、且**紧跟**在 `self._last_at = now` 之后
        ⇒ 陈旧值**永远读不到**（变异 M2 存活 —— **而那不代表它该删**，清干净是这里该有的形状）。

        ⚠️ **`_first_at` 那句现在**有**专门的钉子** —— `test_the_take_window_repins_first_at`（下面那条）；
        而 `_last_at` 那句**没有、也不可能有**（它的读者永远读不到陈旧值）。
        """
        entries = []
        used = 0
        for entry in self._queue:
            item, piggyback, size = entry
            if len(entries) >= self.opts.max_batch_size:
                break
            if entries and used + size > self.opts.max_batch_bytes:
                break
            # One oversized event is delivered alone, bounded by queue bytes.
            entries.append(entry)
            used += size
        batch = [item for item, _, _ in entries]
        queue_bytes = self._queue_bytes - used
        active_count = self._active_count - sum(not piggyback for _, piggyback, _ in entries)
        external_count = self._external_count - sum(
            not piggyback and item.origin == 'external' for item, piggyback, _ in entries)
        remaining = len(self._queue) > len(entries)
        # No event leaves the queue until callbacks that can fail have returned.
        # on_ready remains a notification: during this interval nested takes
        # return None and reentrant pushes are refused before admission.
        if self._timer is not None:
            self._cancel(self._timer)
            self._timer = None
        self._timer_token = None
        if remaining and self._on_ready is not None:
            self._on_ready()
        for _ in entries:
            self._queue.popleft()
        self._queue_bytes = queue_bytes
        self._active_count = active_count
        self._external_count = external_count
        self._ready = remaining
        self._first_at = None
        self._last_at = 0
        self._fixed_deadline = None
        self._only_fixed_windows = True
        return batch
