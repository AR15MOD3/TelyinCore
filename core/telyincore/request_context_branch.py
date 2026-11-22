"""Explicit model-specific request branches over an unchanged durable timeline.

Membership is captured at trusted append boundaries, never by matching text.
No reasoning is manufactured and no durable record is rewritten or replayed.
"""
from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from collections import Counter
from copy import deepcopy
from functools import wraps
import json
import os
from pathlib import Path
import secrets
import tempfile
from urllib.parse import urlsplit

from .provider_record_replay import record_ids
from .snapshot_json import loads as snapshot_loads

MAX_BRANCHES = 16
MAX_MEMBERS = 4096
MAX_METADATA_BYTES = 1024 * 1024
_MISSING_INTENT = object()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def identity_for(config, scope):
    url = urlsplit(config.base_url)
    values = getattr(config, 'values', None)
    values = values() if callable(values) else {}
    return {'provider': _digest([url.scheme, url.hostname, url.port, url.path]),
            'model': str(config.model), 'auth': str(values.get('auth', 'api_key')),
            'scope': scope}


def _hash(value):
    return (isinstance(value, str) and len(value) == 64
            and all(c in '0123456789abcdef' for c in value))


class BranchConflict(ValueError):
    """The opened branch snapshot no longer matches its durable authority."""


def _serialized(function):
    @wraps(function)
    def guarded(self, *args, **kwargs):
        with self._lock:
            return function(self, *args, **kwargs)
    return guarded


def _mutation(function):
    @wraps(function)
    def guarded(self, *args, **kwargs):
        from .source_guard import lease
        with self._lock, lease(self.path):
            self._check_disk()
            return function(self, *args, **kwargs)
    return guarded


@contextmanager
def _session_authority(session):
    from .source_guard import lease
    with lease(session.path):
        session._check_disk()
        yield


def _decode(raw):
    data = snapshot_loads(raw)
    if (not isinstance(data, dict) or set(data) != {'version', 'entries'}
            or type(data['version']) is not int or data['version'] != 1
            or not isinstance(data['entries'], dict)
            or len(data['entries']) > MAX_BRANCHES):
        raise ValueError('invalid request branch metadata')
    for key, entry in data['entries'].items():
        if (not isinstance(entry, dict)
                or set(entry) != {'identity', 'context', 'members'}):
            raise ValueError('invalid request branch entry')
        identity = entry['identity']
        if (not isinstance(identity, dict)
                or set(identity) != {'provider', 'model', 'auth', 'scope'}
                or not _hash(identity.get('provider'))
                or any(not isinstance(v, str) or len(v) > 256 for v in identity.values())
                or key != _digest(identity) or not _hash(entry['context'])
                or not isinstance(entry['members'], list)
                or len(entry['members']) > MAX_MEMBERS
                or any(not _hash(v) for v in entry['members'])
                or len(set(entry['members'])) != len(entry['members'])):
            raise ValueError('invalid request branch identity or members')
    return data['entries']


def _encode(entries):
    raw = json.dumps({'version': 1, 'entries': entries}, sort_keys=True).encode()
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError('request branch metadata exceeds limit')
    return raw


def _publish_intent(path, intent):
    """Expose either a complete journal or none; never replace an older intent."""
    raw = json.dumps(intent, sort_keys=True).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.tmp-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(name, path)
        if os.name != 'nt':
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        Path(name).unlink(missing_ok=True)


def _read_intent(path, limit):
    # A dangling link is unresolved authority, not an absent transaction.
    if path.is_symlink():
        raise ValueError('invalid branch recovery intent path')
    try:
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        return _MISSING_INTENT
    if len(raw) > limit:
        raise ValueError('branch recovery intent exceeds limit')
    return snapshot_loads(raw)


class RequestBranches:
    """Inactive by default. Stale or malformed authority never widens a view."""

    def __init__(self, path):
        self.path = Path(path).absolute()
        self._path = self.path
        self._lock = threading.RLock()
        self._disk_blob = self._read_disk()
        self._entries = {} if self._disk_blob is None else _decode(self._disk_blob)

    def _read_disk(self):
        if self.path != self._path or self.path.resolve() != self.path:
            raise ValueError('invalid request branch path')
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(MAX_METADATA_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(raw) > MAX_METADATA_BYTES:
            raise ValueError('invalid request branch file')
        return raw

    def _check_disk(self):
        if self._read_disk() != self._disk_blob:
            raise BranchConflict('request branch disk changed; reopen before continuing')

    @property
    @_serialized
    def entries(self):
        self._check_disk()
        return deepcopy(self._entries)

    @_serialized
    def get(self, identity):
        self._check_disk()
        return deepcopy(self._entries.get(_digest(identity)))

    @_serialized
    def visibility_revision(self, identity):
        """A fresh context identifies each branch; this owner can only append members."""
        self._check_disk()
        key = _digest(identity)
        entry = self._entries.get(key)
        return key, (entry['context'], len(entry['members'])) if entry is not None else None

    @_mutation
    def _save(self, entries):
        raw = _encode(entries)
        validated = _decode(raw)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + '.tmp-')
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
        self._disk_blob = raw
        self._entries = validated

    @property
    def append_intent_path(self):
        return self.path.with_name(self.path.name+'.append-intent.json')

    @_mutation
    def recover_append(self, session):
        """Finish metadata for exactly the journaled append; never replay a write."""
        from .source_guard import lease
        with lease(self.path), _session_authority(session):
            self._recover_append(session)
            self._recover_claim(session)

    @property
    def claim_intent_path(self):
        return self.path.with_name(self.path.name+'.claim-intent.json')

    def _recover_claim(self, session):
        path = self.claim_intent_path
        intent = _read_intent(path, 1024 * 1024)
        if intent is _MISSING_INTENT:
            return
        if (not isinstance(intent, dict) or set(intent) != {'version','identity','count','prefix','wanted'}
                or type(intent['version']) is not int or intent['version'] != 1
                or type(intent['count']) is not int or intent['count'] < 0
                or not _hash(intent['prefix']) or not isinstance(intent['wanted'], list)
                or len(intent['wanted']) > MAX_MEMBERS
                or any(not _hash(item) for item in intent['wanted'])
                or len(set(intent['wanted'])) != len(intent['wanted'])
                or self.get(intent['identity']) is None):
            raise ValueError('invalid branch claim schema')
        rows = tuple(session.records());ids = record_ids(rows)
        if len(rows) < intent['count'] or _digest(ids[:intent['count']]) != intent['prefix']:
            raise ValueError('branch claim recovery timeline mismatch')
        wanted = set(intent['wanted'])
        recovered = tuple(row for row, ident in zip(rows[:intent['count']], ids) if ident in wanted)
        if (len(recovered) != len(wanted)
                or any(row.scope != intent['identity']['scope'] for row in recovered)):
            raise ValueError('branch claim recovery identity mismatch')
        self.claim(rows, recovered, intent['identity'])
        path.unlink()

    @_mutation
    def claim_transaction(self, session, added, identity):
        """Persist exact input membership before its metadata commit, no replay.

        Input Session writes precede this boundary. A crash before this intent
        exists grants no membership; recovery never guesses from matching text.
        """
        from .source_guard import lease
        with lease(self.path), _session_authority(session):
            self._recover_claim(session)
            added = tuple(added)
            if self.get(identity) is None:
                return
            rows = tuple(session.records());ids = record_ids(rows)
            wanted_objects = {id(row) for row in added if row.scope == identity['scope']}
            selected = [(row, ident) for row, ident in zip(rows, ids) if id(row) in wanted_objects]
            if {id(row) for row, _ in selected} != wanted_objects:
                raise ValueError('branch claim missing original input')
            wanted = [ident for _, ident in selected if ident is not None]
            if not wanted:
                return
            if len(wanted) > MAX_MEMBERS:
                raise ValueError('branch claim limit reached')
            self._claimed_entries(rows, added, identity)
            intent = {'version':1,'identity':dict(identity),'count':len(rows),
                      'prefix':_digest(ids),'wanted':wanted}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _publish_intent(self.claim_intent_path, intent)
            try:
                self.claim(rows, added, identity)
                self.claim_intent_path.unlink()
            except BaseException as error:
                error.branch_claim_intent = str(self.claim_intent_path)
                raise

    def _recover_append(self, session):
        path = self.append_intent_path
        intent = _read_intent(path, 65536)
        if intent is _MISSING_INTENT: return
        if not isinstance(intent,dict) or set(intent) != {'version','identity','count','before','after'} or type(intent['version']) is not int or intent['version'] != 1:
            raise ValueError('invalid branch append intent schema')
        count=intent['count'];rows=tuple(session.records());ids=record_ids(rows)
        if type(count) is not int or count < 0 or not _hash(intent['before']) or not _hash(intent['after']) or self.get(intent['identity']) is None:
            raise ValueError('invalid branch append identity')
        if len(rows) == count and _digest(ids) == intent['before']:
            path.unlink()  # No durable append occurred. Do not manufacture it.
            return
        if len(rows) < count+1 or _digest(ids[:count]) != intent['before'] or _digest(ids[:count+1]) != intent['after']:
            raise ValueError('branch append recovery does not match durable timeline')
        if rows[count].scope != intent['identity']['scope']:
            raise ValueError('branch append recovery scope mismatch')
        self.claim(rows, (rows[count],), intent['identity'])
        path.unlink()

    @_mutation
    def append(self, session, record, identity):
        """Journal the provider identity before writing Session, then claim once."""
        from .source_guard import lease
        with lease(self.path), _session_authority(session):
            self.recover_append(session)
            if self.get(identity) is None:
                session.append(record)
                return
            rows=tuple(session.records())
            if record.scope != identity['scope']:
                raise ValueError('request branch append scope mismatch')
            self._claimed_entries((*rows, record), (record,), identity)
            intent={'version':1,'identity':dict(identity),'count':len(rows),
                    'before':_digest(record_ids(rows)), 'after':_digest(record_ids((*rows,record)))}
            self.path.parent.mkdir(parents=True,exist_ok=True)
            path=self.append_intent_path
            _publish_intent(path, intent)
            try:
                session.append(record)
                self.claim(session.records(), (record,), identity)
                path.unlink()
            except BaseException as error:
                error._branch_append_intent = str(path)
                error.add_note('branch append unresolved; recovery must not append again')
                raise

    @_mutation
    def activate(self, identity):
        """Explicit administration only; creating an empty branch claims no old input."""
        if self.get(identity) is not None or len(self.entries) >= MAX_BRANCHES:
            raise ValueError('request branch already exists or limit reached')
        entries = dict(self.entries)
        entries[_digest(identity)] = {'identity': dict(identity),
                                     'context': secrets.token_hex(32), 'members': []}
        self._save(entries)

    @_mutation
    def claim(self, rows, added, identity):
        entries = self._claimed_entries(rows, added, identity)
        if entries is not None:
            self._save(entries)

    def _claimed_entries(self, rows, added, identity):
        rows, added = tuple(rows), tuple(added)
        entry = self.get(identity)
        if entry is None:
            return
        wanted = {id(row) for row in added if row.scope == identity['scope']}
        counts = Counter(map(id, rows))
        if any(counts[key] > 1 for key in wanted):
            raise ValueError('request branch append identity ambiguous')
        members = list(entry['members'])
        ids = record_ids(rows)
        if members and not set(members).intersection(ids):
            raise ValueError('request branch history changed; explicit renewal required')
        found = set()
        for row, ident in zip(rows, ids):
            if id(row) in wanted:
                found.add(id(row))
                if ident is not None and ident not in members:
                    members.append(ident)
        if found != wanted:
            raise ValueError('request branch append identity missing')
        if len(members) > MAX_MEMBERS:
            raise ValueError('request branch member limit reached')
        if members != entry['members']:
            entries = dict(self.entries)
            entries[_digest(identity)] = {**entry, 'members': members}
            _encode(entries)  # Check total bytes before any Session append.
            return entries

    @_serialized
    def predicate(self, rows, identity):
        rows = tuple(rows)
        entry = self.get(identity)
        if entry is None:
            return None
        members = set(entry['members'])
        allowed = {id(row): row for row, ident in zip(rows, record_ids(rows)) if ident in members}
        counts = Counter(map(id, rows))
        if any(counts[key] > 1 for key in allowed):
            raise ValueError('request branch record identity ambiguous')
        # An existing branch cannot silently lose its history after replacement.
        if members and not allowed:
            raise ValueError('request branch history changed; explicit renewal required')
        return lambda row: allowed.get(id(row)) is row
