"""Read-only file identity including actual change time on both host platforms.

Windows Python's st_ctime can be creation time. Use the native ChangeTime and
file ID instead; restored mtime must not hide an ordinary intervening edit.
This detects observed changes, not a hostile writer able to forge all metadata.
"""
from functools import lru_cache
import os
from pathlib import Path


def _posix(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@lru_cache(maxsize=1)
def _windows_api():
    import ctypes as C
    from ctypes import wintypes as W
    class Basic(C.Structure):
        _fields_ = [('created', C.c_int64), ('accessed', C.c_int64),
                    ('written', C.c_int64), ('changed', C.c_int64), ('attributes', W.DWORD)]
    class Identity(C.Structure):
        _fields_ = [('attributes', W.DWORD), ('created', W.FILETIME),
                    ('accessed', W.FILETIME), ('written', W.FILETIME),
                    ('volume', W.DWORD), ('size_high', W.DWORD), ('size_low', W.DWORD),
                    ('links', W.DWORD), ('index_high', W.DWORD), ('index_low', W.DWORD)]
    kernel = C.WinDLL('kernel32', use_last_error=True)
    basic = kernel.GetFileInformationByHandleEx
    basic.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]
    basic.restype = W.BOOL
    identity = kernel.GetFileInformationByHandle
    identity.argtypes = [W.HANDLE, C.POINTER(Identity)]
    identity.restype = W.BOOL
    return Basic, Identity, basic, identity


def descriptor_signature(fd):
    if os.name != 'nt':
        return _posix(os.fstat(fd))
    import ctypes as C
    import msvcrt
    Basic, Identity, get_basic, get_identity = _windows_api()
    handle = msvcrt.get_osfhandle(fd)
    basic, identity = Basic(), Identity()
    if not get_identity(handle, C.byref(identity)):
        raise C.WinError(C.get_last_error())
    if not get_basic(handle, 0, C.byref(basic), C.sizeof(basic)):  # FileBasicInfo
        raise C.WinError(C.get_last_error())
    return (identity.volume, (identity.index_high << 32) | identity.index_low,
            (identity.size_high << 32) | identity.size_low, basic.written, basic.changed)


def signature(path):
    if os.name != 'nt':
        return _posix(Path(path).stat())
    fd = os.open(path, os.O_RDONLY | os.O_BINARY)
    try:
        return descriptor_signature(fd)
    finally:
        os.close(fd)
