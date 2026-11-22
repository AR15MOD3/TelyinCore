"""Local bot directory discovery, creation and display metadata."""
from __future__ import annotations

import base64
from collections import Counter
import json
import re
import time
from pathlib import Path
from threading import Lock

from bot_bootstrap import ensure_bot


def bot_id(value: object) -> str:
    if (not isinstance(value, str) or not value or len(value) > 64
            or value != value.strip() or value.endswith('.')
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
            or value in ('.', '..')
            or value.casefold() == '.deleted' or value.casefold().startswith('.bot-init-')
            or value.split('.')[0].upper() in {
                'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                *(f'LPT{i}' for i in range(1, 10))}):
        raise ValueError('文件夹名无效：请使用1–64字，不含路径或系统保留字符')
    return value


class BotRegistry:
    def __init__(self, parent: Path, template: Path, active: str,
                 runtime_base: Path | None = None) -> None:
        from runtime_identity import safe_path
        safe_path(parent.absolute(), parent.absolute())
        self.parent, self.template, self.active = parent.resolve(), template, active
        self.lock = Lock()
        self.runtime_base = runtime_base if runtime_base is not None else self.parent.parent / 'core' / 'data'

    def folder(self, name: str) -> Path:
        target = self.parent / bot_id(name)
        from runtime_identity import safe_path
        return safe_path(self.parent, target)

    def valid(self, folder: Path) -> bool:
        from runtime_identity import safe_path
        try:
            bot_id(folder.name)
            safe_path(self.parent, folder)
            if folder.name.startswith('.') or not folder.is_dir():
                return False
            modern = [safe_path(self.parent, folder / "persona" / name)
                      for name in ("宪法.md", "角色设定.md")]
            files = modern if any(path.exists() for path in modern) else [
                safe_path(self.parent, folder / 'persona' / '你定的' / (name + '.md'))
                for name in ('存在方式', '人设', '形象', '世界', '关系', '破甲')]
            for path in files:
                if not path.is_file():
                    return False
                path.read_text(encoding='utf-8')
            return True
        except (OSError, ValueError):
            return False

    def read(self) -> dict:
        from bot_migration_gate import retired_names
        retired = {name.casefold() for name in retired_names(self.runtime_base)}
        rows = []
        entries = sorted(self.parent.iterdir()) if self.parent.is_dir() else []
        names = Counter(path.name.casefold() for path in entries)
        for folder in entries:
            key = folder.name.casefold()
            if key in retired or names[key] != 1:
                continue
            if not self.valid(folder):
                continue
            try:
                from runtime_identity import safe_path
                info = json.loads(safe_path(self.parent, folder / 'bot.json').read_text(encoding='utf-8'))
                display = str(info.get('displayName') or '')
            except (OSError, ValueError, AttributeError):
                display = ''
            rows.append({'id': folder.name, 'displayName': display,
                         'active': folder.name == self.active})
        return {'active': self.active, 'bots': rows}

    def rename(self, value: object) -> dict:
        if (not isinstance(value, str) or not value.strip() or len(value.strip()) > 80
                or any(ord(c) < 32 for c in value)):
            raise ValueError('显示名需要1–80字，不能包含控制字符')
        from runtime_identity import identity_lock, safe_path
        from bot_migration_gate import assert_identity_allowed
        with self.lock, identity_lock(self.runtime_base):
            assert_identity_allowed(self.runtime_base, self.active)
            folder = self.folder(self.active)
            target = safe_path(self.parent, folder / 'bot.json')
            info = {}
            if target.exists():
                info = json.loads(target.read_text(encoding='utf-8'))
                if not isinstance(info, dict):
                    raise ValueError('Bot 元数据格式无效')
                import shutil
                shutil.copy2(target, safe_path(self.parent, target.with_suffix('.json.bak')))
            info['displayName'] = value.strip()
            temp = safe_path(self.parent, target.with_suffix('.json.tmp'))
            temp.write_text(json.dumps(info, ensure_ascii=False), encoding='utf-8')
            temp.replace(target)
        return {'ok': True, 'displayName': value.strip()}

    def create(self, values: dict) -> dict:
        name = bot_id(values.get('id'))
        avatar = values.get('avatar', '')
        raw = b''
        if avatar:
            try:
                raw = base64.b64decode(avatar, validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError('头像格式无效') from exc
            if (len(raw) > 512000 or raw[:8] != b'\x89PNG\r\n\x1a\n'
                    or raw[12:16] != b'IHDR' or len(raw) < 24
                    or int.from_bytes(raw[16:20], 'big') != 256
                    or int.from_bytes(raw[20:24], 'big') != 256):
                raise ValueError('头像须为裁剪后的256×256 PNG')
        target = self.folder(name)
        from runtime_identity import identity_lock, reserved_names, check_layout
        from bot_migration_gate import assert_identity_allowed
        with self.lock, identity_lock(self.runtime_base):
            assert_identity_allowed(self.runtime_base, name)
            check_layout(self.runtime_base, self.parent, selected=name)
            reserved = {n.casefold() for n in reserved_names(self.runtime_base)}
            existing = {p.name.casefold() for p in self.parent.iterdir()} if self.parent.is_dir() else set()
            if name.casefold() in reserved:
                raise ValueError('此名字已有运行数据或身份登记；禁止复用，请先处理迁移')
            if name.casefold() in existing or target.exists() or not ensure_bot(self.parent, name, self.template):
                raise ValueError('同名 Bot 已存在，未覆盖')
            (target / 'bot.json').write_text(
                json.dumps({'displayName': name, 'createdAtNs': time.time_ns()},
                           ensure_ascii=False), encoding='utf-8')
            if raw:
                (target / 'avatar.png').write_bytes(raw)
        return {'ok': True, 'id': name}

    def delete(self, values: dict) -> dict:
        """Remove an inactive Bot from discovery, retaining a recoverable copy."""
        import uuid
        name = bot_id(values.get('id'))
        from runtime_identity import identity_lock
        from bot_migration_gate import assert_identity_allowed
        with self.lock, identity_lock(self.runtime_base):
            assert_identity_allowed(self.runtime_base, name)
            if name.casefold() == self.active.casefold():
                raise ValueError('不能删除当前运行的 Bot，请先切换到其他 Bot')
            if values.get('confirm') != name:
                raise ValueError('请再次输入完整文件夹名确认删除')
            from runtime_identity import reserved_names
            if name.casefold() in {n.casefold() for n in reserved_names(self.runtime_base)}:
                raise ValueError('此 Bot 已有运行归属；请先退出全部 Bot，再使用“管理 Bot”入口归档或安全删除')
            folder = self.folder(name)
            if not self.valid(folder):
                raise ValueError('Bot 目录不符合规范')
            archive = self.parent / '.deleted'
            from runtime_identity import safe_path
            safe_path(self.parent, archive)
            archive.mkdir(exist_ok=True)
            folder.rename(archive / (name + '-' + uuid.uuid4().hex))
        return {'ok': True, 'id': name, 'recoverable': True}
