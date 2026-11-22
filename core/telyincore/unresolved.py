# telyincore/unresolved.py
"""惦记 —— 那一件还没放下的事（规格 `06-工具与派发.md` §6.7 那两行的**真后端**）。

```python
add(text) -> Item        # 「记一条没放下的事」
resolve(id) -> bool      # 「把一条没放下的事划掉」
list() -> list[Item]     # 还惦记着的那些 —— 醒来时看的就是它
handlers(store) -> …     # 把那两个动词包成能绑进 `tools.build_table` 的执行段
```

 规格对这两个工具只给了三样：**一行**（§6.7：名字 ／ 标签 `flow` ／ `effect` `immediate`）、
**为什么有它**（§8.9："醒来时有东西惦记：心里存着那件事还没做完"）、
**在哪一环**（§8.5："醒来 → 看到 D 的 unresolved、自己的状态"）。
**"它长什么样"——存哪儿 ／ 一条是什么 ／ 两个动词收什么参数——一个字都没有。**
（找过 `docs/design/10-整理态.md` 与 `docs/design/12-…`：前者是后台消化线程，后者是
`自述` ／ 《关于自己》两份文件的契约 —— **两份都没提它**。
⚠️ `12` 那一份的名字里有一个字是核心树的禁词（`01-主体边界.md` 纪律 1）⇒ 只留 `…`，
**别补回去**：`tests/test_boundary.py` 会当场变红。）

⇒ 于是这里只造**推得出来的那几样**，每一样都指着出处：

| 决定的 | 出处 |
|---|---|
| 两个动词 | `tools.DECLARED_TOOLS` 那两行的描述原文（"记一条没放下的事"／"把一条没放下的事划掉"） |
| **一个把手（`id`）** | `resolve` 那个动词**要**一个把手（"把**一条**划掉"）；生成法抄 `timers.py` 的 `set` |
| **落盘** | 计划的 File Structure（"存储与读写"）＋ 本仓**已有的那一种**风格（见下） |
| 执行段的**签名** | `dispatch.py:488-489` 自己那句：**"谁写出那 13 个执行段，谁定它"** |
| 回执的措辞 | 同上（没人写过）——只保证它是一句**事实**，而且带得回那个把手 |

 而**没人写过的东西，一条都没造**（照 S6 立的纪律："规格没给的东西，宁可留空并点名"）。
那几笔债**没有一个字是空着的** —— 它们住在 `_OWED` 里（名字 → 欠主），
由 `tests/test_unresolved.py` 钉着（照 `dispatch._HANDLER_OWNERS` 那张表的成法）：

```
· parameter_schema —— `tools._SCHEMA_OWNERS` 写着这一笔的欠主**就是本模块**，
  而 `tools.py` 是**禁改面** ⇒ 那 13 行的 `parameters` 仍然是 `None`：留空 ＋ 在这里点名。
   而**参数名已经定了**，不必等谁来决定：就是 `handlers()` 交出的那两个执行段的签名
  （`unresolved_add(text=…)` ／ `unresolved_resolve(id=…)`）—— `dispatch` 第 6 步把
  **对象当关键字**递（`dispatch.py` 第 6 步那句 `handler(**arguments)`）⇒ 改这两个名字，
  那两条路当场对不上。还欠的**只是那一行 schema 本身**。
· wake_view —— launch 的 facts 消费 list()，按当前 scope 装配概览。
· handlers_bound —— launch 取得 handlers(store)，并入工具表；实际写入仍受门卫限制。
```

⚠️ **原先这一串里还有第三笔 `seal_gate`**（"接它归装配那一层"）—— **本刀把它还掉了**：
   构造收下 `is_open`，而 `add` ／ `resolve` 各自第一句查它（见下面「门卫」那一节）
   ⇒ 它**从 `_OWED` 里删掉**（一条已经兑现的债留在表里就是一句假话）。

── 落盘：**一张快照 ＋ 原子替换**（本仓已有的那一种，不是新一种）────────────
⚠️ 与 `timers.py` **同一个形状**，所以理由抄它那一段（这里只留判据，不重抄全文）：
   这张表**会增也会减**（`resolve` 要拿走东西）⇒ **追加**表达不了"拿走"
   （只能再写一条墓碑，于是 `list()` 变成"整个文件读一遍再折一次"，而文件只增不减地长）；
   而 `log.py` 的 `repair_tail` 在这里**没有活干** —— 它修的是"一截没写完的行"，
   那种半截只会由**原地追加**产生，而本模块用 `os.replace` 换**整份**文件
   ⇒ 盘上任何一刻都是"上一份完整的"或"这一份完整的"。
   ⇒ 抄的是 `log.py` 里**另一半**约定：**字节写法 ＋ 同目录临时文件 → flush → fsync →
     `os.replace`**，而 `fsync` 必须排在替换**之前**（只换名不落盘的话，断电后是
     "重命名已生效、内容却是空的"）。

⚠️ **每一次 `add` ／ `resolve` 当场落盘**（不是退出时）—— 同 `timers.py` 那条：
   "关掉再开它还在"只有在盘上**当下就有它**的时候才成立。
⚠️ **盘上那份读不回来时就吵**，而吵之前**一个字都不写**：这里是**一份快照**，
   下一次写会整个盖掉它 ⇒ 一个"跳过它、当空表"的实现会让那截读不回来的数据
   **永久消失**，而且是**静默**的。（`events.py` 那套宽容是给**只增不减**的库的。）
⚠️ 本模块**没有时刻**（那个字段没人写过）⇒ `timers.py` 那套**时钟注入**在这里没有面：
   连 `time` ／ `datetime` 都不 import，而 `tests/test_unresolved.py` 拿一个
   "一被调用就抛"的时钟把这条路**探**了一遍（不只是声明）。

── 门卫（`seal.Gate`）**接上了** —— 与 `events.py` ／ `log.py` 同形 ──────────
构造收一个 `is_open: Gate = always_open`，而 `add` ／ `resolve` 这两条会写的路
**第一句**查它：封着 ⇒ 当场 `SealedError`，而**表与盘一个字节都不动**。
· ⚠️ **两个动词都是会写的**（这就是"写口"的全部）：`add` 必落盘；`resolve` 只在
  **真划到**一件时才落 —— 而**封着的时候连那一次"划不到"也不许答复**
  （一个封着的表说"本来就没有"，与"办了"一样，都不是关机之后该有的话）。
· ⚠️ **门卫是"第一句"而不是"写之前那一句"**（`log.py` 的 `append_line` 就是这个形状）：
  摆在状态改动**之后**的话，一次被拦下来的写**已经改了状态**、而那是最难查的形状。
· ⚠️ **默认那个是 `seal.always_open` —— 一个活陷阱，而这是刻意的、必须点名**：
  「没有门卫」与「门卫恒开」在**签名上长得一样** ⇒ 一个本该被封的写会悄悄过去
  （`seal.py` ／ `log.py` 都点过这个名）。它不做成必填，是为了那几十条**不要门卫**的
  既有构造（它们测的是"记下没有"，不是关机）⇒ **生产路径必须显式递一个真的**
  （由 `app.build()` 造，读 `loop.sealed` —— 与那两本账本同一个门卫）。
  ⇒ 这件事由**两条**分工钉着，别读成一条：
  · **本模块这半边**归 `tests/test_unresolved.py`（本刀）：参数化的
    `…refuses_every_mutating_entry…`（`add` ／ `resolve` 各一次）
    与 `…defaults_to_always_open…`；
  · **生产路径那半边**（"`build()` 手上那个门卫是真的"＋"`seal()` 之后写口真的抛"）
     **装配那一刀把它兑现了** —— 两条，都在 `tests/test_app.py`，
    而**名字写全在这里**（读这一份的人**不必**再开另一个模块去找它）：
    · `tests/test_app.py:589` `test_build_hands_the_real_gate_to_the_two_new_stores`：
      `add` ／ `resolve` 两条写口各判一次（封之前进得去 ＝ **防真空**；封之后当场抛）；
    · `tests/test_app.py:624` `test_the_gate_reads_the_loops_sealed_flag_on_all_four_stores`：
      端到端 —— `loop.seal()` 之后四本各自**在公开写口上**拒写。
    ⚠️ **行号只算到写它这一刻**（本仓的规矩）；成法照**那两条更老的兄弟**
    （`…wires_a_real_gate…` ／ `…reads_the_loops_sealed_flag…` —— 它们钉的是
    `events` ／ `log` 那半边），而这一半从今天起**有它自己的钉子**。
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from functools import wraps
from .source_guard import lease
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .seal import Gate, SealedError, always_open
from . import snapshot_json

#: 这份惦记就落在这一个文件里（`root` 是个**目录** —— 与 `events.py` ／ `log.py` ／
#: `timers.py` 同一条约定）。
FILE = "unresolved.json"

#: 旧版临时名后缀；新写入使用带随机后缀的独占文件，不打开已有临时路径。
TMP_SUFFIX = ".tmp"

#: 一条记录的键**恰好**是这两个（写侧与读侧跑的是同一张表）。
_ITEM_KEYS = frozenset(("id", "text"))

#: **本模块欠着的那些东西**（名字 → 欠主），一条不落 —— 判据是 `loop.py:60-63`
#: 那条家规的另一面：**留空可以，但"空了"这件事必须有人说得出来**。
#: ⚠️ 它与模块 docstring 是**同一件事的两种形态**：那段是给人读的，这张表是给测试读的
#:    （照 `tools._SCHEMA_OWNERS` ／ `dispatch._HANDLER_OWNERS` 的成法）。
#: ⚠️ 而它与那两张表**不是同一笔债**：那两张说的是"别的工具的 schema ／ 执行段"，
#:    这一张说的是**本模块自己点名欠着的那几样**。
#: ⚠️ 原先这里还有第四样 `seal_gate`（"装配那一层"）—— **本刀把它还掉了**：
#:    构造收下 `is_open`，而 `add` ／ `resolve` 各自第一句查它（见模块 docstring 那一节）。
#:    ⇒ 那一笔**从这张表里删掉**，而不是留一个"已经还了"的名字：
#:    这张表说的是**今天还欠着的**，而一条已经兑现的债留在里面就是**一句假话**
#:    （`tests/test_unresolved.py` 那条按名字钉的表会当场红 —— 那正是它要的对话）。
#: ⚠️ 键是**稳定的机器名**（改措辞不会红），值里必须**点名**（空话会红）。
_OWED: dict[str, str] = {
    "wake_view": (
        " ★ **已经接上了**（2026-10-13 订正）。原先写的是：建上下文那一层"
        "（§8.5 说「醒来 → 看到 D 的 unresolved」，而「怎么给它看」是那一层的事；今天没有那一层）。"
        " 今天的事实：**那一层在** —— 入口把「我这儿还悬着 N 件没放下的」那一句挂进"
        " `facts`（每请求现装）⇒ 它抬头就看得到。"),
    "handlers_bound": (
        " ★ **已经接上了**（2026-10-13 订正）。原先写的是：装配那一层"
        "（`dispatch._WIRING_OWED_BY` 那位）—— 本模块只交出 `handlers(store)`，"
        "今天没有任何生产路径调它。 今天的事实：**入口建 store、取 handlers、"
        "并进 `remember_handlers`**（`launch` 那一处），它们照旧走到表里发给模型。"),
}


class UnresolvedError(ValueError):
    """这一笔进不了这份惦记 —— 原文写不出去 ／ 盘上那份读不回来。

    ⚠️ 调用方**只捕它** —— 不要让裸 `TypeError` ／ `json.JSONDecodeError` ／ `KeyError`
       逃出去（照 `events.py` 的 `EventError` 那条：拒绝面必须是**说得出的那几条**）。
    ⚠️ 它同时也是**执行段**那一边的拒绝面：`dispatch` 第 6 步捕 `Exception` ⇒
       一条原文空白的调用会变成 `tool_failed(UnresolvedError: …)` 那句回执 ——
       模型看得见"这件事没办成"，而不是一次静默的跳过（`loop.py:60-63`）。
    """


class UnresolvedConflict(UnresolvedError):
    """The opened snapshot no longer owns the current on-disk version."""


def _serialized(function):
    @wraps(function)
    def guarded(self, *args, **kwargs):
        with lease(self.path):
            return function(self, *args, **kwargs)
    return guarded


@dataclass(frozen=True)
class Item:
    """一条惦记：id、text 与可选 ISO8601 at。

    旧记录 at 为空表示时刻未知，不据此推断年龄；没有隐含来源、优先级或状态字段。
    ⚠️ `id` 是**这张表发的**，不是调用方给的：`resolve` 要一个把手，而"第几条"
       那种编号在一张会减的表上会**从头再来**（`timers.py` 的 `set` 写过这条理由）。
    """

    id: str
    text: str
    #: 记下它的那一刻（ISO8601）。**缺省空串**：旧档里没有这一格，
    #: 而"不知道多旧"不许当成"很久以前"（那会凭空把旧档算老）。
    at: str = ""


def _a_record(item: "Item") -> dict[str, str]:
    """一条落盘长什么样：**有 at 才写那一格** —— 没时刻的条目逐字节与从前一样。"""
    out = {"id": item.id, "text": item.text}
    if str(getattr(item, "at", "") or ""):
        out["at"] = str(item.at)
    return out


def _encode(items: list[Item]) -> bytes:
    """把一份惦记编成**一个 JSON 对象 ＋ 收尾换行**的字节；编不出来 ⇒ `UnresolvedError`。

    ⚠️ `ensure_ascii=False` 与 `events.py` 的 `to_line()` 同一个写法（非 ASCII 原样落盘）。
    ⚠️ **编码排在动文件之前**（`log.py` 那条）：一个 utf-8 编不出去的字符
       （孤立代理项之类 —— 它是合法 `str`）若在 `open()` **之后**才炸，
       盘上会留下一个**空文件**，而那个空文件读回来是"一件都没有"，**不是**一次失败。
    ⚠️ `UnicodeEncodeError` 是 `ValueError` 的子类 ⇒ 这里那一个出口盖得住它。
    """
    records = [_a_record(item) for item in items]
    try:
        text = json.dumps({"items": records}, ensure_ascii=False, separators=(",", ":"))
        return (text + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise UnresolvedError(f"这一笔写不下去：{exc}") from exc


def _replace(path: Path, blob: bytes, verify=None, create=False) -> None:
    """Write an exclusive staging file, sync, recheck ownership, then publish.

    New destinations use a no-replace link; existing snapshots use replace.
    File fsync does not guarantee directory or hardware power-loss durability.
    A process crash may leave a unique staging orphan; readers ignore it.
    """
    fd, name = tempfile.mkstemp(prefix=path.name + TMP_SUFFIX + '-', dir=path.parent)
    tmp = Path(name)
    primary = None
    published = False
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        if verify is not None:
            verify()
        if create:
            try:
                os.link(tmp, path)
            except FileExistsError as error:
                raise UnresolvedConflict('unresolved disk appeared before publication; reopen before retrying') from error
        else:
            os.replace(tmp, path)
        published = True
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as error:
            # A cleanup orphan must not turn successful publication into failure.
            if primary is None and not published:
                raise
            if primary is not None:
                primary.add_note('unresolved staging cleanup failed: ' + type(error).__name__)


def _sweep(path: Path) -> None:
    """仅清理旧版固定名称的临时文件；不发布或删除随机名称的孤儿。

    ⚠️ 收不掉就是**没那一趟**，仅此而已 —— 它不许挡住启动（`log.py` 扫 `.repair`
       孤儿时同一条裁定：Windows 上 `unlink` 会抛，而"启动"正是这里承诺的东西）。
    """
    try:
        path.with_name(path.name + TMP_SUFFIX).unlink(missing_ok=True)
    except OSError:
        pass


def _decode(raw: bytes, path: Path) -> dict[str, Item]:
    """把盘上那份读回一张表 —— **读不回来就吵**（理由见模块 docstring 那一节）。

    ⚠️ 键的集合**逐一核**（顶层只要 `items`；一条恰好 `id` ＋ `text`）：拿键去"投影"会让
       一个多余的键**静默消失** —— 而这里下一次写会整份盖掉，于是它不是"被忽略"，
       是**被删掉**（`events.py` 的 `_scan` 里那段说的正是"投影看不见未知键"这个盲区，
       那边只增不减所以只是看不见，这边会真丢）。
    ⚠️ `text` 不是字符串那一条也在这儿：**写侧拒它**（见 `add`）⇒ 读侧就得同样拒 ——
       不然盘上那份会被读成"一件显示成 5 的事"，而下一次写把它落定。
    """
    try:
        data = snapshot_json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise UnresolvedError(f"{path} 读不回来：{exc}") from exc
    if not isinstance(data, dict) or set(data) != {"items"}:
        raise UnresolvedError(f"{path} 的形状不对：顶层要是**只带** items 的对象")
    records = data["items"]
    if not isinstance(records, list):
        raise UnresolvedError(f"{path} 的 items 不是一个数组")
    items: dict[str, Item] = {}
    for i, record in enumerate(records):
        # 2026-10-11：`at` 成了认得的第三个键，而**旧档（只有两格）照旧读得进来** ——
        # 缺它就当空串（"不知道多旧"），不许当 0。
        if not isinstance(record, dict) or set(record) not in (_ITEM_KEYS,
                                                              _ITEM_KEYS | {"at"}):
            raise UnresolvedError(f"{path} 的第 {i} 条不是一条只带 id/text 的记录")
        if "at" in record and not isinstance(record["at"], str):
            raise UnresolvedError(f"{path} 的第 {i} 条的 at 不是一个字符串")
        if not isinstance(record["id"], str) or not record["id"]:
            raise UnresolvedError(f"{path} 的第 {i} 条的 id 不是一个非空字符串")
        if not isinstance(record["text"], str) or not record["text"].strip():
            raise UnresolvedError(f"{path} 的第 {i} 条的 text 不是一个非空白的字符串")
        if record["id"] in items:
            raise UnresolvedError(f"{path} 的第 {i} 条与前面某条同 id：{record['id']}")
        items[record["id"]] = Item(id=record["id"], text=record["text"],
                                  at=str(record.get("at") or ""))
    return items


#: **2026-10-10：这一块也有上界了** （ 作者：「那几个数就按你拍的」）。
#: ⚠️ **为什么非有不可** ： 这一块**从前没有上界** —— 记一条就长一条 ，
#: 而**没有任何地方会说"到顶了"** （ 而本仓的规矩是**不许静默** ）。
#: 成法照抄 `launch.py::_the_kept` ： 满了**压最旧的**（ 让出一条再写 ）、
#: **没到上限一条都不动** （ 不排序、不去重 —— **顺序就是它的意思** ）。
UNRESOLVED_AT_MOST = 20

class UnresolvedStore:
    """`root` 目录下的那一份惦记（在 `unresolved.json` 这一个文件里）。

    合作写者通过源文件租约串行提交；已打开的旧快照发现磁盘变化时拒绝覆盖，
    调用方须重新打开，不能把他人的更新覆盖掉。
    """

    def __init__(self, root: Path, *, is_open: Gate = always_open) -> None:
        """收下那个目录，把盘上那份读回来（**读不回来就吵**，见 `_decode`）。

        ⚠️ **没有 `clock`** —— 与 `timers.py` 的区别就在这儿：这一份**没有时刻**
           （那个字段没人写过）。别为了对称给它塞一个：那会是一个**永远不被读**的参数，
           而"一个传进来却没人用的东西"正是 `loop.py:60-63` 那条家规禁的形状。
        ⚠️ **默认门卫是 `seal.always_open` —— 一个活陷阱，而这是刻意的、必须点名**：
           「没有门卫」与「门卫恒开」在**签名上长得一样** ⇒ 一个本该被封的写会悄悄过去。
           它不做成必填，是为了几十条**不要门卫**的既有构造（它们测的是"记下没有"，
           不是关机）⇒ 生产路径**必须显式递一个真的**（由 `app.build()` 造，
           读 `loop.sealed` —— 与那两本账本同一个门卫）。
           ⚠️ 而"生产路径到底递没递"这件事，**本文件这边一条测试都钉不住**
           （`add` 由谁调是另一层的事）：那半边归 `tests/test_app.py`。
        """
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._is_open = is_open
        self._items: dict[str, Item] = self._load()

    # ── 它在盘上的哪儿 ──────────────────────────────────────────
    @property
    def path(self) -> Path:
        """这份惦记落在哪一个文件里。"""
        return self.root / FILE

    @_serialized
    def _load(self) -> dict[str, Item]:
        """启动时那一脚：先收孤儿，再读那份快照（**读不回来就吵**）。"""
        _sweep(self.path)
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            self._disk_blob = None
            return {}                       # 还没写过 ⇒ 一件都没有（不是错误）
        except OSError as exc:
            raise UnresolvedError(f"{self.path} 读不动：{exc}") from exc
        self._disk_blob = raw
        return _decode(raw, self.path)

    def _commit(self, items: dict[str, Item]) -> None:
        """**先落盘、再换进内存**（落盘失败 ⇒ 内存一动不动、盘上也没痕迹）。

        ⚠️ 这个次序就是"一笔要么落在盘上、要么当场被拒"那条家规在本模块的形状：
           反过来（先换内存再写）时，一次写失败会留下一份**只活在内存里**的惦记 ——
           它看起来一切正常，直到重启时那些事**一件都不在**。

        提交前再次核对门卫及磁盘版本，防止编码期间封库或旧快照覆盖。
        """
        blob = _encode(list(items.values()))      # ⚠️ 编码在动文件之前（见 `_encode`）
        self._verify_publish()
        _replace(self.path, blob, self._verify_publish, self._disk_blob is None)
        self._disk_blob = blob
        self._items = items

    def _verify_publish(self):
        if not self._is_open():
            raise SealedError('unresolved store sealed before commit')
        self._check_disk()

    def _check_disk(self):
        try:
            current = self.path.read_bytes()
        except FileNotFoundError:
            current = None
        if current != self._disk_blob:
            raise UnresolvedConflict('unresolved disk changed; reopen before mutation')

    # ── 三个动词（就这三个 —— 别的不接）────────────────────────
    @_serialized
    def add(self, text: str, *, at: str = "") -> Item:
        """记下一条（**当场落盘**），交回那一条（带着它的把手）。

        ⚠️ 拒绝面：原文不是字符串 ／ 一片空白 ⇒ `UnresolvedError`，
           **而表与盘一个字节都不动**。
           · **空白那一条**的判据抄 `log.py` 的 `append_line`（`not text.strip()`）：
             写侧与读侧得是**同一个谓词**，逐个列字符一定会漏（`"\\t"` ／ 全角空格 ／
             `"\\xa0"` …）。
           · **不是字符串那一条**不是洁癖：`dispatch` 递进来的是**解出来的 JSON**，
             `{"text": 5}` 与 `{"text": null}` 都到得了这里 ⇒ 收下它们就是收下一条
             **醒来时显示成 `None` 的事**。
        ⚠️ **封着 ⇒ `SealedError`（第一句）**：它是 `RuntimeError` 的子类、**不是**
           `UnresolvedError` —— "这一笔合不合法"与"**现在还不许写**"不是一件事
           ⇒ 封着的时候，上面那张单子**整体让位**给这一条（照 `events.py` 的 `append`）。
        """
        # **写之前先抬头看一眼那张纸条** —— 关着就拦下来（`seal` 的消费者就是这一眼）。
        #   ⚠️ 它是**第一句**：一个"先写了一笔才拦"的实现会留下半条记录，
        #      而"被拦下来"的那一次还会**改了状态却没写成**（一个最难查的形状）。
        if not self._is_open():
            raise SealedError("惦记这份表已封 —— 这一笔是关机之后才落下来的")
        self._check_disk()
        if not isinstance(text, str) or not text.strip():
            raise UnresolvedError(
                f"这一条记不下：原文得是一个**非空白**的字符串，收到的是 {type(text).__name__}")
        item = Item(id=uuid.uuid4().hex, text=text, at=str(at or ""))
        if item.id in self._items:
            raise UnresolvedError('unresolved ID already exists; existing item preserved')
        items = dict(self._items)
        items[item.id] = item
        # **2026-10-10：满了就让出最旧的一条** （ 作者拍的上界 ）。
        # ⚠️ **"最旧的"按插入序认** —— 这一份**没有时刻** （见 `__init__`
        # 那一段： "别为了对称给它塞一个"）⇒ 而 dict **保序** ⇒
        # `list(items)[0]` 就是**最早写下的那一条** 。
        # ⚠️ **让出几条是算出来的** （ 不是"只删一条"）—— 那样才不管
        # 一次落下几条都收敛到上界 。
        # ⚠️ **没到上限：这一段一个字都不动** （ 不排序、不去重 ——
        # 那两样都会让"它自己写下的顺序"变样 ， 而**顺序就是它的意思** ）。
        while len(items) > UNRESOLVED_AT_MOST:
            oldest = next(iter(items))
            del items[oldest]
        self._commit(items)
        return item

    @_serialized
    def resolve(self, id: str) -> bool:
        """把一条划掉（**当场落盘**）；它不在这儿 ⇒ `False`（那不是错误）。

        ⚠️ 认的是**把手**，不是那句原文：同一天惦记两遍同一件事是**两件**
           （本仓对"同一句重复"没有规矩 —— 《关于自己》那份契约的"同一条不重复"
           是**那一份文件**的规矩）⇒ 按文本匹配会划掉错的那一件，而没有人说话。
        ⚠️ 划掉 = **不再出现**（§本刀那条判据："了结一件 ⇒ 它不再出现"）。
           有没有一条"已了结"的历史，规格没写 ⇒ **不留**（那是另一次记账，不是这一份）。
        ⚠️ **把手那一侧也是全能的**：递进来的若不是字符串（`{"id": []}` ／ `null` ／ 数字），
           一律算"它不在这儿" ⇒ `False`。不为洁癖 —— 一个**不可 hash** 的值会在
           `id not in self._items` 那一句上抛 `TypeError`，而那是**没声明过**的拒绝面
           （本模块声明的只有 `UnresolvedError`）；`dispatch` 第 6 步会把它接成
           `tool_failed(TypeError: …)`，模型不至于看不见，但拒绝面凭空多一句谁也没说过的话。
        ⚠️ **封着 ⇒ `SealedError`（第一句，排在"它在不在"之前）**：它也是**会写**的那条路
           （划到就落盘）⇒ 关着的时候调用方不该从这里拿到 `True` ／ `False` 里的任何一个
           —— "办了"与"本来就没有"在关机之后**都不是真话**。
        """
        # 门卫是**第一句**：排在把手那一眼之前 ⇒ 一个封着的表**从不**告诉调用方
        #   "这件事本来就没有"（那会是一次**被拦下来**的写被读成"没划到"）。
        if not self._is_open():
            raise SealedError("惦记这份表已封 —— 这一笔是关机之后才落下来的")
        self._check_disk()
        if not isinstance(id, str) or id not in self._items:
            return False
        items = dict(self._items)
        del items[id]
        self._commit(items)
        return True

    def list(self) -> list[Item]:
        """还惦记着的那些 —— **记下的先后**（旧的在前），交出去的是一份**新列表**。

        ⚠️ 次序是承重的（`list()` 就是"醒来时看得到"的那个读口），而它是**一个选择**：
           当前保留文件插入次序，不按可选 at 重排；旧记录可能没有时刻。
        ⚠️ 返回新列表 ⇒ 调用方改它不会动到这份惦记。
        """
        return list(self._items.values())

    def current(self) -> list[Item]:
        """Read the published file without changing this writer's snapshot."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return []
        return list(_decode(raw, self.path).values())


def handlers(store: UnresolvedStore) -> dict[str, Callable[..., str]]:
    """那两个执行段 —— 直接喂给 `tools.build_table(DECLARED_TOOLS, handlers)`。

    ⚠️ **签名就是契约**（`dispatch.py:488-489`：谁写出那 13 个执行段，谁定它）：
       参数怎么递是 `dispatch` 第 6 步定的 —— **对象 ⇒ 关键字**，别的 ⇒ 原样一个位置参数
       ⇒ `{"text": "…"}` 会走到 `unresolved_add(text=…)` 上，而一条非对象的参数
       （例如一个裸字符串）会**原样**落在第一个位置参数上，两条路都通。
    ⚠️ 交回的是**回执**（`dispatch` 拿它当模型看到的那句话）：一句**事实**。
       `add` 那条里**带着把手** —— 少了它，`resolve` 就指不到刚记下的那一件上
       （那是这两个动词**合起来**才成立的最小耦合，不是装饰）。
    ⚠️ 划不到东西时**不许长得像办成了**：回执写"没有这条"。
       （一条静默成功的"划掉"与"它根本不在"在可观察上分不出来 —— `loop.py:60-63`。）

    本函数由 launch 装配进工具表；调用签名、scope 与写入门卫仍须一致。
    """
    def unresolved_add(text: str) -> str:
        # 2026-10-11：记下那一刻 —— 它以后判断得了"这件我揣了多久"。
        import datetime                                # noqa: PLC0415
        item = store.add(text, at=datetime.datetime.now().isoformat())
        return f"记下了：{item.text}（{item.id}）"

    def unresolved_resolve(id: str) -> str:
        if store.resolve(id):
            return f"划掉了：{id}"
        return f"没有这条：{id}"

    return {"unresolved_add": unresolved_add, "unresolved_resolve": unresolved_resolve}
