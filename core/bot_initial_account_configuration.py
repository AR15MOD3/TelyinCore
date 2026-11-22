"""First local settings publication only; never reconcile a previously used account.

No HTTP, credentials in RPC, PID adoption, stale-fence clearing or grant reset.
Only original fresh fence and a never-admitted HOST with no existing fixed store.
"""
import os,stat,hashlib
from runtime_identity import identity_lock,safe_path

class InitialAccountConfiguration:
    def __init__(self,host,previous=None):
        self.host=host;self.state='ineligible';self.inode=None
        self.store_pin=self.backup_pin=None;self.previous=None
        g=host.account_grant;f=host.account_fence
        if g is None or f is None:return
        self._unused()
        p=safe_path(host.core.parent,g.store)
        if f.path.exists():return
        if p.exists():
            if (type(previous) is not InitialAccountConfiguration or previous.host is not host
                    or previous.state!='committed' or previous.store_pin is None):return
            if (self._pin(p)!=previous.store_pin
                    or self._pin(p.with_suffix('.json.bak'))!=previous.backup_pin):return
            self.previous=previous
        elif previous is not None:return  # deletion isn't a fresh installation
        # Only the fixed backup of our already-pinned settings may accompany it.
        allowed={p.name,p.with_suffix('.json.bak').name} if self.previous is not None else set()
        if p.parent.exists() and any(x.name.startswith(p.name) and x.name not in allowed for x in p.parent.iterdir()):return
        if (host.core/'data/snowluma-companion-custody.json').exists():return
        self.state='prepared'

    def _pin(self,path):
        path=safe_path(self.host.core.parent,path)
        try:
            with path.open('rb') as stream:
                raw=stream.read(65537);info=os.fstat(stream.fileno())
        except FileNotFoundError:return None
        if len(raw)>65536 or info.st_nlink!=1 or not stat.S_ISREG(info.st_mode):
            raise ValueError('bounded single-link settings file required')
        return (hashlib.sha256(raw).hexdigest(),info.st_dev,info.st_ino)

    def _unused(self):
        h=self.host;g=h.account_grant
        if (h.attempted or h.owner is not None or h.custody is not None or h.qq.attempted
                or g is None or g.path is not None or g.pending or g.work_state!='idle'
                or h.controller.poll() is not None
                or h.controller._handle is not h.controller._birth_handle):
            raise ValueError('first configuration requires unused exact live HOST')
        h.controller.birth_job.verify()

    def bind_fresh_fence(self):
        if self.state!='prepared':return
        self.inode=self.host.account_fence.created_inode
        if self.inode is None:raise ValueError('fresh first-settings fence not confirmed')
        self.state='fenced'

    def commit(self):
        if self.state=='ineligible':return False  # existing state stays revoked
        if self.state!='fenced':raise ValueError('initial configuration commit unresolved; no retry')
        self.state='pending'
        h=self.host;g=h.account_grant;f=h.account_fence
        self._unused()
        with identity_lock(g.base):
            digest,revision=g._snapshot()  # fixed post-save account intent, not RPC claims
            admission=g.base/'parent-account-admissions'/(digest+'.json')
            if safe_path(h.core.parent,admission).exists():
                raise ValueError('configured account has existing admission; reconciliation required')
            backup=g.store.with_suffix('.json.bak')
            allowed={backup.name} if self.previous is not None else set()
            if any(x.name.startswith(g.store.name+'.') and x.name not in allowed for x in g.store.parent.iterdir()):
                raise ValueError('historic recovery metadata blocks first settings commit')
            store_pin=self._pin(g.store);backup_pin=self._pin(backup)
            if store_pin is None:raise ValueError('settings publication missing')
            if self.previous is not None:
                # Actual guarded save must preserve the exact known before-image.
                if backup_pin is None or backup_pin[0]!=self.previous.store_pin[0]:
                    raise ValueError('known settings before-image backup unconfirmed')
            elif backup_pin is not None:raise ValueError('foreign first settings backup')
            p=safe_path(h.core.parent,f.path)
            with p.open('rb') as stream:
                raw=stream.read(1025);info=os.fstat(stream.fileno())
            if (raw!=f.raw or (info.st_dev,info.st_ino)!=self.inode
                    or info.st_nlink!=1 or not stat.S_ISREG(info.st_mode)):
                raise ValueError('first-settings fence changed; not ours to retire')
            self._unused()
            if self._pin(g.store)!=store_pin or self._pin(backup)!=backup_pin:
                raise ValueError('settings changed during parent commit')
            self.store_pin,self.backup_pin=store_pin,backup_pin
            p.unlink()  # own fresh settings gate only, never an existing account grant
        self.state='committed'
        return True
