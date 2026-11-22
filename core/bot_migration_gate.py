"""Persistent fail-closed startup fence and retired names, not a migrator.

Creating a fence does not stop or drain running services. No automatic unfence API:
only a future verified recovery/commit coordinator may remove it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from runtime_identity import safe_path

GATE = 'bot-migration-gate.json'
CREATION_GATE = 'bot-new-deployment-gate.json'
RETIRED = 'bot-retired-names.json'


class MigrationBlocked(ValueError):
    pass


def assert_no_migration(base: Path) -> None:
    path = safe_path(base, base / GATE)
    # Existence is the fence, not a caller-writable status/expiry/JSON field.
    if path.exists() or safe_path(base, base / CREATION_GATE).exists():
        raise MigrationBlocked('有未解除的 Bot 迁移门禁；拒绝启动或修改身份，请先核对事务与备份，不要删除标记绕过')


def retired_names(base: Path) -> set[str]:
    from bot_registry import bot_id
    path = safe_path(base, base / RETIRED)
    if not path.exists():
        return set()
    try:
        with path.open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError()
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate identity manifest key')
                result[key] = value
            return result
        data = json.loads(raw, object_pairs_hook=unique_object)
        if (not isinstance(data, dict) or set(data) != {'version', 'names'}
                or type(data['version']) is not int or data['version'] != 1
                or not isinstance(data['names'], list) or len(data['names']) > 1024):
            raise ValueError()
        names = [bot_id(name) for name in data['names']]
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError()
        return set(names)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError('旧名退休登记损坏或不可读；拒绝继续，不自动清空') from exc


def assert_identity_allowed(base: Path, name: str) -> None:
    from bot_registry import bot_id
    assert_no_migration(base)
    if safe_path(base,base/'host-maintenance.json').exists():
        raise MigrationBlocked('全局 HOST 维护门禁未退役；拒绝新运行实例，不自动删除门禁')
    name = bot_id(name)
    if name.casefold() in {item.casefold() for item in retired_names(base)}:
        raise ValueError('此 Bot 名字已退休；禁止启动、重新绑定或复用，请使用新名字')


def install_gate(base: Path, transaction_id: str) -> None:
    """Low-level exclusive fence only; provides NO quiescence or rename authority."""
    from runtime_identity import identity_lock
    if not isinstance(transaction_id, str) or re.fullmatch('[0-9a-f]{32}', transaction_id) is None:
        raise ValueError('迁移事务编号无效')
    assert_no_migration(base)
    with identity_lock(base):
        assert_no_migration(base)
        path = safe_path(base, base / GATE)
        # A partial write remains a fence. Never replace an existing transaction.
        with path.open('x', encoding='utf-8') as stream:
            json.dump({'version': 1, 'transaction': transaction_id, 'state': 'fenced'}, stream)
            stream.flush(); os.fsync(stream.fileno())


def guarded_instance_lock(base: Path, runtime: Path, name: str, acquire):
    """Serialize final startup admission with cooperative fence installation."""
    from runtime_identity import identity_lock
    safe_path(base, runtime)
    assert_identity_allowed(base, name)
    with identity_lock(base):
        assert_identity_allowed(base, name)
        return acquire(runtime)
