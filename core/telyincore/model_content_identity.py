"""Content identity for cooperatively maintained local embedding model trees.

No inference, network, cache migration or writes. Metadata avoids rehashing an
unchanged tree; replacement with forged identical metadata is outside this local
maintenance contract. A model must not be edited concurrently with its consumers.
"""
import hashlib
from pathlib import Path
from threading import RLock
from .file_signature import signature

_cache={}
_lock=RLock()

def _inventory(root):
    if not root.is_dir():return ()
    rows=[]
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise ValueError('redirected model file rejected')
        if path.is_file():
            rows.append((path.relative_to(root).as_posix(), *signature(path)))
    return tuple(rows)

def identity(model_path):
    root=Path(model_path).resolve()
    key=str(root).casefold()
    with _lock:
        before=_inventory(root)
        old=_cache.get(key)
        if old is not None and old[0]==before:return old[1]
        digest=hashlib.sha256()
        digest.update(b'telyin-model-content-v1\0')
        digest.update(key.encode('utf-8'))
        for name,*_ in before:
            digest.update(b'\0'+name.encode('utf-8')+b'\0')
            file_hash=hashlib.sha256()
            with (root/name).open('rb') as handle:
                while chunk:=handle.read(1024*1024):file_hash.update(chunk)
            digest.update(file_hash.digest())
        if before!=_inventory(root):raise RuntimeError('model changed during content capture')
        result=digest.hexdigest()
        _cache[key]=(before,result)
        return result
