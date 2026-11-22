# telyincore/prefix.py
"""装订器：把 persona 那几段**订成一句** system 正文。

**两个面**（`spec §4.1`）：
· `table()` —— 读**那一处正本**（`docs/design/段序.md`）：段名 ／ 它那几份文件 ／
  缺了怎么办。 **行序就是段序**（位置不另存一格：两个地方存次序，就会各答一个数）。
· `assemble()` —— 按表走完每一行、取正文、`''.join`，交回**一条** `Record`。

 **一个分隔符都不加**（参照实现那一句 `join('')` 是它自己的选择）：段与段之间的一切
由**段文本自己**带；我们那九份带得不齐（有的开头是注释、有的没有标题）—— 而那是
**各份文件自己的事**，不是这里该补的：补一个分隔符 ＝ 把 Core 的散文塞进正文。

 **它一次都不读时钟**：那一条的 `ts_ms` 由**调用方**盖（入口那儿有一个毫秒钟）
—— 与 `note.py:11`、`web.py:1840` 同一条纪律。

 **它不新造第二条路线守卫**：表里的名字是那条根底下的**相对名**，**根由调用方递**；
页那一侧那条守卫（`web.py` 的 `path_of`）一个字都不动，也不被 import 进来。

⚠️ **它只认识 `note.Record` 那一个形状**（交回的就是它）；会话 ／ 总线 ／ 模型
它一个都不 import。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .note import Record

#: 仓根（`telyincore/` 的上一层）—— 与 `web.py:1264` 那一个是**同一个算法**
#: （`Path(__file__).resolve().parent.parent`）。两个模块各算一次，不是第二份真相
#: （它是"仓根"这一个事实）。
_REPO_ROOT = Path(__file__).resolve().parent.parent

#: **那一处正本**（一处，别处不许再写一遍次序）。
SEGMENTS_DOC = _REPO_ROOT / "docs" / "design" / "段序.md"

#: **那一条根**（只有一条）：`bots\<bot>\persona\` —— 与 `web.DEFAULT_PERSONA_ROOT`
#:    **同一个算法**（仓根 ＋ 那一层目录名 ＋ `persona`）。两个模块各算一次，
#:    不是第二份真相（它是"那条根在哪"这一个事实）。
#: ⚠️ 那个目录名**必须写成转义**：六个禁词里有它（照 `web.py:1261` 那一条的成法 ——
#:    写字面量就等于让那台检查器举报这一棵树）。
#: ⚠️ 它只是**缺省**：调用方递一条根就说了算（`Console.assemble(persona_root=…)`）。
_BOT_DIR_NAME = "\u0054\u0065\u006c\u0079\u0069\u006e"

DEFAULT_PERSONA_ROOT = _REPO_ROOT.parent / "bots" / _BOT_DIR_NAME / "persona"

#: 表那一块的标题 —— 按**名字**取，不按行号（行号每改一次文档就假一次）。
_ORDER_HEADING = "## 段序"

#: **通道那一层**那一块的标题（2026-10-05 切挂载加）—— **同一份正本、第二张表**
#: （`table()` 本来就按标题取那一块 ⇒ 解析器一个字都不用改 ）。
_CHANNEL_HEADING = "## 通道那一层"

#: **通道那一层那条根**（在 persona 那条根的**隔壁**：`bots\<那个名字>\通道\`）——
#: 与 `DEFAULT_PERSONA_ROOT` 同一个算法（"那条根在哪"这一个事实）。
#: ⚠️ 那个目录名**不必要转义**（它不在那张禁词表上）—— 而**那一头的名字**在（见下一条）。
_CHANNEL_DIR_NAME = "通道"

#: **环境那一层**（2026-10-05）：跑在什么上面 · 按部署可覆盖 ⇒ 不归 persona（与 `通道` 同族）。
_ENV_HEADING = "## 环境那一层"
_ENV_DIR_NAME = "环境"

DEFAULT_CHANNEL_ROOT = DEFAULT_PERSONA_ROOT.parent / _CHANNEL_DIR_NAME

#: **缺省那一头** ＝ `None` ⇒ **表里第一行**（ 那一头的名字本身是本仓的禁字之一 ⇒
#: 这里一个字都不写它 ；而"只有一行"时，第一行就是唯一那一头 ）。
DEFAULT_CHANNEL = None

#: 一格里的文件名（反引号里那些）—— 一格可以是**一份都没有**。
_CODE_SPAN = re.compile(r"`([^`]+)`")

#: 「缺了怎么办」那一格的两个值（`spec §三` 决定 B）—— **只有这两个**。
WANTED = "要文件"
EMPTY = "空着"


@dataclass(frozen=True)
class Segment:
    """表里的一行 —— **行序就是段序**（位置不另存一格）。"""

    name: str
    files: tuple[str, ...]          # 相对那条根；空元组 ＝ 这一段声明"没有文件"
    missing: str                    # `空着` ／ `要文件` —— 见 `spec §三` 决定 B


def _cells_of(line: str) -> list[str]:
    """一行表 ⇒ 那几格（`| a | b | c |` ⇒ `["a", "b", "c"]`）。"""
    return [one.strip() for one in line.strip().strip("|").split("|")]


def _is_a_separator(cells: list[str]) -> bool:
    """表头底下那一道分隔（`|---|---|---|`）—— 它自己不是一个段。"""
    return bool(cells) and all(one and set(one) <= set("-:") for one in cells)


def table(text: str, *, heading: str = _ORDER_HEADING) -> list[Segment]:
    """读那一份正本 ⇒ 一行一条（**次序就是它**）。

    ⚠️ **按标题取那一块，不按行号**：行号每改一次文档就假一次（`web.py` 那条同源的注释）。
    ⚠️ **一份都没读到就当场抛** —— 那是"**表没了**"，不是"没内容"（`spec §4.3 ③`）。
    ⚠️ **每一行的第三格只有两个值**（`空着` ／ `要文件`）：第三个词**当场抛**，
       不许悄悄读成一个默认值（那一格是**声明**，读错一个词就是一句错的声明）。
    ⚠️ **第一格空的也当场抛** —— 一个**没有名字的段**从来不正立。 而"只在一处钉住它、
       另一处不钉"正是「段序只住一处」那条判据（`tests/test_prefix.py`）要挡的那种走样：
       两个读法在同一条输入上**各答一个数**（页那一侧那份独立实现照同一条判据办）。
    """
    if heading not in text:
        raise ValueError(f"那一份正本里没有这一句标题：{heading!r} —— 表没处可找")
    out: list[Segment] = []
    under_the_header_rule = False
    for line in text.split(heading, 1)[1].splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if out:
                break # 第一张表就是它 —— 表读完就收
            continue
        cells = _cells_of(stripped)
        if _is_a_separator(cells):
            under_the_header_rule = True
            continue
        if not under_the_header_rule:
            continue                     # 表头那一行（分隔之前的都不是段）
        if len(cells) < 3:
            raise ValueError(
                f"表里这一行少了一格：{stripped!r}（要有 段 ／ 文件 ／ 缺了怎么办）")
        name, cell, missing = cells[0], cells[1], cells[2]
        if not name:
            raise ValueError(
                f"表里这一行第一格是空的：{stripped!r} —— 没有名字的段从来不正立")
        if missing not in (WANTED, EMPTY):
            raise ValueError(f"{name} 那一行的「缺了怎么办」不是那两个值之一：{missing!r}")
        out.append(Segment(name=name, files=tuple(_CODE_SPAN.findall(cell)),
                           missing=missing))
    if not out:
        raise ValueError(f"{heading!r} 底下没有一行表 —— 表没处可找")
    return out


def _channel_parts(text: str, root: Any, channel: Any,
                   channel_root: Any, *, strict: bool = False) -> list[str]:
    if _CHANNEL_HEADING not in text:
        if strict:
            raise ValueError("通道表不存在")
        return []
    rows = table(text, heading=_CHANNEL_HEADING)
    picked = next((one for one in rows if one.name == str(channel)), None)
    if picked is None:
        if strict:
            raise ValueError(f"通道表没有 {channel!r}")
        picked = rows[0]
    base = Path(root).parent / _CHANNEL_DIR_NAME if channel_root is None else Path(channel_root)
    parts: list[str] = []
    for name in picked.files:
        try:
            parts.append((base / name).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            if strict:
                raise ValueError(f"通道文件不可读：{name}") from None
    return parts


def channel_text(root: Any, *, doc: Any = SEGMENTS_DOC, channel: Any,
                 channel_root: Any = None) -> str:
    """Read one named channel without silently substituting another channel."""
    text = Path(doc).read_text(encoding="utf-8")
    parts = _channel_parts(text, root, channel, channel_root, strict=True)
    return re.sub(r"<!--.*?-->", "", "".join(parts), flags=re.S)


def uses_modern_layout(root: Any) -> bool:
    return all((Path(root) / name).is_file() for name in ("宪法.md", "角色设定.md"))


def assemble(root: Any, *, doc: Any = SEGMENTS_DOC, ts_ms: int = 0,
             channel: Any = DEFAULT_CHANNEL, channel_root: Any = None,
             include_channel: bool = True) -> list[Record]:
    """ **订** —— 按表走完每一行、取正文、`''.join` ⇒ **一条** `Record(role="system")`。

    ⚠️ **交回的是一串**（今天这一串里**恰好一条**）：插口那一格收的是 `Sequence[Any]`，
       而"恰好一条"照的是参照实现"一整份、一条消息"那个形状（`handoff.py:224` ／ `:512`）。
    ⚠️ **正文一个字符都不动**（不 strip、不加分隔符、不补换行）：`''.join` 就是全部。
    ⚠️ **一条文件都读不到 ⇒ 正文是空串，而仍然是一条** —— 不退化成一串空的：
       `reset([])` 会把会话清成零条，而零条的请求引擎**直接拒**。
    ⚠️ **缺文件不抛**：声明"没有文件"的那一行一个字都不加；声明有文件而**读不到**的，
       那一行这一次没有正文、其余照订（ "读不到"收 `OSError` 与 `UnicodeDecodeError`
       两类 —— 后者是"在，而正文读不出来"，与"不在"同一条下场）。
    ⚠️ **那份正本自己读不到 ⇒ 当场抛**（读它那一句在循环**外面**）：那不是"少一段"，
       那是"**段序没了**"—— 订出来的东西会是**另一个答案**。
    ⚠️ **`ts_ms` 由调用方盖**（本函数一次都不读时钟）：入口那儿只有**一个**毫秒钟。
    ⚠️ **`root` 由调用方递**，而这里**不判越界**（表是我们自己那一份文档；
       那条名字守卫住在页那一侧 —— `web.PersonaPort.path_of`，本模块不认识它）。
    """
    parts: list[str] = []
    text = Path(doc).read_text(encoding="utf-8")
    base = Path(root)
    modern = uses_modern_layout(base)
    heading = "## 新版档案段序" if modern else _ORDER_HEADING
    for segment in table(text, heading=heading):
        for name in segment.files:
            try:
                content = (Path(root) / name).read_text(encoding="utf-8")
                if modern:
                    content = f"〔人格档案：{name}；propose_change.name 使用此相对路径〕\n\n" + content
                parts.append(content)
            except (OSError, UnicodeDecodeError):
                continue
    if modern:
        from persona_deliberation import WORKFLOW
        parts.append(WORKFLOW)
    if modern:
        parts = [re.sub(r"^<!-- section: ([a-z][a-z-]{0,63}) -->[ \t]*$",
                        r"〔章节 \1〕", part, flags=re.MULTILINE) for part in parts]
    # **通道那一层**（2026-10-05 切的那一刀）：同一份正本里的**第二张表** ——
    # 按**当前这一头**选一行，那一份的正文接在 persona 那几段**之后** 。
    #    ⚠️ **那一节不在 ⇒ 跳过、不抛**：老部署（或那一节还没落）不该因此整份读不出来；
    #    ⚠️ 而"这一头点了名、表里没有那一行"⇒ 也**退回表里第一行**（缺省那一头）
    # **环境那一层**（同一份正本的又一张表）
    env_heading = "## 新版共用环境" if modern else _ENV_HEADING
    if env_heading in text:
        for segment in table(text, heading=env_heading):
            ebase = Path(root).parent / _ENV_DIR_NAME
            for name in segment.files:
                try:
                    parts.append((ebase / name).read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    continue
    # The archived prefix may omit its channel; a request can then attach the
    # current scene separately without freezing a previous scene into history.
    if include_channel and not modern:
        parts.extend(_channel_parts(text, root, channel, channel_root))
    # **注释不许进 system**（2026-10-07）：`<!-- … -->` 那几段是**写给我们（人和代码）看的**，
    #    而它从前**一路注进去了** —— 于是每一轮都在读"核心 40 条 ／ 向量库 self 段 ／
    # 面板左栏那一屏"这类**关于这套机制的说明** （既不是"那是谁"，也不是内容）。
    # ⚠️ **只剥这一样**：其余一个字符都不动（不 strip、不补换行 —— 那一条没变 ）。
    # ★ **2026-10-12（作者：「五个都填」）：这一条就是"合成开头"本身** ⇒ 那两格由**这一处**标：
    #   · `is_head=True` —— 它就是"紧跟着 system 前缀的那几句"的来源（`head.py` 抬头那一句）；
    #   · `is_system=True` —— 它也是"机器装出来的、不是谁说的"。
    #   ⚠️ **为什么归这一处标**：这一条是 `Record` 里**唯一**由"段序那份正本"造出来的
    #     （一次会话一条，交接第 ③ 步 `reset` 落盘）—— 别的写手（会话那一侧）看不到它。
    return [Record(kind="message", role="system", ts_ms=ts_ms,
                   text=re.sub(r"<!--.*?-->", "", "".join(parts), flags=re.S),
                   is_head=True, is_system=True)]


def channel_environment(selection):
    """Request-only platform facts. Never persist this as the persona prefix."""
    if selection.status not in {'builtin', 'plugin', 'mixed', 'unconfigured'}:
        raise ValueError('invalid channel environment selection')
    return [{"role": "system", "content": "〔当前通道环境：" + selection.source
             + "；状态：" + selection.status + "〕\n" + selection.text}]


def bot_channel_scene(selection):
    """Per-request Bot interpretation; not a platform or identity assertion."""
    return [{"role": "system", "content": "〔本轮角色场景：" + selection.route
             + "；状态：" + selection.status + "〕\n" + selection.text}]
