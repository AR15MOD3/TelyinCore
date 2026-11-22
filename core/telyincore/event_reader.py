"""One runtime-owned read-only event snapshot; no authorization is retained.

Observed file identity/size/write/change times and the highwater identity gate
reuse. This shares the existing cooperative source lease and native signature
contract, not a defense against forged filesystem metadata. Query results are
still filtered by the caller's current trusted scope.
"""
from contextlib import closing, contextmanager
import hashlib
import json
import sqlite3
from pathlib import Path
from threading import RLock

from .events import EventError, EventStore
from .file_signature import signature
from .log import LogStore
from .source_guard import lease


def _content_fingerprint(root):
    from .persistent_index import digest_file
    digest=hashlib.sha256()
    with closing(_inventory(root)) as paths:
        for path in paths:
            stamp,content=digest_file(path)
            digest.update(json.dumps((str(path),stamp,content),ensure_ascii=True,separators=(',',':')).encode('ascii'))
            digest.update(b'\n')
    try:highwater=digest_file(root/'cursor-highwater.json')
    except FileNotFoundError:highwater=None
    digest.update(json.dumps(highwater).encode('ascii'))
    return digest.hexdigest()


def _inventory(root):
    try:
        with closing(LogStore(root, create=False).iter_day_files(strict=True)) as paths:
            yield from paths
    except OSError as error:
        # An unreadable/partial inventory must not acquire the empty-directory
        # token or fall back to a reader with the same permissive enumeration.
        raise EventError('event source inventory unavailable; retry query') from error


def _fingerprint(root):
    digest = hashlib.sha256()
    with closing(_inventory(root)) as paths:
        for path in paths:
            row = (str(path), signature(path))
            digest.update(json.dumps(row, ensure_ascii=True, separators=(',', ':')).encode('ascii'))
            digest.update(b'\n')
    try:
        highwater = signature(root / 'cursor-highwater.json')
    except FileNotFoundError:
        highwater = None
    digest.update(json.dumps(highwater).encode('ascii'))
    return digest.digest()


def _observed(root):
    try:
        return _fingerprint(root)
    except OSError:
        # A fresh EventStore preserves its established incomplete-source
        # diagnostics. An unverified inventory must never reuse a cached view.
        return None


class EventReadCache:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self._lock = RLock()
        self._store = None
        self._token = None
        self._borrowed = False
        self._closed = False
        from .persistent_index import identity
        self._image_key = identity('events',self.root)
        self._image_source = None
        self._image_state = None

    def _drop(self):
        self._token = None
        if self._store is not None:
            self._store._by_cursor.close()
        self._store = self._token = None
        self._image_source = self._image_state = None

    def _restore_image(self, snapshot):
        from . import persistent_index as images
        if not images.available(self._image_key):return None
        try:
            if not images.available(self._image_key,inventory=_fingerprint(self.root).hex()):return None
            self._image_source = _content_fingerprint(self.root)
        except (OSError,ValueError):return None
        restored=snapshot.restart_load(self._image_key,self._image_source)
        if restored is not None:self._image_state=snapshot.restart_state()
        return restored

    def _save_image(self):
        try:
            snapshot=self._store._by_cursor
            state=snapshot.restart_state()
            if state==self._image_state:return
            self._image_state=state
            if not snapshot.restart_eligible():return
            with lease(self.root):
                if _observed(self.root)!=self._token:return
                source=self._image_source or _content_fingerprint(self.root)
                if _observed(self.root)!=self._token:return
                self._image_source=source
                snapshot.restart_save(self._image_key,source,self._store._next,self._token.hex())
        except (OSError,ValueError,sqlite3.Error):pass

    @contextmanager
    def borrow(self, *, verify_unchanged=False):
        # Serialize callers through rendering, so another request cannot
        # replace/close a snapshot while it is still in use.
        with self._lock:
            if self._borrowed:
                raise RuntimeError('nested event reader borrow is unsupported')
            failed = True
            try:
                with lease(self.root):
                    before = _observed(self.root)
                    if (self._store is None or before is None or before != self._token
                            or self._closed):
                        self._drop()
                        self._store = EventStore(self.root, read_only=True, _read_image=self._restore_image)
                        after = _observed(self.root)
                        if before is not None and after is not None and before != after:
                            raise EventError('event source changed during read; retry query')
                        self._token = after if before is not None and before == after else None
                if verify_unchanged and self._token is None:
                    raise EventError('event source identity unavailable; retry query')
                self._borrowed = True
                yield self._store
                if verify_unchanged and _observed(self.root) != self._token:
                    raise EventError('event source changed during read; retry query')
                if self._token is not None:self._save_image()
                failed = False
            finally:
                self._borrowed = False
                if failed or self._closed or self._token is None:
                    import sys
                    primary = sys.exception()
                    try:self._drop()
                    except BaseException as cleanup:
                        if primary is None:raise
                        primary.add_note('event reader cleanup failed: ' + type(cleanup).__name__)

    def close(self):
        with self._lock:
            self._closed = True
            # A same-thread close requested during a borrow is deferred to its
            # finally. Other threads wait for the active borrow to finish.
            if not self._borrowed:self._drop()


def finish_event_reader(lifecycle, *, primary=None):
    cache = lifecycle.get('event_read_cache')
    if cache is None:return
    try:
        cache.close()
    except BaseException as error:
        if primary is None:raise
        primary.add_note('event reader shutdown failed: ' + type(error).__name__)
    else:
        lifecycle.pop('event_read_cache', None)
