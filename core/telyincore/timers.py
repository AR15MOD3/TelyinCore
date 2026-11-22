# telyincore/timers.py
"""持久定时器（规格 `08-定时器与心跳.md` §8.1）：一张**跨重启**的闹钟表。

接口 = 规格那五个 ＋ 一个"到点判定 ＋ 触发"：

    set(at, payload) -> id ／ cancel(id) ／ list() ／ clear_all() ／ on_due(handler)
    due(now)                    # 谁去"滴答"不归本模块 —— 见末段

 规格**加星**的三条，逐条落在下面：
    · **跨重启恢复** —— 每一次 `set` ／ `cancel` **当场落盘**（见「落盘」）；
    · **载荷对框架不透明** —— 本模块**一个字都不看**它（见「载荷」）；
    · **到期时先从表里移除，再调 handler** —— 次序在 `due` 里，理由也写在那里。

── 落盘：**一张快照 ＋ 原子替换**，不是"追加 ＋ 修尾巴" ────────────────
⚠️ **这是本模块与 `events.py` ／ `log.py` 分开的那一处，所以要说清**：
  那两层的写侧是**只增不减**的（一行一条、追着写、坏了截尾巴），而**这张表会增也会减**
   —— `cancel` ／ `clear_all` ／ 到期三样都要从表里**拿走**东西。两条路都不通：
   · **追加**表达不了"拿走"：只能再写一条墓碑，于是 `list()` 变成"把整个文件读一遍再折一次"，
     而文件对一张反复增删的表**只增不减地长**；`clear_all()` 也就不是"清空"了。
   · **`repair_tail` 在这里没有活干**：它修的是"一截没写完的行"，而那种半截只会由
     `append_line` 那种**原地追加**产生。本模块用 `os.replace` 换整份文件 ⇒
     盘上任何一刻都是**上一份完整的**或**这一份完整的**，没有"半截"这种状态。
     ⇒ 一条永远不被触发的修补路径 = 死代码（本仓栽过死代码：一份过时的副本让变异打偏，
       整整一轮的结论因此错了）⇒ **不抄它**。
⇒ 抄的是 `log.py` 里**另一半**约定（`repair_tail` 与 `_replace` 共用的那一段）：
   **字节写法 ＋ 同目录临时文件 → flush → fsync → `os.replace`**，`fsync` 必须排在替换
   **之前**（只换名不落盘的话，断电后是"重命名已生效、内容却是空的"）。
⚠️ 而**每一次改动都当场落盘**（不是退出时）：跨重启那句"关掉再开，闹钟还在"
   只有在盘上**当下就有它**的时候才成立 —— 退出才落的实现，验收时（两个 store 同时活着）
   **一个闹钟都读不到**。

── 载荷：**不透明 ≠ 不留类型** ────────────────────────────────────────
⚠️ 本模块不许解释载荷 —— 不看键、不看形状、不加字段、不改写它（②）。
   而它**也不是没有要求**：载荷要能**原样读回来**。盘与内存之间只有 JSON 这一条路，
   一个表示不了的载荷（`set`、自定义类、深到爆的嵌套）若被收下，就成了一条
   **重启后静默消失**的闹钟 —— 而那正是 §8.1 存在的理由。
   ⇒ 判据是 `events.py` 的 W1 那一条：**一个库不该写出它自己读不回来的行** ⇒ 写之前当场拒，
   拒绝之前**一个字都不动**（表不变、盘不变）。

── 时刻：按实际时间比较 ─────────────────────────────────────────────
ISO 时间统一到 UTC 后排序和判断到期；没有偏移量的旧时间按本地时区解释。
保存时保留原写法。测试或旧调用注入的不透明时钟仍沿用原比较规则，彼此必须可比。
构造里的 `clock` 决定没有传 `now` 时使用的时刻。

── 门卫（`seal.Gate`）**接上了** —— 与 `events.py` ／ `log.py` 同形 ──────
构造收一个 `is_open: Gate = always_open`，而**每一条会写的路**（`set` ／ `cancel` ／
`clear_all` ／ `due`）**第一句**查它：封着 ⇒ 当场 `SealedError`，而**表与盘一个字节都不动**。
· ⚠️ **哪几条算"会写的路"是量出来的**，不是从名字上猜的：`set` 必落盘；
  `cancel` 只在**真撤到**一只时才落；`clear_all` 只在**表非空**时才落；
   而 `due` **名字里读不出"它会写"** —— 到点的那一只要从表里（内存 ＋ 盘）移除
  ⇒ 它也是写口，也有那一眼。
· ⚠️ **门卫是"第一句"而不是"写之前那一句"**（`events.py` 的 `append` ／ `log.py` 的
  `append_line` 就是这个形状）：摆在状态改动**之后**的话，一次被拦下来的写**已经改了
  状态**、而那是最难查的一种形状 ⇒ 每条路的第一句、排在算几点 ／ 查表 ／ 克隆表之前。
· ⚠️ **默认那个是 `seal.always_open` —— 一个活陷阱，而这是刻意的、必须点名**：
  「没有门卫」与「门卫恒开」在**签名上长得一样** ⇒ 一个本该被封的写会悄悄过去
  （`seal.py` ／ `log.py` 都点过这个名）。它不做成必填，是为了那 100+ 条**不要门卫**的
  既有构造（它们测的是"写进去没有"，不是关机）⇒ **生产路径必须显式递一个真的**
  （由 `app.build()` 造，读 `loop.sealed` —— 与那两本账本同一个门卫）。
  ⇒ 这件事由**两条**分工钉着，别读成一条：
  · **本模块这半边**归 `tests/test_timers.py`（本刀）：参数化的
    `…refuses_every_mutating_entry…`（`set` ／ `cancel` ／ `clear_all` 各一次）、
    `…does_not_fire_a_due_alarm…`、`…is_seal_s_always_open_by_name…` ＋
    `…is_the_always_open_one_and_the_trap_is_named…`，以及**修复轮补的那两条 `due` 的**：
    `…falling_in_the_middle_of_a_due_round_stops_it…`（封库落在 `await handler` 期间 ⇒
    这一趟**到此为止**）与 `…sealed_empty_table_is_still_loud…`（封着 ＋ 表空 ⇒
    门卫**先于**那个提前返回）；
  · **生产路径那半边**（"`build()` 手上那个门卫是真的"＋"`seal()` 之后写口真的抛"）
     **装配那一刀把它兑现了** —— **两条，都在 `tests/test_app.py`**：
    · `tests/test_app.py:589` `test_build_hands_the_real_gate_to_the_two_new_stores`：
      四条写口各判一次（`set` ／ `cancel` ＋ `add` ／ `resolve`），而**防真空**那一句是
      "没封 ⇒ 进得去" —— 本模块这两条写口就在里面；
    · `tests/test_app.py:624` `test_the_gate_reads_the_loops_sealed_flag_on_all_four_stores`：
      端到端 —— `loop.seal()` 之后四本各自**在公开写口上**拒写。
    ⚠️ **行号只算到写它这一刻**（本仓的规矩：别拿它当坐标）；成法照**那两条更老的兄弟**
    （`…wires_a_real_gate…` ／ `…reads_the_loops_sealed_flag…` —— 它们钉的是
    `events` ／ `log` 那半边）。

── 盘上那份读不回来时：**吵**，而不是跳过 ─────────────────────────────
⚠️ 与 `events.py` 的宽容**长得不一样，而那个不一样是刻意的**：那边是只增不减的库
   ⇒ 跳过一片读不出来的分片、其余照读是对的（跳过的那片还在盘上，下一个人看得见）。
   这里的下一次写会**整个盖掉**这个文件 ⇒ 一个"跳过它、当空表"的实现会让那截读不回来的
   数据**永久消失**，而且是**静默**的 ⇒ 读不回来就 `TimerError`，而吵之前**一个字都不写**。

── 不属于本模块的（刻意不做） ────────────────────────────────────────
· **心跳的规则 ／ `schedule_wake` 的编排**（`heartbeat.py` ／ `wake.py`，S8-B）；
· **"谁去滴答"** —— §8.8 结掉的是**"打点器"那一半**：不用自己发明那个"一直转、
  隔一会儿看一次"的东西，**等**由**事件循环**负责 ⇒ 本模块只给"表"与"到点判定"
  （`due`），不含任何循环、不含 sleep；
  ⚠️ **而它没有结掉另一半**（fix wave I4 改准的一处"说满了"）：§8.8:167-169 说
  「闹钟 = `asyncio` 的定时器（`loop.call_later` 之类）」—— 而**没有任何循环会自己来调
  `due()`**，`set()` 也**只交出 id**（接口里唯一有订阅形状的是 `on_due`，
  那是**响的时候**、不是**排的时候**）⇒「谁在滴答、谁在排下一次」是一个**必须有人接的
  接缝** —— 见 `_OWED["due_driver"]` ／ `_OWED["rearm"]`，**别当成已结的事**；
· **"该说什么"** —— §8.9 末句把"被叫醒"与"决定开口"分给了两层，而**后者不是这一层**
  （那句话里有一个词在本仓核心树里是禁词 ⇒ 这里只留省略号，别把原话补回去：
  见 `log.py` 顶上同一处的做法，`tests/test_boundary.py` 会当场变红）。
  本模块只报"到点了"，回一个 handler；
· **不碰 `loop.py`**（逐字节禁改；`sleep` 是个注入面，"谁来等"归装配那一层）。

⚠️ **门卫（`seal.Gate`）原先列在这一节里**（"今天没有接上"）—— **本刀把那一行删了**：
   接上的那个形状写在上面那一节，而理由与当时那条裁定**不冲突**：那时本模块**没有**
   那个形参，硬加一个就等于造一个假接缝；如今加上了，而**递不递真的**归生产路径
   （`app.build()`，与那两本账本同一个门卫）⇒ 这一节里不再有它。
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import os
import uuid
import threading
import tempfile
from functools import wraps
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .seal import Gate, SealedError, always_open
from .time_order import clock_key
from . import snapshot_json


def _serialized(function):
    @wraps(function)
    def guarded(self, *args, **kwargs):
        with self._mutation_lock:
            return function(self, *args, **kwargs)
    return guarded

#: 表就落在这一个文件里（`root` 是个**目录** —— 与 `events.py` ／ `log.py` 同一条约定）。
FILE = "timers.json"

#: 旧版临时名后缀；新写入使用带随机后缀的独占文件，不打开已有临时路径。
TMP_SUFFIX = ".tmp"

#: 一条记录的键**恰好**是这三个（写侧与读侧跑的是同一张表）。
_RECORD_KEYS = frozenset(("id", "at", "payload"))

#: **本模块欠着的那几样**（名字 → 欠主）—— 一条不落。判据是 `loop.py:60-63`
#: 那条家规的另一面：**留空可以，但"空了"这件事必须有人说得出来**。
#: ⚠️ 它与模块 docstring 是**同一件事的两种形态**（那段给人读，这张表给测试读），
#:    照 `unresolved._OWED` ／ `tools._SCHEMA_OWNERS` ／ `dispatch._HANDLER_OWNERS` 的成法。
#: ⚠️ 与那几张表**不是同一笔债**：那几张说的是**别的工具的 schema ／ 执行段**，
#:    这一张说的是**本模块自己点名欠着的这几样**（`unresolved._OWED` 的姊妹表）。
#: ⚠️ 键是**稳定的机器名**（改措辞不会红），值里必须**点名**（空话会红）。
#: ⚠️ 原先这里还有第三样 `seal_gate`（"装配那一层"）—— **本刀把它还掉了**：
#:    构造收下 `is_open`，而那四条会写的路各自第一句查它（见模块 docstring 那一节）。
#:    ⇒ 那一笔**从这张表里删掉**，而不是留一个"已经还了"的名字：
#:    这张表说的是**今天还欠着的**，而一条已经兑现的债留在里面就是**一句假话**
#:    （`tests/test_timers.py` 那条按名字钉的表会当场红 —— 那正是它要的对话）。
_OWED: dict[str, str] = {
    "due_driver": (
        " ★ **已经接上了**（2026-10-13 订正）。原先写的是：装配那一层 ——"
        "**§8.8 只说了一半**（「等」归事件循环，而**没有任何循环会自己来调 `due()`**）。"
        " 今天的事实：**两处在滴** —— 入口那条 20 秒的滴答（`launch._the_tick` 每 20 秒"
        " `due()` 一次）＋ `App.run()` 起的 `pacing.drive`（它按表里最早那只睡）。"
        " ⚠️ 本模块自己仍然不含循环、不含 sleep（那是**接口**的事，不是缺口）。"),
    "rearm": "装配那一层（滴答那一边自己要算的一脚）—— 三件事都得由它做，"
             "因为 `set()` **只交出 id**（接口里唯一有订阅形状的是 `on_due`，"
             "而那是**响的时候**、不是**排的时候** ⇒ '又添了一只'这个口子本模块没有）："
             "① 每添一只都要排一次；② 响过之后按 `list()` 里**最早**那只重排"
             "（`due()` 一次叫响的是**所有**到点的，不是一只）；"
             "③ 排之前**先滴一次** —— 一只 `at <= now` 的不会自己冒出来，"
             "也不该被'等下一只'挡在后面 —— "
             "★ **2026-10-12（作者：「一起解开」）**：① 那一半今天有兑现的路了"
             "（它定闹钟那一手走 `App.arm` ⇒ 写表与置 `wake` 一次做完）；"
             "而**机器自己那几手**写在**滴答那一层**里（`launch._the_tick`）—— "
             "那条路每 20 秒自己 `due()` 一次 ⇒ 也在"
}


def _now_iso() -> str:
    """默认时钟：**本地时区**的 ISO 字符串（与 `events.py` 的 `_now_iso` 同形）。

    保持秒精度和本地偏移量的既有保存格式；比较时由 clock_key 归一到 UTC。
    """
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class TimerError(ValueError):
    """这一笔进不了这张表 —— 载荷写不出去 ／ 盘上那份读不回来。

    ⚠️ 调用方**只捕它** —— 不要让裸 `TypeError` ／ `json.JSONDecodeError` ／ `KeyError`
       逃出去（照 `events.py` 的 `EventError` 那条：拒绝面必须是**说得出的那几条**）。
    """


class TimerConflict(TimerError):
    """The caller's timer snapshot is stale; no write was performed."""


class NoDueHandlerError(RuntimeError):
    """到点了，却没有 handler 可调。

    ⚠️ 是 `RuntimeError` 的子类而不是裸的（照 `seal.py` 的 `SealedError`）：调用方要能
       **只接"没人接那一响"**，而放过别的 `RuntimeError`；本仓不许按措辞分支。
    ⚠️ 为什么是抛而不是静默跳过：一个到点的闹钟**静默消失**与"它根本没到点"在可观察上
       分不出来 —— 那正是 `loop.py:60-63` 那条家规禁的东西。
    """


@dataclass(frozen=True)
class Timer:
    """表里的一条：**到点时刻** ＋ **一件对框架不透明的活儿**。

    ⚠️ 只有这三样（`id` ／ `at` ／ `payload`）—— 本模块不往里加任何自己的记账字段：
       加一个"创建时刻"之类的东西，就是**框架对载荷这件事的第一次越界**，
       而它的负担会一路落到调用方身上。
    """

    id: str
    at: Any
    payload: Any


def _encode(table: dict[str, Timer]) -> bytes:
    """把一张表编成**一个 JSON 对象 ＋ 收尾换行**的字节；编不出来 ⇒ `TimerError`。

    ⚠️ 键的次序由表的次序定（按时刻、同一个时刻按 id）⇒ **同一张表编出的字节一样**。
    ⚠️ `ensure_ascii=False` 与 `events.py` 的 `to_line()` 同一个写法（非 ASCII 原样落盘）。
    ⚠️ **编码排在动文件之前**（`log.py` 那条）：一个写不进去的字符若在 `open()` **之后**
       才炸，盘上会留下一个空文件。
    ⚠️ **整段编码都在这一个 `try` 里面 —— 三句一句都不许留在外面**（fix wave I1）：
       `sorted(...)` 与 `.encode("utf-8")` **各自都会抛**，而它们留在 `try` 外面时逃出去的
       是**裸的** `TypeError` ／ `UnicodeEncodeError` —— 那正是 `TimerError` 那句 docstring
       说的"不许"（拒绝面只剩**说得出的那几条**）。
        两处都在 BASE（`ac5c1d3`）上**实测**过，而**两处的拦截者都不是 `json.dumps`**：
         · `json.dumps({"p": "x\\ud800y"}, ensure_ascii=False)` **成功**（孤立代理项是合法
           `str`，JSON 那一层不拦它）⇒ 拦住它的是**后面那句 `.encode`**；
         · `at` 混了写法（一个 ISO 串 ＋ 一个 `5`）时 `json.dumps` 根本还没轮到
           ⇒ 先炸的是 `sorted` 那个键 `(t.at, t.id)` 的**比较**。
       ⇒ 判据是"**整段编码**在这一道闸里"，不是"`json.dumps` 在这一道闸里"。
       ⚠️ `unresolved.py` 的 `_encode` **一直是这么写的**（那个模块的 `.encode()` 就在它
       自己的 `try` 里）—— 这里此前漏了，而测试也没有它的镜像
       （`tests/test_timers.py` 的同款那条就是补它）。
    """
    try:
        records = [{"id": t.id, "at": t.at, "payload": t.payload}
                   for t in sorted(table.values(), key=lambda t: (clock_key(t.at), t.id))]
        text = snapshot_json.dumps({"timers": records}, ensure_ascii=False, separators=(",", ":"))
        return (text + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise TimerError(f"这一笔写不下去（载荷或时刻表示不了）：{exc}") from exc


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
                raise TimerConflict('timer disk appeared before publication; reopen before retrying') from error
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
                primary.add_note('timer staging cleanup failed: ' + type(error).__name__)


def _sweep(path: Path) -> None:
    """仅清理旧版固定名称的临时文件；不发布或删除随机名称的孤儿。

    ⚠️ 收不掉就是**没那一趟**，仅此而已 —— 它不许挡住启动（`log.py` 扫 `.repair`
       孤儿时同一条裁定：Windows 上 `unlink` 会抛，而"启动"正是这里承诺的东西）。
    """
    try:
        path.with_name(path.name + TMP_SUFFIX).unlink(missing_ok=True)
    except OSError:
        pass


def _decode(raw: bytes, path: Path) -> dict[str, Timer]:
    """把盘上那份读回一张表 —— **读不回来就吵**（理由见模块 docstring 那一节）。

    ⚠️ 键的集合**逐一核**（顶层只要 `timers`；一条记录恰好三个键）：拿键去"投影"会让
       一个多余的键**静默消失** —— 而这里下一次写会整份盖掉，于是它不是"被忽略"，
       是**被删掉**（`events.py` 的 `_scan` 里那段说的正是"投影看不见未知键"这个盲区，
       那边只增不减所以只是看不见，这边会真丢）。
    """
    try:
        data = snapshot_json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise TimerError(f"{path} 读不回来：{exc}") from exc
    if not isinstance(data, dict) or set(data) != {"timers"}:
        raise TimerError(f"{path} 的形状不对：顶层要是**只带** timers 的对象")
    records = data["timers"]
    if not isinstance(records, list):
        raise TimerError(f"{path} 的 timers 不是一个数组")
    table: dict[str, Timer] = {}
    for i, record in enumerate(records):
        if not isinstance(record, dict) or set(record) != _RECORD_KEYS:
            raise TimerError(f"{path} 的第 {i} 条不是一条只带 id/at/payload 的记录")
        if not isinstance(record["id"], str) or not record["id"]:
            raise TimerError(f"{path} 的第 {i} 条的 id 不是一个非空字符串")
        if record["id"] in table:
            raise TimerError(f"{path} 的第 {i} 条与前面某条同 id：{record['id']}")
        table[record["id"]] = Timer(id=record["id"], at=record["at"],
                                    payload=record["payload"])
    return table


class TimerStore:
    """`root` 目录下的一张闹钟表（表在那个目录里的 `timers.json` 这一个文件里）。

    实例内变更串行执行；合作写者通过源目录租约提交。旧快照遇到磁盘变化时
    拒绝写入及无操作的成功答复，调用方须重新打开。due 的 await 交错另行检查。
    """

    def __init__(self, root: Path, *, clock: Callable[[], str] = _now_iso,
                 is_open: Gate = always_open) -> None:
        self.root = Path(root)
        self._mutation_lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        # ⚠️ **默认门卫是 `seal.always_open` —— 一个活陷阱，而这是刻意的、必须点名**：
        #    「没有门卫」与「门卫恒开」在**签名上长得一样** ⇒ 一个本该被封的写会悄悄过去。
        #    它不做成必填，是为了那 100+ 条**不要门卫**的既有构造（它们测的是"写进去没有"，
        #    不是关机）⇒ 生产路径**必须显式递一个真的**（由 `app.build()` 造，
        #    读 `loop.sealed` —— 与那两本账本同一个门卫）。
        #  ⚠️ 而"生产路径到底递没递"这件事，**本文件这边一条测试都钉不住**（`set` 由谁调
        #    是另一层的事）：那半边归 `tests/test_app.py`（见模块 docstring 那一节）。
        self._owner_gate = None
        self._is_open = lambda: is_open() and (self._owner_gate is None or self._owner_gate())
        self._handler: Callable[[Timer], Awaitable[None]] | None = None
        self._prepare_due = None
        self._table: dict[str, Timer] = self._load()

    @_serialized
    def bind_owner_gate(self, gate: Gate) -> None:
        if not callable(gate):
            raise TypeError('timer owner gate must be callable')
        if self._owner_gate is not None and self._owner_gate is not gate and self._owner_gate():
            raise RuntimeError('timer store already belongs to an active owner')
        self._owner_gate = gate

    # ── 它在盘上的哪儿 ──────────────────────────────────────────
    @property
    def path(self) -> Path:
        """这张表落在哪一个文件里。"""
        return self.root / FILE

    def _load(self) -> dict[str, Timer]:
        """启动时那一脚：先收孤儿，再读表（**读不回来就吵**）。"""
        from .source_guard import lease
        with lease(self.root):
            _sweep(self.path)
            try:
                raw = self.path.read_bytes()
            except FileNotFoundError:
                self._disk_blob = None
                return {}
            except OSError as exc:
                raise TimerError(f"{self.path} 读不动：{exc}") from exc
            self._disk_blob = raw
            return _decode(raw, self.path)

    @_serialized
    def _check_disk(self):
        from .source_guard import lease
        with lease(self.root):
            try:
                current = self.path.read_bytes()
            except FileNotFoundError:
                current = None
            if current != self._disk_blob:
                raise TimerConflict('timer disk changed; reopen before retrying')

    def _commit(self, table: dict[str, Timer]) -> None:
        """**先落盘、再换进内存**（落盘失败 ⇒ 内存一动不动、盘上也没痕迹）。

        ⚠️ 这个次序就是"一行要么落在盘上、要么当场被拒"那条家规在本模块的形状：
           反过来（先换内存再写）时，一次写失败会留下一份**只活在内存里**的表 ——
           它看起来一切正常，直到重启时那些闹钟**一只都不在**。

        ⚠️ 这里**不查门卫** —— 那一眼在**调用方**（`set` ／ `cancel` ／ `clear_all` ／
           `due`）的**第一句**上，理由见那些地方（落点只有一个：本方法的调用方恰好
           就是那四条会写的路，而它们各自还得在**不得不用 `_commit` 的那一次之前**
           被拦下来 —— 一个把门卫摆在这里的实现会让 `cancel` ／ `clear_all` 的
           **空趟也跟着抛**）。⇒ 别在这两处各写一份：那会让"门卫在哪"有两个答案。
        """
        blob = _encode(table)               # ⚠️ 编码在动文件之前（见 `_encode`）
        from .source_guard import lease
        with lease(self.root):
            self._verify_publish()
            _replace(self.path, blob, self._verify_publish, self._disk_blob is None)
            self._disk_blob = blob
            self._table = table

    def _verify_publish(self):
        if not self._is_open():
            raise SealedError('timer store sealed before commit')
        self._check_disk()

    # ── 表的基本动作 ────────────────────────────────────────────
    @_serialized
    def set(self, at: Any, payload: Any) -> str:
        """加一只闹钟（**当场落盘**），返回它的 id。

        ⚠️ id 是**每一次现场生成的**（`uuid4`），不是从表里数出来的序号：
           表会减 ⇒ "最大编号 + 1"在一个空表上会**从头再来**，而重发的 id
           会让一只早就撤掉的闹钟复活。
        ⚠️ 拒绝面：载荷表示不了 ⇒ `TimerError`，**而表与盘一个字节都不动**。
        ⚠️ **封着 ⇒ `SealedError`（第一句，排在生成 id 之前）**：它是 `RuntimeError`
           的子类、**不是** `TimerError` —— 载荷合不合法讲的是「这一笔能不能变成
           一条持久记录」，而封库讲的是「**现在还不许写**」，两者不是一件事
           ⇒ 封着的时候，上面那张单子**整体让位**给这一条（照 `events.py` 的 `append`）。
        """
        # **写之前先抬头看一眼那张纸条** —— 关着就拦下来（`seal` 的消费者就是这一眼）。
        #   ⚠️ 它是**第一句**：一个"先写了一半才拦"的实现会把半条记录留在盘上／内存里，
        #      而"被拦下来"的那一次还会**改了状态却没写成**（一个最难查的形状）。
        if not self._is_open():
            raise SealedError("闹钟表已封 —— 这一笔是关机之后才落下来的")
        timer = Timer(id=uuid.uuid4().hex, at=deepcopy(at), payload=deepcopy(payload))
        if timer.id in self._table:
            raise TimerError('timer ID already exists; existing schedule preserved')
        table = dict(self._table)
        table[timer.id] = timer
        self._commit(table)
        return timer.id

    @_serialized
    def cancel(self, id: str) -> bool:
        """撤一只闹钟（**当场落盘**）；它不在表里 ⇒ `False`（那不是错误）。

        ⚠️ **封着 ⇒ `SealedError`（第一句，排在"它在不在"之前）**：它是**会写**的那条
           路之一（撤到就落盘）⇒ 关着的时候调用方不该从这里拿到任何一份"办成了"
           或"本来就没有"的答复 —— 那两样在关机之后**都不是真话**。
        """
        # 门卫是**第一句**：排在"它在不在"那一眼之前 ⇒ 一个封着的表**从不**告诉
        #   调用方"这件事本来就没有"（那会是一次**被拦下来**的写被读成"没撤到"）。
        if not self._is_open():
            raise SealedError("闹钟表已封 —— 这一笔是关机之后才落下来的")
        self._check_disk()
        if id not in self._table:
            return False
        table = dict(self._table)
        del table[id]
        self._commit(table)
        return True

    @_serialized
    def reschedule(self, id: str, at: Any, *, expected_at: Any) -> Timer:
        """Move one live alarm atomically, preserving its ID and opaque payload.

        Serialized with other mutations. A consumed alarm is never
        recreated. Compare-and-swap prevents stale diagnostic requests winning.
        """
        if not self._is_open():
            raise SealedError("timer store sealed")
        self._check_disk()
        old = self._table.get(id)
        if old is None:
            raise KeyError(id)
        if old.at != expected_at:
            raise TimerConflict("timer changed; refresh before retrying")
        if at == old.at:
            return deepcopy(old)
        moved = Timer(id=old.id, at=deepcopy(at), payload=old.payload)
        table = dict(self._table)
        table[id] = moved
        self._commit(table)
        return deepcopy(moved)

    @_serialized
    def list(self) -> list[Timer]:
        """表里的全部闹钟，**按到点时刻**排（同一个时刻按 id）。

        ⚠️ 返回的是一个**新列表**：调用方改它不会动到表。
        ⚠️ 次序是承重的：§8.4 只让**最早到期**的那一个持有闸门。
        ⚠️ ISO 时间按实际时刻比较；不透明旧时钟仍须彼此可比。
        """
        return deepcopy(sorted(self._table.values(), key=lambda t: (clock_key(t.at), t.id)))

    @contextmanager
    def current_view(self):
        """Read current published rows and hold off cooperating schedule writers.

        This is a read/decision boundary, not a reload of this writer's baseline.
        Keep the mutation-lock/source-lease order used by scheduling operations.
        """
        from .source_guard import lease
        def read():
            try:
                return self.path.read_bytes()
            except FileNotFoundError:
                return None
        with self._mutation_lock, lease(self.root):
            raw = read()
            table = {} if raw is None else _decode(raw, self.path)
            rows = deepcopy(sorted(table.values(), key=lambda t: (clock_key(t.at), t.id)))
            if read() != raw:
                raise TimerConflict('timer disk changed during current read')
            yield rows

    @_serialized
    def clear_all(self) -> int:
        """清空整张表（**当场落盘**），返回清掉几条。

        ⚠️ 空表上**不写盘**：盘上那份已经等于"空"，再写一遍只是白改一次文件。
        ⚠️ **封着 ⇒ `SealedError`（第一句，排在"表空不空"之前）**：与 `cancel` 同一条 ——
           它也是**会写**的那条路（表非空就落盘），而空表那一支的 `0` 在关机之后
           同样不是调用方该拿到的答复。
        """
        # 门卫是**第一句**（空表那条早退排在它后面）：见上面那句。
        if not self._is_open():
            raise SealedError("闹钟表已封 —— 这一笔是关机之后才落下来的")
        self._check_disk()
        n = len(self._table)
        if n:
            self._commit({})
        return n

    @_serialized
    def on_due(self, handler: Callable[[Timer], Awaitable[None]], *, prepare=None) -> None:
        """登记**唯一的**到期处理器（规格原话）—— 后登记的顶掉先前的。"""
        self._handler = handler
        self._prepare_due = prepare

    @_serialized
    def _take_due(self, identifier, moment):
        if not self._is_open():
            return None
        self._check_disk()
        live = self._table.get(identifier)
        if live is None or clock_key(live.at) > clock_key(moment):
            return None
        # Persist recoverable work before removing its only scheduling record.
        # Preparation must be idempotent; failure leaves the timer untouched.
        if self._prepare_due is not None:
            self._prepare_due(deepcopy(live), moment)
        table = dict(self._table)
        del table[live.id]
        self._commit(table)
        return live

    # ── 判定 ＋ 触发 ────────────────────────────────────────────
    async def due(self, now: Any = None) -> list[Timer]:
        """把**到点的**都叫一遍，返回真的响过的那些（按响的次序）。

        ⚠️ **时刻的优先级**：`now` 传了就用它（一个字都不许再看别处）；没传才问
           `clock`。这是判据 ④ 的全部内容 —— 而这个签名也是接口里那两样各占一半：
           `clock` 管"没传时算几点"，`now` 管"传了就用传的那个"。

        ⚠️ **次序是承重的**：每响一只，都**先从表里移除**（内存 ＋ 盘一起），再调 handler
           （规格原话）。反过来写的话，handler 一抛，那只闹钟**永远留在表里** ——
           之后每一次滴答都在同一个地方再抛一次，而没有任何东西会把它拿走。

        ⚠️ **而"先从表里移除"要**跨过 `await` 也成立**（预检扫出来的风险 B）：
           `candidates` 是进这个方法那一刻的**旧快照**，而 handler 是 `await` 的 ⇒
           这个 `for` 的两次迭代之间**别的 `due` 可以插进来**。
           ⇒ 每一轮都**回头看一眼表**（`live is None` ⇒ 这一只已经被别处领走了），
           而不是拿着旧快照闷头往下调 handler。少了这一眼，同一只闹钟会响两遍。

        ⚠️ **没有 handler 时当场抛，而表一条都不动**：拒绝要一点痕迹都不留。
        ⚠️ **一只 handler 抛了，这一趟不停（fix round ①）**：每只**单独捕** → 记下 →
           **接着叫后面那些**；整趟走完再把**第一只**那个异常**原样**抛出去
           （类型不变 ⇒ 调用方的 `except` 照旧管用），并在它身上挂一条**说明是哪只闹钟**的注记。
           · **为什么必须接着走**：那只抛了的**已经从表里移除了** ⇒ 这一趟要是不叫它后面的，
             那些**永远不会再响**（下一次滴答也看不见它们），而它们**一个都没做错事**。
           · **为什么还要抛**：失败必须**可见** —— "这一趟少响了几只"不许无声无息。
           · **能"当场穿过去"的是那些"不是 `Exception` 子类"的 `BaseException`** ——
             判据就是那句 `except Exception` 本身：`issubclass(type(exc), Exception)` 为假。
             **典型的就是** `CancelledError` ／ `KeyboardInterrupt` ／ `SystemExit` ／
             `GeneratorExit`，以及 **`BaseExceptionGroup`**（实测 3.11.9：
             `BaseException.__subclasses__()` 里不是 `Exception` 子类的**正好这五个**；
             `TaskGroup` 里**两处以上**的错、其中有一个不是 `Exception` 时抛的就是它）。
             —— 取消就是"现在停下"，不能为了"把这一趟走完"而吞掉它。
             ⚠️ **那几个名字只是例子，不是穷举**（fix round 3 改准的第二处"说满了"）：
             这张表**是开着的** —— 语言以后还能加新的直接子类，用户也能自己
             `class X(BaseException)` ⇒ **判据以那句 `issubclass` 为准，别照抄名单**。
             ⚠️ **`MemoryError` 不在此列**（fix round 2 改准的一处**假话**：上一版把它和
             `CancelledError` 并列写成"当场穿过去"，而 `issubclass(MemoryError, Exception)`
             在 3.11.9 上是 **`True`** ⇒ 它走的是**记下 ＋ 整趟走完再重抛**那条路）。
             本模块**刻意不改**这一点：每只闹钟**在调 handler 之前**就已经从表里移除
             ⇒ 中途停下**一只都不丢** ⇒ 没有一条非改不可的落地路径
             （而"改行为要有理由、要能指着坏实现说它为什么坏"）。
           · **规矩是"不许静默吞掉"** —— 与 `log.py` 捕 `MemoryError` 去**点出那个分片**
             （它也没有吞掉它）**同一条规矩**；⚠️ **别读成"两处的处置相同"**。
        ⚠️ **空表上先回头**（fix wave 的便宜项）：一张没有闹钟的表**没有任何可判的事**
           ⇒ 在那条路上**连时钟都不问**（`clock` 管的是"没传 `now` 时算几点"，
           而一张空表不需要知道几点）。少了那一下，**每一次滴答**都要先去问一次时钟
           —— 而滴答的频率远高于"表里有东西"的频率 ⇒ 每一趟空转都白问一次；
           注入的时钟要是会抛（`tests/test_timers.py` 里那个就是），空表也当场炸。
        ⚠️ **封着 ⇒ `SealedError`（第一句，排在"表空不空"之前）**：`due` **也是写口** ——
           到点的那一只从表里（内存 ＋ 盘）移除，而那正是 `cancel` ／ `clear_all` 做的
           同一件事。⚠️ **这一点从名字上读不出来**（"到点判定"听起来是只读的）⇒ 一条
           只给前三样挂门卫的实现**照样响得起来**，而那时关机已经发生了
           （`tests/test_timers.py::test_a_sealed_store_does_not_fire_a_due_alarm` 钉它）。
        ⚠️ **而这一条路上多出了一眼**（本刀 fix round 加的）：那个循环的**每一轮**
           都重读一次门卫（理由逐条写在循环顶上那一处）。**进方法那一眼不够，而这
           不是锦上添花** —— 循环里有 `await handler(live)` 这个挂起点，一次落在它
           期间的封库会让第 2..n 只**照样响**（`tests/test_timers.py` 的
           `test_a_seal_falling_in_the_middle_of_a_due_round_stops_it` 钉它 ——
           ⚠️ 这里此前写的那个名字（`…does_not_fire_after_a_mid_flight_seal…`）**查无此测试**：
           它是两条真测试的名字拼出来的，而按名字找测试的人会当场找不到；
           而对照是"一次 `due` 两支"那条 —— 那里**没人封库** ⇒ 两只照样都响）。
           ⇒ 开场那句"关着的时候一声都不许响"**指的是每一次**，不是"进方法那一次"。
        """
        # 门卫是**第一句**：关着的时候**一声都不许响** —— 响了就等于
        #   "取走 ＋ 调 handler"这两件事都发生了，而它们都落在写口上。
        if not self._is_open():
            raise SealedError("闹钟表已封 —— 这一笔是关机之后才落下来的")
        self._check_disk()
        if not self._table: # 空表：连时钟都不问（见 docstring 末条）
            return []
        moment = self._clock() if now is None else now
        candidates = [t for t in self.list() if clock_key(t.at) <= clock_key(moment)]
        if not candidates:
            return []
        handler = self._handler
        if handler is None:
            raise NoDueHandlerError(
                f"{len(candidates)} 只闹钟到点了，却没人登记到期处理器 —— "
                "静默丢掉那一响与「它根本没到点」分不出来")
        fired: list[Timer] = []
        failures: list[tuple[Timer, Exception]] = []
        retryable_storage_failure = False
        for candidate in candidates:
            # **每一轮都重读一次门卫** —— 这一眼是**这一条路上唯一多出来的**那一处，
            #    而它非有不可：循环里有 **`await handler(live)` 这个挂起点** ⇒
            #    一次**落在那个 `await` 期间**的封库，若只在进方法时查过一次，
            #    第 2..n 只**照样被移除、照样写进盘、handler 照样跑** ——
            #    而封库的意思是"**现在停下**" ⇒ 一次封库只让这一趟**到此为止**。
            #  ⚠️ **`events.py` ／ `log.py` 那两处没有这个问题**：它们的写口**没有内部的
            #    挂起点**（一次 `append` 同步走到底）⇒ 查一次就够了 ——
            #    **这个坑是"有 `await` 的写口"独有的**，别照抄那两处的写法。
            #  ⚠️ **而"已经响过的那几只"照旧算数**：它们在封库**之前**就结算完了
            #    （`_commit` 落过盘、handler 跑完了）⇒ 不撤、不回滚 ——
            # 下面 `return fired` 交出去的就是它们； 而**这一趟没轮到的那些留在表里**。
            #  ⚠️ **用 `break` 而不是 `continue`**：封了之后**这一趟一只都不许再响** ——
            #    两者语义一样，而 `break` 把"到此为止"写在一处（`continue` 会白问 n 次）。
            if not self._is_open():
                break
            try:
                live = self._take_due(candidate.id, moment)
            except (SealedError, TimerConflict):
                # These invalidate the whole writer, not just this reminder.
                raise
            except Exception as exc:
                # A preparation/dequeue failure leaves this schedule on disk.
                # Other due alarms still get their own preparation attempt.
                import sqlite3
                retryable_storage_failure |= isinstance(exc, (OSError, sqlite3.OperationalError))
                failures.append((candidate, exc))
                continue
            if live is None:
                continue
            fired.append(live)
            try:
                await handler(live) # 再调 handler
            except Exception as exc:                 # 一只抛了 ⇒ 记下来，**这一趟接着走**
                failures.append((live, exc))
        if failures:
            first_timer, first_exc = failures[0]
            # Preserve the historical first-exception contract without losing
            # a later schedule's retryable storage fault behind a hard error.
            first_exc.timer_retryable_storage_failure = retryable_storage_failure
            # ⚠️ **注记而不是改写消息**：`exc.args` 是别人的（可能是库抛的、调用方按它分支）
            #    ⇒ 动它等于替别人改数据；注记挂在同一条异常上、**类型与 `args` 一个都不动**。
            # ⚠️ "不动它"这件事**有测试钉着**（`…first_failure_is_the_one_that_comes_out…`
            #    读的就是 `args`）：少了那条断言，一个把 id 拼进 `args` 的实现照样全绿。
            # ⚠️ **`add_note` 改的是调用方那个异常对象本身** ⇒ 一个**被缓存/当单例**用的异常
            #    会跨趟累积注记（每抛一次多两条）。本模块不改这一点：另一个办法是包一层新类型，
            #    而那就把"抛出来的是**第一只**那个异常、类型原样"这条契约换掉了。
            # ⚠️ 这一条守的是**只有它有**的那点信息：**抛出来的那一只是哪个**。
            #    另外两条给的都是**集合**（"其它抛了的 id"、"叫响了的 id"）—— 从集合里
            #    推不出哪一个才是手上这条异常的主人（还有成功的那几只混在里面）。
            #    ⇒ 它**不是**冗余，不许当冗余删掉；而"它有牙"由
            #    `…first_failure_is_the_one_that_comes_out…` 里那条
            #    **要求 id 与"第一个抛的"出现在同一句注记里**的断言钉着。
            first_exc.add_note(
                f"这一响属于闹钟 {first_timer.id}：到点的**都叫完了**，这只是第一个抛的")
            if len(failures) > 1:
                rest = "、".join(t.id for t, _ in failures[1:])
                first_exc.add_note(f"同一趟里还有 {len(failures) - 1} 只也抛了：{rest}")
            # ⚠️ **`fired` 走这条路就交不出去了**（要抛异常，返回值就没了）⇒ 写进注记：
            #    调用方看 traceback 时**既知道谁炸了、也知道这一趟还叫响了谁**
            #    （少了这一句它只知道前半句）。
            first_exc.add_note(
                f"这一趟一共叫响了 {len(fired)} 只：{'、'.join(t.id for t in fired)}")
            raise first_exc
        return fired
