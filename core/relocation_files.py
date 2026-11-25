"""Back up and atomically replace files belonging to a moved deployment."""
import hashlib
import json
import os
import uuid
from bot_private_file import open_private_new
from runtime_identity import safe_path


def install_bytes(root, path, before, after):
    """Back up the exact original and replace only that observed file."""
    path = safe_path(root, path)
    backup = safe_path(root, root/'runtime/relocation-backups/files'/path.relative_to(root)/
                       hashlib.sha256(before).hexdigest())
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        scratch = backup.with_name(backup.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            with open_private_new(scratch) as out:
                out.write(before)
                out.flush()
                os.fsync(out.fileno())
            os.replace(scratch,backup)
        finally:
            scratch.unlink(missing_ok=True)
    if backup.read_bytes() != before:
        raise ValueError('迁移备份不完整')
    tmp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with open_private_new(tmp) as out:
            out.write(after)
            out.flush()
            os.fsync(out.fileno())
        if path.stat().st_nlink != 1 or path.read_bytes() != before:
            raise ValueError('文件在迁移期间发生变化')
        if os.name == 'nt':
            # ReplaceFile retains the existing file DACL and alternate streams.
            import ctypes
            from ctypes import wintypes
            api = ctypes.WinDLL('kernel32',use_last_error=True).ReplaceFileW
            api.argtypes = [wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD,ctypes.c_void_p,ctypes.c_void_p]
            api.restype = wintypes.BOOL
            if not api(str(path),str(tmp),None,0,None,None):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            os.chmod(tmp, path.stat().st_mode & 0o777)
            os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(root, path, raw, value):
    install_bytes(root, path, raw, (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode())
