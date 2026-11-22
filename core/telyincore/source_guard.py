"""Reentrant local source lease shared by cooperating writers and index sync.

Stable lock sidecar is never removed. Not a lock for external editors or hostile
path replacement. Plain strict enumeration remains read-only and takes no lease.
"""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import os
import threading
import time
import weakref

# Active and waiting leases keep their lock alive; retired paths need no entry.
_registry = weakref.WeakValueDictionary()
_guard = threading.Lock()
_held = threading.local()


@contextmanager
def lease(home, *, timeout=5.0):
    path = Path(home).resolve()
    key = os.path.normcase(str(path))
    deadline = time.monotonic() + timeout
    with _guard:
        lock = _registry.setdefault(key, threading.RLock())
    if not lock.acquire(timeout=max(0, deadline - time.monotonic())):
        raise TimeoutError('source lease busy')
    owned = getattr(_held, 'keys', None)
    if owned is None:
        owned = _held.keys = set()
    descriptor = None
    acquired = False
    nested = key in owned
    try:
        if nested:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path.with_name(path.name + '.index-source.lock'), os.O_CREAT | os.O_RDWR, 0o600)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b'\0')
        while True:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as error:
                if error.errno not in (11, 13, 35, 36):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError('source lease busy') from error
                time.sleep(min(.01, max(0, deadline - time.monotonic())))
        owned.add(key)
        yield
    finally:
        if not nested:
            owned.discard(key)
            if descriptor is not None:
                # Closing releases the OS lock even after an exception/cancel.
                os.close(descriptor)
        lock.release()


def writer(function):
    @wraps(function)
    def guarded(home, *args, **kwargs):
        with lease(home):
            return function(home, *args, **kwargs)
    return guarded
