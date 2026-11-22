"""Explicit per-purpose proxy policy. Never changes system/global proxy settings."""
from pathlib import Path
import hashlib
import json
import os
import tempfile
from urllib.parse import urlsplit

PROVIDERS = ('openai', 'google', 'anthropic', 'xai')
HOSTS = {
    'api.openai.com': 'openai', 'auth.openai.com': 'openai',
    'generativelanguage.googleapis.com': 'google',
    'api.anthropic.com': 'anthropic', 'api.x.ai': 'xai',
}

class ProxySettingsError(ValueError):
    pass

class ProxySettingsConflict(ProxySettingsError):
    pass

def validate_proxy(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ProxySettingsError('代理地址格式无效')
    value = value.strip()
    if not value:
        return ''
    try:
        p = urlsplit(value)
        if (p.scheme not in ('http', 'https') or not p.hostname
                or p.port is None or not 0 < p.port <= 65535
                or p.username is not None or p.password is not None
                or p.path not in ('', '/') or p.query or p.fragment
                or any(c.isspace() or ord(c) < 32 for c in value)):
            raise ValueError()
    except ValueError:
        raise ProxySettingsError('请填写含端口的 HTTP(S) 代理地址，不支持账号密码、路径或查询参数') from None
    return value.rstrip('/')

def system_proxy_hint():
    """Display-only suggestion. It is not enabled until the user saves settings."""
    if os.name != 'nt':
        return ''
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
            if not winreg.QueryValueEx(key, 'ProxyEnable')[0]:
                return ''
            value = winreg.QueryValueEx(key, 'ProxyServer')[0]
        if '=' in value:
            value = dict(v.strip().split('=', 1) for v in value.split(';') if '=' in v).get('https', '')
        return validate_proxy(value if '://' in value else 'http://' + value)
    except (OSError, ValueError, TypeError):
        return ''

def _validated(value):
    if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
        raise ProxySettingsError('代理设置格式无效')
    providers = value.get('providers')
    if (not isinstance(providers, list) or any(not isinstance(p, str) or p not in PROVIDERS for p in providers)
            or len(set(providers)) != len(providers)):
        raise ProxySettingsError('代理供应商范围无效')
    url = validate_proxy(value.get('proxy_url'))
    extras = {key: value.get(key, False) for key in ('market_enabled', 'downloads_enabled')}
    if any(type(flag) is not bool for flag in extras.values()):
        raise ProxySettingsError('代理用途开关格式无效')
    if any(extras.values()) and not url:
        raise ProxySettingsError('启用代理时必须填写地址')
    if value['enabled'] and (not url or not providers):
        raise ProxySettingsError('启用代理时必须填写地址并选择至少一家供应商')
    return {'enabled': value['enabled'], 'proxy_url': url, 'providers': providers, **extras}

def _read(root):
    path = Path(root) / 'model-proxy.json'
    try:
        with path.open('rb') as f:
            raw = f.read(16385)
    except FileNotFoundError:
        return {'enabled': False, 'proxy_url': '', 'providers': list(PROVIDERS), 'market_enabled': False, 'downloads_enabled': False}, 'missing'
    if len(raw) > 16384:
        raise ProxySettingsError('模型代理配置过大，未使用该配置')
    try:
        value = _validated(json.loads(raw))
    except (ValueError, TypeError):
        raise ProxySettingsError('模型代理配置损坏，未静默退回直连，请检查设置') from None
    return value, hashlib.sha256(raw).hexdigest()

def read_settings(root):
    value, revision = _read(root)
    return {**value, 'revision': revision, 'system_proxy': system_proxy_hint()}

def save_settings(root, asked):
    from .engine import _config_lock
    value = _validated(asked)
    if not isinstance(asked.get('revision'), str):
        raise ProxySettingsConflict('请先重新读取代理设置再保存')
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    with _config_lock(root / 'model-proxy.lock'):
        _, revision = _read(root)
        if asked['revision'] != revision:
            raise ProxySettingsConflict('代理设置已在别处改变，请重新读取后再保存')
        raw = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        fd, tmp = tempfile.mkstemp(prefix='.model-proxy-', suffix='.tmp', dir=root)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(raw); f.flush(); os.fsync(f.fileno())
            os.replace(tmp, root / 'model-proxy.json')
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
    return read_settings(root)

def provider_for(url):
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or p.port not in (None, 443) or p.username is not None or p.password is not None:
            return None
        return HOSTS.get(p.hostname or '')
    except (ValueError, TypeError):
        return None

def proxy_for(root, url):
    provider = provider_for(url)
    if provider is None:
        return None  # Includes localhost, LAN, mainland and custom relay endpoints.
    value, _ = _read(root)
    return value['proxy_url'] if value['enabled'] and provider in value['providers'] else None

def auxiliary_proxy(root, purpose):
    key = {'market': 'market_enabled', 'downloads': 'downloads_enabled'}[purpose]
    value, _ = _read(root)
    return value['proxy_url'] if value[key] else None
