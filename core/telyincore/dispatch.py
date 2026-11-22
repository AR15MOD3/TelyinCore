# telyincore/dispatch.py
"""派发 —— 把模型要调的那些调用**真的办掉**：规格 6.4 的七步 ＋ 6.5 的六句回执 ＋ 配对账。

```
① 参数校验（非法 JSON → 就地失败，不进 handler）              ← `stage="validate"`
  → ② 工具名在不在本 session 的表里                          ← `stage="name"`
    → ③ barrier_after / ends_turn                          ← `stage="barrier"`
      → ④ 区域权限检查（它能不能写这块）                       ← `stage="area"`
        → ⑤ effect 路由（immediate 落盘 / pending 进待确认队列） ← `stage="effect"`
          → ⑥ handler                                      ← `stage="handler"`
            → ⑦ 回执                                        ← `stage="receipt"`
```

 **`stage` 是这一层给的那一位**：七步**各有自己那句回执**（6.5 那张表），而"哪一步定的案"
  只有这一层说得清 —— 尤其是**第 2 步与第 4 步**（见下面那条 ⚠️ 与 `UNKNOWN_TOOL`）。

 **"压根没办"与"办了但办砸了"是两族**（规格 6.5 的整句理由）：那一族的分法**长在
   `markers.py` 里**（"没办"是冻住的常量；"办砸了"一律经 `tool_failed(detail)`），
   而**这一层负责给每种情况挑对那一句** —— 挑错一句，模型就分不清那两件事了。
   结构上还有一位：**`stage == "handler"` 当且仅当执行段被进过**
   （没执行的那几条路：`validate` ／ `name` ／ `barrier` ／ `area` ／ `effect`）。

 **配对账（⑤）就住在这里** —— `round.py:103` 的裁定：`call_id` **不写进会话**
   （会话不承载配对键）⇒ **三点式**：一批调用进来**记上**（`record`）／
   收到回执**划掉**（`acknowledge`）／关机时还挂着的**补 `SHUTDOWN_INTERRUPTED`**（`on_shutdown`）。

⚠️⚠️ **我们没见过 `function_call` 的真形状** —— 引擎从没发过工具调用（`stream.py` 里那两个
   事件名是照上游文档写下来、**只为让流不在这里断掉**的），而 `ToolCall` 那三个字段
   **都有默认空串**。⇒ "**缺了的那一半算不算一个完整的调用**"（`stream.py:115-116` 点名给
   这一层）是**按规格判**的、**不是按观察判**的：缺 `call_id`（回执对不上 —— 会话里没有那个键）
   或缺 `name`（不知道跑什么）⇒ `NOT_EXECUTED_INCOMPLETE`，**不执行**；
   参数不是合法 JSON ⇒ `TOOL_FAILED_BAD_ARGS`，**不进执行段**。
   ⚠️ **四条边界都还没在真流上验过**（`stream.py:85-86` 记的那三次代价，说的就是
      "给没见过的形状定契约"）：① 只来 `added`（没有 delta）／② 带完整 `arguments`
      又发 delta ／③ 三字段全空 ／④ **有名字而 `arguments` 是空串** ⇒ 今天判成
      `TOOL_FAILED_BAD_ARGS`、**不进执行段**（判据是"一个不收参数的工具，上游至少该给
      `{}`" —— 这一层**不替它补**："没给"与"不收参数"是两件事，同 `ToolSpec.parameters`
      那条：`None` 不等于 `{}`）。
       **2026-10-11 观测到了**（ 直连引擎、带一个**不收参数的工具** ）：
         · `item` 头一次出现时 `arguments` 是 **`""`** （ 那是**初始值** ）；
         · `delta` ／ `arguments.done` ／ 终态那三块**全是 `{}`** ；
         ⇒ 本仓那个累加器（`stream.py:_note_arguments` ：`delta` 往里加 、
           `done` 与累加值**若都有才比** ）⇒ 得到的是 **`{}`**
           ⇒ **一个不传参数的 `finish_turn` 收得了工** —— 这一段原来的担心**不成立** 。

⚠️ **今天谁欠什么**（`loop.py:60-63` 那条家规：不许让一个东西静默无效）：

```
· **`loop.py` 那个槽接上了**（接线那一刀）：`app.build()` 把 `make_run_tools(dispatcher)`
  递进 `loop.run_tools` —— 那条链是 `loop` 那个槽 ⇒ 这一份的 `run` ⇒ 七步 ⇒ 六句回执。
   ★ **2026-10-13 订正**：那句「生产里仍然没有一个调用点」**只对了一半** ——
   那个**槽**确实没人调（`loop.py` 的 `_run_tools` 一次都没被调用过），
   而**工具真的会跑**：装配那一层把 `run_tools` 递进 `make_run_round`，
   而 `round.py` 在那一轮把手交出去（`console.assemble` ⇒ `round.run_round`）。
   缺的那一步照旧是「把 `Result.calls` ⇒ `run_tools(calls)` ⇒ `RoundOutcome`」，
   见 `_WIRING_OWED_BY`（**那一段更新过**，它现在说的是「那个槽仍然是死的」这件事本身）。
· **回执交回来之后归谁**：`run` 那一趟的 `DispatchResult` 落在**这一部派发器自己的账**上
  （`Dispatcher.receipts`）—— 一处机器看得见的地方，**不是**静默丢掉。 而"把它送回模型"
  那一条路**已经接上了**（2026-10-05 改；原先这里写的是"今天没有 "）：
  `round.py` 写 `function_result` ⇒ `session.py` 按 `call_id` 渲染成 `function_call_output`
  ⇒ `generate` 带着它发 。见 `_RECEIPT_OWED_BY`（⚠️ 而它只在**递了 `run_tools` 的那一趟**
  上成立 —— 生产那边还没递）。
  ★ **2026-10-12 订正（作者：「落进事件库」）**：那本账**从今天起有一个读者了** ——
  入口把那一趟的回执**落进事件库**（`launch.the_receipts_into_the_ledger`，一条
  `type="tool-receipt"` 的事件，`meta` 里带着逐条的名字与阶段）。
  ⚠️ 于是"只写不读"那一句**不成立了**；而它落的是**框架自己那份审计** ——
  与上面那条"送回模型"是**两件事**（那一份进请求，这一份进事件库）。
· **★ 2026-10-13 订正：执行段不是 13 条了** —— 今天表里 **20 条**（`tools.py` 那 20 行
  ＋ 「它自己决断」那一支笔），而「全绑上了」那句口径照旧（有 handler 的都绑，
  没有的走那条点名接缝 —— 那一段下面写着）。
  ⇒ 那条"点名接缝"的路（`NotImplementedError(f"{name} 还没接上 —— 它等 {who}")`）**今天没有一条**
  会走到 ； 而**第 6 步照旧接住真出错的那一些**（记成 `tool_failed(…)` —— 那条规矩没变）。
  ⚠️ `_HANDLER_OWNERS` 那张表**照旧留着**：它现在的活儿是**逐条记"它接在哪"**
  （ 而"谁欠它"那几格该跟着改成"已经接上了" —— 已经改了一部分 ）。
· **区域权限那一步今天没有规矩**（`permissions=None` ⇒ 放行）。见 `_PERMISSION_OWED_BY`。
  ★★ **2026-10-12 作者拍了：那一步「留给插件」** ⇒ 于是它不是"漏了一块"，是**定的**：
  今天**不去**发明一套区域规矩（6.2 那六个标签的语义本来就随插件走），
  谁做插件时把它接上 —— `Dispatcher(permissions=…)` 那个口一直留着。
· **待确认队列由插件提供**（6.3）。★★ **2026-10-12 订正**（这一条原先写的是"没递就
  **当场点名**：那一句从 `run` 里逃出去" —— 而它带来的是下面那一件更坏的事）：
  · **递了队列** ⇒ 那条提议交给它、这一趟**不定案**（6.4 第 ⑤ 步；这一支**原样不动**）；
  · **没递队列**（插件可以缺席）⇒ **执行段照跑**：把它**记下来**（记录那半归记忆层 ——
    就是那一行的 handler，`memory.remember_pending` ⇒ 落「悬着的事」），回执照发；
    **"放行"那半照旧没有** ⇒ 那是"**没人放行**"，不是"装配缺了一块"
    （`docs/superpowers/notes/2026-10-05-\u5979写的那几扇门归谁.md` §三 ③ 那一句
    "插件缺席时：**记录还在**，只是'没人放行'"说的正是它 —— 那一句的原文里还带着
    那一格**当时的名字**，这里按本节那条边界规矩不抄它）。
  ⚠️⚠️ **订正的理由（本机量到的，不是审美）**：那一版缺队列时**当场抛**，而那一抛
    ① 从 `run` 里逃出去（原先的入批检查排在记账之前）② `round.py` 那一层**不咽它**
    （它自己的注释写着"那颗异常冒出去**就是把整批带走**"）③ `loop.run()` **没有 except**。
    ⇒ ★ **它只要调那两件提议里任一件，那一整批（连同同一批里别的工具）全废**，
    连"它提过这条建议"这件事都不会留下 —— 而这两件是**发到它手上**的
    （有 handler ⇒ `launch.py` 那一边照发）⇒ 那是一个"看得见、一碰就炸"的雷。
    ⚠️ 而它**不是**"降级成静默"：记录是**真的发生**（handler 交回的那句话就是回执），
    缺的那一样仍然点着名（`_PENDING_QUEUE_OWED_BY` ／ `_CONFIRMATION_OWED_BY`）。
· **一条提议被确认并执行之后，这笔账怎么了**：这一层自己只在**定案**与后面那四条收尾里
  划账，而 `run` 对提议那一趟交回 `None`（第 5 步不定案）⇒ 一条**办完了**的提议会一直挂在
  账上，直到关机被补一句 `SHUTDOWN_INTERRUPTED`（而它的结果其实回来了）。见 `_CONFIRMATION_OWED_BY`。
· **进了那个队列的那条调用这一趟没有回执**：它的结果**还没回来**（那正是配对账要记的形状）。
   ⚠️ 而**没递队列**时它走执行段 ⇒ **有回执**（"记下了（悬着的事）：…"）——
   ★ 那一条比"什么都没有"更该有：一条**没有回执**的调用会在下一趟请求里留下一个
   **没有结果**的 `function_call`（那一半的账见 `_REQUEST_OWED_BY`）。
   而"提议等着的时候模型看见什么"**不在发请求那一层了** —— 它现在**发**（`body["tools"]` ）：
  剩下那一半归 `round.py` ／ `session.py`（见 `_REQUEST_OWED_BY` ／ `_RECEIPT_OWED_BY` ）。
· **四条收尾今天都还没有生产调用方**（流断 ／ 主循环停 ／ 关机 ／ `repair`）：三条有名有姓的
  各声称一个原因，而 `repair()` 是**说不上原因**时的兜底 —— 今天调它们的是**测试**
  （那是在证明这四句话各造得出来），**不是**生产。⚠️ 它们不是各自欠一个人：
  这一份整体还没有生产调用方，见 `_WIRING_OWED_BY` —— 谁接上那一刀，谁就把这四条路接上。
· **6.6 超长只记 warn、一个字都不截**（`LARGE_RESULT_WARN_CHARS`）：体积归插件管。
```

⚠️ **一处判断（已上报：报告 §5）**：规格 6.4 第 4 步（区域权限）在 **6.5 那张表里没有
   自己那一行**，而记号表是个**闭集**（本刀只许建 `dispatch.py`，不许往 `markers.py` 加记号）
   ⇒ 这里**不发明**记号：看不见的工具与不许用的工具，对模型是**同一句** `UNKNOWN_TOOL`
   （"这个工具在你这里不可用"），区别在**哪一步拒的** —— 那一位在 `stage` 上（`"name"` ／ `"area"`）。
   代价写在那一行上：**模型看不到"为什么不许"**。
   ⚠️⚠️ **而那一行上的漂移就在这里点名**：`markers._CONSUMERS["UNKNOWN_TOOL"]` 写的是
   「`dispatch.py` 第 2 步」—— 而**第 4 步也产它**。那一份是**逐字节冻住**的（本刀不改它），
   ⇒ 它是一条**指针**、不是一句谎：**准确的那一句在这里**（第 2 步 ／ 第 4 步都产它，
   区别在 `stage`）。谁按那张表读代码，要知道这一格指过来的是**两个**第几步。
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future as ThreadFuture
import inspect
import json
import warnings

from .async_tools import Invocation, ManagedAwaitable, current_invocation, current_tool_references
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Generator, Mapping, Sequence

from .markers import (
    MISSING_RESULT,
    NOT_EXECUTED_BARRIER,
    NOT_EXECUTED_INCOMPLETE,
    NOT_EXECUTED_LOOP_STOPPED,
    NOT_EXECUTED_STREAM_ABORTED,
    SHUTDOWN_INTERRUPTED,
    TOOL_FAILED_BAD_ARGS,
    UNKNOWN_TOOL,
    tool_failed,
)
from .stream import ToolCall
from .qq_text_anchor import BoundPrivateText
from .tools import ToolDef, ToolTag

#: 规格 6.6 那条线：**超过它只记 warn，一个字都不截**（"体积归插件管 —— 模型检索返回 3 条
#: 还是 30 条，只有它的记忆系统知道；框架替你砍，只会砍错"）。
LARGE_RESULT_WARN_CHARS = 8_000

#: 规格 6.4 那七行 —— **七步的名字与次序就是它**（次序是承重的：两两相撞时谁先谁后，
#: `tests/test_dispatch.py` 里逐对钉着）。
#:
#: ⚠️⚠️ **它不是 `Receipt.stage` 的取值表**（这里原先就那么写的 —— 那句话对其中**一格**
#:    是假的）：第 5 步（`effect`）**不定案**（提议进队列，`_dispatch_one` 交回 `None`）
#:    ⇒ **没有任何一条 `Receipt` 带着 `stage="effect"`**。按这张表写穷举分支的调用方，
#:    会写出一条**永远走不到**的分支。
#: ⇒ `stage` 今天**真能取到的**是**六个**：这七行里**能定案的那五个**
#:    （`validate` ／ `name` ／ `barrier` ／ `area` ／ `handler`）＋ **`"receipt"`**
#:    （四条收尾借的那一格，见 `_close` —— 它**不是**七步里走到过的一步）。
#: ⚠️ 而 `stage` 是**裸 `str`**（与 `tools.effect` 同一个形状：值域由 tuple 给、不由类型给。
#:    本仓两个形状并存：标签是 `ToolTag(StrEnum)`、值域是 tuple）。要收成 `StrEnum` 的话
#:    按上面那**六个**收 —— 别把七步里那一格（`effect`）也收进去，它到不了回执上。
STAGES: tuple[str, ...] = ("validate", "name", "barrier", "area", "effect", "handler", "receipt")


@dataclass(frozen=True)
class Receipt:
    """一条回执 —— **模型看到的那句话** ＋ **这一层要交回去的两位**。

    ⚠️ `text` 就是模型看到的东西：办成了是执行段的返回；没办/办砸了是 `markers` 里那一句。
    ⚠️ `stage` 说**哪一步定的案**（`STAGES` 之一）：`stage == "handler"` **当且仅当**
       执行段被进过。
       ⚠️ 而 `STAGES` 里**有一格到不了这里**（第 5 步 `effect` 不定案）⇒ 真能取到的
       只有六个（五步 ＋ `"receipt"`），逐条写在 `STAGES` 那段 —— 按那七格穷举写分支，
       会写出一条死分支。
    ⚠️⚠️ **而它不是"哪一族"那一位 —— 两套划分不是同一个**（评审那条 Important）。
       分歧在规格 6.5 第 1 行：**参数非法 JSON** 结算在 `stage="validate"`
       （与 `NOT_EXECUTED_INCOMPLETE` **同一个桶**），而它的**值**是 `tool_failed(…)`
       ⇒ 归"**办了但办砸了**"那一族（6.5 明写"**不执行**" —— 那句话说的是另一件事）。
       ⇒ **族由值定**（"没办"那一族是冻住的常量；"办砸了"一律经 `tool_failed`
         那个**唯一**制造口），**哪一步定的案由 `stage` 定** —— 两件都要看才拼得出全貌。
         ⚠️ 把它们当成一回事的实现在
         `tests/test_dispatch.py::test_the_family_and_the_stage_disagree_on_one_row` 上是红的。
    """
    call_id: str
    name: str
    text: str
    stage: str


@dataclass(frozen=True)
class DispatchResult:
    """跑完一批**交回去**的东西 —— 回执 ＋ 这一轮的两道旗子。

    ⚠️ `ended` 是 6.4 第 3 步的另一半：**模型调了 `finish_turn`**（那一行声明的
       `ends_turn=True`）。`round.py:32` 那句"`ended` 恒 `False`"就是因为它读不到工具表 ——
       这一位是把它接上的那一半（接它的那一刀在 `loop.py` 的 `run_tools` 槽）。
    ⚠️ `receipts` **只装定案的那些**：进待确认队列的调用**不在里面**
       （它的结果还没回来 ⇒ 它在配对账上挂着，见 `Dispatcher.pending`）。
       ⚠️⚠️ **⇒ 拿 `calls` 与 `receipts` 对拉会错位**：它不是"逐位对齐的两列"
       —— 中间少掉的正是进了队列的那几条，所以按**下标**去对会把回执安在另一条调用头上
       （有一条 `pending` 时，它后面每一条都会错一格）。要配对就认 `Receipt.call_id`。
       `Receipt.name` 是给读日志的人看的，**不是**配对键。
    """
    receipts: tuple[Receipt, ...]
    ended: bool


#: **谁欠这一份被接上**（`loop.py:60-63` 那条家规）。
#: ⚠️ **接线那一刀之后，这句话**照旧准确、一个字没改 —— 但它说的**不再是装配那一半**
#: （`app.build()` 已经把 `make_run_tools(dispatcher)` 递进了 `loop.run_tools` ）：
#:    欠的是**那个调用点**。`loop.py:63` 只把递进来的东西存成 `self._run_tools`，
#:    而那个属性**全模块再无第二处出现** ⇒ 一场批跑下来**它一次都不会被调用**；
#:    而调用的**材料**（`Result.calls`，`generate.py:113`）今天落在 `round.py` 那个
#:    `RoundOutcome` 上只有一位 `called_tool: bool = True` —— **调用本身在那里丢掉了**。
#:    ⇒ 谁把 `RoundOutcome` 与那个槽接上，谁欠这一笔（而那要动 `round.py`／`loop.py`
#:    其中之一 —— 两处都不在这一刀里）。
#:    ⚠️ **`loop.py` 里那段「今天不会被调用」的注释照旧在**：本刀不许动那个文件一个字节
#:    （它那 9 个槽就是注入面）⇒ 那句话的删除**仍然欠着**，而它今天**仍然是真的**
#:    （"装配接上了" ≠ "有人调它"—— 这两句必须分开读）。
_WIRING_OWED_BY = ("接上 `loop.py` 的 `run_tools` 槽的那一刀 —— 那一刀要把这里交回去的"
                   "回执接到 `RoundOutcome` 那五位上，并把 `loop.py` 里那段"
                   "「今天不会被调用」的注释删掉（那一段逐字节禁改，所以由动它的人删）")

#: **回执送到模型面前的那条路欠谁** —— 接线那一刀裁定的那条"点名"。
#:
#: ⚠️ **本刀的裁定**：`DispatchResult` 的落点是 `Dispatcher.receipts`（一本公开、追加、
#:    只增不减的账）—— **一处机器看得见的地方**，而不是静默丢掉
#:    （静默丢掉正是 `loop.py:60-63` 禁止的形状）。
#:    ⇒ 而"**送给模型**"是**另一件事**，今天**没有**：回执要进会话（`note.Record`）才可能
#:      被下一轮的请求带上，而那条路要发请求那一层先把 `tools` 发上去（`_REQUEST_OWED_BY`
#:      说的是同一件事的另一半：只发 `tools`、不送回结果 ⇒ 模型看见自己的调用**永远悬着**）。
#: ⚠️ **代价（今天量得到的）**：一轮真的调了工具之后，模型下一轮看到的是**自己那句调用**、
#:    而**看不到结果**（会话里只有 `round.py` 写下的 `function_call` 记录，没有结果那一条）
#:    ⇒ 它会以为那件事**还悬着**、或者干脆重来一遍。这条代价随"接线那一刀"一起交出去，
#:    **不是**"还没做"：落点是拍了并落地的（`receipts`），缺的是**消费方**。
#: **这一条接上了**（2026-10-05 改 —— 它原先写的是「`round.py` 只写 `function_call`，
#: **结果那一条没人写** ⇒ 模型永远看不见调用办没办 」，而那句话写在 `round.py` 补齐之前 ）：
#: · `round.py:216-230` 把办完的结果**写成一个 `function_result` 记录**（自带 `call_id` ）；
#:      · `session._render_message(record, call_id=…)` 把它渲染成
#: `{"type":"function_call_output","call_id":…,"output":…}` ；
#: · 而"两边同一个 id"由 `session.outbound` **按成对次序现造**（`call_1` ／ `call_2` … ）
#: —— 引擎那边**实测**：不带 `call_id` 就 400 ，而它只要求"两边同一个 id" 。
#: ⇒ 从"回执落在 `receipts`"到"模型看得见"这一段**有主了**：
#: `round.py` ⇒ `session.py` ⇒ `generate.build_body` （ 而这一条链**只在递了 `run_tools`
#: 的那一趟**上成立 —— 生产那边还没递，见 `_WIRING_OWED_BY` ）。
_RECEIPT_OWED_BY = ("**已经接上了**（回执 ⇒ `round.py` 写 `function_result` ⇒ `session.py` 按 "
                    "`call_id` 渲染成 `function_call_output` ⇒ 下一轮请求带着它 ）；"
                    "⚠️ 而它**只在递了 `run_tools` 的那一趟**上成立（见 `_WIRING_OWED_BY`）")

#: 区域权限那一步的欠主 —— **规格 6.4 只给了那一行**（"它能不能写这块"），
#: 没给谓词的形状，也没给"拒了回哪一句"（见模块开头那条 ⚠️）。
#: ⚠️ **它欠的是装配那一层，不是插件**：`permissions` 是 `Dispatcher(...)` 的一个入参
#:    ⇒ **谁把这部派发器装起来，谁就递这个谓词进来**（`app.py` 那条形状："能接的接上"）。
#:    标签的**语义**仍然归插件（6.2 说那六个标签的意思是插件定的）—— 但"按不按它拦"这句话
#:    是装配层今天要写的，而这个仓库今天两边都还没有（没有插件，也没有那一次装配）。
_PERMISSION_OWED_BY = ("**留口的那一半在树里了**（`Dispatcher(permissions=…)` 只收一个谓词 "
                       "`ToolDef → bool` ）；**规矩那一半归插件**（6.2 那六个标签的语义 —— "
                       "本仓**不许发明一套**：那是「哪块能写」的规矩，而它随插件走 ）。"
                       "⚠️ 今天两个入口都没递 ⇒ `permissions=None` ⇒ **放行** —— "
                       "而那句「今天没有规矩」是**说出来的**（见类文档 ），不是让人去猜那个 `None` 。"
                       " ★★ **2026-10-12 作者拍了：「留给插件」** ⇒ 这一笔**不是欠账**，"
                       "是一条**决定**（今天不划；谁做插件时把它接上）。")

#: 待确认队列的欠主 —— 6.3 明写"**队列由插件提供**"。
#: **放行那半还欠着**（记录那半**不欠** —— 2026-10-12 订正）：
#:   规格 6.3 说**待确认队列**由插件提供，而今天**没有谁提供它** —— 那一半仍然欠着。
#:   ⚠️ 而"记录那半"**不是**这一笔：队列缺席时**执行段照跑**
#:     （`memory.remember_pending` ⇒ 落「悬着的事」），回执照发。
#:   ⇒ 缺队列说的是"**没人放行**"，不是"装配缺了一块"（本模块开头那一条）。
#:   ⚠️ 它与 `_CONFIRMATION_OWED_BY` 是**两半**：那一条管"办完之后谁把它从账上划掉"。
_PENDING_QUEUE_OWED_BY = ("插件那一侧（规格 6.3：**放行**那半由插件提供；今天没有谁提供它）。"
                          " ⚠️ 记录那半不欠：队列缺席时执行段照跑（落「悬着的事」），"
                          "缺的只是「放行」。")

#: **提议被办完之后，谁把它从账上划掉** —— 那个**队列**那条路的**另一半**的欠主。
#: ⚠️ `_PENDING_QUEUE_OWED_BY` 管的是"**递进去**"，这一条管的是"**办完之后**"：
#:    6.3 只说了队列由插件提供，谁**取出**那条提议、确认之后**怎么办**它，规格一个字没说。
#: ⚠️ 而这一层**自己不会替它划账**：`run` 对提议那一趟交回 `None`（第 5 步不定案），
#:    这一层划账只发生在两个地方（定案 ／ 那四条收尾）⇒ 一条**已经被确认并执行**的提议
#:    会一直挂在账上，直到关机时被补一句 `SHUTDOWN_INTERRUPTED`（"结果拿不回来" ——
#:    而它的结果回来了）。
#: ⇒ 谁从队列里把它取出去办，谁就欠**办完之后划掉它**那一句话：
#:    `Dispatcher.acknowledge(call_id)` —— 那一位今天就有、是公开的；缺的是"谁欠它"。
_CONFIRMATION_OWED_BY = ("从待确认队列里**把那条提议取出去办**的那一层（**插件侧** —— "
                         "它办完欠一句 `Dispatcher.acknowledge(call_id)`）")

#: "提议等着的时候模型看见什么" —— **发请求那一层现在发了**（2026-10-05 改 ——
#: 它原先写的是「`generate.build_body` 今天连 `tools` 都不发 ⇒ 模型压根不可能调工具 」，
#: 而那一刀早落地了 ）：
#:      · `generate.py:146-148`：`tools = getattr(cfg, "tools", ())` ⇒ **非空就放进
#: `body["tools"]`** （ 线上量到 `tools = 277` 那一段就是它 ）；
#: · 而那张表由入口递（`launch.py` 的 `_DeclaresTools` ＋ `a_new_table()` ）。
#: ⇒ 这条路**的门已经开了**：剩下的那一半（提议等着时模型看见什么）归
#: `round.py` ／ `session.py` —— 见 `_RECEIPT_OWED_BY` 。
_REQUEST_OWED_BY = ("**已经接上了**（`generate.build_body` 读 `cfg.tools`、非空就放进 "
                    "`body[\"tools\"]` ；那张表由入口递 —— 见 `launch.py` 的 "
                    "`_DeclaresTools` ／ `a_new_table()` ）")

#: 装配缺待确认队列时**唯一**的那一句话 —— 两处共用（`run` 的入批检查 ／ `_enqueue`）：
#: 两处各写一份的话，它们会漂，而"当场点名"这件事靠的正是这句话里的两个名字。
_QUEUE_SEAM_TEXT = f"待确认队列 还没接上 —— 它等 {_PENDING_QUEUE_OWED_BY}"

#: **13 条执行段各欠谁**（`tools.DECLARED_TOOLS` 逐行的执行那半边）。
#:
#: ⚠️ 它**与 `tools._SCHEMA_OWNERS` 长得像、而不是同一笔债**：那一张说"**参数还没声明**"
#:    （6.1 的 `ToolSpec.parameters`），这一张说"**执行段还没绑**"（`ToolDef.handler is None`）。
#:    今天两笔债**同一个主人**（后端没建），而**可能不同一天还**：声明可以先于实现落地
#:    —— Task 2 就只做了前一半。⇒ **谁也不许把这张表改成去读那一张**：
#:    那样一笔债还清之后，另一笔会指向一个已经过期的人。
#: ⚠️ 它由 `tests/test_dispatch.py` 那条**双向闭合**的测试钉着（照 `tools._SCHEMA_OWNERS`
#:    那条的成法）：加一个工具而忘了填这里 = 当场红；表里留着查无此人的名字 = 也红。
_HANDLER_OWNERS: dict[str, str] = {
    #: **2026-10-07（作者拍的「①b」）**：`pending_read` 是**已经接上了**的那一件 ——
    #: 它读自己那些悬着的事（`memory.pending_of` ），只读 ／ 不读时钟 。
    #: **2026-10-07（作者拍的「①b」）**：`pending_read` 是**已经接上了**的那一件 ——
    #: 它读自己那些悬着的事（`memory.pending_of` ），只读 ／ 不读时钟 。
    "pending_read": "**已经接上了**（2026-10-07）：`memory.handlers` 交了它 —— "
                    "读自己那些**悬着的事**（只读 ／ 不算时间）",
    #: **2026-10-07（第 2 刀）**：`profile_add` 也**接上了** —— 写的是**画像**那一棵树 。
    "profile_add": "**已经接上了**（2026-10-07）：`memory.handlers` 交了它 —— "
                   "写一条**关于某个人的观察**（进画像； 去处由它自己挑 ）",
    "skill_note": "**已经接上了**（2026-10-10）：`memory.handlers` 交了它 —— "
                      "记一条**做法**（进「做法」那一棵；"
                      " 名字是「什么时候用它」）",
       "kb_search": "**已经接上了**（2026-10-05 改 —— 这句「知识库那一侧今天没有」写在它之前 ）："
                 "`memory.handlers` 交了它（`kb_search` ⇒ 只查**知识那一棵树** ，"
                 "与 `memory_search` 分工：那个查经历过什么、这个查定下来的事 ）",
    # **2026-10-10（作者拍的「乙」）：第 17 件 `kb_read`** —— 与 `kb_search` 配套 ：
    # `kb_search` 只给「命中那一条」（目录 ）⇒ 要看全 ／ 看后面走这一条 。
    "kb_read": "**已经接上了**（2026-10-10 加的）：`memory.handlers` 交了它"
               "（`kb_read` ⇒ 看知识里某一条的一截，一次只给 `limit` 行 ——"
               "名字就是 `kb_search` 交回来那一串； 不许越出那一棵树 ）",
    # **2026-10-12（作者拍的：那支笔要有读口）：第 20 件 `skill_read`** ——
    # 与 `skill_note` 是**配套的一对**（那一支写进去 ／ 这一扇门读回来）。
    "skill_read": "**已经接上了**（2026-10-12 加的）：`memory.handlers` 交了它"
                  "（`skill_read` ⇒ 看「做法」那一棵里某一条的一截；**不给名字就列名单** ——"
                  " 名字就是 `skill_note` 交回来那一串； 不许越出那一棵树 ）",
    "memory_search": "**已经接上了**（2026-10-07 改 —— 这句「今天没有」写在它之前 ）："
                     "`memory.handlers` 交了它（查**经历过什么**：对话 ／ 笔记 ／ 整理 ）",
    "profile_read": "**已经接上了**（2026-10-07 改 —— 同上）：`memory.handlers` 交了它"
                    "（读**画像**：关于某个人的那些事实 ）",
    "relations_read": "**已经接上了**（2026-10-07 改 —— 同上）：`memory.handlers` 交了它"
                      "（读**关系**：和谁是什么关系 ／ 当前那条说法 ／ 好感 ）",
    "read_events": (
        " ★ **已经接上了**（2026-10-13 订正）：入口那一层把 `launch._read_events`"
        "接到 `events.EventStore` 上（按时刻或按词翻）。 原先写的是"
        "「事件库那一侧（`events.py` 有信封，**没有**这个读口）」。"),
    "self_note": (
        " ★ **已经接上了**（2026-10-13 订正）：入口那一层把它接上（写在「悬着的事」"
        "那一本里，上限 10 条、满了它自己压）。 原先写的是"
        "「自己那本记录那一侧（**今天没有**；`note.py` 是交接笔记，不是它）」。"),
    "self_observe_add": "**已经接上了**（2026-10-07 改 —— 这句「今天没有」写在它之前 ）："
                        "`memory.handlers` 交了它（写一条**自我观察** ⇒ 落「悬着的事」）；"
                        " 而**队列那半边照旧**见 `pending_queue`（放行归插件 ）。"
                        " ★ 2026-10-12 订正：这一件**真的会跑**（队列缺席时执行段照跑 —— "
                        "见本文件开头那一条），缺的只是「放行」。",
    "abouther_propose": "**已经接上了**（2026-10-07 改 —— 同上）：`memory.handlers` 交了它"
                        "（提一条《关于自己》的补充 ⇒ 落「悬着的事」）；"
                        " ★ 2026-10-12 订正：这一件**真的会跑**（同上）—— "
                        "而**放行**那一半今天**归它自己**（作者：「\u5979自己决断」）"
                        " ⇒ 见下面那一件。",
    # **2026-10-12（作者：「\u5979自己决断」）：第 21 件 `abouther_settle`** ——
    # 批那一半从「插件那一侧的队列」改成「**它自己**」。
    "abouther_settle": "**已经接上了**（2026-10-12）：`launch.py` 的 `remember_handlers`"
                       " 交了它 —— 把它自己提过的那一条**落定**（确认 ／ 降级 ／ 删除，"
                       "走 `hers.act` 那一份纯逻辑），写的是《关于我自己》那一本"
                       "（`web.PersonaPort.save_text`），落定之后那条提议从「悬着的事」里"
                       "**收起来**（`memory.resolve_pending`）",
    # **2026-10-12（作者拍的「乙」的下半）：第 18 ／ 19 件 —— 它自己那份自述那一支笔**。
    # ⚠️ 与上面那一件**分得很清**（那正是「乙」那一条的意思）：
    #    `abouther_propose` 是**提**（进悬着的事 ／ **等它定** —— 见上面那一件）；
    #    这两件是**它自己说了算的那一份**（直接写进 `\u5979\u5199\u7684/\u81ea\u8ff0.md`）。
    "self_account_add": "**已经接上了**（2026-10-12）：`launch.py` 的 `remember_handlers`"
                        " 交了它 —— 往那一份**自述**里添一句（写盘走 "
                        "`web.PersonaPort.save_hers_text` 那扇门：留一版历史 ／ 承重形状门 ／ "
                        "路径守卫都走它，不另写一份）",
    "self_account_set": "**已经接上了**（2026-10-12）：与上面那一件同一处交的 —— "
                        "把那一份**整份换掉**（换下来的旧版留在 `.history-自述/` 里）",
    "unresolved_add": "装配那一层（**`app.build()` 绑了** —— 后端 `unresolved.py`；不经它时仍是接缝）",
    "unresolved_resolve": "装配那一层（**`app.build()` 绑了** —— 后端 `unresolved.py`；同 `unresolved_add`）",
    "set_timer": (
        " ★ **已经接上了**（2026-10-13 订正）：入口那一层把它接上，而且**走那扇门**"
        "（`App.arm` —— 写表与置 `wake` 一次做完）。 原先写的是「装配那一层"
        "（今天没有任何执行段把 `set_timer` 接到它上面；那一步要 S8-B 的 `wake.py` 编排）」。"),
    "send_private": "**已经接上了**（2026-10-07 第 3 刀）：`outbox.handlers` 交了它 —— "
                    "它主动开口 ⇒ 落**出站匣**（`data/outbox.jsonl` ）； 而**递到手机上**那一头"
                    "**今天仍然没有**（6.7 注 2：要外面允许「没人问也发话」）—— 那句实话由"
                    "`outbox.NO_ROAD` 带着走 （本机只有一句桌面通知 ）。",
    # ★ **2026-10-13（第 22 件，作者：「做」）**：`propose_change` —— 它提「改你定的那一份」，
    #   而**放行归人**（门是 `POST /persona/adopt` ⇒ `launch.adopt_a_proposal`）。
    "propose_change": (
        " **已经接上了**（2026-10-13）：入口那一层交了它"
        "（`launch.a_proposal_to_change` —— 落进「悬着的事」的 `persona` 那一格，"
        "目标名在 `source` 上；它自己那几份 ／ 那一棵里没有的名字**当场说清**，"
        "不记一条办不成的提议）。"),
    "decide_change": "入口 launch._decide_change 绑定 persona_deliberation.decide；由 Bot 自行落定设定提议并留档。",
    "finish_turn": (
        " ★ **已经接上了**（2026-10-13 订正）：入口那一层接了它，它的 `ends_turn`"
        "由收工那一格驱动。 原先写的是「主循环那一侧（`loop.py` 的收工口；**今天没有**）」。"),
}

#: 表上**没有**这个名字时的那句欠主（13 条之外的工具是插件带来的 ⇒ 它的实现归声明它的人）。
_OUTSIDE_THE_TABLE = "声明它的那一侧（表上没这个名字 —— 13 条之外的工具由声明它的插件自己绑）"


def _not_wired(name: str, who: str) -> Callable[..., Any]:
    """一条**点名接缝**：它今天没人实现 ⇒ **当场抛，且消息里写着谁欠它**。

    ⚠️ 照 `app.py` 那条已经立过的形状（`loop.py:60-63` 的家规）：
       「一个传进来却没人用的东西，**要说清它还没接上，不许让它静默无效**」。
    ⚠️ **消息里那两个名字都是契约**：`name` 是**哪一个执行段**，`who` 是**谁欠它**（可清点）。
       `tests/test_dispatch.py` 那条测试**逐条**断言这两半 ——
       一个只抛 `NotImplementedError("todo")` 的实现会在那里红。
    """
    def _raise(*_a: Any, **_k: Any) -> Any:
        raise NotImplementedError(f"{name} 还没接上 —— 它等 {who}")
    return _raise


#: 13 条接缝（名字 → 会抛的那个东西）。见 `_HANDLER_OWNERS` 上面那一段。
_HANDLER_SEAMS: dict[str, Callable[..., Any]] = {
    name: _not_wired(name, who) for name, who in _HANDLER_OWNERS.items()
}


def _seam(name: str) -> Callable[..., Any]:
    """名字 → 那条接缝（**13 条在表上**，别的走 `_OUTSIDE_THE_TABLE`）。"""
    return _HANDLER_SEAMS.get(name) or _not_wired(name, _OUTSIDE_THE_TABLE)


def _parse(call: ToolCall) -> tuple[Any, str | None]:
    """ 第 1 步（校验）那一格 —— 能过就交出**解好的参数**，过不了就交出**那一句回执**。

    ⚠️ **两条判据的先后是有意的**（`tests/test_dispatch.py` 有一条专门钉它）：
       "半截"**先于**"参数烂" —— 一条只来了半条、参数也是半截的调用，
       参数是半截的**因为调用本身是半截的** ⇒ 说它"办砸了"（参数不合法）是错的：
       它**压根没办**。规格 6.5 的整句理由就在这里。
    ⚠️ **空 `arguments` 也算不合法 JSON**：一个不收参数的工具，上游至少该给 `{}`。
       这一层**不替它补** —— "没给"与"不收参数"是两件事（`ToolSpec.parameters` 那条同款：
       `None` 不等于 `{}`）。
    """
    if not call.call_id or not call.name:
        return None, NOT_EXECUTED_INCOMPLETE
    try:
        return json.loads(call.arguments), None
    except ValueError:
        return None, TOOL_FAILED_BAD_ARGS


def _warn_if_large(name: str, text: str) -> None:
    """ 规格 6.6：**超长只记 warn，一个字都不截**。

    ⚠️ 判据是**超过** `LARGE_RESULT_WARN_CHARS`（不是"有结果"、也不是"很长"）——
       写成常量的实现在 `tests/test_dispatch.py` 那条"刚好到上限不许出声"上是红的。
    ⚠️ 类别是 `UserWarning`：`pyproject.toml` 把 `RuntimeWarning` 判成错，
       而"结果太长"是**预期内**的事（体积归插件管），不是本仓的错。
    ⚠️ `stacklevel=4`：越过本函数 ／ `_dispatch_one` ／ `run` 三层，指到**调用方**
       （照 `tools.build_table` 那条告警的成法 —— 让人看见是谁跑的这批工具）。
    """
    if len(text) <= LARGE_RESULT_WARN_CHARS:
        return
    warnings.warn(
        f"{name} 的结果有 {len(text)} 字符 —— 超过 {LARGE_RESULT_WARN_CHARS} 只记 warn，"
        "一个字都不截断（规格 6.6：体积归插件管）",
        UserWarning, stacklevel=4)


class Dispatcher:
    """一部派发器：**一张本 session 的表** ＋ 两点从外面递进来的能力。

    ⚠️ **两个能力都从外面递**（本支反复用的同一个手法：能接的接上、接不上的点名）：
       `permissions` 是**区域规矩**（6.4 第 4 步），`pending_queue` 是**待确认队列**（6.3）。
       两者的欠主分别写在 `_PERMISSION_OWED_BY` ／ `_PENDING_QUEUE_OWED_BY` 上。
    ⚠️ **`permissions=None` 是"今天没有规矩"**（放行），不是"谁都不许" ——
       而这句话是**说出来的**（模块开头第一段），不是靠读者去猜那一个 `None`。
    """

    def __init__(self, table: Mapping[str, ToolDef], *,
                 permissions: Callable[[ToolDef], bool] | None = None,
                 pending_queue: Callable[[ToolCall], Any] | None = None,
                 speech_guard: Callable[[ToolDef, ToolCall], bool | str] | None = None,
                 runtime_evidence: Any = None) -> None:
        """收下那一张表与那两个能力。

        ⚠️ `permissions` 拿到的是**那一行声明**（`ToolDef`）—— 6.4 第 4 步问的是
           "它能不能写**这块**"，而"这块"是那一行上的标签（6.2 的六个）。
           ⚠️ **今天拿不到参数**：13 条的 `parameters` 一律 `None`（还没声明）
           ⇒ 谓词拿 `call.arguments` 只能自己再解一遍，而那是**两条真相**。
           谁要按参数分区域，谁先把 schema 那些债还掉。
        ⚠️ `pending_queue` 是**怎么把一条提议交出去**（递一个可调用对象）——
           这一层不规定它返回什么（6.3 说队列归插件，那边的事那边定）。
        """
        self._table: dict[str, ToolDef] = dict(table)
        self._permissions = permissions
        self._pending_queue = pending_queue
        self._speech_guard = speech_guard
        self._runtime_evidence = runtime_evidence
        self.rejected_hook_work = []
        self._pending: dict[str, str] = {}
        self._closing_receipts: dict[str, Receipt] = {}
        from .dispatch_history import ResultHistory, InvocationLedger
        self._receipts = ResultHistory(Receipt, DispatchResult)
        self._active_batch = False
        self._invocations = InvocationLedger()
        self._settling_batch = None

    def describe_tools(self, names):
        """Detached metadata from the current table, checked at read time.

        This never reserves speech, invokes a handler, or grants execution.
        Reloads and current regional restrictions apply to every lookup.
        """
        from copy import deepcopy
        allowed = set(names)
        rows = []
        for name, tool in sorted(self._table.items()):
            if name not in allowed or not callable(tool.handler):
                continue
            if self._permissions is not None and not self._sync_hook(self._permissions, tool):
                continue
            rows.append({'name': name, 'description': tool.description,
                'parameters': deepcopy(tool.parameters), 'usage': tool.usage,
                'tags': [str(tag) for tag in tool.tags], 'effect': tool.effect,
                'barrier_after': tool.barrier_after, 'ends_turn': tool.ends_turn})
        from .qq_tool_menu import clarify_qq_text
        clarify_qq_text(rows)
        return rows

    def receipt_coverage(self, calls):
        """Evidence for exact live input objects; never acknowledge or execute."""
        asked = tuple(calls)
        entry = self._invocations.get(asked)
        if not entry.admitted:
            return {'state': 'not_admitted', 'settled': 0}
        receipts = tuple(entry.receipts[id(c)] for c in asked if id(c) in entry.receipts)
        complete = (len(receipts) == len(asked) and
                    all((r.call_id, r.name) == (c.call_id, c.name)
                        for c, r in zip(asked, receipts)))
        return {'state': 'settled' if complete else 'incomplete', 'settled': len(receipts)}

    def settled_receipts(self, calls: Sequence[ToolCall]) -> tuple[Receipt, ...]:
        """Read definitive receipts for exactly one unchanged live invocation."""
        asked = tuple(calls)
        entry = self._invocations.get(asked)
        if not entry.admitted: raise ValueError('foreign or unrecorded dispatch invocation')
        return tuple(entry.receipts[id(call)] for call in asked if id(call) in entry.receipts)

    def closing_receipts(self) -> tuple[Receipt, ...]:
        """Unconsumed close receipts; reading never executes or acknowledges work."""
        return tuple(self._closing_receipts.values())

    def acknowledge_closing(self, receipt: Receipt) -> None:
        """Consumer calls only after durable delivery, with the original object.

        A pending-queue adapter owns that delivery. The default application does
        not configure such a queue; this API does not approve or replay proposals.
        """
        if self._closing_receipts.get(receipt.call_id) is not receipt:
            raise ValueError('foreign or already consumed closing receipt')
        del self._closing_receipts[receipt.call_id]

    # ── 配对账（`round.py:103` 的裁定：`call_id` 不进会话 ⇒ 账在这）────────
    @property
    def pending(self) -> Mapping[str, str]:
        """**配对账的只读快照**：`call_id` → 工具名（还挂着的那些，**按到账先后**）。

        ⚠️ 交出去的是**快照**（改它改不动这部派发器）—— 与 `session.records()` 同一条纪律。
        ⚠️ 名字留着不是装饰：关机补的那条回执要写清"哪一条调用"（光有 `call_id`
           对下一个读日志的人什么都不是）。
        """
        return MappingProxyType(dict(self._pending))

    # ── 回执的落点（ 接线那一刀的裁定）────────────────────────────────
    @property
    def receipts(self) -> tuple[DispatchResult, ...]:
        """Full process-local history, in order, explicitly materialized on request.

        Cold payloads live in a disposable disk spool; strong hot references are
        bounded. Results still held by callers keep their original identity.
        Durable business evidence remains in the event/session ledgers. Asking
        for this full snapshot can allocate memory proportional to its size.
        """
        return tuple(self._receipts)

    def record(self, calls: Sequence[ToolCall]) -> tuple[str, ...]:
        """ 配对账第一条：**一批调用进来 ⇒ 记上**。收下 → 交回**这一趟新记上的**键。

        ⚠️ 记的是 `call_id`（回执拿它对上）。**同一个键来第二次不覆盖名字**（先到先用）：
           那一条调用已经在账上了，而账上的名字是它第一次报上来的那一个。
        ⚠️ **没有 `call_id` 的调用进不了账**（没有钥匙）—— 它的回执照发，而"进不了账"
           这件事本身就是它被判成半截的一半理由（见 `_parse`）。
        """
        if any(call.call_id in self._closing_receipts for call in calls):
            raise ValueError('closing receipt has not been consumed')
        fresh: list[str] = []
        for call in calls:
            if not call.call_id:
                continue
            if call.call_id not in self._pending:
                fresh.append(call.call_id)
                self._pending[call.call_id] = call.name
        return tuple(fresh)

    def acknowledge(self, call_id: str) -> bool:
        """ 配对账第二条：**收到回执 ⇒ 划掉**。交回"它在不在账上"。

        ⚠️ **它回 `False` 是有用的**：一条**从没记过**的回执进来 ⇒ 配对坏了
           （上游多给了一条 ／ 我们这边漏记了一条），而"划掉了"与"划了个空气"
           不该长得一样。这一层不替调用方决定怎么办（它只知道"对不上"）。
        """
        return self._pending.pop(call_id, None) is not None

    # ── 三种收尾 ＋ 兜底（规格 6.5 的后三行都从这里出去）──────────────────
    def on_stream_aborted(self) -> tuple[Receipt, ...]:
        """ 6.5 第 5 行：**流中途断了** ⇒ 还挂着的补 `NOT_EXECUTED_STREAM_ABORTED`。"""
        return self._close(NOT_EXECUTED_STREAM_ABORTED)

    def on_loop_stopped(self) -> tuple[Receipt, ...]:
        """ **主循环停了** ⇒ 还挂着的补 `NOT_EXECUTED_LOOP_STOPPED`。

        ⚠️ 与"流断了"分开：**它们说的不是同一件事**（流断了那一轮可以重来；主循环停了
           是今天到此为止）—— 而 `markers.py` 那张表里它们本来就是两句。
        """
        return self._close(NOT_EXECUTED_LOOP_STOPPED)

    def on_shutdown(self) -> tuple[Receipt, ...]:
        """ 6.5 第 6 行 ＝ **⑤**：**关机打断** ⇒ 还挂着的补 `SHUTDOWN_INTERRUPTED`。

        ⚠️ **它是"同步做"的**：
           关机那一刻有什么就补什么，不等任何人。
        ⚠️ **不看这一批调用**、只看账 ⇒ 已经办成的那些（`run` 划掉了）**不会**再收一条
           —— 否则模型会看到"我问了两件事，两件都没办"（而其中一件是办成了的）。
        """
        return self._close(SHUTDOWN_INTERRUPTED)

    def repair(self) -> tuple[Receipt, ...]:
        """ **说不上原因**时的兜底：还挂着的补 `MISSING_RESULT`（"结果没回来"）。

        ⚠️ 上面三条是**有名有姓**的（流断 ／ 主循环停 ／ 关机）；而这一条**不声称原因** ——
           `markers.MISSING_RESULT` 的说明点名的消费者就是**这张配对账**。
        ⚠️ **今天没有生产调用方**（`tests/test_dispatch.py` 里有人调它 —— 那是在证明
           "这一句真造得出来"，不是有人在用它）：今天知道的收尾路都落在那三条里。
           谁遇到第四条路（进程重启那一路另有 `MISSING_RESULT_RESTART`，归会话重建那一刀），
           谁就用它 —— 而那四条路**一起**还没有生产调用方（见模块开头那段）。
        """
        return self._close(MISSING_RESULT)

    def run(self, calls: Sequence[ToolCall]) -> DispatchResult:
        if self._runtime_evidence is None:
            return self._run_owned(calls)
        with self._runtime_evidence.scope("tool_sync"):
            return self._run_owned(calls)

    def _run_owned(self, calls: Sequence[ToolCall]) -> DispatchResult:
        """ 跑一批调用：**七步按规格 6.4 的次序**，一条一条来。

        ⚠️⚠️ **这一处从前有一道"入批检查"**（`_needs_the_pending_queue` ⇒ 队列没接上就在
            记账之前停住、一条都不办、一条都不记）：**2026-10-12 那一刀把它撤了**。
            理由：缺队列那一步今天**不再是"装配缺了一块"**（它降级成"执行段照跑、只是
            没人放行"，见模块开头那一条）⇒ 那道检查要防的那扇窗（"排在提议前面的先跑了、
            后面的从没被派发却挂在账上 ⇒ 关机时被补一句 `SHUTDOWN_INTERRUPTED`"）
            **不再存在**：队列在 ⇒ 交给它、不抛；队列不在 ⇒ 记下来、不抛。
            ⚠️ 它当时那两笔账（为什么要有这道检查）留在提交信息里，免得下一个人
            以为那是随手删的。
        ⚠️ **进了那个队列的那些不在回执里**（第 5 步不定案，`stage` 上没有 `"effect"` 这一格）：
           它的结果**还没回来** ⇒ 它留在配对账上挂着。那是**设计**，不是漏了 ——
           而"模型下一轮看到一条没有结果的调用"这件事归**发请求那一层**（见 `_REQUEST_OWED_BY`）。
        ⚠️ **屏障／收工那两道旗子只看"它办到第 6 步没有"**（`stage == "handler"`）：
           一条被拒、被挡、进了队列的 `finish_turn` **不立屏障、也不收工**。
           ⚠️ 而它**与执行段成没成无关**（那一段今天抛 `NotImplementedError` 也照样立）
           —— 收工是**模型的决定**（6.7 注 3），不是后端的功劳；挂在一个没接上的后端上，
           主循环就**永远停不下来**了。

         **交回去之前先记到 `self._receipts` 上**（接线那一刀）：那一趟的结果**不许只**
          活在调用方的局部变量里（见 `receipts` ／ `_RECEIPT_OWED_BY`）。
          ⚠️ **它排在入批检查之后**：那种趟次**一条都没派发过** ⇒ 账上也不该有它
          （一本只记"真跑过的那几趟"的账）。
        """
        self._claim_batch()
        steps = self._batch(calls)
        try:
            done, step = self._advance(steps, None)
            while not done:
                call, blocked, speech_blocked, table, owned_handlers = step
                receipt = self._dispatch_one(call, blocked=blocked,
                                             speech_blocked=speech_blocked, table=table)
                done, step = self._advance(steps, receipt)
            return step
        finally:
            steps.close()
            self._active_batch = False
            self._settling_batch = None

    async def arun(self, calls: Sequence[ToolCall]) -> DispatchResult:
        if self._runtime_evidence is None:
            return await self._arun_owned(calls)
        async with self._runtime_evidence.async_scope("tool_async"):
            return await self._arun_owned(calls)

    def memory_search_diagnostics(self) -> dict[str, Any]:
        """Local aggregate counts, no query, parameter names or returned text."""
        tool = self._table.get('memory_search')
        getter = getattr(getattr(tool, 'handler', None), '__safe_diagnostics__', None)
        if not callable(getter):
            return {'available': False}
        value = getter()
        if (type(value) is not dict or set(value) != {'scope','calls','unknown_argument_calls','saturated'}
                or value['scope'] != 'this_handler_lifetime'
                or type(value['calls']) is not int or type(value['unknown_argument_calls']) is not int
                or not 0 <= value['unknown_argument_calls'] <= value['calls'] <= 2**63-1
                or type(value['saturated']) is not bool):
            raise ValueError('invalid aggregate diagnostics')
        return {'available': True, **value}

    async def _arun_owned(self, calls: Sequence[ToolCall]) -> DispatchResult:
        """Await native async handlers, serially, with the same seven-step contract.

        Sync handlers still run on the event loop. This is NOT a worker pool,
        does not start parallel rounds, and does not make blocking I/O async.
        Cancellation escapes without inventing a completed receipt. The caller
        must join this batch before repairing its remaining pending entries.
        """
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            batch = tuple(calls)
            # This exact branch is before claim/record/handler effects. Reused
            # objects never receive a new no-effect proof for an older run.
            if not any(self._invocations.used(call) for call in batch):
                self._invocations.record(batch, admitted=False)
            raise asyncio.CancelledError  # No new batch may start after stop was requested.
        self._claim_batch()
        steps = self._batch(calls)
        cancellations = 0
        try:
            done, step = self._advance(steps, None)
            while not done:
                call, blocked, speech_blocked, table, owned_handlers = step
                receipt = await self._adispatch_one(
                    call, blocked=blocked, speech_blocked=speech_blocked,
                    task=task, cancellations=cancellations, table=table,
                    owned_handlers=owned_handlers)
                done, step = self._advance(steps, receipt)
            return step
        finally:
            steps.close()
            self._active_batch = False
            self._settling_batch = None

    def _claim_batch(self) -> None:
        if self._active_batch:
            raise RuntimeError("dispatcher already owns an active batch")
        self._active_batch = True

    @staticmethod
    def _advance(steps, receipt):
        # Catch generator exhaustion only, never a handler/hook StopIteration.
        try:
            return False, steps.send(receipt)
        except StopIteration as stopped:
            return True, stopped.value

    def _batch(self, calls: Sequence[ToolCall]) -> Generator[
            tuple[ToolCall, bool, bool, Mapping[str, ToolDef], Mapping[str, Any]],
            Receipt | None, DispatchResult]:
        """Shared ledger/order/barrier state machine for both public runners."""
        batch = tuple(calls)
        keyed = [call.call_id for call in batch if call.call_id]
        if (len({id(call) for call in batch}) != len(batch)
                or len(set(keyed)) != len(keyed)):
            raise ValueError("unique dispatch identities required")
        if any(self._invocations.used(call) for call in batch):
            raise ValueError("dispatch call object already belongs to an invocation")
        if any(call.call_id in self._pending for call in batch):
            raise ValueError("dispatch call id still has unresolved responsibility")
        retained = self._invocations.record(batch, admitted=True).receipts
        self._settling_batch = (batch, retained)
        table = dict(self._table)  # Freeze handlers and barriers across async suspension.
        owned_handlers = {name: getattr(tool.handler, '__owned_async_handler__', None)
                          for name, tool in table.items()}
        self.record(batch)
        receipts: list[Receipt] = []
        #: **硬屏障**：`finish_turn` 那种 —— 立在它后面的**一条都不跑**（收工就是收工）。
        blocked = False
        #: ★ **软屏障**（2026-10-17）：**发言**那种 —— 只挡**后面还想再发言的那一条**。
        #:
        #: ⚠️⚠️ **为什么它必须与硬屏障分开**（真跑量到的）：
        #:   那天 08:51 那一轮模型先 发送工具 说了话、后面紧跟一条 `profile_add`，
        #:   而 `barrier_after` 把**整条后路**都挡了 ⇒ 回执是
        #:   `[not executed: review the preceding tool result first]`，那一条观察就**没了**。
        #:   这跟 `_one_speech_per_turn` 自己写的那句意图**正好相反**：
        #:   "read/write tools keep their existing semantics" —— 本意是**只限发言**，
        #:   而实现把读写一起关在门外了。
        #: ⚠️ 要防的那件事仍然防着："一轮只说一句" —— 两条 SPEAK 排一起，第二条照样不跑。
        #: ⚠️ 而 `finish_turn` 那道屏障**一个字没动**（它不是 SPEAK ⇒ 走 `blocked`）。
        speech_blocked = False
        ended = False
        for call in batch:
            receipt = yield call, blocked, speech_blocked, table, owned_handlers
            if receipt is None:
                continue                       # ⑤ 分岔：进待确认队列，这一趟不定案
            receipts.append(receipt)
            if receipt.stage != "handler":
                continue                       # 没办到第 6 步 ⇒ 两道旗子都不立
            tool = table[call.name]
            speaks = ToolTag.SPEAK in (tool.tags or ())
            if tool.barrier_after:
                if speaks:
                    speech_blocked = True      # 只封"再说一句"
                else:
                    blocked = True             # 整条后路都封（收工那种）
            ended = ended or tool.ends_turn
        result = DispatchResult(tuple(receipts), ended)
        self._receipts.append(result) # 落点：一处**机器看得见**的地方
        return result

    def _prepare(self, call: ToolCall, *, blocked: bool,
                 speech_blocked: bool = False, table: Mapping[str, ToolDef] | None = None
                 ) -> Receipt | None | tuple[Callable[..., Any], Any]:
        """Shared steps 1-5; a prepared handler is not yet an execution receipt."""
        # ① 参数校验 ＋ 半截那个判断（两条一起，见 `_parse`）
        arguments, refusal = _parse(call)
        if refusal is not None:
            return self._settle(call, refusal, "validate")
        # ② 工具名在不在本 session 的表里
        tool = (self._table if table is None else table).get(call.name)
        if tool is None:
            return self._settle(call, UNKNOWN_TOOL, "name")
        # ③ barrier_after / ends_turn（**挡的是排在它后面的那些**）
        # ⚠️ **两种屏障要分开**（见 `run` 那一段）：硬屏障挡一切；软屏障（发言之后）
        #    只挡"后面还想再发言"的那一条 —— 读写照跑（那正是它本来的意图）。
        if blocked:
            return self._settle(call, NOT_EXECUTED_BARRIER, "barrier")
        if speech_blocked and ToolTag.SPEAK in (tool.tags or ()):
            return self._settle(call, NOT_EXECUTED_BARRIER, "barrier")
        # ④ 区域权限检查（见模块开头那条 ⚠️：拒了也是 `UNKNOWN_TOOL`）
        if self._permissions is not None and not self._sync_hook(self._permissions, tool):
            return self._settle(call, UNKNOWN_TOOL, "area")
        if isinstance(call, BoundPrivateText) and call.handler is not tool.handler:
            return self._settle(call, UNKNOWN_TOOL, "area")
        # An unconfirmed portrait draft cannot be copied into a personal note or its queue.
        from .portrait_eligibility.gate import note_copy_denied
        if note_copy_denied(tool.name, arguments):
            return self._settle(call, tool_failed("这份人物草稿尚未通过画像审核，不能原样转存为自己的笔记。可先放着；确有作者原话时，人物观察可分开记原话和推测。"), "area")
        # ⑤ effect 路由：`pending` 只是提议。
        # ⚠️⚠️ **2026-10-12 订正**：这一处从前是**无条件**交给队列
        #    （`self._enqueue(call); return None`）⇒ 队列没递时**当场抛**，
        #    而那一下就把它记下来的那半也带走了（记录那半归记忆层，就是这一行的 handler）
        #    —— 一条提议**既没记下、又把整批带走**。今天：
        #      · **队列在** ⇒ 交给它、这一趟不定案（6.4 第 ⑤ 步，与从前逐字相同）；
        #      · **队列不在** ⇒ **执行段照跑**（记下来）＋ 回执照发。
        #    理由与量到的代价写在模块开头那一条上（"插件缺席时：记录还在"）。
        if tool.effect == "pending" and self._pending_queue is not None:
            self._enqueue(call)
            return None
        # A provable argument-binding failure happens BEFORE the handler's side
        # effects. Let the model see that receipt and choose whether to correct
        # it. Never classify an exception from inside the handler this way:
        # even TypeError/timeout could follow an accepted remote send.
        if tool.handler is not None:
            try:
                signature = inspect.signature(tool.handler, follow_wrapped=False)
            except (TypeError, ValueError):
                signature = None  # Unknown call shape: preserve conservative end.
            if signature is not None:
                try:
                    if isinstance(arguments, dict):
                        signature.bind(**arguments)
                    else:
                        signature.bind(arguments)
                except TypeError as exc:
                    guidance = "请按当前工具声明修正参数。可用 tool_guide 查看参数"
                    if ToolTag.SPEAK in (tool.tags or ()) and isinstance(tool.parameters, Mapping):
                        properties = tool.parameters.get("properties")
                        if (tool.parameters.get("additionalProperties") is False
                                and isinstance(properties, dict) and properties):
                            guidance = "当前工具接受的参数为 " + "、".join(str(key) for key in properties)
                    return self._settle(call, tool_failed(
                        f"{'发言' if ToolTag.SPEAK in (tool.tags or ()) else '工具'}参数不匹配，未执行：{exc}；{guidance}；这次拒绝不证明任何更早操作未执行。"), "validate")
        if ToolTag.SPEAK in (tool.tags or ()) and self._speech_guard is not None:
            try:
                allowed = self._sync_hook(self._speech_guard, tool, call)
            except Exception:
                allowed = False
            if isinstance(allowed, str):
                return self._settle(call, tool_failed(allowed), "area")
            if not allowed:
                return self._settle(call, tool_failed(
                    "speech budget denied before execution; use finish_turn"), "area")
        return tool.handler or _seam(call.name), arguments

    def _dispatch_one(self, call: ToolCall, *, blocked: bool,
                      speech_blocked: bool = False,
                      table: Mapping[str, ToolDef] | None = None) -> Receipt | None:
        prepared = self._prepare(call, blocked=blocked, speech_blocked=speech_blocked,
                                 table=table)
        if not isinstance(prepared, tuple):
            return prepared
        handler, arguments = prepared
        if (isinstance(call, BoundPrivateText) or inspect.iscoroutinefunction(handler) or inspect.isasyncgenfunction(handler)
                or inspect.iscoroutinefunction(getattr(handler, "__call__", None))):
            return self._settle(call, tool_failed("async handler requires Dispatcher.arun"),
                                "validate")
        try:
            result = handler(**arguments) if isinstance(arguments, dict) else handler(arguments)
            self._reject_async_value(result)
        except Exception as exc:  # Cancellation and process-level exceptions escape.
            return self._handler_failure(call, exc)
        return self._handler_success(call, result)

    async def _adispatch_one(self, call: ToolCall, *, blocked: bool,
                            speech_blocked: bool, task, cancellations: int,
                            table: Mapping[str, ToolDef],
                            owned_handlers: Mapping[str, Any]) -> Receipt | None:
        if task is not None and task.cancelling() > cancellations:
            raise asyncio.CancelledError
        prepared = self._prepare(call, blocked=blocked, speech_blocked=speech_blocked,
                                 table=table)
        if task is not None and task.cancelling() > cancellations:
            raise asyncio.CancelledError  # Preparation hooks cannot admit work after stop.
        if not isinstance(prepared, tuple):
            return prepared
        handler, arguments = prepared
        references = current_tool_references.get() or {}
        lease = call.lease if isinstance(call, BoundPrivateText) else None
        token = current_invocation.set(Invocation(call.call_id, call.name, references.get(id(call), ""), batch_lease=lease))
        try:
            try:
                owned = owned_handlers.get(call.name)
                if owned is not None:
                    if not (inspect.iscoroutinefunction(owned) or inspect.iscoroutinefunction(
                            getattr(owned, '__call__', None))):
                        raise TypeError('owned async registration must be native async')
                    handler = owned  # A batch-frozen, explicitly registered entry only.
                result = (handler(**arguments) if isinstance(arguments, dict)
                          else handler(arguments))
                if inspect.isawaitable(result):
                    result = await result
                self._reject_async_value(result)
            finally:
                current_invocation.reset(token)
        except Exception as exc:
            if task is not None and task.cancelling() > cancellations:
                raise asyncio.CancelledError from exc
            return self._handler_failure(call, exc)
        # A handler may catch cancellation for cleanup; never then run later tools.
        if task is not None and task.cancelling() > cancellations:
            raise asyncio.CancelledError
        return self._handler_success(call, result)

    def _reject_async_value(self, result: Any) -> None:
        if isinstance(result, (asyncio.Future, ThreadFuture)):
            row = self._retain_rejected_future(result)
            error = TypeError('unresolved thread Future tool result' if isinstance(result, ThreadFuture)
                              else 'unresolved async tool result; use arun with a final value')
            error.owned_work = row
            raise error
        if inspect.isawaitable(result) or inspect.isasyncgen(result):
            # Close only a fresh native coroutine; never cancel an external Future.
            if inspect.iscoroutine(result) or isinstance(result, ManagedAwaitable):
                result.close()
            raise TypeError("unresolved async tool result; use arun with a final value")

    def _handler_failure(self, call: ToolCall, exc: Exception) -> Receipt:
        return self._settle(call, tool_failed(f"{type(exc).__name__}: {exc}"), "handler")

    def _handler_success(self, call: ToolCall, result: Any) -> Receipt:
        text = result if isinstance(result, str) else str(result)
        _warn_if_large(call.name, text)
        return self._settle(call, text, "handler")

    def _enqueue(self, call: ToolCall) -> None:
        """把一条提议交给待确认队列（6.3：队列由插件提供）。

        ⚠️ **没递队列就当场点名、而且那句话从 `run` 里逃出去**：这不是"这一次调用办没办"
           能表达的事 —— 它是**装配缺了一块**（提议递不出去），不是这次调用的结果。
           ⇒ 不拿一条回执把它盖住（那样"队列没接上"会被读成"模型提的这条建议被拒了"）。
        ⚠️ **调用点只有一个**（`_dispatch_one` 第 ⑤ 步），而那一处**先判过"队列在不在"**
           ⇒ 这里那一判是**第二道**：谁绕过 `run` 直接走 `_dispatch_one` 时，缺队列
           也不许变成一次静默的跳过（`tests/test_dispatch.py::test_a_missing_confirmation_
           queue_is_a_named_seam` 量的就是这一道）。
        ⚠️ **而 `run` 那一道入批检查 2026-10-12 撤了** —— 缺队列**不再**"整批停住"
           （它只降级成"执行段照跑、只是没人放行"，见模块开头那一条）
           ⇒ 今天**从 `run` 走**是走不到这一句的。
        """
        if self._pending_queue is None:
            raise NotImplementedError(_QUEUE_SEAM_TEXT)
        self._sync_hook(self._pending_queue, call)

    def receipt_shutdown_state(self):
        # Rejection does not transfer ownership or prove that returned work ended.
        def retired(work, seen):
            if id(work) in seen or len(seen) >= 64:
                return False
            seen.add(id(work))
            if isinstance(work, (asyncio.Future, ThreadFuture)):
                if not work.done(): return False
                try: value = work.result()
                except BaseException: return True
                if isinstance(value, (asyncio.Future, ThreadFuture)) or inspect.isawaitable(value) or inspect.isasyncgen(value):
                    return retired(value, seen)
                return True
            return inspect.iscoroutine(work) and inspect.getcoroutinestate(work) == inspect.CORO_CLOSED
        pending = bool(self._active_batch or self._closing_receipts
                       or (self._pending_queue is not None and self._pending))
        for row in tuple(self.rejected_hook_work):
            work = row['work']
            if not retired(work, set()):
                pending = True
        # A rejected invocation may have no running work left but still owe a
        # receipt. Preserve incomplete without inventing a live worker.
        return {'pending': pending,
                'incomplete': bool(self.rejected_hook_work or self._pending or self._closing_receipts)}

    def _retain_rejected_future(self, value):
        row = {'work': value, 'error': None}
        self.rejected_hook_work.append(row)
        watched = set()
        def observe(future):
            if id(future) in watched: return
            watched.add(id(future))
            def finished(done):
                try: nested = done.result()
                except BaseException as error: row['error'] = error
                else:
                    if isinstance(nested, (asyncio.Future, ThreadFuture)): observe(nested)
            try: future.add_done_callback(finished)
            except BaseException as error: row['error'] = error
        observe(value)
        return row

    def _sync_hook(self, callback, *args):
        if inspect.iscoroutinefunction(callback) or inspect.iscoroutinefunction(getattr(callback, '__call__', None)):
            raise TypeError('synchronous dispatch hook required')
        value = callback(*args)
        if isinstance(value, (asyncio.Future, ThreadFuture)) or inspect.isawaitable(value) or inspect.isasyncgen(value):
            if isinstance(value, (asyncio.Future, ThreadFuture)):
                row = self._retain_rejected_future(value)
            else:
                row = {'work': value, 'error': None}
                self.rejected_hook_work.append(row)
                if inspect.iscoroutine(value) and inspect.getcoroutinestate(value) == inspect.CORO_CREATED:
                    value.close()
            error = TypeError('unresolved asynchronous dispatch hook')
            error.owned_work = row
            raise error
        return value

    def _settle(self, call: ToolCall, text: str, stage: str) -> Receipt:
        """定案一条：造回执 ＋ **把它从配对账上划掉**（"收到回执 ⇒ 划掉"就落在这里）。"""
        receipt = Receipt(call_id=call.call_id, name=call.name, text=text, stage=stage)
        binding = self._settling_batch
        if (not self._active_batch or binding is None
                or not any(one is call for one in binding[0])):
            raise ValueError("receipt does not belong to the active invocation")
        binding[1][id(call)] = receipt
        self.acknowledge(call.call_id)
        return receipt

    def _close(self, marker: str) -> tuple[Receipt, ...]:
        """把账上**还挂着的**一次收干净（四种收尾共用这一句）—— 按到账先后。

        ⚠️ `stage="receipt"` —— 而这一格**借用了第 ⑦ 步那一行的名字**（规格 6.4 把第 7 步
           就叫"回执"）：这四条收尾**不是七步里的一步**，那些调用**压根没被派发过**，
           而"回执"本身就是这件事的全部。
           ⚠️ ⇒ 按 `stage` 判"哪一步定的案"的调用方，**别把这一格读成"走到过第 7 步"**
           （走到第 7 步的那几条顶着 `"handler"` —— 见 `Receipt.stage` 那条"当且仅当"）。
        """
        if self._active_batch:
            raise RuntimeError("join the active dispatcher batch before repairing receipts")
        out: list[Receipt] = []
        for call_id, name in list(self._pending.items()):
            receipt = Receipt(call_id=call_id, name=name, text=marker, stage="receipt")
            self._closing_receipts[call_id] = receipt
            self._pending.pop(call_id, None)
            out.append(receipt)
        return tuple(out)


# ── 那个 async 薄适配（接线那一刀）────────────────────────────────────
def make_run_tools(dispatcher: Dispatcher) -> Callable[[Sequence[ToolCall]],
                                                       Awaitable[DispatchResult]]:
    """Bridge the round's async slot to serial native-async dispatch.

    No thread migration, retries, parallel handlers, parameter rewriting or
    exception swallowing. Legacy sync-only runner doubles retain their seam.
    """
    async def run_tools(calls: Sequence[ToolCall]) -> DispatchResult:
        arun = getattr(dispatcher, "arun", None)
        if arun is None:
            return dispatcher.run(calls)
        return await arun(calls)

    run_tools.settled_receipts = getattr(dispatcher, "settled_receipts", None)
    run_tools.receipt_shutdown_state = getattr(dispatcher, "receipt_shutdown_state", None)
    run_tools.receipt_coverage = getattr(dispatcher, "receipt_coverage", None)
    return run_tools
