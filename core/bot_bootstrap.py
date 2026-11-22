"""Create a neutral bot only when its whole directory is absent."""
from pathlib import Path
import shutil
import tempfile


def ensure_bot(parent: Path, name: str, template: Path) -> bool:
    if (not name or name in (".", "..") or any(c in name for c in '/\\:')
            or name.casefold() == ".deleted" or name.casefold().startswith(".bot-init-")):
        raise ValueError("invalid bot directory name")
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / name
    if target.exists():
        return False
    stage = Path(tempfile.mkdtemp(prefix=".bot-init-", dir=parent))
    try:
        shutil.copytree(template, stage, dirs_exist_ok=True)
        try:
            stage.rename(target)
        except FileExistsError:
            return False
        return True
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def ensure_bot_support(target: Path, template: Path) -> list[str]:
    """Add missing runtime scene files, never replace user-authored content."""
    target = target.resolve()
    if all((target / "persona" / name).is_file() for name in ("宪法.md", "角色设定.md")):
        return []
    if (template / "persona" / "宪法.md").is_file():
        template = template.parent / "bot-legacy-support"
    added = []
    for category in ('通道', '环境'):
        folder = target / category
        if folder.is_symlink() or not folder.resolve().is_relative_to(target):
            raise ValueError('Bot 支持目录不能指向其他位置')
        folder.mkdir(exist_ok=True)
        for source in sorted((template / category).glob('*.md')):
            dest = folder / source.name
            if dest.is_symlink() or not dest.resolve().is_relative_to(target):
                raise ValueError('Bot 支持文件不能指向其他位置')
            if dest.exists():
                if not dest.is_file():
                    raise ValueError('Bot 支持文件路径不是文件')
                # Invalid UTF-8 is a configuration error, not permission to replace it.
                dest.read_text(encoding='utf-8')
                continue
            data = source.read_bytes()
            try:
                with dest.open('xb') as stream:
                    stream.write(data)
            except FileExistsError:
                continue
            added.append(category + '/' + source.name)
    return added
