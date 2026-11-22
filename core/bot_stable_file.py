"""Bounded identity-checked read for cooperating recovery metadata readers.

Reject ordinary replacement, hard links and observed in-place writes. This is
not an adversarial same-user sandbox or a physical durability claim.
"""
import os,stat
from pathlib import Path
from runtime_identity import safe_path

def read_stable(root,path,limit):
    path=Path(path);safe_path(root,path)
    if type(limit) is not int or not 0<limit<=65536:raise ValueError('bounded read required')
    before=path.lstat()
    def signature(info):return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or not before.st_ino
            or getattr(before,'st_file_attributes',0)&0x400):raise ValueError('unsafe recovery metadata file')
    with path.open('rb') as f:
        first=os.fstat(f.fileno())
        if signature(first)!=signature(before):raise ValueError('recovery metadata replaced before read')
        raw=f.read(limit+1);last=os.fstat(f.fileno())
    safe_path(root,path)
    if len(raw)>limit:raise ValueError('recovery metadata capacity')
    if signature(last)!=signature(before) or signature(path.lstat())!=signature(before):
        raise ValueError('recovery metadata changed during read')
    return raw,(before.st_dev,before.st_ino)
