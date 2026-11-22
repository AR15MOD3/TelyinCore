"""Host-only retrieval policy. Tool arguments and document text grant nothing.

Envelope fields are trusted at the existing plugin/console ingestion boundary,
not when copied out of a model's text. Transcript ``scope`` is projected by the
host; portrait ``source`` is written by _profile_target, never a tool argument.
Legacy free-form markdown needs an administrator's content-bound file grant.
No migration, inference by name, or implicit public classification occurs here.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .session_view import batch_scope, event_scope
from .snapshot_json import loads as snapshot_loads

_ACCOUNT = re.compile(r"[a-z][a-z0-9_-]{1,31}:[^\s:|]{1,128}\Z")
_GROUP = re.compile(r"group:[a-z][a-z0-9_-]{1,31}:[^\s:|]{1,128}\Z")
OWNER = "private:console:local-owner"
GRANTS = ".retrieval-grants.json"


def valid_scope(value: Any) -> bool:
    return isinstance(value, str) and bool(
        _GROUP.fullmatch(value) or
        (value.startswith("private:") and _ACCOUNT.fullmatch(value[8:])))


@dataclass(frozen=True)
class RetrievalScope:
    current: str = ""
    allowed: frozenset[str] = frozenset()
    administrator: bool = False

    def require(self) -> None:
        if not valid_scope(self.current) or self.current not in self.allowed:
            raise PermissionError("当前可信会话范围不可用，检索已拒绝。")

    def permits(self, scope: Any) -> bool:
        return valid_scope(scope) and (self.administrator or scope in self.allowed)

    def memory(self, where: str, row: Any) -> bool:
        if self.administrator:
            return True
        # Portrait provenance is a host-owned field on this one tree. A source
        # string on a note/transcript is NOT an authorization credential.
        path = PurePosixPath(where)
        if path.parts and path.parts[0] == "画像" and path.suffix == ".jsonl":
            source = getattr(row, "source", "")
            if not isinstance(source, str):
                return False
            scope, separator, sender = source.partition("|sender=")
            if scope.startswith("group:"):
                platform = scope.split(":", 2)[1] if valid_scope(scope) else ""
                return bool(separator and _ACCOUNT.fullmatch(sender)
                            and sender.startswith(platform + ":") and self.permits(scope))
            return not separator and scope.startswith("private:") and self.permits(scope)
        return self.permits(getattr(row, "scope", ""))

    def event(self, row: Any) -> bool:
        if self.administrator:
            return True
        # Internal reasoning, receipts and their arbitrary meta/source strings
        # must never be reclassified as an external room's conversation.
        if getattr(row, "origin", None) != "external":
            return False
        account, source = getattr(row, "sender_key", None), getattr(row, "source", None)
        if not (isinstance(account, str) and _ACCOUNT.fullmatch(account)
                and isinstance(source, str) and account.startswith(source + ":")):
            return False
        return self.permits(event_scope(row))


def for_batch(book: Any, batch: Any) -> RetrievalScope:
    """Take a fresh immutable snapshot. Empty/mixed/broken never means admin."""
    try:
        rows = tuple(batch)
        scope = batch_scope(rows)
        if not valid_scope(scope):
            return RetrievalScope()
        external = [row for row in rows if getattr(row, "origin", None) == "external"]
        for row in external:
            account, source = getattr(row, "sender_key", None), getattr(row, "source", None)
            if not (isinstance(account, str) and _ACCOUNT.fullmatch(account)
                    and isinstance(source, str) and account.startswith(source + ":")
                    and event_scope(row) == scope):
                return RetrievalScope()
        if scope.startswith("group:"):
            return RetrievalScope(scope, frozenset([scope]))
        account = scope[8:]
        person = book.resolve(account)  # fail closed on corrupt bindings
        if person is None and scope != OWNER:
            return RetrievalScope()  # stable transport key alone is no identity grant
        accounts = tuple(person.accounts) if person is not None else (account,)
        if any(not isinstance(one, str) or not _ACCOUNT.fullmatch(one) for one in accounts):
            return RetrievalScope()
        allowed = frozenset("private:" + one for one in (*accounts, account))
        # A manually linked owner account does not grant administrative rights.
        return RetrievalScope(scope, allowed, scope == OWNER)
    except Exception:
        return RetrievalScope()


def from_profile(context: Callable[[], Any]) -> RetrievalScope:
    """Compatibility for host library callers; never reads model parameters."""
    try:
        scope, _key, _label = context()
        if valid_scope(scope):
            return RetrievalScope(scope, frozenset([scope]), scope == OWNER)
    except Exception:
        pass
    return RetrievalScope()


def current(provider: Callable[[], Any]) -> RetrievalScope:
    try:
        got = provider()
    except Exception:
        got = None
    if not isinstance(got, RetrievalScope):
        raise PermissionError("当前可信会话范围不可用，检索已拒绝。")
    got.require()
    return got


def file_grants(home: Path, scope: RetrievalScope) -> Callable[[Path], bool]:
    """Read a host-admin allowlist once per call, without caching across calls.

    Schema: {version: 1, files: {relative/path: {sha256: HEX, scopes: [public]}}}.
    Scopes may alternatively contain canonical private/group boundaries. This
    file has NO model-facing write tool. Only an explicit administrative review
    may create it. Any edit of an authorized file invalidates the grant.
    """
    home = Path(home).resolve()
    grants = {}
    try:
        policy = home / GRANTS
        if policy.is_symlink():
            raise ValueError("linked policy")
        raw = snapshot_loads(policy.read_text(encoding="utf-8"))
        if (type(raw.get("version")) is int and raw["version"] == 1
                and isinstance(raw.get("files"), dict)):
            grants = raw["files"]
    except (OSError, ValueError, TypeError, AttributeError):
        pass

    def allowed(path: Path, content: bytes | None = None, *, content_sha256: str | None = None) -> bool:
        try:
            # Do not let a symlink/junction redirect a legitimate name.
            path = Path(path)
            rel = path.relative_to(home)
            if path.resolve() != home / rel or any(
                    (home / Path(*rel.parts[:i])).is_symlink()
                    for i in range(1, len(rel.parts) + 1)):
                return False
            if scope.administrator:
                return True
            grant = grants.get(rel.as_posix())
            if not isinstance(grant, dict):
                return False
            targets = grant.get("scopes")
            if not isinstance(targets, list) or not any(
                    one == "public" or scope.permits(one) for one in targets):
                return False
            # Host readers may supply the digest of their validated byte snapshot
            # to avoid retaining an entire JSONL file just for this check.
            if content_sha256 is not None:
                if content is not None or re.fullmatch(r'[0-9a-f]{64}', content_sha256) is None:
                    return False
                digest = content_sha256
            else:
                digest = hashlib.sha256(path.read_bytes() if content is None else content).hexdigest()
            return grant.get("sha256") == digest
        except (OSError, ValueError, TypeError):
            return False

    return allowed

def event_excerpt(store: Any, scope: RetrievalScope, *, cursor: int, offset: int = 0) -> str:
    """Read a window of an explicitly referenced event through the same scope."""
    scope.require()
    if type(cursor) is not int or cursor < 1:
        raise ValueError("cursor 须为正整数。")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset 须为非负整数。")
    reader = getattr(store, 'read_text_window', None)
    if type(scope) is RetrievalScope:
        projected_reader = getattr(store, 'read_retrieval_window', None)
        if callable(projected_reader):reader = projected_reader
    if callable(reader):
        result = reader(cursor, offset, 2000, accept=scope.event)
        if result is None:
            raise PermissionError("当前可见事件中没有可读取的这一条。")
        row, total, text = result
    else:
        # Preserve the small host-library/get-only store contract.
        row = store.get(cursor)
        if row is None or not scope.event(row):
            raise PermissionError("当前可见事件中没有可读取的这一条。")
        total = len(row.text)
        text = row.text[offset:offset + 2000]
    stop = min(offset + 2000, total)
    head = f"[event:{cursor}] {row.ts}｜{row.source}｜{row.type}｜正文共 {total} 字"
    from .source_attribution import project
    label, event_source = project(row, scope, f"event:{cursor}")
    head += "\n" + label
    if offset >= total:
        return head + "\n（这一偏移处已无正文。）"
    more = f"\n（继续读同一 cursor，offset={stop}）" if stop < total else ""
    from .original_evidence import OriginalText, event_span, source
    originals = source(row.ts, text, f"event:{cursor}") if row.origin == "external" and row.type == "message" else ()
    spans = ()
    if offset == 0 and len(text) == total and getattr(row, 'cursor', None) == cursor:
        spans = event_span(row, text, text, len(head) + 1)
    return OriginalText(head + "\n" + text + more, originals, wire_spans=spans, event_sources=(event_source,),
                        event_times=((f"event:{cursor}", row.ts),) if row.origin == "external" and row.type == "message" else ())
