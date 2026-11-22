"""Host-only adapter candidate. No subscriptions, model tools, sender or config reads.

Callbacks are trusted in-process dependencies, NOT remote-service authentication.
They must be supplied by the host, never populated from a model's arguments.
Every request gets an ephemeral owner; approvals cannot survive this call.
"""
from dataclasses import dataclass, asdict
import json
import threading
from .contracts import Evidence, Proposal, parse_proposal, review_binding
from .preview_host import PreviewOwner, HostSnapshot
from .sharing import Exposure, Policy, digest, key, scope


@dataclass(frozen=True)
class TransportEvent:
    bot: str
    scope: str
    sender: str
    message_id: str
    observed_ms: int
    text: str


@dataclass(frozen=True)
class SourceAssessment:
    classification: str
    topics: tuple = ()
    related_to: tuple = ()


@dataclass(frozen=True)
class ReviewRequest:
    binding: str
    evidence: Evidence
    proposal: Proposal
    destination: str
    final_text: str
    policy_version: str
    expires_ms: int


@dataclass(frozen=True)
class ReviewDecision:
    binding: str
    approved: bool
    supported: bool
    exposure: Exposure
    expires_ms: int


class HostBridge:
    def __init__(self, *, bot, destination, allowed_scopes, clock, assess=None, review=None,
                 review_session=None):
        if (not key(bot) or not scope(destination) or not destination.startswith('private:')
                or type(allowed_scopes) is not frozenset or not allowed_scopes
                or not all(scope(s) for s in allowed_scopes) or not callable(clock)
                or (assess is not None and not callable(assess))
                or (review is not None and not callable(review))
                or (review_session is not None and not callable(review_session))):
            raise ValueError('explicit_host_configuration_required')
        self.bot, self.destination = bot, destination
        self.allowed_scopes, self.clock = allowed_scopes, clock
        self._assess, self._review = assess, review
        self._review_session = review_session
        self._lock, self._closed, self._in_call = threading.RLock(), False, False

    @staticmethod
    def result(action, reason):
        return {'dry_run':True, 'will_send':False, 'action':action, 'reason':reason}

    def close(self):
        with self._lock:
            self._closed = True

    def preview(self, event, proposal, *, ready, busy, quiet, history_verified, attempts=()):
        from contextlib import nullcontext
        try:
            with self._review_session() if self._review_session is not None else nullcontext():
                return self._preview(event,proposal,ready=ready,busy=busy,quiet=quiet,
                                     history_verified=history_verified,attempts=attempts)
        except Exception:
            return self.result('defer','review_session_unavailable')

    def _preview(self, event, proposal, *, ready, busy, quiet, history_verified, attempts=()):
        """HOST-ONLY entry: event and state come from trusted transport/history adapters.

        No history persistence or transport subscription is implemented here.
        Missing history must be passed as unverified, never converted to empty.
        """
        with self._lock:
            if self._closed or self._in_call:
                return self.result('deny','bridge_closed_or_reentrant')
            self._in_call = True
            owner = None
            try:
                now = self.clock()
                if (type(event) is not TransportEvent or event.bot != self.bot
                        or event.scope not in self.allowed_scopes
                        or type(now) is not int or now <= 0
                        or type(event.observed_ms) is not int
                        or not 0 <= now-event.observed_ms < Policy().max_age_ms):
                    return self.result('deny','transport_identity_scope_or_time')
                if not all(type(x) is bool for x in (ready,busy,quiet,history_verified)):
                    return self.result('defer','host_state_unverified')
                if not history_verified or type(attempts) is not tuple:
                    return self.result('defer','history_unverified')
                if self._assess is None or self._review is None:
                    return self.result('review','independent_services_unconfigured')
                # Validate original envelope BEFORE invoking either external hook.
                owner = PreviewOwner(self.bot,self.destination,now=self.clock,enabled=True)
                base = Evidence(event.bot,owner.epoch,event.scope,event.sender,event.message_id,
                    event.observed_ms,event.text,event.scope.split(':',1)[0],'unknown')
                owner._authority._schema(base)
                if type(proposal) is not Proposal:
                    return self.result('deny','proposal_type')
                p = parse_proposal(json.dumps({**asdict(proposal),
                    'spans':[{'start':a,'end':b} for a,b in proposal.spans]},ensure_ascii=False))
                if any(b > len(event.text) for a,b in p.spans):
                    return self.result('deny','span_outside_source')
                assessment = self._assess(event)
                if self._closed:
                    return self.result('deny','bridge_closed_during_assessment')
                if type(assessment) is not SourceAssessment:
                    return self.result('review','source_assessment_unavailable')
                e = Evidence(event.bot,owner.epoch,event.scope,event.sender,event.message_id,
                    event.observed_ms,event.text,base.visibility,assessment.classification,
                    assessment.topics,assessment.related_to)
                owner._authority._schema(e)
                if e.classification == 'credential':
                    return self.result('deny','credential_not_shareable')
                if e.classification == 'unknown':
                    return self.result('review','source_classification_unknown')
                final = ('刚才看到一句话：'+' / '.join(e.text[a:b] for a,b in p.spans)
                         if p.mode=='quote' else p.text)
                if p.mode=='quote' and p.text and p.text != final:
                    return self.result('deny','quote_output_mismatch')
                deadline = min(now+30000,event.observed_ms+Policy().max_age_ms)
                binding = review_binding(e,p,self.destination,final,owner._policy.version)
                request = ReviewRequest(binding,e,p,self.destination,final,owner._policy.version,deadline)
                verdict = self._review(request)
                if self._closed:
                    return self.result('deny','bridge_closed_during_review')
                if (type(verdict) is not ReviewDecision or verdict.binding != request.binding
                        or type(verdict.approved) is not bool or type(verdict.supported) is not bool
                        or type(verdict.exposure) is not Exposure
                        or type(verdict.expires_ms) is not int
                        or not self.clock() < verdict.expires_ms <= request.expires_ms):
                    return self.result('deny','review_binding_schema_or_expiry')
                if not verdict.approved or not verdict.supported:
                    return self.result('deny','independent_review_rejected')
                owner.publish(HostSnapshot(owner.epoch,0,self.clock(),'host-adapter',1,
                    ready,busy,quiet,history_verified,attempts))
                ref = owner.capture(e,expires_ms=deadline)
                owner.review(ref,p,supported=True,approved=True,exposure=verdict.exposure,
                             expires_ms=verdict.expires_ms)
                return owner.preview(json.dumps({'source_ref':ref,'proposal':{
                    **asdict(p),'spans':[{'start':a,'end':b} for a,b in p.spans]}},ensure_ascii=False))
            except Exception:
                # Never include source text, exception details, identifiers or credentials.
                return self.result('defer','host_adapter_failed_closed')
            finally:
                if owner is not None:
                    owner.close()
                self._in_call = False
