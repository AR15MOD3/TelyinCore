"""Installed-plugin configuration, independent of plugin names or Python imports."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid
from pathlib import Path
from threading import RLock
from typing import Any, Callable

SECRET = re.compile(r'(?i)(token|password|secret|api.?key|authorization|cookie|credential)')


def merge(base: dict, overlay: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        result[key] = merge(result[key], value) if (
            isinstance(value, dict) and isinstance(result.get(key), dict)) else copy.deepcopy(value)
    return result


def kind(value: Any) -> str:
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, int):
        return 'integer'
    if isinstance(value, float):
        return 'number'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, list):
        return 'array'
    return 'object' if isinstance(value, dict) else 'null'


def contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        return any(SECRET.search(str(k)) or contains_secret(v) for k, v in value.items())
    if isinstance(value, list):
        return any(contains_secret(v) for v in value)
    return False


class PluginConfig:
    def __init__(self, root: Path, listed: Callable[[], dict], reload: Callable[[str], None], *, scope=None):
        self.root, self.listed, self.reload = root.resolve(), listed, reload
        self.lock = RLock()
        self.scope = scope

    def _folder(self, name: str) -> tuple[Path, dict]:
        rows = [row for row in self.listed().get('plugins', [])
                if row.get('name') == name and row.get('installed') is not False]
        if len(rows) != 1:
            raise ValueError('插件未安装或名称不唯一')
        folder = Path(rows[0]['where']).resolve()
        if not folder.is_relative_to(self.root / 'extensions') or not folder.is_dir():
            raise ValueError('配置只能来自已安装的插件目录')
        return folder, rows[0]

    @staticmethod
    def _path(folder: Path, name: str) -> Path:
        if not isinstance(name, str) or not name or Path(name).is_absolute():
            raise ValueError('配置声明路径无效')
        path = (folder / name).resolve()
        if not path.is_relative_to(folder):
            raise ValueError('配置声明不能越出插件目录')
        return path

    @staticmethod
    def _json(path: Path) -> dict:
        if not path.exists():
            return {}
        if path.stat().st_size > 512000:
            raise ValueError('配置文件过大')
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError('配置须为 JSON 对象')
        return value

    def _load(self, name: str) -> tuple:
        folder, row = self._folder(name)
        manifest = self._json(self._path(folder, 'package.json'))
        declared = manifest.get('telyincore', {})
        if not isinstance(declared, dict):
            declared = {}
        schema = declared.get('configSchema', {})
        if isinstance(schema, str):
            schema = self._json(self._path(folder, schema))
        if not isinstance(schema, dict):
            raise ValueError('configSchema 须为对象或本地 JSON 路径')
        path = self.scope.config_path(name) if self.scope is not None else self._path(folder, 'config.json')
        defaults = self._json(self._path(folder, 'config.example.json'))
        values = merge(defaults, (self.scope.read_config(name) or {}) if self.scope is not None else self._json(path))
        fields: list[dict] = []

        def walk(data: dict, spec: dict, prefix: list[str]) -> None:
            if len(prefix) > 8 or len(fields) > 256:
                raise ValueError('配置层级或字段过多')
            props = spec.get('properties', {})
            if not isinstance(props, dict):
                raise ValueError('schema properties 须为对象')
            for key in dict.fromkeys([*data, *props]):
                if not isinstance(key, str):
                    raise ValueError('配置键须为字符串')
                rule = props.get(key, {})
                if not isinstance(rule, dict):
                    raise ValueError('字段说明须为对象')
                value = data.get(key, rule.get('default'))
                if key not in data and 'default' in rule:
                    data[key] = copy.deepcopy(value)
                declared_type = rule.get('type') or kind(value)
                if declared_type == 'object' and isinstance(value, dict) and value:
                    walk(value, rule, [*prefix, key])
                    continue
                if declared_type == 'object' and rule.get('properties'):
                    walk(value if isinstance(value, dict) else {}, rule, [*prefix, key])
                    continue
                secret = (any(SECRET.search(p) for p in [*prefix, key])
                          or contains_secret(value) or rule.get('format') == 'password'
                          or rule.get('writeOnly') is True)
                fields.append({'path': [*prefix, key], 'label': str(rule.get('title') or key),
                               'description': str(rule.get('description') or ''),
                               'type': declared_type, 'secret': bool(secret),
                               'hasValue': value is not None and value != '',
                               'value': None if secret else value, 'rule': rule})

        walk(values, schema, [])
        raw = path.read_bytes() if path.exists() else None
        revision = hashlib.sha256((raw or b'') + json.dumps(
            [defaults, schema], sort_keys=True).encode()).hexdigest()
        page = ''
        if declared.get('configPage'):
            page_file = self._path(folder, declared['configPage'])
            if page_file.suffix.lower() != '.html' or page_file.stat().st_size > 256000:
                raise ValueError('configPage 须为不超过256KB的单文件HTML')
            page = page_file.read_text(encoding='utf-8-sig')
        if page:
            stamp = json.dumps(declared, sort_keys=True).encode() + page.encode()
            if declared.get('pageBackend'):
                from plugin_page_admin import PluginPageAdmin
                backend = PluginPageAdmin(self, self.scope, None, None, None).declaration(name)[2]
                stamp += backend.read_bytes()
            revision = hashlib.sha256(revision.encode() + stamp).hexdigest()
        return folder, row, path, values, fields, raw, revision, page

    def read(self, name: str) -> dict:
        with self.lock:
            folder, row, _, _, fields, _, revision, page = self._load(name)
            public = [{k: v for k, v in field.items() if k != 'rule'} for field in fields]
            for result, field in zip(public, fields):
                # Never expose a secret's default/enum through its schema either.
                if not field['secret']:
                    for key in ('minimum', 'maximum', 'enum', 'readOnly'):
                        if key in field['rule']:
                            result[key] = field['rule'][key]
            actions = {}
            declared = self._json(self._path(folder, 'package.json')).get('telyincore', {})
            if page and isinstance(declared, dict) and declared.get('pageBackend'):
                from plugin_page_admin import PluginPageAdmin
                actions = PluginPageAdmin(self, self.scope, None, None, None).describe(name)
            return {'name': name, 'displayName': row.get('displayName') or name,
                    'fields': public, 'revision': revision, 'page': page, 'pageActions': actions}

    @staticmethod
    def _check(value: Any, field: dict) -> None:
        expected = field['type']
        actual = kind(value)
        if actual != expected and not (expected == 'number' and actual == 'integer'):
            raise ValueError('字段类型不符：' + '.'.join(field['path']))
        rule = field['rule']
        if rule.get('readOnly'):
            raise ValueError('该字段只读')
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            if not math.isfinite(value):
                raise ValueError('数值须有限')
            if value < rule.get('minimum', -math.inf) or value > rule.get('maximum', math.inf):
                raise ValueError('数值超出声明范围')
        if 'enum' in rule and value not in rule['enum']:
            raise ValueError('选项不在声明范围内')

    def _save_receipt(self, name, row, receipt):
        pending = isinstance(receipt, dict) and receipt.get('restart_required') is True
        message = ('配置已保存，重启当前 Bot 的 Core 服务后生效。' if pending else
                   '配置已保存并重载插件。' if row.get('enabled') or row.get('loaded') else
                   '配置已保存；插件保持停用。')
        return {'ok': True, 'message': message, 'restart_required': pending,
                'config': self.read(name)}

    def save(self, name: str, request: dict) -> dict:
        with self.lock:
            receipt = None
            folder, row, path, values, fields, raw, revision, _ = self._load(name)
            if request.get('revision') != revision:
                raise ValueError('配置已被其他页面修改，请重新加载后再保存')
            changes = request.get('changes')
            if not isinstance(changes, list) or len(changes) > 256:
                raise ValueError('changes 须为字段修改数组')
            allowed = {tuple(field['path']): field for field in fields}
            for change in changes:
                keys = change.get('path') if isinstance(change, dict) else None
                if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
                    raise ValueError('字段路径无效')
                field = allowed.get(tuple(keys))
                if field is None:
                    raise ValueError('未知配置字段')
                value = change.get('value')
                self._check(value, field)
                cursor = values
                for key in keys[:-1]:
                    if not isinstance(cursor.get(key), dict):
                        cursor[key] = {}
                    cursor = cursor[key]
                cursor[keys[-1]] = value
            payload = (json.dumps(values, ensure_ascii=False, indent=2, allow_nan=False)
                       + '\n').encode('utf-8')
            if len(payload) > 512000:
                raise ValueError('保存的配置过大')
            if self.scope is not None:
                expected=hashlib.sha256(raw).hexdigest() if raw is not None else ''
                written=self.scope.save_config(name,values,expected_sha256=expected)
                try:
                    if row.get('enabled') or row.get('loaded'):
                        receipt = self.reload(name)
                except Exception:
                    try:
                        self.scope.restore_config(name,raw,expected_sha256=written)
                    except Exception:
                        raise ValueError('重载失败；配置已有后续修改或还原失败，拒绝覆盖，请核对当前配置') from None
                    try:
                        self.reload(name)
                    except Exception:
                        raise ValueError('重载失败，已还原配置；旧实例恢复未确认，请检查插件日志') from None
                    raise ValueError('插件拒绝新配置，已还原原配置及实例') from None
                return self._save_receipt(name, row, receipt)
            if raw is not None:
                backup = self._path(folder, '.config-backups')
                backup.mkdir(exist_ok=True)
                self._path(folder, '.config-backups/' + uuid.uuid4().hex + '.json').write_bytes(raw)
            temp = self._path(folder, 'config-' + uuid.uuid4().hex + '.tmp')
            temp.write_bytes(payload)
            temp.replace(path)
            try:
                if row.get('enabled') or row.get('loaded'):
                    receipt = self.reload(name)
            except Exception:
                if raw is None:
                    path.unlink(missing_ok=True)
                else:
                    temp.write_bytes(raw)
                    temp.replace(path)
                try:
                    self.reload(name)
                except Exception:
                    raise ValueError('重载失败，已还原配置；旧实例恢复未确认，请检查插件日志') from None
                raise ValueError('插件拒绝新配置，已还原原配置及实例') from None
            return self._save_receipt(name, row, receipt)
