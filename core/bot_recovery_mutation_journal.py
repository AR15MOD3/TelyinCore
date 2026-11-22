"""Append-only cooperative denial before default QQ refresh/load, NOT authority.

No secrets/PID/image/account values, no reset/complete API. Same-object task may
advance refresh -> load only with separate in-memory confirmed response. Persisted
records deny later tasks/objects. File fsync is not directory power-loss/ACL proof.
"""
import hashlib,json,os,re,stat,uuid
from pathlib import Path
from runtime_identity import identity_lock,safe_path
from bot_stable_file import read_stable

HEX32=re.compile(r'[0-9a-f]{32}\Z')
HEX64=re.compile(r'[0-9a-f]{64}\Z')
NAME=re.compile(r'[0-9a-f]{32}\.json\Z')


class MutationJournal:
    def __init__(self,root,store):
        self.root,self.store=Path(root).absolute(),Path(store).absolute()
        safe_path(self.root,self.store)
        self.directory=self.store.with_name(self.store.name+'.process-attempts')
        self.binding=hashlib.sha256(str(self.store.resolve()).encode()).hexdigest()

    def _records(self):
        safe_path(self.root,self.directory)
        if not self.directory.exists():return []
        result=[]
        def unique(pairs):
            out={}
            for k,v in pairs:
                if k in out:raise ValueError('duplicate mutation record field')
                out[k]=v
            return out
        with os.scandir(self.directory) as entries:
            for entry in entries:
                if len(result)>=2 or not NAME.fullmatch(entry.name):raise ValueError('unknown mutation metadata')
                p=safe_path(self.root,self.directory/entry.name)
                raw,_=read_stable(self.root,p,1024)
                row=json.loads(raw,object_pairs_hook=unique)
                if (type(row) is not dict or set(row)!={'version','store','owner','target','operation'}
                        or type(row['version']) is not int or row['version']!=1 or row['store']!=self.binding
                        or type(row['owner']) is not str or not HEX32.fullmatch(row['owner'])
                        or type(row['target']) is not str or not HEX64.fullmatch(row['target'])
                        or row['operation'] not in ('refresh','load')):
                    raise ValueError('invalid mutation record')
                result.append(row)
        return result

    def record(self,owner,target,operation,checkpoint,*,after_observation=False):
        if (type(owner) is not str or not HEX32.fullmatch(owner) or type(target) is not str
                or not HEX64.fullmatch(target) or operation not in ('refresh','load') or not callable(checkpoint) or type(after_observation) is not bool
                or (after_observation and operation!='load')):
            raise ValueError('invalid mutation admission')
        # Lock is derived ONLY from the fixed store path, not a caller-selected
        # identity_base. Two facades for the same store cannot fork this mutex.
        with identity_lock(self.store.parent):
            checkpoint();rows=self._records()
            allowed=(not rows and (operation=='refresh' or after_observation)) or (len(rows)==1 and operation=='load' and not after_observation
                and rows[0]==dict(version=1,store=self.binding,owner=owner,target=target,operation='refresh'))
            if not allowed:raise ValueError('unresolved previous QQ mutation')
            safe_path(self.root,self.directory);self.directory.mkdir(exist_ok=True)
            nonce=uuid.uuid4().hex;temp=self.directory/(nonce+'.pending');final=self.directory/(nonce+'.json')
            payload=(json.dumps(dict(version=1,store=self.binding,owner=owner,target=target,operation=operation),sort_keys=True)+'\n').encode()
            inode=None
            try:
                checkpoint();safe_path(self.root,temp)
                with temp.open('xb') as f:
                    info=os.fstat(f.fileno());inode=(info.st_dev,info.st_ino)
                    f.write(payload);f.flush();os.fsync(f.fileno())
                checkpoint();safe_path(self.root,temp);safe_path(self.root,final)
                info=temp.lstat()
                if (info.st_dev,info.st_ino)!=inode:raise ValueError('mutation temporary changed')
                staged,staged_identity=read_stable(self.root,temp,1024)
                if staged!=payload or staged_identity!=inode:raise ValueError('mutation temporary content changed')
                os.link(temp,final)  # no-clobber atomic publication; no replace fallback
            finally:
                if inode is not None:
                    safe_path(self.root,temp)
                    try:info=temp.lstat()
                    except FileNotFoundError:pass
                    else:
                        if (info.st_dev,info.st_ino)!=inode:raise ValueError('mutation temporary identity changed')
                        temp.unlink()
            checkpoint()
