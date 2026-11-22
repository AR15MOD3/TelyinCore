"""Explicit deployment-only shadow wiring, disabled unless constructed by its host.

A trusted host supplies the actual consumed batch getter and a dedicated EventStore.
Persistence proves publication to that store, NOT authentic QQ/network identity.
No launch integration, transport allocation, native portrait write, or model client.
"""
import hashlib
import json
import re
import uuid
from collections import OrderedDict
from pathlib import Path

from portrait_shadow_context import make_coordinator
from telyincore.frozen_json import FrozenDict, FrozenList
from telyincore.events import EventEnvelope, EventStore
from telyincore.portrait_shadow.dispatch import ShadowDispatchAdapter
from telyincore.portrait_shadow.evidence import Message, Turn
from telyincore.portrait_shadow.hook import PublicShadowHook
from telyincore.portrait_shadow.stage import ShadowProfileStage


class ContextRefused(ValueError):
    pass


def _wire(event):
    if type(event) is not EventEnvelope:
        raise ContextRefused('envelope_required')
    return json.dumps(json.loads(event.to_line()), ensure_ascii=False,
                      sort_keys=True, separators=(',', ':'), allow_nan=False)


def _identity(event):
    if (type(event.cursor) is not int or event.cursor < 1
            or event.type != 'message' or event.origin != 'external'
            or event.source != 'qq' or not isinstance(event.sender_key, str)
            or re.fullmatch(r'qq:[0-9]+', event.sender_key) is None
            or not isinstance(event.text, str) or not event.text.strip()
            or type(event.meta) not in (dict, FrozenDict)
            or set(event.meta) != {'conversation'}
            or not isinstance(event.meta['conversation'], str)
            or re.fullmatch(r'group:[0-9]+', event.meta['conversation']) is None
            or event.blobs or event.context_delivery is not None):
        raise ContextRefused('plain_external_group_message_required')
    return 'group:qq:' + event.meta['conversation'].split(':', 1)[1]


class TrustedBatchContext:
    """Bounded post-subscription evidence; never import historical rows as trust.

    Dedicated small stores only: <=4 shards and <=2 MiB total, <=64 live rows.
    Unknown metadata is rejected, not discarded. No history fallback or truncation.
    EventStore has no unsubscribe; close makes the registered callback inert.
    The host MUST supply its actual post-filter consumed batch, never model input.
    """
    MAX_FILES = 4
    MAX_DISK_BYTES = 2 * 1024 * 1024
    MAX_ROWS = 64
    MAX_ROW_BYTES = 32768
    MAX_BATCH_ROWS = 16
    MAX_BATCH_BYTES = 65536

    def __init__(self, store, batch_of):
        if type(store) is not EventStore or not callable(batch_of):
            raise TypeError('trusted_store_and_batch_getter_required')
        self.store, self.batch_of = store, batch_of
        self.root = store.log.root.resolve(strict=True)
        self._published = OrderedDict()
        self._blocked = False
        self._closed = False
        self._last_ids = ()
        self._retired = set()
        self._nonce = uuid.uuid4().hex
        store.on_append(self._on_append)

    def _persisted(self, event, wire):
        if self.store.log.root.resolve(strict=True) != self.root:
            raise ContextRefused('store_root_changed')
        if self.store.get(event.cursor) is None:
            raise ContextRefused('event_removed')
        files = self.store.log.day_files()
        if not files or len(files) > self.MAX_FILES:
            raise ContextRefused('store_file_bound')
        total, matches = 0, []
        for path in files:
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise ContextRefused('store_path_changed')
            with path.open('rb') as stream:
                raw = stream.read(self.MAX_DISK_BYTES - total + 1)
            total += len(raw)
            if total > self.MAX_DISK_BYTES:
                raise ContextRefused('store_byte_bound')
            if raw and not raw.endswith(b'\n'):
                raise ContextRefused('torn_store')
            for line in raw.splitlines():
                value = json.loads(line)
                if value.get('cursor') == event.cursor:
                    matches.append(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                              separators=(',', ':'), allow_nan=False))
        if matches != [wire]:
            raise ContextRefused('persisted_event_mismatch')

    def _on_append(self, event):
        if self._closed or self._blocked:
            return
        try:
            # Non-message receipt/audit events are not source evidence.
            if event.type != 'message':
                return
            wire = _wire(event)
            if len(wire.encode('utf-8')) > self.MAX_ROW_BYTES:
                raise ContextRefused('source_byte_bound')
            self._persisted(event, wire)
            if event.cursor in self._published or len(self._published) >= self.MAX_ROWS:
                raise ContextRefused('source_window_bound')
            self._published[event.cursor] = wire
        except Exception:
            # Subscriber errors must not masquerade as a failed durable append.
            # Poison this capability; no backend or native write can follow.
            self._blocked = True
            self._published.clear()

    def _source_scope(self, event):
        return _identity(event)

    def __call__(self):
        if self._closed or self._blocked:
            raise ContextRefused('context_closed_or_blocked')
        batch = self.batch_of()
        if type(batch) is not tuple or not 1 <= len(batch) <= self.MAX_BATCH_ROWS:
            raise ContextRefused('current_batch_required')
        ids, scopes, messages, size = [], set(), [], 0
        for event in batch:
            wire = _wire(event)
            scope = self._source_scope(event)
            if self._published.get(event.cursor) != wire:
                raise ContextRefused('unwitnessed_or_mutated_event')
            self._persisted(event, wire)
            ids.append(event.cursor)
            scopes.add(scope)
            size += len(wire.encode('utf-8'))
            messages.append(Message(str(event.cursor), event.sender_key, event.text, True))
        if ids != sorted(set(ids)) or len(scopes) != 1 or size > self.MAX_BATCH_BYTES:
            raise ContextRefused('batch_order_scope_or_size')
        ids = tuple(ids)
        if ids != self._last_ids:
            if self._retired.intersection(ids) or set(self._last_ids).intersection(ids):
                raise ContextRefused('reused_or_overlapping_batch')
            self._retired.update(self._last_ids)
            self._last_ids = ids
        token = hashlib.sha256((self._nonce + ':' + ','.join(map(str, ids))).encode()).hexdigest()
        return Turn(token, scopes.pop(), tuple(messages))

    def close(self):
        self._closed = True
        self._published.clear()
        self._retired.clear()
        self._last_ids = ()


class ShadowWiring:
    """Explicit host composition. Does not touch Console private state or launch.

    receipt_wrapper is the host's established receipt-ledger wrapper. The caller
    owns transport, store and the actual consumed batch getter. Only the five-tool
    shadow whitelist is accepted; this is NOT hot-plugin/full-table integration.
    """
    def __init__(self, *, enabled, store, batch_of, table, backend, receipt_wrapper,
                 journal_root, approved_root, permissions=None, limit=4, ttl=10,
                 source_policy='minimal'):
        if enabled is not True:
            raise ContextRefused('explicit_shadow_enable_required')
        if not callable(backend) or not callable(receipt_wrapper):
            raise TypeError('backend_and_receipt_wrapper_required')
        approved = Path(approved_root).resolve(strict=True)
        if type(store) is not EventStore or not store.log.root.resolve().is_relative_to(approved):
            raise ContextRefused('dedicated_store_outside_approved_root')
        if source_policy == 'minimal':
            context_type = TrustedBatchContext
        elif source_policy == 'qq_plain':
            from portrait_shadow_qq import QQPlainBatchContext
            context_type = QQPlainBatchContext
        else:
            raise ContextRefused('unsupported_source_policy')
        self.context = context_type(store, batch_of)
        try:
            self.coordinator = make_coordinator(limit=limit, ttl=ttl)
            self.stage = ShadowProfileStage(self.context, self.coordinator, backend)
            self.adapter = ShadowDispatchAdapter(table, self.stage, permissions=permissions)
            self.run_tools = receipt_wrapper(self.adapter, store)
            self.hook = PublicShadowHook(Path(journal_root), approved_root=Path(approved_root))
            self.hook.bind(self.adapter, self.run_tools)
        except BaseException:
            self.context.close()
            raise

    def close(self):
        # This revokes future review context, NOT task draining or client closure.
        # The host must cancel/settle a round before releasing its resources.
        self.context.close()
