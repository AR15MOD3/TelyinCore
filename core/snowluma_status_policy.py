"""Configure managed OneBot listeners before SnowLuma reads its account settings."""
import os
from pathlib import Path
import re
import secrets
from uuid import uuid4

from runtime_identity import safe_path
from snowluma_instance import _bytes, _encode, _json, _write_new


def _default_listeners():
    # A status-only global file would suppress SL's first-install listener creation.
    def listener(name, port):
        return dict(name=name, host='127.0.0.1', port=port, path='/',
                    accessToken=secrets.token_urlsafe(32), messageFormat='array', reportSelfMessage=False)
    return dict(httpServers=[listener('http-default', 3000)], httpClients=[],
                wsServers=[dict(listener('ws-default', 3001), role='Universal')], wsClients=[])


def protect_status_command(state):
    """Caller owns the stopped runtime. Preserve its per-account adapter configuration."""
    directory = safe_path(Path(state), Path(state) / 'config')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    default = safe_path(state, directory / 'onebot.json')
    fresh_default = not default.exists()
    # Install account overrides first, so a retry never accidentally merges new defaults.
    accounts = sorted(p for p in directory.glob('onebot_*.json')
                      if re.fullmatch(r'onebot_[0-9]+\.json', p.name))
    for path in [*accounts, default]:
        raw = _bytes(safe_path(state, path), limit=1024*1024)
        config = _json(raw) if raw is not None else {'networks': _default_listeners()}
        command = config.setdefault('statusCommand', {})
        if not isinstance(command, dict):
            raise ValueError('SnowLuma 状态指令配置无效')
        isolate = fresh_default and path != default and config.get('mode') != 'snapshot'
        if command.get('enabled') is False and not isolate:
            continue
        if isolate:
            config['mode'] = 'snapshot'
        command['enabled'] = False
        tmp = path.with_name('.status-policy-' + uuid4().hex)
        try:
            _write_new(tmp, _encode(config))
            if _bytes(path, limit=1024*1024) != raw:
                raise ValueError('SnowLuma 配置已变化，请重新启动')
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
