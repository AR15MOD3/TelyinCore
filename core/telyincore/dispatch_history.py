"""Private result spool and live-object invocation identities.

The event/session ledgers remain durable authorities. This spool only preserves
Dispatcher.receipts' process-local history without retaining every payload in
RAM. Invocation proofs exist while their exact input objects are still alive;
equal reconstructed calls never inherit a proof or an execution identity.
"""
from collections import OrderedDict
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import weakref


def _release(db, directory):
    try: db.close()
    finally: directory.cleanup()


class ResultHistory:
    MAX_BYTES = 2 * 1024 * 1024
    MAX_BATCHES = 64

    def __init__(self, receipt_type, result_type):
        directory = tempfile.TemporaryDirectory(prefix='telyincore-receipts-')
        try:
            db = sqlite3.connect(str(Path(directory.name)/'history.sqlite3'), isolation_level=None, check_same_thread=False)
            db.execute('PRAGMA cache_size=-1024')
            db.execute('PRAGMA synchronous=OFF')  # Not a recovery authority.
            db.execute('CREATE TABLE results(number INTEGER PRIMARY KEY, wire TEXT NOT NULL)')
        except BaseException:
            if 'db' in locals(): db.close()
            directory.cleanup()
            raise
        from .temp_volumes import observe
        observe(Path(directory.name).parent)
        self._db = db
        self._lock = threading.RLock()
        self._cleanup = weakref.finalize(self, _release, db, directory)
        self._receipt_type, self._result_type = receipt_type, result_type
        self._count = 0
        self._bytes = 0
        self._hot = OrderedDict()
        self._live = weakref.WeakValueDictionary()

    def append(self, result):
        with self._lock:
            wire = json.dumps(asdict(result), ensure_ascii=True, separators=(',', ':'))
            number = self._count
            self._db.execute('INSERT INTO results VALUES(?,?)', (number, wire))
            self._count += 1
            self._remember(number, result, len(wire))

    def _remember(self, number, result, size):
        self._live[number] = result
        old = self._hot.pop(number, None)
        if old is not None: self._bytes -= old[1]
        if size <= self.MAX_BYTES:
            self._hot[number] = result, size
            self._bytes += size
        while len(self._hot) > self.MAX_BATCHES or self._bytes > self.MAX_BYTES:
            _, (_, removed) = self._hot.popitem(last=False)
            self._bytes -= removed

    def __len__(self):
        return self._count

    def __iter__(self):
        with self._lock:
            cursor = self._db.execute('SELECT number,wire FROM results ORDER BY number')
            try:
                for number, wire in cursor:
                    result = self._live.get(number)
                    if result is None:
                        data = json.loads(wire)
                        result = self._result_type(tuple(self._receipt_type(**row) for row in data['receipts']), data['ended'])
                        self._remember(number, result, len(wire))
                    yield result
            finally: cursor.close()

@dataclass
class Invocation:
    references: tuple
    identity: tuple
    receipts: dict
    admitted: bool


class InvocationLedger:
    def __init__(self):
        self._batches = {}
        self._used = {}

    def used(self, call):
        reference = self._used.get(id(call))
        return reference is not None and reference() is call

    def record(self, batch, *, admitted):
        key = tuple(map(id, batch))
        owner = weakref.ref(self)
        entry = Invocation((), tuple((c.call_id, c.name, c.arguments) for c in batch), {}, admitted)
        # Do not capture entry in callbacks: that would keep its payload alive
        # through the weak references retained for sibling call identities.
        token = object()
        def retire(reference):
            ledger = owner()
            if ledger is not None:
                current = ledger._batches.get(key)
                if current is not None and current[0] is token:
                    ledger._batches.pop(key)
        entry.references = tuple(weakref.ref(call, retire) for call in batch)
        self._batches[key] = token, entry
        if admitted:
            for call in batch:
                identifier = id(call)
                def unused(reference, identifier=identifier):
                    ledger = owner()
                    if ledger is not None and ledger._used.get(identifier) is reference:
                        ledger._used.pop(identifier)
                self._used[identifier] = weakref.ref(call, unused)
        return entry

    def get(self, calls):
        found = self._batches.get(tuple(map(id, calls)))
        if found is None: raise ValueError('foreign or unrecorded receipt invocation')
        entry = found[1]
        if not all(reference() is call for reference, call in zip(entry.references, calls)):
            raise ValueError('foreign or unrecorded receipt invocation')
        if tuple((c.call_id, c.name, c.arguments) for c in calls) != entry.identity:
            raise ValueError('changed receipt invocation')
        return entry
