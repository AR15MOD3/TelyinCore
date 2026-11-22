# telyincore/pacing.py
"""「谁在滴答」—— 闹钟表（`timers.py`）的**驱动**：按"最早到期"睡，到点去滴一下。

 它接的是 §8.8 那半句话留下的接缝（`timers._OWED["due_driver"]`）：
  `08-定时器与心跳.md:167-169` 说「那个'一直转、隔一会儿看一次'的东西由事件循环负责」，
  而 `timers.py` 只有**一张快照 ＋ `due(now)`** —— **没有任何循环会自己来调它**
  ⇒ 本模块就是那个"到点去滴一下"的人。⚠️ 而它**不是**那个"打点器"：
  正常时按最早到期时间等待。写盘或 SQLite 暂时不可用且调度表没有推进时，
  以 1、2、4…60 秒退避重试；有后续到期时间则优先在那个时刻重算。
  重试只处理仍在调度表里的任务，不重建已消费的提醒或重放不确定的发送。

── 四条判据（计划 Task 2 那四条，逐条落在下面） ──────────────────────
① **按"最早到期"睡**：`next_delay(table, now)` 就是那个决定 —— 差多久 ⇒ `asyncio.sleep`
   那么久；**表空 ⇒ `None`**（"排不出下一次"）⇒ 等一次 `wake`／`stop`：
   **一次都不滴、连时钟都不问**（与 `timers.due` 的"空表连时钟都不问"同形）。
   ⚠️ 固定间隔轮询在这一层是**不成立的形状**：它的答案与表里有什么**无关**，
   而"空烧"恰恰是那种"看不出错"的坏。
② **中途被打断要重算**：谁动那张表（新加／取消），谁 `wake.set()` ⇒ 当次的睡眠
   **当场被叫醒**、下一轮从表重新算起。少了它，一只**更早**的新闹钟会被挡在
   旧闹钟的后面 ⇒ **晚醒**。
③ **`due()` 抛了，循环仍然活** —— 而那次失败**可见**（`on_error`）。
   这是 §十五 里那条 **「第四条接缝」**：`timers.due` **跑完一整趟才重抛第一只**
   （它为了让"排在后面的那些"照样响）⇒ 那一趟的失败**没有别人接**，
   **活不活只能由驱动方裁定**。 **本刀的裁定是"活"**：每一只闹钟是独立的一笔
   —— 一只炸了不许把别的那些带走。而"活"**不等于"咽下去"**：
   那次失败从 `on_error` **交出去**（它就是这条接缝的下游）。
④ **时钟注入**：`now` 一律来自注入的 `clock`，而 `due(now)` 收的**就是那一个**
   （不让它自己去问时钟）。而"等"用的是 `asyncio.sleep`（**不是**墙上时钟：
   测试里从不真的等它 —— 闹钟定在"已经过了"的时刻，第一轮就响）。

── 封着的那本表：**收工**（裁定 ＋ 理由） ─────────────────────────
`timers.py` 的门卫让 `due()` 在一本**封着的**表上**当场抛**（不是回 `[]`）。
而"中途封库"是**可达的**：一次 `seal()` 正好落在某只 handler 的 `await` 期间 ⇒
那一趟 `due` **当场收手**，**没轮到的那些原样留在表里**（内存与盘都在，刻意的）
⇒ 本驱动的**下一轮**必然撞上门卫。本驱动的处置是：

    **认这一眼、收工**（返回 `"sealed"`），而那次 `SealedError` 从 `on_error` 交出去。

理由：**封库 ＝ 关机** ⇒ 从此**一只都不会再响**（`due` 是写口：第一句就抛，
到点的那些既不会被取走、handler 也不会跑）⇒ 继续转下去只会在每一只闹钟的时刻上
**撞同一堵墙** —— 而那是空烧的另一面（判据① 禁的那件事）。
⚠️ **它认的是类型（`SealedError`），不是"这一本书的门卫"**（M1）：今天只有一把门卫
（`app.build()` 造的那一个，两本 store 共用）⇒ 一个**别处**封了的库从 handler 里抛
`SealedError` 会**误判**（驱动在库还开着的时候收工）；今天不可达，写在这里等那一天。
而**不是**"把表当成空的"：留在表里那些**要跨重启活着**（下一次开机它们还会响）
—— 本驱动**不替它收拾**（不 `cancel`、不 `clear_all`）。
⇒ `tests/test_pacing.py` 里两条钉它：一条直接封库，一条**正好封在 handler 里**。

── 四个注入面（**一个默认值都没有**，理由在 `tests/test_pacing.py` 那条里） ──
`clock` ／ `stop` ／ `wake` ／ `on_error` —— 照 `seal.always_open` 那条点过名的活陷阱：
**"没有出口"与"出口恒不做声"在签名上长得一样** ⇒ 这一层不许兜默认值
（`on_error` 兜一个 `None` ⇒ 判据③ 整条可以不存在；`wake` 兜一个内部的 ⇒ 判据② 同）。
· `clock`：**看几点**用它——`due(now)` 收的就是它读出来的那一串；
· `stop`：**收工信号**（谁起这个 task，谁置它）。置上之后**一轮都不再滴**：
  关机路上那一滴会让一只闹钟**在关机中被叫走**（取走 ＋ 落盘 ＋ 跑 handler），
  而盘上那张表正是"下次开机看到的东西"；
· `wake`：**"表变了"的一次性提示** —— 本驱动**每一轮开头把它清掉**
  （它只是"重算一下"这个提示，不是一笔要说清的数据：驱动反正每一轮都重读表）。
  ⚠️ **清得并不丢事**：清完之后、到进等待之前**没有 `await`**
  ⇒ 那一段里到的 `wake` 要么已被读表看见、要么留在事件上被那次等待接住；
· `on_error`：**本模块不抛的错**都从这里出去（**它是同步的**：一个会挂起的报告口
  会把"这一趟失败"变成"这一趟卡住"）。它收两样，而两样**各一条、不重放**：
  ①每一次失败的滴答（**循环照旧活着**）；②**收工那一次**（封库 —— 那不是失败，
  而它同样是一句欠主的话："从此一只都不会再响"）。

⚠️ **`store.list()` 与 `clock()` 自己抛的东西本模块不接**：那两样是**注入面的错**
（表读不动 ／ 时钟坏了），不是"一次失败的滴答"⇒ 它会把这条 task 带走。
 **而"带走"必须有下文** —— 见下面单独那一段（R2 更正的正是"看得见"这三个字的**出处**）。

⚠️⚠️ **"带走"不许等于"静默"（R2）** —— 本模块不接那笔错，而它**必须有人读**：
  那个读者是**起这条 task 的那个人**（本模块不造 task、不持句柄 —— 句柄归主人，
  见下面的归属那一节）。 而"那儿躺着一个 `task.exception()`"**本身不是**看得见：
  `app.App` 那一半**实测**过（评审）—— `stop()` 里那句 `_retrieve` 是**取走、不抛**，
  而 `App` 又持着那条 task 的强引用 ⇒ 连解释器自己那句
  `Task exception was never retrieved` 都不会响 ⇒ **一条死掉的驱动与一台健康的机器
  长得一模一样**（`loop.py:60-63` 禁的形状：一个坏了却看不出来的东西）。
  ⇒ 今天那个读者是 `app.App`：它在起这条 task 时挂一个 done-callback
  （`App._note_pacing_death`），把那笔错收进 `App.pacing_errors` ——
  **与判据③ 的每一次失败同一个出口**（读的人只读那一个面，不必再去看那条 task）。
  ⚠️ 而**本模块仍然不替它兜**：兜了就等于把"装配坏了"读成"一次失败的滴答"
  （那两件事的下场不同：前者带走了 task，后者只是报一声、循环照旧活着）。

── 归属：**`app.py`**（本刀第 3 刀） ────────────────────────────────
⚠️ **它现在有生产调用者了**（ 本刀第 3 刀接上的）：`app.App.run()` 用
`asyncio.create_task(drive(...))` 起它、`app.App.stop()` 置那个 `stop` 并**有上界地**等它收手。
⚠️ **而本模块仍然不造 task、不持句柄**：句柄归主人 —— `app.App` 今天持着**两个**
（`loop.run()` 那一个，与这一个，见 `App._pacing_task`）。 而"谁动那张表谁置 `wake`"
那一半只到「一扇门」这一层：见 `_OWED["wake_setter"]`（门是 `App.arm` ／ `App.disarm` ——
★ **2026-10-12 起它有一个调用方了**：它定闹钟那一手走 `App.arm`，见 `launch._set_timer`）。

── 不属于本模块的（刻意不做） ──────────────────────────────────────
· **不登记到期处理器**（`store.on_due(handler)`）—— "响的时候做什么"不是"什么时候响"；
  而缺了它，`due()` 在一只有到点的闹钟上抛 `NoDueHandlerError` ⇒ 本驱动把它当
  **一次失败的滴答**报出去（判据③）、停到下一次变化（见 `_OWED["due_handler"]`）；
· **不 `set` `wake`**（谁动那张表谁置 —— 见 `_OWED["wake_setter"]`）；
· **不做心跳的规则 ／ `schedule_wake` 的编排**（S8-B）· **不做 wake view**（d）；
· **不碰 `timers.py`**（逐字节禁改）。
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Literal

from .seal import SealedError
from .timers import Timer, TimerStore
from .time_order import instant

#: 收工的**两个原因**（机器名 —— 断言读的就是它们，而措辞改动不该让谁变红）。
StopReason = Literal["stop", "sealed"]

#: **本模块欠着的那几样**（名字 → 欠主）—— 一条不落。
#: 判据是 `loop.py:60-63` 那条家规的另一面：**留空可以，但"空了"这件事必须有人
#: 说得出来**（散文里的东西不会自己冒出来）。照 `timers._OWED` ／ `unresolved._OWED`
#: 的成法：键是**稳定的机器名**（改措辞不会红），值里必须**点名**（空话会红）。
#: ⚠️⚠️ **"今天仍然欠着"的那几笔 ＝ 本表的键 减去 `_OWED_PAID`**（R4 的更正）：
#:   旧写法把两笔**已经还掉的**留在表里、只在值里解释"它其实还了" ⇒ 这张表的契约
#:   （"这些是欠着的"）被它自己的两行推翻，而那条按名字钉的判据只查"名字在不在"
#:   ⇒ 「**还清就删条目**」这条规矩**一个地方都没有在活着**。⇒ 今天分两处写：
#:   本表是**账**（值里点名 ＋ 那两条归属事实），`_OWED_PAID` 是**裁定**（哪几笔到了账）。
_OWED: dict[str, str] = {
    "wake_setter": "**凡调 `store.set` ／ `cancel` ／ `clear_all` 的人都要跟着 `wake.set()`**"
                   " —— "
                   "`timers.TimerStore` 那三条**只交出结果、不通知任何人**"
                   "（那一笔账在 `timers._OWED['rearm']` 里）⇒ 漏了那一半，**两个面都是死的**："
                   "① 驱动正睡在**某只计时器**上时，一只**更早**的新闹钟要等旧的那只到点"
                   "才被看见（**晚醒**）；"
                   "② **表空时（或刚因为 `NoDueHandlerError` 停下时）它一个计时器都不排**"
                   "、只等 `wake` ／ `stop`（见 `drive` 的第 ③ 步与 `_race`）⇒ "
                   "**一台闲着的机器上，第一只闹钟永远不响**（除非有人置 `wake` —— "
                   "⚠️ **`stop` 不算**：它只把循环叫停，那一只**照样没响** ）。"
                   " ★ **2026-10-12（作者：「一起解开」）：那一扇门有调用方了** —— "
                   "`set_timer` 那一段执行段接上了（入口 `launch` 的 `remember_handlers`），"
                   "而它**走 `App.arm`**（见 `launch._set_timer`：写表 ＋ 置 `wake` 一次做完）"
                   "⇒ 「谁动表谁置 `wake`」这一条在**它定的那一手**上成立。"
                   "⚠️ **仍然为真的两半**：① **机器自己那几手**（心跳那一只、闸门那一只）"
                   "写在**滴答那一层**里 —— 它自己就是读者（每 20 秒 `due()` 一次）"
                   "⇒ 它们不必置 `wake`（那一处「直接碰表」是有意的，不是漏的）"
                   "⚠️ **`clear_all`（`timers.TimerStore.clear_all` 那一条）今天连门都没有**"
                   "（Minor 5）："
                   "它同样是**动表** —— 整张清掉 ⇒ 当次那一觉睡着的可能就是**刚被清掉的那一批**"
                   "（醒得晚不晚是另一回事，而「表变了没人知道」这一条与 `set` ／ `cancel` 一字不差）"
                   "，而 `App` 上**没有与它成对的那扇门** ⇒ 谁第一个要用它，谁就得**先**在 "
                   "`App` 上开一扇（照 `arm` ／ `disarm` 的形状：`clear_all()` ＋ `wake.set()`），"
                   "**别直接调 `store.clear_all`**",
    "due_handler": "装配那一层（`app.build()`）—— **本笔已还**（见 `_OWED_PAID`）："
                   "`timers is not None` 时它调 `store.on_due(app._on_due)`。"
                   "⚠️ 本模块**不登记**到期处理器："
                   "`store.on_due(...)` 是那张表的接口（「响的时候做什么」），不是这一层的。"
                   "⚠️ 缺了它，`due()` 在一只有到点的闹钟上抛 `NoDueHandlerError`"
                   " ⇒ 本驱动把它当一次失败的滴答报出去、停到下一次变化（不会静默）。"
                   "⚠️ 而**醒来之后做什么仍然是欠着的**（§8-B 的编排 ／ §8.5 的 wake view）"
                   "—— 那不在这一笔里，见 `app.App._on_due`",
    "task_owner": "`app.py` —— **本笔已还**（见 `_OWED_PAID`）：`App.run()` 用 "
                  "`asyncio.create_task(drive(...))` "
                  "起它、`App.stop()` 置 `stop`（那两样在 `App._pacing_task` ／ "
                  "`App.pacing_stop` 上）。⚠️ **本模块仍然不造 task、不持句柄** —— "
                  "句柄归主人这一条没有变。 **而「句柄归主人」的另一半是「下场也归主人」**"
                  "（R2 补上的）：谁起这条 task，谁就看它带回来的那笔错 —— 今天那是 "
                  "`App._note_pacing_death`（那笔错落进 `App.pacing_errors`，见模块 docstring "
                  "里「带走不许等于静默」那一段）",
}

#: **已经还掉的那两笔**（名字 —— 与上面那张表的键同名）—— R4 的裁定面。
#: ⚠️ **留着它们不等于"没还"**：那两条值里写着**今天归谁**（`app.build()` 登记到期
#:    处理器；驱动那条 task 的下场归 `app.py`）—— 那是下一个人要读的事实，删掉就只剩散文。
#:    ⇒ 于是把「欠不欠」从值里**提出来**，放成这一行机器事实。
#: ⚠️ 判据在 `tests/test_pacing.py`：**两边都判** —— 还掉的必须在集合里，而
#:    `wake_setter`（今天**仍然欠着**：`set_timer` 那段执行段还没有人接）**不许**被标进去。
_OWED_PAID: frozenset[str] = frozenset({"due_handler", "task_owner"})


class PacingError(RuntimeError):
    """**照现在的表排不出下一件事** ／ **滴了一趟而表一步都没动**。

    ⚠️ 两种形状，而它们只有一个出口（`on_error`）：
      · 表里那一只的到点时刻**读不成时刻**（`datetime.fromisoformat` 收不下它）——
        ⚠️ **那一位可能是 `at` 或 `now`**：`_moment` **两个都跑**（`next_delay` 把它们
        相减 ⇒ 两边各自都可能读不出来，而那句话只点 `at` 就少说了一半）；
      · 一趟滴答之后**表一步都没动**，而表说"此刻就该响"。
    ⚠️ 为什么是抛、而不是就地想办法：
      · **跳过那一只** ⇒ 一只闹钟**静默地永远不响**（"它没到点"与"它被跳过了"分不出来）；
      · **按 0 秒再来一次** ⇒ 每一圈都滴一次，而表一步都不动 —— 那就是**空烧**
        （判据① 禁的那件事的另一面）。
      ⇒ 报一次，然后**停到下一次变化**（表变了才有新的账可算）。
    ⚠️ `RuntimeError` 的子类（照 `seal.SealedError` ／ `timers.NoDueHandlerError`）：
       调用方要能**只接这一类**、而放过别的 `RuntimeError`（本仓不许按措辞分支）。
    """


def _moment(stamp: str) -> datetime:
    """按存储层的规则读取 ISO 时刻；没有偏移量时按本地时区解释。"""
    try:
        parsed = instant(stamp)
    except (ValueError, OverflowError, OSError) as exc:
        raise PacingError(f"这个时刻读不出来：{stamp!r}（{exc}）") from exc
    if parsed is None:
        raise PacingError(f"这个时刻读不出来：{stamp!r}")
    return parsed


def next_delay(table: Sequence[Timer], now: str) -> float | None:
    """**这一趟该睡多久** —— 表里最早那一只还差几秒；表空 ⇒ `None`；到点了 ⇒ `0.0`。

    ⚠️ 传进来的 `table` 是 **`TimerStore.list()` 那个次序**（按到点时刻排）：
       "最早"就是**排在最前的那一只** —— 本模块**不另立一套排序**（`timers.py` 那句
       "§8.4 只让最早到期的那一个持有闸门"说的是同一个次序）。
    ⚠️ 它**不读时钟**：`now` 是递进来的（判据④ 的第一句就是这个）。
    ⚠️ 读不出来的时刻 ⇒ `PacingError`（不跳过、也不按 0 糊过去 —— 理由见那个类）。
    ISO 时刻统一到 UTC 后相减，与 TimerStore 的排序、到期判断保持一致。
    """
    if not table:
        return None
    remaining = (_moment(table[0].at) - _moment(now)).total_seconds()
    return max(0.0, remaining)


async def _race(wake: asyncio.Event, stop: asyncio.Event, delay: float | None) -> None:
    """等三样里的**第一件**：「`delay` 到了」（给了它的话）／「`wake` 被置」／「`stop` 被置」。

    ⚠️ **"谁先到都算"就是判据②的全部**：只等那个 `sleep` 的话，一只更早的新闹钟
       得等旧的那只到点才被看见（**晚醒**）。
    ⚠️ **收尾要自己做**（`cancel` ＋ 把它们收回来）：留着没跑完的等待子任务，
       解释器会在回收时说一句 "Task was destroyed but it is pending" ——
       而那是一条**与现场无关**的噪音。`delay is None` ⇒ 不排那个计时器
       （"表空"／"排不出来"那两条路上**一个计时器都不许有**：那正是"不空烧"的形状）。
    """
    waiters = [asyncio.ensure_future(wake.wait()), asyncio.ensure_future(stop.wait())]
    if delay is not None:
        waiters.append(asyncio.ensure_future(asyncio.sleep(delay)))
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for pending in waiters:
            pending.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)


def _ids(store: TimerStore) -> list[str]:
    """手上这张表里现在有哪几只（**次序也看**）—— "这一趟动了没有"就看它。

    ⚠️ 它**不是**"响了几只"的另一种说法：它的用处只有一个 —— 一次滴答之后表
       **一点都没动**时，再滴一次是**同一个结果**（同一个时刻、同一张表）
       ⇒ 那不是"再试一次"，那是空烧。
    """
    return [t.id for t in store.list()]


_driver_owners = {}


async def drive(store: TimerStore, *, clock: Callable[[], str],
                stop: asyncio.Event, wake: asyncio.Event,
                on_error: Callable[[BaseException], None]) -> StopReason:
    """One local driver per canonical timer table; retain ownership through cleanup.

    Cross-process writes remain guarded by TimerStore's source lease and stale
    snapshot checks. This local guard does not claim a distributed scheduler.
    """
    import os
    # Registry mutation has no await; the lock also covers distinct loop threads.
    guard = _driver_guard
    root = getattr(store, 'root', None)
    key = ('path', os.path.normcase(str(root.resolve()))) if root is not None else ('object', id(store))
    token = object()
    with guard:
        if key in _driver_owners:
            raise PacingError('timer table already has an active driver')
        _driver_owners[key] = (token, store)
    try:
        return await _drive_owned(store, clock=clock, stop=stop, wake=wake, on_error=on_error)
    finally:
        with guard:
            if _driver_owners.get(key, (None,))[0] is token:
                del _driver_owners[key]


import threading as _threading
_driver_guard = _threading.Lock()


async def _drive_owned(store: TimerStore, *, clock: Callable[[], str],
                stop: asyncio.Event, wake: asyncio.Event,
                on_error: Callable[[BaseException], None]) -> StopReason:
    """**一直转**（直到 `stop` 被置，或者库封了）：按"最早到期"睡 ⇒ `due(now)` ⇒ 再算。

    返回值是**收工的原因**：`"stop"`（主人叫停）／`"sealed"`（封库 ⇒ 再无一只会响）。
    ⚠️ 返回值给的是"**它为什么不再转了**"的机器名；而**封库那一次同时还从
       `on_error` 出一声** —— 一个不读 `task.result()` 的主人也不会错过它。

    ⚠️ **一轮的五步，各自对应一条判据**（下面代码里逐处点着）：
       ① `stop` 已置 ⇒ **一轮都不滴**（第 ⑤ 步才滴）；
       ② `wake.clear()`：收下"表变了"这个一次性提示，然后**重读表**；
       ③ 表空 ⇒ 等一次 `wake`／`stop`：**不问时钟、不滴答**（判据①）；
       ④ 用 `clock()` 算 `next_delay`：还早 ⇒ 睡多久就睡多久，**被打断就重算**（判据②）；
       ⑤ 到点 ⇒ `due(now)`：抛了 ⇒ 报出去而循环照旧活着；表没动时，
          存储错误有界退避，其他错误等变化或下一只尚未到期的闹钟。

    ⚠️ 它是**协程**，不是 task：**句柄归主人**（`app.py`，见模块 docstring 那一节）。
    """
    retry_delay = 1.0
    while not stop.is_set():                     # ① 主人叫停 ⇒ 一轮都不滴
        wake.clear()                             # ② 收下"表变了"（丢不了事，见模块 docstring）
        table = store.list()                     #    每一轮都**重读表**：没有陈旧的决定
        if table:
            now = clock()                        # ④ 这一轮的时刻：**注入的那个**（判据④）
            try:
                delay = next_delay(table, now)
            except PacingError as exc:
                on_error(exc)                    #    排不出来 ⇒ 报出去（可见），别空转
                await _race(wake, stop, None)    #    …然后等一次变化
                continue
        else:
            delay = None                         # ③ 空表：不问时钟、不滴答（判据①）
        if delay is None or delay > 0:           #    表空 ／ 还早 ⇒ 睡到那一刻为止
            await _race(wake, stop, delay)       # ② 被 `wake`／`stop` 叫醒 ⇒ 下一轮重算
            continue
        # ⚠️ 走到这里 ⇒ 表非空 ⇒ 上面那一支一定给过 `now`（`""` 那种兜底是假话，不要）。
        before = _ids(store)
        failure = None
        try:
            await store.due(now)                 # ⑤ 到点了：滴一趟
        except SealedError as exc:
            # **封库 ⇒ 收工**（本刀的裁定，理由在模块 docstring 那一节）：
            #    从此**一只都不会再响**（`due` 是写口，第一句就抛）⇒ 接着转只会在
            #    每一只闹钟的时刻上撞同一堵墙 —— 那是空烧的另一面。
            #    ⚠️ 而表里那些**原样留着**（跨重启的那一半）：本驱动不替它收拾。
            #    ⚠️ 这一声同时从 `on_error` 出去：收工**不许无声**。
            #    ⚠️ 而它认的是**类型**（`SealedError`），不是"这一本书的门卫"：今天只有
            #       一把门卫（`app.build()` 造的 `lambda: not loop.sealed`，两本 store 共用）
            #       ⇒ 一个**别处**封了的库从 handler 里抛 `SealedError` 会**误判**
            #       （驱动会在库**还开着**的时候收工）。今天不可达；写在这里等那一天。
            on_error(exc)
            return "sealed"
        except Exception as exc:
            # **§十五 的「第四条接缝」就落在这一句上**：`timers.due` 跑完一整趟
            #    才重抛第一只（为了让排在后面的那些照样响）⇒ 那一趟的失败没有别人接，
            # **活不活只能由驱动方裁定**。 本刀裁"活"：每一只闹钟是独立的一笔，
            #    一只炸了不许把别的那些带走。而"活"不等于"咽下去" —— 它从这里出去。
            on_error(exc)
            failure = exc
        if _ids(store) != before:
            retry_delay = 1.0
            continue                             #    表动了 ⇒ 下一轮照常重算（可能是下一只）
        if failure is None:
            # 滴了一趟而表一步都没动，而表说此刻就该响（比如：注入的时刻与表的次序
            # 不是同一个方言 ⇒ `due` 一只都没挑中）—— 报出来，别闷头空烧。
            on_error(PacingError("一趟滴答之后表一步都没动 —— 而表说此刻就该响"))
        # Storage can recover without a timer mutation (e.g. disk space or a
        # SQLite writer lock). Retry only schedules that are still present;
        # a consumed/claimed wake is never recreated or automatically replayed.
        import sqlite3
        delay = None
        if (isinstance(failure, (OSError, sqlite3.OperationalError))
                or getattr(failure, 'timer_retryable_storage_failure', False)):
            delay = retry_delay
            retry_delay = min(60.0, retry_delay * 2)
        # A broken overdue entry must not hide a healthy future deadline.
        # Hard errors still wait for a change if no future deadline exists.
        for timer in store.list():
            try:
                future = next_delay([timer], now)
            except PacingError:
                continue  # Already reported by the main scheduling path.
            if future is not None and future > 0:
                delay = future if delay is None else min(delay, future)
        await _race(wake, stop, delay)
    return "stop"
