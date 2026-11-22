"""Console follow-up lifecycle; only legacy and explicit local-owner scopes.

Reconcile before firing timers. Claim before enqueue for at-most-once attempts;
a failed enqueue is not automatically retried. Explicit user timers are untouched.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from . import snapshot_json

LEDGER = "follow-up-attempt.json"
_UNREAD = object()


def _read_attempt(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _publish_attempt(path, body, expected):
    """Publish only against the same authority used to decide this claim."""
    def verify():
        if _read_attempt(path) != expected:
            raise ValueError('follow_up_attempt_ledger_changed')
    raw = snapshot_json.dumps(body, ensure_ascii=False, indent=1).encode('utf-8')
    verify()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '.tmp-', dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        verify()
        if expected is None:
            os.link(tmp, path)
        else:
            os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass  # Cleanup must not hide failure or undo a committed claim.


def _scope_and_rows(console):
    from .owner_tick import OWNER_SCOPE
    rows = list(console.session.records())
    if any(getattr(r, "scope", "") == OWNER_SCOPE for r in rows):
        return OWNER_SCOPE, [r for r in rows if getattr(r, "scope", "") == OWNER_SCOPE]
    return "", rows


def owner_view(console):
    """Read only this console owner's history for gates and follow-up wording."""
    from types import SimpleNamespace
    scope, rows = _scope_and_rows(console)
    return SimpleNamespace(session=SimpleNamespace(records=lambda: tuple(rows))) if scope else console


def _candidate(console):
    scope, rows = _scope_and_rows(console)
    messages = [r for r in rows if getattr(r, "kind", "") == "message"
                and getattr(r, "role", "") in ("assistant", "user")]
    if not messages or messages[-1].role != "assistant":
        return None
    last = messages[-1]
    if not scope:
        # Keep legacy fail-closed behavior: no guessing a foreign channel.
        for row in reversed(messages):
            if getattr(row, "scope", ""):
                return None
            if row.role == "user":
                break
    text = str(getattr(last, "text", "") or "").strip()
    ts = getattr(last, "ts_ms", None)
    if not text or type(ts) is not int or ts <= 0:
        return None
    identity = [scope, ts, text] if scope else [ts, text]
    key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    return ts, key, scope


def candidate(console):
    source = _candidate(console)
    return source[:2] if source else None


def event_valid(event, console, pending=()):
    """Recheck at dequeue, after any reply/replacement while the tick waited."""
    from .owner_tick import OWNER_SCOPE, MARKER, route
    from .session_view import event_scope
    if route(event) != OWNER_SCOPE:
        return False
    # A newly received owner message already queued also cancels the wake.
    if any(getattr(e, "origin", None) == "external" and event_scope(e) == OWNER_SCOPE
           for e in pending):
        return False
    try:
        source = _candidate(console)
    except Exception:
        return False
    bound = event.meta[MARKER]
    return source == (bound["source_ms"], bound["source_key"], OWNER_SCOPE)



def _book(root, *, raw=_UNREAD):
    path = Path(root) / LEDGER
    if raw is _UNREAD:
        raw = _read_attempt(path)
    if raw is None:
        return {}
    try:
        book = snapshot_json.loads(raw.decode('utf-8'))
    except RecursionError as error:
        raise ValueError('follow_up_attempt_ledger_nesting_exceeded') from error
    if not isinstance(book, dict) or not isinstance(book.get("key"), str) or not book["key"]:
        raise ValueError("follow_up_attempt_ledger_invalid")
    keys = book.get("keys", [book["key"]])
    if (not isinstance(keys, list) or any(type(k) is not str or not k for k in keys)
            or book["key"] not in keys):
        raise ValueError("follow_up_attempt_keys_invalid")
    return {**book, "keys": keys}


def _claimed(root, key):
    book = _book(root)
    return key in book.get("keys", [])


def reconcile(console, store, root, *, now_ms, enabled, quiet_hours,
              delay_ms, followed_ms=None, on_change=None):
    """Keep one source-bound timer; cancel on reply, replacement or opt-out."""
    source = _candidate(console) if enabled and not quiet_hours else None
    if source and (source[0] > now_ms or
                   (followed_ms is not None and followed_ms >= source[0]) or
                   _claimed(root, source[1])):
        source = None
    kept = False
    for alarm in store.list():
        payload = alarm.payload
        if not isinstance(payload, dict) or not payload.get("follow_up"):
            continue
        matches = (source is not None and payload.get("source_key") == source[1]
                   and payload.get("source_ms") == source[0]
                   and payload.get("source_scope", "") == source[2])
        if matches and not kept:
            kept = True
        else:
            if store.cancel(alarm.id) and on_change is not None:
                on_change()
    if source is None or kept:
        return False
    import datetime
    deadline = max(now_ms, source[0] + delay_ms)
    store.set(datetime.datetime.fromtimestamp(deadline / 1000).isoformat(),
              {"what": "看看要不要再说一句", "follow_up": True,
               "source_ms": source[0], "source_key": source[1],
               "source_scope": source[2]})
    if on_change is not None:
        on_change()
    return True


def claim(console, alarm, root, *, now_ms, enabled, quiet_hours, delay_ms):
    """Recheck source and deadline immediately before attempting a wake."""
    if not enabled or quiet_hours:
        return False
    from .source_guard import lease
    path = Path(root) / LEDGER
    # One cooperating claimant owns the complete check-and-publish interval,
    # including across processes. Do not return permission before durable intent.
    with lease(path):
        source = _candidate(console)
        payload = alarm.payload
        if source is None or not isinstance(payload, dict):
            return False
        ts, key, scope = source
        if (payload.get("source_key") != key or payload.get("source_ms") != ts
                or payload.get("source_scope", "") != scope
                or now_ms < ts + delay_ms):
            return False
        raw = _read_attempt(path)
        book = _book(root, raw=raw)
        if key in book.get("keys", []):
            return False
        keys = list(dict.fromkeys([*book.get("keys", []), key]))
        _publish_attempt(path, {"key": key, "keys": keys, "source_ms": ts,
                                "attempt_ms": now_ms}, raw)
        if os.name != 'nt':
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return True
