# telyincore/seal.py
"""「封住写库出口」这一件事 —— **一处概念、一份真相**。

⚠️ **为什么它单独一个文件**：两本账本（`events.py` ／ `log.py`）**互相不认识**
（本仓的模块之间靠鸭子类型耦合），而它们**要抛同一个类型、要看同一个门卫**
⇒ 那个类型与那个门卫必须有**第三个家**。
两本各定义一份就会出现"两个类型" ⇒ `except` 得写两遍、且迟早漂。
"""
from __future__ import annotations

from collections.abc import Callable


class SealedError(RuntimeError):
    """库已封存时拒绝写入的专用异常。

继承 RuntimeError 以兼容原处理路径；调用方可按类型区分封存拒写与其他错误，
不依赖错误措辞，也不静默丢弃迟到写入。"""


#: 门卫的形 —— **他只答一件事："门还开着吗？"**
#: ⚠️ **用 `is_open` 而不是 `is_sealed`**：调用点是 `if not gate(): raise` ——
#:    而那个 `not` 把两件事摆在同一行（**开门才写**）。反过来写（`is_sealed`）会让调用点读成
#:    "**被封了才拦**"，而真正的意思是"**只有开着才许写**"：前者是许可、后者是禁止。
Gate = Callable[[], bool]


def always_open() -> bool:
    """默认门卫：**一直开着**。

    ⚠️⚠️ **它是个活陷阱，而这是刻意的、且必须点名**：不显式递门卫时，
    "没有门卫"与"门卫恒开"**在签名上长得一样** ⇒ 一个本该被封的写会悄悄过去。
    ⇒ 生产路径**必须显式递一个**（由 `app.build()` 造）—— 而"递没递上"由**两条测试分工钉**，
    别读成一条：
    · `tests/test_app.py::test_build_wires_a_real_gate_and_not_the_always_open_default`
      —— 钉的是"`build()` 手上那个门卫**是真的**"（`App.gate` 不是这个恒真默认）；
      而它**一次都不碰两本账本** ⇒ **一本账本静默拿到 `always_open` 它看不见**。
    · `tests/test_app.py::test_the_gate_reads_the_loops_sealed_flag_end_to_end`
      —— **上面那条看不见的那一半归它杀**：`loop.seal()` 之后从**公开写口**写一次，
      `append` ／ `append_line` 各要抛一次 `SealedError`。
      ⚠️⚠️ **而别把它的功劳说满**（复评 `c67c5f4e` 实测）：把 `EventStore` 那条**接错**时
      它照样红（错接的 `append` 会重复抛 `SealedError`）；而把 `LogStore` 那条接错时，
      `append_line` **会真的写成功** ⇒ **两条测试都不红**
      ⇒ 也就是说：**这条端到端今天只盯得住"`EventStore` 那一半"** ——
        另一本要等"**真的有人从 `App.log` 上写**"那一天才有人盯
（删掉这一段的条件就写在 `app.py` 的 `build()` 里）。
    ⚠️ **不把它做成必填**：那会把 100+ 条既有构造全拖进来，而它们**不要**门卫是对的
    （它们是纯粹测"写进去没有"，不是测关机）。
    """
    return True
