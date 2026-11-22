"""Fail-closed folder/runtime binding guard, not a rename or stable-ID service.

Only identity manifests are read. No engine config, conversations or accounts.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import uuid

MARKER = '.runtime-binding.json'
LEDGER = 'bot-runtime-bindings.json'


def safe_path(root: Path, path: Path) -> Path:
    """Reject links/junctions anywhere below the explicitly supplied root."""
    root = root.absolute()
    path = path.absolute()
    if not path.is_relative_to(root):
        raise ValueError('身份路径越界')
    current = root
    for part in ('', *path.relative_to(root).parts):
        if part:
            current = current / part
        try:
            reparse = bool(getattr(current.lstat(), 'st_file_attributes', 0) & 0x400)
        except FileNotFoundError:
            reparse = False
        if current.is_symlink() or reparse:
            raise ValueError('身份路径不能使用链接或目录联接')
        if not current.resolve().is_relative_to(root.resolve()):
            raise ValueError('身份路径越界')
    return path


def _object(path: Path) -> dict:
    try:
        # Identity callers, including product rename projection, must not
        # unboundedly decode manifests before their own snapshot checks.
        with path.open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError('identity manifest size limit')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate identity field')
                result[key] = value
            return result
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f'身份登记损坏或不可读：{path.name}；请核对备份，不要删除登记绕过保护') from exc


def bindings(base: Path) -> dict:
    from bot_registry import bot_id
    path = safe_path(base, base / LEDGER)
    if not path.exists():
        return {}
    data = _object(path)
    rows = data.get('bindings')
    if data.get('version') != 1 or not isinstance(rows, dict):
        raise ValueError('运行归属登记格式无效')
    folded = set()
    for name, token in rows.items():
        bot_id(name)
        if (name.casefold() in folded or not isinstance(token, str)
                or len(token) != 32 or any(c not in '0123456789abcdef' for c in token)):
            raise ValueError('运行归属登记格式无效')
        folded.add(name.casefold())
    if len(set(rows.values())) != len(rows):
        raise ValueError('运行归属登记重复')
    return rows


def reserved_names(base: Path) -> set[str]:
    from bot_registry import bot_id
    names = set(bindings(base))
    from bot_baseline_archive import archived_baseline
    archived = archived_baseline(base)
    for filename in ('bot-runtime-owner.json', 'active-bot.json'):
        path = safe_path(base, base / filename)
        if path.exists():
            data = _object(path)
            owner_name = bot_id(data.get('baseline'))
            if owner_name != archived: names.add(owner_name)
    for dirname in ('instances', 'bot-runtimes'):
        folder = safe_path(base, base / dirname)
        if folder.exists():
            for path in folder.iterdir():
                safe_path(base, path)
                if dirname == 'instances' and path.suffix != '.json':
                    continue
                name = path.stem if dirname == 'instances' else path.name
                names.add(bot_id(name))
    return names


def check_layout(base: Path, parent: Path, *, selected: str | None = None, discovery=False) -> None:
    """Check before auto-create, network reuse, or any runtime initialization."""
    from bot_registry import bot_id
    from bot_migration_gate import assert_no_migration
    assert_no_migration(base)
    _check_layout_fenced(base, parent, selected=selected, discovery=discovery)


def _check_layout_fenced(base: Path, parent: Path, *, selected=None, discovery=False) -> None:
    """Internal identity checks; caller must hold original live origin/fence."""
    from bot_registry import bot_id
    from bot_migration_gate import retired_names
    retired = {name.casefold() for name in retired_names(base)}
    live = reserved_names(base)
    if retired & {name.casefold() for name in live}:
        raise ValueError('退休名字与现有运行归属冲突；请核对迁移事务，不自动接管')
    safe_path(parent, parent)
    safe_path(base, base)
    owner = safe_path(base, base / 'bot-runtime-owner.json')
    legacy = safe_path(base, base / 'active-bot.json')
    if base.is_dir() and not owner.exists() and not legacy.exists():
        # Port allocation and the identity lock contain no Bot history.
        allowed = {'.bot-identity.lock', 'startup-port-allocation', 'bot-retired-names.json'}
        if any((base/name).exists() for name in ('.host-admission.lock','host-start-owners')):
            # Admission sidecars contain no Bot history. Validate their complete
            # bounded schema before treating them like identity/port locks.
            from bot_global_admission import validate_admission_metadata
            validate_admission_metadata(base)
            allowed.update(('.host-admission.lock','host-start-owners'))
        if any(p.name not in allowed for p in base.iterdir()):
            raise ValueError('已有运行资料但缺少归属标记；请先核对来源，不自动认领')
    registered = bindings(base)
    scoped = selected is not None or discovery
    issues = profile_issues(base, parent, live=live, registered=registered, retired=retired)
    if not scoped:
        for name, issue in issues.items():
            folder = parent / name
            if name in live or (folder / MARKER).exists() or folder.is_symlink():
                raise ValueError(issue)
    for name in sorted(live - registered.keys()):
        if name in issues:
            raise ValueError(issues[name])
    if selected in issues:
        raise ValueError(issues[selected])


def profile_issues(base: Path, parent: Path, *, live=None, registered=None, retired=None) -> dict[str, str]:
    """Identify folder problems independently, leaving shared ledgers strict."""
    from collections import Counter
    from bot_registry import BotRegistry, bot_id
    from bot_migration_gate import retired_names

    live = reserved_names(base) if live is None else live
    registered = bindings(base) if registered is None else registered
    retired = {name.casefold() for name in retired_names(base)} if retired is None else retired
    entries = sorted(parent.iterdir()) if parent.is_dir() else []
    names = Counter(path.name.casefold() for path in entries)
    local_names = {name.casefold() for name in live}
    local_tokens = set(registered.values())
    book = BotRegistry(parent, Path(), '', base)
    issues, foreign = {}, {}
    for name in sorted(live | {p.name for p in entries if not p.name.startswith('.') and
                               (p.is_dir() or p.is_symlink())}):
        folder = parent / name
        try:
            safe_path(parent, folder)
            bot_id(name)
            if not folder.is_dir():
                raise ValueError('旧身份的目录缺失；请放回原目录，或到「档案 → 处理档案异常」确认移出')
            if names[name.casefold()] != 1 or (name not in live and name.casefold() in local_names):
                raise ValueError('文件夹名称与本机档案冲突；请核对原目录名')
            if name.casefold() in retired:
                raise ValueError('该名称已归档或移出；要在本机恢复，请使用「恢复已归档的档案」')
            if not book.valid(folder):
                raise ValueError('档案不完整；请检查 persona 下的人设文件是否齐全、可读取')
            marker = safe_path(parent, folder / MARKER)
            if name in registered:
                if not marker.is_file() or _object(marker) != {'name': name, 'token': registered[name]}:
                    raise ValueError('档案与原运行归属不匹配；请放回原档案或核对备份')
            elif marker.exists():
                data = _object(marker)
                token = data.get('token')
                if (set(data) != {'name', 'token'} or data.get('name') != name
                        or name in live or not isinstance(token, str) or len(token) != 32
                        or any(c not in '0123456789abcdef' for c in token) or token in local_tokens):
                    raise ValueError('来源身份标记不匹配；请核对原目录名称和运行登记')
                foreign.setdefault(token, []).append(name)
        except (OSError, ValueError) as error:
            issues[name] = f'{name} · {error}'
    for candidates in foreign.values():
        if len(candidates) > 1:
            for name in candidates:
                issues[name] = f'{name} · 多个档案使用同一来源身份；请核对原档案'
    return issues


@contextmanager
def identity_lock(base: Path):
    """OS lock serializes new-Bot creation and first runtime binding."""
    safe_path(base, base)
    base.mkdir(parents=True, exist_ok=True)
    path = safe_path(base, base / '.bot-identity.lock')
    with path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError('其他进程正在登记 Bot 身份，请稍后重试') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _publish(path: Path, data: dict) -> None:
    """Atomic manifest write; preserve the previous ledger before replacement."""
    import shutil
    if path.exists():
        backup = path.with_name(path.name + '.bak')
        safe_path(path.parent, backup)
        shutil.copy2(path, backup)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush(); os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)



def _prepare_imported_profile(base, parent, folder, *, already_bound):
    """Retain imported host state separately before initializing local stores."""
    import hashlib
    from bot_guarded_store import GuardedStore
    from telyincore.ingress_review import validate
    from telyincore.source_guard import lease

    marker = safe_path(parent, folder / (MARKER + '.bak' if already_bound else MARKER))
    if not marker.is_file():
        return
    provenance = _object(marker)
    token = provenance.get('token')
    if (provenance.get('name') != folder.name or not isinstance(token, str)
            or len(token) != 32 or any(c not in '0123456789abcdef' for c in token)):
        return
    archive = safe_path(parent, folder / '.imported-runtime' / token)
    # Previously bound profiles qualify only with evidence of a prior import.
    if already_bound and (not archive.is_dir() or token == bindings(base)[folder.name]):
        return
    archive.mkdir(parents=True, exist_ok=True)
    # Plugin switches and configs may carry an old Bot identity, paths or
    # credentials. Keep them together for reference and seed local defaults.
    reviewed = safe_path(parent, archive / 'plugin-scope-prepared')
    source = safe_path(parent, folder / 'infinity/plugins')
    saved = safe_path(parent, archive / 'infinity/plugins')
    if not reviewed.is_file():
        if source.exists():
            if saved.exists():
                raise ValueError('迁入档案同时有两份插件配置；请核对备份')
            saved.parent.mkdir(parents=True, exist_ok=True)
            source.rename(saved)
        _publish(reviewed, {'version': 1})
    owner = safe_path(base, base / 'bot-runtime-owner.json')
    legacy = safe_path(base, base / 'active-bot.json')
    baseline = _object(owner if owner.exists() else legacy)['baseline'] if owner.exists() or legacy.exists() else folder.name
    runtime = base if folder.name == baseline else base / 'bot-runtimes' / folder.name
    expected = hashlib.sha256(str((runtime / 'events').resolve()).encode()).hexdigest()
    for delivery in ('ingress-delivery', 'local-observation-delivery'):
        journal = safe_path(parent, folder / 'memory' / delivery)
        if not journal.is_dir():
            continue
        with lease(journal):
            state = GuardedStore(journal, journal / 'pending.json').read()
            if not state or state.get('binding') == expected:
                continue
            binding = state.get('binding')
            if not isinstance(binding, str) or len(binding) != 64 or any(c not in '0123456789abcdef' for c in binding):
                raise ValueError('迁入档案的消息投递记录损坏；已保留原记录')
            validate(state, binding)
            target = safe_path(parent, archive / 'memory' / delivery)
            if target.exists():
                raise ValueError('迁入档案已有一份旧投递记录；请核对档案备份')
            target.parent.mkdir(parents=True, exist_ok=True)
            journal.rename(target)

def bind_runtime(base: Path, parent: Path, name: str, *, scoped=False) -> None:
    """Must run under identity_lock; a partial write blocks subsequent launch."""
    from bot_migration_gate import assert_identity_allowed
    assert_identity_allowed(base, name)
    check_layout(base, parent, selected=name if scoped else None)
    folder = safe_path(parent, parent / name)
    if not folder.is_dir():
        raise ValueError('Bot 目录不存在；请用新建入口建立档案')
    matches = [p.name for p in parent.iterdir() if p.name.casefold() == name.casefold()]
    if matches != [name]:
        raise ValueError('Bot 文件夹大小写与请求身份不一致；请使用原名')
    from bot_registry import BotRegistry
    if not BotRegistry(parent, Path(), name).valid(folder):
        raise ValueError('Bot 档案不完整；不建立运行归属')
    rows = bindings(base)
    _prepare_imported_profile(base, parent, folder, already_bound=name in rows)
    if name in rows:
        return
    # Legacy valid directories may be adopted in-place, without moving data.
    # This cannot prove whether a name was reused before this guard existed.
    token = uuid.uuid4().hex
    rows[name] = token
    _publish(safe_path(base, base / LEDGER), {'version': 1, 'bindings': rows})
    _publish(safe_path(parent, folder / MARKER), {'name': name, 'token': token})
