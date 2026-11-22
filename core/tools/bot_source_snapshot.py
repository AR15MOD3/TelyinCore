"""Bounded static local-import fingerprint closure. Never imports target code.

Only explicit source seeds and their repository Python imports are read; no
runtime/config/data scan, sys.path/site-packages lookup or link following.
Dynamic/file-loaded dependencies need explicit seeds. This is not a complete
runtime dependency or host-entrypoint authority, nor a continuous file monitor.
"""
import ast
import hashlib
from pathlib import Path, PurePosixPath
import stat

MAX_FILES = 4096
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
SUFFIXES = {'.py', '.md', '.toml', '.ts', '.mjs', '.ps1', '.bat'}
# One packaged source asset, NOT a general permission to hash runtime JSON.
SOURCE_JSON = frozenset({'templates/bot-default/通道/场景.json',
                         'templates/bot-default/环境/场景.json'})


def _relative(value):
    if (type(value) is not str or not value or '\\' in value or ':' in value
            or any(p in ('', '.', '..') for p in value.split('/'))):
        raise ValueError('invalid source seed')
    path = PurePosixPath(value)
    if path.is_absolute() or (path.suffix not in SUFFIXES and value not in SOURCE_JSON):
        raise ValueError('invalid source seed')
    return path


def _checked(root, relative):
    current = root
    for part in ('', *relative.parts):
        if part:
            current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('source links/reparse points forbidden')
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('source must be a regular file')
    return current


def source_hashes(core, seeds):
    """Hash local source closure without executing imports or reading data.

Search roots mirror curated pytest helper imports: repository and tests only.
Both plausible local resolutions are retained conservatively. Import aliases,
package initializers and statically present imports inside functions count.
"""
    root = Path(core).absolute()
    if type(seeds) not in (list, tuple) or not seeds or len(seeds) > MAX_FILES:
        raise ValueError('bounded nonempty source seeds required')
    pending = {_relative(seed).as_posix() for seed in seeds}
    result = {}; total = 0

    def add_module(parts, search=('', 'tests')):
        if not parts or not all(p.isidentifier() for p in parts):
            return
        for prefix in search:
            base = PurePosixPath(prefix) if prefix else PurePosixPath()
            # Importing a submodule executes every existing package initializer.
            candidates = [base.joinpath(*parts[:i], '__init__.py')
                          for i in range(1, len(parts) + 1)]
            candidates.append(base.joinpath(*parts).with_suffix('.py'))
            for candidate in candidates:
                if _checked(root, candidate) is not None:
                    pending.add(candidate.as_posix())

    while pending:
        name = min(pending); pending.remove(name)
        if name in result:
            continue
        if len(result) >= MAX_FILES:
            raise ValueError('source closure file limit')
        relative = _relative(name)
        path = _checked(root, relative)
        if path is None:
            raise ValueError('required source missing')
        with path.open('rb') as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        total += len(raw)
        if len(raw) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError('source closure byte limit')
        result[name] = hashlib.sha256(raw).hexdigest()
        if relative.suffix != '.py':
            continue
        tree = ast.parse(raw, filename=name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    add_module(alias.name.split('.'))
            elif isinstance(node, ast.ImportFrom):
                module = node.module.split('.') if node.module else []
                search = ('', 'tests')
                if node.level:
                    package = list(relative.parent.parts)
                    # Root scripts may contain a guarded ``from .pkg`` fallback
                    # for package-style launch. Treat the repository as a
                    # virtual outer package; never resolve beyond its root.
                    if node.level > len(package) + 1:
                        raise ValueError('relative source import escapes package')
                    module = package[:len(package) - node.level + 1] + module
                    search = ('',)
                add_module(module, search)
                for alias in node.names:
                    if alias.name != '*':
                        add_module(module + [alias.name], search)
    return dict(sorted(result.items()))
