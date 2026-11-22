"""Private inherited OS-event cancellation; NOT offline/flush/host authority.

No public listener, PID lookup, argv/env/file secret or named discovery path.
Only a trusted spawn's receiver can observe this particular inherited event.
Cooperative cancellation may wait indefinitely for application cleanup. The
parent retains its own process handle and uses a separate bounded join/retire.
"""
import asyncio
import inspect
import sys
from multiprocessing.synchronize import Event as ProcessEvent


class _PipeSignal:
    """One inherited anonymous stdin pipe; bounded nonblocking poll, no thread.

    The byte is a command, not a secret. OS pipe inheritance is the capability.
    EOF/invalid input is unavailable, never an acknowledgement or offline proof.
    """
    def __init__(self, fd):
        import os
        import stat
        if type(fd) is not int or fd < 0:
            raise ValueError('private launcher pipe required')
        self.fd, self.seen = fd, False
        if os.name == 'nt':
            import ctypes
            import msvcrt
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetFileType.argtypes = [wintypes.HANDLE]
            kernel.GetFileType.restype = wintypes.DWORD
            kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            kernel.PeekNamedPipe.restype = wintypes.BOOL
            handle = msvcrt.get_osfhandle(fd)
            if kernel.GetFileType(handle) != 3:
                raise ValueError('inherited pipe stdin required')
            self.kernel, self.handle = kernel, handle
        else:
            if not stat.S_ISFIFO(os.fstat(fd).st_mode):
                raise ValueError('inherited pipe stdin required')
            os.set_blocking(fd, False)

    def is_set(self):
        import os
        if self.seen:
            return True
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            available = wintypes.DWORD()
            if not self.kernel.PeekNamedPipe(self.handle, None, 0, None, ctypes.byref(available), None):
                raise ValueError('private launcher pipe unavailable')
            if available.value == 0:
                return False
            if available.value != 1:
                raise ValueError('invalid private launcher frame')
        try:
            value = os.read(self.fd, 2)
        except BlockingIOError:
            return False
        if value != b'Q':
            raise ValueError('private launcher pipe closed or invalid')
        self.seen = True
        return True


class ShutdownReceiver:
    """One controller invocation, owned async tasks, no detached stop thread.

    In-process Python and the trusted child can manipulate their own capability;
    this is not a sandbox or authentication against a hostile controller.
    """
    def __init__(self, event):
        if type(event) not in (ProcessEvent, _PipeSignal):
            raise ValueError('private inherited process event required')
        self._event = event
        self._admitted = self._started = self._closed = False
        self.request_observed = False
        self._stop_request = None

    def admit(self):
        # launch.main calls BEFORE filesystem/configuration or plugin work.
        if self._admitted or self._closed:
            raise ValueError('shutdown receiver is single use')
        self._admitted = True

    def bind_stop_request(self, request):
        """Bind the live application's phase-one cutoff, never a completion receipt."""
        if (not self._admitted or not self._started or self._closed or self._stop_request is not None
                or not callable(request) or inspect.iscoroutinefunction(request)):
            raise ValueError('one active synchronous stop request binding required')
        self._stop_request = request

    def close(self):
        self._stop_request = None
        self._closed = True  # no signal, acknowledgement, kill or retirement

    async def _wait(self):
        while True:
            if self._closed:
                raise ValueError('shutdown receiver closed')
            if self._event.is_set():
                return
            await asyncio.sleep(0.05)

    async def run(self, factory):
        if (not self._admitted or self._started or self._closed
                or not inspect.iscoroutinefunction(factory)):
            raise ValueError('admitted async controller entry required')
        self._started = True
        work = monitor = None
        try:
            if self._event.is_set():
                self.request_observed = True
                return 130  # request, not successful application completion
            work = asyncio.create_task(factory())
            monitor = asyncio.create_task(self._wait())
            done, _ = await asyncio.wait((work, monitor), return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                return await work  # preserve natural return/error/cancellation
            await monitor  # event failure is NOT a shutdown acknowledgement
            self.request_observed = True
            if self._stop_request is not None:
                self._stop_request()  # initiate only; work/finally owns actual drain
                return await work  # preserve its real result, failure or outer cancellation
            cancellation = object()  # identify ONLY this request's cancellation
            work.cancel(cancellation)
            try:
                await work
            except asyncio.CancelledError as error:
                if (asyncio.current_task().cancelling()
                        or len(error.args) != 1 or error.args[0] is not cancellation):
                    raise  # external/cleanup cancellation is not our acknowledgement
            # A coroutine which absorbs cancellation still does not prove flush.
            return 130
        finally:
            # Drain only OUR tasks, including on outer cancellation. No global
            # task enumeration, service.stop thread, timeout lie or forced kill.
            owned = [task for task in (work, monitor) if task is not None]
            for task in owned:
                if not task.done():
                    task.cancel()
            primary = sys.exception()
            try:
                results = await asyncio.gather(*owned, return_exceptions=True) if owned else []
                for result in results:
                    if (isinstance(result, BaseException)
                            and not isinstance(result, asyncio.CancelledError)
                            and result is not primary):
                        if primary is None:
                            raise result
                        primary.add_note('owned shutdown cleanup failure: ' + type(result).__name__)
            finally:
                self._closed = True
                self._stop_request = None
