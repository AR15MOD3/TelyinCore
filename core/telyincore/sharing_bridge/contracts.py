"""Offline source/extraction/disclosure contracts, not an NLP classifier.

Trusted host capture and independent reviewer signing are fixture APIs only.
They MUST NOT be exposed to a model as tools. HMAC authenticates who attested
these exact bytes; it cannot prove an attestation's semantic correctness.
"""
from dataclasses import asdict, dataclass, field
import hashlib
import hmac
import json
import secrets

from .sharing import (Candidate, Exposure, Interest, Permit, Source, Variant,
                     digest, key, level, permission_key, positive, scope)
from .rubric import Signals


class Rejected(ValueError):
    pass


class NeedsReview(ValueError):
    pass


def mac(secret, domain, payload):
    return hmac.new(secret, (domain + ":" + digest(payload)).encode(), hashlib.sha256).hexdigest()


def check(secret, domain, payload, signature):
    return (isinstance(signature, str) and len(signature) == 64
            and all(c in "0123456789abcdef" for c in signature)
            and hmac.compare_digest(mac(secret, domain, payload), signature))


@dataclass(frozen=True)
class SafeSpan:
    start: int
    end: int
    exposure: Exposure


@dataclass(frozen=True)
class MaterialFact:
    start: int
    end: int
    fact_id: str  # host-confirmed semantic fact, not a model-proposed label


@dataclass(frozen=True)
class Evidence:
    bot: str
    epoch: str
    scope: str
    sender: str  # original transport account, not nickname or quoted attribution
    anchor: str
    observed_ms: int
    text: str
    visibility: str  # public does NOT mean merely "a group room"
    classification: str  # ordinary / sensitive / confidential / credential / unknown
    topics: tuple[str, ...] = ()
    related_to: tuple[str, ...] = ()
    reviewed_public_spans: tuple[SafeSpan, ...] = ()
    canonical_event: str = ""  # explicit host relation across anchors, no fuzzy inference
    material_facts: tuple[MaterialFact, ...] = ()


@dataclass(frozen=True)
class SignedEvidence:
    evidence: Evidence
    signature: str


@dataclass(frozen=True)
class ReadCapability:
    bot: str
    epoch: str
    scope: str
    expires_ms: int
    signature: str


@dataclass(frozen=True)
class Proposal:
    spans: tuple[tuple[int, int], ...]
    mode: str
    text: str
    desire: int


def parse_proposal(raw):
    if not isinstance(raw, str) or len(raw.encode()) > 16_384:
        raise Rejected("proposal_size")

    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise Rejected("proposal_duplicate_field")
            result[k] = v
        return result

    try:
        data = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, TypeError, RecursionError) as error:
        raise Rejected("proposal_invalid_json") from error
    if (not isinstance(data, dict) or set(data) != {"spans", "mode", "text", "desire"}
            or data["mode"] not in ("quote", "paraphrase", "feelings")
            or not isinstance(data["text"], str) or len(data["text"].encode()) > 4096
            or not level(data["desire"]) or not isinstance(data["spans"], list)
            or not 1 <= len(data["spans"]) <= 3):
        raise Rejected("proposal_schema")
    spans = []
    for item in data["spans"]:
        if (not isinstance(item, dict) or set(item) != {"start", "end"}
                or type(item["start"]) is not int or type(item["end"]) is not int
                or not 0 <= item["start"] < item["end"]):
            raise Rejected("proposal_span")
        spans.append((item["start"], item["end"]))
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise Rejected("proposal_overlapping_or_unordered_spans")
    return Proposal(tuple(spans), data["mode"], data["text"], data["desire"])


@dataclass(frozen=True)
class Review:
    binding: str
    destination: str
    supported: bool
    approved: bool
    exposure: Exposure
    expires_ms: int
    signature: str


def review_binding(evidence, proposal, destination, final_text, policy_version):
    return digest(["review-input-v1", asdict(evidence), proposal.spans,
                   proposal.mode, destination, final_text, policy_version])


class Reviewer:
    """Separate fixture principal. Does not assert that review was automated."""
    def __init__(self):
        self._secret = secrets.token_bytes(32)

    def attest(self, *, evidence, proposal, destination, final_text, policy_version,
               supported, approved, exposure, expires_ms):
        binding = review_binding(evidence, proposal, destination, final_text, policy_version)
        payload = [binding, destination, supported, approved, asdict(exposure), expires_ms]
        return Review(binding, destination, supported, approved, exposure, expires_ms,
                      mac(self._secret, "independent-output-review", payload))

    def verify(self, review):
        payload = [review.binding, review.destination, review.supported, review.approved,
                   asdict(review.exposure), review.expires_ms]
        return check(self._secret, "independent-output-review", payload, review.signature)


class Authority:
    def __init__(self, bot, epoch, destination, policy, reviewer, preferred_topics=()):
        if not key(bot) or not key(epoch) or not scope(destination) or not destination.startswith("private:"):
            raise Rejected("authority_namespace")
        self.bot, self.epoch, self.destination = bot, epoch, destination
        self.policy, self.reviewer = policy, reviewer
        self.preferred_topics = frozenset(preferred_topics)
        self._capture_key = secrets.token_bytes(32)
        self._output_key = secrets.token_bytes(32)

    def capture(self, evidence):
        """HOST-ONLY ingress fixture. Not a model-facing import/backfill API."""
        self._schema(evidence)
        return SignedEvidence(evidence, mac(self._capture_key, "host-evidence", asdict(evidence)))

    def read_capability(self, source_scope, expires_ms):
        """HOST-ONLY grant for computation in this exact original scope."""
        if not scope(source_scope) or not positive(expires_ms):
            raise Rejected("read_capability_schema")
        payload = [self.bot, self.epoch, source_scope, expires_ms]
        return ReadCapability(*payload, mac(self._capture_key, "origin-read", payload))

    def _schema(self, evidence):
        if ((evidence.bot, evidence.epoch) != (self.bot, self.epoch)
                or not scope(evidence.scope) or not key(evidence.anchor)
                or not scope("private:" + evidence.sender)
                or not evidence.sender.startswith(evidence.scope.split(":", 2)[1] + ":")
                or (evidence.scope.startswith("private:") and evidence.scope != "private:" + evidence.sender)
                or not positive(evidence.observed_ms) or not isinstance(evidence.text, str)
                or not 1 <= len(evidence.text.encode()) <= 32_768
                or evidence.visibility not in ("public", "group", "private", "unknown")
                or evidence.classification not in ("ordinary", "sensitive", "confidential", "credential", "unknown")
                or len(evidence.topics) > 32 or not all(key(t) for t in evidence.topics)
                or len(evidence.related_to) > 16 or not all(scope(s) for s in evidence.related_to)
                or len(evidence.reviewed_public_spans) > 16
                or (evidence.canonical_event and not key(evidence.canonical_event))
                or len(evidence.material_facts) > 64):
            raise Rejected("evidence_schema_or_identity")
        pairs = [(span.start, span.end) for span in evidence.reviewed_public_spans]
        if len(pairs) != len(set(pairs)):
            raise Rejected("ambiguous_public_span_review")
        for span in evidence.reviewed_public_spans:
            if (type(span.start) is not int or type(span.end) is not int
                    or not 0 <= span.start < span.end <= len(evidence.text)):
                raise Rejected("evidence_safe_span")
            span.exposure.score()
        mapped = set()
        for fact in evidence.material_facts:
            pair = (fact.start, fact.end)
            if (type(fact.start) is not int or type(fact.end) is not int
                    or not 0 <= fact.start < fact.end <= len(evidence.text)
                    or not key(fact.fact_id) or pair in mapped):
                raise Rejected("ambiguous_or_invalid_material_fact")
            mapped.add(pair)

    def compile(self, signed, capability, proposal, *, now_ms, review=None):
        e = signed.evidence
        self._schema(e)
        if not positive(now_ms) or not check(self._capture_key, "host-evidence", asdict(e), signed.signature):
            raise Rejected("untrusted_evidence")
        cp = [capability.bot, capability.epoch, capability.scope, capability.expires_ms]
        if (not check(self._capture_key, "origin-read", cp, capability.signature)
                or (capability.bot, capability.epoch, capability.scope) != (self.bot, self.epoch, e.scope)
                or not positive(capability.expires_ms) or now_ms >= capability.expires_ms):
            raise Rejected("origin_access_not_authorized")
        if not e.observed_ms <= now_ms < e.observed_ms + self.policy.max_age_ms:
            raise Rejected("evidence_stale_or_future")
        if e.classification == "credential":
            raise Rejected("credential_not_shareable")
        if e.classification == "unknown":
            raise NeedsReview("source_classification_unknown")
        # Reparse even direct dataclass proposals so bypassing JSON cannot widen schema.
        proposal = parse_proposal(json.dumps({"spans": [{"start": a, "end": b} for a, b in proposal.spans],
            "mode": proposal.mode, "text": proposal.text, "desire": proposal.desire}))
        if any(end > len(e.text) for start, end in proposal.spans):
            raise Rejected("span_outside_original_source")
        snippets = [e.text[a:b] for a, b in proposal.spans]
        if proposal.mode == "quote":
            expected = "刚才看到一句话：" + " / ".join(snippets)
            if proposal.text and proposal.text != expected:
                raise Rejected("quote_cannot_append_unproven_details")
            final_text = expected
        else:
            final_text = proposal.text
        if not final_text.strip() or " ".join(final_text.split()) != final_text:
            raise NeedsReview("review_final_outbox_rendering")
        if len(final_text.encode()) > self.policy.max_text_bytes:
            raise Rejected("final_output_size")
        mode = {"quote": "normal", "paraphrase": "minimized", "feelings": "feelings"}[proposal.mode]
        if e.classification == "confidential" and mode != "feelings":
            raise NeedsReview("confidential_source_only_non_disclosing_feelings")
        approved = {(s.start, s.end): s for s in e.reviewed_public_spans}
        auto = (e.visibility == "public" and e.classification == "ordinary"
                and proposal.mode == "quote" and all(pair in approved for pair in proposal.spans))
        expires = min(capability.expires_ms, e.observed_ms + self.policy.max_age_ms)
        if auto:
            exposure = Exposure(*(max(getattr(approved[p].exposure, field) for p in proposal.spans)
                                  for field in ("sensitivity", "identifiability", "expectation_violation")))
        else:
            if review is None:
                raise NeedsReview("exact_final_output_review_required")
            expected = review_binding(e, proposal, self.destination, final_text, self.policy.version)
            if (not self.reviewer.verify(review) or review.binding != expected
                    or review.destination != self.destination
                    or review.supported is not True or review.approved is not True
                    or not positive(review.expires_ms) or review.expires_ms <= now_ms):
                raise Rejected("output_review_invalid_or_not_supported")
            exposure = review.exposure
            expires = min(expires, review.expires_ms)
        if exposure.score() > self.policy.risk_ceiling:
            raise NeedsReview("output_still_exposes_too_much")
        event = "evt-" + digest([self.bot, "host-canonical", e.canonical_event]
            if e.canonical_event else [self.bot, e.scope, e.anchor])
        mapping = {(f.start, f.end): f.fact_id for f in e.material_facts}
        # Offsets prove a quotation, NOT semantic novelty. Without a host-confirmed
        # fact mapping, treat it as one event-level fact; cutting/rephrasing the
        # same source cannot manufacture fresh "material progress".
        facts = tuple(sorted({"fact-" + digest([event, mapping.get(pair, "unclassified-event")])
                              for pair in proposal.spans}))
        source = Source(self.bot, self.epoch, event, e.scope,
                        (e.anchor, "ev-" + digest(asdict(e))), facts, e.observed_ms,
                        restriction="confidential" if e.classification == "confidential" else "ordinary")
        variant = Variant(mode, final_text, () if mode == "feelings" else facts, exposure)
        signals = Signals(frozenset(e.topics), self.preferred_topics,
                          self.destination in e.related_to, proposal.desire)
        binding = permission_key(source, self.destination, variant, self.policy.version)
        permit = Permit(binding, self._seal(binding, expires, signals), expires)
        return Candidate(source, self.destination, Interest(0, 0, 0, 0), (variant,), (permit,)), signals

    def _seal(self, binding, expires_ms, signals):
        payload = [binding, expires_ms, sorted(signals.topics), sorted(signals.preferred_topics),
                   signals.directly_related, signals.desire]
        return "sealed-" + mac(self._output_key, "queue-disclosure", payload)

    def guard(self, candidate, signals, now_ms):
        """Revalidate BOTH admission and dequeue. Computed Interest is not authority."""
        if ((candidate.source.bot, candidate.source.epoch, candidate.destination)
                != (self.bot, self.epoch, self.destination) or not candidate.variants):
            return False
        for variant in candidate.variants:
            binding = permission_key(candidate.source, candidate.destination, variant, self.policy.version)
            matches = [p for p in candidate.permits if p.binding == binding]
            if len(matches) != 1:
                return False
            permit = matches[0]
            if (not positive(permit.expires_ms) or permit.expires_ms <= now_ms
                    or not isinstance(permit.reviewer, str)
                    or not hmac.compare_digest(permit.reviewer.encode(),
                                              self._seal(binding, permit.expires_ms, signals).encode())):
                return False
        return True


def guarded_pipeline(store, authority):
    """Retired prototype entry; no Pipeline implementation ships with Core.

    Production consumers use the explicitly assembled HostBridge. Never load
    an unrelated top-level ``pipeline`` package from the process search path.
    """
    raise RuntimeError('retired prototype pipeline unavailable; use the host-assembled sharing bridge')
