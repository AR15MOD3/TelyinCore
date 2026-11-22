"""Eligibility-first candidate intake. No writer, HTTP client or live default.

Uses Core's trusted Turn type; the host supplies its synchronous context
validator and bounded provider backend. Passing this component never grants
profile writes or replaces independent semantic review.
"""
from __future__ import annotations
import asyncio,inspect,json,time
from types import SimpleNamespace
from dataclasses import dataclass
from pathlib import Path
from ..portrait_shadow.evidence import Turn,valid_turn
from ._frozen.contracts import Refused,canonical
from ._frozen.textspan_v3 import TextSpanFlowV3
from .eligibility_contract import clarify_selector, MEMORY_KINDS, temporal_proposal

@dataclass(frozen=True)
class Candidate:
    call_id: str
    name: str
    raw_arguments: str
    memory_kind: str = ""

@dataclass(frozen=True)
class CandidateBatch:
    turn_id: str
    scope: str
    source_sha256: str
    status: str
    candidates: tuple[Candidate,...]
    raw_selector: str
    materialization_json: str
    review_items: tuple[tuple[int,str], ...] = ()
    def single_report_without_duration(self, message, text):
        """A validated literal self-report, with no separate duration evidence."""
        if message.text != text:
            return False
        decisions = json.loads(self.raw_selector)['decisions']
        if len(decisions) != 1:
            return False
        decision = decisions[0]
        return (decision['decision'] == 'eligible' and decision['self_report'] == 'self'
                and decision['author'] == message.author and decision['quote'] == text
                and all(s['role'] in ('claim', 'qualifier')
                        and s['relation'] not in ('continuity', 'frequency') for s in decision['segments']))

    @property
    def write_authorized(self):return False
    @property
    def semantic_review_completed(self):return False
    @property
    def selector_summary(self):
        # Deliberately omit author identifiers, names, source and quoted text.
        raw=json.loads(self.raw_selector)
        return [dict(decision=d['decision'],kind=d['kind'],self_report=d['self_report'])
                for d in raw['decisions']]

def project_exact_quote(flow, proposal):
    """Repair a unique pointer using the validated partition, never a semantic label."""
    items = flow.audit.get('review_items', [])
    if flow.reason != 'audit_needs_review' or len(items) != 1 or items[0]['source_index'] != 0:
        return None
    reason = items[0]['reason']
    if reason not in ('quote_includes_declared_unrelated_background', 'quote_omits_declared_scope'):
        return None
    raw = json.loads(flow.raw_selection_v3)
    decisions = raw['decisions']
    if len(decisions) != 1:
        return None
    decision = decisions[0]
    spans = flow.materialization.read()['decisions'][0]['segments']
    source = flow.envelope.read()[0]['text']
    relevant = [span for span in spans if span['role'] in ('claim', 'qualifier')]
    if decision['decision'] != 'eligible' or decision['self_report'] != 'self' or not relevant:
        return None
    left, right = relevant[0]['start'], relevant[-1]['end']
    if any(span['role'] not in ('claim', 'qualifier') for span in spans
           if left < span['end'] and span['start'] < right):
        return None
    quote = decision['quote']
    start = source.index(quote)  # AuditedFlow already verified its unique location.
    end = start + len(quote)
    complete = source[left:right]
    if source.find(complete) != left or source.find(complete, left + 1) >= 0:
        return None
    if reason == 'quote_includes_declared_unrelated_background':
        if not start <= left < right <= end:
            return None
        projection = 'trim_declared_background'
    else:
        # Only expand an exact draft that already includes the missing qualifiers.
        # Never complete or overwrite the Bot's proposed writing.
        if not proposal or proposal.get('text') != complete or proposal.get('who') != decision['author']:
            return None
        if not (left <= start < end <= right
                and start in {span['start'] for span in relevant}
                and end in {span['end'] for span in relevant}):
            return None
        if any(span['role'] == 'claim' and not start <= span['start'] < span['end'] <= end
               for span in relevant):
            return None
        projection = 'include_declared_qualifiers'
    decision['quote'] = complete
    return canonical(raw), projection


class CandidateIntake:
    """Explicit host-owned intake; no launch registration or background task.

    Request quota is lifetime-wide on this instance and consumed before await.
    A failed/changed context cannot be retried with the same turn identifier.
    """
    def __init__(self, *, turn_of, validate_turn, invoke, max_requests=16,
                 call_seconds=20, total_seconds=45, clock=time.monotonic):
        if not all(callable(x) for x in (turn_of,validate_turn,invoke,clock)):
            raise TypeError('host_dependencies_required')
        if type(max_requests) is not int or not 1<=max_requests<=16:
            raise ValueError('request_budget')
        if any(type(x) not in (int,float) or not 0<x<=60 for x in (call_seconds,total_seconds)):
            raise ValueError('time_budget')
        self.turn_of=turn_of;self.validate_turn=validate_turn;self.invoke=invoke
        self.max_requests=max_requests;self.call_seconds=call_seconds
        self.total_seconds=total_seconds;self.clock=clock
        self.requests=0;self._seen=set();self._busy=False
    def _snapshot(self):
        turn=self.turn_of()
        if not valid_turn(turn) or not 1<=len(turn.messages)<=2:
            raise Refused('trusted_turn_shape')
        if any(m.trusted is not True or m.origin!='external' for m in turn.messages):
            raise Refused('external_trusted_sources_required')
        allowed=self.validate_turn(turn)
        if inspect.iscoroutine(allowed):allowed.close();raise Refused('synchronous_turn_validator_required')
        if allowed is not True:raise Refused('host_turn_rejected')
        return turn,canonical(dict(turn_id=turn.turn_id,scope=turn.scope,
            messages=[dict(message_id=m.message_id,author=m.author,text=m.text,
                           trusted=m.trusted,origin=m.origin) for m in turn.messages]))
    async def assess(self, *, proposal=None, materialize_locally=False):
        if self._busy:raise Refused('intake_busy')
        self._busy=True
        self.quote_projection = None
        self.protocol_retry = None
        try:
            turn,snapshot=self._snapshot();key=(turn.scope,turn.turn_id)
            if key in self._seen:raise Refused('turn_already_consumed')
            self._seen.add(key);deadline=self.clock()+self.total_seconds
            base=Path(__file__).parent/'_frozen'/'inputs'
            sources = [dict(author=m.author,text=m.text) for m in turn.messages]
            rules = dict(rule=(base/'rules/candidate.txt').read_text(encoding='utf-8'),
                         scaffold=(base/'scaffold.txt').read_text(encoding='utf-8'), admitted_kinds=MEMORY_KINDS)
            flow = TextSpanFlowV3(sources, **rules)
            async def call(request):
                if self._snapshot()[1]!=snapshot:raise Refused('turn_context_changed')
                remaining=min(self.call_seconds,deadline-self.clock())
                if remaining<=0:raise Refused('intake_deadline')
                if self.requests>=self.max_requests:raise Refused('intake_request_budget')
                self.requests+=1
                result=await asyncio.wait_for(self.invoke(request),remaining)
                if self.clock()>=deadline:raise Refused('intake_deadline')
                if self._snapshot()[1]!=snapshot:raise Refused('turn_context_changed')
                return result
            def receive(flow, result):
                request = flow.receive_selection(result)
                if request is None:
                    projected = project_exact_quote(flow, proposal)
                    if projected is not None:
                        raw, projection = projected
                        checked = TextSpanFlowV3(sources, **rules)
                        checked.selection_request()
                        local = SimpleNamespace(status='completed', refusal='', attempts=1, calls=[], text=raw)
                        request = checked.receive_selection(local)
                        if request is not None:
                            self.quote_projection = projection
                            return request, checked, flow
                return request, flow, flow

            selected=await call(clarify_selector(flow.selection_request(), proposal=proposal, context=turn.prior_messages))
            try:
                request, flow, evidence_flow = receive(flow, selected)
            except Refused as error:
                reason = str(error)
                if reason not in ('v3_role_relation', 'v3_exact_ordered_partition', 'v3_incomplete_partition') or self.max_requests < 3:
                    raise
                self.protocol_retry = reason
                flow = TextSpanFlowV3(sources, **rules)
                selected=await call(clarify_selector(flow.selection_request(), retry=reason == 'v3_role_relation', proposal=proposal,
                    repair_reason=reason if reason != 'v3_role_relation' else None,
                    context=turn.prior_messages))
                request, flow, evidence_flow = receive(flow, selected)
            # Only structural inconsistency gets one bounded correction, never semantic rejection.
            reasons = {item['reason'] for item in flow.audit.get('review_items', [])}
            if request is None and reasons == {'quote_omits_declared_scope'} and self.requests == 1 and self.max_requests >= 3:
                self.protocol_retry = 'quote_omits_declared_scope'
                flow = TextSpanFlowV3(sources, **rules)
                selected=await call(clarify_selector(flow.selection_request(), proposal=proposal,
                    repair_reason='quote_omits_declared_scope', context=turn.prior_messages))
                request, flow, evidence_flow = receive(flow, selected)
            if request is None:
                status='needs_review' if flow.reason=='audit_needs_review' else 'no_candidate'
                output=None
            else:
                if materialize_locally:
                    # The selector already bound each exact quote to its author.
                    # Copy those values locally and run the existing proposal validator.
                    result = SimpleNamespace(status='completed', refusal='', attempts=1,
                        calls=[SimpleNamespace(name='profile_add', call_id=f'candidate-{index}',
                            arguments=canonical(dict(who=row['author'], text=row['quote'])))
                            for index, row in enumerate(flow.nominations)])
                else:
                    if any(n['kind'] in ('recent_context','plan') for n in flow.nominations):
                        request = temporal_proposal(request)
                    result = await call(request)
                output = flow.receive_proposal(result)
                status = 'candidate_only'
            return CandidateBatch(turn.turn_id,turn.scope,flow.envelope.sha256,status,
                tuple(Candidate(c.call_id,c.name,c.arguments, next(n['kind'] for n in flow.nominations if n['author']==json.loads(c.arguments)['who'])) for c in output.calls) if output else (),
                evidence_flow.raw_selection_v3 or '',canonical(evidence_flow.evidence()['materialization']),
                tuple((item['source_index'],item['reason']) for item in flow.audit.get('review_items',[])))
        finally:self._busy=False
