"""Bounded persistent management-login intent/local commit, NOT account authority.

Uncertain attempts never clear on cancel, config edits or a new facade. A commit
only means this task stored its returned session and its HTTP client exited; it
is not a QQ login/offline proof. No reset, adoption or remote reconciliation API.
"""
import hashlib,json,os,re,stat,uuid
from pathlib import Path
from runtime_identity import identity_lock,safe_path
from bot_private_file import open_private_new

NAME=re.compile(r'([0-9a-f]{32})\.(intent|committed)\.json\Z')
HEX32=re.compile(r'[0-9a-f]{32}\Z');HEX64=re.compile(r'[0-9a-f]{64}\Z')
MAX_ATTEMPTS=32


class LoginLifecycle:
    def __init__(self,root,store):
        self.root,self.store=Path(root).absolute(),Path(store).absolute()
        safe_path(self.root,self.store)
        self.directory=self.store.with_name(self.store.name+'.login-attempts')
        self.binding=hashlib.sha256(str(self.store.resolve()).encode()).hexdigest()

    def _rows(self):
        safe_path(self.root,self.directory)
        if not self.directory.exists():return {}
        rows={}
        def unique(pairs):
            out={}
            for k,v in pairs:
                if k in out:raise ValueError('duplicate login lifecycle field')
                out[k]=v
            return out
        with os.scandir(self.directory) as entries:
            for entry in entries:
                match=NAME.fullmatch(entry.name)
                if not match or len(rows)>=MAX_ATTEMPTS*2:raise ValueError('unknown/over-limit login lifecycle metadata')
                path=safe_path(self.root,self.directory/entry.name)
                with path.open('rb') as f:info=os.fstat(f.fileno());raw=f.read(1025)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or len(raw)>1024:raise ValueError('invalid login lifecycle file')
                row=json.loads(raw,object_pairs_hook=unique)
                nonce,phase=match.groups()
                if (type(row) is not dict or set(row)!={'version','store','owner','revision','phase'}
                        or type(row['version']) is not int or row['version']!=1 or row['store']!=self.binding
                        or type(row['owner']) is not str or not HEX32.fullmatch(row['owner'])
                        or type(row['revision']) is not str or not HEX64.fullmatch(row['revision'])
                        or row['phase']!=phase):raise ValueError('invalid login lifecycle record')
                rows[(nonce,phase)]=row
        for (nonce,phase),row in rows.items():
            if phase=='committed':
                intent=rows.get((nonce,'intent'))
                if intent is None or dict(row,phase='intent')!=intent:raise ValueError('orphan or mismatched login commit')
        return rows

    def _pending(self,rows):
        return [(nonce,row) for (nonce,phase),row in rows.items() if phase=='intent' and (nonce,'committed') not in rows]

    def check(self,checkpoint):
        with identity_lock(self.store.parent):
            checkpoint();rows=self._rows()
            if self._pending(rows):raise ValueError('unresolved management login')
            checkpoint()
            return len(rows)//2

    def _publish(self,nonce,row,checkpoint):
        safe_path(self.root,self.directory);self.directory.mkdir(exist_ok=True)
        final=safe_path(self.root,self.directory/(nonce+'.'+row['phase']+'.json'))
        temporary=safe_path(self.root,self.directory/(nonce+'.'+row['phase']+'.pending'))
        # Only publish a valid phase AFTER private write+fsync. Partial files,
        # failed fsync/link/unlink remain unknown metadata and deny future use.
        checkpoint()
        with open_private_new(temporary) as f:
            info=os.fstat(f.fileno());identity=(info.st_dev,info.st_ino)
            f.write((json.dumps(row,sort_keys=True)+'\n').encode());f.flush();os.fsync(f.fileno())
        checkpoint();safe_path(self.root,temporary);safe_path(self.root,final)
        info=temporary.lstat()
        if (info.st_dev,info.st_ino)!=identity:raise ValueError('login publication temporary changed')
        os.link(temporary,final)  # exclusive: never replace a competing record
        info=temporary.lstat()
        if (info.st_dev,info.st_ino)!=identity:raise ValueError('login publication temporary changed')
        temporary.unlink()
        checkpoint()

    def begin(self,owner,revision,checkpoint):
        if type(owner) is not str or not HEX32.fullmatch(owner) or type(revision) is not str or not HEX64.fullmatch(revision):raise ValueError('invalid login intent')
        with identity_lock(self.store.parent):
            checkpoint();rows=self._rows()
            if self._pending(rows) or len(rows)//2>=MAX_ATTEMPTS:raise ValueError('login intent unavailable')
            nonce=uuid.uuid4().hex
            self._publish(nonce,dict(version=1,store=self.binding,owner=owner,revision=revision,phase='intent'),checkpoint)
            return nonce

    def commit(self,nonce,owner,revision,checkpoint):
        if type(nonce) is not str or not HEX32.fullmatch(nonce):raise ValueError('invalid login intent reference')
        with identity_lock(self.store.parent):
            checkpoint();rows=self._rows()
            expected=dict(version=1,store=self.binding,owner=owner,revision=revision,phase='intent')
            if self._pending(rows)!=[(nonce,expected)]:raise ValueError('login intent does not match local task')
            self._publish(nonce,dict(expected,phase='committed'),checkpoint)
