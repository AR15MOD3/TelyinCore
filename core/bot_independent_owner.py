"""Parent-owned companion birth outside an exact retained controller job.

This is a HOST primitive, not a controller capability, default bootstrap or an
account grant. The HOST must remain alive and retain this object until drain.
It does not survive HOST death, reconnect/adopt by PID, or relax either job's
KILL_ON_JOB_CLOSE policy. No configuration, credentials, discovery or network.
"""
import ctypes
from ctypes import wintypes as w
import os
import subprocess
import time
import json
import secrets
import re
import hashlib
from threading import Lock, Thread

from bot_birth_process import BirthPopen
from bot_parent_launch import FixedProcessFactory
from startup_terminal import ChildJobFailure


class IndependentOwnerFailure(RuntimeError):
    def __init__(self, owner, operation):
        self.owner = owner
        super().__init__('independent companion ' + operation + ' unconfirmed')


class IndependentProcessOwner:
    def __init__(self, controller, *, argv, cwd, env, cooperative=False, job_name=None):
        if os.name != 'nt' or type(controller) is not BirthPopen:
            raise ValueError('retained Windows birth controller required')
        if type(cooperative) is not bool:raise ValueError('explicit cooperative policy required')
        self.cooperative = cooperative
        self._job_name = job_name
        self._stop_token = secrets.token_hex(32) if cooperative else None
        self._stop_id = hashlib.sha256(self._stop_token.encode()).hexdigest() if cooperative else None
        if cooperative:
            if any(k.casefold()=='snowluma_host_stop_token' for k in env):
                raise ValueError('HOST owns the private stop token')
            env={**env,'SNOWLUMA_HOST_STOP_TOKEN':self._stop_token}
        self._plan = FixedProcessFactory(argv, cwd, env).plan()
        self.controller = controller
        self.controller_job = controller.birth_job
        self._controller_handle = controller._handle
        self._controller_job_handle = self.controller_job.handle
        self.process = self.job = None
        self._handle = self._job_handle = None
        self.state = 'planned'
        self._busy = Lock()
        self._drainer = None
        self._drain_error = None
        self._drain_attempted = False
        self._stdin = self._stdout = None
        self._stop_requested = False
        self._initial_webui_password = None
        self._stop_ack = False
        self._protocol_error = None
        self.failures = []  # Preserve timed-out/failed attempts after late observation.
        self._check_parent()

    def _member(self, handle, job):
        k = self.controller_job.kernel
        member = w.BOOL()
        if not k.IsProcessInJob(w.HANDLE(int(handle)), job, ctypes.byref(member)):
            raise IndependentOwnerFailure(self, 'job query')
        return bool(member.value)

    def _check_parent(self):
        p, j = self.controller, self.controller_job
        if (p._handle is not self._controller_handle or p.birth_job is not j
                or j is not p._birth_job or j.handle != self._controller_job_handle
                or j.handle is None or p._handle is not p._birth_handle
                or p.poll() is not None or j.process is not p):
            raise IndependentOwnerFailure(self, 'controller binding')
        j.verify()
        k = j.kernel
        k.GetCurrentProcess.restype = w.HANDLE
        if self._member(k.GetCurrentProcess(), j.handle):
            raise IndependentOwnerFailure(self, 'HOST is inside controller job')

    def plan(self):
        return dict(argv=list(self._plan['argv']), cwd=self._plan['cwd'], env=dict(self._plan['env']))

    def _consume(self):
        try:
            tail=b''
            while True:
                stdout = self.process.stdout
                chunk = getattr(stdout, 'read1', stdout.read)(4096)
                if not chunk:break
                if not self.cooperative:continue
                tail+=chunk
                while b'\n' in tail:
                    line,tail=tail.split(b'\n',1)
                    clean = re.sub(rb'\x1b\[[0-9;]*m', b'', line).rstrip(b'\r')
                    match = re.fullmatch(rb'[0-9:.]+ INFO +\[WebUI\] initial credentials: user=admin password=([a-f0-9]{16})', clean)
                    if match and not self._stop_requested:
                        self._initial_webui_password = match[1].decode('ascii')
                    if line.startswith(b'@telyin-stop/1 '):
                        try:
                            if len(line)>512:raise ValueError('stop receipt capacity')
                            receipt=json.loads(line[15:].decode('ascii'))
                            if (set(receipt)!={'version','request','closed'} or type(receipt['version']) is not int
                                    or receipt['version']!=1 or receipt['request']!=self._stop_id
                                    or type(receipt['closed']) is not bool or not receipt['closed']
                                    or not self._stop_requested or self._stop_ack):
                                raise ValueError('stop receipt identity or failure')
                            self._stop_ack=True
                        except Exception as error:self._protocol_error=error
                if len(tail)>4096:tail=tail[-4096:]  # Discard logs, retain bounded framing tail only.
        except BaseException as error:
            self._drain_error = error

    def start(self):
        if not self._busy.acquire(False):
            raise IndependentOwnerFailure(self, 'busy')
        try:
            if self.state != 'planned':
                raise IndependentOwnerFailure(self, 'single-use start')
            self.state = 'starting'  # before any effect, never automatic retry
            try:
                self._check_parent()
                p = BirthPopen(self._plan['argv'], cwd=self._plan['cwd'], env=self._plan['env'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    close_fds=True, shell=False, creationflags=subprocess.CREATE_NO_WINDOW,
                    **({} if self._job_name is None else {'job_name': self._job_name}))
                self.process, self.job = p, p.birth_job  # before fallible validation/IO
                self._handle, self._job_handle = p._handle, self.job.handle
                self._stdin,self._stdout=p.stdin,p.stdout
                if self._member(p._handle, self._controller_job_handle) or not self.job.verify():
                    raise IndependentOwnerFailure(self, 'companion separation')
                if not self.cooperative:p.stdin.close()
                self._drainer = Thread(target=self._consume, name='owned-companion-output', daemon=True)
                self._drainer.start()
                self.state = 'owned'
                return p
            except BaseException as error:
                if isinstance(error, ChildJobFailure):
                    self.job = error.job
                    self.process = self.job.process
                    self._job_handle = self.job.handle
                    if self.process is not None:self._handle = self.process._handle
                self.state = 'pending'
                failure = IndependentOwnerFailure(self, 'start')
                raise failure from error
        finally:
            self._busy.release()

    def inspect(self):
        return dict(state=self.state, retained_process=self.process is not None,
            retained_job=self.job is not None and self.job.handle is not None,
            requires_live_host=self.state != 'retired', complete=False,
            untracked_descendants=True, production_enabled=False,
            cooperative=self.cooperative,stop_requested=self._stop_requested,
            stop_acknowledged=self._stop_ack,retained_failures=len(self.failures))

    def assert_releasable(self):
        # Closing a controller/channel does NOT release this owner or kill Snow.
        if self.state != 'retired':
            raise IndependentOwnerFailure(self, 'HOST lifetime still required')
        return True

    def drain(self, *, timeout=5):
        if type(timeout) not in (int, float) or not 0 < timeout <= 5:
            raise ValueError('bounded companion drain required')
        if self.cooperative:return self._cooperative_drain(timeout=timeout,request=True)
        if not self._busy.acquire(False):
            raise IndependentOwnerFailure(self, 'busy')
        try:
            if self.state == 'retired':return True
            if self.state != 'owned' or self._drain_attempted:
                raise IndependentOwnerFailure(self, 'pending drain; retry denied')
            self._drain_attempted = True
            self.state = 'draining'
            try:
                p, j = self.process, self.job
                if (p._handle is not self._handle or p.birth_job is not j
                        or j is not p._birth_job or j.handle != self._job_handle
                        or j.process is not p):
                    raise IndependentOwnerFailure(self, 'retained companion binding')
                j.verify()
                k = j.kernel
                k.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
                k.TerminateJobObject.restype = w.BOOL
                deadline = time.monotonic() + timeout
                if j.inspect()['active_processes'] and not k.TerminateJobObject(j.handle, 1):
                    raise IndependentOwnerFailure(self, 'job termination')
                while j.inspect()['active_processes']:
                    if time.monotonic() >= deadline:
                        raise IndependentOwnerFailure(self, 'subtree drain')
                    time.sleep(.01)
                p.wait(timeout=max(.001, deadline-time.monotonic()))
                self._drainer.join(timeout=max(0, deadline-time.monotonic()))
                if self._drainer.is_alive() or self._drain_error is not None:
                    raise IndependentOwnerFailure(self, 'output drain')
                p.stdout.close()
                j.close()  # confirmed empty before handle close, not root-only absence
                self.state = 'retired'
                return True
            except BaseException as error:
                self.state = 'pending'
                raise IndependentOwnerFailure(self, 'drain') from error
        finally:
            self._busy.release()

    def _original_resource(self):
        p,j=self.process,self.job
        if (p is None or j is None or p._handle is not self._handle or p._birth_handle is not self._handle
                or p.birth_job is not j or p._birth_job is not j or j.handle!=self._job_handle
                or j.process is not p or p.stdin is not self._stdin or p.stdout is not self._stdout):
            raise IndependentOwnerFailure(self,'original cooperative resource binding')
        j.verify()
        return p,j

    def _cooperative_drain(self,*,timeout,request):
        if not self._busy.acquire(False):raise IndependentOwnerFailure(self,'busy')
        from launcher_exit_log import exit_event
        attempted_here=False
        try:
            if self.state=='retired':return True
            if request and (self.state!='owned' or self._drain_attempted):
                raise IndependentOwnerFailure(self,'cooperative stop attempt already retained')
            if not request and (not self._drain_attempted or not self._stop_requested):
                raise IndependentOwnerFailure(self,'no original cooperative request to observe')
            p,j=self._original_resource();deadline=time.monotonic()+timeout
            if request:
                self._drain_attempted=True;self.state='draining';attempted_here=True
                if p.poll() is not None:raise IndependentOwnerFailure(self,'unrequested process exit')
                self._initial_webui_password = None
                self._stop_requested=True  # Unknown write outcome must never replay.
                frame=(json.dumps({'version':1,'op':'shutdown','nonce':self._stop_token},separators=(',',':'))+'\n').encode('ascii')
                if self._stdin.closed or self._stdin.write(frame)!=len(frame):
                    raise IndependentOwnerFailure(self,'private stop write')
                self._stdin.flush()
                exit_event('snowluma_stop_request_written')
            while True:
                self._original_resource()
                if self._protocol_error is not None:raise self._protocol_error
                if p.poll() is not None and j.inspect()['active_processes']==0:break
                if time.monotonic()>=deadline:raise TimeoutError('original cooperative drain timed out')
                time.sleep(.01)
            if p.poll()!=0:raise IndependentOwnerFailure(self,'cooperative process nonzero exit')
            self._drainer.join(timeout=max(0,deadline-time.monotonic()))
            if self._drainer.is_alive() or self._drain_error is not None:
                raise IndependentOwnerFailure(self,'cooperative output drain')
            if not self._stop_ack or self._protocol_error is not None:
                raise IndependentOwnerFailure(self,'original cooperative completion receipt missing')
            exit_event('snowluma_stop_receipt_verified', exit_code=0)
            self._original_resource()
            self._stdin.close();self._stdout.close();j.close()
            self.state='retired';return True
        except BaseException as error:
            self.state='pending'
            if attempted_here or not self.failures:
                self.failures.append(error)
                exit_event('snowluma_cooperative_drain_failed', error=error,
                           request_written=self._stop_requested, receipt_seen=self._stop_ack,
                           protocol_error=self._protocol_error is not None)
            raise IndependentOwnerFailure(self,'cooperative drain retained') from error
        finally:self._busy.release()

    def observe_cooperative(self,*,timeout=.1):
        if not self.cooperative or type(timeout) not in (int,float) or not 0<timeout<=5:
            raise ValueError('bounded original cooperative observation required')
        return self._cooperative_drain(timeout=timeout,request=False)

    def safe_host_exit(self):
        """Physical safety only. Never settles failure, custody or original ACK."""
        if self.state=='retired':return True
        try:
            if self.state=='planned' and self.process is None and self.job is None:return True
            if (self.process is None and self.job is not None and self.job.handle==self._job_handle
                    and self.job.inspect()['active_processes']==0 and self._drainer is None):return True
            p,j=self._original_resource()
            return (p.poll() is not None and j.inspect()['active_processes']==0
                    and self._drainer is not None and not self._drainer.is_alive())
        except Exception:return False
