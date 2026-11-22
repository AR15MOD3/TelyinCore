# telyincore/session.py
"""会话：**模型面对的那张消息表** —— 一个落盘的单文件 ＋ 追加 ／ 只读快照。

**它是 S5 的另一半。** `handoff.py` 的 `snapshot` / `reset` 两个槽一直是**空着的接缝**
（那个模块的 docstring 里就写着"谁把它们接起来是一件独立的事"），而这张表就是那个实现者。

**它存的是 `note.Record`**（`note.py:27-40`，frozen），一行一条、jsonl。三样同时成立：
① 能渲染成线格式 `{"role", "content"}`（`generate()` 要的那个）；
② 可序列化（10 个字段都是标量）；
③ **直接就是 `note.build(records)` 的输入** ⇒ 交接那一侧零转换。
⇒ 所以 `from .note import Record` 属于"有契约的层互相 import"**这一类，不是破例**
（`docs/superpowers/specs/2026-09-30-会话-design.md` §四·一 订正过
"零 import 是既有事实"那句：写那一份时实测 **5 个模块**有内部 import，
而**本模块是第 6 个** ⇒ 规则是"**要序列化就必须知道确切的字段集**"，
这一处鸭子类型不成立）。

⚠️ **它不碰时钟** —— 一条记录发生在哪一刻，由**造记录的人**写进 `Record.ts_ms`；
   本模块一个"现在"都不需要（计划 Task 1 Step 5：收不到任何东西的参数要删，`head.py:58-66`）。
⚠️ **它认识盘，但不认识总线／模型／循环** —— "会话不碰总线"是 `handoff.py:58` 点名的契约：
   注入（交接的那句警告）要排在**总线队列**里，才不会被 `reset` 冲掉。
⚠️ **合成开头不进这里**（`head.py:6-7`）：开头**每次现取**，一落盘就会被改，一改就废缓存。

**本份做到**（计划 Task 1 ＋ Task 2 ＋ Task 3 ＋ Task 4）：`append` ／ `records` ／ `reset`（原子整体替换）／
`load`（修末行：半截截掉、**完整但没收尾的补上**，见 `_settle_tail` —— 截那一半与 `log.py`
**共用同一个实现**）／ `outbound`（合成开头插在**连续 system 之后**，纯拼接、不落盘）／
`est_tokens`（**先渲染成线格式**再交给 `tokens.estimate_messages_tokens` ——
`_render_message` 是全模块**唯一**那一处线格式）／ 落盘 ＋ 建父目录。
"""
from __future__ import annotations

import json
import hashlib
import os
from functools import wraps
from .source_guard import lease
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .log import repair_tail as _repair_tail, _repair_output, _repair_digest, _regular_repair_file
from .note import Record
from .receipt_projection import CurrentBatchOutput, SourceOutput
from .original_evidence import is_original_receipt
from .tokens import estimate_messages_tokens


def _parse_line(line: str) -> Record:
    """**一行 → 一条 `Record`** —— 只解析，不管这一行是怎么来的。

    ⚠️ 与「文件里有什么」（`_read_lines`）**分开写是有意的**：`load` 在**读**那条路上
       插了一步（把断电留下的半截末行剪掉）——
       它插在 `_read_lines` 那一侧，**不碰这里** ⇒ 解析的判据只有一份。
    ⚠️ **不吞异常**：一行读不回来（不是 json ／ 不是对象 ／ 字段名不认识）就照抛 ——
       "跳过读不了的"会让一次**盘损坏**静默地变成"少了几条消息"（`spec §五`：
       末行只修它，**中间坏行抛**）。
    """
    # ⚠️ 那一格（`images`）的形状**由 `Record.__post_init__` 规范化** —— 这里不重复做，
    # 一处只做一件事（旧记录没有那一格 ⇒ 走类的默认值 ）。
    return Record(**json.loads(line))


def _read_records(path: Path):
    """Derive replay locators during the normal physical-line read, including CRLF."""
    records = []
    references = {}
    digest = hashlib.sha256()
    if path.exists():
        with path.open("rb") as stream:
            for raw in stream:
                digest.update(raw)
                if not raw.strip():
                    # The session reader accepts old blank lines. The original
                    # record lookup cannot index this file, so keep full text.
                    references = None
                    continue
                record = _parse_line(raw.decode("utf-8"))
                records.append(record)
                _remember_excerpt(references, len(records), record, digest)
    return records, references, digest


def _render_line(record: Record) -> str:
    """**一条 → 盘上那一行**（**不含**行尾那个换行）—— 只序列化，不管写到哪去。

    ⚠️ 与 `_parse_line` 是一对：那是"一行怎么读成一条"，这是"一条怎么写回一行"。
    ⚠️ **两个写方法共用这一处**（`append` ／ `reset`）：各拼各的话，同一条记录在
       **同一个文件**里就会有两种写法（转义不同 ／ 分隔符不同）—— 而读侧两种都读得回来
       ⇒ **分家不报错、也没人看得见**。
    ⚠️ **一行一条**：`json.dumps` 把正文里的换行转成 `\\n` 两个字符 ⇒ 这条由它保证
       （本模块的**读侧**整条建在它上面），不由调用方保证。
    """
    data = asdict(record)
    if not record.record_ref:
        data.pop('record_ref')
    if not record.display_id:
        data.pop('display_id')
    if not record.tool_ref:
        data.pop('tool_ref')  # Preserve legacy record bytes when not associated.
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class ActivePersona(dict):
    """Current persona message that supersedes archived generated heads."""


def _render_message(record: Record, call_id: str | None = None) -> dict[str, Any]:
    """**一条 → 线格式** `{"role", "content"}` —— 与 `_render_line` 是**两种渲染**，别混。

    ⚠️ **两个渲染不是一件事，因为收的人不是一个人**：
      · `_render_line` —— 给**盘**（10 个字段一条 jsonl，读回来要还原成同一条 `Record`）；
      · 这一个 —— 给**模型**（线格式只要两个键，形状见 `cli.py:36`）。
      合成一个的话，落盘的那一份会带上渲染用的键、或者请求的那一份会带上 10 个字段
      —— 两边都错，而且**都不报错**。

    ⚠️ **它为什么在这里**：`est_tokens()` 要叫的是 `tokens.estimate_messages_tokens`，
       而**它收的是 mapping、不是 `Record`**（它读 `m.get("content")`）⇒
       中间必须有这一步。**整个模块只有这一处渲染线格式** —— 将来接线那一层要出请求，
       也该走这里（两处各拼一份的话，`est_tokens` 量的就不再是真正发出去的那一份）。

    ⚠️ **`role` 必须在**：`tokens` 那一侧只看 `content`（所以漏了 role 它照样算得出数），
       而线格式里 role 是必填 —— 漏了它，这条数就成了"**一份发不出去的请求**"的估算。
    ⚠️ **`text` 落在 `content` 这个键上**：两个名字在两层里各自是对的（本仓的形状），
       转换就发生在**这一行**。
    """
    # **带图的那一条渲染成"片"**（2026-10-05「图片输入」最小路）：
    # ⚠️ 没有图 ⇒ **逐字与从前相同**（`{"role", "content"}` 两个键、content 是字符串
    #       —— 零回归那一条就靠它）。
    # 这里产的是 **`responses` 那一边的形状**（`input_text` ／ `input_image`），
    # 把"另一边叫什么"交给 `protocol._shape_media` 去换 （它本来就是干这个的）。
    # ★★ **2026-10-13（待办账第 9 条的后半，作者：「摆上」）**：**"谁说的"进请求了** ——
    #    那一格（`Record.who`）有值时，正文前面加 `谁：`（终端与页上早就是这个形状，
    #    而**模型这一侧从前没有它**：换一头 ／ 人多起来，它就分不清是谁在说）。
    #    ⚠️ **那一格是空串 ⇒ 逐字与从前一样**（零回归；老会话文件里那些行读回来就是空串）。
    #    ⚠️ 带图那一条**同样**加在那个 `input_text` 片上（文字与图还是同一条消息）。
    mine = str(getattr(record, "who", "") or "")
    said = (mine + "：" + record.text) if mine else record.text
    from .reported_records import project as project_report
    said = project_report(said, record)
    if not record.images:
        out: dict[str, Any] = {"role": record.role, "content": said}
    else:
        parts: list[Any] = [{"type": "input_text", "text": said}]
        for url in record.images:
            parts.append({"type": "input_image", "image_url": url})
        out = {"role": record.role, "content": parts}
    # **工具那两条**（2026-10-05 作者："接上"）—— 引擎那边的形状**实测过**：
    # · `{"type":"function_call","call_id":…,"name":…,"arguments":…}` ⇒ 通
    # · `{"type":"function_call_output","call_id":…,"output":…}` ⇒ 通
    #      ⚠️ **不带 `call_id` ⇒ 400**（`function_call_output must contain a non-empty call_id`）；
    #      ⚠️ chat 那边惯用的 `role="tool"` ⇒ **400**（`unsupported input message role: tool`）。
    # ⚠️ **那个 `call_id` 是现造的**（`call_1` ／ `call_2` …）：引擎**只要求两边同一个 id** ，
    # 而会话**照 Q2 丙不承载配对键** ⇒ 由调用方（`outbound`）按**成对的次序**现造一对 。
    if call_id is not None and record.kind == "function_call":
        return {"type": "function_call", "call_id": call_id,
                "name": record.name, "arguments": record.text}
    if call_id is not None and record.kind == "function_result":
        return {"type": "function_call_output", "call_id": call_id,
                "output": record.text}
    return out


@dataclass(frozen=True)
class TailRepair:
    """`load()` 修末行的结果：**末段到底怎么了** —— 两种情形**必须分得开**。

    ⚠️ **为什么非有不可**：修末行有**两种**动法，而后果**完全不是一回事**：
      · `"terminated"` —— 末段是一条**完整的记录**，只少了收尾那一个字节
        ⇒ 补一个 `\\n` 保住它（`byte_count` ＝ 保住的那一段有多长，**一个字节都没丢**）；
      · `"truncated"` —— 末段读不回来（断电写了一半）⇒ 截掉
        （`byte_count` ＝ **丢掉**的字节数）。
    `None`（`load` 的返回值）＝ **盘上原来就是干净的**，这一趟什么都没修。
    ⚠️ 这张报告存在的理由就是**让"这一次到底丢没丢东西"看得见** ——
    两种情形合成一个"修好了"是**把丢数据与没丢数据说成同一件事**。
    """

    kind: str            # "terminated"（补 `\n` 保住）｜ "truncated"（截掉）
    byte_count: int      # 保住的 ／ 丢掉的，那一段的字节数


def _is_a_whole_record(raw: bytes) -> bool:
    """这**一段字节**读得回来吗（＝ 它是一条完整的 `Record`，不是半截）。

    ⚠️ **判据就是读侧那一条**（`_parse_line`）—— 不另写一份："这一段能不能读"
       与"这一行是不是一条记录"必须是**同一个谓词**（`log.py:97` 那句话的同一个道理）。
       它抛的那三种（不是 json ／ 是 json 但不是对象 ／ 字段名不认识）**就是**"读不回来"
       的全部 ⇒ 收 `ValueError`（`JSONDecodeError` 与 `UnicodeDecodeError` 都在它底下）
       与 `TypeError`。
    """
    try:
        _parse_line(raw.decode("utf-8"))
    except (ValueError, TypeError):
        return False
    return True


def _settle_tail(path: Path) -> TailRepair | None:
    """**会话自己的末行策略** —— 末段（最后一个 `\\n` 之后那些字节）该留还是该截。

    ⚠️ **为什么不能直接用 `_repair_tail`**（＝ `log.repair_tail`，那个共享的实现）：
       它的判据是"**不以 `\\n` 收尾 ⇒ 截**"，而那条判据**只对事件库成立** ——
       事件库**解析不了自己的行**（`log.py` 不知道事件的形状）⇒ 它只能把这种尾巴
       一律当"半截"。会话**认得出自己的行**（`_parse_line`）⇒ 它有资格把那两种分开：
       · 末段**解析得回来** ⇒ 那是**一条完整的记录**，只是断电时少写了收尾那**一个**字节
         ⇒ **补一个 `\\n`**，一条都不丢；
       · 末段**解析不回来** ⇒ 那才真是半截 ⇒ 交给 `_repair_tail` 截掉
         （**截那一半仍然只有一份实现** —— 丙 那条"两家共用"没有被绕过）。
    ⚠️ **这条判据是可靠的**：JSON **对象**的前缀**不可能是合法 JSON** —— 收尾的 `}`
       只在最末尾出现，而正文里的 `}` 会被扫描器按字符串转义正确跳过
       ⇒ **半截的不会被当成完整**收进来（原来那个 bug 是反方向：完整的被当半截）。
    ⚠️ **补 `\\n` 这一步幂等、且只会加**：崩在"写下去"与"落盘"之间只是回到没写收尾的样子
       ⇒ 下一次 `load` 再补一遍，**不会更坏** ⇒ 这一步不摆 temp ＋ `fsync` 那一套
       （截那一半要摆：它是**删**字节，半途停下会留下半份，见 `log.repair_tail` 的 docstring）。
    ⚠️ **前提与 `log.py` 一样：这是一份单写者的文件。** 多写者下这次"补一个字节"会补到
       别人刚写下的半截上 —— 那种用法本模块没有承诺过。
    """
    from .file_signature import descriptor_signature, signature
    try:
        source = path.open('rb')
    except FileNotFoundError:
        return None
    with source:
        initial = descriptor_signature(source.fileno())
        size = initial[2]
        if not size:
            return None
        source.seek(size - 1)
        if source.read(1) == b"\n":
            return None
        # Only the last physical line needs classification. Retaining the whole
        # transcript here doubles startup memory before Session even parses it.
        # Memory scales with one tail record, not all retained session bytes.
        position, parts = size, []
        while position:
            start = max(0, position - 65536)
            source.seek(start)
            block = source.read(position - start)
            if len(block) != position - start:
                raise OSError('session changed during tail classification')
            split = block.rfind(b"\n")
            parts.append(block[split + 1:] if split >= 0 else block)
            if split >= 0:
                break
            position = start
        if descriptor_signature(source.fileno()) != initial:
            raise OSError('session changed during tail classification')
    tail = b''.join(reversed(parts))
    whole = _is_a_whole_record(tail)
    if signature(path) != initial:
        raise OSError('session changed since tail classification')
    if whole:
        # r+b must not recreate a file deleted after the decision. Check the
        # actual opened descriptor as well as the path before adding a newline.
        with open(path, 'r+b') as fh:
            if descriptor_signature(fh.fileno()) != initial or signature(path) != initial:
                raise OSError('session changed before tail termination')
            boundary = size - len(tail)
            if boundary:
                fh.seek(boundary - 1)
                if fh.read(1) != b"\n":
                    raise OSError('session tail boundary changed before termination')
            fh.seek(boundary)
            for offset in range(0, len(tail), 65536):
                expected = tail[offset:offset + 65536]
                if fh.read(len(expected)) != expected:
                    raise OSError('session tail bytes changed before termination')
            if fh.read(1) or signature(path) != initial:
                raise OSError('session changed before tail termination')
            fh.seek(0, os.SEEK_END)
            fh.write(b"\n")
        return TailRepair(kind="terminated", byte_count=len(tail))
    return TailRepair(kind="truncated", byte_count=_repair_tail(path, expected_signature=initial, expected_tail=tail))



class TornTailError(ValueError):
    """末行**不以换行收尾**（一截没写完的行）⇒ 拒绝往里追加。

    ⚠️ 它是 `ValueError` 的子类，理由与 `log.TornShardError` 是同一个：它与
       "这一条现在写不进去"属于同一族。
    ⚠️ 而它**必须是一个自己的类型**：调用方要能把它与"盘满了"那类 `OSError` 分开
       —— 这一条**自己会好**（重开一次 `Session(path)` 就把末行摆平了），而盘满不会。
    """


_PAST_READ_TOOLS = frozenset({"read_events", "timer_read", "timer_history", "pending_read", "tool_guide"})

def _excerptable(record):
    if record.kind == "handoff_note":
        return len(record.text) > 2800
    return (record.kind == "function_result" and record.name in _PAST_READ_TOOLS
            and len(record.text) > 1000 and not record.text.startswith("[tool failed]"))


def _remember_excerpt(references, number, record, digest):
    if references is not None and _excerptable(record):
        references[number] = record.record_ref or f'{digest.hexdigest()}:{number}'


def _qq_history_excerpt(record, reference):
    text = record.text
    lead, tail = (1400, 650) if record.kind == "handoff_note" else (150, 70)
    return (text[:lead] + "\n〔旧记录节选，中间已省略；全文 " + str(len(text)) +
            " 字。如需核对，用 read_events(store=records, record_ref=" + reference +
            ") 翻原文。〕\n" + text[-tail:])


class SessionConflict(ValueError):
    """The active transcript changed or a previous write has uncertain outcome."""


def _serialized(function):
    @wraps(function)
    def guarded(self, *args, **kwargs):
        with lease(self.path):
            return function(self, *args, **kwargs)
    return guarded


class Session:
    """一张落盘的消息表：`append` 追加一条（**先核这个文件的末行没断**）／ `reset` 整体替换
    （原子），`load` 从盘上读回来（末行：半截截掉、完整但没收尾的补上），`records()` 取只读
    快照，`outbound(head)` 出请求时把合成开头插进**连续 system 之后**，`est_tokens()` 量
    **存档那一份**有多长。"""

    def __init__(self, path: Path) -> None:
        """收一个 path（由接线那一层给：`05:150` 的 `data/session-main.jsonl`），**建父目录**。

        ⚠️ **父目录在这里建** —— `append` 只做 `open(..., "ab")`，它不替谁建目录；
           而 path 是接线那一层拼出来的（`data/` 这种目录第一跑并不存在）。
        ⚠️ **构造时就把盘上那一份读进来** —— "重启之后那条还在"没有第二个落点；
           读那一趟整个在 `load()` 里（修末行 ＋ `_read_lines` ＋ `_parse_line`）。
        ⚠️ **而构造那一趟修末行的结果挂在 `self.tail_repair` 上**（`TailRepair | None`，
           由 `load()` 写）：`__init__` 递不出第二个返回值，而接线那一层走的就是构造 ⇒
           没有这个属性，**构造那一趟修了什么就没有任何接收方**。
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 内部那一份是 **list**（记账发生在 `append` 里）—— `records()` 交出去的是 tuple。
        self._records: list[Record] = []
        self.load()                     # ← 读那一趟（修末行 ＋ 逐行解析）全在 `load` 里

    @_serialized
    def load(self) -> TailRepair | None:
        """**把盘上那一份读回来**：先摆平那截没写完的末行，再逐行解析；内存里换成这一份。

        ⚠️ **末行那两种情形要分开**（会话自己的策略，全文在 `_settle_tail` 的 docstring 里）：
           末段**是一条完整的记录**、只少了收尾的那一个字节 ⇒ **补一个 `\\n` 保住它**；
           读不回来才是半截 ⇒ 截掉。直接借 `log.repair_tail` 的话（它只认"没收尾 ⇒ 截"），
           一次少写一个字节就会把**已经写完的那一条**当垃圾删掉 —— 而那是本模块自己的
           数据，会话认得出它（事件库认不出 ⇒ 那一半仍然共用 `log.repair_tail`）。
        ⚠️ **修的只可能是末行** —— 中间有坏行就**照抛**（`_parse_line` 不吞异常）。
           理由不是洁癖：把"跳过读不了的"当成宽容，会让一次**盘损坏**静默地变成
           "少了几条消息"，而那正是 `spec §八③` 要判的那件事（"中间坏行 ⇒ 抛"）。
        ⚠️ **次序是"先盘后内存"** —— 与 `append` ／ `reset` **同一条次序、同一个理由**：
           先把新的那一份**整个读出来**，成功了才换上去 ⇒ 读到一半抛了，内存里仍是
           原来那一份（不是半份新的）。两个视图分家是静默的，所以这里不给自己留口子。
           （⚠️ 末行的整理**先落在盘上**、与解析无关：它自己的正确性不依赖解析成功；
             解析抛了，这一次就没有报告 —— 异常把它带走了，而 `self.tail_repair`
             保持上一次那一份。）
        ⚠️ **返回值是这次修末行的报告**（`spec §四` 草图那个 `RepairReport | None` 的形状）：
           `None` ＝ 盘上本来就干净。**两种情形必须分得开**（`TailRepair`）——
           把"补了一个换行"与"截掉一段"报成同一件事，就是把"没丢"与"丢了"报成同一件事。
        ⚠️ **构造那一趟也有报告可看**（`self.tail_repair`）：`__init__` 递不出第二个返回值，
           而接线那一层走的就是构造 ⇒ 报告只从返回值出去的话，
           **构造那一趟恰恰是看不见的那一趟** —— 而那正是原来静默出事的那一趟。
        """
        repair = _settle_tail(self.path)
        before = self._disk_signature()
        content_size, content_hash = self._content_state()
        loaded, references, parsed_hash = _read_records(self.path)
        after_size, after_hash = self._content_state()
        if (parsed_hash.digest() != content_hash.digest() or self._disk_signature() != before or after_size != content_size
                or after_hash.digest() != content_hash.digest()):
            raise SessionConflict('session changed during load')
        self._disk_state = before
        self._content_size, self._content_hash = content_size, content_hash
        self._write_failed = False
        self._records = loaded
        self._qq_excerpt_refs = references
        self._host_observations = {}
        self._tool_evidence_epoch = object()
        self._tool_evidence = []
        self.tail_repair = repair
        return repair

    def records(self) -> tuple[Record, ...]:
        """ **只读快照**（`spec §六④`：给 tuple，不给内部那个 list）。

        ⚠️ **为什么非是 tuple**：落盘与记账都挂在 `append()` 上 ⇒ 交出内部 list 的话，
           调用方一个 `.append()` 就能**内存里多一条、盘上没有**，
           而那条会在**下次重启**时无声无息地消失（`spec §六④` 防的就是这件静默的事）。
        ⚠️ **这里每次都 `tuple(...)` 一份**（O(n)）—— 而**不做缓存是有意的**：
           缓存等于在每条写入路径上都欠一次失效，谁漏一处，`records()` 就交出**过期的一份**，
           而那也是静默的。几十／几百条的量级不值得拿一条静默路去换。
        """
        return tuple(self._records)

    def recent_records(self, limit=32):
        """Bounded tail snapshot for host checkpoints; original records stay intact."""
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError('recent record limit must be 1..128')
        return tuple(self._records[-limit:])

    def retains_history_boundary(self, boundary):
        """An append preserves a prior in-memory record position. A reload or replacement may not."""
        if not boundary:
            return False
        count, last = boundary
        rows = self._records
        return 0 < count <= len(rows) and rows[count - 1] is last

    def _disk_signature(self):
        from .file_signature import signature
        try:
            return signature(self.path)
        except FileNotFoundError:
            return None

    def _content_state(self):
        # A finite raw-byte baseline for destructive replacement. Native
        # timestamps can coalesce; rendering parsed records would also lose
        # original whitespace/CRLF and is not a valid byte baseline.
        digest = hashlib.sha256()
        try:
            source = self.path.open('rb')
        except FileNotFoundError:
            return None, digest
        with source:
            size = os.fstat(source.fileno()).st_size
            remaining = size
            while remaining:
                block = source.read(min(65536, remaining))
                if not block:
                    raise SessionConflict('session shortened during content check')
                digest.update(block)
                remaining -= len(block)
            if source.read(1):
                raise SessionConflict('session grew during content check')
        return size, digest

    def _content_matches(self):
        size, digest = self._content_state()
        return size == self._content_size and digest.digest() == self._content_hash.digest()

    def _check_content(self):
        if not self._content_matches():
            raise SessionConflict('session bytes changed; reload before replacement')

    def _check_disk(self):
        if self._write_failed:
            raise SessionConflict('previous session write outcome uncertain; reload before mutation')
        if self._disk_signature() != self._disk_state:
            raise SessionConflict('session changed by another writer; reload before mutation')

    @_serialized
    def append(self, record: Record) -> None:
        """追加**一条**：盘上多一行，内存里多一条。**两件都做，次序是"先盘后内存"。**

        ⚠️ **次序就是这个方法的全部**：先写盘、成功了才记账 —— 反过来的话，
           写盘失败时内存里会多出一条 `records()` 看得见、盘上没有的记录。
           "内存 ⇔ 盘上"那条不变量的**唯一**保证就是这两行的先后。
        ⚠️ **一行一条、且不含换行** —— 拼那一行的活儿在 `_render_line` 里
           （`reset` 走同一处）：一条记录**永远**只占一行，本模块的读侧就建在这条上。
        ⚠️ **行尾是写下来的那个字节 `\\n`**（`"ab"` ＋ 先编码）：文本模式的话，
           这台机器（Windows）会把 `\\n` 涨成 `\\r\\n`，而别处的文件系统写 `\\n`
           ⇒ 盘上的形状由**跑的那台机器**定 —— 一种没写在纸上的持久格式。
           `reset` ／ 读侧**必须一起**是字节（见 `reset`），只改一条会把**两种行尾
           写进同一个文件**，那比任何一种一致的选择都坏。
        ⚠️ **编码发生在 `open()` 之前** —— 照 `log.py` 的 `append_line` 那条理由：
           一个写不进去的字符（孤立代理项之类）若在 `open()` 之后才炸，
           盘上会留下**一个零字节的文件** —— 而"**还没写过**"与"**写过但没写进去**"
           在读侧长得一模一样（两者都是空会话）⇒ 一次拒绝，一点痕迹都不留。
        ⚠️ **写之前还要核另一半：这个文件必须以换行收尾**，否则 `TornTailError`。
           理由与 `log.py:178-190` 的 `TornShardError` 逐字同源，而这一份**还要说出下一步**：
           往一截没写完的末行后面追加，会**粘出**一条谁也解不开的行 —— 而粘出来的那条
           **以收尾换行结尾** ⇒ `_settle_tail` 的判据（`data.endswith(b"\\n")`）**再也看不见它**
           ⇒ 本模块的修复路径**再也不可能触发**，那个文件要手工改才能启动框架。
        ⚠️ **那条"单写者"前提盖不住它**（`_settle_tail` 的 docstring 末尾那条）：这里的写者
           **就是本进程自己**，而它还活着 —— 上一刀断电留下的残尾，这一句会把它粘住。
        ⚠️ **不在这里截断**：截／补是 `load` 的事、时机是"重开一次" ⇒ 写路径上**静默截断**
           等于替别人丢数据（照 `log.py` 那条）。**当场拒**才是本层的口径。
        ⚠️ 只读**最后一个字节**（不 `read_bytes()`）—— 追加是热路径；而这一眼**不许带出
           静默写入**：只有文件不存在可直接创建，其他读取失败须在追加前报错。
        """
        data = (_render_line(record) + "\n").encode("utf-8")
        # ⚠️ **一次 `stat`，不是"先 `exists()` 再 `stat()`"**：那两步之间文件可能被别的东西
        # 删掉 ⇒ 第二步抛的 `FileNotFoundError` 会让本层的拒绝面凭空多一条（照 `log.py`）。
        try:
            if self.path.stat().st_size > 0:
                with open(self.path, "rb") as fh:
                    fh.seek(-1, os.SEEK_END)
                    if fh.read(1) != b"\n":
                        raise TornTailError(
                            f"{self.path.name} 不以换行收尾（一截没写完的末行）⇒ 拒绝追加："
                            "粘上去的那一条谁也解不开，而它会让修末行的判据再也看不见它。"
                            "下一步：重开一次 `Session(path)`（它会把末行摆平 —— 完整缺收尾的"
                            "补回换行、真半截的截掉），然后重试这次 append。")
        except FileNotFoundError:
            # ⚠️ 只有**读探针**在这儿 —— 上面那条 `TornTailError` 是 `ValueError`，谁也不接它。
            pass
        self._check_disk()
        try:
            with open(self.path, "ab") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            self._disk_state = self._disk_signature()
        except BaseException:
            self._write_failed = True
            raise
        # Advance the exact written-byte baseline without rereading the full
        # transcript on every append. Reset revalidates it before destruction.
        self._content_hash.update(data)
        self._content_size = (self._content_size or 0) + len(data)
        self._records.append(record)
        _remember_excerpt(self._qq_excerpt_refs, len(self._records), record, self._content_hash)
        if record.kind in ('function_call', 'function_result') and record.tool_ref:
            reference = record.record_ref or f'{self._content_hash.hexdigest()}:{len(self._records)}'
            self._tool_evidence.append((len(self._records), record, 'record:' + reference))
            del self._tool_evidence[:-128]

    @_serialized
    def reset(self, records: Sequence[Record]) -> None:
        """**整体替换**：盘上换成**这一份**（不是追加在后面），内存里也换成同一份。

        ⚠️ **原子**（`05:197` 点名那条）—— 次序是「**写 temp 全写完 ⇒ 才 rename 覆盖**」。
           直接 `open(self.path, "w")` 的话，断电那一刀落在"已经清空"与"还没写完"
           之间 ⇒ 盘上是一份**空的**（整场会话没了）。走 temp ＋ 换名之后，
           **任何一刻断电**盘上都只有两种样子：旧的完整一份，或新的完整一份 —— 取不到中间态。
        ⚠️ **必须是 `os.replace(...)`（属性访问），不许 `from os import replace`**：
           那条原子性测试造"换名那一刻断电"用的是 `monkeypatch.setattr(os, "replace", …)`
           —— 本模块手里若攥着另一个引用，那一刀就落在替身之外 ⇒ 测试照样绿，
           而**它量的是空气**（那条测试是本任务唯一的原子性证据）。
        ⚠️ **temp 与 `path` 在同一个目录**（名字 ＝ `path` 的名字 ＋ `.tmp`）：
           `os.replace` 只在**同一个文件系统**内保证原子，跨盘会退化成"复制 ＋ 删"。
        ⚠️ **`flush` ＋ `os.fsync` 不是装饰** —— 只写进操作系统的缓存的话，
           断电那一刀仍可能落在"temp 是空的 ／ 半截的"上；交给盘之后它就是完整的。
           （⚠️ 这一条**进程内的测试看不见** —— 变异检验实测：删掉 fsync、甚至连着
             `flush` 一起删掉，四条 `reset` 的测试**全绿**。它按 `brief` Step 3 点名写字，
             **不是**被测试逼出来的 ⇒ 谁要删它，得先回答"这儿的断电算不算数"。）
        ⚠️ **先盘后内存** —— 与 `append` **同一条次序、同一个理由**（见它的 docstring）：
           先改内存的话，换名失败时内存里已经是新的、盘上还是旧的 ⇒
           再 `append` 就会把新记录接到旧那份后面 ⇒ 两个视图**永久分家**，且不报错。
        ⚠️ **失败之后不替谁清理那个 temp**：保留兼容的固定名字；下一次 `reset`
           先核验普通文件、链接数、归属及打开身份，才截断重写。暂存内容发布前另验摘要
           ⇒ 上一回留下的半截产物不会被当成内容读进去
           （读侧只看 `self.path`，从不看 temp）。
        ⚠️ **行尾是写下来的那个字节 `\\n`**（`"wb"` ＋ 先编码）—— 与 `append` ／
           读侧**一起**是字节，理由见 `append`。⚠️ 半途写坏时在盘上的是 temp
           （`os.replace` 还没发生），而两种行尾混在**一个文件**里正是"只改了其中一条路"
           留下的形状 ⇒ 那一条路**必须**跟着一起改。
        ⚠️ 签名收的是 `Sequence`（`spec §四` 那张表）⇒ 先 `list(...)` 落成一份**复制**：
           直接存的话，调用方事后改自己那个 list 就能让快照里多一条、盘上没有
           —— 与 `records()` 给 tuple 防的是同一件事。
           （⚠️ 这一条**套件看不见** —— 变异检验实测：改成 `self._records = records`，
             四条 `reset` 的测试**全绿**。它是这里要一个 list 的顺带产物，
             **不是**被测试逼出来的。）
        """
        self._check_disk()
        self._check_content()
        new = list(records)
        from dataclasses import replace
        from .record_history import preserve, current_references, reuse_preserved
        retained = {id(row) for row in new} & {id(row) for row in self._records}
        if retained:
            references = current_references(self.path, self._records)
            digest = hashlib.sha256()
            stable = []
            for number, row in enumerate(new, 1):
                line = (_render_line(row) + "\n").encode("utf-8")
                prospective = digest.copy(); prospective.update(line)
                if (id(row) in retained and not row.record_ref
                        and references[id(row)] != f'{prospective.hexdigest()}:{number}'):
                    row = replace(row, record_ref=references[id(row)])
                    line = (_render_line(row) + "\n").encode("utf-8")
                digest.update(line)
                stable.append(row)
            new = stable
        if not reuse_preserved(self.path, self._content_size, self._content_hash.hexdigest()):
            preserve(self.path)  # Failure must prevent context replacement.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        from .file_signature import descriptor_signature, signature
        written, payload = 0, hashlib.sha256()
        excerpt_refs = {}
        with _repair_output(tmp) as fh:
            identity = descriptor_signature(fh.fileno())[:2]
            for number, record in enumerate(new, 1):
                raw = (_render_line(record) + "\n").encode("utf-8")
                fh.write(raw)
                payload.update(raw)
                _remember_excerpt(excerpt_refs, number, record, payload)
                written += len(raw)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            if not _regular_repair_file(tmp.lstat()):
                raise OSError('unsafe session replacement staging file')
            with tmp.open('rb') as check:
                staged = descriptor_signature(check.fileno())
                if (staged[:2] != identity or staged[2] != written
                        or _repair_digest(check, 0, written) != payload.digest()
                        or check.read(1)):
                    raise OSError('session replacement staging changed')
            self._check_disk()
            self._check_content()
            if signature(tmp) != staged:
                raise OSError('session replacement staging changed before publication')
            # Cooperating writers hold the lease. Byte checks do not turn the
            # final check/replace into filesystem CAS for hostile writers.
            os.replace(tmp, self.path)
            self._disk_state = self._disk_signature()
        except BaseException:
            try:
                self._write_failed = (self._disk_signature() != self._disk_state
                                      or not self._content_matches())
            except (OSError, SessionConflict):
                self._write_failed = True
            raise
        self._content_size, self._content_hash = written, payload
        self._records = new
        self._qq_excerpt_refs = excerpt_refs
        self._host_observations = {}
        self._tool_evidence_epoch = object()
        self._tool_evidence = []

    @_serialized
    def tool_evidence_snapshot(self):
        """Bounded locators for this process's recent durable tool records.

        The incremental written-byte digest supplies exact prefix references;
        no transcript scan, duplicate body, or authorization is cached here.
        Reload/reset retire the locators. Consumers must recheck source/scope.
        """
        self._check_disk()
        return self._tool_evidence_epoch, len(self._records), tuple(self._tool_evidence)

    def complete_receipt_evidence(self, references, *, visible):
        """Include an available exact paired result when citing its request.

        Host tool_ref/name/scope establish the pair, never language similarity.
        A result can be a failure or an interruption; inclusion proves neither
        business success nor that the model understood the result.
        """
        _, _, entries = self.tool_evidence_snapshot()
        out = list(references)
        for _, call, ref in entries:
            if ref not in references or call.kind != 'function_call' or not visible(call):
                continue
            matches = [(row, target) for _, row, target in entries
                       if row.kind == 'function_result' and row.tool_ref == call.tool_ref
                       and row.name == call.name and row.scope == call.scope and visible(row)]
            if len(matches) > 1:
                raise ValueError('ambiguous tool result; read original evidence again')
            if matches and matches[0][1] not in out:
                out.append(matches[0][1])
        if len(out) > 16:
            raise ValueError('paired result evidence exceeds 16 references; narrow the evidence set')
        return out

    def outbound(self, head: Sequence[Mapping],
                 tail: Sequence[Mapping] | None = None, *,
                 visible: Callable[[Record], bool] | None = None,
                 compact_history: bool = False, anchor_owner: bool = False) -> list[Mapping]:
        """**出请求时的那张表** —— 持久历史（**渲染成线格式**）＋ 合成开头，
        开头插在**连续 `system` 之后**。

        ⚠️ **位置不是"第 0 位"，是"连续 system 之后"**（`loop.ts:409-416`）：system 前缀
           本身要**逐字固定**才能命中缓存（`head.py:6-7`），而合成开头是**紧跟着它**的那几句
           ⇒ 插在 `system` 之前 ⇒ **每次请求的前缀都不一样** ⇒ 缓存全废（那是钱与延迟）。
           两个 system 连着时也只有那一段**开头连续的**算前缀 —— 历史中间再出现
           `role == "system"` 那是**历史的一部分**，不参与这个计数。

        ⚠️ **交出去的是线格式**（裁定 H）：它**就是"交给引擎的那一份"**
           （`generate(cfg, messages, …)` ⇒ `build_body`，`cli.py:36` 那个形状）。
           交一张**混合形状**（`Record` ＋ 映射）的表，等于让每一个消费者都**同时**知道
           两种形状**以及**怎么渲染它们 —— 而那是**本模块**的知识（`_render_message`）。
           `Record` 那一半因此走 `_render_message`（本模块唯一那处线格式）。

        ⚠️ **而 `head` 由调用方渲染好递进来，本模块不渲染它**：`head.HeadRecord` 的字段形状
           **不是会话的**（模块 docstring 那句"不认识总线／模型／循环"）—— 想去渲染它就得
           认识它。⇒ 它**原样**插进去（同一个对象，不复制、不改写），两边拼成齐整的一份。
           ⚠️ 这不是"少做了一步"：`head` 那几句是缓存的一部分，**逐字相同**才有效。

        ⚠️ **它是个纯操作**（`spec §六③`：一个 list 插进另一个）：**不落盘、不进内存、
           不改调用方递进来的那个 list**。合成开头**落盘就会被改**，一改就废缓存
           （`head.py:6-7`）—— 所以 `head` 那几句只在这张表里出现，`records()` 里永远没有。
        ⚠️ **每次调用都是**新的 list（`head` 与内部的 `records` 都不被就地改）——
           调用方事后 `.append()` 一条不会拐进这个会话里。
        """
        # **成对现造一个 id**（2026-10-05）：会话那两条（调用 ＋ 结果）**成对**写 ，
        # 而配对键按 Q2 丙**不进会话** ⇒ 这里**按次序数**：第 n 对叫 `call_n`
        # （引擎**只要求两边同一个 id** ⇒ 现造的一对就对得上 实测过 ）。
        # ⚠️ **只数那两条**：别的 kind（`message` ／ `handoff` ／ …）不占号 。
        original_head = head
        rendered: list[dict[str, Any]] = []
        pairs = 0
        pending: list[tuple[str, str]] = []
        references: dict[str, tuple[str, str]] = {}
        tool_phase: str | None = None
        replace_persona = bool(head and isinstance(head[0], ActivePersona))
        closed = 0
        batch_boundary = 0
        source_batch = 0
        from .batch_prompt import BatchTail
        context_record = (tail.incoming.record if (compact_history or anchor_owner) and isinstance(tail, BatchTail)
                          else None)
        context_position = None
        dated = tail.dated_observations() if context_record is not None else None
        if anchor_owner and dated is None:
            context_record = None
        past_observations = []
        observation_owners = {}
        if compact_history:
            closed = next((i for i in range(len(self._records), 0, -1)
                           if self._records[i-1].kind == "ui_batch_end"), 0)
            batch_boundary = next((i for i in range(len(self._records), closed, -1)
                                   if self._records[i-1].kind == "ui_batch"), closed)
        for position, one in enumerate(self._records, 1):
            if one.kind == "ui_batch":
                source_batch = position
                continue
            if one.kind in ("ui_batch_end", "ui_handoff_note"):
                source_batch = 0
                continue
            if replace_persona and one.is_head and one.role == "system":
                continue
            if visible is not None and not visible(one):
                continue
            # Compact only the request view, after audience and branch filtering.
            # A current tool receipt remains intact for every hop of its batch.
            if compact_history and (position < closed or one.kind == "handoff_note") and _excerptable(one):
                reference = (self._qq_excerpt_refs or {}).get(position)
                if reference:
                    one = replace(one, text=_qq_history_excerpt(one, reference))
            if one.kind in ("function_call", "function_result"):
                if one.kind == "function_call":
                    # A new assistant-call group cannot consume unpaid calls
                    # from an earlier group, even when the tool name repeats.
                    if tool_phase != "calls":
                        pending.clear()
                    tool_phase = "calls"
                    pairs += 1
                    call_id = f"call_{pairs}"
                    pending.append((one.name, call_id))
                    if one.tool_ref:
                        if one.tool_ref in references:
                            raise ValueError('duplicate durable tool reference')
                        references[one.tool_ref] = (one.name, call_id)
                else:
                    # All calls are recorded before their receipts; the latest id
                    # is not the id of every result. Match names, FIFO for repeats.
                    if one.tool_ref:
                        match = references.pop(one.tool_ref, None)
                        if match is None or match[0] != one.name:
                            raise ValueError('foreign or mismatched durable tool reference')
                        index = next((i for i, pair in enumerate(pending)
                                      if pair == match), None)
                    else:
                        index = next((i for i, (name, _) in enumerate(pending)
                                      if name == one.name), None)
                    if index is None:
                        raise ValueError("tool result has no matching visible call")
                    _, call_id = pending.pop(index)
                    tool_phase = "results"
                item = _render_message(one, call_id=call_id)
                if one.kind == "function_result":
                    if batch_boundary and position > batch_boundary:
                        item["output"] = CurrentBatchOutput(one.text, scope=one.scope, name=one.name, batch=source_batch)
                        # Eligible Chat adapters can place a fresh locator after
                        # its visible result. This identity never goes on the wire.
                        if context_position is not None:
                            item["output"].record = one
                            item["output"].wire_call_id = call_id
                    elif source_batch and one.scope and is_original_receipt(one.name, one.text) and one.text.wire_spans:
                        item["output"] = SourceOutput(one.text, scope=one.scope, name=one.name, batch=source_batch)
                rendered.append(item)
            else:
                pending.clear()
                tool_phase = None
                rendered.append(_render_message(one))
                if one is context_record:
                    context_position = len(rendered)
                elif (dated and context_position is None and one.role == 'user'
                      and one.scope == context_record.scope):
                    snapshot = self._host_observations.get(id(one))
                    if snapshot is not None and snapshot.route == tail.layout_route:
                        past_observations.append((len(rendered), snapshot))
                        if tail.provider_replay:
                            observation_owners[len(rendered)] = one
        if context_position is not None:
            batch_tail = tail
            incoming_row = rendered[context_position - 1]
            if batch_tail.pin_boundary and type(incoming_row.get('content')) is str:
                from .strata_prefix import PrefixBoundary
                incoming_row['content'] = PrefixBoundary(incoming_row['content'])
            first_input = context_position == len(rendered)
            layout = dated[1] if dated else batch_tail
            prompt_context, tail = layout.partition(first_input=first_input)
            # The host policy is independent of a private message or its tools.
            # Put it after the existing head, never ahead of the persona. Only
            # this exact already-visible host row can move across older history.
            from .evidence_contract import EVIDENCE_CONTRACT
            if prompt_context and prompt_context[0] == {'role': 'system', 'content': EVIDENCE_CONTRACT}:
                # The record is visible in the current owner's scoped projection.
                # Revoked recall, a different branch or a lost input keeps the full policy.
                if batch_tail.private_rule is not None:
                    prompt_context[0] = {'role': 'system', 'content': batch_tail.private_rule}
                if head and head[0].get('role') == 'system':
                    head = [*head, prompt_context[0]]
                    prompt_context = prompt_context[1:]
            if batch_tail.provider_replay:
                from .batch_prompt import AnchoredHost
            if dated:
                past, observed, updates = dated[0].project(
                    past_observations, batch_tail.incoming, self._host_observations,
                    first_input=first_input)
                prompt_context = [*observed, *prompt_context]
                tail = [*tail, *updates]
                if batch_tail.provider_replay:
                    prompt_context = [AnchoredHost(row['content'], context_record) for row in prompt_context]
                    past = {pos: [AnchoredHost(row['content'], observation_owners[pos]) for row in block]
                            for pos, block in past.items()}
                rendered[context_position:context_position] = prompt_context
                if past:
                    rows = []
                    for position, row in enumerate(rendered, 1):
                        rows.append(row)
                        if position in past:
                            rows.extend(past[position])
                    rendered = rows
            else:
                if batch_tail.provider_replay:
                    prompt_context = [AnchoredHost(row['content'], context_record) for row in prompt_context]
                rendered[context_position:context_position] = prompt_context
        after = 0
        while after < len(rendered) and rendered[after].get("role") == "system":
            after += 1
        # 2026-10-11（尾巴缝）：`tail` 接在**整段历史的最后**。
        # 为什么：`head` 那一段要**逐字固定**才命中缓存（见上面那段），
        # 而"每轮都会变的那几句"（现在是几点 ／ 出口那几行 …）从前就装在 head 里
        # ⇒ 每轮一改写，引擎就得从很靠前的地方重来。放到尾巴上，
        # 前缀就只剩"它那本底 ＋ 只增不改的历史" —— 那一段才是能被整段复用的。
        # ⚠️ 它照旧是**纯操作**：不落盘、不进 `records()`、不改调用方递进来的表。
        if replace_persona:
            rows = [head[0], *rendered[:after], *head[1:], *rendered[after:], *(tail or ())]
        else:
            rows = [*rendered[:after], *head, *rendered[after:], *(tail or ())]
        if not dated or context_position is None:
            return rows
        from .host_observations import ObservationRequest
        from .input_budget import hard_input_tokens
        from .media_budget import estimate_request_tokens
        cfg = batch_tail.observation_config
        snapshot = dated[0].now if first_input and batch_tail.remember else None
        if cfg is None:
            return ObservationRequest(rows, self, batch_tail.incoming, None, snapshot)
        estimated = estimate_request_tokens(rows, cfg)
        hard = hard_input_tokens(getattr(cfg, 'context_window', None), getattr(cfg, 'max_tokens', 0), cfg.base_url)
        if past_observations and hard is not None and estimated > hard:
            # Old observations are a derived optimization. Shed these before a
            # first-round send could exceed the window. Keep the same recipient
            # predicate and the original fresh facts, clock, guards and evidence.
            snapshot = None
            compact = batch_tail.current_observations_only()
            rows = self.outbound(original_head, compact, visible=visible, compact_history=compact_history)
            estimated = estimate_request_tokens(rows, cfg)
        return ObservationRequest(rows, self, batch_tail.incoming, estimated, snapshot)

    def admitted_suffix_tokens(self, boundary, scope):
        """Numerical planning cost of new records in the admitted private room."""
        from .tokens import REQUEST_BASE_TOKENS
        records = [_render_message(row, call_id='call') for row in self._records[boundary:]
                   if row.scope == scope and row.kind not in ('ui_batch', 'ui_batch_end', 'ui_handoff_note')]
        return estimate_messages_tokens(records) - REQUEST_BASE_TOKENS if records else 0

    def est_tokens(self) -> int:
        """**这张表有多少 token** —— 与 `tokens.estimate_messages_tokens` **同一口径**。

        ⚠️ **它量的是"存档那一份"**（裁定 I）—— `records()` 里那些，**不含合成开头**。
           ⚠️ 原来那句写的是"引擎会看到的那一份"，而它**与下一条打架**：合成开头在
           `outbound` 那一侧、每次现取 ⇒ 那句话把插进去的那几句也算进来了，而这里量不到。
           ⇒ **先渲染再量**：那个函数收的是 mapping（`{"role", "content"}`，`cli.py:36`
           有先例），而不是 `Record` ⇒ 这一步渲染就在 `_render_message` 里（**只有那一处**）；
           直接递 `Record` 会当场 `AttributeError`（它读 `m.get("content")`）。

        ⚠️ **含实测出来的固定开销**（基座 ＋ 每条消息的包装）：**连空会话都是 0** ——
           那一笔在 `tokens` 那一侧按消息条数收，会话这边**不加码也不打折**
           （零条消息的请求不存在，实测引擎会拒 `input: []`）。
        ⚠️ **消费者**：`handoff` 的软阈 `estimate`（`spec §六②`）—— 接线那一层把它递进去。
           它多算少算都会让"该交接了"偏，所以判据就是**与那个模块同一个数**。
        ⚠️ **合成开头不在这笔账里**：它在 `outbound` 那一侧、每次现取 ⇒
           要量"连开头一起"的那一份，得把 `outbound()` 的结果自己渲染了量。
        """
        return estimate_messages_tokens(
            [_render_message(r) for r in self._records
             if r.kind not in ("ui_batch", "ui_batch_end", "ui_handoff_note")])
