"""Bounded, cooperative recovery-store replacement, not OS-atomic CAS/ACL proof.

Caller MUST hold its cooperative identity guard over snapshot and all uses.
Experimental callers additionally hold their host-revocation guard; the default
writer does not gain host authority. No locks, credential decryption or production
enablement here. Optional checkpoints only add cancellation/close/gate denial.
"""
import json
import os
import sys
import stat
from pathlib import Path
import uuid

from runtime_identity import safe_path
from bot_private_file import open_private_new

MAX_BYTES = 65536


def _object(raw):
    if raw is None:
        return {}
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError('duplicate recovery store field')
            result[k] = v
        return result
    def constant(value):
        raise ValueError('nonfinite recovery store number')
    result = json.loads(raw, object_pairs_hook=unique, parse_constant=constant)
    if type(result) is not dict:
        raise ValueError('recovery store must be an object')
    return result


class GuardedStore:
    def __init__(self, root, path, *, checkpoint=None):
        self.root, self.path = Path(root).absolute(), Path(path).absolute()
        self.backup = self.path.with_suffix('.json.bak')
        self.checkpoint = checkpoint or (lambda: None)
        self.raw, self._identity = self._snapshot(self.path)
        self.backup_raw, self._backup_identity = self._snapshot(self.backup)
        _object(self.raw)  # refuse malformed before-images before any write

    def _snapshot(self, path):
        safe_path(self.root, path)
        try:
            before = path.lstat()
        except FileNotFoundError:
            return (None, None)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or getattr(before, 'st_file_attributes', 0) & 0x400):
            raise ValueError('recovery file must be a single regular non-reparse file')
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        with path.open('rb') as stream:
            info = os.fstat(stream.fileno())
            if identity(info) != identity(before):
                raise ValueError('recovery file changed before read')
            raw = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
        safe_path(self.root, path)
        if identity(after) != identity(before) or identity(path.lstat()) != identity(before):
            raise ValueError('recovery file changed during read')
        if len(raw) > MAX_BYTES:
            raise ValueError('recovery store exceeds guarded limit')
        return (raw, (info.st_dev, info.st_ino))

    def _read(self, path):
        return self._snapshot(path)[0]

    def _unchanged(self):
        if (self._snapshot(self.path) != (self.raw, self._identity)
                or self._snapshot(self.backup) != (self.backup_raw, self._backup_identity)):
            raise ValueError('recovery store changed during guarded operation')

    def read(self):
        self._unchanged()
        return _object(self.raw)

    def _install(self, destination, raw):
        temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.pending')
        safe_path(self.root, temporary)
        identity = None
        try:
            with open_private_new(temporary) as stream:
                info = os.fstat(stream.fileno())
                identity = (info.st_dev, info.st_ino)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self.checkpoint()
            self._unchanged()
            safe_path(self.root, temporary)
            info = temporary.lstat()
            if (info.st_dev, info.st_ino) != identity:
                raise ValueError('recovery store temporary changed before install')
            staged_raw, staged_identity = self._snapshot(temporary)
            if staged_identity != identity or staged_raw != raw:
                raise ValueError('recovery temporary content changed before install')
            os.replace(temporary, destination)
            if destination == self.path:
                self._identity = identity
            else:
                self._backup_identity = identity
            identity = None  # consumed; never delete a new file at the old name
        finally:
            if identity is not None:
                primary = sys.exception()
                try:
                    info = temporary.lstat()
                    if (info.st_dev, info.st_ino) == identity:
                        temporary.unlink()
                except FileNotFoundError:
                    pass
                except BaseException as cleanup:
                    if primary is None:
                        raise
                    primary.add_note('recovery temporary cleanup failed: ' + type(cleanup).__name__)

    def write(self, data):
        if type(data) is not dict:
            raise ValueError('recovery store payload must be an object')
        payload = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
        if len(payload) > MAX_BYTES:
            raise ValueError('recovery store payload exceeds guarded limit')
        _object(payload)
        self.checkpoint()
        self._unchanged()
        safe_path(self.root, self.path.parent)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.raw is not None:
            self._install(self.backup, self.raw)
            self.backup_raw = self.raw
        self._install(self.path, payload)
        self.raw = payload

    def clear_backup(self):
        self.checkpoint()
        self._unchanged()
        if self.backup_raw is not None:
            self.backup.unlink()  # authorized fixed backup, exact pin rechecked
            self.backup_raw = None
            self._backup_identity = None

    def remove(self):
        """Remove only the still-pinned primary; not secure erase/host retirement."""
        self.checkpoint()
        self._unchanged()
        if self.raw is not None:
            self.path.unlink()
            self.raw = None
            self._identity = None
