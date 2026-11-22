"""主循环：批与轮的状态机。

⚠️ **它不认识总线、模型、事件库** —— 那些都**从外面递进来**。
   与这一仓已经用过四次的同一个手法一致（`note.build` 注入 `estimate`／`timestamp_of`；
   `head` 有自己的 `Item`；`bus` 注入 `clock`／`schedule`／`cancel`）。

⚠️ **它从不调用 `asyncio.run`** —— 循环归**入口**所有，它只在里面跑。

⚠️ **喂给 `take_batch` 的那个东西，必须已经接上了「叫醒」** ——
   否则桥永远不醒、`bus.ready is True`、**零报错**（只有外层超时收得住）。
   这个前提在 `feed.py` 那边有测试钉着（`test_the_real_bus_wakes_the_real_bridge`）；
   而**在这一模块里，它是"外面递进来的东西"的契约**。
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence


def _say(message: str) -> None:
    """ **这一层的嘴**（2026-10-05 作者："加"）：只在**本来会静默收手**的地方说一句。

    ⚠️⚠️ **为什么值得动这个冻面**（一笔写清理由的改动）：从前这一层**没有嘴** ——
       那些"悄悄 return"的早退**既不做、也不出声** ⇒ 作者那一次（页面开着、推得出去、
       `batches` 停在 1、事件库一条没有、回话没有）就**查不动**：三处都空，而**没有一处
       说"我在这儿收手了"** 。 与本仓那条纪律相反（"接不上的要**吵**" ）。
     走 **stderr**：服务那条日志里当场看得见 ；而它**不进**事件库、**不进**会话 。
    """
    print(f"[loop] {message}", file=sys.stderr, flush=True)


@dataclass(frozen=True)
class RoundOutcome:
    """一轮跑完之后**交回来**的结果 —— 循环只读它，不解释它的内容。

    ⚠️ 这五个位**不是**"模型的返回"，而是**外面那一层对它的判读**：
       `called_tool` ／ `ended` ／ `overflowed` ／ `over_budget` ／ `failed`。
    """
    called_tool: bool = False     # 这一轮调了工具（⇒ 轮要接着跑）
    ended: bool = False           # 调了"收工"（⇒ 这一批完了）
    overflowed: bool = False      # 上游报输入超长（⇒ **只记一笔**，批末交接）
    over_budget: bool = False     # 这一轮之后发现太长了（⇒ **只记一笔**，批末交接）
    failed: bool = False          # 这一次请求最终失败了（⇒ 走层 B 的重试预算）
    terminal_failure: bool = False  # appended: preserve existing positional contract


class Loop:
    """批与轮的状态机。**只读外面递进来的东西，不解释它们的内容。**"""

    def __init__(
        self,
        *,
        take_batch: Callable[[], Awaitable[Sequence[Any] | None]],
        deliver: Callable[[Sequence[Any]], Awaitable[bool]],
        run_round: Callable[[int], Awaitable[RoundOutcome]],
        maintenance: Callable[[], Awaitable[None]],
        flush_handoff: Callable[[], Awaitable[None]], # 等 `handoff.py`（第三份，还没来）
        batch_end_check: Callable[[], Awaitable[None]],
        flush_boundary: Callable[[], Awaitable[None]],
        attach_hint: Callable[[], Awaitable[None]],
        run_tools: Callable[..., Awaitable[Any]] | None = None,
        # 兼容旧装配；执行入口只由 round.py 使用，这里不保存也不调用。
        sleep: Callable[[int], Awaitable[None]] = lambda ms: asyncio.sleep(ms / 1000),
        soft_rounds: int = 10,
        hard_rounds: int = 12,
        max_consecutive_failures: int = 2,
        max_failures_per_batch: int = 4,
        backoff_ms: Sequence[int] = (2000, 10000),
        # Operator 静候 retains input; it is neither ordinary idle nor stop.
        # Console also gates before session/ingress consumption. Generic Loop
        # callers receive the same no-discard/no-generation behavior here.
        gated: Callable[[], bool] | None = None,
        batch_finished: Callable[[Sequence[Any]], None] | None = None,
    ) -> None:
        self._outputs_bound = False
        self._started = False
        self._take_batch = take_batch
        self._deliver = deliver
        self._run_round = run_round
        # 上面那个接缝（ 缺省 `None` ⇒ 这一层的行为**逐字不变** ）
        # ＋ 关闸期间丢掉的那些条数 （ 页上那一格读的就是它 ）。
        self.gated = gated
        self.wait_paused = None
        self._batch_finished = batch_finished
        self.dropped_count = 0
        self._maintenance = maintenance
        self._flush_handoff = flush_handoff
        self._batch_end_check = batch_end_check
        self._flush_boundary = flush_boundary
        # ② 的投递口 —— **注入面是唯一的那条活路**。
        #    ⚠️ 这里**没有**同名方法（`flush_handoff`／`batch_end_check`／`_sleep` 也都没有）：
        #       曾经有过一个 `async def _attach_hint()`，而**这个名字被下面这一行整个遮掉**
        #       ⇒ 那个方法成了**死代码**：下一个想改"提醒怎么投递"的人会去改它，
        #       然后**什么都不会发生**（而拿掉这个实例属性之后，它的方法体会
        #       `await self._attach_hint()` ⇒ **自递归到 `RecursionError`**）。
        #       ⇒ 它在全支 fix round 3 被**删掉**了。谁要再加一个同名方法，先读
        #       `tests/test_loop.py::test_the_hint_hook_is_injected_and_no_method_shadows_it`
        #       （它会当场红）。
        self._attach_hint = attach_hint
        self._sleep = sleep # 层 B 的退避："等"靠它，别读成 `loop` 碰了时钟
        self._soft = soft_rounds
        self._hard = hard_rounds
        self._max_consecutive = max_consecutive_failures
        self._max_per_batch = max_failures_per_batch
        self._backoff_ms = tuple(backoff_ms)

        self.processing = False # 「我正在干活」—— 批边界那个记号
        self.generation = 0 # 每次「停」+1 —— 迟到的结果靠它判「这代还活着吗」
        self.sealed = False # 「往库里写」那个出口的**记号** —— 消费方是写库那层（见 seal()）
        self._running = True
        self._hint_generation = self.generation # 等一轮之前的**代**（回来的东西靠它判迟不迟到）
        self.handoff_requested = False   # ⑤⑥ 只"记一笔"
        self.hint_attached = 0           # 软上限处**到过**几次（把提醒真附上去由 `attach_hint` 做）
        self._consecutive_failures = 0   # 连败 —— **没失败的那一轮就清零**（清零那一步在 `_rounds()`）
        self._failures_in_batch = 0 # 这一批失败过几轮 —— **一批的开始就归零**（那一步在
                                         #    `process_one_batch()` 里，与「我正在干活」同一带）
        self._inherited_failures = False # **这一批开始时带不带连败** —— 层 B 退避的**判据**
                                         #    （在 `process_one_batch()` 的开头记下来；见那个方法）

    def remaining_rounds(self, number):
        """Current batch limit, exposed without extending it or scheduling work."""
        if type(number) is not int or number < 1: raise ValueError('invalid round number')
        return max(0, self._hard - number + 1)

    def bind_outputs(self, *, deliver=None, flush_boundary=None) -> None:
        """Complete application wiring once, before this loop can do any work.

        App needs this Loop's live gate to construct its stores. This explicit
        construction phase closes that cycle without exposing private slots.
        Validate the whole binding before changing either callback.
        """
        if self._outputs_bound or self._started or self.sealed or not self._running:
            raise RuntimeError("loop output binding is closed")
        for name, callback in (("deliver", deliver), ("flush_boundary", flush_boundary)):
            if callback is not None and not callable(callback):
                raise TypeError(f"{name} must be callable")
        if deliver is not None:
            self._deliver = deliver
        if flush_boundary is not None:
            self._flush_boundary = flush_boundary
        self._outputs_bound = True

    async def process_one_batch(self, batch: Sequence[Any]) -> None:
        """跑完**一批**。这一批的边界就是「置 `processing` ⇒ 干完 ⇒ 松开」。

        ⚠️ 这一带还有**另一个**动作：**把这一批的失败计数归零** —— 不归零的话 `_max_per_batch`
           量的是"这个 Loop 一共失败过几轮"（那会**随运行时间累积** ⇒ 第一波之后每一批都
           一碰失败就放弃，而且**不会报错**）。

        ⚠️ **层 B 的「带退避」就落在这里 —— 只在**失败那条路**上**（spec §一／§六 把"带退避"
           点名给这一层；`design 05` 记的触发条件 `0/429/≥500` 里 **429 就是限流**）：
           收批**之前**（`self._sleep(...)` 那一句）按 `backoff_ms` 等一次，而**走那条路的条件
           是"这一轮失败了"** —— 也就是下面 ⑦ 那一支判出"这一批不往下走"的时候。
           ⇒ 少了这一句，**「预算耗尽的收批」与「正常批末」在可观察上完全一样**
              （`backoff_ms` 就成了一个零读者的构造参数）；
           ⇒ 而**每一批开头都等**是错的读法：那**不是退避、是一笔恒定税** ——
              它让每一次**正常的**批都慢一档，而 spec 要的不是那个东西。
           ⚠️ **`loop` 不碰墙上时钟**：`sleep` 是**注入**的（默认 `asyncio.sleep`，
              与 `generate.py` 的同款参数一致）⇒ 测试递一个记账桩进来就能看见"等过多久"。
           ⚠️ 与 `generate.py` 那个**同名**参数的**口径不同，而这是应该的**：层 A 管
              「这一次请求要不要再试」，层 B 管「这一批还要不要往下走」
              （两处**触发条件**本来就不同；spec §六 那句"两处都判＝两处会走岔"防的是
               **判"什么错可重试"**，不是"两边同名参数必须同义"）。

        ⚠️ **`handoff_requested` 谁清**（ 这一条只在 gitignored 的报告里，所以写进树）：
           ① **成功交接之后由 `handoff.py` 清** —— 「记一笔」是**请求**，做完才不是请求
              （`loop` 不自己清：自己清掉的话，那次交接失败就**再也没人重试**）；
           ② **换代（`stop()`）之后自然不算数**：迟到的结果一律被丢弃（见 `_rounds()` 的判代），
              所以旧那一代留下的记号**没有消费者**。
           ⇒ 两种都**留给 `handoff.py`**：本模块**只立位、只读位**，"谁在什么时候清"归那条路。
        """
        self._started = True
        _say(f"批到手了（{len(batch)} 项）⇒ 先等维护、再推、再进轮")
        await self._maintenance()        # ⚠️ 先等维护收尾（万一交接/清空正做一半，别插队）
        self.processing = True
        self._failures_in_batch = 0 # 一批的开始 ⇒ 这一批的失败计数归零（见上面那段）
        self._inherited_failures = self._consecutive_failures > 0 # 层 B 退避的判据（见那个方法）
        try:
            # **推出去没有，要出声**（2026-10-05 作者："加"）：上一笔给 `_rounds()` 加了嘴，
            # 而它**连那一句都没轮到** ⇒ 所以问题在**这一步之前**（推的返回值 ／ 批有没有到手 ）。
            # 于是这一句就是那只手电：`delivered=True/False` 一出来，
            # "推不出去（⑧ 之前就停）"与"推得出去了而轮里没动"当场分得开 。
            delivered = await self._deliver(batch)
            _say(f"推给订阅者：delivered={delivered}")
            if delivered:
                await self._rounds()
                await self._flush_handoff()
                await self._batch_end_check()
            await self._flush_handoff()
        finally:
            self.processing = False # 异常路径上也要松开
        await self._flush_boundary()
        if self._batch_finished is not None:
            self._batch_finished(batch)

    async def _backoff_before_the_batch_would_end(self) -> None:
        """ **层 B 的退避**：**这一批因为失败要收了** ⇒ 在收它之前按 `backoff_ms` 等一次。

        ⚠️ 它**只在**⑦ 判出"不往下走"（`_on_a_failed_round()` 回假）那一条路上被调用 ——
           **正常批末不走这里**（这正是"退避"与"恒定税"的分界，见 `process_one_batch()` 那段）。
        ⚠️ 两个数都是**构造参数**里递进来的，判据是「**这一批是不是继承了连败**」：
           · 这一批开始时**没有**连败 ⇒ **基线那一档**（`[0]`）；
           · 这一批开始时**已经带着**连败 ⇒ **更长的那一档**（`[-1]`）——
             那说明**上一次收批也是因为失败**，对方多半还在限流。
            **判据读的是 `_inherited_failures`（一批开始时记下的那个布尔），不是现算的**：
              这一批**开始时**的连败是 0 还是 >0 —— `process_one_batch()` 一进来就把它记下来了。
        ⚠️ **为什么不能在**这里**读 `_consecutive_failures` 自己判**（这一处栽过一次）：
           `_on_a_failed_round()` 一回来就已经把**这一轮**的失败算进去了 ⇒ 那个数里
           **总是**含本轮这一次 ⇒ 它区分不了"继承来的"与"这一批自己败出来的"。
           实测（全支 fix round 4 复评）：判据写成 `> 1` 时，一个**什么都没继承的新批**
           只要在本批里连败两次，就会读到 `2` ⇒ **走长的那一档** —— 与这段的第一句相反
           ⇒ 现在改成读那个布尔：**"本批开始时的连败"才是它要问的东西。**
        ⚠️ **它自己等**（`await`），但**不碰墙上时钟**：`sleep` 是注入的。
        """
        if self._inherited_failures:
            await self._sleep(self._backoff_ms[-1])
        else:
            await self._sleep(self._backoff_ms[0])

    async def _wait_for_resume(self):
        while self._running and self.gated is not None and self.gated():
            if self.wait_paused is not None:
                if not await self.wait_paused():
                    return False
            else:
                await self._sleep(250)
        return self._running

    async def run(self) -> None:
        """Wait before taking input and again if the gate changed during take."""
        self._started = True
        while await self._wait_for_resume():
            batch = await self._take_batch()
            if batch is None:
                break
            if not await self._wait_for_resume():
                break
            await self.process_one_batch(batch)

    def _active(self) -> bool:
        """ **「还能不能往下走」** —— **今天读的是 `_running`**（`stop()` 置假的那个开关）。

        ⚠️ **它不是"判代"**：`generation` 是判「**迟到的结果属于哪一代**」的**基准**，
           而那件事的消费者在**迟到的那条路上** —— `_rounds()` 里 `await` **之前**存一份代、
           **回来**先比一次（见那一处的注释）。两件事的名字与关系写在这里，别读混。
        """
        return self._running

    async def _rounds(self) -> None:
        """一轮的循环 —— **八种收尾**判定都在这一层里。**② 是唯一不 `return` 的一种**
        （它只附一句提醒、接着往下判；⑧③⑥⑤①④ 直接 `return`，⑦ 视预算 `return` 或 `continue`）。

        ⚠️ **判代（迟到的东西不许写进来）** —— spec §九-⑤ 那句"证明迟到的东西不许写进来"
           的**见证就在这一层**：`await` **之前**把 `generation` 存进 `_hint_generation`，
           **回来第一件事**就是比一次；不等 ⇒ 这一个 outcome **整条丢掉**（`return`，
           **一个位都不读**，所以 `handoff_requested` 之类**不会被一个迟到的记号改掉**）。
           ⇒ 这一句是**可观察的**，不是声称：`await` 中间有人调了 `stop()`
              （＝换代），那个跑完才回来的 `RoundOutcome(over_budget=True)` 就**不再落记号**
              （`tests/test_loop.py` 的 `test_a_late_outcome_after_a_stop_is_discarded`）。
           ⚠️ 它与 ⑧ **不是一件事**：⑧ 判的是**下一轮开头**还要不要跑（读 `_running`）；
              判代管的是**已经发出去的那一轮**回来时算不算数。少了判代那一步，⑧ 是拦不住
              一个"回来时已经换了代"的结果的 —— 它在 `await` **之前**就已经判过了。

        ⚠️ **⑥ 必须先于 ①** —— 由 `tests/test_loop.py` 那条
           `test_the_overflow_check_comes_before_the_tool_call_check` 钉着：
           两者同时成立时，⑥ 先判才**记得上那一笔交接**，① 先判就当场把它收掉。
        ⚠️ **⑤ 同理，必须先于 ① 与 ④** —— **次序**只有那条钉子
           `test_exit_5_marks_the_handoff_before_any_later_check_can_return` 钉着
           （依次断言两半：对 ① 的半 1、对 ④ 的半 2）；而 `test_exit_5` 量的是
           "只记一笔、批末交接" —— 把 ⑤ 挪回 ①④ 之后，它**照旧绿**。
           「记一笔」**先落**，然后才是"这一轮收不收"。否则 `over_budget` 撞上
           「没调工具」或「正好是硬上限那一轮」时，**那一笔会被静默丢掉**。
           ⚠️ 它排在 ② **之后**（而 ② 不是 `return`、抢不走那一笔）—— 这样
              "既是软上限又超长"的那一轮，**提醒与那笔交接两样都不丢**。
        ⚠️ ⑤ 这一处**改动了 brief 的次序**（brief 把它放在最后）—— 理由与两半的证据
           见 Task 2 报告 §Fix round 4（`plan-mandated` 的更正）。
        """
        for round_no in range(1, self._hard + 1):
            if not self._active():                   # ⑧ 停过 ⇒ 当轮收手（判的是 `_running` 那个开关）
                # **说一句再走**（2026-10-05 作者："加"）：从前这里**悄悄 return**
                # ⇒ 外面看着就是"批推出去了、然后什么都没发生"（三处都空 ）——
                # 今天那一跳就是被这一句瞒住的 。 停机是**正常**动作 ，但"我在这儿
                # 收手了"必须**看得见** （不然"没跑"与"链断了"分不开 ）。
                _say(f"第 {round_no} 轮前收手：_running={self._running}（⑧ 停过）")
                return
            self._hint_generation = self.generation # 判代的基准 —— 必须在 `await` **之前**取
            outcome = await self._run_round(round_no)
            if self.generation != self._hint_generation:
                _say(f"第 {round_no} 轮的结果**迟到**（换代了）⇒ 整个丢掉") # 同一条纪律
                return # 迟到：这一代已经换过 ⇒ 整个 outcome 丢掉

            if outcome.terminal_failure:
                self._consecutive_failures += 1
                self._failures_in_batch += 1
                _say('本轮处理失败；错误不适合自动重试，请查看诊断')
                return
            if outcome.failed:                       # ⑦ 连败 ⇒ 层 B 的预算见 `_on_a_failed_round()`
                if not await self._on_a_failed_round():
                    await self._backoff_before_the_batch_would_end() # 层 B 的退避
                    return
                continue
            self._consecutive_failures = 0 # 没失败的那一轮 ⇒ 连败清零（⑦ 的闸看的是连败）

            if outcome.ended:                        # ③ 收工了
                return
            if outcome.overflowed:                   # ⑥ 上游报超长 ⇒ **只记一笔**
                self.handoff_requested = True
                return
            if round_no == self._soft:               # ② 软上限 ⇒ **不结束**，只附一句
                self.hint_attached += 1
                # ⚠️ 2026-10-07：**没接就不附** —— 原来这一处会当场抛 ⇒ 整批失败
                #    （真跑量到：NotImplementedError: 把提醒附上去 还没接上）
                # 而那一格是提醒（可选的），不是承重的：不附与炸是两件事。
                try:
                    await self._attach_hint()
                except NotImplementedError as why:
                    import sys as _s
                    print("   [hint] 那一句提醒今天还没接上（照旧往下走）："
                          + str(why)[:120], file=_s.stderr, flush=True)
            if outcome.over_budget:                  # ⑤ 太长了 ⇒ **也只记一笔**
                self.handoff_requested = True # **记号先落**，再收这一轮
                return
            if not outcome.called_tool:              # ① 没调工具 ⇒ 这一轮完了
                return
            if round_no >= self._hard:               # ④ 硬上限 ⇒ 强制收
                return

    async def _on_a_failed_round(self) -> bool:
        """⑦ 一次失败之后：还继不继续。**层 B 只做两件事 —— 数这一批的失败数、数连败。**

        ⚠️ **它自己那一下不退避**（别把摘要读成"它会等一下"）：`backoff_ms` 的那个"等"
           **不在这个方法里** —— 它在**它的调用方**：`_rounds()` 里 ⑦ 那一支，
           **判出"不往下走"之后就 `await self._backoff_before_the_batch_would_end()`**
           （尺度由构造参数 `backoff_ms` 递进来；`loop.py` 不碰墙上时钟、测试也不许等）。
           ⇒ 分工是：**这个方法只判"还继不继续"，"等多久"由那一支按判出来的结果去等**。
           ⚠️ **两个返回值里只有一个是"要等"**：回真 ⇒ 继续跑（**不等**）；
              回假 ⇒ 这一批要收 ⇒ **收之前等一次**。
           ⚠️ 而**正常批末**（③①④ 那几条路）**一次都不等** ——
              否则那笔"等"就成了一笔**恒定税**（每一次正常的批都慢一档），而不是退避。

        ⚠️ **重试分两层，别混：**
           层 A（`generate()` 内部）管「**这一次请求**」要不要再试 —— 它靠 `exc.retryable` 判。
           层 B（**这里**）管「**这一批**」还要不要往下走 —— 它**只数连败 ＋ 退避**，
           **绝不判"什么错可以重试"**（那是 `generate()` 的事；两处都判 ＝ 两处会走岔）。

        ⚠️ **本层今天真在做的只有"数"** —— 两道预算：一批里的失败总数（`_max_per_batch`）
           与连败（`_max_consecutive`）；**退避那个"等"在调用方那一句**
           （`_backoff_before_the_batch_would_end()`，`sleep` 是注入的）⇒ 写清这一条，
           免得上面那句"退避"被读成"它在这个方法里 sleep 了"。

        ⚠️ **"连败"怎么才算连败**：把连败抹平的那一步**不在这个方法里** —— 它在 `_rounds()` 里
           （**没失败的那一轮**把 `_consecutive_failures` 清零）。少了那一步，这里量的就是
           "**这一批里失败过几轮**"，而那是**另一件事**（两者给出的放弃时机不同）。

        ⚠️ **两道闸的复位点不一样**（别把它们读成同一件事）：**这一批的失败总数按批复位**
           （`process_one_batch()` 的开头），而**连败只有"没失败的那一轮"才被抹平** ——
           **跨过批边界仍然是连败**。⇒ 两条预算量的是两个不同的东西，这也是它们各响各的前提。
            **而"跨批仍是连败"还有一个代价，记在这里别让它无声**：上一批以连败收尾 ⇒
              这一批**第一条失败**就把它再推一格，那时 `_consecutive_failures` 已经 > 1
              ⇒ 退避走 `_backoff_ms[-1]`（更长的那档）—— **上游持续 429／5xx 时，
              节奏由这个数决定**，而不是零延迟热循环。
              （这正是"带退避"存在的理由：spec 记的触发条件 `0/429/≥500` 里 429 就是限流。）
        """
        self._consecutive_failures += 1
        self._failures_in_batch += 1
        if self._failures_in_batch >= self._max_per_batch:
            return False
        if self._consecutive_failures >= self._max_consecutive:
            return False                    # 连败够多 ⇒ 这一批结束（不再往下跑）
        return True

    def stop(self) -> None:
        """ **一个动作**：关开关 ＋ 换代。

        ⚠️ **spec §五 的「停」是五件事，这里写清每一件今天在哪**（本模块只做前两件）：
           ① **关开关**（`_running`）—— 就是下面这一行；
           ② **换代**（`generation += 1`）—— 下面那一行；而**换代的消费者**是 `_rounds()`
              里那道判代（`await` 之前存、回来先比）⇒ 迟到的结果**一个位都写不进来**；
           ③ **「取消当前那一轮」—— 没有单独的名字，现在补上**：
              **本轮不取消、跑完才收**（轮首那道检查在 `_rounds()` 的循环开头）——
              也就是说 `await self._run_round(...)` 那一下**会跑完**，回来才被丢弃；
              **真正的取消留给入口**（与下面那条同一条理由：`loop.py` 是纯状态机，
              它不该去管"谁在跑我"；取消要持有 task 句柄）。
              ⇒ 这件事**被 `test_exit_8_a_stop_ends_the_rounds` 的形状默认了**
                 （它在**第一轮之前**停，量的正是"当轮就不许再起"）——
                 而那**不**等于"停会打断正在跑的那一轮"。
           ④ **「叫醒正在等的那一个」** —— 异步下这**不再**是"把定时器叫停"，
              而是**取消一次正在 `await` 的等待**（`CancelledError`）。
              而那**需要循环持有自己那个 task 的句柄** ⇒
              **⇒ 本份先把这个形状记下来，实际的取消留给入口**（`loop.py` 是纯状态机，
              它不该去管"谁在跑我"）。**⚠️ 这一句是 spec §五 明确要求的一件事，不许丢。**
           ⑤ **给"没闭合的调用"补一条「被中断」** —— **点名交给 `handoff.py`**（第三份）：
              循环**不认识"调用"**（它只按形状用外面递进来的东西），那件事的概念属于事件库／交接。
           ⇒ ① ② 在本模块；③ ④ 的"做"在入口（本模块只把形状写明）；⑤ 在 `handoff.py`。
              ⚠️ 一件写在规格里、而清单上只有它旁边的两件 —— **没有名字的东西事后无法被清点**
                 （③ 就是这么找到的：它一直没被点过名）。
        """
        self._running = False
        self.generation += 1
        self._retire_batch_identity()

    def _retire_batch_identity(self):
        # Synchronous revocation also invalidates previously held lease objects.
        self._identity_retired = True
        lease = getattr(self, '_batch_identity_lease', None)
        if lease is not None:
            lease.revoke()
        self._batch_identity_lease = None

    def seal(self) -> None:
        """ **一个记号 ＋ 谁消费它**（spec §五 那个「保证」的形状）：
        把「往库里写」这个出口**标成封住** —— 迟到的东西只能在内存里结束。

        ⚠️ **今天它只是一个记号 —— 它的消费者是"写库那一层"**（`events.py` ＋ `log.py`
           **已经在树里了**，缺的是**接线**：把这条路连到 `seal()` 读的那个位上）。
           本模块**不假装**自己是那个出口：它只把这个位立起来，**谁在什么时候消费它**由那一层定
           （与 `handoff_requested`／`hint_attached` 同一个形状：先立位，别 invent 语义）。
           ⇒ 准话是「**写库层还没接上**」，不是「还没做」—— 两件事，别混。
        ⚠️ 它**不**换代：那是「停」的事（两件事，别混）。
        """
        self.sealed = True
        self._retire_batch_identity()
