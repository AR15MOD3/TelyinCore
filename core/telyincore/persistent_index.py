"""Bounded private restart images, never original data or authorization.

Eight fixed slots bound discovery and storage. Each slot has a nonblocking
process lease, one complete image and at most one pending image. A content
digest, source digest and running-code version gate reuse. Missing, busy,
incompatible, interrupted or damaged images are ordinary cache misses.
"""
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

from .derived_cache import _ordinary, _lock
from .file_signature import signature, descriptor_signature

SLOTS = 8
MAX_BYTES = 32 * 1024 * 1024
MIN_SOURCE_BYTES = 128 * 1024
_MARKER = b'telyincore restart index v1\n'
_APPLICATION = 0x54434931
_FILES = {'.ready', '.lease', 'index.sqlite3', 'manifest.json',
          'pending.sqlite3', 'pending.sqlite3-journal', 'pending.json'}


def location():
    user = str(os.getuid()) if hasattr(os, 'getuid') else 'user'
    return Path(tempfile.gettempdir()) / ('.telyincore-restart-index-v1-' + user)


def identity(kind, path):
    return hashlib.sha256((kind + '\0' + os.path.normcase(str(Path(path).absolute())))
                          .encode('utf-8', 'surrogatepass')).hexdigest()


@lru_cache(maxsize=1)
def version():
    # Parsing, identity, tokenization and schema changes all invalidate images.
    digest = hashlib.sha256(_MARKER + repr((sys.implementation.cache_tag,sys.version_info[:3],
                                         sqlite3.sqlite_version, os.name)).encode())
    root = Path(__file__).parent
    for path in sorted(root.rglob('*.py')):
        digest.update(str(path.relative_to(root)).encode());digest.update(b'\0')
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(65536), b''):digest.update(block)
    return digest.hexdigest()


def digest_file(path, safe=lambda p: None, *, limit=None):
    """Hash exactly a stable observed file with bounded read buffers."""
    safe(path);before = signature(path)
    if limit is not None and before[2] > limit:raise ValueError('image size limit')
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        if descriptor_signature(stream.fileno()) != before:raise OSError('source changed while opening')
        remaining = before[2]
        while remaining:
            block = stream.read(min(65536, remaining))
            if not block:raise OSError('source shortened while hashing')
            digest.update(block);remaining -= len(block)
        if descriptor_signature(stream.fileno()) != before:raise OSError('source changed while hashing')
    safe(path)
    if signature(path) != before:raise OSError('source changed after hashing')
    return before, digest.hexdigest()


def _sync_directory(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)


def _private_directory(path, create):
    if create:
        try:path.mkdir(mode=0o700)
        except FileExistsError:pass
        else:
            with (path / '.ready').open('xb') as stream:
                stream.write(_MARKER);stream.flush();os.fsync(stream.fileno())
            _sync_directory(path)
    if not _ordinary(path, directory=True):return False
    if hasattr(os, 'getuid') and path.stat().st_mode & 0o077:return False
    if not _ordinary(path / '.ready'):return False
    with (path / '.ready').open('rb') as stream:
        return stream.read(len(_MARKER)+1) == _MARKER


@contextmanager
def _slot(key, *, create=False):
    root = location();fd = None
    try:
        if not _private_directory(root, create):yield None;return
        slot = root / str(int(key[:16],16) % SLOTS)
        if not _private_directory(slot, create):yield None;return
        with os.scandir(slot) as children:
            for child in children:
                if child.name not in _FILES or not _ordinary(slot / child.name):yield None;return
        lease = slot / '.lease'
        if not lease.exists():
            if not create:yield None;return
            try:
                fd = os.open(lease, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                os.write(fd,b'\0');os.fsync(fd)
            except FileExistsError:fd = None
        if fd is None:
            if not _ordinary(lease) or lease.stat().st_size != 1:yield None;return
            fd = os.open(lease, os.O_RDWR | getattr(os,'O_NOFOLLOW',0))
        if not os.path.samestat(os.fstat(fd),lease.lstat()):raise OSError('lease identity changed')
        _lock(fd)
        _clear_pending(slot)
        yield slot
    finally:
        if fd is not None:os.close(fd)


def _clear_pending(slot):
    # Caller owns the slot lease. Never remove a complete image, its manifest,
    # or unknown content. Failure to unlink must not mask the primary error.
    for name in ('pending.sqlite3', 'pending.sqlite3-journal', 'pending.json'):
        try:(slot/name).unlink(missing_ok=True)
        except OSError:pass


def _manifest(slot, key):
    path = slot / 'manifest.json'
    if not _ordinary(path) or path.stat().st_size > 16384:return None
    with path.open('rb') as stream:raw = stream.read(16385)
    if len(raw)>16384:return None
    value=json.loads(raw)
    if not isinstance(value,dict):return None
    integrity=value.pop('integrity',None)
    if integrity!=_integrity(value):return None
    if (value.get('key')!=key
            or value.get('version')!=version() or type(value.get('bytes')) is not int
            or not 0<value['bytes']<=MAX_BYTES):return None
    return value


def _integrity(value):
    # Bind cursor/search-budget metadata too, not just SQLite image bytes.
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,
                                     separators=(',',':')).encode('ascii')).hexdigest()


def available(key, *, inventory=None):
    try:
        with _slot(key) as slot:
            manifest=_manifest(slot,key) if slot is not None else None
            if manifest is None:return False
            if inventory is None:return True
            metadata=manifest.get('metadata')
            return isinstance(metadata,dict) and metadata.get('inventory')==inventory
    except (OSError,ValueError,RecursionError,sqlite3.Error):return False


def _copy(source, target):
    if source.in_transaction or target.in_transaction:raise ValueError('active index transaction')
    size=source.execute('PRAGMA page_size').fetchone()[0]
    count=source.execute('PRAGMA page_count').fetchone()[0]
    if size*count>MAX_BYTES:raise ValueError('image size limit')
    deadline=time.monotonic()+10
    def progress(status, remaining, total):
        # SQLITE_DONE means the destination has already committed. Raising
        # here would report a miss although main was replaced, and a caller
        # would rebuild on top of that image. Only unfinished copies may abort.
        if status == sqlite3.SQLITE_DONE:return
        if status in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED):raise ValueError('busy index image')
        if total*size>MAX_BYTES or time.monotonic()>deadline:raise ValueError('index copy budget')
    source.backup(target,pages=256,progress=progress,sleep=0)


def restore(db, key, source, valid_metadata):
    """Only main is restored; connection-local TEMP inventories are retained."""
    restored = None
    try:
        with _slot(key) as slot:
            if slot is None:return None
            manifest=_manifest(slot,key)
            if manifest is None or manifest.get('source')!=source:return None
            metadata=manifest.get('metadata')
            if not valid_metadata(metadata):return None
            path=slot/'index.sqlite3'
            if not _ordinary(path):return None
            stamp,digest=digest_file(path,limit=MAX_BYTES)
            if stamp[2]!=manifest['bytes'] or digest!=manifest.get('sha256'):return None
            with path.open('rb') as stream:header=stream.read(100)
            if len(header)!=100 or header[:16]!=b'SQLite format 3\0' or int.from_bytes(header[68:72],'big')!=_APPLICATION:return None
            image=sqlite3.connect(path.absolute().as_uri()+'?mode=ro',uri=True,timeout=0)
            try:
                image.execute('PRAGMA trusted_schema=OFF');image.execute('PRAGMA query_only=ON')
                if signature(path)!=stamp or image.execute('PRAGMA quick_check').fetchone()!=('ok',):return None
                _copy(image,db)
                restored = metadata
            finally:image.close()
            return metadata
    except (OSError,ValueError,RecursionError,sqlite3.Error):
        # Closing a handle after successful backup cannot undo its commit.
        return restored


def publish(db, key, source, metadata):
    try:
        if db.in_transaction:return False
        if db.execute('PRAGMA page_size').fetchone()[0]*db.execute('PRAGMA page_count').fetchone()[0]>MAX_BYTES:return False
        with _slot(key,create=True) as slot:
            if slot is None:return False
            try:
                for name in ('pending.sqlite3','pending.sqlite3-journal','pending.json'):
                    (slot/name).unlink(missing_ok=True)
                pending=slot/'pending.sqlite3';fd=os.open(pending,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600);os.close(fd)
                image=sqlite3.connect(pending,timeout=0)
                try:
                    image.execute('PRAGMA synchronous=FULL');_copy(db,image)
                    image.execute(f'PRAGMA application_id={_APPLICATION}')
                    image.commit()
                finally:image.close()
                with pending.open('r+b') as stream:os.fsync(stream.fileno())
                stamp,digest=digest_file(pending,limit=MAX_BYTES)
                manifest={'key':key,'version':version(),'source':source,'metadata':metadata,
                          'bytes':stamp[2],'sha256':digest}
                manifest['integrity']=_integrity(manifest)
                raw=json.dumps(manifest,ensure_ascii=True,separators=(',',':')).encode()
                if len(raw)>16384:raise ValueError('manifest limit')
                with (slot/'pending.json').open('xb') as stream:
                    stream.write(raw);stream.flush();os.fsync(stream.fileno())
                os.replace(pending,slot/'index.sqlite3')
                os.replace(slot/'pending.json',slot/'manifest.json')
                _sync_directory(slot)
                return True
            finally:_clear_pending(slot)
    except (OSError,ValueError,RecursionError,sqlite3.Error):return False
