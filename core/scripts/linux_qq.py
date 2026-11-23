"""Linux QQ host for the release's private Mybot workspace."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import urlsplit
from uuid import uuid4

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from scripts.linux_snowluma import listener_pid, program
from qq_protocol_health import eligible, probe
from infinity_control import QQControl, _tail
from snowluma_connection import connection_settings

PLUGIN = 'core-infinity-qq'


def save_json(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        pending = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
    try:
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def trusted(core, config):
    """Both loopback listeners must belong to this release's SL process."""
    try:
        if not eligible(config, config.get('linuxAccount')):
            return False
        ports = {urlsplit(config[k]).port for k in ('httpUrl', 'wsUrl')}
        return listener_pid(core, ports) is not None
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return False


async def verify_ws(socket, account):
    echo = uuid4().hex
    await socket.send(json.dumps({'action': 'get_login_info', 'params': {}, 'echo': echo}))
    async with asyncio.timeout(5):
        for _ in range(128):
            body = json.loads(await socket.recv())
            if isinstance(body, dict) and body.get('echo') == echo:
                if (body.get('status') == 'ok' and type(body.get('retcode')) is int
                        and body['retcode'] == 0 and isinstance(body.get('data'), dict)
                        and str(body['data'].get('user_id')) == account):
                    return
                break
    raise ValueError('SL WebSocket 登录账号与配置不符')


def require_connection(core, data):
    account = data.get('linuxAccount')
    status = probe(data, account, current=lambda: True, trusted=lambda: trusted(core, data))
    if status['status'] != 'online':
        raise ValueError('SL 身份或在线状态检查未通过（' + status['status'] + '）')


class LinuxQQControl(QQControl):
    def __init__(self, root, **kwargs):
        from scripts.linux_sl_companion import web_port
        from scripts.linux_qq_management import LinuxRecovery
        core = root.parent.parent
        self.release_core = core
        port = web_port(core.parent/'runtime/linux-snowluma')
        kwargs.setdefault('tts_runtime', core.parent/'services/CosyVoice3')
        recovery = LinuxRecovery(self, kwargs['bot_scope']/'processes/snowluma-recovery.json', core, root, port)
        recovery.legacy_account = lambda: self._config().get('linuxAccount', '')
        super().__init__(root, snowluma_root=program(core), recovery=recovery, **kwargs)
        self.management_port = port

        from qq_protocol_health import ProtocolHealth
        self.local_health = ProtocolHealth(self._local_snapshot,
            lambda data: trusted(core, data), lambda data: False)

    def _local_snapshot(self):
        from runtime_identity import safe_path
        self._require_open()
        account = self.recovery.state().get('targetUin') or ''
        directory = self.release_core.parent/'runtime/linux-snowluma/config'
        try:
            directory = safe_path(self.release_core.parent, directory)
            if account:
                if not account.isascii() or not account.isdecimal() or len(account)>20:
                    return {}, '', False
                files = [directory/f'onebot_{account}.json']
            else:
                files = [p for p in directory.glob('onebot_*.json')
                         if p.stem[7:].isascii() and p.stem[7:].isdecimal() and len(p.stem[7:])<=20]
            if len(files) != 1:
                return {}, '', False
            path = safe_path(directory, files[0])
            account = path.stem[7:]
            with path.open('rb') as f:
                raw = f.read(1024*1024+1)
            if len(raw)>1024*1024:
                return {}, '', False
            config = json.loads(raw)
            if not isinstance(config, dict) or config.get('mode') != 'snapshot':
                return {}, '', False
            self._validate_scoped_target(account, config)
            data = dict(connection_settings(config), linuxAccount=account)
            return data, account, True
        except (OSError, ValueError):
            return {}, '', False

    def _verified_local(self):
        data, account, available = self._local_snapshot()
        if available and probe(data, account, current=lambda: self._local_snapshot()==(data, account, True),
                               trusted=lambda: trusted(self.release_core, data))['status']=='online':
            return data
        return {}

    def _route_config(self):
        data = self._config()
        if not data.get('token'):
            data.update(self._verified_local())
        return data

    def close(self):
        try:
            self.local_health.close()
        finally:
            super().close()

    def start_snowluma_with_bot(self, *, enabled=False):
        if not enabled or listener_pid(self.release_core, {self.management_port}) is None:
            return
        from scripts.linux_qq_wake import wake
        fd = os.pidfd_open(os.getpid())
        try:
            message = wake(self.release_core, fd, selected=self.recovery.state().get('qqPath') or '')
            if message:
                print('[QQ] ' + message, flush=True)
        finally:
            os.close(fd)
        # Let the AppImage mount, QQ identity and compatibility environment settle
        # before recovery enumerates the new process.
        from scripts.linux_qq_desktop import qq_processes
        import time
        stable = None
        for _ in range(25):
            rows = qq_processes()
            pid = rows[0]['ProcessId'] if len(rows) == 1 else None
            if pid is not None and pid == stable:
                time.sleep(2)
                break
            stable = pid
            time.sleep(.2)
        self.recovery.start()

    def initial_snowluma_password(self):
        from scripts.linux_qq_management import initial_password
        self._require_open()
        return initial_password(self.release_core, self.management_port)

    def _snow_management_password_changed(self):
        base = self.release_core.parent
        return self._read_password_changed(base, base / 'runtime/linux-snowluma/config/webui.json')

    def import_connection(self, *, reload=None):
        from snowluma_connection import connection_settings
        self._require_open()
        target = self.recovery.state().get('targetUin')
        local = self._verified_local()
        settings = local or connection_settings(self.recovery.connection_config())
        if self.recovery.state().get('targetUin') != target:
            raise ValueError('目标 QQ 已变化，请重新导入')
        target = target or local.get('linuxAccount')
        if not target:
            raise ValueError('目标 QQ 已变化，请重新导入')
        with self._lock:
            original = self.config_path.read_bytes() if self.config_path.exists() else None
            data = self._config()
            data.update(settings, linuxAccount=target)
            self._commit_config(data, original, reload if data.get('privates') or data.get('groups') else None)
        return {'ok': True, 'message': '已导入目标账号的 OneBot 配置。请保存会话名单并检测接入。'}

    def _ws_endpoint_trusted(self, data):
        return trusted(self.release_core, data)

    def installation_status(self):
        root = program(self.release_core)
        ready = (root / 'index.mjs').is_file() and os.access(root / 'node', os.X_OK)
        return {'nodeAvailable': ready, 'entryPresent': ready, 'ready': ready,
                'nodeVersion': 'v24.22.1' if ready else ''}

    def _snow_log(self, secrets):
        from runtime_identity import safe_path
        base = self.release_core.parent
        try:
            folder = safe_path(base, base/'runtime/linux-snowluma/logs')
            latest = max((p for p in folder.glob('snowluma-*.log')
                          if re.fullmatch(r'snowluma-\d{4}-\d{2}-\d{2}\.log', p.name)
                          and not p.is_symlink() and p.is_file()), key=lambda p: p.name, default=None)
            if latest is None:
                return ''
            tail = _tail(safe_path(base, latest), secrets)
        except (OSError, ValueError):
            return ''
        # Native stdout also includes login details and per-action chat payloads.
        lines = []
        for line in tail.splitlines():
            if not re.match(r'^\d{2}:\d{2}:\d{2} ', line):
                continue
            if re.search(r'\[WebUI(?:\.Auth)?\].*(?:密码|凭据|credential|bootstrap)', line, re.I):
                continue
            lines.append(line.partition(' params=')[0])
        return '\n'.join(lines)

    def action(self, action):
        from scripts.linux_sl_companion import ensure, managed_watcher
        import select, signal
        self._require_open()
        if action in ('snowluma_start', 'snowluma_start_service', 'snowluma_recover'):
            fd = os.pidfd_open(os.getpid())
            try:
                ensure(self.release_core, fd)
            finally:
                os.close(fd)
            if action != 'snowluma_start_service':
                self.recovery.start()
        elif action == 'snowluma_stop':
            with managed_watcher(self.release_core, self.management_port) as fd:
                if fd is None:
                    raise ValueError('此 SL 由独立终端启动，请在原窗口休止')
                self.recovery.cancel()
                signal.pidfd_send_signal(fd, signal.SIGTERM)
                if not select.select([fd], [], [], 12)[0]:
                    raise ValueError('SL 正在收尾，请稍后刷新')
        elif action == 'snowluma_stop_installation':
            raise ValueError('独立启动的 SL 请在原窗口休止')
        else:
            return super().action(action)
        return self.read()

    def read(self):
        from scripts.linux_sl_companion import managed_watcher
        result = super().read()
        up = listener_pid(self.release_core, {self.management_port}) is not None
        with managed_watcher(self.release_core, self.management_port) as handle:
            managed = handle is not None
        result['snowluma'].update(webuiRunning=up, managed=managed, sharedService=up and not managed,
            controlAvailable=True, controlMessage='', canStopInstallation=False)
        data, account, available = self._local_snapshot()
        health = self.local_health.read(data, account, available)
        online = health['fresh'] and health['status'] == 'online' and health['identityVerified']
        snow = result['snowluma']
        from scripts.linux_qq_wait_status import waiting_for_exit
        snow['waitingForDesktopExit'] = up and not online and waiting_for_exit(self.release_core)
        snow['localAccountOnline'] = bool(online)
        if online:
            snow['recovery']['targetUin'] = snow['recovery'].get('targetUin') or account
            snow.update(configured=True, running=True)
        return result


def configure(core, config_path):
    from infinity_control import _qq_ids
    print('在已启动的 SL WebUI 接入 Linux QQ，并启用带令牌的本机 HTTP 和正向 Universal WebSocket。')
    account = _qq_ids([input('机器人登录的 QQ 号：').strip()], 'linuxAccount')[0]
    # v2.4 stores either a complete per-account snapshot or an overlay.
    # Only a snapshot has standalone semantics; validate both live endpoints.
    from runtime_identity import safe_path
    path = safe_path(core.parent, core.parent / 'runtime/linux-snowluma/config' / f'onebot_{account}.json')
    settings = None
    if path.is_file() and path.stat().st_size <= 1024 * 1024:
        raw = json.loads(path.read_bytes())
        if isinstance(raw, dict) and raw.get('mode') == 'snapshot':
            settings = connection_settings(raw)
    if settings is None:
        http = int(input('HTTP 端口 [3000]：').strip() or '3000')
        ws = int(input('WebSocket 端口 [3001]：').strip() or '3001')
        token = getpass.getpass('HTTP 令牌：')
        ws_token = getpass.getpass('WebSocket 令牌（相同直接回车）：') or token
        def listener(port, secret):
            return {'host': '127.0.0.1', 'port': port, 'accessToken': secret}
        settings = connection_settings({'networks': {
            'httpServers': [listener(http, token)], 'wsServers': [listener(ws, ws_token)]}})
    data = json.loads(config_path.read_bytes()) if config_path.exists() else {}
    data.update(settings)
    privates = _qq_ids(input('允许的私聊 QQ 号（空格分隔）：').split(), 'privates')
    groups = _qq_ids(input('允许的群号（空格分隔）：').split(), 'groups')
    if not (privates or groups):
        raise ValueError('至少填写一个私聊或群号')
    data.update(linuxAccount=account, privates=privates, groups=groups)
    require_connection(core, data)
    async def check():
        from websockets.asyncio.client import connect
        async with connect(data['wsUrl'], additional_headers={'Authorization':'Bearer ' + data['wsToken']},
                           proxy=None, open_timeout=5, close_timeout=2) as socket:
            await verify_ws(socket, account)
    try:
        asyncio.run(check())
    except Exception as error:
        raise ValueError('SL WebSocket 账号核对失败，请检查正向 Universal 节点、端口和令牌。') from error
    save_json(config_path, data)
    print('HTTP 和 WebSocket 账号核对通过，已保存会话名单。')


def main(argv=None):
    from scripts.linux_profile_start import main as start_profile
    return start_profile(argv)


if __name__ == '__main__':
    raise SystemExit(main())
