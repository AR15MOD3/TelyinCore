"""Cooperative formal-entry lifetime leases and exclusive maintenance HOST.

No service actions, PID adoption, global daemon, account merging or automatic
unfence. Failed births keep durable owner records. Arbitrary trusted Python and
older unpatched installations are outside this admission protocol.
"""
from contextvars import ContextVar
from pathlib import Path
from threading import Lock
import hashlib,json,os,uuid
from runtime_identity import safe_path,identity_lock
from bot_stable_file import read_stable

ENTRY_FILES=('startup.py','launch.py','bot_launcher_child.py','bot_birth_process.py',
             'startup_terminal.py','bot_global_admission.py','bot_launcher_host.py',
             'bot_host_registry.py','bot_host_inventory.py','bot_offline_lease.py',
             'bot_migration_gate.py','runtime_identity.py')
_current=ContextVar('original_startup_admission',default=None)
_jobs={};_jobs_lock=Lock()
GATE='host-maintenance.json'

def _bounded_entries(folder):
    rows=[]
    for p in folder.iterdir():
        if len(rows)>=1024:raise ValueError('admission owner capacity')
        rows.append(p)
    return rows

def validate_admission_metadata(base, *, held_lock=None):
    """Only classify control sidecars, never adopt a PID or claim retirement."""
    import re
    p=safe_path(base,base/'.host-admission.lock')
    if p.exists():
        if held_lock is None:
            raw,_=read_stable(base,p,1024)
        else:
            # Windows denies reading a byte range through a second handle even
            # when this process owns its exclusive lock. Read the owning handle.
            if (type(held_lock) is not HostAdmissionLock or not held_lock.exclusive
                    or held_lock.core != Path(base).parent.resolve()):
                raise ValueError('original exclusive admission lock required')
            held_lock.check()
            stream=held_lock.stream; position=stream.tell()
            try:
                stream.seek(0); raw=stream.read(1025)
            finally: stream.seek(position)
            if len(raw)>1024: raise ValueError('invalid admission lock sidecar')
        if not raw or set(raw)!={ord('0')}:raise ValueError('invalid admission lock sidecar')
    folder=safe_path(base,base/'host-start-owners')
    if not folder.exists():return
    if not folder.is_dir():raise ValueError('invalid admission owner directory')
    entries=_bounded_entries(folder)
    def unique(pairs):
        out={}
        for key,value in pairs:
            if key in out:raise ValueError('duplicate admission control key')
            out[key]=value
        return out
    for p in entries:
        safe_path(base,p)
        if re.fullmatch('[0-9a-f]{32}\\.json',p.name) is None:raise ValueError('unknown admission owner')
        raw,_=read_stable(base,p,65536);row=json.loads(raw,object_pairs_hook=unique)
        if (type(row) is not dict or set(row)!={'version','owner','entry_sources'}
                or type(row['version']) is not int or row['version']!=1):raise ValueError('invalid admission control schema')
        n=row['owner'];pins=row['entry_sources']
        if (type(n) is not dict or set(n)!={'pid','state','created_filetime','image'}
                or type(n['pid']) is not int or not 0<n['pid']<0xffffffff or n['state']!='alive'
                or type(n['created_filetime']) is not int or n['created_filetime']<=0
                or type(n['image']) is not str or not Path(n['image']).is_absolute()
                or type(pins) is not dict or set(pins)!=set(ENTRY_FILES)
                or any(type(v) is not str or re.fullmatch('[0-9a-f]{64}',v) is None for v in pins.values())):
            raise ValueError('invalid admission owner fields')

def source_pins(core):
    result={}
    for name in ENTRY_FILES:
        p=safe_path(core,core/name);before=p.stat()
        if before.st_size>4*1024*1024 or before.st_nlink!=1:raise ValueError('entry source capacity/identity')
        with p.open('rb') as f:
            raw=f.read(4*1024*1024+1);after=os.fstat(f.fileno())
        signature=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if signature(before)!=signature(after) or signature(after)!=signature(p.stat()):raise ValueError('entry source changed')
        result[name]=hashlib.sha256(raw).hexdigest()
    return result

class HostAdmissionLock:
    """One-byte OS shared/exclusive lock; nonblocking, process-safe on Windows."""
    def __init__(self,core,*,exclusive):
        self.core=Path(core).resolve();self.exclusive=exclusive;self.stream=None;self.overlap=None
    def acquire(self):
        if self.stream is not None:raise ValueError('single lock acquisition required')
        p=safe_path(self.core,self.core/'data/.host-admission.lock')
        if p.exists() and p.stat().st_nlink!=1:raise ValueError('admission lock hardlink refused')
        f=p.open('a+b');self.stream=f
        try:
            if f.tell()==0:f.write(b'0');f.flush()
            f.seek(0)
            if os.name=='nt':
                import ctypes as c,msvcrt
                from ctypes import wintypes as w
                class Overlap(c.Structure):
                    _fields_=[('internal',c.c_size_t),('internal_high',c.c_size_t),('offset',w.DWORD),('offset_high',w.DWORD),('event',w.HANDLE)]
                self.kernel=c.WinDLL('kernel32',use_last_error=True);self.overlap=Overlap()
                self.kernel.LockFileEx.argtypes=[w.HANDLE,w.DWORD,w.DWORD,w.DWORD,w.DWORD,c.POINTER(Overlap)]
                self.kernel.UnlockFileEx.argtypes=[w.HANDLE,w.DWORD,w.DWORD,w.DWORD,c.POINTER(Overlap)]
                self.handle=msvcrt.get_osfhandle(f.fileno())
                if not self.kernel.LockFileEx(self.handle,1|(2 if self.exclusive else 0),0,1,0,c.byref(self.overlap)):
                    raise ValueError('another startup/maintenance admission owns this core')
            else:
                import fcntl
                fcntl.flock(f.fileno(),(fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
            self.identity=(os.fstat(f.fileno()).st_dev,os.fstat(f.fileno()).st_ino)
            self.check();return self
        except BaseException:
            f.close();self.stream=None;raise
    def check(self):
        if self.stream is None or self.stream.closed:raise ValueError('original admission lock lost')
        p=safe_path(self.core,self.core/'data/.host-admission.lock')
        st=p.stat();fd=os.fstat(self.stream.fileno())
        if (st.st_nlink!=1 or fd.st_nlink!=1 or (st.st_dev,st.st_ino)!=(fd.st_dev,fd.st_ino)
                or (fd.st_dev,fd.st_ino)!=self.identity):
            raise ValueError('admission lock file replaced')
    def close(self):
        if self.stream is None:return
        f=self.stream
        try:
            if os.name=='nt':
                import ctypes as c
                if not self.kernel.UnlockFileEx(self.handle,0,1,0,c.byref(self.overlap)):raise OSError('admission unlock unconfirmed')
            else:
                import fcntl
                fcntl.flock(f.fileno(),fcntl.LOCK_UN)
        finally:f.close();self.stream=None

class ControllerExitUnconfirmed(ValueError):
    """Native retirement succeeded, but controller outcome is not successful."""
    def __init__(self, code):
        self.controller_exit_code = code
        self.native_retirement_confirmed = True
        super().__init__('controller exit status unconfirmed after native retirement: ' + str(code))


class StartupAdmission:
    def __init__(self,core):
        self.core=Path(core).resolve();self.lock=HostAdmissionLock(self.core,exclusive=False)
        self.records=[];self.birth_attempted=False;self.token=None;self.owner=None;self.completed=False;self._finish_attempted=False;self._uncontained=False
    def __enter__(self):
        if _current.get() is not None:raise ValueError('nested formal startup admission denied')
        if not (self.core/'data').exists():
            source_pins(self.core)  # only a complete package may initialize runtime data
            safe_path(self.core,self.core/'data').mkdir(exist_ok=True)
        self.lock.acquire()
        try:
            with identity_lock(self.core/'data'):
                if (self.core/'data'/GATE).exists():raise ValueError('exclusive HOST maintenance remains fenced')
                self.pins=source_pins(self.core)
                from bot_host_observer import probe_process
                native=probe_process(os.getpid())
                if native.get('state')!='alive':raise ValueError('original launcher native identity unavailable')
                folder=safe_path(self.core,self.core/'data/host-start-owners');folder.mkdir(exist_ok=True)
                if len(_bounded_entries(folder))>=1024:raise ValueError('startup owner capacity')
                self.owner=folder/(uuid.uuid4().hex+'.json')
                raw=json.dumps({'version':1,'owner':native,'entry_sources':self.pins},sort_keys=True).encode()
                with self.owner.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
                self.owner_snapshot=read_stable(self.core,self.owner,65536)
            self.token=_current.set(self);return self
        except BaseException:self.lock.close();raise
    def before_birth(self):
        self.lock.check()
        if self.completed or self.birth_attempted:raise ValueError('one original native birth per entry required')
        if source_pins(self.core)!=self.pins:raise ValueError('startup source drift')
        self.birth_attempted=True  # partial Popen failures keep owner, even without a returned child
    def register_birth(self,process):
        from bot_birth_process import BirthPopen
        if not self.birth_attempted or self.records or type(process) is not BirthPopen:raise ValueError('original birth process required')
        job=process.birth_job
        if (job is not process._birth_job or job.process is not process or not job.verified
                or job.handle is None or process._handle is not process._birth_handle):raise ValueError('original birth job required')
        record={'entry':self,'process':process,'job':job,'job_handle':job.handle,
                'process_handle':process._handle,'pid':process.pid,'empty':False,'retired':False,'error':None}
        with _jobs_lock:
            if job in _jobs:raise ValueError('birth job already registered')
            _jobs[job]=record;self.records.append(record)
    def finish(self,code):
        if self._finish_attempted:raise ValueError('original admission finish already attempted')
        self._finish_attempted=True
        self.lock.check()
        if self.birth_attempted and (type(code) is not int or not self.records
                or any(not r['retired'] or r['error'] is not None for r in self.records)):
            raise ValueError('original native birth/job responsibility unresolved')
        forced = any(r.get('forced_cleanup', False) for r in self.records)
        if forced and code == 0:code = 1  # cleanup is not successful application shutdown
        if source_pins(self.core)!=self.pins:raise ValueError('startup source drift at closeout')
        with identity_lock(self.core/'data'):
            if read_stable(self.core,self.owner,65536)!=self.owner_snapshot:raise ValueError('original startup owner replaced')
            if self.birth_attempted and code != 0:
                # Native retirement and controller success are different facts.
                # Preserve the failed outcome without keeping a dead subtree active.
                folder=safe_path(self.core,self.core/'data/retired-startup-owners')
                folder.mkdir(exist_ok=True)
                destination=safe_path(self.core,folder/self.owner.name)
                receipt={'version':1,'owner_record':json.loads(self.owner_snapshot[0]),
                         'controller_exit_code':code,'native_retirement_confirmed':True,
                         'forced_cleanup':forced}
                with destination.open('x',encoding='utf-8') as f:
                    json.dump(receipt,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
            self.owner.unlink();self.completed=True
        with _jobs_lock:
            for r in self.records:_jobs.pop(r['job'],None)
        if self.birth_attempted and code != 0:
            error=ControllerExitUnconfirmed(code)
            error._host_admission=self
            raise error  # still an unsuccessful controller outcome, never code 0
    def __exit__(self,kind,error,tb):
        try:
            if not self.completed and not self.birth_attempted and not self._finish_attempted:
                # Argument/selection rejection before any accepted native birth.
                self.finish(None)
            elif (not self.completed and error is not None and not self._finish_attempted
                  and self.birth_attempted and self.records
                  and all(r['retired'] and r['error'] is None for r in self.records)):
                # The original native job has already proved empty and closed.
                # Preserve the application exception, but archive its retired owner.
                try:self.finish(1)
                except ControllerExitUnconfirmed:
                    error._host_admission=self
                    error.add_note('original native subtree retired; failed startup archived')
            elif not self.completed and error is not None:
                error._host_admission=self
                error.add_note('startup owner and original job responsibility retained')
            elif self._uncontained:
                pass  # Preserve legacy return value, never clear its unknown native responsibility.
            elif not self.completed:
                raise ValueError('original startup closeout was not completed')
        except BaseException as cleanup:
            if error is None:raise
            error._host_admission=self;error.add_note('startup admission closeout failed: '+type(cleanup).__name__)
        finally:
            if self.token is not None:_current.reset(self.token)
            self.lock.close()

def before_birth():
    entry=_current.get()
    if entry is None:raise ValueError('formal startup lifetime admission required')
    entry.before_birth()
def run_uncontained(core,call):
    """Shared-root preview/custom factory is not a tracked OS birth subtree.

    Keep its ordinary return/error contract, but retain the durable unknown
    owner. No complete maintenance proof is inferred from Python return alone.
    """
    with StartupAdmission(core) as entry:
        entry.birth_attempted=True;entry._uncontained=True
        return call()
def register_birth(process):
    entry=_current.get()
    if entry is None:raise ValueError('formal startup lifetime admission required')
    entry.register_birth(process)
def before_job_close(job):
    with _jobs_lock:row=_jobs.get(job)
    if row is None:return
    if row['retired']:return
    try:
        p=row['process']
        if (job.handle!=row['job_handle'] or p.birth_job is not job or p._birth_job is not job
                or p._handle is not row['process_handle'] or p.pid!=row['pid'] or job.process is not p
                or job._process_handle is not p._handle or p.poll() is None
                or not job.assigned or not job.verified or job.inspect()['active_processes']!=0):
            raise ValueError('original birth job not independently empty before close')
        row['empty']=True
    except BaseException as error:row['error']=error  # do not hide the original shutdown error or skip existing cleanup
def after_job_close(job):
    with _jobs_lock:row=_jobs.get(job)
    if row is not None and row['error'] is None and row['empty']:
        if job.handle is None and job.thread_handle is None:row['retired']=True
        else:row['error']=ValueError('original native close unconfirmed')

def note_forced_cleanup(job):
    with _jobs_lock:row=_jobs.get(job)
    if row is not None:row['forced_cleanup']=True
    # This flag alone grants nothing. before/after_job_close must independently
    # verify the original handles, root exit, empty native job and successful close.

class MaintenanceAdmission:
    """Exclusive offline-only coverage authority, not a running-Bot broker.

The persistent marker is deliberately retained on every exit. It never becomes
a permission token to reopen starts. No automatic unfence/recovery is provided.
"""
    def __init__(self,core,pins):
        self.core=Path(core).resolve()
        if type(pins) is not dict or set(pins)!=set(ENTRY_FILES):raise ValueError('exact reviewed entry source pins required')
        self.pins=dict(pins);self.lock=HostAdmissionLock(self.core,exclusive=True);self.active=False;self._release_attempted=False
    def _reader_snapshot(self):
        paths=('telyincore/ingress_delivery.py','telyincore/ingress_review.py','tools/ingress_review.py',
               'telyincore/local_observations.py')
        return {name:(read_stable(self.core,self.core/name,65536) if (self.core/name).exists() else None) for name in paths}
    def _scope(self):
        from bot_host_inventory import _scope
        names,baseline,roots,records,hashes=_scope(self.core)
        if records:raise ValueError('instance evidence still present; no PID adoption or cleanup')
        folder=self.core/'data/host-start-owners'
        if folder.exists() and any(folder.iterdir()):raise ValueError('original startup owners unresolved')
        return names,baseline,roots,hashes
    def __enter__(self):
        self.lock.acquire()
        try:
            with identity_lock(self.core/'data'):
                if (self.core/'data'/GATE).exists():raise ValueError('previous maintenance still fenced')
                if source_pins(self.core)!=self.pins:raise ValueError('unapproved entry source bytes')
                self.scope=self._scope();self._probe_runtime_locks();self._unused_readers=self._reader_snapshot()
                p=self.core/'data'/GATE
                with p.open('x',encoding='utf-8') as f:
                    json.dump({'version':1,'session':uuid.uuid4().hex,'state':'fenced'},f);f.flush();os.fsync(f.fileno())
                self.marker=read_stable(self.core,p,65536)
                self.active=True
            return self
        except BaseException:self.lock.close();raise
    def _probe_runtime_locks(self):
        from bot_offline_lease import acquire_runtime_lock,release_runtime_lock
        hs=[]
        try:
            for root in sorted(self.scope[2]):
                h=acquire_runtime_lock(root)
                if h is None:raise ValueError('consumer runtime lock still busy')
                hs.append(h)
        finally:
            for h in reversed(hs):release_runtime_lock(h)
    def check(self):
        if not self.active:raise ValueError('original maintenance authority expired')
        self.lock.check()
        if (source_pins(self.core)!=self.pins or self._scope()!=self.scope
                or read_stable(self.core,self.core/'data'/GATE,65536)!=self.marker):
            raise ValueError('maintenance authority source, scope or fence changed')
    def __call__(self,core):
        # Called inside HostEvidenceRegistry's identity->registry lock order.
        if Path(core).resolve()!=self.core:raise ValueError('maintenance core mismatch')
        self.check();self._probe_runtime_locks()
        return {'core':str(self.core),'bots':self.scope[0],'baseline':self.scope[1],
                'complete':True,'processes':[],'entrypoints_guarded':True}
    def release_unused(self):
        """Explicit coordinator closeout of an UNUSED maintenance window only.

        Never undo a migration fence, reader publication or failed/reused release.
        Automatic context exit deliberately does not call this method.
        """
        if self._release_attempted:raise ValueError('original maintenance release already attempted')
        self._release_attempted=True
        with identity_lock(self.core/'data'):
            self.check();self._probe_runtime_locks()
            if (any((self.core/'data'/name).exists() for name in ('bot-migration-gate.json','bot-new-deployment-gate.json'))
                    or self._reader_snapshot()!=self._unused_readers):
                raise ValueError('used/changed transaction requires phase-aware recovery; fence retained')
            if read_stable(self.core,self.core/'data'/GATE,65536)!=self.marker:raise ValueError('original fence changed')
            (self.core/'data'/GATE).unlink();self.active=False
        # The OS exclusive lease stays held until __exit__, preventing a start
        # between exact marker removal and the original coordinator closeout.
    def __exit__(self,*args):
        self.active=False;self.lock.close()  # marker remains, including success
