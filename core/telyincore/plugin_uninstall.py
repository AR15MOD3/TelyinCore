"""Owner-requested shared uninstall. Drain all advertised runtimes; retain all bytes.

Package leases also protect starting/unadvertised instances. A failed stop or an
unreachable instance leaves the installed package intact. No process is killed.
"""
import json
import os
from pathlib import Path
import shutil
import time
import urllib.request
import uuid
from .plugin_package_lock import PackageLock
from .plugin_scope_store import PluginScopeStore, plugin_name, identifier

class RestartNeeded(ValueError):
    def __init__(self, message, bot, local):
        super().__init__(message)
        self.bot, self.local = bot, local


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def _request(port, path, body=None):
    base = f'http://127.0.0.1:{port}'
    raw = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=raw,
        headers={'Origin': base, 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=15) as response:
        value = response.read(1024*1024 + 1)
    if len(value) > 1024*1024: raise ValueError('档案回复超出大小限制')
    got = json.loads(value)
    if not isinstance(got, dict): raise ValueError('档案回复格式无效')
    return got


def _peers(core, active):
    from runtime_identity import safe_path
    home = safe_path(core, core/'data/instances')
    peers = []
    for path in sorted(home.glob('*.json')) if home.exists() else ():
        bot = identifier(path.stem)
        if bot == active: continue
        path = safe_path(core, path)
        if path.stat().st_size > 4096: raise ValueError('档案实例登记过大')
        raw = path.read_bytes(); row = json.loads(raw)
        if (set(row) != {'pid','port'} or type(row['pid']) is not int
                or row['pid'] <= 0 or type(row['port']) is not int or not 0 < row['port'] < 65536):
            raise ValueError('档案实例登记无效')
        def verify(path=path, raw=raw, bot=bot, row=row):
            if path.read_bytes() != raw: raise ValueError('档案实例已变化，请重试')
            live = _request(row['port'], '/infinity/bots')
            if (live.get('active') != bot or live.get('pid') != row['pid']
                    or live.get('coreRoot') != str(core)):
                raise ValueError('档案端点身份不符，不发送停用请求')
        verify()
        status = _request(row['port'], '/plugins')
        if status.get('shared_uninstall') != 1:
            raise ValueError('其他档案仍使用旧版插件管理，请更新或退出后重试')
        peers.append((bot, row['port'], verify, status))
    return peers


def uninstall_shared(core, name, *, scope, stop_local, roster):
    """The host supplies its authenticated scope, local stop and Bot registry."""
    from runtime_identity import safe_path
    from . import plugins
    plugins._require_unconfined_lifecycle()
    plugins._require_open_root(core)
    core = Path(core).resolve()
    name = plugin_name(name)
    if type(scope) is not PluginScopeStore or scope.bots.resolve() != core.parent/'bots':
        raise ValueError('卸载需要当前档案的宿主上下文')
    stopped = []
    try:
        with PackageLock(core, 'global-uninstall', exclusive=True, operation=True):
            folder, manifest = plugins._plugin_folder(core, name)
            if folder is None or manifest is None: raise ValueError('插件未安装，请刷新列表')
            if manifest.get('name', name) != name: raise ValueError('插件目录与声明不一致')
            folder = safe_path(core, folder)
            if folder.parent != plugins.the_place(core).resolve(): raise ValueError('插件目录不在安装区')
            # Validate every target before stopping the first instance.
            scopes = []
            for bot in roster()['bots']:
                item = PluginScopeStore(scope.bots, identifier(bot['id']))
                item.read_state(); scopes.append(item)
            peers = _peers(core, scope.bot)
            for bot, port, verify, status in peers:
                verify()
                if not any(row.get('name') == name and row.get('installed') is not False
                           for row in status.get('plugins', [])):
                    raise ValueError('其他档案的插件清单与共享安装区不一致')
                result = _request(port, '/plugins/switch', {'name': name, 'on': False})
                if result.get('restart_required') or result.get('applied') is False:
                    raise RestartNeeded('档案「' + bot + '」需要先完成重启；安装包已保留', bot, False)
                if result.get('ok') is not True:
                    raise ValueError('档案「' + bot + '」未能停用插件，安装包已保留')
                stopped.append(bot)
            result = stop_local(name, False)
            if result.get('restart_required') or result.get('applied') is False:
                raise RestartNeeded('当前档案需要重启后完成停用；安装包已保留', scope.bot, True)
            if result.get('ok') is not True:
                raise ValueError('当前档案未能停用插件，安装包已保留')
            stopped.append(scope.bot)
            with PackageLock(core, name, exclusive=True):
                # No startup can acquire the package between this check and rename.
                for item in scopes:
                    item.update({name: False})
                destination = safe_path(core, core/'data/plugin-retained'/name/
                    (str(time.time_ns()) + '-' + uuid.uuid4().hex[:8]))
                destination.mkdir(parents=True, mode=0o700)
                receipt = {'schema':1, 'plugin':name, 'reason':'uninstall',
                    'retains':'complete original package, configuration and cache', 'bots':stopped}
                (destination/'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False), encoding='utf-8')
                plugins._save_scope_switch(core, None, name, False)
                # Retain even plugin-private files unknown to Core. Never rmtree user data.
                os.replace(folder, destination/'package')
            return {**plugins.installed(core, scope=scope), 'ok':True, 'name':name,
                'said':'插件已从所有档案卸载，配置和缓存已保留。', 'stopped_bots':stopped,
                'retained':True}
    except (OSError, ValueError, KeyError, TimeoutError) as error:
        # Avoid upstream bodies/credentials; remote errors only expose a fixed type.
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        return {**plugins.installed(core, scope=scope), 'ok':False, 'name':name,
            'said':'卸载未完成：' + detail + '。已停用的档案保持停用；配置和缓存未删除。',
            'stopped_bots':stopped,
            **({'restart_required': True, 'restart_local': error.local,
                'restart_bots': [error.bot]} if isinstance(error, RestartNeeded) else {})}


def publish_package(core, name, stage, destination):
    """Publish under the same lease. Reinstall restores retained data, never old code."""
    from runtime_identity import safe_path
    core = Path(core).resolve()
    with PackageLock(core, name, exclusive=True):
        if destination.exists(): raise ValueError('插件已安装，请先卸载')
        retained = safe_path(core, core/'data/plugin-retained'/plugin_name(name))
        archives = sorted(retained.iterdir(), reverse=True) if retained.exists() else []
        for archive in archives:
            old = safe_path(core, archive/'package')
            if not old.is_dir(): continue
            for path in old.rglob('*'):
                path = safe_path(core, path)
                if not path.is_file(): continue
                rel = path.relative_to(old)
                if ('node_modules' in rel.parts or '__pycache__' in rel.parts
                        or path.suffix.lower() in {'.py','.pyc','.pyd','.js','.mjs','.cjs','.ts','.dll','.exe','.so','.sh','.bat','.cmd','.ps1','.vbs','.wasm'}
                        or path.name in {'package.json','package-lock.json','pnpm-lock.yaml'}): continue
                target = stage/rel
                if target.exists() and path.name not in {'config.json','settings.json','.env'}: continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            break
        stage.rename(destination)
