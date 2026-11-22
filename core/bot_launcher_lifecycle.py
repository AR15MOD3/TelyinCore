"""Private controlled-launcher handoff; not host completeness or production enablement.

Key/session travel only on this retained child's anonymous stdin. The numeric
inherited handle is an endpoint label, never a secret or process authority.
"""
import json
import os
import struct
import time
from multiprocessing import get_context
if os.name=='nt':
    from multiprocessing.connection import PipeConnection
else:
    from multiprocessing.connection import Connection as PipeConnection

MAX_BOOT = 4096


def _decode(raw):
    def unique(pairs):
        out={}
        for key,value in pairs:
            if key in out:raise ValueError('duplicate launcher bootstrap field')
            out[key]=value
        return out
    value=json.loads(raw,object_pairs_hook=unique)
    if (type(value) is not dict or set(value)!={'key','session','bot'}
            or type(value['key']) is not str or len(value['key'])!=64
            or type(value['session']) is not str or len(value['session'])!=32
            or any(c not in '0123456789abcdef' for c in value['key']+value['session'])):
        raise ValueError('invalid private launcher bootstrap')
    from bot_registry import bot_id
    if type(value['bot']) is not str or bot_id(value['bot'])!=value['bot']:
        raise ValueError('invalid private launcher Bot binding')
    return bytes.fromhex(value['key']),value['session'],value['bot']


def receive(fd,handle,timeout=5):
    from bot_controller_shutdown import _PipeSignal
    from bot_lifecycle_channel import LifecycleClient
    if type(handle) is not int or handle<=0 or type(timeout) not in (int,float) or not 0<timeout<=5:
        raise ValueError('invalid inherited launcher endpoint')
    connection=PipeConnection(handle)
    try:
        signal=_PipeSignal(fd)  # reject terminal/file stdin before reading anything
        deadline=time.monotonic()+timeout
        def take(count):
            out=bytearray()
            while len(out)<count:
                if time.monotonic()>=deadline:raise ValueError('private launcher handoff timeout')
                if os.name=='nt':
                    import ctypes
                    from ctypes import wintypes
                    available=wintypes.DWORD()
                    if not signal.kernel.PeekNamedPipe(signal.handle,None,0,None,ctypes.byref(available),None):
                        raise ValueError('private launcher bootstrap unavailable')
                    n=min(count-len(out),available.value)
                    if not n:time.sleep(.005);continue
                else:n=count-len(out)
                try:part=os.read(fd,n)
                except BlockingIOError:time.sleep(.005);continue
                if not part:raise ValueError('private launcher bootstrap EOF')
                out.extend(part)
            return bytes(out)
        size=struct.unpack('!I',take(4))[0]
        if not 0<size<=MAX_BOOT:raise ValueError('private launcher bootstrap size')
        key,session,bot=_decode(take(size))
        return LifecycleClient(connection,key,session,timeout=2),signal,bot
    except BaseException as error:
        try:connection.close()
        except BaseException:error.add_note("inherited launcher endpoint cleanup failed")
        raise


class ParentLauncherChannel:
    def __init__(self,registry,bot,*,instance_only=False,launch_options=None):
        from bot_host_registry import HostEvidenceRegistry
        if type(instance_only) is not bool or type(registry) is not HostEvidenceRegistry or bot not in registry.bots or os.name!='nt':
            raise ValueError('explicit Windows launcher host required')
        from bot_lifecycle_channel import private_channel
        self.registry,self.bot=registry,bot
        from bot_launcher_options import normalize_options
        self.launch_options=normalize_options(launch_options)
        if self.launch_options and not instance_only:raise ValueError('web options require instance-only parent')
        self.instance_only=instance_only
        self.companion_host=None
        self.parent,self.child,self.key,self.session=private_channel(get_context('spawn'))
        self.process=self.broker=None;self.closed=False;self.attempted=False
        self._terminal_host=None

    def spawn_options(self):
        import subprocess
        if self.closed or self.attempted:raise ValueError('launcher channel single use')
        os.set_handle_inheritable(self.child.fileno(),True)
        info=subprocess.STARTUPINFO();info.lpAttributeList={'handle_list':[self.child.fileno()]}
        return str(self.child.fileno()),dict(startupinfo=info,close_fds=True)

    def after_spawn(self):
        if self.child is not None:
            try:os.set_handle_inheritable(self.child.fileno(),False)
            finally:self.child.close()
            self.child=None

    def handoff(self,process,*,control_stream=None):
        if self.closed or self.attempted:raise ValueError('launcher channel unavailable')
        self.attempted=True;self.process=process
        from bot_lifecycle_channel import LifecycleBroker
        stream=process.stdin if control_stream is None else control_stream
        controller=self.registry._alive(process.pid)
        registered=self.registry._processes.get(process.pid)
        if (registered is None or any(registered.get(k)!=v for k,v in controller.items())
                or process.poll() is not None or stream is None or stream.closed):
            raise ValueError("registered owned launcher required before private handoff")
        from bot_birth_process import BirthPopen
        if self.instance_only and type(process) is BirthPopen:
            from bot_launcher_companion import LauncherCompanionHost
            self.companion_host=LauncherCompanionHost(self.registry.core,process,account_bot=self.bot,account_baseline=self.bot==self.registry.baseline)
        self.broker=LifecycleBroker(self.registry,self.bot,controller,self.parent,self.key,self.session,{},timeout=2,fixed_only=True,instance_markers=True,instance_only=self.instance_only,launch_options=self.launch_options,companion_host=self.companion_host)
        raw=json.dumps({'key':self.key.hex(),'session':self.session,'bot':self.bot},separators=(',',':')).encode()
        frame=struct.pack('!I',len(raw))+raw
        try:
            if stream.write(frame)!=len(frame):raise ValueError('private launcher short handoff')
            stream.flush()
        except BaseException:
            self.close();raise

    def tick(self):
        if self.closed:return False  # expired endpoint grants nothing; do not kill controller
        if self.broker is None:raise ValueError('launcher parent channel unavailable')
        if getattr(self,'companion_host',None) is None and not self.broker.clock()<self.broker.deadline:
            self.close();return False  # no renewal, no complete/offline ACK
        try:return self.broker.tick()
        except BaseException:self.close();raise

    def watch_tick(self):
        """Revoke failed RPC capability without terminating the healthy controller.

        The watch still independently checks its original native child. Startup
        admission continues using strict tick(); interrupts remain interrupts.
        """
        if self.closed:return False
        try:
            first_account = getattr(getattr(self, 'companion_host', None), 'first_account', None)
            if first_account is not None:
                first_account.tick()
            return self.tick()
        except Exception as error:
            self.close()  # no reconnect, retry, local fallback or owner release
            failure=getattr(self.broker,'last_failure',{})
            operation=failure.get('operation','unknown')
            kind=failure.get('kind',type(error).__name__)
            self.last_watch_failure=dict(operation=operation,kind=kind)
            print('  [lifecycle] 父级能力通道已撤权：'+operation+'/'+kind
                  +'；等待原控制器退出及责任核账；禁止重复启动或接管。',flush=True)
            return False

    def report_watch_closeout(self, *, exit_code, local_retired, cleanup_failed):
        """Diagnostic only: never grants release, reconnect or remote drain authority."""
        failure = getattr(self, 'last_watch_failure', None)
        if failure is None:
            return None
        consistent = (type(exit_code) is int and exit_code == 0
                      and local_retired is True and cleanup_failed is False
                      and self.closed and not getattr(self, '_cleanup_failed', True))
        state = 'consistent_with_local_shutdown' if consistent else 'unconfirmed_or_abnormal'
        self.last_watch_closeout = dict(state=state, exit_code=exit_code,
            local_retired=local_retired, cleanup_failed=cleanup_failed,
            first_failure=dict(failure), remote_complete=False)
        detail = ('原控制器正常退出且本地登记退役，断连与收尾时序一致（非因果证明）'
                  if consistent else '断连后退出或本地退役未确认，或收尾异常；原失败保留')
        print('  [lifecycle] '+detail+'；不代表账号、模型或全部后代排空。', flush=True)
        return dict(self.last_watch_closeout)

    def wait_companion(self):
        # Called after controller job/instance/output retirement. Keep the actual
        # external HOST alive; closing the RPC endpoint never kills companion.
        if self.companion_host is None:return True
        result=self.companion_host.wait_persistent()
        # The exact retained original object is the source, not its receipt JSON.
        if self.process is not None and self.process.poll() is None:
            raise ValueError('controller still live; no parent local retirement')
        self.companion_host.assert_local_retired()
        self._terminal_host=self.companion_host
        return result

    def assert_local_retired(self):
        from bot_launcher_companion import LauncherCompanionHost
        h=self._terminal_host
        if (not self.closed or getattr(self,'_cleanup_failed',True)
                or type(h) is not LauncherCompanionHost or h is not self.companion_host
                or h.controller is not self.process or h.core!=self.registry.core.resolve()):
            raise ValueError('original parent endpoint/HOST retirement unconfirmed')
        return h.assert_local_retired()

    def retire_instance(self):
        marker = None if self.broker is None else self.broker.instance_marker
        return False if marker is None else marker.retire()

    def close(self):
        self.closed=True;self.registry.revoke_coverage()
        failed=[]
        for endpoint in (self.broker if self.broker is not None else self.parent,self.child):
            if endpoint is None:continue
            try:endpoint.close()
            except BaseException:failed.append(endpoint)
        self._cleanup_failed=failed  # retain failed endpoint references, not an offline ACK
        if not failed:self.child=None
        return not failed
