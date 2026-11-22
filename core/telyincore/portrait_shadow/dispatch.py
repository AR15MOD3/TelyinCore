(
    'Offline-only two-phase SHADOW adapter, not a '
    'drop-in production dispatcher.\nThe entire ori'
    'ginal synchronous batch runs once with profil'
    'e_add substituted by\ncapture-only. Reviews ru'
    'n afterwards; no native profile writer is cal'
    'led.\n'
)
import asyncio, json, uuid
from dataclasses import replace
from types import MappingProxyType
from telyincore.dispatch import Dispatcher, DispatchResult, Receipt
from .review import canonical

class ShadowDispatchAdapter:
    ALLOWED_OTHER = frozenset(['finish_turn', 'kb_search', 'kb_read', 'pending_read'])

    def __init__(self, table, stage, *, permissions=None, pending_queue=None):
        original = dict(table)
        if 'profile_add' not in original or not callable(original['profile_add'].handler):
            raise ValueError('bound_profile_add_required')
        for name, tool in original.items():
            if name != tool.name or name not in self.ALLOWED_OTHER | {'profile_add'}:
                raise ValueError('unsupported_shadow_table')
        self.stage = stage
        self._running = False
        self._tickets = {}
        self._review_pending = {}
        self.audit = []
        self.results = []

        def capture(text='', who='', standing='', favor=None, weight=None):
            args = (
                json.loads(canonical(dict(text=text, who=who, standing=standing, favor=favor,
                    weight=weight)))
            )
            token = 'shadow-ticket:' + uuid.uuid4().hex
            self._tickets[token] = args
            return token
        copied = dict(original)
        copied['profile_add'] = replace(original['profile_add'], handler=capture)
        self.table = MappingProxyType(copied)
        check = None if permissions is None else lambda tool: permissions(original[tool.name])
        self._dispatcher = Dispatcher(copied, permissions=check, pending_queue=pending_queue)

    @property
    def dispatch_pending(self):
        return self._dispatcher.pending

    @property
    def review_pending(self):
        return MappingProxyType(dict(self._review_pending))

    async def __call__(self, calls):
        if self._running:
            raise RuntimeError('shadow_batch_already_inflight')
        batch = tuple(calls)
        ids = [c.call_id for c in batch if c.call_id]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate_call_id_in_shadow_batch')
        self._running = True
        record = (
            dict(calls=[c.call_id for c in batch], status='dispatching', captured=0, reviewed=0,
                native_profile_calls=0)
        )
        record['call_snapshot'] = (
            [dict(call_id=c.call_id, name=c.name, arguments=c.arguments) for c in batch]
        )
        self.audit.append(record)
        try:
            raw = self._dispatcher.run(batch)
            record['raw_stages'] = (
                [dict(call_id=r.call_id, name=r.name, stage=r.stage) for r in raw.receipts]
            )
            for r in raw.receipts:
                if (
                    r.name == 'profile_add' and r.stage == 'handler' and (r.text in self._tickets)
                ):
                    self._review_pending[r.call_id] = r.text
            record['captured'] = len(self._review_pending)
            record['captured_ids'] = tuple(self._review_pending)
            record['raw_receipts'] = raw.receipts
            final = []
            for receipt in raw.receipts:
                token = self._review_pending.get(receipt.call_id)
                if (
                    receipt.name != 'profile_add' or receipt.stage != 'handler' or token !=
                        receipt.text
                ):
                    final.append(receipt)
                    continue
                args = self._tickets.pop(token)
                reviewed = await self.stage.call(args, call_id=receipt.call_id)
                if (
                    not isinstance(reviewed, Receipt) or (reviewed.call_id, reviewed.name,
                        reviewed.stage) != (receipt.call_id, 'profile_add', 'receipt')
                ):
                    raise ValueError('invalid_shadow_review_receipt')
                final.append(reviewed)
                record['reviewed'] += 1
                self._review_pending.pop(receipt.call_id, None)
            if self._tickets or self._review_pending:
                raise RuntimeError('unmatched_shadow_ticket')
            result = DispatchResult(tuple(final), raw.ended)
            self.results.append(result)
            record.update(status='completed', ended=raw.ended)
            return result
        except asyncio.CancelledError:
            record['status'] = 'cancelled'
            raise
        except BaseException as exc:
            record.update(status='failed', error=type(exc).__name__)
            raise
        finally:
            self._tickets.clear()
            self._review_pending.clear()
            self._running = False

# Migrated source notes (non-executable):
# Migrated source notes (non-executable):
# Only entered after the original Dispatcher gates. No await here.
# Preserve the object presented to host permissions, including handler identity.
# These are our capture/review tickets, NEVER the original effect queue.
