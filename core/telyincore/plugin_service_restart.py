"""Restart the exact owned Bot through its existing launcher, without applying updates.

This is a cooperative local handoff, not a sandbox or permission to kill a PID.
Only the original parent may consume a request after its child and cleanup exit.
"""
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time

ENV = 'TELYIN_PLUGIN_RESTART_PARENT'
TTL = 600


def offer(parent_pid):
    return f'{parent_pid}:{secrets.token_hex(16)}'


def _offer(value):
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]*:[a-f0-9]{32}', value):
        raise ValueError('当前启动器不支持页面内重启，请更新并重新打开 Core 启动器。')
    parent, nonce = value.split(':')
    return int(parent), nonce


def _path(core, pid):
    from runtime_identity import safe_path
    if type(pid) is not int or pid <= 0:
        raise ValueError('invalid original child')
    return safe_path(Path(core), Path(core)/'data/plugin-restarts'/f'{pid}.json')


def consume(core, pid, bot, port, capability, *, exit_code):
    """Parent checks its ORIGINAL child, never adopts a discovered process."""
    path = _path(core, pid)
    if not path.exists():
        return False
    try:
        raw = path.read_bytes()
        if len(raw) > 4096:
            return False
        row = json.loads(raw)
        parent, nonce = _offer(capability)
        now = time.time()
        return (exit_code == 0 and parent == os.getpid()
                and type(row) is dict and set(row) == {'pid','parent','nonce','bot','port','at'}
                and row['pid'] == pid and row['parent'] == parent and row['nonce'] == nonce
                and row['bot'] == bot and row['port'] == port
                and type(row['at']) in (int,float) and 0 <= now-row['at'] <= TTL)
    except (OSError, ValueError, TypeError):
        return False
    finally:
        path.unlink(missing_ok=True)


class PluginServiceRestart:
    def __init__(self, core, bot, read_plugins, *, allowed=False):
        self.core, self.bot, self.read_plugins = Path(core), bot, read_plugins
        self.lock = threading.Lock()
        self.requested = False
        self.capability = os.environ.get(ENV, '') if allowed and os.name == 'nt' else ''
        try:
            parent, _ = _offer(self.capability)
            if parent != os.getppid():
                self.capability = ''
        except ValueError:
            self.capability = ''

    def snapshot(self):
        return {'bot': self.bot, 'restart_supported': bool(self.capability),
                'restart_requested': self.requested,
                'restart_reason': '' if self.capability else
                '当前服务不支持页面内重启，请从 Core 启动器重新启动此 Bot。'}

    def request(self, name, action, port):
        from .plugin_scope_store import plugin_name
        plugin_name(name)
        if action not in ('enable', 'disable', 'remove', 'config'):
            raise ValueError('无效插件操作')
        with self.lock:
            parent, nonce = _offer(self.capability)
            if parent != os.getppid():
                raise ValueError('原启动器已变化，拒绝自动重启')
            row = next((p for p in self.read_plugins()['plugins'] if p['name'] == name), None)
            expected = 'disable' if action == 'remove' else action
            if not row or not row.get('restart_required') or row.get('restart_action') != expected:
                raise ValueError('插件待生效状态已变化，请刷新后重试')
            if self.requested:
                return
            target = _path(self.core, os.getpid())
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = {'pid': os.getpid(), 'parent': parent, 'nonce': nonce,
                       'bot': self.bot, 'port': port, 'at': time.time()}
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as f:
                temp = Path(f.name)
                f.write(json.dumps(payload).encode());f.flush();os.fsync(f.fileno())
            try:
                os.replace(temp, target)
            finally:
                temp.unlink(missing_ok=True)
            self.requested = True
