"""Recover only an absent Core endpoint advertisement, never a HOST/account.

Called by the default launcher while holding its port-allocation lock. All
identity, custody, grant and migration guards remain authoritative afterwards.
No process adoption, termination or account/config reads. A busy old port may
belong to another Bot; only a native-pinned, bounded read-only identity probe
can establish that exception. Unknown listeners remain a refusal.
Cooperative file locks are not an adversarial same-user filesystem sandbox.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import time
import uuid
from contextlib import ExitStack

from bot_registry import bot_id
from bot_host_observer import probe_process
from bot_migration_gate import assert_identity_allowed
from bot_offline_lease import acquire_runtime_lock, release_runtime_lock
from bot_private_file import open_private_new
from bot_stable_file import read_stable
from runtime_identity import identity_lock, safe_path, check_layout


def _snapshot(base, path):
    return read_stable(base, path, 4096)


def _absent(pid):
    row = probe_process(pid)
    if (type(row) is not dict or type(row.get('pid')) is not int
            or row['pid'] != pid or row.get('state') != 'absent'):
        raise ValueError('旧实例仍存活或原生状态未知；不自动清理')


def _other_bot_endpoint(core, name, port):
    """Prove a reassigned IPv4 port, never adopt it or grant offline authority.

    Return a revalidation closure pinned to the other marker, native process
    birth and listener. The caller still holds the target runtime/identity locks.
    """
    import urllib.request
    from bot_startup_probe import _json, _NoRedirect
    from snowluma_connection import management_listener_owners

    def native_identity(pid):
        row = probe_process(pid)
        if (type(row) is not dict or type(row.get('pid')) is not int
                or row['pid'] != pid or row.get('state') != 'alive'
                or type(row.get('created_filetime')) is not int or row['created_filetime'] <= 0
                or not isinstance(row.get('image'), str) or not Path(row['image']).is_absolute()):
            raise ValueError('旧端口进程身份无法核验；不自动清理')
        return row

    listeners = management_listener_owners(port)
    if (not isinstance(listeners, list) or len(listeners) != 1
            or type(listeners[0]) is not dict
            or set(listeners[0]) != {'address', 'pid'}
            or listeners[0]['address'] != '127.0.0.1'
            or type(listeners[0]['pid']) is not int or listeners[0]['pid'] <= 0):
        raise ValueError('旧端口监听归属无法核验；不自动清理')
    pid = listeners[0]['pid']
    process = native_identity(pid)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(f'http://127.0.0.1:{port}/infinity/bots', timeout=1) as response:
        raw = response.read(8193)
    if len(raw) > 8192:
        raise ValueError('旧端口身份响应超限；不自动清理')
    live = _json(raw)
    other = bot_id(live.get('active'))
    if (other.casefold() == name.casefold() or type(live.get('pid')) is not int
            or live['pid'] != pid or live.get('coreRoot') != str(core.resolve())):
        raise ValueError('旧端口不是同一core的其他Bot；不自动清理')
    base = core / 'data'
    assert_identity_allowed(base, other)
    path = safe_path(base, base / 'instances' / (other + '.json'))
    snapshot = _snapshot(base, path)
    marker = _json(snapshot[0])
    if (set(marker) != {'pid', 'port'} or type(marker['pid']) is not int
            or type(marker['port']) is not int or marker != {'pid': pid, 'port': port}):
        raise ValueError('其他Bot登记与旧端口不符；不自动清理')

    def current():
        assert_identity_allowed(base, other)
        if (_snapshot(base, path) != snapshot or native_identity(pid) != process
                or management_listener_owners(port) != listeners
                or native_identity(pid) != process):
            raise ValueError('其他Bot端口归属发生变化；不自动清理')

    current()
    return current


def recover_stale_endpoint(core, name, *, maintenance_lock=None):
    """Archive a stale endpoint after independent absence, lock and port checks.

    False means no advertisement, not complete/offline. True means only this
    file was archived; all remaining launch admission still runs unchanged.
    """
    from bot_startup_probe import _json
    core = Path(core).absolute()
    name = bot_id(name)
    base = core / 'data'
    path = safe_path(base, base / 'instances' / (name + '.json'))
    if not path.exists():
        return False
    def check_recovery_scope():
        if maintenance_lock is None:
            check_layout(base, core.parent / 'bots', selected=name)
        else:
            # Offline maintenance may repair advertisements while a Bot folder
            # is missing. This grants no authority to create/adopt that identity.
            from bot_global_admission import HostAdmissionLock
            from runtime_identity import bindings
            if (not isinstance(maintenance_lock, HostAdmissionLock)
                    or not maintenance_lock.exclusive or maintenance_lock.core != core):
                raise ValueError('残留登记核对需要本部署的独占维护锁')
            maintenance_lock.check()
            if name not in bindings(base):
                raise ValueError('残留实例没有匹配的身份登记；保留原件')
    with identity_lock(base), ExitStack() as stack:
        assert_identity_allowed(base, name)
        check_recovery_scope()
        initial = _snapshot(base, path)
        try:
            marker = _json(initial[0])
            if (set(marker) != {'pid', 'port'} or type(marker['pid']) is not int
                    or not 0 < marker['pid'] < 0xFFFFFFFF
                    or type(marker['port']) is not int or not 0 < marker['port'] <= 65535):
                raise ValueError('旧实例登记损坏或字段未知；保留原件')
        except ValueError:
            # Parsing failure is refusal, never evidence of an absent instance.
            # Keep raw metadata out of the public error; do not proceed to probes.
            raise ValueError('cannot adopt or recover invalid instance marker; '
                             '旧实例登记损坏或字段未知；保留原件') from None
        owner = _json(_snapshot(base, base / 'bot-runtime-owner.json')[0])
        if set(owner) != {'baseline'}:
            raise ValueError('运行数据根归属未知；不自动清理')
        baseline = bot_id(owner['baseline'])
        root = base if name == baseline else base / 'bot-runtimes' / name
        safe_path(base, root)
        # Never create a missing runtime/lock and call that absence evidence.
        lock_path = safe_path(root, root / '.telyincore-instance.lock')
        if not root.is_dir() or not lock_path.is_file():
            raise ValueError('旧实例缺少原运行锁；需人工核对')
        handle = acquire_runtime_lock(root)
        if handle is None:
            raise ValueError('旧实例运行锁仍被占用；请先正常退出原启动器')
        stack.callback(release_runtime_lock, handle)
        directories = (base, root, path.parent)
        pins = {p: (p.stat().st_dev, p.stat().st_ino) for p in directories}
        lock_info = os.fstat(handle.fileno())
        lock_pin = (lock_info.st_dev, lock_info.st_ino)

        def check_pins():
            for directory, identity in pins.items():
                safe_path(base, directory)
                info = directory.stat()
                if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != identity:
                    raise ValueError('核验期间运行目录被替换；不自动清理')
            safe_path(root, lock_path)
            info = lock_path.stat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or not info.st_ino or (info.st_dev, info.st_ino) != lock_pin):
                raise ValueError('核验期间原运行锁被替换；不自动清理')

        check_pins()
        _absent(marker['pid'])
        other_endpoint = None
        families = [(socket.AF_INET, '0.0.0.0')]
        if socket.has_ipv6:
            families.append((socket.AF_INET6, '::'))
        for family, address in families:
            listener = stack.enter_context(socket.socket(family, socket.SOCK_STREAM))
            if os.name == 'nt':
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            if family == socket.AF_INET6:
                listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            try:
                listener.bind((address, marker['port']))
            except OSError as error:
                # Port numbers are shared across Bots, not owned forever by an
                # old advertisement. Only a verified OTHER Bot may occupy it.
                # IPv6 conflicts and unknown/wildcard listeners still refuse.
                if family != socket.AF_INET:
                    raise ValueError('旧实例端口仍被占用或无法独占核验；不自动清理') from error
                try:
                    other_endpoint = _other_bot_endpoint(core, name, marker['port'])
                except Exception:
                    raise ValueError('旧实例端口仍被占用或无法独占核验；不自动清理') from None
        time.sleep(.1)
        _absent(marker['pid'])
        check_pins()
        if _snapshot(base, path) != initial:
            raise ValueError('核验期间实例登记发生变化；保留原件')
        if other_endpoint is not None:
            other_endpoint()
        assert_identity_allowed(base, name)
        check_recovery_scope()
        archive = safe_path(base, base / 'retired-instance-endpoints' / uuid.uuid4().hex)
        archive.mkdir(parents=True, exist_ok=False)
        # Durable intent + byte backup BEFORE the move. Failure preserves marker.
        for filename, raw in (
            ('marker.original', initial[0]),
            ('intent.json', json.dumps(dict(bot=name, marker=marker,
                sha256=hashlib.sha256(initial[0]).hexdigest(),
                scope='endpoint advertisement only; no host/account authority'),
                sort_keys=True).encode('utf-8')),
        ):
            with open_private_new(archive / filename) as stream:
                if stream.write(raw) != len(raw):
                    raise OSError('short stale endpoint backup')
                stream.flush()
                os.fsync(stream.fileno())
        _absent(marker['pid'])
        check_pins()
        if _snapshot(base, path) != initial:
            raise ValueError('归档前实例登记发生变化；保留原件')
        if other_endpoint is not None:
            other_endpoint()
        assert_identity_allowed(base, name)
        # Same-volume no-clobber rename on supported Windows. Do not unlink or
        # touch any other record. A crash leaves original OR recoverable archive.
        os.rename(path, archive / 'retired.json')
        return True
