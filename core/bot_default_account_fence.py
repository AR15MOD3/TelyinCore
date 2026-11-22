"""Parent-derived default account revocation, never login/offline authority.

No account/config/credentials are accepted from RPC. Fixed Bot scope comes from
HOST bootstrap. Revocation precedes mutation and has no clear/reset API. Checks
are read-only and take no shared identity lock across child RPC callbacks.
"""
from pathlib import Path
import hashlib,json,os,stat
from bot_registry import bot_id
from bot_private_file import open_private_new
from runtime_identity import identity_lock,safe_path


class DefaultAccountFence:
    def __init__(self,core,bot):
        self.core=Path(core).resolve();self.bot=bot_id(bot)
        self.base=self.core/'data'
        name=hashlib.sha256(self.bot.encode()).hexdigest()
        self.path=self.base/'default-account-revocations'/(name+'.json')
        self.created_inode=None  # fresh successful private creation by this revoke only
        self.raw=(json.dumps(dict(version=1,scope=name),sort_keys=True)+'\n').encode()

    def check(self):
        p=safe_path(self.core.parent,self.path)
        if p.exists():raise ValueError('default account scope revoked or pending reconciliation')
        return True  # ONLY no known fence; not authenticated/online/account ownership

    def revoke(self, *, save_evidence=None):
        self.created_inode=None
        with identity_lock(self.base):
            p=safe_path(self.core.parent,self.path)
            if p.exists():
                with p.open('rb') as f:
                    raw=f.read(1025);info=os.fstat(f.fileno())
                if (raw!=self.raw or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1):
                    raise ValueError('default account revocation uncertain')
                return True  # durable denial, never an offline/drain ACK
            if save_evidence is not None:
                row = dict(version=3, scope=self.path.stem, **save_evidence())
                self.raw = (json.dumps(row, sort_keys=True) + '\n').encode()
            p.parent.mkdir(parents=True,exist_ok=True)
            with open_private_new(p) as f:
                f.write(self.raw);f.flush();os.fsync(f.fileno())
                info=os.fstat(f.fileno());self.created_inode=(info.st_dev,info.st_ino)
            return True
