"""Cooperative configured-account INTENT reservation, never account authority.

No release/reset API. Digests are correlatable (accounts are low entropy), not
anonymization. Normal-file retention only, not hostile-writer/ACL/power-loss proof.
"""
import hashlib,json,os,stat
from pathlib import Path
from bot_plugin_ownership import _account
from runtime_identity import identity_lock,safe_path


def _digest_path(path):
    return hashlib.sha256(os.path.normcase(os.path.normpath(str(path.resolve()))).encode()).hexdigest()


class ConfiguredAccountReservation:
    def __init__(self,root,namespace):
        self.root,self.namespace=Path(root).absolute(),Path(namespace).absolute()
        safe_path(self.root,self.namespace)
        self.directory=self.namespace/'qq-configured-account-reservations'

    def reserve(self,account,store,checkpoint):
        account=_account(account)  # validate before any directory/file creation
        store=Path(store).absolute();safe_path(self.root,store)
        if not callable(checkpoint):raise ValueError('reservation checkpoint required')
        digest=hashlib.sha256(b'configured-qq-account-v1\0'+account.encode('ascii')).hexdigest()
        expected=dict(version=1,scope=_digest_path(self.namespace),account=digest,store=_digest_path(store))
        final=self.directory/(digest+'.json');temp=self.directory/(digest+'.pending')
        def unique(pairs):
            out={}
            for k,v in pairs:
                if k in out:raise ValueError('duplicate reservation metadata')
                out[k]=v
            return out
        with identity_lock(self.namespace):
            checkpoint();safe_path(self.root,self.directory);safe_path(self.root,final);safe_path(self.root,temp)
            if temp.exists():raise ValueError('incomplete account reservation')
            if final.exists():
                with final.open('rb') as f:
                    info=os.fstat(f.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>512:
                        raise ValueError('invalid account reservation file')
                    raw=f.read(513)
                if len(raw)>512:raise ValueError('oversized account reservation')
                row=json.loads(raw,object_pairs_hook=unique)
                if type(row) is not dict or type(row.get('version')) is not int or row!=expected:
                    raise ValueError('conflicting or invalid configured-account reservation')
                checkpoint();return  # same store intent revalidation, NOT login proof
            self.directory.mkdir(exist_ok=True)
            inode=None
            try:
                checkpoint();safe_path(self.root,temp)
                with temp.open('xb') as f:
                    info=os.fstat(f.fileno());inode=(info.st_dev,info.st_ino)
                    f.write((json.dumps(expected,sort_keys=True)+'\n').encode());f.flush();os.fsync(f.fileno())
                checkpoint();safe_path(self.root,temp);safe_path(self.root,final)
                info=temp.lstat()
                if (info.st_dev,info.st_ino)!=inode or info.st_nlink!=1:
                    raise ValueError('reservation temporary changed')
                os.link(temp,final)  # exclusive publication, no overwrite fallback
            finally:
                if inode is not None:
                    safe_path(self.root,temp)
                    try:info=temp.lstat()
                    except FileNotFoundError:pass
                    else:
                        if (info.st_dev,info.st_ino)!=inode:raise ValueError('reservation temporary identity changed')
                        temp.unlink()
            checkpoint()
