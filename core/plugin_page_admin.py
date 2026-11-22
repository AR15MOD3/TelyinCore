"""Generic operator-only plugin page backend; never an agent tool or lifecycle start.

Installed Python plugins are trusted code. This interface scopes host-provided
resources, not a sandbox for malicious code. Explicit UI requests only.
"""
import hashlib
import json
from pathlib import Path
import re


class PluginPageAdmin:
    def __init__(self, config, scope, lifecycle_transaction, require_runtime, private_file):
        self.config, self.scope = config, scope
        self.transaction, self.require_runtime = lifecycle_transaction, require_runtime
        self.private_file = private_file

    def declaration(self, name):
        folder, row = self.config._folder(name)
        original = Path(row['where']).absolute()
        if original.resolve() != original or any(p.is_symlink() for p in (original, *original.parents)):
            raise ValueError('插件页面包路径不允许重定向')
        declared = self.config._json(self.config._path(folder, 'package.json')).get('telyincore', {})
        backend = declared.get('pageBackend') if isinstance(declared, dict) else None
        actions = declared.get('pageActions') if isinstance(declared, dict) else None
        if (declared.get('pageBackendApi') != 1 or not isinstance(backend, str) or not isinstance(actions, dict)
                or not 1 <= len(actions) <= 16):
            raise ValueError('插件未声明正式页面操作接口')
        # Exact file, no redirected package/file and no path traversal.
        path = folder / backend
        if (Path(backend).is_absolute() or '..' in Path(backend).parts
                or path.suffix != '.py' or path.resolve() != path.absolute()
                or not path.is_file() or path.stat().st_size > 256000):
            raise ValueError('插件页面后端路径无效')
        result = {}
        for action, rule in actions.items():
            if (not isinstance(action, str) or re.fullmatch(r'[a-z][a-z0-9_]{0,47}', action) is None
                    or not isinstance(rule, dict) or set(rule) != {'mutates', 'label'}
                    or type(rule['mutates']) is not bool or not isinstance(rule['label'], str)
                    or not 1 <= len(rule['label']) <= 100):
                raise ValueError('插件页面操作声明无效')
            result[action] = dict(rule)
        return folder, row, path, result

    def describe(self, name):
        return self.declaration(name)[3]

    def call(self, name, request):
        # Before import, callbacks, path or disk reads; same sticky host guard.
        self.require_runtime()
        if self.scope is None:
            raise ValueError('插件页面操作需要正式 Bot 作用域')
        if (type(request) is not dict or set(request) != {'action', 'payload', 'revision', 'confirmed'}
                or type(request['payload']) is not dict or type(request['confirmed']) is not bool
                or len(json.dumps(request, ensure_ascii=False).encode()) > 16384):
            raise ValueError('插件页面操作参数无效')
        with self.transaction(), self.config.lock:
            folder, row, path, actions = self.declaration(name)
            action = request['action']
            rule = actions.get(action) if isinstance(action, str) else None
            if rule is None:
                raise ValueError('未声明的插件页面操作')
            if rule['mutates'] and request['confirmed'] is not True:
                raise ValueError('页面写操作需要宿主确认')
            raw = path.read_bytes()
            if request['revision'] != self.config.read(name)['revision']:
                raise ValueError('插件配置已变化，请重新读取页面')
            if raw != path.read_bytes() or self.declaration(name)[3] != actions:
                raise ValueError('插件页面后端发生并发变化，未执行操作')
            config_path = self.scope.config_path(name)
            context = dict(bot_id=self.scope.bot, config_path=str(config_path),
                data_dir=str(config_path.parent/'runtime'), plugin=dict(row),
                open_private_new=self.private_file)
            namespace = {'__name__': 'plugin_operator_'+hashlib.sha256(raw).hexdigest(),
                         '__file__': str(path)}
            # Execute verified bytes, no package sys.path or persistent module cache.
            exec(compile(raw, str(path), 'exec'), namespace)
            handler = namespace.get('handle')
            if not callable(handler):
                raise ValueError('插件页面后端缺少 handle')
            result = handler(context, action, request['payload'])
            if type(result) is not dict or len(json.dumps(result, ensure_ascii=False).encode()) > 256000:
                raise ValueError('插件页面回执无效或过大')
            return result
