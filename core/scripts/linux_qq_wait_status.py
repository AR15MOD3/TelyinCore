"""Read-only panel signal for the lifetime of a Linux desktop QQ exit wait."""
from contextlib import contextmanager
import fcntl
import os


def _path(core):
    return core.parent / 'runtime/linux-snowluma/desktop-exit.wait'


@contextmanager
def report_exit_wait(core):
    try:
        fd = os.open(_path(core), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        yield
        return
    try:
        # The existing desktop launcher owns the single wait. Process exit
        # releases the lock even after an interrupted shutdown.
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            pass
        yield
    finally:
        os.close(fd)


def waiting_for_exit(core):
    try:
        with os.fdopen(os.open(_path(core), os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
    except OSError:
        pass
    return False
