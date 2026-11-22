"""One-run, no-network shadow host for fixed synthetic fixtures only.

This asynchronous entry has no provider/config/credential/QQ/service switches.
It is not the ordinary launch path and does not authorize portrait writes.
"""
from channel_view_policy import qq_group_policy as _boundary_group_policy
import asyncio
from dataclasses import asdict
import json
import hashlib
import math
from pathlib import Path
import shutil
import stat
import tempfile
import time
from types import SimpleNamespace
import uuid

from launch import tools_with_receipt_ledger
from portrait_shadow_wiring import ContextRefused, ShadowWiring
from telyincore.config import Config
from telyincore.console import Console, CHECK_THE_BUS
from telyincore.dispatch import DispatchResult, Receipt
from telyincore.events import EventStore
from telyincore.generate import Result
from telyincore.portrait_shadow.review import Completion, DIMENSIONS, canonical
from telyincore.stream import ToolCall
from telyincore.tools import DECLARED_TOOLS, build_table

CASES = frozenset(('normal', 'wrong_who', 'review_fail', 'review_timeout',
                   'main_error', 'main_timeout', 'internal_cancel', 'tools_refusal',
                   'policy_refusal', 'main_refusal', 'close_error', 'close_timeout',
                   'main_error_close_error', 'init_error', 'policy_mixed_refusal'))


class HostBudgetRefused(RuntimeError):
    pass


class _Budget:
    def __init__(self, limits):
        self.limits = dict(limits)
        self.started = {k: 0 for k in limits}
        self.refused = {k: 0 for k in limits}

    def reserve(self, name, count=1):
        if type(count) is not int or count < 1:
            raise ValueError('positive_reservation_required')
        if self.started[name] + count > self.limits[name]:
            self.refused[name] += count
            raise HostBudgetRefused(name)
        self.started[name] += count

    def snapshot(self):
        return {'limits': dict(self.limits), 'started': dict(self.started),
                'refused': dict(self.refused),
                'consistent': all(0 <= self.started[k] <= v for k,v in self.limits.items())}


def _remove_owned_root(root, parent, marker):
    """Verify absolute containment, ownership and all reparse points BEFORE rmtree."""
    root, parent = Path(root), Path(parent)
    if (not root.is_absolute() or root.parent != parent
            or root.resolve(strict=True) != root
            or not root.name.startswith('portrait-shadow-host-')
            or (root/'owner.json').read_text('utf-8') != marker):
        raise RuntimeError('temporary_root_ownership_mismatch')
    for path in [root, *root.rglob('*')]:
        attrs = getattr(path.lstat(), 'st_file_attributes', 0)
        if path.is_symlink() or attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise RuntimeError('temporary_root_reparse_refused')
    shutil.rmtree(root)


def _review_response(payload, verdict):
    data = json.loads(payload)
    source = data['sources'][0]
    dimensions = {key: 'pass' for key in DIMENSIONS}
    if verdict == 'fail':
        dimensions['intensity'] = 'fail'
    result = {key: data[key] for key in
              ('request_id', 'request_sha256', 'proposal_sha256', 'author')}
    result.update(source_id=source['message_id'], source_sha256=source['source_sha256'],
                  dimensions=dimensions, verdict=verdict)
    return Completion(canonical(result))


async def run_offline_case(case='normal', *, execution_seconds=2.0,
                           cleanup_seconds=0.3, audit=None, started=None):
    """Run exactly one fixed fixture. Genuine caller cancellation is re-raised.

    Limits count callback entries, NOT HTTP attempts or usage. No real callbacks
    or transports can be injected here. Failure roots are preserved, successful
    roots removed only after evidence has been copied into the returned report.
    """
    if type(case) is not str or case not in CASES:
        raise ValueError('unknown_offline_case')
    for value, ceiling in ((execution_seconds, 5), (cleanup_seconds, 1)):
        if (type(value) not in (int,float) or not math.isfinite(value)
                or not 0.01 <= value <= ceiling):
            raise ValueError('offline_time_bound')
    if audit is not None and type(audit) is not dict:
        raise TypeError('audit_dict_required')
    if started is not None and not isinstance(started, asyncio.Event):
        raise TypeError('started_event_required')
    t0 = time.monotonic()
    before_tasks = set(asyncio.all_tasks())
    root = Path(tempfile.mkdtemp(prefix='portrait-shadow-host-')).resolve()
    parent = root.parent
    marker = json.dumps({'owner': uuid.uuid4().hex, 'kind': 'offline-shadow-fixture'})
    (root/'owner.json').write_text(marker, encoding='utf-8')
    budget = _Budget({'main': 1 if case == 'main_refusal' else 2, 'review': 1, 'tools': 1})
    report = {'case': case, 'offline': True, 'status': 'initializing', 'ok': False,
              'root': str(root), 'root_removed': False, 'cleanup_errors': [],
              'primary_error': None, 'native_profile_calls': 0, 'frames': [],
              'main_inputs': [], 'review_inputs': [], 'requested_calls': [],
              'receipts': [], 'policy_refused': [], 'policy_violation_ids': [], 'usage': None,
              'http_attempts': None, 'real_model_requests': 0}
    wire, con, store, run_task = None, None, None, None

    class Transport:
        closed = False

        async def aclose(self):
            if case in ('close_error', 'main_error_close_error'):
                raise OSError('synthetic close failure')
            if case == 'close_timeout':
                await asyncio.Event().wait()
            self.closed = True

    transport = Transport()
    try:
        con = Console(write=report['frames'].append)
        con.owner_view = lambda _: True
        store = EventStore(root/'events')

        async def backend(payload):
            budget.reserve('review')
            report['review_inputs'].append(json.loads(payload))
            if case == 'internal_cancel':
                raise asyncio.CancelledError
            if case == 'review_timeout':
                await asyncio.Event().wait()
            return _review_response(payload, 'fail' if case == 'review_fail' else 'pass')

        def forbidden_writer(**kwargs):
            report['native_profile_calls'] += 1
            raise AssertionError('native profile writer must never run')

        names = frozenset(('profile_add', 'finish_turn'))
        table = build_table([t for t in DECLARED_TOOLS if t.name in names],
                            {'profile_add': forbidden_writer, 'finish_turn': lambda: '收工'})
        def budget_and_ledger(adapter, event_store):
            async def budget_tools(calls):
                batch = tuple(calls)
                report['requested_calls'].extend(asdict(c) for c in batch)
                denied = [c.call_id for c in batch if c.name not in names]
                refused = False
                if denied:
                    report['policy_violation_ids'].extend(denied)
                    report['policy_refused'].extend(c.call_id for c in batch)
                    refused = True
                elif batch:
                    try:
                        budget.reserve('tools', len(batch))
                    except HostBudgetRefused:
                        refused = True
                if refused:
                    # Reject all over-budget calls explicitly; execute none, drop no IDs.
                    result = DispatchResult(tuple(Receipt(c.call_id,c.name,
                        '本次未执行：离线宿主白名单或预算拒绝。','receipt') for c in batch), True)
                else:
                    result = await adapter(batch)
                report['receipts'].extend(asdict(r) for r in result.receipts)
                return result
            return tools_with_receipt_ledger(budget_tools, event_store)

        wire = ShadowWiring(enabled=True, store=store, batch_of=con.consumed_batch,
                            table=table, backend=backend, receipt_wrapper=budget_and_ledger,
                            journal_root=root/'journal', approved_root=root,
                            source_policy='qq_plain', limit=1, ttl=0.3)
        run_tools = wire.run_tools

        async def generate(config, messages, transport_arg, **kwargs):
            budget.reserve('main')
            report['main_inputs'].append(messages)
            if started is not None:
                started.set()
            if case in ('main_error', 'main_error_close_error'):
                report['main_callback_error'] = 'OSError'
                raise OSError('synthetic main failure')
            if case == 'main_timeout':
                await asyncio.Event().wait()
            if budget.started['main'] == 1:
                name = 'self_note' if case == 'policy_refusal' else 'profile_add'
                who = 'qq:222' if case == 'wrong_who' else 'qq:111'
                calls = [ToolCall('host-c1', name, json.dumps(
                    {'text': '我一直很怕吵。', 'who': who}, ensure_ascii=False))]
                if case in ('tools_refusal', 'policy_mixed_refusal'):
                    second = 'self_note' if case == 'policy_mixed_refusal' else 'profile_add'
                    calls.append(ToolCall('host-c2',second,calls[0].arguments))
                return Result(text='', reasoning='', attempts=1, status='completed',
                              calls=tuple(calls))
            return Result(text='SHADOW-FIXTURE-ACK', reasoning='', attempts=1,
                          status='completed')

        async def read():
            return CHECK_THE_BUS if con.bus.pending() else ''

        async def noop():
            pass

        if case == 'init_error':
            raise RuntimeError('synthetic initialization failure')
        con.assemble(root=root/'runtime', head=[], prefix=[],
                     config=Config(base_url='https://offline.invalid/v1', model='fixture',
                                   thinking=False, max_tokens=2048),
                     transport=transport, generate=generate, read=read,
                     handoff=SimpleNamespace(flush_handoff=noop, batch_end_check=noop,
                                             _maintenance=noop),
                     linked_accounts=lambda _: (), event_store=store,
                     run_tools=run_tools, wrap_round=wire.hook, grace_ms=20,
            group_policy=_boundary_group_policy,
        )
        draft = dict(type='message', ts='2026-10-20T03:00:00+08:00', source='qq',
                     origin='external', sender_key='qq:111',
                     text='[QQ群444·消息#7] A：我一直很怕吵。',
                     meta={'conversation':'group:444','message_id':'7','user_id':'111',
                           'group_id':'444','self_id':'999','segment_types':['text'],
                           'has_user_text':True}, dedup_key='qq:999:group:444:7')
        report['source'] = draft
        con.bus.push(store.append(draft), trigger='flush')
        run_task = asyncio.create_task(con.run(), name='offline-shadow-host')
        try:
            await asyncio.wait_for(run_task, execution_seconds)
        except asyncio.CancelledError:
            report['status'] = 'cancelled'
            if asyncio.current_task().cancelling():
                report['primary_error'] = 'CancelledError'
                raise
        except TimeoutError:
            report['status'] = 'execution_timeout'
            report['primary_error'] = 'TimeoutError'
        except Exception as exc:
            report['status'] = 'refused' if any(budget.refused.values()) else 'failed'
            report['primary_error'] = type(exc).__name__
        else:
            if any(budget.refused.values()) or report['policy_refused']:
                report['status'] = 'refused'
            elif len(wire.stage.records) == 1 and wire.stage.records[0].get('status') == 'shadow':
                report['status'] = 'fixture_passed'
            else:
                report['status'] = 'review_rejected'
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        report['status'] = 'failed'
        report['primary_error'] = type(exc).__name__
    finally:
        report['primary_status'] = report['status']
        report['budget'] = budget.snapshot()
        report['accounting'] = {
            'main': len(report['main_inputs']) == budget.started['main'],
            'review': len(report['review_inputs']) == budget.started['review'],
            'tools': len(report['requested_calls']) == (budget.started['tools']
                     + budget.refused['tools'] + len(report['policy_refused']))}
        if wire is not None:
            report['reviews'] = list(wire.stage.records)
            report['pending_dispatch'] = len(wire.adapter.dispatch_pending)
            report['pending_review'] = len(wire.adapter.review_pending)
            report['pending_coordinator'] = len(wire.coordinator.pending)
            wire.close()
            try:
                wire.context()
            except ContextRefused as exc:
                report['context_after_close'] = str(exc)
            else:
                report['cleanup_errors'].append('context_still_available')
        if con is not None and con.session is not None:
            report['session'] = [asdict(r) for r in con.session.records()]
        if store is not None:
            report['events'] = [json.loads(e.to_line()) for e in store.range()]
        journal = root/'journal/shadow-round-state.json'
        report['journal'] = json.loads(journal.read_text('utf-8')) if journal.exists() else None
        report['run_task_done'] = run_task is None or run_task.done()
        try:
            await asyncio.wait_for(transport.aclose(), cleanup_seconds)
        except Exception as exc:
            report['cleanup_errors'].append(type(exc).__name__)
        report['transport_closed'] = transport.closed
        report['new_pending_tasks'] = [t.get_name() for t in asyncio.all_tasks()
                                       if t not in before_tasks and not t.done()]
        report['files'], report['file_hashes'] = [], {}
        try:
            for path in sorted(root.rglob('*')):
                attrs = getattr(path.lstat(), 'st_file_attributes', 0)
                if path.is_symlink() or attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise RuntimeError('evidence_reparse_refused')
                if path.is_file():
                    if path.stat().st_size > 1024 * 1024:
                        raise RuntimeError('evidence_file_size_refused')
                    rel = str(path.relative_to(root))
                    report['files'].append(rel)
                    report['file_hashes'][rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception as exc:
            report['cleanup_errors'].append('evidence:' + type(exc).__name__)
        report['cleanup_ok'] = (transport.closed and not report['cleanup_errors']
                                and report['run_task_done'] and not report['new_pending_tasks']
                                and not report.get('pending_dispatch', 0)
                                and not report.get('pending_review', 0)
                                and not report.get('pending_coordinator', 0)
                                and all(report['accounting'].values()))
        if not report['cleanup_ok'] and report['status'] == 'fixture_passed':
            report['status'] = 'cleanup_failed'
        if report['status'] == 'fixture_passed' and report['cleanup_ok']:
            try:
                _remove_owned_root(root, parent, marker)
                report['root_removed'] = True
            except Exception as exc:
                report['cleanup_errors'].append(type(exc).__name__)
                report['cleanup_ok'] = False
                report['status'] = 'cleanup_failed'
        report['ok'] = report['status'] == 'fixture_passed' and report['cleanup_ok']
        report['elapsed'] = time.monotonic() - t0
        if audit is not None:
            audit.update(json.loads(json.dumps(report, ensure_ascii=False)))
    return report
