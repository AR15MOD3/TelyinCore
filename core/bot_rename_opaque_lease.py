"""Bounded Windows payload-preservation lease for an internal rename session.

Retains native read handles WITHOUT FILE_SHARE_WRITE. Windows forbids moving a
parent with open descendants even with SHARE_DELETE: release only the moving
subtree, then reopen exact mapped files and verify original bounded byte hashes
BEFORE ACK. Uncertain effects are never guessed/retried. No content parsing,
rewriting, backup copying or account/offline authority; not a hostile kernel or
preexisting writable memory-mapping sandbox or persisted recovery capability.
"""
from physical_close import close_once, close_after_failure
import os
from pathlib import Path
import stat
import hashlib
from contextlib import contextmanager
from runtime_identity import safe_path
from bot_migration_gate import GATE

MAX_FILES=4096
MAX_DIRECTORIES=2048
MAX_DEPTH=64
MAX_FILE_BYTES=64*1024*1024
MAX_TOTAL_BYTES=256*1024*1024


def _identity(info):
    return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns


def _attributes(path):
    if os.name!='nt':raise OSError('native Windows payload lease required')
    import ctypes,msvcrt
    from ctypes import wintypes as W
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateFileW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,ctypes.c_void_p,W.DWORD,W.DWORD,W.HANDLE]
    k.CreateFileW.restype=W.HANDLE
    k.CloseHandle.argtypes=[W.HANDLE];k.CloseHandle.restype=W.BOOL
    # Non-inheritable, read only, no FILE_SHARE_WRITE; bounded opaque hash only.
    h=k.CreateFileW(str(path),0x80000000,0x1|0x4,None,3,0x00200000,None)
    if h==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:fd=msvcrt.open_osfhandle(h,os.O_RDONLY|os.O_BINARY)
    except BaseException:
        k.CloseHandle(h)
        raise
    try:return os.fdopen(fd,'rb',closefd=True)
    except BaseException:
        os.close(fd)
        raise


def _digest(stream, size):
    if not 0<=size<=MAX_FILE_BYTES:raise ValueError('bounded opaque file bytes exceeded')
    stream.seek(0);h=hashlib.sha256();seen=0
    while True:
        chunk=stream.read(min(1024*1024,MAX_FILE_BYTES+1-seen))
        if not chunk:break
        seen+=len(chunk)
        if seen>size:raise ValueError('opaque bytes changed during snapshot')
        h.update(chunk)
    if seen!=size:raise ValueError('opaque bytes changed during snapshot')
    return h.digest()


class _OpaqueLease:
    def __init__(self,transaction):
        if os.name!='nt':raise OSError('native Windows payload lease required')
        self.root=Path(transaction.root)
        self.operations=transaction.operations
        self.controls={p for phase in transaction.phases for p,_ in phase.files}
        self.directories={};self.files={};self.closed=False;self.failed=False;self.suspended=False;self._suspended_cursor=None;total=0
        roots=sorted(transaction.phases[0].roots,key=lambda p:(len(p),p))
        roots=[p for i,p in enumerate(roots) if not any(p.startswith(a+'/') for a in roots[:i])]
        try:
            todo=[(p,0) for p in roots]
            while todo:
                relative,depth=todo.pop()
                if depth>MAX_DEPTH or len(self.directories)>=MAX_DIRECTORIES:
                    raise ValueError('bounded opaque directory scope exceeded')
                path=safe_path(self.root,self.root/relative);info=path.lstat()
                if not stat.S_ISDIR(info.st_mode):raise ValueError('opaque directory changed')
                self.directories[relative]=(info.st_dev,info.st_ino)
                for child in path.iterdir():
                    key=child.relative_to(self.root).as_posix()
                    safe_path(self.root,child)
                    if self._excluded(key):continue
                    info=child.lstat()
                    if stat.S_ISDIR(info.st_mode):todo.append((key,depth+1))
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink==1:
                        if len(self.files)>=MAX_FILES:raise ValueError('bounded opaque file scope exceeded')
                        total+=info.st_size
                        if total>MAX_TOTAL_BYTES:raise ValueError('bounded aggregate opaque bytes exceeded')
                        stream=_attributes(child)
                        self.files[key]=[stream,_identity(info),None]
                        if _identity(os.fstat(stream.fileno()))!=_identity(info):
                            raise ValueError('opaque identity changed during native pin')
                        self.files[key][2]=_digest(stream,info.st_size)
                        if _identity(os.fstat(stream.fileno()))!=_identity(info):
                            raise ValueError('opaque changed during bounded hash')
                    else:raise ValueError('single-link regular opaque resource required')
            self.check(0)
        except BaseException as primary:
            close_after_failure(self, primary)
            raise

    def _excluded(self,key):
        # Only fixed control files and this engine's non-payload working scope.
        # Never ignore arbitrary pending/custody/account files or .rename temps.
        return (key in self.controls or key=='core/data/'+GATE
                or key=='core/data/.bot-identity.lock'
                or key=='core/data/bot-rename-transactions'
                or key.startswith('core/data/bot-rename-transactions/'))

    def _at(self,path,cursor):
        for op in self.operations[:cursor]:
            if op.kind in ('move','rekey') and (path==op.path or path.startswith(op.path+'/')):
                path=op.destination+path[len(op.path):]
        return path

    def check(self,cursor):
        if self.closed or self.failed or self.suspended:raise ValueError('original opaque lease expired/pending/suspended; no blind reacquire')
        if type(cursor) is not int or not 0<=cursor<=len(self.operations):
            raise ValueError('invalid opaque phase')
        directories={self._at(p,cursor):v for p,v in self.directories.items()}
        files={self._at(p,cursor):v for p,v in self.files.items()}
        expected=set(directories)|set(files)
        for key,identity in directories.items():
            path=safe_path(self.root,self.root/key);info=path.lstat()
            if not stat.S_ISDIR(info.st_mode) or (info.st_dev,info.st_ino)!=identity:
                raise ValueError('opaque directory identity changed')
            actual={child.relative_to(self.root).as_posix() for child in path.iterdir()
                    if not self._excluded(child.relative_to(self.root).as_posix())}
            wanted={p for p in expected if p.rsplit('/',1)[0]==key}
            if actual!=wanted:raise ValueError('opaque namespace changed; retain fence')
        for key,(stream,identity,digest) in files.items():
            path=safe_path(self.root,self.root/key);info=path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1
                    or _identity(info)!=identity or _identity(os.fstat(stream.fileno()))!=identity):
                raise ValueError('opaque file changed/replaced; retain fence')

    def suspend(self,cursor):
        # Only acknowledged original sessions may release their read handles.
        # The byte digests/native identities remain the original before-images.
        self.check(cursor)
        self.suspended=True;self._suspended_cursor=cursor
        try:
            for row in self.files.values():
                row[0].close();row[0]=None
        except BaseException:
            self.failed=True
            raise
        return True

    def resume(self,cursor):
        if (self.closed or self.failed or not self.suspended
                or type(cursor) is not int or cursor!=self._suspended_cursor):
            raise ValueError('original suspended payload lease required')
        try:
            for key,row in self.files.items():
                path=safe_path(self.root,self.root/self._at(key,cursor))
                stream=_attributes(path);row[0]=stream
                if (_identity(os.fstat(stream.fileno()))!=row[1]
                        or _digest(stream,row[1][2])!=row[2]
                        or _identity(os.fstat(stream.fileno()))!=row[1]):
                    raise ValueError('original opaque bytes changed while suspended')
            self.suspended=False
            self.check(cursor)  # original directory/native namespace, no new snapshot
            self._suspended_cursor=None
            return True
        except BaseException as primary:
            self.failed=True
            close_after_failure(self, primary)
            raise

    @contextmanager
    def effect_window(self,op,reverse,target):
        # No fallback to ordinary rename with unchecked released payloads.
        moving={}
        if op.kind in ('move','rekey'):
            source=op.destination if reverse else op.path
            before=target+1 if reverse else target-1
            moving={key:row for key,row in self.files.items()
                    if self._at(key,before)==source or self._at(key,before).startswith(source+'/')}
        try:
            for stream,_,_ in moving.values():stream.close()
            for row in moving.values():row[0]=None
            yield
            for key,row in moving.items():
                path=safe_path(self.root,self.root/self._at(key,target))
                stream=_attributes(path);row[0]=stream
                if (_identity(os.fstat(stream.fileno()))!=row[1]
                        or _digest(stream,row[1][2])!=row[2]
                        or _identity(os.fstat(stream.fileno()))!=row[1]):
                    raise ValueError('opaque bytes/identity changed during directory move; no ACK')
        except BaseException:
            self.failed=True
            raise

    def close(self):
        def release():
            errors=[]
            for stream,_,_ in self.files.values():
                if stream is None:continue
                try:stream.close()
                except BaseException as error:errors.append(error)
            if errors:
                self.failed=True
                if len(errors)==1:raise errors[0]
                raise BaseExceptionGroup('opaque resource close failed',errors)
        close_once(self, release)
