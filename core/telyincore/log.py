# telyincore/log.py
"""追加写的物理层：一行一个完整 JSON，按天分片；修尾巴是 `repair_all()` 的事。

⚠️ **「启动时修尾巴」是真的了**（Task 2 给了判据，Task 4 接上了时机）：
`repair_all()` 扫描所有分片，
把**不以 `\\n` 收尾**的那半截行截掉 —— 判据在**原始字节**上，因为
`read_lines()` 是个行视图、**故意抹掉了**「结尾有没有换行」这个信号。
只有**半截**被截：收尾完整、只是内容坏掉的行**留着** —— 那是数据损坏，
该被看见，不是断电的必然结果。

⚠️ **而「启动时」这一脚在**上一层**（Task 4 接上了）** —— 本层**刻意不在 `__init__`
里调** `repair_all()`：`LogStore` 只管物理，它不认"启动"是哪个时刻。真的在启动时
调它的是 `EventStore.__init__`（`events.py`），而且**排在扫分片之前**。
⇒ 这句话的两个半边各有归属：**修的判据**在本层（`repair_tail` / `repair_all`），
**修的时机**在上一层（`EventStore.__init__`）。

⚠️ 替换提供完整文件的原子可见性；不把它称为任意硬件断电下的事务保证。截断走
「写同目录临时文件 → `flush` → `fsync` → `os.replace`」。替换之前的普通写入失败不改变
原文件；替换成功后读者看到完整新文件。文件同步不等于目录项已耐久，也不证明硬件已兑现
断电保护；断电或替换返回不确定时必须重读核对，不能保证一定保留旧版或新版。
写下的是**字节**：没有文本层的换行翻译。

**这一层不知道事件的语义** —— 它只认「行」。事件的形状在 `events.py`（S3）。

为什么按天分片：`09-日志与配置.md` §9.5 说
「事件库**不滚** …… **按时间分段存**」。而**主体没有 run 这个概念** ——
信封里 `run?` 是可选的，它不是分片键。

⚠️ 上面那句是**节引**：原文中间有一段用了纪律 1 禁的词（见 `01-主体边界.md`）
⇒ 核心里只留省略号，**别把原话补回去** —— `tests/test_boundary.py` 会当场变红。
"""
from __future__ import annotations

import os
import io
import hashlib
import stat
import fnmatch
import sqlite3
from contextlib import ExitStack, closing, contextmanager
from functools import wraps
from pathlib import Path

from .seal import Gate, SealedError, always_open
from .source_guard import lease
from .derived_cache import CacheDirectory
from .file_signature import signature, descriptor_signature

PREFIX = "events-"


def _leased(function):
    @wraps(function)
    def guarded(self, *args, **kwargs):
        with lease(self.root):
            return function(self, *args, **kwargs)
    return guarded


class _PrefixReader(io.RawIOBase):
    """Stream only the byte prefix present when this read began."""
    def __init__(self, source):
        self.source = source
        self.remaining = os.fstat(source.fileno()).st_size

    def readable(self):
        return True

    def readinto(self, buffer):
        raw = self.source.read(min(len(buffer), self.remaining))
        self.remaining -= len(raw)
        buffer[:len(raw)] = raw
        return len(raw)


class TornShardError(ValueError):
    """目标分片**不以换行收尾**（一截没写完的行）⇒ 拒绝往里追加。

    ⚠️ 它是 `ValueError` 的子类，因为它与 `append_line` 的另外两条守卫是**同一族**：
    "这一行现在写不进去"。
    ⚠️ 而它**必须是一个自己的类型**：上一层（`EventStore.append`）要把**这一条**包成
    `EventError`，而**多行 / 空白**那条**不包**（那是本模块内部不变量坏了，该吵）——
    靠异常消息去分辨那两条是行不通的（改一个字就分家了）。
    """


class RepairReport(dict[str, int]):
    """`repair_all()` 的返回值：**修过的**（文件名 → 截掉字节数）＋ **没修成的那些**。

    ⚠️ 为什么是 `dict` 的子类，而不是换成 `(fixed, failed)` 那样的二元组：
    「**只报真的修过的**」那半个契约**已经被当作一张普通的表在用**（调用方拿它就是拿 dict，
    测试里也是 `== {"events-…jsonl": 12}` 的形状）⇒ **加**一半，不动那一半。
    ⚠️ `failed` 里**只放文件名、不放原因** —— 与本项目给 `EventStore.skipped` 的同一条理由：
    一个 reason 字符串会冻住上层要重写的措辞。顺序与 `day_files()` 同序（名字升序），
    也就是说**它是一份可以逐项核对的事实，不是一句建议**。

    ⚠️ **它为什么存在**：一张**只含修过的片**的表**表达不了"试过而失败"** ⇒
    那个失败是**沉默**的，而沉默的代价已经量出来了：
    一截没修掉的半行 + 一次追加（`append_line` 的守卫）＝ **一条静默丢失的事件**。
    ⚠️ 而它**不是**那条守卫的替代品 —— 两者管的是**不同时刻**：
    `append_line` 的守卫管**写的那一刻**（覆盖补尾**之后**才出现的半截），
    这张表管**启动那一刻**（这次启动修了什么、什么没修成）。
    """

    def __init__(self, fixed: dict[str, int], failed: tuple[str, ...] = ()) -> None:
        super().__init__(fixed)
        self.failed = failed


def _regular_repair_file(info):
    return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and not getattr(info, 'st_file_attributes', 0) & 0x400
            and (not hasattr(os, 'getuid') or info.st_uid == os.getuid()))


@contextmanager
def _repair_output(path):
    # Keep the established orphan filename, but never truncate through a link
    # or a path that was replaced between inspection and opening. Cooperating
    # callers serialize repair; external edits are checked before publication.
    try:
        previous = path.lstat()
    except FileNotFoundError:
        previous = None
    if previous is not None and not _regular_repair_file(previous):
        raise OSError('unsafe tail repair staging file')
    flags = os.O_RDWR | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    if previous is None:
        flags |= os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        opened, current = os.fstat(fd), path.lstat()
        if (not _regular_repair_file(opened) or not _regular_repair_file(current)
                or not os.path.samestat(opened, current)
                or (previous is not None and not os.path.samestat(previous, opened))):
            raise OSError('tail repair staging file changed before write')
        os.ftruncate(fd, 0)
        stream = os.fdopen(fd, 'wb')
        fd = None
        with stream:
            yield stream
    finally:
        if fd is not None:
            os.close(fd)


def _repair_digest(source, start, length, *, no_newline=False):
    source.seek(start)
    digest = hashlib.sha256()
    remaining = length
    while remaining:
        block = source.read(min(65536, remaining))
        if not block or (no_newline and b"\n" in block):
            raise OSError('source tail changed during repair')
        digest.update(block)
        remaining -= len(block)
    return digest.digest()


def repair_tail(path: Path, *, expected_signature=None, expected_tail=None) -> int:
    """`LogStore.repair_tail` 的实现 —— 提出来给**两家共用**。

    ⚠️ **判据与前提的全文在那个方法的 docstring 里**（它一个字都没动，这里不抄第二遍）：
    写两份的话，改一份忘另一份，两边就又开始分岔 —— `append_line` 那句
    「判据与读侧是**同一个谓词**」防的正是它。
    """
    path = Path(path)
    if not path.exists():
        if expected_signature is not None:
            raise OSError('source disappeared before tail repair')
        return 0
    # Most shards are complete. Inspect the tail before allocating any body.
    with path.open('rb') as source:
        initial = descriptor_signature(source.fileno())
        if expected_signature is not None and initial != expected_signature:
            raise OSError('source changed since tail classification')
        source.seek(0, os.SEEK_END)
        size = source.tell()
        if not size:
            return 0
        source.seek(-1, os.SEEK_END)
        if source.read(1) == b"\n":
            return 0
        position = size
        cut = 0
        while position:
            start = max(0, position - 65536)
            source.seek(start)
            block = source.read(position - start)
            index = block.rfind(b"\n")
            if index >= 0:
                cut = start + index + 1
                break
            position = start
        # Timestamps can coalesce for rapid same-size edits. Bind the decision
        # to bytes too, and verify the reverse scan still describes this tail.
        source.seek(max(0, cut - 1))
        if cut and source.read(1) != b"\n":
            raise OSError('source tail boundary changed')
        tail_digest = _repair_digest(source, cut, size - cut, no_newline=True)
        if expected_tail is not None and (len(expected_tail) != size - cut
                or hashlib.sha256(expected_tail).digest() != tail_digest):
            raise OSError('source tail bytes changed since classification')
        tmp = path.with_name(path.name + ".repair")
        source.seek(0)
        payload = hashlib.sha256()
        with _repair_output(tmp) as out:
            identity = descriptor_signature(out.fileno())[:2]
            remaining = cut
            while remaining:
                block = source.read(min(65536, remaining))
                if not block: raise OSError('shard changed during tail repair')
                out.write(block)
                payload.update(block)
                remaining -= len(block)
            out.flush()
            os.fsync(out.fileno())
    # Re-read only the finite expected prefix. A fixed orphan path is not
    # authority: an external replacement, same-size edit or growth must never
    # be published as repaired source. Memory stays bounded by the copy block.
    if not _regular_repair_file(tmp.lstat()):
        raise OSError('unsafe tail repair staging file')
    with tmp.open('rb') as check:
        staged = descriptor_signature(check.fileno())
        if staged[:2] != identity or staged[2] != cut:
            raise OSError('tail repair staging file changed before publication')
        observed = hashlib.sha256()
        remaining = cut
        while remaining:
            block = check.read(min(65536, remaining))
            if not block:
                raise OSError('tail repair staging file shortened before publication')
            observed.update(block)
            remaining -= len(block)
        if check.read(1) or observed.digest() != payload.digest():
            raise OSError('tail repair staging bytes changed before publication')
    # Compare the finite original bytes, not just metadata: native change
    # timestamps may be equal for distinct writes within one clock tick.
    if signature(path) != initial:
        raise OSError('source changed during tail repair')
    with path.open('rb') as current:
        if (descriptor_signature(current.fileno()) != initial
                or _repair_digest(current, 0, cut) != payload.digest()
                or _repair_digest(current, cut, size - cut) != tail_digest
                or current.read(1)):
            raise OSError('source bytes changed during tail repair')
    # Detect observed source edits, replacements and deletion, including
    # same-size edits with restored mtime. This is not filesystem CAS against
    # a hostile writer racing the final check and rename.
    if signature(tmp) != staged or signature(path) != initial:
        raise OSError('source or staging file changed during tail repair')
    os.replace(tmp, path)
    return size - cut


class LogStore:
    """一个目录下的若干 `events-YYYY-MM-DD.jsonl`。"""

    def __init__(self, root: Path, *, is_open: Gate = always_open, create: bool = True) -> None:
        self.root = Path(root)
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        # ⚠️ **默认门卫是 `seal.always_open` —— 一个活陷阱，而这是刻意的、必须点名**：
        #    「没有门卫」与「门卫恒开」在**签名上长得一样** ⇒ 一个本该被封的写会悄悄过去。
        #    它不做成必填，是为了那 100+ 条**不要门卫**的既有构造（它们测的是"写进去没有"，
        #    不是关机）⇒ 生产路径**必须显式递一个真的**（由 `app.build()` 造）。
        self._is_open = is_open

    def path_for(self, day: str) -> Path:
        return self.root / f"{PREFIX}{day}.jsonl"

    def day_files(self) -> list[Path]:
        # ⚠️ 按**名字**升序 —— 日期是定长的 ISO 格式，所以字典序就是时间序。
        return sorted(self.root.glob(f"{PREFIX}*.jsonl"))

    def iter_day_files(self, *, strict=False):
        return self._iter_files(f'{PREFIX}*.jsonl', strict=strict)

    def _iter_files(self, pattern, *, strict=False):
        """Snapshot names in Path order without retaining every Path in memory.

        Small directories stay in memory; larger inventories spill to a private,
        disposable SQLite index. Finish enumeration before yielding, so a repair
        cannot add its own temporary files to the inventory being consumed.
        Consumers that stop early must close this iterator.
        Strict callers require a complete inventory: only an initially absent
        directory is empty; other opening/iteration failures are propagated.
        """
        with ExitStack() as resources:
            names = []
            database = None
            ordinal = 0
            try:
                scanner = os.scandir(self.root)
            except FileNotFoundError:
                return
            except OSError:
                if strict:raise
                return  # Match pathlib.glob's unavailable-directory behavior.
            with scanner:
                while True:
                    try:
                        entry = next(scanner)
                    except StopIteration:
                        break
                    except OSError:
                        if strict:raise
                        return
                    name = entry.name
                    if not fnmatch.fnmatch(name, pattern):
                        continue
                    if database is None and len(names) < 256:
                        names.append(name)
                        continue
                    if database is None:
                        directory = resources.enter_context(
                            CacheDirectory())
                        database = sqlite3.connect(str(Path(directory) / 'names.sqlite3'))
                        resources.callback(database.close)
                        database.execute('PRAGMA cache_size=-256')
                        database.execute('PRAGMA synchronous=OFF')  # Disposable inventory.
                        database.execute('CREATE TABLE names (sort_key BLOB, ordinal INTEGER, '
                                         'name BLOB, PRIMARY KEY(sort_key, ordinal)) WITHOUT ROWID')
                        for ordinal, previous in enumerate(names):
                            database.execute('INSERT INTO names VALUES(?,?,?)',
                                (os.path.normcase(previous).encode('utf-8', 'surrogatepass'),
                                 ordinal, previous.encode('utf-8', 'surrogatepass')))
                        ordinal = len(names)
                        names.clear()
                    database.execute('INSERT INTO names VALUES(?,?,?)',
                        (os.path.normcase(name).encode('utf-8', 'surrogatepass'),
                         ordinal, name.encode('utf-8', 'surrogatepass')))
                    ordinal += 1
            if database is None:
                for name in sorted(names, key=os.path.normcase):
                    yield self.root / name
            else:
                database.commit()
                with closing(database.execute('SELECT name FROM names ORDER BY sort_key, ordinal')) as rows:
                    for (name,) in rows:
                        yield self.root / name.decode('utf-8', 'surrogatepass')

    def append_line(self, line: str, *, day: str) -> Path:
        """追加**一行**（自动补 `\\n`），返回写进的那个分片。

        ⚠️ **封了库之后，这里先抛 `SealedError`**（第 ④ 刀的门卫，见 `seal.py`）——
        它是 `RuntimeError` 的子类、**不是** `ValueError` ⇒ 下面那两条判据（一行 / 尾巴）
        **整体让位**给这一条。而它必须是**第一句**：一个被拦下来的写**一点痕迹都不留**。

        ⚠️ **收的必须是不含换行的单行**，否则 `ValueError` —— 因为「盘上一行」
        就是这个类的契约，而补尾（Task 2）建在它上面：
        · 多行（现实事故：`json.dumps(..., indent=2)`）会被写成好几行 ⇒
          `read_lines` 把**一次追加**读成**好几条**；
        · 自带收尾 `\\n` 的输入会多写一个**空行** —— 而空行**读不回来**。
        ⇒ 两条都是写侧与读侧对「什么是一行」的分歧 ⇒ 宁可当场拒，也不写半截。

        ⚠️ **判据与读侧是同一个谓词**：`line.strip()` 得非空，且不含 `\\n` / `\\r`。
        为什么必须"同一个"：读侧（`read_lines`）正是拿 `line.strip()` 判一条在不在
        ⇒ 守卫若只认"空串"（`not line`），两边之间就剩一段缺口，而缺口里站着**一整族**
        输入（`"  "`、`"\\t"`、`"\\xa0"`、`"\\u3000"` ……）：写侧说"我写了一条"、
        读侧说"没有这一条"。⇒ 统一到**读侧那一个谓词**，这一族一起关；
        **逐个列字符一定会漏。**
        （`\\r` 尤其要拦 —— 这台机器是 Windows，而**下一个写这个库的人未必走
        `json.dumps`** ⇒ 一旦走了别的路，`\\r` 会把**一条静默地读成两条**，
        而补尾的前提正建立在"一条就是一条"上。）

        ⚠️ **编码发生在动文件之前** —— 因为一个写不进去的字符（孤立代理项之类）
        若在 `open()` **之后**才炸，盘上会留下一个**空分片**：它在 `day_files()` 里
        是个合法分片、只是零条事件 ⇒ 补尾会去扫它、`_scan` 会去看它 ⇒ **幽灵分片**。
        ⇒ 一次拒绝，一点痕迹都不留。（写下去的是**字节**：落盘的就是 UTF-8 ＋ `\\n`，
        中间没有文本层的换行翻译政策 —— 补尾要在字节上判尾巴。）

        ⚠️ **而写之前还要核另一半：目标分片必须以 `\\n` 收尾**，否则 `TornShardError`。
        理由不是洁癖，是**静默丢数据**：往一截没写完的行后面追加，会**粘出**一条谁也解不开
        的行；而它**以 `\\n` 收尾** ⇒ `repair_tail` 的判据**再也看不见它** ⇒
        那条事件在**下一次启动**时消失。（现实入口：片被别的进程占着 ⇒
        `repair_all()` 的那次 `os.replace` 抛 ⇒ 那一片**留在盘上没修**，而启动照常完成。）
        ⚠️ **不在这里截断**：截是 `repair_tail` 的事、时机是"启动"（`EventStore.__init__`）
        ⇒ 写路径上**静默截断**等于替别人丢数据。**当场拒**才是本层的口径。
        ⚠️ 判据与 `repair_tail` 是**同一个谓词**（`not data or data.endswith(b"\\n")`）⇒
        **空片不算坏**：补尾会把"只有一截半行"的分片截成 0 字节，那是它的正常产物。
        ⚠️ 只读**最后一个字节**（不 `read_bytes()`）—— 追加是热路径，
        不为这一眼把整片（可能几十 MB）读进来。
        非空分片必须先成功读取末尾。只有文件不存在时才允许直接创建；权限错误、
        共享锁或其他读取错误都交给调用方，不能把“无法核对”当成“尾部完整”。
        否则可写不可读的半截记录会与新记录粘连，之后两条都无法正常读取。
        """
        # **写之前先抬头看一眼那张纸条** —— 关着就拦下来（`seal` 的消费者就是这一眼）。
        #   ⚠️ 它是 `append_line` 的**第一句**：一行要么落在盘上、要么当场被拒，
        #      不许出现"改了一半状态才拦"的形状。
        if not self._is_open():
            raise SealedError("日志库已封 —— 这一笔是关机之后才落下来的")
        if not line.strip() or "\n" in line or "\r" in line:
            raise ValueError("append_line 只收一行：不得为空白、且不含换行（补尾建在这个契约上）")
        data = (line + "\n").encode("utf-8")
        path = self.path_for(day)
        with lease(self.root):
            # The gate may close while another writer/repair owns the lease.
            if not self._is_open():
                raise SealedError("日志库已封 —— 等待期间写入权限已撤销")
            return self._append_bytes(path, data)

    def _append_bytes(self, path: Path, data: bytes) -> Path:
        # A missing shard may be created. Every other probe failure must stop
        # the append; write access does not prove the existing tail is complete.
        try:
            has_bytes = path.stat().st_size > 0
            # ⚠️ 读那一跳**也在 try 里** —— `st_size > 0` 之后走的就是它，见 docstring 那两个面。
            if has_bytes:
                with open(path, "rb") as fh:
                    fh.seek(-1, os.SEEK_END)
                    if fh.read(1) != b"\n":
                        raise TornShardError(
                            f"{path.name} 不以换行收尾（一截没写完的行）⇒ 拒绝追加："
                            "粘上去的那一条谁也解不开，而补尾的判据再也看不见它")
        except FileNotFoundError:
            pass
        with open(path, "ab") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        return path

    def read_lines(self, path: Path) -> list[str]:
        """一个分片的**原始行视图** —— 本层**不修尾**，也不判断行是不是合法 JSON。

        ⚠️ **最后一行可能是个半截片段**（断电留下的那种），它会**原样**作为最后一个
        元素返回：签名里没有任何"这行是坏的"的信号 ⇒ 下游若信这个名字直接
        `json.loads`，会在**尾部**炸。补尾是 Task 2 的 `repair_tail` 的事。

        ⚠️ **补尾的判断必须在原始字节上做**（`path.read_bytes().endswith(b"\\n")`），
        **绝不能用这个函数的输出** —— 它按行读、按行 `rstrip`，**故意抹掉了**
        "结尾到底有没有换行"这个信号 ⇒ 从这里**看不出**尾巴是不是完整的。
        """
        return list(self.iter_lines(path))

    def iter_lines(self, path: Path):
        """Read a finite prefix, preserving UTF-8 and universal-newline behavior.

        Appends after opening belong to a later read; no write lease or source
        modification is needed. The prefix may still contain an incomplete tail.
        """
        path = Path(path)
        try:
            source = open(path, 'rb')
        except FileNotFoundError:
            return
        with source, io.TextIOWrapper(io.BufferedReader(_PrefixReader(source)),
                                     encoding='utf-8') as fh:
            for raw in fh:
                line = raw.rstrip("\n")
                if line.strip():
                    yield line

    @_leased
    def repair_tail(self, path: Path) -> int:
        """截掉最后那半截行；返回截掉的字节数（0 = 没坏）。

        ⚠️ 判定规则：**最后一行不以 `\\n` 结尾** ⇒ 它是半截的。
        不断言「必须是合法 JSON」—— 那样会把「真的写坏的一行」和「断电留下的半截」
        混为一谈；前者该被看见，后者该被截掉。

        ⚠️ **判据在原始字节上**（空文件或末字节为 `b"\\n"`），
        **不能用 `read_lines()` 的输出** —— 它是行视图，`rstrip` 之后
        「结尾有没有换行」这个信号就没了。而写下来的是**字节**
        （`append_line` 先编码再 `open(..., "ab")`），所以这里也认字节。
        ⚠️ **前提：这个判据只对 `append_line` 写出的分片成立。**
        它是「一行一个完整对象 ＋ 收尾 `\\n`」的**写侧契约**的另一半 ——
        一个**不是**本类写出来的文件（逐块拼的、别的工具导的），若以一条完整的行结尾
        却**没有**尾换行，**会被判成半截而截掉**。⇒ 分片目录是 `append_line` 的地盘；
        要往里塞别的东西，先让它以 `\\n` 收尾。
        """
        return repair_tail(path)

    @_leased
    def repair_all(self, *, _record=None) -> RepairReport | None:
        """修**所有**分片；返回 `RepairReport` —— **修过的那些** ＋ **没修成的那些**。

        ⚠️ **只报修过的**（`dict` 的那一半）：没坏的填进去会让「启动时报告了什么」变成噪声，
        而这张表存在的唯一理由就是「这次启动，盘上有几处断电的痕迹」。

        ⚠️ **而它的另一半是 `.failed`**（fix wave 加，见 `RepairReport`）：
        「试过、但没修成」原先**表达不出来** —— 而那不是措辞问题：那个沉默的代价是
        **一条静默丢失的事件**（一截没修掉的半行 + 一次追加 ⇒ `append_line` 的守卫）。
        ⇒ 跳过的那一片现在**有名有姓**，而不是"没出现在表里"。

        ⚠️ **一片修不动 ⇒ 跳过那一片，其余照修。** 启动扫描许的是「尽力修完」，
        不是「修不动就整体起不来」：Windows 下 `os.replace` 会抛 `PermissionError`
        （片被一个**不共享删除**的 handle 占着 —— 本库自己的 `open(..., "ab")` 就是，
        或者片是只读）⇒ 没有这个 `except`，**一片被占用会让 `EventStore.__init__` 起不来**。
        跳过的那片保持**原样**（那正是「原子」的可见后果），它留下的 temp 由下一趟收。
        ⚠️ 而它**不是**"追加到那一片上也是安全的"：那条不变量由 `append_line` **当场**守住
        （`TornShardError`）—— 跳过只说明"这一趟没修"，不说明"这一片没问题"。

        ⚠️ 开头的清扫是给**惰性孤儿**的：`.repair` **不会**被 `day_files()` 收
        （glob 是 `events-*.jsonl`）⇒ 没人清就是永久垃圾。定长 temp 名让
        「崩 → 重启」的主路径**自愈**（下一趟直接覆盖它）；只有当那片**后来不再坏**时
        才成孤儿，而这一趟把它收掉。**两者配合，不是二选一。**

        ⚠️ **清扫自己也要包** —— 这是同一个炸半径**在同一个函数里**的第二处：
        `unlink` 在 Windows 上会抛，而入口**两条都实测过**：
        · `.repair` 只读 ⇒ `PermissionError` [WinError 5]；
        · `.repair` 被占用（另一个进程正开着它）⇒ `PermissionError` [WinError 32]
          —— 最便宜的触发是**第二个进程在第一个正跑 `repair_tail` 的时候启动**。
        逃出去的话，`repair_all()` 会在**清扫这一步**炸 ⇒ 启动起不来 ——
        而「启动」正是这个方法承诺的东西。⇒ 收掉不到就是**没那一趟**，仅此而已。
        ⚠️ **收不掉的不进 `.failed`** —— 那半个契约说的是「**分片**没修成」：
        孤儿清不掉时，那个分片本身**没有断电的痕迹要修**（修得对不对由 `repair_tail` 说）。

        ⚠️ **`MemoryError` 故意不吞 —— 但它必须指名是哪一个分片。**
        判据：`OSError` 那一族说的是「**这一片**现在动不了」⇒ 跳过它、继续扫是对的；
        `MemoryError` 说的是「**这台机器**撑不住了」⇒ 跳过这一片去读下一片只会更糟
        （下一次读取也可能撑不住，而那时启动已经走了一半、还什么都没报）。
        ⇒ **中止是对的**；但只在异常里写「内存不够」，现场就不知道是哪个片把它耗干的
        （现实入口：某一天的分片大得离谱）。本层不另有日志设施 ⇒ 信息只能随异常走。
        """
        with closing(self._iter_files('*.jsonl.repair')) as paths:
            for stale in paths:
                try:
                    stale.unlink(missing_ok=True)
                except OSError:
                    pass                     # 收不掉就是没那一趟：这不该挡住启动
        # EventStore streams these facts to its private snapshot. The public
        # no-argument API keeps its complete standalone RepairReport contract.
        fixed = {} if _record is None else None
        failed = [] if _record is None else None
        with closing(self.iter_day_files()) as paths:
            for path in paths:
                try:
                    n = self.repair_tail(path)
                except OSError:
                    if _record is None:
                        failed.append(path.name)
                    else:
                        _record(path.name, None)
                    continue                     # 一片坏不该让整条启动失败
                except MemoryError as exc:
                    raise MemoryError(f"{path}：读这一片把内存耗干了") from exc
                if n:
                    if _record is None:
                        fixed[path.name] = n
                    else:
                        _record(path.name, n)
        return RepairReport(fixed, tuple(failed)) if _record is None else None
