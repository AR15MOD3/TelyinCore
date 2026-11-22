"""Bounded persistent hints for disposable-cache recovery, never authority.

Only generated basenames are recorded. Reapers must still check each cache's
marker, contents and lifetime lease. A missing/busy/damaged registry falls back
to ordinary discovery; it must never prevent creating a usable cache.
"""
from contextlib import contextmanager
from itertools import islice
import os
from pathlib import Path
import re
import sqlite3
import stat

PREFIX = 'telyincore-derived-v1-'
CACHE_NAME = re.compile(re.escape(PREFIX) + r'[a-z0-9_]{8}')
MAX_ENTRIES = 4096
_MARKER = b'telyincore cache recovery hints v1\n'
_APPLICATION = 0x54435231


def location(parent):
    user = str(os.getuid()) if hasattr(os, 'getuid') else 'user'
    return Path(parent) / ('.telyincore-cache-registry-v1-' + user)


def _ordinary(path, directory=False):
    info = path.lstat()
    if (getattr(info, 'st_file_attributes', 0) & 0x400
            or (hasattr(os, 'getuid') and info.st_uid != os.getuid())):
        return False
    return stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1


@contextmanager
def _database(parent, *, create=False):
    root = location(parent)
    try:
        root.mkdir(mode=0o700) if create else None
    except FileExistsError:
        pass
    else:
        if create:
            with (root / '.ready').open('xb') as marker:
                marker.write(_MARKER);marker.flush();os.fsync(marker.fileno())
    if not root.exists() or not _ordinary(root, True):
        yield None
        return
    if hasattr(os, 'getuid') and root.stat().st_mode & 0o077:
        yield None
        return
    if not _ordinary(root / '.ready'):
        yield None
        return
    with (root / '.ready').open('rb') as marker:
        if marker.read(len(_MARKER) + 1) != _MARKER:
            yield None
            return
    allowed = {'.ready', 'registry.sqlite3', 'registry.sqlite3-journal',
               'registry.sqlite3-wal', 'registry.sqlite3-shm'}
    with os.scandir(root) as children:
        for child in children:
            if child.name not in allowed or not _ordinary(root / child.name):
                yield None
                return
    path = root / 'registry.sqlite3'
    existed = path.exists()
    if not existed and not create:
        yield None
        return
    if existed:
        if path.stat().st_size > 1024 * 1024:
            yield None
            return
        with path.open('rb') as source:
            header = source.read(100)
        # Inspect our application tag before SQLite can perform recovery or
        # modify a foreign database placed inside an otherwise valid root.
        if (len(header) != 100 or header[:16] != b'SQLite format 3\0'
                or int.from_bytes(header[68:72], 'big') != _APPLICATION):
            yield None
            return
    else:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
    initial = path.lstat()
    db = sqlite3.connect(path.absolute().as_uri() + '?mode=rw', uri=True, timeout=0.05)
    try:
        if not os.path.samestat(initial, path.lstat()):
            raise OSError('registry path changed while opening')
        db.execute('PRAGMA cache_size=-128')
        db.execute('PRAGMA synchronous=NORMAL')
        db.execute('PRAGMA max_page_count=256')  # At most 1MiB with the fixed 4KiB page size.
        if db.execute('PRAGMA page_size').fetchone()[0] != 4096:
            yield None
            return
        identity = db.execute('PRAGMA application_id').fetchone()[0]
        if not existed:
            with db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('CREATE TABLE IF NOT EXISTS caches(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS progress(id INTEGER PRIMARY KEY CHECK(id=1),last INTEGER NOT NULL)')
                db.execute('INSERT OR IGNORE INTO progress VALUES(1,0)')
                db.execute(f'PRAGMA application_id={_APPLICATION}')
        elif identity != _APPLICATION:
            yield None
            return
        yield db
    finally:
        db.close()


def register(parent, name):
    return name in register_many(parent, [name])


def register_many(parent, names):
    """Adopt at most one reaper batch without per-cache lock waits.

    Existing IDs and the finite sweep cursor stay unchanged. These entries are
    only retry hints; callers must still validate contents and lifetime leases.
    """
    names = tuple(dict.fromkeys(name for name in islice(names, 64)
                  if isinstance(name, str) and CACHE_NAME.fullmatch(name)))
    if not names:return ()
    try:
        with _database(parent, create=True) as db:
            if db is None:return ()
            accepted = []
            with db:
                db.execute('BEGIN IMMEDIATE')
                count = db.execute('SELECT count(*) FROM caches').fetchone()[0]
                for name in names:
                    if db.execute('SELECT 1 FROM caches WHERE name=?', (name,)).fetchone():
                        accepted.append(name)
                    elif count < MAX_ENTRIES:
                        db.execute('INSERT INTO caches(name) VALUES(?)', (name,))
                        count += 1
                        accepted.append(name)
            return tuple(accepted)
    except (OSError, sqlite3.Error):
        return ()


def candidates(parent):
    try:
        with _database(parent) as db:
            if db is None:return []
            with db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT last FROM progress WHERE id=1').fetchone()
                if row is None or not isinstance(row[0], int):return []
                # Freeze each sweep's upper ID. Without this fence, a steady
                # stream of newer registrations can keep the cursor moving
                # forward forever while old, previously live caches never get
                # revisited. Both bounds commit before returning candidates.
                # A separate additive table keeps pre-fence registries readable
                # by older builds; first adoption preserves their last cursor.
                db.execute('CREATE TABLE IF NOT EXISTS sweep_fence('
                           'id INTEGER PRIMARY KEY CHECK(id=1),ceiling INTEGER NOT NULL)')
                saved = db.execute('SELECT ceiling FROM sweep_fence WHERE id=1').fetchone()
                last = row[0]
                ceiling = saved[0] if saved is not None else 0
                if last < 0 or not isinstance(ceiling, int) or ceiling < 0:return []
                if ceiling == 0 or last > ceiling:
                    ceiling = db.execute('SELECT coalesce(max(id),0) FROM caches').fetchone()[0]
                    if saved is not None or last > ceiling:last = 0
                query = 'SELECT id,name FROM caches WHERE id>? AND id<=? ORDER BY id LIMIT 32'
                rows = db.execute(query, (last, ceiling)).fetchall()
                if not rows:
                    # The remaining tail of this finite sweep may have been
                    # cleaned by its owners. Start a new sweep in the same
                    # transaction, still returning at most32 candidates.
                    ceiling = db.execute('SELECT coalesce(max(id),0) FROM caches').fetchone()[0]
                    rows = db.execute(query, (0, ceiling)).fetchall()
                last = rows[-1][0] if rows else 0
                if last >= ceiling or not rows:last = ceiling = 0
                db.execute('UPDATE progress SET last=? WHERE id=1', (last,))
                db.execute('INSERT OR REPLACE INTO sweep_fence VALUES(1,?)', (ceiling,))
            return [name for _, name in rows if isinstance(name, str) and CACHE_NAME.fullmatch(name)]
    except (OSError, sqlite3.Error):
        return []


def forget(parent, name):
    forget_many(parent, [name])


def forget_many(parent, names):
    """Best-effort removal of one bounded reaper batch in one transaction.

    Stale hints are safe to retry. Do not reopen/wait on a busy registry once
    for every reclaimed directory; cache creation can invoke the reaper.
    """
    names = tuple(dict.fromkeys(name for name in islice(names, 64)
                  if isinstance(name, str) and CACHE_NAME.fullmatch(name)))
    if not names:return
    try:
        with _database(parent) as db:
            if db is not None:
                with db:db.executemany('DELETE FROM caches WHERE name=?', ((name,) for name in names))
    except (OSError, sqlite3.Error):
        pass
