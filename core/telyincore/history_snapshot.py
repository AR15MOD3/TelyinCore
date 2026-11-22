"""Disposable incremental index for original session records.

Archives and the active JSONL remain authoritative. Stat identities include
ctime; this detects ordinary edits, replacements and restored mtimes, not a
hostile filesystem that can forge all metadata. Every changed archive is hashed
and validated again before any cached result is published. No access decision
is cached: callers must apply their current visibility to each result.
"""
from collections import OrderedDict
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import weakref

from .note import Record
from .file_signature import signature, descriptor_signature
from .source_bytes import checked_open
from .derived_cache import CacheDirectory
from .search_growth import SearchGrowth
from .json_text_window import span as text_span, window as text_window, FoldedMatcher, WindowCursor

_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()
_MAX_SESSIONS = 4
_DEFAULTS = asdict(Record())
# Listing/window rendering and live access checks use Record metadata, never
# image payloads or extension objects. Keep those only in the original wire
# and semantic identity; a private preview must not duplicate them on disk or
# decode them for every query. Present fields retain their exact values.
_PREVIEW_FIELDS = tuple(key for key in _DEFAULTS if key != 'images')


def _json_members(value):
    # Sources have already passed JSON parsing: object keys are strings.
    # Explicit frames avoid imposing a lower recursion limit than that parser.
    mapping = isinstance(value, dict)
    yield True, b'{' if mapping else b'['
    members = sorted(value.items()) if mapping else value
    for index, item in enumerate(members):
        if index:
            yield True, b', '
        if mapping:
            key, item = item
            yield False, key
            yield True, b': '
        yield False, item
    yield True, b'}' if mapping else b']'


def _semantic_digest(row):
    # Preserve sorted stdlib JSON identity, including float spelling, default
    # fields, whitespace, surrogate values and the excluded reference itself.
    values = {k: v for k, v in (_DEFAULTS | row).items() if k != 'record_ref'}
    # Common small flat records keep the fast C encoder. This preflight bounds
    # both field count and string input; escaping can expand it by at most 6x.
    budget = 4096
    if len(values) <= 64:
        for key, value in values.items():
            budget -= len(key) + (len(value) if isinstance(value, str) else 40)
            if (budget < 0 or isinstance(value, (dict, list, tuple)) and value
                    or isinstance(value, int) and value.bit_length() > 128):
                break
        else:
            return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False)
                                  .encode('utf-8', 'surrogatepass')).digest()
    digest = hashlib.sha256()
    stack = [iter(((False, values),))]
    while stack:
        try:
            literal, value = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if literal:
            digest.update(value)
        elif isinstance(value, (dict, list, tuple)):
            stack.append(iter(_json_members(value)))
        elif isinstance(value, str):
            digest.update(b'"')
            for offset in range(0, len(value), 4096):
                # JSON escaping is character-local. Do not assemble either a
                # full encoded scalar or the full record before hashing it.
                piece = json.dumps(value[offset:offset + 4096], ensure_ascii=False)[1:-1]
                digest.update(piece.encode('utf-8', 'surrogatepass'))
            digest.update(b'"')
        else:
            digest.update(json.dumps(value, ensure_ascii=False).encode('utf-8'))
    return digest.digest()


class _RetryWithoutSearch(ValueError):
    """A rolled-back warm refresh may be rebuilt before publishing any rows."""


def _release(db, directory):
    try: db.close()
    finally: directory.cleanup()


def _stamp(path):
    return signature(path)


class HistorySnapshot:
    MAX_SEARCH_SOURCE_BYTES = 32 * 1024 * 1024
    MAX_SEARCH_GROWTH_BYTES = 16 * 1024 * 1024

    def __init__(self):
        directory = CacheDirectory()
        try:
            db = sqlite3.connect(str(Path(directory.name)/'snapshot.sqlite3'), check_same_thread=False)
            db.execute('PRAGMA cache_size=-2048')
            db.execute('PRAGMA temp_store=FILE')
            db.execute('PRAGMA synchronous=OFF')  # Disposable derived data only.
            db.execute('PRAGMA recursive_triggers=ON')
            db.executescript('''
CREATE TABLE record_bodies(key BLOB PRIMARY KEY, wire TEXT NOT NULL, preview TEXT NOT NULL,
                           copies INTEGER NOT NULL CHECK(copies>=0),
                           text_start INTEGER NOT NULL, text_bytes INTEGER NOT NULL);
CREATE TABLE entries(file TEXT, position INTEGER, reference TEXT, digest BLOB, body BLOB NOT NULL,
                     PRIMARY KEY(file, position));
CREATE INDEX entries_reference ON entries(reference);
CREATE TABLE records(reference TEXT PRIMARY KEY, file TEXT, position INTEGER,
                     source_bytes INTEGER NOT NULL CHECK(source_bytes>=0));
CREATE INDEX records_order ON records(file,position);
CREATE TABLE sources(file TEXT PRIMARY KEY, stamp TEXT NOT NULL, prefix TEXT NOT NULL, lines INTEGER NOT NULL, terminated INTEGER NOT NULL);
CREATE TEMP TABLE observed(file TEXT PRIMARY KEY, path TEXT NOT NULL, stamp TEXT NOT NULL, prefix TEXT, lines INTEGER, terminated INTEGER);
CREATE TEMP TABLE affected(reference TEXT PRIMARY KEY);
CREATE TABLE search_budget(bytes INTEGER NOT NULL);
INSERT INTO search_budget VALUES(0);
CREATE TRIGGER body_insert AFTER INSERT ON entries BEGIN
 UPDATE record_bodies SET copies=copies+1 WHERE key=new.body;
END;
CREATE TRIGGER body_delete AFTER DELETE ON entries BEGIN
 UPDATE record_bodies SET copies=copies-1 WHERE key=old.body;
 DELETE FROM record_bodies WHERE key=old.body AND copies=0;
END;
CREATE TRIGGER budget_insert AFTER INSERT ON records BEGIN
 UPDATE search_budget SET bytes=bytes+new.source_bytes;
END;
CREATE TRIGGER budget_delete AFTER DELETE ON records BEGIN
 UPDATE search_budget SET bytes=bytes-old.source_bytes;
END;
''')
            search_available = (sqlite3.sqlite_version_info >= (3, 34, 0) and
                bool(db.execute("SELECT sqlite_compileoption_used('ENABLE_FTS5')").fetchone()[0]))
        except BaseException:
            if 'db' in locals(): db.close()
            directory.cleanup()
            raise
        self.db = db
        self.lock = threading.RLock()
        self._windows = OrderedDict()
        from .listing_metadata import ListingMetadata
        self._listing = ListingMetadata()
        from .short_terms import ShortTerms
        self._short_terms = ShortTerms()
        self._search_growth = SearchGrowth()
        self.search_ready = False
        # Optional acceleration: older/custom SQLite builds retain the exact
        # scanning path. Production Windows and Linux both provide FTS5 trigram.
        self.search_available = search_available
        self.cleanup = weakref.finalize(self, _release, db, directory)
        self._restart_attempted = False
        self._restart_key = None
        self._restart_saved = None
        self._revision = 0

    def _restart_metadata(self):
        return {'kind':'history', 'inventory':self._restart_token(content=False), 'available':self.search_available, 'ready':self.search_ready,
                'growth':self._search_growth.used_bytes,
                'source_budget':self.MAX_SEARCH_SOURCE_BYTES, 'growth_budget':self.MAX_SEARCH_GROWTH_BYTES}

    def _restart_state(self):
        return self._revision, self.search_available, self.search_ready, self._search_growth.used_bytes

    def _restart_token(self, table='sources', *, content=True):
        # table is one of two private constants, never a tool argument.
        digest = hashlib.sha256()
        columns='file,stamp,prefix' if content else 'file,stamp'
        for row in self.db.execute('SELECT '+columns+' FROM '+table+' ORDER BY file'):
            digest.update(json.dumps(row,ensure_ascii=True,separators=(',',':')).encode('ascii'))
            digest.update(b'\n')
        return digest.hexdigest()

    def restart_load(self, session, paths_factory, safe):
        if self._restart_attempted:return
        self._restart_attempted = True
        from . import persistent_index as images
        self._restart_key = images.identity('history',session)
        if not images.available(self._restart_key):return
        try:
            self.db.execute('CREATE TEMP TABLE restart_sources(file TEXT PRIMARY KEY,stamp TEXT,prefix TEXT,path TEXT)')
            from contextlib import closing
            with self.db, closing(paths_factory()) as paths:
                for source in paths:
                    path=source.absolute()
                    safe(path);stamp=signature(path);safe(path)
                    key=('1:' if path==session else '0:')+os.path.normcase(path.name)
                    self.db.execute('INSERT INTO restart_sources VALUES(?,?,NULL,?)',(key,json.dumps(stamp),str(path)))
            inventory=self._restart_token('restart_sources',content=False)
            # A rejection hint only. Matching native identities still require
            # full original-byte validation, never metadata-only acceptance.
            if not images.available(self._restart_key,inventory=inventory):return
            with self.db:
                cursor=self.db.execute('SELECT file,stamp,path FROM restart_sources ORDER BY file')
                try:
                    for key,expected,path in cursor:
                        stamp,digest=images.digest_file(Path(path),safe)
                        if json.dumps(stamp)!=expected:return
                        self.db.execute('UPDATE restart_sources SET prefix=? WHERE file=?',(digest,key))
                finally:cursor.close()
            token=self._restart_token('restart_sources')
            def valid(meta):
                return (isinstance(meta,dict) and meta.get('kind')=='history'
                    and all(type(meta.get(k)) is bool for k in ('available','ready'))
                    and type(meta.get('growth')) is int and 0<=meta['growth']<2**63
                    and meta.get('source_budget')==self.MAX_SEARCH_SOURCE_BYTES
                    and meta.get('growth_budget')==self.MAX_SEARCH_GROWTH_BYTES
                    and (not meta['available'] or self.search_available)
                    and (not meta['ready'] or meta['available']))
            meta=images.restore(self.db,self._restart_key,token,valid)
            if meta is not None:
                self.search_available,self.search_ready=meta['available'],meta['ready']
                self._search_growth.used_bytes=meta['growth']
                self._restart_saved=self._restart_state()
        except (OSError,ValueError,sqlite3.Error):
            # The ordinary sync still checks the original inventory and errors.
            # Failed SQLite backup rolls back its destination transaction.
            if self.db.in_transaction:self.db.rollback()
        finally:
            self.db.execute('DROP TABLE IF EXISTS temp.restart_sources')

    def restart_save(self):
        if self._restart_key is None or self.db.in_transaction:return
        state=self._restart_state()
        if state==self._restart_saved:return
        self._restart_saved=state
        try:
            from . import persistent_index as images
            if self.db.execute('SELECT bytes FROM search_budget').fetchone()[0]<images.MIN_SOURCE_BYTES:return
            images.publish(self.db,self._restart_key,self._restart_token(),self._restart_metadata())
        except (OSError,ValueError,sqlite3.Error):pass

    def sync(self, session, paths, safe, parse):
        def key(path):
            # Match the native Path identity used by the cache.
            # A Windows case alias must update/delete the same stored source.
            return ('1:' if path == session else '0:') + os.path.normcase(path.name)
        with (self._search_growth.limit(self.db, self.MAX_SEARCH_GROWTH_BYTES)
              if self.search_ready else nullcontext()), self.db:
            # Keep per-source identities on disk too. JSON preserves unsigned
            # native file IDs without SQLite's signed-integer coercion. The
            # observed inventory and source rows commit/roll back together.
            self.db.execute('DELETE FROM observed')
            for path in paths:
                safe(path)
                current = _stamp(path)
                safe(path)
                self.db.execute('INSERT INTO observed(file,path,stamp) VALUES(?,?,?)',
                                (key(path), str(path), json.dumps(current)))
            changed = '''SELECT o.file,o.path,o.stamp FROM observed o
LEFT JOIN sources s ON s.file=o.file WHERE s.file IS NULL OR s.stamp!=o.stamp'''
            removed = 'SELECT file FROM sources WHERE file NOT IN (SELECT file FROM observed)'
            if (self.db.execute(changed + ' LIMIT 1').fetchone() is None
                    and self.db.execute(removed + ' LIMIT 1').fetchone() is None):
                self.db.execute('DELETE FROM observed')
                return
            # Verify the old active prefix before reusing its parsed entries.
            # A larger file may also contain edits to earlier lines. This
            # optimization saves JSON/SQL work, not source verification I/O.
            append_file = ''; append_prefix = None; append_size = append_lines = 0
            active = self.db.execute('SELECT * FROM (' + changed + ') WHERE file=?',
                                     (key(session),)).fetchone()
            old = self.db.execute('SELECT stamp,prefix,lines,terminated FROM sources WHERE file=?',
                                  (key(session),)).fetchone()
            if active and old and old[3]:
                initial = tuple(json.loads(active[2])); previous = tuple(json.loads(old[0]))
                if initial[:2] == previous[:2] and initial[2] > previous[2]:
                    safe(session)
                    digest = hashlib.sha256(); remaining = previous[2]
                    with checked_open(session) as stream:
                        if descriptor_signature(stream.fileno()) != initial:
                            raise ValueError('原始记录追加核验时身份变化，检索不完整')
                        while remaining:
                            block = stream.read(min(65536, remaining))
                            if not block: raise ValueError('原始记录追加核验时缩短，检索不完整')
                            digest.update(block); remaining -= len(block)
                        if descriptor_signature(stream.fileno()) != initial:
                            raise ValueError('原始记录追加核验期间变化，检索不完整')
                    safe(session)
                    if _stamp(session) != initial:
                        raise ValueError('原始记录追加核验后变化，检索不完整')
                    if digest.hexdigest() == old[1]:
                        append_file, append_prefix = active[0], digest
                        append_size, append_lines = previous[2], old[2]
            self.db.execute('DELETE FROM affected')
            changed_files = 'SELECT file FROM (' + changed + ') UNION ' + removed
            self.db.execute('INSERT OR IGNORE INTO affected SELECT reference FROM entries '
                            'WHERE file IN (' + changed_files + ') AND file!=?', (append_file,))
            self.db.execute('DELETE FROM entries WHERE file IN (' + changed_files + ') AND file!=?', (append_file,))
            for file, location, stamp in self.db.execute(changed + ' ORDER BY o.file'):
                path, initial = Path(location), tuple(json.loads(stamp))
                prefix = append_prefix.copy() if file == append_file else hashlib.sha256()
                safe(path)
                with checked_open(path) as stream:
                    if descriptor_signature(stream.fileno()) != initial:
                        raise ValueError('原始记录打开时身份变化，检索不完整')
                    offset = append_size if file == append_file else 0
                    stream.seek(offset)
                    remaining = initial[2] - offset
                    number = append_lines if file == append_file else 0
                    line = b''
                    while remaining:
                        # A concurrent producer cannot extend this scan forever.
                        # Exact initial bytes include a final unterminated line;
                        # the final stamp check refuses any observed source drift.
                        line = stream.readline(remaining)
                        if not line:
                            raise ValueError('原始记录在读取过程中缩短，检索不完整')
                        remaining -= len(line)
                        number += 1
                        row = next(parse(line))[1]
                        prefix.update(line)
                        reference = row.get('record_ref') or f'{prefix.hexdigest()}:{number}'
                        digest = _semantic_digest(row)
                        preview = {key: row[key] for key in _PREVIEW_FIELDS if key in row}
                        preview.update(text=row['text'][:240], _text_length=len(row['text']))
                        # Only identical original bytes share storage. Semantic
                        # digest/reference identity and every physical source
                        # occurrence remain separate and are validated below.
                        body = hashlib.sha256(line).digest()
                        wire = line.decode('utf-8')
                        start, size = text_span(wire) if len(row['text']) > 240 else (0, 0)
                        self.db.execute('INSERT OR IGNORE INTO record_bodies VALUES(?,?,?,0,?,?)',
                            (body, wire,
                             json.dumps(preview, ensure_ascii=True, separators=(',', ':')), start, size))
                        self.db.execute('INSERT INTO entries VALUES(?,?,?,?,?)',
                            (file, number, reference, digest, body))
                        self.db.execute('INSERT OR IGNORE INTO affected VALUES(?)', (reference,))
                    if descriptor_signature(stream.fileno()) != initial:
                        raise ValueError('原始记录在读取过程中变化，检索不完整')
                if path != session and prefix.hexdigest() != path.stem:
                    raise ValueError('原始记录归档校验失败，检索不完整')
                if _stamp(path) != initial:
                    raise ValueError('原始记录在读取过程中变化，检索不完整')
                # A matching stat result alone does not enforce the no-links
                # contract when a path changes after inventory validation.
                safe(path)
                self.db.execute('UPDATE observed SET prefix=?,lines=?,terminated=? WHERE file=?',
                                (prefix.hexdigest(), number, int(not line or line.endswith(b'\n')), file))
            conflict = self.db.execute('''SELECT reference FROM entries
WHERE reference IN (SELECT reference FROM affected)
GROUP BY reference HAVING count(DISTINCT digest)>1 LIMIT 1''').fetchone()
            if conflict: raise ValueError('同一原始记录引用对应不同内容，检索不完整')
            if self.search_ready:
                self.db.execute('DELETE FROM record_search WHERE rowid IN '
                                '(SELECT rowid FROM records WHERE reference IN (SELECT reference FROM affected))')
            self.db.execute('DELETE FROM records WHERE reference IN (SELECT reference FROM affected)')
            self.db.execute('''INSERT INTO records
SELECT reference,file,position,length(CAST(b.wire AS BLOB)) FROM (
 SELECT reference,file,position,body,
 row_number() OVER (PARTITION BY reference ORDER BY file,position) AS occurrence
 FROM entries WHERE reference IN (SELECT reference FROM affected)
) e JOIN record_bodies b ON b.key=e.body WHERE occurrence=1''')
            if self.search_ready and self._within_search_budget():
                self._index_search_rows(' WHERE r.reference IN (SELECT reference FROM affected)')
            self.db.execute('DELETE FROM sources WHERE file IN (' + removed + ')')
            self.db.execute('INSERT OR REPLACE INTO sources SELECT o.file,o.stamp,o.prefix,o.lines,o.terminated '
                            'FROM observed o WHERE o.prefix IS NOT NULL')
            self.db.execute('DELETE FROM affected')
            self.db.execute('DELETE FROM observed')
        self._revision += 1

    def _index_search_rows(self, where=''):
        # Short text is already complete in the compact preview. Longer text
        # is projected from its validated span, without loading unrelated
        # original metadata just to build an optional text candidate index.
        cursor = self.db.execute('SELECT r.rowid,b.text_bytes,CASE WHEN b.text_bytes>0 '
                                 'THEN substr(CAST(b.wire AS BLOB),b.text_start+1,b.text_bytes) '
                                 'ELSE b.preview END FROM records r JOIN entries e '
                                 'ON e.file=r.file AND e.position=r.position '
                                 'JOIN record_bodies b ON b.key=e.body' + where)
        try:
            for rowid, size, value in cursor:
                # NUL can terminate tokenizer input; SQLite TEXT cannot accept
                # lone surrogates from escaped legacy JSON. Substitute only in
                # this candidate index. Exact matching and original reads keep
                # every original character, and surrogate queries bypass FTS.
                text = json.loads(b'"' + value + b'"') if size else json.loads(value)['text']
                folded = (text.casefold().replace('\0', ' ')
                          .encode('utf-8', 'replace').decode('utf-8'))
                self.db.execute('INSERT INTO record_search(rowid,folded) VALUES(?,?)', (rowid, folded))
        finally: cursor.close()

    def _ensure_search(self):
        if self.search_ready: return
        with self._search_growth.limit(self.db, self.MAX_SEARCH_GROWTH_BYTES), self.db:
            from .search_schema import options
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS record_search USING fts5(folded, tokenize='trigram case_sensitive 1'" + options() + ")")
            self.db.execute('DELETE FROM record_search')
            self._index_search_rows()
        self.search_ready = True

    def _within_search_budget(self):
        if self.db.execute('SELECT bytes FROM search_budget').fetchone()[0] <= self.MAX_SEARCH_SOURCE_BYTES:
            return True
        # Count the canonical records actually offered to FTS. The same record
        # in several archives does not spend this budget again; distinct
        # references sharing identical wire bytes still each count once.
        # Crossing the budget disables indexing for this cache's lifetime.
        self.search_available = self.search_ready = False
        return False

    def _prepare_search(self):
        outer_transaction = self.db.in_transaction
        try:
            self._ensure_search()
        except sqlite3.Error as error:
            if (getattr(error, 'sqlite_errorcode', None) != sqlite3.SQLITE_FULL
                    or error.__cause__ is not None or error.__context__ is not None
                    or outer_transaction or self.db.in_transaction):
                raise
            # The canonical snapshot was committed before this optional build.
            # Keep exact reads available when only the accelerator cannot grow.
            self.search_available = self.search_ready = False
            return False
        return True

    def nearby_previews(self, reference):
        """At most eight positions from the canonical source containing this exact reference."""
        location = self.db.execute('SELECT file,position FROM records WHERE reference=?', (reference,)).fetchone()
        if location is None:
            return
        path, at = location
        cursor = self.db.execute(
            'SELECT e.reference,b.preview FROM entries e JOIN record_bodies b ON b.key=e.body '
            'WHERE e.file=? AND e.position BETWEEN ? AND ? AND e.position!=? ORDER BY e.position',
            (path, at-4, at+4, at))
        try:
            for ref, preview in cursor:
                yield ref, json.loads(preview)
        finally:
            cursor.close()

    def rows(self, reference=None, *, keyword='', preview=False, window=None, visible=None, select=None, listing=False):
        # Preview is derived from validated source bytes, not a replacement for
        # original-reference reads. Keyword previews verify the entire body below.
        # Default callers still get wire and retain the candidate-only contract.
        # records selects one validated occurrence. Identical source bytes are
        # stored once even across overlapping archives; references and current
        # visibility are not deduplicated or cached by the shared body key.
        column = 'preview,b.rowid,b.text_start,b.text_bytes,b.key' if preview and keyword else ('preview' if preview else 'wire')
        if listing:
            if not preview or keyword or window is not None:raise ValueError('invalid listing projection')
            column='key,b.rowid'
        source = ('records r JOIN entries e ON e.file=r.file AND e.position=r.position '
                  'JOIN record_bodies b ON b.key=e.body')
        if reference is not None and window is not None:
            # Metadata is the same validated preview used by list reads. Only
            # explicit continuation reads opt into a bounded body window.
            cursor = self.db.execute(f'SELECT r.reference,b.preview,b.rowid,b.text_start,b.text_bytes,b.key '
                                     f'FROM {source} WHERE r.reference=?', (reference,))
            try:
                for ref, preview_wire, rowid, start, size, body_key in cursor:
                    row = json.loads(preview_wire)
                    if visible is not None and not visible(row.get('scope')):continue
                    offset, count = window
                    if offset >= row['_text_length']:
                        row['text'] = ''
                    elif row['_text_length'] <= 240:
                        row['text'] = row['text'][offset:offset + count]
                    else:
                        # SQLite row IDs can be reused after source replacement.
                        # Immutable original-byte identity separates positions;
                        # the current permission gate above always runs first.
                        key = (body_key, start, size)
                        position = self._windows.pop(key, None)
                        if position is None:position = WindowCursor()
                        self._windows[key] = position
                        while len(self._windows) > 4:self._windows.popitem(last=False)
                        with self.db.blobopen('record_bodies', 'wire', rowid, readonly=True) as body:
                            row['text'] = text_window(body, start, size, offset, count, cursor=position)
                    yield ref, row
            finally: cursor.close()
            return
        folded = keyword.casefold()
        if (reference is None and self.search_available and len(folded) >= 3
                and '\0' not in folded and not any(0xD800 <= ord(c) <= 0xDFFF for c in folded)
                and self._within_search_budget() and self._prepare_search()):
            # Treat punctuation, quotes and FTS operators as literal input.
            # A bounded literal prefix is a necessary condition for any exact
            # match; very long user input must not build an unbounded FTS phrase.
            phrase = '"' + folded[:128].replace('"', '""') + '"'
            # A broad MATCH otherwise materializes/sorts an unbounded rowid
            # inventory before returning the first record. Probe at most 257;
            # selective searches use only the bounded IDs, broad ones stream
            # canonical source order and retain the caller's exact filtering.
            candidates = self.db.execute('SELECT rowid FROM record_search '
                'WHERE record_search MATCH ? LIMIT 257', (phrase,)).fetchall()
            if len(candidates) > 256:
                cursor = self.db.execute(f'SELECT r.reference,b.{column} FROM {source} ORDER BY r.file,r.position')
            elif candidates:
                ids = [row[0] for row in candidates]
                placeholders = ','.join('?' for _ in ids)
                cursor = self.db.execute(f'SELECT r.reference,b.{column} FROM {source} '
                    f'WHERE r.rowid IN ({placeholders}) ORDER BY r.file,r.position', ids)
            else:
                return
        elif reference is None:
            cursor = self.db.execute(f'SELECT r.reference,b.{column} FROM {source} ORDER BY r.file,r.position')
        else:
            cursor = self.db.execute(f'SELECT r.reference,b.{column} FROM {source} WHERE r.reference=?', (reference,))
        try:
            with self._listing.scan() if listing and reference is None else nullcontext():
                matcher = FoldedMatcher(folded) if preview and keyword else None
                for result in cursor:
                    reference, wire = result[:2]
                    if listing:
                        key,body_rowid=result[1:]
                        row=self._listing.get(key)
                        if row is None:
                            # Warm metadata needs no transfer of large display
                            # fields from SQLite. A miss still reads the validated
                            # preview through this same locked snapshot.
                            pending=self.db.execute('SELECT preview FROM record_bodies WHERE rowid=?',(body_rowid,))
                            try:stored=pending.fetchone()
                            finally:pending.close()
                            if stored is None:raise ValueError('原始记录派生元数据不完整')
                            full=json.loads(stored[0])
                            row=self._listing.add(key,full)
                            if row is None:row=full
                    else:row = json.loads(wire)
                    # Ephemeral current-policy gate, before large body scans. The
                    # authoritative source inventory was already fully validated;
                    # no decision is cached and the caller still checks output.
                    body_work = preview and keyword and row['_text_length'] > 240
                    if body_work and visible is not None and not visible(row.get('scope')):continue
                    # Current list-category metadata can reject a long body before
                    # keyword matching. This predicate is never retained in a cache.
                    if body_work and select is not None and not select(row):continue
                    if preview and keyword:
                        if row['_text_length'] <= 240:
                            if folded not in row['text'].casefold(): continue
                        else:
                            rowid, start, size, body_key = result[2:]
                            if len(folded) <= 2:
                                def blocks():
                                    from .json_text_window import chunks
                                    with self.db.blobopen('record_bodies', 'wire', rowid, readonly=True) as body:
                                        yield from chunks(body, start, size)
                                if not self._short_terms.maybe(body_key, folded, blocks):
                                    continue
                            with self.db.blobopen('record_bodies', 'wire', rowid, readonly=True) as body:
                                matches = matcher.contains(body, start, size)
                            if not matches:
                                self._short_terms.remember_miss(body_key, folded)
                                continue
                    # A bounded short preview can reject a miss cheaply first.
                    # It still cannot leave this iterator without the live gate.
                    if not body_work and visible is not None and not visible(row.get('scope')):continue
                    if not body_work and select is not None and not select(row):continue
                    yield reference, row
        finally: cursor.close()


@contextmanager
def snapshot(session, paths, safe, parse, *, without_search=False, restart_paths=None):
    session = session.absolute()
    with _CACHE_LOCK:
        entry = _CACHE.pop(session, None)
        if entry is None: entry = HistorySnapshot()
        _CACHE[session] = entry
        while len(_CACHE) > _MAX_SESSIONS:
            # A generator in progress still owns its entry; finalization waits
            # until that reader releases it, even after LRU eviction.
            _CACHE.popitem(last=False)
    yielded = False
    had_search, outer_transaction = False, True
    try:
        with entry.lock:
            if restart_paths is not None and not without_search:
                entry.restart_load(session,restart_paths,safe)
            had_search, outer_transaction = entry.search_ready, entry.db.in_transaction
            if without_search:
                entry.search_available = entry.search_ready = False
            entry.sync(session, (p.absolute() for p in paths), safe, parse)
            yielded = True
            yield entry
            entry.restart_save()
    except sqlite3.Error as error:
        with _CACHE_LOCK:
            if _CACHE.get(session) is entry: _CACHE.pop(session)
        if (getattr(error, 'sqlite_errorcode', None) == sqlite3.SQLITE_FULL
                and not yielded and not without_search and had_search and not outer_transaction
                and not entry.db.in_transaction and error.__cause__ is None
                and error.__context__ is None):
            raise _RetryWithoutSearch('原始记录派生索引容量不足，需要重新校验来源') from error
        raise ValueError('原始记录派生索引不可用，检索不完整') from error
