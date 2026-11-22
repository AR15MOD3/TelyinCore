# telyincore/app.py
"""装配者 ＋ 生命周期 —— `loop.py` 那个纯状态机**之外**的三件事。

⚠️ **三件事，一句说清**：
   ① **装配**：把 9 个槽填上 —— 能接的接上，**接不上的点名**
   ② **生命周期**：`loop.py:289-313` 那份"停的五件事"里，**③「取消当前那一轮」**
      与 **④「叫醒正在等的那一个」** 明写"**留给入口**"（它们要 task 句柄）。
   ⚠️⚠️ **③ 的接线是两半，而两半今天不一样**（接线那一刀改准了）：**`round.py` 那一半
      接上了**（`make_run_round(signal=…)` 收得下、也真的把它递给 `generate`，并把
      `RoundCancelled` 翻成"这一轮没发生"）； **缺的是装配那一半** —— 把**同一个**
      signal 对象递给 `make_run_round` 的那个调用者**今天不存在**（`make_run_round`
      只有测试在调）⇒ **③ 在生产里仍然不取消任何东西**，④ 的硬取消是今天唯一活着的路。
      ⇒ 那一笔欠在 `round._OWED["signal_source"]` 上（谁欠它、代价是什么），
        而 `App.stop()` 的 ③ 那一段留着"这半接上了、那半没有"的处境。
   ③ **装配（这一刀）**：`build()` 把零件**真的装起来** —— 造齐（两本账本 ＋
      一张闹钟表 ＋ 一份惦记 ＋ 一张工具表）／把那两段 `unresolved_*` **绑进表**／
      `run()` 起**两个**任务、`stop()` 停**两个**（主循环那一条 ＋ 那个驱动）。

 **装到哪儿、没装到哪儿**（写在头上 —— 别把它读满）：
   ✅ **整机能装、起得来、停得下**（`build()` ⇒ `run()` ⇒ `stop()`；两个任务都归 `App`）。
   ✅ **闹钟真的会响**（`pacing.drive` 到点调 `due(now)` ⇒ 那只从表里取走、handler 跑）。
      ⚠️ **而"醒来之后做什么"今天只到"记一笔"**（`App.woke`）：§8-B 的编排 ／ §8.5 的
      wake view 都**不在这把刀里**（`App._on_due` 的 docstring 点名了谁欠它）。
   ✅ **醒来有事可做**：那两段 `unresolved_*` 真的绑上了（`tools.build_table`）——
      而它们唯一那条入口是 `App.dispatcher`（ **接上了、而没有人调它** —— 见下一条）。
    **那个派发器接上了那条链**（接线那一刀）：`build()` 把
      `dispatch.make_run_tools(app.dispatcher)` 递进 `loop.run_tools`，
      而那个槽**不再是一条会当场炸的点名接缝**
      ⚠️ **而这不等于"模型的一次工具调用真的被执行了"** —— 那个槽的**调用点还是空的**：
      `loop.py` 只把它存成 `self._run_tools`，**全模块再无第二处出现** ⇒ 一场批跑下来
      它一次都不会被调用。缺的那一步是"`Result.calls` ⇒ `run_tools(calls)` ⇒
      `RoundOutcome`"（要动 `round.py` 或 `loop.py`，那不在这一刀里）——
       **准话**：**递进来的是真东西了；而没有人调它。** 见 `dispatch._WIRING_OWED_BY`。
    **批首那一步接上了（四个槽 Task 1）**：`maintenance` 递的是 `handoff.py` 的公开口
      `Handoff.flush_handoff` —— `loop.py:127` 那一句**不再是接缝**（见 `build()` 里那段）。
    **批末那一步接上了（四个槽 Task 2）**：`flush_boundary` 递的是**这一套自己的**
      `App.flush_boundary` —— `loop.py:139` 那一句**不再是接缝**（那份接线在 `build()`
      的末尾）。 而那一步**今天一个字节都不写**：四本 store 各自在**自己的写口上**就落了盘
      ⇒ **"边界那一下还需要做什么"的答案是"没有"**，出处与判据逐条写在
      `App.flush_boundary` 的 docstring 上（**那里是下一批读者要看的那一页**）。
    **软上限那条提醒接上了（四个槽 Task 3）**：`attach_hint` 那一格递的是 `handoff.py`
      **新开的公开口** `Handoff.inject_notice`，用 `functools.partial(…, hint_text)` 折成
      零参协程（`loop.py:49` 那份声明要的形状 ）。 而**那条提醒的文案由外面给**
      （`build(hint_text=…)` —— 依据是 `loop` 那边立的规矩："内容由外面给，循环不写散文"）
      ⇒ **两样缺一，那一格照旧是那条点名接缝**（设计稿 §四③ 那种"今天没有那个后端"的
      情形）—— 欠主与代价写在 `build()` 里那一格上（那是下一批读者要看的那一页）。
    **送货那一步接上了（四个槽 Task 4）**：`deliver` 递的是**这一套自己的**
      `App.deliver` —— `loop.py:132` 那一句**不再是接缝**（那份接线在 `build()` 的末尾）。
       **而它做的是"记账 ＋ 点名"，不是"真的送出去"**：那一批进 `App.delivered`
      （谁都能读的公开面），而"真的送出去"**归外边那一条投递通道** —— **它今天不存在**，
      连代价一起点名在 `_DELIVERY_OWED_BY` 与 `App.deliver` 上（那是下一批读者要看的那一页）。
      ⚠️ **别把 `:132` 那个 `True` 读成"你收到了"** —— 它问的是"这一批接下了吗"
      。
   ✅ **`loop.run()` 今天跑得完第一批** —— 那四步各有人做（维护 ／ 送货 ／ 落盘 ／ 附提醒）。
    **仍然会在半路炸的那一格**：`:236` 的 `attach_hint` —— **默认那一套**里它还是接缝
      （递齐 `inject_notice` ＋ `hint_text` 之后不是 —— 见上一条）；而它要跑到第
      `soft_rounds` 轮（默认 10）才够得着 ⇒ **走不满那一轮的批走不到它**（两句话同时为真）。
      ⚠️ **那是"短的批"，不是"第一批" —— 这条判据是确定的，不是概率**（单笔修波 I1 改准）：
         `process_one_batch` **每一批**都从 `_rounds()` 的第 1 轮重新数（`loop.py:214`）
         ⇒ 一批跑满 `soft_rounds` 轮，它就在**那一批**里够得着那一格。
         ⇒ 一个 **10 轮的、一直调工具的**第一批**照样会撞上它**；反过来，第 50 批只要
            一轮就收工，就照样够不着。 这里原写的是"第一批**多半**走不到它" ——
            "多半"把一个**确定的**条件（轮数 < `soft_rounds`）说成了概率。
    **真的送到人手上** —— `deliver` 是记账 ＋ 点名（见上一条）：那一批落在**一本内存账**
      上，而**没有任何人收到它**（那条通道归外边：`_DELIVERY_OWED_BY`）。
    **取消那一轮**（`signal` → `round.py`：**`round.py` 那一半接上了**，而欠的是
      装配那一半 —— 见 `App.stop()` 的 ③ 与 `round._OWED["signal_source"]`）。

⚠️⚠️ **它今天跑得完第一批 —— 而"跑得完"是"四步都有人做"，不是"那条通道通了"**：
   `process_one_batch` 的四步**各有人做**（批首的 `await self._maintenance()`：四个槽
   Task 1 接的 `Handoff.flush_handoff`；`:132` 的送货：Task 4 接的 `App.deliver`；
   `:134`／`:136` 的交接：递进来的那两份；批末的 `await self._flush_boundary()`：
   四个槽 Task 2 接的 `App.flush_boundary`）。
    **而它不是"那批东西到了谁手上"** ⇒ 准话是**两句，不是一句**：
   **跑得完** ／ **有人收到** —— `deliver` 记账 ＋ 点名（设计稿 Q2 甲）。
   ⚠️ 而**"落在账上、不是落在你手机上"**这一句是这一整支最该被读到的一句，
      它写在 `App.deliver` 与 `_DELIVERY_OWED_BY` 上（这里只是提一句）。
   ⚠️ **唯一还可能在半路炸的是 `:236` 的 `attach_hint`**（**只在没递那两样的装配里**）。
   **那不是缺陷，是这件事的真实状态。**

装配分为两步：先构造 Loop 和共享实时写入门，再由 Loop.bind_outputs()
完成 App 的投递与批末回调。该接口只允许在运行前接线一次，显式覆盖仍优先。
工具派发器通过 App.run_tools 供模型轮调用，不写 Loop 的内部工具属性。

⚠️⚠️ **「接上了」与「有人调」是两句话**（本刀最重要的一条纪律 —— 别把它们读成一句）：
   那个槽**接上了**（递进来的是真东西）**而它的调用点还是空的**（一场批里没人调它）。
   两句**同时为真**，而只有第二句被解决之后，"模型的一次工具调用真的被执行"才在**生产**里成立。
"""
from __future__ import annotations

import asyncio
import math
import warnings
from functools import partial
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .dispatch import Dispatcher, make_run_tools
from .events import EventStore
from .log import LogStore
from .loop import Loop
from .pacing import StopReason, drive
from .seal import Gate
# ⚠️ `_now_iso` 在这里**不是**新的一份时钟：它是 `TimerStore.__init__` 的 `clock` 默认值
#    **那一个对象**（`build()` 把同一个 callable 交给表和驱动两处，见那里的"一个时钟"）。
#    那份 ISO 的写法（定宽 ＋ 带偏移量）是承重的 ⇒ 在 app 里再写一遍就是"第二条真相"
#    （`timers.py` 的 `_now_iso` docstring 逐条写过）。
from .timers import Timer, TimerStore, _now_iso
from .tools import DECLARED_TOOLS, ToolDef, build_table
from .unresolved import UnresolvedStore
from .unresolved import handlers as unresolved_handlers

#: 关机时**等那一条收工的上界**（毫秒）—— 到点就返回，`stop()` 不拿它当"必须等到"。
#: ⚠️ **为什么要有它**（I3）：`await task` 没有上界，而"那一轮咽下取消、再等一个没人
#:    碰的新东西"是**可达**的形状 ⇒ 一次关机**永远不返回**。
#: ⚠️ **上界不是"等它跑完这一轮"**：取消在下一个 await 点就生效，这里等的是它**收手**
#:    （收尾、关流）；要给的是"它有机会收干净"，不是"它必须干完活"。
#: ⚠️ **它是注入面**（`build(grace_ms=…)`）：测试递 `0` 就能**不等墙上时间**地量到这条上界。
_STOP_GRACE_MS = 5_000

#: **本模块欠着的那几样**（名字 → 欠主）—— 一条不落。
#: ⚠️ **这三笔此前只住在那一刀的报告里**（task-3 那份"记下来、**没有**动"的清单），
#:    而 `.superpowers/` 是 **gitignore 的**（`.gitignore:5`）⇒ **clone 下来的人读不到它们**，
#:    而那份报告**已随工作区删掉**（收尾时整棵删）。⇒ 落进这棵树（那条判据在
#: `tests/test_app.py` 末尾，按**名字**钉）—— **树就是记录**，别去那个路径找。
#: 成法照 `timers._OWED` ／ `unresolved._OWED` ／ `pacing._OWED`：键是**稳定的机器名**
#: （改措辞不会红），值里必须**点名**（空话会红），而**还清就删条目**
#: —— 一条已经兑现的债留在表里就是一句假话。
_OWED: dict[str, str] = {
    "stop_order": "**`App.stop()` 先 `_settle_pacing()`、后 `task.cancel()`** —— 今天"
                  "**不可达**（那个驱动不卡在到期处理器上），而 **S8-B 把活挂到到期处理器上"
                  "之后就是真的**：那时驱动会先被 handler 拖住 ⇒ 主循环那一条要等到 "
                  "`_grace_ms` 才被取消。⚠️ 今天两半的顺序**不许**当作定论被抄下去。"
                  "欠主：**S8-B ／ §8.5**（量它的形状照 "
                  "`tests/test_app.py::test_stop_returns_when_the_driver_hangs_on_a_due_handler`）",
    "timer_door_home": "**那对动表的门（`App.arm` ／ `App.disarm`）住在 `app.py`** —— ★ "
                       "**2026-10-12（作者：「一起解开」）改准**：它们**有调用方了**"
                       "（`launch._set_timer` 走 `App.arm`），而且那一只表也成了**唯一的一只**"
                       "（入口造一只、递进 `app.build`）⇒ 这一格**不再是功能上的缺口**，"
                       "只是**摆放**上的一条：谁要它挨着 `pacing`，那是 §8-B 的事。"
                       "欠主：**§8-B**（`08-定时器与心跳.md` §8.3 的 `schedule_wake` 编排）",
    "restart": "**「一套机器重启」这条路今天没有门**：I1 裁定 `run()` 只许起一次"
               "（句柄 ＋ 立着的 `pacing_stop` 两样都拦），要再起只能 `build()` 另造一套。"
               "⚠️ 谁将来要一个「重启」入口，它得**连带**处理 `loop.py` 的 `_running` 开关 ＋ "
               "`signal` ＋ `sealed` —— 那是**整机重来**，不该混进 `run()`。"
               "欠主：**S8-B ／ §8.5**（落地时确认那时到底要不要这条门）",
}


#: **`attach_hint` 那一格今天欠谁** —— 四个槽 Task 3 把这条欠账**改准了**：
#: 旧的欠主是"**`handoff` 把注入那条路公开**"，而**那一笔已经还清**（`handoff.py` 的
#: `Handoff.inject_notice` 就是那个公开口）⇒ 留着一句已经兑现的欠账就是一句假话。
#: ⚠️ **剩下的半笔不是 `app.py` 欠的**（本模块只提供那两个键，自己不欠谁）⇒ 它**不进
#:    `_OWED` 那张表**（那张表的每一笔都是"**本模块**欠着的"）—— 它进的是那一格的**接缝
#:    消息**：谁递齐两样（`inject_notice` ＋ `hint_text`），谁就走开。
_HINT_OWED_BY = (
    "装配那一层把 `Handoff.inject_notice`（公开口）与那条提醒的**文案**一起递进来"
    "（`build(inject_notice=…, hint_text=…)`）—— 缺一，这一格就仍是接缝"
)

#: **真的"送出去"归谁** —— 四个槽 Task 4 把这一笔点名了（成法照 `_HINT_OWED_BY`）。
#: **那一本账最多留几批**（2026-10-12 作者：「加上限」）。
#: ⚠️ **为什么会有这一格**：这一本存的是**那一批信封的原件**（`tuple(batch)`，
#:    "不复制、不渲染" ⇒ 用户消息原文跟着进程一直活着），而这一套是**长命**的
#:    （配速驱动 ＋ 定时器就在那儿转）⇒ 一场跑一天的会话里，它就是一条**只增不减**的
#:    引用链。作者 2026-10-12 拍了「加上限」⇒ 于是**满了压最旧的**。
#: ⚠️ **为什么是 50**：与本仓另外几处同一个数量级（画像 ／ 关系那两处一份 50 条）——
#:    一屏翻得完的量；这一格是**一条线**：谁要改成别的数，改这一处 ＋ 这条注释。
#: ⚠️ **压掉的不许无声**：`App.delivered_dropped` 记着"一共压掉过几批"
#:    ⇒ "送出去过几批"这个数**仍然只有一个答案**（`len + dropped`）。
#: ⚠️ **此前那一版是"故意不给上界"**（理由："一个没有排水口的截断会让'账上有几批'
#:    变成两个答案"）—— 那一句**今天作废**：作者拍了上界，而那个"两个答案"的顾虑
#:    由**那个计数**接住。仍然为真的是：**没有人从它里面拿走**（排水口照旧归外边那条
#:    投递通道的接线方）。
DELIVERED_AT_MOST = 50
PACING_ERRORS_AT_MOST = 100


#: ⚠️ **它为什么不进 `_OWED` 那张表**：那张表的每一笔都是"**本模块**欠着的"，而这一笔
#:    本模块**欠不了** —— 把一批东西真的送到人手上，要的是一条**本仓之外**的通道
#:    。⇒ 本模块能做的、也做了的是**记账**（`App.delivered`）
#:    ＋ **点名**（这一条）—— 两样合起来就是设计稿 Q2 甲那两句"记账 ＋ 点名"。
#: ⚠️ **代价（这一条要写进树里的就是它，逐条说）**：那条通道**今天不存在** ⇒
#:    落在 `App.delivered` 上的那一批**只在一本内存账上**：
#:    ① **没有人看见它**（除了一次进程内的读）；② **关机就没了**；
#:    ③ 而它**不是**"你手机上那条消息" —— 两件事，别把它们读成一句；
#:    ④ ~~**它只涨不落**~~ ⇒ ★ **2026-10-12 改成"有上界"**（作者：「加上限」）：
#:       一批一项，而**满了压最旧的**（`DELIVERED_AT_MOST = 50`）＋ 压掉几批记在
#:       `App.delivered_dropped` 上（⇒ "送出去过几批"仍然只有一个答案）。
#:       ⚠️ **原来那一条为什么必须单独写**（单笔修波 I3 量过）：前三条说的都是"**看不见**"，
#:       而这一条说的是"**记得太多**" —— 两个不同的病。那一本里存的**不是摘要**，
#:       是那一批 `EventEnvelope` 的**原件**（`tuple(batch)`，`App.deliver` 逐字写着
#:       "不复制、不渲染"）⇒ **用户消息原文会跟着进程一直活着**。而这一套是**长命**的
#:       （配速驱动 ＋ 定时器就在那儿转）：一场跑一天的会话里，这一本账从前就是一条
#:       **只增不减**的引用链 —— 没有读者、没有排水口、也没有上界。（★ 2026-10-12 起
#:       有上界了：见 `DELIVERED_AT_MOST`；**仍然没有的**是"有人从它里面拿走"。）
#: **它今天为什么敢这么放着**（与 `App.woke`（`:271`）**同一个形状，故意的**）：
#:       两本都是"**今天只为了让某一件事可观察**"而留的内存面（`woke` 那句逐字写着
#:       "今天「醒来」这件事的**全部**可观察面"），而**两个都点名了各自的接班人**：
#:       `woke` 等 §8-B 的编排 ／ §8.5 的 wake view（`:468-473`），
#:       `delivered` 等**外边那一条投递通道** —— 那一位一到，这本账就不再是终点
#:       （它变成"送出去了哪些"的排队面，`drain` 是它的下一站）。
#:    ⚠️ **排水口今天归谁**（照 `_OWED` 那张表的成法点名）：**归那条投递通道的接线方** ——
#:       谁把外边那一条接上，谁就同时收下"**读**这本账 ＋ **从它里面拿走**已经送出去的那些"
#:       这两件事（只读不拿 ＝ 那本账一直压到上界；只拿不读 ＝ 丢掉那本账存在的理由）。
#:       ★ **而"上界"与"排水口"是两件事**（2026-10-12 作者拍了前者）：
#:       上界**今天有**（`DELIVERED_AT_MOST = 50` ＋ `delivered_dropped` 那个计数）；
#:       排水口**今天没有**（没人从它里面拿走）—— 别把这两样读成一样。
#: ⚠️ **谁将来把那条通道接上，谁就来把这一条改成"归谁 ＋ 接在哪"**：一条已经兑现的欠账
#:    留在树里就是一句假话（`_OWED` 那张表立过的规矩）。
_DELIVERY_OWED_BY = (
    "**这一条已经兑现了**（2026-10-05 改 —— 照它自己那句「谁接上谁来改」）："
    " **本仓这一侧的投递通道是 `console.deliver`**（`console.py:981`）—— 它把那一批"
    "**先入账**（`App.delivered` ）再**写出去**（`self.write`），而 web 那条路上"
    "`self.write` 由 `web.wire()` 接成**那条推的通道（SSE /events）**⇒ **那几页真的收到** 。"
    "⇒ 接法：`console.assemble` 那一句 **`build(console=self)`**（`console.py:1220` ）"
    "⇒ `loop.py:132` 那一格接成 `partial(console.deliver, ledger=app)`（**先记账、再打** ）；"
    "不递 `console=` 时才回落到只记账的 `App.deliver` 。"
    "⚠️ **仍然为真的是哪一半**（⚠️ 这两条是**两件不同的事** —— 2026-10-05 量过一遍，"
    "这一句从前把它们写成了一条 ）："
    " ① **真的发到用户手上那一边**（设计稿 `08-定时器与心跳.md:192` 的「能出去」那一栏："
    "要 `send_private` ＋ **那一侧得允许「没人问也发话」**）—— 那条设计稿自己标着"
    "**「没做」** ，而它归**外边那一层**（那要那个通讯侧的通道与凭据 ）**不归本仓** ；"
    "  ② ⚠️ **`onDelivery` 不是这一条**（`02-框架与插件.md:62`：那是**插件钩子表**里的一行 —— "
    "「一批已渲染、**尚未进上下文**」／「心跳的 tick 注入在这儿」）—— 我先前把它与"
    "「外边那一边」写在同一句里 ，那是**两件事** 。"
    "⚠️ **代价照旧在**（④）：`App.delivered` 那本账是**内存**账、而且**没有人从它里面拿走**"
    "（一批一项，存的还是那一批信封的**原件**）—— ★ **2026-10-12 作者拍了「加上限」**："
    " 它从此**有上界**（`DELIVERED_AT_MOST = 50`，满了压最旧的；压掉几批记在 "
    "`App.delivered_dropped` 上 ⇒ 「送出去过几批」仍然只有一个答案）；"
    " **而「没有人读它 ／ 没有人从它里面拿走」照旧为真** —— 那才是排水口那一半，"
    " 它归外边那条投递通道的接线方（谁接上谁就来把这一句改窄）。"
)


def _not_wired(what: str, who: str) -> Callable[..., Awaitable[Any]]:
    """一个**点名接缝**：它今天没人实现 ⇒ **当场抛，且消息里写着谁欠它**。

    ⚠️ 照 `run_tools` 已经立过的规矩（`loop.py:60-62`）：
       「一个传进来却没人用的东西，**要说清它还没接上，不许让它静默无效**」。
    ⚠️ **消息里那两个名字都是契约**：`what` 是**哪一面**，`who` 是**谁欠它**（可清点）。
       `tests/test_app.py` 那条"接缝各一条"的测试**逐字**断言这两半 ——
       一个只抛 `NotImplementedError("todo")` 的实现会在那里红。
        **那条清单今天只剩一个**（`attach_hint`）：
         `maintenance` 在**四个槽 Task 1** 被换成了真的东西（见 `build()` 里那一段）、
         `flush_boundary` 在**四个槽 Task 2** 被换成了 `App.flush_boundary`（见那个方法）、
         `deliver` 在**四个槽 Task 4** 被换成了 `App.deliver`（见那个方法），
         而那条测试**跟着改了三次**（**更新，不是删** —— 它红出来正是"接缝真被换掉"的证明）。
        **而 `attach_hint` 这一条与前三条不同类**（四个槽 Task 3）：它**不是被无条件
         换掉的**，而是"**递齐那两样才接上**"（`build(inject_notice=…, hint_text=…)`）⇒
         在**默认那一套**里它**照旧是这条接缝**（设计稿 §四③ 那种"今天没有那个后端"的
         情形 —— 后端是有了，而**文案归声明侧**，树里今天没有那一条）。
         ⇒ 那条测试的**第三改**落在 `who` 那一半上（旧的欠主"`handoff` 把注入那条路公开"
         **已经还清**了 —— `inject_notice` 就是那个口）：一句已经兑现的欠账留在消息里，
         就是一句假话。
    """
    async def _raise(*_a: Any, **_k: Any) -> Any:
        raise NotImplementedError(f"{what} 还没接上 —— 它等 {who}")
    return _raise


class ShutdownIncomplete(RuntimeError):
    """The deadline ended with owned work still pending; handles remain owned."""

    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        super().__init__('shutdown_incomplete')


class ShutdownFailed(RuntimeError):
    """Quiescent does not mean cleanup succeeded; inspect the distinct fields."""

    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        super().__init__('shutdown_failed')


def _drain_deadline(value: Any) -> float:
    if type(value) not in (int, float):
        raise ValueError('finite_absolute_deadline_required')
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError('finite_absolute_deadline_required') from exc
    if not math.isfinite(result):
        raise ValueError('finite_absolute_deadline_required')
    return result


def _shutdown_task_state(task: Any, *, pacing: bool = False) -> dict[str, Any]:
    if task is None:
        return {'state': 'absent'}
    if not task.done():
        return {'state': 'pending'}
    if task.cancelled():
        return {'state': 'cancelled'}
    error = task.exception()
    if error is not None:
        return {'state': 'failed', 'error_type': type(error).__name__}
    state = {'state': 'done'}
    if pacing:
        reason = task.result()
        state['reason'] = reason if reason in ('stop', 'sealed') else 'unexpected'
    return state



class App:
    """装配好的那一套 ＋ 它的生命周期。**它持有 task 句柄**（两个：主循环 ＋ 那个驱动）。"""

    def __init__(self, loop: Loop, *, signal: asyncio.Event,
                 store: EventStore | None = None, log: LogStore | None = None,
                 gate: Gate | None = None,
                 timers: TimerStore | None = None, clock: Callable[[], str] | None = None,
                 unresolved: UnresolvedStore | None = None,
                 tools: Mapping[str, ToolDef] | None = None,
                 dispatcher: Dispatcher | None = None,
                 run_tools: Callable[..., Awaitable[Any]] | None = None,
                 grace_ms: int = _STOP_GRACE_MS) -> None:
        """装配好的那一套。

        ⚠️ **`store` ／ `log` ／ `gate` 三个都默认 `None`** —— 它们与四个接缝**不同一类**：
           那四个是"**还没接上**"（要吵），而这三个是"**这一个 App 没有那本账**"
           （`tests/test_app.py` 那些生命周期测试就不需要它）⇒ 缺席是**合法状态**，
           不是静默失效。⚠️ **谁造它们**：`build()` —— 门卫要读 `loop.sealed`，
           而那个 `loop` 是 `build()` 那一行造的（见那里那段"先有鸡还是先有蛋"）。

        ⚠️ **第 3 刀加的那五位是同一条规矩**（`timers` ／ `clock` ／ `unresolved` ／
           `tools` ／ `dispatcher`）：`build()` 造它们，而**不给某个 root 就没有那一样**
           —— "这一个 App 没有那张表"同样是**合法状态**，不是静默失效。
           ⚠️ **而 `clock` 与 `timers` 是成对的**：有表而没有时钟 ⇒ `_start_pacing()`
              **当场点名**（那不是一个"没有驱动"的状态，那是装配缺了一块）。
        ⚠️ **三个新面各是干什么的**（谁要读它们，读这里）：
             · `App.wake` ／ `App.pacing_stop`：那个驱动的两个注入面（`pacing.drive` 的
               `wake` ／ `stop`）。 **`wake` 是"表变了"那一下，而它归调用方置** ——
               `App.arm` ／ `App.disarm` 就是那一对调用点（`pacing._OWED["wake_setter"]`）。
             · `App.woke`：到点响过的那些闹钟（**今天"醒来"这件事的全部可观察面**；
               它由 `_on_due` 写 —— 那是 `build()` 登记给 `on_due` 的那一个 handler）。
             · `App.pacing_errors`：最近 100 次驱动失败，淘汰数记在 pacing_errors_dropped（循环活，
               而失败可见；封库收工那一声也走这里）。 **R2：那条 task 带着一笔错
               收工**时也走这里（注入面的错 —— `store.list()` ／ `clock()` 抛了，
               见 `_note_pacing_death`）：**它是这一层的唯一出口**，别的地方不看这条 task。

        ⚠️ **`self.gate` 存的就是递进来的那一个**（没递 ⇒ `None`）：这里**不**给它兜
           `seal.always_open` —— 兜一个恒真的门卫，就等于把"没有门卫"与"门卫恒开"
           在**这个对象上**也变成同一个形状（`seal.always_open` 的 docstring 点名的那条陷阱）。
        ⚠️ **而它今天没有消费者**：照那四个点名接缝的形式说 —— 「**写库那条路** 还没接上 ——
           它等 **一个真的往 `App.store` ／ `App.log` 上写的层**」（今天生产链上没人写这两本
           账本 ⇒ 这个门卫**一次都不会被问到**）。
        ⚠️ **第 3 刀把上一句改准了一半**（别读成"接上了"）：门卫的**消费者**从两本变成
           **四本**（`App.timers` ／ `App.unresolved` 的写口也读它 —— 四本共用 `build()`
           造的那**一把**），而**生产里今天仍然没有人从任何一个写口上写**：
             · `App.store` ／ `App.log`：全树没有调用点（只有测试）；
             · `App.timers`：唯一那对调用点是 `App.arm` ／ `App.disarm`，而它们**今天也还
               没有生产调用方**（`set_timer` 那一段执行段归 S8-B —— 见 `App.arm` 的 docstring）；
             · `App.unresolved`：那两段**绑上了**，而**那条路仍不活** —— `build()` 把
               `make_run_tools(dispatcher)` 递进了那个槽 ，而那个槽的**调用点**还是空的
               （`loop.py` 只存不调）⇒ 一场批跑下来，那两段**一次都不会被走到**。
           ⇒ 一句话：**门卫接上了四本，而那四本的写口在今天这条链上都还不是活的**。
        ⚠️⚠️ **别把这一段读反了**：`gate=None` 那条默认值今天是**死的**（本类的生命周期
           一次都不碰它），而**活的那个门卫是空转的** —— 只把"没递门卫"写响，
           就正好把重点写反了。
        """
        self.loop = loop
        self.signal = signal
        self.store = store
        self.log = log
        self.gate = gate
        self.timers = timers
        self.clock = clock
        self.unresolved = unresolved
        self.tools = tools
        self.dispatcher = dispatcher
        self._run_tools = run_tools if run_tools is not None else _not_wired("跑工具", "S6")
        # **那个驱动的两个注入面**（`pacing.drive` 的 `wake` ／ `stop`）—— 它们归这里持有，
        #   而**不归 `loop.py` 那份"停的五件事"**（那是另一条机器、另一个任务）。
        #   ⚠️ `wake` 由**调用方**置（`arm` ／ `disarm`），`pacing_stop` 由 `stop()` 置。
        self.wake = asyncio.Event()
        self.pacing_stop = asyncio.Event()
        #: 到点响过的那些闹钟 —— **今天"醒来"这件事的全部可观察面**（`_on_due` 写它）。
        self.woke: list[Timer] = []
        #: **投递那本账** —— `App.deliver` 写它：**一批一项**、原样（见那个方法）。
        #: ⚠️ 它是**公开面**（与 `woke` 同族：谁都能读），而**它只在内存里** ——
        #:    真的"送出去"归外边那一条投递通道，而那条通道今天不存在（`_DELIVERY_OWED_BY`）。
        self.delivered: list[tuple[Any, ...]] = []
        #: **被上界压掉过几批**（2026-10-12 作者：「加上限」）—— 见 `DELIVERED_AT_MOST`。
        #:   ⚠️ 有它，"送出去过几批"这个数**仍然只有一个答案**
        #:      （`len(self.delivered) + self.delivered_dropped`）。
        self.delivered_dropped: int = 0
        #: 最近的驱动失败（含封库收工）；上限之外只保留淘汰计数。
        self.pacing_errors: list[BaseException] = []
        self.pacing_errors_dropped = 0
        self._task: asyncio.Task[None] | None = None
        self._pacing_task: asyncio.Task[StopReason] | None = None
        self._grace_ms = grace_ms
        self.shutdown_report: dict[str, Any] | None = None

    # ── "跑工具"那一步（接线那一刀接上的那一个槽）────────────────────────
    @property
    def run_tools(self) -> Callable[..., Awaitable[Any]]:
        """派发器的执行入口；真正的轮次由 `round.py` 调用它。"""
        return self._run_tools

    # ── 那对"动表"的门（ `wake` 就是在这里置的）──────────────────────────
    def arm(self, at: Any, payload: Any) -> str:
        """ **定一只闹钟** —— `store.set(...)` ＋ `wake.set()`，而这两半**必须一起做**。

        ⚠️⚠️ **为什么成对**（`pacing._OWED["wake_setter"]` 那一笔）：`TimerStore.set`
           **只交出 id、不通知任何人**（那一笔账在 `timers._OWED["rearm"]` 里）⇒ 不置 `wake`
           的话，两个面都是死的：
             ① 驱动正睡在**某只计时器**上时，一只**更早**的新闹钟要等旧的那只到点才被看见
                （**晚醒**）；
             ② **表空时它一个计时器都不排**（只等 `wake` ／ `stop`）⇒ **一台闲着的机器上，
                第一只闹钟永远不响**（下面那条端到端测试钉的就是这一只）。
        ⚠️ **今天谁是调用方**：`set_timer` 那一段执行段 —— 它归 **S8-B**
           （`08-定时器与心跳.md` §8.3 的 `schedule_wake` 编排，见 `dispatch._HANDLER_OWNERS`）
           ⇒ **生产里今天还没有人自动调这里**。 而在那一刻到来之前，**这扇门就是那唯一的
           一对调用点**：谁接上 `set_timer`，谁就走 `App.arm` ／ `App.disarm`，
           **别直接碰 `App.timers.set`**（那一条路上 `wake` 不会动，而"不会动"是看不见的）。
        ⚠️ **没有表 ⇒ 当场点名**（不静默）：那说明 `build()` 没拿到 `pacing_root`。
        """
        if self.timers is None:
            raise RuntimeError(
                "这一套没有闹钟表（`build()` 没拿到 `pacing_root`）—— 定不了闹钟")
        timer_id = self.timers.set(at, payload)
        self.wake.set() # 那一半：不置它，上表也没人看
        return timer_id

    def disarm(self, id: str) -> bool:
        """ **撤一只闹钟** —— `store.cancel(...)` ＋ `wake.set()`（同 `arm` 那条理由）。

        ⚠️ **撤销也要置 `wake`**：当次那一觉正睡着的**可能就是被撤掉的这一只** ⇒ 不叫醒它
           的话，驱动要等那只**已经不存在的**闹钟到点才重算（"为一只不存在的闹钟白醒一次"
           —— 而若表里还有更早的，那就是晚醒）。`pacing` 那一侧有一条测试钉着"撤销 ⇒
           当次的睡眠要被叫醒"。
        ⚠️ **没撤到（`False`）也照置**：表一步都没动 ⇒ 驱动的下一轮重算出的还是同一个答案，
           而"多叫醒一次"的代价是零（它反正要重读表）。
        """
        if self.timers is None:
            raise RuntimeError(
                "这一套没有闹钟表（`build()` 没拿到 `pacing_root`）—— 撤不了闹钟")
        gone = self.timers.cancel(id)
        self.wake.set() # 与 `arm` 成对的那一半（理由见上面那一段）
        return gone

    # ── 送货那一步（`loop.py:132` 那一道闸：记账 ＋ 点名）──────────────────────
    async def deliver(self, batch: Sequence[Any]) -> bool:
        """ **`loop.py:132` 那一格** —— `if await self._deliver(batch):` 问的那一道闸。

        ⚠️⚠️ **先把那个 `bool` 说死**（这是本刀最容易写出一条假话的地方 ⇒ 拿实物核过）：
           它**不是**"这一批送到人手上没有"。`:132` 的实物是
           `if await self._deliver(batch): await self._rounds() …` ⇒
           **它问的是「这一批接下了吗、要不要往下跑轮」**。
           ⇒ **`True` ＝ 记进账了 ＋ 有东西可干**；**`False` ＝ 这一批里没有东西**
             （`:132` 那一支**不跑轮**，而 `:136` 的交接与 `:139` 的边界**照旧**走 ——
             那两句在 `if` 之内／之外的位置是逐字核过的）。
           ⚠️ **而"你真的收到了吗"这件事要分两半说**（2026-10-05 改 —— 见 `_DELIVERY_OWED_BY`）：
             · **本仓这一侧有人收到** —— 递了 `console=` 的时候（`build(console=self)` ），
               `loop.py:132` 那一格接的是 `partial(console.deliver, ledger=app)`
               ⇒ 那一批**先入账、再写出去**，而 web 那条路上那是**那条推的通道**（SSE ）
               ⇒ **那几页真收到** ；
             · **"到了你手机上"仍然不是本树答得了的** —— 那一位归**本仓之外**
               。
             **别把这个 `True` 读成"那一批到了你手机上"** （那一句照旧成立 ）。

        ⚠️⚠️ **记账 ＋ 点名**（设计稿 Q2 甲的两半，这一格就是它们的落点）：
           · **记账**：这一批**原样**进 `App.delivered`（那本是**公开面**，与 `App.woke`
             同族 —— 谁都能读：`app.delivered`，一批一项、按到达次序）；
           · **点名**：谁真的把它送出去 ＋ 那件事的代价，写在 `_DELIVERY_OWED_BY` 上。
        ⚠️⚠️ **那本账只涨不落**（单笔修波 I3 补的一条，代价 ④）：`App.delivered`
           **没有读者、也没有排水口** —— 谁读它（除了一次进程内的读）／谁把送出去的那些
           从它里面**拿走**，两件事今天都还没有人做 ⇒ **一批一项，进程活着就一直在**，
           而这一套是长命的（配速驱动 ＋ 定时器）。⚠️ 更要紧的是**它存的是什么**：
           存的是那一批 `EventEnvelope` 的**原件**（下面那句"不复制、不渲染"）⇒
           **用户消息原文跟着这本账一起活着**。 排水口点名归**那条投递通道的接线方**
           （见 `_DELIVERY_OWED_BY` 的 ④）；在那之前本模块**不造上界** —— 理由与下面
           "空批不记账"同一条：账上的项数只许有一个答案。

        ⚠️ **那批东西的形状是读出来的，不是猜的**：真的那条链上 `take_batch` 就是
           `feed.next_batch(bus, ready)`（`feed.py:21`），而它交回的是
           `bus.take_if_ready()`（`bus.py:226`）给的那一串 —— 队里排的是 `WakeItem`，
           而 `WakeItem = EventEnvelope`（`bus.py:24`）⇒ **一批就是几个信封**。
           ⚠️ 而本方法**不许按那个形状做任何事**：它**不解释**递进来的东西（`loop.py`
           那条"只读，不解释内容"的规矩照旧）—— 记的就是那一批本身（`tuple(batch)`，
           同一批东西、不复制、不渲染）。少一个依赖，就少一处会与那边分家的第二份真相。

        ⚠️ **空批为什么回 `False`**：那是 `loop.py` 那边点过名的那条路
           （`tests/test_loop.py` 的 `NOTHING_NEW`："没有新东西" ⇒ **一轮都不跑，
           而边界照样收**）。
            **而它今天到不了**（量过）：真的那条链上 `feed.next_batch` 只在
             `bus.take_if_ready()` 给了东西时才回来，而它给东西 ⇒ 队里非空
             （`bus.py:241-242` 那句"`_ready` 真 ⇒ 队列必非空"与那条测试钉着它）
           ⇒ 这一句是一道**防身闸**：谁哪天把 `take_batch` 接到一个会给空批的东西上，
             这一批就**一轮都不跑**，而**不是**让模型去答一个空的。
           ⚠️ 而**空批不记账**：没有东西可记的一批在账上留一个空项，只会让"账上有几批"
             这个问题变成两个答案。

        ⚠️ **它自己不等、不看钟**：这一格只碰内存里那一本账 ⇒ 测试那条"不许依赖墙上时间"
           的规矩在这里没有可违的地方（它是那一格里唯一不必注入时钟的一步）。
        """
        if not batch:
            return False                    # 「没有新东西」那条路 —— 见上面那一段
        self.delivered.append(tuple(batch))
        # **2026-10-12（作者：「加上限」）**：满了**压最旧的**，并把压掉的**记个数** ——
        #   ⚠️ 压掉**不许无声**：`delivered_dropped` 一加，"送出去过几批"这个数
        #      仍然只有一个答案（`len(self.delivered) + self.delivered_dropped`）。
        extra = len(self.delivered) - DELIVERED_AT_MOST
        if extra > 0:
            del self.delivered[:extra]
            self.delivered_dropped += extra
        return True

    # ── 批末那一下（`loop.py:139`：**落盘**那个落点）────────────────────────
    async def flush_boundary(self) -> None:
        """ **批末那一下** —— `loop.py:139` 的 `await self._flush_boundary()`：
        「到一个边界，把它落了」。 而**今天它落的是"没有东西在飞"** —— 逐条证据在下面。

        ⚠️⚠️ **先把准话说死**（这一格最容易被读成"看起来做了事"）：
           **它一个字节都不写**，而**那不是漏**：它要落的那四本**各自在自己的写口上
           就落了盘** ⇒ 边界上**不存在"还没落的"东西**。每一个字都有出处：

           · **`events.EventStore.append`** —— 那一行**在同一个调用里**经 `log.append_line`
             写下去（`events.py:549`），**之后**才动内存（`:568-576`）：游标 ／ 去重表 ／
             分片表全是"盘上那份的镜像"，没有队列、没有延迟写。
           · **`log.LogStore.append_line`** —— `open(..., "ab")` ＋ 写 ＋ 关
             （`log.py:207-209`）：**没有缓冲对象活过那一句**（每次调用自己开、自己关）。
           · **`timers.TimerStore._commit`** —— 同目录 temp ⇒ `flush` ⇒ `fsync` ⇒
             `os.replace`，**先落盘、再换内存**（`timers.py:334-349`）；四条会写的路
             （`set` ／ `cancel` ／ `clear_all` ／ `due`）**都走它**。
           · **`unresolved.UnresolvedStore._commit`** —— 同一形状（`unresolved.py:297`）；
             两条会写的路（`add` ／ `resolve`）都走它。
           ⇒ 四本**都不持有**长开的句柄、也都没有"攒着"的形状 ⇒
             **"边界"在这儿没有对应的东西**。
            **判据**（这句话不是散文）：`tests/test_app.py` 那条
             `test_the_boundary_lands_nothing_because_every_store_lands_on_its_own_write`
             —— 它量的正是这两半：**写口上就落得了盘**（一个全新的读者当场看得见那四笔）
             ＋ **边界那一步一个字节都不改**（目录树逐字节快照相同）。

        ⚠️ **它拿着谁**（判据 ①：接谁）：**就是这一套的那四本** —— `self.store` ／
           `self.log` ／ `self.timers` ／ `self.unresolved`（`build()` 造的那四本）。
           ⚠️ 那四本**可以缺席**（`None` 是合法状态，见 `__init__` 那段）——
              缺席时"没有东西可落"照旧成立（没有那本表，就没有那本表要落的盘）。

        ⚠️ **一笔要分开说的账**（别把上面那句读成"断电也丢不了"）：`append_line` 那一族
           **关句柄、不 `fsync`** ⇒ "最后一行进没进盘"是一个**断电**问题，它不是
           "边界上有半成品"。 而那一半**不归这一步**：在 `app.py` 里替它们补一发
           `fsync` 就是给"落盘纪律"造**第二份真相** —— 哪一句写、要不要 `fsync`，
           由 `log.py` ／ `timers.py` ／ `unresolved.py` **各自**管
           （`log.py:17-20` 逐句写着它们那边"原子"是什么意思）。
           ⇒ **准话**：**边界那一下没有东西可落**；"断电丢最后一行"是**另一件事**，
             而它不在这一格里，也没被这句话盖住。

        ⚠️⚠️ **归属（控制者裁定，2026-09-30）**：那几本 store 的**收尾**归**这一格**，
           **不归 `maintenance`** —— `maintenance` 管的是"**别插一件做一半的交接**"
           （`loop.py:127` 那句注释），而"**到一个边界就把它落了**"是这一格的事
           （`loop.py:139`，在 `finally` **之外**、每一批正常收尾之后都走）。
            本刀量出来的答案是：**那一份"收尾"今天是一份空活**——四本自己已经落干净了；
             而**这话是量出来的**，不是"没人接"：见上面那四条出处与那条判据。
           ⚠️ 设计稿 §二把这几本的收尾记在 `maintenance` 那一行
              （`docs/superpowers/specs/2026-09-30-四个槽-design.md:69`）——
              那一行**从本刀起作废**，以这里为准。
               **2026-09-30 更正（单笔修波 I1）**：这一处此前写着"设计稿不在本刀的落点里"
              和一个指错行的坐标（`:61`）—— **两句都不对了**：那一位读者后来**到了**
              （同一个切片的改准波改的就是那两本），而设计稿 §二 后面还跟着一行
              （`:71-73`）写着 `flush_boundary` 归这一格 ⇒ 它今天的话是准的。
              ⇒ 那句"更正落在这里"留在**它自己的历史里**，读者按今天的坐标去读。

        ⚠️ **谁量出"真的有事要落"的那一天，谁就来把这一格换成那一步**：判据就是上面那条
           测试（一本**攒着不写**的 store 会让它当场红）。在那之前这一格**空得有名有姓**：
           `loop.py:60-63` 那条家规要的是"别让一个东西**静默**无效" ——
           而"这里什么都没有"这件事**在这一页写着、在那条测试里判着**，不静默。

        ⚠️ **它为什么不接别的口**（判据 A：读调用点，不按名字猜）：`loop.py:48` 声明的是
           `Callable[[], Awaitable[None]]`，`:139` 那一句**零参、不读返回值**，而它在
           `try/finally` **之外** ⇒ 每一批**正常收尾之后**都要走它（`:132` 那一句抛了的路
           走不到 —— 那时异常已经把这一批带走了：`process_one_batch` 的 `finally` 只松开
           `processing`，异常照旧往上飞）。 返回值那一半**没有意义**（声明收的就是 `None`）。
        """
        # **没有一步可做** —— 而这一格**不假装**：四本各自的写口已经落完了（见上面四条）。
        #   往这里添一笔写（重写一份文件、补一个 fsync、记一条流水）才是那句被禁的形状：
        #   一个"看起来做了事"的槽。⇒ 谁真量出"有东西要落"，谁把下面这一句换成那一步。
        return None

    # ── 那一声响（`build()` 把它登记给 `TimerStore.on_due`）──────────────────
    async def _on_due(self, timer: Timer) -> None:
        """ **一声响就到这儿** —— `build()` 交给 `TimerStore.on_due` 的那一个 handler。

        ⚠️ **没有它**：`timers.due` 在一只有到点的闹钟上抛 `NoDueHandlerError`（那一条是
           "静默丢掉那一响与「它根本没到点」分不出来"那条家规的落地）⇒ 驱动把它当
           **一次失败的滴答**报出去，然后**停到下一次变化**：不静默，而机器从此不再做
           它该做的事（`pacing._OWED["due_handler"]` 那一笔说的就是这个）。
        ⚠️⚠️ **而今天它的整个身体只有一句"记一笔"**（`App.woke`）—— 那不是漏了：
           "醒来之后做什么"是两件**明令不在这一刀**的事：
             · §8-B 的编排（`08-定时器与心跳.md` §8.3 的 `schedule_wake`）；
             · §8.5 的 wake view（"醒来 → 看到"，欠主见 `unresolved._OWED["wake_view"]`）。
            **谁接上它们，谁就把下面这一句身体换成那一步** —— 这一行就是那个待办
           （照 `loop.py:60-63` 那条家规：一个接了一半的东西要说清它接在哪儿）。
        ⚠️ **"记一笔"不是静默**：`App.woke` 是**公开面**（端到端那条测试读的就是它），
           记下的是那一刻的**实物**（那一只 `Timer`，连它的载荷）。
        ⚠️ **每一只闹钟在进这里之前就已经从表里取走了**（`timers.due` 的次序是承重的）
           ⇒ 这里抛出去也不会让那一只留在表里反复响；而 `due` 会把同一趟里别的那些叫完
           （一只炸了不许把别的带走）—— 那次失败照旧从驱动的 `on_error` 出去。
        """
        self.woke.append(timer)

    # ── 那个驱动（**第二个任务**）──────────────────────────────────────
    def _start_pacing(self) -> asyncio.Task[StopReason] | None:
        """ 起那个驱动 —— 它才是"到点去滴一下"的人（`timers._OWED["due_driver"]` 那一笔）。

        ⚠️ **没有表 ⇒ 交回 `None`**（不起任务）：`pacing.drive` 要的第一样就是一张表 ——
           而"这一个 App 没有闹钟表"是合法状态（`build()` 没拿到 `pacing_root`）。
        ⚠️ **有表而没有时钟 ⇒ 当场点名**（不静默、也不退化成"没有驱动"）：那两样是
           `build()` 一起造的，缺一样就说明装配缺了一块（而缺了时钟的驱动**排不出下一次**
           —— 那正是 `pacing.drive` 四个注入面一个默认值都没有的理由）。
        ⚠️ **四个注入面一个不落**：`clock`（注入的时钟）／`stop`（`App.pacing_stop`）／
           `wake`（`App.wake`，由 `arm` ／ `disarm` 置）／`on_error`（`App.pacing_errors`）。
        ⚠️ **封库那一声今天走两条路**：`drive` 的**返回值**（`"sealed"`，落在
           `App._pacing_task.result()` 上）**和** `on_error` 那一句（进 `App.pacing_errors`）
           —— 两条**同时**给（那是 `pacing.drive` 刻意做的）⇒ **不读返回值的主人也漏不掉它**。
        ⚠️ **它不许被 `await` 掉**（`run()` 只 `await` 主循环那一条）：这个驱动**到 `stop`
           被置为止一直转** ⇒ 谁 `await` 它，谁就永远回不来。
        ⚠️⚠️ **R2：它带回来的那笔错也要有人读**（`task.exception()` 是那个"没人读的结果"）——
           见 `_note_pacing_death`：`pacing.drive` **刻意不接**注入面的错
           （`store.list()` ／ `clock()` 抛了 ⇒ 那条 task 带着它收工），而**谁起的谁看**。
        """
        if self.timers is None:
            return None
        if self.clock is None:
            raise RuntimeError(
                "有闹钟表却没有时钟 —— 那个驱动排不出下一次"
                "（`clock` 是必填的注入面，而 `build()` 是两样一起造的）")
        task = asyncio.create_task(drive(
            self.timers, clock=self.clock, stop=self.pacing_stop, wake=self.wake,
            on_error=self._note_pacing_error))
        # **R2：挂上那个"记账"的回调** —— 少了这一句，一条**死掉的**驱动与一台健康的
        #    机器长得一模一样（见 `_note_pacing_death` 那一段，判据③ 与 I1 那条守卫
        #    用的是同一个论证）。
        task.add_done_callback(self._note_pacing_death)
        return task

    def _note_pacing_error(self, error: BaseException) -> None:
        """Keep recent diagnostics bounded, with an explicit omitted count."""
        self.pacing_errors.append(error)
        extra = len(self.pacing_errors) - PACING_ERRORS_AT_MOST
        if extra > 0:
            del self.pacing_errors[:extra]
            self.pacing_errors_dropped += extra

    def _note_pacing_death(self, task: asyncio.Task[StopReason]) -> None:
        """ **那条驱动死了也要有人记账** —— 它的下场落到 `App.pacing_errors` 上（R2）。

        ⚠️⚠️ **为什么要有它**（这正是 R2 那条禁令的形状）：`pacing.drive` **刻意不接**
           `store.list()` ／ `clock()` 的错（那两样是**注入面的错**，见那个模块的 docstring）
           ⇒ 那条 task **带着那笔错收工**。而它在生产里**此前没人读**：
             · `App.stop()` 那一句 `_retrieve` 是**取走、不抛**（I2）—— 读过就丢；
             · `App` 又**持有**那条 task 的强引用 ⇒ 解释器自己那句
               `Task exception was never retrieved` **也不会响**。
           ⇒ 结果是**每一个公开面都读作「健康空闲」**：闹钟还留在表里、`App.woke` 空、
           `app.pacing_errors` 空。（评审实测过：`arm()` 照常给 id、`task.exception()` 是那个
           `RuntimeError`，而上面三条一个字节都没变。）那正是 `loop.py:60-63` 禁的形状
           —— 而它**与 I1 那条守卫是同一个论证**（"`_pacing_task.result()` 生产里没人读
           ⇒ 它与一台健康的机器长得一模一样"）：同一句话，此前只对"守卫"那一半说了。

        ⚠️ **所以：谁起这条 task，谁就看它的下场**（`pacing._OWED["task_owner"]` 的另一半）。
           接口就是那个**已有的**面 —— 与判据③ 的每一次失败**同一个出口**
           （读的人只读 `App.pacing_errors` 这一本，不必再去看那条 task）。

        ⚠️ **被取消不算"死了"**：`cancelled()` 那一支直接返回 —— 一次取消不是一笔错
           （今天 `App` 从不取消它，写上是为了那一天）。
        ⚠️ **它不抛、也不改那条 task**：回调只**记账**。I2 那条纪律照旧（这条 task 的
           下场不抛给调用方），诊断也照旧在 `task.exception()` 上（回调读它**不消耗**它）。
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._note_pacing_error(exc)

    async def _settle_pacing(self) -> None:
        """ **有上界地**等那个驱动收手，并把它的下场**取走**（与主循环那一条同一个形状）。

        ⚠️ **上界到点就返回**（I3 那条纪律：`stop()` 只保证"**我**不再等了"）——
           "它到底收工了没有"是**同步事实**：问 `app._pacing_task.done()`。
        ⚠️ **取走、但不抛**（I2 那条纪律）：不取的话解释器会在回收它时打一句
           `Task exception was never retrieved`（一条假警报，而本仓的测试夹具把它判成失败）。
        ⚠️ **这一段里没有 `except CancelledError`** —— 与主循环那一半同一个形状：
           要咽掉"`stop()` 自己被取消"就得专门写一句 `pass`（而那正是 ⑤ 那条测试会当场红的写法）。
        ⚠️ **"取走"在 `finally` 里 —— 与主循环那一半逐字同形**（Minor 1）：旧写法把它摆在
           那个 `await` **之后**，于是 `stop()` 里那句"两半是同一个形状"**差了一步** ——
           一次取消正好落在这半的等待上时，主循环那半会取走、这半不会。
           ⇒ 今天两半都读作："置一个信号 ⇒ 在一个**有上界**的等待里等它自己收手 ⇒
           取走它的下场 ⇒ **没有 `except CancelledError`**"。
        ⚠️ **幂等**：已经 settle 过那一条走 `_retrieve`，什么都不做。
        """
        pacing = self._pacing_task
        if pacing is None:
            return
        try:
            if not pacing.done():
                await asyncio.wait({pacing}, timeout=self._grace_ms / 1000)
        finally:
            # 两条路都取走一次（与主循环那一条同一个形状）：**被取消**那条路也要 ——
            #   那笔错（或那条 task 的下场）没有别人读过。`_retrieve` 是同步的，
            #   而对已经取过的那一条是幂等的（它自己那两条守卫就是干这个的）。
            self._retrieve(pacing)

    def shutdown_state(self) -> dict[str, Any]:
        """Observe real handles; never infer quiescence merely from stop flags."""
        main = _shutdown_task_state(self._task)
        pacing = _shutdown_task_state(self._pacing_task, pacing=True)
        requested = self.signal.is_set() and self.pacing_stop.is_set()
        quiescent = all(t['state'] != 'pending' for t in (main, pacing))
        pacing_ok = pacing['state'] == 'absent' or (
            pacing['state'] == 'done' and pacing.get('reason') in ('stop', 'sealed'))
        cleanup_ok = requested and pacing_ok
        return {'stop_requested': requested, 'quiescent': quiescent,
                'cleanup_ok': cleanup_ok, 'work_ok': main['state'] != 'failed',
                'main': main, 'pacing': pacing}

    async def drain_stop(self, *, deadline: float,
                         clock: Callable[[], float]) -> dict[str, Any]:
        """Confirm an already requested stop, within the caller's ONE deadline.

        No new driver, stop request, cancellation, or hidden grace is introduced.
        The monotonic clock is injected; expired budgets are observation-only.
        """
        limit = _drain_deadline(deadline)
        if not callable(clock):
            raise TypeError('monotonic_clock_required')
        before = self.shutdown_state()
        interrupted = False
        try:
            if before['stop_requested']:
                pending = {t for t in (self._task, self._pacing_task)
                           if t is not None and not t.done()}
                remaining = limit - _drain_deadline(clock())
                if pending and remaining > 0:
                    await asyncio.wait(pending, timeout=remaining)
        except asyncio.CancelledError:
            interrupted = True
            raise
        finally:
            after = self.shutdown_state()
            self.shutdown_report = {'before': before, 'after': after,
                                    'deadline': limit, 'interrupted': interrupted}
        if not after['quiescent']:
            raise ShutdownIncomplete(self.shutdown_report)
        if not after['cleanup_ok']:
            raise ShutdownFailed(self.shutdown_report)
        return self.shutdown_report


    async def run(self) -> None:
        """建 task、跑它。 **task 句柄归这里** —— 那是清单里 ③④ 需要的东西。

        ⚠️ **句柄是新开的那个 task**（跑 `loop.run()` 的），**不是 `current_task()`**：
           后者给的是**调用方**那个 task —— 拿它去 `cancel()` 是去取消别人，
           而 ④ 要叫醒的是**我们自己这一条等待**。
        ⚠️ **同时只许有一条跑着的 `loop.run()`**：句柄只有一个 —— 两次并发地调它，
           `self._task` 会**指向后来那一条**（先起的那条**没人再拿得住、也没人取消得了**），
           而两条 `loop.run()` **同时在吃 `take_batch`**。⇒ 再调一次**当场抛**（下一句）。
           ⚠️ **跑完之后再调**：**没有闹钟表的那些**照旧允许（那时句柄不歧义 —— 会不会
           再吃 `take_batch` 取决于 `loop.py` 那个开关：`stop()` 之后 `_running` 是假
           ⇒ 立刻返回）； **而带闹钟表的那些不行** —— 见下面 I1 那一段。
        ⚠️⚠️ **I1（本刀裁定）：有闹钟表的时候，`run()` 只许起一次驱动** ——
           旧写法那两句守卫只挡"还在跑"这一种，而**三个真实状态它都漏**（三个都量过）：
             ① **驱动还活着**（`run()` 返回 ≠ 关机，见下面那一段）⇒ 再起一条就是
                两条驱动**同时吃同一张表**（`wake` 只有一个，谁先看见谁算）；
             ② **`stop()` 之后 `App.pacing_stop` 还立着** ⇒ 而 `drive` 的**第一句**就是
                `while not stop.is_set()`（`pacing.drive` 的**轮首那一句**）⇒ 新起的那条驱动**在第一次
                检查上就回 `"stop"`**、当场收工 ⇒ 一台**上着电却没有滴答器**的机器；
                而 `_pacing_task.result()` 生产里**没人读** ⇒ **它与一台健康的机器长得
                一模一样** —— 静默的那一半就在这里。
                ⚠️⚠️ **R2 那个回调盖不住这一条**（两句都要留着）：`_note_pacing_death`
                只记「**带着一笔错**收工」，而这里死去的那条驱动**是正常返回的**
                （`result()` 是 `"stop"`）—— 一笔没有异常的、**正常的早退**，照样没有
                任何一个面看得出来。 这正是这条守卫**必须**存在的理由（回调不是它的替身）。
             ③ **`stop()` 先于 `run()`**（"还没起就关"是合法次序，`test_stop_before_run_returns`
                钉着它）⇒ 那条链一个字节都没动过、句柄也是 `None`，**而 `pacing_stop`
                已经立着** ⇒ 下面照旧起得来，而起的是一条**当场就死**的驱动 ——
                与 ② 是同一件事的另一条路（守卫因此要**两样都看**，不能只看句柄）。
           ⇒ **裁定：不许**（而**不是**替调用方把那个事件清掉、再起一条 —— "重起"要到
             `loop.py` 那个开关也跟着重置才成立，而那不在这一刀里）。
           ⇒ 于是守卫**在三个状态上都抛**，而**两句话各说各的**：
             驱动还在跑 ⇒ "已经在跑"；另外两个（**已收工** ／ **先关了机**）⇒
             点名"第一次检查就会回 `stop`" —— **不许**让 `run()` 静默地上一条死驱动
             （③ 更直白：那一次是**头一次** `run()`，句柄从头到尾都是 `None`）。
           ⚠️ **"有闹钟表"是量出来的**（`self.timers is not None` ／ `_pacing_task is not None`）：
             `build()` 没拿到 `pacing_root` 的那些**从来不起驱动**、也没有那个事件可立，
             ⇒ `run()` 在它们那边的语义**一个字都没变**。
            ⚠️ **「重启」那件事今天没有门**（那是**记在案上**的一笔，不是漏了）：
              I1 的裁定就是"要再起就另造一套（`build()`）"⇒ 见 `_OWED["restart"]`
              （欠主 S8-B ／ §8.5：落地时确认那时到底要不要一个重启入口）。

        ⚠️ **第 3 刀：这里起**两个**任务**（次序**是有意的、不是承重的** —— 两条都由
           `create_task` 起：它们都要等**本协程让出**才跑第一步，而两步之间**没有 `await`**
           ⇒ 没有任何一只闹钟能在那个缝里到点；让那一只有人看着的，是驱动**起来了**
           ＋ `on_due` 登记了，**不是这两行谁在前**）：
           ① **那个驱动先起**（`_start_pacing()`）：先有"谁在滴答"，再有"取一批来干"；
           ② 主循环那一条后起，而**本方法 `await` 的是它**（不是那个驱动 —— 见
              `_start_pacing`）。
           ⇒ **`run()` 返回 ≠ 关机**：主循环收工（`take_batch` 给了 `None`）之后那个驱动
              **照旧在转** —— 停它的是 `stop()`。那是有意的：闹钟的活与"这一批干完没有"
              是两件事。
        ⚠️ **两条 task 各有自己的守卫**（再调一次当场抛）：主循环那条见上一段；驱动那条
           的第一句理由同它 —— 两条驱动会**同时吃同一张表**（`wake` 只有一个，谁先看见
           谁算）； 而它**不止那一句**：驱动**已经收工**那一种有它自己的后果（I1 的 ②／③）。
        """
        if self._task is not None and not self._task.done():
            raise RuntimeError(
                "这一套已经在跑了 —— 再起一条会让两条 loop.run() 同时吃 take_batch")
        # **I1：起驱动之前看两样** —— ①这一套**已经起过一次**（句柄还在，`done()` 也罢）；
        #    ②**这一套已经关过机**（`stop()` 置过 `pacing_stop`，而本方法**不替谁清它**）。
        #    ⚠️ **②不能写成"句柄在不在"**：`stop()` 先于 `run()`（"还没起就关"是合法次序）
        #    时句柄还是 `None`，而那才是最干净的死驱动形状 —— 什么都没碰过，却起不起来。
        #    ⇒ 下面那句 `_start_pacing()` 在两种情形下都会**当场**产出一条死在第一次检查上的
        #    驱动，而"死"与"健康"在生产里**长得一模一样**（没人读 `_pacing_task.result()`）。
        if self._pacing_task is not None and not self._pacing_task.done():
            raise RuntimeError(
                "那个驱动已经在跑了 —— 再起一条就是两条驱动在吃同一张闹钟表")
        stopped = self.timers is not None and self.pacing_stop.is_set()
        if self._pacing_task is not None or stopped:
            raise RuntimeError(
                "这一套已经关过机了，或者它的驱动已经起过一次了"
                " —— 两条路都通向同一件事：再起一条驱动会在它第一次检查上就回 `stop`，"
                "那是一台没有滴答器的机器，而它与一台健康的机器长得一模一样"
                "（生产里没人读 `_pacing_task.result()`）"
                " ⇒ 带闹钟表的那些只许起一次；要再起就另造一套（`build()`）。")
        self._pacing_task = self._start_pacing()
        self._task = asyncio.create_task(self.loop.run())
        await self._task

    async def stop(self) -> None:
        """ **清单里的 ③ 与 ④**。

        ③ **取消当前那一轮**：置信号 —— **两半都接上了**（`round.py` 收得下它，装配那一层
           也把**同一个对象**递了进去）：
           · **`round.py` 那一半**：它收得下 `signal`（`make_run_round(signal=…)`）、
             真的把它递给了 `generate`，并且**接住了 `RoundCancelled`**
             —— 把它翻成「这一轮没发生」（五位全假，**不是失败**）。
           · ★ **装配那一半（2026-10-12，待办账第 20 条改准）**：装配那一层就是
             `Console.assemble` —— 它把 `self.signal`（**同一个对象**）同时递给
             `make_run_round(signal=…)` 与 `build(signal=…)`（见那两处调用点）；
             判据是 `tests/test_app.py::`
             `test_the_signal_the_shutdown_side_sets_is_the_one_the_round_reads`。
             ⚠️ **仍然为真的一半**：`App.stop()` 在生产里**还没有调用方**
             （今天只有测试在调）⇒ **一次取消在生产里不会发生** —— 那一半照旧记在
             `round._OWED["signal_source"]` 那一笔上（下一个读的人去那里看「还欠谁」）。
           · ⚠️ **谁先到 —— 把 ③ 与 ④ 放在一起时最容易错建模的一处**：③ 是**协作式**的，
             「置上」与「生效」之间隔着**下一个 chunk 边界**；而 ④ 是**硬取消**，它**当场**
             把那次等待叫停 ⇒ 在 `stop()` 这条路上**先到的通常是 ④**。⇒ 既别把 ③ 读成
             「取消是靠信号完成的」，也别把 ④ 读成「信号没用」：两者判的不是同一件事 —— ④ 管
             **这里别再等了**，③ 管（**接上之后**）**下游怎么收手**。
        ④ **叫醒正在等的那一个**：取消一次正在 `await` 的等待（`CancelledError`）。
           ⚠️ **它打断不了正在跑的那一轮**：`Task.cancel()` 只在**下一个 await 点**掷出，
              不在 chunk 边界（那是 ③ 的位置）⇒ 一次取消**落在流中间**时的收手方式，
              见下面那条 `ResponseProtocolError` / `IrreversibleError` 的分岔。
        ⚠️ **它不持"那五个位算不算数"的判断** —— 那是 `loop.py` 的判代，**已经在了**
           （`await` 之前存、回来先比）⇒ 本方法只让那一轮**早点结束**。

        ⚠️⚠️ **I2：那条 task 的下场，本方法一律不抛给调用方。**
           旧写法直接 `await` ⇒ 它会把 task 结束时的那笔错**原样转给调用方**
           （实测：一个 `take_batch` 抛普通异常 ⇒ `stop()` 把它抛了出来）。生产里最可能的
           那一个就是**第一个接缝**的 `NotImplementedError` —— **四个槽接上之前**
           那是 `maintenance` 那一步（`loop.run()` 的第一句就 `await` 它），
           **几步接上之后**今天撞得着的是 `loop.py:236` 的 `attach_hint`
           （ 而它**只在没递那两样的装配里**才是接缝；`deliver` 在 Task 4 之后不是了）
           ⇒ 一次关机被一个与它无关的异常打断。
           ⇒ 照 `generate.py` 那段「清理抛的错不许顶掉主错」的形状：
           **取走它（`_retrieve`）、但不抛**。**诊断没丢**：它在 `app._task.exception()` 上。
           ⚠️ **也因此它对"已经 settle 过"的那一条是幂等的**：`task.done()` 那一支直接返回，
           **不去重放它的结局**（旧写法每调一次就重抛一次）。
           ⚠️ **唯一照旧往外抛的是"`stop()` 自己被取消"** —— 咽掉的话那次取消就**不算数**了
              （实测 3.11.9：协程咽下 `CancelledError` 后正常返回 ⇒ `task.cancelled()`
              为 **False**）⇒ 关机时的一次 Ctrl-C 会被静默吃掉。
               这一点现在是**结构性**的：下面那个 `try` **没有 `except CancelledError`**
              （旧写法里要靠 `current_task().cancelling()` 去分辨两种取消；如今
              **前一种到不了这里** —— `asyncio.wait()` 把我们取消的那一条收进 `done`，
              它**不抛** `CancelledError`）⇒ 想咽它得**专门写一句 `pass`**，
              而那正是 `test_a_cancel_of_stop_itself_is_not_swallowed` 会当场红的写法。

        ⚠️⚠️ **I3：这里的等待有上界**（`_STOP_GRACE_MS`／注入的 `grace_ms`）。
           "那一轮**咽下**取消、再等一个**没人碰的**新 `Event`"是**可达**的形状
           （Task 2 的修复轮在测试里就踩到过它）⇒ 无上界的 `await task` 会让 `stop()`
           **永远不返回**。上界到点就**返回**：`stop()` 只保证"**我**不再等了"。
           ⚠️ **"它到底收工了没有"是同步事实**：问 `app._task.done()`
              （`False` ⇒ 上界到了它还赖着）—— 不是靠一个猜出来的返回值。
           ⚠️ **不用 `asyncio.wait_for`**：它超时那一刻会去 `await` 那一条
              （`_cancel_and_wait`）⇒ 同一种咽法**照样把它挂住**，上界形同虚设。
              `asyncio.wait` 只等它自己那个计时器，到点就回（那条 task 留在 pending）。

        ⚠️ **第 3 刀：本方法今天停**两个**任务**（上面那三条保证**照旧成立**，逐条对得上）：
           · **主循环那一条**（`self._task`）：③④ ＋ I2 ＋ I3 —— 就是上面那些；
           · **那个驱动**（`self._pacing_task`）：`self.pacing_stop.set()` ＋
             `self._settle_pacing()`（有上界地等、取走下场、**不抛**）。
           ⇒ 三条保证在这两半上是**同一个形状**，而那不是巧合：两半都是"置一个信号 ⇒
             在一个有上界的等待里等它自己收手 ⇒ 取走它的下场 ⇒ **没有 `except CancelledError`**"。
           ⚠️ **两半的先后是有意的**：驱动那一半**先做** —— 它是**同步**的置信号
             （先让"别再滴了"落地），之后才去取消主循环那一条**正在干活**的。
           ⚠️ **而那个先后今天带着一笔账**（Minor 2）：见 `_OWED["stop_order"]` ——
             "先 `_settle_pacing()`、后 `cancel()`"是**今天这样跑得通**，不是"将来也这样"：
             S8-B 把活挂到到期处理器上之后，那一次 `_settle_pacing()` 会先撞上
             `_grace_ms` 那道上界（驱动被 handler 拖住）⇒ 主循环那条要等到那时才被取消。
        ⚠️ **它照样不封库**（`loop.seal()` 不在这里）：封库说的是"从此一只都不许写"，
           而本条讲的是"这一台机器到此为止"。 两者**先后有讲究**（Task 1 评审判下的 M2）：
           **先 `stop()`、后 `seal()`** 才是顺的；反过来时 `due` 每一拍都撞门卫 ⇒
           驱动收工并报一声 `"sealed"`（那不是故障，是那个次序 —— 见 `_start_pacing`）。

        ⚠️⚠️ **一条必须带下去的事实**（Task 1 评审批下来的；下一个读的人会读成相反的）：
           **不是**所有取消都以 `RoundCancelled` 回来。真正**落在流中间**的那一次，
           `transport` 在 **chunk 边界**提前 `return`、**没有终态事件** ⇒ 接着抛的是
           `ResponseProtocolError`（`stream.py` 那句 "stream ended with no terminal event"，
           **既不包装、也不进重试**）；而那一轮若**已经外化过内容**，抛的是
           `IrreversibleError`。**只有重试环自己那两处检查看到信号，才掷 `RoundCancelled`。**
           ⇒ 谁在 `stop()` 之后去看那一轮的结局，都得**自己读 `signal.is_set()`** 再下结论：
              **类型在这里讲不出"取消"这件事**，而按措辞分支是本仓明令禁止的
              （理由见 `generate.py` 里 `RoundCancelled` 那段）。
        """
        self.signal.set()                      # ③
        self.loop.stop()                       # 它做清单里的 ①②
        # **第 3 刀：第二个任务**（那个驱动）—— 它**不归上面那份清单**（那是另一台机器）。
        #    ⚠️ **先置、再等**，而这两件事的纪律各有出处：
        #      · `pacing_stop.set()` 是**同步**的：置上之后 `drive` **一轮都不再滴**
        #        （它每一轮开头看它），而**当次那一觉当场被叫醒**（`_race` 里那一臂）。
        #        关机路上最要紧的正是"别再滴一下" —— 那一滴会让一只闹钟**在关机中被叫走**
        #        （取走 ＋ 落盘 ＋ 跑 handler），而盘上那张表正是"下次开机看到的东西"。
        #      · 等它收手那一段**有上界**（同一个 `_grace_ms`），而"它到底收工了没有"
        #        与主循环那一条同一条纪律：问 `app._pacing_task.done()`。
        #    ⚠️ **它与"封库"的先后（Task 1 评审判下来的 M2）**：**先 `stop()`、后 `seal()`**
        #       才是顺的 —— 反过来的话，`due` 每一拍都撞门卫（`SealedError`）⇒ 驱动**收工**
        #       并报一声（`"sealed"`，那不是故障，是那个次序）。而 `stop()` 自己**不封库**：
        #       封不封是入口那一层的决定（本刀没接那条线）。
        #    ⚠️ **不许咽掉"`stop()` 自己被取消"**：这一段里**没有 `except CancelledError`**
        #       —— 与下面那一半同一个形状（⑤ 那条测试钉的正是这个形状）。
        self.pacing_stop.set()
        await self._settle_pacing()
        task = self._task
        if task is None:                       # ④
            return
        if task.done():
            # **已经 settle 过 ⇒ 幂等**（见 docstring 里 I2 那一段）：不去重放它的结局。
            self._retrieve(task)
            return
        task.cancel()
        try:
            # **有上界地等它收手**（I3）：到点就返回，不拿那条 task 当"必须等到的东西"。
            await asyncio.wait({task}, timeout=self._grace_ms / 1000)
        finally:
            # 两条路都取走一次（I2）：**被取消**那条路也要 —— 否则那笔错没人读过。
            self._retrieve(task)

    @staticmethod
    def _retrieve(task: asyncio.Task[Any]) -> None:
        """把那条 task 的下场**取走**，但**不抛给调用方**（I2 的修法就在这一句）。

        ⚠️ **为什么要取**：不取的话它那笔错从没被人读过 ⇒ 解释器在回收它时会打一句
           `Task exception was never retrieved` —— 一条**假警报**，而错误的告警比没有告警
           更费下一个人的时间。
        ⚠️ **为什么取到也不抛**：见 `stop()` 的 docstring（关机这条路不该被它的旁观者打断）。
        ⚠️ **诊断没丢**：那个异常对象仍在 `task.exception()` 上 —— 谁想看，随时可问。
        ⚠️ **两条 task 都走这里**（主循环那一条 `Task[None]` ＋ 那个驱动
           `Task[StopReason]`）⇒ 形参是 `Task[Any]`：它们的**返回值类型不同**，
           而"取走下场"这件事对两条是一样的。
        """
        if task.done() and not task.cancelled():
            task.exception()


def build(*, take_batch: Any, run_round: Any, flush_handoff: Any, batch_end_check: Any,
          store_root: Path | None = None, log_root: Path | None = None,
          pacing_root: Path | None = None, unresolved_root: Path | None = None,
          event_store: Any = None, runtime_evidence: Any = None,
          timers: Any = None,
          clock: Callable[[], str] | None = None,
          inject_notice: Callable[[str], Awaitable[None]] | None = None,
          hint_text: str | None = None,
          console: Any = None,
          processing_owner: Any = None,
          memory_handlers: Mapping[str, Callable[..., str]] | None = None,
          **rest: Any) -> App:
    """构造一个独立运行时，集中完成回调、状态读口与存储门的接线。

    显式回调覆盖默认回调；processing_owner 用于批末方法被包装的场景，
    未提供时兼容直接传入的绑定方法。所有存储共享此 Loop 的实时 sealed 状态。
    App 创建后通过一次性公开接口绑定输出，运行后不再允许重新接线。
    """
    signal = rest.pop("signal", None)
    if signal is None:
        warnings.warn(
            "build() 没拿到 signal ⇒ 这里新造一个，而**装配里没有人把这一份递给 round.py**"
            "（round.py 那一半接上了 —— `make_run_round(signal=…)` 收得下它，缺的是把"
            "**同一个对象**递过去的那个调用者，见 round._OWED 的 signal_source 那一笔）"
            " —— 谁把那条链接上，谁就来删掉这句警告。",
            UserWarning, stacklevel=2)
        signal = asyncio.Event()
    grace_ms = rest.pop("grace_ms", _STOP_GRACE_MS)
    if "attach_hint" not in rest and (inject_notice is None) != (hint_text is None):
        warnings.warn(
            "build() 只拿到 `inject_notice` 与 `hint_text` 里的**一半** ⇒ `attach_hint`"
            " 那一格留在点名接缝上（跑到第 soft_rounds 轮时会当场抛）。两样要一起递："
            "`build(inject_notice=handoff.inject_notice, hint_text=…)`。",
            UserWarning, stacklevel=2)
    if console is not None and "deliver" in rest:
        warnings.warn(
            "build() 同时拿到 `console=` 与调用方自己的 `deliver=` ⇒ 投递那一格归"
            "**调用方**（本函数不静默顶掉递进来的东西），而那个 console **一个字都不会打**。"
            "要那个出口就把 `deliver=` 去掉；要自己那一份就别递 `console=`。",
            UserWarning, stacklevel=2)
    slots: dict[str, Any] = {
        "deliver": _not_wired("投递面", "一个还没写的投递层"),
        "maintenance": flush_handoff, # Task 1 接上的那一步（见上面那一大段）
        "flush_boundary": _not_wired("批末那个动作", "交接与投递的共同落点"),
        "attach_hint": (
            partial(inject_notice, hint_text)
            if inject_notice is not None and hint_text is not None
            else _not_wired("把提醒附上去", _HINT_OWED_BY)),
    }
    slots.update(rest)                         # 调用方给的覆盖点名接缝
    supplied_run_tools = slots.pop("run_tools", None)
    loop = Loop(take_batch=take_batch, run_round=run_round,
                flush_handoff=flush_handoff, batch_end_check=batch_end_check, **slots)

    if "batch_end_check" not in rest:
        fin = batch_end_check
        owner = processing_owner if processing_owner is not None else getattr(fin, "__self__", None)
        if owner is not None and hasattr(owner, "processing_of"):
            owner.processing_of = lambda: loop.processing # 那一句**装读口**

    gate: Gate = lambda: not loop.sealed

    store = (event_store if event_store is not None else
             (EventStore(store_root, is_open=gate) if store_root is not None else None))
    log = LogStore(log_root, is_open=gate) if log_root is not None else None

    if clock is None:
        clock = _now_iso
    timers = (timers if timers is not None
              else (TimerStore(pacing_root, clock=clock, is_open=gate)
                    if pacing_root is not None else None))
    if isinstance(timers, TimerStore):
        timers.bind_owner_gate(gate)
    unresolved = (UnresolvedStore(unresolved_root, is_open=gate)
                  if unresolved_root is not None else None)

    if unresolved is None:
        tools = None
        dispatcher = None
    else:
        bound = dict(unresolved_handlers(unresolved))
        bound.update(memory_handlers or {})
        tools = build_table(DECLARED_TOOLS, bound)
        dispatcher = Dispatcher(tools, runtime_evidence=runtime_evidence)
        if supplied_run_tools is None:
            supplied_run_tools = make_run_tools(dispatcher)

    app = App(loop, signal=signal, store=store, log=log, gate=gate,
              timers=timers, clock=clock, unresolved=unresolved,
              tools=tools, dispatcher=dispatcher,
              run_tools=(supplied_run_tools if supplied_run_tools is not None
                         else _not_wired("跑工具", "S6")),
              grace_ms=grace_ms)
    if timers is not None:
        timers.on_due(app._on_due)

    loop.bind_outputs(
        flush_boundary=app.flush_boundary if "flush_boundary" not in rest else None,
        deliver=(app.deliver if console is None else partial(console.deliver, ledger=app))
                if "deliver" not in rest else None,
    )
    return app
