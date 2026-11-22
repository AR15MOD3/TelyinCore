"""Offline prototype, not wired to send_private or any provider.

Host-originated sources, disclosure reviews, event/fact IDs and delivery results
are trust contracts, NOT authenticated merely by constructing these dataclasses.
Never expose these constructors/permit minting as model-facing tools. Scores do
not grant access to source histories, nor permission to disclose their contents.
"""
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import re
import unicodedata


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def key(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[a-zA-Z0-9_.:-]{1,160}", value))


def scope(value):
    return isinstance(value, str) and bool(re.fullmatch(
        r"(?:private|group):[a-z][a-z0-9_-]{1,31}:[a-zA-Z0-9_.-]{1,80}", value))


def positive(value):
    return type(value) is int and value > 0


def level(value):
    return type(value) is int and 0 <= value <= 4


def text_id(text):
    # Supplemental literal dedup, NOT a semantic event classifier or anonymizer.
    return digest(" ".join(unicodedata.normalize("NFKC", text).split()).casefold())


@dataclass(frozen=True)
class Interest:
    relevance: int
    desire: int
    novelty: int
    timing: int

    def score(self):
        values = (self.relevance, self.desire, self.novelty, self.timing)
        if not all(level(v) for v in values):
            raise ValueError("invalid_interest")
        return sum(v * w for v, w in zip(values, (35, 25, 25, 15))) / 4


@dataclass(frozen=True)
class Exposure:
    sensitivity: int
    identifiability: int
    expectation_violation: int

    def score(self):
        values = (self.sensitivity, self.identifiability, self.expectation_violation)
        if not all(level(v) for v in values):
            raise ValueError("invalid_exposure")
        # A low score elsewhere cannot cancel one high-risk dimension.
        return max(values) * 25


@dataclass(frozen=True)
class Source:
    bot: str
    epoch: str
    event: str  # host canonical event ID; cross-room equivalence needs host proof
    scope: str
    anchors: tuple[str, ...]
    facts: tuple[str, ...]  # verified material fact IDs, not model synonyms
    observed_ms: int
    authorized: bool = True
    reliable: bool = True
    restriction: str = "ordinary"  # ordinary / confidential / credential


@dataclass(frozen=True)
class Variant:
    mode: str  # normal / minimized / feelings
    text: str
    facts: tuple[str, ...]
    exposure: Exposure  # exact-output review, never reuse pre-redaction labels


@dataclass(frozen=True)
class Permit:
    binding: str
    reviewer: str
    expires_ms: int


@dataclass(frozen=True)
class Candidate:
    source: Source
    destination: str
    interest: Interest
    variants: tuple[Variant, ...]
    permits: tuple[Permit, ...]


@dataclass(frozen=True)
class Attempt:
    bot: str
    destination: str
    event: str
    attempt_id: str
    text_hash: str
    facts: tuple[str, ...]
    at_ms: int
    status: str  # pending / accepted / unknown / failed
    receipt_id: str = ""


@dataclass(frozen=True)
class State:
    bot: str
    epoch: str
    destination: str
    revision: int = 0
    ready: bool = True  # unavailable ledger/revocation feed => False, NOT empty
    route_count: int = 1
    busy: bool = False
    quiet: bool = False
    attempts: tuple[Attempt, ...] = ()
    revoked: frozenset[str] = frozenset()  # source event IDs or anchor IDs


@dataclass(frozen=True)
class Policy:
    enabled: bool = False
    version: str = "sharing-offline-v1"
    send_score: int = 65
    topic_score: int = 35
    risk_ceiling: int = 25
    quiet_extra: int = 20  # lower frequency, not an unconditional night ban
    max_age_ms: int = 21_600_000  # six hours, NOT continuity's proposed five minutes
    cooldown_ms: int = 1_800_000
    retry_cooldown_ms: int = 300_000
    max_per_24h: int = 3
    max_text_bytes: int = 2048

    def validate(self):
        bounded = ((self.send_score, 0, 100), (self.topic_score, 0, self.send_score),
                   (self.risk_ceiling, 0, 50), (self.quiet_extra, 0, 35),
                   (self.max_age_ms, 1, 86_400_000), (self.cooldown_ms, 1, 86_400_000),
                   (self.retry_cooldown_ms, 1, 86_400_000), (self.max_per_24h, 1, 20),
                   (self.max_text_bytes, 1, 8192))
        if (type(self.enabled) is not bool or not key(self.version)
                or any(type(v) is not int or not lo <= v <= hi for v, lo, hi in bounded)):
            raise ValueError("invalid_policy")


def permission_key(source, destination, variant, version):
    # Exact final body + fact IDs + exposure review + audience + source binding.
    return digest(["sharing-disclosure-v1", asdict(source), destination,
                   asdict(variant), version])


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    value: float
    risk: int | None
    mode: str = ""
    text: str = ""
    facts: tuple[str, ...] = ()
    ticket: str = ""


def evaluate(candidate: Candidate, state: State, *, now_ms: int,
             policy: Policy = Policy()) -> Decision:
    policy.validate()
    s = candidate.source
    if (not positive(now_ms) or type(state.revision) is not int or state.revision < 0
            or len(state.attempts) > 4096 or len(state.revoked) > 4096
            or not 1 <= len(candidate.variants) <= 4 or len(candidate.permits) > 16):
        raise ValueError("invalid_snapshot_or_size")
    value = candidate.interest.score()

    def result(action, reason, variant=None):
        exposed = variant.exposure.score() if variant else None
        # Non-send decisions never carry body, even when topic-worthy.
        body = variant.text if action == "send" else ""
        facts = variant.facts if action == "send" else ()
        mode = variant.mode if variant else ""
        ticket = digest(["sharing-ticket-v1", asdict(candidate), asdict(policy),
                         state.bot, state.epoch, state.destination, state.revision,
                         action, reason, body, facts])
        return Decision(action, reason, value, exposed, mode, body, facts, ticket)

    if not policy.enabled:
        return result("off", "default_off")
    if not (all(key(v) for v in (s.bot, s.epoch, s.event)) and scope(s.scope)
            and scope(candidate.destination) and candidate.destination.startswith("private:")
            and s.bot == state.bot and s.epoch == state.epoch
            and candidate.destination == state.destination
            and s.authorized is True and s.reliable is True
            and 1 <= len(s.anchors) <= 16 and all(key(v) for v in s.anchors)
            and len(s.anchors) == len(set(s.anchors))
            and len(s.facts) <= 64 and all(key(v) for v in s.facts)
            and len(s.facts) == len(set(s.facts))
            and s.restriction in ("ordinary", "confidential", "credential")):
        return result("deny", "source_or_audience_unproven")
    if state.ready is not True or type(state.route_count) is not int or state.route_count != 1:
        return result("defer", "state_or_route_unavailable")
    if ({s.event, *s.anchors} & state.revoked):
        return result("deny", "source_revoked")
    if not positive(s.observed_ms) or not s.observed_ms <= now_ms < s.observed_ms + policy.max_age_ms:
        return result("defer", "stale_or_future_source")
    if s.restriction == "credential":
        return result("deny", "credential_not_a_share_topic")

    # Prefer original expression if safe; otherwise choose reviewed minimized or
    # feelings-only alternatives. NEVER auto-delete names and assume safety.
    viable = []
    for v in candidate.variants:
        risk = v.exposure.score()
        if (v.mode not in ("normal", "minimized", "feelings") or not isinstance(v.text, str)
                or not v.text.strip() or len(v.text.encode()) > policy.max_text_bytes
                or len(v.facts) != len(set(v.facts)) or not set(v.facts) <= set(s.facts)
                or (v.mode == "feelings" and v.facts)
                or (s.restriction == "confidential" and v.mode != "feelings")
                or risk > policy.risk_ceiling):
            continue
        permit_id = permission_key(s, candidate.destination, v, policy.version)
        permits = [p for p in candidate.permits if p.binding == permit_id]
        if (len(permits) != 1 or not key(permits[0].reviewer)
                or not positive(permits[0].expires_ms) or permits[0].expires_ms <= now_ms):
            continue
        viable.append(v)
    if not viable:
        return result("defer", "need_safe_expression_or_review")
    viable.sort(key=lambda v: (("normal", "minimized", "feelings").index(v.mode),
                              v.exposure.score()))
    v = viable[0]
    history = [a for a in state.attempts if a.bot == s.bot and a.destination == candidate.destination]
    # Missing/corrupt ledger does not mean "never sent". Only trusted structured
    # acceptance counts as sent; unknown delivery does not authorize blind retries.
    if any(a.status not in ("pending", "accepted", "unknown", "failed")
           or not positive(a.at_ms) or a.at_ms > now_ms
           or not key(a.attempt_id) or not key(a.event)
           or not isinstance(a.text_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", a.text_hash)
           or len(a.facts) > 64 or len(a.facts) != len(set(a.facts))
           or not all(key(f) for f in a.facts)
           or (a.status == "accepted" and not key(a.receipt_id))
           or (a.status != "accepted" and a.receipt_id) for a in history):
        return result("defer", "ledger_invalid")
    if len({a.attempt_id for a in history}) != len(history):
        return result("defer", "ledger_conflict")
    matching = [a for a in history if a.event == s.event or a.text_hash == text_id(v.text)]
    if any(a.status in ("pending", "unknown") for a in matching):
        return result("defer", "delivery_pending_or_unknown", v)
    accepted = [a for a in matching if a.status == "accepted"]
    known_facts = {fact for a in accepted if a.event == s.event for fact in a.facts}
    if (any(a.text_hash == text_id(v.text) for a in accepted)
            or (accepted and set(v.facts) <= known_facts)):
        return result("duplicate", "same_event_no_material_update", v)
    if value < policy.topic_score:
        return result("skip", "low_share_value", v)
    if value < min(100, policy.send_score + (policy.quiet_extra if state.quiet else 0)):
        return result("topic", "save_for_natural_conversation", v)
    if state.busy:
        return result("defer", "recipient_busy", v)
    recent = [a for a in history if a.status != "failed" and now_ms - a.at_ms < 86_400_000]
    if len(recent) >= policy.max_per_24h:
        return result("defer", "rolling_budget", v)
    if any(now_ms - a.at_ms < (policy.retry_cooldown_ms if a.status == "failed"
                              else policy.cooldown_ms) for a in history):
        return result("defer", "cooldown", v)
    return result("send", "reviewed_and_worth_sharing", v)


def reserve(candidate, state, decision, *, now_ms, attempt_id, policy):
    """Pure state transition. Production must persist+CAS this under dispatch lock.

    Call before ANY outbox/tell/provider/plugin IO. This function performs no IO.
    """
    fresh = evaluate(candidate, state, now_ms=now_ms, policy=policy)
    if fresh != decision or fresh.action != "send":
        raise ValueError("stale_or_non_send_decision")
    if not key(attempt_id) or any(a.attempt_id == attempt_id for a in state.attempts):
        raise ValueError("invalid_attempt_id")
    attempt = Attempt(state.bot, state.destination, candidate.source.event, attempt_id,
                      text_id(fresh.text), fresh.facts, now_ms, "pending")
    return replace(state, revision=state.revision + 1, attempts=(*state.attempts, attempt))


def settle(state, *, attempt_id, outcome, receipt_id=""):
    """Host-authenticated, exact-attempt result, never a parsed outbox string.

    No automatic retry. A truly pending attempt may later be reconciled from an
    unknown outcome using a trustworthy delivery query; this prototype does not
    perform that query. Keep ambiguous attempts blocked until reconciled.
    """
    matches = [a for a in state.attempts if a.attempt_id == attempt_id]
    if len(matches) != 1 or outcome not in ("accepted", "failed", "unknown"):
        raise ValueError("invalid_delivery_result")
    old = matches[0]
    if old.bot != state.bot or old.destination != state.destination:
        raise ValueError("delivery_namespace_mismatch")
    if (outcome == "accepted" and not key(receipt_id)) or (outcome != "accepted" and receipt_id):
        raise ValueError("invalid_receipt")
    updated = replace(old, status=outcome, receipt_id=receipt_id)
    if old.status != "pending":
        if old == updated:
            return state  # exact result repeated is idempotent
        raise ValueError("delivery_result_conflict")
    return replace(state, revision=state.revision + 1,
                   attempts=tuple(updated if a.attempt_id == attempt_id else a for a in state.attempts))
