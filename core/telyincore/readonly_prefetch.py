"""Exact, plugin-declared public reads before the first model decision.

Uses the live guarded ToolDef and the existing Dispatcher/receipt ledger. It
does not synthesize a model function_call, send messages or grant capabilities.
"""
from __future__ import annotations
from collections import OrderedDict
import hashlib
import json
import time
import uuid
from .note import Record
from .session_view import batch_scope, event_scope
from .stream import ToolCall
from .tools import ToolTag

class ReadonlyPrefetch:
    def __init__(self, dispatcher, runner, *, current_batch, current_event):
        self.dispatcher, self.runner = dispatcher, runner
        self.current_batch, self.current_event = current_batch, current_event
        self.seen = OrderedDict()

    def _current(self, batch, signal):
        try:
            return (not (signal is not None and signal.is_set())
                    and len(batch) == 1 and tuple(self.current_batch()) == batch
                    and self.current_event(batch[0]) is True)
        except Exception:
            return False

    def _plan(self, batch):
        if len(batch) != 1: return None
        item = batch[0]; meta = getattr(item, 'meta', None)
        if (getattr(item, 'type', '') != 'message' or getattr(item, 'origin', '') != 'external'
                or getattr(item, 'blobs', ()) or not isinstance(meta, dict)
                or meta.get('segment_types') != ['text'] or meta.get('has_user_text') is not True):
            return None
        text = meta.get('plain_text')
        if not isinstance(text, str) or not 0 < len(text) <= 64: return None
        scope = event_scope(item)
        if not scope or scope != batch_scope(batch): return None
        sender = str(getattr(item, 'sender_key', '') or '')
        if not sender.startswith(str(getattr(item, 'source', '')) + ':'): return None
        mid = meta.get('message_id'); account = meta.get('self_id')
        if not isinstance(mid, str) or not mid or not account: return None
        matches = []
        for name, tool in tuple(self.dispatcher._table.items()):
            claim = getattr(tool.handler, '__readonly_prefetch__', None)
            if not isinstance(claim, dict) or set(claim) != {'version', 'source', 'text', 'arguments', 'public'}:
                continue
            if (type(claim['version']) is not int or claim['version'] != 1 or claim['public'] is not True
                    or claim['source'] != getattr(item, 'source', None) or claim['text'] != text.strip()
                    or set(tool.tags or ()) != {ToolTag.READ} or tool.effect != 'immediate'
                    or tool.ends_turn or tool.barrier_after or not isinstance(claim['arguments'], dict)):
                continue
            # Static arguments only; no user/account/image interpolation from events.
            args = claim['arguments']
            if any(not isinstance(k, str) or type(v) not in (str, bool, int, float, type(None))
                   for k, v in args.items()): continue
            encoded = json.dumps(args, ensure_ascii=False, sort_keys=True, allow_nan=False)
            if len(encoded) > 1024: continue
            matches.append((name, encoded, tool))
        if len(matches) != 1: return None  # declaration conflicts always fall back to model
        name, args, tool = matches[0]
        key = hashlib.sha256(json.dumps([scope, sender, account, mid, name, args],
                                       ensure_ascii=False).encode()).hexdigest()
        return key, name, args, tool, scope

    async def __call__(self, session, batch, signal=None):
        batch = tuple(batch)
        if not self._current(batch, signal): return False
        try: plan = self._plan(batch)
        except (ValueError, TypeError, AttributeError): return False
        if plan is None: return False
        key, name, args, tool, scope = plan
        if key in self.seen: return False
        # Restart/retry reuse is tied to this exact transport message, never a global receipt.
        for row in reversed(session.records()):
            if row.kind == 'host_read_receipt' and row.scope == scope:
                try:
                    if json.loads(row.text).get('request_key') == key: return False
                except (ValueError, TypeError): pass
        if not self._current(batch, signal) or self.dispatcher._table.get(name) is not tool:
            return False
        self.seen[key] = True
        while len(self.seen) > 128: self.seen.popitem(last=False)
        call = ToolCall(call_id='host_read_' + uuid.uuid4().hex, name=name, arguments=args)
        # Normal runner retains lifecycle, area permission, validation and audit gates.
        result = await self.runner((call,))
        if not self._current(batch, signal) or self.dispatcher._table.get(name) is not tool:
            return False
        receipts = tuple(getattr(result, 'receipts', ()) or ())
        if len(receipts) != 1 or receipts[0].call_id != call.call_id: return False
        receipt = receipts[0]
        payload = {'origin': 'host_read_prefetch', 'request_key': key,
                   'tool': name, 'stage': receipt.stage, 'arguments': json.loads(args),
                   'delivered_to_user': False, 'result': receipt.text}
        # Public receipts only; unexpected large output is not silently truncated or injected.
        text = json.dumps(payload, ensure_ascii=False)
        if len(text.encode()) > 16384: return False
        session.append(Record(kind='host_read_receipt', role='assistant', name=name,
                              text=text, ts_ms=int(time.time()*1000), scope=scope))
        return True
