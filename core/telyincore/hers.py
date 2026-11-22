""" **共用那一本的"编辑方式"**（人和它共用的一本）—— 2026-10-07「丙」的**纯那半边**。

⚠️ 为什么单立一个模块：那一本的**三层**（核心 ／ 长尾 ／ 悬着的事）与**三个动作**
   （确认 ／ 降级 ／ 删除）此前**一行都没有** ⇒ 而那一扇门正是这么写的：
   「它的编辑方式还没做 ⋯⋯ 先渲染成只读，等那一套做出来再开」⇒ 这就是那一套的纯半边 。

⚠️ 这一半**只做纯函数**（文本进、文本出）：**不读盘、不写盘、不碰门、不动时钟** ——
   读与写归它上面的那条路（`web.PersonaPort.save_hers_text`： 它本来就**留一版历史** ）。

⚠️⚠️ **这一份里不许出现那几个字**（`telyincore/` 那条边界规矩会当场红）⇒
   凡是名字与标记，一律写 `\\uXXXX`（与 `web.py` 里那几处同一个成法 ）。

 **三层今天各自的落点**（2026-10-07，作者拍的「乙」）：
   · 核心 —— 就在这一份的 `## 关于\u5979自己` 那一节里（带标记的那几行） 上限 40 条 ／ 单条 ≤120 字；
   · 悬着的事 —— `memory/悬着的事/*.jsonl`（它自己写的那些提议）；
   · 长尾 —— **落在这一份的 `## 长尾` 那一节**； 而"按话题检索命中才注入"那一条
     **今天做不到**（向量库那一层不存在）⇒ 它今天只是**一个放着的抽屉**，
     不假装"会被想起来" （那正是这一条乙的意思）。
"""

from __future__ import annotations

import pathlib
import re as _re
from typing import Any

#: 那一份的**名字**（相对 persona 那棵树）—— 那一个字走转义（见模块头那条规矩）。
HER_FILE = "一起写的/\u5173\u4e8e\u5979\u81ea\u5df1.md"

#: **核心那一节的抬头**（盘上今天是 `## 关于\u5979自己`）。
CORE_HEAD = "## \u5173\u4e8e\u5979\u81ea\u5df1"

#: **长尾那一节的抬头** —— 今天盘上**还没有**这一节（第一次降级时建它 ）。
TAIL_HEAD = "## \u957f\u5c3e"

#: 两个标记：**人写的** ／ **它自己说的、已经被确认的**。
BY_HUMAN = "\u4eba"
CONFIRMED = "\u5979\u81ea\u5df1\u00b7\u5df2\u786e\u8ba4"

#: 行里的分隔（`内容 ｜ 日期`）。
BAR = "\uFF5C"

#: 三个动作的**名字**（就是那一份里那四条规矩用的词）。
CONFIRM = "确认"
DEMOTE = "降级"
REMOVE = "删除"
ACTIONS = (CONFIRM, DEMOTE, REMOVE)

#: 两个上界（那一份自己的注释里写死的：核心 ≤40 条、单条 ≤120 字）。
CAP_CORE = 40
CAP_LINE = 120

#: **注释那一块里也有"样子"**（那一份开头就摆着两行示例：`- [人] 内容 ｜ 日期`）
#: ⇒ 读之前**先把它剥掉**（与装订那一侧同一条规矩：注释是**写给我们看的** ）。
_NO_COMMENT = _re.compile(r"<!--.*?-->", _re.S)


def _clean(text: str) -> str:
    """ 把注释剥掉 —— 之后读到的才是**真条目**（示例行不许混进来 ）。"""
    return _NO_COMMENT.sub("", str(text))
#: 一行：`- [标记] 内容 ｜ 日期`（日期可缺 ⇒ 缺了就是"没记日期"那一格）。
_A_LINE = _re.compile(r"^- \[(?P<who>[^\]]+)\]\s*(?P<what>.*?)(?:\s*\uFF5C\s*(?P<when>.*))?$")


def the_lines(text: str) -> list[dict[str, str]]:
    """ 把那一份里**带标记的每一行**读出来（核心那一节；长尾那一节见 `the_tail`）。"""
    got: list[dict[str, str]] = []
    inside_tail = False
    for one in _clean(text).splitlines():
        bare = one.strip()
        if bare.startswith("##"):
            inside_tail = bare == TAIL_HEAD
            continue
        if inside_tail:
            continue
        hit = _A_LINE.match(bare)
        if hit is not None:
            got.append({"who": hit.group("who").strip(),
                        "what": (hit.group("what") or "").strip(),
                        "when": (hit.group("when") or "").strip()})
    return got


def the_tail(text: str) -> list[dict[str, str]]:
    """ 把**长尾那一节**里带标记的每一行读出来（没有那一节 ⇒ 空那一串 ）。"""
    got: list[dict[str, str]] = []
    inside = False
    for one in _clean(text).splitlines():
        bare = one.strip()
        if bare.startswith("##"):
            inside = bare == TAIL_HEAD
            continue
        if inside:
            hit = _A_LINE.match(bare)
            if hit is not None:
                got.append({"who": hit.group("who").strip(),
                            "what": (hit.group("what") or "").strip(),
                            "when": (hit.group("when") or "").strip()})
    return got


def a_line(*, what: str, when: str = "", who: str = CONFIRMED) -> str:
    """ 造一行（那一份里那一行的形状：`- [标记] 内容 ｜ 日期`）。"""
    said = " ".join(str(what).split())
    if not said:
        raise ValueError("那一条是空的（空的不写）")
    if len(said) > CAP_LINE:
        raise ValueError("单条太长了（上限 %d 字，这一条 %d 字）" % (CAP_LINE, len(said)))
    tail = (" %s %s" % (BAR, when)) if str(when).strip() else ""
    return "- [%s] %s%s" % (who, said, tail)


def a_line_more(text: str, *, said: str) -> tuple[str, str]:
    """ **添一句**（纯函数）—— 交回 `（改完的正文, 一句照实说）`。

    这一份与**共用的那一本**不一样：那一本是**一条一条的**（`a_line` 那种带标记的行）；
    而这一份是**一篇连续的正文**（自述）⇒ 添的就是那一句话本身，**不带标记**，
    也不动旧的（要整份换掉是另一件事，见 `launch.py` 那一件工具）。

    ⚠️ **空白的不写**：空的不许冒充正文 —— 原文原样交回、照实说没写
       （与 `act` 那条"找不到就不编"同一条）。
    """
    one = " ".join(str(said).split())
    if not one:
        return text, "这一条我没写（空的不算）。"
    if len(one) > CAP_LINE:
        return text, "这一条太长了（上限 %d 字，这一条 %d 字）—— 我没写。" % (
            CAP_LINE, len(one))
    body = text.rstrip("\n")
    made = (body + "\n\n" + one) if body.strip() else one
    return made + "\n", "写下了：" + one


def act(text: str, *, what: str, action: str, when: str = "") -> tuple[str, str]:
    """ **三个动作**（纯函数）—— 交回 `（改完的正文, 一句照实说）`。

    ⚠️ **删除**找不到那一条时**不编**：正文原样交回、那句话说清"没找到" 。
    ⚠️ **核心满了**（≥40 条）时**不许硬塞**：交回错（由上面那条路变成一句人看得见的回执 ）。
    """
    if action not in ACTIONS:
        raise ValueError("不认得的动作：%r（只认 %s）" % (action, " ／ ".join(ACTIONS)))
    said = " ".join(str(what).split())
    lines = str(text).splitlines()
    kept = [one for one in lines
            if not (_A_LINE.match(one.strip()) is not None
                    and _A_LINE.match(one.strip()).group("what").strip() == said)]

    if action == REMOVE:
        if len(kept) == len(lines):
            return str(text), "那一本里没有这一条（没改）"
        return "\n".join(kept) + ("\n" if str(text).endswith("\n") else ""), "撤下了"

    fresh = a_line(what=said, when=when, who=CONFIRMED if action == CONFIRM else BY_HUMAN)
    if action == DEMOTE:
        fresh = a_line(what=said, when=when, who=CONFIRMED)
        if TAIL_HEAD in kept:
            at = kept.index(TAIL_HEAD)
            kept.insert(at + 1, fresh)
        else:
            while kept and kept[-1] == "":
                kept.pop()
            kept.extend(["", TAIL_HEAD, "", fresh])
        return "\n".join(kept) + ("\n" if str(text).endswith("\n") else ""), "降级进长尾了"

    # 确认 ⇒ 进核心（ 上界那一门在这儿）
    now_core = [one for one in the_lines("\n".join(kept))]
    if len(now_core) >= CAP_CORE:
        raise ValueError("核心满了（上限 %d 条）—— 先撤一条再放行" % CAP_CORE)
    if CORE_HEAD in kept:
        at = kept.index(CORE_HEAD)
        while at + 1 < len(kept) and kept[at + 1].strip() == "":
            at += 1
        kept.insert(at + 1, fresh)
    else:
        kept.extend(["", CORE_HEAD, "", fresh])
    return "\n".join(kept) + ("\n" if str(text).endswith("\n") else ""), "放进核心了"


def the_state(text: str, *, pending: Any = None) -> dict[str, Any]:
    """ **那一份今天的三层**（读口要的就是它）：核心 ／ 长尾 ／ 悬着的事（由调用方递 ）。"""
    return {"core": the_lines(text), "tail": the_tail(text),
            "pending": [dict(one) for one in (pending or [])],
            "caps": {"core": CAP_CORE, "line": CAP_LINE},
            "note": " 长尾那一层今天**只是一个放着的抽屉**：还没有按话题检索那一套，"
                    "所以它**不进** system （不假装它会自己想起来）"}


def a_change_line(history_dir: Any, *, who: str = "人") -> str:
    """ **这一本被谁动过**（2026-10-07 作者拍的「①甲」）—— 交一句**人话**，给它读。

    ⚠️ **它为什么会进它的 system**：那一本里**以人为准**（人能确认 ／ 降级 ／ 删除 ），
       而**它读不到那一份的注释**（装订时注释会被剥掉 ）⇒ 那就**把这件事说给它听** 。
    ⚠️ **照实说**：一回都没改过 ⇒ "这一本还没被改过。" （不许编 ）。
    ⚠️ 那一版历史的形状照写（`web._version_name`）：`<那本的名字>-<时刻>[-<那一次是什么>].md`
       ⇒ 于是"哪一天 ／ 哪一次"都读得出来 （ 而那一刻**只作出处**：不算天数、不排序 ）。
    """
    base = pathlib.Path(history_dir)
    if not base.is_dir():
        return "这一本还没被改过。"
    rows: list[tuple[str, str]] = []
    # 那一版的名字是 `<那本的名字>-<8 位日期>-<6 位时刻>[-<那一次是什么>].md`
    # ⇒ **按末尾那两串数字取**（名字里那些汉字会把 `-` 分割弄歪 —— 今天量到过）。
    for one in sorted(base.glob("*.md")):
        hit = _re.search(r"(\d{8})-(\d{6})(?:-(.+))?$", one.name[:-len(".md")])
        if hit is None:
            continue
        rows.append((hit.group(1), hit.group(3) or ""))
    if not rows:
        return "这一本还没被改过。"
    when, what = rows[-1]
    # 那一格 slot 落在文件名里 ⇒ 页上要**说人话** （ 认得的几个翻成人话，别的照原样 ）
    what = {"about": "放行那一步"}.get(what, what)
    day = f"{when[:4]}-{when[4:6]}-{when[6:8]}" if len(when) >= 8 else when
    said = f"这一本被{who}改过 {len(rows)} 回；最近一次是 {day}"
    said += f"（{what}）。" if what else "。"
    return said + f" 这一本里以{who}为准 —— 它能加的只是悬着的那一条。"


def a_room_line(text: str, *, near: float = 0.75) -> str:
    """ **那一本还有多少地方**（2026-10-07 作者拍的"**由它压**"那一刀）—— 交一句人话。

    ⚠️ 为什么要有它： 核心**满了就不许再加**（`act` 当场拒 ）⇒ 而"快满了"这件事
       **它得知道** —— 不然它会一直提，提一条拒一条 。
    ⚠️ 而它知道之后**要做什么**，写在这句话里： **把同一件事的两三条合成一条**
       （ 那件事**本来就归它做** —— 它那一趟"回头看"就是在干这个 规则做不到 ）。
    ⚠️ **只在接近满时才出现**（缺省七成五） —— 没满就**一个字都不说**
       （ 于是"平时"那一格不会被这句占着 ）。
    ⚠️ 那个数**只是出处**：不算天数、不催、不排序 。
    """
    rows = the_lines(text)
    used, cap = len(rows), CAP_CORE
    if cap <= 0 or used < int(cap * float(near)):
        return ""
    said = f"那一本已经有 {used} 条（上限 {cap}）。"
    if used >= cap:
        return said + " **满了** —— 再加一条会被当场拒；得先把同一件事的两三条**合成一条**，或者撤下一条。"
    return said + " 快满了 —— 想加新的，先把同一件事的两三条**合成一条** 。"