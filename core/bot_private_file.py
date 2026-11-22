"""Exclusive NEW files: protected process-user/SYSTEM DACL on Windows.

No existing-file chmod/repair, identity-store authority, directory ACL or power-loss
claim. A failed security API never falls back to ordinary inherited permissions.
"""
import os
from pathlib import Path


def _user_sid(kernel,security):
    import ctypes
    from ctypes import wintypes as W
    kernel.GetCurrentProcess.argtypes=[];kernel.GetCurrentProcess.restype=W.HANDLE
    kernel.CloseHandle.argtypes=[W.HANDLE];kernel.CloseHandle.restype=W.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    security.OpenProcessToken.argtypes=[W.HANDLE,W.DWORD,ctypes.POINTER(W.HANDLE)]
    security.OpenProcessToken.restype=W.BOOL
    security.GetTokenInformation.argtypes=[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD,ctypes.POINTER(W.DWORD)]
    security.GetTokenInformation.restype=W.BOOL
    security.ConvertSidToStringSidW.argtypes=[ctypes.c_void_p,ctypes.POINTER(W.LPWSTR)]
    security.ConvertSidToStringSidW.restype=W.BOOL
    token=W.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(),0x0008,ctypes.byref(token)):
        raise ctypes.WinError()
    try:
        size=W.DWORD()
        security.GetTokenInformation(token,1,None,0,ctypes.byref(size))
        if not 0<size.value<=65536:raise OSError('bounded process-user token required')
        buffer=ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token,1,buffer,size,ctypes.byref(size)):
            raise ctypes.WinError()
        sid=ctypes.cast(buffer,ctypes.POINTER(ctypes.c_void_p))[0]  # TOKEN_USER.User.Sid
        text=W.LPWSTR()
        if not security.ConvertSidToStringSidW(sid,ctypes.byref(text)):raise ctypes.WinError()
        try:return text.value
        finally:kernel.LocalFree(ctypes.cast(text,ctypes.c_void_p))
    finally:
        if not kernel.CloseHandle(token):raise ctypes.WinError()



def _check_windows_acl(kernel,security,handle,expected_sid):
    """Verify the exact open handle before any payload byte may be written."""
    import ctypes
    from ctypes import wintypes as W
    P=ctypes.POINTER(ctypes.c_void_p)
    security.GetSecurityInfo.argtypes=[W.HANDLE,ctypes.c_int,W.DWORD,P,P,P,P,P]
    security.GetSecurityInfo.restype=W.DWORD
    security.GetSecurityDescriptorControl.argtypes=[ctypes.c_void_p,ctypes.POINTER(W.WORD),ctypes.POINTER(W.DWORD)]
    security.GetSecurityDescriptorControl.restype=W.BOOL
    security.GetAclInformation.argtypes=[ctypes.c_void_p,ctypes.c_void_p,W.DWORD,ctypes.c_int]
    security.GetAclInformation.restype=W.BOOL
    security.GetAce.argtypes=[ctypes.c_void_p,W.DWORD,P];security.GetAce.restype=W.BOOL
    owner=ctypes.c_void_p();acl=ctypes.c_void_p();descriptor=ctypes.c_void_p()
    result=security.GetSecurityInfo(handle,1,5,ctypes.byref(owner),None,ctypes.byref(acl),None,ctypes.byref(descriptor))
    if result:raise ctypes.WinError(result)
    def sid_text(pointer):
        text=W.LPWSTR()
        if not security.ConvertSidToStringSidW(pointer,ctypes.byref(text)):raise ctypes.WinError()
        try:return text.value
        finally:kernel.LocalFree(ctypes.cast(text,ctypes.c_void_p))
    try:
        control=W.WORD();revision=W.DWORD()
        if not security.GetSecurityDescriptorControl(descriptor,ctypes.byref(control),ctypes.byref(revision)):
            raise ctypes.WinError()
        if not control.value & 0x1000 or not acl.value or sid_text(owner)!=expected_sid:
            raise OSError('private file owner/protected DACL unconfirmed')
        class Size(ctypes.Structure):
            _fields_=[('count',W.DWORD),('used',W.DWORD),('free',W.DWORD)]
        size=Size()
        if not security.GetAclInformation(acl,ctypes.byref(size),ctypes.sizeof(size),2):raise ctypes.WinError()
        if size.count!=2:raise OSError('private file access entries unconfirmed')
        seen=set()
        for index in range(size.count):
            ace=ctypes.c_void_p()
            if not security.GetAce(acl,index,ctypes.byref(ace)):raise ctypes.WinError()
            header=ctypes.string_at(ace,8)
            length=int.from_bytes(header[2:4],'little')
            if header[0]!=0 or header[1]!=0 or not 12<=length<=4096 or int.from_bytes(header[4:8],'little')!=0x1f01ff:
                raise OSError('private file allow-only access mask unconfirmed')
            seen.add(sid_text(ace.value+8))
        if seen!={expected_sid,'S-1-5-18'}:raise OSError('private file grantees unconfirmed')
    finally:kernel.LocalFree(descriptor)


def _windows_new(path):
    import ctypes,msvcrt
    from ctypes import wintypes as W
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    security=ctypes.WinDLL('advapi32',use_last_error=True)
    sid=_user_sid(kernel,security)
    class Attributes(ctypes.Structure):
        _fields_=[('length',W.DWORD),('descriptor',ctypes.c_void_p),('inherit',W.BOOL)]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[W.LPCWSTR,W.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(W.DWORD)]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype=W.BOOL
    descriptor=ctypes.c_void_p()
    # Explicit owner + protected DACL, no inherited Everyone/Users/Admin grant.
    sddl='O:'+sid+'D:P(A;;FA;;;SY)(A;;FA;;;'+sid+')'
    if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,ctypes.byref(descriptor),None):
        raise ctypes.WinError()
    try:
        attributes=Attributes(ctypes.sizeof(Attributes),descriptor,False)
        kernel.CreateFileW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,ctypes.POINTER(Attributes),W.DWORD,W.DWORD,W.HANDLE]
        kernel.CreateFileW.restype=W.HANDLE
        handle=kernel.CreateFileW(str(path),0x40000000,0,ctypes.byref(attributes),1,0x80,None)
        if handle==ctypes.c_void_p(-1).value:
            error=ctypes.get_last_error()
            if error in (80,183):raise FileExistsError(error,'private destination already exists',str(path))
            raise ctypes.WinError(error)
    finally:kernel.LocalFree(descriptor)
    try:
        _check_windows_acl(kernel,security,handle,sid)
        fd=msvcrt.open_osfhandle(handle,os.O_WRONLY|os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    try:
        os.set_inheritable(fd,False)
        return os.fdopen(fd,'wb')  # same native handle, no path reopen window
    except BaseException:
        os.close(fd)
        raise


def open_private_new(path):
    path=Path(path)
    if not path.is_absolute() or str(path).startswith(('\\\\','//')):
        raise ValueError('absolute local private-file path required')
    if os.name=='nt':return _windows_new(path)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_CLOEXEC',0),0o600)
    try:
        os.set_inheritable(fd,False)
        return os.fdopen(fd,'wb')
    except BaseException:
        os.close(fd)
        raise
