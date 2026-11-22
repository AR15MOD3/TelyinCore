"""Default-disabled, temporary synthetic SHADOW replay assembly entry.

No serve/QQ/plugin/tick/Engine/client factory or native writer is present. Injected
callbacks are trusted host code, not a security sandbox. Deadlines are cooperative;
a real deployment must additionally own its supervisor and network policy.
"""
from channel_view_policy import qq_group_policy as _boundary_group_policy
import asyncio
from dataclasses import asdict, dataclass
import inspect
import json
import math
from pathlib import Path
import re
import stat
import tempfile
import time
from types import SimpleNamespace


class EntryRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class Limits:
    main: int = 2
    review: int = 2
    attempts: int = 4
    tools: int = 2
    seconds: float = 10.0
    cleanup: float = 1.0

    def validate(self):
        for key, cap in [('main', 2), ('review', 2), ('attempts', 4), ('tools', 2)]:
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= cap:
                raise EntryRefused('invalid_limit:' + key)
        for key, cap in [('seconds', 240), ('cleanup', 30)]:
            value = getattr(self, key)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= cap:
                raise EntryRefused('invalid_limit:' + key)
        if self.cleanup >= self.seconds:
            raise EntryRefused('cleanup_must_be_inside_total')


@dataclass(frozen=True)
class Source:
    author: str
    text: str


@dataclass(frozen=True)
class Dependencies:
    config: object
    main_transport: object
    review_transport: object
    generate: object
    review: object  # async (payload, budgeted_review_transport) -> Completion


class Ledger:
    def __init__(self, limits):
        self.limits = limits
        self.started = dict(main=0, review=0, attempts=0, tools=0)
        self.refused = []
        self.closed = False
        self.role_attempts = dict(main=0, review=0)
        self.reserved_output = 0

    def attempt(self, role):
        if (role not in self.role_attempts or
                self.started[role] != self.role_attempts[role] + 1):
            self.refused.append('unreserved_or_retry:' + role)
            raise EntryRefused('unreserved_or_retry')
        self.reserve('attempts')
        self.role_attempts[role] += 1
        self.reserved_output += 2048 if role == 'main' else 4096

    def reserve(self, kind):
        if self.closed or self.started[kind] >= getattr(self.limits, kind):
            self.refused.append(kind)
            raise EntryRefused('budget_or_closed:' + kind)
        self.started[kind] += 1


class BudgetTransport:
    def __init__(self, inner, ledger, role):
        self.inner, self.ledger, self.role = inner, ledger, role
        self.active = 0
        self.closed = False

    async def stream_chat(self, base, body, *, frame_idle_ms, signal=None, path=None):
        # This candidate is an offline injection entry, not a real-provider host.
        if (self.closed or base != 'https://' + self.role + '.invalid/v1'
                or path != ('/responses' if self.role == 'main' else '/chat/completions')):
            raise EntryRefused('offline_endpoint_or_closed')
        key, cap = ('max_output_tokens', 2048) if self.role == 'main' else ('max_tokens', 4096)
        if type(body.get(key)) is not int or not 1 <= body[key] <= cap:
            raise EntryRefused('output_budget')
        self.ledger.attempt(self.role)
        self.active += 1
        stream = None
        try:
            stream = self.inner.stream_chat(base, body, frame_idle_ms=frame_idle_ms,
                                            signal=signal, path=path)
            total = 0
            async for chunk in stream:
                if not isinstance(chunk, (bytes, str)):
                    raise EntryRefused('transport_chunk_type')
                total += len(chunk.encode('utf-8') if isinstance(chunk, str) else chunk)
                if total > (131072 if self.role == 'main' else 1048576):
                    raise EntryRefused('response_byte_budget')
                yield chunk
        finally:
            try:
                if stream is not None:
                    await stream.aclose()
            finally:
                self.active -= 1

    async def aclose(self):
        if self.active:
            raise EntryRefused('active_streams_not_drained')
        try:
            await self.inner.aclose()
        finally:
            self.closed = True


def _validate_root(root):
    if not isinstance(root, (str, Path)):
        raise EntryRefused('explicit_root_required')
    root = Path(root)
    anchor = Path(tempfile.gettempdir()).resolve()
    if (not root.is_absolute() or root.exists() or not root.name.startswith('portrait-entry-')
            or not root.parent.is_dir() or not root.parent.resolve().is_relative_to(anchor)):
        raise EntryRefused('new_owned_temporary_root_required')
    for node in [root.parent, *root.parent.parents]:
        attrs = getattr(node.lstat(), 'st_file_attributes', 0)
        if node.is_symlink() or attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise EntryRefused('reparse_root')
    return root


def _validate_sources(scope, sources):
    if type(scope) is not str or re.fullmatch(r'group:qq:[1-9][0-9]{0,19}', scope) is None:
        raise EntryRefused('synthetic_group_scope')
    if type(sources) is not tuple or not 1 <= len(sources) <= 16:
        raise EntryRefused('bounded_source_tuple')
    if any(type(s) is not Source or type(s.author) is not str or
           re.fullmatch(r'qq:[1-9][0-9]{0,19}', s.author) is None or
           type(s.text) is not str or not s.text.strip() for s in sources):
        raise EntryRefused('source_shape')
    if sum(len(s.text.encode('utf-8')) for s in sources) > 32768:
        raise EntryRefused('source_byte_budget')


async def run_shadow_once(*, enabled=False, root=None, scope=None, sources=(),
                          dependencies=None, table=None, permissions=None,
                          receipt_wrapper=None, head=(), limits=Limits(), audit=None):
    """Replay synthetic sources once; return evidence, never native write authority.

    False enable returns before validating/reading paths or touching dependencies.
    CancelledError/original errors are re-raised after cleanup; the caller-owned
    audit dict retains outcome, cleanup errors, ledger and truthful Session state.
    """
    if enabled is not True:
        return dict(status='disabled', root_created=False, model_started=False)
    if type(limits) is not Limits:
        raise EntryRefused('typed_limits_required')
    limits.validate()
    root = _validate_root(root)
    _validate_sources(scope, sources)
    if (type(dependencies) is not Dependencies or not callable(dependencies.generate)
            or not callable(dependencies.review) or not callable(receipt_wrapper)
            or set(table or {}) != {'profile_add'} or table['profile_add'].effect != 'immediate'):
        raise EntryRefused('explicit_shadow_dependencies_and_table')
    if (dependencies.main_transport is dependencies.review_transport or
            getattr(dependencies.config, 'base_url', '') != 'https://main.invalid/v1'
            or getattr(dependencies.config, 'max_tokens', 0) != 2048
            or getattr(dependencies.config, 'retry_delays_ms', None) != ()
            or getattr(dependencies.config, 'thinking', None) is not False):
        raise EntryRefused('offline_config_and_distinct_transports')
    declared = getattr(dependencies.config, 'tools', ())
    if (len(declared) != 1 or type(declared[0]) is not dict or
            declared[0].get('function', declared[0]).get('name') != 'profile_add'):
        raise EntryRefused('profile_only_wire_declaration')
    if (type(head) is not tuple or any(type(x) is not str for x in head)
            or (audit is not None and type(audit) is not dict)):
        raise EntryRefused('head_or_audit_shape')
    # Delayed formal imports; default-disabled path has no deployment imports.
    from portrait_shadow_sequential import SequentialShadowWiring
    from telyincore.console import Console, CHECK_THE_BUS
    from telyincore.events import EventStore

    start = time.monotonic()
    deadline, stop_at = start + limits.seconds, start + limits.seconds - limits.cleanup
    root.mkdir()
    (root/'owner.json').write_text(json.dumps(dict(kind='synthetic-shadow-entry',
        scope=scope, native_write_authorized=False)), encoding='utf-8')
    report = audit if audit is not None else {}
    report.update(status='initializing', synthetic_only=True, native_write_authorized=False,
                  cleanup_errors=[], primary=None, frames=[])
    ledger = Ledger(limits)
    main = BudgetTransport(dependencies.main_transport, ledger, 'main')
    review = BudgetTransport(dependencies.review_transport, ledger, 'review')
    con = wire = None
    primary = None
    observed_errors = []
    expected_ids = ()
    final_notice_seen = False
    try:
        con = Console(write=report['frames'].append)
        con.owner_view = lambda _: True
        store = EventStore(root/'events')

        async def backend(payload):
            ledger.reserve('review')
            try:
                return await dependencies.review(payload, review)
            except BaseException as exc:
                observed_errors.append(exc)
                raise

        def before_dispatch(call, turn):
            ledger.reserve('tools')

        wire = SequentialShadowWiring(enabled=True, store=store,
            batch_of=con.consumed_batch, table=table, backend=backend,
            receipt_wrapper=receipt_wrapper, journal_root=root/'journal', approved_root=root,
            permissions=permissions, before_dispatch=before_dispatch,
            limit=limits.review, ttl=limits.seconds)

        async def generate(config, messages, transport, **kwargs):
            nonlocal expected_ids, final_notice_seen
            try:
                ledger.reserve('main')
                result = await dependencies.generate(config, messages, transport, **kwargs)
                if result.status != 'completed' or result.refusal:
                    raise EntryRefused('main_not_completed')
                if ledger.started['main'] == 1:
                    calls = tuple(result.calls)
                    if (not calls or len(calls) > limits.tools or
                            len({c.call_id for c in calls}) != len(calls) or
                            any(not c.call_id or c.name != 'profile_add' for c in calls)):
                        raise EntryRefused('complete_profile_only_batch_required')
                    expected_ids = tuple(c.call_id for c in calls)
                elif result.calls:
                    raise EntryRefused('followup_tool_calls_forbidden')
                elif result.text != '本次未写入正式画像。':
                    raise EntryRefused('truthful_final_notice_required')
                else:
                    final_notice_seen = True
                return result
            except BaseException as exc:
                observed_errors.append(exc)
                raise

        async def read():
            return CHECK_THE_BUS if con.bus.pending() else ''

        async def noop():
            pass

        con.assemble(root=root/'runtime', head=[dict(role='system', content=x) for x in head],
            prefix=[], config=dependencies.config, transport=main, generate=generate, read=read,
            handoff=SimpleNamespace(flush_handoff=noop, batch_end_check=noop, _maintenance=noop),
            linked_accounts=lambda _: (), event_store=store, run_tools=wire.run_tools,
            wrap_round=wire.hook, grace_ms=20,
            group_policy=_boundary_group_policy,
        )
        for item in sources:
            event = store.append(dict(type='message', ts='', source='qq', origin='external',
                sender_key=item.author, text=item.text,
                meta={'conversation':'group:'+scope.rsplit(':',1)[1]}))
            con.bus.push(event, trigger='flush')
        await asyncio.wait_for(con.run(), max(0.001, stop_at-time.monotonic()))
        if observed_errors:
            raise observed_errors[0]
        dispatches = wire.adapter.audit
        row = dispatches[0] if len(dispatches) == 1 else {}
        report['completion'] = dict(
            expected_ids=list(expected_ids), final_notice_seen=final_notice_seen,
            dispatch_status=row.get('status'),
            stage_ids=[x['call_id'] for x in wire.stage.records])
        complete = (bool(expected_ids) and len(dispatches) == 1
            and row.get('status') == 'completed'
            and tuple(c['call_id'] for c in row['call_snapshot']) == expected_ids
            and tuple(row['captured_ids']) == expected_ids
            and tuple(row['dispatch_started_ids']) == expected_ids
            and row['reviewed'] == len(expected_ids)
            and tuple(x['call_id'] for x in wire.stage.records) == expected_ids
            and all(x['status'] == 'shadow' for x in wire.stage.records)
            and final_notice_seen and not ledger.refused)
        report['status'] = 'shadow_completed' if complete else 'policy_stopped'
    except BaseException as exc:
        primary = (observed_errors[0] if observed_errors and
                   not isinstance(exc, (asyncio.CancelledError, TimeoutError)) else exc)
        report.update(
            status='cancelled' if isinstance(primary, asyncio.CancelledError) else 'failed',
                      primary=type(primary).__name__)
        if primary is not exc:
            report['wrapper_error'] = type(exc).__name__
            primary.add_note('shadow_round_wrapper:'+type(exc).__name__)
    finally:
        ledger.closed = True
        # A single remaining cleanup deadline; do not add time per component.
        for name, close in [('console', con.stop if con else None),
                            ('main_transport', main.aclose), ('review_transport', review.aclose)]:
            if close is None:
                continue
            try:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('cleanup_deadline')
                await asyncio.wait_for(close(), remaining)
            except BaseException as exc:
                report['cleanup_errors'].append(name+':'+type(exc).__name__)
                if isinstance(exc, asyncio.CancelledError) and primary is None:
                    primary = exc
                    report.update(status='cancelled', primary='CancelledError')
        tasks = [t for t in (getattr(getattr(con, 'app', None), '_task', None),
                              getattr(getattr(con, 'app', None), '_pacing_task', None))
                 if t is not None and not t.done()]
        if tasks:
            try:
                await asyncio.wait(tasks, timeout=max(0, deadline-time.monotonic()))
            except BaseException as exc:
                report['cleanup_errors'].append('owned_tasks:'+type(exc).__name__)
                if isinstance(exc, asyncio.CancelledError) and primary is None:
                    primary = exc
                    report.update(status='cancelled', primary='CancelledError')
            finally:
                report['owned_tasks_pending'] = sum(not task.done() for task in tasks)
        else:
            report['owned_tasks_pending'] = 0
        if wire is not None:
            try:
                wire.close()
            except BaseException as exc:
                report['cleanup_errors'].append('context:'+type(exc).__name__)
                if isinstance(exc, asyncio.CancelledError) and primary is None:
                    primary = exc
                    report.update(status='cancelled', primary='CancelledError')
            report['stage_records'] = list(wire.stage.records)
            report['pending'] = dict(dispatch=len(wire.adapter.dispatch_pending),
                review=len(wire.adapter.review_pending), coordinator=len(wire.coordinator.pending))
        records = list(con.session.records()) if con and con.session else []
        report['session'] = [asdict(r) for r in records]
        report['ledger'] = dict(started=dict(ledger.started), refused=list(ledger.refused),
            attempts_by_role=dict(ledger.role_attempts), reserved_output=ledger.reserved_output)
        report['active_streams'] = main.active + review.active
        report['elapsed'] = time.monotonic()-start
        report['cleanup_ok'] = (not report['cleanup_errors'] and not report['active_streams']
            and not any(report.get('pending', {}).values()) and not report['owned_tasks_pending']
            and main.closed and review.closed)
        if not report['cleanup_ok'] and primary is None:
            report['status'] = 'cleanup_failed'
        try:
            (root/'entry-result.json').write_text(
                json.dumps(report,ensure_ascii=False,default=str),encoding='utf-8')
        except OSError as exc:
            report['cleanup_errors'].append('evidence:'+type(exc).__name__)
            report['cleanup_ok'] = False
            if primary is None:
                report['status'] = 'cleanup_failed'
    if primary is not None:
        if report['cleanup_errors']:
            primary.add_note('shadow_entry_cleanup_failed:'+','.join(report['cleanup_errors']))
        raise primary
    if not report['cleanup_ok'] or report['elapsed'] > limits.seconds:
        raise EntryRefused('cleanup_or_deadline_unconfirmed')
    return report


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['--preflight']:
        print(json.dumps(dict(enabled=False, provider_host=False, native_write=False,
                              synthetic_replay_api_only=True)))
    else:
        print('disabled_no_cli_execution', file=sys.stderr)
        raise SystemExit(2)
