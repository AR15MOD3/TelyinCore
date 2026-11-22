"""Accept an explicit, already-identified account from this Bot's live SnowLuma."""
import hashlib
import hmac
import json
import os
import re
import secrets
import time

from bot_configured_account import _digest_path
from bot_guarded_store import GuardedStore
from bot_plugin_ownership import _account
from bot_private_file import open_private_new
from runtime_identity import identity_lock, safe_path


def assert_account_available(grant, target, store):
    legacy = grant.core / 'data/infinity-processes/snowluma-recovery.json'
    bots = safe_path(grant.core.parent, grant.core.parent / 'bots')
    others = [legacy]
    if bots.is_dir():
        others.extend(folder / 'infinity/processes/snowluma-recovery.json'
                      for folder in bots.iterdir() if folder.is_dir() and not folder.name.startswith('.'))
    for other in others:
        if other == store:
            continue
        existing = GuardedStore(grant.core.parent, other).read().get('targetUin')
        if existing and _account(existing) == target:
            raise ValueError('这个 QQ 已绑定到另一个 Bot')
    reservation_id = hashlib.sha256(b'configured-qq-account-v1\0' + target.encode()).hexdigest()
    reservation = grant.base / 'qq-configured-account-reservations' / (reservation_id + '.json')
    if safe_path(grant.core.parent, reservation.with_suffix('.pending')).exists():
        raise ValueError('这个 QQ 的账号预留尚未完成')
    reservation_store = GuardedStore(grant.core.parent, reservation)
    if reservation_store.raw is not None and reservation_store.read() != dict(
            version=1, scope=_digest_path(grant.base),
                                     account=reservation_id, store=_digest_path(store)):
        raise ValueError('这个 QQ 已由另一个 Bot 预留')
    digest = hashlib.sha256(b'parent-account-admission-v1\0' + target.encode()).hexdigest()
    admission = grant.base / 'parent-account-admissions' / (digest + '.json')
    for path in (admission, admission.with_suffix('.work.json'),
                 admission.with_suffix('.local-retired.json')):
        if safe_path(grant.core.parent, path).exists():
            raise ValueError('这个 QQ 仍有托管登记，无法首次绑定')


class FirstSnowAccount:
    def __init__(self, host, instance):
        self.host = host
        self.key = secrets.token_bytes(32)
        self.request = instance.root / ('first-account-' + secrets.token_hex(16) + '.json')
        self.response = self.request.with_suffix('.result')
        self.last_nonce = None

    def environment(self):
        return {'SNOWLUMA_FIRST_ACCOUNT_FILE': str(self.request),
                'SNOWLUMA_FIRST_ACCOUNT_KEY': self.key.hex()}

    def _read(self):
        root = self.host.core.parent
        # Use the same bounded, non-link snapshot rules as account storage.
        store = GuardedStore(root, self.request)
        raw = store.raw
        if raw is None:
            return None
        signature, separator, body = store.read().get('signed', '').partition('\n')
        if not separator or not hmac.compare_digest(
                signature.encode('ascii'), hmac.digest(self.key, body.encode(), 'sha256').hex().encode()):
            return None
        row = json.loads(body)
        if (type(row) is not dict or set(row) != {'nonce', 'uin', 'expires'}
                or type(row['nonce']) is not str or not re.fullmatch('[0-9a-f]{32}', row['nonce'])
                or type(row['uin']) is not str or not re.fullmatch('[1-9][0-9]{4,19}', row['uin'])
                or type(row['expires']) is not int
                or not time.time() * 1000 < row['expires'] < time.time() * 1000 + 10000):
            return None
        return row

    def _checkpoint(self):
        host = self.host
        grant = host.account_grant
        host.account_fence.check()
        grant._native(host.controller, host.owner)
        if grant.pending or grant.local_retirement != 'open' or grant.path is not None or grant.work_state != 'idle' or host.qq.attempted:
            raise ValueError('当前档案已存在账号工作，无法执行首次绑定')

    def _check_available(self, target, store):
        assert_account_available(self.host.account_grant, target, store)

    def _bind(self, target, expires):
        def checkpoint():
            if time.time() * 1000 >= expires:
                raise ValueError('账号选择已过期')
            self._checkpoint()

        host = self.host
        grant = host.account_grant
        with identity_lock(grant.base):
            checkpoint()
            store = GuardedStore(host.core.parent, grant.store, checkpoint=checkpoint)
            data = store.read()
            existing = data.get('targetUin')
            if existing:
                if _account(existing) != target:
                    raise ValueError('当前 Bot 已绑定其他 QQ')
                return
            self._check_available(target, grant.store)
            # The settings file is the durable account intent. Other first
            # binds scan it under this same identity lock.
            data.setdefault('username', 'admin')
            data.setdefault('qqPath', '')
            data['targetUin'] = target
            store.write(data)
        # Existing admission owns the account settings and supports later changes
        # to credentials for this same account through the usual Core workflow.
        grant.check(host.controller, host.owner, host.account_fence)

    def tick(self):
        try:
            if not self.request.exists():
                return
            row = self._read()
        except (OSError, ValueError, AttributeError, TypeError):
            return
        if row is None or row['nonce'] == self.last_nonce:
            return
        self.last_nonce = row['nonce']
        try:
            self._bind(row['uin'], row['expires'])
        except (OSError, ValueError):
            ok = False
        else:
            ok = True
        temporary = self.response.with_suffix('.' + row['nonce'] + '.writing')
        try:
            temporary = safe_path(self.host.core.parent, temporary)
            with open_private_new(temporary) as stream:
                stream.write(json.dumps({'nonce': row['nonce'], 'ok': ok}).encode())
            try:
                os.replace(temporary, safe_path(self.host.core.parent, self.response))
            finally:
                temporary.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
