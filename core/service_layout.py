"""Fixed service layout with explicit legacy compatibility, never discovery."""
from pathlib import Path

from runtime_identity import safe_path


def snowluma_directory(core: Path) -> Path:
    """Prefer services/SnowLuma; a sole old installation remains supported."""
    base = Path(core).absolute().parent
    current = safe_path(base, base / 'services' / 'SnowLuma')
    legacy = safe_path(base, base / 'SnowLuma')
    if current.exists() and legacy.exists():
        raise ValueError('ambiguous SnowLuma installation; select one fixed layout')
    selected = legacy if legacy.exists() else current
    if selected.exists() and not selected.is_dir():
        raise ValueError('SnowLuma installation directory required')
    return selected


def cosyvoice_directory(core: Path) -> Path:
    """Prefer the runtime next to Core and preserve the old Windows installation."""
    import os
    base = Path(core).absolute().parent
    current = safe_path(base, base / 'services/CosyVoice3')
    if os.name == 'nt' and not current.exists():
        legacy = Path('D:/AI/TTS/CosyVoice3')
        if legacy.is_dir():
            return legacy
    return current


def node_executable(core: Path) -> str | None:
    """Shared portable Node, with system Node compatibility for older installs."""
    import os, shutil
    if os.name == 'nt':
        base = Path(core).absolute().parent
        path = safe_path(base, base / 'services/node/node.exe')
        if path.is_file(): return str(path)
    return shutil.which('node.exe' if os.name == 'nt' else 'node')


def qq_recovery_file(core: Path, bot: str, *, baseline: bool) -> Path:
    """New QQ credentials follow every Bot; preserve a sole legacy owner's store."""
    from bot_registry import bot_id
    core = Path(core).resolve(); bot = bot_id(bot)
    own = safe_path(core.parent, core.parent / 'bots' / bot / 'infinity/processes/snowluma-recovery.json')
    legacy = safe_path(core.parent, core / 'data/infinity-processes/snowluma-recovery.json')
    if baseline and legacy.exists():
        if own.exists(): raise ValueError('QQ 凭据存在新旧两份归属，请先核对；未选择或覆盖')
        return legacy
    return own
