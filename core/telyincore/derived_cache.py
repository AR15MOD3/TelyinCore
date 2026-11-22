"""Private, disposable caches with a lifetime lease and conservative recovery.

Only directories created by this protocol are reclaimed. Original histories,
old unmarked temporary directories and anything with unfamiliar contents stay
untouched. A process lock, rather than age or a reusable PID, proves inactivity.
"""
import os
from collections import OrderedDict
import atexit
from pathlib import Path
import shutil
import stat
import tempfile
import threading
import time
import weakref

from . import cache_registry

_PREFIX = cache_registry.PREFIX
_PROTOCOL_MARKER = b'telyincore disposable cache v1\n'
_FILES = frozenset({'.ready', '.lease', 'snapshot.sqlite3', 'names.sqlite3'} |
    {name + suffix for name in ('snapshot.sqlite3', 'names.sqlite3')
     for suffix in ('-journal', '-wal', '-shm')})
_GUARD = threading.Lock()
_NEXT_SWEEP = 0.0
_REAP_GUARD = threading.Lock()
_REAP_POSITIONS = OrderedDict()
_CACHE_NAME = cache_registry.CACHE_NAME


def _lock(fd):
    if os.name == 'nt':
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _ordinary(path, *, directory=False):
    info = path.lstat()
    if (getattr(info, 'st_file_attributes', 0) & 0x400 or
            (hasattr(os, 'getuid') and info.st_uid != os.getuid())):
        return False
    return (stat.S_ISDIR(info.st_mode) if directory else
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1)


def reap(parent=None):
    """Best-effort recovery of at most 64 recognized caches per sweep.

    The lease is released before removal for Windows compatibility. A cache's
    creator acquires it before publishing .ready and never reacquires it, so an
    inactive directory cannot become live again. Concurrent reapers may race
    only over already abandoned derived data; missing/locked paths are skipped.
    """
    parent = Path(tempfile.gettempdir() if parent is None else parent)
    with _REAP_GUARD:
        _reap_batch(parent)


def _close_scans():
    while _REAP_POSITIONS:
        _, (_, scan) = _REAP_POSITIONS.popitem(last=False)
        scan.close()


atexit.register(_close_scans)


def _next_candidates(parent, limit=64):
    # Resume directory enumeration instead of rescanning/sorting the entire
    # temp directory for every small batch. Both unrelated names and matching
    # candidates consume a bounded per-sweep budget. At most eight directory
    # handles survive between sweeps; exhaustion, replacement, errors and LRU
    # eviction close them. Reopening after exhaustion also sees newly added
    # names that the OS did not include in the previous enumeration.
    key = os.path.normcase(str(parent.absolute()))
    saved = _REAP_POSITIONS.pop(key, None)
    scan = saved[1] if saved else None
    try:
        info = parent.stat()
        identity = (info.st_dev, info.st_ino)
        if saved and saved[0] != identity:
            scan.close()
            scan = None
        resumed = scan is not None
        if scan is None:
            scan = os.scandir(parent)
        names = []
        for _ in range(4096):
            try:
                entry = next(scan)
            except StopIteration:
                scan.close()
                # A previous batch may have stopped exactly at its final name.
                # Allow one fresh pass when that saved iterator was exhausted.
                if resumed and not names:
                    resumed = False
                    scan = os.scandir(parent)
                    continue
                return names
            if _CACHE_NAME.fullmatch(entry.name):
                names.append(entry.name)
                if len(names) == limit:
                    break
        _REAP_POSITIONS[key] = (identity, scan)
        scan = None  # Ownership transferred to the bounded registry.
        while len(_REAP_POSITIONS) > 8:
            _, (_, expired) = _REAP_POSITIONS.popitem(last=False)
            expired.close()
        return names
    finally:
        if scan is not None:
            scan.close()


def _reap_batch(parent):
    forgotten = []
    pending = []
    try:
        registered = cache_registry.candidates(parent)
        # Keep the established combined 64-candidate / 4096-directory-entry
        # budget, reserving at least half for legacy/unregistered discovery.
        try:
            discovered = _next_candidates(parent, 64 - len(registered))
        except OSError:
            discovered = []  # Known registered paths may still be accessible.
        names = registered + discovered
        for name in dict.fromkeys(names):
            path = parent / name
            fd = None
            try:
                if not path.exists() and not path.is_symlink():
                    forgotten.append(name)
                    continue
                if not _ordinary(path, directory=True):
                    continue
                recognized = False
                with os.scandir(path) as children:
                    names = set()
                    for child in children:
                        if child.name not in _FILES or not _ordinary(path / child.name):
                            break
                        names.add(child.name)
                    else:
                        if {'.ready', '.lease'} <= names:
                            with (path / '.ready').open('rb') as mark:
                                if mark.read(len(_PROTOCOL_MARKER) + 1) != _PROTOCOL_MARKER:
                                    continue
                            recognized = True
                if recognized:
                    # A legacy cache may be live or temporarily undeletable.
                    # Remember this validated discovery across process exits;
                    # registration never replaces the lease/content checks.
                    if name not in registered:
                        pending.append(name)
                    fd = os.open(path / '.lease', os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0))
                    _lock(fd)
                    os.close(fd)
                    fd = None
                    shutil.rmtree(path)
                    forgotten.append(name)
            except OSError:
                pass  # Busy, foreign, replaced or unreadable: leave it alone.
            finally:
                if fd is not None:
                    os.close(fd)
    except OSError:
        pass  # Housekeeping must not prevent opening an otherwise usable cache.
    finally:
        # At most64 distinct candidates were examined. Failed hint cleanup is
        # harmless: the next sweep can forget already absent directories.
        cache_registry.forget_many(parent, forgotten)
        cache_registry.register_many(parent, (name for name in pending if name not in forgotten))


def _release(directory, fd):
    # SQLite is closed by the caller before this runs. Close the lease before
    # deleting it because Windows does not unlink an open ordinary file.
    os.close(fd)
    directory.cleanup()
    path = Path(directory.name)
    cache_registry.forget(path.parent, path.name)


class CacheDirectory:
    def __init__(self):
        global _NEXT_SWEEP
        with _GUARD:
            now = time.monotonic()
            if now >= _NEXT_SWEEP:
                reap()
                _NEXT_SWEEP = now + 60
        directory = tempfile.TemporaryDirectory(prefix=_PREFIX)
        fd = None
        try:
            fd = os.open(Path(directory.name) / '.lease', os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.write(fd, b'\0')
            _lock(fd)
            (Path(directory.name) / '.ready').write_bytes(_PROTOCOL_MARKER)
            path = Path(directory.name)
            cache_registry.register(path.parent, path.name)
            from .temp_volumes import observe
            observe(path.parent)
        except BaseException:
            if fd is not None:
                os.close(fd)
            directory.cleanup()
            raise
        self.name = directory.name
        self._cleanup = weakref.finalize(self, _release, directory, fd)

    def cleanup(self):
        self._cleanup()

    def __enter__(self):
        return self.name

    def __exit__(self, *exc):
        self.cleanup()
