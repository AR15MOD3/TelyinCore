"""Default-off, in-memory host wiring candidate. NO send/outbox/notify API.

Only sharing_preview is model-facing. All other methods are trusted-host APIs.
This module is not mounted in production launch.py.
"""
from dataclasses import dataclass, replace
import hashlib
import hmac
import json
import secrets
import threading
import uuid

from .contracts import Authority, NeedsReview, Rejected, Reviewer, parse_proposal
from .rubric import rate
from .sharing import Policy, State, digest, evaluate


@dataclass(frozen=True)
class HostSnapshot:
    epoch: str
    generation: int
    observed_ms: int
    route_id: str = ''
    route_count: int | None = None
    ready: bool | None = None
    busy: bool | None = None
    quiet: bool | None = None
    history_verified: bool = False
    attempts: tuple = ()
    revoked: frozenset = frozenset()


class PreviewOwner:
    def __init__(self, bot, destination, *, now, enabled=False, policy=Policy(), preferred_topics=()):
        if type(enabled) is not bool or not callable(now):
            raise ValueError('explicit_bool_and_host_clock_required')
        self.enabled = enabled
        self.epoch = 'preview-'+uuid.uuid4().hex
        self._policy = replace(policy, enabled=True)  # simulation only, no production flag changed
        self._reviewer = Reviewer()
        self._authority = Authority(bot, self.epoch, destination, self._policy, self._reviewer, preferred_topics)
        self._now, self._lock, self._snapshot = now, threading.RLock(), None
        self._records, self._audit = {}, []
        self._audit_key = secrets.token_bytes(32)
        self._closed = False

    def publish(self, snapshot):
        """HOST-ONLY. Missing history is unknown, never automatically an empty ledger."""
        with self._lock:
            if self._closed or type(snapshot) is not HostSnapshot or snapshot.epoch != self.epoch:
                raise ValueError('host_snapshot_namespace')
            if (type(snapshot.generation) is not int or snapshot.generation < 0
                    or type(snapshot.observed_ms) is not int or snapshot.observed_ms <= 0
                    or type(snapshot.attempts) is not tuple or type(snapshot.revoked) is not frozenset):
                raise ValueError('host_snapshot_schema')
            prior = self._snapshot
            if prior and (snapshot.generation < prior.generation
                          or (snapshot.generation == prior.generation and snapshot != prior)
                          or not snapshot.revoked.issuperset(prior.revoked)
                          or not set(prior.attempts).issubset(snapshot.attempts)):
                raise ValueError('snapshot_rollback_or_history_loss')
            self._snapshot = snapshot

    def capture(self, evidence, *, expires_ms):
        """HOST-ONLY fixture ingress. Never exported as a tool or scope read API."""
        with self._lock:
            if self._closed or not self.enabled or len(self._records) >= 128:
                raise ValueError('capture_disabled_or_full')
            signed = self._authority.capture(evidence)
            cap = self._authority.read_capability(evidence.scope, expires_ms)
            ref = secrets.token_hex(24)
            self._records[ref] = (signed, cap, {})
            return ref

    def review(self, ref, proposal, *, supported, approved, exposure, expires_ms):
        """HOST-ONLY independent-review fixture; not an automatic privacy classifier."""
        with self._lock:
            signed, _, reviews = self._records[ref]
            evidence = signed.evidence
            final = ('刚才看到一句话：'+' / '.join(evidence.text[a:b] for a, b in proposal.spans)
                     if proposal.mode == 'quote' else proposal.text)
            reviews[digest(proposal.__dict__)] = self._reviewer.attest(evidence=evidence,
                proposal=proposal, destination=self._authority.destination, final_text=final,
                policy_version=self._policy.version, supported=supported, approved=approved,
                exposure=exposure, expires_ms=expires_ms)

    def close(self):
        with self._lock:
            self._closed = True
            self._records.clear()

    def audit(self):
        with self._lock:
            return tuple(dict(row) for row in self._audit)

    def _result(self, action, reason, *, mode='', value=None, risk=None, ref=''):
        row = {'preview_id': uuid.uuid4().hex, 'dry_run': True, 'will_send': False,
               'action': action, 'reason': reason, 'mode': mode, 'value': value, 'risk': risk}
        if self.enabled and not self._closed:
            # Session-keyed opaque correlation; no source text, nickname, destination or raw hash.
            tag = hmac.new(self._audit_key, str(ref).encode(), hashlib.sha256).hexdigest()
            self._audit.append({**row, 'source_tag': tag})
            self._audit[:] = self._audit[-256:]
        return row

    def preview(self, raw):
        with self._lock:
            if self._closed:
                return self._result('deny', 'session_retired')
            if not self.enabled:
                return self._result('off', 'default_off')
            try:
                if type(raw) is not str or len(raw.encode()) > 20000:
                    raise ValueError('size')
                def unique(pairs):
                    d = {}
                    for k, v in pairs:
                        if k in d:
                            raise ValueError('duplicate')
                        d[k] = v
                    return d
                request = json.loads(raw, object_pairs_hook=unique)
                if (type(request) is not dict or set(request) != {'source_ref', 'proposal'}
                        or type(request['source_ref']) is not str or len(request['source_ref']) != 48):
                    raise ValueError('schema')
                proposal = parse_proposal(json.dumps(request['proposal'], ensure_ascii=False))
            except (ValueError, TypeError, UnicodeError, RecursionError):
                return self._result('deny', 'invalid_preview_request')
            ref = request['source_ref']
            if ref not in self._records:
                return self._result('deny', 'source_unavailable')
            s, now = self._snapshot, self._now()
            if (type(now) is not int or now <= 0 or s is None or s.observed_ms > now
                    or now-s.observed_ms > 30000):
                return self._result('defer', 'host_snapshot_missing_or_stale', ref=ref)
            if (not all(type(getattr(s, f)) is bool for f in ('ready', 'busy', 'quiet', 'history_verified'))
                    or type(s.route_count) is not int or not isinstance(s.route_id, str) or not s.route_id
                    or s.history_verified is not True):
                return self._result('defer', 'host_state_or_history_unverified', ref=ref)
            signed, capability, reviews = self._records[ref]
            try:
                c, signals = self._authority.compile(signed, capability, proposal, now_ms=now,
                    review=reviews.get(digest(proposal.__dict__)))
                if not self._authority.guard(c, signals, now):
                    raise Rejected('guard')
                state = State(self._authority.bot, self.epoch, self._authority.destination,
                    revision=s.generation, ready=s.ready, route_count=s.route_count,
                    busy=s.busy, quiet=s.quiet, attempts=s.attempts, revoked=s.revoked)
                c = rate(c, state, signals, now_ms=now)
                d = evaluate(c, state, now_ms=now, policy=self._policy)
            except NeedsReview:
                return self._result('review', 'needs_independent_review', ref=ref)
            except (Rejected, ValueError, TypeError, AttributeError):
                return self._result('deny', 'source_or_output_not_authorized', ref=ref)
            # Never return a sending ticket, draft body or permission signature.
            action = {'send': 'allow', 'duplicate': 'skip'}.get(d.action, d.action)
            return self._result(action, d.reason, mode=d.mode, value=d.value, risk=d.risk, ref=ref)

    def tool_handler(self, **args):
        return json.dumps(self.preview(json.dumps(args, ensure_ascii=False)), ensure_ascii=False)


def wire_handlers(handlers, *, owner=None):
    """Candidate host seam: preserve existing handlers byte-for-byte by identity.

    Does not install send hooks or change existing send_private semantics. Production
    tool declaration/launch integration remains separate and is NOT performed here.
    """
    result = dict(handlers)
    if owner is None:
        return result
    if type(owner) is not PreviewOwner:
        raise ValueError('preview_owner_or_name_conflict')
    if not owner.enabled:
        return result
    if 'sharing_preview' in result:
        raise ValueError('preview_owner_or_name_conflict')
    result['sharing_preview'] = owner.tool_handler
    return result
