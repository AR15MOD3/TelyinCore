# telyincore/events.py
"""事件信封（14 个字段）与事件库（S3）：`EventEnvelope` ＋ `EventStore`。

信封包含 14 个字段，其中 dedupKey 用于去重（见 `03-事件与事件库.md`）。

⚠️ **`meta` 是不透明的** —— `channel` / `audience` 都在里面，而核心**不读它的键**
（`01-主体边界.md` 纪律 3：核心不知道群 / 好友 / 消息 id）。

⚠️ **`EventStore` 就是 `03` 那句「一个库，不是多本册子」的落地**（Task 4 兑现了
本文件顶上原来那句承诺）：`root` 是一个目录，按天分片只是它的**物理切法** ——
而**游标跨分片单调** ⇒「取一段」永远是**一个库上的区间**，不是"去翻某一本册子"。
物理那一半（补尾、一行一个对象）在 `log.py`，两层不重叠。
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import tempfile
from collections.abc import Callable
from contextlib import closing, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .log import LogStore, RepairReport, TornShardError
from .seal import Gate, SealedError, always_open
from .source_guard import lease
from .file_signature import signature
from .snapshot_json import loads as load_snapshot_json

#: 盘上的键名是驼峰⇒ Python 侧下划线，
#: 转换只发生在这里一处。
#:
#: ⚠️ **只有这一张表**：写侧（`_as_wire`）与读侧（`from_line`）都从它取 ——
#: 读侧走的是「遍历已知的下划线名 → 查它对应的线上键」，**不需要反查表**。
#: 早先这里还派生了 `_FROM_WIRE`，**没人读** ⇒ 已删（任务 3 fix round 1）：
#: 这个项目栽过死代码 —— 一份过时的副本让变异打到它身上，看着像"测试没牙齿"、
#: 其实是**探针打偏**，而整整一轮的结论因此错了。
_TO_WIRE = {
    "context_delivery": "contextDelivery",
    "sender_key": "senderKey",
    "dedup_key": "dedupKey",
}

_REQUIRED = ("cursor", "type", "ts", "source", "origin", "text")
_OPTIONAL = ("run", "tags", "context_delivery", "sender_key",
             "meta", "blobs", "ephemeral", "dedup_key")

#: `append` 的入参：不带 `cursor`（它由 store 分配）。
DRAFT_FIELDS = frozenset(("type", "ts", "source", "origin", "text") + _OPTIONAL)


def _not_str(value: Any) -> str | None:
    # ⚠️ 只报类型名，不 `!r` 那个值：payload 可能很大，错误消息不该把它抄一遍。
    return None if isinstance(value, str) else f"must be a str, got {type(value).__name__}"


def _not_positive_int(value: Any) -> str | None:
    # ⚠️ `bool` 要单独挡：`True` 是 `int` 的实例，而 `cursor=True` 进得了字典、也排得了序。
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return None
    return f"must be a positive int, got {value!r}"


def _not_array(value: Any) -> str | None:
    if isinstance(value, (list, tuple)):
        return None
    return f"must be an array, got {type(value).__name__}"


def _not_hashable(value: Any) -> str | None:
    try:
        hash(value)
    except TypeError:
        return f"must be hashable, got {type(value).__name__}"
    return None


#: 字段级判据的**唯一真相源**：`(字段名, 判据)`，判据返回"违规的说明"（合规 ⇒ `None`）。
#:
#: ⚠️ **写侧（`_as_wire`）与读侧（`from_line`）跑的是同一个循环、同一张表。**
#: 上一轮的 `TYPE_CHECKED` 只统一了**名字**（`text` / `ts`），谓词还是在两边各写一遍；
#: 这一轮连谓词一起收进来，**并把写侧也拉上** ⇒ 两侧不可能对同一个形状给两个答案 ——
#: 而这个项目已经栽过那种"一句话说的是三样、代码里其实只有两样"的账。
#: 它**替掉了** `TYPE_CHECKED`（同一个机制，只是长全了）：`cursor` 原先在写读两侧
#: 各手写了一遍的那份重复，也就此收进表里（W1 那一次 fix round 判的）。
#:
#: 判据只有一条：**"一个错类型会不会炸掉一个读者"**（不是"字段注解声明的是什么类型"）：
#: · `cursor` ⇒ `_scan` 的字典键与 `range()` 的 `sorted()`；
#: · `text` / `ts` ⇒ `grep` 的 `.lower()` 与 `_matches` 的比较；
#: · `tags` / `blobs` 的形状 ⇒ `tuple(5)` 在 `from_line` 里抛 `TypeError`（**启动**那一个读者）；
#: · `dedup_key` 能不能进字典 ⇒ `_scan` 的 `setdefault` 抛 `TypeError`（同上）。
#: ⚠️ 而 `type` / `source` / `origin` 不是 `str` 时**没有任何读路径会炸**（只做相等比较）
#: ⇒ 那几样**不在这张表里**、照单全收（写在 `append` 的 docstring 上，并有测试钉着）。
#:
#: ⚠️ **写侧为什么也得跑**：`append` 写得出**一行自己读不回来的行**（W1，实测）——
#: `append({"tags": 5})` 一路成功、落盘，而重启时那行被 `from_line` 当坏行丢掉 ⇒
#: 它的游标**没进高水位** ⇒ 下一次 `append` 复用那个号 ⇒ 盘上两行同一个游标。
#: 「dataclass 会替我们拦住它」是**假的**：dataclass **不验类型**，`tuple(5)` 那一步
#: 在写侧**根本不存在**。这张表就是那道守卫。
FIELD_RULES: tuple[tuple[str, Callable[[Any], str | None]], ...] = (
    ("cursor", _not_positive_int),
    ("text", _not_str),
    ("ts", _not_str),
    ("tags", _not_array),
    ("blobs", _not_array),
    ("dedup_key", _not_hashable),
)


def _first_violation(values: Mapping[str, Any]) -> str | None:
    """跑那张表，返回**第一条**违规的说明（都合规 ⇒ `None`）。

    ⚠️ `values` 是**将要落盘 / 刚从盘上读出来**的那些字段值：规则**按表里的顺序**跑，
    所以"先报哪一条"是确定的（`cursor` → `text` → `ts` → 形状）。
    ⚠️ **缺席的与显式 `null` 的选填字段不进 `values`**（两侧对"没写"是同一个意思）；
    而必填那几样**一定在里面** —— `cursor` 尤其不能漏：一个 `"cursor":null` 放过去，
    `_scan` 会拿 `None` 当字典键，`range()` 的 `sorted()` 随后就炸。
    """
    for name, rule in FIELD_RULES:
        reason = rule(values[name]) if name in values else None
        if reason is not None:
            return f"{name} {reason}"
    return None


class EventError(ValueError):
    """事件行不合法（坏 JSON / 缺字段 / 游标不对）。

    ⚠️ 调用方**只捕它** —— 不要让 `json.JSONDecodeError` 或 `KeyError` 逃出去。
    """


@dataclass(frozen=True)
class EventEnvelope:
    cursor: int
    type: str
    ts: str
    source: str
    origin: str
    text: str
    run: str | None = None
    tags: tuple[str, ...] | None = None
    context_delivery: str | None = None
    sender_key: str | None = None
    meta: Mapping[str, Any] | None = None
    blobs: tuple[str, ...] | None = None
    ephemeral: bool | None = None
    dedup_key: str | None = None

    def __post_init__(self) -> None:
        from .frozen_json import freeze
        try:
            object.__setattr__(self, 'meta', freeze(self.meta))
        except (ValueError, RecursionError) as error:
            raise EventError('invalid event metadata structure') from error
        for name in ('tags', 'blobs'):
            value = getattr(self, name)
            if isinstance(value, list):
                object.__setattr__(self, name, freeze(value))

    def _as_wire(self) -> dict[str, Any]:
        # ⚠️ **先把"将以什么形状落盘"算出来，再判**（判据必须跑在**转换之后**）：
        # `json.dumps` 自己就会把 tuple 写成 list（`("a",)` ⇒ `["a"]`）⇒ 拿转换前的元组去判，
        # 等于"以为它是个 hashable 的元组，而盘上躺着一个 list"（`dedup_key` 那条正是这样）。
        written: dict[str, Any] = {name: getattr(self, name) for name in _REQUIRED}
        for name in _OPTIONAL:
            value = getattr(self, name)
            if value is None:
                continue                      # ⚠️ 缺省字段**不写**，不是写 null
            written[name] = list(value) if isinstance(value, tuple) else value
        # ⚠️ **与读侧 `from_line` 同一个循环、同一张表**（`FIELD_RULES`）—— 这一句是 W1 的落点：
        # 写侧从前只验 `cursor` / `text` / `ts`，而 **dataclass 不验类型** ⇒
        # `append({"tags": 5})` 写得出**一行自己读不回来的行**（重启时被当坏行丢掉，
        # 游标没进高水位 ⇒ 下次 `append` 复用那个号）。**一个库不该写出它自己读不回来的行**
        # ⇒ 拒在写之前：盘上没痕迹，游标及磁盘快照索引都没动。
        violation = _first_violation(written)
        if violation is not None:
            raise EventError(violation)
        out: dict[str, Any] = {name: written[name] for name in _REQUIRED}
        for name in _OPTIONAL:
            if name in written:
                out[_TO_WIRE.get(name, name)] = written[name]
        return out

    def to_line(self) -> str:
        """一行 JSON —— 不缩进、不换行、中文原样（不转 `\\uXXXX`）。"""
        return json.dumps(self._as_wire(), ensure_ascii=False, separators=(",", ":"))

    def without_cursor(self) -> dict[str, Any]:
        d = self._as_wire()
        d.pop("cursor", None)
        return d

    @classmethod
    def from_line(cls, line: str) -> EventEnvelope:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EventError(f"not json: {line[:80]!r}") from exc
        if not isinstance(raw, dict):
            raise EventError(f"not an object: {line[:80]!r}")
        for key in _REQUIRED:
            if key not in raw:
                raise EventError(f"missing {key!r}: {line[:80]!r}")
        # ⚠️ **判据与写侧 `_as_wire` 是同一个循环、同一张表**（`FIELD_RULES`）——
        # 这里交出去的是「这一行**在盘上是什么形状**」：
        # · 必填那几样一律交（`null` 也是一个要判的值）——`cursor` 尤其不能漏：
        #   一个 `"cursor":null` 放过去，`_scan` 会拿 `None` 当字典键、`range()` 的
        #   `sorted()` 随后就炸；
        # · 选填那几样只交**存在且不是 `null`** 的（缺席与显式 `null` 在两侧都是"没写"）。
        # ⚠️ **而这不是写侧那处的重复劳动**：盘上**已经有**的行（老版本写的 / 手改的 /
        # 别的工具生成的）绕过写侧的守卫 ⇒ 不在这里拦，一条 `"text":null` 就会让
        # **每一次启动之后的每一次 `grep`** 都抛 `AttributeError`（评审实测过）。
        # ⇒ 拦在这里的后果是**已知且有意的**：这一行被当成坏行跳掉（`_scan` 那条路径），
        # 而"坏行是真的静默"这条边界照旧（本层没有日志设施）。
        values: dict[str, Any] = {name: raw[name] for name in _REQUIRED}
        for name in _OPTIONAL:
            wire = _TO_WIRE.get(name, name)
            if wire in raw and raw[wire] is not None:
                values[name] = raw[wire]
        violation = _first_violation(values)
        if violation is not None:
            raise EventError(f"{violation}: {line[:80]!r}")

        kwargs: dict[str, Any] = {k: raw[k] for k in _REQUIRED}
        for name in _OPTIONAL:
            wire = _TO_WIRE.get(name, name)
            if wire in raw:
                kwargs[name] = raw[wire]
        for name in ("tags", "blobs"):
            if kwargs.get(name) is not None:
                kwargs[name] = tuple(kwargs[name])
        return cls(**kwargs)


def _now_iso() -> str:
    """默认时钟：**本地时区**的 ISO 字符串（分片日期取它前 10 个字符）。

    ⚠️ `astimezone()` 那一跳是承重的：分片名 `events-YYYY-MM-DD.jsonl` 说的是
    「**哪一天**」，不是"UTC 的哪一天"。少了它，UTC+8 的凌晨会写进**前一天**的分片。
    """
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class RangeQuery:
    """`range` 的条件：**一个都不给** ⇒ 全库、升序。

    ⚠️ 它与 `GrepQuery` 的**八个过滤字段逐一相同**（名字与类型）——
    那是 `_matches` 能同时收下两者的全部理由（见那里）。
    """

    from_cursor: int | None = None
    to_cursor: int | None = None
    from_ts: str | None = None
    to_ts: str | None = None
    sender_key: str | None = None
    source: str | None = None
    origin: str | None = None
    limit: int | None = None


@dataclass(frozen=True)
class GrepQuery:
    """`grep` 的条件：**关键字 ＋ 上下文条数**，再加**同一组**过滤字段。

    ⚠️ 那八个过滤字段与 `RangeQuery` **逐一相同**（名字与类型）—— 这就是鸭子类型的用法所在；
    这里只多出 `keyword` 与 `context` 两样。
    ⚠️ **`from_cursor` / `to_cursor` 两样不是"顺便加的"**：`_matches` 对**每个**查询
    都会读它们（它对两个类型一视同仁）⇒ 少写一个，`grep` 就是**每条都跑不动**
    （`AttributeError`），而不是"某个边界没覆盖"。
    `test_the_two_query_dataclasses_share_the_same_filter_fields` 钉着这一条。
    """

    keyword: str
    context: int
    from_cursor: int | None = None
    to_cursor: int | None = None
    from_ts: str | None = None
    to_ts: str | None = None
    sender_key: str | None = None
    source: str | None = None
    origin: str | None = None
    limit: int | None = None


@dataclass(frozen=True)
class GrepHit:
    """一个命中：**它自己的游标** ＋ **它周围实际发生的那一段**（含它自己）。

    ⚠️ `events` 是**上下文**，**不是**"符合筛选条件的那些" —— 邻居不过滤（见 `grep`）。
    """

    hit_cursor: int
    events: tuple[EventEnvelope, ...]


class EventStore:
    """一个目录下的事件库 —— **一个库，不是多本册子**（`03`）。

    分片按天（`log.py` 的 `events-YYYY-MM-DD.jsonl`），而**游标跨分片全局单调**：
    `__init__` 扫出**所有**分片里的最大游标，`next = max + 1`。

    ⚠️ **「启动时」的那一脚在这里，不在 `LogStore.__init__` 里**（①）：
    顺序是 `repair_all()` → `_scan()`。物理层刻意不在自己的构造里修尾 ——
    它只管物理、不认"启动"是哪个时刻 ⇒ 时机由库落，判据留在物理层。

    ⚠️ **而 `repair_all()` 修不动一片时只跳过那一片**（Windows 上被别的进程占着 /
    只读）—— 那是它**有意**的宽容：一片坏不该让整条启动失败。⇒ **这里不再包一层**（②）：
    把它包成"启动失败"，就是把那份宽容原样抹掉。

    ⚠️ **同一条宽容在小弟身上也要成立**（Fix round 1–2）：`_scan` 遇到**读不出来的片**
    **同样跳过它、其余照读** —— 否则 ② 的宽容在**下一步**就白做了
    （`repair_all()` 刚跳过一片，`_scan` 又把整条启动炸掉）。
    ⚠️ **"读不出来"是两个面，不是一个**：**解不开 UTF-8**（`UnicodeDecodeError`）与
    **根本打不开**（`OSError` 那一族：只读 / 独占句柄 / 名字像分片的**目录**）。
    ⇒ 而"跳过了什么"**必须能看见**：记进 `self.skipped`（见 `_scan`）。

    ⚠️ **而"跳过了一片"不等于"往它上面追加是安全的"**（Critical 1，fix wave）：
    跳过只说明**这一趟没修**。往一截没写完的行后面追加会把新事件**粘**成一条谁也解不开的行，
    而那条行**以 `\\n` 收尾** ⇒ `repair_tail` 的判据**再也看不见它** ⇒
    那条事件在**下一次启动**时消失。⇒ 写侧**当场拒**（`append_line` 的 `TornShardError`，
    在这里包成 `EventError`）—— 而"哪一片没修成"由 `self.repairs` 说出来。
    """

    def __init__(self, root: Path, *, clock: Callable[[], str] = _now_iso,
                 is_open: Gate = always_open, read_only: bool = False, _read_image=None) -> None:
        if type(read_only) is not bool:
            raise TypeError('read_only must be bool')
        if _read_image is not None and not read_only:
            raise ValueError('restart images are only for read-only event readers')
        self._read_only = read_only
        self.log = LogStore(Path(root), create=not read_only)
        # ⚠️ **默认门卫是 `seal.always_open` —— 一个活陷阱，而这是刻意的、必须点名**：
        #    「没有门卫」与「门卫恒开」在**签名上长得一样** ⇒ 一个本该被封的写会悄悄过去。
        #    它不做成必填，是为了那 100+ 条**不要门卫**的既有构造（它们测的是"写进去没有"，
        #    不是关机）⇒ 生产路径**必须显式递一个真的**（由 `app.build()` 造）。
        self._is_open = is_open
        self._clock = clock                    # 只用来算分片日期（`day`），不写进事件
        self._listeners: list[Callable[[EventEnvelope], None]] = []
        # String and historical numeric dedup identities live in the disk
        # snapshot, preserving Python equality (1 == True == 1.0).
        from .event_snapshot import EventSnapshot
        self._by_cursor = EventSnapshot(EventEnvelope.from_line)
        # Exact shard membership and counts live in the disposable disk snapshot.
        self._next = 1
        # ⚠️ **启动时读不出来、被跳过的那些分片**（**按名字升序的只读元组**）。
        # 本层**没有日志设施**，而"跳过了什么"**必须能看见** ⇒ 这就是那个唯一的通道。
        # ⚠️ 它记的是**事实**，不是建议："这一片读不出来"（`_scan`，两个面见那里）。
        # ⚠️ **一个目录也装得下** —— 记的是 `Path`，记下来**不需要它是文件**：
        # 别让"跳过"因为**记不下来**而变成静默（Fix round 2 点名核过的那条）。
        # ⚠️ 而它**不会被 `clear()` 碰**：`clear()` 只认 磁盘快照里的分片成员，
        # 进入快照的前提是**至少读出一条游标** ⇒ 读不出来的片永远不在里面
        # （⇒ `unlink` 也不会落到一个目录上）。
        # ⚠️ **坏行不进它**（那条边界仍写着"真的静默"）：坏行只丢那一行、这一片照样可读，
        # 而整片跳掉是**一整天的历史看不见** —— 量级差一档，且后者一个路径就记得下。
        # The complete tuple remains available on demand; internal diagnostics
        # live in the private snapshot instead of one retained Path per shard.

        # ⚠️ **`repair_all()` 的返回值不再丢掉**（fix wave 推翻了原先那条「没人看得见 ⇒ S9 的活」）：
        # 一张**只含修过的片**的表**表达不了"试过而失败"** ⇒ S9 永远报不出那句注释承诺
        # 它会报的东西，而那份沉默的代价已经量出来了 —— **一条静默丢失的事件**（见类 docstring）。
        # ⇒ 现在这里是 `RepairReport`：**修过的那些**（照 `dict` 用）＋ **没修成的那些**（`.failed`）。
        # ⚠️ 它**不是**"跳过的那一片还能不能追加"的答案 —— 那个答案由写侧的守卫给（当场拒）。
        try:
            with (nullcontext() if read_only else lease(self.log.root)):
                highwater = self._read_highwater()  # Refuse corrupt authority before tail repair.
                if not read_only and is_open():
                    self.log.repair_all(_record=self._by_cursor.add_repair)
                restored = _read_image(self._by_cursor) if _read_image is not None else None
                if restored is None:self._scan()
                else:self._next = restored
                self._next = max(self._next, highwater + 1)
                self._write_failed = False
                self._disk_state = self._disk_signature()
        except BaseException:
            self._by_cursor.close()
            raise

    @property
    def repairs(self) -> RepairReport:
        # Materialize only on explicit access, retaining the mutable public
        # object and its identity for callers that annotate or keep the report.
        if '_repairs_report' not in self.__dict__:
            self._repairs_report = self._by_cursor.repair_report()
        return self._repairs_report

    @repairs.setter
    def repairs(self, report):
        self._repairs_report = report

    @property
    def skipped(self) -> tuple[Path, ...]:
        return self._by_cursor.skipped_paths()

    @property
    def _highwater_path(self):
        return self.log.root / 'cursor-highwater.json'

    def _read_highwater(self):
        path = self._highwater_path
        # A dangling link is invalid authority too, not an absent watermark.
        if path.is_symlink():
            raise EventError('invalid event cursor highwater')
        try:
            with path.open('rb') as stream:
                raw = stream.read(257)
        except FileNotFoundError:
            return 0
        if len(raw) > 256:
            raise EventError('invalid event cursor highwater')
        try:
            # Never select the last of two competing cursor/version fields.
            # Enforce size on the actual read, not a prior, changeable stat.
            data = load_snapshot_json(raw)
        except (ValueError, UnicodeError) as error:
            raise EventError('invalid event cursor highwater') from error
        if (type(data) is not dict or set(data) != {'version', 'cursor'}
                or type(data['version']) is not int or data['version'] != 1
                or type(data['cursor']) is not int or data['cursor'] < 0):
            raise EventError('invalid event cursor highwater')
        return data['cursor']

    def _save_highwater(self, cursor):
        if self._read_highwater() >= cursor:
            return
        self.log.root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.log.root, prefix='.cursor-')
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(json.dumps({'version': 1, 'cursor': cursor}).encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self._highwater_path)
        finally:
            Path(name).unlink(missing_ok=True)

    def _disk_signature(self):
        # Include actual change time as well as identity/size/mtime: ordinary
        # same-size edits can restore mtime. Windows st_ctime is creation time,
        # so the shared helper reads native ChangeTime and file identity there.
        # This still does not defend against forged filesystem metadata.
        digest = hashlib.sha256()
        with closing(self.log.iter_day_files(strict=self._read_only)) as paths:
            for path in paths:
                try:
                    stamp = signature(path)
                except OSError as error:
                    if not self._by_cursor.has_skipped(path):
                        raise EventError('event shard identity unreadable; reopen before mutation') from error
                    # Keep the established startup diagnostics for already skipped
                    # directories/locked shards. This marker cannot equal a readable
                    # signature: restored access forces a new content comparison.
                    info = path.stat()
                    stamp = ('skipped', info.st_dev, info.st_ino, info.st_size,
                             info.st_mtime_ns, info.st_ctime_ns)
                # ASCII JSON frames paths (including legacy surrogate names) and
                # metadata unambiguously, without retaining one tuple per shard.
                wire = json.dumps((str(path), stamp), ensure_ascii=True, separators=(',', ':'))
                digest.update(wire.encode('ascii') + b'\n')
        return digest.digest()

    def _check_disk(self, *, force=False):
        if self._write_failed:
            raise EventError('previous event write outcome uncertain; reopen before mutation')
        if self._read_highwater() > self._next - 1:
            raise EventError('event cursor changed by another writer; reopen before mutation')
        if force or self._disk_signature() != self._disk_state:
            # Preserve the existing tolerance for complete invalid lines, but
            # never adopt another writer's valid event/cursor under a stale view.
            probe = object.__new__(EventStore)
            probe.__dict__ = dict(self.__dict__)
            from .event_snapshot import EventSnapshot
            probe._by_cursor = EventSnapshot(EventEnvelope.from_line)
            probe._next = 1
            try:
                probe._scan()
                if (probe._by_cursor != self._by_cursor
                        or probe._by_cursor.skipped_fingerprint() != self._by_cursor.skipped_fingerprint()):
                    self._write_failed = True
                    raise EventError('event store changed by another writer; reopen before mutation')
                self._disk_state = self._disk_signature()
            finally:
                probe._by_cursor.close()

    # ── 启动扫描 ────────────────────────────────────────────────
    def _scan(self) -> None:
        """逐行建立磁盘快照；分片计数也在磁盘，内存只保留有界热点缓存。

        ⚠️ **`except EventError` 捕到哪几样、捕不到哪一类（③）**：
        捕到的是**解码 ＋ 字段判据**那一层的坏行 —— **坏 JSON / 不是对象 / 缺必填 /
        不合 `FIELD_RULES` 那张表的字段**（`from_line` 里那几处检查）：
        `cursor` 不是正整数、`text`·`ts` 不是 `str`（fix wave 加：它们各自能让一条查询
        **永久**抛）、`tags` / `blobs` 不是数组、`dedupKey` 进不了字典（同一次 fix round 补）。
        不合法的 `tags` / `blobs` / `dedupKey` 形状由 `from_line` 的字段判据拒绝，
        合法的字符串与数字去重身份存入磁盘快照；不再逐条保留 Python 去重字典。
        ⚠️ **而这几样此后 `append` 自己也拒它**（W1）：写侧与读侧跑的是**同一张表** ⇒
        **这个库写不出自己读不回来的行**。⇒ 这一组现在只对**已经躺在盘上**的行有用
        （老版本写的 / 手改的 / 别的工具生成的）—— 与 `text` / `ts` 那一组同一个处境。
        **捕不到"字段名错了"那一类**，因为
        `from_line` 是**投影**：它遍历**已知的**下划线名、去线上表里取那个键 ⇒
        · 一个**大小写 / 拼写不对的选填键**（线上写成 `dedupkey`）**一声不响**：
          它既不在必填里、也不在已知的选填名里 ⇒ **静默丢掉**，而这一行照样算"好"的；
        · 一个**未知键**（`{"nope": 1}`）同理 —— 投影根本不知道它存在。
        ⇒ 所以"跳过一行"**不等于**"所有坏行都被拦在这儿了"：剩下的那半张脸是
        **一条看起来完好的行，只是少了一个键**（要抓它得比对**键的集合**，不是解码 ——
        `tests/test_events.py` 那条「字段表与 dataclass 分家」钉的就是这个面）。

        ⚠️ **而跳过是真的静默**：本层**没有日志设施**（计划里原先那句"不静默 —— 见日志"
        在写这段时是空话）⇒ 坏行就是被丢掉、没人报。那正是这条边界本身：
        它**不该**让库起不来（一条历史坏行不该毁掉整条启动），
        而"有东西坏了"这件事**得由更上一层去发现**（S9 的日志，还没写）。

        ⚠️ **读不出来的分片：整片跳过，并记进 `self.skipped`**（Fix round 1–2）——
        与 `repair_all()` 对"动不了的片"的宽容**同源**：**一片坏，不该让库起不来**。
        ⚠️ **而"读不出来"有两个面，缺一个就只补了一半**：
        · **解不开 UTF-8** ⇒ `UnicodeDecodeError`（Fix round 1）；
        · **根本打不开** ⇒ `OSError` 那一族（只读 / 独占句柄 / **名字像分片的目录**，Fix round 2）。
        第二个面**尤其不能漏**：`repair_all()` 正是逐片 `except OSError` 的 ⇒
        漏了它，**② 有意做的那份宽容在下一步就白做**（刚跳过的一片，`_scan` 又炸整条启动）。
        ⚠️ 而它比"坏行"严重一档：坏行只丢一行，**读不出来是整条启动失败**
        （`UnicodeDecodeError` / `OSError` 都不是 `EventError`，没人接着它们 ⇒ `__init__` 起不来）。
        ⚠️ 跳的是**整片**，不是坏的那一行：`read_lines` **逐行解码** ⇒ 坏字节抛在哪一行，
        它**已经读出来的那些也不返回** ⇒ 想只丢那一行，就得在这里**再写第二个读器** ——
        那比"整片跳过"更坏（两个读法迟早分家）。⇒ 整片跳过，而它**必须能被看见**
        （只读元组 `skipped`；⚠️ 坏行**不进**它，理由写在 `__init__` 那段）。
        ⚠️ **`MemoryError` 故意不吞**（与 `repair_all()` 同一条裁定）：`OSError` 说的是
        "**这一片**现在动不了" ⇒ 跳过它是对的；`MemoryError` 说的是"**这台机器**撑不住了"
        ⇒ 接着读下一片只会更糟。而它不是 `OSError` ⇒ 天然不被这一处接住。
        ⚠️ **代价要说清**：跳过的那一片，它的游标**进不了高水位** ⇒ 之后 `append`
        可能发出**和那一片里已经占着的号相同**的游标。而这不是新问题 ——
        "坏行"那条早就有同一类"我们看不见"，差别只在**范围**（一行 vs 一片）；
        当前另存持久高水位，正常追加及 clear 后重启不会仅靠扫描复用游标。
        对缺少高水位的旧库，损坏或不可读分片中的未知游标仍无法凭扫描恢复。
        """
        self._by_cursor.invalid_lines = False
        self._by_cursor.clear_skipped()
        with closing(self.log.iter_day_files(strict=self._read_only)) as paths:
            for path in paths:
                count = 0
                highest = 0
                duplicate_cursor = None
                duplicate_key = None
                try:
                    # A bad UTF-8 shard is skipped as a whole, including any valid
                    # prefix. The snapshot savepoint prevents publishing half a shard.
                    with self._by_cursor.shard():
                        for line in self.log.iter_lines(path):
                            try:
                                event = EventEnvelope.from_line(line)
                            except EventError:
                                self._by_cursor.invalid_lines = True
                                continue
                            key = event.dedup_key
                            if key is not None:
                                previous = self._by_cursor.dedup_cursor(key)
                                if previous is not None and previous != event.cursor:
                                    duplicate_key = (previous, event.cursor)
                            if not self._by_cursor.put(event, retain=False, unique=True, segment=os.path.normcase(path.name)):
                                duplicate_cursor = event.cursor
                            count += 1
                            highest = max(highest, event.cursor)
                except (UnicodeDecodeError, OSError):
                    self._by_cursor.add_skipped(path)
                    continue
                # Only diagnose a fully readable shard. A later UTF-8/read failure
                # rolls back the whole shard, including any conflicting prefix.
                if duplicate_cursor is not None:
                    raise EventError(f'duplicate event cursor {duplicate_cursor} in {path.name}; source unchanged')
                if duplicate_key is not None:
                    raise EventError(f'event dedup identity assigned to cursors {duplicate_key[0]} and {duplicate_key[1]}; source unchanged')
                if count:
                    self._next = max(self._next, highest + 1)

    def _current_day(self) -> str:
        """`append` 此刻会写进哪一天的分片 —— 只取时钟字符串的日期那 10 个字符。"""
        return self._clock()[:10]

    # ── 写 ─────────────────────────────────────────────────────
    def append(self, draft: Mapping[str, Any]) -> EventEnvelope:
        if self._read_only:
            raise SealedError('read-only event store')
        with lease(self.log.root):
            self._check_disk()
            try:
                return self._append(draft)
            finally:
                self._disk_state = self._disk_signature()

    def _append(self, draft: Mapping[str, Any]) -> EventEnvelope:
        """把一份草稿变成事件：分配游标 → 落盘 → 通知订阅者。

        ⚠️ **封了库之后，这里先抛 `SealedError`**（第 ④ 刀的门卫，见 `seal.py`）——
        它是 `RuntimeError` 的子类、**不是** `EventError`：下面那张"拒绝面"讲的是
        「这份草稿本身合不合法」，而封库讲的是「**现在还不许写**」，两者不是一件事
        ⇒ 封着的时候，那一整张单子**整体让位**给这一条。

        ⚠️ **拒绝面：把"哪几条路"点名列出来 —— 不许再用一句形容词"闭合"糊过去。**
        一份草稿要么**变成一个事件**，要么抛 `EventError`；而 `EventError` 覆盖的是
        **下面这几条**（`EventError` 的契约正是「调用方只捕它」）：
        · 未知字段；
        · 缺必填；
        · 草稿里带 `cursor`；
        · **字段不合 `FIELD_RULES` 那张表**（W1 把写侧拉上同一张表，逐条见那里）：
          `text` / `ts` 不是 `str`；`tags` / `blobs` 不是数组（`5` / `true` / `"ab"` / 一个对象）；
          `dedup_key` **落盘之后**进不了字典（`("a",)` 被 `json` 写成 `["a"]` ⇒ `_scan` 抛）。
          理由一句话：**这几样写出去，这个库自己就读不回来** —— 见 `_as_wire` 里那一段；
        · **`text` 写不出去**（孤立代理对 ⇒ `UnicodeEncodeError`，裁定 ④）；
        · **目标分片不以换行收尾**（一截没修掉的半行 ⇒ `TornShardError`，见下面的段落）；
        · **目标分片读不到**（被一个"不共享读"的句柄握着 / 只读 / 独占 ⇒ `OSError`，
          fix round 里补的那一条：`append_line` 的读探针**吞掉**"读不到"之后，
          紧接着的 `open(..., "ab")` 会说出**同一个**答案 —— 那一句必须落在这张单子里）；
        · **序列化不了**（`meta` 里放 `set` / `bytes`，或者**自引用**）← fix wave 补的这一条。
        ⚠️ **原先这里写的是「拒绝面是闭合的」，而它当时就是假的**：
        这一段自己 20 行之后就承认有 `TypeError` ⇒ 一份草稿**可以**从中间漏出去。
        ⇒ 现在的口径是：**上面这几条抛 `EventError`，漏在外面的那些写在下面**。

        ⚠️ **孤立代理对（裁定 ④）：包成 `EventError`。** `to_line()` 对含孤立代理对的
        `text` 是**成功**的（`json.dumps` 照抄那个码位），真炸的地方在
        `log.append_line` 的 `(line + "\\n").encode("utf-8")` —— 抛 `UnicodeEncodeError`，
        **而那不是 `EventError`**。理由三条：
        （a）`append` 已经是「草稿被拒」这**一个**语义的出口（上面两处都抛它）——
            一个写不进去的字符是同一类事：**这份草稿变不成一个持久事件**；
        （b）payload 来自**外部**（模型输出 / 通道传输），正是孤代理对会出现的地方 ⇒
            放它原样出去等于在「调用方只捕 `EventError`」这条契约上开一个洞：
            守卫 `append` 的那一层会在那儿炸，而它没有任何办法预见；
        （c）代价是零：`append_line` **在 `open()` 之前**就编码（Task 1 为"幽灵分片"定的）
            ⇒ 盘上一点痕迹不留、游标也没被烧掉（`self._next` 还没 `+= 1`）、
            快照索引与游标都没动 ⇒ 包一层不需要任何回滚。
        而**原因不丢**：`from exc` 留着它。

        ⚠️ **一截没修掉的半行（Critical 1，fix wave）：也包成 `EventError`。**
        `log.append_line` 现在**拒绝**往一个不以换行收尾的分片里追加（`TornShardError`）——
        因为粘上去的那一条谁也解不开，而它**以 `\\n` 收尾** ⇒ `repair_tail` 的判据
        **再也看不见它** ⇒ 那条事件在**下一次启动**时**静默消失**。
        理由与裁定 ④ **同三条**（它**就是**那三条，只是换了个入口进来）：
        （a）`append` 已经是「草稿被拒」这**一个**语义的出口 ——
            "这一片现在写不进去"是同一类事：**这份草稿变不成一个持久事件**；
        （b）条件来自**外部**（另一个进程崩在写一半 ／ 片被占着 ⇒ 上一趟没修成）⇒
            调用方**无从预见**；
        （c）代价同样是零：拒在写之前 ⇒ 盘上没痕迹，游标及快照索引没动。
        ⚠️ 而"**跳过的那一片为什么没修成**"由 `self.repairs.failed` 说出来（见 `__init__`）——
        那是**启动那一刻**的事实；这条守卫管的是**写的那一刻**。**两者不互相替代。**

        ⚠️ **序列化那一步的失败也包**（`to_line()`，Important 2）：`json.dumps` 对
        `set` / `bytes` 抛 `TypeError`，而对**自引用**抛的是 **`ValueError:
        Circular reference detected`** —— 后者**连 `except EventError` 都接不住**
        （`EventError` 是 `ValueError` 的**子类**，接不住父类，而 `json` 模块不认它）。
        ⇒ 两条都在这里归到一处，`from exc` 留着原因。

        ⚠️ **不包的，两条 —— 已知的、有意的边界**（写下来，不是假装它不存在）：
        · `append_line` 的另一个 `ValueError`（多行 / 空白）—— `json.dumps`
          永不发裸换行 ⇒ 走到那儿说明**本模块的内部不变量坏了**，那该吵，
          不该伪装成"你的草稿不合法"；
        · **字段类型里没被验的那些**：表里只有**六样**（`FIELD_RULES`：`cursor` / `text` / `ts` /
          `tags` / `blobs` / `dedup_key`），其余（`type` / `source` / `origin` …）不是 `str` 时
          **没有读路径会炸**（只做相等比较）⇒ 照单全收。而一条**不可哈希的 `dedup_key`**
          在**查重那一步**抛 `TypeError` —— 那一步在序列化**之前**（比表守卫更早），
          所以它也不在上面那几条里。
        **⇒ 那两条边界由 `test_the_rejection_surface_still_leaves_field_type_errors_out`
        钉住**：谁把话说小、或把代码放宽，两边一起动才看得出来。

        ⚠️ 订阅者抛的异常**不吞**（见下面那段注释），而**通知排在状态全部落定之后**。
        """
        # **写之前先抬头看一眼那张纸条** —— 关着就拦下来（`seal` 的消费者就是这一眼）。
        #   ⚠️ 它是 `append` 的**第一句**：一个"先写了一半才拦"的实现会把半条记录留在盘上，
        #      而"被拦下来"的那一次还会**改了状态却没写盘**（一个最难查的形状）。
        if not self._is_open():
            raise SealedError("事件库已封 —— 这一笔是关机之后才落下来的")
        unknown = set(draft) - DRAFT_FIELDS
        if unknown:
            raise EventError(f"unknown field(s): {sorted(unknown)}")
        missing = (set(_REQUIRED) - {"cursor"}) - set(draft)
        if missing:
            raise EventError(f"missing field(s): {sorted(missing)}")
        if draft.get("dedup_key") is not None:
            key = draft["dedup_key"]
            hash(key)  # Preserve the existing TypeError for unhashable drafts.
            if isinstance(key, float) and math.isnan(key):
                # Distinct live NaNs are distinct dict keys, but JSON decoding
                # makes them the same singleton. Such writes can break reopen.
                raise EventError('dedup_key NaN has no stable persisted identity')
            try:
                seen = self._by_cursor.dedup_cursor(key)
            except (ValueError, OverflowError) as exc:
                # E.g. Python's integer-to-text digit limit: still a rejected
                # draft, just as when ordinary wire serialization rejects it.
                raise EventError('dedup_key has no encodable identity') from exc
            if seen is not None:
                return self._by_cursor[seen]   # ⚠️ 返回**已有的那条**，不是新投递的这份

        e = EventEnvelope(cursor=self._next, **dict(draft))
        try:
            line = e.to_line()
        except EventError:
            raise        # ⚠️ `_as_wire` 自己按 `FIELD_RULES` 的拒绝（六样，W1 之后含形状）
        except (TypeError, ValueError) as exc:
            # ⚠️ 两种类型都要接：不可序列化的 payload 是 `TypeError`，而**自引用**是
            # `ValueError`（`Circular reference detected`）—— 它不是 `EventError`。
            raise EventError(
                f"draft is not serializable: {type(exc).__name__}: {exc}") from exc
        try:
            path = self.log.append_line(line, day=self._current_day())
            self._save_highwater(e.cursor)
        except UnicodeEncodeError as exc:
            raise EventError(
                f"text is not encodable: {exc.reason} (at {exc.start})") from exc
        except TornShardError as exc:
            raise EventError(f"cannot append, the shard is torn: {exc}") from exc
        except OSError as exc:
            # The append or sync may have reached disk before reporting failure.
            # Retain uncertainty; never reuse its cursor in this live instance.
            self._write_failed = True
            # ⚠️ **包在这里，不包进 `log.py`**（fix round 裁的落点）：`append` 是**唯一**
            # 声明过拒绝面的那一层（"一份草稿要么变成一个事件、要么抛 `EventError`"），
            # 而这句是**同一类事**——草稿变不成一个持久事件，且条件来自外部、调用方无从预见。
            # ⚠️ 也**不翻成 `TornShardError`**（那是够得着的最像的一个）：`log.py` 拿它说的是
            # "一截没写完的行"（**粘上去会静默丢数据**），而这里说的是"这一片现在动不了"
            # （读不到 / 被占着 / 只读）⇒ 翻过去就是把一个**更坏的**诊断安在一条普通的失败上，
            # 而 `self.repairs.failed` 那条通道又会跟着说反话。
            # A failed tail probe is also a write refusal. Never append when
            # the existing tail could not be verified; preserve its bytes.
            raise EventError(
                f"cannot verify or append the shard: {type(exc).__name__}: "
                f"{exc}") from exc
        try:
            self._by_cursor.put(e, segment=os.path.normcase(path.name))
        except BaseException:
            self._write_failed = True  # Source committed; a cold reopen must rebuild the index.
            raise
        self._next += 1
        self._disk_state = self._disk_signature()
        # ⚠️ 通知在**状态全部落定之后**：订阅者一进来读到的就是一致的库。
        # ⚠️ 而订阅者自己抛的异常**不吞**：吞掉会留下"事件落了盘、却没人知道"那种
        # 状态 —— 那比吵更坏。要让订阅者出错不影响 `append`，得由订阅方自己包
        # （或者等 S9 的日志设施）。边界写在这儿，不假装它闭合。
        for cb in self._listeners:
            cb(e)
        return e

    # ── 读 ─────────────────────────────────────────────────────
    def get(self, cursor: int) -> EventEnvelope | None:
        """按游标取一条；没有就是 `None`（`0` / 负数 / 还没到的也一律 `None`）。"""
        return self._by_cursor.get(cursor)

    def get_by_dedup_key(self, key: str) -> EventEnvelope | None:
        cursor = self._by_cursor.dedup_cursor(key)
        return self.get(cursor) if cursor is not None else None

    def latest_cursor(self) -> int:
        """**已经分配出去的最大游标**；空库是 `0`。

        ⚠️ 它是**高水位**（`_next - 1`），不是"现存最大的那条"：坏行被跳过时它不动，
        `clear()` 之后它**也不回退**（那正是 `clear` 的裁定）。
        """
        return self._next - 1

    def current_count(self) -> int:
        """**当前那一份**分片里的条数（= `append` 此刻会写进去的那一个）。

        ⚠️ 是**按日期认**的那一份，不是"分片表末项"：
        取末项的话，`clear()` 清掉今天那一份之后它会去数**更早**那一份，
        而 `clear()` 自己已经按"当前那一份"动了手 ⇒ 两个方法会说两套话。
        ⚠️ 数的是**这一份管辖的游标**，不是"一段区间里的每个整数" ——
        `_scan` 会跳过坏行，所以一个分片里的游标可能不连续。
        """
        return self._by_cursor.segment_count(os.path.normcase(self.log.path_for(self._current_day()).name))

    def _matches(self, e: EventEnvelope, q: RangeQuery) -> bool:
        """这八个过滤字段**逐一相同**的两个查询**共用它** —— 注解写的是 `RangeQuery`，
        而 `grep` 递进来的是 `GrepQuery`。

        ⚠️ **那是有意的鸭子类型，不是笔误**：两个 `frozen` dataclass 的过滤字段
        **名字与类型逐一相同**（`GrepQuery` 只多出 `keyword` / `context`），
        所以这里读得到的每一样东西在两者上都成立。
        ⇒ **不要为此抽一个基类**（YAGNI）：约定写在这儿就够了；真要有共同祖先，
        得先出现**第三个**用得上它的地方。

        ISO 时间按实际时刻比较；无时区的旧 ISO 时间按主机本地时区解释。
        非 ISO 的旧逻辑时钟保留原比较方式，不改变事件在盘上的时间写法。
        """
        from .time_order import compare
        if q.from_cursor is not None and e.cursor < q.from_cursor:
            return False
        if q.to_cursor is not None and e.cursor > q.to_cursor:
            return False
        if q.from_ts is not None and compare(e.ts, q.from_ts) < 0:
            return False
        if q.to_ts is not None and compare(e.ts, q.to_ts) > 0:
            return False
        if q.sender_key is not None and e.sender_key != q.sender_key:
            return False
        if q.source is not None and e.source != q.source:
            return False
        if q.origin is not None and e.origin != q.origin:
            return False
        return True

    def range(self, q: RangeQuery = RangeQuery(), *, accept=None) -> list[EventEnvelope]:
        """区间查询：筛过的那些，**按游标升序**（不给条件 ⇒ 全库）。

        ⚠️ **游标就是键** ⇒ 按它排就是时间序（游标跨分片单调，`03` 那句"一个库"的地基）
        ⇒ 这里看的是**整个库**：分片是物理切法，在这一层**不可见**
        （`test_range_spans_segments` 钉的就是它）。

        ⚠️ **`limit` 从区间的尾部取**（"最近 N 条"），而**返回仍升序** —— 别写反。
        顺序是：先筛 → 再从**尾**切 → 返回**升序**的那一段。
        可选 accept 同样在计入 limit 前检查；拒绝的正文不会挤占返回名额。

        ⚠️ **`limit` 的退化输入 —— 两个查询共用同一条规则**：**只有 `>= 0` 才是上界**，
        `0` 就是"一条都不要"，**负数 = 不限**。那句 `q.limit >= 0` 的守卫就是这个意思：
        没有它，`-1` 会算出 `matched[len(matched) + 1:]` ＝ **空**（"不限"退化成"空"）。
        ⚠️ **`grep` 的 `limit` 与这里逐字同一条** —— 它曾经**不是**（判断排在 `append`
        之后 ⇒ `0` 放行第一个命中、负数也一样）⇒ **两个查询对同一类输入答得不一样**，
        那是 fix wave 修掉的那条（理由与形状见 `grep` 的 docstring）。

        ⚠️ **而"这里空"不等于"库是空的"**：`_scan` 会跳过**读不出来的分片**
        （⇒ `self.skipped`，Task 4）—— 那一片里的事件**根本没进内存**，
        所以**本方法（以及 `around` / `grep`）都看不见它们**。
        ⇒ **`range()` 全空 ＋ `skipped` 非空 ≠ 库是空的**（也可能只是一整天没读进来）。
        ⚠️ **不要试图在查询里补它**（那要重新读盘）—— 读法、上界、一致性都得重来，
        那是另一个设计。这里是**必然看不见**，不是漏了。
        """
        return self._range(q, accept=accept, metadata_only=False)

    def range_metadata(self, q: RangeQuery = RangeQuery(), *, accept=None, retrieval_projection=False) -> list[EventEnvelope]:
        """Select empty-text metadata; projection is for RetrievalScope/header fields only."""
        return self._range(q, accept=accept, metadata_only=True, retrieval_projection=retrieval_projection)

    def _range(self, q, *, accept, metadata_only, retrieval_projection=False):
        if q.limit == 0:
            return []
        upper = self.latest_cursor() if q.to_cursor is None else min(q.to_cursor, self.latest_cursor())
        matched = []
        # Latest-first scanning stops as soon as the requested page is full.
        # Cursor bounds use the maintained index rather than scanning history.
        # Time comparisons precede sender equality in _matches and can raise;
        # preserve that order whenever a time bound is present.
        sender = q.sender_key if q.from_ts is None and q.to_ts is None else None
        for cursor in self._by_cursor.ordered(lower=q.from_cursor, upper=upper, reverse=True, sender_key=sender):
            if metadata_only:
                result = self._by_cursor.read_window(cursor, 0, 0,
                    accept=lambda event: self._matches(event, q) and (accept is None or accept(event)),
                    retrieval_projection=retrieval_projection)
                event = result[0] if result is not None else None
                accepted = event is not None
            else:
                event = self._by_cursor[cursor]
                accepted = self._matches(event, q) and (accept is None or accept(event))
            if accepted:
                matched.append(event)
                if q.limit is not None and q.limit > 0 and len(matched) >= q.limit:
                    break
        matched.reverse()
        return matched

    def read_text_window(self, cursor: int, offset: int, count: int, *, accept, _retrieval_projection=False):
        """Read from this verified view after a fresh caller-supplied check."""
        if type(cursor) is not int or cursor < 1:
            raise ValueError('cursor must be a positive integer')
        if type(offset) is not int or offset < 0 or type(count) is not int or count < 0:
            raise ValueError('offset and count must be nonnegative integers')
        return self._by_cursor.read_window(cursor, offset, count, accept=accept, retrieval_projection=_retrieval_projection)

    def read_retrieval_window(self, cursor: int, offset: int, count: int, *, accept):
        """For fixed retrieval policy/header fields only; arbitrary metadata uses read_text_window."""
        return self.read_text_window(cursor, offset, count, accept=accept, _retrieval_projection=True)

    def around(self, cursor: int, before: int, after: int) -> list[EventEnvelope]:
        """`cursor` **自己** ＋ 它前后各若干条 —— 窗口是 `[cursor-before, cursor+after]`。

        ⚠️ **含中心那一条**（裁定过的语义，不是"前后各 N 条、不含自己"）。
        ⚠️ 窗口两端被夹到库里实际有东西的地方：`center` 先夹进 `[1, latest]`，
        `before` / `after` 为负当 `0`，越界的下界／上界由 `range` 的过滤自然吃掉。
        ⚠️ **一条都没写过**时返回 `[]`（`latest == 0` ⇒ 不去猜一个窗口）；
        清空过的库由 `range` 的过滤自然给空（`latest_cursor()` **不回退**，所以别拿它当"库里有几条"）。
        ⚠️ **与 `range` 同一条"看不见"**：跳过的分片里的事件这里也看不见
        （⇒ **空的窗口 ＋ 非空的 `skipped` ≠ 那一段没有事件**，见 `range` 的 docstring）。
        """
        latest = self.latest_cursor()
        if latest == 0:
            return []
        center = min(max(cursor, 1), latest)
        return self.range(RangeQuery(from_cursor=center - max(0, before),
                                     to_cursor=center + max(0, after)))

    def grep(self, q: GrepQuery) -> list[GrepHit]:
        """搜文本：**不区分大小写的纯文本包含**；每个命中附**前后各 `context` 条**。

        ⚠️ **邻居不经过筛选** —— 它们是**上下文**，不是命中：上下文的意义就是
        "它周围实际发生了什么"；把邻居也过一遍 `sender_key` / `ts` 之类的过滤，
        它就不是上下文了。⇒ 取的是**真实邻接**，不是"筛过的那几条里离它最近的"
        （`test_grep_context_events_skip_the_filters` 钉着这一条）。

        ⚠️ `limit` 数的是**命中**，不是上下文条数；返回按命中游标**升序**。
        ⚠️ **`limit` 的退化输入与 `range` 逐字同一条规则**（见那里）：只有 `>= 0` 才是上界，
        `0` = 一条都不要、**负数 = 不限**。⚠️ **判断必须排在 `append` 之前** ——
        排在后面的话 `0` 会放行**第一个**命中（"已经够了"要到下一轮才发现），负数也一样 ⇒
        **两个查询对同一类输入答得不一样**。那是 fix wave 修掉的那条：
        `range(limit=0)` 给 `[]`、而 `grep(limit=0)` 给一个命中 ——
        **两个函数对同一类输入给不同答案本身就是缺陷，与"谁先写的"无关**。
        对齐的方向是 **`grep` 跟 `range`**（不是反过来）：`0` 当"0 条"是**收口**的那一侧
        （一个算出来的 `limit=0` 不该变成"把整个库端上来"，那是这一层最该躲的失效模式），
        而 `range` 那条 `>= 0` 守卫**本来就是这个语义**，动它要多改一处既有行为。
        `context` 为负当 `0`（不后退到"往前删邻居"那种事上）。

        ⚠️ **与 `range` 同一条"看不见"**：跳过的分片里的事件这里也搜不到
        （⇒ **搜不到 ＋ 非空的 `skipped` ≠ 库里没有这个词**，见 `range` 的 docstring）。
        """
        return list(self.iter_grep(q))

    @property
    def incomplete_records(self):
        return self._by_cursor.invalid_lines

    def keyword_page(self, keyword, *, budget, before, limit, sender_key, accept, retrieval_projection=False):
        """Search the next slice of visible events in decreasing cursor order."""
        if type(budget) is not int or not 1 <= budget <= 256:
            raise ValueError('scan_budget 须为 1..256')
        if before is not None and (type(before) is not int or before < 1):
            raise ValueError('scan_before 须为正整数')
        if not keyword or type(limit) is not int or limit < 1:
            raise ValueError('分段检索需要关键词和正数 limit')
        candidates = self.range_metadata(
            RangeQuery(to_cursor=before - 1 if before is not None else None,
                       sender_key=sender_key, limit=budget + 1),
            accept=accept, retrieval_projection=retrieval_projection)
        more = len(candidates) > budget
        page = candidates[-budget:]
        if not page:
            return [], None
        query = GrepQuery(keyword=keyword, context=0, from_cursor=page[0].cursor,
                          to_cursor=page[-1].cursor, sender_key=sender_key)
        hits = [hit.events[0] for hit in self.iter_grep_metadata(
            query, accept=accept, retrieval_projection=retrieval_projection)]
        hits.reverse()
        if len(hits) > limit:
            return hits[:limit], hits[limit - 1].cursor
        return hits, page[0].cursor if more else None

    def newest_metadata(self, q: GrepQuery, *, accept, retrieval_projection=False):
        """Latest keyword hits for the scoped reader, without scanning older bodies."""
        if q.context != 0:
            raise ValueError('newest metadata requires zero context')
        if q.limit == 0: return []
        if q.limit is None or q.limit < 0:
            raise ValueError('newest metadata requires a finite limit')
        return list(self._by_cursor.newest_metadata(keyword=q.keyword.lower(), limit=q.limit,
            sender_key=q.sender_key,
            accept=lambda row: self._matches(row, q) and accept(row),
            retrieval_projection=retrieval_projection))

    def iter_grep(self, q: GrepQuery):
        """Stream hits from the initial cursor span, retaining no prior bodies."""
        yield from self._iter_grep(q, metadata_only=False)

    def iter_grep_metadata(self, q: GrepQuery, *, accept=None, retrieval_projection=False):
        """Exact empty-text hits; optional projection only supports retrieval policy/header fields."""
        if max(0, q.context):raise ValueError('metadata grep requires zero context')
        yield from self._iter_grep(q, metadata_only=True, accept=accept, retrieval_projection=retrieval_projection)

    def _iter_grep(self, q, *, metadata_only, accept=None, retrieval_projection=False):
        kw = q.keyword.lower()
        ctx = max(0, q.context)
        # ⚠️ 与 `range` **同一条规则**：`limit` 只有 `>= 0` 时才是上界（`0` = 一条都不要），
        # 负数 = **不限**。正负都归一到这里，好让下面只有一处判断。
        limit = None if q.limit is None or q.limit < 0 else q.limit
        if limit == 0: return
        found = 0
        upper = self.latest_cursor() if q.to_cursor is None else min(q.to_cursor, self.latest_cursor())
        sender = q.sender_key if q.from_ts is None and q.to_ts is None else None
        for cursor in self._by_cursor.ordered(lower=q.from_cursor, upper=upper, keyword=kw, sender_key=sender, accept=accept,
                                             retrieval_projection=retrieval_projection):
            if metadata_only:
                result = self._by_cursor.read_window(cursor, 0, 0,
                    accept=lambda event: self._matches(event, q) and (accept is None or accept(event)),
                    retrieval_projection=retrieval_projection)
                if result is None:continue
                e = result[0]
            else:
                e = self._by_cursor[cursor]
                if not self._matches(e, q):continue
            # ordered(keyword=...) already performs the exact whole-string
            # lower match, including after FTS candidate selection. Repeating
            # it here copies the full decoded body after a bounded match.
            # ⚠️ 邻近记录**不经过筛选** —— 它们是上下文，不是命中。
            found += 1
            yield GrepHit(hit_cursor=e.cursor, events=(e,) if metadata_only else tuple(self.around(e.cursor, ctx, ctx)))
            # Check before requesting another candidate: finding that next
            # match could otherwise scan every remaining source page.
            if limit is not None and found >= limit:
                return

    def iter_after(self, cursor: int, upper: int):
        """Replay committed envelopes in bounded pages, including sparse cursor histories."""
        for key in self._by_cursor.ordered(lower=cursor + 1, upper=upper):
            event = self.get(key)
            if event is not None:
                yield event

    def on_append(self, cb: Callable[[EventEnvelope], None]) -> None:
        """订阅**新**事件 —— 去重命中的那一次**不通知**（它没有变成一条新事件）。"""
        self._listeners.append(cb)

    # ── 清 ─────────────────────────────────────────────────────
    def clear(self) -> int:
        if self._read_only or not self._is_open():
            raise SealedError('event store is not open for clearing')
        with lease(self.log.root):
            # Destruction cannot rely solely on possibly coalesced timestamps.
            # Keep fast append checks unchanged; compare the snapshot here.
            self._check_disk(force=True)
            try:
                return self._clear()
            finally:
                self._disk_state = self._disk_signature()

    def _clear(self) -> int:
        """**只清当前那一份分片**，返回清掉几条；游标**不回退**。

        ⚠️ 今天还一条都没写过 ⇒ 返回 `0`、**一个文件都不碰**（见 `_segment_index`）：
        "清掉当前这一份"不该落到**别的日子**头上。
        ⚠️ 走 `unlink` 而不是截成零字节：一个**零字节的分片**仍是"幽灵分片"那个形状
        （`day_files()` 照样收它）—— 那正是 Task 1 把编码挪到 `open()` 之前要躲开的东西。
        删除前持久保存游标高水位；重启取分片与高水位的最大值，不复用已清游标。
        此合同依赖同一目录及合作写入协议，不承诺外部删除高水位后的恢复。
        """
        path = self.log.path_for(self._current_day())
        count = self._by_cursor.segment_count(os.path.normcase(path.name))
        if not count:
            return 0
        from .record_history import _file_digest
        original = _file_digest(path)
        self._save_highwater(self._next - 1)
        # Persisting authority performs I/O and can overlap an external write.
        # Validate both semantic state and the exact finite target bytes again.
        self._check_disk(force=True)
        if _file_digest(path) != original:
            self._write_failed = True
            raise EventError('event shard bytes changed before clear; reopen before mutation')
        path.unlink(missing_ok=True)  # Do not discard memory if disk removal fails.
        try:
            self._by_cursor.clear_segment(os.path.normcase(path.name))
        except BaseException:
            self._write_failed = True
            raise
        return count
