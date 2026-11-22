# telyincore/skills.py
""" **做法（程序性记忆）的治理层** —— 稳定身份 ／ 可逆合并 ／ 有效条目口径（2026-10-17 批③）。

 **为什么要有这一层**：做法原先只是 `做法/*.md` 一堆文件，身份就是**文件名** ——
 于是改名就换了身份、合并只能靠"再写一条新的"、已并入的旧条目在列表 ／ 检索 ／ 计数里
 仍然算"有效"。这一层**不改那些正文**，只在旁边记一份**权威的元数据**：

 ```
 做法/<标题>.md          ← 正文（旧文件原样保留；这一层**不重写**它们）
 做法/.ids.json          ← 权威元数据：稳定 ID ／ 版本 ／ 状态 ／ 并入目标 ／ 操作账
 做法/.history-<id>/     ← 我们**自己**动过的那几版（改名不搬历史；合并前留一版）
 做法/.ops/<op>.json     ← 合并操作的账（阶段 ＋ 载荷摘要 ＋ 结果）
 ```

 **四条口径**（这一层的判据都从这里来）：
 · **ID 稳定**：ID 由 `.ids.json` 分配；**改名不换 ID**（按正文版本认出"这还是那一条"）；
   重复登记、重启都不会重新分配。
 · **读不写正文**：`register()` 只动元数据；外部改了正文 ⇒ 只更新版本号，**不"帮"那份正文改回去**。
 · **有效 only**：`rows()` 默认只交 `active`；`merged` 要显式要（`include_merged=True`）。
   列表 ／ 计数 ／ 喂索引 全部走这一条 —— 于是"已并入"的旧条目不会再单独冒出来。
 · **合并可逆**：合并要**由发起方自己给出最终正文**；系统只负责版本、归属、历史与恢复，
   **不代写内容、不判同义**。

 ⚠️ **这一层不碰人格文件**（`HER_FILES` 那一族）：做法是使用者的经验，不是人设。
 ⚠️ **不引入相似度**：合并的来源是**发起方点名的 ID**，不是"看起来像"。
"""
from __future__ import annotations
from .source_guard import writer as _source_writer

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Mapping

#: **共同锁**（2026-10-17 补验）：按"哪一棵"隔离；同一棵里的
#: 登记 ／ 修订 ／ 合并 ／ 恢复 ／ 回滚 **共用一把**（`RLock`，同一线程可重入）。
#: ⚠️ 它只护**进程内**；跨进程仍然没有文件锁（见工单里的边界）。
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(home: Any) -> "threading.RLock":
    key = os.path.normcase(str((Path(home) / SKILL_TREE).resolve()))
    with _LOCKS_GUARD:
        got = _LOCKS.get(key)
        if got is None:
            got = threading.RLock()
            _LOCKS[key] = got
        return got


@contextmanager
def _locked(home: Any):
    lock = _lock_for(home)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


#: 那一棵（与 `memory.SKILL_TREE` 同一个名字 —— 这一层不另立一条根）。
SKILL_TREE = "做法"
SKILL_KINDS = {"communication": "沟通做法", "operation": "操作技能", "unclassified": "待分类"}


def kind_of(entry: Mapping[str, Any]) -> str:
    return entry.get("kind") or "unclassified"


def reference_ids(text: str) -> list[str]:
    pattern = r"\[\[(sk-\d{4,}|kb-[0-9a-f]{32}|event:[1-9]\d*)\]\]"
    return list(dict.fromkeys(re.findall(pattern, text)))


def validate_kind(kind: str) -> None:
    if kind not in SKILL_KINDS:
        raise SkillRejected("类别须为 communication 或 operation；历史条目可保留待分类。")

#: 权威元数据（在那一棵**里面**：谁搬这一棵，元数据跟着走）。
INDEX_NAME = ".ids.json"
#: 我们动过的那几版。
HISTORY_PREFIX = ".history-"
#: 合并操作的账。
OPS_DIR = ".ops"
CREATE_INTENT = ".create-intent.json"
REVISION_INTENT = ".revision-intent.json"
#: 元数据格式版本。
FORMAT = 1
#: ID 的形状：`sk-` ＋ 四位起（够用；不做"永不上限"的承诺，见 `_next_id`）。
_ID_SHAPE = re.compile(r"^sk-(\d+)$")


# ── 错误那几件（都要**说得出话**）────────────────────────────────────────
class SkillError(RuntimeError):
    """ 这一层所有拒绝的基类（子类带更多字段，日志与回执读它们）。"""


class SkillRejected(SkillError):
    """ 参数不合法（ID 不存在 ／ 越权 ／ 重复 ／ 操作载荷不一致）。"""


class SkillAmbiguous(SkillError):
    """ 按标题找，但**不止一条** —— 必须列候选，**不许静默取第一条**。"""

    def __init__(self, said: str, candidates: list[dict]) -> None:
        self.said = said
        self.candidates = candidates
        super().__init__(
            "「" + str(said) + "」对得上 " + str(len(candidates)) + " 条做法："
            + "、".join(one["id"] + "（" + one["title"] + "）" for one in candidates)
            + " —— 用 ID 指定要哪一条。")


class SkillConflict(SkillError):
    """ 版本对不上（别人改过了）—— **不覆盖**。"""

    def __init__(self, said: str, expected: str, actual: str) -> None:
        self.said = said
        self.expected = str(expected)
        self.actual = str(actual)
        super().__init__(
            "「" + str(said) + "」在读完以后被改过了（我读到 "
            + self.expected[:12] + "…，盘上现在 " + self.actual[:12] + "…）—— 这一版不落。")


# ── 小工具 ────────────────────────────────────────────────────────────────
def version_of(text: str) -> str:
    """ 正文版本的算法 —— 与 `web._version_of` **同一条**（逐字节 sha256）。"""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _tree(home: Any) -> Path:
    return Path(home) / SKILL_TREE


def _index_path(home: Any) -> Path:
    return _tree(home) / INDEX_NAME


def _local_path(home: Any, relative: str) -> Path:
    """Never follow a metadata/history path outside this original skill tree."""
    root = _tree(home).absolute()
    path = root / relative
    if path.resolve() != path.absolute() or root not in path.parents:
        raise SkillRejected('做法文件路径越界或含有链接。')
    return path


def _history_stamp(stamp: str) -> str:
    value = str(stamp or 'auto').strip() or 'auto'
    if value in ('.', '..') or any(c in value for c in '/\\\x00'):
        raise SkillRejected('历史版本标记不能包含路径分隔符。')
    # ISO stamps contain colons; retain their meaning in metadata while giving
    # history files a portable name (including Windows, which rejects colons).
    return re.sub(r'[:<>"|?*\x01-\x1f]', '_', value).rstrip(' .') or 'auto'


def _file_name(file_name: str) -> str:
    if (not isinstance(file_name, str) or Path(file_name).name != file_name
            or any(c in file_name for c in '\\:\x00') or Path(file_name).suffix.lower() != '.md'):
        raise SkillRejected('做法登记只接受本目录中的 Markdown 文件名。')
    return file_name


def _atomic_json(path: Path, body: Any) -> None:
    """ 元数据**原子**写（临时文件 → 关闭 → 替换；失败不留半份）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, made = tempfile.mkstemp(prefix="." + path.name + ".tmp-", dir=str(path.parent))
    tmp = Path(made)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            json.dump(body, stream, ensure_ascii=False, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def _atomic_text(path: Path, text: str, *, expected: str | None = None,
                 verify=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, made = tempfile.mkstemp(prefix="." + path.name + ".tmp-", dir=str(path.parent))
    tmp = Path(made)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(str(text).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck after preparing the replacement, including time spent saving
        # history/intent. Cooperating writers hold the source lease throughout;
        # this detects observed external edits, not an OS-level compare/swap.
        if verify is not None:
            verify()
        if expected is not None:
            if path.resolve() != path.absolute() or not path.is_file():
                raise SkillRejected('做法正文路径已变化；不发布替换。')
            actual = version_of(_read_body(path))
            if actual != expected:
                raise SkillConflict(path.name, expected, actual)
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def _read_body(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _blank_index() -> dict:
    return {"format": FORMAT, "next": 1, "entries": {}, "ops": {}}


def load(home: Any) -> dict:
    """ 读那份权威元数据（没有就交一份空的 —— **不建文件**）。"""
    path = _index_path(home)
    if path.resolve() != path.absolute():
        raise SkillRejected("做法元数据路径无效。")
    if not path.is_file():
        return _blank_index()
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise SkillRejected('做法元数据必须是对象')
    if body.get("format") != FORMAT:
        raise SkillRejected("做法那本的元数据版本不认得：" + str(body.get("format")))
    body.setdefault("next", 1)
    body.setdefault("entries", {})
    body.setdefault("ops", {})
    if not isinstance(body['entries'], dict) or not isinstance(body['ops'], dict):
        raise SkillRejected('做法元数据条目或操作表无效。')
    for key, entry in body['entries'].items():
        if not isinstance(key, str) or not _ID_SHAPE.fullmatch(key) or not isinstance(entry, dict):
            raise SkillRejected('做法元数据条目身份无效。')
        _local_path(home, _file_name(entry.get('file')))
    return body


@_source_writer
def save(home: Any, index: Mapping[str, Any]) -> None:
    _atomic_json(_index_path(home), index)


def _sync_creation_directory(home):
    if os.name != 'nt':
        for directory in (_tree(home), Path(home)):
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def _new_body(path, text):
    """Publish a creation without replacing a concurrently appearing file."""
    fd, name = tempfile.mkstemp(prefix='.create-body-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(text.encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.resolve() != path.absolute() or not path.is_file() or _read_body(path) != text:
                raise SkillRejected('做法创建目标已变化；保留正文与恢复记录。')
    finally:
        temporary.unlink(missing_ok=True)


def _creation_entry(file_name, text, kind, recorded_at, scope):
    from .retrieval_scope import valid_scope
    _file_name(file_name)
    validate_kind(kind)
    if (not isinstance(text, str) or not isinstance(recorded_at, str)
            or not isinstance(scope, str) or (scope and not valid_scope(scope))):
        raise SkillRejected('做法创建内容或来源范围无效。')
    version = version_of(text)
    return {'file': file_name, 'title': Path(file_name).stem, 'version': version,
            'state': 'active', 'merged_into': None, 'op': None,
            'registered': version, 'kind': kind, 'recorded_at': recorded_at,
            **({'scope': scope} if scope else {})}


def _recover_creation(home):
    """Caller holds source and skill locks; never infer provenance from a body."""
    intent = _local_path(home, CREATE_INTENT)
    if not intent.exists():
        return
    try:
        plan = json.loads(intent.read_text(encoding='utf-8'))
        entry, key, text = plan['entry'], plan['id'], plan['text']
        if (type(plan.get('format')) is not int or plan['format'] != 1 or not isinstance(key, str)
                or not _ID_SHAPE.fullmatch(key) or type(plan['next']) is not int
                or plan['next'] <= int(_ID_SHAPE.fullmatch(key)[1])
                or entry != _creation_entry(entry['file'], text, entry['kind'],
                                           entry['recorded_at'], entry.get('scope', ''))):
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise SkillRejected('做法创建恢复记录无效；保留现场，不猜测来源。') from error
    index = load(home)
    existing = index['entries'].get(key)
    owners = [ident for ident, row in index['entries'].items()
              if os.path.normcase(row['file']) == os.path.normcase(entry['file'])]
    if (existing is not None and existing != entry) or owners not in ([], [key]):
        raise SkillRejected('做法创建身份已变化；保留元数据与恢复记录。')
    target = _local_path(home, entry['file'])
    if target.exists():
        if not target.is_file() or _read_body(target) != text:
            raise SkillRejected('做法创建目标已变化；保留正文与恢复记录。')
    else:
        _new_body(target, text)
    _local_path(home, entry['file'])
    _sync_creation_directory(home)
    if existing is None:
        index['entries'][key] = entry
        index['next'] = max(int(index['next']), plan['next'])
        save(home, index)
    _sync_creation_directory(home)
    intent.unlink()
    _sync_creation_directory(home)


@_source_writer
def recover_creation(home):
    with _locked(home):
        _recover_creation(home)


@_source_writer
def create_body(home, file_name, text, *, kind='unclassified', recorded_at='', scope=''):
    """Journal body, stable ID and scope before publishing a new skill."""
    entry = _creation_entry(file_name, text, kind, recorded_at, scope)
    with _locked(home):
        _recover_creation(home)
        target = _local_path(home, file_name)
        if target.exists():
            raise FileExistsError(file_name)
        index = load(home)
        if any(os.path.normcase(row['file']) == os.path.normcase(file_name)
               for row in index['entries'].values()):
            raise SkillRejected('该文件名已有做法身份，不能把缺失正文重新归给另一条创建。')
        key = _next_id(index)
        _atomic_json(_local_path(home, CREATE_INTENT),
                     {'format': 1, 'id': key, 'entry': entry, 'text': text, 'next': index['next']})
        _sync_creation_directory(home)
        _recover_creation(home)
        return target


# ── 登记 ／ 认人 ──────────────────────────────────────────────────────────
def _entries(index: Mapping[str, Any]) -> dict:
    return dict(index.get("entries") or {})


def _by_file(index: Mapping[str, Any]) -> dict[str, str]:
    return {str(one.get("file") or ""): key for key, one in _entries(index).items()}


def _next_id(index: dict) -> str:
    while True:
        made = "sk-%04d" % int(index.get("next") or 1)
        index["next"] = int(index.get("next") or 1) + 1
        if made not in (index.get("entries") or {}):
            return made


@_source_writer
def register(home: Any, file_name: str, *, title: str | None = None,
             index: dict | None = None, kind: str = "unclassified",
             recorded_at: str = "", scope: str = "", recognize_rename: bool = True) -> dict:
    """ **把一份做法登进那本权威账**（只动元数据；**不重写正文**）。

    ⚠️ 三种情形分开办（这一条就是"改名 ／ 外部改动 ／ 首次登记"的口径）：
      · **已知这一份**（按文件名）：只更新版本（外部改过 ⇒ 版本跟着变）；
      · **没见过的名字**：先按**正文版本**认——某条记录的**文件已经不在了**而版本一样
        ⇒ 那是**改名**，**沿用原 ID**。⚠️ **不分状态**：已并入的那一条改名之后
        仍然是**它**（还是 `merged`），不许变成一条新的有效做法；
      · **都不是**，但**名字对上了某条已归档条目** ⇒ **歧义**（`SkillAmbiguous`）：
        那可能是"把归档的那条改了个内容"，也可能是"新建的一条恰好同名" —— 分不清就**报**，
        不替发起方猜；
      · 剩下的才是**首次登记**。
    ⚠️ **重复登记、重启**都不会重新分配 ID（账里已经有的就是它）。
    ⚠️ 整段在**这一棵的共同锁**里（登记也是读改写）。
    """
    validate_kind(kind)
    tree = _tree(home)
    tree.mkdir(parents=True, exist_ok=True)
    path = _local_path(home, _file_name(file_name))
    with _locked(home):
        _recover_creation(home)
        _recover_revision(home)
        if path.resolve() != path.absolute() or not path.is_file():
            raise SkillRejected("没有这一份做法：" + str(file_name))
        if index is None:
            index = load(home)
        entries = index.setdefault("entries", {})
        body = _read_body(path)
        now = version_of(body)
        stem = Path(file_name).stem

        for key, one in entries.items():
            if str(one.get("file") or "") != file_name:
                continue
            before = dict(one)
            if one.get("version") != now:
                one["version"] = now
                one["seen_external"] = True
            one.setdefault("title", stem)
            one.setdefault("state", "active")
            if one != before:
                save(home, index)
            return dict(one, id=key)

        renamed = [
            dict(one, id=key) for key, one in entries.items()
            if recognize_rename and one.get("version") == now and not (tree / one["file"]).is_file()
        ]
        if len(renamed) > 1:
            raise SkillAmbiguous(file_name, renamed)
        if renamed:
            key = renamed[0]["id"]
            one = entries[key]
            one["renamed_from"] = str(one.get("title") or "")
            one["file"] = file_name
            one["title"] = stem if title is None else str(title)
            save(home, index)
            return dict(one, id=key)

        # **身份不明确**：新文件的名字对上了某条**已归档**条目的标题
        merged = [dict(one, id=key) for key, one in entries.items()
                  if str(one.get("state") or "active") != "active"
                  and stem in (str(one.get("title") or ""),
                               Path(str(one.get("file") or "")).stem)]
        if merged:
            raise SkillAmbiguous(file_name, merged)

        made = _next_id(index)
        entries[made] = {"file": file_name, "title": stem if title is None else str(title),
                         "version": now, "state": "active", "merged_into": None,
                         "op": None, "registered": now, "kind": kind, "recorded_at": recorded_at,
                         **({"scope": scope} if scope else {})}
        save(home, index)
        return dict(entries[made], id=made)


@_source_writer
def register_all(home: Any) -> dict:
    """ 把那一棵里**所有** `.md` 登一遍（旧条目首次登记就走这条）。"""
    with _locked(home):
        _recover_creation(home)
        _recover_revision(home)
        index = load(home)
        tree = _tree(home)
        if not tree.is_dir():
            return index
        for one in sorted(tree.glob("*.md")):
            register(home, one.name, index=index)
        return index


# ── 读 ────────────────────────────────────────────────────────────────────
def rows(home: Any, *, include_merged: bool = False) -> list[dict]:
    """ **有效条目**（默认不含"已并入"）。每一项带 `id` ／ `title` ／ `version` ／ `state`。

    ⚠️ 列表 ／ 计数 ／ 喂索引**都走这一条** —— 于是"已并入"的旧条目不会再单独冒出来。
    """
    index = register_all(home)
    out = []
    for key, one in sorted(index.get("entries", {}).items(), reverse=True):
        state = str(one.get("state") or "active")
        if not include_merged and (state != "active" or not (_tree(home) / one["file"]).is_file()):
            continue
        out.append({"id": key, "title": str(one.get("title") or ""),
                    "file": str(one.get("file") or ""),
                    "version": str(one.get("version") or ""),
                    "state": state, "kind": kind_of(one), "recorded_at": one.get("recorded_at", ""),
                    "merged_into": one.get("merged_into")})
    return out


def resolve(home: Any, said: str) -> dict:
    """ **按 ID 或标题找一条** —— 标题对上多条 ⇒ `SkillAmbiguous`（**不许**取第一条）。"""
    want = str(said or "").strip()
    if not want:
        raise SkillRejected("要拿哪一条？给我 ID 或者标题。")
    index = register_all(home)
    entries = index.get("entries", {})
    if want in entries:
        return dict(entries[want], id=want)
    stem = want[:-3] if want.endswith(".md") else want
    hits = [dict(one, id=key) for key, one in entries.items()
            if stem in (str(one.get("title") or ""), Path(str(one.get("file") or "")).stem)]
    if not hits:
        raise SkillRejected("没有这一条做法：" + want)
    if len(hits) > 1:
        raise SkillAmbiguous(want, hits)
    return hits[0]


def body_of(home: Any, entry: Mapping[str, Any]) -> str:
    path = _tree(home) / str(entry.get("file") or "")
    if not path.is_file():
        raise SkillRejected("这一条的做法文件不在了：" + str(entry.get("file")))
    return _read_body(path)


# ── 我们**自己**动正文时：留版 ＋ 版本校验 ────────────────────────────────
def _keep(home: Any, key: str, text: str, stamp: str) -> Path:
    """ **留一版** —— **永不覆盖**已有的历史文件。

    ⚠️ 2026-10-17 补验：从前是 `<stamp>.md` 撞了改 `-2`，**再撞就直接盖掉 `-2`**
       ⇒ 同一秒里第三次修订会**吃掉**上一版。现在是"一直往后找空位"。
    """
    if not _ID_SHAPE.fullmatch(key):
        raise SkillRejected('历史版本的做法 ID 无效。')
    base = _history_stamp(stamp)
    box = _local_path(home, HISTORY_PREFIX + key)
    box.mkdir(parents=True, exist_ok=True)
    name, n = base, 1
    while (box / (name + ".md")).exists():
        n += 1
        name = base + "-" + str(n)
    target = _local_path(home, (Path(HISTORY_PREFIX + key) / (name + '.md')).as_posix())
    _new_body(target, text)
    return target


@_source_writer
def write_body(home: Any, key: str, text: str, *, expect: str | None,
               stamp: str, kind: str | None = None) -> dict:
    """ **改一条做法的正文**：先留版和修订意图，再替换正文并提交元数据。

    ⚠️ `expect` 对不上 ⇒ `SkillConflict`（**不覆盖**）。
    ⚠️ 只有"我们改"才留历史；外部改动只更版本（见 `register`）。
    """
    _history_stamp(stamp)
    with _locked(home):
        index = register_all(home)
        one = index["entries"].get(key)
        if one is None:
            raise SkillRejected("没有这个 ID：" + str(key))
        # ★★ 2026-10-18 补验 ②：**状态要在锁里再验一次**。
        #    上层（`skill_note`／`remember_skill`）是先 `resolve` 再调这里 ——
        #    而这两步之间**可能刚好有人把这一条并掉**。只靠上层那道检查，
        #    就会出现"对已并入的 B 写入成功、B 仍是 merged、新正文没进目标"。
        if str(one.get("state") or "active") != "active":
            raise SkillRejected("这一条已经并入 " + str(one.get("merged_into"))
                                + " 了 —— 不能就地改写归档条目。")
        path = _tree(home) / str(one["file"])
        before = _read_body(path)
        now = version_of(before)
        if expect is not None and not _the_same(str(expect), now):
            raise SkillConflict(str(key), str(expect), now)
        if kind is not None:
            validate_kind(kind)
            if kind_of(one) not in ("unclassified", kind):
                raise SkillRejected("修订正文须保留原类别。")
        kept = _keep(home, key, before, stamp)
        after = _revision_entry(one, str(text), kind)
        _atomic_json(_local_path(home, REVISION_INTENT),
                     {'format': 1, 'id': key, 'before': one, 'after': after,
                      'before_text': before, 'text': str(text), 'kind': kind,
                      'history': kept.name})
        _sync_creation_directory(home)
        _recover_revision(home)
        return {"id": key, "version": after["version"], "history": str(kept)}


def _revision_entry(before, text, kind):
    after = dict(before, version=version_of(text), seen_external=False)
    if kind is not None:
        validate_kind(kind)
        if kind_of(before) not in ('unclassified', kind):
            raise SkillRejected('修订正文须保留原类别。')
        after['kind'] = kind
    return after


def _recover_revision(home):
    """Finish only a journaled revision with the original identity/preimage.

    The source lease and skill lock are held by callers. A different body,
    metadata identity or damaged history blocks recovery without adopting it.
    """
    intent = _local_path(home, REVISION_INTENT)
    if not intent.exists():
        return
    try:
        plan = json.loads(intent.read_text(encoding='utf-8'))
        key, before, after = plan['id'], plan['before'], plan['after']
        old_text, text, kind = plan['before_text'], plan['text'], plan['kind']
        if (type(plan.get('format')) is not int or plan['format'] != 1
                or not isinstance(key, str) or not _ID_SHAPE.fullmatch(key)
                or not isinstance(before, dict) or not isinstance(after, dict)
                or not isinstance(old_text, str) or not isinstance(text, str)
                or before.get('state') != 'active'
                or before.get('version') != version_of(old_text)
                or after != _revision_entry(before, text, kind)):
            raise ValueError()
        name = _file_name(before['file'])
        history_name = _file_name(plan['history'])
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise SkillRejected('做法修订恢复记录无效；保留现场。') from error
    index = load(home)
    current = index['entries'].get(key)
    owners = [ident for ident, entry in index['entries'].items()
              if os.path.normcase(entry['file']) == os.path.normcase(name)]
    if current not in (before, after) or owners != [key]:
        raise SkillRejected('做法修订身份已变化；保留元数据与恢复记录。')
    history = _local_path(home, (Path(HISTORY_PREFIX + key) / history_name).as_posix())
    if not history.is_file() or _read_body(history) != old_text:
        raise SkillRejected('做法修订历史不完整；保留正文与恢复记录。')
    path = _local_path(home, name)
    if not path.is_file():
        raise SkillRejected('做法修订目标不在了；不重建外部删除的正文。')
    now = version_of(_read_body(path))
    if now != after['version']:
        if now != before['version']:
            raise SkillConflict(key, before['version'], now)
        if current != before:
            raise SkillRejected('已提交修订的正文又被改回；保留现场。')
        _atomic_text(path, text, expected=now)
    _sync_creation_directory(home)
    if current != after:
        index['entries'][key] = after
        save(home, index)
    _sync_creation_directory(home)
    intent.unlink()
    _sync_creation_directory(home)


# ── 合并 ──────────────────────────────────────────────────────────────────
def _the_same(expected: str, actual: str) -> bool:
    """ **预期版本与实际版本是不是同一版**。

    ⚠️ 允许**前缀**：`skill_read` 回执里给的是全文，而转手写进 `expect` 时
       可能抄短了（先用 8 位试）。**8 位以上**的前缀才算数 —— 再短就挡不住碰撞。
    """
    expected, actual = str(expected), str(actual)
    if not expected:
        return False
    if expected == actual:
        return True
    return len(expected) >= 8 and actual.startswith(expected)

#: 操作 ID 的形状：字母数字开头，后面只许字母数字 ／ `.` `-` `_`，最长 64。
_OP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _require_op_id(op_id: str) -> str:
    """ **操作 ID 要能当文件名，且不许跳出 `.ops/`**（2026-10-17 补验）。

    ⚠️ 从前直接把它拼进路径 ⇒ `op_id="../../escaped-operation"` 会把操作账写到
       「做法」**外面**去。这里两道：**形状**（不许分隔符）＋ **解析后必须在 `.ops/` 底下**。
    """
    said = str(op_id or "").strip()
    if not _OP_ID.match(said) or ".." in said:
        raise SkillRejected("操作 ID 只能用字母 ／ 数字 ／ `.` `-` `_`，且不许含 `..`："
                            + repr(op_id))
    return said


def _op_path(home: Any, op_id: str) -> Path:
    ops = _local_path(home, OPS_DIR)
    path = _local_path(home, (Path(OPS_DIR) / (_require_op_id(op_id) + '.json')).as_posix())
    if path.parent != ops:
        raise SkillRejected("操作 ID 越出了 `.ops/`：" + repr(op_id))
    return path


def _payload_digest(keep: str, sources: Iterable[str], expect: Mapping[str, str],
                    text: str) -> str:
    body = json.dumps({"keep": keep, "sources": sorted(sources),
                       "expect": {k: str(v) for k, v in sorted(expect.items())},
                       "text": text}, ensure_ascii=False, sort_keys=True)
    return version_of(body)


@_source_writer
def merge(home: Any, *, op_id: str, keep: str, sources: Iterable[str],
          expect: Mapping[str, str], text: str, stamp: str) -> dict:
    """ **点名把几条并成一条**（系统不代写正文、不判同义）。

    ⚠️ **先全验，再动盘**：ID 存在 ／ 都属于这一棵 ／ 都是 `active` ／ keep 不在 sources ／
       不重复 ／ 每个版本都对得上 —— 一条不合就**一份都不写**。
    ⚠️ **同一个 op_id 重放**：载荷一样 ⇒ 交回原结果（幂等）；载荷不同 ⇒ **拒绝**。
    ⚠️ 来源**保留原文与历史**，只标 `merged`（`merged_into` ＋ `op`）—— 不删、不搬。
    """
    op_id = _require_op_id(op_id)
    _history_stamp(stamp)
    keep = str(keep or "").strip()
    # ⚠️ **先查重复，再去重**（2026-10-17 补验）：从前先 `dict.fromkeys` 去重，
    #    后面那道"重复"检查永远为假 ⇒ `sources=[B, B]` 照样合并成功。
    raw_sources = [str(one).strip() for one in sources or ()]
    if len(set(raw_sources)) != len(raw_sources):
        raise SkillRejected("来源里有重复的 ID。")
    made_sources = list(raw_sources)
    if not keep or not made_sources:
        raise SkillRejected("合并要写清：留下哪一条（keep）、并掉哪几条（sources）。")
    if keep in made_sources:
        raise SkillRejected("来源不能包含目标自己。")
    if not str(text or "").strip():
        raise SkillRejected("合并后的正文是空的 —— 这一版不写。")

    with _locked(home):
        return _merge_locked(home, op_id=op_id, keep=keep, made_sources=made_sources,
                             expect=expect, text=text, stamp=stamp)


def _merge_locked(home: Any, *, op_id: str, keep: str, made_sources: list[str],
                  expect: Mapping[str, str], text: str, stamp: str) -> dict:
    """ `merge` 的里层（**已经拿着锁**）—— 校验 ／ 计划 ／ 续办都在这里。"""
    index = register_all(home)
    digest = _payload_digest(keep, made_sources, expect, text)
    old = (index.get("ops") or {}).get(op_id)
    if old is not None:
        if str(old.get("echo") or "") != digest:
            raise SkillRejected("这个操作 ID 已经用过了，而且载荷不一样 —— 换一个 ID。")
        if old.get("stage") == "rolled-back":
            raise SkillRejected("这个操作已经回滚过了 —— 换一个操作 ID 再合。")
        if old.get("stage") == "done":
            _sync_op_mirror(home, op_id, old)
            return dict(old.get("result") or {}, replayed=True)
        return _finish(home, index, op_id, old)

    for one in [keep] + made_sources:
        entry = index["entries"].get(one)
        if entry is None:
            raise SkillRejected("没有这个 ID：" + one)
        if str(entry.get("state") or "active") != "active":
            raise SkillRejected("这一条已经不是有效条目了：" + one)
        path = _tree(home) / str(entry["file"])
        if not path.is_file():
            raise SkillRejected("这一条的做法文件不在了：" + one)
        want = str((expect or {}).get(one, ""))
        if not want:
            raise SkillRejected("每个 ID 都要带预期版本：" + one)
        if not _the_same(want, version_of(_read_body(path))):
            raise SkillConflict(one, want, version_of(_read_body(path)))

    scopes = {index["entries"][key].get("scope", "") for key in [keep, *made_sources]}
    if len(scopes) > 1:
        raise SkillRejected("只能合并同一保存范围的条目。")
    kinds = {kind_of(index["entries"][key]) for key in [keep, *made_sources]}
    if len(kinds) > 1:
        raise SkillRejected("只能合并同一类别的条目。")

    plan = {"op": op_id, "keep": keep, "sources": made_sources,
            "expect": {one: str((expect or {})[one]) for one in [keep] + made_sources},
            "text": str(text), "echo": digest, "stage": "planned",
            "stamp": str(stamp), "baseline": {
                one: str(index["entries"][one].get("version") or "")
                for one in [keep] + made_sources}}
    _atomic_json(_op_path(home, op_id), plan)
    index.setdefault("ops", {})[op_id] = plan
    save(home, index)
    return _finish(home, index, op_id, plan)


def _verify_keep_before_write(home: Any, index: dict, op_id: str, plan: dict) -> None:
    """ **写正文之前**核目标 —— 一条都不许改。

    ⚠️ 2026-10-18 补验：`planned` 阶段从前是**先写正文、后核来源** ⇒
       来源已被另一笔接走时，恢复会**先改写目标正文、并把阶段推进到 body**，然后才报错。
       验收要的是"拒绝时**什么都没变**"，所以把复核挪到写之前。
    ⚠️ 目标已经是"这次要写的正文"（断在写之后、存之前）也算合格。
    """
    keep = str(plan["keep"])
    entry = index["entries"].get(keep)
    if entry is None:
        raise SkillRejected("账里没有这个目标了：" + keep)
    if str(entry.get("state") or "active") != "active":
        raise SkillRejected("目标已经不是有效条目了 —— 这一趟不写。")
    path = _tree(home) / str(entry.get("file") or "")
    if not path.is_file():
        raise SkillRejected("目标的做法文件不在了：" + keep)
    now = version_of(_read_body(path))
    if now == version_of(str(plan["text"])):
        return                                   # 已经写过了
    want = str((plan.get("expect") or {}).get(keep) or "")
    if not _the_same(want, now):
        raise SkillConflict(keep, want, now)
    for other in (index.get("ops") or {}).values():
        if (other is not plan and other.get("stage") == "done"
                and keep in (other.get("sources") or [])):
            raise SkillRejected("目标已经被另一次合并并走了（" + str(other.get("op"))
                                + "）—— 这一趟不写。")


def _verify_sources(home: Any, index: dict, op_id: str, plan: dict) -> None:
    """ **来源还配不配被这一笔归档** —— 版本 ／ 状态 ／ 归属，三样都要对。

    ⚠️ 只比版本不够（2026-10-18 补验）：正文没动、但它已经被**另一次合并**接管时，
       再归档一次就是把归属**抢过来** —— 那一笔的账还写着 done，两本账从此对不上。
    """
    ops = index.get("ops") or {}
    for one in plan["sources"]:
        src = index["entries"].get(one)
        if src is None:
            raise SkillRejected("账里没有这个来源了：" + one)
        src_path = _tree(home) / str(src.get("file") or "")
        if not src_path.is_file():
            raise SkillRejected("来源的文件不在了：" + one)
        now = version_of(_read_body(src_path))
        want = str((plan.get("expect") or {}).get(one) or "")
        if not _the_same(want, now):
            raise SkillConflict(one, want, now)
        if str(src.get("state") or "active") != "active":
            raise SkillRejected("来源 " + one + " 已经不是有效条目了（"
                                + str(src.get("state")) + " → "
                                + str(src.get("merged_into")) + "）—— 不再归档。")
        if src.get("merged_into") or src.get("op"):
            raise SkillRejected("来源 " + one + " 已经被另一次操作占着（"
                                + str(src.get("op")) + " → "
                                + str(src.get("merged_into")) + "）—— 不再归档。")
        for other in ops.values():
            if other is plan or other.get("stage") != "done":
                continue
            if one in (other.get("sources") or []):
                raise SkillRejected("来源 " + one + " 已经被另一次完成的合并并走了（"
                                    + str(other.get("op")) + "）—— 不再归档。")


def _finish(home: Any, index: dict, op_id: str, plan: dict) -> dict:
    """ **按阶段续办**：正文 → 目标状态 → 来源归档 → done。可重复调用。

    ⚠️ **续办前复核**（2026-10-17 补验）：来源要在归档之前**再核一次版本** ——
       中断期间有人改了来源、而它又**没**进目标正文的话，标成"已并入"就是把
       一段没进目标的字**藏起来**。对不上 ⇒ `SkillConflict`，停。
    ⚠️ **`rolled-back` 是终态**：再调 `recover` ／ `merge` 都不许把它改回 `done`。
    """
    if str(plan.get("stage") or "") == "rolled-back":
        raise SkillRejected("这次操作已经回滚过了，不能再续办。")
    stage = plan.get('stage')
    if stage not in ('planned', 'body', 'archived', 'done'):
        raise SkillRejected('做法恢复阶段未知；保留原计划，不自动提交')
    keep = str(plan["keep"])
    text = str(plan["text"])

    if stage == "planned":
        # ★★ 2026-10-18 补验：**先核完，再动盘** —— 目标与来源都要在"一个字节都没改"时
        #    就是合格的；不合格就**停在 planned**，正文 ／ 历史 ／ 阶段**一样都不许变**。
        _verify_keep_before_write(home, index, op_id, plan)
        _verify_sources(home, index, op_id, plan)

        entry = index["entries"][keep]
        want = str(plan["expect"][keep])
        path = _tree(home) / str(entry["file"])
        before = _read_body(path)
        now = version_of(before)
        if now == version_of(text):
            stage = "body"                       # 已经写过了（上次中断在这一步之后）
            entry['version'] = version_of(text)
            plan["wrote_version"] = version_of(text)
            plan["stage"] = stage
            index["ops"][op_id] = plan
            save(home, index)
        else:
            if not _the_same(want, now):
                raise SkillConflict(keep, want, now)
            plan["history"] = str(_keep(home, keep, before, str(plan["stamp"])))
            # Publish the exact preimage binding before replacing the body.
            # A crash after the body write must not force rollback to guess.
            index["ops"][op_id] = plan
            save(home, index)
            _atomic_json(_op_path(home, op_id), plan)
            _atomic_text(path, text, expected=now,
                         verify=lambda: _verify_sources(home, index, op_id, plan))
            entry["version"] = version_of(text)
            plan["wrote_version"] = version_of(text)
            stage = "body"
            plan["stage"] = stage
            index["ops"][op_id] = plan
            save(home, index)

    if stage == "body":
        # ★★ 2026-10-18 补验 ①：**归档之前先复核目标**（不只看来源）。
        #    只差来源不看目标的话，"正文写完 → 第三方把目标改成另一份 → 恢复"
        #    会把来源标成已并入，而目标**根本不含合并内容** —— 那是把一段字藏起来。
        #    三条一起核：**正文还是这次写的那一版** ／ **目标仍是有效条目** ／
        #    **目标没有被别的已完成操作并走**。
        keep_entry = index["entries"][keep]
        keep_path = _tree(home) / str(keep_entry["file"])
        if not keep_path.is_file():
            raise SkillRejected("目标的做法文件不在了：" + keep)
        wrote = version_of(text)
        now_keep = version_of(_read_body(keep_path))
        if not _the_same(wrote, now_keep):
            raise SkillConflict(keep, wrote, now_keep)
        if str(keep_entry.get("state") or "active") != "active":
            raise SkillRejected("目标已经不是有效条目了（" + str(keep_entry.get("state"))
                                + "）—— 这一趟不再往下走。")
        for other in (index.get("ops") or {}).values():
            if (other is not plan and other.get("stage") == "done"
                    and keep in (other.get("sources") or [])):
                raise SkillRejected("目标已经被另一次合并并走了（" + str(other.get("op"))
                                    + "）—— 这一趟不再往下走。")

        # ★★ 2026-10-18 补验 ③：**来源资格是三样，不是一样**。
        #    只比正文版本会漏掉"它已经被**另一次操作**接管"：
        #    op1（B→A）断在 body、op2（B→C）做完 ⇒ 恢复 op1 会把 B 从
        #    `merged_into=C` 抢成 `A` —— 而 op2 的账还写着 done。两本账从此对不上。
        _verify_sources(home, index, op_id, plan)

        entry = index["entries"][keep]
        entry["state"] = "active"
        entry["merged_into"] = None
        for one in plan["sources"]:
            src = index["entries"][one]
            src["state"] = "merged"
            src["merged_into"] = keep
            src["op"] = op_id
        stage = "archived"
        plan["stage"] = stage
        index["ops"][op_id] = plan
        save(home, index)

    if stage == "archived":
        # 收尾之前再确认一次：来源仍记在这一笔名下（中途被人改过 ⇒ 停，不记 done）
        for one in plan["sources"]:
            src = index["entries"].get(one) or {}
            if (src.get('state') != 'merged'
                    or str(src.get("merged_into") or "") != keep
                    or str(src.get("op") or "") != op_id):
                raise SkillRejected("来源的归属已经变了（" + one + " → "
                                    + str(src.get("merged_into")) + " ／ "
                                    + str(src.get("op")) + "）—— 这一趟不再往下走。")
            source_path = _local_path(home, _file_name(src.get('file')))
            if not source_path.is_file():
                raise SkillRejected('归档来源文件不在了：' + one)
            expected = str(plan['expect'][one])
            actual = version_of(_read_body(source_path))
            if not _the_same(expected, actual):
                raise SkillConflict(one, expected, actual)
        if str(index["entries"][keep].get("state") or "active") != "active":
            raise SkillRejected("目标已经不是有效条目了 —— 这一趟不再往下走。")
        # ★★ 2026-10-18 补验：**不许把"当前目标版本"冒充成"这次合并的结果"**。
        #    否则"合并完成后有人又改了目标 → recover 把它记成 keep_version →
        #    rollback 用旧正文把**后来的内容**盖掉"。结果版本绑定的是**这次计划写的正文**。
        wrote = version_of(text)
        now_keep = version_of(_read_body(_tree(home) / str(index["entries"][keep]["file"])))
        if not _the_same(wrote, now_keep):
            raise SkillConflict(keep, wrote, now_keep)

    result = {"op": op_id, "keep": keep, "sources": list(plan["sources"]),
              "keep_version": version_of(text)}
    plan["stage"] = "done"
    plan["result"] = result
    index["ops"][op_id] = plan
    _atomic_json(_op_path(home, op_id), plan)
    save(home, index)
    return dict(result, replayed=False)


@_source_writer
def recover(home: Any, op_id: str) -> dict:
    """ **把一次中断的合并续上**（可重复执行；不覆盖第三方新版本）。

    ⚠️ **`rolled-back` 是终态**（2026-10-17 补验）：回滚之后再 `recover` 会把它从
       `rolled-back` 改回 `done` —— 那会让账与盘各说一套。这里**拒绝**。
    """
    op_id = _require_op_id(op_id)
    with _locked(home):
        index = load(home)
        plan = (index.get("ops") or {}).get(op_id)
        if plan is None:
            path = _op_path(home, op_id)
            if not path.is_file():
                raise SkillRejected("没有这次操作：" + str(op_id))
            plan = json.loads(path.read_text(encoding="utf-8"))
            index.setdefault("ops", {})[op_id] = plan
        if plan.get("stage") == "rolled-back":
            raise SkillRejected("这次操作已经回滚过了，不能再续办。")
        if plan.get("stage") == "rolling-back":
            return _rollback_locked(home, op_id, stamp=str(plan.get('rollback', {}).get('stamp', '')))
        if plan.get("stage") == "done":
            _sync_op_mirror(home, op_id, plan)
            return dict(plan.get("result") or {}, replayed=True)
        return _finish(home, index, op_id, plan)


# ── 恢复（回滚）──────────────────────────────────────────────────────────
@_source_writer
def rollback(home: Any, op_id: str, *, stamp: str) -> dict:
    """ （见下）—— **已经拿着锁** 的那一层叫 `_rollback_locked`。"""
    op_id = _require_op_id(op_id)
    with _locked(home):
        return _rollback_locked(home, op_id, stamp=stamp)


def _rollback_locked(home: Any, op_id: str, *, stamp: str) -> dict:
    """ **把一次合并退回去** —— 只有"合完以后没人再动过"才允许。

    ⚠️ 判据：目标现在的版本 == 那次合并**写下去**的版本，而且目标**没有**再被合并进别的条目。
       对不上 ⇒ `SkillRejected`（**不拿旧快照盖新内容**）。
    ⚠️ 退回的是**那次合并前留的那一版**（`_finish` 里 `_keep` 存的）。
    """
    index = load(home)
    plan = (index.get("ops") or {}).get(op_id)
    if plan is None or plan.get("stage") not in ("done", "rolling-back"):
        raise SkillRejected("这次操作没有完成过，退不了：" + str(op_id))
    if plan.get('stage') == 'done':
        _sync_op_mirror(home, op_id, plan)
    keep = str(plan["keep"])
    entry = index["entries"][keep]
    # Operation insertion order is preserved by this ledger's JSON writes.
    # An earlier completed merge is the preimage lineage, not a later edit.
    operations = list(index['ops'])
    later = [index['ops'][key] for key in operations[operations.index(op_id) + 1:]
             if (one := index['ops'][key]).get("stage") == "done"
             and (keep in (one.get("sources") or []) or one.get("keep") == keep)]
    if later:
        raise SkillRejected("这一条后来又动过了（" + str(len(later)) + " 次操作）—— 不盲目回退。")
    path = _tree(home) / str(entry["file"])
    now = version_of(_read_body(path))
    wrote = str((plan.get("result") or {}).get("keep_version")
                or plan.get("wrote_version") or version_of(str(plan.get("text") or "")))
    # ⚠️ 只认**这次合并写下去的那一版**（`wrote`）：当前版本不等于它 ⇒ 说明合并之后
    #    有人改过目标 ⇒ **不拿旧快照盖新内容**。
    reversing = plan.get('stage') == 'rolling-back'
    was = _rollback_preimage(home, keep, plan)
    restored = version_of(was)
    intent = plan.get('rollback')
    if reversing and (not isinstance(intent, dict) or intent.get('before') != wrote
                      or intent.get('after') != restored):
        raise SkillRejected('回滚意图与原始版本不一致；保留现场。')
    if not _the_same(wrote, now) and not (reversing and now == restored):
        raise SkillRejected("目标在合并之后被改过 —— 不拿旧快照盖新内容。")
    if str(entry.get('state') or 'active') != 'active':
        raise SkillRejected('回滚目标已不再有效；保留现场。')
    for one in plan['sources']:
        src = index['entries'].get(one) or {}
        if src.get('state') != 'merged' or src.get('merged_into') != keep or src.get('op') != op_id:
            raise SkillRejected('回滚来源归属已变化：' + str(one))
    if not reversing:
        plan['rollback'] = {'before': wrote, 'after': restored, 'stamp': str(stamp)}
        plan['stage'] = 'rolling-back'
        index['ops'][op_id] = plan
        save(home, index)
        _atomic_json(_op_path(home, op_id), plan)
    if now != restored:
        _atomic_text(path, was, expected=now)
    entry["version"] = version_of(was)
    for one in plan["sources"]:
        src = index["entries"][one]
        if str(src.get("merged_into") or "") == keep and str(src.get("op") or "") == op_id:
            src["state"] = "active"
            src["merged_into"] = None
            src["op"] = None
    plan["stage"] = "rolled-back"
    index["ops"][op_id] = plan
    _atomic_json(_op_path(home, op_id), plan)
    save(home, index)
    return {"op": op_id, "keep": keep, "restored_version": entry["version"]}


def _sync_op_mirror(home: Any, op_id: str, plan: dict) -> None:
    """Repair a legacy lagging mirror from the authoritative completed index."""
    path = _op_path(home, op_id)
    if path.exists():
        current = json.loads(path.read_text(encoding='utf-8'))
        if current == plan:
            return
        if (not isinstance(current, dict) or current.get('op') != op_id
                or current.get('echo') != plan.get('echo')
                or current.get('stage') not in ('planned', 'body', 'archived', 'done')
                or any(current.get(key) != plan.get(key)
                       for key in ('keep', 'sources', 'expect', 'text'))):
            raise SkillRejected('做法操作镜像身份冲突；保留原记录。')
    _atomic_json(path, plan)


def _rollback_preimage(home: Any, keep: str, plan: dict) -> str:
    """Read only a verified pre-merge version, never the newest arbitrary file."""
    expected = str((plan.get('baseline') or {}).get(keep)
                   or (plan.get('expect') or {}).get(keep) or '')
    box = _tree(home) / (HISTORY_PREFIX + keep)
    recorded = str(plan.get('history') or '')
    if recorded:
        # History trees can move with their Bot; only inspect this tree's copy.
        candidate = box / Path(recorded).name
        if candidate.is_file():
            if candidate.resolve() != candidate.absolute():
                raise SkillRejected('回滚快照路径无效。')
            text = _read_body(candidate)
            if not _the_same(expected, version_of(text)):
                raise SkillRejected('回滚快照与合并前版本不一致。')
            return text
    # Older interrupted plans may have published the body before recording the
    # history pathname. Locate the exact preimage by its saved version instead.
    matches = {}
    for candidate in sorted(box.glob('*.md')) if box.is_dir() else ():
        if candidate.resolve() != candidate.absolute():
            raise SkillRejected('回滚快照路径无效。')
        text = _read_body(candidate)
        version = version_of(text)
        if _the_same(expected, version):
            matches[version] = text
    if len(matches) == 1:
        return next(iter(matches.values()))
    if not matches and _the_same(expected, version_of(str(plan.get('text') or ''))):
        return str(plan['text'])  # Merge left the target body unchanged.
    raise SkillRejected('找不到唯一且可校验的合并前版本；不猜测历史快照。')


# ── 给页面看的那两样（2026-10-18 批④）—— **只读**，不改任何东西 ─────────
def _history_count(home: Any, key: str) -> int:
    box = _tree(home) / (HISTORY_PREFIX + key)
    return len(list(box.glob("*.md"))) if box.is_dir() else 0


def history_view(home: Any, *, at_most_ops: int = 20) -> dict:
    """ **做法治理历史**：有效 ／ 已并入 ／ 操作账（页面那一侧只读）。

    ⚠️ **版本只给前 12 位**（元数据里仍然是完整 SHA-256）—— 页面不拿全串去比。
    ⚠️ 已并入的**只在 `merged` 里**：这一条就是"归档条目不进有效列表"的读口。
    ⚠️ 只读：不 `register` 之外的写、不改正文（`register` 也只动元数据）。
    """
    index = register_all(home)
    active: list[dict] = []
    merged: list[dict] = []
    missing: list[dict] = []
    for key, one in sorted(index.get("entries", {}).items(), reverse=True):
        row = {"id": key, "title": str(one.get("title") or ""),
               "state": str(one.get("state") or "active"),
               "kind": kind_of(one), "recorded_at": one.get("recorded_at", ""),
               "version": str(one.get("version") or "")[:12],
               "merged_into": one.get("merged_into"),
               "op": one.get("op"),
               "history": _history_count(home, key)}
        path = _tree(home) / one["file"]
        try:
            text = _read_body(path)
        except FileNotFoundError:
            missing.append(dict(row, state="missing"))
        else:
            row["body"] = text
            row["references"] = reference_ids(text)[:20]
            (merged if row["state"] != "active" else active).append(row)
    # ⚠️ **按"可靠时间"排，再取最近 20 笔**（2026-10-18 补）：操作 ID 是随机尾巴，
    #    它的字典序与时间无关 —— 按 ID 排会把新的排到后面切掉。时间字段用 `stamp`
    #    （落账那一刻），缺它才退回 ID。
    ordered = sorted((index.get("ops") or {}).items(),
                     key=lambda pair: (str(pair[1].get("stamp") or ""), str(pair[0])))
    ops = []
    for op_id, one in ordered[-at_most_ops:]:
        ops.append({"op": op_id, "stage": str(one.get("stage") or ""),
                    "keep": one.get("keep"), "sources": list(one.get("sources") or []),
                    "at": str(one.get("stamp") or "")})
    return {"active": active, "merged": merged, "missing": missing, "ops": ops,
            "active_count": len(active), "merged_count": len(merged)}


#: 台账里那几笔 → 页面上的五档（**只认见过的**；没见过 ⇒ `unknown`）
_STAGES = {"planned": "recovery-pending", "body": "recovery-pending",
           "archived": "recovery-pending", "rolling-back": "recovery-pending"}
_LEDGER = {"consolidate-running": "running", "consolidate-done": "idle",
           "consolidate-failed": "failed", "consolidate-partial": "partial",
           "consolidate-files-only": "partial", "consolidate-conflict": "failed",
           "consolidate-recovered": "idle", "consolidate-queued": "running"}


def _the_events(ledger: Any, *, at_most: int = 400) -> list[dict]:
    """ 台账 → 事件。每行 `{ts, type, text}`，`text` 里是 JSON 详情。**读不出就跳过**。"""
    if ledger is None:
        return []
    path = Path(ledger)
    if not path.is_file():
        return []
    rows = [one for one in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if one.strip()]
    out: list[dict] = []
    for line in rows[-at_most:]:
        try:
            got = json.loads(line)
        except ValueError:
            continue
        detail: dict = {}
        try:
            parsed = json.loads(str(got.get("text") or ""))
            if isinstance(parsed, dict):
                detail = parsed
        except ValueError:
            detail = {}
        out.append({"ts": str(got.get("ts") or ""), "type": str(got.get("type") or ""),
                    "detail": detail})
    return out


def status_view(home: Any, *, ledger: Any = None) -> dict:
    """ **运行状态**（页面那一侧只读）：`running ／ idle ／ failed ／ partial ／
    recovery-pending ／ unknown`。

    ★ **同一个整理任务的事实要留住**（2026-10-18 补）：
      一条 `failed` **不等于**"什么都没写下去" —— 它前面可能有 `partial` ／
      `files-only`，那一笔**部分提交**的事实与**失败详情**都要一起交出去。
    ⚠️ 看的是**最近那个任务**（按台账里的先后），不是"最后一行"：
      一个任务会出好几行（running → partial → failed），只读最后一行会把前面的事实丢掉。
    ⚠️ 有未完成的操作 ⇒ `recovery-pending`（与"正在跑"不是一回事）。
    ⚠️ 台账读不到 ／ 一行都没有 ⇒ `unknown`（**不猜**）。
    """
    index = load(home)
    stuck = [one for one in (index.get("ops") or {}).values()
             if str(one.get("stage") or "") in _STAGES]
    pending = len(stuck) + sum(_local_path(home, name).exists()
                              for name in (CREATE_INTENT, REVISION_INTENT))
    events = _the_events(ledger)
    if pending:
        return {"state": "recovery-pending", "recovery_pending": pending,
                "last": events[-1] if events else None,
                "why": "有 " + str(pending) + " 笔操作停在半路"}
    if not events:
        return {"state": "unknown", "recovery_pending": 0, "last": None,
                "why": "没有台账或读不到"}

    # 最近那个任务：从后往前找第一个带 task 的事件
    task = ""
    for one in reversed(events):
        task = str(one["detail"].get("task") or "")
        if task:
            break
    mine = ([one for one in events if str(one["detail"].get("task") or "") == task]
            if task else [events[-1]])

    partial: list[dict] = []
    failed: dict | None = None
    terminal = ""          # 最后一次**有效终态**：done ／ failed ／ partial ／ ""
    for one in mine:
        kind = one["type"]
        if kind in ("consolidate-running", "consolidate-queued", "consolidate-recovering"):
            terminal = "running"
        elif kind == "consolidate-done":
            # ★★ 2026-10-18 补验 ④：**`done` 不等于"办完了"** —— 恢复那一趟
            #    会写 `phase=files-only`（文件好了、后续还欠着）⇒ 那是**未完成**。
            phase = str(one["detail"].get("phase") or "")
            if phase == "files-only":
                terminal = "partial"
                partial.append(one)
            else:
                terminal = "done"
        elif kind in ("consolidate-partial", "consolidate-files-only"):
            partial.append(one)
            terminal = "partial"
        elif kind in ("consolidate-failed", "consolidate-conflict"):
            failed = one
            terminal = "failed"
        elif kind == "consolidate-recovered":
            terminal = "done"

    committed: list[str] = []
    for one in partial:
        for key in ("committed", "wrote", "settled"):
            committed.extend(str(x) for x in (one["detail"].get(key) or []))
    detail: dict = {"task": task,
                    "partial": bool(partial),
                    # ⚠️ **"resolved"看最后一次有效终态**，不是"历史上出现过 done"：
                    #    先 done 后来又 files-only ⇒ 现在仍是没办完。
                    "resolved": terminal == "done",
                    "committed": committed,
                    "failures": sum(1 for one in mine if one["type"] == "consolidate-failed"),
                    "phases": [one["type"] for one in mine]}
    if partial and partial[-1]["detail"].get("failed_at"):
        detail["failed_at"] = str(partial[-1]["detail"]["failed_at"])
    if any(one["type"] == "consolidate-followup-unknown" for one in mine):
        detail["followup_uncertain"] = terminal != "done"
    if failed is not None:
        detail.update({"error": str(failed["detail"].get("err") or ""),
                       "error_class": str(failed["detail"].get("err_class") or ""),
                       "attempt": failed["detail"].get("attempt")})

    if terminal == "done":
        state = "idle"
    elif terminal in ("failed", "partial", "running"):
        state = terminal
    else:
        state = "unknown"
    detail["recovered_after_failure"] = bool(terminal == "done" and detail["failures"])
    return {"state": state, "recovery_pending": 0, "last": events[-1],
            "detail": detail,
            "why": "最近那个任务：" + " → ".join(detail["phases"])}
