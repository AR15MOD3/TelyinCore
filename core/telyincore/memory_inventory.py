"""Native suffix inventory with explicit administrator traversal failures.

Restricted readers retain the established omission of inaccessible sources;
no private path or unreadable filename is exposed by a new diagnostic.
The pending directory stack retains native traversal's size-dependent cost.
"""
import os
from pathlib import Path
import stat


def paths(root, suffix, *, recursive=True, strict=True):
    root = Path(root)
    if not strict:
        yield from (root.rglob('*'+suffix) if recursive else root.glob('*'+suffix))
        return
    try:
        info = root.stat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode):return
    # Readers already reject redirected source paths. Do not traverse them.
    if root.resolve()!=root.absolute() or root.is_symlink():return
    pending = [root]
    suffix = os.path.normcase(suffix)
    while pending:
        folder = pending.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                path = Path(entry.path)
                if os.path.normcase(entry.name).endswith(suffix):yield path
                if recursive and entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                    # Windows junctions are redirected directories too.
                    if getattr(entry, 'is_junction', lambda: False)():continue
                    pending.append(path)


from contextlib import contextmanager

@contextmanager
def ordered(root, suffix, *, recursive=True, strict=True):
    from .ordered_paths import ordered as sort
    with sort(paths(root,suffix,recursive=recursive,strict=strict)) as names:
        yield names
