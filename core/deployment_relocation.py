"""Recover path-bound delivery records after a complete installation moves."""
import hashlib
from pathlib import Path

from bot_guarded_store import GuardedStore
from bot_registry import bot_id
from bot_stable_file import read_stable
from runtime_identity import MARKER, _object, bindings, safe_path


def previous_installation(core):
    root = core.parent
    hint = safe_path(root, root / 'runtime/previous-installation.txt')
    if hint.exists():
        raw, _ = read_stable(root, hint, 8192)
        previous = Path(raw.decode('utf-8-sig').strip())
    else:
        cfg = next((candidate for sub in ('.venv', '.venv-linux')
                    if (candidate := safe_path(root, core/sub/'pyvenv.cfg')).is_file()), None)
        if cfg is None:
            return None
        raw, _ = read_stable(root, cfg, 16384)
        command = next((s[10:] for s in raw.decode('utf-8').splitlines() if s.startswith('command = ')), '')
        _, marker, argument = command.partition(' -m venv ')
        venv = Path(argument.strip().strip('"'))
        if not marker or venv.name not in ('.venv', '.venv-linux') or venv.parent.name != 'core':
            return None
        previous = venv.parent.parent
    if (not previous.is_absolute() or '..' in previous.parts or previous == root
            or str(previous).startswith(('\\\\', '//'))):
        return None
    if (previous / 'core/launch.py').exists():
        raise ValueError('原安装目录仍有 Core，请先核对正在使用的部署；已保留旧登记。')
    return previous


def path_digest(path):
    return hashlib.sha256(str(path).encode('utf-8')).hexdigest()


def verify_bot(core, bot):
    base = core / 'data'
    bot = bot_id(bot)
    token = bindings(base).get(bot)
    if not token or _object(safe_path(core.parent, core.parent / 'bots' / bot / MARKER)) != {'name': bot, 'token': token}:
        raise ValueError('档案与此部署的 Bot 身份不一致，已保留旧登记。')
    return base if bot == _object(safe_path(core, base/'bot-runtime-owner.json'))['baseline'] else base/'bot-runtimes'/bot


def relocate_deliveries(core, bot, home, store, *, previous=None, linux_profile=False):
    from contextlib import ExitStack
    from telyincore.ingress_delivery import digest
    from telyincore.ingress_review import validate
    from telyincore.source_guard import lease

    core = Path(core).resolve()
    home = Path(home)
    events = Path(store.log.root).resolve()
    new = path_digest(events)
    changes = []
    with ExitStack() as locks:
        for name in ('ingress-delivery', 'local-observation-delivery'):
            root = home / name
            if not (root / 'pending.json').is_file():
                continue
            locks.enter_context(lease(root))
            disk = GuardedStore(root, root/'pending.json')
            row = disk.read()
            if row.get('binding') == new:
                continue
            event_root = core/'data' if linux_profile else verify_bot(core, bot)
            if linux_profile:
                marker = GuardedStore(core.parent, core.parent/'acceptance.json').read()
                if marker.get('mode') != 'linux-profile-acceptance' or marker.get('bot') != bot:
                    raise ValueError('Linux 收件记录归属不符')
            if home != safe_path(core.parent, core.parent/'bots'/bot_id(bot)/'memory') or events != event_root/'events':
                raise ValueError('收件记录与当前 Bot 的运行目录不一致，原记录已保留。')
            if previous is None:
                previous = previous_installation(core)
            if previous is None or path_digest(previous/events.relative_to(core.parent)) != row.get('binding'):
                raise ValueError('未能核实收件记录的原安装路径。如已整体搬家，请在 runtime/previous-installation.txt 填写原安装目录后重试。')
            validate(row, row['binding'])
            for entry in row['records'].values():
                if 'cursor' not in entry or entry['state'] == 'RESOLVED':
                    raise ValueError('旧收件记录仍有待核对的投递或处置凭据，保留原状态，请在原部署核对后再迁移。')
                event = store.get(entry['cursor'])
                if event is None or digest(event) != entry['digest']:
                    raise ValueError('待处理收件与迁入的事件历史不一致，已保留原记录。')
            changes.append((name, disk, row))
        # Both sources are preflighted before the first write. After interruption,
        # already migrated sources match the new binding and are skipped.
        for name, disk, row in changes:
            backup = safe_path(core.parent, core.parent/'runtime/relocation-backups'/bot/name)
            key = hashlib.sha256(disk.raw).hexdigest()
            archive = GuardedStore(core.parent, backup/(key+'.json'))
            if archive.raw is None:
                archive.write(row)
            elif archive.read() != row:
                raise ValueError('迁移备份内容不匹配。')
            disk.write(dict(row, binding=new))
    return [name for name, _, _ in changes]


def effective_asr_directory(configured, model_root):
    """Resolve a moved bundled voice model without modifying account-bound config bytes."""
    if not configured or model_root is None:
        return configured
    old = Path(configured)
    if not old.is_absolute() or old.exists():
        return configured
    root = Path(model_root)
    if old.parent.parts[-4:] != ('core', 'models', 'voice', 'asr') or root.name != 'models':
        return configured
    try:
        previous = previous_installation(root.parent)
        if previous is None or old.parent != previous/'core/models/voice/asr':
            return configured
        candidate = safe_path(root, root/'voice/asr'/old.name)
        if all(safe_path(root, candidate/name).is_file() for name in ('model.int8.onnx','tokens.txt')):
            return str(candidate)
    except (OSError, ValueError):
        # The voice panel can report a missing model while QQ text stays usable.
        pass
    return configured
