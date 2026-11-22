"""Opt-in denial metadata across ordinary process restart, NOT host authority.

Caller serializes writes under its identity lock. No delete/reset API, secrets,
account configuration reads or process operations. File fsync is NOT a directory
power-loss proof. Root digest detects misbinding, NOT malicious tampering.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from runtime_identity import safe_path
from bot_stable_file import read_stable

MAX_RECORDS = 1024
MAX_RECORD_BYTES = 4096
_NAME = re.compile(r'[0-9a-f]{32}\.json\Z')


class RevocationJournal:
    def __init__(self, core):
        self.core = Path(core).absolute()
        safe_path(self.core.parent, self.core)
        self.directory = self.core/'data/bot-host-revocations'
        self.digest = hashlib.sha256(str(self.core.resolve()).encode('utf-8')).hexdigest()

    def load(self, allowed):
        try:
            safe_path(self.core.parent, self.directory)
            if not self.directory.exists():
                return set()
            result = set()
            with os.scandir(self.directory) as entries:
                for index, entry in enumerate(entries):
                    if index >= MAX_RECORDS or not _NAME.fullmatch(entry.name):
                        raise ValueError()
                    path = self.directory/entry.name
                    safe_path(self.core.parent, path)
                    if not entry.is_file(follow_symlinks=False):
                        raise ValueError()
                    raw,_ = read_stable(self.core.parent,path,MAX_RECORD_BYTES)
                    def unique(pairs):
                        record = {}
                        for k,v in pairs:
                            if k in record:
                                raise ValueError()
                            record[k] = v
                        return record
                    record = json.loads(raw, object_pairs_hook=unique)
                    if (type(record) is not dict or set(record) != {'version','core_digest','scope','nonce'}
                            or type(record['version']) is not int or record['version'] != 1
                            or record['core_digest'] != self.digest
                            or type(record['scope']) is not str or record['scope'] not in allowed
                            or record['nonce'] != entry.name[:-5]):
                        raise ValueError()
                    result.add(record['scope'])
            return result
        except Exception:
            raise ValueError('host revocation journal unavailable') from None

    def record(self, scope, allowed):
        if scope not in allowed:
            raise ValueError('unknown revocation scope')
        if scope in self.load(allowed):
            return
        safe_path(self.core.parent, self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        with os.scandir(self.directory) as entries:
            for index, _ in enumerate(entries):
                if index >= MAX_RECORDS - 1:
                    raise ValueError('revocation journal record limit')
        nonce = uuid.uuid4().hex
        path = self.directory/(nonce+'.json')
        safe_path(self.core.parent, path)
        raw = json.dumps(dict(version=1,core_digest=self.digest,scope=scope,nonce=nonce),
                         sort_keys=True,ensure_ascii=True).encode('ascii')
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError('revocation record exceeds limit')
        # Final-name exclusive creation deliberately leaves incomplete evidence
        # on write/fsync failure. A fresh registry MUST reject such evidence.
        with path.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        persisted,_=read_stable(self.core.parent,path,MAX_RECORD_BYTES)
        if persisted!=raw:raise ValueError('revocation publication changed')
