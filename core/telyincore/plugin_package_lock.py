"""Cross-process shared package leases; a retained plugin keeps its code pinned."""
import os
from pathlib import Path
from .plugin_scope_store import plugin_name

class PackageLock:
    def __init__(self, root, name, *, exclusive=False, operation=False):
        from runtime_identity import safe_path
        self.root = Path(root).resolve()
        folder = safe_path(self.root, self.root/'data/plugin-package-locks')
        folder.mkdir(parents=True, exist_ok=True)
        self.path = safe_path(self.root, folder/(('operation-' if operation else 'package-') + plugin_name(name) + '.lock'))
        self.exclusive = exclusive
        self.stream = None

    def __enter__(self):
        from runtime_identity import safe_path
        safe_path(self.root, self.path)
        if self.path.exists() and self.path.stat().st_nlink != 1:
            raise ValueError('插件锁文件不是独立文件')
        f = self.path.open('a+b')
        self.stream = f
        try:
            if f.tell() == 0: f.write(b'0'); f.flush()
            f.seek(0)
            if os.name == 'nt':
                import ctypes as c, msvcrt
                from ctypes import wintypes as w
                class Overlap(c.Structure):
                    _fields_ = [('internal', c.c_size_t), ('internal_high', c.c_size_t),
                                ('offset', w.DWORD), ('offset_high', w.DWORD), ('event', w.HANDLE)]
                self.kernel = c.WinDLL('kernel32', use_last_error=True)
                self.overlap = Overlap()
                self.kernel.LockFileEx.argtypes = [w.HANDLE,w.DWORD,w.DWORD,w.DWORD,w.DWORD,c.POINTER(Overlap)]
                self.kernel.UnlockFileEx.argtypes = [w.HANDLE,w.DWORD,w.DWORD,w.DWORD,c.POINTER(Overlap)]
                self.handle = msvcrt.get_osfhandle(f.fileno())
                if not self.kernel.LockFileEx(self.handle, 1 | (2 if self.exclusive else 0), 0, 1, 0, c.byref(self.overlap)):
                    raise ValueError('插件仍被其他档案使用，或另一个插件操作尚未完成，请稍后重试')
            else:
                import fcntl
                fcntl.flock(f.fileno(), (fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            a, b = self.path.stat(), os.fstat(f.fileno())
            if (a.st_dev,a.st_ino) != (b.st_dev,b.st_ino) or b.st_nlink != 1:
                raise ValueError('插件锁文件发生变化')
            return self
        except BaseException:
            f.close(); self.stream = None
            raise

    def close(self):
        if self.stream is None: return
        f, self.stream = self.stream, None
        try:
            if os.name == 'nt':
                import ctypes as c
                if not self.kernel.UnlockFileEx(self.handle, 0, 1, 0, c.byref(self.overlap)):
                    raise OSError('插件锁释放未确认')
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally: f.close()

    def __exit__(self, *args): self.close()
