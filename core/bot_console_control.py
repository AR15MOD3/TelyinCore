"""Default terminal's own private pipe, separate from original user stdin.

No discovery/PID/HTTP/account capability. Caller owns the spawned process. This
is not a sandbox against trusted in-process Python or complete host coverage.
"""
import os,sys,time
from bot_controller_shutdown import ShutdownReceiver,_PipeSignal


class ConsoleShutdownReceiver(ShutdownReceiver):
    def __init__(self,signal):
        if type(signal) is not _PipeSignal or signal.fd==sys.stdin.fileno():
            raise ValueError('console needs distinct private control pipe')
        super().__init__(signal)


def receive_control_fd(label):
    if os.name!='nt' or not label.isascii() or not label.isdecimal() or int(label)<=0:
        raise ValueError('invalid inherited console control label')
    import msvcrt
    return msvcrt.open_osfhandle(int(label),os.O_RDONLY|os.O_BINARY)


class ConsoleControlPipe:
    def __init__(self):
        if os.name!='nt':raise ValueError('Windows private console pipe required')
        read,write=os.pipe()
        self.read,self.writer=read,None
        try:self.writer=os.fdopen(write,'wb',buffering=0)
        except BaseException:os.close(read);os.close(write);raise
        self._inherited=False

    def inherit(self,options):
        if self.read is None or self._inherited:raise ValueError('console pipe single inheritance')
        import msvcrt
        handle=msvcrt.get_osfhandle(self.read)
        info=options.get('startupinfo')
        if info is None or options.get('close_fds') is not True:
            raise ValueError('private launcher handle list required')
        handles=info.lpAttributeList.get('handle_list')
        if type(handles) is not list or not handles or handle in handles:
            raise ValueError('distinct console control handle required')
        os.set_handle_inheritable(handle,True)
        self._inherited=True;handles.append(handle)
        return str(handle)

    def after_spawn(self):
        if self.read is not None:
            import msvcrt
            try:os.set_handle_inheritable(msvcrt.get_osfhandle(self.read),False)
            finally:os.close(self.read);self.read=None

    def close(self):
        failed=[]
        try:
            try:self.after_spawn()
            except Exception:failed.append('read')
        finally:
            try:
                if self.writer is not None:self.writer.close()
            except Exception:failed.append('writer')
            self._cleanup_failed=failed  # keep read FD/writer for the holder's retry
        return not failed


def watch_console(process,*,shutdown,pump):
    from bot_launcher_child import LauncherShutdown
    if type(shutdown) is not LauncherShutdown or shutdown.process is not process or not callable(pump):
        raise ValueError('matching private console lifetime control required')
    # Parent NEVER reads terminal/user stdin. Q/B/etc remain ordinary user text.
    # Only the parent's Ctrl-C requests private cancellation. No web port needed.
    while process.poll() is None:
        try:pump();time.sleep(.05)
        except ValueError:
            # Peer close can precede native exit. Never repair/reuse trust or
            # infer offline from EOF: bounded wait on the SAME retained child.
            # A still-running/faulty child times out; parent owns final cleanup.
            return process.wait(timeout=8)
        except KeyboardInterrupt:
            result=process.poll()
            if result is not None:return result
            shutdown.request();return process.wait(timeout=8)
    return process.returncode

class ControlCloseFailure(OSError):
    """Retain exact resources and errors; never equate close with retirement."""
    def __init__(self, failures):
        self.failures = tuple(failures)
        self._launcher_failure_phase = 'shutdown'
        super().__init__('private control close unconfirmed')


def collect_control_close_failure(console_control, shutdown, *, primary=None):
    """Attempt both controls once; caller still runs every retirement barrier.

    Return the retained failure for the caller's final exception/exit decision.
    If unwinding a primary error, attach responsibility without replacing it.
    Only literal True confirms these boolean close contracts.
    """
    failures = []
    for name, owner in (('console_control', console_control), ('shutdown', shutdown)):
        if owner is None:
            continue
        try:
            confirmed = owner.close()
        except BaseException as error:
            failures.append((name, owner, error))
        else:
            if confirmed is not True:
                failures.append((name, owner, None))
    if not failures:
        return None
    failure = ControlCloseFailure(failures)
    if primary is not None:
        primary._control_close_failure = failure
        primary.add_note('private control close unconfirmed; original owners retained')
    return failure
