"""Cooperative restart handoff, not exactly-once business execution.

STORED and QUEUED envelopes have not entered a consumer and can be recovered.
APPENDING/OFFERING/PROCESSING remain quarantined. Consumer retirement is not
proof of business success. Resolved operator decisions move to immutable
resolved/<binding>-<entry>.json files before their live capacity is reused. No raw event
text is stored here.
"""
import hashlib
import os
from pathlib import Path
import uuid
from bot_guarded_store import GuardedStore
from .source_guard import lease

def digest(event):
    return hashlib.sha256(event.to_line().encode('utf-8')).hexdigest()

class DeliveryJournal:
    def __init__(self, root, store):
        self.root=Path(root).resolve();self.store=store
        self.binding=hashlib.sha256(str(Path(store.log.root).resolve()).encode()).hexdigest()
        self.root.mkdir(parents=True,exist_ok=True)
        with lease(self.root):
            self.disk=GuardedStore(self.root,self.root/'pending.json')
            state=self.disk.read()
            if state:
                from .ingress_review import validate
                validate(state,self.binding)
            else:state={'schema':2,'binding':self.binding,'generation':'','records':{}}
            self._retire_resolved(state)
            state['schema']=2  # Legacy uncertain attempts stay uncertain.
            self.generation=uuid.uuid4().hex;state['generation']=self.generation
            self.disk.write(state)

    def _change(self, fn):
        with lease(self.root):
            state=self.disk.read()  # stale generations/instances fail before another effect
            if state['generation']!=self.generation:raise RuntimeError('retired ingress generation')
            self._retire_resolved(state)
            result=fn(state['records']);self.disk.write(state);return result

    def _retire_resolved(self, state):
        """Publish immutable operator evidence before freeing bounded live slots.

        Interrupted compaction can leave both copies, never an unrecorded
        disposition. Existing archive contents must match exactly on retry.
        """
        resolved = [(key, row) for key, row in state['records'].items()
                    if row['state'] == 'RESOLVED']
        if not resolved: return
        folder = self.root / 'resolved'
        folder.mkdir(exist_ok=True)
        for key, row in resolved:
            payload = {'schema': 1, 'binding': state['binding'], 'entry': key,
                       'record': row}
            archive = GuardedStore(self.root, folder / (state['binding'] + '-' + key + '.json'))
            if archive.raw is None:
                archive.write(payload)
            elif archive.read() != payload:
                raise ValueError('resolved ingress archive changed')
        if os.name != 'nt':
            for directory in (folder, self.root):
                fd = os.open(directory, os.O_RDONLY)
                try: os.fsync(fd)
                finally: os.close(fd)
        for key, _ in resolved:
            del state['records'][key]

    def prepare(self):
        token=uuid.uuid4().hex
        def reserve(rows):
            if len(rows)>=128:raise RuntimeError('unresolved ingress capacity')
            rows[token]={'state':'APPENDING'}
        self._change(reserve);return token

    def stored(self, token, event):
        def update(rows):
            if rows[token]['state']!='APPENDING':raise RuntimeError('delivery transition')
            rows[token]={'state':'STORED','cursor':event.cursor,'digest':digest(event)}
        self._change(update)

    def discard_duplicate(self, token):
        def discard(rows):
            if rows[token]['state']!='APPENDING':raise RuntimeError('delivery transition')
            del rows[token]
        self._change(discard)

    def offer(self, event, push):
        def begin(rows):
            found=[(key,row) for key,row in rows.items() if row.get('cursor')==event.cursor]
            if len(found)!=1:raise RuntimeError('delivery identity missing or ambiguous')
            key,row=found[0]
            if row['state'] not in ('STORED','QUEUED') or row['digest']!=digest(event):raise RuntimeError('delivery identity changed or uncertain')
            row['state']='OFFERING';return key
        token=self._change(begin)
        push(event)  # once; exception/cancellation leaves OFFERING, never automatically replayed
        def finish(rows):
            row=rows[token]
            if row['state']!='OFFERING' or row['digest']!=digest(event):raise RuntimeError('delivery completion identity changed')
            row['state']='QUEUED'
        self._change(finish)

    def begin_batch(self, events):
        """Persist responsibility before filtering, session writes or model effects."""
        def begin(rows):
            tickets=[]
            by_cursor={row.get('cursor'):(key,row) for key,row in rows.items()
                       if row['state'] != 'RESOLVED'}
            for event in events:
                found=by_cursor.get(event.cursor)
                if found is None:continue  # Internal events use their own owner.
                key,row=found
                if (row['state']!='QUEUED' or row['digest']!=digest(event)
                        or self.store.get(event.cursor) is not event):
                    raise RuntimeError('delivery consumer identity changed or uncertain')
                row['state']='PROCESSING';tickets.append(key)
            return tuple(tickets)
        return self._change(begin)

    def finish_batch(self, tickets):
        """Retire a normally returned/discarded batch; never called on exceptions."""
        if not tickets:return
        def finish(rows):
            for key in tickets:
                if rows[key]['state']!='PROCESSING':raise RuntimeError('delivery consumer transition')
            for key in tickets:del rows[key]
        self._change(finish)

    def recover(self):
        from .events import RangeQuery
        recovered=[]
        with lease(self.root):
            rows=self.disk.read()['records']
            for row in sorted(rows.values(),key=lambda r:r.get('cursor',0)):
                if row['state'] not in ('STORED','QUEUED'):continue
                found=self.store.range(RangeQuery(from_cursor=row['cursor'],to_cursor=row['cursor']))
                if len(found)!=1 or digest(found[0])!=row['digest']:raise RuntimeError('recovery source missing or changed')
                recovered.append(found[0])
        return recovered

    def status(self):
        with lease(self.root):rows=self.disk.read()['records']
        return {'recoverable':sum(r['state'] in ('STORED','QUEUED') for r in rows.values()),
                'uncertain':sum(r['state'] in ('APPENDING','OFFERING','PROCESSING') for r in rows.values()),
                'business_acknowledged':False}
