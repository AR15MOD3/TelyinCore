"""Held, revalidated offline evidence for managed instances; no stop/kill operations.

The inventory callback must be a trusted complete host observer, NOT user JSON.
No production observer is bundled: absence, uncertainty, or a busy lock rejects.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path

from bot_migration_gate import GATE
from bot_registry import bot_id
from runtime_identity import bindings, identity_lock, safe_path


def read_control(path):
    with path.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('oversized offline control manifest')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate offline control field')
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError('nonfinite offline control number')
    data = json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
    if not isinstance(data, dict):
        raise ValueError('invalid offline control manifest')
    return data


def _gate(base, transaction):
    data = read_control(safe_path(base, base / GATE))
    if (data != {'version': 1, 'transaction': transaction, 'state': 'fenced'}
            or type(data.get('version')) is not int):
        raise ValueError('offline lease requires this exact fenced transaction')


def _scopes(base, bots):
    owner = read_control(safe_path(base, base / 'bot-runtime-owner.json'))
    baseline = bot_id(owner.get('baseline'))
    names = {baseline, *bindings(base)}
    roots = {base}
    runtime_parent = safe_path(base, base / 'bot-runtimes')
    if runtime_parent.exists():
        for path in runtime_parent.iterdir():
            safe_path(base, path)
            if not path.is_dir():
                raise ValueError('unclassified runtime scope')
            names.add(bot_id(path.name)); roots.add(path)
    instances = safe_path(base, base / 'instances')
    records = {}
    if instances.exists():
        for path in instances.iterdir():
            safe_path(base, path)
            if path.suffix != '.json' or not path.is_file():
                raise ValueError('unclassified instance record')
            name = bot_id(path.stem); record = read_control(path)
            if type(record.get('pid')) is not int or record['pid'] <= 0:
                raise ValueError('invalid instance PID evidence')
            names.add(name); records[name] = record
    if bots.is_dir():
        for path in bots.iterdir():
            if path.name == '.deleted' or path.name.startswith('.bot-init-'):
                continue
            safe_path(bots, path)
            if path.is_dir():
                names.add(bot_id(path.name))
    return sorted(names), sorted(roots), records


def _observe(observer, base, names, records):
    if not callable(observer):
        raise ValueError('trusted complete process observer required; missing is not offline')
    value = observer(base.parent, tuple(names), dict(records))
    if (not isinstance(value, dict) or value.get('complete') is not True
            or value.get('core') != str(base.parent.resolve())
            or value.get('unresolved') != [] or not isinstance(value.get('processes'), list)):
        raise ValueError('incomplete or wrong-scope process evidence')
    # Even drained live processes may write again; no process is adopted or killed.
    if value['processes']:
        raise ValueError('managed/unknown processes remain; stale PID or zero pending is not offline')


def acquire_runtime_lock(root):
    """Native cooperating-instance lock, bounded and non-blocking."""
    path = safe_path(root, root / '.telyincore-instance.lock')
    handle = path.open('a+b'); handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close(); return None
    return handle


def release_runtime_lock(handle):
    try:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


class OfflineLease:
    def __init__(self, base, bots, transaction, observer, names, roots, records, handles):
        self.base, self.bots, self.transaction = base, bots, transaction
        self.observer, self.names, self.roots, self.records = observer, names, roots, records
        self.handles = handles; self.active = True; self.stage = 'runtime_locks_held'

    def validate(self):
        if not self.active:
            raise ValueError('offline lease expired; cannot reuse a persisted boolean')
        _gate(self.base, self.transaction)
        names, roots, records = _scopes(self.base, self.bots)
        if (names, roots, records) != (self.names, self.roots, self.records):
            raise ValueError('managed scope/instance evidence changed')
        _observe(self.observer, self.base, self.names, self.records)

    def release_for_directory_move(self):
        """Windows cannot rename a directory containing our open lock file.

Only after independent absence evidence; global identity lock and durable fence
remain held by the enclosing lease. A new process must obtain a NEW lease.
"""
        self.validate()
        while self.handles:
            release_runtime_lock(self.handles.pop())
        self.stage = 'fenced_global_lock_held'
        self.validate()


@contextmanager
def managed_offline_lease(base: Path, bots: Path, transaction: str, *, observer=None):
    """All known roots, records, discovered scopes; fail closed on unknowns.

No production process scanner or public offline receipt deserializer is provided.
"""
    safe_path(base, base); safe_path(bots, bots)
    if not callable(observer):
        raise ValueError('trusted complete process observer required')
    with identity_lock(base):
        _gate(base, transaction)
        names, roots, records = _scopes(base, bots)
        _observe(observer, base, names, records)
        handles = []; lease = None
        try:
            for root in roots:
                handle = acquire_runtime_lock(root)
                if handle is None:
                    raise ValueError('managed runtime lock busy; no offline proof')
                handles.append(handle)
            lease = OfflineLease(base, bots, transaction, observer, names, roots, records, handles)
            lease.validate()
            yield lease
        finally:
            if lease is not None:
                lease.active = False
            while handles:
                release_runtime_lock(handles.pop())
