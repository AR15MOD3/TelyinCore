"""Default launcher's private stdin endpoint. No HTTP/discovery/real config here."""
import sys
from threading import Lock
from bot_controller_shutdown import ShutdownReceiver, _PipeSignal


def child_environment():
    """Explicit non-secret environment for controlled Windows asyncio children.

    SystemRoot is obtained from native OS, not an arbitrary ambient variable.
    Without it Winsock/_overlapped may fail with WinError 10106 on Windows.
    """
    import os
    env = {'PYTHONIOENCODING': 'utf-8'}
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetWindowsDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
        kernel.GetWindowsDirectoryW.restype = wintypes.UINT
        buf = ctypes.create_unicode_buffer(32768)
        length = kernel.GetWindowsDirectoryW(buf, len(buf))
        if not 0 < length < len(buf):
            raise ValueError('native Windows runtime directory unavailable')
        env['SystemRoot'] = buf.value
    return env


class LauncherShutdown:
    """Single private byte on the retained child's ORIGINAL stdin, never a port.

    Trusted caller owns the process; not an arbitrary-PID or sandbox boundary.
    Main uses unbuffered binary Popen pipes, one short frame, no retransmission.
    """
    def __init__(self, process, *, control_stream=None):
        self._separate_control = control_stream is not None
        stream = getattr(process, 'stdin', None) if control_stream is None else control_stream
        if stream is None or stream.closed:
            raise ValueError('owned child stdin unavailable')
        self.process, self.stream = process, stream
        self._retained_stream = stream
        self._attempted = self._closed = False
        self._sending = Lock()

    def request(self):
        if not self._sending.acquire(blocking=False):
            raise ValueError('private launcher shutdown busy')
        try:
            if (self._closed or self._attempted or self.stream is not self._retained_stream
                    or (not self._separate_control and self.process.stdin is not self.stream)
                    or self.stream.closed or self.process.poll() is not None):
                raise ValueError('private launcher shutdown unavailable')
            self._attempted = True
            if self.stream.write(b'Q') != 1:
                raise ValueError('private launcher short write')
            self.stream.flush()
        except BaseException as error:
            if isinstance(error, Exception):
                raise ValueError('private launcher request uncertain; do not retry') from None
            raise
        finally:
            self._sending.release()

    def close(self):
        self._closed = True
        try:
            self.stream.close()
            return True
        except Exception:
            return False  # explicit cleanup failure, NOT an offline proof


def admit_launch_arguments(argv,description,bot):
    from pathlib import Path
    from bot_launcher_options import options_argv
    approved=options_argv(description.get('launch_options'))
    if (len(argv)!=6+len(approved) or argv[6:]!=approved or argv[0]!='--root' or argv[2]!='--bot' or argv[4]!='--port'
            or Path(argv[1]).absolute()!=Path(description['core'])/'data'
            or argv[3]!=bot or not argv[5].isascii() or not argv[5].isdecimal()
            or not 0<int(argv[5])<=65535):
        raise ValueError('private launcher arguments do not match authenticated host')


def main(argv=None):
    # The label is not authority; key/session arrive only on inherited stdin.
    argv=list(sys.argv[1:] if argv is None else argv)
    labels=[value for value in argv if value.startswith('--parent-lifecycle-handle')]
    client=None;control_fd=None
    controls=[v for v in argv if v.startswith('--parent-control-handle')]
    if controls and (len(controls)!=1 or not controls[0].startswith('--parent-control-handle=') or not labels):
        raise ValueError('one separate private console control handle required')
    try:
        if labels:
            if len(labels)!=1 or not labels[0].startswith('--parent-lifecycle-handle='):raise ValueError('one inherited lifecycle handle required')
            label=labels[0].split('=',1)[1]
            if not label.isascii() or not label.isdecimal():raise ValueError('invalid inherited lifecycle handle')
            argv.remove(labels[0])
            if controls:
                from bot_console_control import receive_control_fd
                control_fd=receive_control_fd(controls[0].split('=',1)[1])
                argv.remove(controls[0])
            from bot_launcher_lifecycle import receive
            client,signal,bot=receive(sys.stdin.fileno() if control_fd is None else control_fd,int(label))
            description=client.request('describe')
            admit_launch_arguments(argv,description,bot)
            console=description.get('launch_options',{}).get('console') is True
            if console != (control_fd is not None):
                raise ValueError('console requires authenticated mode and separate private control')
            if description.get('instance_only') is True:
                from bot_instance_publisher import InstancePublisher
                publisher=InstancePublisher(description['core'],description['bot_scope'],bot,client)
                publisher.bind(description['core'],bot)
                bridge=None
            else:
                from bot_lifecycle_entry import LifecycleProcessBridge
                bridge=LifecycleProcessBridge(description['core'],description['bot_scope'],client,metadata_only=True)
                bridge.bind(description['core'],description['bot_scope'])
                publisher=None
        else:
            signal=_PipeSignal(sys.stdin.fileno())
            bridge=None
            publisher=None
        if control_fd is not None:
            from bot_console_control import ConsoleShutdownReceiver
            receiver=ConsoleShutdownReceiver(signal)
        else:receiver=ShutdownReceiver(signal)
        from launch import main as launch_main
        try:
            if publisher is not None:return launch_main(argv,shutdown_receiver=receiver,instance_lifecycle=publisher)
            if bridge is None:return launch_main(argv,shutdown_receiver=receiver)
            return launch_main(argv,shutdown_receiver=receiver,process_lifecycle=bridge)
        finally:receiver.close()
    finally:
        try:
            if client is not None:client.close()
        finally:
            if control_fd is not None:
                import os
                os.close(control_fd)


def cli(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if not any(value.startswith('--parent-lifecycle-handle=') for value in argv):
        raise ValueError('standalone launcher child requires private parent; use startup.py')
    return main(argv)


if __name__ == '__main__':
    raise SystemExit(cli())
