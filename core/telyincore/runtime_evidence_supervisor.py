"""Explicit Windows process-tree owner for finite, pinned Python entry scripts.

No adoption of existing services. Child code is gated until Job assignment.
This module does not start anything on import. Native job accounting is distinct
from journal sealing and cannot establish application resource retirement.
"""
import ctypes as C
from ctypes import wintypes as W
import hashlib,os,subprocess,sys,threading,time,uuid
from pathlib import Path
from .runtime_evidence_report import observe,commit,read_commit

class BasicLimits(C.Structure):
    _fields_=[('process_time',C.c_int64),('job_time',C.c_int64),('flags',W.DWORD),
              ('min_ws',C.c_size_t),('max_ws',C.c_size_t),('active_limit',W.DWORD),
              ('affinity',C.c_size_t),('priority',W.DWORD),('scheduling',W.DWORD)]
class ExtendedLimits(C.Structure):
    _fields_=[('basic',BasicLimits),('io',C.c_uint64*6),('memory',C.c_size_t*4)]
class Accounting(C.Structure):
    _fields_=[('times',C.c_int64*4),('faults',W.DWORD),('total',W.DWORD),('active',W.DWORD),('terminated',W.DWORD)]

class Job:
    def __init__(self):
        if os.name!='nt':raise OSError('Windows Job Object required')
        self.k=C.WinDLL('kernel32',use_last_error=True)
        signatures={'CreateJobObjectW':([C.c_void_p,W.LPCWSTR],W.HANDLE),
            'SetInformationJobObject':([W.HANDLE,C.c_int,C.c_void_p,W.DWORD],W.BOOL),
            'AssignProcessToJobObject':([W.HANDLE,W.HANDLE],W.BOOL),
            'QueryInformationJobObject':([W.HANDLE,C.c_int,C.c_void_p,W.DWORD,C.c_void_p],W.BOOL),
            'TerminateJobObject':([W.HANDLE,W.UINT],W.BOOL),'CloseHandle':([W.HANDLE],W.BOOL)}
        for name,(arguments,result) in signatures.items():
            function=getattr(self.k,name);function.argtypes=arguments;function.restype=result
        self.handle=self.check(self.k.CreateJobObjectW(None,None))
        limits=ExtendedLimits();limits.basic.flags=0x2000
        try:self.check(self.k.SetInformationJobObject(self.handle,9,C.byref(limits),C.sizeof(limits)))
        except BaseException:self.close();raise
    @staticmethod
    def check(value):
        if not value:raise C.WinError(C.get_last_error())
        return value
    def assign(self,process):self.check(self.k.AssignProcessToJobObject(self.handle,W.HANDLE(int(process._handle))))
    def state(self):
        state=Accounting()
        self.check(self.k.QueryInformationJobObject(self.handle,1,C.byref(state),C.sizeof(state),None))
        return {'total':int(state.total),'active':int(state.active),'terminated':int(state.terminated)}
    def terminate(self):self.check(self.k.TerminateJobObject(self.handle,124))
    def close(self):
        if self.handle:self.check(self.k.CloseHandle(self.handle));self.handle=None

BOOTSTRAP="""import sys
if sys.stdin.readline() != 'GO\\n': raise SystemExit(126)
import hashlib,runpy
from pathlib import Path
script=Path(sys.argv[1]);raw=script.read_bytes()
if hashlib.sha256(raw).hexdigest()!=sys.argv[2]:raise SystemExit(126)
sys.argv=[str(script),*sys.argv[3:]]
exec(compile(raw,str(script),'exec'),{'__name__':'__main__','__file__':str(script)})
"""

def supervise(script,expected_sha256,source,destination,*,arguments=(),execution_seconds=180.0,cleanup_seconds=15.0):
    if os.name!='nt':raise OSError('Windows Job Object required')
    for value in (execution_seconds,cleanup_seconds):
        if type(value) not in (int,float) or not .05<=value<=180:raise ValueError('finite supervisor budget')
    script=Path(script).absolute();source=Path(source).absolute();destination=Path(destination).absolute()
    if script.is_symlink() or getattr(script.lstat(),'st_file_attributes',0)&0x400:raise ValueError('linked script')
    raw=script.read_bytes()
    if len(raw)>1048576 or hashlib.sha256(raw).hexdigest()!=expected_sha256:raise ValueError('script pin')
    if any(type(value) is not str for value in arguments):raise ValueError('script arguments')
    if destination.exists():raise FileExistsError('fresh supervisor report directory required')
    observer_errors=[];process=None;observer=None;job=Job();forced=False;timed_out=False
    start=time.monotonic();primary=None;state={'active':None}
    def watch():
        try:observe(source,destination,main_after=execution_seconds,late_for=cleanup_seconds)
        except BaseException as exc:observer_errors.append(exc)
    try:
        observer=threading.Thread(target=watch,name='runtime-evidence-observer',daemon=False);observer.start()
        environment={k:v for k,v in os.environ.items() if not k.upper().startswith(('PYTHON','PYTEST'))}
        environment.update(PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
        process=subprocess.Popen([sys.executable,'-B','-c',BOOTSTRAP,str(script),expected_sha256,*arguments],
            cwd=script.parent,env=environment,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        job.assign(process)
        process.stdin.write(b'GO\n');process.stdin.flush();process.stdin.close()
        while process.poll() is None:
            if observer_errors:raise RuntimeError('evidence observer failed') from observer_errors[0]
            if time.monotonic()-start>=execution_seconds:
                timed_out=True;forced=True;job.terminate();break
            time.sleep(.01)
        process.wait(timeout=cleanup_seconds)
        deadline=time.monotonic()+cleanup_seconds
        state=job.state()
        while state['active'] and time.monotonic()<deadline:
            time.sleep(.01);state=job.state()
        if state['active']:
            forced=True;job.terminate()
            deadline=time.monotonic()+cleanup_seconds
            while state['active'] and time.monotonic()<deadline:
                time.sleep(.01);state=job.state()
    except BaseException as exc:
        primary=exc
    finally:
        try:
            if process is not None and process.poll() is None:
                forced=True;job.terminate();process.kill();process.wait(timeout=cleanup_seconds)
            state=job.state()
            if state['active']:
                forced=True;job.terminate()
                deadline=time.monotonic()+cleanup_seconds
                while state['active'] and time.monotonic()<deadline:
                    time.sleep(.01);state=job.state()
        except BaseException as cleanup:
            if primary is None:primary=cleanup
            else:primary.add_note('owned process cleanup incomplete')
        finally:
            try:job.close()
            except BaseException as cleanup:
                if primary is None:primary=cleanup
                else:primary.add_note('owned job handle close incomplete')
                primary.runtime_evidence_job=job
        if observer is not None:observer.join()
    if observer_errors:
        if primary is None:primary=observer_errors[0]
        else:primary.add_note('observer report incomplete')
    if not observer_errors:
        try:
            _,main_digest=read_commit(destination,'MAIN');_,late_digest=read_commit(destination,'LATE')
            report={'schema':'TEV-OBSERVATION/1','phase':'PROCESS','clock_origin_id':uuid.uuid4().hex,
                    'script_sha256':expected_sha256,'main_sha256':main_digest,'late_sha256':late_digest,
                    'exit_code':None if process is None else process.returncode,'timed_out':timed_out,
                    'forced_cleanup':forced,'job':state,'owned_process_tree_drained':state['active']==0,
                    'status':'INCOMPLETE' if primary is not None or timed_out or forced or state['active']!=0 or process is None or process.returncode!=0 else 'PROCESS_EXITED',
                    'business_success_proven':False,'resource_retirement_proven':False}
            commit(destination,'PROCESS',report)
        except BaseException as failure:
            if primary is None:primary=failure
            else:primary.add_note('process report commit incomplete')
    if primary is not None:raise primary
    return read_commit(destination,'PROCESS')[0]
