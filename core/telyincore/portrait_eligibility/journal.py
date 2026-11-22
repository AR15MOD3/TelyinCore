"""Durable replay claims and separate rolling request limits (hashes only).

The first admission migrates a validated v1 JSON ledger to SQLite, preserves the
exact legacy bytes, then atomically publishes a small v2 manifest. Older readers
reject v2 rather than silently ignoring later claims. Missing/corrupt authority
fails closed. Claims and rate slots commit together under the existing OS lease;
permanent replay keys are never pruned to bound the rolling limit window.
"""
import datetime, hashlib, json, math, os, time, tempfile
import re, sqlite3, uuid
from contextlib import contextmanager
from ..source_guard import lease
from pathlib import Path
from ._frozen.contracts import canonical, Refused

DEFAULT_POLICY = {'window_seconds': 60, 'per_author_requests': 12, 'bot_requests': 120}

class RequestLimited(Refused):
    def __init__(self, bucket, used, limit, window, retry):
        super().__init__('portrait_rate_limited')
        self.rate_limit = dict(bucket=bucket, used=used, limit=limit,
                               window_seconds=window, retry_after_seconds=retry)

class Claims:
    def __init__(self, home, *, clock=time.time):
        self.root = Path(home).absolute()/'.portrait-write-gate'
        self.clock = clock

    def claim(self, scope, turn_id, call_id, *, author=''):
        self.root.mkdir(exist_ok=True)
        try:
            with lease(self.root/'claims', timeout=0):
                return self._claim(scope, turn_id, call_id, author=author)
        except TimeoutError:
            raise Refused('portrait_claim_locked') from None

    def _migrate(self, path, old):
        try:
            obj = json.loads(old) if old is not None else dict(version=1, claims={})
            if obj.get('version') != 1 or type(obj.get('claims')) is not dict:
                raise ValueError()
            for k, v in obj['claims'].items():
                if not isinstance(k, str) or len(k)!=64 or any(c not in '0123456789abcdef' for c in k): raise ValueError()
                datetime.date.fromisoformat(v)
            rates = obj.get('rate_requests', [])
            if type(rates) is not list: raise ValueError()
            for r in rates:
                if type(r) is not dict or type(r.get('at')) not in (int,float) or not math.isfinite(r['at']) or not isinstance(r.get('author'), str): raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise Refused('portrait_claim_corrupt') from None
        identity = uuid.uuid4().hex
        database = self.root / f'claims-{identity}.sqlite3'
        published = False
        try:
            with _database(database, create=True) as db:
                db.executescript('CREATE TABLE identity(token TEXT NOT NULL);\nCREATE TABLE claims(key TEXT PRIMARY KEY, day TEXT NOT NULL) WITHOUT ROWID;\nCREATE TABLE requests(at REAL NOT NULL, author TEXT NOT NULL);\nCREATE INDEX requests_time ON requests(at);\nCREATE INDEX requests_author_time ON requests(author, at);')
                with db:
                    db.execute('INSERT INTO identity VALUES(?)', (identity,))
                    db.executemany('INSERT INTO claims VALUES(?,?)', obj['claims'].items())
                    db.executemany('INSERT INTO requests VALUES(?,?)',
                                   ((r['at'], r['author']) for r in rates))
            if old is not None:
                backup = self.root / ('legacy-claims-' + hashlib.sha256(old).hexdigest() + '.json')
                if backup.exists():
                    if backup.read_bytes() != old: raise Refused('portrait_claim_corrupt')
                else:
                    _publish(backup, old)
            _sync_directory(self.root)
            if (path.read_bytes() if path.exists() else None) != old:
                raise Refused('portrait_claim_drift')
            manifest = dict(version=2, database=database.name, identity=identity)
            _publish(path, canonical(manifest).encode())
            published = True
            return manifest
        finally:
            # An uncertain publication must retain the database; never make a
            # published manifest point at a deliberately removed authority.
            if not published:
                try:
                    current = json.loads(path.read_bytes()) if path.exists() else {}
                except (ValueError, OSError):
                    current = {'database': database.name}
                if type(current) is dict and current.get('database') != database.name:
                    database.unlink(missing_ok=True)

    def _manifest(self):
        path = self.root / 'claims.json'
        old = path.read_bytes() if path.exists() else None
        if old is None and any(self.root.glob('claims-*.sqlite3')):
            raise Refused('portrait_claim_corrupt')  # Missing authority is not an empty ledger.
        try:
            manifest = json.loads(old) if old is not None else {'version': 1}
            if type(manifest) is not dict: raise ValueError()
            if manifest.get('version') == 1:
                return self._migrate(path, old)
            if (manifest.get('version') != 2
                    or set(manifest) != {'version', 'database', 'identity'}
                    or not isinstance(manifest['identity'], str)
                    or not re.fullmatch('[0-9a-f]{32}', manifest['identity'])
                    or manifest['database'] != f"claims-{manifest['identity']}.sqlite3"):
                raise ValueError()
            return manifest
        except Refused:
            raise
        except (ValueError, TypeError, AttributeError):
            raise Refused('portrait_claim_corrupt') from None

    def _claim(self, scope, turn_id, call_id, *, author):
        try:
            manifest = self._manifest()
            path = self.root / manifest['database']
            if path.is_symlink(): raise Refused('portrait_claim_corrupt')
            with _database(path) as db, db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT token FROM identity').fetchall() != [(manifest['identity'],)]:
                    raise Refused('portrait_claim_corrupt')
                return self._claim_transaction(db, scope, turn_id, call_id, author=author)
        except sqlite3.DatabaseError:
            raise Refused('portrait_claim_corrupt') from None

    def _claim_transaction(self, db, scope, turn_id, call_id, *, author):
        key = hashlib.sha256(canonical([scope,turn_id,call_id]).encode()).hexdigest()
        if db.execute('SELECT 1 FROM claims WHERE key=?', (key,)).fetchone():
            raise Refused('portrait_call_already_consumed')
        policy = dict(DEFAULT_POLICY)
        config = self.root/'rate-policy.json'
        try:
            if config.exists():
                supplied = json.loads(config.read_text())
                if type(supplied) is not dict or set(supplied)-set(policy): raise ValueError()
                policy.update(supplied)
            if any(type(v) is not int or v<1 for v in policy.values()): raise ValueError()
            if policy['window_seconds']>3600 or policy['bot_requests']>10000 or policy['per_author_requests']>policy['bot_requests']: raise ValueError()
        except (ValueError, TypeError):
            raise Refused('portrait_rate_policy_invalid') from None
        now = self.clock(); window = policy['window_seconds']
        if type(now) not in (int, float) or not math.isfinite(now):
            raise Refused('portrait_claim_clock_invalid')
        # Replay keys are permanent. Only expired rate slots can be deleted.
        db.execute('DELETE FROM requests WHERE at<=?', (now-window,))
        author_key = hashlib.sha256(canonical([scope,author]).encode()).hexdigest()
        own = db.execute('SELECT count(*) FROM requests WHERE author=?', (author_key,)).fetchone()[0]
        active = db.execute('SELECT count(*) FROM requests').fetchone()[0]
        for bucket, used, limit in [('author',own,policy['per_author_requests']),
                                     ('bot',active,policy['bot_requests'])]:
            if used >= limit:
                where, params = (' WHERE author=?', (author_key,)) if bucket == 'author' else ('', ())
                stamp = db.execute('SELECT at FROM requests' + where + ' ORDER BY at LIMIT 1 OFFSET ?',
                                   params + (used-limit,)).fetchone()[0]
                retry = max(1, math.ceil(stamp+window-now))
                raise RequestLimited(bucket, used, limit, window, retry)
        day = datetime.datetime.fromtimestamp(now,datetime.timezone.utc).date().isoformat()
        db.execute('INSERT INTO claims VALUES(?,?)', (key, day))
        db.execute('INSERT INTO requests VALUES(?,?)', (now, author_key))
        return dict(bucket='admitted', author_used=own+1, bot_used=active+1, **policy)


@contextmanager
def _database(path, *, create=False):
    # mode=rw prevents silently recreating a lost replay ledger.
    connection = sqlite3.connect(path.as_uri() + ('?mode=rwc' if create else '?mode=rw'), uri=True)
    try:
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('PRAGMA cache_size=-2048')
        yield connection
    finally:
        connection.close()


def _sync_directory(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)


def _publish(path, raw):
    fd, temporary = tempfile.mkstemp(prefix='claims-', suffix='.tmp', dir=path.parent)
    tmp = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
        _sync_directory(path.parent)
    finally:
        tmp.unlink(missing_ok=True)
