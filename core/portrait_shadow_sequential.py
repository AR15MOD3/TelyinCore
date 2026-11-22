"""Opt-in, immediate profile-only SHADOW composition; no writer or model allocation.

Additive candidate. Does not replace the legacy ShadowWiring or Dispatcher.
The host owns admission budgets, transport and task/client draining. Settlement
confirms only Session records and observer acknowledgements, not native writes.
"""
import asyncio
import datetime
import inspect
from concurrent.futures import Future as ConcurrentFuture
from pathlib import Path
from types import MappingProxyType

from portrait_shadow_wiring import ContextRefused, TrustedBatchContext
from portrait_shadow_context import make_coordinator
from telyincore.dispatch import DispatchResult, Receipt
from telyincore.note import Record
from telyincore.portrait_shadow.dispatch import ShadowDispatchAdapter
from telyincore.portrait_shadow.stage import ShadowProfileStage
from telyincore.portrait_shadow.review import fingerprint
from telyincore.portrait_shadow.journal import (
    DurableShadowJournal, DurableShadowRound, JournalRefused,
    assert_tool_history_shape,
)


class AsyncCallbackRefused(TypeError):
    def __init__(self, work):
        super().__init__('synchronous_callback_required')
        self.work = work
        self.outcome = None
        self.observer_error = None
        if isinstance(work, (asyncio.Future, ConcurrentFuture)):
            try:work.add_done_callback(self._observe)
            except BaseException as error:self.observer_error=error

    def _observe(self, work):
        try:
            work.result()
        except BaseException as error:
            self.outcome = error


def sync_call(callback, *args):
    value = callback(*args)
    if inspect.isawaitable(value) or isinstance(value, ConcurrentFuture):
        if inspect.iscoroutine(value) and inspect.getcoroutinestate(value) == inspect.CORO_CREATED:
            value.close()
        raise AsyncCallbackRefused(value)
    return value


class SequentialShadowAdapter:
    def __init__(self, table, stage, *, context, permissions=None, before_dispatch=None):
        if set(table) != {'profile_add'} or table['profile_add'].effect != 'immediate':
            raise ValueError('immediate_profile_shadow_only')
        if not callable(context) or (before_dispatch is not None and
                (not callable(before_dispatch) or inspect.iscoroutinefunction(before_dispatch))):
            raise TypeError('synchronous_admission_and_context_required')
        self.inner = ShadowDispatchAdapter(table, stage, permissions=permissions)
        self.stage, self.table = stage, self.inner.table
        self.context, self.before_dispatch = context, before_dispatch
        self.audit, self.results = [], []
        self._running = False
        self.callback_failures = []
        self._closing = False

    def begin_shutdown(self):
        self._closing = True

    def shutdown_tasks(self):
        observers=[]
        for error in self.callback_failures:
            work=error.work
            if isinstance(work, ConcurrentFuture):work=asyncio.wrap_future(work)
            if isinstance(work, asyncio.Future) and not work.done():
                observers.append(asyncio.shield(work))
        return tuple(observers)

    def assert_releasable(self):
        if self._running or self.dispatch_pending or self.review_pending:
            error=RuntimeError('shadow_work_or_failure_responsibility_unresolved')
            error.shadow_owner=self
            raise error

    async def wait_closed(self, *, deadline):
        self.begin_shutdown()
        observers=self.shutdown_tasks()
        try:
            if observers:
                await asyncio.wait(observers,timeout=max(0,deadline-asyncio.get_running_loop().time()))
        finally:
            for observer in observers:
                if not observer.done():observer.cancel()  # shields only
                else:
                    try:observer.result()
                    except BaseException:pass  # exact work/error retained above
        self.assert_releasable()

    @property
    def dispatch_pending(self):
        pending = dict(self.inner.dispatch_pending)
        # Preserve the mapping/len contract used by outer cleanup reports.
        # Tuple keys cannot collide with native string call identities.
        for index, error in enumerate(self.callback_failures):
            pending[('rejected_callback', index)] = error
        return MappingProxyType(pending)

    @property
    def review_pending(self):
        return self.inner.review_pending

    async def __call__(self, calls):
        if self._closing:raise RuntimeError('shadow_admission_closed')
        if self._running:
            raise RuntimeError('shadow_batch_already_inflight')
        batch = tuple(calls)
        ids = [c.call_id for c in batch]
        if not batch or any(type(x) is not str or not x for x in ids) or len(ids) != len(set(ids)):
            raise ValueError('nonempty_unique_call_ids_required')
        row = dict(status='started', call_snapshot=[dict(call_id=c.call_id,
            name=c.name, arguments=c.arguments) for c in batch],
            captured_ids=(), raw_receipts=(), captured=0, reviewed=0,
            native_profile_calls=0)
        self.audit.append(row)
        self._running = True
        actual, started, reviewed_count = [], [], 0
        inner_audit_start = len(self.inner.audit)

        def complete_remaining(reason):
            by_id = {r.call_id: r for r in actual}
            return tuple(by_id.get(c.call_id) or Receipt(c.call_id, c.name,
                ('本次未写入画像（本条处理已' + reason + '）。') if c.call_id in started else
                '本次未写入画像（本条未派发：前项停止或轮次中断）。', 'receipt') for c in batch)

        try:
            initial = fingerprint(sync_call(self.context))
            for call in batch:
                turn = sync_call(self.context)
                if fingerprint(turn) != initial:
                    raise ContextRefused('context_changed_before_dispatch')
                if self.before_dispatch is not None:
                    try:
                        sync_call(self.before_dispatch, call, turn)
                    except AsyncCallbackRefused as error:
                        self.callback_failures.append(error)
                        raise
                if fingerprint(sync_call(self.context)) != initial:
                    raise ContextRefused('context_changed_during_admission')
                started.append(call.call_id)
                prior = len(self.stage.records)
                result = await self.inner((call,))
                if len(result.receipts) != 1:
                    raise RuntimeError('one_immediate_receipt_required')
                receipt = result.receipts[0]
                if (receipt.call_id, receipt.name) != (call.call_id, call.name):
                    raise RuntimeError('receipt_identity_mismatch')
                actual.append(receipt)
                reviewed = len(self.stage.records) == prior + 1
                reviewed_count += int(reviewed)
                passed = reviewed and self.stage.records[-1].get('status') == 'shadow'
                if result.ended or not passed:
                    out = DispatchResult(complete_remaining('中断'), True)
                    row.update(status='policy_stopped', raw_receipts=out.receipts)
                    self.results.append(out)
                    return out
            out = DispatchResult(tuple(actual), False)
            row.update(status='completed', raw_receipts=out.receipts)
            self.results.append(out)
            return out
        except BaseException as primary:
            if isinstance(primary, AsyncCallbackRefused) and not any(error is primary for error in self.callback_failures):
                self.callback_failures.append(primary)
            cancelled = isinstance(primary, asyncio.CancelledError)
            row.update(status='cancelled' if cancelled else 'failed',
                primary_error=type(primary).__name__,
                raw_receipts=complete_remaining('取消' if cancelled else '中断'))
            raise
        finally:
            captured = tuple(cid for audit in self.inner.audit[inner_audit_start:]
                for cid in audit.get('captured_ids', ()))
            row.update(captured_ids=captured, captured=len(captured),
                dispatch_started_ids=tuple(started), reviewed=reviewed_count)
            self._running = False


class SequentialRoundSettlement:
    """Preserve original exceptions; close a whole failed/cancelled batch, or block."""
    def __init__(self, session, run_round, adapter, *, scope_of, on_settled=None):
        self.session, self.run_round, self.adapter = session, run_round, adapter
        self.scope_of, self.on_settled = scope_of, on_settled
        self.audit, self.blocked = [], False

    async def __call__(self, round_no):
        if self.blocked:
            raise RuntimeError('shadow_settlement_blocked')
        scope = self.scope_of()
        start, audit_start = len(self.session.records()), len(self.adapter.audit)
        try:
            return await self.run_round(round_no)
        except (Exception, asyncio.CancelledError) as primary:
            self.blocked = True
            row = dict(status='not_confirmed', settled=0, primary_error=type(primary).__name__)
            self.audit.append(row)
            try:
                if not scope or self.scope_of() != scope:
                    raise RuntimeError('scope_changed_during_settlement')
                if len(self.adapter.audit) != audit_start + 1:
                    raise RuntimeError('no_unique_dispatch')
                dispatch = self.adapter.audit[-1]
                expected = 'cancelled' if isinstance(primary, asyncio.CancelledError) else 'failed'
                if dispatch['status'] != expected or self.adapter.dispatch_pending:
                    raise RuntimeError('dispatch_not_safe_to_settle')
                records = self.session.records()[start:]
                calls = [r for r in records if r.kind == 'function_call']
                if any(r.kind == 'function_result' for r in records):
                    raise RuntimeError('partial_results_not_safe_to_append')
                snapshot, receipts = dispatch['call_snapshot'], dispatch['raw_receipts']
                if [(r.name, r.text) for r in calls] != [(c['name'], c['arguments']) for c in snapshot]:
                    raise RuntimeError('session_call_snapshot_mismatch')
                if [(r.call_id, r.name) for r in receipts] != [(c['call_id'], c['name']) for c in snapshot]:
                    raise RuntimeError('receipt_snapshot_mismatch')
                if any('shadow-ticket:' in r.text for r in receipts):
                    raise RuntimeError('internal_ticket_not_settled')
                for call, receipt in zip(calls, receipts):
                    self.session.append(Record(kind='function_result', role='assistant',
                        name=receipt.name, text=receipt.text, ts_ms=call.ts_ms, tool_ref=call.tool_ref))
                    row['settled'] += 1
                row['session_status'] = 'confirmed'
                if self.on_settled is not None:
                    sync_call(self.on_settled, receipts, dict(scope=scope,
                        round_no=round_no, reason=expected))
                row['status'] = expected + '_settled'
            except (Exception, asyncio.CancelledError) as secondary:
                row.update(status='settlement_unconfirmed', secondary_error=type(secondary).__name__)
                primary.add_note('shadow_settlement_unconfirmed:' + type(secondary).__name__)
                primary._shadow_settlement_error = secondary
            raise


class SequentialShadowHook:
    def __init__(self, root, *, approved_root):
        self.root, self.approved_root = root, approved_root
        self.adapter = self.wrapper = None
        self.audit = []

    def bind(self, adapter, run_tools):
        if self.adapter is not None or not callable(run_tools):
            raise RuntimeError('invalid_or_repeated_bind')
        self.adapter, self.dispatch = adapter, run_tools

    def __call__(self, run_round, context):
        if self.adapter is None or self.wrapper is not None:
            raise RuntimeError('unbound_or_reused_hook')
        if not isinstance(context, MappingProxyType) or set(context) != {
            'session', 'run_tools', 'scope_of', 'record_event', 'tell', 'owner_visible'}:
            raise TypeError('context_contract')
        if context['run_tools'] is not self.dispatch:
            raise RuntimeError('dispatch_identity_mismatch')

        def observer(receipts, meta):
            row = dict(event_status='started', ui_forwarded=0, ui_status='not_attempted')
            self.audit.append(row)
            if context['scope_of']() != meta['scope']:
                raise RuntimeError('observer_scope_mismatch')
            event = context['record_event'](dict(type='shadow-review-' + meta['reason'],
                ts=datetime.datetime.now(datetime.timezone.utc).isoformat(), source='portrait-shadow',
                origin='internal', text='；'.join(r.text for r in receipts), meta=dict(
                    scope=meta['scope'], round_no=meta['round_no'], status=meta['reason'] + '_no_profile_write',
                    receipts=[dict(call_id=r.call_id, name=r.name, stage=r.stage) for r in receipts])))
            row.update(event_status='confirmed', event_cursor=event.cursor)
            if not context['owner_visible']():
                row['ui_status'] = 'owner_hidden'
                return
            row['ui_status'] = 'started'
            for r in receipts:
                context['tell'](dict(kind='function_result', name=r.name,
                    text=r.text, run='run-' + str(meta['round_no'])))
                row['ui_forwarded'] += 1
            row['ui_status'] = 'forwarded'

        self.settlement = SequentialRoundSettlement(context['session'], run_round,
            self.adapter, scope_of=context['scope_of'], on_settled=observer)
        self.journal = DurableShadowJournal(self.root, approved_root=self.approved_root)
        prior = self.journal.check_start()
        if prior is None and assert_tool_history_shape(context['session'].records()):
            raise JournalRefused('unbound_tool_history')
        self.wrapper = DurableShadowRound(self.settlement, self.journal, context)
        return self.wrapper


class SequentialShadowWiring:
    def __init__(self, *, enabled, store, batch_of, table, backend, receipt_wrapper,
                 journal_root, approved_root, permissions=None, source_policy='minimal',
                 before_dispatch=None, limit=4, ttl=10):
        if enabled is not True:
            raise ContextRefused('explicit_shadow_enable_required')
        if not callable(backend) or not callable(receipt_wrapper):
            raise TypeError('backend_and_receipt_wrapper_required')
        if set(table) != {'profile_add'} or table['profile_add'].effect != 'immediate':
            raise ValueError('immediate_profile_shadow_only')
        from telyincore.events import EventStore
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
            self.adapter = SequentialShadowAdapter(table, self.stage, context=self.context,
                permissions=permissions, before_dispatch=before_dispatch)
            self.run_tools = receipt_wrapper(self.adapter, store)
            self.hook = SequentialShadowHook(Path(journal_root), approved_root=approved)
            self.hook.bind(self.adapter, self.run_tools)
        except BaseException as primary:
            try:self.context.close()
            except BaseException as cleanup:
                BaseException.__setattr__(primary,'shadow_cleanup_error',cleanup)
                BaseException.__setattr__(primary,'shadow_wiring',self)
            raise

    def shutdown_tasks(self):
        return self.adapter.shutdown_tasks()

    async def wait_closed(self, *, deadline):
        self.adapter.begin_shutdown()
        self.context.close()
        try:await self.adapter.wait_closed(deadline=deadline)
        except BaseException as error:
            BaseException.__setattr__(error,'shadow_wiring',self)
            raise

    def close(self):
        self.adapter.begin_shutdown()
        self.context.close()
        try:self.adapter.assert_releasable()
        except BaseException as error:
            BaseException.__setattr__(error,'shadow_wiring',self)
            raise
