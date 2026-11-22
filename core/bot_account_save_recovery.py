"""Recover an interrupted settings save without clearing account operation ledgers."""
import hashlib
import json
import os
import re
from uuid import uuid4

from bot_guarded_store import GuardedStore, _object
from bot_host_observer import probe_process
from bot_stable_file import read_stable
from runtime_identity import identity_lock, safe_path


def _digest(raw):
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def save_evidence(grant):
    host = probe_process(os.getpid())
    if host.get('state') != 'alive':
        raise ValueError('account settings HOST identity unavailable')
    store = GuardedStore(grant.core.parent, grant.store)
    return dict(host_pid=host['pid'], host_created=host['created_filetime'],
                store=_digest(str(grant.store).encode()), before=_digest(store.raw),
                before_target=_digest(str(_object(store.raw).get('targetUin') or '').encode()),
                first_save=store.raw is None and store.backup_raw is None)


def recover_interrupted_save(fence, grant):
    """Called before companion birth, after the selected Bot's Job preflight."""
    from bot_login_lifecycle import LoginLifecycle
    from bot_recovery_http_lease import RecoveryClientLease
    from bot_recovery_mutation_journal import MutationJournal
    from bot_startup_probe import _json

    if not fence.path.exists():
        return False
    with identity_lock(grant.base):
        snapshot = read_stable(grant.core.parent, fence.path, 1024)
        row = _json(snapshot[0])
        fields = {'version', 'scope', 'host_pid', 'host_created', 'store', 'before', 'before_target'}
        if (type(row.get('version')) is not int
                or not (row['version'] == 2 and set(row) == fields
                        or row['version'] == 3 and set(row) == fields | {'first_save'}
                        and type(row['first_save']) is bool) or row['scope'] != fence.path.stem
                or row['store'] != _digest(str(grant.store).encode())
                or type(row['host_pid']) is not int or type(row['host_created']) is not int
                or row['host_pid'] <= 0 or row['host_created'] <= 0):
            return False  # Legacy markers have no original HOST or before-image.
        host = probe_process(row['host_pid'])
        if not (host.get('state') == 'absent' or host.get('state') == 'alive'
                and host.get('created_filetime', 0) > row['host_created']):
            return False
        if any(safe_path(grant.core.parent, path).exists() for path in (
            grant.base/'snowluma-companion-custody.json',
            grant.core.parent/'bots'/grant.bot/'infinity/snowluma/companion-custody.json',
        )):
            return False
        store = GuardedStore(grant.core.parent, grant.store)
        data = _object(store.raw)
        target = str(data.get('targetUin') or '')
        if _digest(store.raw) != row['before']:
            first_publication = (row.get('first_save') is True and row['before'] is None
                                 and row['before_target'] == _digest(b'')
                                 and store.backup_raw is None and bool(target))
            if first_publication:
                from bot_snow_first_account import assert_account_available
                from bot_plugin_ownership import _account
                assert_account_available(grant, _account(data.get('targetUin')), grant.store)
            elif (_digest(store.backup_raw) != row['before']
                  or _digest(target.encode()) != row['before_target']):
                return False
        staged = []
        if grant.store.parent.is_dir():
            pattern = re.escape(grant.store.name) + r'(?:\.bak)?\.[0-9a-f]{32}\.pending'
            for path in grant.store.parent.iterdir():
                if not (path.name.startswith(grant.store.name + '.') and path.name.endswith('.pending')):
                    continue
                if not re.fullmatch(pattern, path.name):
                    return False
                staged.append((path, read_stable(grant.core.parent, path, 65536)))
            RecoveryClientLease.check(grant.core.parent, grant.store)
            LoginLifecycle(grant.core.parent, grant.store).check(lambda: None)
            if MutationJournal(grant.core.parent, grant.store)._records():
                return False
        if target:
            digest, _ = grant._snapshot()
            admission = grant.base/'parent-account-admissions'/(digest + '.json')
            if any(safe_path(grant.core.parent, p).exists() for p in (
                    admission, admission.with_suffix('.work.json'),
                    admission.with_suffix('.local-retired.json'))):
                return False
        check = GuardedStore(grant.core.parent, grant.store)
        if (check.raw, check.backup_raw, check._identity, check._backup_identity) != (
                store.raw, store.backup_raw, store._identity, store._backup_identity):
            return False
        if read_stable(grant.core.parent, fence.path, 1024) != snapshot:
            return False
        archive = safe_path(grant.core.parent, grant.base/'retired-account-save-fences')
        archive.mkdir(exist_ok=True)
        archive = safe_path(grant.core.parent, archive/uuid4().hex)
        archive.mkdir()
        for path, original in staged:
            if read_stable(grant.core.parent, path, 65536) != original:
                return False
            path.rename(archive/path.name)
        fence.path.rename(archive/fence.path.name)
        return True
