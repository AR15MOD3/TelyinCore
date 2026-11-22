""" **用法账** —— 一条记忆**被用过几次**（2026-10-07「用法信号」那一刀）。

⚠️ 为什么单立一本： ③ 那一刀要"**按用过几次**定去留"，而**信号之前不存在** ——
   核心那一本**每轮都注入**（无区分度 ）；"它在对话里引用过"系统看不见 。
   ⇒ 真正有区分度的是：**它回头去查，而且真的命中了某一条**
     （`kb_search` ／ `memory_search` ／ `profile_read` ／ `pending_read` 那一族读口 ）。

⚠️ 这一本**只记一件事**：`谁 ／ 哪一份的哪一条 ／ 什么时刻`（append-only 不改前面的 ）。
    **不判断、不排序、不算权重、不算天数** —— "谁该走"那件事归调用方
     （ 而它今天也还没接上去：**撤哪一条仍然由人挑** 见 `12` 那一节）。

⚠️ **本模块一次都不读时钟**（本仓那条老规矩 ⇒ 时刻与"哪一天"都由调用方**递** ）。

⚠️ ⚠️ **它记的是"命中"，不是"注入"** —— 每轮都进 system 的那种**不算**（算了就没有区分度 ）。
"""

from __future__ import annotations

from .append_journal import append, rows
import pathlib
from typing import Any

#: 那本账的名字（放在**调用方递来的那个目录**底下 —— 它是服务那一侧的东西）。
THE_BOOK = "used.jsonl"

#: 哪些读口算"用过"（ 白名单：**只认真的回头去查的那几个** ）。
THE_READERS = ("kb_search", "memory_search", "profile_read", "pending_read", "relations_read")


def a_line(*, reader: str, key: str, stamp: str, day: str = "") -> dict[str, str]:
    """ 造一条（ 读口名 ／ 那一条的名字 ／ 时刻 —— 三样缺一不收 ）。"""
    which = " ".join(str(reader).split())
    name = " ".join(str(key).split())
    if not which:
        raise ValueError("没说是哪一个读口 → 不收")
    if not name:
        raise ValueError("没说命中的是哪一条 → 不收")
    when = str(stamp).strip()
    if not when:
        raise ValueError("没给时刻 —— 本模块不读钟，时刻得由调用方递")
    return {"reader": which, "key": name, "stamp": when,
            "day": str(day or when[:8])}


def put(base: Any, *, reader: str, key: str, stamp: str, day: str = "") -> dict[str, str]:
    """ **记一笔** —— 追加进那本账（一个字节都不改前面的 ）。"""
    one = a_line(reader=reader, key=key, stamp=stamp, day=day)
    path = pathlib.Path(base) / THE_BOOK
    append(path, one)
    return one


def the_lines(base: Any, *, key: str = "") -> list[dict[str, str]]:
    """ 那本账里的几笔（给了 `key` ⇒ 只要那一条的 ；坏掉的行**跳过**、不抛 ）。"""
    return [row for row in rows(pathlib.Path(base) / THE_BOOK)
            if not key or row.get("key", "") == key]


def the_counts(base: Any) -> dict[str, int]:
    """ **每一条各被用过几次** —— 这就是那条信号（"谁该走"由调用方拿它去定 ）。"""
    out: dict[str, int] = {}
    for one in rows(pathlib.Path(base) / THE_BOOK):
        name = one.get("key", "")
        if name:
            out[name] = out.get(name, 0) + 1
    return out


def the_count(base: Any, *, key: str) -> int:
    """ 那一条被用过几次（没有 ⇒ 0 ）。"""
    name = str(key)
    return sum(1 for row in rows(pathlib.Path(base) / THE_BOOK)
               if name and row.get("key", "") == name)
