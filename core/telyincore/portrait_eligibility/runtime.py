"""Common Console write-before orchestration. No Bot/channel allowlist."""
import asyncio,hashlib,time,os,re
from contextlib import contextmanager
from pathlib import Path
from collections import deque
_LOADED_SOURCE_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
from . import release
from .context import capture
from .direct_source import direct_source
from .provenance import captured
from .gate import arguments,signature,authorized,draft_digest,rejected_copies
from .journal import Claims
from .review_journal import persist_review,read_review,read_reviews
from .configured import ConfiguredBackend
from .service import CandidateIntake
from .eligibility_contract import ELIGIBILITY_CONTRACT
from .review_contract import REVIEW_CONTRACT
from ._frozen.contracts import Refused,bounded_json,canonical
from ..portrait_shadow.review import ReviewCoordinator

_SAVED_CLAIM = re.compile(
    r'(?:^|[。！？；，：、\n—])\s*(?:那(?:我)?(?:就)?|我(?:已经|这就|就|也)?|已经)?'
    r'(?:记下来了|记下了|记住了|保存好了|保存了|写进(?:了)?画像|写入(?:了)?画像)(?=$|[。！？；，：、\s]|你|这)'
)
_SAVED_RECEIPTS = ('记下了（画像记录', '这条原话已记入画像', '画像已有相同的最新记录')


class ProfileWriteGuard:
    def __init__(self,console,handler,*,config_of,transport,backend_factory=None,enabled=None,clock=time.monotonic):
        self.console=console;self.handler=handler
        self.binding=handler._portrait_write_binding
        self.config_of=config_of;self.transport=transport;self.backend_factory=backend_factory or ConfiguredBackend
        self.enabled=release.ENABLED if enabled is None else enabled
        self.audit=[];self.busy=False;self.clock=clock
        # Only official Console-registered writers are capability-gated. Trusted
        # direct library handlers outside a Console retain their old API.
        self.binding['required']=self.enabled is True
        self.binding['verified_sources'] = self.verified_sources
        self._denied_drafts = deque(maxlen=64)
        self._receipt_bridge = None
        self._write_confirmed = False

    @contextmanager
    def verified_sources(self):
        from ..event_reader import EventReadCache
        from ..events import EventError
        store = self.console.app.store
        try:
            owner = self.console.current_batch_identity()
            if owner is None:
                raise ValueError('no active batch')
        except ValueError as error:
            raise PermissionError('画像原话的会话已变化。') from error
        policy = getattr(self.console, 'event_filter', None)
        reader = getattr(self.console, 'portrait_source_reader', None)
        temporary = reader is None
        if temporary:
            reader = EventReadCache(store.log.root)
        elif reader.root != store.log.root.absolute():
            raise PermissionError('画像原话的事件库已变化。')
        try:
            with reader.borrow(verify_unchanged=True) as disk:
                read = {}
                def lookup(cursor):
                    event = store.get(cursor)
                    if event is None or event != disk.get(cursor) or (policy and policy(event) is not True):
                        return None
                    read[cursor] = event
                    return event
                yield lookup
                if (self.console.app.store is not store or self.console.current_batch_identity() is not owner
                        or getattr(self.console, 'event_filter', None) is not policy
                        or any(lookup(cursor) != original for cursor, original in tuple(read.items()))):
                    raise PermissionError('画像原话来源当前无法核实。')
        except (EventError, OSError) as error:
            raise PermissionError('画像原话来源当前无法核实。') from error
        finally:
            if temporary:
                reader.close()

    def capture_sources(self, turn, author, primary, *, include_context=True):
        # Record exactly what the admitted write reviewed. Disk reads belong
        # to explicit source lookups, which verify these event fingerprints.
        return captured(turn, author, primary, self.console.app.store.get,
                        include_context=include_context)

    def speech_denial(self, call):
        if not self.enabled or self._receipt_bridge is None or self._write_confirmed:
            return None
        if self.console.current_batch_identity() is not self._receipt_bridge:
            return None
        body = bounded_json(call.arguments, 65536, 'portrait_speech')
        if type(body) is dict and isinstance(body.get('text'), str) and _SAVED_CLAIM.search(body['text']):
            return '本轮画像未保存，消息还未发出。请按你的判断继续聊天，只去掉已保存或已记住的表述。'
        return None
    def public_state(self):
        # Only non-sensitive in-process facts; no home/author/draft or callbacks.
        last = self.audit[-1] if self.audit else read_review(self.binding["home"])
        summary = {key: last[key] for key in ('status', 'stage', 'error_type',
                   'error_code', 'candidate_assessment', 'candidate_review_items', 'review_reason', 'review_dimensions', 'review_diagnostics', 'items', 'at', 'conflict_fields', 'conflict_shapes', 'write_grants', 'rate_limit', 'request_admission', 'protocol_retry', 'candidate_quote_projection', 'memory_kind', 'joint_source_anchor', 'joint_source_review') if key in last}
        return dict(version=1, process_id=os.getpid(), enabled=self.enabled is True,
                    last_review=summary or None,
                    recent_reviews=read_reviews(self.binding["home"]),
                    writer_capability_required=self.binding['required'] is True,
                    busy=self.busy, release_id=release.RELEASE_ID,
                    acceptance_id=getattr(release, 'ACCEPTANCE_ID', None),
                    acceptance_result_sha256=getattr(
                        release, 'ACCEPTANCE_RESULT_SHA256', None),
                    eligibility_contract=ELIGIBILITY_CONTRACT,
                    review_contract=REVIEW_CONTRACT,
                    last_candidate_assessment=(last.get('candidate_assessment')),
                    loaded_runtime_source_sha256=_LOADED_SOURCE_SHA256)

    def identity(self):
        cfg=self.config_of();base=cfg.peek() if callable(getattr(cfg,'peek',None)) else cfg
        return canonical([base.base_url,base.model,getattr(cfg,'kind',None)])
    async def prepare(self,call,snapshot,turn,identity,*,bridge,record=None):
        if not call.call_id or call.name!='profile_add':raise Refused('portrait_call_identity')
        args=arguments(bounded_json(call.arguments,16384,'portrait_write'))
        before=signature(self.binding,args)
        authors={m.author for m in turn.messages}
        if args['who'] in authors:author=args['who']
        elif len(authors)==1:
            author=next(iter(authors))
            if self.binding['resolve'](args['who'])!=self.binding['resolve'](''):raise Refused('portrait_current_author_only')
        else:raise Refused('portrait_exact_author_required')
        if record is not None: record['stage'] = 'request_admission'
        admission = Claims(self.binding['home']).claim(turn.scope,turn.turn_id,call.call_id,author=author)
        if record is not None: record['request_admission'] = admission
        def current():
            return capture(self.console,bridge=bridge)[1]==snapshot and self.identity()==identity and signature(self.binding,args)==before
        # With a single source the implicit target must match the requested
        # target and its provenance must identify the source room and author.
        provenance = turn.scope if turn.scope.startswith('private:') else turn.scope + '|sender=' + author
        target = self.binding['resolve'](args['who'])
        direct = (direct_source(self.console, turn, author, args['text'])
                  if target == self.binding['resolve']('') and target[1] == provenance
                  else None)
        if direct is not None:
            if not current(): raise Refused('portrait_context_changed')
            cursor, kind = direct
            if record is not None: record.update(stage='direct_source', memory_kind=kind)
            return (id(self.handler),before), dict(call_id=call.call_id, memory_kind=kind, source_cursor=cursor,
                                                    source_author=author, source_text=args['text'],
                                                    portrait_sources=self.capture_sources(turn, author, str(cursor),
                                                        include_context=False))
        backend=self.backend_factory(self.config_of(),self.transport,self.console.signal)
        evidence_turn = turn
        resolver = getattr(backend, 'select_evidence', None)
        if turn.prior_messages and callable(resolver):
            if record is not None: record['stage'] = 'source_selection'
            try:
                evidence_turn = await resolver(turn, author, dict(who=author,text=args['text']))
                if not current(): raise Refused('portrait_context_changed')
            finally:
                if record is not None:
                    from .review_diagnostics import review_diagnostics
                    record['review_diagnostics'] = review_diagnostics('', backend)
        # One nomination per author. A selected current clarification anchors
        # the claim; the antecedent stays available to both semantic checks.
        intake_turn = evidence_turn
        if len(evidence_turn.messages) == 2 and len({m.author for m in evidence_turn.messages}) == 1:
            from dataclasses import replace
            current_ids = {message.message_id for message in turn.messages}
            anchor = next((message for message in reversed(evidence_turn.messages)
                           if message.message_id in current_ids), evidence_turn.messages[0])
            context = tuple(sorted((*evidence_turn.prior_messages,
                                    *(m for m in evidence_turn.messages if m is not anchor)),
                                   key=lambda m: int(m.message_id)))
            intake_turn = replace(evidence_turn, messages=(anchor,), prior_messages=context)
            if record is not None:
                record['joint_source_anchor'] = ('current' if anchor.message_id in current_ids else 'historical')
        intake=CandidateIntake(turn_of=lambda:intake_turn,validate_turn=lambda t:current() and t==intake_turn,invoke=backend,max_requests=3)
        if record is not None:
            record['stage'] = 'candidate'
        try:
            batch=await intake.assess(proposal=dict(who=author,text=args['text']),
                                      materialize_locally=isinstance(backend, ConfiguredBackend))
        finally:
            if record is not None:
                from .review_diagnostics import review_diagnostics
                record['review_diagnostics'] = review_diagnostics('', backend)
            if record is not None and getattr(intake, 'protocol_retry', None):
                record['protocol_retry'] = intake.protocol_retry
        if record is not None:
            if getattr(intake, 'quote_projection', None):
                record['candidate_quote_projection'] = intake.quote_projection
            record['candidate_assessment']=dict(
                status=batch.status,decisions=batch.selector_summary)
            record['candidate_review_items']=[dict(source_index=i,reason=reason) for i,reason in batch.review_items]
        joint_uncertain = (
            intake_turn is not evidence_turn and batch.status == 'needs_review'
            and intake_turn.messages[0].message_id in current_ids
            and all(int(m.message_id) <= int(intake_turn.messages[0].message_id)
                    for m in evidence_turn.messages)
            and {reason for _, reason in batch.review_items} == {'declared_uncertain'}
            and len(batch.selector_summary) == 1
            and batch.selector_summary[0]['decision'] == 'uncertain'
        )
        if joint_uncertain:
            # A single-source nomination cannot always resolve a two-source
            # fact. Let the independent reviewer judge both bound originals.
            # No candidate, writer grant or semantic verdict is manufactured.
            if record is not None:
                record['joint_source_review'] = True
        else:
            if batch.status != 'candidate_only':
                reasons = {reason for _, reason in batch.review_items}
                if reasons == {'quote_includes_declared_unrelated_background'}:
                    raise Refused('portrait_quote_background_conflict')
                if reasons == {'declared_uncertain'}:
                    raise Refused('portrait_source_uncertain')
                if reasons == {'quote_omits_declared_scope'}:
                    raise Refused('portrait_candidate_scope_mismatch')
                raise Refused('portrait_'+batch.status)
            candidate = next((c for c in batch.candidates
                              if bounded_json(c.raw_arguments, 16384, 'portrait_candidate')['who'] == author), None)
            if candidate is None:
                raise Refused('portrait_author_has_no_candidate')
            backend.memory_kind = candidate.memory_kind
            if record is not None:
                record['memory_kind'] = candidate.memory_kind
        if isinstance(backend, ConfiguredBackend):
            backend.literal_current_report = (
                len(turn.messages) == 1 and evidence_turn.messages == turn.messages
                and batch.single_report_without_duration(turn.messages[0], args['text']))
        coordinator=ReviewCoordinator(limit=1,ttl=20,validate_turn=lambda t:current() and t==evidence_turn)
        request=coordinator.issue(evidence_turn,dict(who=author,text=args['text']))
        if record is not None:
            record['stage'] = 'independent_review'
        decision=await coordinator.review(request,backend.review)
        kind = backend.memory_kind
        if record is not None:
            # Fixed codes and verdicts only. Never persist prompts, drafts or raw errors.
            record['review_reason'] = (decision.reason if decision.reason in
                ('advisory_pass','advisory_fail','advisory_unknown','expired',
                 'incomplete_completion','refusal_or_tool_call','review_budget',
                 'consumed_or_foreign_request') else 'review_protocol_error')
            from .review_diagnostics import review_diagnostics
            record['review_diagnostics'] = review_diagnostics(decision.reason, backend)
            record['review_dimensions'] = dict(decision.dimensions)
            record['memory_kind'] = kind
        if decision.status!='pass':
            from .review_diagnostics import review_diagnostics
            detail = review_diagnostics(decision.reason, backend)
            specific = detail.get('error_code')
            if specific in ('portrait_kind_unknown', 'portrait_kind_binding', 'portrait_kind_protocol', 'portrait_per_write_request_budget'):
                raise Refused(specific)
            if specific == 'review_timeout':
                raise Refused('portrait_independent_review_timeout')
            reason = {'advisory_fail': 'fail', 'advisory_unknown': 'unknown'}.get(decision.reason, 'protocol_error')
            raise Refused('portrait_independent_review_'+reason)
        kind = backend.memory_kind
        if record is not None: record['memory_kind'] = kind
        if not current():raise Refused('portrait_context_changed')
        # Advisory pass is not itself a capability. Host additionally checked
        # candidate coverage, exact draft, identity, source, writer target and TTL.
        sources = self.capture_sources(evidence_turn, author, decision.source_id)
        return (id(self.handler),before),dict(call_id=call.call_id,review=request.request_id,
                                                memory_kind=kind,portrait_sources=sources)
    def wrap(self,run_tools):
        if not self.enabled:return run_tools
        async def guarded(calls):
            calls=tuple(calls)
            profiles=[c for c in calls if c.name=='profile_add']
            if not profiles:
                if self._denied_drafts:
                    bridge = self._receipt_bridge
                    with rejected_copies(self._denied_drafts, lambda: bridge is not None and self.console.current_batch_identity() is bridge):
                        return await run_tools(calls)
                return await run_tools(calls)
            if self.busy:raise Refused('portrait_guard_busy')
            self.busy=True;tickets={};snapshot=None;identity=None;bridge=None;grant_deadline=0;refusal=None;refusals={}
            record=dict(profile_calls=len(profiles),status='started',semantic_review='not_completed',write_grants=0)
            items=[dict(call_index=i,status='not_reviewed',write_grants=0) for i in range(len(profiles))] if len(profiles)<=2 else []
            record['items']=items
            self.audit.append(record)
            if len(self.audit)>256:del self.audit[0]
            try:
                try:
                    bridge=self.console.current_batch_identity()
                    if self._receipt_bridge is not bridge:
                        self._denied_drafts.clear()
                    self._receipt_bridge = bridge
                    self._write_confirmed = False
                    turn,snapshot=capture(self.console,bridge=bridge);identity=self.identity()
                    if len(profiles)>2 or len({c.call_id for c in profiles})!=len(profiles):raise Refused('portrait_profile_batch_limit_or_duplicate_id')
                    deadline=self.clock()+90
                    for c,item in zip(profiles,items):
                        item['status']='reviewing'
                        try:
                            key,ticket=await asyncio.wait_for(self.prepare(c,snapshot,turn,identity,bridge=bridge,record=item),min(60,max(.001,deadline-self.clock())))
                            if key in tickets:raise Refused('portrait_duplicate_draft')
                            tickets[key]=ticket
                            item['status']='approved'
                        except asyncio.CancelledError:
                            item['status']='cancelled'
                            raise
                        except Exception as error:
                            item.update(status='refused',error_type=type(error).__name__,error_code=str(error) if isinstance(error,Refused) else None)
                            if hasattr(error,'rate_limit'):item['rate_limit']=dict(error.rate_limit)
                            raise
                        finally:
                            # Keep the legacy top-level summary; individual results never overwrite each other.
                            record.update({k:v for k,v in item.items() if k not in ('call_index','status','write_grants')})
                    grant_deadline=min(deadline,self.clock()+5)
                    direct_count = sum('source_cursor' in ticket for ticket in tickets.values())
                    review_state = ('completed' if not direct_count else
                                    'direct_source' if direct_count == len(tickets) else 'mixed')
                    record.update(status='prepared',semantic_review=review_state,write_grants=len(tickets))
                    for item in items:item['write_grants']=1
                except asyncio.CancelledError:
                    record.update(status='cancelled');raise
                except Exception as e:
                    tickets.clear();record.update(status='refused',error_type=type(e).__name__,error_code=str(e) if isinstance(e,Refused) else None)
                    refusal = str(e) if isinstance(e, Refused) else type(e).__name__
                    for c,item in zip(profiles,items):
                        if item['status']=='approved':
                            item['status']='withheld'
                            item['withheld_reason']='portrait_batch_aborted'
                            item_reason='portrait_batch_aborted'
                        elif item['status']=='not_reviewed':
                            item_reason='portrait_batch_not_reviewed'
                        else:
                            item_reason=item.get('error_code') or refusal
                        try:
                            value=arguments(bounded_json(c.arguments,16384,'portrait_write'))
                            if item_reason in {'portrait_quote_background_conflict','portrait_candidate_scope_mismatch',
                                              'portrait_needs_review','portrait_source_uncertain','portrait_no_candidate',
                                              'portrait_no_supported_source','portrait_independent_review_fail','portrait_independent_review_unknown'}:
                                key_digest = draft_digest(value['text'])
                                if key_digest is not None:
                                    self._denied_drafts.append(key_digest)
                            item_key=(id(self.handler),signature(self.binding,value))
                            refusals[item_key]=dict(reason=item_reason,rate_limit=item.get('rate_limit'))
                        except Exception:
                            pass  # Invalid arguments cannot acquire a write capability.
                    if hasattr(e, 'rate_limit'):
                        record['rate_limit'] = dict(e.rate_limit)
                    if refusal == 'chat_tool_identity_drift':
                        record['stage'] = 'proposal'
                        record['conflict_fields'] = getattr(e, 'conflict_fields', [])
                        record['conflict_shapes'] = getattr(e, 'conflict_shapes', {})
                    elif refusal == 'v3_audit_json':
                        record['stage'] = 'selector'
                def current():
                    try:
                        if self.clock() >= grant_deadline or snapshot is None:
                            return False
                        current_turn, current_snapshot = capture(self.console, bridge=bridge)
                        if current_snapshot != snapshot or self.identity() != identity:
                            return False
                        return all(
                            direct_source(self.console, current_turn, ticket['source_author'], ticket['source_text'])
                            == (ticket['source_cursor'], ticket['memory_kind'])
                            for ticket in tickets.values() if 'source_cursor' in ticket
                        )
                    except Exception:
                        return False
                with authorized(tickets,current,refusal=refusal,rate_limit=record.get('rate_limit'),refusals=refusals):
                    with rejected_copies(self._denied_drafts, lambda: bridge is not None and self.console.current_batch_identity() is bridge):
                        result=await run_tools(calls)
                record['dispatch_returned']=True
                if bridge is self._receipt_bridge:
                    by_id = {receipt.call_id: receipt for receipt in result.receipts
                             if receipt.name == 'profile_add'}
                    self._write_confirmed = all(
                        (receipt := by_id.get(call.call_id)) is not None
                        and receipt.stage == 'handler' and receipt.text.startswith(_SAVED_RECEIPTS)
                        for call in profiles)
                return result
            finally:
                self.busy=False
                persist_review(self.binding['home'], record)
        guarded.settled_receipts=getattr(run_tools,'settled_receipts',None)
        return guarded
