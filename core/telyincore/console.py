# telyincore/console.py
"""最小控制台 · **出口**（第一段 Task 1）—— 那些话打出来。

⚠️⚠️ **这个模块今天只做一件事**：把 `App.delivered` 上那些东西**变成一个看得见的出口**。
    它的形状**拿实物读出来的**，不是按名字猜的（这一支栽过两次）：

```
① **接谁**：`app.py:301` 的 `self.delivered: list[tuple[Any, ...]] = []` —— **一批一项**，
   写成它的是 `App.deliver`（`app.py:371`）：一批**原样**进账（`tuple(batch)`，不复制、
   不渲染），空批不进账、回 `False`。
② **那一批从哪儿来**：`loop.py:132` 那句 `if await self._deliver(batch):`
   —— 而 `build()` 末尾（`app.py:1248`）把那一格接成了 `app.deliver`
   ⇒ 出口接进来，走的就是**同一条流水线**，不是另造一条入路。
③ **那个 `bool` 是什么意思**（别读成"你收到了"）：它答的是「这一批接下了吗、要不要往下
   跑轮」—— 出处逐条在 `App.deliver` 的文档上。
```

 **"什么时候打"——拍在这里**：**递送那一下、一批一次**（不是"跟着 `delivered` 一项项长
  出来就逐条打"，也不是"等这一批跑完再打"）。理由是**接谁**决定的：`App.deliver` 在
  `loop.py:132` 上**每一步批里只走一次**（批首，在跑轮**之前**）⇒ "一颗心跳"就是
  `loop.py:132`，而出口挂在它上面，于是**账上长出一项 ⇔ 屏幕上多一批**，两件事同一拍。
  ⚠️ 三样都写清：**一批一次** ／ **那一刻是批首**（不是批末 —— `:139` 的批末那一下今天
  一个字节都不写，见 `App.flush_boundary`）／ **一批里几项就几行**（第 1 项带批号，其余照排）。

⚠️⚠️ **出口是注入面**（`Console(write=…)`，默认 `_to_stdout`）—— 与这棵树里每一个面同一条
    纪律：**默认打到标准输出，而测试递一个假的**。理由是量出来的两条：
    · 直写 `sys.stdout` 会把"出门那一行"与"批有没有接上"**混成一个判据**（测试要捕它、
      而捕 stdout 是脆的、还挑终端与编码）；
    · 而 `write` 一注入，"**打了什么**"就成了一条当场可判的事实（`tests/test_console.py`）。
     `write` **一帧一次**（收的是**一帧**，不是一批）：于是"出口到哪个流"这件事与
      "一批分成几帧"这件事**各自只有一个答案**。
       **而"谁说的 ／ 哪一刻"从本刀起是帧自己的字段**（`_frame_for` 那张表就是线格式的
         正本）：它们**不在正文里** ⇒ 出厂那条路上打出来的仍是**正文**
         （与用户自己打的那一行逐字节相同），而**页那一头读的是同一帧的那两格**
         （`chat.ts::asFrame` ／ `tests/test_web.py::_read_frames`）——
          两个读者读的是**同一份字段**，于是"剥"这件事**没有了对象**（本刀删掉的那几件
         机器就是它）。
          于是**注入面一个字都不动**：`Console(write=…)` 收到的照旧是"一帧"
         （判据见 `tests/test_console.py` 那条 `…prints_the_frames_text_without_any_marker`：
         两个读者各自拿到什么）。

⚠️⚠️ **四、本刀不处理的那一笔 —— `_DELIVERY_OWED_BY` 的 ④「只涨不落」**（明写，不静默）：
    `App.delivered` 仍然**只涨不落**，本刀**不给它上界、也不给它排水口**。依据是
    `app.py` 自己立的规矩（`_DELIVERY_OWED_BY` 那段 ④ 的末段）：**排水口归那条投递通道的
    接线方** —— 谁接上外边那一条，谁就同时收下"读这本账 ＋ 把已经送出去的拿走"这两件事
    （只读不拿 ＝ 让 ④ 继续涨；只拿不读 ＝ 丢掉那本账存在的理由）。
    ⇒ **归第二段**（外边那一条通道 ＋ 它的接线方）。本刀能做、也做了的是**点名**：
      那个常量就是这一笔的名字。⚠️ 而这条不是"本模块欠着"（本模块**不碰那本账**：
      出口只**打**它收下的那一批，从不读、也从不从 `App.delivered` 里拿东西）——
      它是**那本账自己那一笔**的欠主，在这里点名以免那一句只活在代码注释里。

⚠️⚠️ **本刀不做的两件事**（各归各的任务，别把这一页读满）：
    · **进来那一条**（一行 ⇒ 一批）归 **Task 2**：走的是**现成的**机械
      `feed.next_batch(bus, ready)`（`feed.py:21`，它只按形状用 `bus.take_if_ready()`）
      —— **不许另造一条入路**（两份真相）；
    · **引擎那一条**（`Config` ＋ `build_body` ＋ `HttpxTransport`）归 **Task 3**。

⚠️ **本模块不碰事件循环**（`asyncio.run` 在树里**只许被调用一处** —— `cli.py:54`，
    `loop.py` 抬头那句"循环归入口所有"就是那条纪律的出处）：这里的 `deliver` 是一个
    **被调用**的协程，谁在跑它由入口决定。
     **Task 3 之后这一条照旧**：本模块交出的那个入口是**一个协程**（`main()`），
      起循环的那一句在**命令**里 —— 本模块没有、也不许有那个调用。

────────────────────────────────────────────────────────────────────────────
 **Task 3：那个入口**（装 ／ 起 ／ 泵 ／ 打 ／ 停）—— 本模块现在是**一个能起的进程**。
    ⇒ `assemble(root=…)`（装）／ `run()`（起 ＋ 起来之后一定停）／ `stop()`（停）
      ／ `main()`（那个协程；起循环的那一句在命令里，见 `main()` 的 docstring）。

 **接线接在哪一处 —— 读出来的，不是按名字猜的**（这一条是承重的）：
    那个调 `generate` 的地方只有**一处**：`round.py:119` ／ `:121` —— `make_run_round`
    里那个闭包用**递进来的** `config` ／ `transport` ／ `signal` 呼 `generate`。
    而 `make_run_round` **在生产里此前没有调用者**（只有测试在调 —— 那一笔欠在
    `round._OWED["signal_source"]` 上，而它点名的欠主就是"装配那一层"）
    ⇒ **装配那一层就是那个调用者**，"把 `Config` ＋ `HttpxTransport` 接到那一处"
      这件事**只能**发生在入口里（`build()` 收到的 `run_round` 是**造好了才递进去的**）。
     **引擎那一层一个新零件都没造**：`load_config()` ／ `build_body()` ／
      `HttpxTransport` ／ `make_run_round` 全是现成的（本刀只把它们递到一起）。
     而 `signal` 那一半也在这里还上：`assemble()` **先造那一个 `Event`**，再把它递给
      **两处**（`build(signal=…)` 与 `make_run_round(signal=…)`）—— 那两处**必须是同一个
      对象**（`round.py` 收得下它，而"递进去的必须是那一份"就是那一笔欠账的第一句）。

 **两笔本刀补上、而仍然欠着的账**（各自的欠主写在常量上，机器可判）：
    · `_BATCH_TO_SESSION_OWED_BY` —— **"谁把你打的那一行写进会话"**：全树**没有**这个桥
      ，而少了它
      `round.py:113` 发出的请求里**没有你说的话** ⇒ "这一套能说话"这句话根本不成立。
      本刀在**取批那一步**补了最小的一步（`_take_a_batch` ⇒ `_into_the_session`）。
    · `_DECLARATION_OWED_BY` —— **声明侧那一笔**。（★ 2026-10-12 **结掉** —— 下面重写。）
      **它不是"缺内容"，是"接口留白"**（作者 2026-10-12 当场拍："结"）：
      请求里那几段"合成开头" = **它的人设那几段**（"它是谁"那一块），而**今天已经在放了** ——
      `_a_head_cell` 的 `head is None` 那一支**每一趟请求现装**（`_the_prefix_head(persona_root…)`，
      盘上若已经有同一份就交回空）；**不必等交接**（交接重置时那一次照旧有：`build_prefix`）。
      ⇒ 缺的只是"**从外面递一份进来**"那个插口：本入口把口留着（`assemble(head=…)`），
      **不替谁编一句**（缺省那条路读的是它自己的那一棵树，够用）。
      ⚠️ **那一句原来是"今天仍然没有来源"** —— 那句话读起来像"少了一块"，而实际是"没人在外面递、
      它自己读"；同一条同源的话在 `build()` 里是 `hint_text` 那一条，而**那一条 2026-10-12
      已经接上了**（跑到第 `SOFT_ROUNDS` 轮时那句提醒 —— 见 `handoff.SOFT_ROUNDS_NOTE`）。

 **谁先停、谁等谁**（"三个任务"里其实只有**两个** `asyncio.Task` —— 读出来的）：
    · **主循环那条**（`App._task`）—— 那个泵**不是**一条 task：它是 `take_batch`
      那个协程，在**主循环那条 task 里**被 `await`（`loop.py:171`）；
    · **那个驱动**（`App._pacing_task`，只有给了 `pacing_root` 时才有）。
    ⇒ 收工次序：**主循环先自己收工**（读法到头 ⇒ 泵交回 `None` ⇒ `loop.run()` 退出
      ⇒ `App.run()` 返回），**那个驱动后收工**（`run()` 的 `finally` 调 `stop()`：
      置 `pacing_stop` ＋ 有上界地等它）。 而 `App.stop()` 内部**先停驱动、再管主循环**
      （那两句话是它自己的，本层一个字都不改）。
    ⚠️ **那个泵占住事件循环**（Task 2 自己点名的那条边界）：默认那个读法是**同步**的
      ⇒ **你打字的时候，驱动不滴答**；而那一路的**代价**（以及为什么本刀选它、
      不换成线程里读）写在 `run()` 的 docstring 上 —— 那里是下一批读者要看的那一页。

 **两句话都要带下去（照设计稿 §四①）**：**"这一套能说话"这一条从本刀起能被验**
   （一批出去 ⇒ 看得见），而 **"有个界面"仍然不是** （服务 ／ 页面 ／ 路由 —— 那归第二段）。
"""
from __future__ import annotations

import asyncio
# 那一本账落一行要用它（2026-10-05 作者拍的"甲"）： 我第一版**忘了这一行**
#   ⇒ `POST /handoff` 当场 `NameError`（那一条 traceback 就是它）。
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .feed import next_batch
from .input_budget import hard_input_tokens

#: **泵每一跳的上界（秒）** —— 见 `Console.pump` 那段"`idle_s` 是这条链上唯一
#: 的活性来源"：没有它，那条链在"没装 `on_ready` 的总线 ＋ 一个阻塞的读法"上**会停住**
#: （标准输入那头打 Ctrl-D，而那个进程不动）。 这个数是**拍的**：对"你的手"够细
#: （四分之一秒），对"一个空闲进程每四分之一秒醒一次"也够粗。真入口可以调它。
#: ⚠️⚠️ **那个"醒一次"的代价就记在这个数旁边**（本波 Minor #3）：
#:   · **4 次／秒**（`1 / 0.25`）—— 而 `pump` 在**每一跳**上都走一次
#:     `asyncio.wait_for(next_batch(...), idle_s)` ⇒ 空闲时就是**每秒 4 次**"排一个上界、
#:     到点、再把那一探取消"。
#: · **一天 ≈ 34.6 万次**（`4 × 86400`）。 这是**量级**：它换来的是"输入到头看得见"
#:     （一个**阻塞的读法**上**唯一**看得见的方式 —— 见 `Console.pump` 那段）。
#:   · ⚠️ **它今天有多明显**：默认那个读法是**同步**的（`_stdin_line` 走 `sys.stdin.readline()`）
#:     ⇒ 没在打字的时候，循环就停在那一次读上 ⇒ 这一跳**看不到**什么（它不是忙等）。
#:     谁把那个读法换成"不占住循环的那一种"（线程里读 ／ 一个消息队列），
#:     这 4 Hz 就**第一次真的变成**每秒 4 次循环唤醒 —— 那时该调的就是这个数。
#: · **要调它的人在这里改**：它是参数（`pump(idle_s=…)`），而缺省值就是这一行。
#: **2026-10-11（乙）**： 读法可以交回**这一个值** ——
#: 意思是「这会儿没有一行，但**总线有活儿**」。
#: ⚠️ 一个值一个意思 ： `""` 与 `StopAsyncIteration` 照旧**只**表示「到头了」。
CHECK_THE_BUS: Any = object()

_DEFAULT_IDLE_S = 0.25

#: **`_DELIVERY_OWED_BY` 那第四样（"只涨不落"）的欠主** —— 本刀**明写"不处理"**，
#: 成法照 `app._DELIVERY_OWED_BY` ／ `app._HINT_OWED_BY`：**一个名字 ＋ 一个欠主**。
#: ⚠️ 为什么本模块**不替它造上界**（不该在这里修的理由有两句，都要读）：
#:   ① 上界该长在**排水口**上，而排水口的地址归那条投递通道的接线方（出处见
#:      `app._DELIVERY_OWED_BY` 的 ④）；本模块只是个**出口**，它连那本账都不读；
#:   ② 一个没有排水口的环 ／ 一个丢数据的截断，会把"账上有几批"这个问题变成**两个答案**
#:      —— 与 `App.deliver` 里"空批不记账"同一条理由（那一句也是为什么这里对空批
#:      **一个字都不打**：出口上多一个空行，就是同一个问题的第二个答案）。
#: ⚠️ **谁把外边那一条通道接上，谁就来改这一句**（把它改成"归谁 ＋ 接在哪"，或者删掉）——
#:    一条已经兑现的欠账留在树里就是一句假话（`_OWED` 那张表立过的规矩）。
_DEBT_OWED_BY = (
    "`App.delivered` ④「只涨不落」：那本账仍然**没有排水口、也没有上界** —— "
    "本刀（最小控制台 Task 1）**不处理**它，归**第二段**（外边那一条投递通道 ＋ 谁接上它，"
    "见 `app._DELIVERY_OWED_BY` 的 ④）。本模块只**打**自己收下的那一批，"
    "从不读 `App.delivered`、也从不从它里面拿东西（出口不是排水口）"
)

# 外部事件在取批时投影为会话记录，有正文的事件各自写入 user 记录。
# 视图字段由 _the_view_fields_of 计算；不要将事件接收、会话持久化与对外送达混为一谈。
_BATCH_TO_SESSION_OWED_BY = (
    '「**外部事件 ⇒ 会话**」那一层：一批信封按什么规则落成 `note.Record` —— `internal` ／ `external` 各落成什么、要不要按批合一条、同一笔要不要落事件库。★ **2026-10-12 那几格里有四格定了**（作者：「五个都填」）：`is_system` ／ `is_flow` ／ `is_ephemeral` ／ `is_speak` 由 `_the_view_fields_of` 算（逐格理由在那个函数上）；`is_head` 归 `prefix.assemble`（合成开头那一条不走这一条路）。 ⚠️ **仍然没定的两样**：① **要不要按批合一条**——今天**一件一条**；② **同一笔要不要也落事件库**。Task 3 的入口只补了最小的一步。'
)

#: **声明侧那一笔（请求里那句合成开头）今天仍然没有来源**。
#: ⚠️ **本刀把那一格的缺省填上了**，而填进去的**不是**合成开头：是**那一句话**
#:    （persona 那几段订成的 system 前缀 —— 每请求现装，见 `_a_head_cell`）。
#: 于是"**那条根从第一句话起就在请求里**"这件事成立了； 而合成开头照旧欠着，
#:    它有了来源就落在那一句话**之后**（那一格交回的是一串）。
#: ⚠️ **`head` 那一格是"每次出请求现取一次"的插口**（`make_run_round` 那个槽，
#: `session.outbound(head)` 把它插在连续 system 之后）—— 本刀**没有**改那一格的
#:    形状，只改了它的**缺省**（空那一串 ⇒ `None` 哨兵）。
_DECLARATION_OWED_BY = (
    "**声明侧**（人设 ／ 那一批声明）：请求里那句**合成开头**（`head.head(items)` 要的那几个 "
    "`Item`）—— 树里今天没有它的来源，而它**不是**这一层该编的内容（循环不写散文那条家规）。"
    " 插口已经在了：`Console.assemble(head=…)` —— 而它**今天装着那一句话**（每请求现装）；"
    " 合成开头有了来源就落在那一句话之后（那一格交回的是一串）。"
)

#: **入口那套状态落在哪个目录**（会话 ／ 事件库 ／ 闹钟表各一格）。
#: ⚠️ 那个名字是**树里已经写过的那一个**：`session.py:207` 记着
#:    「由接线那一层给：`05:150` 的 `data/session-main.jsonl`」⇒ 这里不另发明一个目录名
#:    （同一件事只有一个答案）。
#: ⚠️ 而它**是一个参数**（`assemble(root=…)`）：测试递 `tmp_path`（仓里一行都不写），
#: 而一个真进程用缺省那个。 三个子目录名（`events` ／ `timers`）是**本入口自己的**安排
#:    ——树里没有第二处写过它们，所以只写在这一行上。
_DEFAULT_ROOT = Path("data")

#: **那五段的名字**（2026-10-05）—— 顺序就是链上的顺序 ：
#:   说话 → [**asr**] → 收到 → [**thinking**] → [**output**] → [**tts**] → 听到
#: `answer` ＝ **思考 ＋ 输出**（那一格显示的 ／ 2026-10-05 作者："显示的是思考 和输出"）
STAGE_KEYS = ("asr", "thinking", "output", "tts", "total", "answer")
#: **只有这两格归插件**（`asr` ／ `tts`）—— 其余三格由引擎那一路填 。
PLUGIN_STAGE_KEYS = ("asr", "tts")

def _now_ms() -> int:
    """ **这一层唯一的那个毫秒钟**（墙上时间，毫秒整数）—— 三处用它，一处写。

    ⚠️ 三个消费者要的是**同一种方言**：总线那套合批数学（`bus.py` 注入的 `clock`）／
       交接的切点（`handoff` 的 `now_ms`，落进 `pressure_warned_at`）／会话记录的时间戳
       （`note.Record.ts_ms`）。在三个地方各写一份 `int(time.time() * 1000)`
       就是三份会漂的东西。
    ⚠️ **它不落进任何判据**：测试一个字节都不读它（那条"不许依赖墙上时间"说的是判据
       不许落在时刻上 —— 而这里只是**记下来**）。
    """
    return time.time_ns() // 1_000_000

def _now_at() -> str:
    """ **说出去那一刻的钟点** —— `HH:MM`，写进那一帧的 `at` 那一格。

    ⚠️ **出厂的那一道**（读一次现在）—— ⚠️ **判据不读它**：那一刻由调用方递给 `say`
       的 `at`（"钟可注入"，与 `_now_ms` 同一档写法）。
    ⚠️ **它只在缺省那一格被叫**（`say(text, at=…)` 递了就一个字节都不读它）⇒
       "那一刻是判据的一部分"那几条全落在**递进去的那个值**上，钟不参与。
    """
    # **2026-10-09 同一个形状**（与 `_at_of` 一起改的 ）： 三个生产者
    # （`_at_of` ／ 这里 ／ `history`）**必须交同一种形状** —— 不然收帧那一侧
    # 就要认三种 。
    # ⇒ 交 `"2026-10-02T09:00"` 这样的整串 （ "是不是今天"**由入口判** ）。
    # ⚠️ "读一次现在"那件事**没变** （ 还是一次 `strftime` ）； 判据照旧不读它 。
    # ⚠️ **这一句 `return` 删不得** —— 我 2026-10-09 那天就把它连同注释一起换掉过
    # ⇒ 本函数**静默交回 `None`** ⇒ "另一头那一帧"的 `at` 当场变空 、
    # 而外层**照样跑**（`say()` 把 `None` 原样写出去了 ）⇒ 两条判据红 。
    return time.strftime("%Y-%m-%dT%H:%M")

def _a_stamp() -> str:
    """ **留一版历史时那一串时刻** —— `YYYYMMDD-HHMMSS`（形状与 `web._STAMP` 同一条）。

    ⚠️ ⚠️ **它只在一个地方被叫**：整理态把交回来的那两段写下去的时候
        （`SaveHersText` 那一扇门要一个 `stamp`）。 而**那一刻由这里造、由梦递过去**
        —— 梦自己不读钟（`consolidate.py` 抬头那条纪律），于是"哪一秒"只有一个出处。
    ⚠️ **形状是两处一起看的**：`web.py::_STAMP` 是**验**（`POST /persona` 递进来的那一串），
        这一格是**造** —— 有一条判据拿这里造出来的串去过那一个正则
        （`tests/test_consolidate.py`）：两处漂开的那一天它会红，而不是等到"页上多出一个
        没人认得的 `.history-` 目录"。
    """
    return time.strftime("%Y%m%d-%H%M%S")


#: **"流程类"那几样**（2026-10-12）：机器自己推着走的 —— 心跳 ／ 交接 ／ 整理。
#:   ⚠️ 判据取的是**信封上那两个名字**（`type` ／ `source`），不是正文里写了什么：
#:      正文是散文，而这两个名字由产生方给（`_inject_a_note` ／ bus 那一层）。
FLOW_TYPES: tuple[str, ...] = ("tick", "handoff", "consolidate")
FLOW_SOURCES: tuple[str, ...] = ("handoff", "consolidate")


def _the_view_fields_of(item: Any) -> dict[str, bool]:
    """ **一条记录那几格视图的写手**（2026-10-12 作者：「五个都填」）。

    逐格说清"值从哪来"（每一步都有出处，没有一处是猜的）：
      · `is_system`    = `origin == "internal"`（机器自己注入的那一类）；
      · `is_flow`      = `type` ／ `source` 落在机器那几类里（心跳 ／ 交接 ／ 整理）；
      · `is_ephemeral` = 信封上那个 `ephemeral`，**或者**是心跳那一条 ——
        它下一批进来就被回滚（那段回滚逻辑就在 `_into_the_session` 头上）；
      · `is_speak`     = **这一条路上恒假**（进这一条路的都是"外面来的"）；
        真正那一位写手在 `round.py`：它说出去的那句话落记录时带 `is_speak=True`；
      · `is_head`      = **这里也不填**：合成开头那一条由 `prefix.assemble` 自己标
        （它不经过这一条路 —— 见 `prefix.py` 末尾那一段）。
    ⚠️ **五个格子各有其主**（不是"一处全包"）：会话这一侧管三格，`round.py` 管一格，
       `prefix.py` 管一格 —— 谁是那条记录的造者，谁就标自己知道的那几格。
    """
    kind = str(getattr(item, "type", "") or "")
    source = str(getattr(item, "source", "") or "")
    return {
        "is_system": str(getattr(item, "origin", "") or "") == "internal",
        "is_flow": kind in FLOW_TYPES or source in FLOW_SOURCES,
        "is_ephemeral": bool(getattr(item, "ephemeral", False)) or kind == "tick",
        # ⚠️ **这一条路上恒假**（进这一条路的都是"外面来的"）—— 真正那一位写手在
        #    `round.py`：它说出去的那句话落记录时带 `is_speak=True`。
        "is_speak": False,
        "is_head": False,
    }


def _has_the_soft_limit_door(handoff: Any) -> bool:
    """ **那一份交接有没有"软阈那句提醒"那扇门**（2026-10-12）。

    ⚠️ **为什么要有这个判据**：那三样（`inject_notice` ／ `on_soft_limit_note()` ／
       `soft_rounds`）是 `handoff.Handoff` 上的公开面；而判据里递进来的替身、
       或者别的实现**不一定有** ⇒ 齐了才递，不齐就**一个字都不递**
       （行为逐字与从前一样：那一格照旧留在接缝上）。
    ⚠️ **不抛、不猜**：缺哪一样就回假 —— "没有那扇门"是一个合法状态
       （与"不给某个 root 就没有那本账"同一条）。
    """
    return (callable(getattr(handoff, "inject_notice", None))
            and callable(getattr(handoff, "on_soft_limit_note", None))
            and isinstance(getattr(handoff, "soft_rounds", None), int))


def _later(ms: int, cb: Callable[[], None]) -> Any:
    """ **总线那一条计时**（`bus.WakeBus` 注入的 `schedule`）—— 排在 `ms` 毫秒之后调 `cb`。

    ⚠️ **它按"那一刻"取当下那个循环**（不在 import 时抓一个副本）：`assemble()` 是**同步**
       的，而它可以在一个还没有循环的地方被调 ⇒ 抓早了会拿到 `None`。
    ⚠️ **必须是同一条线程**：`bus.py:213-216` 那条契约点名的就是这个 —— 跨线程的话
       `on_ready` 那一下**不会报错，只会让那座桥永远不醒**。
    """
    return asyncio.get_running_loop().call_later(ms / 1000, cb)

def _cancel_later(handle: Any) -> None:
    """ 撤掉上面排的那一个（`bus.WakeBus` 注入的 `cancel`）—— `TimerHandle.cancel()`。"""
    handle.cancel()

def _to_stdout(frame: Any) -> None:
    """ **默认那个出口**：把**一帧**打到**标准输出**。

    ⚠️ **取 `sys.stdout` 那一刻的**（不是 import 时抓一个副本）：于是"默认打到标准输出"
       这件事在测试里当场可判（换掉 `sys.stdout` 即可，不必有终端、不必有编码）。
    ⚠️⚠️ **它读的是那一帧自己那两格**（`kind` 与 `text`，见 `_frame_for` 那张表）——
        **终端上要的是正文**（那个头一帧也是照它的正文打：`[批 N]`），而"谁说的 ／
       哪一刻"是**另一件事**（终端上从来没有它们，见 `_frame_for`）。
       ⚠️⚠️ **这里没有"剥"这一步** —— 本刀之前它剥的是 `_mark_line` 贴上去的那一截，
       而那截记号从本刀起**不存在**（它整件被删了）：正文就是正文。
    ⚠️ **多行正文照原样打**（`write` 一帧一次，而这一帧的正文里可以有换行）——
       出口不替谁折行、也不替谁去掉换行（`_text` 的抬头逐字写着这一条）。
    ⚠️ **`write` 写的是字节**：一次 `sys.stdout.write` —— 不打二次、不加前缀、不吞异常。
       （写不出去 —— 非 UTF-8 控制台、管道那头关了 —— 是**本端的事**，`cli.py:58-61`
        那一节逐字写过为什么它不该被报成"引擎坏了"。这里照同一条：**让它出去**。）
    ⚠️ **它照旧认"一帧"这一个形状**（一个映射）：`web.py::_STOCK_WRITE` 那一对名字
       指着本函数的**名字与模块**（判据在 `tests/test_web.py`）⇒ 这一格不许换名；
        而"换成别的形状的实参"会当场抬 `TypeError` —— 那是**故意的**：
       一个不是帧的东西到了这一格，就是上游有人没走 `_frame_for`。
    """
    text = frame.get("text") if isinstance(frame, Mapping) else None
    if not isinstance(text, str):
        raise TypeError(
            f"出口收的是**一帧**（一个映射，见 `_frame_for` 那张表），收到的是：{frame!r}")
    sys.stdout.write(text + "\n")

async def _stdin_line() -> str:
    """ **默认那个入口**：从**标准输入**读**一行**（交出去的是**带行尾**的那一行）。

    ⚠️ **取 `sys.stdin` 那一刻的**（不是 import 时抓一个副本）—— 与 `_to_stdout` 同一条纪律：
       于是"默认从哪儿读"这件事在测试里当场可判（换掉 `sys.stdin` 即可，不必有终端）。
    ⚠️ **行尾在这里留着**（`readline()` 的原样）：去掉它的是 `Console.pump` 那一步 ——
       本函数只管"读一行"，而"一个换行算不算话的一部分"是**泵**那条语义（→ 见那里，
       本刀的测试量过：不去掉就会带着走）。
    ⚠️ **是同步的 `readline()`** —— 它是这一层**已知的**那一条边界（见 `Console.pump`）：
       终端上没有别的东西在跑，阻塞那一会儿是这一层**唯一**的活；
       而在别处（真入口）要让它不占住事件循环，得换一个读法（一个线程里的 `read1` 之类）。
       ⇒ **别把一个同步读悄悄搬进一个真在跑别的协程的地方**。
    """
    return sys.stdin.readline()

def _split_a_line(line: Any) -> tuple[str, tuple[str, ...] | None]:
    """ **一行可以只是文本，也可以是"带附件的一行"**（2026-10-05「图片输入」的入口）。

    ⚠️ 两种形状**只有这一处拆**（`POST /say` 那条 JSON 路交过来的是个 Mapping ）——
       而"一件东西只有一个造法"那条照旧：附件**不在这里造件**，只把正文与附件分开，
       件仍由 `_line_to_event` 造、附件由调用处 `replace` 补上 （与 `_inject_a_note` 同一手法）。
    ⚠️ 畸形的那几种（不是 Mapping ／ `text` 不是字符串 ／ `images` 不是数组）**一律当纯文本** ：
       这一层不判"你发错了" —— 那是 HTTP 那一层的事（它已经判过了 ）。
    """
    if isinstance(line, dict):
        text = line.get("text")
        images = line.get("images")
        blobs = tuple(str(one) for one in images) if isinstance(images, (list, tuple)) else None
        return (text if isinstance(text, str) else ""), (blobs or None)
    return (line if isinstance(line, str) else ""), None

def _line_to_event(line: str, *, cursor: int) -> Any:
    """ **什么样算一批 —— 拍在这里**：**一行 ⇒ 一件**（一行就是这一批的全部）。

    ⚠️⚠️ **为什么是"一行一件"而不是"一个空行终止的块"**（这是本刀要拍的两件事之一，
       另一件是 `pump` 的 EOF）：
       · **空行是一种**行 —— 它按原文进来（只有**行尾**被去掉），**不是**块与块之间的界符；
       · 依据是**这一层够得着的判据**：`App.deliver` 对空批回 `False`（`app.py:422`）
         而 `loop.py:132` 那一句是 `if await self._deliver(batch):` ⇒ **一个空批 = 一轮都不跑**。
         于是"空行当界符"得靠一条**空的**件去占位，而 `bus.push` 收的就是件、
         `take_if_ready` 只在 `_ready` 为真（队非空）时给东西 ⇒ 那条空件会**一路走到底**、
         把"空批不跑轮"变成"跑一轮空的" （要绕开它就得给总线加一条"这次是界符"的路
         —— 那是**第二条真相**，本刀不许 ）；
       · 而"**一行的行尾算不算这一行的话**"这件事由 `pump` 那一步说了算（**不算** ——
         一个行尾在读的那一步被去掉，两种结束符归一），不由本函数猜：
         一个**读到换行**的读法交出的就是**整行**（一件），一个**读到 EOF**的读法交出的也是
         **整行**（一件，只是不带行尾）—— 两种情形在这里**同一件事**
         ⇒ "打一行 ⇒ 一批"在**源上**成立，而"几行合成一批"由总线的合批说了算（见 `pump`）。

    ⚠️ **形状是照着实物写的**（`bus.WakeItem` 就是 `EventEnvelope`，`bus.py:24`；必备字段见
       `events.py:129` 那个 dataclass）—— **不 import 那个类**，与 `feed.next_batch`
       只按形状用 `bus` 是同一条纪律：这一层只造一件**对得上**的东西，不去问它是什么类。

    `cursor` 是调用方提供的临时编号。已组装的 Console 会在 pump 中先将草稿
    写入自己的 EventStore，再把库返回的原始事件交给总线；最终游标由库分配。
    单独使用 pump 的测试或适配器没有事件库，沿用调用方编号。

    ⚠️ **`origin="external"`**：这一行是**外边**来的（与 `bus.push` 的缺省规则同一条，
       `bus.py:127-128`）。 而 `pump` 那一步**显式**给 `trigger="flush"` —— 与那个缺省规则
       说的是同一件事，不是另立一档（理由见 `pump`）。

    ⚠️ **`ts` 取的是当下** —— 与 `events._now_iso` 同一条写法（本地时区 ＋ 秒级），
       而**不 import 它**：那是事件库那一层的私有件（`events.py:221`），
       而这一层与 `feed.next_batch` 一样只按形状办事。 测试不依赖它：
       测试判的是文本、次序与条数，**从不判这个时刻**。
    """
    from datetime import datetime, timezone

    from .events import EventEnvelope

    return EventEnvelope(
        cursor=cursor,
        type="message",
        ts=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        source="console",
        origin="external",
        # This marks the local console entrance, not an authenticated person.
        sender_key="console:local-owner",
        text=line,
    )

#: **线格式那四格的正本** —— 一帧就是一个映射，键恰好这四个（顺序即此表）。
#: ⚠️⚠️ **每个读者要的就是这四个名字**：页那一头从这一帧里读 `who` ／ `at` ／ `text`
#:    （`src/web/client/chat.ts`），而批号那一帧读的是 `kind` ／ `text`。
#:    ⇒ 它们是**协议**，不是本模块的私事：改一个键就是一次"两边一起改 + 说清为什么"。
#: **为什么那一刻与说话人是字段、而不是正文里的一截前缀**（本刀删掉的那一套）：
#:    正文里的前缀要**每个读者各自剥**，于是"剥"这件事在链上有三处（贴一处、剥两处），
#:    而每一处都是"这一行是谁说的"的第二个答案 —— 前两刀那四个缺陷都长在这儿：
#:    多行正文只剥第一行 ⇒ 一个 U+001F 控字符与那截前缀**画到了页上**。
#: 字段之后**没有可剥的东西**：正文就是正文。
_FRAME_FIELDS = ("kind", "who", "at", "text")

#: **帧的种类**（`kind` 那一格）—— 只有两种，而它们各对应页上一种画法。
#: ⚠️ `_A_BATCH` 那一帧**不是谁说的话**（它那两格都是空）； 而它照旧走**同一条通道**、
#:    同一个帧形状（`chat.ts::appendFrame` 按 `kind` 分两支画）。
_A_BATCH = "batch"
_A_MESSAGE = "msg"

#: **2026-10-10 作者：「7、8 这些工具…能一并显示在终端里吗。但不是以对话的形式，
#: 而是类似一种卡片，贴在上面，供我查看这批里的思考过程和调用了什么工具。」**
#: ⚠️ **为什么非分出来不可** ： 那两条记录（`function_call` ／ `function_result` ）
#: 的 `role` 正好是 `assistant` ⇒ 从前它们**照样进帧** ， 只是被当成
#: `msg` ⇒ 页上把 `{"query":"图书馆"}` 那种画成**“说话”那一支的气泡**
#: （ 经历页上看得见的那几行就是它们 ）。
#: ⚠️ **引文里略去了一个字** —— 本仓的边界规矩是「这棵树里一个都不许有」
#: 于是这一格： 它们**自己的类别** —— 页那一侧据此画**卡片** ，不画气泡 。
#: 帧里比 `msg` **多带两格**：`tool`（工具名 ）与 `stage`（`call` ／ `result` ）。
_A_TOOL = "tool"

#: ★ **2026-10-12（作者：「你直接做吧」）**：**它主动说的那句话**那一件 ——
#:   `send_private`（"主动开口：不等有人先问"）。
#: ⚠️ **它为什么不算一张卡片**：那件事**不是"动了手"，是"说了话"** ——
#:   作者那一笔的原话是"它选择说出去的那句在页上仅作为工具卡片露出一行"。
#:   ⇒ 于是 `_frames_for` 把它**画成它自己的气泡**（与它回答你时同一个 `kind` 与 `who`：
#:   页那一侧**一个字都不用改**）。
#: ⚠️ **只在 `function_call` 那一条上出气泡**：它的参数里带着那句话；
#:   而 `function_result` 那一条装的是**回执**（"这句话先落在匣子里了…"）⇒ 不进帧。
_A_SPOKEN_OUT_LOUD = "send_private"


def _the_text_she_said_out_loud(raw: str) -> str:
    """ **从那一件 SPEAK 的调用参数里取出那句话**（解析不出来 ⇒ 交回空那一串）。

    ⚠️ **它只做一件事**：把 `{"text": "…"}` 那一格取出来 —— 取不到就什么都不说
       （画一个空气泡比不画坏：那会让人以为它说了句空话）。
    """
    try:
        got = json.loads(raw or "{}")
    except ValueError:
        return ""
    if not isinstance(got, Mapping):
        return ""
    return str(got.get("text") or "").strip()

#: **两个说话人** —— 线上那一格就是这两个值，而**它们都不是页上那个名字**。
#: ⚠️ 页上那个名字从它自己的档案读（`src/web/client/speaker.ts`）： 于是改名不动这里、
#: 也不必重记冻结面； 而"两头分得开"这件事**只剩下一个依据**：这**两个不同的值**
#:    （本刀之前是那两个不同的头字符 —— 那两条回归就是这么来的）。
_ME = "user"
_OTHER = "bot"


def _text(item: Any) -> str:
    """一件东西**排成一行** —— 有正文就用正文，没有就照它的样子。

    ⚠️ **出口不解释那一批**（`loop.py` 抬头那条"只读，不解释内容"的规矩照旧）：
       它只按**一件事**分层 —— 有没有 `text`。真批那一串是信封（`EventEnvelope` 有
       `text`，`events.py:136`），而既有测试里的 `["一件活"]` 那种字符串批**没有**它
       ⇒ 后者照原样打出来（不然出口会在那些形状上打一个空行，比不打更坏）。
    ⚠️ 而**多行正文**照原样打（`write` 一行一次，而这一行的内容里可以有换行）——
       出口不替谁折行、也不替谁去掉换行。
    """
    text = getattr(item, "text", None)
    return text if isinstance(text, str) else str(item)

def _at_of(item: Any) -> str | None:
    """ **那一刻那一格** —— 取 `ts` 的 `HH:MM` 那五个字符；读不到 ⇒ **空着**（`None`）。

    ⚠️ **它不读钟**（判据不许落在墙上时间上）：那一刻是这一件**自己带的**
       （`_line_to_event` 在**读进来那一刻**盖的那一个），这里只取那五个字符：
       `"2026-10-02T09:00:00+08:00"[11:16]` ＝ `"09:00"`。
    ⚠️ **读不到就空着**（`None`）—— 字符串批那种形状（`["一件活"]`）走的就是这一格。
        本刀之前它交回的是**空那一串**：那时它要凑出"头 ＋ 分隔符 ＋ 空 ＋ 分隔符"
       那三格；现在那一格空了就是空了（`null`），没有人要在它里面切。
    ⚠️ **纯函数**（不碰流、不看钟）：与 `_text` 同一档。
    """
    ts = getattr(item, "ts", None)
    # **2026-10-09 作者：「对于现实世界里的今天的消息就不用添加日期了，如果不是，那就要
    # 添加日期，不然不好辨别。」**
    # ⇒ 原来这里**只切出那五个字符**（`ts[11:16]` ⇒ `"09:00"` ）， 日期被丢掉了
    # ⇒ 收帧那一侧**再也没有日期可判** 。
    # ⇒ 改成**把那一串原样交回**（`"2026-10-02T09:00"` ）—— 判"是不是今天"由
    # **入口**做 （ 只有那一层读钟合法 ： 本模块一次都不读钟 ， 页上也不许 ）。
    return ts[:16] if isinstance(ts, str) and len(ts) >= 16 else None

def _frame_for(item: Any, *, who: str) -> dict[str, Any]:
    """**一件东西 ⇒ 一帧** —— 谁说的 ／ 那一刻 ／ 正文，**各占一格**（纯函数）。

    ⚠️⚠️ **多行正文是这一帧正文那一格里的换行**（不是几条 `data:`）：
       帧的编码在 `web.py::_frame` 那一处，而它把这一帧**整份**编成**一条** `data:`
       （JSON 会把换行转义）⇒ 一句话永远是**一帧**、**一条 `data:`**。
        本刀之前正相反：正文被按行拆成几条 `data:`，于是**每一段都得自己带记号**
       —— 那两处机器（`_mark_line` 的按行贴 ／ `_unmark_line` 的按行剥）就是这么长出来的。
    ⚠️ **它只知道它是哪一头的**（`who` 由调用方递）：进来的那一批**全部**是输入
       （另一头的话走 `say` 那条不经过这里的出口）⇒ 那一格只从 `_frames_for` 那一处出。
    ⚠️ **正文照原样**（`_text` 那一格）：一个字节都不动 —— 本格的正文里可以有换行。
    """
    internal = getattr(item, 'origin', None) == 'internal'
    return {"kind": "system-notice" if internal else _A_MESSAGE,
            "who": "host" if internal else who, "at": _at_of(item), "text": _text(item)}

def _context_only_batch(batch: Sequence[Any]) -> bool:
    """Host context deliveries are recorded, but are not autonomous wake grants.

    A continuation has its own ticket and deliberately does not match here.
    Inspect envelope provenance, never the words in an external message.
    """
    if len(batch) != 1:
        return False
    item = batch[0]
    if getattr(item, 'sender_key', None) is not None:
        return False
    key = (getattr(item, 'source', None), getattr(item, 'origin', None),
           getattr(item, 'type', None))
    from .handoff import INJECT_INTERNAL_KIND, INJECT_NOTE_KIND, INJECT_NOTICE_KIND
    from .consolidate import CONSOLIDATE_KIND
    return key in {('handoff', 'internal', INJECT_INTERNAL_KIND),
                   ('handoff', 'internal', INJECT_NOTICE_KIND),
                   ('handoff', 'external', INJECT_NOTE_KIND),
                   ('consolidate', 'internal', CONSOLIDATE_KIND)}


def _frames_for(batch: Sequence[Any], n: int, *, who: str = _ME) -> list[dict[str, Any]]:
    r"""**一批 ⇒ 几帧** —— 第一帧是批号，之后每项一帧（纯函数，不碰任何流）。

    ⚠️ **批号在头一帧上**（`kind` ＝ `_A_BATCH`）：它是"这一批"与"上一批"之间那唯一的
       把手 —— 收据上多一批、少一批、次序反了，都只在这里看得出来。
        而它是**另一种 `kind`**、不是"一句话"：上面那两格空着（`who` ／ `at`）
       ⇒ 页那一侧照 `kind` 分两支画（`chat.ts::appendFrame`），而不是去猜正文的形状。
       ⚠️ **它的正文照旧是 `[批 N]` 那个字符串**（本刀一个字没动它）：页上那一条正则
       （`^\[批 \d+\]$`）与终端上那一行都读它 —— 这一格只把"它是哪一类"说清楚。
    ⚠️ **它不数项数**：几项看帧数就知道（`len(batch)` 是同一件事的第二个写法）。
    ⚠️⚠️ **它为什么是一帧一项、而不是一个字符串**：`tests/test_web.py::_read_frames` 一族
       判的是"**那一条 `data:` 交回来的那个对象**"；一帧一项之后，谁说的 ／ 那一刻 ／
       正文各是一格，于是"那一格是不是那个值"是**当场可判**的一件事
       （本刀之前要靠"按两个分隔符切一刀"才读得出来）。
    """
    out: list[dict[str, Any]] = [{"kind": _A_BATCH, "who": None, "at": None,
                                  "text": f"[批 {n}]"}]
    out.extend(_frame_for(item, who=who) for item in batch)
    return out



def _content_text(item: Any) -> str | None:
    """ **「这一件有没有正文 —— 有的话是哪一句」这条规则只有这一处**（两个调用者见下）。

    ⚠️⚠️ **它是一个共用件（不是"两处各判一次"）**，理由是量出来的：这条链上**两半**
       （出口 `deliver` ／ 记进会话 `_into_the_session`）本来各持一半规则 ——
       `_into_the_session` 一直判"`text` 是字符串且非空"，而 `deliver` 只判
       `if not batch:` ⇒ 于是两边在同一条空行上**给出相反答案**：
       · 泵那一步**恰去掉一个行尾**（见 `pump` 里那一句"空行是行"）⇒ 标准输入上一个空行
         就是 `EventEnvelope(text="")`；
       · `[EventEnvelope(text="")]` **是真值**（非空表）⇒ 旧出口放它过去、回 `True`
         ⇒ `loop.py:132` 那一句 `if await self._deliver(batch):` **跑一整轮**。
        实测的形状（恰两个字节 `在吗\\n\\n`）：屏幕上是 `[批 1] 在吗 在的` ＋
         `[批 2] 一个空行 在的`，请求体有两个（第二个里只有上一轮的回执 ⇒ 模型**重答
         上一轮**），会话盘上多一条 `assistant` 记录 —— 而"空行不跑轮"正是这一层
         抬头（`_line_to_event` ／ `pump` ／ `deliver` 三处）各自写过一次的规矩。
       ⇒ **一个规则、两个调用者**：出口那道闸（`_content_text(item) is None` ⇒ 这一件不算）
         与"记进会话"那一句（`text = _content_text(item)`）是**同一条操作**
         —— 不是同一个答案的两个写法，也不可能再各自漂走。

    ⚠️⚠️ **为什么它交回正文、而不是回一个 `bool`**：回 `bool` 的话，"有内容"与"那一句正文"
       就是**两条**各自判 `text` 的语句 —— 而 `_into_the_session` 要的恰恰是正文
       ⇒ 一个非 `str` 的 `text`（`None` ／ 一个数字）会在"有内容"那一侧过闸、
       而在"取正文"那一侧露出原形（`Record(text=…)` 会收到一个不是字符串的东西）。
       ⇒ 交回正文 ⇒ 两道闸**物理上**是同一次调用，第二种答案没有地方可写。

    ⚠️ **判据是 `text.strip()`**（不是 `text == ""`）：`_into_the_session` 那一侧从来不收
       只有空白的正文，而 `pump` **故意**不用 `strip()` 抹平它（那会把 `"   "` 也变成空行
        —— 见 `pump` 里那一句）⇒ 于是"只有空白"必须在**这一条规则**上判，不能靠上游抹。

    ⚠️⚠️ **形状分两层，别把它们读成一句**：
       · **有 `text` 且它是字符串** ⇒ 这一层说了算（只有空白 ⇒ `None`，否则就是它）；
       · **别的东西**（没有 `text` ／ `text` 不是字符串）⇒ **照旧算有内容**，
         而"摆成一行"那件事交给 `_text`（真批那一串是信封，而既有测试里的 `["一件活"]`
         那种字符串批**没有** `text` ⇒ 后者必须照旧打得出来 —— `_text` 那一句
         "不然出口会在那些形状上打一个空行，比不打更坏"是同一个口径）。
       ⇒ 于是"这一件算不算数"与"它排成哪一行"**只有一个答案**：
         `_has_content(item)` ⟺ `_content_text(item) == _text(item)`。

    ⚠️ **纯判据**：不碰流、不等、不看钟 ⇒ 测试那两条规矩（不依赖墙上时间 ／ 不依赖外部
       可选物）在它身上没有可违的地方。
    """
    text = getattr(item, "text", None)
    if not isinstance(text, str):
        return _text(item) # 没有正文这一层 —— "摆成一行"归 `_text`
    return text if text.strip() else None

def _has_content(item: Any) -> bool:
    """ `_content_text` 那个共用判据的**是非面**（同一条规则，不是第二条）。

    ⚠️ 它只写一次 `is None`：两个调用者（出口那道闸与"记进会话"）读的是**同一句**。
       本函数存在只为让"这一件算不算数"在**过滤**那一句上读起来是它要问的那件事。
    """
    return _content_text(item) is not None

    return out

def _no_head() -> list[Any]:
    """ **还没装配过**的那一格（`head`）：交回空的。

    ⚠️ 它**不是**"那一句话是空的"那条路：它只在 `request_size()` 于一个**没装配过**的
       `Console` 上被叫时读得到 —— 而那一条**先判过会话**（没有会话 ⇒ `None`，不是 `(0, 0)`：
       `web.state_of()` 那条"不许把不知道画成零"）。
    """
    return []

def _the_persona_root(persona_root: Any) -> Any:
    """ **那一条根的唯一答法** —— 递了听递的，没递用装订器那条缺省。

    ⚠️ **"那条根在哪"这一个事实在本模块里只有这一个答法**：交接第 ③ 步（`build_prefix`）
       与本刀这一格（`_the_prefix_head`）**都要它** —— 各写一遍就是两处会各答一个数，
       而两次都报成功（`tests/test_prefix.py` 那条判据挡的正是"两条口子的缺省根漂开"）。
    ⚠️ **它不自己拼那条路径**（拼一份就是第三份口径）：缺省那一份归**装订器**。
    """
    if persona_root is not None:
        return persona_root
    from . import prefix as prefix_module

    return prefix_module.DEFAULT_PERSONA_ROOT

def _the_prefix_head(persona_root: Any, channel: Any = None,
                     *, include_channel: bool = True) -> list[dict[str, str]]:
    """ **每一次出请求**现装的那一句 system 前缀 —— 按**线格式**交出来。

    ⚠️ **它就是本刀那一格的正文**：`prefix.assemble` 按那一处正本订出来的**一条** `Record`，
       在这里渲染成线格式 —— 因为 `session.outbound` 收的 `head` **就是那个形状**
       （`session.py:376-379` 逐字写着"由调用方渲染好递进来，本模块不渲染它"）。
    ⚠️ **渲染走 `session._render_message`**（ 本模块唯一一处 import 一个私有名）：
       那一处的 docstring 逐字写着"**将来接线那一层要出请求，也该走这里**"——
        在本模块里再写一遍 `{"role": r.role, "content": r.text}`，那句话**当场变成假的**，
       而两处渲染会各答一个数（那正是它防着的那件事）。
    ⚠️ **`ts_ms` 不盖**：这一条**不进会话**（`outbound` 是纯操作）⇒ 那个字段没有读者；
       盖一个现读的毫秒反而会让"同一份正本两次现装"**逐字节不同**（判据 (b) 当场红）。
    ⚠️ **它不吞异常**：那份正本读不到 ⇒ `prefix.assemble` 当场抛（"段序没了"）；
        而**某一份人设文件**读不到照旧不抛（"这一段这次没正文"）—— 两条各有出处。
    ⚠️ **`channel` 那一格**（2026-10-10 加）：它说的是「**当前这一头**是哪一个」——
       装订器拿它去 `## 通道那一层` 那张表里**选一行**（`prefix.assemble(channel=…)`）。
        **缺省 `None` ⇒ 退回表里第一行**（ 那一头的名字**是本仓的禁字之一** ⇒
         这里一个字都不写它 ，见 `prefix.py::DEFAULT_CHANNEL`）⇒
          **不递 ⇒ 逐字节与从前一样** 。
       固定通道仍须与交接第 ③ 步同值；动态通道则只归档共同前缀，
       当前场景每次请求另附，不能把上一轮的场景写进交接后的开头。
    """
    from . import prefix as prefix_module
    from .session import ActivePersona, _render_message

    records = prefix_module.assemble(_the_persona_root(persona_root),
                                     doc=prefix_module.SEGMENTS_DOC,
                                     channel=channel,
                                     include_channel=include_channel)
    rendered = [_render_message(one) for one in records]
    if prefix_module.uses_modern_layout(_the_persona_root(persona_root)):
        rendered[0] = ActivePersona(rendered[0])
    return rendered

def _in_the_archive_already(session: Any, fresh: list[dict[str, str]]) -> bool:
    """ 盘上**开头那一条**与现装的**逐字相同** ⇒ 那一句话**已经在请求里了**（不再说第二遍）。

    ⚠️⚠️ **为什么非要这一条**：交接第 ④ 步**照旧**把同一份正文写进会话
       （`handoff.py:512` —— 本刀**一个字不动**它）⇒ 一个"闭着眼睛现装"的实现会让那一句话
       在请求里**出现两遍**（本机实测的形状：`[system, system, user]`，16553 字节 × 2）。
    ⚠️ **比的是"逐字相同"、不是"开头有一条 system"**：盘上那一条**旧了**的时候
       （页上改过字、还没交接），现装那一条**照旧要进请求** —— 否则"改一个字，下一次请求
       就生效"这句话在交接之后**当场变成假的**。 而那时它在**后面**（`outbound` 的插法）。
    ⚠️ **零条会话**（第一次开机）⇒ 盘上没有开头那一条 ⇒ 现装那一份**照旧进请求**。
    """
    from .session import ActivePersona

    if fresh and isinstance(fresh[0], ActivePersona):
        return False
    records = session.records()
    return bool(records) and records[0].role == "system" and records[0].text == fresh[0]["content"]

def _with_the_facts(rows: list[Any], facts: Any) -> list[Any]:
    """ **那一格事实**（2026-10-07「第 3 刀 3b-②」）—— 它**自己说过什么**，摆在每一轮眼前 。

    ⚠️ **缺省 `None` ⇒ 一个字都不加**（于是那两条"逐字"的判据**照旧** ——
       新东西缺省为空，是这一刀最省事、也最不骗人的做法 ）。
    ⚠️ 空那一串 ⇒ 也不加（"今天还没主动说过话"那句由 `outbox` 那一侧决定要不要说 ）。
    ⚠️ 它是**事实**，不是闸：加这一格的唯一目的是让"要不要说"这件事**它自己看得见** 。
    """
    if facts is None:
        return rows
    said = str(facts() if callable(facts) else facts).strip()
    if not said:
        return rows
    # ⚠️ **2026-10-11：抬头改成中性的** —— 那一框里现在**不只**"说过的话"
    # （ 还进了「现在是几点」）⇒ 而每一行**自带说明**
    # （ `outbox.the_facts` 交回的是「今天主动说了 N 封；最近一封 …」）
    # ⇒ 中性抬头不丢信息 。
    return [*rows, {"role": "system", "content": "〔给你的事实〕\n" + said}]

#: **只看最近这几条**（ 再往前的"说过的词"不算这一趟碰到 ）。
_SAID_AT_MOST = 2


def _the_said(session: Any) -> list[dict[str, str]]:
    """ **这一趟说过的话**（只取 `kind == "message"` 那几条 ）—— 给"自动拉进"看 。

    ⚠️ 取法与旁边那一处（读历史那一族）**同一套格子**：`kind` 是 `"message"`
       （ 不是帧那边的 `"msg"` —— 两者是两个格子上的值 ），正文取 `.text` 。
    ⚠️ 读不成（没有 `records`）⇒ **空那一串** （ 于是"拉不进来任何东西"而不是崩 ）。
    """
    got = getattr(session, "records", None)
    try:
        rows = list(got() if callable(got) else (got or ()))
    except Exception:                            # noqa: BLE001
        return []
    # ⚠️ 2026-10-07：**只看最近几条** —— 原来交的是**整份历史** ⇒
    # 而 很久以前说过的一句"图书馆"就**从此每轮都命中**（真跑量到 ）⇒
    # 于是"触发词收窄"那一修**根本看不出来** 。
    # ⇒ 取最近这一小段（ 一句里出现的词才算"这一趟碰到了" ）。
    rows = rows[-_SAID_AT_MOST:]
    out: list[dict[str, str]] = []
    for one in rows:
        if getattr(one, "kind", None) != "message":
            continue
        if getattr(one, "role", None) not in (_ME, _OTHER, "assistant"):
            continue
        out.append({"who": getattr(one, "role", "") or "",
                    "text": getattr(one, "text", "") or ""})
    return out

def _pull_in_rows(pull_in: Any, session: Any) -> list[Any]:
    """Read transient retrieval from the current scoped view; failure adds nothing."""
    if not callable(pull_in):
        return []
    try:
        return list(pull_in(_the_said(session)))
    except Exception:  # Retrieval is optional and must not fail a model request.
        return []


def _current_channel_rows(persona_root, channel, channel_context, channel_scene):
    from . import prefix as prefix_module
    rows = []
    if callable(channel_context):
        selection = channel_context()
        rows.extend(prefix_module.channel_environment(selection))
        if callable(channel_scene):
            rows.extend(prefix_module.bot_channel_scene(channel_scene(selection)))
    else:
        chosen = channel()
        scene = prefix_module.channel_text(
            _the_persona_root(persona_root),
            doc=prefix_module.SEGMENTS_DOC, channel=chosen)
        rows.append({"role": "system",
                     "content": "〔当前通道：" + str(chosen) + "〕\n" + scene})
    return rows


def _a_head_cell(head: Any, persona_root: Any, session: Any,
                 facts: Any = None,
                 pull_in: Any = None,
                 channel: Any = None,
                 identity_hint: Any = None,
                 channel_context: Any = None,
                 channel_scene: Any = None,
                 channel_at_tail: bool = False,
                 stable_facts: str = "") -> Callable[[], list[Any]]:
    """ **那一格的唯一读法** —— 每次出请求叫一次，三种形状各答一个数：

    · **`None`** ⇒ **现装**那一句话（本刀新加的那条缺省； 盘上若已经有同一份，交回空）；
    · **可调用** ⇒ 叫它一次（调用方自己现取 —— 它想现取什么由它说了算）；
    · **一串** ⇒ 拷贝一份（照旧：调用方说了算；**空那一串 ＝ 明确不要**）。

    ⚠️ **`None` 与 `()` 是两个意思**（照 `prefix` 那一格的哨兵成法）：前者是"没人递过 ⇒
       现装"，后者是"**调用方明确不要**" —— 一个值分不开两件事。
    ⚠️ **每次叫都重来一遍**：那是**故意的**（本仓没有任何按前缀的缓存；正文是 `''.join`
       出来的 ⇒ 同一份正本就是同一份字节）—— 它正是"改一个字，下一次请求就生效"的
       **唯一**实现。代价量过：一次 `段序.md` ＋ 九次 `read_text` ＝ 16553 字节。
    动态 `channel` 是零参来源：共同前缀与本轮场景分开，旧场景不进会话归档。
    """
    def read() -> list[Any]:
        # **2026-10-10：那两行"临时诊断"收掉了** （ 作者当天说「做一下」）。
        # ⚠️ **它原来是什么** ：2026-10-07 加的
        # `[head] 那一格装了 N 秒（M 行）` —— 抬头自己写着"**临时：每次都打**"，
        # 用处是"**分开「卡在 head 里」与「卡在 generate 里」**"。
        # ⚠️ **为什么该收** ： 它「**每次都打**」正是它的用处 ——
        # 而 那一格**一秒要装几十次** ⇒ 日志被它**淹没**
        # （ 今天找那几行 `[call]` 时， 得从它里面往外扒 ）。
        # ⚠️ **没有消费者** ： 它是 `stderr` 的一行自由文本 ——
        # 没有任何判据／读者按它取数 ⇒ 收掉不动别的任何东西 。
        # ⚠️ **`_t0` ／ `_cost` ／ `_sys` 三样只服务它** （ 各自只出现在那两行 ）
        # ⇒ 一并收掉 （ 留一个没人读的局部量，是这一仓点过名的形状 ）。
        # **2026-10-07「3b-②」**：那几行照旧，只在**出门那一刻**过一下那一格事实
        # （`facts is None` ⇒ 逐字节与从前一样 ）。
        if head is None:
            if callable(channel) or callable(channel_context):
                # A source-aware channel is a per-request scene, not a fact
                # to preserve in the handoff's archived system prefix.
                from . import prefix as prefix_module

                fresh = _the_prefix_head(persona_root, include_channel=False)
                rows = [] if _in_the_archive_already(session, fresh) else fresh
                if not channel_at_tail:
                    rows.extend(_current_channel_rows(persona_root, channel, channel_context, channel_scene))
            else:
                fresh = _the_prefix_head(persona_root, channel=channel)
                rows = [] if _in_the_archive_already(session, fresh) else fresh
        elif callable(head):
            rows = list(head())
        else:
            rows = list(head)
        # **2026-10-07「触发／自动拉进」**（lorebook 那一手 ）：
        # 把**这一趟说过的话**交给那一格 —— 它回来"该附上的那几份"
        # ⚠️ `pull_in is None`（没人递）⇒ **一个字都不多**：逐字节与从前一样
        # （ 于是那两千多条判据不受影响 —— "缺省为空"那条老成法 ）
        # ⚠️ 它坏了 ／ 抛了 ⇒ **照旧**（不许带走这一条请求 ）。
        rows = rows + _pull_in_rows(pull_in, session)
        if callable(identity_hint):
            hint = str(identity_hint() or "")
            if hint:
                rows.append({"role": "system", "content": hint})
        if stable_facts:
            rows.append({"role": "system", "content": stable_facts})
        got = _with_the_facts(rows, facts)
        return got

    return read

class BatchIdentityStopped(asyncio.CancelledError):
    """Cooperative rejection of the exact batch retired by operator stop.

    Never grants permission to consume the late result. The original rejection
    remains the cause; unrelated identity failures remain ValueError.
    """


@dataclass
class Console:
    """ **出口** —— 收下一批，**打出来**，照旧让那一批进账（见 `deliver`）。

    ⚠️⚠️ **两件事，一头一件**（别把这一页读成一件事）：
       · **出去**（Task 1）：`deliver` —— 收下一批，打出来（**这里不再变**）；
       · **进来**（Task 2）：`pump` —— 读一行、推给总线、把那批交回给调用方
         （ 它**每一跳都带上界**：那一条链上"输入到头"**只**在那一跳里看得见，
         而"谁先停、谁等谁"仍归 Task 3 —— 见 `pump` 的 `idle_s` 那一段）。
          **两件"什么算一批"的问题各有各的答案，写在各自的地方**：出去那边是
         "递送那一下、一批一次"；进来这边是 `_line_to_event`（一行 ⇒ 一件）
         ＋ `pump`（一批由 `bus` 合出来、由 `feed.next_batch` 取走）。

    ⚠️⚠️ **这个对象现在持有四样 ＋ 那个入口**（ Task 3 把装配那一半接在了这里）：
       · 出去那条线（`deliver`）＋ 进来那条线（`pump`）＋ 泵自己那一半状态
         （`ready` / `cursors` / `done`）；
       · **总线也在它身上了**（字段 `bus`，由 `assemble()` 造、`on_ready` 也在那儿装）：
         Task 2 的 finding #1 逐字是"**装配那一层就是你**：谁造总线、谁装 `on_ready`、
         谁把泵接进那个能起的入口"—— `WakeBus` 在本刀之前**只被 `tests/` 造过**。
       · **Task 3 的落点就是"这一半"**：`assemble()` ／ `run()` ／ `stop()` ／ `main()`
         （装 ／ 起 ／ 停 ＋ 那个协程），而 `_take_a_batch` 把泵接成了 `loop` 的
         `take_batch`、`_spoke` 把 `generate` 包成"模型说了什么就打出来"。
       · ⚠️ **Task 1 留下的那一处 `partial` 的答案**：它搬进了 `build()` 的 `console=` 那一格
         （`loop._deliver = partial(console.deliver, ledger=app)`）—— **不是**事后从外面
         去戳 `loop._deliver`：那一句 `if "deliver" not in rest:` 的守卫只有 `build()` 里
         写得出，而"不许静默顶掉调用方递进来的替代品"是这一棵树的家规。

    ⚠️ **`write` 是注入面**（默认 `_to_stdout`）：递一个假的进来，出口就成了一条
       当场可判的事实（测试不碰真的标准输出）。 **进来那一边的注入面是 `pump(read=…)`**
       （默认 `_stdin_line`）—— 同一条纪律，另一头：读法递得进来，泵就量得动。
    ⚠️ **`ledger` 是那一本账的主人**（`App`；不递 ⇒ `None`）：它只被**递给**
       `App.deliver`，本类**不读它、也不从它里面拿东西**。
    ⚠️ **`batches` 是本出口的序号**（不是账上的项数）： 它**只对真的打出去了的批 +1**
       （见 `deliver`）—— 空批不占号，于是"账上有几批"与"屏幕上第几批"**同一个答案**。
    ⚠️ **`ready` / `cursors` / `done` 是泵那一半**（见 `pump` 与各字段上的注释）——
       其中 `ready` 是喂给 `feed.next_batch` 的**那一个** `asyncio.Event`。
    ⚠️⚠️ **这个对象现在同时是那个入口**（ Task 3 把装配那一半接在了这里）：
       `assemble()` 造总线并装 `on_ready`、把泵接成 `take_batch`、把那两样递给
       `make_run_round`（引擎那一条）；`run()` 起 ＋ 起来之后一定停；`stop()` 停。
       ⚠️ **本类仍然不碰事件循环**（`asyncio.run` 全树只许在 `cli.py:54` 那一处被调用，
       本模块那条纪律照旧）—— `run()` 是**被跑**的那一个协程。
    """

    write: Callable[[str], None] | None = field(default=None)
    ledger: Any = None

    #: 已经打出去的批数（ 出口自己的序号 —— 见类文档里那一句"空批不占号"）。
    batches: int = 0

    #: **泵抬的那面旗** —— 喂给 `feed.next_batch` 的**那一个** `asyncio.Event`
    #: （`feed.py:21` 第二个形参）。⚠️ 它在 `__post_init__` 里造：`asyncio.Event()` 今天
    #: 绑不到循环上（3.10 起），所以在没有循环的地方造它是合法的；而**绑不绑得上**由
    #: "喂给 `next_batch` 的那一次调用在不在一个真循环里"说了算（那就是 `pump`）。
    #: ⚠️ 类型写成 `Any`：本模块**不许** import `asyncio`（一个字节都不用它 —— 那面旗由
    #: `feed` 那一侧等），而这一个字段只要"有个 `.set()` / `.clear()`"。
    ready: Any = None

    #: **泵自己那一半状态**：从这个泵推下去的行数（见 `_line_to_event` —— 它**不是**
    #: 事件库的游标）。它是**泵的**、不是出口的：出口那边数的是 `batches`。
    cursors: int = 0

    #: **模型答过话没有** —— `say()` 每打一句置真（`_spoke` 也走那一句 ⇒ 模型真答了
    #: 一次就置真）。 它存在的唯一理由是**退出码要诚实**（见 `main()`）：恒 `0` 的退出码
    #: 让"跑通了"与"什么都没跑"在收据上长得一样（实测：一个死掉的端点 ⇒ 48.6 秒沉默 ＋
    #: 一句答案都没有 ＋ `RC=0`）⇒ 那个 `0` 于是**证明不了任何事**。
    #: ⚠️ 判据是"**这一端真的写出去过一句**"，不是"那一轮成功"：一次失败的回答在这个出口
    #: 上是无声的（见 `_spoke` 那段"已知的一处"），于是它**不该**被算成"出过话"。
    answered: bool = False

    #: **EOF 到了没有**（`pump` 里"没有了"那一条路置真）—— 见 `pump` 那条"它给收工用"。
    done: bool = False

    # ── Task 3：那个入口持有的那几样（`assemble()` 填它们；`run()` ／ `stop()` 用）──
    #: 那条总线（ **由 `assemble()` 造，而装 `on_ready` 的也是它** —— Task 2 的
    #: finding #1 那句"装配那一层就是你"，落点就在这一格）。
    bus: Any = None
    #: **装出来的那一套**（`assemble()` 的返回值也记在这儿 —— `run()` ／ `stop()` 要它）。
    app: Any = None
    #: **模型面对的那张消息表**（`session.Session`）—— 你的话与它的话都落在这上面
    #: （你的那一半由 `_into_the_session` 补，它的是 `round.py` 的回执）。
    session: Any = None
    #: **那面信号旗 —— 它必须是 `App.signal`（同一个对象）**：`assemble()` 先造它、
    #: 再递给**两处**（`build(signal=…)` 与 `make_run_round(signal=…)`）⇒
    #: `App.stop()` 置的那一个就是 `generate` 读的那一个（`round._OWED` 的第一句）。
    signal: Any = None
    #: **泵的读法**（缺省走 `pump` 自己那一个 `_stdin_line`）：入口把它原样转交给 `pump`
    #: ⇒ 递一个脚本读法进来，整条链就量得动（测试不碰真的标准输入）。
    read: Callable[[], Any] | None = None
    # An optional world-side delivery hook. The core forwards the source batch
    # without interpreting its channel or choosing a recipient.
    output_tap: Callable[[Sequence[Any], str], Any] | None = None
    event_filter: Callable[[Any], bool] | None = None
    event_isolation: Callable[[Any], bool] | None = None
    batch_admission: Callable[[Sequence[Any]], Any] | None = None
    channel_blocked_batches: int = 0
    # Audience of the local terminal/WebUI; never changes durable storage or
    # the model's recipient-scoped view. None preserves standalone Console use.
    owner_view: Callable[[Any], bool] | None = None
    _active_batch: tuple[Any, ...] = ()
    _batch_identity: Any = field(default=None, init=False, repr=False, compare=False)
    # Ready snapshots, consumed serially before asking the producer for more.
    _pending_batches: list[list[Any]] = field(default_factory=list)
    _taking_batch: bool = False
    _ingress_batch: Any = field(default=None, init=False, repr=False, compare=False)
    _local_observation_batch: Any = field(default=None, init=False, repr=False, compare=False)
    #: 引擎那一层的 HTTP 客户端（`HttpxTransport`）—— ⚠️ **谁造谁关**（见 `stop()`）。
    transport: Any = None
    #: **装出来的那一份交接**（`handoff.Handoff`）—— `assemble()` 填它。
    #: ⚠️⚠️ **它为什么是一个字段而不是一个局部变量**（本刀加的那一格）：手动那一条路
    #:    （`POST /handoff` ⇒ `ask_for_handoff()`）要**够得着** `Handoff.request()`，
    #:    而在此之前那一份交接**装完就没人拿着** —— 只有它的两个绑定方法
    #:    （`flush_handoff` ／ `batch_end_check`）被递进了 `build()` ⇒
    #:    `request()` 那个公开入口**在生产里一个调用者都没有**（`handoff.py` 抬头自己写着）。
    #: 于是"手动入梦"这件事在接线那一层是**缺一个把手**，不是缺一个机制。
    handoff: Any = None
    #: **这个 transport 是不是本入口造的**：造的那个由 `stop()` 关掉；**递进来的归调用方**
    #: （照 `cli.py:28-31` 那条"谁的谁关"—— 替对方关掉是替它做了一个它没要求的决定）。
    owns_transport: bool = False
    shutdown_report: Any = field(default=None, init=False, repr=False, compare=False)
    _ingress_shutdown: Any = field(default=None, init=False, repr=False, compare=False)
    _drain_owner: Any = field(default=None, init=False, repr=False, compare=False)
    _drain_stop_return: Any = field(default=None, init=False, repr=False, compare=False)
    _drain_tasks: dict[str, Any] = field(default_factory=dict, init=False,
                                         repr=False, compare=False)

    def __post_init__(self) -> None:
        """⚠️ 不给 `write` ⇒ **标准输出**（而不是"没有出口"）：两者不是一回事。
        ⚠️ 而 `ready` 是**造在构造里**（不是等 `pump` 那次调用）：喂给 `next_batch` 的
           必须是**同一个** `Event` 对象，于是它的生命周期得长过某一次 `pump`。
        """
        if self.write is None:
            self.write = _to_stdout
        if self.ready is None:
            self.ready = asyncio.Event()
        #: 那一格（`head`）**这一趟装配用的是哪一个** —— 缺省是"还没装配过"那一格，
        #:    而 `assemble()` 会把它换成真的那一个（见 `_a_head_cell`）。
        self._head_cell: Callable[[], list[Any]] = _no_head

    @staticmethod
    def _resolve_plugin_image(reference: str) -> str:
        from . import plugins
        return plugins.resolve_media(reference)

    def _request_image_resolver(self, root):
        from .request_media import RequestImageResolver
        legacy = self._resolve_plugin_image
        if legacy is not Console._resolve_plugin_image:
            return legacy  # Explicit injected standalone/test resolver, not migrated.
        # root is this Bot's data root, NOT the registered plugin Core root.
        # Only the actual launcher may bind this prepared host entry.
        prepare = getattr(self, "_prepare_plugin_media", None)
        if not callable(prepare):
            raise RuntimeError("native media host preparation is not bound")
        return RequestImageResolver(prepare)


    async def pump(self, bus: Any, *, read: Callable[[], Any] | None = None,
                   idle_s: float = _DEFAULT_IDLE_S) -> list[Any] | None:
        """ **进来的泵** —— `read` 读**一行** ⇒ 推给 `bus` ⇒ **等** `feed.next_batch` 把它取走。

        ⚠️⚠️ **接的是那条现成的机械**：返回的正是 `feed.next_batch(bus, self.ready)` 的返回值，
           也就是说 `bus.take_if_ready()` 给的那一批（`feed.py:31`）—— **本方法不另造一条入路**，
           也不自己从任何地方"凑"一批出来（下面有一条测试专门钉这个：把 `take_if_ready`
           换成恒回 `None` 的一个替身 ⇒ 本方法必须回 `None` **而不是**一个自己拼的批）。
           ⇒ "什么东西算一批"这个问题于是只有**一个**答案，且那个答案长在总线上。

        ⚠️⚠️ **`bus` 是参数、不是本类自己的字段** —— 这是本刀**故意**留下的那一处"住哪儿"：
           · ★ **2026-10-13 订正**：**有人在持有一条总线了** —— `assemble()` 造它
             （就是这一层那一格），而 `run()` 起泵时喂给 `feed.next_batch`。
             原先写的是「生产上还没有人持有一条总线（读出来的：`WakeBus` 在树里
             只被 `tests/` 造过）」—— 那是**这一刀之前**的形状。
             ⇒ `bus` 与 `ready` 谁造、谁持有，是**装配那一层**的事；
           · 而"谁造总线"与"怎么读一行"是**两件事** —— 前者归 Task 3（那个入口），
             后者就是本方法。把两者绑死（比如本类自己造一条）会让"总线归谁"这一个问题
             出现第二个答案，而 pin 住 `on_ready` 契约要的恰恰是**一个**答案。
           · ⇒ 本类只持**泵自己那一半状态**（`ready` ＋ `cursors`），总线从外面递进来。
             喂给 `next_batch` 的那个 `bus` **必须**是**同一个对象**：`next_batch` 取的是
             `bus.take_if_ready()` ⇒ 递一个副本进来，泵就推到一条**没人读**的总线上

        ⚠️⚠️ **`ready` 是泵抬的，而 `on_ready` 是总线自己调的** —— 两条**不同**的规矩，
           别在这条缝上加第二个答案：
           · **泵**在每一件推下去之后自己 `self.ready.set()`（下面那一句）——
             理由是 `bus` 那边**没有**统一的"我投递了"的回调：`_deliver` 只在**有** `on_ready`
             时才叫它（`bus.py:222`），而 `on_ready` 是**构造总线时**就定下的
             ⇒ 泵推的那一下能不能抬 `ready`，**取决于别人怎么造那条总线**（一个可选的、
             外部注入的钩子）。
           · ⇒ 于是这里选的是**不靠**那个钩子：泵推完自己抬。
             ⚠️⚠️ **"一处写清、一条测试钉住"那半句是假的 —— 本波量过，这里改正**：
             把上面那一句 `self.ready.set()` **删掉**之后，**全套测试照旧全绿**
             （本波实测：764 passed ／ 2 skipped，一条都没红）⇒ 它今天**不是承重**的那一句。
              **承重的是"先再探一次"那一跳**（见下面那一段）：删掉它 ⇒ **3 条红**。
             ⚠️ 它仍然留在树里的理由照旧（**应然**，不是量出来的行为差）："推完自己抬"
             是"我推了一件、请看一眼"这条契约的另一半 —— 今天投递是同步的（`bus.push`
             返回时那一件已经在队里）⇒ 桥的第一探就取得到；**谁哪天把投递改成异步，
             这一句立刻变成承重的**（逐字写在它下面那段注释里）。
             ⚠️ 引用的那个**名字**也改正：`test_feed.py` 那条真名是
             `test_the_real_bus_wakes_the_real_bridge`（`tests/test_feed.py:101`），
             它钉的是"**喂给桥的真总线必须装了 `on_ready`**"（去掉 `on_ready=ready.set`
             它红）—— 那是**总线那一半**的规矩，与本句"泵自己抬"**不是同一条**。
           · ⚠️ 装配那一层**可以**再装一个 `on_ready`（定时器到点的那些投递也要叫醒桥）——
             那是**加**一条叫醒源，不是与这一句争**同一个**答案。

        ⚠️⚠️ **`trigger="flush"` 是按供给写的**（不是另立一档）：一行到了 ⇒ 这就是**现在**
           该处理的，而"刚到的一行要不要立刻投"这件事，`bus.push` 的缺省规则自己就答过：
           `origin="external"` 缺省是 `debounce`（`bus.py:127-128`），于是**显式** `flush`。
           代价写清楚（它不为零）：**每一行自己就是一批**。 而"几行合成一批"**没有**被本方法
           绕开 —— 谁要是攒了一串再取（终端上你手快打了两行、而消费者还在忙），
           总线照旧把它们并成一批交出去（它一次给整个队）⇒ 合批权**始终**在 `bus` 那一侧。

        ⚠️⚠️ **EOF 拍在这里**：`read()` 说"没有了"（`""`，或一个**抛 `StopAsyncIteration`**
           的读法）⇒ **收工**，返回 `None`，**空批不许造**。两条理由：
           · `None` 与"一批"在**形状上就分得开**（循环那边看 `loop.py` 抬头那句：
             它收 `list | None` —— `None` 就是那条收工的信号）⇒ "没有行了"与"一批空的"
             不可能是同一个值；
           · 而**空批进总线会一路走到最坏的那一步**：`take_if_ready` 只在队非空时给东西
             （`bus.py:248`），而**能**进队的件会被 `App.deliver` 收下、回 `True`
             ⇒ 一个被造出来的空批会让 `loop.py:132` **跑一轮空的**（模型答一个空的东西），
             而这一刀要的恰恰是"打了一行才跑一轮"。
           ⚠️ **收工那一刻不抬 `self.ready`**：抬了的话，等在 `next_batch` 里的那一位会被唤醒、
             再等（`feed.py` 那个 `await ready.wait()` 在**已 set** 时立刻返回）⇒ 变成
             **不让出事件循环的空转** —— `feed.py` 抬头那一段骂的就是那个形状。
             ⇒ 于是本方法把结果**交回给调用方**，由它决定收工（那个泵是**一轮一批**跑的、
             不是后台任务 ⇒ 调用方天然知道 `None` 是什么意思）。
           ⚠️⚠️ **而"收工"那一刻，队里还排着的东西**（本波 Minor #2，写清不修）：
             本方法是**一轮一批**跑的 ⇒ "输入到头"那一下**只**让这一跳交回 `None`；
             **总线上**如果这时还有**没被取走**的件（`take_if_ready` 没来得及给的那些），
             它们**不会被本方法取出来**，而调用方收到 `None` 之后**收工** ⇒ 那几件**就此丢掉**
             （账上没有、会话说不上、屏幕上也没有 —— **没有一声**）。
              **为什么今天不修**：那要么得让本方法在收工前**再探一次、把剩下的交出去**
               （那就把"收工"从**一个值**变成**一串值**，而 `loop.py` 那一侧收的形状是
               `list | None` ⇒ 改的是**那条契约**），要么得给总线**加一条"这次是界符"的路**
               （`_line_to_event` 抬头逐字写过：那是**第二条真相**，本刀不许 ）。
             ⚠️ **今天走得到吗**：真入口里那个泵**一轮一批**、每一跳都带一个上界 ⇒
               一行到了那一跳就交出去了，队里通常是空的；**能**到那一格的形状是
               "投递比取走快"（一次推了好几行而消费者还在忙）—— 而今天没有生产者那样干
               （那正是**合批**要处理的形状）。
             ⇒ 所以这是一格**点名的边界**，不是一句"没问题"：谁哪天加了一个
               一次推好几行的生产者，谁就来改这一段（或者删掉它）。

        ⚠️⚠️ **`done` / `cursors` 是公开的**（读得到、判得着 —— 不是内务）：
           · `done` 是给**收工**用的：泵还在跑、可总线上这一会儿**没有**可以交出去的批
             （比如 `wait_for` 那一头超时了），装配那一层靠它分辨"输入到头了"与"那头还在想"。
             ⇒ 这是本刀**能**给的那一半；**谁先停、谁等谁**那件事照旧欠在 Task 3
             （设计的自检 #3 已经点过名）。
           · `cursors` 数的是**从这个泵推下去的行数**（见 `_line_to_event` 那句
             "它不是事件库的游标"）。

        ⚠️⚠️ **已知边界：默认那个读法是同步的** —— `_stdin_line()` 走 `sys.stdin.readline()`。
           在**只有它一件事**的终端里，阻塞那一会儿是这一层唯一的活；而在**真入口**
           （Task 3：循环 ＋ 配速驱动 ＋ 这一条泵，三个一起）里，一个同步读会**占住事件循环**
           ⇒ 那一层要么把这一个读法换掉（一个线程里的读、或者一个每行一条消息的队列），
           要么接受"只有你打字时那头不动"那一条。 写在这里是因为**我量不到它**
           （没有真终端可测）—— 谁在 Task 3 起那个进程，谁就第一个量得到它。

        ⚠️⚠️ **`idle_s` 不是讲究，是这条链上**唯一**的活性来源 —— 本刀三个探针量出来的**：
           这一格**不能**写成"一直等 `next_batch`"，理由是逐层测出来的：
           · `next_batch` 等的是那面旗（`self.ready`），而那面旗**只有**两个来源：
             ① 泵推完自己抬（下面那一句）；② 造总线时装的那个 `on_ready`（`bus.py:222`）。
           · ⚠️ 而**两条都到不了"一直等"那一趟**：
             · `next_batch` 自己会在**每次探空之后** `ready.clear()`（`feed.py:34`）——
               泵抬的那一次**在它第一探里就被它自己清掉了**（实测：`take_if_ready` #1
               探空 ⇒ 旗被清 ⇒ 泵停在 `await ready.wait()` 上，**再没有人**会来抬它）；
             · **没装 `on_ready` 的总线**不会叫（`bus.py:222` 那一句只在它存在时才走）。
           · ⇒ 于是"一直等"那一版带着一个**真的死法**：标准输入那头打 Ctrl-D，而那个进程
             **停在那儿不动**（实测次序：读法只被叫了 **1** 次，泵停在 `console.py` 里那句
             `await next_batch(...)` 上，直到外面那个上界到点才红 —— 那是它唯一的症状）。
           · ⇒ 所以本方法**每一跳都带一个上界**：到点**先再探一次总线**，没有再回读法那一头
             看一眼（"没有了" ⇒ 收工；"有一行" ⇒ 照常推下去）。 它**不丢东西**：
             那一跳里没取走的东西照旧在队里，下一趟拿得到。
           · ⚠️ **代价写清楚**（它不为零）：输入空转时泵每一跳醒一次。 那是"输入到头"
             在**一个阻塞的读法**上**唯一**看得见的方式（`readline` 不会在你打字之前回来）
             ⇒ 换来的是一个会收工的进程，而不是一个停住的。
           · ⚠️ **`_DEFAULT_IDLE_S = 0.25` 是拍的**（不是量出来的）：对"你的手"够细
             （四分之一秒），对"一个空闲进程每四分之一秒醒一次"也够粗。真入口要调它 ——
             连同那个读法一起（见下面"默认那个读法是同步的"那一条）。
           · ⚠️ **"再探一次"那一拍是量出来的**（变异 M7 ⇒ `test_the_pump_wakes_the_bridge_itself…`
             那一条红 —— 那个变异的名字与结果在 Task 2 的账上）：只回读法那一头的话，
             那一批**会丢** —— 实测次序：`take_if_ready` #1 说没有 ⇒ 超时 ⇒ 回读一行 ⇒
             第二次探取**根本没发生** ⇒ 队列里有东西，而泵交回了 `None`。
              本波把一个**更重**的形状复量了一遍（把整个 `while` 换成一个光秃秃的
             `await next_batch(...)`：连超时那一跳都没有了）⇒ **3 条红**，头一条还是它。
           · ⚠️ **而出路那一跳为什么必须是"带上界"、不能是"一直等一句有 `on_ready` 的总线"**：
             `next_batch` 会在**每次探空之后**清旗（`feed.py:34`），而**没装 `on_ready`** 的总线
             永不叫（`bus.py:222`）⇒ 两条合起来，那个等就**只剩上界**这一个出口。
              "在每一跳之后重试"这件事由 `wait_for` + 上面那个计数完成，**不是**靠 `ready`
             再被抬起（变异 M5 ⇒ 把 `self.ready.set()` 删掉：**0 red —— 存活**，账在 Task 2；
             本波复量：764 passed ／ 2 skipped。它的角色见上面那段"应然"的注释）。
        """
        if read is None:
            read = _stdin_line
        self.done = False
        while True:
            # ⚠️ `read()` 是**一个 await 点**（它被声明成"要等的"）—— 于是这一格既是"等一行"，
            #    也是这一层唯一的让出点；而那两件同步的事（造件 ＋ `bus.push`）之间没有 await。
            # ⚠️ **"没有了"的第二种形状在这里收下**：`StopAsyncIteration` —— 它正是
            #    `__anext__` 到头时抛的那个，于是**一个 async 迭代器可以原样递给 `read`**
            # （不必让人在外面再包一层"到头了就回空串"）。 而收在这里是**必须**的：
            #    一个协程让 `StopAsyncIteration` 逃出去，Python **会把它换成 `RuntimeError`**
            #    （"coroutine raised StopAsyncIteration"）⇒ 不收的话，"到头了"会变成一声炸。
            try:
                line = await read()
                evidence = getattr(self, 'runtime_evidence_owner', None)
                evidence_parent = None
                if evidence is not None:
                    line, evidence_parent = evidence.unwrap_input(line)
                #  **2026-10-11（乙）： 「去看看总线」**  ——  读法可以交回这一个值 
                # （ 意思是「没有行，而总线有活儿」）⇒ **不推任何东西** 、
                # 直接去把那一批端回来 （ 也就是「一轮由总线触发」 —— §8.5 要的正是它 ）。
                # ⚠️ **它不改变收工** ： `""` ／ `StopAsyncIteration` 照旧只表示「到头了」
                # ⇒ 服务一停，那条泵照样收工 （ 一次只有一个意思 ）。
                if line is CHECK_THE_BUS:
                # ⚠️ **上界到点要接住** —— 不接 ⇒ `TimeoutError` 一路逃出去、
                # **服务当场退出** （ 2026-10-11 真崩过一次 —— 而它跑在
                # `[tick]` 那条路上， 崩在看见那一轮之后 ）。
                    try:
                        return await asyncio.wait_for(next_batch(bus, self.ready), idle_s)
                    except asyncio.TimeoutError:
                        continue # 那一批还没到手 ⇒ 回去再等一行 ／ 总线
            except StopAsyncIteration:
                self.done = True
                return None
            if line == "":
                self.done = True
                return None
            # ⚠️⚠️ **行尾是这一层的事，不是那一行字的事** —— 一条 `readline()` 给的行走的是
            #    `text='在吗\n'`（**实测**：本刀的第一趟实现就是那样，而下面那条测试当场抓住
            #    了它：`text` 里带一个换行 ⇒ 出口那一侧多打一行空白、下游拿到的是
            #    "那一行 ＋ 一个换行"）⇒ 于是**恰好在读的这一步**去掉**一个**行尾，
            #    而不是留给"谁看见谁自己去掉"（那会是同一件事的第二个答案）。
            # ⚠️ **两种结束符都归一**（`\r\n` 与 `\n` 各一个）：终端在 Windows 上给的是 `\r\n`
            # ⇒ 不归一的话，这一层交出去的东西会**随终端变**
            # ⚠️ **`[""]` 与 `["\n"]` 在这里是同一件事**（一个空行）—— 这正是不用 `strip()`
            #    （那会把 `"   "` 也抹成空行）而用**恰一个行尾**的理由：
            #    **空行是行**（见 `_line_to_event`），而行尾只能有一个。
            # **先把那一行拆开**（2026-10-05「图片输入」）——`line` 可能是"带附件的那一条"
            # （一个 dict）⇒ 而**去行尾那一套只对字符串有意义** ⇒ 拆必须在它**之前** 。
            # ⚠️⚠️ **上一版把拆放在下面（造件那一步）** ⇒ 一条带图的 dict 走到
            #    `line.endswith` 就炸：`AttributeError: 'dict' object has no attribute 'endswith'`，
            #    而那个异常**一路逃出 `console.run()` ⇒ 整个服务退出** ——
            # 作者实测到的就是"发出去之后没多久 core 关停了" （现场在 `data/service.err.log`）。
            # ⇒ 教训：**一条新形状进来时，要把这一层里"读过它"的每一处都数一遍**
            # （只改自己新写的那一处，就是这一次的崩法 ）。
            text_of, blobs_of = _split_a_line(line)
            if text_of.endswith("\r\n"):
                text_of = text_of[:-2]
            elif text_of.endswith("\n"):
                text_of = text_of[:-1]
            self.cursors += 1
            # ⚠️⚠️ **两句话各说一件事，次序是量出来的**：
            #    · `clear()` 清的是**上一次那面残留的旗**（`feed.py` 抬头骂的忙等就是它）——
            #      留着的话，`next_batch` 的第一探一旦探空，它自己会把旗清掉（`feed.py:34`），
            #      看着没事；可**在那之前**的任何一句读旗，读到的都是**上一次**的答案。
            #    · 于是次序是「清（残留）→ 推 → set（**现在**可以试了）」。
            # 第一趟实现把它排成了「set → 清 → 推」，实测量到：那一句 `set()` 到
            #      `next_batch` 那一步**已经被自己那句 `clear()` 抹掉** ⇒ **一个字节都不承重**
            #      （删掉也全绿）⇒ 次序改过来之后它才说得通。
            self.ready.clear()
            # **带附件的那一条**（2026-10-05「图片输入」）：附件**不在这里造件**，
            # 件照旧借 `_line_to_event` 造、附件用 `replace` 补 （同一手法见 `_inject_a_note`）。
            # ⚠️ 拆（`_split_a_line`）**在上面**（去行尾之前 ）—— 这里只管用它 。
            one = _line_to_event(text_of, cursor=self.cursors)
            if blobs_of is not None:
                from dataclasses import replace

                one = replace(one, blobs=blobs_of)
            local_owner = getattr(self, 'local_observations', None)
            if local_owner is not None:
                one, _ = local_owner.submit(one, mode='ready')
                self.cursors = max(self.cursors, one.cursor)
            elif getattr(getattr(self, "app", None), "store", None) is not None:
                # 画像审核要求事件来自同一本库，且保留 append 返回的对象身份。
                # 写入失败时停止投递，不能将未经登记的消息送进本轮。
                from dataclasses import fields

                one = self._record_event({
                    field.name: getattr(one, field.name)
                    for field in fields(one)
                    if field.name != "cursor" and getattr(one, field.name) is not None
                })
            if evidence is not None:
                evidence.bind_event(one, evidence_parent)
            if local_owner is None:
                bus.push(one, trigger="flush")
            # ⚠️⚠️ **这一句是"应然"，今天量不到它的行为差 —— 别把它读成承重的那一句**：
            #    本刀的变异 M5 把这一句**删掉**之后，19 条测试**全绿**（实测，不是推断）。
            #    原因在**这一条链今天的形状**，逐条写清，别让下一个人重新量一遍：
            #      · `bus.push(..., trigger="flush")` 是**同步**投递（`bus.py:133-134`）⇒
            #        它返回时那一件**已经**在 `ready` 里了 ⇒ 桥的**第一探**就取得到；
            #      · 而 `feed.next_batch` 每次探空之后**自己**会 `ready.clear()`（`feed.py:34`）
            #        ⇒ 就算这里不抬，它也不会落在一次"已 set 的 wait"上；
            #      · "投递发生在 wait 之后"那种次序（旗才有用的那一种）今天**不存在**：
            #        `take_if_ready` 与 `_deliver` 都是同步的、`flush` 也不排定时器。
            #    ⇒ **为什么仍然留着**：泵这边"我推了一件、请看一眼"这件事得由泵**说了**，
            #      不能靠"总线恰好是同步的" —— 那是 `feed.next_batch` 抬头那份契约的**另一半**
            #      （它写到"已 set 的 wait 不 yield"为止，而"谁来 set"这一句在这儿）。
            #      ⚠️ 谁哪天把投递改成异步（线程定时器 / 一个真队列），**这一句立刻变成承重的**：
            #      那时删掉它，泵就会一直等到下一跳超时才被救回来（或等不到，见 docstring）。
            self.ready.set()
            # ⚠️⚠️ **那一跳"先再探一次、没有才回读法"** —— 两拍**都量过，而结论不一样**：
            #    · **补探那一拍：承重**（本波实测：把整个 `while` 换成一个光秃秃的
            #      `await next_batch(...)` ⇒ **3 条红**，头一条正是
            #      `test_the_pump_wakes_the_bridge_itself_when_it_pushes_a_line`）：
            #      不补的话第二次探取**根本不发生** ⇒ 队列里有东西而泵交回 `None`（那一批丢了）。
            #    · **推完放旗那一拍（上面那句 `self.ready.set()`）：不是承重**（本波实测：
            #      删掉 ⇒ 764 passed ／ 2 skipped，一条都没红 —— 见上面那一段的改正）。
            #      ⚠️ **这一拍说的是上面那一句，不是下面 `except` 里的 `clear()`**：
            #      两者是**两个值**（设上 ／ 抹掉），别把它们读成同一件事。
            #      `wait_for` 到点会把那一探**取消**，于是 `next_batch` 的 `clear()` 那一步
            #      没走到、旗留在原地；可下一圈是**同一个 `wait_for`**，它照旧按上界到点 ⇒
            #      依旧**让出事件循环**（旗只让那一探回得快一点、不产生忙等）。
            #      ⇒ 留着它的理由只有一条：那一圈里旗的状态应当是"泵知道的事实"
            #        （推过、还没被取走），而不是一个**上一次的遗留**。
            tries = 0
            batch = None
            while True:
                # ⚠️⚠️ **这里接的是哪一个 `TimeoutError`，说清**（本波 Minor #1）：接的是
                #    **`wait_for` 那个上界到点**交出来的那一个（"还没到"），
                #    **不是** `next_batch` 自己抛的 `TimeoutError`（那是**总线那一侧**的事故，
                #    吞掉它等于把一条真故障读成"还没到"）。
                # 今天分不开它们（`wait_for` 到点正是拿它当信号传出来的，而下游
                #    `next_batch` 那一侧只等一面 `asyncio.Event`、从不超时 —— 所以那条路
                #    今天走不到）。⇒ 这里**只把话说清**，不另造一条分支（造了就是一条
                #    永远走不到的假路）。
                #    ⚠️ **真要分开，改法是把 `wait_for` 换成一个不借 `TimeoutError` 传信号的
                #    上界**（3.11 的 `asyncio.timeout` ／ 一个自己排的句柄）；那是**另一个
                #    形状**，而它与 "`try` 只圈住一次调用" 这条一起改才对 ——
                #    **别只把 `except` 收窄**：那样 `batch = ...` 那一半就会落在 `except` 之外，
                #    于是每次成功都要`next_batch` **调两遍**（一个可判的形状，不是风格问题）。
                try:
                    batch = await asyncio.wait_for(next_batch(bus, self.ready), idle_s)
                    break
                except TimeoutError:
                    if tries >= 1:
                        break                                  # ⇒ 落到下面那一句（回读一行）
                    tries += 1
                    self.ready.clear()                         # ⚠️ 见上：清干净，不是救命的那一句
            if batch is not None:
                return batch

    def _revoke_batch_identity(self):
        from .voice_draft import retire
        retire(self)
        self._handoff_rule_route = None
        self._identity_read_view = None
        self._incoming_prompt_context = None
        self._owner_prompt_batch = None
        lease = self._batch_identity
        if lease is not None:
            lease.revoke()
        self._batch_identity = None
        owner = getattr(self, '_batch_identity_owner', None)
        if owner is not None and getattr(owner, '_batch_identity_lease', None) is lease:
            owner._batch_identity_lease = None
        self._batch_identity_owner = None

    def _bind_batch_identity(self, batch):
        from .channel_admission import BatchIdentityBridge
        batch = tuple(batch)
        owner = getattr(self.app, 'loop', None)
        if ((self.signal is not None and self.signal.is_set())
                or getattr(owner, 'sealed', False) or getattr(owner, '_identity_retired', False)):
            self._revoke_batch_identity()
            raise ValueError('batch identity retired')
        lease = self._batch_identity
        if (lease is not None and len(batch) == len(lease.snapshot)
                and all(a is b for a, b in zip(batch, lease.snapshot))):
            self.current_batch_identity()
            lease.resolve(batch)
            return lease.snapshot
        self._revoke_batch_identity()
        self._observation_budget = None   # A different batch starts a new audience binding.
        lease = BatchIdentityBridge(batch)
        from .continuation import is_continuation
        for item in batch:
            if is_continuation(item):
                policy = getattr(self, 'wake_policy', None)
                if policy is None:
                    raise ValueError('continuation policy unavailable')
                policy.claim_continuation(item, lease, now_ms=_now_ms())
        self._batch_identity = lease
        self._batch_identity_owner = owner
        self._batch_identity_generation = getattr(owner, 'generation', None)
        if owner is not None:
            previous = getattr(owner, '_batch_identity_lease', None)
            if previous is not None and previous is not lease:
                previous.revoke()
            owner._batch_identity_lease = lease
        self._active_batch = lease.snapshot
        return lease.snapshot

    def current_batch_identity(self):
        owner = getattr(getattr(self, 'app', None), 'loop', None)
        bound_owner = getattr(self, '_batch_identity_owner', None)
        if ((self.signal is not None and self.signal.is_set())
                or getattr(owner, 'sealed', False) or getattr(owner, '_identity_retired', False)
                or (bound_owner is not None and (owner is not bound_owner
                    or getattr(owner, 'generation', None) != self._batch_identity_generation))):
            rejection = ValueError('batch identity retired')
            stopped = self._matches_operator_retirement(owner)
            self._revoke_batch_identity()
            if stopped:
                error = BatchIdentityStopped('operator stop retired active batch')
                self._shutdown_identity_rejection = error
                raise error from rejection
            raise rejection
        lease = self._batch_identity
        if lease is None and self._active_batch:
            raise ValueError("batch identity unavailable")
        if lease is not None:
            lease.resolve(self._active_batch)
        return lease

    def _matches_operator_retirement(self, owner):
        baseline = getattr(self, '_operator_retirement', None)
        if baseline is None:
            return False
        prior_owner, lease, generation, snapshot, originals, seal = baseline
        if (owner is not prior_owner or self._batch_identity_owner is not prior_owner
                or self._batch_identity is not lease
                or owner.generation != generation + 1
                or not owner._identity_retired or owner.sealed
                or len(self._active_batch) != len(snapshot)
                or any(a is not b for a, b in zip(self._active_batch, snapshot))):
            return False
        return (lease._fingerprint(self._active_batch) == seal
                and lease._fingerprint(originals) == seal)

    def original_batch(self):
        lease = self.current_batch_identity()
        return lease.resolve(self._active_batch) if lease is not None else ()

    async def deliver(self, batch: Sequence[Any], *, ledger: Any = None) -> bool:
        try:
            admitted = await self._deliver_bound(batch, ledger=ledger)
        except BaseException:
            self._revoke_batch_identity()
            raise
        if not admitted:
            self._revoke_batch_identity()
        return admitted

    async def _deliver_bound(self, batch: Sequence[Any], *, ledger: Any = None) -> bool:
        """ **`loop.py:132` 那一格** —— 出口接的就是这一条线（`build()` 末尾那一句的位置）。

        ⚠️ **谁递进来的**：那个 `ledger` 就是 `App`（`app.deliver` 的主人）。它在这里
           **先走**：那一批照旧进 `App.delivered`（**出口不是把账本换掉**，是账本之外
           多一个出口）—— 少了这一步，"看得见"与"账上有"就分了家，而这一刀要的正是
           **两件事同一拍**。⚠️ 而它的返回值**在这里被丢掉**（`ledger` 缺席时不抛）；
           下面那个 `bool` 说的是**同一件事**（"这一批里有东西"），由本方法自己判。

        ⚠️ **`ledger=None` 是合法状态，而不是"没有账本"**：它指的是"这一条出口**只打**"——
           测试用它把"出口自己那一步"与"两点连起来"分开量（`tests/test_console.py` 里
           默认打到标准输出那一条用的就是这个形状）。 而那个 `Any` 不是偷懒：
           本模块**不许 import `App`**（那是装配那一层的东西 —— 出口只要"有个 `.deliver`
           的收到这一批"）。

        ⚠️ **空批**：**一个字都不打**，回 `False` —— 与 `App.deliver` 对空批的答案、
           与 `loop.py:132` 那道闸**同一个口径**（空批不跑轮；而不打，是为了让"账上有几批"
           这个问题只有一个答案）。
        ⚠️⚠️ **"空批"有两个形状，而这一点是本波修的**：一个**空表**（`[]`）与
           一个**只有没有正文的件**的批（`[EventEnvelope(text="")]`）是**同一件事** ——
           后者是**真值**，于是"只判 `not batch`"会让它一路走到 `loop.py:132` 那一句、
           **跑一整轮**（模型重答上一轮 ＋ 会话里多一条 assistant）。判据就是 `_has_content`
           那**一条**共用规则（它的 docstring 记着实测的形状）：
           · 批里**一件有正文的都没有** ⇒ 与空批同解：不打、回 `False`、`ledger` 也不动；
           · 有 ⇒ 只把**有正文的那些**交下去（记账 ／ 打印 ／ 往后走都是**同一份**）
             ⇒ `_into_the_session` 那一边从此收不到"没有正文的件"（它照旧自己再判一次，
               那是**同一个答案**的第二道读，不是第二条规则）。
           ⚠️ 于是 `batches` 那个序号也只有一个口径：**空批不占号**（类文档那一句）。
        ⚠️ **它自己不等、不看钟**：这一格只碰内存与那个注入面 ⇒ 测试那条"不许依赖墙上
           时间"的规矩在这里没有可违的地方。
        ⚠️ **写不出去时不咽**（见 `_to_stdout`）：出口抛，就是**这一端**的事故，
           而"咽掉它、让这一批看起来送出去了"才是这一格里最坏的一种写法。
        """
        if not batch:
            return False
        # ⚠️⚠️ **"什么算一批"与"什么算有正文"是两件事，而这一句是前者的**第二半"** ——
        #    见 `_has_content` 那条共用规则：一条**没有正文**的件**不算东西**
        #    （一个空行就是那个形状，实测的形状写在那条规则的 docstring 里）。
        # 落在这里、落成"过滤一份再往下走"，是为了让**下面每一步看的是同一份**：
        #      账本 ／ 打印 ／ `_frames_for`（"一批几项就几帧"那条契约照旧成立）。
        batch = [item for item in batch if _has_content(item)]
        if not batch:
            return False
        batch = self._bind_batch_identity(batch)
        admission = None
        if callable(self.batch_admission):
            self._display_batch_marker = None
            from .channel_admission import ChannelAdmission
            # Pin a private snapshot before checking receipts or appending.
            try:
                # Host-only context delivery is not a request for a channel reply.
                # Its provenance is checked by the same predicate as guarded_round.
                admission = (ChannelAdmission(True, "internal") if _context_only_batch(batch)
                             else self.batch_admission(self._active_batch))
                if type(admission) is not ChannelAdmission:
                    raise ValueError("invalid channel admission result")
                admission.__post_init__()
                if admission.admitted and admission.remote_notice:
                    raise ValueError("invalid channel admission result")
            except Exception:
                admission = ChannelAdmission(False, "admission_failed")
            if admission.admitted:
                self._into_the_session(batch)
        if ledger is not None:
            await ledger.deliver(batch) # 先入账（同一条流水线，见类文档）
        self.batches += 1
        # ⚠️ 这一句是**取一次**、不是判一次：`__post_init__` 已经保证了它有值
        #    （默认那个 `_to_stdout`）—— 一个"没递出口"的状态在本类里**不存在**，
        #    所以这里没有第三条分支可写（写了就是一个永远走不到的假路）。
        write = self.write if self.write is not None else _to_stdout
        visible = [item for item in batch if self._visible_to_owner(item)]
        for index, frame in enumerate(_frames_for(visible, self.batches) if visible else ()):
            if not index and _context_only_batch(batch):
                continue
            if index:
                frame['display_id'] = self._frame_reference(visible[index - 1])
            elif (marker := getattr(self, "_display_batch_marker", None)) is not None:
                frame = marker
            write(frame)
        self._display_batch_refs = {}
        if admission is not None and not admission.admitted:
            from .channel_admission import CHANNEL_UNAVAILABLE_NOTICE
            self.channel_blocked_batches += 1
            write({"kind": _A_TOOL, "who": "host", "at": _now_at(),
                   "tool": "channel_admission", "stage": "result", "run": "",
                   "text": CHANNEL_UNAVAILABLE_NOTICE,
                   "host_notice": True, "status": admission.reason})
            if admission.remote_notice and callable(self.output_tap):
                try:
                    import inspect
                    sent = self.output_tap(self._active_batch, CHANNEL_UNAVAILABLE_NOTICE)
                    if inspect.isawaitable(sent):
                        await sent
                except Exception:
                    # No model retry, no alternate recipient, no fallback send.
                    write({"kind": _A_TOOL, "who": "host", "at": _now_at(),
                           "tool": "channel_admission", "stage": "result", "run": "",
                           "text": "固定提示未能送达；未重试或切换回复目标。",
                           "host_notice": True, "status": "notice_delivery_failed"})
            return False
        return True

    def enable_display_timeline(self) -> None:
        if not callable(getattr(self.session, "records", None)):
            return
        self.display_timeline = True
        self._display_batch_marker = None
        records = self.session.records()
        epochs = [int(row.name.split(":", 1)[1]) for row in records
                  if row.kind == "ui_handoff_note" and row.name.startswith("handoff_note:")
                  and row.name.split(":", 1)[1].isdecimal()]
        self._display_batch_base = epochs[-1] if epochs else 0
        self.batches = max([self._display_batch_base, *(int(row.name) for row in records
                            if row.kind == "ui_batch" and row.name.isdecimal())])

    def _display_marker(self, kind, number, text):
        from uuid import uuid4
        from .note import Record
        from .owner_tick import OWNER_SCOPE

        stamp = _now_ms()
        display_id = "timeline:" + uuid4().hex
        try:
            self.session.append(Record(kind=kind, role="system", name=str(number), text=text,
                                       ts_ms=stamp, scope=OWNER_SCOPE, is_flow=True,
                                       display_id=display_id))
        except (OSError, ValueError) as error:
            print(f"   [web] 会话定位标记保存失败（{type(error).__name__}）。",
                  file=sys.stderr, flush=True)
            return None
        return self._timeline_frame(kind, text, stamp, display_id)

    @staticmethod
    def _handoff_note_frame(record):
        return {"kind": "tool", "who": "host", "tool": "handoff_note", "stage": "result",
                "text": record.text, "host_notice": True, "display_id": record.display_id,
                "at": time.strftime("%Y-%m-%dT%H:%M", time.localtime(record.ts_ms / 1000))}

    @staticmethod
    def _timeline_frame(kind, text, stamp, display_id):
        return {"kind": "batch" if kind == "ui_batch" else "batch-status",
                "who": None, "at": time.strftime("%Y-%m-%dT%H:%M", time.localtime(stamp / 1000)),
                "text": text, "display_id": display_id}

    def _visible_to_owner(self, item: Any) -> bool:
        if self.owner_view is None:
            return True
        try:
            return bool(self.owner_view(item))
        except Exception:
            return False

    def _owner_round_visible(self) -> bool:
        external = (item for item in self._active_batch
                    if str(getattr(item, "origin", "") or "") == "external")
        return all(self._visible_to_owner(item) for item in external)

    # ── Task 3：那个入口（装 ／ 起 ／ 泵 ／ 打 ／ 停）────────────────────────
    def observe_text(self, text, *, mode='collect', round_id=None):
        """Local operator observation with explicit scheduling, on the host loop."""
        from .activity_control import DeliveryMode
        from dataclasses import fields
        mode = DeliveryMode(mode)
        if not isinstance(text, str) or not text.strip() or len(text) > 16384:
            raise ValueError('observation text must be 1..16384 characters')
        if self.activity_control.stopping():
            raise RuntimeError('activity admission is stopping')
        if mode in (DeliveryMode.YIELD, DeliveryMode.INTERRUPT):
            current = self.round_control.status()
            if not round_id or current['round_id'] != round_id or not current['processing']:
                raise ValueError('transfer requires the current round identity')
        elif round_id is not None:
            raise ValueError('round identity only applies to transfers')
        event = _line_to_event(text, cursor=self.cursors + 1)
        owner = getattr(self, 'local_observations', None)
        if owner is not None:
            event, result = owner.submit(event, mode=mode, round_id=round_id)
        else:
            event = self._record_event({field.name:getattr(event, field.name)
                for field in fields(event) if field.name != 'cursor' and getattr(event, field.name) is not None})
            result = self.activity_control.submit(event, mode=mode, round_id=round_id)
        self.cursors = max(self.cursors, event.cursor)
        return {**result, 'event_ref':'event:' + str(event.cursor)}

    async def read_observations(self, source):
        """Read local input while allowing admitted bus observations to wake it."""
        if self.bus.ready:
            return CHECK_THE_BUS
        line = asyncio.create_task(source())
        wake = asyncio.create_task(self.ready.wait())
        try:
            done, _ = await asyncio.wait({line, wake}, return_when=asyncio.FIRST_COMPLETED)
            return line.result() if line in done else CHECK_THE_BUS
        finally:
            for task in (line, wake):
                if not task.done():
                    task.cancel()
            await asyncio.gather(line, wake, return_exceptions=True)

    def observe_line(self, line):
        """Admit the existing local text/image envelope before model scheduling."""
        from dataclasses import replace, fields
        evidence = getattr(self, 'runtime_evidence_owner', None)
        parent = None
        if evidence is not None:
            line, parent = evidence.unwrap_input(line)
        if self.activity_control.stopping():
            raise RuntimeError('activity admission is stopping')
        text, blobs = _split_a_line(line)
        if not text and not blobs:
            raise ValueError('empty local observation')
        event = replace(_line_to_event(text, cursor=self.cursors+1), blobs=blobs)
        owner = getattr(self, 'local_observations', None)
        if owner is not None:
            event, result = owner.submit(event, mode='ready')
        else:
            event = self._record_event({field.name:getattr(event,field.name)
                for field in fields(event) if field.name != 'cursor' and getattr(event,field.name) is not None})
            result = self.activity_control.submit(event, mode='ready')
        if evidence is not None:
            evidence.bind_event(event, parent)
        self.cursors = max(self.cursors, event.cursor)
        return {**result, 'seq':event.cursor, 'event_ref':'event:'+str(event.cursor)}

    def manage_timers(self, operation, body=None):
        """Local diagnostics; invoked only on the application's event loop."""
        from .timer_control import manage
        app = getattr(self, "app", None)
        signal = getattr(self, "signal", None)
        if (app is None or getattr(app, "timers", None) is None or
                getattr(getattr(app, "loop", None), "sealed", False) or
                (signal is not None and signal.is_set())):
            return 503, {"error": "timer_runtime_unavailable"}
        policy = getattr(self, "wake_policy", None)
        result = manage(
            app.timers, operation, body,
            follow_up_delay_ms=getattr(self, "timer_follow_up_delay_ms", None),
            approve=(None if policy is None else policy.approve), wake=app.wake.set)
        if operation == "list" and result[0] == 200 and policy is not None:
            try:
                result[1]["wake_policy"] = policy.snapshot(now_ms=_now_ms())
                live = {t.id: t for t in app.timers.list()}
                for row in result[1]["timers"]:
                    row["effective_kind"] = policy.kind(live[row["id"]], now_ms=_now_ms())
            except (OSError, ValueError, TypeError, OverflowError):
                result[1]["wake_policy"] = {"error": "wake_budget_unavailable"}
        return result

    def _admit_wake_speech(self, text="", *, phase="plain", action=""):
        admission = getattr(self, "speech_admission", None)
        if not callable(admission):
            return True
        try:
            allowed, reason = admission(text=text, phase=phase, action=action, now_ms=_now_ms())
        except Exception:
            allowed, reason = False, "speech_budget_unavailable"
        if not allowed:
            import sys
            print("[speech] not delivered: admission rejected", file=sys.stderr, flush=True)
        return bool(allowed)

    def _reminder_notice_visible(self):
        from .note import Record
        from .retrieval_scope import OWNER
        return self._visible_to_owner(Record(role='system', scope=OWNER))

    def flush_reminder_notices(self):
        from .timers import _now_iso
        notices = getattr(self, 'reminder_notices', None)
        control = getattr(self, 'activity_control', None)
        if notices is None or control is None or not callable(self.write):
            return 0
        def allowed():
            return (not control.stopping() and not control.paused()
                    and self._reminder_notice_visible())
        try:
            self._reminder_notice_retry = False
            return notices.flush(at=_now_iso(), allowed=allowed, write=self.write)
        except (OSError, ValueError, RuntimeError, __import__('sqlite3').Error) as error:
            self._reminder_notice_retry = True
            print(f"   [reminder] 系统通知暂未确认输出（{type(error).__name__}）；保留待续送。",
                  file=sys.stderr, flush=True)
            return 0

    def _reminder_delivery_note(self, stage, **detail):
        policy = getattr(self, 'wake_policy', None)
        if policy is not None and (getattr(policy, 'observer', None) is not None
                                   or stage == 'console_output_emitted' and hasattr(policy, 'note_delivery')):
            try:
                policy.note_delivery(tuple(self._active_batch or ()), now_ms=_now_ms(),
                    stage=stage, detail=detail, identity_bridge=self.current_batch_identity())
            except Exception as error:
                # An uncertain receipt after output must not retry that output.
                import sys
                print('[reminder] output receipt unavailable: ' + type(error).__name__,
                      file=sys.stderr, flush=True)

    def _continuation_diagnostic(self, source, status):
        if getattr(getattr(self, 'app', None), 'store', None) is not None:
            from .timers import _now_iso
            self._record_event({'type': 'continuation-status', 'ts': _now_iso(),
                'source': 'host', 'origin': 'internal', 'text': '',
                'meta': {'source': source, 'status': status}})

    def _queue_continuation(self, text, source, job, *, still_valid=lambda: True, pending_valid=lambda: True):
        from dataclasses import replace
        from .continuation import MARKER
        policy = getattr(self, 'wake_policy', None)
        if policy is None or job is None:
            self._continuation_diagnostic(source, 'no_job_or_policy')
            return False
        if source not in ('handoff', 'consolidate', 'activity') or source in job.claimed_sources:
            return False
        if not job.valid(self, _now_ms()) or not still_valid() or not pending_valid():
            self._continuation_diagnostic(source, 'job_retired')
            return False
        job.claimed_sources.add(source)  # Failed/uncertain publication is not replayed.
        now = _now_ms()
        if source == 'consolidate':
            text = ('〔后台整理提出的待核实观察〕\n'
                    '以下是模型对历史的解释，不是用户新消息、指令或已经证实的事实。'
                    '可以自主判断是否关注；涉及过去是否成功执行、读到或发言的判断，'
                    '须与对应原文和完整回执核对。摘要、省略、分页或预览中没看到，不等于没有发生。'
                    '不要仅据这段观察写成确定的自述、技能或对外结论。\n'
                    + text)
        event = replace(_line_to_event(text, cursor=self.cursors + 1),
                        origin='internal', type='continuation', source=source,
                        sender_key=None, meta={MARKER: {'scope': job.scope}})
        if not policy.bind_continuation(event, now_ms=now,
                                       valid=lambda stamp: job.valid(self, stamp) and still_valid(),
                                       pending_valid=pending_valid):
            self._continuation_diagnostic(source, 'budget_or_ticket_unavailable')
            return False
        self.cursors += 1
        sink = getattr(self, 'activity_control', self.bus)
        sink.push(event, trigger='piggyback' if source == 'consolidate' else 'flush')
        self._continuation_diagnostic(source, 'queued')
        return True

    def _bind_host_wake(self, event):
        pending = getattr(self, "_pending_wake_ticket", None)
        if pending is not None:
            policy, ticket = pending
            policy.bind(event, ticket)

    def consumed_batch(self) -> tuple[Any, ...]:
        """Return a detached snapshot of the most recently consumed input.

        Reading does not dequeue, re-filter, append, or change lifecycle state.
        Nested values are copied, not recursively frozen. Availability follows
        the existing consumer; a snapshot is not an authorization capability.
        """
        from copy import deepcopy
        from dataclasses import fields
        from .events import EventEnvelope
        from .frozen_json import thaw
        snapshot = deepcopy(tuple(self._active_batch))
        for event in snapshot:
            if type(event) is EventEnvelope:
                # Detached observations may be edited locally. The store's
                # original envelope remains recursively immutable.
                for field in fields(event):
                    object.__setattr__(event, field.name, thaw(getattr(event, field.name)))
        return snapshot

    def assemble(self, *, root: Any = _DEFAULT_ROOT, config: Any = None,
                 transport: Any = None, generate: Any = None, session: Any = None,
                 handoff: Any = None, read: Callable[[], Any] | None = None,
                 head: Any = None, prefix: Sequence[Any] | None = None,
                 persona_root: Any = None, grace_ms: int | None = None,
    # **2026-10-07「3b-②」**：那一格事实（ 缺省 None ⇒ 前缀一个字都不变 ）。
    facts: Any = None,
                 # **2026-10-07「③ 触发／自动拉进」**：那一格（ 同样缺省 None ⇒
                 # 前缀一个字都不变 ； 而它一到，"这一趟碰到那个词"那份就在眼前 ）。
                 pull_in: Any = None,
                 # **2026-10-10「当前这一头」**（本刀）：装"**正在说话的是哪一头**"的
                 #    名字，或逐请求读取名字的零参来源。
                 #    固定名字照旧由装订器选一行；来源则把当前场景另附。
                 # **缺省 `None` ⇒ 退回表里第一行**（ 那一头的名字**是本仓的禁字
                 # 之一** ⇒ 这里一个字都不写它 ）⇒ **不递 ⇒ 逐字节
                 # 与从前一样** （与 `facts` ／ `pull_in` 同一条成法：**由入口递**，
                 # 本层不认识任何一头的名字 —— 核心不知道语义那条纪律）。
                 channel: Any = None,
                 channel_context: Any = None,
                 channel_scene: Any = None,
                 batch_admission: Any = None,
                 request_send_fence: Any = None,
                 cache_prefix_diagnostics: bool = False,
                 linked_accounts: Any = None,
                 identity_hint: Any = None,
                 identity_hint_at_tail: bool = True,
                 retrieval_at_tail: bool = False,
                 channel_at_tail: bool = False,
                 consolidation_write: Callable[[str, str, str], Any] | None = None,
                 consolidation_read: Callable[[str], str] | None = None,
                 consolidation_pending_read: Any = None,
                 consolidation_pending_settle: Any = None,
                 consolidation_stamp: Callable[[], str] | None = None,
                 #: 记忆层那几个执行段（2026-10-05）：由入口递，本层不认识它。
                 memory_handlers: Mapping[str, Callable[..., str]] | None = None,
                 #: **跑工具那一步**（2026-10-05 作者："往下走" ⇒ "做"）—— 由入口递
                 #: （形如 `async def run_tools(calls) -> DispatchResult` ）。
                 #: ⚠️ **调用点早就在**（`round.py:214` ）；缺的一直是"递进来"这件事 ——
                 #: `round.py:45` 那句"欠主：装配／入口那一层"说的就是这一格 。
                 #: 不递 ⇒ 那一层的行为**逐字不变**（`round.py:213` 那个 `is not None` ）。
                 run_tools: Callable[..., Any] | None = None,
                 event_store: Any = None,
                 #: ★ **2026-10-12（作者：「一起解开」）**：那一只闹钟表**原样转交** `build()` ——
                 #: 由入口造一只递进来 ⇒ 全树**只留一只**（否则两处各造一只：
                 #: `due()` 看的是内存快照 ⇒ 它定的闹钟打点器看不见，而整表覆盖会互相抹掉）。
                 #: ⚠️ 不递 ⇒ 逐字节与从前一样（从 `pacing_root` 现造一只）。
                 timers: Any = None,
                 #: **2026-10-12（作者：「补」）**：**真请求那几颗鉴权头** —— 一个零参可调用，
                 #: 由入口递（那里认识 `engine` 与那一档的 `secret`；本层不认识密钥）。
                 #: ⚠️ 只在"这一层自己造 transport"时用得上（`owns`）——
                 #: 调用方递了 transport，那一份就归它自己（与 `read` ／ `generate` 同一条守卫）。
                 #: 不递 ⇒ 一颗头都不加（逐字节与从前一样；本机那条路就是如此）。
                 key_headers: Any = None,
                 group_policy: Any = None,
                 wrap_round: Callable[..., Any] | None = None,
                 runtime_evidence: Any = None,
                 reply_review: bool | str = False
                 ) -> Any:
        """ **装** —— 把零件接成一个能起的进程，交回 `App`（也记在 `self.app` 上）。

        ⚠️⚠️ **它接的是那四样，而每一处都有一个出处**（别按名字猜 —— 这一支栽过两次）：
           · **总线**（`bus.WakeBus`）： 三个注入面一个不落（`clock` ／ `schedule` ／
             `cancel`），而 **`on_ready=self.ready.set`** —— Task 2 的 finding #1 那句
             "装 `on_ready` 的是装配那一层"就落在这里（**同一个 `Console`**：喂给
             `feed.next_batch` 的那面旗与 `pump` 推完自己抬的那面旗**必须是同一个**）；
           · **泵 ⇒ `take_batch`**：`self._take_a_batch`（**一个绑定的方法**，不是
             `partial`）—— `loop.py:171` 那个槽要的就是"零参、可等待、回 `list | None`"，
             而 `Console.pump` 的返回值**逐字**就是那个形状（Task 2 那一刀的设计）；
           · **引擎 ⇒ `make_run_round`**：`config` ／ `transport` ／ `signal` ＋ 四个
             "外面给的东西"（`head` ／ `hard_tokens` ／ `estimate` ／ `record_event`）
             ＋ 两个时钟 —— **这是那个调 `generate` 的地方唯一的那一个调用者**；
           · **出口 ⇒ `build(console=self)`**：`loop.py:132` 那一格接成
             `partial(self.deliver, ledger=app)`（先记账、再打）—— ⚠️ 而**不是**事后从
             外面戳 `loop._deliver`（见类文档里那一句：守卫只有 `build()` 里写得出）。

        ⚠️ **`hard_tokens` 那条公式**：`max(0, context_window − max_tokens)` ——
           `Config` 那两个字段就是为它准备的（`config.py:17-21` 逐字写着这个式子，
           而 `handoff-recon` §六 点名"它是装配者的一部分"）。两个数都从 `config` 现读。
        ⚠️ **`head` ／ `prefix` 是声明侧的插口**：见 `_DECLARATION_OWED_BY`。
            **`head` 的缺省从空那一串换成 `None`（＝现装那一句话）**（本刀拍的）：
           空那一串**同时**表示"调用方明确不要"与"没人递过"，而那是**两个意思**
           ⇒ 与 `prefix` 那一格同一条哨兵成法（`None` ＝ 现装；`()` ＝ 明确不要；
           一串或一个可调用 ＝ 调用方说了算）。
           ⚠️ **那一句话每请求现装一遍**（一次 `段序.md` ＋ 九次 `read_text`，量到 16553
           字节）—— 那是**故意的**：同一份正本订出来的是**同一份字节**，而它正是
           "改一个字，下一次请求就生效"这句话的**唯一**实现。
        ⚠️ **`prefix` 的缺省是 `None`（＝现装），不是空那一串**（本刀拍的）：
           空那一串**同时**表示"调用方明确不要前缀"与"没人递过"，而那是**两个意思**
           ⇒ 缺省哨兵把它们分开（`None` ＝ 现装；`[]` ＝ 明确不要）。
        ⚠️ **`persona_root`**：装订器要的那条**根**（它认的名字是相对名）—— 不递就用
           `prefix.DEFAULT_PERSONA_ROOT`（与页面那一侧**同一个算法**）。
        ⚠️ **谁造谁关**：`transport` 不递 ⇒ 这里造一个**真的** `HttpxTransport`，而在
           `stop()` 里关掉它（`owns_transport`）；**递进来的那个归调用方**（`cli.py:28-31`）。
        ⚠️ **`store_root` 给了、`log_root` ／ `unresolved_root` 不给**（都是合法状态）：
           事件库是**真的要用**的（`round.py` 那条"思考进日志"经 `_record_event` 写它）；
           其余存储是否装配取决于各自 root 与 handlers；当前 launch 会装配 unresolved
           执行段，run_tools 也已接入。不能用早期“工具不可达”描述当前正式入口。
        ⚠️ **导入在这一层是懒的**（在函数体里）：这一页的四个新邻居（`app` ／ `bus` ／
           `handoff` ／ `round`）都不必在 import 时就拉进来 —— 与 `_line_to_event`
           那一处的成法一致（少一个 import 时的耦合边），而它们**没有循环**。
        """
        if reply_review is not False and reply_review is not True and reply_review != 'host':
            raise ValueError('reply_review must be False, True, or host')
        # ⚠️ 都是在函数体里 import（见 docstring 末段）—— 不写在这一页的头上是有意的。
        # Opt-in, trusted host API only. Never silently ignore a configured fence.
        if request_send_fence is not None:
            if not callable(request_send_fence):
                raise TypeError("request_send_fence must be a trusted host context factory")
            if transport is not None:
                raise ValueError("request_send_fence requires Console-owned transport")
        if wrap_round is not None and transport is None:
            raise ValueError("wrap_round requires caller-owned transport")
        from .app import build
        self.runtime_evidence_owner = runtime_evidence
        from .bus import WakeBus, WakeBusOptions
        from .engine import live as live_config
        from .generate import generate as generate_itself
        from .protocol import adapt as adapt_protocol
        from .round import make_run_round
        from .session import Session
        from .timers import _now_iso
        from .tokens import estimate_messages_tokens
        from .transport import HttpxTransport

        self.batch_admission = batch_admission
        root = Path(root)
        if config is None:
            # **刀 2：缺省那一份从"一个值"换成"一个来源"** ——
            #    `engine.live()` 交回的是活视图：**属性一读就现算** ⇒ 手工改
            #    `data/engine.json` 之后**下一次请求就换了脑**（不必重开进程）。
            #    ⚠️ 仍然**一个字不改 `generate()` ／ `round.py`**：活视图与 `Config` **同形**。
            config = live_config(root)
        if session is None:
            session = Session(root / "session-main.jsonl")
        owns = transport is None
        from .consolidation_prefix import PrefixCache, CaptureWire, ReviewWire
        self._consolidation_prefix = PrefixCache()
        if transport is None:
            # **2026-10-12**：那几颗鉴权头交给 transport **每次请求现算**（见
            #   `transport._headers_of` 那一段：档案可以中途换 ⇒ 头也必须跟着换）。
            from .model_proxy import model_network
            selector = getattr(config, "model_network", None) or getattr(config, "proxy_for", None)
            if not callable(selector):
                selector = lambda url: model_network(root, url)
            from .provider_record_replay import check_observation_before_send
            transport = HttpxTransport(headers_of=key_headers, proxy_of=selector,
                                       request_check=check_observation_before_send,
                                       **({"send_fence": request_send_fence}
                                          if request_send_fence is not None else {}))
        # **先造那面旗，再把它递给两处** —— `build(signal=…)` 与
        #    `make_run_round(signal=…)` **必须是同一个对象**（`round._OWED` 的第一句）。
        if self.signal is None:
            self.signal = asyncio.Event()
        self.read = read
        self.session = session
        self._request_config = config
        self._completed_request_preview = None
        self._request_branch_incoming = ()
        self._replay_fallback_in_batch = False
        self._replay_history_boundary = None
        from .provider_record_replay import HistoryQuotes
        self._history_quotes = HistoryQuotes()
        from .request_context_branch import RequestBranches, identity_for as branch_identity
        branches = RequestBranches(root / "provider-state" / "request-context-branches-v1.json")
        branches.recover_append(session)
        self.request_context_branches = branches
        self._branch_config = config

        def branch_key(active_config: Any = None, scope: Any = None) -> dict[str, Any]:
            from .session_view import batch_scope
            return branch_identity(active_config if active_config is not None
                                   else self._branch_config,
                                   batch_scope(self._active_batch) if scope is None else scope)

        def branch_filter() -> Any:
            return (branches.predicate(session.records(), branch_key())
                    if branches.entries else None)

        def branch_revision() -> Any:
            return branches.visibility_revision(branch_key())

        def branch_append(row: Any) -> None:
            branches.append(session, row, branch_key())

        def branch_inputs() -> None:
            incoming = self._request_branch_incoming
            if branches.entries:
                branches.claim_transaction(session, incoming, branch_key())
            self._request_branch_incoming = ()

        if linked_accounts is None:
            round_session = session
        else:
            from .session_view import ScopedSession, batch_scope

            round_session = ScopedSession(
                session, lambda: batch_scope(self._active_batch), linked_accounts,
                group_policy=group_policy, request_filter=branch_filter,
                request_revision=branch_revision,
                recall_available=lambda: any(isinstance(tool, Mapping) and tool.get("name") == "read_events"
                                             for tool in (getattr(self._branch_config, "tools", ()) or ())),
                append_transaction=branch_append)
        self._request_session = round_session
        # Display-only boot preview: no active lease, linked identity lookup,
        # retrieval callback, channel scene, or model request is manufactured.
        def standby_messages():
            if head is None:
                fixed = _the_prefix_head(persona_root, include_channel=False)
                fixed = [] if _in_the_archive_already(session, fixed) else fixed
            elif callable(head):
                raise ValueError('standby_dynamic_head_unavailable')
            else:
                fixed = list(head)
            stable = getattr(facts, 'stable_prefix', '')
            if stable:
                fixed.append({'role': 'system', 'content': stable})
            scope = 'private:console:local-owner'
            branch = (branches.predicate(session.records(), branch_key(self._request_config, scope))
                      if branches.entries else None)
            def visible(row):
                shared = bool(row.is_head and row.role == 'system')
                return shared or (row.scope == scope and (branch is None or branch(row)))
            return session.outbound(fixed, (), visible=visible)
        self._standby_messages = standby_messages if linked_accounts is not None else None
        # **那一格**（`head`）：**每次出请求现取一次**的插口 —— 本刀把缺省填上
        # （`None` ⇒ 现装那一句话）。 它插在会话开头**连续 system 之后**
        #    （`session.py:388-391`）⇒ 次序就是"那一句话在前、我那一行在后"。
        # A retrieval source can explicitly declare that its changing evidence
        # belongs to this request, never to the stable history prefix.
        retrieval_at_tail = retrieval_at_tail or getattr(pull_in, "request_local", False) is True
        a_head_cell = _a_head_cell(head, persona_root, round_session, facts=None,
                                   pull_in=None if retrieval_at_tail else pull_in,
                                   channel=channel,
                                   identity_hint=None if identity_hint_at_tail else identity_hint,
                                   channel_context=channel_context,
                                   channel_scene=channel_scene,
                                   channel_at_tail=channel_at_tail,
                                   stable_facts=getattr(facts, "stable_prefix", ""))
        # **那一格存下来** —— 页上那两个数（`request_size()`）要读的正是它：
        #    它们量的必须是**这一趟装配**现装的那一份请求（Task 1 里它没有读者 ⇒ 不存）。
        # 2026-10-11（尾巴缝）：那一格事实**改走尾巴** —— 它每轮都会变
        # （"现在是几点"），放在 system 前缀里就等于每轮改写开头 ⇒ 缓存全废。
        # `_with_the_facts([], facts)` 就是"只造那一条 system 项"，形状与从前逐字相同。
        #
        # ⚠️⚠️ **2026-10-17：试过让软阈那句提醒也骑这条尾巴 —— 量完回退了。**
        #    那一改**机制上成立**（请求体逐轮验过：第 11 轮里它确实在，而且是最后一条、
        #    只出现一次）。**但它把一个补救拆掉了**：那句话原来走总线
        #    ⇒ 批末之后**另起一批** ⇒ 而那一批**没有新的问话**，
        #    模型在那批里**会把话收掉**（实测原话："没有那本词典……你要查哪个？给我个词"）。
        #    ⇒ 改骑尾巴之后：模型**在批内看到它、照样再调一次**，12 轮撞硬顶、
        #      **一句不说**（同一批三趟：5 ／ 6 ／ 12 轮，**三趟都没答话**），
        #      而**再没有"下一批"来给模型收尾**了。
        #    ⇒ 所以：那句提醒的用处**不在"打断打转"**（它本来也不是干这个的），
        #      而在**给模型一个收尾的机会** —— 而那个机会是"另起一批"给的。
        #    ⚠️ 判据 `tests/test_the_soft_hint_rides_the_tail.py` **跟着删掉**
        #      （留一条钉着已回退的行为就是一句假话）。
        # Current identity is request-local; changing it must not replace the
        # prefix before durable history. False preserves the old position for A/B.
        tail_identity = identity_hint if identity_hint_at_tail else None
        tail_retrieval = pull_in if retrieval_at_tail else None

        from .evidence_contract import EVIDENCE_CONTRACT, LOCAL_PRIVATE_EVIDENCE_CONTRACT, HostEvidenceText
        evidence_row = {'role': 'system', 'content': HostEvidenceText(EVIDENCE_CONTRACT)}
        local_rule = HostEvidenceText(LOCAL_PRIVATE_EVIDENCE_CONTRACT)
        self._handoff_rule_route = None
        self._observation_budget = None

        def budget_key(cfg, scope):
            from .consolidation_prefix import _budget_binding
            binding = _budget_binding(cfg)
            if binding is None:
                return None
            try:
                branch = branches.get(branch_key(cfg, scope))
            except (OSError, TypeError, ValueError):
                return None   # An unverified maintenance estimate cannot alter a reply.
            return binding, getattr(cfg, 'context_window', None), branch['context'] if branch else None

        def observed_pressure():
            saved = self._observation_budget
            if saved is None:
                return 0
            epoch, count, scope, binding, estimated = saved
            if epoch is not session._tool_evidence_epoch or budget_key(config, scope) != binding:
                return 0
            return estimated + session.admitted_suffix_tokens(count, scope)

        def rule_route(cfg):
            return (getattr(cfg, 'kind', 'responses'), getattr(cfg, 'base_url', None),
                    getattr(cfg, 'model', None), getattr(cfg, 'tools', ()))

        def verified_rule():
            witnessed = self._handoff_rule_route
            if witnessed is not None and witnessed[0] == rule_route(config):
                return {'role': 'system', 'content': witnessed[1]}
            return None

        def request_tail() -> list[Any]:
            rows = (_current_channel_rows(persona_root, channel, channel_context, channel_scene)
                    if channel_at_tail and (callable(channel) or callable(channel_context)) else [])
            context_slots = [(('channel', i), i + 1) for i in range(len(rows))]
            rows.insert(0, evidence_row.copy())
            activity = getattr(self, 'activity_control', None)
            activity_at = None
            lease = self._batch_identity
            if activity is not None and lease is not None and self._active_batch is lease.snapshot:
                hint = activity.model_hint(lease.resolve(self._active_batch))
                if hint:
                    activity_at = len(rows)
                    rows.append({'role': 'system', 'content': hint})
            guard_start = len(rows)
            guard = getattr(self, 'reply_evidence_guard', None)
            if guard is not None:
                notice = guard.closing_notice()
                if notice: rows.append({'role':'system', 'content':notice})
                receipt_hint = guard.receipt_context()
                if receipt_hint: rows.append({'role':'system', 'content':receipt_hint})
            guard_end = len(rows)
            rows.extend(_pull_in_rows(tail_retrieval, round_session))
            if callable(tail_identity):
                hint = str(tail_identity() or "")
                if hint:
                    context_slots.append(('identity', len(rows)))
                    rows.append({"role": "system", "content": hint})
            facts_at = len(rows)
            rows = _with_the_facts(rows, facts)
            clock_facts_at = (facts_at if len(rows) > facts_at and
                              getattr(facts, 'clock_first', False) is True else None)
            navigation = getattr(self, 'memory_navigation_hint', None)
            if callable(navigation):
                hint = navigation()
                if hint:
                    context_slots.append(('navigation', len(rows)))
                    rows.append({'role':'system', 'content':hint})
            # All providers need the same preservation boundary during internal
            # continuation. Keep it at the request tail so stable history and
            # record-owned reasoning bindings remain unchanged.
            from .wake_view import internal_tick_only
            if internal_tick_only(tuple(self._active_batch or ())):
                from .prompts import MEMORY_SCOPE_CONTRACT
                rows.append({"role": "system", "content": MEMORY_SCOPE_CONTRACT})
            incoming = getattr(self, '_incoming_prompt_context', None)
            if incoming is not None and lease is not None and self._active_batch is lease.snapshot:
                from .batch_prompt import BatchTail, local_chat, official_deepseek_chat
                cfg = self._branch_config
                local = local_chat(cfg)
                # The owner's console keeps its normal full history. Only the
                # official Chat adapter can replay these host rows in place.
                if self._active_batch is getattr(self, '_owner_prompt_batch', None) and not official_deepseek_chat(cfg):
                    return rows
                if local or official_deepseek_chat(cfg):
                    model = getattr(cfg, 'model', None)
                    route = (cfg.base_url, model)
                    from .batch_prompt import verified_local_qwen
                    from .session_view import batch_scope
                    historical = (clock_facts_at is not None
                                  and (official_deepseek_chat(cfg) or verified_local_qwen(cfg.kind, cfg.base_url, model))
                                  and batch_scope(self._active_batch) == incoming.record.scope)
                    return BatchTail(rows, incoming, guard_start, guard_end, layout_route=route,
                                     observation_slots=(activity_at, facts_at, context_slots) if historical else None,
                                     observation_config=cfg if historical else None,
                                     clock_facts_at=clock_facts_at if local else None,
                                     clock_route=route if local and isinstance(model, str) and model else None,
                                     provider_replay=not local, pin_boundary=local,
                                     private_rule=local_rule if local else None)
            return rows

        self.session.the_tail = request_tail
        self._head_cell = a_head_cell
        self.bus = WakeBus(
            WakeBusOptions(), clock=_now_ms, schedule=_later, cancel=_cancel_later,
            # 那一句"装配那一层就是你"的落点：**装 `on_ready` 的是这里**，
            #    而递进去的 `ready` 与喂给 `feed.next_batch` 的是**同一个** `Event`。
            on_ready=self.ready.set)
        if handoff is None:
            # 那一刻的形状（`YYYYMMDD-HHMMSS`）—— 缺省是出厂那一个，递了就用递的：
            #   在调用之前先算出来（不然那一行会挤到 100 个字符以外 —— 本仓那条宽度契约）。
            stamp_of = _a_stamp if consolidation_stamp is None else consolidation_stamp
            # 2026-10-11：先把它存成一格把手，下一行才递进交接。理由是外面要能按节拍
            # 点火（一夜一次的后台整理）—— 从前它只是个局部量，门外够不到。
            consolidation_generate = generate
            if owns:
                background_image_resolver = self._request_image_resolver(root)
                closed_background_failures = []

                async def consolidation_generate(_config: Any, messages: Any,
                                                 _transport: Any) -> Any:
                    # Background work must not capture the unadapted transport
                    # above or mutate the main round's pinned profile. Pin at
                    # this request boundary and own a separate borrowed-free wire.
                    if self.consolidation is not consolidation_owner:
                        raise RuntimeError("consolidation_owner_changed")
                    request_config = (_config if _config is not None else
                                      config.pinned() if hasattr(config, "pinned") else config)
                    pin_request = getattr(request_config, "pinned", None)
                    if callable(pin_request):
                        request_config = pin_request()
                    request_values = getattr(request_config, "values", None)
                    if request_values is None:
                        request_values = lambda: {
                            "thinking": getattr(request_config, "thinking", None)}
                    background_wire = HttpxTransport(headers_of=key_headers, proxy_of=selector)
                    ledger = getattr(self, "cache_observations", None)
                    if ledger is not None:
                        from .cache_observation import WireCacheObserver
                        background_wire = WireCacheObserver(background_wire, ledger)
                        cycle_ledger = getattr(self, "cache_cycle_observations", None)
                        if cycle_ledger is not None:
                            background_wire = WireCacheObserver(background_wire, cycle_ledger,
                                local_prefix=self.cache_local_prefix,
                                labels_of=lambda: {"bot": str(root.resolve()), "epoch": self._the_conversation_epoch()})
                    prefix_plan = getattr(_config, 'consolidation_prefix', None)
                    if prefix_plan is not None:
                        background_wire = ReviewWire(background_wire, prefix_plan, request_config)
                    request_transport = adapt_protocol(
                        background_wire,
                        kind_of=lambda: getattr(request_config, "kind", "responses"),
                        values_of=request_values,
                        image_resolver=background_image_resolver,
                        request_of=getattr(request_config, "request_snapshot", None),
                        runtime_evidence=runtime_evidence)
                    primary = None
                    try:
                        request_generate = generate_itself if generate is None else generate
                        if ledger is not None:
                            from .cache_observation import observe_generate
                            request_generate = observe_generate(request_generate, lambda _: {
                                "bot": str(root.resolve()), "scope": "background:consolidation",
                                "epoch": "background", "auth": request_values().get("auth", "api_key")})
                        return await request_generate(request_config, messages, request_transport)
                    except BaseException as error:
                        primary = error
                        raise
                    finally:
                        # Keep this request until its one real close settles.
                        # Repeated observer cancellation must not cancel that close.
                        close = asyncio.create_task(request_transport.aclose())
                        consolidation_owner.own_background_task(close, role="close")
                        interrupted = None
                        while not close.done():
                            try:
                                await asyncio.shield(close)
                            except asyncio.CancelledError as error:
                                interrupted = interrupted or error
                            except BaseException:
                                break
                        try:
                            close.result()
                        except BaseException as error:
                            if primary is not None:
                                raise primary from error
                            if interrupted is not None:
                                raise interrupted from error
                            raise
                        if interrupted is not None:
                            raise primary if primary is not None else interrupted
                        if isinstance(primary, Exception) and callable(acknowledge):
                            closed_background_failures.append(primary)
                # Forward only the exact generator's local failure disposition.
                # Consolidation invokes it after recording its own failed attempt.
                acknowledge = getattr(generate, "acknowledge_failure", None)
                if callable(acknowledge):
                    def acknowledge_background_failure(error):
                        if not any(error is item for item in closed_background_failures):
                            return False
                        settled = acknowledge(error)
                        if settled:
                            closed_background_failures[:] = [
                                item for item in closed_background_failures if item is not error]
                        return settled
                    consolidation_generate.acknowledge_failure = acknowledge_background_failure
            consolidation_owner = self._a_consolidation(
                config=config, transport=transport, generate=consolidation_generate,
                write=consolidation_write, read=consolidation_read,
                stamp=stamp_of, root=root,
                pending_read=consolidation_pending_read,
                pending_settle=consolidation_pending_settle)
            self.consolidation = consolidation_owner
            handoff = self._a_handoff(session=session, root=root, head=head, prefix=prefix,
                                      persona_root=persona_root, config=config,
                                      channel=channel, channel_context=channel_context,
                                      channel_scene=channel_scene,
                                      consolidation=self.consolidation,
                                      scoped_context=linked_accounts is not None,
                                      fixed_rule=verified_rule, observed_pressure=observed_pressure)
        # **留一个把手**（本刀）—— 手动那一条路（`POST /handoff`）要够得着它：
        #    在那之前这一份交接装完就没人拿着（只有两个绑定方法进了 `build()`）⇒
        #    `request()` 那个公开入口在生产里**一个调用者都没有**。见 `ask_for_handoff`。
        self.handoff = handoff
        # **累计那一份落在 `<root>/usage.json`**（2026-10-05）：
        # ⚠️ `root` 是 `assemble` 的**形参** ⇒ 先存到这一格上，
        # `_keep_usage`（那一层在别处）才够得着 ；并把**盘上的**读回来
        # ⇒ 于是"缓存命中"成了一个**长期**读数（重启不丢 ）。
        self.usage_root = root
        # **连「那笔累计属于哪个会话」一起读回来** （2026-10-11 ）——
        # 起点对不上 ⇒ 那一笔**不是这个会话的** ⇒ 从头算 。
        self.usage_totals, self.usage_since = self._load_usage_totals()
        # **开机就先把口径对上**（2026-10-11 ）—— 光靠"下一轮再说"不够 ：
        # 一个**一句话都没发**的新会话会一直挂着**上一个会话**那个数
        # （ 作者 2026-10-11 报的正是这个 ）⇒ 这里就判一次 。
        # ⚠️ **这时 `self.session` 已经在了** （它在上面的 L1224 就设好了 ）——
        # 而 `usage_totals` 是**换过会话**的 ⇒ 归零 （ 那一笔不是这个会话的 ）。
        _born = self._the_conversation_epoch()
        if _born is not None and self.usage_since != _born:
            self.usage_totals = {"input": 0, "cached": 0}
            self.usage_since = _born
            self._save_usage_totals()
        self.timing_last = self._load_timing_last()
        from .batch_timing import load_batch_timing
        self.batch_timing_last = load_batch_timing(self.usage_root)
        # **规则①（设计稿 §七 第 1 条）**：**正在跑的那一轮保持原绑定** ——
        #    从**下一轮**起用新值。落点就在这一句上：`run_round` 是每一轮的入口，
        #    而"钉住"必须发生在**那一轮开始的那一刻**（不是装配那一刻，也不是每次读的时候）。
        #    ⚠️ 递进去的是**钉过的那个视图**（而不是那张活视图本身）⇒ 那一轮中途有人保存，
        #       它读到的照旧是它开始时的那一份。
        pinned = config.pinned() if hasattr(config, "pinned") else None
        self._branch_config = pinned if pinned is not None else config
        # Additive sidecar only. Caller-owned transports remain untouched.
        self.cache_local_prefix = None
        self.cache_observations = None
        self.cache_cycle_observations = None
        self.cache_observation_error = None
        self.cache_prefix_diagnostics_requested = cache_prefix_diagnostics is True
        self.cache_prefix_diagnostics_buffer = None
        self.cache_prefix_diagnostics_transport = None
        self.cache_prefix_diagnostics_error = None
        if owns:
            try:
                from .cache_observation import CacheLedger, WireCacheObserver
                self.cache_observations = CacheLedger(root / "cache-observations-v1.json")
                self.cache_cycle_observations = CacheLedger(root / "cache-cycle-observations-v1.json")
                if self.cache_prefix_diagnostics_requested:
                    try:
                        from .cache_prefix_probe import PrefixProbe
                        from .cache_prefix_buffer import DiagnosticBuffer
                        from .cache_prefix_transport import PrefixDiagnosticTransport
                        from .cache_observation import _identity
                        sink = DiagnosticBuffer()
                        diagnostic = PrefixDiagnosticTransport(
                            transport, PrefixProbe(), _identity.get, sink.append)
                        self.cache_prefix_diagnostics_buffer = sink
                        self.cache_prefix_diagnostics_transport = diagnostic
                        transport = diagnostic
                    except Exception:
                        self.cache_prefix_diagnostics_error = "setup_failed"
                from .local_prefix_usage import LocalPrefixUsage
                self.cache_local_prefix = LocalPrefixUsage(by_epoch=True)
                transport = WireCacheObserver(transport, self.cache_cycle_observations,
                    local_prefix=self.cache_local_prefix,
                    labels_of=lambda: {"bot": str(root.resolve()), "epoch": self._the_conversation_epoch()})
                transport = WireCacheObserver(transport, self.cache_observations)
            except Exception:
                self.cache_observation_error = "setup_failed"
        if owns:
            transport = CaptureWire(transport, self._consolidation_prefix)
            self._provider_state_root = Path(root) / "provider-state"
            holder = pinned if pinned is not None else config
            values_of = getattr(holder, "values", None)
            transport = adapt_protocol(
                transport,
                # Pinned refreshes at round boundaries. Read its current kind,
                # not the protocol string captured when Console was assembled.
                kind_of=lambda: getattr(holder, "kind", "responses"),
                values_of=(values_of if values_of is not None
                           else (lambda: {"thinking": getattr(config, "thinking", None)})),
                image_resolver=self._request_image_resolver(root),
                request_of=getattr(holder, "request_snapshot", None),
                runtime_evidence=runtime_evidence,
                replay_root=Path(root) / "provider-state",
                runtime_replay=True,
                local_native_compat=lambda: str(config.model).lower().endswith(".gguf"),
                replay_context_of=lambda: self._aprovider_replay_context(
                    round_session, linked_accounts is not None),
            )
            self.transport = transport

        from .media_budget import fit_images_to_context, estimate_request_tokens
        speaking_generate = self._spoke(generate_itself if generate is None else generate)
        if self.cache_observations is not None:
            from .cache_observation import observe_generate
            from .session_view import batch_scope

            def cache_labels(active_config: Any, scope_override: Any = None) -> dict[str, Any]:
                values = getattr(active_config, "values", None)
                values = values() if callable(values) else {}
                scope = (scope_override if scope_override is not None else
                         batch_scope(self._active_batch) if linked_accounts is not None
                         else "unscoped")
                branch = (branches.get(branch_key(active_config, scope))
                          if branches.entries else None)
                epoch = self._the_conversation_epoch()
                if branch is not None:
                    epoch = str(epoch) + ":" + branch["context"]
                return {"bot": str(root.resolve()), "scope": scope,
                        "epoch": epoch,
                        "auth": values.get("auth", "api_key")}

            def cache_console_view(snapshot: Any) -> dict[str, Any]:
                # Select the native console, never the latest external/autonomous bucket.
                pin = getattr(config, "pinned", None)
                current = pin() if callable(pin) else config
                all_paths = cache_all_view(snapshot, current=current)
                if linked_accounts is None:
                    return {"version": 1, "scope": "console", "status": "unavailable", "all_paths": all_paths}
                labels = cache_labels(current, scope_override="private:console:local-owner")
                identity = self.cache_observations.identity_for(
                    current.base_url, current.model, labels)
                bucket = next((b for b in snapshot["buckets"]
                               if b["identity"] == identity), None)
                return {"version": 1, "scope": "console", "unit": "http_attempt",
                        "status": "ready" if bucket is not None else "no_sample",
                        "model": identity["model"], "selected": bucket, "all_paths": all_paths}

            def cache_all_view(snapshot, *, current=None):
                from .cache_observation import aggregate_buckets
                if current is None:
                    pin = getattr(config, "pinned", None)
                    current = pin() if callable(pin) else config
                cycle = self.cache_cycle_observations
                identity = cycle.identity_for(
                    current.base_url, current.model, {"bot": str(root.resolve()), "epoch": self._the_conversation_epoch()})
                background = cycle.identity_for(current.base_url, current.model,
                    {"scope": "background:consolidation"})["scope"]
                result = aggregate_buckets(cycle.snapshot(), identity, background, current_epoch=True)
                result['period'] = 'conversation_epoch'
                result['coverage_note'] = '本次交接周期：含对话、后台整理及辅助调用；成功交接或上下文重置后重新计数，历史用量保留。升级前本周期未采集的用量不补算。'
                if cycle.persistence_error or cycle.observation_error:
                    result['status'] = 'observation_error' 
                if self.cache_local_prefix is not None:
                    result['local_prefix'] = self.cache_local_prefix.view(identity)
                return result

            self.cache_all_view = cache_all_view
            self.cache_console_view = cache_console_view
            speaking_generate = observe_generate(speaking_generate, cache_labels)

        pending_record_binding = {}
        self.reply_evidence_guard = None
        if reply_review:
            from .reply_evidence import ReplyEvidence
            self.reply_evidence_guard = ReplyEvidence(self, semantic_review=reply_review is True)


        def report_history_quote(missing: int) -> None:
            print(f"[provider-replay] 已将 {missing} 条旧助手记录作为原文引用，本轮可继续思考和调用工具。",
                  file=sys.stderr, flush=True)

        def report_replay_fallback(missing: int) -> None:
            print(f"[provider-replay] {missing} 条助手消息缺少原始推理，无法全部作为旧对话引用。此请求以非思考模式继续。",
                  file=sys.stderr, flush=True)
            self._replay_fallback_in_batch = True
            try:
                self._record_event({"type": "provider-replay-fallback", "ts": _now_iso(),
                    "source": "host", "origin": "internal", "text": "DeepSeek 本次请求使用非思考模式",
                    "meta": {"diagnostic": {"code": "deepseek_history_incompatible",
                                           "missing_assistant_turns": missing}}})
            except Exception as error:
                print("[provider-replay] 诊断保存失败：" + type(error).__name__,
                      file=sys.stderr, flush=True)

        wake_projection = None
        wake_batch = None

        from functools import wraps

        def consolidation_witness(*, review=False):
            if not owns or not isinstance(self.session, Session):return None
            rows=self.session.outbound([])
            if not review and round_session.outbound([])!=rows:return None
            from copy import deepcopy
            return dict(epoch=self._the_conversation_epoch(),rows=deepcopy(rows))
        self._consolidation_witness = consolidation_witness

        @wraps(speaking_generate)
        @self._consolidation_prefix.capture(consolidation_witness)
        async def generate_with_media_budget(active_config: Any, messages: Any,
                                             active_transport: Any, **kwargs: Any) -> Any:
            guard = self.reply_evidence_guard
            if guard is not None:
                active_config = guard.primary_config(active_config)
            admission = getattr(self, "wake_model_admission", None)
            if callable(admission):
                allowed, reason = admission(now_ms=_now_ms())
                if not allowed:
                    if reason == "wake_model_call_budget":
                        from .wake_policy import WakeModelBudgetExhausted
                        raise WakeModelBudgetExhausted
                    from .wake_policy import WakeModelAdmissionDenied
                    raise WakeModelAdmissionDenied(reason)
            from .wake_view import internal_tick_only, model_config_for_wake
            active_config = model_config_for_wake(active_config, self._active_batch)
            privacy = getattr(self, "qq_privacy_config", None)
            if privacy is not None:
                active_config = privacy(active_config)
            fitted = fit_images_to_context(messages, active_config)
            from urllib.parse import urlsplit
            if (internal_tick_only(tuple(self._active_batch or ()))
                    and urlsplit(active_config.base_url).hostname in (
                        "127.0.0.1", "localhost", "::1")):
                fitted = wake_projection(fitted)
            if getattr(active_config, "model", None) == "iq3_xxs":
                protocol_kind = getattr(active_transport, "_kind_of", None)
                if callable(protocol_kind):
                    from .receipt_projection import fit_local_receipt_layout
                    fitted = fit_local_receipt_layout(fitted, active_config, protocol_kind())
            pending_record_binding.clear()
            pending_record_binding['review_input'] = (active_config, fitted)
            from . import provider_record_replay as record_replay
            from .protocol import _chat_scope, deepseek_chat
            enabled = (owns and isinstance(self.session, Session)
                       and deepseek_chat(active_config.base_url, active_transport._kind_of())
                       and _chat_scope.get() is None)
            if not enabled:
                return await speaking_generate(active_config, fitted, active_transport,
                    _defer_speech=reply_review, **kwargs)
            if len(fitted) != len(messages):
                raise ValueError("provider media projection length changed")
            context = await self._aprovider_replay_context(round_session, linked_accounts is not None)
            frame = await record_replay.aprepare(
                self.session, round_session, messages, root=self._provider_state_root,
                context=context, base_url=active_config.base_url, model=active_config.model,
                history_boundary=self._replay_history_boundary)
            if await self._aprovider_replay_context(round_session, linked_accounts is not None) != context:
                raise ValueError("provider context changed during disk read")
            if frame.check_observation_authority is not None:
                frame.check_observation_authority()
            frame.request_hash = record_replay.digest(fitted)
            frame.on_incompatible = report_replay_fallback
            frame.bind_quotes(self._history_quotes, report_history_quote)
            if getattr(generate, "owns_record_replay_frame", False) is True:
                # Lifecycle-managed generate creates its own Task. The frame is
                # first activated there; do not inherit a parent-owned binding.
                result = await speaking_generate(
                    active_config, fitted, active_transport,
                    _record_replay_frame=frame, _defer_speech=True, **kwargs)
            else:
                with record_replay.use(frame):
                    result = await speaking_generate(
                        active_config, fitted, active_transport,
                        _defer_speech=True, **kwargs)
            pending_record_binding["reply"] = (result, frame)
            return result

        raw_generate = generate_with_media_budget

        @wraps(raw_generate)
        async def reviewed_generate(active_config, messages, active_transport, **kwargs):
            # Outside PrefixCache.capture and provider_record_replay.use: the
            # auxiliary wire cannot overwrite the main request/reasoning cache.
            owner = self.current_batch_identity() if reply_review else None
            plan = self._provider_context_plan(round_session, linked_accounts is not None) if reply_review and owns else None
            lease = self._batch_identity
            self._handoff_rule_route = None
            from .host_observations import ObservationRequest
            planning = None
            if isinstance(messages, ObservationRequest) and messages.estimated is not None:
                planning = budget_key(active_config, messages.incoming.record.scope)
            result = await raw_generate(active_config, messages, active_transport, **kwargs)
            admitted, projected = pending_record_binding['review_input']
            guard = self.reply_evidence_guard
            if guard is not None:
                guard.check_owner(owner)
                cfg, visible = pending_record_binding['review_input']
                changed = await guard.apply(result, cfg, active_transport, visible)
                guard.check_owner(owner)
                if owns and self._provider_context_plan(round_session, linked_accounts is not None) != plan:
                    raise ValueError('reply evidence visibility changed')
                binding = pending_record_binding.get('reply')
                if changed and binding is not None and binding[0] is result:
                    binding[1].completed = False
            rule_text = next((row['content'] for row in projected if row.get('role') == 'system'
                              and isinstance(row.get('content'), HostEvidenceText)), None)
            if (lease is self._batch_identity and getattr(result, 'status', None) in (None, 'completed')
                    and rule_text is not None):
                self._handoff_rule_route = (rule_route(admitted), rule_text)
                incoming = getattr(self, '_incoming_prompt_context', None)
                if lease is not None and incoming is not None:
                    observation = (messages if isinstance(messages, ObservationRequest)
                                   and messages.observation is not None else projected)
                    incoming.admit_clock(observation, admitted)
                    if (planning is not None and messages.incoming is incoming
                            and messages.epoch is self.session._tool_evidence_epoch):
                        scope = incoming.record.scope
                        if planning == budget_key(admitted, scope) == budget_key(config, scope):
                            self._observation_budget = (
                                messages.epoch, messages.boundary, scope, planning, messages.estimated)
            return result

        async def save_provider_reply(result: Any, rows: Any) -> None:
            binding = pending_record_binding.get("reply")
            if binding is None or binding[0] is not result:
                return
            frame = binding[1]
            scoped_record = getattr(round_session, "scoped_record", None)
            staged = tuple(map(scoped_record, rows)) if callable(scoped_record) else tuple(rows)
            context_plan = self._provider_context_plan(round_session, linked_accounts is not None)
            await frame.commit_async(self.session, result, staged=staged)
            current_plan = self._provider_context_plan(round_session, linked_accounts is not None)
            if (self.session.records() != frame.before
                    or current_plan != context_plan
                    or (callable(scoped_record) and tuple(map(scoped_record, rows)) != staged)):
                raise ValueError("provider reply context changed before publication")

        async def publish_reply(result: Any) -> None:
            binding = pending_record_binding.pop("reply", None)
            if reply_review or (binding is not None and binding[0] is result):
                self._say_result(result)
            await self._tap_reply(result)

        # Common registration covers every officially assembled Bot and channel.
        # The shared memory writer consumes one-use host capabilities; no writer
        # or model arguments can enable this through a Bot-specific flag.
        settled_receipts = getattr(run_tools, "settled_receipts", None)
        profile_writer = (memory_handlers or {}).get('profile_add')
        if (run_tools is not None and profile_writer is not None
                and hasattr(profile_writer, '_portrait_write_binding')):
            from .portrait_eligibility.runtime import ProfileWriteGuard
            self.portrait_write_guard = ProfileWriteGuard(
                self, profile_writer,
                config_of=lambda: pinned if pinned is not None else config,
                transport=transport)
            if self.portrait_write_guard.enabled:
                inside = getattr(run_tools, 'with_inner_guard', None)
                run_tools = (inside(self.portrait_write_guard.wrap) if callable(inside)
                             else self.portrait_write_guard.wrap(run_tools))

        if self.reply_evidence_guard is not None and run_tools is not None:
            run_tools = self.reply_evidence_guard.wrap_tools(run_tools)
        from .wake_view import internal_tick_only
        one_round = make_run_round(
            session=round_session,
            # **把它递给那一层**（2026-10-05 作者："往下走" ⇒ "做"）——
            # 调用点在 `round.py:214`（`handed = await run_tools(calls)` ）早就在了，
            # 而**十一个 `make_run_round(...)` 调用点一个都没递** ⇒ 这就是缺的那一跳 。
            # ⚠️ **不递 ⇒ 那一层逐字不变**（`round.py:213` 那个 `is not None` ）。
            run_tools=run_tools,
            settled_receipts=settled_receipts,
            host_interrupted=lambda: bool(getattr(self,'round_control',None)
                and self.round_control.status()['interrupting']),
            tell=self.a_tool_row,
            generate=reviewed_generate,
            config=(pinned if pinned is not None else config), transport=transport,
            head=a_head_cell,
            # ⚠️ 这一条**故意读活视图**（不是钉住的那一份）：设计稿 §七 第 2 条要求
            #    `context_window` **立即进硬阈**（换个窗口小的模型 ⇒ 下一轮可能触发一次交接）。
            # **上下文上限没有 ⇒ 硬阈也「没有」**（2026-10-05 作者裁定）——
            # ⚠️ 交接层对 `None` 的语义是「**不据此限制输入**」（`handoff.py:353`）；
            # 而 `context_window - max_tokens` 在 `None` 时会当场抛 ⇒ 这一格先判 。
            hard_tokens=lambda: hard_input_tokens(
                config.context_window, config.max_tokens, config.base_url),
            # Prospective budget includes declarations even when media fitting
            # retains no images. Keep the existing live hard-window policy.
            estimate=lambda messages: self._context_pressure(messages, config),
            record_event=self._record_event,
            now_ms=_now_ms, now_iso=_now_iso,
            signal=self.signal,
            # **每轮把终态那份用量存下来**（2026-10-05）—— 页面那一行"缓存命中"
            # 读的就是这一格 （`/usage` 那一头现读 ）。
            on_usage=self._keep_usage,
            on_timing=self._keep_timing,
            on_reply=publish_reply, before_reply=save_provider_reply,
            allow_empty_internal=lambda: internal_tick_only(tuple(self._active_batch or ())))
        async def prepare_declared_read() -> None:
            nonlocal wake_projection, wake_batch
            from .wake_view import WakeProjection
            batch = tuple(self._active_batch or ())
            if wake_projection is None or batch != wake_batch:
                wake_batch = batch
                wake_projection = WakeProjection([
                    {"origin": row.origin, "type": row.type, "source": row.source,
                     "cursor": row.cursor, "text": row.text} for row in batch
                ] if internal_tick_only(batch) else ())
            prepare = getattr(self, "read_prepare", None)
            if callable(prepare):
                await prepare(round_session, tuple(self._active_batch or ()),
                              self.signal)
            appraisal = getattr(self, "affect_prepare", None)
            if callable(appraisal):
                try:
                    await appraisal(pinned if pinned is not None else config,
                                    transport, self.signal, round_session)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    # Extra appraisal failure must not erase the normal reply path.
                    print("[affect-assessment] prepare failed: " + type(error).__name__,
                          file=sys.stderr, flush=True)

        if pinned is None:
            async def run_round(round_no: Any) -> Any:
                await prepare_declared_read()
                branch_inputs()
                return await one_round(round_no)
        else:
            async def run_round(round_no: Any) -> Any:
                """ 每一轮开头钉一次（规则①）—— 之后这一轮读到的都是那一刻的生效值。"""
                prepare_upstream = getattr(config, "prepare_round", None)
                if callable(prepare_upstream):
                    from .strata_follow import StrataFollowError
                    from .engine import EngineConflict
                    try:
                        await prepare_upstream()
                    except (StrataFollowError, EngineConflict, TimeoutError) as error:
                        from .loop import RoundOutcome
                        print("[strata-follow] refresh failed: " + type(error).__name__,
                              file=sys.stderr, flush=True)
                        self._record_event({"type": "strata-follow-failed", "ts": _now_iso(),
                            "source": "host", "origin": "internal",
                            "text": "上游能力同步失败，本轮未请求模型；不使用过期窗口或视觉能力。",
                            "meta": {"error_class": type(error).__name__,
                                     "message": "upstream refresh failed"}})
                        return RoundOutcome(failed=True)
                pinned.refresh()
                await prepare_declared_read()
                branch_inputs()
                return await one_round(round_no)
        if owns:
            provider_round = run_round
            async def run_with_provider_checkpoint(round_no: Any) -> Any:
                try:
                    result = await provider_round(round_no)
                except BaseException:
                    try:
                        await self._aprovider_replay_context(round_session, linked_accounts is not None)
                    except Exception as error:
                        print("[provider-context] checkpoint failed: " + type(error).__name__,
                              file=sys.stderr, flush=True)
                    raise
                await self._aprovider_replay_context(round_session, linked_accounts is not None)
                return result
            run_round = run_with_provider_checkpoint
        if wrap_round is not None:
            from types import MappingProxyType
            from .session_view import batch_scope

            context = MappingProxyType({
                "session": round_session, "run_tools": run_tools,
                "scope_of": lambda: batch_scope(self._active_batch),
                "record_event": self._record_event, "tell": self.a_tool_row,
                "owner_visible": self._owner_round_visible,
            })
            wrapped = wrap_round(run_round, context)
            if not callable(wrapped):
                close = getattr(wrapped, "close", None)
                if callable(close):
                    close()
                raise TypeError("wrap_round must return a callable")
            run_round = wrapped

        self.transport, self.owns_transport = transport, owns
        # **刀 3：把"这一条档案说的是哪一套话"那一层装上**（设计稿 §六）——
        #    它是一个**装饰器**（`protocol.ChatTransport`）：`chat` 那一边把请求体塑形、
        #    把回来的字节翻译成 Responses 事件；`responses` 那一边**原样直通**。
        #    ⚠️ **只包我们自己造的那一条**（`owns`）：递进来的那条由调用方说了算 ——
        #       判据里那些替身本来就照 Responses 那一套回话，包一层会把它们的回话翻译走样。
        #    ⚠️ `kind` 与那几格读的是**钉过的那一份**（与这一轮的 `config` 同一个来源）⇒
        #       一轮中途换了档案，这一轮的话不跟着变（规则①）。

        from .round_control import RoundControl
        self.round_control = RoundControl(self._record_event)
        from .activity_control import ActivityControl
        def activity_paused():
            gate = getattr(getattr(self.app, 'loop', None), 'gated', None)
            return bool(callable(gate) and gate())
        self.activity_control = ActivityControl(
            bus=self.bus, control=self.round_control, paused=activity_paused,
            processing=lambda: bool(getattr(getattr(self.app, 'loop', None), 'processing', False)),
            stopping=lambda: bool(self.signal.is_set()
                or getattr(self, '_operator_shutdown_requested', False)))
        bind_activity = getattr(getattr(self, 'consolidation', None), 'bind_activity_control', None)
        if callable(bind_activity):
            bind_activity(self.activity_control)
        uncontrolled_round = run_round
        async def controlled_round(number):
            result = await self.round_control.run(uncontrolled_round, number)
            if self.round_control.last_yielded or self.round_control.last_interrupted:
                checkpoint = getattr(self, 'activity_checkpoint', None)
                if callable(checkpoint):
                    state = 'paused' if self.round_control.last_yielded else 'waiting'
                    try:
                        saved = checkpoint(state)
                    except Exception as error:
                        self._continuation_diagnostic('activity', 'checkpoint_unavailable:' + type(error).__name__)
                        return result  # No automatic retry of an unrecorded transfer.
                    if state == 'paused':
                        from .continuation import capture
                        originals = tuple(self.original_batch())
                        valid = getattr(self, 'activity_resume_valid', None)
                        if callable(valid):
                            self._queue_continuation(
                                '〔暂歇后的复核机会〕检查点 ' + saved['id']
                                + '。先 attention_read 查看并核对原文与工具回执，再决定继续、调整、等待或取消。'
                                '这不是新委托，不授权重复执行；不需要回应时可安静结束。',
                                'activity', capture(self, _now_ms()),
                                still_valid=lambda: valid(saved, originals, pending=False),
                                pending_valid=lambda: valid(saved, originals))
            return result
        run_round = controlled_round

        wake_batch_result = None
        retire_after_flush = False
        async def retiring_batch_end():
            nonlocal retire_after_flush
            try:
                result = await handoff.batch_end_check()
            except BaseException:
                self._history_quotes.settle(False)
                self._revoke_batch_identity()
                raise
            # Loop flushes once more after batch_end_check; preserve authority
            # for that already-admitted final handoff, then retire it.
            retire_after_flush = True
            return result

        async def guarded_handoff_flush():
            nonlocal retire_after_flush, wake_batch_result
            finished = False
            try:
                result = await handoff.flush_handoff()
                if retire_after_flush and wake_batch_result is not None:
                    event, state = wake_batch_result
                    wake_batch_result = None
                    originals = self.original_batch()
                    if len(originals) == 1 and originals[0] is event:
                        try:
                            self.wake_policy.finish_processing(originals, now_ms=_now_ms(), state=state)
                        except (OSError, ValueError, TypeError) as error:
                            print("[wake] 唤醒结果未保存 · " + type(error).__name__, flush=True)
                if retire_after_flush and getattr(self, "_display_batch_marker", None) is not None:
                    if self._owner_round_visible():
                        started = getattr(self, "_display_batch_started", None)
                        elapsed = round(max(0.0, time.monotonic() - started), 1) if started is not None else None
                        from .batch_timing import finish_batch_timing
                        self.batch_timing_last = finish_batch_timing(
                            getattr(self, "usage_root", None), elapsed,
                            getattr(self, "_display_batch_timings", []))
                        duration = f"{elapsed:.1f}" if elapsed is not None else None
                        text = f"用时 {duration} 秒" if duration is not None else "本轮已结束"
                        if self.round_control.last_failed:
                            text = f"处理失败 · {duration} 秒 · 可导出诊断" if duration is not None else "处理失败 · 可导出诊断"
                        if self.round_control.last_failed and getattr(self, "_subscription_limit_batch", None) == self.batches:
                            from .transport import SUBSCRIPTION_LIMIT_HINT
                            text = SUBSCRIPTION_LIMIT_HINT
                        if self.round_control.last_interrupted:
                            text = f"已停止 · 用时 {duration} 秒" if duration is not None else "已停止"
                        elif self.round_control.last_yielded:
                            text = f"已暂歇 · 用时 {duration} 秒" if duration is not None else "已暂歇"
                        if self._replay_fallback_in_batch:
                            text += " · DeepSeek 非思考兼容"
                        frame = self._display_marker("ui_batch_end", self.batches, text)
                        if frame is not None and self.write is not None:
                            self.write(frame)
                    self._display_batch_marker = None
                finished = True
                return result
            except BaseException:
                self._history_quotes.settle(False)
                self._revoke_batch_identity()
                raise
            finally:
                if retire_after_flush:
                    # The last handoff has settled. Keep the excerpt built by
                    # the finished batch only if its original history survives.
                    boundary = self._replay_history_boundary
                    if self._history_quotes.pending or self._history_quotes.ready:
                        self._history_quotes.settle(
                            bool(finished and self.session.retains_history_boundary(boundary)
                                 and not self.round_control.last_failed
                                 and not self.round_control.last_interrupted
                                 and not self.round_control.last_yielded))
                    self._remember_completed_request_preview()
                    self._revoke_batch_identity()
                    retire_after_flush = False

        inner_round = run_round
        from .identity import IdentitySnapshotChanged
        async def guarded_round(number):
            nonlocal wake_batch_result
            wake_batch_result = None
            try:
                guard = getattr(self, 'reply_evidence_guard', None)
                if guard is not None:
                    guard.bind()
                    guard.rounds_remaining = self.app.loop.remaining_rounds(number)
                if (self._batch_identity is not None
                        and _context_only_batch(self.original_batch())):
                    from .loop import RoundOutcome
                    return RoundOutcome(ended=True)
                self.flush_reminder_notices()
                if runtime_evidence is not None:
                    parents = runtime_evidence.parents_of(self.original_batch())
                    async with runtime_evidence.async_scope('round', parents=parents):
                        result = await inner_round(number)
                else:
                    result = await inner_round(number)
                if hasattr(getattr(self, "wake_policy", None), "finish_processing"):
                    originals = self.original_batch()
                    if len(originals) == 1 and originals[0].origin == "internal":
                        state = ("interrupted" if self.round_control.last_interrupted or self.round_control.last_yielded
                                 else "failed" if result.failed else "processed")
                        wake_batch_result = (originals[0], state)
                self._reminder_delivery_note('model_round_finished', round=number,
                    failed=bool(getattr(result, 'failed', False)),
                    ended=bool(getattr(result, 'ended', False)),
                    console_speech_accepted=bool(self.answered))
                return result
            except IdentitySnapshotChanged:
                from .loop import RoundOutcome
                self._revoke_batch_identity()
                self._record_event({"type": "identity-snapshot-changed", "ts": _now_iso(),
                    "source": "host", "origin": "internal",
                    "text": "本轮身份绑定发生变化，已结束当前轮。下一条消息会重新读取身份绑定。"})
                return RoundOutcome(failed=True)
            except BaseException as error:
                self._reminder_delivery_note('model_round_interrupted', error=type(error).__name__)
                self._revoke_batch_identity()
                raise

        self.app = build(processing_owner=handoff,
            runtime_evidence=runtime_evidence,
            # 记忆层那几个执行段（2026-10-05）：照原样转交。
            memory_handlers=memory_handlers,
            take_batch=self._take_a_batch, run_round=guarded_round,
            batch_finished=self._finish_ingress_batch,
            flush_handoff=guarded_handoff_flush, batch_end_check=retiring_batch_end,
            console=self, signal=self.signal,
            store_root=root / "events", pacing_root=root / "timers", clock=_now_iso,
            event_store=event_store,
            # ★ **2026-10-12（作者：「一起解开」）**：那只闹钟表**由入口递** ——
            #   递了就用它（全树只留一只）；不递 ⇒ 从 `pacing_root` 现造一只（照旧）。
            timers=timers,
            # **2026-10-12（作者拍的那句提醒）**：跑到软阈那一轮附一句话 ——
            #   它**只说个数**（`handoff.SOFT_ROUNDS_NOTE`；⚠️ 2026-10-17 名实对齐过：
            #   那个数数的是**这一趟里的模型往返**，不是"整场对话第几个来回"），
            #   **不说该怎么办**（作者原话："不能说让它收一收……那它不就被泼了冷水"）。
            #   · `inject_notice` 是 handoff 的**公开口**（"通道由它给"）；
            #   · 那句话与那个数**从同一处来**（`handoff.SOFT_ROUNDS` ／ `SOFT_ROUNDS_NOTE`），
            #     而那个数**同一趟**也递给主循环（`soft_rounds=`）⇒ 两边不会漂
            #     （以后谁改了那个数，那句话跟着改，不会变成假话）。
            #   ⚠️ **两样要一起递**（`app.build` 那一格的守卫就认这个）：只递一样 ⇒
            #      它会当场出一声 `UserWarning` 然后留在接缝上。
            #   ⚠️ 那三样**齐了才递**：手边这一份交接要是没有那扇门（判据里的替身、
            #      或者别的实现）⇒ 一个字都不递，行为逐字与从前一样（照旧那条接缝）。
            #   ⚠️ **2026-10-17：这一格试过换成"骑尾巴"的投递，量完回退了** ——
            #      理由逐字写在 `self.session.the_tail` 那一段上（那一改拆掉了
            #      "批末另起一批给模型收尾"那条补救）。这里照旧递 `handoff.inject_notice`。
            **({
                "inject_notice": handoff.inject_notice,
                "hint_text": handoff.on_soft_limit_note(),
                "soft_rounds": handoff.soft_rounds,
            } if _has_the_soft_limit_door(handoff) else {}),
            **({} if grace_ms is None else {"grace_ms": grace_ms}))
        self.app.loop.wait_paused = self.activity_control.wait_paused
        return self.app

    async def queue_consolidation_recovery(self, task):
        worker = getattr(self, "consolidation", None)
        return bool(worker is not None and worker.schedule_recover(task))

    def consolidation_jobs(self):
        worker = getattr(self, "consolidation", None)
        return worker.chunk_status() if worker is not None else []

    def consolidate_once(self) -> bool:
        """按需排一次整理态 —— 入口按节拍点火用（一夜一次的后台活）。

        与交接那条路的差别只有一处：那里是交接的副产品，这里由外面看着钟点火。
        两处**同一条实现**（都走 `Consolidation.schedule`），所以"要不要做、
        能不能做"的判据仍然只有那一份（only-system 不跑、没接 read 不跑）。

        ⚠️ 交回的就是 `schedule` 那个回值：`False` ＝ 这一趟没排上（空跑保护
        挡下来、或者根本没装），不是"跑失败了"。

        ⚠️ 没装（终端那半、或者 `read` 没接）⇒ 回 `False`，一个字都不做 ——
        与 `ask_for_handoff` 那条"没接就照实说"同一个口径。
        """
        the_dream = getattr(self, "consolidation", None)
        if the_dream is None:
            return False
        try:
            from .continuation import current_job, capture
            token = current_job.set(capture(self, _now_ms(), scope=''))
            try:
                return bool(the_dream.schedule(self.session.outbound(self._head_cell())))
            finally:
                current_job.reset(token)
        except Exception:                              # noqa: BLE001
            return False

    async def ask_for_handoff(self) -> dict[str, Any]:
        """ **请它交接一次** —— 手动那一条路（`POST /handoff` 落到这里）。

        ⚠️⚠️ **它是 `Handoff.request()` 在生产里的第一个调用者** —— `handoff.py` 抬头
           那句"**今天没有调用者**"从本刀起不再成立（那一句的更正写在那个文件上，同一刀）。
            而它**不是**"执行一次交接"：`request()` 的语义是**请求**（`05:123-136`）——
           批内只登记（批末兑现），空闲才当场做。
        ⚠️ **交回四个记号，而不是一个布尔值**（照实说清"那一刻是哪一种情形"）：
           · `wired` —— 有没有那一份交接（`assemble()` 跑过没有）；`False` ⇒ 上层回 503；
           · `asked` —— `request()` 自己那个回值（`False` ＝ 单飞挡住：已经有一次在飞、
             或者已经登记过一张票）；
           · `requested` ／ `processing` —— 那一刻**公开只读的**两个记号 ⇒
             "登记了、批末兑现"与"当场做了"这两件事在页面上分得开。
        ⚠️ **它不 await 那一次交接本身**：空闲时 `request()` 会 `await self._force()`
           （六步跑完才回来），而那是**交接**在做的事 —— 本方法只把"请"递过去、
           把那一刻的记号读回来。
        ⚠️ **别把它读成"一个入梦按钮"**：整理态是**交接的副产品**（`_a_handoff` 的
           `snapshot` 那一格排的），所以按这一下 ＝ **交接 ＋ 起整理态**两件一起 ——
            页上那个词要照实写（参考工程那个面板写的是"强制交接并入梦"，正是这个意思）。
        """
        if self.handoff is None:
            return {"wired": False, "asked": False, "requested": False, "processing": False}
        from .continuation import current_job, capture
        from .owner_tick import OWNER_SCOPE
        manual_job = capture(self, _now_ms(), scope=OWNER_SCOPE)
        previous_manual = getattr(self, '_manual_handoff_job', None)
        self._manual_handoff_job = manual_job
        asked = False
        token = current_job.set(manual_job)
        try:
            asked = await self.handoff.request()
        finally:
            current_job.reset(token)
            if getattr(self, '_manual_handoff_job', None) is manual_job:
                if not asked:
                    self._manual_handoff_job = previous_manual
                elif not self.handoff.requested:
                    self._manual_handoff_job = None
                    quotes = getattr(self, '_history_quotes', None)
                    if quotes is not None and (quotes.pending or quotes.ready):
                        if not self.session.retains_history_boundary(self._replay_history_boundary):
                            quotes.settle(False)
        return {"wired": True, "asked": bool(asked),
                "requested": bool(self.handoff.requested),
                "processing": bool(self.handoff.processing)}

    async def run(self) -> None:
        """ **起** —— 主循环 ＋ 那个驱动跑到收工为止； **回来之前一定停**。

        ⚠️ **它 `await` 的是主循环那一条**（`App.run()` 里那句 `await self._task`）：
           本协程返回 ⇒ 那一批批都干完了 —— 而**收工那条路是"输入到头"**：
           读法回了个空 ⇒ 泵交回 `None`（Task 2 那一刀拍的）⇒ `loop.run()` 从循环里出来。
        ⚠️⚠️ **回来之后必须 `stop()`（`finally`）**：`App.run()` 返回 **≠ 关机** ——
           那个驱动还在转（`app.py` 那句"停它的是 `stop()`"）⇒ 少了这一步，
           一个"打一行就退"的进程会**留下一个还在滴答的闹钟表**。
        ⚠️ **取消那条路也走 `finally`**（Ctrl-C ／ 外面的取消）：`stop()` 的三条保证照旧，
           尤其**不许吞掉调用方自己的取消** —— 本方法**没有 `except`**，那一位照旧往上走。

        ⚠️⚠️ **那个泵占住事件循环，而这是**拍过的**一处代价**（Task 2 那条边界点名要我拍）：
           · 默认那个读法是**同步**的（`_stdin_line` 走 `sys.stdin.readline()`）⇒
             **没在打字的时候，整个循环停在那一次读上**：那个驱动不滴答、
             闹钟到点也不会响、`stop()` 也送不进去（取消要等到下一个 await 点）。
           · 本刀**选的是"接受它"**（不就是"换成线程里读"），两条理由都要读：
             ① **换法的代价更大**：`asyncio.to_thread(sys.stdin.readline)` 会让
                `asyncio.run` 退出时去 join 那个**堵在 `readline` 上**的线程
                （`loop.shutdown_default_executor()`）⇒ 一个"按了 Ctrl-C 却不退出"的进程，
                而那比"打字时才动"坏得多；
             ② **它是可换的**：读法是注入面（`assemble(read=…)` ／ `pump(read=…)`）——
                真入口要把它换成一个消息队列（每行一条）或者一个非阻塞的源，
                那时这一条**整段作废**（谁换谁删这一句）。
           · ⚠️ 而**你能把它停下来**：输入那头打一个 EOF（Windows 上是 `Ctrl-Z` 再回车）
             ⇒ 读法交出空串 ⇒ 上面那条干净的路。Ctrl-C 也能停，只是它要**等你按一次回车**
             （信号处理器要把那次取消排进循环，而循环正堵在 `readline` 里）。
        """
        if self.app is None:
            raise RuntimeError(
                "这一套还没装（`assemble()` 没跑过）—— 那个入口要先装再起："
                "`console = Console(); console.assemble(root=…); await console.run()`")
        notice_task = None
        if getattr(self, 'reminder_notices', None) is not None:
            from .reminder_notices import watch
            notice_task = asyncio.create_task(watch(self), name='local-due-notices')
        try:
            await self.app.run()
        except BatchIdentityStopped as error:
            if error is not getattr(self, '_shutdown_identity_rejection', None):
                raise
            # The exact rejection is retained, and the inner TEV scope recorded
            # CANCELLED. This is a normal operator exit, not successful work.
        finally:
            if notice_task is not None:
                notice_task.cancel()
                await asyncio.gather(notice_task, return_exceptions=True)
            await self.stop()

    def bind_ingress_shutdown(self, owner):
        from .ingress_shutdown import IngressShutdown
        if type(owner) is not IngressShutdown:
            raise TypeError('exact ingress shutdown owner required')
        if self._ingress_shutdown is not None and self._ingress_shutdown is not owner:
            raise RuntimeError('ingress shutdown owner changed')
        self._ingress_shutdown = owner

    def request_shutdown(self) -> None:
        """Loop-thread admission cutoff; accepted work remains owned, not cancelled.

        This is phase one only. run()/stop() still own actual retirement.
        """
        if getattr(self, '_operator_shutdown_requested', False):
            return
        self._operator_shutdown_requested = True
        # One fixed sub-deadline, within the existing 30-second launcher budget.
        # Repeated stop observations must never renew this waiting allowance.
        if getattr(self, '_ingress_stop_deadline', None) is None:
            try:
                shutdown_loop = asyncio.get_running_loop()
            except RuntimeError:
                # Synchronous admission-only callers still perform every cutoff.
                # Actual async stop establishes its one deadline when entered.
                pass
            else:
                self._ingress_stop_deadline = shutdown_loop.time() + 5.0
        errors = []
        self._operator_retirement = None
        if self.bus is not None:
            try:
                self.bus.begin_shutdown()
            except BaseException as error:
                errors.append(error)
        owner = getattr(self.app, 'loop', None)
        lease = self._batch_identity
        if lease is not None:
            try:
                self.current_batch_identity()
                originals = lease.resolve(self._active_batch)
                self._operator_retirement = (owner, lease, owner.generation,
                    tuple(self._active_batch), originals, lease._sealed)
            except Exception as error:
                # A pre-existing identity fault is never relabelled as stop.
                errors.append(error)
        # Stop future rounds before calling extension-owned shutdown hooks.
        if self.app is not None:
            self.app.loop.stop()
            self.app.pacing_stop.set()
        for owner in (self._ingress_shutdown, getattr(self, 'consolidation', None)):
            if owner is not None:
                try:
                    if owner is self._ingress_shutdown:
                        owner.begin()
                    else:
                        owner.begin_shutdown()
                except BaseException as error:
                    errors.append(error)
        if errors:
            errors[0]._shutdown_admission_errors = tuple(errors)
            raise errors[0]

    async def stop(self) -> None:
        """ **停** —— 叫停那一套（`App.stop()`），再把**本入口造的那个** client 关掉。

        ⚠️⚠️ **三条保证照旧**（第 ③ 刀立的，一条都不许动 —— 这一层只是转交）：
           ① **不许吞掉调用方自己的取消** —— 本方法**没有** `except CancelledError`
              （下面 `finally` 里那一下是 `finally`，不是 `except`：取消照旧往上走）；
           ② **有上界** —— 那是 `App.stop()` 的 `grace_ms`（本层**不另造一个**上界，
              也不替它吞掉那条 task 的下场）；
           ③ **幂等** —— 关两次不许炸（`App.stop()` 幂等 ＋ `aclose()` 幂等）。
        ⚠️ **谁关这个 client**：照 `cli.py:28-31` 那条"谁的谁关" —— **本入口造的那个**
           在这里关掉；**递进来的那个归调用方**（替对方关掉，等于替它做了一个它没要求的
           决定：它可能还要复用那个 client）。
        """
        self._revoke_batch_identity()
        import sys
        primary = sys.exception()
        errors = []
        if self.bus is not None:
            try:
                self.bus.begin_shutdown()
            except BaseException as error:
                errors.append(error)
        ingress = self._ingress_shutdown
        if ingress is not None:
            if getattr(self, '_ingress_stop_deadline', None) is None:
                self._ingress_stop_deadline = asyncio.get_running_loop().time() + 5.0
            try:
                # Signal first; completion is checked after the owned observers settle.
                ingress.begin()
                ingress.observers()
            except BaseException as error:
                errors.append(error)
        worker = getattr(self, "consolidation", None)
        if worker is not None:
            worker.begin_shutdown()
        try:
            if self.app is not None:
                await self.app.stop()
        except BaseException as error:
            errors.append(error)
        if ingress is not None:
            try:
                await ingress.wait(deadline=self._ingress_stop_deadline)
            except BaseException as error:
                # Diagnostic state contains counts only, no event or exception bodies.
                self.ingress_shutdown_report = ingress.state()
                error.add_note('ingress shutdown state: ' + repr(self.ingress_shutdown_report))
                errors.append(error)
        try:
            if self.owns_transport and self.transport is not None:
                await self.transport.aclose()
        except BaseException as error:
            errors.append(error)
        if errors:
            chosen = primary if primary is not None else errors[0]
            chosen._console_shutdown_errors = tuple(errors)
            chosen.add_note('Console cleanup unresolved; ingress ownership retained')
            if primary is None:
                raise chosen

    def _pin_shutdown_owner(self) -> Any:
        loop = asyncio.get_running_loop()
        owner = (self.app, self.transport, bool(self.owns_transport), loop,
                 getattr(self, "consolidation", None), self._ingress_shutdown)
        if self._drain_owner is None:
            self._drain_owner = owner
            self._drain_bus = self.bus
        elif not (owner[0] is self._drain_owner[0]
                  and owner[1] is self._drain_owner[1]
                  and owner[2:4] == self._drain_owner[2:4]
                  and owner[4] is self._drain_owner[4]
                  and owner[5] is self._drain_owner[5]
                  and self.bus is self._drain_bus):
            raise RuntimeError('shutdown_owner_changed')
        return owner

    @staticmethod
    def _take_shutdown_result(task: Any) -> None:
        if task.done() and not task.cancelled():
            task.exception()

    def _own_shutdown_task(self, name: str, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._drain_tasks[name] = task
        task.add_done_callback(self._take_shutdown_result)

    @staticmethod
    async def _invoke_shutdown(owner: Any, method: str) -> None:
        # Attribute lookup, synchronous factories, and awaits all fail in an owned task.
        result = await getattr(owner, method)()
        if result is False:
            raise RuntimeError("shutdown close returned False")

    async def _request_shutdown_stop(self, app: Any) -> None:
        outcome = 'returned'
        try:
            await app.stop()
        except BaseException:
            outcome = 'raised'
            raise
        finally:
            self._drain_stop_return = {'outcome': outcome, 'state': app.shutdown_state()}

    def _shutdown_observation(self) -> dict[str, Any]:
        from copy import deepcopy
        from .app import _shutdown_task_state
        app, transport, owned, loop, worker, ingress = self._drain_owner
        same = (self.app is app and self.transport is transport
                and bool(self.owns_transport) == owned
                and getattr(self, "consolidation", None) is worker
                and self._ingress_shutdown is ingress
                and self.bus is self._drain_bus)
        ingress_state = ingress.state() if ingress is not None else {
            'accepting': False, 'pending': 0, 'buffered': 0, 'failed': 0, 'drained': True}
        state = app.shutdown_state() if app is not None else {
            'stop_requested': True, 'quiescent': True,
            'cleanup_ok': True, 'work_ok': True}
        background = worker.shutdown_state() if worker is not None else {
            'quiescent': True, 'cleanup_ok': True, 'work_ok': True, 'tasks': ()}
        bus_state = self._drain_bus.shutdown_state() if self._drain_bus is not None else {
            'accepting': False, 'timer_pending': False, 'buffered': 0,
            'failed': 0, 'cleanup_ok': True}
        tasks = {name: _shutdown_task_state(task)
                 for name, task in self._drain_tasks.items()}
        close = tasks.get('close', {'state': 'absent'})
        if not owned or transport is None:
            transport_state = 'borrowed' if transport is not None else 'absent'
            close_ok = True
        else:
            transport_state = close['state']
            close_ok = transport_state == 'done'
        stop = tasks.get('stop', {'state': 'absent'})
        quiescent = not bus_state['timer_pending'] and state['quiescent'] and background['quiescent'] and ingress_state['drained'] and not ingress_state.get('receipt_pending', False) and all(
            task['state'] != 'pending' for task in tasks.values())
        cleanup_ok = (same and bus_state['cleanup_ok'] and state['cleanup_ok'] and background['cleanup_ok'] and close_ok
                      and not ingress_state['accepting'] and ingress_state['failed'] == 0
                      and ingress_state['pending'] == 0 and ingress_state['buffered'] == 0
                      and ingress_state['drained'] is True
                      and not ingress_state.get('release_failed', False)
                      and not ingress_state.get('receipt_pending', False)
                      and stop['state'] in ('done', 'absent'))
        work_ok = (bus_state['buffered'] == 0 and not bus_state['failed'] and state['work_ok'] and background['work_ok']
                   and tasks.get('run', {}).get('state') not in ('failed', 'cancelled'))
        return {'app': state, 'consolidation': background, 'ingress': ingress_state, 'wake_bus': bus_state, 'owned_tasks': tasks,
                'transport': transport_state, 'owner_changed': not same,
                'quiescent': quiescent, 'cleanup_ok': cleanup_ok,
                'work_ok': work_ok, 'at_stop_return': deepcopy(self._drain_stop_return)}

    async def _drain_with_primary(self, *, deadline: float,
                                  primary: BaseException | None,
                                  budget_exhausted: bool = False) -> dict[str, Any]:
        from .app import ShutdownFailed, ShutdownIncomplete, _drain_deadline
        limit = _drain_deadline(deadline)
        app, transport, owned, loop, worker, ingress = self._pin_shutdown_owner()
        before = self._shutdown_observation()
        expired_at_entry = loop.time() >= limit
        # Expiry is observation-only: no new stop/close tasks or cancellation.
        # Tasks admitted with a live budget remain owned even after it expires.
        if (not budget_exhausted and app is not None and 'stop' not in self._drain_tasks
                and loop.time() < limit):
            self._own_shutdown_task('stop', self._request_shutdown_stop(app))
        if (not budget_exhausted and owned and transport is not None
                and 'close' not in self._drain_tasks
                and loop.time() < limit):
            self._own_shutdown_task('close', self._invoke_shutdown(transport, 'aclose'))
        if not budget_exhausted and loop.time() < limit:
            self._revoke_batch_identity()
            if self._drain_bus is not None:
                self._drain_bus.begin_shutdown()
            if ingress is not None:
                ingress.begin()
            if worker is not None:
                worker.begin_shutdown()
        interrupted = False
        prior_primary = type(primary).__name__ if primary is not None else None
        try:
            while True:
                if not (self.app is app and self.transport is transport
                        and bool(self.owns_transport) == owned
                        and getattr(self, "consolidation", None) is worker
                        and self._ingress_shutdown is ingress):
                    break
                app_tasks = (() if app is None else (app._task, app._pacing_task))
                background_tasks = () if worker is None else worker.shutdown_tasks()
                ingress_tasks = (() if ingress is None or ingress.state()['accepting']
                                 or budget_exhausted or loop.time() >= limit else ingress.observers())
                pending = {t for t in (*self._drain_tasks.values(), *app_tasks, *background_tasks, *ingress_tasks)
                           if t is not None and not t.done()}
                remaining = limit - loop.time()
                if budget_exhausted or not pending or remaining <= 0:
                    break
                await asyncio.wait(pending, timeout=remaining)
                if loop.time() >= limit:
                    break
        except asyncio.CancelledError as cancellation:
            # Caller cancellation does not cancel the owned cleanup tasks.
            if primary is not None:
                cancellation.__cause__ = primary
                cancellation.add_note("prior work failure retained in owned run task")
            primary = cancellation
            interrupted = True
        finally:
            after = self._shutdown_observation()
            pending = [name for name, state in after['owned_tasks'].items()
                       if state['state'] == 'pending']
            if not after['ingress']['drained']:
                pending.append('plugin_ingress')
            for index, task in enumerate(after['consolidation']['tasks']):
                if task['state'] == 'pending':
                    pending.append('consolidation.' + task['role'] + '.' + str(index))
            for name in ('main', 'pacing'):
                if after['app'].get(name, {}).get('state') == 'pending':
                    pending.append('app.' + name)
            if (app is not None and 'stop' not in self._drain_tasks
                    and not after['app']['stop_requested']):
                pending.append('request_stop')
            if owned and transport is not None and 'close' not in self._drain_tasks:
                pending.append('close_transport')
            exhausted = budget_exhausted or loop.time() >= limit
            self.shutdown_report = {
                'before': before, 'after': after, 'deadline': limit,
                'expired_at_entry': expired_at_entry, 'deadline_exhausted': exhausted,
                'wait_budget_exhausted': budget_exhausted,
                'responsibility': {'owner': 'this_console', 'pending': pending,
                                   'requires_new_budget': exhausted and bool(pending),
                                   'next_action': ('stop_and_drain_with_new_deadline'
                                                   if exhausted and pending else None)},
                'interrupted': interrupted, 'prior_primary_type': prior_primary,
                'primary_type': type(primary).__name__ if primary is not None else None}
        if primary is not None:
            raise primary
        if not after['quiescent'] or pending:
            raise ShutdownIncomplete(self.shutdown_report)
        if not after['cleanup_ok'] or not after['work_ok']:
            raise ShutdownFailed(self.shutdown_report)
        return self.shutdown_report

    async def stop_and_drain(self, *, deadline: float) -> dict[str, Any]:
        """Opt-in bounded shutdown, retaining pending work for later confirmation.

        Legacy run/stop remain unchanged. Only owned transport is closed. Repeated
        confirmations reuse actual tasks; they do not retry work or client close.
        Expired calls only observe. Retained work needs a NEW explicit stop budget.
        """
        return await self._drain_with_primary(deadline=deadline, primary=None)

    async def run_and_drain(self, *, deadline: float) -> dict[str, Any]:
        """Opt-in run AND cleanup sharing one caller-supplied absolute deadline.

        Primary failure/cancellation keeps its identity. Cleanup errors or pending
        work remain visible in shutdown_report and in retained owned task handles.
        """
        from .app import _drain_deadline
        limit = _drain_deadline(deadline)
        if self.app is None:
            raise RuntimeError('strong_run_requires_assembled_app')
        app, transport, owned, loop, worker, ingress = self._pin_shutdown_owner()
        if self._drain_tasks or app._task is not None or app.pacing_stop.is_set():
            raise RuntimeError('strong_lifecycle_already_started')
        primary = None
        exhausted = False
        try:
            remaining = limit - loop.time()
            if remaining <= 0:
                exhausted = True
                raise TimeoutError('run_deadline_exhausted')
            self._own_shutdown_task('run', app.run())
            run = self._drain_tasks['run']
            done, pending = await asyncio.wait({run}, timeout=remaining)
            if pending:
                exhausted = True
                raise TimeoutError('run_deadline_exhausted')
            run.result()
        except BaseException as error:
            primary = error
        return await self._drain_with_primary(
            deadline=limit, primary=primary, budget_exhausted=exhausted)


    def a_tool_row(self, record: Any) -> None:
        """ **2026-10-10 卡片第三刀（乙）**： 把一条工具记录也**推出去** 。

        ⚠ **它走的是与 `say` 同**一条通道** （`self.write` ）——
           而**多带两格**：`tool`（工具名 ）与 `stage`（`call` ⁄ `result` ）。
           后面那两层（`web.py::push` → `_frame`）**一个字都不用改** ：
           `_the_moment` 是 `out = dict(frame)` （ 只改 `at` ）⇒ 其余字段原样过去 。
        """
        if not self._owner_round_visible():
            return
        write = self.write if self.write is not None else _to_stdout
        one = record if isinstance(record, dict) else {}
        kind = str(one.get("kind", "") or "")
        ref = str(one.get('tool_ref', '') or '')
        display = {'display_id': ref + ':' + kind} if ref else {}
        if str(one.get("name", "") or "") == _A_SPOKEN_OUT_LOUD and kind == "function_call":
            # A request is intent, not receipt; do not set answered or hide failure.
            said = _the_text_she_said_out_loud(one.get("text", "") or "")
            if said:
                write({"kind": _A_MESSAGE, "who": _OTHER, "at": _now_at(),
                       "text": "拟发送（未确认送达）：" + said, "delivery": "intent", **display})
                return
        write({"kind": _A_TOOL, "who": _OTHER, "at": _now_at(),
               "tool": str(one.get("name", "") or ""),
               "stage": "call" if kind == "function_call" else "result",
               "text": one.get("text", "") or "", **display,
               # **2026-10-10 卡片第四刀**： 轮号与**思考**也带过去
               # （ 页上展开时才看得见 ）。 而它们**不进会话**
               # —— 那一条路全在 `round.py` 那两处 `session.append` 上 。
               "run": str(one.get("run", "") or ""),
               })

    def say(self, text: str, at: str | None = None, *, display_id: str = "") -> None:
        """ **它说的那一句** —— 与那一批走**同一个注入面**（`write`）。

        ⚠️ **"哪一句是说给人听的"照 `cli.py:20-23` 那条现成的口径**：
           正文优先，`text.strip()` 为空时用**拒绝文本**（"只回拒绝的流里正文是空的，
           而**那是一次成功的对话**"）。⇒ 出口不必去猜会话里哪几条是"模型刚说的"
           （那是同一件事的第二个答案）。
        ⚠️ **一次 `write` 一帧**：与出口那边 `_text` 同一条 ——
           那一帧的正文里可以有换行，出口不替谁折行。
        ⚠️ **这一句就是"模型出过话"那一格**（`answered`）：模型答的每一句都经这里
           （`_spoke` 那一层调的就是它）⇒ 一个字段、一处置真，"出过话没有"只有一个答案。

         **这一帧的 `who` 是另一头**（`_OTHER`），而 `at` 是它自己那一刻（`HH:MM`）——
        与他那一帧**同一个形状**，只有那两格的值不同（`_frame_for` 那张表是线格式的正本）。
        ⚠️ **为什么盖在这一处**（与他那一帧对称）：他那一刻由 `_line_to_event`
           在**读进来**那一刻盖（本模块里），而说话的函数在**说出去**那一刻盖。
            另一条路（把 `round.py:157` 那个记录的 `ts_ms` 送进来）动的是**两面冻结面**，
           而它说的是"这一条记在哪一刻"——不是"这一句何时说出去"（见 `_now_at`）。
        ⚠️ **`at` 可注入**（与 `_now_ms` 同一档）：缺省那一格才读一次现在。
        ⚠️ 而两个读者各自要什么**一个字都不变**：页那一头发的是**这一帧**
           （`_frame` 把它编成一条 `data:`），而终端那一头读的是它的**正文**
           （`_to_stdout` 只认 `text` 那一格）—— 两个读者读的是**同一份字段**，
           而"剥一截前缀"这件事从本刀起**不存在**（那一截没有了）。
        """
        if not self._admit_wake_speech(text):
            return
        self.answered = True
        if self._owner_round_visible():
            write = self.write if self.write is not None else _to_stdout
            try:
                write({"kind": _A_MESSAGE, "who": _OTHER, "at": _now_at() if at is None else at,
                       "text": text, **({"display_id": display_id} if display_id else {})})
            except BaseException as error:
                self._reminder_delivery_note('console_output_uncertain', error=type(error).__name__)
                raise
            self._reminder_delivery_note('console_output_emitted', human_read_confirmed=False)

    def _spoke(self, inner: Any) -> Any:
        """ **把那个调 `generate` 的地方包一层** —— 模型说了什么，就从这儿出去。

        ⚠️⚠️ **为什么包在这一层、而不是"事后读会话"**：`generate` 的返回**就是**那一句话
           （`Result.text` ／ `.refusal`，`generate.py:95-113`），而这一刀要的"模型回了一句"
           **当场**就有答案 ⇒ 出口不必再去猜会话里哪几条属于刚跑完的那一轮
           （那是**第二个答案**，而且要跨一轮才知道边界）。
        ⚠️ **它一个字节都不改**：参数原样转交 —— `round.py:115-121` 那两路调用的形状
           **不一样**（一路三个位置参数、一路多一个 `signal=`）⇒ 这里是
           `*args, **kwargs`，而它**不解释**它们（转发就是全部）。
        ⚠️ **`generate()` 抛 ⇒ 一个字都不打，而异常照旧往上走**（那是 `round.py` 三条
           `except` 管的事：判成"这一轮失败"，由层 B 的预算去收）。
            **代价写在这里**：一次失败在**这一个出口**上是无声的（你只看到自己那一行
           回荡，而机器照常在转）—— 那是本刀**已知**的一处，不是漏：
           失败那一笔的账在 `loop` 的失败计数上，而"把它画出来"是另一个出口的事。
        """
        console = self

        from functools import wraps

        @wraps(inner)
        async def generate_and_say(*args: Any, _defer_speech=False, **kwargs: Any) -> Any:
            from .voice_draft import observe_reply
            result = await observe_reply(inner, console, args, kwargs)
            text = console._reply_text(result)
            if text.strip():
                from uuid import uuid4
                reference = uuid4().hex
                try:
                    result.display_id = reference
                except (AttributeError, TypeError):
                    reference = ""
                if not _defer_speech:
                    console.say(text, display_id=reference)
            return result

        return generate_and_say

    @staticmethod
    def _reply_text(result: Any) -> str:
        text = getattr(result, "text", "") or ""
        return text if text.strip() else getattr(result, "refusal", "") or ""

    def _say_result(self, result: Any) -> None:
        text = self._reply_text(result)
        if text.strip():
            self.say(text, display_id=getattr(result, "display_id", "") or "")

    async def _tap_reply(self, result: Any) -> None:
        """Offer committed plain speech to the external world, if one owns the batch."""
        if (getattr(result, "calls", ()) or ()) or not callable(self.output_tap):
            return
        text = getattr(result, "text", "") or getattr(result, "refusal", "") or ""
        if not isinstance(text, str) or not text.strip():
            return
        if not self._admit_wake_speech(text, phase="tap"):
            return
        try:
            import inspect
            sent = self.output_tap(self._active_batch, text)
            if inspect.isawaitable(sent):
                await sent
        except Exception as exc:  # delivery failure must not rerun the model
            import sys
            print(f"[output] delivery failed: {type(exc).__name__}",
                  file=sys.stderr, flush=True)
            self._reminder_delivery_note('output_tap_failed', error=type(exc).__name__)
        else:
            self._reminder_delivery_note('output_tap_returned', human_read_confirmed=False)

    def _finish_ingress_batch(self, batch) -> None:
        activity = getattr(self, 'activity_control', None)
        if activity is not None:
            activity.finish_foreground()
        gate = getattr(getattr(self.app, 'loop', None), 'gated', None)
        if callable(gate) and gate():
            self._reminder_delivery_note('consumption_deferred', reason='gate_closed_after_enqueue')
        local_pending = self._local_observation_batch
        if local_pending is not None:
            owner, tickets = local_pending
            owner.finish_batch(tickets)
            self._local_observation_batch = None
        pending = self._ingress_batch
        if pending is not None:
            ingress, tickets = pending
            ingress.finish_batch(tickets)
            self._ingress_batch = None

    async def _take_a_batch(self) -> list[Any] | None:
        """ **取批那一步**（`loop.py:171` 的 `take_batch`）—— 泵那一跳 ＋ **把它记进会话**。

        ⚠️⚠️ **两件事挤在一个协程里，而那是有意的**（这里是"外面来的东西进这一套机器"
           唯一的那道门）：
           · **泵**（Task 2）：读一行 ⇒ 推给总线 ⇒ 把 `feed.next_batch` 那一批端回来；
           · **把那一批写进会话**（`_into_the_session`）—— 少了这一笔，`round.py:113`
             发出去的请求里**没有你说的话**：会话上只有模型的回执，而引擎**直接拒**空
             `input`（`session.py:404-405` 记着实测那一条）⇒ "这一套能说话"根本不成立。
           ⚠️ 这一笔**不是本刀的活**，本刀只补最小的那一步 —— 欠主见
           `_BATCH_TO_SESSION_OWED_BY`（那一层的名字与它要回答的问题都在那里）。
        ⚠️ **次序是"先记进会话、再交回给循环"**：交回之后 `loop.py:132` 那一句立刻会
           跑这一批，而那一轮要的请求正是从会话里取的 ⇒ 记晚了就是"模型看不见你这一行"。
        ⚠️ **空批不记**：`pump` 只在"读法到头"时回 `None`（它不造空批，Task 2 拍过）
           ⇒ 这里那个 `if batch` 是一道防身闸，不是一条会走的路。
        """
        self.flush_reminder_notices()
        # With admission enabled, deliver owns snapshot/check/append before any round.
        # Blocked batches never enter the model session; ingress audit logs stay intact.
        from .batch_queue import conversation_batches

        # The loop owns this consumer. Never replace a model/tool/output's
        # audience while that batch is processing, even through an extra caller.
        loop = getattr(self.app, "loop", None)
        if self._taking_batch or getattr(loop, "processing", False):
            raise RuntimeError("a conversation batch is already in flight")
        self._taking_batch = True
        try:
            while True:
                if self._ingress_batch is not None or self._local_observation_batch is not None:
                    raise RuntimeError('previous ingress batch has not retired')
                activity = getattr(self, 'activity_control', None)
                if (self._pending_batches and activity is not None
                        and activity.has_transfer and self.bus.ready):
                    urgent_snapshot = self.bus.take_if_ready()
                    if urgent_snapshot:
                        self._pending_batches.extend(conversation_batches(
                            urgent_snapshot, isolate=self.event_isolation))
                if not self._pending_batches:
                    batch = await self.pump(self.bus, read=self.read)
                    if batch is None:
                        self._revoke_batch_identity()
                        self._active_batch = ()
                        return None
                    if not batch:
                        return batch
                    self._pending_batches.extend(conversation_batches(batch, isolate=self.event_isolation))
                # Filter at consumption, not snapshot time: a queued message
                # can have been recalled while the previous audience ran.
                activity = getattr(self, 'activity_control', None)
                if activity is not None and not await activity.wait_foreground_admission():
                    return None
                batch = (activity.select_batch(self._pending_batches) if activity is not None
                         else self._pending_batches.pop(0))
                local_owner = getattr(self, 'local_observations', None)
                owner_inputs = False
                if local_owner is not None:
                    tickets = local_owner.begin_batch(batch)
                    self._local_observation_batch = (local_owner, tickets)
                    # The journal attests each original event before filtering.
                    # A plugin's claimed sender, or a mixed batch, cannot opt in.
                    owner_inputs = bool(tickets) and len(tickets) == len(batch)
                if self._ingress_shutdown is not None:
                    ingress = self._ingress_shutdown.ingress
                    self._ingress_batch = (ingress, ingress.begin_batch(batch))
                if callable(self.event_filter):
                    batch = [item for item in batch if self.event_filter(item)]
                if not batch:
                    self._finish_ingress_batch(batch)
                    continue
                from .continuation import is_continuation
                policy = getattr(self, 'wake_policy', None)
                batch = [e for e in batch if not is_continuation(e) or
                         (policy is not None and policy.continuation_valid(e, now_ms=_now_ms()))]
                if not batch:
                    self._finish_ingress_batch(batch)
                    continue
                from .owner_tick import has_marker
                from .follow_up import event_valid
                pending = tuple(e for later in self._pending_batches for e in later)
                batch = [e for e in batch if not has_marker(e) or event_valid(e, self, pending)]
                if not batch:
                    self._finish_ingress_batch(batch)
                    continue
                # No await between dequeue, audience assignment and append.
                # Once consumed, an exception/cancellation never requeues it:
                # outputs may already have escaped; automatic replay is unsafe.
                batch = [item for item in batch if _has_content(item)]
                if not batch:
                    self._finish_ingress_batch(batch)
                    continue
                append_early = not callable(self.batch_admission)
                if append_early and self.handoff is not None:
                    # Standalone consoles append here. Wait for any idle
                    # handoff before writing a user message into its old file.
                    await self.handoff.flush_handoff()
                batch = self._bind_batch_identity(batch)
                if owner_inputs:
                    self._owner_prompt_batch = batch
                if append_early:
                    self._into_the_session(batch)
                return batch
        except BaseException:
            self._revoke_batch_identity()
            raise
        finally:
            self._taking_batch = False

    def _into_the_session(self, batch: Sequence[Any]) -> None:
        """ **一批外面来的话 ⇒ 会话里那几条记录**（一笔点名了的补丁，见那个常量）。

        ⚠️ **只有带正文的那些进**（`text` 是 `str` 且非空）：一条空记录会让下一轮请求里
           多一句空气 —— 而"外面来的东西里有几件是真话"这件事归**那一批**说了算
           （出口那边同一口径：`_text` 那一层照原样处理缺 `text` 的东西）。
            **而这条判据不再是它私有的一句**：它是 `_has_content` 那**一条**共用规则
           （出口 `deliver` 收批的那道闸引用的是**同一句** —— 从前两半各判一半，
           于是同一个空行在两边得到相反答案，见那条规则的 docstring）。
           ⚠️ 这里**照旧自己再判一次**：两处引用同一条规则 ⇒ 是同一个答案的第二道读，
             不是第二条真相；而少了这一道，"谁的批从别处进来"那种形状就没人判了。
        ⚠️ **kind 照那一件的 `type` 落**（缺省 `message`）：交接注入的那几种
           （`handoff` ／ `notice` ／ `handoff_note`）靠它**在笔记里被认出来**
           （`note.py:19-20` 的 `HANDOFF_NOTE_KIND` 就是按 kind 跳过上一份笔记的）。
        ⚠️ **一件一条**（不是把这一批拼成一条）：每一件都是**外面说的一件事**，
           `note.build` 逐条看记录、`outbound` 逐条渲染 —— 拼起来会把"他说了两句"
           变成"我们把它粘成了一句"。
        ⚠️ **写盘会抛就照抛**（`Session.append` 先落盘、再记账）：一条静默丢掉的用户消息
           是这一层最不该有的东西（下一轮模型会答一个"没听清"的东西）。
        """
        from .note import Record

        # 上一条唤醒注记，在**下一批写进来之前**回滚掉（2026-10-11，B 的窄形状）。
        # 为什么不在"那一轮跑完"那一刻删：那要动 loop 那一层的轮末，而在轮中间
        # 动会话有删到真对话的风险。改成在这里做，效果是同一件 —— 会话里
        # 任何时候最多留一条唤醒注记，真实的对话一开始它就没了。
        # 范围卡死：只删 kind == "tick" 且 role == "system" 的那些（唤醒专用）；
        # 交接 / 整理态那几种 kind 一个字不动。
        # 读不动 / 改不动就照旧往下走（回滚不是这条路的必要条件）。
        try:
            before = list(self.session.records())
            kept = [one for one in before
                    if not (str(getattr(one, "kind", "")) == "tick"
                            and str(getattr(one, "role", "")) == "system")]
            if len(kept) != len(before):
                self.session.reset(kept)
        except Exception:                              # noqa: BLE001
            pass

        if getattr(self, "display_timeline", False):
            self._display_batch_marker = None
            gate = getattr(getattr(self.app, "loop", None), "gated", None)
            if (not _context_only_batch(batch) and not (callable(gate) and gate())
                    and any(self._visible_to_owner(item) for item in batch)):
                number = self.batches + 1
                self._display_batch_started = time.monotonic()
                self._display_batch_timings = []
                self._display_batch_marker = self._display_marker(
                    "ui_batch", number, f"[批 {number - getattr(self, '_display_batch_base', 0)}]")
        self._replay_fallback_in_batch = False
        from .session import Session
        prior = self.session.records() if isinstance(self.session, Session) else ()
        self._replay_history_boundary = (len(prior), prior[-1]) if prior else None
        self._request_branch_incoming = ()
        for item in batch:
            text = _content_text(item) # 与 `deliver` 那道闸**同一次调用**
            if text is None:
                continue
            kind = getattr(item, "type", None) or "message"
            # **这一件带的图也落进会话**（2026-10-05「图片输入」的入口那一半）——
            # ⚠️ 没有附件的那一件 ⇒ 元组是空 ⇒ **与从前逐字节相同** （零回归）。
            blobs = getattr(item, "blobs", None) or ()
            #  **2026-10-11（① 身份）**： **机器自己注入的那种，不写成「用户说的话」**  ——
            # 量到过： 这里从前**一律** `role="user"` ⇒ 心跳 ／ 整理态 ／ 交接
            # 全都变成「**你说了一句**」 ⇒ 对它来说就是有人在说话 ⇒ 它当然回答
            # （ 2026-10-11：页上那个「对话」的样子就是这么来的 ）。
            # ⚠️ **判据是 `origin`** ： `internal` ＝ 机器自己注入的 ⇒ `system` ；
            # `external` ＝ 真的有人打了一行 ⇒ 照旧 `user` （ 一个值一个意思 ）。
            role_of = ("system" if str(getattr(item, "origin", "") or "") == "internal"
                       else "user")
            # **2026-10-12（作者：「加」）**：**谁说的**那一格 —— 信封上带着
            #    （`EventEnvelope.sender_key`），从前没落进记录 ⇒ 它眼里"这是谁说的话"
            #    这一格一直是空的。⚠️ 它**不进 `name`**（那一格是"哪一类东西"）——
            #    见 `note.Record.who` 那一段。
            who_of = str(getattr(item, "sender_key", "") or "")
            # ★ **2026-10-12（作者：「五个都填」）**：那五个视图格从这一刀起**有写手**
            #    （逐格的理由见 `_the_view_fields_of`）—— 在那之前它们在盘上**全是假**，
            #    凡是拿它们当判据的代码都是"看着像有牙、其实咬不动"。
            views = _the_view_fields_of(item)
            from .session_view import event_scope

            record = Record(kind=str(kind), role=role_of, text=text, ts_ms=_now_ms(),
                            images=tuple(str(one) for one in blobs), who=who_of,
                            scope=event_scope(item), display_id=self._frame_reference(item), **views)
            self.session.append(record)
            self._request_branch_incoming += (record,)
        self._incoming_prompt_context = None
        if self._request_branch_incoming:
            last = self._request_branch_incoming[-1]
            from .owner_tick import OWNER_SCOPE
            owner_input = batch is getattr(self, '_owner_prompt_batch', None) and last.scope == OWNER_SCOPE
            if last.role == 'user' and (last.scope.startswith('private:qq:') or owner_input):
                from .batch_prompt import IncomingContext
                self._incoming_prompt_context = IncomingContext(last)

    def _timing_path(self) -> Path | None:
        """ 那三段落在哪儿（与累计**分开两个文件** —— 一个是账 ／ 一个是上一次 ）。"""
        root = getattr(self, "usage_root", None)
        if root is None:
            return None
        return Path(root) / "timing.json"

    def _load_timing_last(self) -> dict[str, float | None] | None:
        """ **把上一次那三段读回来**（没有 ／ 读不动 ⇒ `None` —— **不编** ）。"""
        path = self._timing_path()
        if path is None or not path.exists():
            return None
        try:
            got = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(got, Mapping):
            return None
        out: dict[str, float | None] = {}
        for key in STAGE_KEYS:
            value = got.get(key)
            out[key] = float(value) if isinstance(value, (int, float)) else None
        return out if out.get("total") is not None else None

    def _keep_timing(self, timing: Mapping[str, Any] | None) -> None:
        """ **记下这一轮那三段** ＋ 落盘（照 `_keep_usage` 那一套 ）。

        ⚠️ 没报（`None`）⇒ 也**照记 `None`** 而不是留着上一次的 ——
           留着的话，页上会把**上一轮**的时间冒充成这一轮的 （"过期收据"那一条 ）。
        ⚠️ 写不动盘 ⇒ **不拦路**（那只是一格读数 ）。
        """
        if getattr(self, "_display_batch_marker", None) is not None:
            self._display_batch_timings.append(dict(timing) if isinstance(timing, Mapping) else {})
        if not isinstance(timing, Mapping):
            self.timing_last = None
            self._save_timing_last()
            return
        out: dict[str, float | None] = {}
        for key in STAGE_KEYS:
            value = timing.get(key)
            out[key] = float(value) if isinstance(value, (int, float)) else None
        self.timing_last = out
        self._save_timing_last()

    def keep_stage(self, name: str, seconds: float | None) -> None:
        """ **给插件报自己那一段的入口**（2026-10-05 作者："可以先留个接口，
        我打算把 ASR 和 TTS 作为插件加载"）。

        整条链是：`说话 → [ASR] → 收到 → [思考] → [输出] → [TTS] → 听到`
        ⇒ 那两段在**两端**（不在"思考／输出"里 ），所以它们各占一格：
            · **ASR** —— 在"收到消息"**之前**（把语音变成那条消息的就是它 ）；
            · **TTS** —— 在"输出"**之后**（把文字变成语音的是它 ）。
        ⚠️ 只认那两格（`STAGE_KEYS` 里除 `thinking` ／ `output` ／ `total` 之外的 ）——
           其余三格由引擎那一路填，插件不许覆盖（那会把"这一轮真的花了多久"说成别的 ）。
        ⚠️ 没有下家／没报 ⇒ 留 `None`（页上画 `—` —— **不编一个 0** ）。
        """
        if name not in PLUGIN_STAGE_KEYS:
            raise ValueError(f"不认这个段：{name!r}（插件能报的只有 {list(PLUGIN_STAGE_KEYS)}）")
        current = getattr(self, "timing_last", None)
        if not isinstance(current, dict):
            current = {key: None for key in STAGE_KEYS}
        current[name] = float(seconds) if isinstance(seconds, (int, float)) else None
        self.timing_last = current
        self._save_timing_last()

    def _save_timing_last(self) -> None:
        """ **原子写**（临时文件 ＋ `os.replace` —— 与累计那一份同一条成法 ）。"""
        path = self._timing_path()
        if path is None:
            return
        try:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(self.timing_last), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            return

    def _usage_path(self) -> Path | None:
        """ 累计那一份落在哪儿 —— **没有 root ⇒ 不落**（**不编一个位置** ）。"""
        root = getattr(self, "usage_root", None)
        if root is None:
            return None
        return Path(root) / "usage.json"

    def _load_usage_totals(self) -> tuple[dict[str, int], int | None]:
        """ **把盘上的累计读回来**（没有 ／ 读不动 ⇒ 从零 —— **不编** ）。

         一并交出**那笔累计属于哪个会话**（`since` ＝ 那个会话最早那一条的时刻 ）——
           盘上**没有这一格**（ 从前留下的文件就没有 ）⇒ 起点**未知**
          ⇒ 下一次累加时**从头算** （ 不知道它是谁的，就不冒充是这个会话的 ）。
        """
        zero: tuple[dict[str, int], int | None] = ({"input": 0, "cached": 0}, None)
        path = self._usage_path()
        if path is None or not path.exists():
            return zero
        try:
            got = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return zero
        if not isinstance(got, Mapping):
            return zero
        since = got.get("since")
        if any(type(got.get(key, 0)) is not int or got.get(key, 0) < 0
               for key in ("input", "cached")):
            return zero
        if since is not None and (type(since) is not int or since < 0):
            return zero
        return ({"input": got.get("input", 0), "cached": got.get("cached", 0)}, since)

    def _save_usage_totals(self) -> None:
        """ **原子写**（临时文件 ＋ `os.replace` —— 本仓的成法）。

        ⚠️ 写不动（只读目录 ／ 盘满）⇒ **不拦路**：那只是一格**读数** ，
           不该让一趟对话为它停下来 。
        """
        path = self._usage_path()
        if path is None:
            return
        try:
            tmp = path.with_name(path.name + ".tmp")
            # 起点跟着一起存 —— 不然重启之后就不知道它属于谁了 。
            box = dict(self.usage_totals)
            box["since"] = getattr(self, "usage_since", None)
            tmp.write_text(json.dumps(box), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            return

    def _provider_context_plan(self, view: Any, has_scope: bool) -> str:
        """Keep provider identity across append/restart, rotate after history replacement."""
        import hashlib
        path = Path(self.session.path).resolve()
        scope = ""
        if has_scope:
            from .session_view import batch_scope
            scope = batch_scope(self._active_batch)
        from .provider_path_identity import session_identity
        root = getattr(self, "_provider_state_root", path.parent / "provider-state")
        context_key = [session_identity(path, root), scope]
        branches = getattr(self, "request_context_branches", None)
        if branches is not None and branches.entries:
            from .request_context_branch import identity_for
            branch = branches.get(identity_for(self._branch_config, scope))
            if branch is not None:
                context_key.extend(["request-branch", branch["context"]])
        key = hashlib.sha256(json.dumps(context_key).encode()).hexdigest()
        root = getattr(self, "_provider_state_root", path.parent / "provider-state")
        file = Path(root) / "contexts" / (key + ".json")
        from .provider_record_replay import identity_fields
        rows = [hashlib.sha256(json.dumps(identity_fields(row), sort_keys=True).encode()).hexdigest()
                for row in view.records() if not row.is_ephemeral]
        return file, rows

    @staticmethod
    def _persist_provider_context(file, rows):
        import hashlib, uuid
        saved = None
        if file.exists():
            if file.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("provider context metadata exceeds size limit")
            saved = json.loads(file.read_text(encoding="utf-8"))
            valid_hash = lambda value: (isinstance(value, str) and len(value) == 64
                                        and all(c in "0123456789abcdef" for c in value))
            if (not isinstance(saved, dict) or type(saved.get("version")) is not int
                    or saved.get("version") != 1
                    or not valid_hash(saved.get("context"))
                    or not isinstance(saved.get("records"), list)
                    or not all(valid_hash(value) for value in saved["records"])):
                raise ValueError("invalid provider context metadata")
        if saved is not None and rows[:len(saved["records"])] == saved["records"]:
            identity = saved["context"]
        else:
            identity = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        current = {"version": 1, "context": identity, "records": rows}
        if current != saved:
            file.parent.mkdir(parents=True, exist_ok=True)
            import os, tempfile
            fd, name = tempfile.mkstemp(prefix=file.name + '.', suffix='.tmp', dir=file.parent)
            temp = Path(name)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(json.dumps(current, sort_keys=True).encode('utf-8'))
                    stream.flush()
                    os.fsync(stream.fileno())
                temp.replace(file)
            finally:
                temp.unlink(missing_ok=True)
        return identity

    def _provider_replay_context(self, view: Any, has_scope: bool) -> str:
        return self._persist_provider_context(*self._provider_context_plan(view, has_scope))

    async def _aprovider_replay_context(self, view: Any, has_scope: bool) -> str:
        from .retained_io import file_call
        plan = self._provider_context_plan(view, has_scope)
        identity = await file_call(self._persist_provider_context, *plan)
        if self._provider_context_plan(view, has_scope) != plan:
            raise ValueError('provider context changed during checkpoint')
        return identity

    def _the_conversation_epoch(self) -> int | None:
        """ **本会话的起点**（最早那一条的时刻 ）—— 它变了 ⇒ 会话被换过 。

        ⚠️ **为什么是它** （ 2026-10-11 作者提的那条 ）：
          · **交接** ⇒ `handoff.py:550` 那句 `reset([*prefix, *tail])`
            （ 本仓自己的注释：「**会话在此刻整体替换**」）⇒ 最早那条**必变** ；
          · **手工清上下文** ⇒ 走的是同一条路 ；
          · ⚠️ **进程重启不动会话** ⇒ 它**不变** ⇒ 那笔累计接着算
            （ 当初落盘要保住的正是它 ）。
        """
        session = getattr(self, "session", None)
        rows = getattr(session, "records", None)
        if not callable(rows):
            return None
        try:
            first = rows()
        except Exception: # noqa: BLE001 —— 读不动 = 不知道
            return None
        if not first:
            return None
        got = getattr(first[0], "ts_ms", None)
        return int(got) if isinstance(got, int) else None

    def _keep_usage(self, usage: Mapping[str, Any] | None) -> None:
        """ **把这一轮的用量记下来**（2026-10-05）—— 存**原样**那一份，不解释它 。

        ⚠️ 上游没报 ⇒ 也**照记 `None`** 而不是"留着上一次的"：留着的话，页上那一行
           会把**上一轮**的读数冒充成这一轮的 （那是"过期收据"，正是本仓怕的那一类）。
        """
        self.last_usage = dict(usage) if isinstance(usage, Mapping) else None
        # **会话累计**（2026-10-05 晚）—— 那一格要的是**整个会话**的命中率 ：
        # 参考那一份就是这么算的（它那一格的 `usage` 注释写着 "the session's
        # token-usage projection value" ，而页上那个 99.6% 是累计的 ）。
        # ⚠️ 单次的话它会**跳**（实测连发三次：0% ／ 0% ／ 100% —— 第一次建前缀、
        # 之后命中，那是**正常**的，只是"上一次请求"这个口径读起来像坏了 ）。
        if not isinstance(usage, Mapping):
            return
        told = usage.get("input_tokens")
        details = usage.get("input_tokens_details")
        cached = details.get("cached_tokens") if isinstance(details, Mapping) else None
        if isinstance(told, int) and isinstance(cached, int):
            totals = getattr(self, "usage_totals", None)
            # **换了会话就从头算**（2026-10-11 作者提的那条 ）——
            # 起点变了（交接 ／ 清上下文）⇒ 那一笔**不是这个会话的**
            # ⇒ 从零起 ； 起点没变（进程重启）⇒ 接着算 。
            now = self._the_conversation_epoch()
            stale = (now is not None
                     and getattr(self, "usage_since", None) != now)
            if not isinstance(totals, dict) or stale:
                totals = {"input": 0, "cached": 0}
                self.usage_totals = totals
                self.usage_since = now
            totals["input"] += told
            totals["cached"] += cached
            # **落盘**（2026-10-05）：不写盘的话，服务一重启这个数就**归零**
            # （今天实测过一次：作者聊了 22 条，进程一退，那几句的读数全没了 ）。
            self._save_usage_totals()

    def _record_event(self, draft: Mapping) -> Any:
        """ 交给 `make_run_round` 的那个"写一条事件"（"思考进日志"那条路，`round.py:237`）。

        ⚠️ **它按"当时那一本"写**（读 `self.app.store`）：事件库是 `build()` 造的，
           而 `make_run_round` 在那一行**之前**就要那个可调用对象 ⇒ 只能这么晚绑。
            这样拿到的是**同一本**（不是另造一本：两本 `EventStore` 落在同一个目录上
           就是**两份真相**）。
        ⚠️ **没有事件库就当场点名**（不许静默丢一条日志）：`build()` 没拿到 `store_root`
           时 `App.store` 是 `None` —— 而那条日志是**唯一的取证面**（`round.py` 那一节
           写着"丢了一条日志，不丢结论"：那是**写的时候**丢得起，不是"可以不写"）。
        """
        store = getattr(self.app, "store", None)
        if store is None:
            raise RuntimeError(
                "这一套没有事件库（`build()` 没拿到 `store_root`）⇒ 这条日志写不出去 —— "
                "静默丢掉它不行：那是这一套唯一的取证面")
        if draft.get("type") in ("round-failure", "provider-replay-fallback"):
            draft = dict(draft)
            meta = dict(draft.get("meta") or {})
            diagnostic = dict(meta.get("diagnostic") or {})
            from .transport import SUBSCRIPTION_LIMIT
            if draft.get("type") == "round-failure" and diagnostic.get("code") == SUBSCRIPTION_LIMIT:
                self._subscription_limit_batch = self.batches
            diagnostic["batch"] = self.batches
            control = getattr(self, "round_control", None)
            diagnostic["round_id"] = control.status().get("round_id") if control else None
            draft["meta"] = {**meta, "diagnostic": diagnostic}
        return store.append(dict(draft))

    def request_size(self) -> tuple[int, int] | None:
        """ **这一份请求有多大** —— `(消息几条, 输入 token 估算)`，**含那一句话**。

        ⚠️⚠️ **它与 `session.est_tokens()` 不是一个数、也不该是一个数**：那一条说的是
           "**存档那一份**"（`session.py:393-411` —— 本刀一个字不动它，它说的是真话）；
           这一个是"**当前配置下下一请求的源侧预览**"（消息加工具声明估算）
           （一个悄悄少算了一截的格子，正是这一刀在打的那一类东西）。
        ⚠️ **两个数同出一处**（同一次 `outbound` 的同一个 list）：分开算两次的话，
           `messages` 与 `tokens` 就会在同一个格子里答**两份不同的请求**。
        ⚠️ **没装配过 ⇒ `None`**（不是 `(0, 0)`）：一台没有会话的机器上"这一份请求有多大"
           **没有答案** —— 与 `web.state_of()` 那条"不许把不知道画成零"同一条纪律。
        ⚠️ **它每叫一次就现装一次那一句话**（`_head_cell()`）：页上那一格是**现读**的
           （`/state` 每来一次问一次）⇒ 那一遍 IO 与出一次请求同一量级（16553 字节）。
            本仓**没有任何按前缀的缓存**，而这是"改一个字，下一次请求就生效"的代价。
        """
        report = self.request_usage_preview()
        if report is None or report['status'] != 'estimated':
            return None
        return report['message_count'], report['total_estimated_tokens']

    def _remember_completed_request_preview(self) -> None:
        """Capture numeric diagnostics before retiring authority; never renew a lease."""
        from .session_view import batch_scope
        from .request_estimate import capture_size_config
        # A background/group batch must not replace the console's visible size.
        if (getattr(self, "_request_session", self.session) is not self.session
                and batch_scope(self._active_batch) != "private:console:local-owner"):
            return
        try:
            report = self.request_usage_preview()
            if (report is not None and report.get("status") == "estimated"
                    and report.get("estimate_source") != "completed_batch_snapshot"):
                from copy import deepcopy
                self._completed_request_preview = (
                    self._the_conversation_epoch(),
                    capture_size_config(self._request_config),
                    deepcopy({**report, "estimate_source": "completed_batch_snapshot",
                              "captured_at_ms": _now_ms()}))
        except Exception:
            # Diagnostics cannot prevent the exact batch's retirement.
            pass

    def uses_standby_preview(self) -> bool:
        from .session_view import batch_scope
        if not callable(getattr(self, '_standby_messages', None)):
            return False
        # The loop retains its last batch for accounting after its access has expired.
        # Only the console's own completed request can supply a retained size snapshot.
        return not self._active_batch or (
            self._batch_identity is None and (not getattr(self, '_completed_request_preview', None)
            or batch_scope(self._active_batch) != 'private:console:local-owner'))

    def request_usage_preview(self) -> dict[str, Any] | None:
        """One read-only source preview shared by /usage and request_size.

        Not native usage, the last sent wire, or an atomic Engine snapshot.
        No request/round state is mutated and no handoff policy is consulted.
        """
        session = getattr(self, "session", None)
        if session is None:
            return None
        from .request_estimate import capture_size_config, estimate_request

        snapshot = capture_size_config(getattr(self, "_request_config", None))
        if self.uses_standby_preview():
            try:
                report = estimate_request(self._standby_messages(), snapshot, include_parts=True)
            except (OSError, ValueError):
                report = {'status': 'unknown', 'reason': 'standby_preview_unavailable',
                          'total_estimated_tokens': None, 'native_input_tokens': None}
            report.update(window=getattr(snapshot, 'context_window', None),
                          estimate_source='standby_archive_preview')
            return report
        tail = getattr(session, "the_tail", None)
        try:
            head = self._head_cell()
            tail = tail() if callable(tail) else tail
            from .batch_prompt import BatchTail
            if isinstance(tail, BatchTail):
                tail = tail.for_preview()
            messages = getattr(self, "_request_session", session).outbound(head, tail)
        except (ValueError, BatchIdentityStopped):
            # No new access through a retired identity. Return only a numeric,
            # explicitly dated snapshot captured while that identity was valid.
            prior = getattr(self, "_completed_request_preview", None)
            if (prior is not None and prior[0] == self._the_conversation_epoch()
                    and snapshot is not None and prior[1] is not None
                    and prior[1].tools == snapshot.tools
                    and prior[1].max_tokens == snapshot.max_tokens):
                from copy import deepcopy
                report = deepcopy(prior[2])
                report['estimated_for_window'] = report.get('window')
                report['window'] = snapshot.context_window
                return report
            return {'status': 'unknown', 'reason': 'request_context_unavailable',
                    'total_estimated_tokens': None, 'native_input_tokens': None,
                    'window': getattr(snapshot, 'context_window', None)}
        report = estimate_request(messages, snapshot, include_parts=True)
        report['window'] = getattr(snapshot, 'context_window', None)
        return report

    def _context_pressure(self, messages, config, *, fixed_tail=()):
        """Planning pressure, distinct from both UI heuristics and billed usage."""
        from .media_budget import estimate_request_tokens
        # A prior provider measurement already includes its host contract.
        # Add the fixed request cost only to the independently estimated source.
        source_messages = [*messages, *fixed_tail] if fixed_tail else messages
        source = estimate_request_tokens(source_messages, config)
        details = dict(source_estimated_tokens=source, estimated_tokens=source)
        try:
            witness = self._consolidation_witness(review=True)
            measured = self._consolidation_prefix.pressure(messages, witness, config)
            if measured is not None:
                details.update(measured)
                details['estimated_tokens'] = max(source, measured['estimated_tokens'])
        except Exception:
            # Unknown bindings retain the pre-existing source budget, never a
            # different room/model's measured request or a fabricated zero.
            pass
        self.handoff_pressure_last = details
        return details['estimated_tokens']

    def _frame_reference(self, item):
        from uuid import uuid4
        cache = getattr(self, '_display_batch_refs', None)
        if cache is None:
            cache = self._display_batch_refs = {}
        entry = cache.get(id(item))
        if entry is None or entry[0] is not item:
            entry = cache[id(item)] = (item, uuid4().hex)
        return entry[1]

    def history(self) -> list[dict[str, Any]]:
        """ **已经说过的话 ⇒ 一串帧**（2026-10-05 作者：那一列抬头写着"刷新之后旧话就不在了
        —— 历史重放还没有" ）。

        持久化的批次和处理结束标记随会话重放。
        ⚠️ **造帧走同一处**（`_frame_for` ）：历史与现场**同一个形状** ——
           页那一侧因此**只用一条路**（`chat.append(wire)` ），不必为历史另开一支 。
        ⚠️⚠️ **那一刻那一格自己造**：`Record` 带的是 `ts_ms`（毫秒 ），
           而 `_at_of` 读的是那件东西的 `ts`（一个 ISO 串 —— 那种东西只在**泵**里现做 ）
           ⇒ 这里把 `ts_ms` 折成同一形状的 `HH:MM` （⚠️ 它**不读钟**：只转**它自己带的**
           那个数 —— 与 `_at_of` 的纪律同一条 ）。
        ⚠️ **没有会话 ⇒ 空那一串**（不是编几条假的 ）。
        """
        session = getattr(self, "session", None)
        if session is None:
            return []
        out: list[dict[str, Any]] = []
        # ⚠️⚠️ **`records` 是方法**（`session.py` 里 `def records(self)` —— 它内部
        # 交回 `tuple(self._records)` ）⇒ 必须**调用**它 。
        # ⚠️ 从前这里写的是 `session.records`（没有括号 ）⇒ 实跑当场
        # `TypeError: 'method' object is not iterable` （而判据里那个假 session
        # 用的是**属性** ⇒ 它**没抓到** —— 又一次"替身比真的宽松 ⇒ 判据绿而真跑崩" ）。
        for ordinal, record in enumerate(session.records() if callable(getattr(session, "records", None))
                                         else getattr(session, "records", ())):
            if not self._visible_to_owner(record):
                continue
            # ⚠️⚠️ **这里比的是 `Record.kind` 的正本**（`"message"` —— 落盘那一条就是这么写的 ），
            # **不是**帧那一侧的 `_A_MESSAGE`（`"msg"` ）：两者是**两个格子上的值**
            # （一个在**记录**上、一个在**帧**上）⇒ 拿后者筛前者会一条都匹配不到
            # （实测抓到过一次：`history()` 回空而会话里明明有话 ）。
            # **2026-10-10（卡片第一刀）：工具那两条也要过** —— 从前这一句
            # 只放 `"message"` ⇒ 那两条 `function_*` **根本走不到下面**
            # （ 实测：会话里 10 条 ⇒ `history()` 只交回 8 帧 ， 两条凭空没了 ）。
            # ⚠️ 而它们的 `role` 与**说话那一支一模一样**（都是 `assistant` ）⇒ 只能按
            # **`kind`** 分 —— 这正是这一句放开之后、下面那一支要做的事 。
            if getattr(record, "kind", "") == "ui_handoff_note":
                out.append(self._handoff_note_frame(record))
                continue
            if getattr(record, "kind", "") in ("ui_batch", "ui_batch_end"):
                out.append(self._timeline_frame(record.kind, record.text, record.ts_ms,
                                                record.display_id))
                continue
            if (getattr(record, 'role', '') == 'system'
                    and not getattr(record, 'is_head', False)
                    and getattr(record, 'kind', '') in ('notice', 'handoff', 'message')
                    and getattr(record, 'text', '')):
                out.append({'kind': 'system-notice', 'who': 'host',
                            'at': time.strftime('%Y-%m-%dT%H:%M', time.localtime(record.ts_ms / 1000)),
                            'text': record.text, 'display_id': record.display_id})
                continue
            if str(getattr(record, "kind", "") or "") not in (
                    "message", "function_call", "function_result"):
                continue
            role = getattr(record, "role", None)
            if role not in (_ME, _OTHER, "assistant"):
                continue
            who = _ME if role == _ME else _OTHER
            stamp = getattr(record, "ts_ms", 0)
            # **2026-10-09 改成整串**（同一天 ）—— 原来这里折成 `HH:MM`
            # ⇒ 日期在**这一处也被丢掉** （ 这是**第三个**生产者 ）。
            # ⚠️ **它照旧不读钟** ： 只转**它自己带的**那个 `ts_ms` 。
            # ⚠️ **这一句 `at = …` 同样删不得** —— 同一天我在这一处也犯过一次
            # （ 换注释时把赋值一起吃掉了 ⇒ `NameError: name 'at' is not defined` ，
            # 而它**编译得过** —— 要到跑起来才炸 ）。
            at = (time.strftime("%Y-%m-%dT%H:%M", time.localtime(stamp / 1000))
                  if isinstance(stamp, int) and stamp > 0 else None)
            # **2026-10-10：工具那两条要走自己的类别** （见 `_A_TOOL` 那一段 ）。
            # ⚠️ 它们的 `role` 与**说话那一支一模一样** （都是 `assistant` ）
            # ⇒ 只能**按 `kind` 分** （ 那是这一仓区分"说了话"与"动了手"
            # 唯一现成的记号 —— 与 `_take_the_reply` 写下去的那两处同源 ）。
            kind = str(getattr(record, "kind", ""))
            ref = str(getattr(record, 'tool_ref', '') or '')
            display_id = (ref + ':' + kind if ref and kind.startswith('function_')
                          else str(getattr(record, 'display_id', '') or ''))
            if not display_id:
                import hashlib
                display_id = 'legacy:' + str(ordinal) + ':' + hashlib.sha256(repr(record).encode()).hexdigest()
            display = {'display_id': display_id}
            if kind in ("function_call", "function_result"):
                name = str(getattr(record, "name", "") or "")
                if name == _A_SPOKEN_OUT_LOUD and kind == "function_call":
                    said = _the_text_she_said_out_loud(getattr(record, "text", "") or "")
                    if said:
                        out.append({"kind": _A_MESSAGE, "who": who, "at": at,
                                    "text": "拟发送（未确认送达）：" + said, "delivery": "intent", **display})
                        continue
                out.append({"kind": _A_TOOL, "who": who, "at": at,
                            "tool": name,
                            "stage": "call" if kind == "function_call" else "result",
                            "text": getattr(record, "text", "") or "", **display})
                continue
            out.append({"kind": _A_MESSAGE, "who": who, "at": at,
                        "text": getattr(record, "text", "") or "", **display})
        notices = getattr(self, 'reminder_notices', None)
        if notices is not None:
            try:
                cards = notices.history(visible=self._reminder_notice_visible)
                if cards:
                    out = sorted([*out, *cards], key=lambda row: row.get('at') or '')
            except (OSError, ValueError, RuntimeError, __import__('sqlite3').Error) as error:
                print(f"   [reminder] 系统通知历史暂不可读（{type(error).__name__}）。", file=sys.stderr)
        return out

    def _a_consolidation(self, *, config: Any, transport: Any, generate: Any,
                 write: Callable[[str, str, str], Any] | None,
                 stamp: Callable[[], str],
                 read: Callable[[str], str] | None,
                 pending_read: Any = None, pending_settle: Any = None,
                 #: 那一本账**也落一行到哪儿**（2026-10-05，作者拍的"甲"）：`<root>/consolidation.jsonl`。
                 #: `None` ⇒ 只留内存那一本（旧行为 ）。
                 root: Any = None) -> Any:
        """ **装那一趟整理态**（`consolidate.py`）—— 没递 `write` ⇒ **不装**（而那是一句准话）。

        ⚠️ **`write` 就是"它那扇门"**（`web.PersonaPort.save_hers_text`）—— 由**页面那一侧**
           递进来，因为"哪一份文件归谁写"那条守卫住在 `web.py`，而这一层**不许**再写第二份
           （`02:127`：两处各写一份 ＝ 两处各答一个数）。
           ⇒ 于是**这个入口（终端那一半）今天装不出梦**：它手上没有那条守卫。
           ⚠️ **那一笔是点着名的，不是静默的**：见 `_DREAM_OWED_BY`。
        ⚠️ **`read` 与 `write` **同一个来源**（也是页面那一侧）**: 它把那两份**读回来**
           摆进那一条指令 —— 这一格是**第二次真跑补上的**（没有它，`自述` 交回来的
           是一句"我原样交回"，而那一句会真的变成那一份的正文）。
           ⚠️ **`read` 没递 ⇒ 装出来的梦一趟都不跑**（`Dream.schedule` 当场回假）：
               "读不到它要改的东西却照样改"是瞎写 —— 而它比"这一趟没跑"坏得多。
        ⚠️ **`generate` 不递就用本仓那一个**（与 `run_round` 那一格**同一个**：
           `generate_itself`）—— 梦**不是另一个调模型的口**。
        ⚠️ **`inject` 接的是本层那一个**（`_inject_a_note`）：浮现那一条走**同一条总线**、
           `source="consolidate"`（不是写进会话 —— 理由见 `_a_handoff` 那一段 ④）。
        ⚠️ **`log` 落在 `self.consolidation_log`**（一本**公开、追加、只增不减**的账）：
           这一趟是**后台**的、没有人 `await` 它的结果 ⇒ "它办砸了"必须有**一处机器看得见**
           的地方（`dispatch.receipts` 那条成法）。 一组判据读它
           （`tests/test_consolidate.py`：空跑保护 ／ 退一次 ／ 办砸了不写半份）。
        """
        if write is None:
            return None
        from .consolidate import CONSOLIDATE_KIND, Consolidation
        from .generate import generate as generate_itself

        self.consolidation_log: list[tuple[str, dict]] = []

        # 那本账的**落点**（作者 2026-10-05 拍的"甲"）：`<root>/consolidation.jsonl`。
        ledger_path: Path | None = None
        if root is not None:
            base = Path(root)
            try:
                base.mkdir(parents=True, exist_ok=True)
                ledger_path = base / "consolidation.jsonl"
            except OSError:                              # noqa: BLE001 —— 建不出来就只留内存那本
                ledger_path = None

        def log_one(event: str, detail: dict) -> None:
            self.consolidation_log.append((str(event), dict(detail)))
            # **也落一行到文件**（2026-10-05，作者拍的"甲"）：那本账在**内存**里 ⇒
            # 从外面**看不见**"它跑了没有、办砸在哪一步" ⇒ 于是它同时写一行到
            # `<root>/consolidation.jsonl`（与事件库分开 —— 不偷它的语义 ）。
            # ⚠️ **写不进去不许静默** ：那就把"写不进去"这件事**记进那本内存账**
            #    （看得见 ⇒ 与 `dispatch.receipts` 那条成法同源）。
            if ledger_path is None:
                return
            line = json.dumps({"ts": str(stamp()) if stamp is not None else "",
                               "type": str(event), "source": "core",
                               "text": json.dumps(dict(detail), ensure_ascii=False)},
                              ensure_ascii=False)
            try:
                with open(ledger_path, "a", encoding="utf-8", newline="") as handle:
                    handle.write(line + "\n")
            except OSError as exc:                       # noqa: BLE001 —— 见上：不静默
                self.consolidation_log.append(
                    ("consolidation-log-unwritable", {"err_class": type(exc).__name__}))

        async def inject(text: str) -> None:
            from .continuation import current_job
            if getattr(self, 'wake_policy', None) is not None:
                if not self._queue_continuation(text, 'consolidate', current_job.get()):
                    # Keep the notice as context even if its old job can no longer wake.
                    self._inject_a_note(text, CONSOLIDATE_KIND, 'internal', source='consolidate')
            else:
                self._inject_a_note(text, CONSOLIDATE_KIND, 'internal', source='consolidate')

        # ★ 2026-10-17 批②：**草稿与提交账**落在 `<root>/consolidation/` ——
        #   中断之后靠它续上（`Consolidation.recover`）。`root` 没有 ⇒ 不存草稿
        #   （判据里那一档；那种情况下提交仍然带版本检查，只是没有草稿可续）。
        return Consolidation(config=config, transport=transport, stable_prefix=True,
                     prefix_of=lambda context: (self._consolidation_prefix.prepare(context, lambda: self._consolidation_witness(review=True))
                         if hasattr(self, '_consolidation_witness') else None),
                     generate=generate_itself if generate is None else generate,
                     write=write, stamp=stamp, read=read, inject=inject, log=log_one,
                     pending_read=pending_read, pending_settle=pending_settle,
                     drafts=(None if root is None else Path(root) / "consolidation"))

    def _inject_a_note(self, text: str, kind: str, origin: str, *, source: str) -> None:
        """ 把一条注入**排进总线**（不是写进会话）—— `_a_handoff` 那一段 ④ 那条契约。

        ⚠️ **它与"写进会话的消息表"是两条路**：后者会在 `reset` 那一刻被冲掉，而那是**静默的**
           （`handoff.py` 抬头 ④）。⇒ 注入一律走 `bus.push`。
        ⚠️ **一件东西只有一个造法**：那一件的形状**借 `_line_to_event` 造**
           （`WakeItem = EventEnvelope` 那个实物），再把三样换成注入的。
        ⚠️ **`source` 由调用方给**（交接笔记是 `"handoff"`，整理态那一条是 `"consolidate"`）——
            日志里要分得开这两件事；合成一个就再也分不开。
        整理浮现始终随附，包括旧任务续接失效后的兜底；其他宿主通知立即入队。
        """
        from dataclasses import replace

        self.cursors += 1
        event = replace(_line_to_event(text, cursor=self.cursors),
                        type=str(kind), source=str(source), origin=origin, sender_key=None)
        self._bind_host_wake(event)
        # Emergence is an unverified observation, including expired-lease fallback.
        ingress = getattr(self, "activity_control", None) or self.bus
        if source == "consolidate":
            ingress.push(event, trigger="piggyback")
        else:
            ingress.push(event)

    def _inject_owner_follow_up(self, text: str, *, source_key: str, source_ms: int) -> None:
        """Queue a host-owned console tick, never a synthetic external message."""
        from .owner_tick import MARKER
        self._inject_owner_tick(text, source_key=source_key, source_ms=source_ms, marker=MARKER)

    def _inject_owner_reminder(self, text: str, *, source_key: str, source_ms: int) -> None:
        """A reminder is independent of the unanswered-speech follow-up rule."""
        from .owner_tick import REMINDER_MARKER
        self._inject_owner_tick(text, source_key=source_key, source_ms=source_ms, marker=REMINDER_MARKER)

    def _inject_owner_tick(self, text: str, *, source_key: str, source_ms: int, marker: str) -> None:
        from dataclasses import replace
        from .owner_tick import OWNER_SCOPE, route
        event = replace(_line_to_event(text, cursor=self.cursors + 1),
                        type="tick", source="heartbeat", origin="internal", sender_key=None,
                        meta={marker: {"scope": OWNER_SCOPE, "source_key": source_key,
                                       "source_ms": source_ms}})
        if route(event) != OWNER_SCOPE:
            raise ValueError("owner_follow_up_binding_invalid")
        self.cursors += 1
        self._bind_host_wake(event)
        (getattr(self, "activity_control", None) or self.bus).push(event)

    def _a_handoff(self, *, session: Any, root: Path, head: Any,
                   prefix: Sequence[Any] | None, persona_root: Any,
                   config: Any, consolidation: Any = None,
                   channel: Any = None, channel_context: Any = None,
                   channel_scene: Any = None, scoped_context: bool = False,
                   fixed_rule: Callable[[], Any] = lambda: None,
                   observed_pressure: Callable[[], int] = lambda: 0) -> Any:
        """装配交接的快照、笔记、重建、持久化及通知回调。

        整理态取得重建前的请求快照。先保存笔记文件，再原子提交前缀与范围笔记。
        启用范围过滤时，正文按原始会话范围独立渲染并共用一个预算池；
        这些记录随重建一起持久化，避免无收件人的注入事件被私聊视图过滤。
        账号关联由读取时判定，不预先把不同账号的内容合成一份。
        唤醒与压力通知仍走总线；无范围过滤的独立装配同样持久化笔记。
        """
        from .handoff import Handoff, HandoffPolicy
        from .media_budget import estimate_request_tokens
        from .note import build as note_build, is_never_in_a_note
        from .tokens import estimate_messages_tokens, estimate_tokens

        request_head = _a_head_cell(head, persona_root, session, facts=None,
                                   pull_in=None, channel=channel, channel_context=channel_context,
                                   channel_scene=channel_scene)
        def a_head_cell():
            # Handoff is host maintenance, not a new channel request. Its shared
            # prefix must remain readable after the preceding batch lease retires.
            if head is None and (callable(channel) or callable(channel_context)):
                fresh = _the_prefix_head(persona_root, include_channel=False)
                return [] if _in_the_archive_already(session, fresh) else fresh
            return request_head()

        def batch_pressure():
            archive = session.outbound(a_head_cell())
            rule = fixed_rule()
            extra = (rule,) if rule is not None and rule not in archive else ()
            source = self._context_pressure(archive, config, fixed_tail=extra)
            foreground = observed_pressure()
            if foreground:
                pressure = max(source, foreground)
                self.handoff_pressure_last.update(
                    foreground_source_tokens=foreground, estimated_tokens=pressure)
                return pressure
            return source

        def can_relieve_pressure(limit):
            # A fresh archive still needs the persona, host policy and enabled
            # tool declarations. Do not reset history to chase their own cost.
            rule = fixed_rule()
            if rule is None:
                return True  # No verified foreground request cost on this route.
            fixed = session.outbound(a_head_cell(),
                                     visible=lambda row: row.is_head and row.role == 'system')
            if rule not in fixed:
                fixed.append(rule)
            return estimate_request_tokens(fixed, config) < limit

        handoff_job = [None]

        async def snapshot() -> Any:
            """① 那一格 —— **顺带把整理态排上**（`consolidation.schedule`，见下面那一段）。

            ⚠️⚠️ **为什么排在这里**：这一刻**正是**"交接前那一份上下文"—— 而交接的第 ④ 步
               （`session.reset`）会把它换掉 ⇒ **晚一步就抓不到了**（`consolidation.schedule` 的抬头
               逐字写着这一条）。 那也正是参考实现的位置（梦在 `onHandoff` 里排）。
            ⚠️ **递的是"线格式那一份"**（`session.outbound(a_head_cell())`），不是 `Record` 那
               一份：梦要的是"**发出去的那张表**"，而 Record ⇒ 线格式那一步**只有一处实现**
               （`session._render_message`）⇒ 在这里另写一遍就是第二个答案。
            ⚠️ **它不 `await`**（`schedule` 自己就是"排上就走"）：交接照旧一路走到底。
            ⚠️ **`consolidation is None` ⇒ 一个字都不做**（终端那半没有那扇门，见 `_a_consolidation`）。
            """
            from .continuation import capture, current_job
            records = session.records()
            manual = getattr(self, '_manual_handoff_job', None)
            if manual is not None and not manual.valid(self, _now_ms()):
                manual = None
            self._continuation_epoch = getattr(self, '_continuation_epoch', 0) + 1
            self._manual_handoff_job = None
            # A local handoff does not borrow the identity of the latest QQ sender.
            scope = manual.scope if manual is not None else None
            job = capture(self, _now_ms(), scope=scope)
            handoff_job[0] = job
            token = current_job.set(job)
            try:
                if consolidation is not None:
                    consolidation.schedule(session.outbound(a_head_cell()))
            finally:
                current_job.reset(token)
            return records

        scoped_handoff_records = []
        handoff_note_id = [None]

        async def note(records: Any, **kw: Any) -> Any:
            from uuid import uuid4
            from .note import Record, HANDOFF_NOTE_KIND
            from .record_history import current_references
            path = getattr(session, 'path', None)
            if path is not None:
                references = current_references(path, records)
                kw = {**kw, 'evidence_layout': True,
                      'reference_of': lambda row: references.get(id(row), '')}
            handoff_note_id[0] = "handoff:" + uuid4().hex
            scoped_handoff_records.clear()
            if scoped_context:
                # Keep each original audience separate. Account linking is checked
                # at read time so revoking a link also revokes its note contents.
                scopes = sorted({row.scope for row in records
                                 if row.scope.startswith(("private:", "group:"))
                                 and row.scope.partition(":")[2]
                                 and not is_never_in_a_note(row)})
                # A linked-account view can contain several scopes. Share the
                # original note pool rather than multiplying it per audience.
                pool, remainder = divmod(kw['budget_tokens'], max(1, len(scopes)))
                for ordinal, scope in enumerate(scopes):
                    options = {**kw, 'budget_tokens': pool + (ordinal < remainder)}
                    parts = note_build([row for row in records if row.scope == scope], **options)
                    for index, part in enumerate((parts.earlier, parts.recent)):
                        if part:
                            scoped_handoff_records.append(Record(
                                kind=HANDOFF_NOTE_KIND, role="user", text=part,
                                scope=scope, is_flow=True, ts_ms=_now_ms(),
                                display_id=f"{handoff_note_id[0]}:{scope}:{index}"))
            return note_build(records, **kw)

        async def build_prefix() -> list[Any]:
            """③ 那一格 —— **递了就用递的；没递就现装**（`prefix.assemble`）。

            ⚠️ **缺省是 `None`，不是空那一串**：空那一串是"调用方明确不要前缀"，
               而 `None` 是"没人递过 ⇒ 现装"—— 两件事，一个值分不开（见 `assemble()`）。
            ⚠️ **`ts_ms` 在**这里**盖**（本层唯一那个毫秒钟）：装订器一次都不读时钟。
            ⚠️ **根由调用方递**（`prefix.assemble(root=…)`）：装订器不认识"那条根在哪"，
               它认的名字是**相对那条根**的。
            ⚠️ **`doc` 那一格是**现读**的**（不把它绑成默认实参）：默认实参在**定义时**
               求值 ⇒ 测试里换掉 `prefix.SEGMENTS_DOC` 就换不动了。
            ⚠️ **这里不吞异常**：正本读不到 ⇒ 它当场抛 ⇒ 这次交接**在换掉会话之前**
               停下来（那就是这一格的形状，照旧是对的）。
            ⚠️⚠️ 固定 `channel` 必须与 `_the_prefix_head` 同值 ——
               （2026-10-10 加）**这一条不是风格，是能不能成立的问题**：
               · ④ `reset` 之前，**这一句**会被写进会话当开头那一条 `system`；
               · 而此后**每一次请求**那句前缀是由 `_the_prefix_head` **现装**的；
               · 两者**不同值** ⇒ 盘上开头那条 ≠ 现装那条 ⇒ `_in_the_archive_already`
                 比"逐字相同"就**对不上** ⇒ **那一行在请求里出现两遍**
                 （正是 `_in_the_archive_already` 那条判据防的形状，本机实测过 16553 字节 × 2）；
               · 更要紧的是**内容**就错了：交接之后，接下来那些话会带着**另一头**的通道段 。
               ⇒ 所以固定通道两处收的是**同一个**入参。
               动态来源是另一种契约：这里只归档共同前缀，请求端另附当前场景。
            """
            if prefix is not None:
                return list(prefix)
            from . import prefix as prefix_module

            return prefix_module.assemble(_the_persona_root(persona_root),
                                          doc=prefix_module.SEGMENTS_DOC,
                                          ts_ms=_now_ms(),
                                          channel=None if callable(channel) else channel,
                                          include_channel=not (callable(channel)
                                                               or callable(channel_context)))

        async def reset(records: Any) -> None:
            session.reset(records)

        async def commit(records: Any, text: str) -> str:
            if not text:
                session.reset(records)
                return ""
            path = root / "handoff-note.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            import os
            import tempfile
            fd, temporary = tempfile.mkstemp(prefix='.handoff-note-', dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            from uuid import uuid4
            from .note import Record
            from .owner_tick import OWNER_SCOPE
            stamp = _now_ms()
            marker = Record(kind="ui_handoff_note", role="system",
                            name=f"handoff_note:{self.batches}",
                            text=text, ts_ms=stamp, scope=OWNER_SCOPE, is_flow=True,
                            display_id=handoff_note_id[0] or "handoff:" + uuid4().hex)
            # Prefix and scoped notes share one atomic session replacement.
            # A crash leaves either the old context or the complete new context.
            durable_notes = list(scoped_handoff_records)
            if not scoped_context and text:
                durable_notes.append(Record(kind="handoff_note", role="user",
                    text=text, ts_ms=stamp, is_flow=True,
                    display_id=marker.display_id + ":context"))
            session.reset([*records, *([marker] if text else []), *durable_notes])
            self._display_batch_base = self.batches
            if self.write is not None:
                try:
                    self.write(self._handoff_note_frame(marker))
                except Exception as error:
                    # UI delivery cannot turn a durable commit into a retry.
                    print(f"[handoff] committed; UI notification failed: {type(error).__name__}",
                          file=sys.stderr, flush=True)
            return str(path)

        def inject(text: str, kind: str, origin: str) -> None:
            """ 排进总线 —— **本刀把它提到方法上了**（`_inject_a_note`）。

            ⚠️ **为什么提**：整理态那条浮现**也要走同一个口**（`source="consolidate"`），
               而它住在本类外面（`_a_consolidation`）⇒ 一个闭包只有 `_a_handoff` 看得见。
                两处各留一份就是"注入的造法"第二个答案 —— 而那件事的次序与档位规则
               只写在 `_inject_a_note` 上。
            ⚠️ **这里只补一个 `source`**（`"handoff"`）⇒ 本函数的签名与行为逐字不变。
            """
            self._inject_a_note(text, kind, origin, source="handoff")

        async def inject_internal(text: str, kind: str) -> None:
            inject(text, kind, "internal")

        async def inject_external(text: str, kind: str) -> None:
            # Notes are already durable; do not enqueue a duplicate copy.
            return None

        def handoff_log(event, detail):
            if event == 'completed' and handoff_job[0] is not None:
                job, handoff_job[0] = handoff_job[0], None
                self._queue_continuation(
                    '交接已完成。请根据交接笔记与当前状态，自主判断是否继续未完成的话题或工作；'
                    '这不是用户新消息，无需重复回答已经完成的问题，也不要求发言。', 'handoff', job)
            from .timers import _now_iso
            draft = {"type": "handoff-" + str(event), "ts": _now_iso(),
                "source": "core", "origin": "internal", "text": "",
                "meta": {k: v for k, v in detail.items()
                         if k in ("estimate", "hard", "soft", "error_type",
                                  "split_at_ms", "reason")}}
            if event in ('pressure', 'started'):
                draft['meta'].update(getattr(self, 'handoff_pressure_last', {}))
            if getattr(getattr(self, "app", None), "store", None) is not None:
                self._record_event(draft)
            else:
                # Directly assembled terminal/test integrations have no EventStore.
                print("[handoff] " + json.dumps(draft, ensure_ascii=False), file=sys.stderr)


        return Handoff(
            log=handoff_log,
            automatic_handoff_allowed=lambda: any(
                not is_never_in_a_note(record) for record in session.records()),
            snapshot=snapshot, note=note, note_estimate=estimate_tokens,
            timestamp_of=lambda record: record.ts_ms,
            build_prefix=build_prefix, reset=reset, write_note=lambda text: commit([], text),
            commit=commit,
            inject_internal=inject_internal, inject_external=inject_external,
            estimate=batch_pressure,
            can_relieve_pressure=can_relieve_pressure,
            # **上下文上限没有 ⇒ 硬阈也「没有」**（2026-10-05 作者裁定）——
            # ⚠️ 交接层对 `None` 的语义是「**不据此限制输入**」（`handoff.py:353`）；
            # 而 `context_window - max_tokens` 在 `None` 时会当场抛 ⇒ 这一格先判 。
            hard_tokens=lambda: hard_input_tokens(
                config.context_window, config.max_tokens, config.base_url),
            # 2026-10-05：**软阈那三个数接上同一个窗口** —— 从前这一格不传，
            #    `HandoffPolicy` 就用它写死的 `max_tokens = 64000` ⇒ 软阈 54400 与硬阈
            #    `context_window − max_tokens` 各说各的：窗口小的时候**硬阈先到**，
            #    那句「交接快来了」永远发不出，直接硬交接（静默）。
            #    ⚠️ 递的是**装配那一刻的窗口**（`config` 是钉住的那一份）；
            #       而硬阈那一格**故意读活视图**（见上面 `run_round` 那条注释）。
            #       ⇒ 换窗口那一轮，软阈与新窗口**下一轮**才对齐 —— 与硬阈同一条纪律。
            #    见 `handoff.HandoffPolicy` 的抬头（那里写着接上之后的前提）。
            policy=HandoffPolicy(context_window=config.context_window),
            policy_of=lambda: HandoffPolicy(context_window=config.context_window, allow_unknown_pressure=False),
            now_ms=_now_ms)

async def main(root: Any = _DEFAULT_ROOT, *, on_assembled: Any = None,
               prepare_plugin_media: Any = None,
               runtime_evidence: Any = None, evidence_stop_timeout: float = 5.0,
               record_runtime: bool = False,
               evidence_finalizer: bool = True,
               **handed: Any) -> int:
    """ **那个入口的协程那一半** —— 装 ／ 起 ／ 泵 ／ 打 ／ 停，一句话就是它。

    ⚠️⚠️ **它为什么是协程、而不是一个 `if __name__ == "__main__"`**：`asyncio.run`
       在全树**只许被调用一处**（`tests/test_boundary.py` 用 ast 钉着那一条，
       而那一处是 `cli.py:54`）⇒ "循环归入口所有"这条家规在本刀里没有变
       ⇒ 本模块只交出**那个协程**，起循环的那一句在**命令**里：

    ```
    .\\.venv\\Scripts\\python.exe -c "import asyncio,sys; from telyincore.console import main;
        sys.exit(asyncio.run(main()))"
    ```

    ⚠️⚠️ **从何处跑：仓根**（就是有 `.venv` 与 `telyincore/` 的那一层；本仓的仓根可与
       本模块所在的目录不同，跑之前先看一眼当前目录）。 理由是一条读得出来的事实：
       `root` 的缺省是 `_DEFAULT_ROOT`（`Path("data")`，**相对于当下工作目录**）
       ⇒ 在别处跑，`data/` 就会落在别处（会话 ／ 事件库 ／ 闹钟表一起搬走，
       而"上一次那段对话"看起来很"没了"）。
        而本波已经给 `data/` 加上了 `.gitignore` 那一行 —— 见那条的注释：**它遮的是
       "一个真跑过的工作目录"，不是"这个目录可以进仓"**（一段私人的对话 ／ 一个 key
       永远不该落进这棵树）。

    ⚠️ 这不是绕开那条纪律：`loop.py:7` 那句"循环归**入口**所有"说的是**谁持有那个循环**
       —— `cli.py` 今天持有着它，而本入口**被谁跑**由那一句决定（谁跑谁就持有它）。
    ⚠️ **`root` 是那一套状态的目录**（缺省 `data/`，见 `_DEFAULT_ROOT`）。

    ⚠️⚠️ **返回什么、以及为什么那个返回值**（ 本波改的就是这一句）：
       · **这一趟出过话**（`console.answered`）⇒ **`0`**；
       · **一句都没出过** ⇒ **`1`**。
        **为什么必须这样**：从前这里恒回 `0` ⇒ 一个**死掉的端点**那一趟是
         "**48.6 秒沉默 ＋ 一句答案都没有 ＋ `RC=0`**"（实测的形状）——
         于是 `RC=0` **证明不了任何事**：它把"跑通了"与"什么都没跑"在收据上**说成同一样**。
       ⇒ 诚实的判据是**打出来的那一句**（`[批 N]` ／ 你那一行 ／ 模型答的那一句），
         而退出码从此跟着**同一件事**走（"模型出过话没有"）。
       ⚠️ **`1` 是"这一趟没成"，不是"哪里炸了"**：异常照旧往上走、照旧带自己的型
         （这一层不把一条真故障压成一个数字）；这个 `1` 只说"一句答案都没出过"。
       ⚠️ 用法照旧：那一条命令 `sys.exit(asyncio.run(main()))`（与 `cli.main` 同一个用法）。

    ★★ **2026-10-12（作者：「要」）：那几样今天也递得进来了** ——
       从前这一句是 `console.assemble(root=root)`：**只递 `root`** ⇒ 从这条路跑起来，
       它**一件工具都没有**（工具 ／ 事实 ／ 拉进 ／ 通道 ／ 整理态那几样一个都没人递），
       而"梦"也装不出来。今天它把**外面递进来的**（`**handed`）**原样转交** `assemble` ——
       而"外面"是 `launch --console`（那一层才知道那几样怎么造：它把与页面那条路
       **同一套**递给这里）。
       ⚠️ **不递 ⇒ 逐字节与从前一样**（这条路照旧跑得起来，只是没有那几样）——
       与 `assemble` 那几格"缺省 ⇒ 一个字都不变"同一条成法。
    """
    from .runtime_evidence import observed_lifecycle, select_owner
    if type(evidence_finalizer) is not bool or (record_runtime and not evidence_finalizer):
        raise ValueError('created evidence requires lifecycle owner')
    runtime_evidence = select_owner(root, runtime_evidence, record_runtime, stop_timeout=evidence_stop_timeout)
    if prepare_plugin_media is not None and not callable(prepare_plugin_media):
        raise TypeError('callable native media preparation required')
    async def operation():
        console = Console()
        # The request resolver is constructed during assemble, before on_assembled.
        if prepare_plugin_media is not None:
            console._prepare_plugin_media = prepare_plugin_media
        console.runtime_evidence_owner = runtime_evidence
        if runtime_evidence is None:
            console.assemble(root=root, **handed)
        else:
            console.assemble(root=root, runtime_evidence=runtime_evidence, **handed)
        if on_assembled is not None:
            on_assembled(console)
        await console.run()
        return 0 if console.answered else 1
    if not evidence_finalizer:return await operation()
    return await observed_lifecycle(runtime_evidence, operation,
                                    stop_timeout=evidence_stop_timeout)
