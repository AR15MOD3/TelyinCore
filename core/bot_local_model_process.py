"""Retain one Windows model Job from native birth through confirmed subtree exit.

No PID discovery, listener adoption or whole-host/account offline claim.
"""
import os
import subprocess
import time
from threading import Thread, RLock

from bot_birth_process import BirthPopen


class LocalModelProcess:
    def __init__(self):
        self._lock = RLock()
        self.process = self.job = None
        self._binding = None
        self._drainer = None
        self._drain_error = None
        self._failed = False
        self._retired = False
        self._attempted = False

    def start(self, argv, cwd):
        if self._attempted:
            raise RuntimeError('model birth already attempted')
        self._attempted = True
        try:
            p = BirthPopen(list(argv), cwd=cwd, env=dict(os.environ),
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, close_fds=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            self.process, self.job = p, p.birth_job
            self._binding = (p, self.job, p._handle, self.job.handle, p.stdin, p.stdout)
            # This model contract has no interactive input. Output is discarded
            # in bounded chunks, just as the former DEVNULL implementation.
            p.stdin.close()
            def drain():
                try:
                    while p.stdout.read(65536):
                        pass
                except BaseException as error:
                    self._drain_error = error
            self._drainer = Thread(target=drain, name='owned-model-output', daemon=True)
            self._drainer.start()
            return p
        except BaseException as error:
            # BirthPopen exposes the original Job even on partial creation.
            if self.job is None:
                self.job = getattr(error, 'job', None)
                self.process = getattr(self.job, 'process', None)
            self._failed = True
            raise

    def _original(self, *, retired=False):
        b = self._binding
        if b is None or len(b) != 6:
            raise RuntimeError('model birth binding unavailable')
        p, j, handle, job_handle, stdin, stdout = b
        if (p is not self.process or j is not self.job or type(p) is not BirthPopen
                or p._handle is not handle or p._birth_handle is not handle
                or p.birth_job is not j or p._birth_job is not j
                or j.process is not p or j._process_handle is not handle
                or p.stdin is not stdin or p.stdout is not stdout
                or (j.handle is not None if retired else j.handle != job_handle)):
            raise RuntimeError('original model process/Job binding changed')
        return p, j

    def stop(self):
        with self._lock:
            return self._stop_locked()

    def _stop_locked(self):
        if self._retired:
            self.assert_retired()
            return
        if self._failed:
            raise RuntimeError('model responsibility unresolved')
        try:
            p, j = self._original()
            j.verify()
            import ctypes
            from ctypes import wintypes
            j.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            j.kernel.TerminateJobObject.restype = wintypes.BOOL
            deadline = time.monotonic() + 2
            if j.inspect()['active_processes'] and not j.kernel.TerminateJobObject(j.handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            while j.inspect()['active_processes']:
                if time.monotonic() >= deadline:
                    raise TimeoutError('original model Job drain timed out')
                time.sleep(.01)
            p.wait(timeout=max(.001, deadline-time.monotonic()))
            self._drainer.join(timeout=max(0, deadline-time.monotonic()))
            if self._drainer.is_alive() or self._drain_error is not None:
                raise RuntimeError('model output drain unconfirmed')
            self._original()
            p.stdout.close()
            j.close()  # Only after native accounting confirms the whole Job empty.
            self._retired = True
            self.assert_retired()
        except BaseException:
            self._failed = True
            raise

    def assert_retired(self):
        with self._lock:
            return self._assert_retired_locked()

    def _assert_retired_locked(self):
        if self._failed or not self._retired:
            raise RuntimeError('model subtree retirement unconfirmed')
        p, j = self._original(retired=True)
        if (p.poll() is None or j.thread_handle is not None or self._drainer is None
                or self._drainer.is_alive() or self._drain_error is not None
                or not p.stdin.closed or not p.stdout.closed):
            raise RuntimeError('model subtree retirement binding changed')
        return True
