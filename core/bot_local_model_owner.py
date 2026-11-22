"""Default Local control: retained native ownership, never listener/PID adoption."""
import asyncio
from telyincore.owned_blocking import owned_thread
import math
from concurrent.futures import Future, TimeoutError as FutureTimeout
from threading import Lock
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit


class LocalModelUnresolved(RuntimeError):
    def __init__(self, owner):
        self.owner = owner  # retain exact Popen and watcher handles for retry
        self.report = owner.snapshot()
        super().__init__(f"local_model_unresolved: {self.report}")


def native_contract(decl):
    if type(decl) is not dict or decl.get('native_argv_v1') is not True:
        raise ValueError('explicit native_argv_v1 declaration required; legacy start is not owned')
    argv = decl.get('argv')
    if (type(argv) is not list or not 1 <= len(argv) <= 64
            or any(type(a) is not str or not a or len(a)>8192 or any(c in a for c in ('\0','\n','\r')) for a in argv)
            or sum(map(len,argv))>24000):
        raise ValueError('bounded native argv required')
    path = Path(argv[0])
    if not path.is_absolute() or str(path).startswith(('\\','//')):
        raise ValueError('absolute local executable required')
    path = path.resolve(strict=True)
    if (str(path).startswith(('\\\\','//')) or not path.is_file() or path.suffix.lower()!='.exe'
            or path.name.lower() in {'cmd.exe','powershell.exe','pwsh.exe','wscript.exe','cscript.exe','mshta.exe','rundll32.exe','regsvr32.exe'}):
        raise ValueError('direct executable required; no shell/start wrapper')
    cwd = Path(decl.get('cwd',str(path.parent)))
    if not cwd.is_absolute() or str(cwd).startswith(('\\','//')):
        raise ValueError('absolute local working directory required')
    cwd = cwd.resolve(strict=True)
    if str(cwd).startswith(('\\\\','//')) or not cwd.is_dir():raise ValueError('working directory missing')
    return (str(path),*argv[1:]),str(cwd)


class LocalModelOwner:
    """One-loop cooperative controller, not an executable sandbox/descendant census.

    HTTP readiness is merely usability. It can never mint native ownership.
    Stop uses only Popen's retained handle, no netstat/taskkill/PID rediscovery.
    """
    def __init__(self,gate,*,url_of,decl_of,probe,target_of):
        self.gate,self.url_of,self.decl_of,self.probe,self.target_of=gate,url_of,decl_of,probe,target_of
        self._closed=False;self._fault=False;self._contract=None
        self._process=None;self._history=[];self._watch=None;self._generation=0
        self._native_owners=[]
        self._terminated=False;self._loop=None;self._controls=set()
        self._request_lock=Lock();self._requests=set();self._request_timeout=4.0

    def _bind(self):
        loop=asyncio.get_running_loop()
        if self._loop is None:self._loop=loop
        elif self._loop is not loop:raise RuntimeError('local model loop changed')
        return loop

    def bind_loop(self):
        if self._closed:raise RuntimeError('local model owner closed')
        self._bind()

    def request_gate(self,closed):
        """Marshal WebService's HTTP worker to the already-bound owning loop.

        A queued timeout cancels before execution. A running timeout is uncertain,
        sticks fault and retains the request/native handles; no blind retry grant.
        """
        if type(closed) is not bool:raise ValueError('boolean gate required')
        with self._request_lock:
            if self._closed:raise RuntimeError('local model owner closed')
        try:current=asyncio.get_running_loop()
        except RuntimeError:current=None
        if current is self._loop and current is not None:return self.set_gate(closed)
        loop=self._loop
        if loop is None or not loop.is_running():raise RuntimeError('local model owner loop unavailable')
        future=Future()
        with self._request_lock:
            if self._closed:raise RuntimeError('local model owner closed')
            if len(self._requests)>=8:raise RuntimeError('local model control request capacity')
            self._requests.add(future)
        def retire():
            with self._request_lock:self._requests.discard(future)
        def apply():
            try:
                if not future.set_running_or_notify_cancel():return
                try:task=self.set_gate(closed)
                except BaseException as error:
                    future.set_exception(error);retire()
                else:
                    def settled(done):
                        try:future.set_result(done.result())
                        except BaseException as error:future.set_exception(error)
                        finally:retire()
                    task.add_done_callback(settled)
            except BaseException:
                retire();raise
        try:loop.call_soon_threadsafe(apply)
        except BaseException:
            future.cancel();retire();raise
        try:return future.result(timeout=self._request_timeout)
        except FutureTimeout:
            if future.cancel():retire()  # proven not started; late callback must skip
            else:self._fault=True  # effect may already be in progress, never repair
            raise RuntimeError('local model control timeout; queued cancelled or running unresolved')

    def _invalidate(self):
        self._generation+=1
        if self._watch is not None and not self._watch.done():
            watch = self._watch
            try: current = asyncio.get_running_loop()
            except RuntimeError: current = None
            if current is watch.get_loop(): watch.cancel()
            else: watch.get_loop().call_soon_threadsafe(watch.cancel)
        self.gate['round']=int(self.gate.get('round') or 0)+1
        self.gate['starting']=False

    def seal(self):
        with self._request_lock:
            self._closed=True
            for future in tuple(self._requests):
                if future.cancel():self._requests.discard(future)
        self._invalidate();self.gate['closed']=True

    def snapshot(self):
        with self._request_lock:requests=tuple(self._requests)
        return {'closed':self._closed,'faulted':self._fault,'attempts':len(self._history),
                'own_process':('absent' if self._process is None else 'alive' if self._process.poll() is None else 'exited'),
                'watch_pending':self._watch is not None and not self._watch.done(),
                'control_pending':any(not future.done() for future in (*requests,*self._controls)),
                'complete':False}  # immediate child is NOT whole model/host offline

    def assert_native_retired(self):
        if (not self._native_owners or len(self._native_owners)!=len(self._history)
                or self._process is not self._history[-1]):
            raise RuntimeError('model native history binding unavailable')
        from bot_local_model_process import LocalModelProcess
        for owner,process in zip(self._native_owners,self._history):
            if type(owner) is not LocalModelProcess or owner.process is not process:
                raise RuntimeError('model native history binding changed')
            owner.assert_retired()
        return True

    def _stop_native(self):
        if self._native_owners:
            owner=self._native_owners[-1]
            if owner.process is not self._process or len(self._native_owners)!=len(self._history):
                raise RuntimeError('model native responsibility binding changed')
            owner.stop()
            return True
        if self._process is None:return False
        if self._process.poll() is None:
            if not self._terminated:
                self._process.terminate();self._terminated=True
            self._process.wait(timeout=2)
        return True  # only this retained native child's exit, not descendants

    def _create_process(self,argv,cwd):
        if os.name=='nt':
            from bot_local_model_process import LocalModelProcess
            native=LocalModelProcess()
            self._native_owners.append(native)  # retain even failed native birth
            return native.start(argv,cwd)
        else:
            return subprocess.Popen(list(argv),cwd=cwd,stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True,
                shell=False)

    def set_gate(self,closed):
        if self._closed:raise RuntimeError('local model owner closed')
        if type(closed) is not bool:raise ValueError('boolean gate required')
        loop=self._bind()
        self._controls={task for task in self._controls if not task.done()
                        or task.cancelled() or task.exception() is not None}
        if any(not task.done() for task in self._controls):
            raise LocalModelUnresolved(self)
        self.gate.update(closed=True,starting=not closed,note='Local 控制进行中')
        coroutine=self._set_gate_async(closed)
        try:
            task=loop.create_task(coroutine)
        except BaseException:
            coroutine.close()
            self._fault=True
            self.gate.update(closed=True,starting=False,note='Local 控制任务创建失败；未执行操作')
            raise
        self._controls.add(task)
        def completed(done):
            if done.cancelled() or done.exception() is not None:self._fault=True
        task.add_done_callback(completed)
        return task

    async def _set_gate_async(self,closed):
        if type(closed) is not bool:raise ValueError('boolean gate required')
        if self._closed:raise RuntimeError('local model owner closed')
        self._bind();self._invalidate();self.gate.update(closed=True,note='已关闸；本次 Local 操作尚未确认')
        if closed:
            try:
                stopped=await owned_thread(self._stop_native)
                self.gate['note']=('已关闸；仅本次自有 native child 已退出（非全部后代证明）' if stopped else '已关闸；未持有自有 child，未按端口操作任何服务')
            except BaseException:
                self.gate['note']='已关闸；自有 native child 退出未确认，句柄保留'
                raise
            return
        if self._fault:raise LocalModelUnresolved(self)
        url=self.url_of()
        if url is None:
            self.gate.update(closed=False,note='开闸；在线接口，不涉及 native 进程')
            return
        parsed=urlsplit(url)
        if (parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.username or parsed.password):
            raise ValueError('local model readiness must be plain loopback HTTP')
        decl=self.decl_of()
        ready=(decl or {}).get('ready','/models')
        if type(ready) is not str or not ready.startswith('/') or len(ready)>512 or any(c in ready for c in ('\\','?','#','\r','\n')):
            raise ValueError('bounded readiness path required')
        target=self.target_of(url,decl)
        if await owned_thread(self.probe,target,timeout=.35) is True:
            if self._closed or self._fault:raise LocalModelUnresolved(self)
            self.gate.update(closed=False,starting=False,note='开闸；仅 HTTP readiness，不据此接管服务进程')
            return
        seconds=(decl or {}).get('ready_seconds',20)
        if type(seconds) not in (int,float) or not 0<seconds<=180 or not math.isfinite(seconds):
            raise ValueError('bounded readiness seconds required')
        if self._closed or self._fault:raise LocalModelUnresolved(self)  # do not spawn after seal/timeout
        contract=native_contract(decl)
        if self._contract is not None and self._contract!=contract:
            raise ValueError('native model contract changed; no replacement/adoption')
        if len(self._history)>=32:raise ValueError('native model spawn budget exhausted')
        if self._process is not None and self._process.poll() is None:
            raise ValueError('owned model still alive; no duplicate spawn')
        if self._watch is not None and not self._watch.done():
            raise ValueError('previous readiness task still pending')
        if self._native_owners:
            # An exited launcher can leave live descendants in its original Job.
            # Retire that retained subtree before accepting another generation.
            await owned_thread(self._stop_native)
            if self._closed or self._fault:raise LocalModelUnresolved(self)
        self._contract=contract
        argv,cwd=contract
        try:
            process=self._create_process(argv,cwd)
        except BaseException:
            self._fault=True  # uncertain failed birth cannot be retried/forgotten
            raise
        self._process=process;self._history.append(process);self._terminated=False
        generation=self._generation;mine=self.gate['round'];loop=self._bind()
        self.gate.update(starting=True,note='自有 native child 已创建；等待 HTTP readiness（非归属证明）')
        async def watch():
            deadline=loop.time()+float(seconds)
            try:
                while True:
                    if self._fault:
                        self.gate.update(starting=False,closed=True,note='Local 控制未确认；旧 readiness 不得开闸');return
                    if self._closed or generation!=self._generation or mine!=self.gate.get('round'):return
                    if process.poll() is not None:
                        self.gate.update(starting=False,closed=True,note='自有 native child 已退出；未开闸');return
                    if await owned_thread(self.probe,target,timeout=.35) is True:
                        current_decl=self.decl_of()
                        current_seconds=(current_decl or {}).get('ready_seconds',20)
                        if (self.url_of()!=url or native_contract(current_decl)!=contract
                                or (current_decl or {}).get('ready','/models')!=ready
                                or type(current_seconds) is not type(seconds) or current_seconds!=seconds):
                            self.gate.update(starting=False,closed=True,note='readiness 配置已变化；旧任务不能开闸')
                            return
                        if not self._closed and generation==self._generation and mine==self.gate.get('round'):
                            self.gate.update(starting=False,closed=False,note='开闸；HTTP readiness 与自有 child 分别观察，未证明 listener 归属')
                        return
                    if loop.time()>=deadline:
                        self.gate.update(starting=False,closed=True,note='HTTP readiness 超时；自有 child/句柄仍保留');return
                    await asyncio.sleep(.1)
            except asyncio.CancelledError:raise
            except BaseException:
                self._fault=True;self.gate.update(starting=False,closed=True,note='readiness 失败；所有权未修复')
                raise
        coroutine=watch()
        try:self._watch=asyncio.create_task(coroutine)
        except BaseException:
            coroutine.close();self._fault=True
            raise  # process already retained, outer cleanup still has exact handle

    def finish_sync(self,*,primary=None,allow_stop=True):
        self.seal()
        error=None
        if self._watch is not None and self._watch.done() and not self._watch.cancelled():
            if self._watch.exception() is not None:self._fault=True
        if allow_stop and not self.snapshot()['watch_pending'] and not self.snapshot()['control_pending']:
            try:self._stop_native()
            except BaseException as exc:error=exc
        report=self.snapshot()
        if error is not None or report['faulted'] or report['watch_pending'] or report['control_pending'] or report['own_process']=='alive':
            unresolved=LocalModelUnresolved(self)
            if primary is None or isinstance(primary,asyncio.CancelledError):raise unresolved from (error or primary)
            primary.add_note(str(unresolved))

    async def close(self,*,allow_stop=True):
        self.seal()
        if self._watch is not None:
            if asyncio.current_task() is self._watch:raise LocalModelUnresolved(self)
            await asyncio.wait((self._watch,),timeout=2)
        pending=tuple(task for task in self._controls if not task.done())
        if pending:await asyncio.wait(pending,timeout=2)
        await owned_thread(self.finish_sync,allow_stop=allow_stop)
        return self.snapshot()
