"""TEV-PRODUCTION/2: bounded local observation, not business or drain authority.

Caller owns one fresh directory per Bot/run. No pytest import, network, payloads,
automatic replay, rotation, deletion or inference across a process restart.
Synchronous fsync may block in the OS; elapsed limits detect, not interrupt I/O.
"""
import asyncio,hashlib,json,os,sys,threading,time,uuid
import contextvars
import weakref
from contextlib import contextmanager, asynccontextmanager
from pathlib import Path
from collections import deque
from concurrent.futures import Future

_current=contextvars.ContextVar('runtime_evidence_parent',default=None)
_assembly=contextvars.ContextVar('runtime_evidence_assembly',default=None)

class UncoveredToken:
    __slots__=('owner','kind','active','__weakref__')
    def __init__(self,owner,kind):self.owner=owner;self.kind=kind;self.active=True

class EvidenceInput:
    __slots__=('value',)
    def __init__(self,value):self.value=value

def encode(row):return json.dumps(row,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def read(path):
    with path.open('rb') as f:raw=f.read(4097)
    if len(raw)>4096:raise ValueError('evidence frame capacity')
    return raw

class RuntimeEvidence:
    def __init__(self,root,*,max_requests=4096,write_budget=5.0,queue_capacity=64,capacity_policy="reject",_retention_full=False):
        if type(queue_capacity) is not int or not 1<=queue_capacity<=4096:raise ValueError('queue capacity')
        if type(max_requests) is not int or not 1<=max_requests<=100000:raise ValueError('request capacity')
        if type(write_budget) not in (int,float) or not 0<write_budget<=30:raise ValueError('write budget')
        if capacity_policy not in ('reject','continue_uncovered'):raise ValueError('capacity policy')
        self.capacity_policy=capacity_policy;self.coverage_gap=None;self._uncovered_pending=0
        self._partial_start=False
        self._uncovered_issued=weakref.WeakSet()
        self._memory_only=_retention_full
        self.root=Path(root).absolute()
        for p in (self.root,*self.root.parents):
            if p.is_symlink() or (p.exists() and getattr(p.lstat(),'st_file_attributes',0)&0x400):raise ValueError('linked evidence root')
        if not self._memory_only:self.root.mkdir(parents=True,exist_ok=False)
        self.epoch=uuid.uuid4().hex;self.max_requests=max_requests;self.write_budget=write_budget
        self.lock=threading.RLock();self.pending={};self.started=0;self.sequence=0
        self.head='0'*64;self.failed=False;self.accepting=True;self._tokens={}
        self._known={};self._input_parents={};self._event_parents={};self._links=0
        self._async_jobs=set()
        self._work_lock=threading.Lock();self._work=deque();self._worker=None
        self._queue_capacity=queue_capacity
        self._closing=threading.Event()
        if self._memory_only:self.coverage_gap='RETAINED_RUN_CAPACITY'
        else:self._emit('RUN_BEGIN',None,None)

    def _publish(self,path,raw):
        if len(raw)>4096:raise ValueError('evidence frame capacity')
        stage=path.with_name(path.name+'.'+uuid.uuid4().hex+'.pending')
        with stage.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        if read(stage)!=raw:raise ValueError('readback mismatch')
        os.link(stage,path)  # final name is immutable and never partially visible
        stage.unlink()
        if read(path)!=raw:raise ValueError('published readback mismatch')

    def _emit(self,kind,identity,outcome):
        started=time.monotonic();seq=self.sequence+1
        row={'schema':'TEV-PRODUCTION/2','epoch':self.epoch,'sequence':seq,
             'clock_origin_id':self.epoch,'monotonic_ns':time.monotonic_ns(),
             'previous':self.head,'kind':kind,'identity':identity,'outcome':outcome}
        raw=encode(row);digest=sha(raw)
        try:
            self._publish(self.root/('%08d.event.json'%seq),raw)
            self._publish(self.root/('%08d.receipt.json'%seq),encode({'epoch':self.epoch,'sequence':seq,
                'sha256':digest,'scope':'LOCAL_FILE_FSYNC_READBACK_ONLY'}))
            if time.monotonic()-started>self.write_budget:raise TimeoutError('evidence write elapsed budget')
        except BaseException:
            self.failed=True;self.accepting=False;raise
        self.sequence=seq;self.head=digest

    def _is_uncovered(self,token):
        return isinstance(token,UncoveredToken) and token.owner is self and token in self._uncovered_issued

    def _gap(self,reason):
        if self.coverage_gap is None:
            # Reserved constant-size marker; never silently turn storage errors into capacity.
            self._emit('COVERAGE_GAP',None,reason)
            self.coverage_gap=reason

    def begin(self,kind,parents=None):
        if kind not in ('http','tool_sync','tool_async','model_stream','round','queued_input','plugin_work','plugin_delivery','plugin_background','plugin_background_tail','startup'):raise ValueError('fixed boundary kind')
        if parents is None:
            parent=self.capture();parents=() if parent is None else (parent,)
        parents=tuple(parents)
        if len(parents)>64:raise ValueError('parent capacity')
        with self.lock:
            if kind=='plugin_background_tail':self._partial_start=True
            if any(parent not in self._known and not self._is_uncovered(parent) for parent in parents):raise ValueError('foreign evidence parent')
            parents=tuple(dict.fromkeys(parents))
            if self._closing.is_set() or not self.accepting or self.failed:
                raise RuntimeError('runtime evidence not admitting')
            if self.capacity_policy=='continue_uncovered':
                if self.started>=self.max_requests:self._gap('REQUEST_CAPACITY')
                elif self._links+len(parents)>65536:self._gap('RELATION_CAPACITY')
                if self.coverage_gap is not None:
                    self._uncovered_pending+=1
                    token=UncoveredToken(self,kind);self._uncovered_issued.add(token)
                    return token
            if self._links+len(parents)>65536:raise ValueError('relation capacity')
            if self._closing.is_set() or not self.accepting or self.failed or self.started>=self.max_requests:
                raise RuntimeError('runtime evidence not admitting')
            token=object();identity=uuid.uuid4().hex
            self._emit('BEGIN',identity,kind)
            self.started+=1;self.pending[identity]=kind;self._tokens[token]=identity
            self._known[token]=identity
            for parent in parents:
                self._emit('LINK',identity,self._known[parent]);self._links+=1
            return token

    def capture(self):
        current=_current.get()
        return current[1] if current is not None and current[0] is self else None

    def active_kind(self,token):
        with self.lock:
            if self._is_uncovered(token):return token.kind if token.active else None
            identity=self._tokens.get(token)
            return None if identity is None else self.pending.get(identity)

    def enter(self,token):
        with self.lock:
            if token not in self._known and not (self._is_uncovered(token) and token.active):raise ValueError('foreign evidence context')
        return _current.set((self,token))

    def leave(self,context):_current.reset(context)

    def wrap_input(self,value):
        parent=self.capture()
        if parent is None:return value
        with self.lock:
            if self.coverage_gap is not None:return value
            if len(self._input_parents)>=self.max_requests:raise RuntimeError('input correlation capacity')
            token=self.begin('queued_input',parents=(parent,))
            if self._is_uncovered(token):
                self.finish(token,'RETURNED');return value
            wrapper=EvidenceInput(value)
            self._input_parents[id(wrapper)]=(wrapper,token)
            return wrapper

    def unwrap_input(self,value):
        if not isinstance(value,EvidenceInput):return value,None
        with self.lock:
            bound=self._input_parents.get(id(value))
            if bound is None or bound[0] is not value:raise ValueError('foreign or consumed evidence input')
            self.finish(bound[1],'RETURNED')
            del self._input_parents[id(value)]
            return value.value,bound[1]

    def reject_input(self,value):
        if not isinstance(value,EvidenceInput):return
        with self.lock:
            bound=self._input_parents.get(id(value))
            if bound is None or bound[0] is not value:raise ValueError('foreign evidence input')
            self.finish(bound[1],'FAILED')
            del self._input_parents[id(value)]

    def bind_event(self,event,parent):
        if parent is None:return
        with self.lock:
            if self._is_uncovered(parent):return
            if parent not in self._known:raise ValueError('foreign evidence parent')
            if len(self._event_parents)>=self.max_requests:
                if self.capacity_policy=='continue_uncovered':self._gap('EVENT_CORRELATION_CAPACITY');return
                raise RuntimeError('event correlation capacity')
            key=id(event)
            def retired(reference):
                with self.lock:
                    current=self._event_parents.get(key)
                    if current is not None and current[0] is reference:
                        del self._event_parents[key]
            self._event_parents[key]=(weakref.ref(event,retired),parent)

    def parents_of(self,events):
        with self.lock:
            return tuple(dict.fromkeys(bound[1] for event in events
                if (bound:=self._event_parents.get(id(event))) is not None and bound[0]() is event))

    def finish(self,token,outcome):
        if outcome not in ('RETURNED','FAILED','CANCELLED','INTERRUPTED','CONSUMER_CLOSED'):raise ValueError('outcome')
        with self.lock:
            if self._is_uncovered(token):
                if not token.active:raise ValueError('already settled uncovered token')
                token.active=False;self._uncovered_pending-=1;return
            identity=self._tokens.get(token)
            if identity is None:raise ValueError('foreign or already settled token')
            if self.failed:raise RuntimeError('failed evidence owner retained')
            self._emit('END',identity,outcome)
            del self.pending[identity];del self._tokens[token]

    @contextmanager
    def scope(self,kind,*,parents=None):
        token=self.begin(kind,parents)
        context=self.enter(token)
        try:yield
        except BaseException as primary:
            outcome='CONSUMER_CLOSED' if isinstance(primary,GeneratorExit) else 'CANCELLED' if isinstance(primary,asyncio.CancelledError) else 'FAILED' if isinstance(primary,Exception) else 'INTERRUPTED'
            try:self.finish(token,outcome)
            except BaseException:primary.add_note('runtime evidence incomplete; original exception retained')
            raise
        else:self.finish(token,'RETURNED')  # Not a business success or resource-release claim.
        finally:self.leave(context)

    def stop(self):
        self._closing.set()
        if self._async_jobs:return False
        with self.lock:
            self.accepting=False
            if self.pending or self.failed or self._uncovered_pending:return False
            if getattr(self,'_sealed',False):return True
            if not self._memory_only:self._emit('RUN_END',None,None)
            self._sealed=True
            return True

    async def _owned_io(self, function, *args):
        # Keep the observer alive until the thread returns. Repeated cancellation
        # cannot cancel fsync, discard its result, or manufacture a release.
        task=asyncio.wrap_future(self._submit(function,*args))
        cancelled=None
        try:
            while not task.done():
                try:await asyncio.shield(task)
                except asyncio.CancelledError as exc:cancelled=exc
                except BaseException:break
            try:result=task.result()
            except BaseException:
                if cancelled is not None:
                    cancelled.add_note('runtime evidence I/O failed; owner retained')
                    raise cancelled
                raise
            return result,cancelled
        finally:pass  # Worker, not a cancelled observer, owns job retirement.

    def _submit(self,function,*args):
        with self._work_lock:
            if len(self._async_jobs)>=self._queue_capacity:
                raise RuntimeError('evidence queue full; no work submitted')
            future=Future();self._async_jobs.add(future)
            self._work.append((future,function,args))
            if self._worker is None:
                self._worker=threading.Thread(target=self._work_loop,name='runtime-evidence-'+self.epoch,daemon=False)
                try:self._worker.start()
                except BaseException:
                    self._worker=None;self._work.pop();self._async_jobs.remove(future)
                    raise
            return future

    def _work_loop(self):
        # One journal worker, bounded submitted work, no default executor.
        # Exit when idle; never kill a thread which still owns filesystem I/O.
        while True:
            with self._work_lock:
                if not self._work:
                    self._worker=None;return
                future,function,args=self._work.popleft()
            try:result=function(*args)
            except BaseException as exc:
                with self._work_lock:self._async_jobs.remove(future)
                future.set_exception(exc)
            else:
                with self._work_lock:self._async_jobs.remove(future)
                future.set_result(result)

    def worker_state(self):
        # Does not acquire the journal lock (which might be held by slow fsync).
        with self._work_lock:
            return {'outstanding':len(self._async_jobs),'queued':len(self._work),
                    'worker_present':self._worker is not None,'capacity':self._queue_capacity,
                    'closing':self._closing.is_set()}

    @asynccontextmanager
    async def async_scope(self,kind,*,parents=None):
        if parents is None:
            parent=self.capture();parents=() if parent is None else (parent,)
        token,cancelled=await self._owned_io(self.begin,kind,parents)
        context=self.enter(token)
        try:
            if cancelled is not None:raise cancelled
            yield
        except BaseException as primary:
            outcome='CONSUMER_CLOSED' if isinstance(primary,GeneratorExit) else 'CANCELLED' if isinstance(primary,asyncio.CancelledError) else 'FAILED' if isinstance(primary,Exception) else 'INTERRUPTED'
            try:await self._owned_io(self.finish,token,outcome)
            except BaseException:primary.add_note('runtime evidence incomplete; original exception retained')
            raise
        else:
            _,cancelled=await self._owned_io(self.finish,token,'RETURNED')
            if cancelled is not None:raise cancelled
        finally:self.leave(context)

    async def async_stop(self,*,timeout=None):
        if timeout is not None and (type(timeout) not in (int,float) or not 0<=timeout<=30):
            raise ValueError('stop timeout')
        self._closing.set()
        try:future=self._submit(self._stop_without_observer)
        except RuntimeError:return False
        observer=asyncio.wrap_future(future)
        # A timed-out/cancelled caller leaves the accepted job owned and visible.
        observer.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        done,_=await asyncio.wait([observer],timeout=timeout)
        if not done:return False
        return observer.result()

    def _stop_without_observer(self):
        with self.lock:
            self.accepting=False
            if self.pending or self.failed or self._uncovered_pending:return False
            if getattr(self,'_sealed',False):return True
            if not self._memory_only:self._emit('RUN_END',None,None)
            self._sealed=True
            return True

    def inspect(self):
        with self.lock:return {'accepting':self.accepting and not self._closing.is_set(),'pending':len(self.pending),
            'failed':self.failed,'sealed':getattr(self,'_sealed',False) and self.coverage_gap is None and not self._partial_start,
            'coverage_complete':self.coverage_gap is None and not self._partial_start,'coverage_gap':self.coverage_gap,
            'uncovered_pending':self._uncovered_pending}

    def capacity_state(self):
        work=self.worker_state()
        if not self.lock.acquire(blocking=False):
            return {'snapshot':'BUSY','recording_status':'UNCOVERED' if self.coverage_gap is not None else 'UNKNOWN',
                    'coverage_gap':self.coverage_gap,'worker':work,
                    'business_success_proven':False,'resource_retirement_proven':False}
        try:
            remaining=max(0,self.max_requests-self.started)
            status=('UNCOVERED' if self.coverage_gap is not None else 'FAILED' if self.failed else 'PARTIAL_INITIAL_COVERAGE' if self._partial_start else 'SEALED' if getattr(self,'_sealed',False)
                    else 'CLOSING' if self._closing.is_set() else 'CAPACITY_EXHAUSTED' if remaining==0
                    else 'CAPACITY_LOW' if remaining<=max(1,self.max_requests//10) else 'RECORDING')
            return {'snapshot':'AVAILABLE','recording_status':status,'requests_started':self.started,
                    'request_capacity':self.max_requests,'requests_remaining':remaining,
                    'relations_remaining':65536-self._links,'pending':len(self.pending),
                    'buffered_inputs':len(self._input_parents),'worker':work,
                    'policy':self.capacity_policy,'coverage_gap':self.coverage_gap,
                    'uncovered_pending':self._uncovered_pending,'coverage_complete':self.coverage_gap is None and not self._partial_start,
                    'persistent_run_exists':not self._memory_only,
                    'business_success_proven':False,'resource_retirement_proven':False}
        finally:self.lock.release()

def create_for_bot(bot_root,*,max_runs=32,**options):
    """Explicit opt-in, under an existing absolute runtime root, no deletion.

    Caller must establish the actual Bot/root identity before calling. Allocation
    is serialized among cooperating creators; hostile path replacement is not
    prevented by these pathname checks.
    """
    root=Path(bot_root)
    if not root.is_absolute() or not root.is_dir():raise ValueError('explicit existing Bot root required')
    if type(max_runs) is not int or not 1<=max_runs<=1024:raise ValueError('run capacity')
    parent=root/'runtime-evidence'
    for path in (parent,*parent.parents):
        if path.is_symlink() or (path.exists() and getattr(path.lstat(),'st_file_attributes',0)&0x400):
            raise ValueError('linked Bot evidence root')
    parent.mkdir(exist_ok=True)
    lock=parent/'.allocation.lock'
    descriptor=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    identity=os.fstat(descriptor)
    owner=None
    try:
        count=0
        for entry in parent.iterdir():
            if entry.name==lock.name:continue
            count+=1
            if count>=max_runs:
                if options.get('capacity_policy')=='continue_uncovered':
                    owner=RuntimeEvidence(parent/uuid.uuid4().hex,_retention_full=True,**options)
                    owner.bot_root_fingerprint=sha(os.fsencode(os.path.normcase(str(root))))
                    return owner
                raise RuntimeError('retained evidence run capacity; no automatic cleanup')
        owner=RuntimeEvidence(parent/uuid.uuid4().hex,**options)
        owner.bot_root_fingerprint=sha(os.fsencode(os.path.normcase(str(root))))
        return owner
    finally:
        primary=sys.exception()
        try:
            os.close(descriptor)
            current=lock.stat()
            if (current.st_dev,current.st_ino)!=(identity.st_dev,identity.st_ino):
                raise RuntimeError('allocation lock identity changed; retained')
            lock.unlink()
        except BaseException as cleanup:
            if primary is not None:primary.add_note('allocation lock cleanup incomplete')
            else:
                if owner is not None:cleanup.runtime_evidence_owner=owner
                raise

def select_owner(root,supplied,record_runtime,*,stop_timeout=5.0):
    if type(stop_timeout) not in (int,float) or not 0<=stop_timeout<=30:
        raise ValueError('stop timeout')
    if type(record_runtime) is not bool:raise ValueError('record_runtime must be boolean')
    if supplied is not None and record_runtime:raise ValueError('two evidence owners requested')
    return create_for_bot(root,capacity_policy='continue_uncovered') if record_runtime else supplied

class EvidenceIncomplete(RuntimeError):
    def __init__(self,owner):
        super().__init__('runtime evidence incomplete; owner retained')
        self.runtime_evidence_owner=owner

class ObservedRunTools:
    """Observe the exact outer consumer, retaining its original receipt producer."""
    def __init__(self,inner,owner):self._inner=inner;self._owner=owner
    def __getattr__(self,name):return getattr(self._inner,name)
    async def __call__(self,*args,**kwargs):
        async with self._owner.async_scope('tool_async'):
            return await self._inner(*args,**kwargs)

def observe_tools(inner,owner):
    return inner if owner is None else ObservedRunTools(inner,owner)

def observe_generate(inner,owner):
    if inner is None or owner is None:return inner
    from functools import wraps
    @wraps(inner)
    async def observed(*args,**kwargs):
        async with owner.async_scope('model_stream'):
            return await inner(*args,**kwargs)
    return observed

def begin_assembly(lifecycle,root,enabled):
    """After verified Bot lock, before engines/plugins; outer caller owns close."""
    if type(enabled) is not bool:raise ValueError('recording flag')
    if not enabled:return
    if _assembly.get() is not None or 'runtime_evidence_owner' in lifecycle:
        raise RuntimeError('assembly evidence already owned')
    owner=create_for_bot(Path(root).absolute(),capacity_policy='continue_uncovered')
    lifecycle['runtime_evidence_owner']=owner
    lifecycle['runtime_evidence_root']=Path(root).absolute()
    lifecycle['runtime_evidence_context']=_assembly.set(lifecycle)
    lifecycle['runtime_evidence_startup']=owner.begin('startup',parents=())

def finish_assembly(lifecycle,*,primary=None):
    owner=lifecycle.get('runtime_evidence_owner')
    if owner is None:return
    try:
        if _assembly.get() is not lifecycle:raise RuntimeError('assembly owner changed')
        token=lifecycle.get('runtime_evidence_startup')
        if token is not None:
            owner.finish(token,'FAILED' if primary is not None else 'RETURNED')
            lifecycle['runtime_evidence_startup']=None
        if not owner.stop():raise EvidenceIncomplete(owner)
    except BaseException as error:
        error.runtime_evidence_owner=owner
        if primary is None:raise
        primary.runtime_evidence_owner=owner
        primary.add_note('assembly evidence close incomplete; original failure retained')
    finally:
        context=lifecycle.pop('runtime_evidence_context',None)
        if context is not None:_assembly.reset(context)

async def run_recorded(root,enabled,operation):
    """Launcher calls this only after choosing its actual runtime partition."""
    if type(enabled) is not bool:raise ValueError('recording flag')
    assembly=_assembly.get()
    if assembly is not None:
        if not enabled or Path(root).absolute()!=assembly['runtime_evidence_root']:raise ValueError('assembly evidence root/flag mismatch')
        owner=assembly['runtime_evidence_owner']
        token=assembly.get('runtime_evidence_startup')
        if token is not None:
            owner.finish(token,'RETURNED');assembly['runtime_evidence_startup']=None
        return await operation(owner)
    owner=create_for_bot(Path(root).absolute(),capacity_policy='continue_uncovered') if enabled else None
    return await observed_lifecycle(owner,lambda:operation(owner))

async def observed_lifecycle(owner,operation,*,stop_timeout=5.0):
    """Explicit owner transfer for one entry lifetime; never infer resource drain.

    The owner must already be bound to the intended Bot/run by its creator.
    On incomplete close retain it on the propagated exception for outer custody.
    """
    if owner is None:return await operation()
    if type(stop_timeout) not in (int,float) or not 0<=stop_timeout<=30:
        raise ValueError('stop timeout')
    try:result=await operation()
    except BaseException as primary:
        try:
            if not await owner.async_stop(timeout=stop_timeout):
                primary.runtime_evidence_owner=owner
                primary.add_note('runtime evidence incomplete; owner retained')
        except BaseException:
            primary.runtime_evidence_owner=owner
            primary.add_note('runtime evidence close failed; original exception retained')
        raise
    else:
        try:closed=await owner.async_stop(timeout=stop_timeout)
        except BaseException as exc:
            exc.runtime_evidence_owner=owner
            raise
        if not closed:raise EvidenceIncomplete(owner)
        return result

def verify(root,*,max_events=266000):
    """Strict read-only evidence view. Missing terminal records stay incomplete."""
    root=Path(root);head='0'*64;epoch=None;pending=set();seen=set();ended=False;count=0
    for path in (root,*root.parents):
        if path.is_symlink() or (path.exists() and getattr(path.lstat(),'st_file_attributes',0)&0x400):
            raise ValueError('linked evidence root')
    schema=None;links=set();begins={};outcomes={};gap=None;partial_start=False
    names=[]
    for path in root.iterdir():
        if len(names)>=2*max_events:raise ValueError('evidence directory capacity')
        if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400:raise ValueError('linked evidence')
        names.append(path)
    events=sorted(p for p in names if p.name.endswith('.event.json'))
    expected_names=set()
    last_time=-1
    for seq,path in enumerate(events,1):
        if seq>max_events or path.name!='%08d.event.json'%seq:raise ValueError('sequence gap')
        raw=read(path);row=json.loads(raw)
        if encode(row)!=raw or set(row)!={'schema','epoch','sequence','clock_origin_id','monotonic_ns','previous','kind','identity','outcome'}:raise ValueError('event shape')
        if epoch is None:
            epoch=row['epoch']
            schema=row['schema']
            if type(epoch) is not str or len(epoch)!=32 or any(c not in '0123456789abcdef' for c in epoch):raise ValueError('epoch')
        if (schema not in ('TEV-PRODUCTION/1','TEV-PRODUCTION/2') or row['schema']!=schema or row['epoch']!=epoch or row['clock_origin_id']!=epoch
                or type(row['sequence']) is not int or row['sequence']!=seq or row['previous']!=head or type(row['monotonic_ns']) is not int
                or row['monotonic_ns']<last_time or ended):raise ValueError('event binding')
        ackpath=root/('%08d.receipt.json'%seq);ackraw=read(ackpath);ack=json.loads(ackraw)
        if encode(ack)!=ackraw or ack!={'epoch':epoch,'sequence':seq,'sha256':sha(raw),'scope':'LOCAL_FILE_FSYNC_READBACK_ONLY'}:raise ValueError('receipt mismatch')
        kind=row['kind'];identity=row['identity'];outcome=row['outcome']
        if seq==1:
            if kind!='RUN_BEGIN' or identity is not None or outcome is not None:raise ValueError('run begin')
        elif kind=='COVERAGE_GAP':
            if gap is not None or identity is not None or outcome not in ('REQUEST_CAPACITY','RELATION_CAPACITY','EVENT_CORRELATION_CAPACITY'):raise ValueError('coverage gap')
            gap=outcome
        elif kind=='BEGIN':
            if gap is not None:raise ValueError('begin after coverage gap')
            if type(identity) is not str or len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity) or identity in seen or outcome not in ('http','tool_sync','tool_async','model_stream','round','queued_input','plugin_work','plugin_delivery','plugin_background','plugin_background_tail','startup'):raise ValueError('request begin')
            pending.add(identity);seen.add(identity)
            if outcome=='plugin_background_tail':partial_start=True
            begins[identity]=seq
        elif kind=='LINK':
            if (schema!='TEV-PRODUCTION/2' or identity not in pending or outcome not in seen
                    or begins[outcome]>=begins[identity] or (identity,outcome) in links
                    or len(links)>=65536):raise ValueError('invalid causal link')
            links.add((identity,outcome))
        elif kind=='END':
            if identity not in pending or outcome not in ('RETURNED','FAILED','CANCELLED','INTERRUPTED','CONSUMER_CLOSED'):raise ValueError('request end')
            pending.remove(identity)
            outcomes[outcome]=outcomes.get(outcome,0)+1
        elif kind=='RUN_END':
            if pending or identity is not None or outcome is not None:raise ValueError('false completion')
            ended=True
        else:raise ValueError('unexpected event')
        expected_names.update((path.name,ackpath.name));head=sha(raw);last_time=row['monotonic_ns'];count=seq
    if {p.name for p in names}!=expected_names:raise ValueError('uncommitted or foreign evidence retained')
    return {'status':'UNCOVERED' if gap is not None else 'PARTIAL_INITIAL_COVERAGE' if partial_start else 'OBSERVATION_SEALED' if ended else 'INCOMPLETE','coverage_gap':gap,'coverage_complete':gap is None and ended and not partial_start,'events':count,'pending':len(pending),
            'last_event_sha256':head,
            'links':len(links),'outcomes':outcomes,
            'business_success_proven':False,'resource_retirement_proven':False,'epoch':epoch}
