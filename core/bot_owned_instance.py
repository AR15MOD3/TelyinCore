"""Owned launcher instance receipt; cooperative path checks, NOT OS-atomic CAS/ACL or offline proof."""
import json
import os
from pathlib import Path
import stat
from threading import Lock
import uuid

from bot_host_registry import HostEvidenceRegistry
from bot_host_inventory import _identity
from runtime_identity import safe_path


def _id(info):
    if (type(info.st_dev) is not int or info.st_dev < 0
            or type(info.st_ino) is not int or info.st_ino <= 0):
        raise ValueError('native instance file identity unavailable')
    return info.st_dev,info.st_ino


class OwnedInstanceMarker:
    def __init__(self,registry,bot,controller):
        if type(registry) is not HostEvidenceRegistry or bot not in registry.bots:
            raise ValueError('explicit owned instance registry required')
        self.registry,self.bot=registry,bot
        self.identity=_identity(controller)
        self.row=dict(self.identity,bot=bot)
        self.base=registry.core/'data'
        self.path=self.base/'instances'/(bot+'.json')
        self.root=registry.core.parent
        self.receipt=self.temporary=None
        self.attempted=self.cleaned=False
        self._busy=Lock()
        with registry._write():
            self._owner()
            self.pins={p:self._directory(p) for p in (self.root,registry.core,self.base)}

    def _directory(self,path):
        safe_path(self.root,path)
        info=path.stat()
        if not stat.S_ISDIR(info.st_mode):raise ValueError('instance directory required')
        return _id(info)

    def _paths(self):
        if self.registry.core != self.base.parent:
            raise ValueError('instance registry root binding changed')
        for path,identity in self.pins.items():
            if self._directory(path)!=identity:raise ValueError('instance directory binding changed')
        safe_path(self.root,self.path)

    def _owner(self):
        if (self.registry._processes.get(self.identity['pid'])!=self.row
                or self.registry._alive(self.identity['pid'])!=self.identity
                or self.registry._processes.get(self.identity['pid'])!=self.row):
            raise ValueError('registered instance controller changed')

    def _snapshot(self,path):
        self._paths();safe_path(self.root,path)
        with path.open('rb') as stream:
            before=os.fstat(stream.fileno());raw=stream.read(4097);after=os.fstat(stream.fileno())
        self._paths();current=path.stat()
        if (len(raw)>4096 or not stat.S_ISREG(before.st_mode)
                or _id(before)!=_id(after) or _id(before)!=_id(current)
                or before.st_size!=after.st_size or len(raw)!=after.st_size):
            raise ValueError('bounded stable instance file required')
        return raw,_id(after),after.st_nlink

    def _cleanup_temporary(self):
        if self.temporary is None:return
        path,identity,raw=self.temporary
        seen=self._snapshot(path)
        if seen[:2]!=(raw,identity):raise ValueError('owned instance temporary replaced')
        self._paths();path.unlink();self.temporary=None

    def publish(self,port):
        if not self._busy.acquire(blocking=False):raise ValueError('owned instance operation busy')
        try:
            if self.attempted or type(port) is not int or not 1<=port<=65535:
                raise ValueError('single-use owned instance port required')
            self.attempted=True
            self._paths()  # refuse known directory replacement before acquiring its file lock
            with self.registry._write():
                self._paths();self._owner();self._paths()
                self.path.parent.mkdir(exist_ok=True)
                self.pins[self.path.parent]=self._directory(self.path.parent)
                if self.path.exists():raise ValueError('preexisting instance marker cannot be adopted')
                raw=json.dumps({'pid':self.identity['pid'],'port':port},separators=(',',':')).encode('ascii')
                temporary=self.path.with_name('.'+self.path.name+'.'+uuid.uuid4().hex+'.pending')
                safe_path(self.root,temporary)
                try:
                    with temporary.open('xb') as stream:
                        self.temporary=(temporary,_id(os.fstat(stream.fileno())),raw)
                        if stream.write(raw)!=len(raw):raise ValueError('short instance publication')
                        stream.flush();os.fsync(stream.fileno())
                    self._owner();self._paths()
                    seen=self._snapshot(temporary)
                    if seen!=(raw,self.temporary[1],1):raise ValueError('instance temporary changed before publication')
                    self.receipt=(raw,self.temporary[1])  # retain even if link/ACK fails after creation
                    os.link(temporary,self.path)  # atomic no-clobber, NEVER replace another marker
                    if self._snapshot(self.path)[:2]!=self.receipt:
                        raise ValueError('instance publication changed')
                finally:
                    import sys
                    primary=sys.exception()
                    try:self._cleanup_temporary()
                    except BaseException as error:
                        if primary is None:raise
                        primary.add_note('owned instance temporary cleanup failed: '+type(error).__name__)
                if self._snapshot(self.path)!=(raw,self.receipt[1],1):
                    raise ValueError('instance publication link binding changed')
                self._owner();self._paths()
        finally:self._busy.release()

    def retire(self):
        # No receipt => no marker lookup, adoption or cleanup of somebody else's file.
        if self.cleaned:return True
        if self.receipt is None:return False
        if not self._busy.acquire(blocking=False):raise ValueError('owned instance operation busy')
        try:
            self._paths()
            with self.registry._write(allow_fenced=True):
                if self.registry._processes.get(self.identity['pid'])!=self.row:
                    raise ValueError('instance retirement ownership row changed')
                seen=self.registry.probe(self.identity['pid'])
                if (type(seen) is not dict or type(seen.get('pid')) is not int
                        or seen['pid']!=self.identity['pid'] or seen.get('state')!='absent'):
                    raise ValueError('instance controller not independently absent')
                self._paths()
                self._cleanup_temporary()
                raw,identity=self.receipt
                if self._snapshot(self.path)!=(raw,identity,1):
                    raise ValueError('owned instance marker changed; preserved')
                if self.registry._processes.get(self.identity['pid'])!=self.row:
                    raise ValueError('instance retirement row changed')
                # Cooperative identity guard, not a kernel atomic conditional unlink.
                self._paths();self.path.unlink();self.cleaned=True
                return True
        finally:self._busy.release()
