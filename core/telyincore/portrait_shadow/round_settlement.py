(
    'Isolated cancellation settlement for the real'
    ' Round+Session shadow seam.\nNot installed. On'
    'ly immediate-effect shadow tables are support'
    'ed. Cancellation\ncontinues to propagate; a ca'
    'ncellation receipt is NOT a successful write.'
    '\n'
)
import asyncio, inspect
from telyincore.note import Record

class ShadowRoundSettlement:

    def __init__(self, session, run_round, adapter, *, on_settled=None, scope_of=None):
        if any((t.effect != 'immediate' for t in adapter.table.values())):
            raise ValueError('queued_effect_round_settlement_out_of_scope')
        self.session, self.run_round, self.adapter = (session, run_round, adapter)
        self.audit = []
        self.blocked = False
        if on_settled is not None and inspect.iscoroutinefunction(on_settled):
            raise TypeError('synchronous_settlement_observer_required')
        self.on_settled, self.scope_of = (on_settled, scope_of)
        self._settled_count = 0

    def _settle(self, start, audit_start, expected_scope):
        if self.scope_of is not None and self.scope_of() != expected_scope:
            raise RuntimeError('scope_changed_during_cancellation')
        if len(self.adapter.audit) != audit_start + 1:
            raise RuntimeError('no_unique_shadow_dispatch')
        row = self.adapter.audit[-1]
        if row['status'] != 'cancelled' or 'raw_receipts' not in row:
            raise RuntimeError('dispatch_not_completed_before_cancel')
        records = self.session.records()[start:]
        calls = [r for r in records if r.kind == 'function_call']
        if any((r.kind == 'function_result' for r in records)):
            raise RuntimeError('partial_results_not_safe_to_append')
        snapshot = row['call_snapshot']
        receipts = row['raw_receipts']
        if [(r.name, r.text) for r in calls] != [(c['name'], c['arguments']) for c in snapshot]:
            raise RuntimeError('session_call_snapshot_mismatch')
        if (
            [(r.call_id, r.name) for r in receipts] != [(c['call_id'], c['name']) for c in snapshot]
        ):
            raise RuntimeError('receipt_order_or_pending_mismatch')
        if self.adapter.dispatch_pending:
            raise RuntimeError('native_pending_not_empty')
        captured = set(row['captured_ids'])
        closed = []
        for call, r in zip(calls, receipts):
            text = '本次未写入画像（shadow审核轮次已取消）。' if r.call_id in captured else r.text
            if 'shadow-ticket:' in text:
                raise RuntimeError('internal_ticket_not_settled')
            self.session.append(Record(kind='function_result', role='assistant', name=r.name,
                text=text, ts_ms=call.ts_ms))
            self._settled_count += 1
            from telyincore.dispatch import Receipt
            closed.append(Receipt(r.call_id, r.name, text, 'receipt' if r.call_id in captured
                else r.stage))
        return tuple(closed)

    async def __call__(self, round_no):
        if self.blocked:
            raise RuntimeError('shadow_round_settlement_blocked')
        expected_scope = self.scope_of() if self.scope_of is not None else None
        self._settled_count = 0
        start = len(self.session.records())
        audit_start = len(self.adapter.audit)
        try:
            return await self.run_round(round_no)
        except asyncio.CancelledError as primary:
            record = dict(status='not_settled', cancel_propagated=True, settled=0)
            self.audit.append(record)
            try:
                closed = self._settle(start, audit_start, expected_scope)
                record['settled'] = len(closed)
                if self.on_settled is not None:
                    result = (
                        self.on_settled(closed, dict(scope=expected_scope, round_no=round_no))
                    )
                    if inspect.isawaitable(result):
                        if hasattr(result, 'close'):
                            result.close()
                        raise TypeError('asynchronous_settlement_observer_not_supported')
                record['status'] = 'cancelled_settled'
            except Exception as exc:
                self.blocked = True
                record.update(status='cleanup_failed', settled=self._settled_count, error=type(
                    exc).__name__ + ':' + str(exc))
                primary.add_note('shadow_cancel_settlement_failed:' + record['error'])
            raise
