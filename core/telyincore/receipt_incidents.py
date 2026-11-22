"""Bounded append-only receipt/release evidence. No bodies, retries or grants."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,re,uuid

REASONS=frozenset(('APPEND_RETURNED_FALSE','APPEND_RAISED','UNACCOUNTED_BATCH_ERROR',
    'RELEASE_FAILURE_RETAINED','RECEIPT_OBSERVER_FAILURE_RETAINED'))

def safe_note(error,text):
    try:BaseException.add_note(error,text)
    except BaseException as secondary:return secondary
    return None

def error_facts(error):
    kinds=[];frames=[];seen=set()
    while isinstance(error,BaseException) and id(error) not in seen and len(kinds)<8:
        seen.add(id(error));kinds.append(type.__getattribute__(type(error),'__name__')[:128])
        tb=BaseException.__traceback__.__get__(error,BaseException)
        while tb is not None and len(frames)<8:
            frames.append({'file_name_sha256':hashlib.sha256(Path(tb.tb_frame.f_code.co_filename).name.encode('utf-8',errors='replace')).hexdigest(),
                'line':tb.tb_lineno})
            tb=tb.tb_next
        cause=BaseException.__cause__.__get__(error,BaseException)
        if cause is None and not BaseException.__suppress_context__.__get__(error,BaseException):
            cause=BaseException.__context__.__get__(error,BaseException)
        error=cause
    return {'error_types':kinds,'frames':frames}

def batch_fingerprint(calls):
    from .stream import ToolCall
    digest=hashlib.sha256();count=0;known=True
    for count,call in enumerate(calls,1):
        for attr in ('call_id','name'):
            value=object.__getattribute__(call,attr) if type(call) is ToolCall else None
            # Do not serialize arbitrary objects or retain identifiers/text.
            if type(value) is not str or len(value)>512:known=False;value=''
            raw=value.encode('utf-8',errors='replace');digest.update(len(raw).to_bytes(4,'big'));digest.update(raw)
    return digest.hexdigest() if known else None,count

class ReceiptFailureJournal:
    def __init__(self,root,*,max_records=1024):
        if type(max_records) is not int or not 1<=max_records<=4096:raise ValueError('bounded receipt evidence capacity required')
        self.root=Path(root).absolute();self.max_records=max_records
        self.scope=hashlib.sha256(os.path.normcase(str(self.root)).encode()).hexdigest()
        self._binding=(self.root,self.max_records,self.scope)
        self._root_identity=None
        self._check()

    def _check(self):
        from runtime_identity import safe_path
        if self.root is not self._binding[0] or (self.max_records,self.scope)!=self._binding[1:]:
            raise ValueError('receipt journal original scope changed')
        path=safe_path(self.root.parent,self.root)
        if path.exists():
            st=path.stat();identity=(st.st_dev,st.st_ino)
            if self._root_identity is not None and identity!=self._root_identity:raise ValueError('receipt journal directory replaced')
            self._root_identity=identity
        return path

    def record(self,reason,*,calls=(),error=None,lane='normal'):
        from runtime_identity import identity_lock,safe_path
        from bot_private_file import open_private_new
        if reason not in REASONS or lane not in ('normal','isolated','ingress'):raise ValueError('fixed receipt evidence vocabulary required')
        batch,count=batch_fingerprint(calls)
        row={'version':1,'scope_sha256':self.scope,'record_id':uuid.uuid4().hex,
            'at':datetime.now(timezone.utc).isoformat(),'reason':reason,'lane':lane,
            'batch_sha256':batch if count else None,'call_count':count,
            **error_facts(error),'retry_allowed':False,'receipt_completed':False,'failure_cleared':False}
        raw=(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n').encode()
        if len(raw)>4096:raise ValueError('receipt evidence byte capacity')
        self._check()
        with identity_lock(self.root):
            files=[]
            for p in self.root.iterdir():
                if p.name=='.bot-identity.lock':continue
                if re.fullmatch('[0-9a-f]{32}\\.json',p.name) is None:raise ValueError('unknown receipt evidence entry')
                files.append(p)
                if len(files)>=self.max_records:raise OSError('receipt evidence capacity exhausted; records retained')
            self._check();path=safe_path(self.root,self.root/(row['record_id']+'.json'))
            with open_private_new(path) as f:
                self._check()
                f.write(raw);f.flush();os.fsync(f.fileno())
            return row['record_id']

    def inspect(self):
        """Read-only; no directory creation, tail repair, retirement or grant."""
        from bot_stable_file import read_stable
        self._check();rows=[]
        if self.root.exists():
            for p in sorted(self.root.iterdir()):
                if p.name=='.bot-identity.lock':continue
                if len(rows)>=self.max_records:raise ValueError('receipt evidence count capacity')
                if re.fullmatch('[0-9a-f]{32}\\.json',p.name) is None:raise ValueError('unknown receipt evidence entry')
                raw,_=read_stable(self.root,p,4096);row=json.loads(raw)
                if type(row) is not dict or row.get('version')!=1 or row.get('scope_sha256')!=self.scope or type(row.get('record_id')) is not str or row['record_id']+'.json'!=p.name:
                    raise ValueError('receipt evidence scope/identity mismatch')
                if row.get('reason') not in REASONS or row.get('retry_allowed') is not False or row.get('failure_cleared') is not False:
                    raise ValueError('invalid receipt evidence disposition')
                rows.append(row)
        return {'records':rows,'release_grants':0,'replayed':0,'deleted':0}
