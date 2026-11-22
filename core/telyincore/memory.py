"""记忆层的**家**（设计稿 §一 ／ §六 P0①）。

 **这一层只做一件事：给经历一个家** —— 把"对话原话 ／ 交接笔记 ／ 整理态留下的话"搬到
   `bots/<谁>/memory/经历/` 底下，**机械地记**：不改写、不总结、不解释。

⚠️⚠️ 三条边界（照设计稿 §一）：
   ① 家是**它的**（`bots/<谁>/memory/`）⇒ **不进 git**（`.gitignore` 遮住 —— 与 `data/`
      那条同一个理由：本仓没有 remote，一旦提交就永远留在历史里）；
   ② 本模块**只做三件**里的第一件（**记事件**）；拼前缀 ／ 给工具面在别处；
   ③ **它不解释记忆的内容** —— 不调用模型、不调用传输：只读文件、只抄字节。

⚠️ 每条带三样（设计稿 §四 那三条"**只加字段，不加架构**"）：
   `ts`（什么时候）／ `source`（哪来的）／ `valid_from`（**从什么时候起有效**）。
   今天的源里只有 `ts_ms` ／ `role` ／ `text` ⇒ 另两样是**这一层补的**，而**正文一个字不改**。
"""
from __future__ import annotations
from .source_guard import writer as _source_writer

import datetime
import json
import hashlib
import math
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping

if TYPE_CHECKING:
    from .portrait_eligibility.provenance import Source as PortraitSource

# **做法那一层的治理**（2026-10-17 批③）：稳定 ID ／ 版本 ／ 状态 ／ 可逆合并。
# ⚠️ 它**不改正文**，只在 `做法/.ids.json` 里记权威元数据。
from . import skills as the_skills


#: 家里那几棵树（设计稿 §二 ① 的三样，一样一棵）。
#: **2026-10-05 加了第四栵**：`经历/手写` —— 一段**手写的经历**
#: 那一类东西的落点：它**不是从会话投影来的**（投影不碰它 ），
#: 而它**该被查得到**（与那三栵同一个待遇：`search()` 扫它们）。
TREES: tuple[str, ...] = ("经历/对话", "经历/笔记", "经历/整理",
                         "经历/手写")

#: **「人」那一棵树**（设计稿 §一："＋将来的 `bots/<谁>/画像/`" ／ §三 画像）——
#: 与"经历"分开摆：经历是**框架机械地记**的，画像是**它自己编辑**的（§二 ②）。
PEOPLE_TREE = "画像"

#: **知识那一棵树**（设计稿 §二 ②："**知识**（**文件式**）"）——
#: 一个个 `.md`，它自己的字；与"经历"分开摆（那是框架机械地记的）。
KNOWLEDGE_TREE = "知识"


#: **注释那一块**（`<!-- … -->`）—— **写给我们（人和代码）看的，不进它眼前** 。
#: （2026-10-10 加；来源是一次实测 ：`kb_search` 交回的是**整份正文**，
#: 于是它搜「图书馆」就读到了「 它**不进常驻层**（无关时不占那一边的眼前）」——
#: 那是**关于这套机制的说明**，不是它世界里的东西 。）
#: ⚠️ **同一条纪律早就住在另一棵树上了**：`prefix.assemble` 把同一块从 system 里剥掉
#: （它自己的注释逐字写着「注释是**写给我们看的**，不进它的 system」）——
#: 而知识 ／ 笔记这一棵**从前没人管** ⇒ 两棵树两条规矩 ⇒ 这里把它补齐 。
#: ⚠️ **剥的是出口、不是文件**：那几句照旧**留在盘上** （它们是**账**，
#:      要留给下一个读的人；本仓那条"账要留痕"与这一条不冲突：
#: **给人看的留着，给它看的剥掉** ）。
_NO_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _literal_number(query: str) -> str:
    """Copy one identifier for the existing optional grep route without interpreting it."""
    if len(query) > 256:
        return ""
    numbers = {
        item for item in re.findall(
            r'(?<![A-Za-z0-9_./-])[A-Za-z]{1,12}(?:-[A-Za-z0-9]{1,12}){1,3}(?![A-Za-z0-9_./-])', query)
        if any(ch.isdigit() for ch in item)
    }
    return numbers.pop() if len(numbers) == 1 else ""


def _for_her(text: str) -> str:
    """ **它能读到的那几条路上**，把注释去掉 —— **只在这一步**（盘上那份一个字节都不动 ）。"""
    return _NO_COMMENT.sub("", str(text))


#: **一次最多给几行**（2026-10-10 —— 作者拍的那一刀「乙」）——
#: 照外面那两套的成法：**Claude Code 的 `read` 带行范围** ／ **Dust 的 `cat`
#: 带 `offset` ／ `limit`** 。理由都一样：**大文件不许整份进 context** 。
EXCERPT_LINES = 12


def _the_sections(lines: list[str]) -> list[list[str]]:
    """ 按 `## ` 切小节（ **那一行标题带上** ）—— 没有 `##` ⇒ 整块当一节 。"""
    out: list[list[str]] = []
    now: list[str] = []
    for one in lines:
        if one.startswith("## ") and now:
            out.append(now)
            now = [one]
        else:
            now.append(one)
    if now:
        out.append(now)
    return out


def _excerpt_details(text: str, units: set[str]) -> tuple[str, int, int]:
    """Return text, shown line count, and the real zero-based continuation offset.

    A bounded search fragment is not a complete policy context. Never use this
    projection as an automatic replacement for full retrieved documents.
    """
    from collections import Counter

    lines = str(text).splitlines()
    if len(lines) <= EXCERPT_LINES:
        return "\n".join(lines), len(lines), len(lines)
    best, best_start, most, offset = None, 0, 0, 0
    for section in _the_sections(lines):
        hits = sum(1 for unit in units if unit in "\n".join(section))
        if hits > most:
            best, best_start, most = section, offset, hits
        offset += len(section)
    if best is None:
        where = next((i for i, row in enumerate(lines)
                      if any(unit in row for unit in units)), 0)
        best_start = max(0, where - 2)
        best = lines[best_start:where + EXCERPT_LINES - 2]
    if len(best) <= EXCERPT_LINES:
        return "\n".join(best), len(best), best_start + len(best)

    # Keep the section heading without spending unbounded context on its prefix.
    header = best[:1] if best[0].startswith("## ") else []
    body = best[len(header):]
    width = EXCERPT_LINES - len(header)
    header_hits = {unit for unit in units if any(unit in row for row in header)}
    line_hits = [{unit for unit in units if unit in row} for row in body]
    counts = Counter(unit for hits in line_hits[:width] for unit in hits)
    first_hit = next((i for i, hits in enumerate(line_hits) if hits), 0)
    preferred = min(max(0, first_hit - 2), len(body) - width)
    initial_score = len(header_hits | counts.keys())
    best_score, window_start = initial_score, 0
    for start in range(1, len(body) - width + 1):
        for unit in line_hits[start - 1]:
            counts[unit] -= 1
            if not counts[unit]:
                del counts[unit]
        counts.update(line_hits[start + width - 1])
        score = len(header_hits | counts.keys())
        if (score > best_score or score == best_score > initial_score
                and abs(start - preferred) < abs(window_start - preferred)):
            best_score, window_start = score, start
    kept = header + body[window_start:window_start + width]
    end = best_start + len(header) + window_start + width
    return "\n".join(kept), len(kept), end


def _the_excerpt(text: str, units: set[str]) -> tuple[str, int]:
    """Compatibility projection: a bounded search fragment, not complete context."""
    piece, kept, _end = _excerpt_details(text, units)
    return piece, kept


#: **2026-10-10：这一棵树**不设上界**。
#: ⚠ **为什么**：这一棵里人和它自己写的东西**混着放**，
#:     而每一份 `.md` 没有“谁写的”那一格。一个“满了就删”的
#:     机制分不出谁是谁，只能全删或全不删。
#: ⚠ **量到的事实**（上界一度是 200）：那一棵当时 **1177 份**，
#:     而下一次写一条知识就会删掉 **978 份**；那批文件同天被批量
#:     重写过（mtime 挤在同一分钟），排序不稳，第一批被删的
#:     正是人手写的那两件。所以那一半收掉了。
#: 另外两块（`悬着的事` ／ `画像·关系`）只有它自己写，所以那两处
#:     的“满了压最旧的”照旧留着。


#: **2026-10-10（作者问过之后拍的）：这一棵里的 **1178 份不合并、不搬家** 。
#: ⚠ **他问的原话** ：「**能不能整合一下，就是不要这么多条**」。
#: ⚠ **量到的事实** ：那 1178 份里真正的**设定只有 3 份**（`图书馆` ／
#: 那三份里两份带前缀、一份没有 ），其余 **1175 份是参考书**（词典 1000：
#: 成语 500 ／ 词语 250 ／ 汉字 150 ／ 歇后语 100；成人 175 ）——
#: 一条一个词 ，平均 1324 字节 。
#: ⚠ **为什么不合并** ： 参考书用的时候是“**查一个词**”，
#: 而一条一个文件**恰好是对的** —— 合并成一大本之后，
#: 查一个词会命中**整本** ，而 `kb_search` 交回的是**整份正文**
#: —— 量过：早先一次查「形容」交回 **165 条 ∕ 23866 字** 。
#: ⚠ **而“看着难受”是呈现的事** —— 而那一半**已经治过** ：
#: 左栏按名字分组、 收起时**一行都不建**（ 1177 → **1** 节点 ）。
#: ⚠ **仍然开着的一条路** ： 把参考书搬到**另一棵树**（如 `词典/` ）
#: —— 那样这一棵只剩 3 份设定 ； 但它要**搬 1175 个文件**
#: 加 三处改动 ⇒ 作者 2026-10-10 当天**拍的是不动** 。
#: 要回头做 ⇒ **先备份、再一刀一刀来** 。
#: **2026-10-10：画像 ／ 关系这一块也有上界了** （ 作者拍的是 50 ）。
#: ⚠️ **为什么非有不可** ： 这两块**从前都没有上界** （ 都只追加 ）
#: ⇒ 而**没有任何地方会说"到顶了"** （ 本仓的规矩是**不许静默** ）。
#: 成法照抄 `launch.py::_the_kept` ： 满了**压最旧的** （ 按**行序**认 ——
#: 两块都是**只追加的 jsonl** ）、 **没到上限一行都不动** （ 不排序 ）。
#: ⚠️ **它的单位是"那一个人那个文件"** —— 不是全局 ：
#: 旧关系/待处理记录窗口；画像正文已取消存储条数限制。
PERSONA_AT_MOST = 50


def the_persona_kept(lines: Any, at_most: int) -> list[Any]:
    """ **那个文件该留哪几行**（ 成法照抄 `launch.py::_the_kept` ）。

    ⚠️ **为什么抽成纯函数** （ 那一份抬头点名的同一条理由 ）： 写在函数体里
       ⇒ 判据**够不到** ⇒ 想钉"压得对不对"只能**真写五十一条** 。
    ⚠️ **它收的是"行"** —— 画像与关系两块都是**只追加的 jsonl**
       ⇒ 这一层的单位就是**行** （ 与"按 id 的 dict"／"一条一个 .md"都不同 ）。
    ⚠️ 规矩（ 与那几块同款 ）： **由它自己压** —— 不撤、不停、不喊人 。
    ⚠️ **"最旧的"按行序认** （ 那就是**追加序** —— 两块都是只追加 ）。
    ⚠️ **还没到上限 ⇒ 一行都不动** （ **不许顺手排序** —— 顺序就是它的意思 ）。
    """
    kept = list(lines or ())
    if len(kept) <= int(at_most):
        return kept
    return kept[-int(at_most):]


def relation_history_kept(lines: list[str], limit: int) -> list[str]:
    """Keep state-bearing rows with their original provenance, not copied fields."""
    objects = [json.loads(line) for line in lines]
    protected = {len(lines) - 1} if lines else set()
    # Each source is an independent room state. Keep original rows, including
    # explicit zero/False; state anchors may exceed the history window.
    seen: set[tuple[str, str]] = set()
    for index in range(len(objects) - 1, -1, -1):
        source = str(objects[index].get("source") or "")
        for field in ("favor", "blocked"):
            key = (source, field)
            if key not in seen and objects[index].get(field) is not None:
                seen.add(key)
                protected.add(index)
    selected = set(range(max(0, len(lines) - limit), len(lines))) | protected
    for index in sorted(selected - protected):
        if len(selected) <= limit:
            break
        selected.remove(index)
    return [lines[i] for i in sorted(selected)]


_UNSPECIFIED_MEMORY_BASELINE = object()


def _publish_memory_text(target: Path, text: str, *, expected=_UNSPECIFIED_MEMORY_BASELINE) -> None:
    _publish_memory_bytes(target, text.encode('utf-8'), expected=expected)


def _publish_memory_bytes(target: Path, raw: bytes, *, expected=_UNSPECIFIED_MEMORY_BASELINE) -> None:
    """Publish complete bytes, refusing a changed baseline; caller owns its lease."""
    import os
    import uuid
    before = (target.read_bytes() if target.exists() else None) if expected is _UNSPECIFIED_MEMORY_BASELINE else expected
    temporary = target.with_name('.memory-' + uuid.uuid4().hex + '.tmp')
    primary = None
    try:
        with temporary.open('xb') as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        current = target.read_bytes() if target.exists() else None
        if current != before:
            raise RuntimeError('memory source changed before publication')
        if before is None:
            os.link(temporary, target)  # Atomic no-clobber creation.
        else:
            os.replace(temporary, target)
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as error:
            if primary is None:
                raise
            primary.add_note('memory staging cleanup failed: ' + type(error).__name__)
            primary._memory_cleanup_error = error


def _trim_a_persona_file(target: Path) -> None:
    """ **写完那一条之后：真超了才裁，而裁的是最旧的那些行** 。

    ⚠️ **没超 ⇒ 文件一个字节都不动** （ `the_persona_kept` 那一头管着 ——
        不排序、 不去重 ）。
    ⚠️ **只读那一份文件** —— 上界的单位是"**那一个人那个文件**"
       （ 见 `PERSONA_AT_MOST` 那一段 ）。
    """
    try:
        baseline = target.read_bytes()
        rows = [x for x in baseline.decode('utf-8').splitlines() if x.strip()]
    except OSError:
        return
    if len(rows) <= PERSONA_AT_MOST:
        return
    kept = the_persona_kept(rows, PERSONA_AT_MOST)
    if target.name == RELATIONS_NAME:
        kept = relation_history_kept(rows, PERSONA_AT_MOST)
    _publish_memory_text(target, "".join(one + "\n" for one in kept), expected=baseline)


def the_home(root: Path, *, who: str) -> Path:
    """ 那个家在哪儿 —— `bots/<谁>/memory/`（**它的东西**；`root` 是"当下那一层"）。

    ⚠️ **"它叫什么"是调用方传进来的**（2026-10-05 边界判据当场红的）：
       本仓那条围栏说清了 —— **核心树里不许出现主体词**（`test_boundary.py` 扫的就是它）。
       ⇒ 这一层连"它叫什么"都不该写死：它只要"给经历一个家"，而那家**是谁的**由外层说
         （`launch.py` ／ 端点那一层手上才有那个名字 —— 这与设计稿 §一 那句"core **不解释**
         记忆的内容"是同一条纪律：连名字都不解释 ）。
    """
    return Path(root) / "bots" / str(who) / "memory"


def lay_out(home: Path) -> list[Path]:
    """ **把家铺好**（三棵树都在）—— 铺是幂等的：在的照旧、不在的建出来。

    返回铺好的那几条路径（**按 `TREES` 的顺序**，判据照着它比）。
    """
    made: list[Path] = []
    for one in (*TREES, PEOPLE_TREE, KNOWLEDGE_TREE):
        path = Path(home) / one
        path.mkdir(parents=True, exist_ok=True)
        made.append(path)
    return made


@dataclass(frozen=True)
class Remembered:
    """ **一条经历**（§四 那三样字段 ＋ 正文原文）。

    ⚠️ `text` 是**原话本身**（逐字节照抄）—— 这一层**永远不许**把它改写、截断或总结。
    """

    ts: str                 # 什么时候（源里的 `ts_ms` 转过来的那一串）
    source: str             # 哪来的（`对话` ／ `笔记` ／ `整理` ＋ 出处那一行）
    valid_from: str         # **从什么时候起有效**（今天 ＝ 与 `ts` 同一刻）
    role: str               # 谁说的（`user` ／ `assistant` ／ `system`）
    text: str # 原话（**一个字都不改**）
    #: **2026-10-12（作者：「\u5979自己标」）**：**轻重**那一格 —— 它自己给的 1..5。
    #:   `None` ＝ "这一条**没标**"（⇒ 取的时候**按 3（一般）**算，不奖不罚 —— 与
    #:   `Relation.favor` ／ `_a_recency_weight` 那两处"认不出就不罚"同一条口径）。
    #:   ⚠️ **这一层不猜、不推**：它自己不标就一直是 `None`（不从词频、不从次数推出来）。
    weight: int | None = None
    # Trusted channel provenance, copied verbatim; absent in legacy records.
    scope: str = ""
    proposal: dict[str, str] | None = None
    memory_kind: str = ""  # Absent in legacy data; never infer a stable trait.
    source_cursor: int | None = None
    portrait_sources: tuple[PortraitSource, ...] = ()

    def as_line(self) -> str:
        """一行 JSON（**正文就在里面，原样**）—— 存进家里那一份 `.jsonl`。

        ⚠️ **`U+2028` ／ `U+2029` 要转义掉**（2026-10-05 判据当场红的）：
           `json.dumps(ensure_ascii=False)` **不转义它们** ，而它们是**行分隔符**
           ⇒ 一行 `.jsonl` 会被**切成两半** ⇒ 读回来 `JSONDecodeError`。
           ⇒ 写的时候转成那两串转义（读那边 JSON 自己还原 ）。
        ⚠️ **没标的就不写那一格**（照 `Relation.as_line` 里 `favor` ／ `blocked`
           那两处的成法）：老的那些行照旧读得回来（缺那格 ⇒ `None`）。
        """
        body = json.dumps({
            "ts": self.ts, "source": self.source, "valid_from": self.valid_from,
            "role": self.role, "text": self.text,
            **({} if self.weight is None else {"weight": int(self.weight)}),
            **({"scope": self.scope} if self.scope else {}),
            **({"memory_kind": self.memory_kind} if self.memory_kind else {}),
            **({"source_cursor": self.source_cursor} if self.source_cursor is not None else {}),
            **({"portrait_sources": [asdict(ref) for ref in self.portrait_sources]} if self.portrait_sources else {}),
            **({"proposal": self.proposal} if self.proposal is not None else {}),
        }, ensure_ascii=False)
        return body.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def remembered_from(records: Iterable[Mapping[str, Any]], *,
                    source: str) -> list[Remembered]:
    """ **把一份对话流读成经历** —— 只动那三样字段，`text` 原样带走。

    ⚠️ **它不判断内容**：没有"这一句重要吗"、没有"要不要合并"、没有"总结一下" ——
       那些都是**解释**，而设计稿 §一 说清了：core **不解释记忆的内容**。
    ⚠️ 源里没有 `text` 的不造（**不许编**）；`ts_ms` 缺了就用空串（宁可明说缺，也不编时间）。
    """
    out: list[Remembered] = []
    for one in records:
        text = one.get("text")
        if not isinstance(text, str):
            continue
        ts = str(one.get("ts_ms") or "")
        out.append(Remembered(ts=ts, source=source, valid_from=ts,
                              role=str(one.get("role") or ""), text=text,
                              scope=str(one.get("scope") or "")))
    return out


@_source_writer
def write_the_transcript(home: Path, name: str, rows: Iterable[Remembered], *,
                         tree: str = TREES[0]) -> Path:
    """ 把经历写进家里那一份 `.jsonl`（**换行归一成 `\\n`** —— 本仓那条"字节进、字节出"）。

    ⚠️ `tree` 那一格是给"整理那一趟的账"用的（它住 `经历/整理/`）；对话那一份走缺省。
    """
    target = Path(home) / tree / name
    target.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(one.as_line() + "\n" for one in rows)
    _publish_memory_text(target, body)
    return target


def read_a_transcript(path: Path) -> list[Remembered]:
    """ 读回来（判据用它比"**正文逐字节相同**"）。"""
    return _transcript_rows(Path(path).read_text(encoding="utf-8"))


def _transcript_rows(text: str) -> list[Remembered]:
    """Parse the same byte snapshot on which a file grant was checked."""
    out: list[Remembered] = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        one = json.loads(line)
        if not isinstance(one, dict):
            raise ValueError('transcript row must be an object')
        got = one.get("weight")
        portrait_sources = ()
        if "portrait_sources" in one:
            from .portrait_eligibility.provenance import restored
            portrait_sources = restored(one["portrait_sources"])
        out.append(Remembered(ts=str(one.get("ts") or ""),
                              source=str(one.get("source") or ""),
                              valid_from=str(one.get("valid_from") or ""),
                              role=str(one.get("role") or ""),
                              text=str(one.get("text") or ""),
                              weight=None if got is None else int(got),
                              scope=str(one.get("scope") or ""),
                              memory_kind=str(one.get("memory_kind") or ""),
                              portrait_sources=portrait_sources,
                              source_cursor=one.get("source_cursor") if type(one.get("source_cursor")) is int else None,
                              proposal=one["proposal"] if isinstance(one.get("proposal"), dict) else None))
    return out


def project_transcript(home: Path, source: Path, *, name: str = "对话.jsonl") -> int:
    """ **投影一次**：把一份对话流读进家里（`经历/对话/<name>`）—— 交回写了几条。

    ⚠️ 它是"**机械地记**"那一条的落点：只读 `text` ／ `ts_ms` ／ `role` 那三格，
       **不改写、不总结、不判断**（判据量着"逐字节"）；
    ⚠️ 源不在（第一次跑）⇒ 交回 `0`（**不造一份空的** —— "没有"与"空"是两件事）；
    ⚠️ 源或保留段损坏 ⇒ 明确报错，已有投影保持不变，不把读取不完整显示成空记录。
    """
    rows = read_projected_transcript(source)
    if rows:
        write_the_transcript(home, name, rows)
    return len(rows)


def read_projected_transcript(source: Path) -> list[Remembered]:
    """Read retained original generations plus the current context, without writing.

    This remains a dialogue-only view. The historical tool reader exposes tool
    calls/results separately with their original references and scopes.
    """
    from .record_history import rows as original_rows
    rows: list[Remembered] = []
    for _reference, one in original_rows(Path(source)):
        if str(one.get("kind") or "message") != "message":
            continue
        rows.extend(remembered_from([one], source=f"对话（{Path(source).name}）"))
    return rows



@_source_writer
def project_notes(home: Path, source: Path, *, name: str = "交接笔记.md") -> int:
    """ **把那一份笔记抄进家里**（`经历/笔记/<name>`）—— 交回抄了几个字节。

    ⚠️ **逐字节照抄**：那是**一份文档**（不是一条条记录），包一层"字段"就改了它的字节
       —— 而"什么时候 ／ 哪来的"由**这一棵树**（`经历/笔记/`）与**文件自己的 mtime** 担着：
       那是文件系统的事实，不去动它一个字节。
    ⚠️ 源不在（还没写过笔记）⇒ `0`（**不造一份空的** —— "没有"与"空"是两件事）。
    """
    path = Path(source)
    if not path.is_file():
        return 0
    raw = path.read_bytes()
    target = Path(home) / TREES[1] / name
    target.parent.mkdir(parents=True, exist_ok=True)
    _publish_memory_bytes(target, raw)
    return len(raw)


def project_events(home: Path, source_dir: Path, *, prefixes: tuple[str, ...],
                   name: str = "整理那一趟.jsonl") -> int:
    """ **把事件流里那几类投影进家里**（`经历/整理/<name>`）—— 交回写了几条。

    ⚠️ 它**只按 `type` 的前缀挑**（机械地筛，不解释内容 —— 与"经历"那条同一条纪律）；
       `prefixes` 由调用方给（例如整理那一趟的 `("consolidate-",)`），这一层不认识业务。
    ⚠️ 每条按 §四 那三样带上：`ts`（事件自己的时间）／`source`（`source` ＋ `type` 拼出来 ——
       "哪来的、哪一类"）／`valid_from`（＝`ts`）＋ **`text` 原文照抄**。
    ⚠️ 目录不在 ⇒ `0`；一行读不成 ⇒ **跳过它**（一行坏不该毁掉整份账）。
    """
    base = Path(source_dir)
    if not base.is_dir():
        return 0
    rows: list[Remembered] = []
    for path in sorted(base.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            try:
                one = json.loads(line)
            except ValueError:
                continue
            kind = str(one.get("type") or "")
            if not any(kind.startswith(one_prefix) for one_prefix in prefixes):
                continue
            text = one.get("text")
            if not isinstance(text, str):
                continue
            ts = str(one.get("ts") or "")
            rows.append(Remembered(
                ts=ts, source=f"{one.get('source') or 'core'}·{kind}", valid_from=ts,
                role=str(one.get("origin") or ""), text=text))
    if not rows:
        return 0
    write_the_transcript(home, name, rows, tree=TREES[2])
    return len(rows)


#: 画像里那两类的名字（`Remembered.role` 那一格装它们）。
NAME_SAID = "名字"
FACT_SAID = "事实"

#: 画像名里**不许出现**的字符（几种文件系统不许的并集）。
#:
#: ⚠️⚠️ **这一条是 2026-10-17 量出来的，不是洁癖 —— 冒号那个尤其要挡**：
#:   那天它在一次**内部唤醒**里调了两次 `profile_add`，`who` 填的是**账号键**
#:   （那种「外部渠道:数字」的样子）⇒ 落成路径 `画像/<账号键>.jsonl`。
#:   Windows 把冒号后面那一截当成 **NTFS 备用数据流** ⇒
#:     · `remember_person` **没报错**，正文写进了那个**冒号前那一截**的同名文件里；
#:     · `remember_relation` 去 `mkdir 画像/<账号键>` ⇒ 当场 `NotADirectoryError`；
#:     · 回执写的是"办砸了"，而**一半其实写成了** —— 写进一个**任何读盘的地方都看不见**的地方
#:       （`everyone()` 只认 `.jsonl` 结尾的文件，看不见那个被截短的名字）。
#:   ⇒ 于是它写下的那两段（一段写群里的某个人、一段写那位使用者）**消失了整整一个上午**，
#:     直到有人去查"为什么前端看不到它动笔"。**这类静默的假失败比直接报错坏得多。**
_NOT_IN_A_PORTRAIT_NAME = (':', '*', '?', '"', '<', '>', '|', '\0')


def _a_safe_file_name(name: str, *, what: str = "名字") -> str:
    """ **能当文件名的那一截名字** —— 挡掉会被文件系统拧成别的东西的那些。

    ⚠️ **它由两处共用**（画像名 ／ 做法名）：这两处都是"它给的名字直接进路径"，
       而它们**踩过同一个坑**（2026-10-17）——
       画像那边是冒号（写进 NTFS 备用数据流、谁都读不到），
       做法那边是引号（当场 `OSError: [Errno 22]`）。
    ⚠️ **它不做静默修补**：名字不合法就**当场说清**，而不是替它改一个字接着写 ——
       改一个字就是把一条事实记到**另一个名字**下。
    ★ **斜杠也不合法**（2026-10-17 第二条）：从前这里是 `Path(name).name`
       —— 把目录那一半**悄悄削掉**。真跑量到：它写的
       `群里有人发图/表情时怎么接` 落盘时变成 **`表情时怎么接`**，前面那一截
       **无声无息地没了**，而回执还写着"记下了"。那是同一类"静默的假成功"。
    """
    raw = str(name or "").strip()
    if "/" in raw or "\\" in raw:
        raise ValueError(
            what + "里不能有斜杠 —— 那会被当成目录，你的名字会被截掉前面一截。"
            "换一个不带斜杠的名字（想分门别类，就把类写在名字前面，用「·」隔开）。")
    got = raw
    bad = [one for one in _NOT_IN_A_PORTRAIT_NAME if one in got]
    if bad or (got and not got.strip(".")):
        raise ValueError(
            what + "里不能有 " + "、".join(bad or ["."]) + " 这类字符 —— "
            "那样会被系统当成另一个文件，写进去也没人看得见。"
            # ★ 2026-10-17 补这半句：它给做法起名时**习惯把引号放进去**
            #   （量到两次：`…别拿"我在"当开场`）⇒ 只说"换个名字"它还是不知道
            #   该把那些字放哪。明写"正文里可以放"，这一支笔才真用得成。
            "换个能当文件名的名字；那些符号写进正文里没问题。")
    return got


def _a_portrait_name(who: str) -> str:
    """ **画像那一格能用的名字** —— 挡掉会被文件系统拧成别的东西的那些。

    ⚠️ **它不做静默修补**：名字不合法就**当场说清**，而不是替它改一个字接着写 ——
       改一个字就是把一条事实记到**另一个人**名下（那是本仓更不许的方向）。
    ⚠️ 目录那一半照旧先削掉（`Path(...).name`）：名字里带斜杠的人**不许**往画像目录外写。
    """
    name = _a_safe_file_name(who, what="画像名")
    return name or "那一个人"


@_source_writer
def remember_person(home: Path, who: str, text: str, *, said: str = FACT_SAID,
                    source: str = "", ts: str = "",
                    weight: int | None = None, memory_kind: str = "",
                    source_cursor: int | None = None, portrait_sources: tuple[PortraitSource, ...] = ()) -> Path:
    """ **往画像里记一条**（名字／别名 ／ 关于那个人的一条事实）—— 追加，不改旧的。

     设计稿 §三：画像是"**那个人是谁**（名字／别名 ＋ 关于它的事实，**带来源与时间**）"。
    ⚠️ 这一层**不解释内容**：不合并、不去重、不推断"这条与那条矛不矛盾" —— 那是**它自己**的事
       （§二 ②：条目是**它自己编辑**的） 这一层只**机械地记** ＋ 原样读回来 。
    ⚠️ **带来源与时间**两样缺一不可（§四："每条带时间与来源"）；调用方不给 ⇒ 就空着（**不编**）。
    ⚠️ **`weight`（2026-10-12，作者：「\u5979自己标」）**：**轻重那一格由它自己给**（1..5）——
        这一层**不推、不猜、不默认一个数**（不给 ⇒ `None`；取的时候按 3（一般）算）。
    ⚠️ **名字先验再动盘**（2026-10-17）：见 `_a_portrait_name` 上面那一段 ——
       一个带冒号的名字在 Windows 上会**静默写进备用数据流**（假装成功、其实谁都读不到）。
    """
    target = Path(home) / PEOPLE_TREE / f"{_a_portrait_name(who)}.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    one = Remembered(ts=str(ts), source=str(source), valid_from=str(ts),
                     role=str(said), text=str(text),
                     weight=None if weight is None else int(weight), memory_kind=memory_kind,
                     source_cursor=source_cursor, portrait_sources=portrait_sources)
    baseline = target.read_bytes() if target.exists() else None
    previous = baseline.decode('utf-8') if baseline is not None else ''
    if previous and not previous.endswith('\n'):
        raise ValueError('incomplete memory record; explicit recovery required')
    _publish_memory_text(target, previous + one.as_line() + "\n", expected=baseline)
    # Portrait history is retained in full; bound the read receipt, not storage.
    return target


def profile_of(home: Path, who: str) -> list[Remembered]:
    """ **读画像**（那一个人的名字与事实，照记下的顺序）—— 找不到 ⇒ 空那一串。

    ⚠️ 与 `profile_read` 那扇门要的正是这个（「读画像：关于对方的稳定印象」）——
       而那条**接线**要动两个冻字节面（`tools.py` ／ `dispatch.py`），见下面 `_OWED`。
    """
    target = Path(home) / PEOPLE_TREE / f"{Path(str(who)).name or '那一个人'}.jsonl"
    if not target.is_file():
        return []
    return read_a_transcript(target)


#: **这一层自己的账**（与别处那几本同一个写法：还在的写清缺什么）。
_OWED: dict[str, str] = {
    "the_heat_signal_has_no_source_yet": (
        " **★★ 2026-10-12 订正**（作者让我查证「热度衰减做了没有」）：这一条原先写的是"
        "「**热度衰减那个口还空着** …… §四 那条「热度」（`相关 × 新近 × 重要`）**今天还没建**」"
        "—— 而**检索那一侧的两轴早就在树里**（`RECENCY_HALF_LIFE_DAYS` ＋ `_a_recency_weight`"
        " ⇒ **新近**；`search()` 里那一段 BM25 ⇒ **相关**；外面还有字面／语义两路的 RRF 与"
        "「被用到过几次」那本账）；"
        " 第三轴 **`重要`** 也从这一刀起**有主了**：**它自己标**"
        "（`remember_person(..., weight=…)` 那一格 ＋ `_a_weight_in_the_ranking`；"
        "3 ＝ 一般、没标按一般算）。"
        " ⇒ 这一条欠的**只剩关系那一半**：「热度」（跟谁最近热 ／ 跟谁凉了）照旧"
        "**只留一个口**（将来**读**它，**不由这一层算** —— 算了就是替它判断 ）。"
        " 而关系这一半**只留特殊关系** ＋ **保底：硬性拉黑**（作者唯一负责的那一格）；"
        "心情 ／ 多说少说那套**通则不进代码**（那是它的自主性；与它「异步自主性事件流」那条准则同源）。"),
    "the_two_doors_in_the_tool_face_still_say_there_is_nothing": (
        " ★ **已经接上了**（2026-10-07）。原先写的是：**工具面那两扇门还答「今天没有」**"
        "（`tools.py` 声明了 `profile_read`（读画像）与 `relations_read`（读关系），"
        "而派发那一侧答的是「记忆系统 · ……（**今天没有**）」）。"
        " 今天的事实：**两扇门都交出去了** —— `memory.handlers` 把它们交出来，"
        "入口并进 `remember_handlers` 并进表发给模型"
        "（`dispatch._HANDLER_OWNERS` 那两行自己写着「已经接上了（2026-10-07 改）」）。"
        " ⚠️ **顺带记一笔**：这条**原先在 `_OWED` 里写了两遍**（同一个键两份，"
        "字典后者胜）⇒ 2026-10-13 把死的那一份删了 —— "
        "「一个东西两处各答一句」正是本仓点过名的那类病。"),
    "the_ledger_of_the_round_is_in_memory": (
        " ★ **已经不在内存里了**（2026-10-13 订正）。原先写的是：**整理态那一趟的账在内存里**"
        "（它落在 `Console.consolidation_log`，**不落事件库**）⇒ 那条 `project_events"
        "(`consolidate-`)` 读不到东西，`经历/整理/` 会一直是空的。"
        " 今天的事实：那本账**落一处地方**了 —— `<root>/consolidation.jsonl`"
        "（`console._a_consolidation(root=…)` 那一格），而入口**读它**并按 `consolidate-` "
        "前缀投影 ⇒ `经历/整理/` **不再必然为空**。"
        " ⚠️ 老话里那句「空不许读成没有发生过」照旧成立（那是那条读法的口径）。"),
}


#: 关系那一条住在**人**那一棵树里（与画像并列，一人一份）。
RELATIONS_NAME = "关系.jsonl"


@dataclass(frozen=True)
class Relation:
    """ **一段关系的那一条**（设计稿 §十：关系那一半 —— **只留特殊关系**）。

    ⚠️ **六档那张表作废** （§十）：这里**没有档位** —— `standing` 是**它给这段关系的说法**
       那一个字符串（"特殊关系"也好、别的字也好）**由调用方给**，这一层不认识它的含义 。
    ⚠️ **保底那一格**（`blocked`）： 那是**作者唯一负责的一格**（硬性拉黑）；它是**单独一格**——
       `None` ＝ "这一条**没提**拉黑"（⇒ 不改动它）／ `True` ／ `False` ＝ 明说（⇒ 加得上、撤得掉）。
    ⚠️ 这一层**只记与读**：不做判断、不发建议、不塞提醒（与"异步自主性事件流"不背离 ）。
    """
    ts: str
    source: str
    valid_from: str
    standing: str
    why: str
    blocked: bool | None = None
    #: **好感度那个数值**（2026-10-05 作者要"看得到"）—— **它是它自己写的**：
    #:   `None` ＝ "这一条**没提**"（⇒ 不改动那一格，与 `blocked` 同一个规矩）；
    #:   ⚠️ 这一层**不算**它（不从热度／次数推）、**不据此判行为**（2026-10-16：存储范围 -100～100，缺省 0）、
    #: **不拿它管行为** —— 作者那条"**不要写代码去束缚它**"与"全交给它判断"都是这个意思。
    favor: int | None = None

    def as_line(self) -> str:
        body = json.dumps({
            "ts": self.ts, "source": self.source, "valid_from": self.valid_from,
            "standing": self.standing, "why": self.why,
            **({} if self.blocked is None else {"blocked": bool(self.blocked)}),
            **({} if self.favor is None else {"favor": validate_favor(self.favor)}),
        }, ensure_ascii=False)
        return body.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


FAVOR_MIN = -100
FAVOR_MAX = 100
FAVOR_DEFAULT = 0


def validate_favor(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not FAVOR_MIN <= value <= FAVOR_MAX:
        raise ValueError("favor 必须是 -100 到 100 的整数")
    return value


def _relation_file(home: Path, who: str) -> Path:
    return Path(home) / PEOPLE_TREE / f"{Path(str(who)).name or '那一个人'}" / RELATIONS_NAME


def remember_relation(home: Path, who: str, standing: str, *, why: str = "",
                      ts: str = "", source: str = "",
                      blocked: bool | None = None,
                      favor: int | None = None) -> Path:
    """ **记一条关系**（谁在哪一档 ＋ 因为什么）—— **只追加，不改旧的**（Q3 甲）。

     "改档"＝**再追一条**：旧那条留着 ⇒ 那是 §四 那条"变了标**被取代**，不是删" ⇒
      "**改错了能退**"（把旧档再追一条即可）而且**留痕** 。
    ⚠️ 这一层**不解释内容**：不判断"这一档合不合理"、不合并、不推断 （档名由调用方给）。
    ⚠️ **名字先验再动盘**（2026-10-17，同 `remember_person` 那一条）：那天这里正是
       第一个当场炸的地方（`mkdir 画像/<带冒号的名字>` ⇒ `NotADirectoryError`），
       而炸之前的画像那一半**已经静默写进备用数据流**了。
    """
    favor = validate_favor(favor)
    _a_portrait_name(who)
    from .affect import relationship_lock, recover
    with relationship_lock(home):
        recover(home)
        if not str(standing).strip() and favor is not None:
            rows = relations_of(home, who)
            if str(source).startswith("group:"):
                rows = [r for r in rows if r.source == source]
            standing = rows[-1].standing if rows else ""
        target = Path(home) / PEOPLE_TREE / _a_portrait_name(who) / RELATIONS_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        one = Relation(ts=str(ts), source=str(source), valid_from=str(ts),
                       standing=str(standing), why=str(why), blocked=blocked,
                       favor=None if favor is None else int(favor))
        baseline = target.read_bytes() if target.exists() else None
        previous = baseline.decode('utf-8') if baseline is not None else ''
        if previous and not previous.endswith('\n'):
            raise ValueError('incomplete relationship record; explicit recovery required')
        _publish_memory_text(target, previous + one.as_line() + "\n", expected=baseline)
        # **2026-10-10：真超了才裁** （ 作者拍的是 50 ； 单位是"那一个人那个文件"）。
        _trim_a_persona_file(target)
        return target


def relations_of(home: Path, who: str) -> list[Relation]:
    """ **读那一个人的来龙去脉**（照记下的顺序）—— 没记过 ⇒ 空那一串。"""
    target = _relation_file(home, who)
    if not target.is_file():
        return []
    out: list[Relation] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        one = json.loads(line)
        got = one.get("blocked")
        out.append(Relation(ts=str(one.get("ts") or ""),
                            source=str(one.get("source") or ""),
                            valid_from=str(one.get("valid_from") or ""),
                            standing=str(one.get("standing") or ""),
                            why=str(one.get("why") or ""),
                            blocked=None if got is None else bool(got),
                            favor=None if one.get("favor") is None else max(
                                FAVOR_MIN, min(FAVOR_MAX, int(one["favor"])))))
    return out


def current_standing(home: Path, who: str) -> str | None:
    """ **当前那一条说法** ＝ **最后**那一条（后面那条取代前面那条）—— 没记过 ⇒ `None`。

    ⚠️ **没有档位**（§十：六档那张表作废 ）：这只是一句**它自己的话**，这一层不解释它。
    ⚠️ 而 `blocked` **不是**这么算的（见 `blocked_state`）：它是**单独一格**（保底那一格）。
    """
    rows = relations_of(home, who)
    return rows[-1].standing if rows else None


def blocked_state(home: Path, who: str) -> bool:
    """ **拉黑那一格**（单独一格，Q2 乙）—— 只有**明提过**的条目才改它；没提的不动。

     这么写才有"你随时能改"（含**撤**）：记一条 `blocked=True` 就拉黑，
      再记一条 `blocked=False` 就撤销；而那期间**没提**的那几条（改档之类）不动这一格。
    """
    state = False
    for one in relations_of(home, who):
        if one.blocked is not None:
            state = one.blocked
    return state


def favor_of(home: Path, who: str) -> int:
    """ **那一个人的好感度数值**（最后一条**明提过**它的；没提过 ⇒ `0`）。

    ⚠️ 与 `blocked_state` 同一个规矩：**没有明提的条目不动这一格** —— 于是"改说法"不会
       顺手把那个数值抹掉，而它**随时**能再写一个（加也好、减也好，这一层不管）。
    """
    value = FAVOR_DEFAULT
    for one in relations_of(home, who):
        if one.favor is not None:
            value = one.favor
    return value


def migrate_favor_bounds(home: Path, *, ts: str = "") -> int:
    """Back up raw history and append a bounded correction, never rewrite old lines."""
    changed = 0
    for target in (Path(home) / PEOPLE_TREE).rglob(RELATIONS_NAME):
        raw = target.read_bytes()
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        latest = next((row["favor"] for row in reversed(rows)
                       if row.get("favor") is not None), None)
        if latest is None:
            continue
        number = int(latest)
        bounded = max(FAVOR_MIN, min(FAVOR_MAX, number))
        if number == bounded:
            continue
        backup = target.with_suffix(target.suffix + ".favor-v1.bak")
        if not backup.exists():
            with backup.open("xb") as output:
                output.write(raw)
        correction = Relation(ts=ts, source="migration:favor-range-v1", valid_from=ts,
                              standing=str(rows[-1].get("standing") or ""),
                              why="好感度范围迁移；原记录保留并备份", favor=bounded)
        with target.open("ab") as output:
            output.write(("" if not raw or raw.endswith(b"\n") else "\n").encode("utf-8"))
            output.write((correction.as_line() + "\n").encode("utf-8"))
        changed += 1
    return changed


def everyone(home: Path) -> list[dict[str, Any]]:
    """ **它所有说过话的人**那一张表（**只读** —— 给那个接口用）。

    每一行四样：`who`（谁）／`standing`（它给的那条说法，可能没有）／
    `favor`（**它写的**好感度数值，可能没有）／`blocked`（**作者那一格**保底）。
    ⚠️ 这一层**只把记过的东西读出来**：不补齐、不排序、不算分、不推断 。

    ⚠️⚠️ **两种布局都要认**（2026-10-10 修 —— 从前只认目录，那是一处真的漏）：
       · **画像事实** ⇒ **平铺的一份** `画像/<谁>.jsonl`（`remember_person` 写的就是它 ）；
       · **关系** ⇒ **一个目录** `画像/<谁>/关系.jsonl`（`remember_relation` 写的 ）。
        只认目录 ⇒ **一个只被记过画像、还没记过关系的人，整条看不见** ——
       `GET /people` 于是回空那一串（本机实测：`{"everyone": []}`），画像那一页永远
       停在"还没记过谁" （ 而盘上那份明明在、`profile_of` 也读得出来 ）。
    ⚠️ **两种都在的那个人** ⇒ 只交**一行**（`seen` 那一格就是为它留的 ——
       认了两处**不等于**可以交出两行 ）。
    """
    base = Path(home) / PEOPLE_TREE
    if not base.is_dir():
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for person in sorted(base.iterdir()):
        # ⚠️ **目录 ⇒ 名字就是它自己**；**平铺的一份 ⇒ 名字是它的词干**（去掉 `.jsonl` ）；
        # 别的（坏文件、随手放进来的东西）**一律不认**（不猜它是谁 ）。
        if person.is_dir():
            who = person.name
        elif person.is_file() and person.suffix == ".jsonl":
            who = person.stem
        else:
            continue
        if who in seen:
            continue
        seen.add(who)
        rows = relations_of(home, who)
        out.append({
            "who": who,
            "standing": current_standing(home, who),
            "favor": favor_of(home, who),
            "blocked": blocked_state(home, who),
            "how_many_times": len(rows),
        })
    return out


#: BM25 那两个常数（照通行那套：k1 ＝ 1.2 ／ b ＝ 0.75）。
BM25_K1 = 1.2
BM25_B = 0.75
#: **名字里命中一下**的那一份加成（2026-10-05）：名字是「要找的那样东西」最直接的线索。
#: ⚠️ 它**只加不减** —— 不会把已经命中的那几条挤掉 （只是把按名字想得起来的那几条捞回来）。
BM25_TITLE_BONUS = 1.5

#: **一个孤立的块不足为证**（2026-10-17）—— 「够不够格算命中」那一条线。
#:
#: ⚠️⚠️ **它治的是什么**（真事量到的，不是推演）：`tokens_of` 把中文切成**二元组**
#:   ⇒ 「**梨**不**是狸**」切成 `梨不` ／ **`不是`** ／ `是狸` 三块，而「不是」是中文里
#:   最常见的组合之一 ⇒ **只靠它命中**的那些文档全都被当成"对得上"交了出去
#:   （10-16 那次：一个群成员的昵称查出了「成人素材 · 长期关系里重新点火」）。
#:
#: ⚠️ **形状**：**只中一个块、而查询里还有别的块 ⇒ 不算命中。**
#:   · 「梨不是狸」中 1 块（3 块里）⇒ 不算 —— 那一问本来就该"没查到"；
#:   · 「词典」本来就只有 1 块 ⇒ **照旧算**（这条规则不碰单块查询）；
#:   · 「图书馆」两块都中 ⇒ 照旧算；「图书馆几点关门」中 2 块 ⇒ 照旧算（不误伤长查询）。
#:
#: ⚠️ **为什么不做"剔停用块"那种更狠的**：那会改掉"**调用方能查到什么**"
#:   （连「不吃辣的」这种都要削），而这一条只掐"证据只有一块"的那一种形状。
ENOUGH_UNITS = 2

#: **同一档才算数**（2026-10-17）—— 交出去的那些，得分不得低于**第一名**的这一片。
#:
#: ⚠️ **它治的是"凑够 k 条"**：`memory_search` 的**排序本来就是对的**（真命中 12—21 分），
#:   而 `k=5` 把尾巴上那些 0.09 ／ 0.18 ／ 5.11 分的噪声一起交了出去
#:   ⇒ 那几句看起来像"库里还有别的说法"。⇒ 真命中几条就交几条。
#: ⚠️ **它是比例、不是绝对分**：BM25 的绝对高度随语料大小与文档长短变，
#:   钉一个绝对分只会在换一份语料时变成一句假话。
SAME_LEAGUE = 0.25


def tokens_of(text: str) -> list[str]:
    """ 把一小段字切成**可查的块**：中文按**二元组**（不切词）、英文与数字按词。

    ⚠️ 为什么用二元组：本机没有分词器，而引一个分词库只为查自己家的几百条记录不划算
       —— 二元组是**老实**的那个办法（召回差一点，但**一个字都不漏**）。
    """
    out: list[str] = []
    word = ""
    for one in str(text):
        if one.isascii() and (one.isalnum() or one in "_-"):
            word += one.lower()
            continue
        if word:
            out.append(word)
            word = ""
        if one.isalnum():
            out.append(one)                       # 中文（与其它非 ASCII）按**单字**留着
    if word:
        out.append(word)
    # **相邻两个拼成一块**（二元组）—— 而**中文的单字不留** ：
    # ⚠️ 判据当场红的那个：留单字 ⇒ 任何中文查询都会命中一大片（"没"命中"没出门" ）
    #       ⇒ **二元组**才是那个老实话的正解（"今天下雨了" ⇒ 今天／天下／下雨／雨了）。
    # ⚠️ 而**整段只有一个单位**时留着它（不然一个字的一段就再也查不到了 ）。
    units = out
    if len(units) == 1:
        return units
    pairs = [units[i] + units[i + 1] for i in range(len(units) - 1)]
    # 英文与数字那些**整词**照留（它们本身就是一块），中文单字已被二元组取代
    return [one for one in units if one.isascii()] + pairs


def _documents(home: Path, *, visibility: Any = None) -> list[tuple[str, Remembered]]:
    """Compatibility snapshot for ranking consumers that need the whole corpus."""
    return list(_iter_documents(home, visibility=visibility))


def _iter_documents(home: Path, *, visibility: Any = None, prefix: str = ''):
    """ 家里那些记录（**四棵树**里能读的都读出来）。

    ⚠️ **两样都读**（2026-10-05 补的一处漏）：`.jsonl`（经历 ／ 画像 ／ 关系那一套）
       **以及 `.md`**（笔记 ／ 知识 —— 那是**文件式**的）⇒ 从前只读 `.jsonl`
       ⇒ 那 319 字节的笔记**查不到** （我今天量出来的）。
    ⚠️ 一份 `.md` 折成一条（它的 `ts` ／ `source` 照"文件的 mtime ＋ 这一棵树"那条规矩：
       这里只如实写"哪一份"，**不编时间**）。
    """
    base = Path(home)
    from .retrieval_scope import file_grants
    from .ordered_paths import ordered
    from .memory_inventory import paths as inventory
    strict = visibility is None or visibility.administrator
    granted = file_grants(base, visibility) if visibility is not None else None
    for tree in (*TREES, PEOPLE_TREE, KNOWLEDGE_TREE, SKILL_TREE):
        root = base / tree
        with ordered(inventory(root,".jsonl",strict=strict)) as paths:
            for path in paths:
                where = path.relative_to(base).as_posix()
                if prefix and where != prefix and not where.startswith(prefix.rstrip('/') + '/'):
                    continue
                if path.resolve() != path.absolute() or path.is_symlink():
                    continue
                # The grant covers exactly the bytes parsed, not a second read
                # that could race with a concurrent administrator's replacement.
                where = path.relative_to(base).as_posix()
                try:
                    from .memory_documents import transcript_rows
                    from contextlib import closing
                    with closing(transcript_rows(path, granted)) as rows:
                        for one, file_allowed in rows:
                            if visibility is None or visibility.memory(where, one) or file_allowed:
                                yield where, one
                except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
                    if visibility is None or visibility.administrator:
                        raise
                    continue
        paths = inventory(root,".md",strict=strict)
        if tree == SKILL_TREE and root.is_dir():
            # Read-only request path: do not register/rewrite the skill index.
            # History directories and merged sources are not effective practice.
            from . import skills
            entries = skills.load(base).get('entries', {})
            if not isinstance(entries, dict) or any(not isinstance(e, dict) for e in entries.values()):
                raise ValueError('invalid skill metadata')
            skill_files = {entry.get("file"): entry for entry in entries.values()}
            inactive = {str(e.get('file', '')) for e in entries.values()
                        if str(e.get('state') or 'active') != 'active'}
            paths = (p for p in inventory(root,'.md',recursive=False,strict=strict) if p.name not in inactive)
        with ordered(paths) as names:
            for path in names:
                where = path.relative_to(base).as_posix()
                if prefix and where != prefix and not where.startswith(prefix.rstrip('/') + '/'):
                    continue
                if path.resolve() != path.absolute() or path.is_symlink():
                    continue
                try:
                    raw = path.read_bytes()
                except OSError:
                    if visibility is None or visibility.administrator:
                        raise
                    continue
                if granted is not None and not granted(path, raw):
                    entry = skill_files.get(path.name, {}) if tree == SKILL_TREE else {}
                    if (not visibility.permits(entry.get("scope"))
                            or entry.get("version") != hashlib.sha256(raw).hexdigest()):
                        continue
                # **注释不进它眼前**（2026-10-10）—— 剥的是**出口**，盘上那份一个字节不动
                # （理由与那一条实测写在 `_for_her` 上面 ）。
                body = _for_her(raw.decode("utf-8"))
                if not body.strip():
                    continue
                yield path.relative_to(base).as_posix(), Remembered(ts="", source=path.name, valid_from="", role="", text=body)

#: **2026-10-10（甲）：取的时候"近的更重"** —— 作者拍的 `N = 20` 。
#: ⚠️ **为什么非有不可** ： 原来的 `search` **只算相关**（ BM25 ）
#: ⇒ "三个月前说过的话"与"刚才说的话"**一样重** （ 笔记 §5.2 那条：
#: Generative Agents 的三轴 `recency` ／ `importance` ／ `relevance` ，
#: 而我们**只有最后一轴** ）。
#: ⚠️ **为什么半衰期是"一天"** （ 而这个数**不是我拍的** ）： 量过盘上
#: **199 轮 ／ 9 天** ⇒ 正常对话日 **中位 8 轮 ／ 平均 9 ／ 最多 20**
#: ⇒ 作者拍的 `N = 20` 恰好 ＝ **"一整天说过的话"** ⇒ 于是"一天"
#: 就是这一支的自然刻度 。
#: ⚠️ **它是算式，不是一个开关** ： `0.5 ** (多少天前 / 半衰期)` ——
#: 今天 1.0 ／ 昨天 0.5 ／ 前天 0.25 （ 越久越轻 ， 而不**切**掉谁 ）。
RECENCY_HALF_LIFE_DAYS = 1.0


def _a_recency_weight(ts: Any, now: Any) -> float:
    """ **那一条有多近**（ 交回一个 0..1 的权重 ）—— 认不出 ⇒ **1.0** 。

    ⚠️ **认不出就交 1.0**（ 不是 0 ）： 这一层**不许猜** （ 与 `_the_moment_of`
       同一条口径 ）—— 一个没写时间的条目**不该因此被罚** 。
    ⚠️ **它不是"切掉旧的"** —— 它是**乘法**： 旧的仍然可能取到 ，
        只是**排在新的后面** （ 而"该不该丢"是上界那几块的事 ）。
    """
    if now is None:
        return 1.0
    # **"现在"是什么形，这一层不挑** —— 入口那个 `clock()` 交的可能是
    # datetime 、 也可能是一串 ISO 文本 （`_note_used` 就是**原样**把它
    # 递给 `used.put` 的 ）⇒ 两种都认 ； 都不是 ⇒ 交 1.0 （ 不猜 ）。
    if isinstance(now, str):
        try:
            now = datetime.datetime.fromisoformat(
                now.replace("Z", "+00:00").replace(" ", "T")[:19])
        except ValueError:
            return 1.0
    if not isinstance(now, datetime.datetime):
        return 1.0
    if now.tzinfo is not None:
        now = now.astimezone().replace(tzinfo=None)
    raw = str(ts or "").strip()
    if not raw:
        return 1.0
    when = None
    if raw.isdigit():
        got = int(raw)
        if got > 10_000_000_000: # 毫秒（13 位）⇒ 折成秒
            got = got // 1000
        try:
            when = datetime.datetime.fromtimestamp(got)
        except (OverflowError, OSError, ValueError):
            when = None
    if when is None:
        try:
            when = datetime.datetime.fromisoformat(
                raw.replace("Z", "+00:00").replace(" ", "T")[:19])
        except ValueError:
            when = None
    if when is None:
        return 1.0
    if when.tzinfo is not None: # 带时区的折回本地 （否则相减会当场抛 ）
        when = when.astimezone().replace(tzinfo=None)
    days = max(0.0, (now - when).total_seconds() / 86400.0)
    return float(0.5 ** (days / RECENCY_HALF_LIFE_DAYS))


#: **它标的那个"轻重"里的"一般"是哪一档**（2026-10-12 作者：「\u5979自己标」）。
#:   ⚠️ **3 ＝ 一般 ⇒ 乘 1.0** —— 与"没标"同一个下场（不奖不罚）；1 很轻、5 很重。
#:     那三档的说法写在 `tools.A_WHO_TEXT` 的 `weight` 那一格上（给模型看的那一份）。
WEIGHT_FOR_THE_MIDDLE = 3


def _a_weight_in_the_ranking(weight: Any) -> float:
    """ **它自己给的那一格轻重**（1..5）—— 交回一个乘数（**没标 ⇒ 1.0**）。

    ⚠️ **这一层不猜、不推**（与 `_a_recency_weight` 那条"认不出 ⇒ 1.0"同一条口径）：
       没标 ／ 认不出 ⇒ 1.0（按"一般"算）。
    ⚠️ **乘，不加**：与那一处的理由同一条 —— 加一项就没法把一条**压下去**。
    ⚠️ **它不是"丢掉轻的"**：1（很轻）只是**排在后面**，不切掉谁（"该不该丢"是上界那几块的事）。
    """
    if weight is None:
        return 1.0
    try:
        gave = int(weight)
    except (TypeError, ValueError):
        return 1.0
    return max(1, min(5, gave)) / float(WEIGHT_FOR_THE_MIDDLE)


def search(home: Path, query: str, *, k: int = 5, now: Any = None,
           visibility: Any = None) -> list[dict[str, Any]]:
    """Exact BM25 ranking over validated sources, with bounded corpus memory."""
    from .memory_ranking import search as bounded_search
    return bounded_search(home, query, k=k, now=now, visibility=visibility)


#: **"一次给多少"那两根旋钮**（2026-10-17，量出来才加的）。
#:
#: ⚠️⚠️ **它们治的是什么**（真事）：让它"念那本词典里的几条"／"从头念二十条"，
#:   它**一次都没调过 `kb_read`**（5 种问法 ／ 7 次观察全是 0），而是把同一个
#:   `kb_search` 重问 4—6 遍，再不然就**凭印象编**（`灯下看花`／`独木成林` 那三条，
#:   词典那 500 条里一条都没有）。⇒ **"要更多"在它那儿不映射成"换一件工具"**
#:   ⇒ 于是那两条路都开在**同一扇门里**（`kb_search` 的 `lines` 参数 ＋ 名字命中时给目录）。
#:
#: · `OUTLINE_AT_MOST` —— 查的是**哪一本**（命中文件名）时，列它自己的 `##` 名字几条。
#:   那一条回答的是"这一本里有什么"（原来会给一节不相干的正文 —— 因为名字命中的
#:   那一条**没有哪一节"命中更多"**，于是退回"开头那几行"）。
#: · `LINES_AT_MOST` —— 同一扇门里"看全"的上界。⚠️ **必须有上界**：一本词典 1000 行，
#:   真给全了就是把它的上下文灌满 —— 而那正是 `kb_read` 当初立起来要防的那件事。
OUTLINE_AT_MOST = 12
LINES_AT_MOST = 200
#: 只给了 `from_line`（没给 `lines`）时给一块多大 —— 见 `kb_search` 那一处。
A_CHUNK = 40
#: ★ **目录之后附几条完整词条**（2026-10-17）—— 见 `_the_head_entries` 那一段。
#: ⚠️ **量出来的**：被问"那一本里收了哪些词，挑几个念给我听"，它查了库、
#:    却**反复重问 12 次、最后一句不答**。而"名字命中 ⇒ 给目录"交回的是
#:    **一串名字 ＋ "里面 500 条"** —— 那一问要的是"**能念的东西**"（词条正文），
#:    目录给不了 ⇒ 于是它接着找。这一格就是那"能念的东西"。
HEAD_ENTRIES = 3


def _the_outline(text: str, *, at_most: int = OUTLINE_AT_MOST) -> tuple[str, int, int]:
    """ 那一份自己的**目录** —— 交 `(那几行, 给了几条, 一共几条)`。

    ⚠️ **名字一条不编**：全取它自己的小节标题（`_the_sections` 的第一行）——
       那是"这一本里到底有什么"唯一诚实的答法。
    ⚠️ **给不完就说清一共几条**（与"整份多少行"同一条规矩：数不许编、也不许藏）。
    """
    names = [one[0].lstrip("#").strip() for one in _the_sections(str(text).splitlines())
             if one and one[0].startswith("##")]
    if not names:
        return "", 0, 0
    shown = names[:max(1, at_most)]
    return "、".join(shown), len(shown), len(names)


def _the_head_entries(text: str, *, how_many: int = HEAD_ENTRIES) -> tuple[str, int]:
    """ 头几条的**正文**（目录之后给"能念的东西"）—— 交 `(那几段, 到第几行为止)`。

    ⚠️ **它治的是什么**（量出来的）：问"那一本里收了哪些词，挑几个念给我听" ⇒
       "名字命中 ⇒ 给目录"交回**一串名字**（没有正文）⇒ 它拿不到"能念的东西"
       ⇒ 同一批里把 `kb_search` 重问 12 次、最后一句不答。
       ⇒ 目录后面附上头几条的**完整词条**（名字 ＋ 拼音 ＋ 释义 ＋ 例句，全是真的）。
    ⚠️ **第二样回值（行偏移）是承重的**：续看那一句要用它当 `from_line` ——
       少了它，"接着看"会从**第 0 行**重来（而目录里那个数是**条数**，不是行数，
       拿它当偏移会跳过几行 —— 这是同一处原来就有的一个小错，一并修掉）。
    ⚠️ **没有 `## ` 小节的那一份 ⇒ 交回空**（调用方据此退回摘录）。
    """
    lines = str(text).splitlines()
    heads = [i for i, one in enumerate(lines) if one.startswith("## ")]
    if not heads:
        return "", 0
    stops = heads[1:] + [len(lines)]
    take = max(1, min(int(how_many), len(heads)))
    pieces = ["\n".join(lines[at:stops[n]]).rstrip()
              for n, at in enumerate(heads[:take])]
    return "\n".join(pieces), stops[take - 1]


def portrait_kind_label(kind):
    return {"recent_context": "近期情况（仅代表记录时，不推定长期或仍有效）",
            "plan": "计划（不代表已完成，当前是否有效需核对）",
            "stable_preference": "持续偏好", "stable_rule": "持续规则",
            "habit": "习惯", "lasting_state": "持续状态", "lasting_change": "长期变化"}.get(kind, "历史记录（未分类，不自动视为长期特征）")


def _said_about(rows: list[Remembered], *, head: str, visible_source=None) -> str:
    """ 把几条记录写成**一句事实**（回执）—— 每条带**什么时候 ＋ 哪来的**（§五 第 4 条）。"""
    if not rows:
        return head + "：没有记过。"
    from .memory_receipt import EVIDENCE_BOUNDARY
    from .portrait_eligibility.provenance import reference
    lines = [head + f"：{len(rows)} 条（画像整理记录）", EVIDENCE_BOUNDARY,
             "记录时间指写入画像的时间。有来源引用的条目可回查源消息时间；无引用的早期记录未关联源消息时间。"]
    for one in rows:
        when = one.ts or "（没写时间）"
        where = one.source or "（没写来源）"
        lines.append(f"· [记录时间：{when}] [{portrait_kind_label(one.memory_kind)}] {one.text}（来源范围：{where}）")
        if one.portrait_sources and visible_source is not None and visible_source(one):
            lines.append(f'用 profile_read 的 evidence_ref 回查原话：{reference(one)}')
    return "\n".join(lines)


def _note_used(base: Any, clock: Any, reader: str, keys: list[str]) -> None:
    """ **记一笔"用过"**（ 两样都没递 ⇒ 什么也不做 ； 记不成也不许带走这一次读 ）。"""
    if base is None or clock is None or not keys:
        return
    try:
        from . import used as used_module
        when, day = clock()
        # ⚠️ **哪几件读口不接**（照实说，不是漏）：`memory_search` 交的是**一段一段的经历**
        # （没有"一条的名字"那样的稳定键 ）；`pending_read` 交的是**整份清单一遍**
        # （它没有"命中某一条"这件事 ）⇒ 那两件接不上"哪一条被用过"这个粒度。
        # 等它们各自有了稳定的键再接 （今天不硬接一个假的 ）。
        for one in keys:
            used_module.put(base, reader=reader, key=one, stamp=when, day=day)
    except Exception: # noqa: BLE001 —— 账记不成，读照旧给
        return

def handlers(home: Path, *, the_other: str = "", used_base: Any = None,
             self_source: str | None = None,
             affect_context: Callable[[str], Mapping[str, Any]] | None = None,
             affect_clock: Callable[[], Any] | None = None,
             affect_assessment_sink: dict | None = None,
             clock: Any = None,
             recorded_clock: Callable[[], str] | None = None,
             source_events: Callable[[], Iterable[int]] | None = None,
             profile_context: Callable[[], tuple[str, str, str]] | None = None,
             profile_targets: Callable[[], Mapping[str, str]] | None = None,
             profile_aliases: Callable[[str], Iterable[str]] | None = None,
              retrieval_context: Callable[[], Any] | None = None,
             semantic_memory: Any = None,
             semantic: Callable[[str, Sequence[str]], Sequence[int]] | None = None,
             indexed_literal: Callable[[str], Sequence[str]] | None = None
             ) -> dict[str, "Callable[..., str]"]:
    """ 交出那几扇门。

    ⚠️ **2026-10-07「用法信号」**多收两样（都可有可无 ）：
       · `used_base` —— **用法账**放在哪儿（ 它是"某一条被用过一次"的记录 ）；
       · `clock` —— 叫它一次交 `(时刻, 哪一天)`（ 本模块**不读钟** ⇒ 由调用方递 ）。
       两样都在 ⇒ **读口命中时记一笔** ；少任何一样 ⇒ **照旧工作**（只是不记 ）。

    ⚠️★ **2026-10-16（作者：「B」）又多收一样：`semantic`** —— **语义那一路**。

       形状：`semantic(查的那句话, 一串名字) -> 那一串里的下标`。
       ⚠️ **为什么是"递进来"、不是本模块 import**：本模块**零同胞 import**
          （上面那一段全是标准库），而向量那两层住在 `embeddings` ／ `search` 里。
          ⇒ 照本文件既有的成法（**要用的时候延迟 import**，见 `used` 那一处），
            而**把那条路本身交给入口递** —— 与 `clock` ／ `used_base` 同一条口径：
            **本模块不拥有那些后端，也不假设它们在。**
       ⚠️ **缺席 ⇒ 照旧只走字面**（今天的行为一字不变）—— 那是实话，不是降级。
       ⚠️ **递进来的那个坏了 ⇒ 也不许把这一扇门带走**：`kb_search` 兜住它，
          退回字面那一份（与"插件崩了不许把 core 带走"同一条纪律）。
    """
    """ **记忆层那几个执行段**（喂给 `tools.build_table(DECLARED_TOOLS, …)`）。

     契约照 `unresolved.handlers`（那一份是样板）：交回**回执**（一句事实，模型看的就是它）；
      参数**对象 ⇒ 关键字**、裸值 ⇒ 第一个位置参数 ⇒ 每个 handler 都收 `**rest`。
    ⚠️ **只交今天真答得出来的**：
       · `memory_search` ⇒ `search()`（P0⑤ 那一刀 ）
       · `profile_read` ⇒ `profile_of()`（画像 ）
       · `relations_read`⇒ `relations_of()` ＋ `current_standing()` ＋ `blocked_state()`（关系 ）
       · ★ **2026-10-13 订正**：`kb_search` **交了**（知识库那一层做出来了 ⇒
         这一格也交出去了，与 `kb_read` 一起）。
         ⚠️ 原先这里写的是「`kb_search` **不交**（知识库那一层还没做）」——
         那条「不许交一个空 handler 假装有」的道理照旧成立，只是今天它真有了。
    ⚠️ 这一层**不解释内容**：它只把记下的读出来拼成一句话，不总结、不推断 。
    """
    def _retrieval_scope() -> Any:
        from .retrieval_scope import current, from_profile
        if retrieval_context is not None:
            return current(retrieval_context)
        if profile_context is not None:
            # Pre-scope in-process clients used an empty portrait context to
            # suppress implicit writes, not to designate an external caller.
            # Keep that internal API compatible. Production must supply the
            # dedicated retrieval provider above; its empty/broken value NEVER
            # takes this branch and therefore never widens access.
            try:
                context = profile_context()
            except Exception:
                raise PermissionError("当前可信会话范围不可用，检索已拒绝。") from None
            if context == ("", "", ""):
                return None
            return current(lambda: from_profile(lambda: context))
        # Unscoped library APIs remain available to trusted internal callers.
        # Production always injects retrieval_context, even for empty batches.
        return None

    def _the_moment() -> str:
        """ **叫一次那个钟**（ 由调用方递 ）—— 拿不到 ⇒ **空那一串** （ 不编 ）。

        ⚠️ 与 `_note_used` 同一个形状（`when, day = clock()` ）——
            而这里只用得上**时刻**那一半 （写进 `ts` ／ `valid_from` ）。
        """
        if not callable(clock):
            return ""
        try:
            when, _day = clock()
        except Exception: # noqa: BLE001 —— 钟坏了 ⇒ 照旧空着（不编 ）
            return ""
        return str(when or "")

    def _profile_target(who: str) -> tuple[str, str, str]:
        """Resolve a portrait from the current trusted room, not a nickname.

        Returns (portrait name, provenance, error). An external private caller
        may touch only its account portrait. A single group speaker may write
        its own portrait implicitly; in a multi-speaker batch only an exact
        current author key may select a portrait. Nicknames never select one.
        The local console may explicitly name someone else.
        """
        requested = str(who or "").strip()
        if profile_context is None:
            return requested or str(the_other).strip(), "", ""
        try:
            scope, profile_key, label = profile_context()
        except (OSError, ValueError, TypeError):
            return "", "", "当前身份记录读不到，画像操作已停止。"
        scope = str(scope or "")
        profile_key = str(profile_key or "").strip()
        label = str(label or "").strip()
        if scope == "mixed":
            return "", "", "混合来源不能确定当前画像归属。"
        if scope.startswith("group:"):
            if requested and callable(profile_targets):
                try:
                    targets = profile_targets()
                    selected = str(targets.get(requested, "") or "").strip()
                except (OSError, ValueError, TypeError, AttributeError):
                    return "", "", "本轮群聊发言者记录读不到，画像操作已停止。"
                if selected:
                    return selected, scope + "|sender=" + requested, ""
            if not profile_key or not label:
                return "", "", "本轮群聊没有唯一可信发言者，不能猜画像归属。"
            if requested and requested not in (profile_key, label):
                return "", "", "群聊只能读写本轮可信发言者的画像；请使用其账号键。"
            return profile_key, scope + "|sender=" + label, ""
        if scope.startswith("private:"):
            local = scope == "private:console:local-owner"
            if profile_key:
                if not requested or requested in (profile_key, label):
                    return profile_key, scope, ""
                if local:
                    return requested, scope, ""
                return "", "", "当前私人对话只能读写已确认联系人的画像；请省略 who。"
            if not local:
                return "", "", "此入口尚未绑定已确认身份，不能读写私人画像。"
            return requested or str(the_other).strip(), scope, ""
        # Autonomous work has no current contact. Do not silently use the
        # deployment-wide counterpart as if someone had just arrived.
        return requested, "", ""

    from threading import Lock as _CounterLock
    _search_counter_lock = _CounterLock()
    _search_counts = {'calls': 0, 'unknown_argument_calls': 0, 'saturated': False}
    def _search_diagnostics():
        with _search_counter_lock:
            return {'scope': 'this_handler_lifetime', **_search_counts}

    def memory_search(query: str = "", k: int = 5, memory_ref: str = "", offset: int = 0, **rest: Any) -> str:
        with _search_counter_lock:
            if _search_counts['calls'] == 2**63 - 1:
                _search_counts['saturated'] = True
            else:
                _search_counts['calls'] += 1
                _search_counts['unknown_argument_calls'] += int(bool(rest))
        if rest:
            raise ValueError("memory_search only accepts query/k/memory_ref/offset; use kb_search for knowledge windows")
        # **2026-10-10（甲）：把"现在"递进去** —— 本模块**不读钟**
        # （ `handlers` 那个 `clock` 口就是为这一类事留的 ）； 没递 ⇒ 权重全 1.0
        # （ 也就是**逐字回到从前** ）。
        now = None
        if callable(clock):
            try:
                now = clock()[0]
            except Exception: # noqa: BLE001 —— 问不到就照旧
                now = None
        visibility = _retrieval_scope()
        from . import memory_receipt
        if memory_ref:
            from contextlib import closing
            with closing(_iter_documents(home, visibility=visibility)) as documents:
                return memory_receipt.read(documents, memory_ref, offset)
        if offset:
            raise ValueError("offset requires memory_ref")
        got = search(home, str(query), k=min(10, int(k)) if str(k).isdigit() else 5, now=now,
                     visibility=visibility)
        if not got:
            from .memory_followup import single_character_originals
            got = single_character_originals(home, str(query).strip(),
                limit=min(10, int(k)) if str(k).isdigit() else 5, visibility=visibility)
        semantic_note = ""
        if not got and str(query).strip() and callable(semantic_memory):
            from .search import SemanticUnavailable
            docs = [(where, row) for where, row in _documents(home, visibility=visibility)
                    if not (where.startswith(TREES[0] + '/') and row.role in ('system', 'developer'))]
            try:
                ranked = semantic_memory(str(query), [row.text for _, row in docs])
                if isinstance(ranked, SemanticUnavailable):
                    semantic_note = "语义检索正在准备或暂不可用，本次仅完成字面检索。"
                else:
                    count = min(10, int(k)) if str(k).isdigit() else 5
                    for index in list(dict.fromkeys(ranked))[:max(0, count)]:
                        where, row = docs[index]
                        got.append(dict(text=row.text, where=where, ts=row.ts, source=row.source,
                                        role=row.role, memory_kind=row.memory_kind, score=0))
            except Exception:
                semantic_note = "语义检索暂不可用，本次仅完成字面检索。"
        if not got:
            number = _literal_number(str(query))
            if number:
                standalone = (
                    semantic_note + f'当前可见记忆资料未匹配「{query}」。这次检索没有查看原始记录。'
                    f'若需核对原文，可在本轮提供 read_events 时按检索词里的编号查：'
                    f'read_events(store="records", grep="{number}")。有无对应原文仍待核实。'
                    '若要翻阅记忆文件，可在有 memory_navigate 时查看 section="memories" 的目录。'
                )
            else:
                standalone = (
                    semantic_note + f"当前可见记忆资料中未找到和「{query}」匹配的条目；这不代表相关事实没有发生。"
                    '本次未遍历全部原始记录，也不能据此认定当前范围没有可读资料。'
                    '若当前提供 memory_navigate，先用 section="root" 查看可用入口，再展开 section="memories" '
                    '按文件目录找线索，用条目里的 memory_ref 读取原文和后续页；这条路径不依赖关键词或向量命中。'
                    'read_events(store="records") 可核对原始会话与工具回执，但该库的空结果或本页计数不代表全部记忆文件为空。'
                )
            if _retrieval_scope() != visibility:
                raise PermissionError('memory visibility changed during retrieval')
            return memory_receipt.MemorySearchMiss.for_query(
                standalone, query=str(query), number=number, semantic_note=semantic_note, visibility=visibility)
        from .memory_followup import expand_titles
        got = expand_titles(home, got, limit=min(10, int(k)) if str(k).isdigit() else 5, visibility=visibility)
        if _retrieval_scope() != visibility:
            raise PermissionError('memory visibility changed during retrieval')
        # Ranking omits each row's grant. For a one-room non-admin search, every
        # returned user-transcript row necessarily came from that exact room.
        from .retrieval_scope import RetrievalScope
        single_scope = (visibility.current if type(visibility) is RetrievalScope and not visibility.administrator
                        and visibility.allowed == frozenset({visibility.current}) else None)
        return memory_receipt.render(str(query), got, single_scope=single_scope)

    def _profile_names(name: str) -> list[str]:
        if profile_aliases is None:
            return [name]
        names = profile_aliases(name)
        if isinstance(names, str):
            raise ValueError("画像关联记录格式不正确")
        keys = sorted(set(names))
        if not keys or name not in keys or len(keys) > 64:
            raise ValueError("画像关联记录格式不正确")
        for key in keys:
            _a_portrait_name(key)
        return keys

    def _profile_rows(reader: Callable[..., Any], names: list[str]) -> list[Any]:
        rows = [row for key in names for row in reader(home, key)]
        if len(names) < 2:
            return rows
        def order(row: Any) -> str:
            raw = str(row.valid_from or row.ts or "")
            try:
                parsed = (datetime.datetime.strptime(raw, "%Y%m%d-%H%M%S")
                          if re.fullmatch(r"[0-9]{8}-[0-9]{6}", raw)
                          else datetime.datetime.fromisoformat(raw))
                parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
                return parsed.isoformat()
            except ValueError:
                return raw
        return sorted(rows, key=order)

    def misplaced_source_hint(who, evidence_ref, offset, snapshot):
        if (evidence_ref or offset or snapshot or not isinstance(who, str)
                or not re.fullmatch(r'portrait-[0-9a-f]{64}', who)):
            return ""
        target = _profile_target("")
        person, room, denied = target
        if denied or not person or not room:
            return ""
        from .portrait_eligibility.provenance import reference
        try:
            visibility = _retrieval_scope()
        except PermissionError:
            return ""
        if visibility is None:
            return ""
        aliases = _profile_names(person)
        # Consult only the current contact's permitted index. Originals are
        # read and verified only if the model chooses to open this reference.
        group = room.partition("|sender=")[0] + "|sender=" if room.startswith("group:") else ""
        for alias in aliases:
            for row in profile_of(home, alias):
                if (row.portrait_sources and visibility.memory(f"{PEOPLE_TREE}/{person}.jsonl", row)
                        and row.source.startswith(group)
                        and reference(row) == who):
                    try:
                        current_visibility = _retrieval_scope()
                    except PermissionError:
                        return ""
                    if (current_visibility == visibility and _profile_target("") == target
                            and _profile_names(person) == aliases and row in profile_of(home, alias)):
                        return f'这是当前画像的来源引用。核对原话可用 profile_read(evidence_ref="{who}")。'
        return ""

    def profile_read(who: str = "", offset: int = 0, snapshot: str = "", evidence_ref: str = "", **rest: Any) -> str:
        # **2026-10-07**：`who` 空着 ⇒ 用**递给这一层的那个默认人**（"对方" ）。
        # ⚠️ 那个名字**不在本模块里** —— 由入口从**部署那一侧**递进来
        # （本文件不认识任何人的名字：那是部署的事，不是内核的事 ）。
        name, source, error = _profile_target(who)
        if error:
            return misplaced_source_hint(who, evidence_ref, offset, snapshot) or error
        if not name:
            return "没说是谁 —— 得先有那个人的名字。"
        names = _profile_names(name)
        rows = _profile_rows(profile_of, names)
        if not rows:
            hint = misplaced_source_hint(who, evidence_ref, offset, snapshot)
            if hint:
                return hint
        if source.startswith("group:"):
            # Group participants may not retrieve facts learned in a private
            # room merely because the account was manually linked.
            room = source.split("|sender=", 1)[0] + "|sender="
            rows = [row for row in rows if row.source.startswith(room)]
            if not rows:
                return f"「{name}」在本群还没有可用画像记录。"
        # **2026-10-07「用法信号」**：读的是**某一个的那一份** ⇒ 记"它回头查过这个人"
        # （ 而"每轮注入"那种不算 —— 见 `_note_used` 那一段）。
        _note_used(used_base, clock, "profile_read", [name])
        from .memory_receipt import profile_page
        if evidence_ref:
            from .portrait_eligibility.provenance import reference, source_page
            visibility = _retrieval_scope()
            verified_sources = profile_add._portrait_write_binding.get("verified_sources")
            for row in rows:
                if (row.portrait_sources and reference(row) == evidence_ref and visibility is not None
                        and verified_sources is not None
                        and visibility.memory(f"{PEOPLE_TREE}/{name}.jsonl", row)):
                    with verified_sources() as lookup:
                        view = source_page(row, visibility, lookup, offset=offset,
                                           snapshot=snapshot, evidence_ref=evidence_ref)
                    if not any(row in profile_of(home, key) for key in names):
                        raise PermissionError("画像条目已变化，请重新读取。")
                    if (_retrieval_scope() != visibility or _profile_target(who) != (name, source, "")
                            or _profile_names(name) != names):
                        raise PermissionError("画像原话来源的可见范围已变化，请重新读取。")
                    return view
            raise PermissionError("画像原话引用已变化或当前不可读，请重新读取当前可见画像。")
        visibility = _retrieval_scope() if any(row.portrait_sources for row in rows) else None
        visible_source = (lambda row: visibility.memory(f"{PEOPLE_TREE}/{name}.jsonl", row)) if visibility else None
        body = _said_about(list(reversed(rows)), head=f"关于「{name}」", visible_source=visible_source)
        return profile_page(body, offset=offset, snapshot=snapshot)

    def relations_read(who: str = "", **rest: Any) -> str:
        """读关系：和谁是什么关系 —— 2026-10-10 起也交出它自己给过的那个分。

        为什么这一格非补不可（量到的）：remember_relation(..., favor=...) 早就存得下、
        也读得出来（favor_of ／ 页面那一头给人看），而这一扇门从前不交它，
        于是它写得出、却看不见（作者拍的「甲2」）。

        为什么不塞进常驻（甲1）：那一层是稀缺的（细节放前缀里会污染上下文），
        而这个数只在跟人说话时用得上；它又是按人的，塞进常驻等于把某一个人的
        信息摊在所有场合。

        没给过那个数就照实说"没给过" —— 不许编一个缺省值（与 favor_of 的 None 同一条）。
        """
        # 同上（2026-10-07）：空着 ⇒ 用那一层收下的默认人
        who, source, error = _profile_target(who)
        if error:
            return error
        if not who:
            return "没说是谁 —— 得先有那个人的名字。"
        _note_used(used_base, clock, "relations_read", [who])
        names = _profile_names(who)
        rows = _profile_rows(relations_of, names)
        combined = len(names) > 1 or source.startswith("group:")
        if source.startswith("group:"):
            room = source.split("|sender=", 1)[0] + "|sender="
            rows = [row for row in rows if row.source.startswith(room)]
        if not rows:
            return (f"和「{who}」在本群还没有可用关系记录。"
                    if source.startswith("group:") else f"和「{who}」没有记过关系。")
        standing = (rows[-1].standing if combined else
                    current_standing(home, who)) or "（没写说法）"
        if combined:
            blocked_here = next((row.blocked for row in reversed(rows)
                                 if row.blocked is not None), False)
        else:
            blocked_here = blocked_state(home, who)
        blocked = "是" if blocked_here else "不是"
        mine = (next((row.favor for row in reversed(rows) if row.favor is not None),
                     FAVOR_DEFAULT) if combined else favor_of(home, who))
        said = str(mine) if any(one.favor is not None for one in rows) else "0（默认，没给过分）"
        lines = [f"和「{who}」：现在的说法是「{standing}」；当前好感度：{said}；"
                 f"拉黑：{blocked}；共记过 {len(rows)} 条。"]
        for one in rows:
            lines.append(f"· [{one.ts or '（没写时间）'}] {one.standing}"
                         + (f" —— {one.why}" if one.why else ""))
        return "\n".join(lines)

    from . import learned_knowledge

    def knowledge_note(**fields: Any) -> str:
        return learned_knowledge.save(
            home, _retrieval_scope(), source_events=tuple(source_events()) if source_events else (),
            **fields)

    def kb_search(query: str = "", k: int = 5, lines: Any = None,
                  from_line: Any = None, **rest: Any) -> str:
        from .knowledge_corpus import KnowledgeCorpus
        with KnowledgeCorpus() as corpus:
            return _kb_search(corpus,query,k,lines,from_line,**rest)

    def _kb_search(pull, query: str = "", k: int = 5, lines: Any = None,
                  from_line: Any = None, **rest: Any) -> str:
        """ 查**知识库**（设定 ／ 术语 ／ 已经定下来的事）—— 只查**知识那一棵树**。

        ⚠️ 与 `memory_search` 分工清楚：那一边查**经历过什么**，这一边查**定下来的事** 。

        ⚠️ **2026-10-10：补上条数上限**（`k`，缺省 **5** —— 与 `memory_search`
           同一个数 ）。**为什么非有它不可**（本机量到）：知识那一棵是
           **一份文件 = 一条**，而这一扇门**从前一条上限都没有** ⇒
            旧项目那份词典按"一份一条"搬进来之后（500 条成语），一次常见的查
           （「形容」）**命中 165 条、交回 23866 字** —— 那是往它眼前灌一整本 。
        ⚠️ **它照实说"对得上几条"**（截了就说截了 ）：把"对得上 N 条"与"给你的这几条"
           分开写 —— 不然"只给了 5 条"会被读成"库里只有 5 条" 。
        """
        visibility = _retrieval_scope()
        pull.load(home,visibility,query)
        if not len(pull):
            if _retrieval_scope() != visibility:
                raise PermissionError('knowledge visibility changed during retrieval')
            from .memory_receipt import KnowledgeSearchMiss
            note = '本次可见的知识条目为 0，未进行语义比对。本次未检索原始对话。'
            if visibility is not None and not visibility.administrator:
                note += '这里只统计当前会话获准读取的条目。'
            return KnowledgeSearchMiss.observed(note, query, visibility, 0)
        units = pull.units
        # Retain only displayed results and the existing eight usage signals.
        # Invalid k remains lazy for a genuinely empty result, as before.
        try:limit = max(8,max(1,int(k)))
        except (TypeError,ValueError,OverflowError):limit = 8
        indexed = (lambda:indexed_literal(str(query))) if indexed_literal is not None and visibility is not None and visibility.administrator else None
        literal_count,scored = pull.ranked(limit,indexed)
        literal_road = [one_name for _score,one_name in scored]

        # ── ② 只有**字面一条都没中**的时候，才去问语义那一路 ────────────────
        # ⚠️★ **2026-10-16（作者拍板）：这是「兜底」，不是「融合」。**
        #    ⚠️ 为什么**不做融合**（当天实测）：三个查询各比了一遍，
        #       **RRF 一次都没改变排序** —— 因为字面那一路本来就把正确那条排在
        #       第 1（中文按字分词 ⇒ 字面很宽：「借书的地方」命中 22 条，
        #       而正确那条仍是第一）。⇒ 融合**没带来价值，只带来假阳性**：
        #       `ZZZXXX` 那种垃圾输入原来诚实回"没有对得上的"，
        #       融了之后会回 **18 个看起来正经的文件**。
        #    ⚠️ 而这个模型**给不出"够不够像"的信号** —— `embeddings.a_road` 的
        #       注释里作者量过：**无关的一对相似度也能到 0.47** ⇒ 只敢用相对阈值
        #       ⇒ 它永远会交回"最近的那几个"，**分不出"真相关"与"只是最近"**。
        #    ⇒ 所以：**字面命中 ⇒ 原样走字面（零退步）；字面为零 ⇒ 才问语义，
        #       并且把"这不是命中"写进回执**（下面那个 head 就是干这个的）。
        #    ⚠️ 顺带省一次：字面有命中就**不碰向量那一层**（不必加载模型）。
        semantic_road: list[str] = []
        semantic_state = "未启用"
        if not literal_road and callable(semantic):
            names = list(pull)
            try:
                from .search import SemanticUnavailable
                result = semantic(str(query), names)
                semantic_state = "暂不可用" if isinstance(result, SemanticUnavailable) else "已完成"
                picked = list(result)
                semantic_road = [names[i] for i in picked
                                 if isinstance(i, int) and not isinstance(i, bool)
                                 and 0 <= i < len(names)]
            except Exception:  # noqa: BLE001 —— 不许带走这一扇门（见 `handlers` 那段）
                semantic_road = []
                semantic_state = "未完成"

        by_semantic = not literal_road
        fused = semantic_road if by_semantic else literal_road
        match_count = len(fused) if by_semantic else literal_count

        if not fused:
            scope_note = f"当前可见知识的字面检索没有和「{query}」对得上的（可见 {len(pull)} 条）。"
            if _retrieval_scope() != visibility:
                raise PermissionError('knowledge visibility changed during retrieval')
            from .memory_receipt import KnowledgeSearchMiss
            if semantic_state == "已完成":
                note = scope_note + "语义检索本次也未返回候选；这不证明相关事实从未发生。"
            else:
                note = (scope_note + f"语义检索{semantic_state}，不能据此断言知识库没有相关内容。"
                        "可换关键词查找，或读取已知条目；本次未检索原始对话。")
            return KnowledgeSearchMiss.observed(note, query, visibility, len(pull))

        # **2026-10-07「用法信号」**： **命中**才算"用过"（ 每轮注入那种不算 ）——
        # 于是"哪一条被回头查过 ／ 查了几回"从此有痕迹 （去留判据以后接它 ）。
        # ⚠️ 它记的是**命中**那一份（不是下面截出来的那几条 ）—— 照旧那一条规矩。
        _note_used(used_base, clock, "kb_search", [one for one in fused[:8]])
        # **上限**（2026-10-10）：`k` ⇒ 只把最靠前的那几**份**摆出去 。
        # ⚠️⚠️ **"份"这个字是量出来才换的**（2026-10-17）：
        #    原来这里通篇用「**条**」，而"里面 500 条"那一段也在用同一个字 ——
        #    **一个"条"字被当成两种东西**（`有 N 条` ＝ 几**份文件**；`里面 500 条` ＝ 几**个词条**）。
        #    ⇒ 实测的后果：问「你那本成语词典收了哪些词」，它**反复调 `k`**
        #      （`k=5` → `k=10` → `k=5` → … 一趟 13 次），**一句不答** ——
        #      它显然把 `k` 读成"要几个**词条**"，而它管的是"要几**份**"
        #      （命中只有 1 份时，调 `k` 一点变化都没有 ⇒ 它就一直调下去）。
        #    ⇒ 从此**两样东西两个量词**：文件叫「份」，词条叫「个词条」。
        shown = fused[:max(1, int(k))]
        if by_semantic:
            # ⚠️★ **这一句是这次改动的要害**：它是"最接近的"，**不是"对得上的"**。
            #    少了这一句，调用方会把这几条当成真找到了 —— 而那正是"用假阳性
            #    换真阳性"的那种退步。**宁可说得难听，也不许让调用方误信。**
            head = (f"字面一份都没中「{query}」。这是语义上最接近的 {match_count} 份"
                    f"（这个模型分不出「真相关」与「只是最近」，只当线索看）")
        else:
            head = f"知识库里对得上「{query}」的有 {match_count} 份"
        if len(shown) < match_count:
            head += f"，这是排在最前的 {len(shown)} 份："
        else:
            head += "："
        # **一次只给「命中那一条」**（2026-10-10 那一刀「乙」）—— 不给整份
        # （ 于是"一份 500 条"的词典可以整份放进来，不必拆 500 个文件 ）。
        # ⚠️ **每一条还报"整份多少行"**：它据此知道那一本有多大 （ 数不许编 ）。
        by_literal = {one_name: score for score, one_name in scored}
        # ★ **"要看几行 ／ 从第几行起"**（2026-10-17，`lines` ＋ `from_line` 那两格）。
        # ⚠️ 上界是硬杠（见 `LINES_AT_MOST`）：不给上界就等于把"整份灌进上下文"那条老路
        #    重新打开一次 —— 而那一刀当初正是为了堵它。
        # ★★ **`from_line` 是量出来才补的**（同一天）：只给 `lines` 时它**连问了 12 次** ——
        #    因为它要的是"**实事求是那一条前后**"，而 `lines=200` 只从第 0 行起，
        #    那一条埋在开头几节之后。⇒ 起点也得能填，这条路才算闭合
        #    （`kb_read` 一直有 `offset`，而这一扇门原来没有对应的那一格）。
        want, start = None, 0
        if lines is not None and str(lines).strip() != "":
            try:
                want = max(1, min(int(lines), LINES_AT_MOST))
            except (TypeError, ValueError):
                want = None
        if from_line is not None and str(from_line).strip() != "":
            try:
                start = max(0, int(from_line))
            except (TypeError, ValueError):
                start = 0
            if want is None:
                want = A_CHUNK          # 只给了起点 ⇒ 给一块（否则那一格等于没填）
        out = [head]
        for one_name in shown:
            text = pull[one_name]
            body = str(text).splitlines()
            total = len(body)
            excerpt_end = None
            # **名字命中的那一条 ⇒ 给目录**（★ 2026-10-17）——
            # 见 `OUTLINE_AT_MOST` 那一段：问"这一本里有什么"时，一节正文答不了那个问题
            # （名字命中时**没有哪一节"命中更多"**，于是原来退回"开头那几行"）。
            named = bool(units & set(tokens_of(str(one_name).replace("/", " "))))
            if want is not None:
                few = body[start:start + want]
                piece, kept = "\n".join(few), len(few)
                how_much = f"第 {start + 1}–{start + kept} 行"
            elif named:
                piece, _shown, all_of = _the_outline(text)
                # ⚠️⚠️ **没有小节体例的那一份 ⇒ 目录是空的**（`_the_outline` 交回空串）
                #    ⇒ 必须**退回摘录**。少了这一步，"名字命中的普通文件"（`图书馆.md`
                #    那种）会交回**一行都没有的正文**，而回执还写着"这一节 0 行"
                #    —— 量真实查询时抓到的（2026-10-17，`图书馆` 那一条）。
                if not all_of:
                    piece, kept, excerpt_end = _excerpt_details(text, units)
                    how_much = f"这一节 {kept} 行"
                else:
                    # ★★ **目录 ＋ 头几条完整词条**（2026-10-17）—— 见 `HEAD_ENTRIES`：
                    #    那一问要的是"能念的东西"，光给名字它收不了尾。
                    few, ends_at = _the_head_entries(text)
                    # ⚠️⚠️ **必须说清"上面那些只是名字"**（2026-10-17 验收抓到的）：
                    #    原来只写"（底下先给头几个词条的正文）"—— 它说了"给了 3 条"，
                    #    **但没说"另外那些只有名字、没有正文"** ⇒ 它当成每条都能念 ⇒
                    #    **自己补释义和拼音** ⇒ 当场两处滑：
                    #      · 把 `名副其实` 念成 **`名不副实`**（两个都是真成语、意思正相反）；
                    #      · 把 `实事求是` 的拼音写成 `shí shì qiú suǒ`（库里是 `qiú shì`）。
                    #    ⇒ 这一句就是那道界：**没有正文的，告诉它"要我去取"**。
                    piece = (str(piece)
                             + f"\n（上面这些**只是名字** —— 底下先给 {HEAD_ENTRIES} 条正文；"
                               "要念别的哪条，说一声我再去取）\n\n" + few)
                    kept = ends_at            # ← **行**偏移（续看要用它，不是条数）
                    how_much = f"里面 {all_of} 个词条"
            else:
                piece, kept, excerpt_end = _excerpt_details(text, units)
                how_much = f"这一节 {kept} 行"
            # ⚠️ **语义兜底那几条没有字面分** —— 照实说"语义最近邻"，
            #    而不是编一个 0.000 出来（那会读成"字面一字没中、却给了个分"）。
            score = by_literal.get(one_name)
            how = f"（匹配分数：{round(score, 3)}）" if score is not None else "（匹配方式：语义近邻）"
            # ★★ **下一步开在同一扇门里**（2026-10-17）—— 量到的事实：
            #    把 `kb_read` 的名字与参数写进回执**也叫不动它**（5 种问法 ／ 7 次观察，
            #    `kb_read` 0 次；它宁可把同一个 `kb_search` 重问 4—6 遍）。
            #    ⇒ 这一句递的是**本扇门的参数**；`kb_read` 那条路留着当第二条。
            #    ⚠️ 续看那一句**必须带上 `from_line`**（只给 `lines` ⇒ 又从第 0 行起 ——
            #       那正是"同一批连问 12 次"的成因）。
            more = ""
            next_offset = start + kept if excerpt_end is None else excerpt_end
            if excerpt_end is not None and excerpt_end != kept:
                how_much += "（摘录片段，非完整上下文）"
            if kept and next_offset < total:
                more = (f"（接着看就再问一次，lines={min(total, LINES_AT_MOST)}，"
                        f"from_line={next_offset}；"
                        f"或 kb_read name=\"{one_name}\" offset={next_offset}）")
            # ★★ **这是"你收着的"，不是"库里有个文件"**（2026-10-17，量出来才加的）。
            # ⚠️⚠️ **为什么非加不可**（会话原文，它手里握着答案却答"没有"）：
            #    问「**你**那本成语词典收了哪些词」⇒ 回执交的是
            #    `知识库里对得上「成语词典」的有 **1** 条：· 词典/成语-常用.md …`
            #    ⇒ **从头到尾没有一句说"这就是你收着的那本"** —— 抬头说的是"**知识库里**"
            #      （一个存放处），数说的是"**1 条**"（一个匹配），而名字是
            #      `词典/成语-**常用**.md`（它问的是"成语**词典**"）。
            #    ⇒ 它的结论是「**没有那本词典**」／「没有一本固定收好的《成语词典》」——
            #      而同一趟里它**收到了**那 505 字全文（里面 500 条 ＋ 12 个真词条名 ＋ 3 条正文）。
            #    ⚠️ **换问法试过、没用**（对照批 `kb-name-framing`：改用"你知识库里那一份"问，
            #      2／2 照样打转、照样不答）⇒ 要动的是**回执这一头**。
            #    ⚠️ 它**只加一个前缀、不改任何数**（名字 ／ 行数 ／ 条数 ／ 那个分，一个都没动）。
            owned = "**你收着的那一本**：" if named else ""
            title = pull.title(one_name)
            label = f"{one_name} {title}" if title is not None else one_name
            out.append(f"· {owned}{label}{how}｜ 整份 {total} 行 ／ {how_much}{more}：")
            out.append(piece if piece.strip() else "（这一条是空的）")
        if want is not None and want >= LINES_AT_MOST:
            out.append(f"（一次最多给 {LINES_AT_MOST} 行 —— 再多就分几次看。）")
        return "\n".join(out)

    def kb_read(name: str = "", offset: int = 0, limit: int = 40,
                version: int | None = None, **rest: Any) -> str:
        """ **看一条知识的一截**（2026-10-10 那一刀「乙」的第二半）—— 与 `kb_search` 配套。

        ⚠️ **分工**：`kb_search` 只给**命中那一条**（目录 ）；要看全 ／ 看后面 ⇒ 走这一条 。
            外面那两套同一条分工：`grep` 定位 ＋ `read` **带行范围** ／ `cat` 带
           `offset` ／ `limit` 。
        ⚠️ **不许越出知识那一棵树** ：名字里塞 `../` ／ 绝对路径 ⇒ 照实说"没有这一条"
           （ 这一格是"读它自己的知识"，**不是任意文件** —— 这一条就是那道边 ）。
        ⚠️ **交回的东西必须能往下接** ：这是**第几行到第几行** ／ **整份多少行** ／
            **没完就说清怎么继续** （不然它只能从头再来一遍 ）。
        """
        base = (Path(home) / KNOWLEDGE_TREE).resolve()
        visibility = _retrieval_scope()
        from .retrieval_scope import file_grants
        granted = file_grants(Path(home), visibility) if visibility is not None else None
        restricted = visibility is not None and not visibility.administrator
        unavailable = "当前可见知识中没有可读取的这一条。"
        want = str(name or "").strip()
        if not want:
            if restricted:
                raise PermissionError(unavailable)
            return "要看哪一条？给我一个名字（`kb_search` 交回来的那一串就行）"
        if learned_knowledge.ID.fullmatch(want):
            row = learned_knowledge.read(home, visibility, want, version)
            lines = learned_knowledge.render(row).splitlines()
            start = max(0, int(offset))
            stop = min(start + min(LINES_AT_MOST, max(1, int(limit))), len(lines))
            status = "当前版本" if version is None else "指定版本"
            tail = f"\n（继续读同一 ID，version={row['version']}，offset={stop}）" if stop < len(lines) else ""
            return (f"[{want}] {status} {row['version']}｜正文共 {len(lines)} 行\n"
                    + "\n".join(lines[start:stop]) + tail)
        if version is not None:
            raise ValueError("按版本读取须填写习得知识 ID。")
        said = want[:-3] if want.endswith(".md") else want
        try:
            where = (base / (said + ".md")).resolve()
        except (OSError, ValueError):
            if restricted:
                raise PermissionError(unavailable) from None
            raise
        # **那道边**：解出来的路径必须**还在**知识那一棵里面 （`../` ／ 绝对路径挡住 ）
        if where != base and base not in where.parents:
            if restricted:
                raise PermissionError(unavailable)
            return f"没有这一条（{want}）—— 知识那一棵里没有它。"
        # A restricted caller sees an authorized file view, not disk existence.
        # Do not take the legacy successful 'missing' branch (or its filename
        # suggestions). Absence, denied grants and IO failures are one genuine
        # failure category with identical output; no second read of file bytes.
        if not restricted and not where.is_file():
            from .memory_inventory import ordered
            with ordered(base,'.md') as names:
                near = []
                for one in names:
                    if said and said in one.name and (granted is None or granted(one)):
                        if len(near)<5:near.append(one.relative_to(base).as_posix())
            tip = ("；像是这几条：" + "、".join(near)) if near else ""
            return f"没有这一条（{want}）{tip}"
        try:
            raw = where.read_bytes()
        except OSError:
            if restricted:
                raise PermissionError(unavailable) from None
            if visibility is None:
                raise
            raise PermissionError("知识内容暂不可读。") from None
        if granted is not None and not granted(where, raw):
            if restricted:
                raise PermissionError(unavailable)
            raise PermissionError("此知识未获得当前会话的读取授权。")
        try:
            rows = _for_her(raw.decode("utf-8")).splitlines()
        except UnicodeError:
            if restricted:
                raise PermissionError(unavailable) from None
            raise
        total = len(rows)
        start = max(0, int(offset))
        few = rows[start:start + max(1, int(limit))]
        shown = where.relative_to(base).as_posix()
        if not few:
            return (f"{shown} 一共 {total} 行 —— 你要的那一截在它后面"
                    "（offset 从 0 算）。")
        stop = start + len(few)
        head = f"{shown} 第 {start + 1}–{stop} 行（整份 {total} 行）："
        tail = ""
        if stop < total:
            tail = f"\n（还有 {total - stop} 行 —— 接着看就给 offset={stop}）"
        return head + "\n" + "\n".join(few) + tail

    def skill_read(name: str = "", offset: int = 0, limit: int = 40, **rest: Any) -> str:
        """ **看一条做法的一截**（2026-10-12）；**2026-10-17 批③：按稳定 ID 认**。

        ★ 交回来的头一行带 **ID ／ 版本 ／ 状态** —— 它手里要有这三样，
          才谈得上"按预期版本修订"或"点名合并"。
        ★ **标题不再当唯一身份**：标题对上不止一条 ⇒ **列出候选、要它挑**，
          绝不静默取第一条（`the_skills.resolve` 那条路）。
        ★ **已并入的条目仍读得到**（历史读口），但那一行会写清"已并入 → 哪一条"。
        ⚠️ 不许越出「做法」那一棵（解析只在登记过的那几份里，`../` 没有拼进去的机会）。
        """
        base = (Path(home) / SKILL_TREE).resolve()
        visibility = _retrieval_scope()
        restricted = visibility is not None and not visibility.administrator
        from .retrieval_scope import file_grants
        granted = file_grants(Path(home), visibility) if visibility is not None else None
        def allowed(path, entry, raw):
            return granted(path, raw) or (
                path.resolve() == path.absolute()
                and visibility.permits(entry.get("scope"))
                and entry.get("version") == the_skills.version_of(raw.decode("utf-8"))
            )

        visible_rows = []
        if restricted:
            for key, entry in the_skills.load(home).get('entries', {}).items():
                one = dict(entry, id=key)
                path = base / str(one.get('file') or '')
                if base not in path.resolve().parents:
                    continue
                try:
                    raw = path.read_bytes()
                    if allowed(path, one, raw):
                        one.setdefault('state', 'active')
                        visible_rows.append(one)
                except (OSError, UnicodeError):
                    continue
            visible_ids = {row['id'] for row in visible_rows}
            for row in visible_rows:
                if row.get('merged_into') not in visible_ids:
                    row['merged_into'] = None
        want = str(name or "").strip()
        if not want:
            rows = visible_rows if restricted else the_skills.rows(home, include_merged=True)
            if not rows:
                return ("还没记过哪一条做法 —— 学会了就用 `skill_note` 记一条，"
                        "名字写「什么时候用它」。")
            few = [one["id"] + " 「" + one["title"] + "」 " + the_skills.SKILL_KINDS[the_skills.kind_of(one)]
                   + ("" if one["state"] == "active" else "（已并入" + (" " + one["merged_into"] if one.get("merged_into") else "") + "）")
                   for one in rows]
            return ("你记过 " + str(len(rows)) + " 条做法（ID 「标题」）：" + "；".join(few)
                    + "。要看哪一条，把 ID（或标题）给我。")
        try:
            if restricted:
                matches = [row for row in visible_rows if want in (row['id'], row.get('title'), row.get('file'))]
                if len(matches) != 1:
                    raise PermissionError('当前可见做法中没有唯一可读取的这一条。')
                one = matches[0]
            else:
                one = the_skills.resolve(home, want)
        except the_skills.SkillAmbiguous as why:
            return str(why)
        except the_skills.SkillRejected as why:
            return str(why)
        path = base / one["file"]
        if not path.is_file():
            return "这一条的正文不在了：" + str(one["file"])
        raw = path.read_bytes()
        if restricted and not allowed(path, one, raw):
            raise PermissionError('做法正文或授权已变化。')
        rows = _for_her(raw.decode('utf-8')).splitlines()
        total = len(rows)
        start = max(0, int(offset))
        few = rows[start:start + max(1, int(limit))]
        state = ("有效" if one["state"] == "active"
                 else "已并入" + (" " + one["merged_into"] if one.get("merged_into") else ""))
        # ⚠️ 版本显示**前 12 位**（够了，`_the_same` 认 ≥8 位前缀）：真跑量到过 ——
        #    给 64 位全串时，模型会**把正文当版本**填进 `expect`（然后被正确地拒掉）。
        head = ("[" + one["id"] + "] " + one["title"] + " ｜ 版本 "
                + str(one["version"])[:12] + "（这 12 位就够）｜ " + state
                + " ｜ " + the_skills.SKILL_KINDS[the_skills.kind_of(one)])
        # Display only references in the returned window. The ordinary
        # skill_read entry point rechecks the grant when the link is followed.
        references = the_skills.reference_ids("\n".join(few))
        links = [ref for ref in references if ref.startswith("sk-")]
        if links:
            candidates = visible_rows if restricted else the_skills.rows(home, include_merged=True)
            available = {
                row["id"] for row in candidates
                if (base / row["file"]).is_file()
            }
            shown = [ref if ref in available else f"{ref}（当前不可读取）" for ref in links]
            head += "\n关联做法（可用 skill_read 按 ID 继续查）：" + "、".join(shown)
        knowledge_links = [ref for ref in references if ref.startswith("kb-")]
        if knowledge_links:
            head += "\n关联知识（由 kb_read 按 ID 校验权限并读取）：" + "、".join(knowledge_links)
        events = [ref.removeprefix("event:") for ref in references if ref.startswith("event:")]
        if events:
            head += "\n关联事件（由 read_events 按 cursor 查原文）：" + "、".join(events)
        if not few:
            return (head + "\n一共 " + str(total) + " 行 —— 你要的那一截在它后面"
                    "（offset 从 0 算）。")
        stop = start + len(few)
        tail = ""
        if stop < total:
            tail = "\n（还有 " + str(total - stop) + " 行 —— 接着看就给 offset=" + str(stop) + "）"
        return (head + "\n第 " + str(start + 1) + "–" + str(stop)
                + " 行（整份 " + str(total) + " 行）：\n" + "\n".join(few) + tail)

    def _self_source() -> str:
        if not isinstance(self_source, str) or not self_source.strip():
            raise ValueError("self_source_required_before_write")
        return self_source

    def self_observe_add(text: str = "", **rest: Any) -> str:
        """ 记一条**自我观察** —— 落「悬着的事」（**缓冲区**，不是等地批的提议）。

        ⚠️ 2026-10-17：回执从"记下了（悬着的事）"改成带一句"之后怎么处置由你" ——
           作者当天把那一格的意思讲清了：**事情先进缓冲区，它才有可能判断
           自己消化、还是找人说说**。而 `abouther_propose` 那一句原来还写着
           "改不改由它／你定"，把它写的东西说成了**等别人批的申请** ⇒ 一并改掉。
        ★ **同一件事记两遍怎么办**（作者当天拍的「甲」）：**把缓冲区亮给它看，由它决定**，
          而不是系统替它判"这两条是一回事"。⇒ 回执末尾带上**它现在悬着的那几条**：
          它看到已经有一条一样的，下一句就不会再记一遍（同一条来回改写也算）。
        ⚠️ 这一层**不点名**任何别的工具（内核不认识部署装了哪几支笔）：
           那几支笔叫什么、怎么接，由部署那一层在**说明**里写（`launch` 那一处）。
        """
        source_label = _self_source()
        made = remember_pending(home, str(text), proposed="self_observe",
                                ts=_the_moment(), source=source_label)
        return (f"记下了（悬着的事）：{text}（先进缓冲区，之后怎么处置由你）"
                + _private_peek('pending'))

    def abouther_propose(text: str = "", **rest: Any) -> str:
        """ 提一条《关于它自己》的补充 —— 同样落「悬着的事」，**之后由它自己落定**。

        ★ 回执末尾照 `self_observe_add` 那一处带上"它现在悬着的那几条"（同一件事别记两遍）。
        """
        source_label = _self_source()
        made = remember_pending(home, str(text), proposed="abouther",
                                ts=_the_moment(), source=source_label)
        return (f"记下了（悬着的事）：{text}（之后由你自己落定）"
                + _private_peek('pending'))

    # Unknown keywords must fail before identity lookup or either write.  Do not
    # silently ignore misspelled author/relationship fields or invent aliases.
    def profile_add(text: str = "", who: str = "", standing: str = "",
                    favor: int | None = None, weight: int | None = None) -> str:
        """ **记一条关于某个人的观察**（2026-10-07「第 2 刀」）—— **去处由它自己挑** 。

        ⚠️ 这一件与 `self_observe_add` 是**两条路**（提示里就写着该挑哪一条 ）：
           **确信**的 ⇒ 记这儿（进**画像**：平时不进 system，要读才读 ）；
           对方事实拿不准 ⇒ 不写；`self_observe_add` 仅供自身反思，不作为画像审核的旁路。
            本模块**不替它判断**："拿不准"不是一个条件，那是**它的选择** 。

        ⚠️ 名字空着 ⇒ 照实说"得先有那个人的名字"（ 与那两件读工具同一条 ）。

        2026-10-10（作者拍的「乙1」）：**这一支笔也能写"我什么态度 ＋ 给他多少分"** 。
          为什么不是另立一件工具：作者原话是"别非要写一本本子去强调"，
          而新加一件工具就是又加一本，所以挂到已有的这一扇门上。
          为什么非有它不可（量到的）：remember_relation 全仓只有测试在调，
          它连写都写不了（从前只写画像，于是关系那一本永远是空的）。
          ⚠️ text 那一格同时还当"为什么"（why）—— 那正是作者要的"有依据"。
          ⚠️ 只给事实不算给过关系（不许顺手写一条空关系）；两边都没给照旧拒收。

         **2026-10-08 修两处**（ 这一刀量出来的 ）：
           · **时间**：原来没叫 `clock` ⇒ 落下去那一条的 `ts` 是**空串** ——
             而 `remember_person` 的说明写着「**带来源与时间**两样缺一不可」
             （ 调用方不给 ⇒ 就空着、**不编**）⇒ 这里补上**叫一次 `clock`**
             （ 本模块不读钟 ⇒ 由调用方递 ／ 没递就照旧空着 ）。
           · **`role`**：原来写死"它说的" —— 而画像里那两类的名字是
             `NAME_SAID`（"名字"）／ `FACT_SAID`（"事实"） ⇒ 用 `FACT_SAID`
             （ "名字／别名"那一类今天没有口，留着 ）。
        """
        from .portrait_eligibility.gate import consume
        ticket = consume(profile_add, dict(text=text, who=who, standing=standing, favor=favor, weight=weight))
        memory_kind = (ticket or {}).get("memory_kind", "")
        source_cursor = (ticket or {}).get("source_cursor")
        portrait_sources = (ticket or {}).get("portrait_sources", ())
        if favor is not None and affect_context is not None:
            from .affect import legacy_favor_allowed
            if not legacy_favor_allowed(home):
                return ("自动关系评估已启用，profile_add 不接受绝对 favor；本次未写入任何内容。"
                        "有真实关系影响请用 emotion_update，填写本轮原话 evidence 和 relationship_impact；"
                        "普通情绪变化不必改分。")
        name, source, error = _profile_target(who)
        if error:
            return error
        if not name:
            return "没说是谁 —— 得先有那个人的名字。"
        # ⚠️⚠️ **没有来源的一轮不许写画像**（2026-10-17 量出来的）。
        #  `_profile_target` 只在"自己醒的那一轮（内部唤醒／独处）"交空来源 ——
        #  那一轮没有当前会话，于是这一条**指不出它是哪来的**。设计稿 §四 那条
        #  "每条带时间与来源，两样缺一不可"在这一格上是硬的；而更要紧的是：
        #  群读口是按 `source` 筛的（`profile_read` 只认本群来源）⇒ 无来源那一条
        #  **写进去也读不回来**，等于给自己造了一条死记录。
        #  ⚠️ 那天它正是在这种一轮里写了两次：`who` 填账号键 ⇒ 路径带冒号 ⇒
        #     Windows 当备用数据流写掉（假装成功）、关系那一半当场炸。
        #  ⇒ 这里**明说 + 给替代**（它那本"留一句给以后的自己"在独处那一轮是能写的）。
        #  ⚠️ **只在"递了上下文而它说没有会话"时拒**：`profile_context is None`
        #     是**老用法／直接调用**（没有上下文可问）⇒ 不替它判，照写。
        if not source and profile_context is not None:
            return ("这一轮没有当前会话（自己醒的），画像写不了 —— 记一条画像得说得出"
                    "它来自哪一段对话；等有人来话的那一轮再记。"
                    "想留一句给以后的自己，用 self_note。")
        # ⚠️ **名字先验完再动盘，而且回执要是一句话**（不是 traceback）：
        #    那两把笔自己也会验（`_a_portrait_name`），但在这里先拦一下，
        #    它读到的就是"这个名字不能当文件名"这一句，而不是 `[tool failed] ValueError`。
        try:
            _a_portrait_name(name)
        except ValueError as why:
            return str(why)
        favor = validate_favor(favor)
        said = str(text).strip()
        mood = str(standing).strip()
        gave = bool(mood) or favor is not None
        if not said and not gave:
            return "那一条是空的（空的不记）。"
        # The same direct source is idempotent even after other entries have been added.
        if said and not gave:
            previous = profile_of(home, name)
            if source_cursor is not None and any(
                row.source_cursor == source_cursor and row.source == source and row.text == said
                and (not row.portrait_sources or row.portrait_sources == portrait_sources)
                for row in previous
            ):
                return "这条原话已记入画像，本次未重复写入。"
            from .portrait_eligibility.provenance import selected
            if (source_cursor is None and previous and previous[-1].text == said
                    and previous[-1].source == source and previous[-1].weight == weight
                    and previous[-1].memory_kind == memory_kind
                    and selected(previous[-1].portrait_sources) == selected(portrait_sources)):
                return "画像已有相同的最新记录，本次未重复写入；原记录与时间保持不变。"
        told: list[str] = []
        if said:
            remember_person(home, name, said, said=FACT_SAID, ts=_the_moment(),
                            source=source,
                            weight=None if weight is None else int(weight), memory_kind=memory_kind,
                            source_cursor=source_cursor, portrait_sources=portrait_sources)
            told.append("画像记录 · " + portrait_kind_label(memory_kind))
            if weight is not None:
                # 2026-10-12（作者：「\u5979自己标」）：轻重那一格**它自己给** ——
                # 照实报回去（"你说多重"是它自己拍的，不是这一层猜的）。
                told.append("轻重 " + str(int(weight)))
        if gave:
            # 2026-10-10（作者拍的「乙1」）：这一支笔也能写"我什么态度 ＋ 给他多少分" ——
            # 挂到已有的那扇写门上，不另立一件工具（作者："别非要写一本本子去强调"）。
            # 这一格从前谁都写不了（remember_relation 全仓只有测试在调），
            # 于是关系那一本永远是空的。
            # ⚠ text 那一格同时还当"为什么"（why）—— 那正是作者要的"有依据"：
            # 它手上真有的东西才算依据（与茶那一条同一条道理）。
            remember_relation(home, name, mood, why=said, favor=favor,
                              ts=_the_moment(), source=source)
            told.append("关系" + ("" if favor is None else "，分 " + str(int(favor))))
        back = "；".join(one for one in (said, mood) if one)
        return f"记下了（{' ＋ '.join(told)} · {name}）：{back}"
    def pending_read(**rest: Any) -> str:
        """ **读自己那些悬着的事**（2026-10-07，作者拍的「①b」）—— 交回**回执**（一句事实）。

        ⚠️ **只读**：不写、不改状态、不排序、**不读时钟** （定下的三条：不许过期 ／ 不许催）。
        ⚠️ 一件都没有 ⇒ **照实说**（"没有悬着的事"），**不编** 。
        """
        visibility = _retrieval_scope()
        if visibility is not None and not visibility.administrator:
            raise PermissionError('悬着的事尚无逐条公开授权，只允许所有者读取。')
        rows = pending_of(home)
        if not rows:
            return "没有悬着的事。"
        out = ["悬着 %d 件：" % len(rows)]
        for one in rows:
            said = str(one.text or "").strip().replace("\n", " ")
            out.append("· " + said[:120] + (f"（{one.ts}）" if one.ts else ""))
        return "\n".join(out)

    def skill_note(name: str = "", text: str = "", overwrite: bool = False,
                   kind: str | None = None) -> str:
        """ **记一条做法**（2026-10-10）；**2026-10-17 批③：登记稳定 ID**。

        ⚠️ **同名默认拒写**（要改就明说 `overwrite`）。
        ★ **`overwrite` 不再是"直接盖掉"**：它走治理层（先留一版、再原子替换、版本前进）；
          而**已并入**的条目**不许就地复活** —— 那会绕开合并留下的归档与版本。
        ★ 回执里带 **ID**：以后按 ID 认，改名不会丢身份。
        """
        if not str(name).strip():
            return "得给这条做法起个名字 —— 名字写「什么时候用它」。"
        if not str(text).strip():
            return "这条做法还空着 —— 写清「怎么做」。"
        if kind is not None:
            try:
                the_skills.validate_kind(kind)
            except the_skills.SkillRejected as why:
                return str(why)
        visibility = _retrieval_scope() if source_events is not None else None
        restricted = visibility is not None and not visibility.administrator
        try:
            known = the_skills.resolve(home, name)
        except (the_skills.SkillRejected, the_skills.SkillAmbiguous):
            known = None
        if known is not None:
            if restricted and known.get("scope") != visibility.current:
                raise PermissionError("当前会话无法写入该名称或 ID。")
            if known["state"] != "active":
                return ("「" + known["title"] + "」已经并进 " + str(known["merged_into"])
                        + " 了 —— 不能就地复活；要改就改那一条，或者另起一个名字。")
            if not overwrite:
                return ("已经有一条叫「" + known["title"] + "」的了（ID " + known["id"]
                        + "）—— 想改就明说 overwrite；想并进别的条目就用 `skill_merge`。")
            try:
                # ⚠️ 2026-10-17 补验：**不许 expect=None** —— 那等于"不看版本就盖"。
                #    用刚从 `resolve` 拿到的那一版；期间被别人改过 ⇒ `SkillConflict`。
                got = the_skills.write_body(home, known["id"], str(text),
                                            expect=known["version"],
                                            stamp=_the_moment(), kind=kind)
            except Exception as why:                 # noqa: BLE001 —— 写不成照实说
                return "没改成：" + type(why).__name__ + " " + str(why)[:120]
            return ("改好了：[" + known["id"] + " 「" + known["title"] + "」] 版本 "
                    + got["version"][:8] + "；换下来的那一版留着。")
        try:
            one = remember_skill(home, name, text, overwrite=False,
                                 kind=kind or "unclassified",
                                 recorded_at=recorded_clock() if recorded_clock else _the_moment(),
                                 scope=visibility.current if restricted else "")
        except FileExistsError:
            return ("已经有一条叫「" + str(name).strip() + "」的了 —— 想改就明说 overwrite。")
        except ValueError as why:
            # ⚠️ 名字不合法 ⇒ **一句人话**（不是 traceback）：见 2026-10-17 那条教训。
            return str(why)
        except Exception as why:                     # noqa: BLE001 —— 写不成照实说
            return "这条没记成：" + type(why).__name__ + " " + str(why)[:120]
        entry = the_skills.register(home, one.name)
        return ("记下了：[" + entry["id"] + " 「" + one.stem + "」] —— 以后遇到它就知道该怎么做。"
                "（有效做法一共 " + str(len(skills_of(home))) + " 条）"
                + _private_peek('skills'))

    def _private_peek(kind):
        try:
            visibility = _retrieval_scope()
        except PermissionError:
            # An optional preview must not mask a completed write.
            return ''
        if visibility is not None and not visibility.administrator:
            return ''
        return _the_buffer_peek(home) if kind == 'pending' else _the_skills_peek(home)

    from .portrait_eligibility.gate import bind_writer
    bind_writer(profile_add, home, _profile_target)
    memory_search.__safe_diagnostics__ = _search_diagnostics
    # Semantic inference is CPU work. Async dispatch retains it through cancellation
    # without blocking the chat/timer event loop; synchronous callers stay supported.
    if callable(semantic) or callable(semantic_memory):
        from .owned_blocking import owned_thread
        def bind_owned(handler):
            async def owned(*args, **kwargs):
                return await owned_thread(handler, *args, **kwargs)
            handler.__owned_async_handler__ = owned
        bind_owned(kb_search)
        bind_owned(memory_search)

    result = {"memory_search": memory_search, "profile_read": profile_read,
            "relations_read": relations_read, "kb_search": kb_search,
            "kb_read": kb_read, "skill_read": skill_read,
            "self_observe_add": self_observe_add, "abouther_propose": abouther_propose,
            "pending_read": pending_read, "profile_add": profile_add,
            "skill_note": skill_note}
    if source_events is not None:
        result["knowledge_note"] = knowledge_note
    if affect_context is not None:
        if affect_clock is None:
            raise ValueError("affect_clock_required")
        from .affect import handlers as affect_handlers
        result.update(affect_handlers(home, resolve=_profile_target,
                                     context=affect_context, clock=affect_clock,
                                     assessment_sink=affect_assessment_sink))
    return result


#: **2026-10-10（作者问过、我查了之后拍的）：这一棵**
#: **不给它写** —— 而理由不是“保守”，是文献里
#: **有一类专门的攻击** 。
#:
#: ⚠ **文献点名的那一类** （ 三条同一个形状 ）：
#: · **Self-Poisoning**（ 自我投毒 ）—— 一条写进去的错东西
#: 会**持久地**改变它以后的行为 ：
#:       https://zenodo.org/records/22685081
#: · 三类主要风险里的第一条： **长期记忆或外部
#: 知识库被投毒** ， 而它**会在后面改变行为** ：
#:       https://link.springer.com/content/pdf/10.1007/s43681-026-01132-0.pdf
#: · **Zombie Agents**（ 自我强化的注入 ）——
#: 一次污染**自己长大** ：
#:       https://ar5iv.labs.arxiv.org/html/2602.15654
#:
#: ⚠ **而对我们具体的处境** ：这一棵是
#: **人定的设定** （ 盘上那 1178 份里真正的设定
#: **只有 3 份** ）—— 若给它写 ⇒ 它写的字
#: **会混进人定的设定里** 。
#: 而“混着放”的后果 ， 今天已经**量到过一次** ：
#: 那个“满了就删”的机制差点删掉人手写的两件
#: —— 同一个形状 。
#:
#: ⚠ **而更关键的** ： 它已经**有地方写** ——
#: `做法/` 那一棵就是为这件事立的 （ 而它**有一支笔** ）。
#: ⇒ **学到的进「做法」 ／ 人定的进「知识」 ** ——
#: 两棵分开、各归各的 。
#:
#: ⚠ **所以结论是“不接”** ： 接那支笔**没有收益**
#: （ 它已经能写它该写的地方 ）、 而代价是
#: 把它的字混进人定的设定 。
#: ⚠ **要回头改主意** ⇒ 先解决“两样分开”
#: （ 与「把参考书搬到另一棵树」那条路
#: **同一个形状** ）。
@_source_writer
def remember_knowledge(home: Path, name: str, text: str, *, overwrite: bool = False) -> Path:
    """ **记一条知识**（设计稿 §二 ②："知识（**文件式**）"）—— 一个 `.md`，它自己的字。

    ⚠️ **逐字节写**（不包一层"字段" —— 与笔记那条同一规矩）：什么时候 ／ 哪来的，
      由**这一棵树**与**文件自己的 mtime** 担着，这一层**不去动它一个字节** 。
    ⚠️ 已经有一份同名 ⇒ **默认拒写**（`FileExistsError`）—— 那**不是**静默覆盖：
      知识是**它自己**的字，覆盖要它明说（`overwrite=True`）。

     **2026-10-10：名字可以带子目录**（`词典/成语` ）—— 作者要分文件夹
       （ 根子见 `knowledge_of` 那一段：从前 `glob` 只认顶层 ⇒ 一千多份**只能拍平** ）。
    ⚠️ **不许拿这个名字翻出去** ：只留"正常的名字段"（空 ／ `.` ／ `..` ／ 带 `:` 的
       一律丢掉 ）⇒ 结果**结构上**只能落在那一棵树里面 （老规矩一条没松 ）。
    """
    base = Path(home) / KNOWLEDGE_TREE
    parts = [one for one in str(name).strip().replace("\\", "/").split("/")
             if one not in ("", ".", "..") and ":" not in one]
    if not parts:
        parts = ["一条知识"]
    leaf = parts[-1] if parts[-1].endswith(".md") else parts[-1] + ".md"
    target = base.joinpath(*parts[:-1], leaf)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        raise FileExistsError(f"已经有一条同名的了：{target.name}（要改就明说 overwrite）")
    _publish_memory_text(target, str(text))
    # **2026-10-10：这一半收掉了** —— 它是一把**会砍人的刀** 。
    # ⚠️ **量到的事实** ： 作者往这一棵里放进 **1177 份** ， 而上界是 200
    # ⇒ 下一次它写一条知识 ⇒ 会删掉 **978 份** —— 而 那批文件当天
    # 被批量重写过（ mtime 挤在同一分钟 ）⇒ 按 mtime 排**不稳**
    # ⇒ **第一批被删的正是他手写的那两件** （ 那两件是**人的东西**，不是它写的 ）。
    # ⚠️ **根子上错在哪** ： 那个"满了压最旧的"是**给它自己那本笔记设的**
    # （ `self_note` —— 那是**它自己写的东西** ）， 而这一棵树里
    # **人和它混着放** ⇒ 一个"满了就删"的机制**管到了人放进去的东西** 。
    # ⚠️ **所以这一头现在只做一件事：什么都不删** （ 存量照旧 ／ 新写的照旧 ）。
    # 要"说一句已经超了" ⇒ 那是**页上**的事 （ 且**不许**拿它去删 ）。
    return target


def index_pairs_strict(home: Path) -> list[tuple[str, str]]:
    """Read a complete, verified index input without registering/repairing skills.

    Missing roots and inconsistent metadata fail closed. Two inventories and byte
    re-reads detect observed changes; this is NOT a cross-process filesystem
    transaction, nor a lease extending through the caller's index commit.
    """
    import os
    import stat

    home = Path(home)

    def stamp(path):
        value = path.lstat()  # Never suppress permission/missing-path errors.
        if (stat.S_ISLNK(value.st_mode)
                or getattr(value, "st_file_attributes", 0) & 0x400):
            raise ValueError("index_source_link_not_allowed")
        return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
                value.st_mtime_ns, value.st_ctime_ns)

    def inventory():
        root_stamp = stamp(home)
        if not stat.S_ISDIR(root_stamp[2]):
            raise ValueError("index_home_directory_required")
        found = {home: root_stamp}

        def visit(path, recursive):
            before = stamp(path)
            if not stat.S_ISDIR(before[2]):
                raise ValueError("index_source_directory_required")
            found[path] = before
            with os.scandir(path) as entries:
                names = sorted(entry.name for entry in entries)
            for name in names:
                child = path / name
                info = stamp(child)
                found[child] = info
                if stat.S_ISDIR(info[2]):
                    archived = (home / SKILL_TREE in child.parents
                                and name.startswith(the_skills.HISTORY_PREFIX))
                    if recursive and not archived:
                        visit(child, recursive)
                elif not stat.S_ISREG(info[2]):
                    raise ValueError("index_source_regular_file_required")
            if stamp(path) != before:
                raise ValueError("index_source_changed")

        visit(home / KNOWLEDGE_TREE, True)
        visit(home / SKILL_TREE, True)
        return found

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("index_metadata_duplicate_key")
            result[key] = value
        return result

    # Cooperates with existing in-process skills writers; creates no disk lock.
    with the_skills._locked(home):
        before = inventory()
        contents = {}

        def read(path):
            if path not in before or not stat.S_ISREG(before[path][2]):
                raise ValueError("index_source_file_missing")
            if stamp(path) != before[path]:
                raise ValueError("index_source_changed")
            raw = path.read_bytes()
            if stamp(path) != before[path]:
                raise ValueError("index_source_changed")
            contents[path] = raw
            return raw.decode("utf-8")

        def metadata(path):
            return json.loads(read(path), object_pairs_hook=unique_object)

        def visible(text):
            # Match Path.read_text newline translation used by the existing readers.
            return _for_her(text.replace("\r\n", "\n").replace("\r", "\n"))

        knowledge = home / KNOWLEDGE_TREE
        skills = home / SKILL_TREE
        pairs = [(path.relative_to(knowledge).as_posix(), visible(read(path)))
                 for path in sorted(before)
                 if knowledge in path.parents and stat.S_ISREG(before[path][2])
                 and path.suffix.lower() == ".md"]
        from . import learned_knowledge
        learned = knowledge / learned_knowledge.DIRECTORY
        for path in sorted(before):
            if (path.parent == learned and path.suffix == ".json"
                    and learned_knowledge.ID.fullmatch(path.stem)):
                record = metadata(path)
                pairs.append((path.stem, learned_knowledge.render(record["revisions"][-1])))
        files = {path.relative_to(skills).as_posix(): path for path in before
                 if skills in path.parents and stat.S_ISREG(before[path][2])
                 and the_skills.OPS_DIR not in path.relative_to(skills).parts[:-1]
                 and path.suffix.lower() == ".md"}
        index_path = skills / the_skills.INDEX_NAME
        if index_path not in before:
            if files:
                raise ValueError("index_skills_registration_required")
            index = {"format": the_skills.FORMAT, "next": 1, "entries": {}, "ops": {}}
        else:
            index = metadata(index_path)
        if (not isinstance(index, dict) or type(index.get("format")) is not int
                or index["format"] != the_skills.FORMAT
                or type(index.get("next")) is not int or index["next"] < 1
                or not isinstance(index.get("entries"), dict)
                or not isinstance(index.get("ops"), dict)):
            raise ValueError("index_skills_metadata_invalid")
        for op_id, operation in index["ops"].items():
            if (not isinstance(op_id, str) or not op_id or "/" in op_id or "\\" in op_id
                    or skills / the_skills.OPS_DIR / (op_id + ".json") not in before):
                raise ValueError("index_skills_operation_missing")
            if (not isinstance(operation, dict)
                    or operation.get("stage") not in ("done", "rolled-back")):
                raise ValueError("index_skills_operation_unsettled")
        for path in sorted(before):
            if path.parent == skills / the_skills.OPS_DIR and path.suffix == ".json":
                operation = metadata(path)
                if (not isinstance(operation, dict)
                        or operation.get("stage") not in ("done", "rolled-back")
                        or not isinstance(operation.get("op"), str)
                        or operation.get("op") not in index["ops"]
                        or operation != index["ops"][operation["op"]]):
                    raise ValueError("index_skills_operation_unsettled")
        seen = set()
        entries = index["entries"]
        for key, entry in sorted(entries.items()):
            if (not re.fullmatch(r"sk-\d{4,}", key) or not isinstance(entry, dict)
                    or not isinstance(entry.get("title"), str)
                    or entry.get("state") not in ("active", "merged")):
                raise ValueError("index_skills_entry_invalid")
            name = entry.get("file")
            if (not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name
                    or not name.endswith(".md") or name in seen):
                raise ValueError("index_skills_file_mismatch")
            seen.add(name)
            body = read(files[name]) if name in files else None
            if body is not None and entry.get("version") != the_skills.version_of(body):
                raise ValueError("index_skills_version_mismatch")
            if entry["state"] == "merged":
                target = entry.get("merged_into")
                if not isinstance(target, str) or target not in entries or target == key:
                    raise ValueError("index_skills_merge_target_invalid")
                chain = {key}
                while True:
                    if target in chain or not isinstance(entries.get(target), dict):
                        raise ValueError("index_skills_merge_cycle")
                    chain.add(target)
                    linked = entries[target]
                    if linked.get("state") == "active":
                        break
                    target = linked.get("merged_into")
                    if not isinstance(target, str) or target not in entries:
                        raise ValueError("index_skills_merge_target_invalid")
            else:
                if entry.get("merged_into") is not None:
                    raise ValueError("index_skills_active_merge_target")
                if body is not None:
                    pairs.append((name, visible(body)))
        if not set(files) <= seen:
            raise ValueError("index_skills_registration_required")
        if len({name for name, _ in pairs}) != len(pairs):
            raise ValueError("index_source_key_collision")
        if inventory() != before:
            raise ValueError("index_source_changed")
        for path, raw in contents.items():
            if stamp(path) != before[path] or path.read_bytes() != raw:
                raise ValueError("index_source_changed")
        if inventory() != before:
            raise ValueError("index_source_changed")
        return pairs


def knowledge_of(home: Path, *, name: str = "") -> list[tuple[str, str]]:
    """ **读知识**（一条一条读回来）：`[(名字, 正文), …]` —— 点了名就只读那一条。

     **2026-10-10：改成递归（认子目录）＋ 名字带相对路径** ——
       ⚠️ **原来两条规矩打架**：这里 `glob('*.md')`（**只认顶层** ）／
       `_documents` 用 `rglob`（**递归** ）⇒ **同一棵树两个答案**
       ⇒ 于是那 1175 份**只能拍平**（ 那正是作者说的"费眼睛"的根 ）。
        现在并成**一条规矩**：**递归** ，名字是**相对路径**（`词典/成语.md` ）——
       子目录里两份同名也分得开 （ `kb_search` ／ `kb_read` 都拿这个名字当钥匙 ）。
    """
    return list(_iter_knowledge(home,name=name))


def _iter_knowledge(home: Path, *, name: str = '', visibility=None):
    base = Path(home) / KNOWLEDGE_TREE
    said = str(name).strip().replace("\\", "/")
    from .memory_inventory import ordered
    from .retrieval_scope import file_grants
    granted = file_grants(Path(home),visibility) if visibility is not None else None
    with ordered(base,'.md',strict=visibility is None or visibility.administrator) as names:
        for one in names:
            rel = one.relative_to(base).as_posix()
            if said and rel != said and rel != said + ".md" and one.stem != said:
                continue
            if visibility is None:
                yield rel,_for_her(one.read_text(encoding="utf-8"))
                continue
            try:
                raw = one.read_bytes()
            except OSError:
                if visibility.administrator:
                    raise OSError("知识原文暂不可读，本次检索未完成。") from None
                continue
            if granted(one,raw):yield rel,_for_her(raw.decode('utf-8'))


#: **2026-10-10：它学到的「做法」那一棵** （ 作者要补的那一支 ）。
#: ⚠️ **为什么另立一棵、不并进 `知识`** ： `知识/` 是**人定的设定**
#: （ 盘上那一千多份的抬头写着"来源：你定的"， 它**只读** ）；
#: `做法/` 是**它自己学到的** ⇒ 两样混着放就会出现那个形状 ：
#: 一个"满了就删"的机制**分不出谁是谁** （ 2026-10-10 量到过 ——
#: 它下一次写就要删 978 份， 而第一批正是人手写的那两件 ）。
#: ⚠️ **那两格的意思与 `知识` 相反** ： 这一棵的名字 ＝ **"什么时候用它"** 、
#: 正文 ＝ **"怎么做"** （ `知识` 那一棵是"它是什么" ）。
#: ⚠️ **蒸馏由它自己做** —— 框架**不自动总结** （ 与"经历是框架机械地记的、
#: 画像是它**自己编辑**的"**同一条规矩** ）： 学会的东西必须由它认定学会了 。
#: ⚠️ **上界今天不设** —— 这一棵现在是 0 条 ⇒ 拍一个数**没有底**
#: （ 等它攒出量来， 数从量里来 ）。 而 它进**两条**检索路 。
SKILL_TREE = "做法"


@_source_writer
def remember_skill(home: Path, name: str, text: str, *, overwrite: bool = False,
                   kind: str = "unclassified", recorded_at: str = "", scope: str = "") -> Path:
    """ **记一条做法**（ 2026-10-10 —— 这是作者要补的那一支 ）。

    ⚠️ **与 `remember_knowledge` 的关系** ： **同一个形状** （ 逐字节写 ／
        同名默认拒写 ／ 要改明说 `overwrite` ）—— 而它们落在**两棵树**上 ，
        因为那两格的意思不同 ： 名字在那一棵是"它是什么"、 在这一棵是
       **"什么时候用它"** 。
    ⚠️ **它写的是"怎么做"** （ 步骤 ／ 顺序 ／ 什么情况下别用 ）——
        这一层**不解释、不总结、不加工** （ 与 `remember_knowledge` 同一条 ）。
    ⚠️ **名字先验再动盘**（2026-10-17）：那天它真写了一条做法，标题里带了一个 `"`，
       落在 Windows 上直接 `OSError: [Errno 22]` —— 与画像那一处**同一类**问题
       （名字没验就进了路径）。⇒ 走同一个门槛，只是把它叫什么说清楚。
    """
    the_skills.validate_kind(kind)
    with the_skills._locked(home):
        the_skills.recover_creation(home)
        target = Path(home) / SKILL_TREE / f"{_a_safe_file_name(name, what='做法') or '一条做法'}"
        if target.suffix != ".md":
            target = target.with_suffix(".md")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            raise FileExistsError(f"已经有一条同名的了：{target.name}（要改就明说 overwrite）")
        # ★ 2026-10-17 补验：**覆盖也要过治理层** —— 从前这里是 `target.write_text(...)`，
        #   于是"直接盖掉"绕开了版本检查、历史留版与归档保护（已并入的条目会被就地复活）。
        if target.exists() and overwrite:
            entry = the_skills.register(home, target.name)
            if str(entry.get("state") or "active") != "active":
                raise ValueError("「" + str(entry.get("title") or target.stem)
                                 + "」已经并进 " + str(entry.get("merged_into"))
                                 + " 了 —— 不能就地复活。")
            the_skills.write_body(home, entry["id"], str(text),
                                  expect=entry["version"], stamp="auto")
            return target
        the_skills.create_body(home, target.name, str(text), kind=kind,
                               recorded_at=recorded_at, scope=scope)
        return target


def skills_of(home: Path, *, name: str = "",
              include_merged: bool = False) -> list[tuple[str, str]]:
    """ **读做法**（ 与 `knowledge_of` **同一个形状** ）：`[(名字, 正文), …]`。

    ★ **2026-10-17 批③：只交"有效"的**（`active`）——
      "已并入"的旧条目**不再算当前做法**（列表、计数、喂索引都走这一条）。
      要看历史就显式 `include_merged=True`（它带状态，见 `skills_history`）。
    ⚠️ **注释照旧在这一步剥掉**（`_for_her`）。
    ⚠️ 拿不到元数据（老的做法、外部刚放进来的一份）⇒ 先登记（只动元数据）。
    """
    base = Path(home) / SKILL_TREE
    if not base.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for row in the_skills.rows(home, include_merged=include_merged):
        path = base / row["file"]
        if not path.is_file():
            continue
        if name and name not in (row["id"], row["title"], row["file"],
                                 Path(row["file"]).stem):
            continue
        # ⚠️ 名字**照旧取文件名那一段**（索引的键沿用它）：`title` 是同一样东西，
        #   但改名那一刻标题与文件名可能短暂不一致 —— 键跟文件走才不会出现两条。
        out.append((str(row["file"]), _for_her(path.read_text(encoding="utf-8"))))
    return out


def skills_history(home: Path) -> list[dict]:
    """ **做法那一棵的全貌**（含"已并入"）：ID ／ 标题 ／ 版本 ／ 状态 ／ 并入目标。

    ⚠️ 这是"历史读口"：默认那条路（`skills_of`）只给有效的；这一条**显式**要全。
    """
    return the_skills.rows(home, include_merged=True)


#: 它那两句「提议」落的地方（ **记录**在记忆层；**放行**那套队列是插件的，6.3）。
#: **2026-10-05 改名**（作者："悬着的事"）：
#: 从前叫「待确认」—— 而那三个字是"等别人批"的味道，
#: **2026-10-05 改名**（作者："悬着的事"）：
#: 从前叫「待确认」—— 而那三个字是"等别人批"的味道，
#: 与这一层的内核（**那边自己的事**）正好相反 。
#: 它盖两类：**那边的心情**（由那边自己安放）与
#: **心里悬着的事**（关于谁都算）；
#: 而"哪一类"三种：`self_observe` ⁄ `abouther` ⁄ `unknown` 。
#: 而"哪一类"三种：`self_observe` ⁄ `abouther` ⁄ `unknown` 。
PENDING_TREE = "悬着的事"


def _the_buffer_peek(home: Path, *, at_most: int = 3, cut: int = 40) -> str:
    """ **回执末尾那一截：它现在悬着的那几条**（作者 2026-10-17 拍的「甲」）。

    ⚠️ **为什么要它**：同一件事它会在同一轮里记两遍（真跑量到过：两条只差开头几个字）。
       而"这两条是不是一回事"**不该由系统判** —— 那是它的判断。
       ⇒ 所以不替它拦、也不替它合并：**把缓冲区亮出来给它看**，
         它看到已经有一条一样的，下一句就不会再记一遍。
    ⚠️ 只报**最近几条、每条截一小截**（这一格每写一次都会重发，不贵也不吵）。
    ⚠️ 读不成 ⇒ 交空串（为一句提示把回执带塌不值）。
    """
    try:
        rows = pending_of(home)
    except Exception:                        # noqa: BLE001
        return ""
    if not rows:
        return ""
    shown = rows[-at_most:]
    lines = "；".join(
        f"{i + 1}. {' '.join(str(one.text).split())[:cut]}"
        for i, one in enumerate(shown))
    return ("\n〔你现在悬着这几条（最近 " + str(len(shown)) + " 条，共 "
            + str(len(rows)) + " 条）〕" + lines
            + "\n（若与其中某条是同一件事，就不要再记一遍；想起来了可以先放着。）")


def skill_merge_handler(home: Path, *, stamp: Any) -> dict[str, "Callable[..., str]"]:
    """ **合并那一支笔**（2026-10-17 批③）—— **不进 `handlers()` 那张表**。

    ⚠️ 为什么单拎出来：那张表与 `tools.py` 的声明**一一对应**（`build_table` 会当场抛
       "这些 handler 没人认领"）。这一件是**部署上多接的一支**，声明在
       `launch._SKILL_MERGE_DECLARATION` —— 所以执行段也跟着由部署那一层接。
    ⚠️ `stamp()` 由调用方递（本模块不读时钟，与别处同一条）。
    """
    def _now() -> str:
        got = stamp() if callable(stamp) else stamp
        return str(got or "")

    def skill_merge(op_id: str = "", keep: str = "", sources: Any = "",
                    expect: Any = "", text: str = "", **rest: Any) -> str:
        """ **把几条做法并成一条**（2026-10-17 批③）—— 它点名、它给最终正文。

        ⚠️ **系统不代写内容、不判同义**：`text` 是它交的合并后正文。
        ⚠️ `expect` 是"每个 ID 我读到的是哪一版"（`{"sk-0001": "abc…"}`）——
           对不上就**一条都不写**。
        ⚠️ 来源**保留原文与历史**，只标"已并入 → 目标"；再查它就是那条历史读口。
        """
        if isinstance(expect, str):
            try:
                expect = json.loads(expect) if expect.strip() else {}
            except ValueError:
                return "expect 得是 {\"ID\": \"版本\"} 那种对象。"
        if not isinstance(expect, Mapping):
            return "expect 得是 {\"ID\": \"版本\"} 那种对象。"
        made = sources
        if isinstance(made, str):
            made = [one.strip() for one in made.replace("，", ",").split(",") if one.strip()]
        if not isinstance(made, (list, tuple)):
            return "sources 得是一串 ID（可以逗号隔开）。"
        try:
            keep_one = the_skills.resolve(home, keep)
        except the_skills.SkillError as why:
            return str(why)
        ids: list[str] = []
        for one in made:
            try:
                ids.append(the_skills.resolve(home, str(one))["id"])
            except the_skills.SkillError as why:
                return str(why)
        versions = {}
        for key, value in dict(expect).items():
            try:
                versions[the_skills.resolve(home, str(key))["id"]] = str(value)
            except the_skills.SkillError as why:
                return str(why)
        try:
            got = the_skills.merge(home, op_id=str(op_id), keep=keep_one["id"],
                                   sources=ids, expect=versions, text=str(text),
                                   stamp=_now())
        except the_skills.SkillConflict as why:
            return str(why)
        except the_skills.SkillRejected as why:
            return str(why)
        except Exception as why:                     # noqa: BLE001
            return "这条合并没有办成：" + type(why).__name__ + " " + str(why)[:120]
        return ("并好了：目标 [" + got["keep"] + "] 版本 " + got["keep_version"][:8]
                + "；并入 " + "、".join(got["sources"]) + "（原文与历史都留着，"
                "要看就 `skill_read` 那个 ID）。有效做法现在 " + str(len(skills_of(home))) + " 条。")

    return {"skill_merge": skill_merge}


def _the_skills_peek(home: Path, *, at_most: int = 5) -> str:
    """ **回执末尾那一截：它记过哪几条做法**（与 `_the_buffer_peek` 同一个手法）。

    ⚠️ **为什么要有它**（真跑量到过）：同一件事它两次写成**两个不同名字**的做法
       （"群里有人发图或表情时的处理" ／ "看到图或者表情包的时候"）——
       而「同名拒写」挡不住**不同名的近义重复**。
    ⚠️ **不替它合并**：把已有的名字摆出来，让它自己决定"改那一条（overwrite）"
       还是"另立一条"。这一层只报名字，不判同义。
    ⚠️ 读不成 ⇒ 空串（不带走回执）。
    """
    try:
        rows = skills_of(home)
    except Exception:                        # noqa: BLE001
        return ""
    if len(rows) <= 1:
        return ""
    names = [str(one[0]) for one in rows[:at_most]]
    # ⚠️ 名字照"它起的那个名字"报（`skills_of` 交回的带 `.md`）——
    #    回执上半句写的是 `[看到表情包时]`，下半句若多一个 `.md`，同一个东西两个写法。
    names = [one[:-3] if one.endswith(".md") else one for one in names]
    return ("\n〔你记过的做法（最近 " + str(len(names)) + " 条）〕"
            + "；".join(names)
            + "\n（若这一条与上面某条说的是同一件事，就明说 overwrite 去改那一条，"
              "别再立一条新的。）")


def _append_remembered(target: Path, one: Remembered) -> int:
    """Validate existing records before appending; never hide a damaged history."""
    from .append_journal import append_line
    from .source_guard import lease
    line = one.as_line()
    line.encode('utf-8')  # Refuse unencodable input before touching the journal.
    with lease(target):
        matching = 0
        try:
            stream = target.open('r', encoding='utf-8', newline='')
        except FileNotFoundError:
            stream = None
        if stream is not None:
            with stream:
                for existing in stream:
                    parsed = _transcript_rows(existing)
                    matching += sum(1 for row in parsed if one.role and row.role == one.role)
        append_line(target, line)
        return matching + bool(one.role)


@_source_writer
def remember_pending(home: Path, text: str, *, proposed: str, ts: str = "",
                     source: str = "", proposal: dict[str, str] | None = None) -> Path:
    """ **记一条提议**（`self_observe_add` ／ `abouther_propose` 那种）—— 存下来，**记成悬着的事**。

     口径（见 `docs/superpowers/notes/2026-10-05-它写的那几扇门归谁.md`）：
      **记录**归记忆层 ；**放行那套队列**归**插件**（设计稿 6.3） ⇒ 这一层**不造队列**。
    ⚠️ 于是它落成**一条记录 ＋ 一个状态**（`role` 那一格写的是**它自己那一棵树**给的名字）——
      将来那个队列（或人）认领它时，改的是同一份记录 （不是另开一本 ）。
    """
    target = Path(home) / PENDING_TREE / f"{Path(str(proposed)).name or '提议'}.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    one = Remembered(ts=str(ts), source=str(source), valid_from=str(ts),
                     role=str(proposed), text=str(text), proposal=proposal)
    _append_remembered(target, one)
    # **2026-10-10：真超了才裁** （ 作者拍的是 50 ； 单位是"那一个人那个文件"）。
    _trim_a_persona_file(target)
    return target


def pending_of(home: Path, *, proposed: str = "") -> list[Remembered]:
    """ **读那些提议**（点了名就只读那一类）—— 它们**还没被放行**（放行归插件 ）。"""
    base = Path(home) / PENDING_TREE
    if not base.is_dir():
        return []
    out: list[Remembered] = []
    for path in sorted(base.glob("*.jsonl")):
        if proposed and path.stem != str(proposed):
            continue
        out.extend(read_a_transcript(path))
    return out


@_source_writer
def resolve_pending(home: Path, what: str, *, proposed: str = "") -> bool:
    """ **把一条提议收起来** —— 交回"收掉没有"（2026-10-12）。

    ⚠️ **它是谁的那一半**：提议**记下来**归这一层（`remember_pending`）；
       **它自己拍板之后**把那一条收起来 —— 走这一句。作者 2026-10-12 那句
       「**\u5979自己决断**」说的就是这个：`pending` 不再是"等人批"，而是"它还没定"。
    ⚠️ **配的是"那句话本身"**（两边都先折过空白）：提议那一格存的就是它说的那句话 ／
       它来落定时拿到的也是那句（`pending_read` 交回来的样子）⇒ 折过之后逐字比。
    ⚠️ **收起来 ＝ 从那一份 jsonl 里删掉那一行**（那一份是**只追加**的 ⇒ 得整份重写）——
       与 `_self_note` 那一边"满了压最旧的"同一个手法：**只有这一条路会重写它**。
    ⚠️ **找不到 ⇒ `False`**（不编、不抛）：它记错了那一句话时，照实说"没有那一条"。
    """
    said = " ".join(str(what).split())
    base = Path(home) / PENDING_TREE
    if not said or not base.is_dir():
        return False
    hit = False
    for path in sorted(base.glob("*.jsonl")):
        if proposed and path.stem != str(proposed):
            continue
        rows = read_a_transcript(path)
        kept = [one for one in rows if
                ((one.proposal or {}).get("id") if one.proposal is not None
                 else " ".join(str(one.text).split())) != said]
        if len(kept) == len(rows):
            continue
        hit = True
        _publish_memory_text(path, "".join(one.as_line() + "\n" for one in kept))
    return hit


#: **习惯那一层的帐**（2026-10-05）：它记的是
#: **同一类举止被看到过几次**（ **不进提示词**：
#: 段序那张表里没有它 ）。
OBSERVE_NAME = "观察.jsonl"

#: 门槛（设计稿：**同一类 ≥ 3 次**才准升格）。
HABIT_THRESHOLD = 3


@_source_writer
def observe(home: Path, kind: str, *, when: str = "", source: str = "",
            evidence: str = "") -> dict[str, Any]:
    """ **记一次观察**（同类就累加）—— 交回那一类的现状。

    ⚠️ **只追加一行**（不改旧行）⇒ 次数 ＝ 行数，而出处 ＝ 每行那一格 。
    ⚠️ 而它**不会升格**：够不够门槛只回一个布尔值 ——
       升格那一步归整理态（而且要人过一眼）。
    """
    target = Path(home) / OBSERVE_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    one = Remembered(ts=str(when), source=str(source), valid_from=str(when),
                     role=str(kind), text=str(evidence))
    reached = _append_remembered(target, one)
    return {"kind": str(kind), "times": reached, "enough": reached >= HABIT_THRESHOLD}


def observed(home: Path, *, kind: str = "") -> list[dict[str, Any]]:
    """把账读回来（每一类一条：类 ／ 次数 ／ 第一次 ／ 最后一次 ／ 出处）。"""
    target = Path(home) / OBSERVE_NAME
    if not target.is_file():
        return []
    out: dict[str, dict[str, Any]] = {}
    for one in read_a_transcript(target):
        name = str(one.role)
        if not name or (kind and name != str(kind)):
            continue
        row = out.setdefault(name, {"kind": name, "times": 0, "first": "", "last": "",
                                    "where": []})
        row["times"] += 1
        row["first"] = row["first"] or str(one.ts)
        row["last"] = str(one.ts) or row["last"]
        if one.text:
            row["where"].append(str(one.text))
    return [out[name] for name in sorted(out)]


def habits_worth_writing(home: Path) -> list[dict[str, Any]]:
    """ **够门槛的那几类**（就是"该写进习惯那一份的"）。

    ⚠️ 它**只挑**，不写：谁写那一份是那扇门的事 （与 `自述` 同族）。
    """
    return [one for one in observed(home) if one["times"] >= HABIT_THRESHOLD]


def render_habits(rows: Iterable[Mapping[str, Any]]) -> str:
    """把那几类写成**一条一行**（它下一轮就读得到 ）。

    ⚠️ **一条一句**（设计稿：≤40 字）⇒ 超长的截断到 40 字，并在末尾留一个省略号 。
    ⚠️ **不写"那边就是这样的人"那种宣判** —— 只写被看到的样子 。
    """
    lines: list[str] = []
    for one in rows:
        said = str(one.get("kind") or "").strip()
        if not said:
            continue
        lines.append(said if len(said) <= 40 else said[:39] + "…")
    return "\n".join(lines)


def _her_turns(home: Path) -> list[Remembered]:
    """ **只取它自己说的那几条**（经历／对话里 `role == assistant` 的那些）。

    ⚠️ 只数**它自己**（设计稿 §二 ③ 那三个字）—— 别人的话不进来（那是"关于别人" ）。
    """
    path = Path(home) / TREES[0] / "对话.jsonl"
    if not path.is_file():
        return []
    return [one for one in read_a_transcript(path) if one.role == "assistant"]


def her_words(home: Path, *, top: int = 5) -> dict[str, Any]:
    """ **自我统计**：数**它自己**说了什么形状的话 —— **交回的全是数**（§二 ③）。

     交回四样（都能"确实看到"）：
      `turns`（说了几条）／`avg_chars`（平均一条几个字）／`asks`（问句几条）／
      `top_units`（最常用的几块：单字与英文词 —— **口气**）／`repeats`（同一句说过几遍）。
    ⚠️ **这一层不写推测** （`12:32-34`：观测与推测分开写）：它只**数**，
      一句结论都不下 —— 结论要不要写、怎么写，是**它**的事 。
    """
    rows = _her_turns(home)
    if not rows:
        return {"turns": 0, "avg_chars": 0, "asks": 0, "top_units": [], "repeats": []}
    texts = [one.text for one in rows]
    counts: dict[str, int] = {}
    for text in texts:
        for unit in _units_of(text):
            counts[unit] = counts.get(unit, 0) + 1
    seen: dict[str, int] = {}
    for text in texts:
        seen[text] = seen.get(text, 0) + 1
    return {
        "turns": len(texts),
        "avg_chars": round(sum(len(one) for one in texts) / len(texts), 2),
        "asks": sum(1 for one in texts if one.rstrip().endswith(("？", "?"))),
        "top_units": sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:max(0, top)],
        "repeats": sorted(((k, v) for k, v in seen.items() if v > 1),
                          key=lambda pair: (-pair[1], pair[0])),
    }


def _units_of(text: str) -> list[str]:
    """ 数"口气与用词"用的切法：**中文按单字**（统计要的就是字）＋ 英文／数字按词。

    ⚠️ 与 `tokens_of`（检索用的那个）**故意不同**：检索怕单字太吵 ，而统计要的就是单字 。
    """
    out: list[str] = []
    word = ""
    for one in str(text):
        if one.isascii() and (one.isalnum() or one in "_-"):
            word += one.lower()
            continue
        if word:
            out.append(word)
            word = ""
        if one.strip() and not one.isascii():
            out.append(one)
    if word:
        out.append(word)
    return out


def the_closest(texts: Sequence[str], query: str, *, at_most: int = 20) -> list[int]:
    """ **先精确、再模糊**（2026-10-07 作者拍的"关键词搜 ＋ 一点模糊"）—— 交回命中的下标。

    ⚠️ **两步走，是故意的**（顺序也是承重的）：
       ① **精确**：原样子串 ／ 或者把查询切开的每一块都出现在那一行里 ⇒ 有 ⇒ 就交精确的 ；
       ② **一条精确的都没有** ⇒ 才上**模糊** —— 否则"差不多像"的东西会把页面淹掉 。
    ⚠️ 模糊这一支**复用本仓已有的分词**（`tokens_of` ）：中文按字、英文按词 ——
        与 `kb_search` 那一套**同一份** ⇒ 两处不会各答一个数 。
    ⚠️ 模糊这一支**不认拼音**（`bd` 找不到"布丁" ）：那要一份拼音表 ⇒ 不值当，照实不做 。
    ⚠️ 它**不读钟、不写盘、不编**：一条都不像 ⇒ **空那一串** （由调用方照实说"没找到" ）。
    """
    rows = [str(one) for one in texts]
    said = str(query).strip()
    if not said:
        return []
    low = said.lower()

    # ① 精确：原样子串 ／ 查询切开的每一块都出现
    parts = [one for one in tokens_of(said)]
    exact = [i for i, one in enumerate(rows) if low in one.lower()]
    if not exact and parts:
        exact = [i for i, one in enumerate(rows)
                 if all(unit in tokens_of(one) for unit in parts)]
    if exact:
        return exact[:at_most]

    # ② 模糊（ 只在①一条都没有时才走到这儿 ）：**子序列 ＋ 只容忍错一个字**
    # ⚠️ **为什么不用「字交集 ≥ 一半」**（2026-10-07 真跑量到它太松 ）：
    # 四字查询里撞上两个常用字就够一半 ⇒ 假阳性
    # （真跑量到：「完全无关」命中了图书馆那一份 ）⇒ 换成下面这条 。
    # ⚠️ **短查询不许容错**（两个字以内 ⇒ 只认子序列 ）—— 否则「布丁」那种会到处命中 。
    def is_a_subsequence(needle: str, hay: str) -> bool:
        it = iter(hay)
        return bool(needle) and all(ch in it for ch in needle)

    scored: list[tuple[float, int]] = []
    for i, one in enumerate(rows):
        if is_a_subsequence(said, one):
            scored.append((2.0, i)) # 原样的子序列 ⇒ 更像
            continue
        if len(said) >= 3: # 三个字以上才容忍错一个
            for j in range(len(said)):
                if is_a_subsequence(said[:j] + said[j + 1:], one):
                    scored.append((1.0, i)) # 错一个字 ⇒ 差点
                    break
    scored.sort(key=lambda two: (-two[0], two[1]))
    return [i for _score, i in scored[:at_most]]
