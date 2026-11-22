# telyincore/head.py
"""合成开头：每次出请求时插在 system 前缀之后、持久历史之前的那几句。

**为什么它值得单独一个模块：**
那几句是**每次请求都一样**的（逐字固定）⇒ 前缀命中缓存 ⇒ 便宜、快。
⇒ 所以**它必须与上一次逐字相同** —— 这就是为什么它**不落盘**
  （落盘就会被别的东西改，而改一次就废掉一次缓存）。

⚠️ **它不是"重建新会话"。** 重建新会话是 `session.reset([...])`，那是 S5 的另一半。
⚠️ **它里面那三个 item id 是硬的** —— 那是为了让每次请求的前缀逐字相同（同上）。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

#: ⚠️ 这三个 id 是**硬固定**的 —— 内容不变时每次请求的前缀逐字相同 ⇒ 命中缓存。
#: 改它们等于主动废掉缓存（而那是钱与延迟）。
FIRST_TURN_IDS = ("msg_first_turn_user", "rs_first_turn", "msg_first_turn_reply")

#: 这两个 role 的**消息**条目**一律丢掉** —— 而它们**不是**同一种东西，理由也不一样：
#: ⚠️ **这句话原来写的是「这两个 role 的条目一律丢掉」—— 那是过度声称** ：
#:    代码还要求 `type == "message"`，所以 `Item(type="note", role="system")` **会被留下**
#: （而本节的测试正拿这一条断言它**留下**）⇒ **它说的比它做的大**
#: · `system` 有**独立理由**：system 前缀是**另外一条路**建的，而合成开头里再放一个 system
#:   ⇒ 请求里会出现两个 system，前缀就不再逐字固定 ⇒ 缓存失效
#:   （那条理由也写在 `test_system_items_are_dropped` 的注释里）。
#: · `developer` **只是同一条规则的另一半** —— 它**没有**独立理由：它跟着 system 一起丢。
_DROPPED_ROLES = ("system", "developer")


@dataclass(frozen=True)
class Item:
    type: str = "message"
    role: str = "user"
    id: str = ""
    text: str = ""


@dataclass(frozen=True)
class HeadRecord:
    item: Item
    head: bool = True


def head(
    items: list[Item],
    *,
    fix_pairing: Callable[[list[HeadRecord]], list[HeadRecord]] | None = None,
) -> list[HeadRecord]:
    """丢掉 system／developer 的**消息**条目，打上 head 标记，可选地**叫一次**配对修补。

    ⚠️ **首句原来写的是「丢掉 system/developer，打上 head 标记，可选地修一遍工具配对」
       —— 两处过度声称** ：
       ① 代码还要求 `type == "message"`（同 `_DROPPED_ROLES` 那处）；
       ② **"修"不是这个函数做的** —— 它只把回调**叫一次**（修补本身是 S6 的事）。

    ⚠️ **这里原来还有一个 `on_error_swallow: bool = False` —— 它已删除**
       那个参数想表达"读失败时不抛，只是这一次不注入"。而**读不在这个函数里**
       （它接的是现成的 `list[Item]`）⇒ 这个参数**收不到任何东西、也管不了任何事**，
       它只是一句**写在签名里的愿望**
       ⇒ 而真正的落点是**调用方**：读失败时给空 list ——
       ⚠️ **而"调用方那半"本仓不存在，也没有任何测试钉得住它**
          （这句原来写的是"这一条由 `test_an_empty_input_gives_an_empty_output` 钉住 "
            —— **指认过宽**）
          那条测试钉的是**空 list 进 ⇒ 空 list 出**那一半 ，不是调用方

    ⚠️ **标记在修补之后是最后一步** —— 因为修补可能返回**换了身份**的记录，
    而那一步的目的正是"让这一整串都带上 head"（见测试 `test_head_marks_survive_the_optional_fix`）。
    """
    kept = [i for i in items if not (i.type == "message" and i.role in _DROPPED_ROLES)]
    out = [HeadRecord(item=i, head=True) for i in kept]
    if fix_pairing is not None:
        out = fix_pairing(out)
    return [HeadRecord(item=r.item, head=True) for r in out]
