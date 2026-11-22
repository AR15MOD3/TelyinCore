"""Live origin for exclusively NEW, fenced, never-started neutral state.

No old-root adoption, serialized/offline receipt, coverage callback or runtime
installation. Atomic publication exposes the startup-denial marker from birth.
Local creation authority is NOT complete host/external-account authority.
"""
import os
import json
import stat
import uuid
import time
from pathlib import Path
from threading import Lock
from bot_private_file import open_private_new
from bot_migration_gate import CREATION_GATE
from bot_registry import bot_id
from runtime_identity import safe_path,LEDGER,MARKER
from bot_host_registry import HostEvidenceRegistry


def _directory(path):
    import ctypes,msvcrt
    from ctypes import wintypes as W
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateFileW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,ctypes.c_void_p,W.DWORD,W.DWORD,W.HANDLE]
    k.CreateFileW.restype=W.HANDLE
    k.CloseHandle.argtypes=[W.HANDLE];k.CloseHandle.restype=W.BOOL
    h=k.CreateFileW(str(path),0x81,3,None,3,0x02000000|0x00200000,None)
    if h==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:return msvcrt.open_osfhandle(h,os.O_RDONLY|os.O_BINARY)
    except BaseException:
        k.CloseHandle(h)
        raise


def _new(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    with open_private_new(path) as f:
        f.write(raw);f.flush();os.fsync(f.fileno());s=os.fstat(f.fileno())
    return s.st_dev,s.st_ino


def _json(row):return (json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n').encode()


class NewDeploymentState:
    def __init__(self,root,*,names=('Mybot',),baseline='Mybot'):
        if type(names) not in (tuple,list) or not 1<=len(names)<=16:
            raise ValueError('finite new-deployment Bot roster required')
        names=tuple(bot_id(n) for n in names);baseline=bot_id(baseline)
        if (baseline not in names or any(n.startswith('.') for n in names)
                or len(set(n.casefold() for n in names))!=len(names)):
            raise ValueError('canonical distinct new Bot names required')
        root=Path(root).absolute();safe_path(root.parent,root)
        # Reject existing roots BEFORE opening any template/config/private body.
        if root.exists():raise ValueError('existing deployment cannot obtain creation admission')
        if os.name!='nt':raise OSError('native Windows creation origin required')
        self.root=root;self.core=root/'core';self._pid=os.getpid();self._fd=None
        self._expired=False;self._used=False;self._ready=False;self._busy=Lock()
        self._nonce=uuid.uuid4().hex;stage=root.parent/('.bot-new-deployment-'+uuid.uuid4().hex)
        stage.mkdir(exist_ok=False)
        try:
            base=stage/'core/data';base.mkdir(parents=True)
            self._barrier=_json(dict(version=1,origin=self._nonce,state='never-started-fenced'))
            self._barrier_inode=_new(base/CREATION_GATE,self._barrier)
            template=Path(__file__).resolve().parent/'templates/bot-default'
            catalog={};total=0
            for p in sorted(template.rglob('*')):
                safe_path(template,p)
                if p.is_dir():continue
                info=p.lstat();rel=p.relative_to(template)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1
                        or (p.suffix!='.md' and rel.as_posix() not in {'通道/场景.json','环境/场景.json'})
                        or info.st_size>65536 or len(catalog)>=128):
                    raise ValueError('unsupported packaged neutral template resource')
                raw=p.read_bytes();total+=len(raw)
                if total>1024*1024 or len(raw)!=info.st_size:
                    raise ValueError('bounded neutral template required')
                catalog[rel]=raw
            if not catalog:raise ValueError('neutral template missing')
            rows={n:uuid.uuid4().hex for n in names}
            for n in names:
                folder=stage/'bots'/n
                for rel,raw in catalog.items():_new(folder/rel,raw)
                _new(folder/'bot.json',_json(dict(displayName=n,createdAtNs=time.time_ns())))
                _new(folder/MARKER,_json(dict(name=n,token=rows[n])))
                if n!=baseline:(base/'bot-runtimes'/n).mkdir(parents=True)
            _new(base/LEDGER,_json(dict(version=1,bindings=rows)))
            _new(base/'bot-runtime-owner.json',_json(dict(baseline=baseline)))
            _new(base/'active-bot.json',_json(dict(baseline=baseline,active=baseline)))
            # Windows rename refuses existing destination directories, even empty.
            # A competing creator wins; its root is never adopted/overwritten.
            stage.rename(root)
            self._fd=_directory(root);s=os.fstat(self._fd);self._inode=(s.st_dev,s.st_ino)
            self._registry=HostEvidenceRegistry(self.core,list(names),baseline)
            self._ready=True;self.check(self.core)
        except BaseException:
            self.close()
            # Leave partial OWN staging/published state fenced; no recursive delete.
            raise

    def __reduce__(self):raise TypeError('live native creation origin is not serializable')

    def check(self,core):
        try:self._check_origin(core)
        except BaseException:
            self._expired=True
            raise

    def _check_origin(self,core):
        if (type(self) is not NewDeploymentState or self._expired or not self._ready
                or self._pid!=os.getpid() or self._fd is None
                or Path(core).absolute()!=self.core):
            raise ValueError('original live new-deployment origin required')
        safe_path(self.root.parent,self.root)
        s=self.root.lstat();native=os.fstat(self._fd)
        if (s.st_dev,s.st_ino)!=self._inode or (native.st_dev,native.st_ino)!=self._inode:
            raise ValueError('new-deployment native root changed')
        p=safe_path(self.root,self.core/'data'/CREATION_GATE);s=p.lstat()
        if (not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_size!=len(self._barrier)
                or (s.st_dev,s.st_ino)!=self._barrier_inode):
            raise ValueError('new-deployment fence changed; no admission')
        with p.open('rb') as f:
            raw=f.read(1025);seen=os.fstat(f.fileno())
        if raw!=self._barrier or (seen.st_dev,seen.st_ino)!=self._barrier_inode:
            raise ValueError('new-deployment fence changed during read')

    def close(self):
        self._expired=True
        if self._fd is not None:os.close(self._fd);self._fd=None

    def status(self):
        return dict(origin='exclusive-new-never-started-state',expired=self._expired,
                    used=self._used,startup_fenced=True,global_complete=False,
                    deployable_runtime=False,production_apply_supported=False)


def _begin_creation(core,old,new,*,creation,registry=None,**scope):
    if type(creation) is not NewDeploymentState:
        raise ValueError('original native new-deployment origin required')
    if not creation._busy.acquire(blocking=False):raise ValueError('creation admission busy')
    try:
        creation.check(core)
        if creation._used:raise ValueError('creation origin consumed; blind re-admission denied')
        if registry is not None and registry is not creation._registry:
            raise ValueError('creation registry cannot be substituted')
        creation._used=True
        from copy import deepcopy
        from runtime_identity import identity_lock
        from bot_rename_projection import project_identity
        from bot_rename_transaction import _ControlledTransaction
        from bot_rename_coordinator import _RegistryPhaseCoordinator
        state=creation._registry.inspect()
        if (state['sealed'] or state['coverage_revoked'] or state['registered_processes']
                or state['bound_configs'] or state['pending_account_changes']):
            raise ValueError('new-deployment registry changed/revoked; no admission')
        with identity_lock(creation.core/'data'):
            with creation._registry.capture(creation.core) as capture:
                creation.check(core)
                if capture['complete'] is not False or capture['processes'] or capture['accounts']:
                    raise ValueError('never-started original registry required')
                projection=project_identity(core,old,new,_creation=creation,**scope)
                transaction=_ControlledTransaction._prepare_locked(projection)
                original=deepcopy(capture)
        coordinator=_RegistryPhaseCoordinator(transaction,creation._registry,creation=creation)
        if coordinator._snapshot!=original:
            coordinator.close()
            raise ValueError('creation registry changed before bind; retain fence')
        return coordinator
    finally:creation._busy.release()
