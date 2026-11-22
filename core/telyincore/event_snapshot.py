"""Private disposable disk snapshot with bounded strong references.

The JSONL source remains authoritative. Cold reads use this snapshot, not a file
that an external editor may have changed after EventStore opened it. Weak live
references preserve object identity while a consumer still owns an envelope.
Rolled-back mutations invalidate cached objects before subsequent reads.
"""
from collections import OrderedDict
from collections.abc import MutableMapping
from contextlib import contextmanager, nullcontext
import hashlib
from datetime import datetime, timezone
import json
import math
import os
import re
from pathlib import Path
import sqlite3
import threading
import weakref

from .derived_cache import CacheDirectory
from .search_growth import SearchGrowth
from .json_text_window import span as text_span, LowerMatcher, WindowCursor

# Byte bounds refer to the validated canonical wire stored in this same row.
# Optional index construction projects only that JSON string into Python;
# exact scans stream the same span through blobopen. Whole event reads retain
# the original wire. This needs neither JSON1 nor an extra body copy.
_TEXT_JSON = 'substr(CAST(wire AS BLOB),text_start+1,text_bytes)'
_SURROGATE_PAIRS = re.compile('[\ud800-\udbff][\udc00-\udfff]')


def _time_hint(value):
    """Bounded derived hint; naive timestamps keep host-local query semantics."""
    if not isinstance(value, str): return ''
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return 'N' + parsed.isoformat(timespec='microseconds')
        return 'A' + parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')
    except (ValueError, OverflowError, OSError):
        return ''


def _moment_hint(hint):
    if hint.startswith('A'): return hint[1:]
    if hint.startswith('N'):
        try:
            return datetime.fromisoformat(hint[1:]).astimezone(timezone.utc).isoformat(timespec='microseconds')
        except (ValueError, OverflowError, OSError):
            pass
    return ''


def _text_value(raw):
    return json.loads(b'"' + raw + b'"')


def _wire_chunks(wire):
    for offset in range(0, len(wire), 8192):
        yield wire[offset:offset + 8192].encode('utf-8')


def _wire_identity(wire):
    """Exact UTF-8 identity and accounting, without a second full byte string."""
    if len(wire) <= 8192:
        raw = wire.encode('utf-8')
        return hashlib.sha256(raw).digest(), len(raw)
    digest = hashlib.sha256()
    size = 0
    for chunk in _wire_chunks(wire):
        digest.update(chunk)
        size += len(chunk)
    return digest.digest(), size


def _wire_size(wire):
    return len(wire) if wire.isascii() else sum(len(chunk) for chunk in _wire_chunks(wire))


def _retrieval_projection(event, metadata_bytes):
    """Optional small projection for the fixed retrieval policy, not arbitrary callbacks.

    Only large metadata with bounded actually-used fields earns another stored
    value. Small records and unusually large/typed headers keep the full path.
    """
    if metadata_bytes <= 8192:return None
    fields = {name: getattr(event, name) for name in ('type','ts','source','origin','sender_key')}
    room = event.meta.get('conversation') if isinstance(event.meta, dict) else None
    if not isinstance(room, str):room = None
    values = [*fields.values(), room]
    if any(value is not None and type(value) is not str for value in values):return None
    if sum(len(value) for value in values if value is not None) > 4096:return None
    if event.cursor.bit_length() > 4096:return None
    fields['senderKey'] = fields.pop('sender_key')
    fields.update(cursor=event.cursor, text='', meta={'conversation':room})
    wire = json.dumps(fields, ensure_ascii=True, separators=(',', ':'))
    return wire if len(wire) * 2 < metadata_bytes else None


def _sender_hint(value):
    """Bounded rejection hint, never equality or authorization authority.

    Unusual string/subclass representations disable this optimization for the
    view. Matching hints still pass EventStore's original equality checks.
    """
    if value is None or type(value) in (bool, int, float, list, dict, tuple):
        return b''  # Built-in non-strings cannot equal a plain string query.
    if type(value) is not str or len(value) > 256:
        return None
    try:
        raw = value.encode('utf-8')
    except UnicodeEncodeError:
        return None
    return hashlib.blake2b(raw, digest_size=8).digest()


def _release(connection, directory):
    try:
        connection.close()
    finally:
        directory.cleanup()


def _long_key_digest(key):
    digest = hashlib.sha256()
    for offset in range(0, len(key), 8192):
        digest.update(key[offset:offset + 8192].encode('utf-8', 'surrogatepass'))
    return b'h:' + digest.digest()


def _dedup_identity(key):
    """Encode JSON scalar equality without SQLite numeric coercion or rounding.

    Python equates bool/int/integral floats; strings remain a separate domain.
    Historical NaNs get a read-only marker so conflicting persisted identities
    are still refused. EventStore refuses new NaN keys before source writes.
    """
    if isinstance(key, str):
        # JSON can normalize an explicit surrogate pair into one scalar.
        # Keep the original representation for that legacy in-memory case.
        if len(key) > 256 and not _SURROGATE_PAIRS.search(key):
            return _long_key_digest(key)
        return b's:' + key.encode('utf-8', 'surrogatepass')
    if isinstance(key, int):
        numerator, denominator = int(key), 1
    elif isinstance(key, float):
        if math.isnan(key): return b'nan'
        if math.isinf(key): return b'+inf' if key > 0 else b'-inf'
        numerator, denominator = key.as_integer_ratio()
    else:
        return None  # None is absent; other shapes fail ordinary wire validation.
    return f'n:{numerator}/{denominator}'.encode('ascii')


class EventSnapshot(MutableMapping):
    MAX_CACHE_BYTES = 8 * 1024 * 1024
    MAX_CACHE_EVENTS = 128
    # Bound input to the optional trigram index. This is not a quota on the
    # authoritative history or on SQLite's total file size (pages add overhead).
    MAX_SEARCH_SOURCE_BYTES = 32 * 1024 * 1024
    MAX_SEARCH_GROWTH_BYTES = 16 * 1024 * 1024

    def __init__(self, decode):
        directory = CacheDirectory()
        try:
            connection = sqlite3.connect(str(Path(directory.name)/'snapshot.sqlite3'),
                                         isolation_level=None, check_same_thread=False)
            connection.execute('PRAGMA cache_size=-2048')
            connection.execute('PRAGMA temp_store=FILE')
            connection.execute('PRAGMA synchronous=OFF')  # Disposable, never a recovery authority.
            connection.execute('CREATE TABLE events(cursor TEXT PRIMARY KEY, digits INTEGER NOT NULL, '
                               'wire TEXT NOT NULL, digest BLOB NOT NULL, dedup BLOB, segment TEXT NOT NULL, '
                               'text_start INTEGER NOT NULL, text_bytes INTEGER NOT NULL, sender_hint BLOB, '
                               'text_chars INTEGER NOT NULL, retrieval_wire TEXT, time_hint TEXT NOT NULL)')
            connection.execute('CREATE INDEX event_order ON events(digits,cursor)')
            connection.execute('CREATE INDEX event_sender_order ON events(sender_hint,digits,cursor)')
            connection.execute('CREATE INDEX event_dedup ON events(dedup) WHERE dedup IS NOT NULL')
            connection.execute('CREATE INDEX event_segment ON events(segment)')
            connection.execute('PRAGMA recursive_triggers=ON')  # REPLACE deletes must update counts too.
            connection.executescript('''
CREATE TABLE event_segments(segment TEXT PRIMARY KEY, count INTEGER NOT NULL CHECK(count>=0));
CREATE TABLE repair_results(ordinal INTEGER PRIMARY KEY, name TEXT NOT NULL, removed INTEGER);
CREATE TABLE skipped_shards(ordinal INTEGER PRIMARY KEY, path TEXT NOT NULL, identity BLOB UNIQUE NOT NULL);
CREATE TABLE search_budget(bytes INTEGER NOT NULL);
INSERT INTO search_budget VALUES(0);
CREATE TRIGGER segment_insert AFTER INSERT ON events BEGIN
 UPDATE search_budget SET bytes=bytes+length(CAST(new.wire AS BLOB));
 INSERT INTO event_segments VALUES(new.segment,1)
 ON CONFLICT(segment) DO UPDATE SET count=count+1;
END;
CREATE TRIGGER segment_delete AFTER DELETE ON events BEGIN
 UPDATE search_budget SET bytes=bytes-length(CAST(old.wire AS BLOB));
 UPDATE event_segments SET count=count-1 WHERE segment=old.segment;
 DELETE FROM event_segments WHERE segment=old.segment AND count=0;
END;
''')
            connection.create_function('event_moment', 1, _moment_hint)
            connection.create_function('event_contains', 2, lambda raw, needle:
                needle.decode('utf-8', 'surrogatepass') in _text_value(raw).lower(),
                deterministic=True)
            search_available = (sqlite3.sqlite_version_info >= (3, 34, 0) and
                bool(connection.execute("SELECT sqlite_compileoption_used('ENABLE_FTS5')").fetchone()[0]))
        except BaseException:
            if 'connection' in locals(): connection.close()
            directory.cleanup()
            raise
        self._db = connection
        self._cleanup = weakref.finalize(self, _release, connection, directory)
        self._decode = decode
        self._lock = threading.RLock()
        self._live = weakref.WeakValueDictionary()
        self._hot = OrderedDict()
        self._cache_bytes = 0
        self._windows = OrderedDict()
        from .short_terms import ShortTerms
        self._short_terms = ShortTerms()
        self._search_available = search_available
        self._search_growth = SearchGrowth()
        self._search_ready = False
        self._search_requested = False
        self.invalid_lines = False

    def restart_metadata(self, next_cursor):
        return {'kind':'events','next':next_cursor,'available':self._search_available,
                'ready':self._search_ready,'requested':self._search_requested,'invalid_lines':self.invalid_lines,
                'growth':self._search_growth.used_bytes,
                'source_budget':self.MAX_SEARCH_SOURCE_BYTES,'growth_budget':self.MAX_SEARCH_GROWTH_BYTES}

    def restart_state(self):
        return (self._db.total_changes,self._search_available,self._search_ready,
                self._search_requested,self._search_growth.used_bytes)

    def restart_load(self, key, source):
        from . import persistent_index as images
        def valid(meta):
            return (isinstance(meta,dict) and meta.get('kind')=='events'
                and type(meta.get('next')) is int and meta['next']>0
                and all(type(meta.get(k)) is bool for k in ('available','ready','requested','invalid_lines'))
                and type(meta.get('growth')) is int and 0<=meta['growth']<2**63
                and meta.get('source_budget')==self.MAX_SEARCH_SOURCE_BYTES
                and meta.get('growth_budget')==self.MAX_SEARCH_GROWTH_BYTES
                and (not meta['available'] or self._search_available)
                and (not meta['ready'] or meta['available']))
        meta=images.restore(self._db,key,source,valid)
        if meta is not None:
            self._search_available,self._search_ready=meta['available'],meta['ready']
            self.invalid_lines = meta['invalid_lines']
            self._search_requested=meta['requested'];self._search_growth.used_bytes=meta['growth']
            return meta['next']

    def restart_eligible(self):
        from . import persistent_index as images
        return (not self._db.in_transaction
            and self._db.execute('SELECT bytes FROM search_budget').fetchone()[0]>=images.MIN_SOURCE_BYTES
            and self._db.execute('PRAGMA page_count').fetchone()[0]*self._db.execute('PRAGMA page_size').fetchone()[0]<=images.MAX_BYTES)

    def restart_save(self,key,source,next_cursor,inventory=None):
        from . import persistent_index as images
        metadata=self.restart_metadata(next_cursor);metadata['inventory']=inventory
        return images.publish(self._db,key,source,metadata)

    def close(self):
        with self._lock:
            self._cleanup()
            self._live.clear()
            self._windows.clear()
            self._hot.clear()
            self._short_terms.entries.clear()
            self._short_terms.bytes = 0
            self._cache_bytes = 0

    @staticmethod
    def _path_identity(path):
        # Match Path equality, including Windows case aliases and POSIX
        # surrogate-escaped filenames. JSON below preserves the display path.
        return os.path.normcase(str(Path(path))).encode('utf-8', 'surrogatepass')

    def add_repair(self, name, removed):
        with self._lock:
            self._db.execute('INSERT INTO repair_results(name,removed) VALUES(?,?)',
                (json.dumps(name, ensure_ascii=True), removed))

    def repair_report(self):
        from .log import RepairReport
        with self._lock:
            fixed, failed = {}, []
            for name, removed in self._db.execute(
                    'SELECT name,removed FROM repair_results ORDER BY ordinal'):
                name = json.loads(name)
                if removed is None:
                    failed.append(name)
                else:
                    fixed[name] = removed
            return RepairReport(fixed, tuple(failed))

    def clear_skipped(self):
        with self._lock:
            self._db.execute('DELETE FROM skipped_shards')

    def add_skipped(self, path):
        with self._lock:
            self._db.execute('INSERT INTO skipped_shards(path,identity) VALUES(?,?)',
                (json.dumps(str(path), ensure_ascii=True), self._path_identity(path)))

    def has_skipped(self, path):
        with self._lock:
            return self._db.execute('SELECT 1 FROM skipped_shards WHERE identity=?',
                (self._path_identity(path),)).fetchone() is not None

    def skipped_paths(self):
        # The public API explicitly asks for a complete immutable report.
        # Materialize on request; internal guards never request this tuple.
        with self._lock:
            return tuple(Path(json.loads(row[0])) for row in self._db.execute(
                'SELECT path FROM skipped_shards ORDER BY ordinal'))

    def skipped_fingerprint(self):
        # Preserve ordered Path equality without retaining every diagnostic.
        digest = hashlib.sha256()
        with self._lock:
            for (identity,) in self._db.execute('SELECT identity FROM skipped_shards ORDER BY ordinal'):
                digest.update(len(identity).to_bytes(8, 'big'))
                digest.update(identity)
        return digest.digest()

    @contextmanager
    def shard(self):
        with self._transaction('shard'):
            yield

    @contextmanager
    def _transaction(self, name, *, invalidate_cache=True):
        # Names are private constants, never derived from event/query input.
        with self._lock:
            search_ready_before = self._search_ready
            outer_transaction = self._db.in_transaction
            self._db.execute('SAVEPOINT ' + name)
            try:
                yield
                # RELEASE commits an outermost savepoint and can fail after
                # every statement succeeded (for example, a concurrent reader).
                self._db.execute('RELEASE ' + name)
            except BaseException as failure:
                # SQLITE_FULL may roll back the entire transaction itself.
                # Preserve that original failure instead of replacing it with
                # "no such savepoint" from cleanup.
                restored = False
                try:
                    if self._db.in_transaction:
                        try:
                            if outer_transaction:
                                self._db.execute('ROLLBACK TO ' + name)
                                self._db.execute('RELEASE ' + name)
                            else:
                                # ROLLBACK TO followed by RELEASE would retry
                                # the same failing outer commit. Abort it fully.
                                self._db.execute('ROLLBACK')
                            restored = True
                        except sqlite3.Error as cleanup_error:
                            # An unsuccessful rollback is never safe to degrade.
                            raise cleanup_error from failure
                finally:
                    # SQLite rollback cannot roll back Python objects or the
                    # lazy-index flag. Never serve an uncommitted hot value or
                    # assume a rolled-back first FTS build still exists.
                    # A single put publishes its Python object only after the
                    # SQL transaction succeeds; its older objects remain valid.
                    # A shard transaction may already have published many puts.
                    if invalidate_cache or not restored:
                        self._live.clear()
                        self._hot.clear()
                        self._cache_bytes = 0
                        self._windows.clear()
                    # An existing committed FTS table survives rollback. A
                    # first build inside this transaction does not.
                    self._search_ready = search_ready_before if restored else False
                raise

    def put(self, event, *, retain=True, unique=False, segment=''):
        wire = event.to_line()
        text_chars = len(event.text)
        try:
            digest, wire_bytes = _wire_identity(wire)
        except UnicodeEncodeError:
            # Historical JSON may legally contain an escaped lone surrogate.
            # Keep that opened view readable even though new UTF-8 appends refuse it.
            wire = json.dumps(event._as_wire(), ensure_ascii=True, separators=(',', ':'))
            digest, wire_bytes = _wire_identity(wire)
            # JSON joins escaped surrogate pairs into one code point. Ordinary
            # source reads already normalized these; private snapshot callers
            # can still supply the escaped pair directly.
            text_chars -= sum(1 for _ in _SURROGATE_PAIRS.finditer(event.text))
        with self._lock:
            statement = ('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(cursor) DO NOTHING'
                         if unique else 'INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)')
            dedup = _dedup_identity(event.dedup_key)
            cursor = str(event.cursor)
            # to_line/_as_wire guarantee one top-level text key. Do not parse
            # the (potentially large) trailing metadata again just for bounds.
            start, size = text_span(wire, first=True)
            values = (cursor, len(cursor), wire, digest, dedup, segment, start, size,
                      _sender_hint(event.sender_key), text_chars, _retrieval_projection(event, wire_bytes - size), _time_hint(event.ts))
            had_search, outer_transaction = self._search_ready, self._db.in_transaction
            try:
                with (self._search_growth.limit(self._db, self.MAX_SEARCH_GROWTH_BYTES)
                      if had_search else nullcontext()), (
                      self._transaction('event_update', invalidate_cache=False) if had_search else nullcontext()):
                    old = (self._db.execute('SELECT rowid FROM events WHERE cursor=?', (cursor,)).fetchone()
                           if self._search_ready else None)
                    result = self._db.execute(statement, values)
                    if not result.rowcount: return False
                    if self._search_ready and self._within_search_budget():
                        if old: self._db.execute('DELETE FROM event_search WHERE rowid=?', old)
                        self._index_text(result.lastrowid, event.text)
            except sqlite3.Error as error:
                if (getattr(error, 'sqlite_errorcode', None) != sqlite3.SQLITE_FULL
                        or not had_search or outer_transaction or self._db.in_transaction
                        or error.__cause__ is not None or error.__context__ is not None):
                    raise
                # The complete derived update rolled back. Retry ONLY its
                # canonical row, never the caller's already-written JSONL.
                # If this required write also fails, the ordinary uncertain
                # source-write guard still forces reopening.
                self._retire_search()
                result = self._db.execute(statement, values)
                if not result.rowcount: return False
            if retain:
                self._remember(event, wire_bytes, digest)
            else:
                # Retention controls the new value, not invalidation of an
                # older cached value for this cursor after replacement.
                self._live.pop(event.cursor, None)
                previous = self._hot.pop(event.cursor, None)
                if previous is not None:self._cache_bytes -= previous[1]
            return True

    def dedup_cursor(self, key):
        identity = _dedup_identity(key)
        if identity is None: return None
        with self._lock:
            if identity.startswith(b'h:'):
                # A digest selects candidates only. Compare the full key from
                # the canonical wire without transferring its message body.
                candidates = self._db.execute(
                    'SELECT cursor,rowid,text_start,text_bytes FROM events WHERE dedup=?', (identity,))
                try:
                    for cursor, rowid, start, size in candidates:
                        if json.loads(self._metadata_wire(rowid, start, size)).get('dedupKey') == key:
                            return int(cursor)
                    return None
                finally:
                    candidates.close()
            row = self._db.execute('SELECT cursor FROM events WHERE dedup=? LIMIT 1',
                                  (identity,)).fetchone()
            return int(row[0]) if row else None

    def _index_text(self, rowid, text):
        # Only a candidate filter. ordered() still performs exact lower matching;
        # sanitize legacy NUL/surrogates here so suffixes remain indexable.
        text = text.lower().replace('\0', ' ').encode('utf-8', 'replace').decode('utf-8')
        self._db.execute('INSERT INTO event_search(rowid,text) VALUES(?,?)', (rowid, text))

    def _retire_search(self):
        # The optional chronological index participates in writes; drop it
        # before a canonical-only retry so it cannot consume required capacity.
        self._db.execute('DROP INDEX IF EXISTS event_time_order')
        self._search_available = self._search_ready = False

    def _indexed_time_order(self):
        if not self._search_ready: return False
        if self._db.execute("SELECT 1 FROM sqlite_master WHERE name='event_time_order'").fetchone() is None:
            return False
        first = self._db.execute('SELECT time_hint FROM events INDEXED BY event_time_order '
                                 'ORDER BY time_hint ASC LIMIT 1').fetchone()
        last = self._db.execute('SELECT time_hint FROM events INDEXED BY event_time_order '
                                'ORDER BY time_hint DESC LIMIT 1').fetchone()
        # Naive times depend on the current host timezone; invalid hints keep
        # existing error/filter behavior. Only the all-aware fast path is safe.
        return bool(first and last and first[0].startswith('A') and last[0].startswith('A'))

    def _ensure_search(self):
        if self._search_ready: return
        with self._search_growth.limit(self._db, self.MAX_SEARCH_GROWTH_BYTES), self._transaction('event_search_build'):
            from .search_schema import options
            self._db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS event_search USING fts5(text, tokenize='trigram case_sensitive 1'" + options() + ")")
            self._db.execute('DELETE FROM event_search')
            cursor = self._db.execute('SELECT rowid,' + _TEXT_JSON + ' FROM events')
            try:
                for rowid, raw in cursor: self._index_text(rowid, _text_value(raw))
            finally: cursor.close()
            self._db.execute('CREATE INDEX IF NOT EXISTS event_time_order ON events(time_hint DESC,digits,cursor)')
        self._search_ready = True

    def _within_search_budget(self):
        if self._db.execute('SELECT bytes FROM search_budget').fetchone()[0] <= self.MAX_SEARCH_SOURCE_BYTES:
            return True
        # Once over budget, this snapshot scans exactly for its remaining
        # lifetime. Do not repeatedly attempt a large build or use stale rows.
        # Old index pages remain reusable; original events are never removed.
        self._retire_search()
        return False

    def _prepare_search(self):
        outer_transaction = self._db.in_transaction
        try:
            self._ensure_search()
        except sqlite3.Error as error:
            # Only an optional build with no caller-owned transaction may
            # degrade after SQLite has completed its rollback. I/O, corruption
            # and incomplete rollback remain errors, never empty results.
            if (getattr(error, 'sqlite_errorcode', None) != sqlite3.SQLITE_FULL
                    or error.__cause__ is not None or error.__context__ is not None
                    or outer_transaction or self._db.in_transaction):
                raise
            self._retire_search()
            return False
        return True

    def _narrow_search(self, clauses, args, start, stop, *, constrained=False):
        # A small restricted cursor window is cheaper to scan than to build or
        # consult a whole-history text index. Probe at most 129 stored keys;
        # cursor gaps and integers larger than SQLite int64 must not affect it.
        with self._lock:
            latest = self._db.execute('SELECT digits,cursor FROM events '
                                      'ORDER BY digits DESC,cursor DESC LIMIT 1').fetchone()
            if latest is None:
                return True
            if not constrained and start <= 1 and (stop is None or (len(str(stop)), str(stop)) >= latest):
                return False
            where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
            return self._db.execute('SELECT 1 FROM events' + where +
                ' ORDER BY digits,cursor LIMIT 1 OFFSET 128', args).fetchone() is None

    def _keyword_candidates(self, keyword):
        # Caller holds the snapshot lock. A broad result is deliberately not
        # materialized; exact matching and live authorization remain mandatory.
        candidates = None
        if (self._search_requested and self._within_search_budget()
                and self._prepare_search()):
            phrase = '"' + keyword[:128].replace('"', '""') + '"'
            candidates = self._db.execute('SELECT rowid FROM event_search '
                'WHERE event_search MATCH ? LIMIT 257', (phrase,)).fetchall()
        self._search_requested = True
        return candidates

    def _short_candidate(self, key, query, rowid, start, size):
        if not 0 < len(query) <= 2: return True
        from .json_text_window import chunks
        def blocks():
            with self._db.blobopen('events', 'wire', rowid, readonly=True) as body:
                yield from chunks(body, start, size)
        return self._short_terms.maybe(key, query, blocks)

    def newest_metadata(self, *, keyword, limit, accept, sender_key=None, retrieval_projection=False):
        """Sort bounded timestamp hints, then match bodies only until top-k.

        SQLite's file-backed sorter still scans metadata and may use temporary
        disk. No additional persistent chronological index or authorization
        cache is created. Ties preserve ascending numeric cursor order, exactly
        like the previous stable heap over the canonical grep stream.
        """
        clauses, args = [], []
        with self._lock:
            hint = _sender_hint(sender_key) if type(sender_key) is str else None
            if hint is not None and self._db.execute(
                    'SELECT 1 FROM events WHERE sender_hint IS NULL LIMIT 1').fetchone() is None:
                clauses.append('sender_hint=?'); args.append(hint)
            if (self._search_available and len(keyword) >= 3 and '\0' not in keyword
                    and not any(0xD800 <= ord(c) <= 0xDFFF for c in keyword)
                    and len(self) > 128
                    and (not clauses or not self._narrow_search(clauses, args, 1, None, constrained=True))):
                candidates = self._keyword_candidates(keyword)
                if candidates == []: return
                if candidates is not None and len(candidates) <= 256:
                    clauses.append('rowid IN (' + ','.join('?' for _ in candidates) + ')')
                    args.extend(row[0] for row in candidates)
            where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
            order = 'time_hint DESC,digits,cursor' if self._indexed_time_order() else 'event_moment(time_hint) DESC,digits,cursor'
            reader = self._db.execute('SELECT cursor FROM events' + where + ' ORDER BY ' + order, args)
        matcher = None
        found = 0
        try:
            while True:
                with self._lock: page = reader.fetchmany(128)
                if not page: return
                for (cursor,) in page:
                    with self._lock:
                        result = self.read_window(int(cursor), 0, 0, accept=accept,
                                                  retrieval_projection=retrieval_projection)
                        if result is None: continue
                        descriptor = self._db.execute('SELECT rowid,text_start,text_bytes,digest FROM events '
                                                      'WHERE cursor=?', (cursor,)).fetchone()
                        if descriptor is None: continue
                        rowid, start, size, body_key = descriptor
                        if not self._short_candidate(body_key, keyword, rowid, start, size): continue
                        if size <= 8192:
                            raw = self._db.execute('SELECT ' + _TEXT_JSON + ' FROM events WHERE cursor=?',
                                                   (cursor,)).fetchone()[0]
                            matches = keyword in _text_value(raw).lower()
                        else:
                            if matcher is None: matcher = LowerMatcher(keyword)
                            with self._db.blobopen('events', 'wire', rowid, readonly=True) as body:
                                matches = matcher.contains(body, start, size)
                        if not matches:
                            self._short_terms.remember_miss(body_key, keyword)
                            continue
                    yield result[0]
                    found += 1
                    if found >= limit: return
        finally:
            with self._lock: reader.close()

    def ordered(self, *, lower=None, upper=None, reverse=False, keyword='', sender_key=None, accept=None, retrieval_projection=False):
        """Keyset pages avoid a live SQL reader across yields and unbounded lists.

        Positive integer cursors use (decimal length, decimal text), so legacy
        values above SQLite's signed 64-bit integer range retain numeric order.
        """
        import math
        clauses, args = [], []
        start, stop = 1, None
        for value, op, round_bound in ((lower, '>=', math.ceil), (upper, '<=', math.floor)):
            if value is not None:
                value = round_bound(value)
                if value < 1:
                    if op == '<=': return
                    continue
                if op == '>=':
                    start = value
                else:
                    stop = value
                key = str(value)
                clauses.append(f'(digits,cursor) {op} (?,?)')
                args.extend((len(key), key))
        # Empty cursor polls must neither build nor prime the lazy text index.
        if stop is not None and start > stop:
            return
        hint = _sender_hint(sender_key) if type(sender_key) is str else None
        if hint is not None:
            with self._lock:
                # Unknown/custom representations retain the old full scan,
                # including their comparison and decoding behavior.
                unknown = self._db.execute('SELECT 1 FROM events WHERE sender_hint IS NULL LIMIT 1').fetchone()
            if unknown is not None:
                hint = None
            else:
                clauses.append('sender_hint=?')
                args.append(hint)
        if (self._search_available and len(keyword) >= 3 and '\0' not in keyword
                and not any(0xD800 <= ord(c) <= 0xDFFF for c in keyword)
                and not self._narrow_search(clauses, args, start, stop, constrained=hint is not None)):
            with self._lock:
                candidates = self._keyword_candidates(keyword)
            if candidates == []: return
            if candidates is not None and len(candidates) <= 256:
                clauses.append('rowid IN (' + ','.join('?' for _ in candidates) + ')')
                args.extend(row[0] for row in candidates)
        # Evaluate within bounded *source* pages, not as a WHERE filter before
        # LIMIT. A rare match must not scan the entire database under one lock
        # before delivering the first page. Read validated body spans directly
        # from SQLite in chunks, not as a whole Python BLOB/lowercase copy.
        # Small bodies keep the cheaper C/SQLite projection path. CASE is lazy:
        # an oversized body is never materialized as a Python function argument.
        small_match = ('NULL' if accept is not None or 0 < len(keyword) <= 2 else
            'CASE WHEN text_bytes<=8192 THEN event_contains(' + _TEXT_JSON + ',?) ELSE NULL END')
        columns = 'cursor,rowid,text_start,text_bytes,digest,' + small_match if keyword else 'cursor,1'
        predicate_args = [keyword.encode('utf-8', 'surrogatepass')] if keyword and accept is None and not 0 < len(keyword) <= 2 else []
        matcher = None
        direction = 'DESC' if reverse else 'ASC'
        last = None
        while True:
            page_clauses, page_args = list(clauses), list(args)
            if last is not None:
                page_clauses.append(f'(digits,cursor) {"<" if reverse else ">"} (?,?)')
                page_args.extend((len(last), last))
            where = ' WHERE ' + ' AND '.join(page_clauses) if page_clauses else ''
            with self._lock:
                rows = self._db.execute('SELECT ' + columns + ' FROM events' + where
                    + f' ORDER BY digits {direction},cursor {direction} LIMIT 128', predicate_args + page_args).fetchall()
                if keyword:
                    for index, (cursor, rowid, body_start, body_size, body_key, matches) in enumerate(rows):
                        if accept is not None:
                            metadata = self.read_window(int(cursor), 0, 0, accept=accept, retrieval_projection=retrieval_projection)
                            if metadata is None:
                                rows[index] = cursor, False
                                continue
                            # A caller callback can reenter the snapshot. Read
                            # the current descriptor after its authorization.
                            rowid, body_start, body_size, body_key = self._db.execute(
                                'SELECT rowid,text_start,text_bytes,digest FROM events WHERE cursor=?', (cursor,)).fetchone()
                            if not self._short_candidate(body_key, keyword, rowid, body_start, body_size):
                                rows[index] = cursor, False
                                continue
                            if body_size <= 8192:
                                raw = self._db.execute('SELECT ' + _TEXT_JSON +
                                    ' FROM events WHERE cursor=?', (cursor,)).fetchone()[0]
                                matches = keyword in _text_value(raw).lower()
                        if accept is None and matches is None and not self._short_candidate(body_key, keyword, rowid, body_start, body_size):
                            rows[index] = cursor, False
                            continue
                        if matches is None:
                            if matcher is None:
                                matcher = LowerMatcher(keyword)
                            with self._db.blobopen('events', 'wire', rowid, readonly=True) as body:
                                matches = matcher.contains(body, body_start, body_size)
                        if not matches:
                            self._short_terms.remember_miss(body_key, keyword)
                        rows[index] = cursor, matches
            if not rows: return
            for cursor, matches in rows:
                if matches: yield int(cursor)
            last = rows[-1][0]

    def segment_count(self, segment):
        # Trigger-maintained derived metadata shares each event statement and
        # shard rollback; reading a day's count never loads its event bodies.
        with self._lock:
            row = self._db.execute('SELECT count FROM event_segments WHERE segment=?', (segment,)).fetchone()
            return row[0] if row else 0

    def clear_segment(self, segment):
        # Bounded batches also invalidate weak/hot entries without loading bodies.
        with self.shard():
            while True:
                rows = self._db.execute('SELECT cursor FROM events WHERE segment=? LIMIT 128',
                                        (segment,)).fetchall()
                if not rows: return
                for row in rows: del self[int(row[0])]

    def _remember(self, event, size, digest):
        # Request-only evidence identity of the existing verified whole wire.
        # This private attribute is absent from dataclass fields and JSONL.
        object.__setattr__(event, '_snapshot_wire_digest', digest)
        key = event.cursor
        self._live[key] = event
        previous = self._hot.pop(key, None)
        if previous is not None: self._cache_bytes -= previous[1]
        if size <= self.MAX_CACHE_BYTES:
            self._hot[key] = (event, size)
            self._cache_bytes += size
        while self._hot and (len(self._hot) > self.MAX_CACHE_EVENTS
                             or self._cache_bytes > self.MAX_CACHE_BYTES):
            _, (_, removed) = self._hot.popitem(last=False)
            self._cache_bytes -= removed

    def __getitem__(self, key):
        with self._lock:
            event = self._live.get(key)
            if event is not None:
                if key in self._hot: self._hot.move_to_end(key)
                return event
            row = self._db.execute('SELECT wire,digest FROM events WHERE cursor=?', (str(key),)).fetchone()
            if row is None: raise KeyError(key)
            event = self._decode(row[0])
            self._remember(event, _wire_size(row[0]), row[1])
            return event

    def _metadata_wire(self, rowid, start, size):
        with self._db.blobopen('events', 'wire', rowid, readonly=True) as source:
            prefix = source.read(start)
            source.seek(start + size)
            suffix = source.read(len(source) - start - size)
        return (prefix + suffix).decode('utf-8')

    def read_window(self, key, offset, count, *, accept, retrieval_projection=False):
        """Authorize projected metadata, then read only the requested body span.

        Four immutable-body cursor hints retain no text, handles or authority.
        Metadata required by the authorization/header contract can still grow.
        """
        with self._lock:
            query = 'SELECT rowid,digest,text_start,text_bytes,text_chars FROM events WHERE cursor=?'
            descriptor = self._db.execute(query, (str(key),)).fetchone()
            if descriptor is None:return None
            rowid, digest, start, size, total = descriptor
            projection = (self._db.execute('SELECT retrieval_wire FROM events WHERE cursor=?', (str(key),)).fetchone()[0]
                          if retrieval_projection else None)
            if projection is None:
                projection = self._metadata_wire(rowid, start, size)
            metadata = self._decode(projection)
            if not accept(metadata):return None
            if self._db.execute(query, (str(key),)).fetchone() != descriptor:
                raise ValueError('event snapshot changed during window authorization')
            text = ''
            if count > 0 and offset < total:
                identity = key, digest, start, size
                cursor = self._windows.get(identity)
                if cursor is None:cursor = WindowCursor()
                with self._db.blobopen('events', 'wire', rowid, readonly=True) as source:
                    text = cursor.read(source, start, size, offset, min(count, total - offset))
                self._windows[identity] = cursor
                self._windows.move_to_end(identity)
                while len(self._windows) > 4:self._windows.popitem(last=False)
            if offset == 0 and count >= total and total > 0:
                # Even a metadata projection carries the exact identity of its
                # authorized source, checked above in this same snapshot lock.
                object.__setattr__(metadata, '_snapshot_wire_digest', digest)
            return metadata, total, text

    def __setitem__(self, key, event):
        if key != event.cursor: raise ValueError('snapshot cursor mismatch')
        self.put(event)

    def __delitem__(self, key):
        with self._lock:
            # FTS deletes may allocate tombstone pages. Canonical deletion can
            # already follow removal of the authoritative shard, so optional
            # maintenance must not obstruct it. Retire this index instead;
            # exact scans remain available, and stale FTS rows are never used.
            self._retire_search()
            result = self._db.execute('DELETE FROM events WHERE cursor=?', (str(key),))
            if not result.rowcount: raise KeyError(key)
            self._live.pop(key, None)
            old = self._hot.pop(key, None)
            if old is not None: self._cache_bytes -= old[1]

    def __len__(self):
        with self._lock:
            return self._db.execute('SELECT count(*) FROM events').fetchone()[0]

    def __iter__(self):
        with self._lock:
            cursor = self._db.execute('SELECT cursor FROM events')
            try:
                for row in cursor: yield int(row[0])
            finally:
                cursor.close()

    def __eq__(self, other):
        if not isinstance(other, EventSnapshot): return super().__eq__(other)
        with self._lock, other._lock:
            if len(self) != len(other): return False
            left = self._db.execute('SELECT cursor,digest,segment FROM events ORDER BY cursor')
            right = other._db.execute('SELECT cursor,digest,segment FROM events ORDER BY cursor')
            try:
                return all(a == b for a, b in zip(left, right))
            finally:
                left.close(); right.close()

    def cache_state(self):
        with self._lock:
            return {'events': len(self._hot), 'bytes': self._cache_bytes,
                    'max_events': self.MAX_CACHE_EVENTS, 'max_bytes': self.MAX_CACHE_BYTES}
