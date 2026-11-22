# telyincore/reserved.py
"""框架保留名的统一判断入口，供 tools.build_table 默认使用。

当前集合为空，因此 is_reserved 总是 False；待定责任见 _OWED_BY。
新增框架占用名时更新此处，不在装配层另建判断表。"""
from __future__ import annotations

#: 框架自己占着的名字。**今天是空的** —— 理由见模块开头那三句（不是忘了填）。
RESERVED_NAMES: frozenset[str] = frozenset()

#: **谁欠这个集合** —— 空集合也必须有人认领（`loop.py:60-63` 那条家规：
#: 「一个传进来却没人用的东西，要说清它还没接上，不许让它静默无效」）。
#: ⚠️ 私有，而它由 `tests/test_tools.py` 那条钉着（"空也要有欠主"）。
_OWED_BY = "`bus.py` 那一侧的投递帧表头（`markers.py` 开头点名过：投递帧表头归总线）"


def is_reserved(name: str) -> bool:
    """这个名字是不是框架自己占着的？

    ⚠️ 它是 `tools.build_table(..., reserved=…)` 的**默认**那一份 ——
       装配层要换一套保留名，递一个自己的谓词进去就行（本支的规矩：能力从外面递进来）；
       而"默认这一份"与"`tools.py` 里实际用的那一份"**必须是同一个函数**（见模块开头末段）。
    """
    return name in RESERVED_NAMES
