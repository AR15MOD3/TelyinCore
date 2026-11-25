import json
import os
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

import bot_lifecycle_boot
import bot_host_observer
from bot_global_admission import ENTRY_FILES
from telyincore import online_install, update_preflight
from telyincore.package_updates import PackageUpdates
from telyincore.web import WebService


def owner(folder, digit, pid, created):
    identity = dict(pid=pid, created_filetime=created, image=str(folder/'python'), state='alive')
    (folder / (digit*32 + '.json')).write_text(json.dumps({
        'version': 1, 'owner': identity, 'entry_sources': {n: 'a'*64 for n in ENTRY_FILES}}))
    return identity


def test_windows_only_current_launcher_and_previous_boot_allowed(tmp_path, monkeypatch):
    import bot_global_admission
    pins = {n: 'a'*64 for n in ENTRY_FILES}
    monkeypatch.setattr(bot_global_admission, 'source_pins', lambda _: pins)
    folder = tmp_path/'core/data/host-start-owners'
    folder.mkdir(parents=True)
    monkeypatch.setattr(bot_lifecycle_boot, 'boot_filetime', lambda: 5_000_000_000)
    owner(folder, '0', 999, 1)
    current = owner(folder, '1', os.getppid(), 5_000_000_100)
    monkeypatch.setattr(bot_host_observer, 'probe_process', lambda pid: current if pid == os.getppid() else {'state':'absent'})
    update_preflight.check_before_exit(tmp_path, 'win', 7799)
    owner(folder, '2', os.getppid(), 5_000_000_001)  # recycled PID
    owner(folder, '3', 33272, 5_000_000_002)
    with pytest.raises(update_preflight.UpdateBlocked, match=f'{os.getppid()}、33272'):
        update_preflight.check_before_exit(tmp_path, 'win', 7799)
    assert len(list(folder.iterdir())) == 4


@pytest.mark.parametrize('action', ['install', 'restart', 'stage'])
def test_blocked_http_leaves_listener_and_pending_untouched(tmp_path, action):
    core = tmp_path/'core/data'
    core.mkdir(parents=True)
    (core/'host-maintenance.json').write_text('{}')
    (tmp_path/'candidate.json').write_text(json.dumps({'schema':'windows-core-acceptance-v1','version':'beta.0.5.4.9'}))
    checker = PackageUpdates(tmp_path, 'win', 'example/Core', lambda *_: {'latest':'beta.0.5.6.3'})
    op = build_opener(ProxyHandler({}))
    with WebService(port=0, package_updates=checker) as server:
        server._shutdown_callback = lambda: pytest.fail('premature shutdown')
        req = Request(server.url('/package-update/'+action), data=b'{"cachedVersion":"beta.0.5.6.3"}',
                      headers={'Origin':server.url().rstrip('/')})
        with pytest.raises(HTTPError) as exc:
            op.open(req,timeout=5)
        reply = json.load(exc.value)
        assert exc.value.code == 409 and reply['code'] == 'update_preflight_blocked'
        assert 'Core 继续运行' in reply['error'] and '档案维护' in reply['error']
        assert json.load(op.open(server.url('/package-update'),timeout=5))['current'] == 'beta.0.5.4.9'
        assert not checker._restart_requested and not server.shutdown_requested
        assert online_install.pending(tmp_path) is None


def test_linux_foreign_flock_is_reported_while_service_lives(tmp_path):
    fcntl = pytest.importorskip('fcntl')
    core = tmp_path/'core'
    core.mkdir()
    with (core/'.linux-acceptance-launch.lock').open('wb') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        with pytest.raises(update_preflight.UpdateBlocked, match='另一项服务'):
            update_preflight.check_before_exit(tmp_path,'linux',7810)
    update_preflight.check_before_exit(tmp_path,'linux',7810)
