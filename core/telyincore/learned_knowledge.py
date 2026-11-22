"""Bot-authored knowledge with append-only revisions and trusted scope."""

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from .retrieval_scope import OWNER, RetrievalScope, valid_scope
from .file_signature import descriptor_signature, signature
from .source_guard import writer
from .tools import ToolDef, ToolTag

ID = re.compile(r"kb-[0-9a-f]{32}")
DIRECTORY = ".learned"
_TEXT_LIMITS = {"title": 200, "text": 24000, "conditions": 2000, "basis": 4000}

DECLARATION = ToolDef(
    name="knowledge_note",
    description="保存规则、配方或资料，说明来源与适用条件。改标题或正文时填写原 ID 和当前版本。"
                "观察、推测和实际执行另记。操作技能可引用 [[知识ID]]。",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "text": {"type": "string"},
            "conditions": {"type": "string"},
            "basis": {"type": "string", "description": "依据说明，可引用已读到的事件 ID 或资料"},
            "id": {"type": "string"},
            "expect": {"type": "integer", "minimum": 1},
        },
        "required": ["title", "text", "conditions", "basis"],
    },
    tags=(ToolTag.WRITE,), effect="immediate",
)


def _directory(home):
    root = Path(home).absolute()
    path = root / "知识" / DIRECTORY
    if path.resolve() != path:
        raise ValueError("习得知识目录路径无效。")
    return path


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('习得知识字段重复；不猜测有效值。')
        result[key] = value
    return result


def _invalid_number(value):
    raise ValueError('习得知识包含无效数值。')


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        _invalid_number(value)
    return number


def _snapshot(path, *, decode=True):
    """Read one stable file and retain a bounded fingerprint of those bytes."""
    if path.is_symlink() or path.resolve() != path:
        raise ValueError("习得知识路径无效。")
    try:
        with path.open('rb') as stream:
            before = descriptor_signature(stream.fileno())
            if decode:
                raw = stream.read()
                record = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_fields,
                                    parse_constant=_invalid_number, parse_float=_finite_float)
                digest = hashlib.sha256(raw).digest()
            else:
                record = None
                digest = hashlib.file_digest(stream, 'sha256').digest()
            after = descriptor_signature(stream.fileno())
        if before != after or signature(path) != after:
            raise ValueError('知识版本已变化，请重读后再修订。')
        return record, (digest, after)
    except FileNotFoundError:
        return None, None


def _publish(path, record, baseline):
    """Publish only over the exact authorized snapshot; creations never replace.

    The source lease serializes cooperating writers. Rechecking observed edits
    is not an OS-level compare-and-swap against arbitrary external writers.
    """
    if path.resolve() != path:
        raise ValueError('习得知识路径无效。')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.knowledge-', dir=path.parent)
    temporary = Path(name)
    primary = None
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            json.dump(record, stream, ensure_ascii=False, indent=1, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        if path.resolve() != path:
            raise ValueError('习得知识路径已变化；不发布替换。')
        if baseline is None:
            os.link(temporary, path)
        else:
            if _snapshot(path, decode=False)[1] != baseline:
                raise ValueError('知识版本已变化，请重读后再修订。')
            os.replace(temporary, path)
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as error:
            if primary is None:
                raise
            primary.add_note('knowledge staging cleanup failed: ' + type(error).__name__)


def _authorized_record(path, scope, *, snapshot=False):
    try:
        record, baseline = _snapshot(path)
        if record is None or (scope is not None and not scope.permits(record["scope"])):
            return None
        if not isinstance(record["revisions"], list) or not record["revisions"]:
            raise ValueError("习得知识版本记录无效。")
        if not valid_scope(record["scope"]):
            raise ValueError("习得知识来源范围无效。")
        for number, revision in enumerate(record["revisions"], 1):
            _validate_revision(revision, number)
        return (record, baseline) if snapshot else record
    except (OSError, ValueError, TypeError, KeyError):
        if scope is None or scope.administrator:
            raise
        return None


def _validate_revision(row, number):
    # The array position is the public version selector. Never let a damaged
    # version label or missing evidence silently become another valid revision.
    if (not isinstance(row, dict) or type(row.get("version")) is not int
            or row["version"] != number):
        raise ValueError("习得知识版本序列无效。")
    for field, limit in _TEXT_LIMITS.items():
        value = row.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError("习得知识版本正文或依据无效。")
    events = row.get("source_events")
    if not isinstance(events, list) or any(type(cursor) is not int or cursor < 1 for cursor in events):
        raise ValueError("习得知识来源事件无效。")
    stamp = row.get("recorded_at")
    if not isinstance(stamp, str):
        raise ValueError("习得知识记录时间无效。")
    try:
        if datetime.fromisoformat(stamp).tzinfo is None:
            raise ValueError()
    except ValueError as error:
        raise ValueError("习得知识记录时间无效。") from error


def visible(home, scope):
    from .memory_inventory import ordered
    with ordered(_directory(home),'.json',recursive=False,strict=scope is None or scope.administrator) as names:
        for path in names:
            if ID.fullmatch(path.stem):
                record = _authorized_record(path, scope)
                if record is not None:
                    yield path.stem, record["revisions"][-1]


def read(home, scope, key, version=None):
    record = _authorized_record(_directory(home) / f"{key}.json", scope) if ID.fullmatch(key) else None
    if record is not None:
        revisions = record["revisions"]
        if version is None:
            return revisions[-1]
        if type(version) is int and 1 <= version <= len(revisions):
            return revisions[version - 1]
    raise PermissionError("当前可见知识中没有可读取的这一条。")


@writer
def save(home, scope: RetrievalScope | None, *, title, text, conditions, basis,
         id="", expect=None, source_events=()):
    for field, value in (("title", title), ("text", text), ("conditions", conditions), ("basis", basis)):
        limit = _TEXT_LIMITS[field]
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError("请填写标题、正文、适用条件及依据，并控制篇幅。")
    if scope is not None:
        scope.require()
    directory = _directory(home)
    if id:
        if type(expect) is not int or expect < 1 or not ID.fullmatch(id):
            raise ValueError("修订须提供知识 ID 和预期版本。")
        path = directory / f"{id}.json"
        authorized = _authorized_record(path, scope, snapshot=True)
        if authorized is None:
            raise PermissionError("当前可见知识中没有可修订的这一条。")
        record, baseline = authorized
        if record["scope"] != (scope.current if scope else OWNER) and scope and not scope.administrator:
            raise PermissionError("当前会话不能修订这条知识。")
        if expect != len(record["revisions"]):
            raise ValueError("知识版本已变化，请重读后再修订。")
    else:
        if expect is not None:
            raise ValueError("预期版本须与知识 ID 一起使用。")
        id = "kb-" + uuid4().hex
        path = directory / f"{id}.json"
        baseline = None
        record = {"scope": scope.current if scope else OWNER, "revisions": []}
    version = len(record["revisions"]) + 1
    revision = {
        "version": version, "title": title.strip(), "text": text,
        "conditions": conditions, "basis": basis, "source_events": list(source_events) if record["scope"] == (scope.current if scope else OWNER) else [],
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _validate_revision(revision, version)
    record["revisions"].append(revision)
    _publish(path, record, baseline)
    return f"已保存知识 {id}，版本 {version}。可用 kb_read 按 ID 读取。"

def render(row):
    events = "、".join(f"event:{cursor}" for cursor in row["source_events"])
    return (f"{row['title']}\n记录时间 {row['recorded_at']}\n"
            f"适用条件 {row['conditions']}\n依据 {row['basis']}\n"
            + (f"记录时的会话事件 {events}\n" if events else "")
            + row["text"])
