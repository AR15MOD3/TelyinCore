"""Exclusive default Recovery HTTP client lifetime; no account/offline authority.

One private marker per store, before effect. Unknown construction/enter/exit or
failed publication retains denial and the exact returned client. Only this
object's confirmed fresh __exit__ (or never attempted factory) can release it.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib,json,os,stat,sys
import httpx
from bot_private_file import open_private_new
from runtime_identity import identity_lock,safe_path


class RecoveryClientLease:
    def __init__(self,root,store,owner,checkpoint):
        self.root,self.store=Path(root).absolute(),Path(store).absolute()
        safe_path(self.root,self.store)
        if (type(owner) is not str or len(owner)!=32 or any(v not in '0123456789abcdef' for v in owner)
                or not callable(checkpoint)):raise ValueError('explicit HTTP lease owner/checkpoint required')
        self.path=self.store.with_name(self.store.name+'.http-client-active.json')
        self.client=self.entered_client=None;self.attempted=self.exited=self.released=False
        self._checkpoint=checkpoint
        self.raw=(json.dumps(dict(version=1,owner=owner,store=hashlib.sha256(str(self.store.resolve()).encode()).hexdigest()),sort_keys=True)+'\n').encode()
        self._identity=None
        with identity_lock(self.store.parent):
            checkpoint();safe_path(self.root,self.path)
            if self.path.exists():raise ValueError('previous Recovery client lifetime unresolved')
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with open_private_new(self.path) as f:
                info=os.fstat(f.fileno());self._identity=(info.st_dev,info.st_ino)
                f.write(self.raw);f.flush();os.fsync(f.fileno())
            checkpoint()

    @staticmethod
    def check(root,store):
        root,store=Path(root).absolute(),Path(store).absolute()
        with identity_lock(store.parent):
            path=safe_path(root,store.with_name(store.name+'.http-client-active.json'))
            if path.exists():raise ValueError('previous Recovery client lifetime unresolved')

    def _release(self):
        with identity_lock(self.store.parent):
            p=safe_path(self.root,self.path)
            with p.open('rb') as f:raw=f.read(1025);info=os.fstat(f.fileno())
            if (raw!=self.raw or (info.st_dev,info.st_ino)!=self._identity
                    or info.st_nlink!=1 or not stat.S_ISREG(info.st_mode)):
                raise ValueError('Recovery client lease changed; not released')
            p.unlink()  # exact owned marker after resource-close acknowledgement
            self.released=True

    def release_unused(self):
        if self.released:return
        if self.attempted:raise ValueError('attempted client requires its original exit acknowledgement')
        self._release()  # factory never called: no HTTP resource exists to drain

    def _exit(self,exc):
        if self.exited:raise ValueError('Recovery client exit already attempted')
        self.exited=True  # failed close must not be automatically retried/forgotten
        suppressed=self.client.__exit__(*exc)
        if self.client.is_closed is not True:raise ValueError('HTTP client exit not closed')
        self._release()
        return suppressed

    @contextmanager
    def open(self,factory):
        if self.attempted or self.released:raise ValueError('Recovery client lifetime is single use')
        self._checkpoint();self.attempted=True
        try:
            self.client=factory()  # retain BEFORE enter/other fallible calls
            if not isinstance(self.client,httpx.Client):raise ValueError('supported httpx client required')
            entered=self.entered_client=self.client.__enter__()
            if entered is not self.client:raise ValueError('client context identity changed')
        except BaseException as error:
            error._recovery_http_lease=self
            raise
        try:
            yield entered
        except BaseException as primary:
            try:suppressed=self._exit(sys.exc_info())
            except BaseException as cleanup:
                primary._recovery_http_lease=self
                primary.cleanup_errors=tuple(getattr(primary,'cleanup_errors',()))+(cleanup,)
                primary.add_note('Recovery HTTP client drain unconfirmed: '+type(cleanup).__name__)
                raise primary from None
            if not suppressed:raise
        else:
            try:self._exit((None,None,None))
            except BaseException as error:
                error._recovery_http_lease=self
                raise
