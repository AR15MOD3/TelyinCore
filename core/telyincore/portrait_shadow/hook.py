(
    "Consumes candidate Console's explicit wrap_ro"
    'und context, no private assignment.'
)
from types import MappingProxyType
import datetime
from .round_settlement import ShadowRoundSettlement
from .journal import (
    DurableShadowJournal, DurableShadowRound, assert_tool_history_shape, JournalRefused
)

class PublicObserver:

    def __init__(self, context):
        self.context = context
        self.audit = []

    def __call__(self, receipts, meta):
        c = self.context
        scope = meta['scope']
        if not scope or c['scope_of']() != scope:
            raise RuntimeError('observer_scope_mismatch')
        row = (
            dict(scope=scope, event_status='started', ui_forwarded=0, ui_status='not_attempted')
        )
        self.audit.append(row)
        draft = (
            dict(type='shadow-review-cancelled', ts=datetime.datetime.now(datetime.timezone(
                datetime.timedelta(hours=8))).isoformat(), source='portrait-shadow', origin=
                    'internal', text='；'.join((r.name + '：' + r.text for r in receipts)), meta=
                        dict(scope=scope, round_no=meta['round_no'], status=
                            'cancelled_no_profile_write', receipts=[dict(call_id=r.call_id, name
                                =r.name, stage=r.stage) for r in receipts]))
        )
        try:
            ev = c['record_event'](draft)
            row.update(event_status='confirmed', event_cursor=ev.cursor)
        except Exception as exc:
            row.update(event_status='unconfirmed_error', event_error=type(exc).__name__)
            raise
        if not c['owner_visible']():
            row['ui_status'] = 'owner_hidden'
            return
        row['ui_status'] = 'started'
        try:
            for r in receipts:
                c['tell'](dict(kind='function_result', name=r.name, text=r.text, run='run-' +
                    str(meta['round_no'])))
                row['ui_forwarded'] += 1
            row['ui_status'] = 'forwarded'
        except Exception as exc:
            row.update(ui_status='unconfirmed_error', ui_error=type(exc).__name__)
            raise

class PublicShadowHook:

    def __init__(self, root, *, approved_root, fault=None):
        self.root = root
        self.approved_root = approved_root
        self.fault = fault
        self.adapter = None
        self.wrapper = None

    def bind(self, adapter, run_tools):
        if self.adapter is not None:
            raise RuntimeError('hook_already_bound')
        if not callable(run_tools):
            raise TypeError('run_tools_callable_required')
        self.adapter = adapter
        self.dispatch = run_tools

    def __call__(self, run_round, context):
        if self.adapter is None or self.wrapper is not None:
            raise RuntimeError('unbound_or_reused_hook')
        if (
            not isinstance(context, MappingProxyType) or set(context) != {'session', 'run_tools'
                , 'scope_of', 'record_event', 'tell', 'owner_visible'}
        ):
            raise TypeError('context_contract')
        if context['run_tools'] is not self.dispatch:
            raise RuntimeError('dispatch_identity_mismatch')
        self.context = context
        self.original = run_round
        self.observer = PublicObserver(context)
        self.settlement = (
            ShadowRoundSettlement(context['session'], run_round, self.adapter, on_settled=self.
                observer, scope_of=context['scope_of'])
        )
        self.journal = (
            DurableShadowJournal(self.root, approved_root=self.approved_root, fault=self.fault)
        )
        prior = self.journal.check_start()
        has_tools = assert_tool_history_shape(context['session'].records())
        if prior is None and has_tools:
            raise JournalRefused('unbound_tool_history')
        self.wrapper = DurableShadowRound(self.settlement, self.journal, context)
        return self.wrapper
