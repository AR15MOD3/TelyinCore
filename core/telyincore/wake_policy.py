"""Separate cheap checks, model admission, and speech attempts.

Cooperating reservations serialize the entire read/decide/write transaction.
Durable reservations precede side effects; failure
never refunds an uncertain send. A host-issued in-memory ticket, NOT message
text/metadata, identifies an autonomous batch. External replies are unaffected.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import os
import tempfile
import time
from pathlib import Path
import uuid
from functools import wraps
from . import snapshot_json

FILE = "wake-policy-v2.json"
KINDS = ("heartbeat", "follow_up", "timer", "user_test", "user_reminder", "continuation")
DAY_LIMIT = {"follow_up": 4, "timer": 4, "continuation": 24}
AUTO_SPEECH_DAY = 6
AUTO_SPEECH_NIGHT = 1
AUTO_SPEECH_GAP_MS = 60 * 60 * 1000
MAX_CALLS_PER_WAKE = 4

_RAISE = object()


def _read_snapshot(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


class _BudgetSnapshot(dict):
    """Carry publication preimages with the decision, never in persisted JSON."""
    def __init__(self, data, sources):
        super().__init__(data)
        self.sources = sources

    def verify(self):
        for path, raw in self.sources.items():
            if _read_snapshot(path) != raw:
                raise ValueError('wake policy source changed before publication')


def _decode_snapshot(raw):
    try:
        return snapshot_json.loads(raw.decode('utf-8'))
    except RecursionError as error:
        raise ValueError('wake policy snapshot nesting exceeded') from error


def _transaction(*, unavailable=_RAISE, autonomous=False):
    def decorate(function):
        @wraps(function)
        def call(self, *args, **kwargs):
            # Human replies never depend on the autonomous ledger being writable.
            if autonomous:
                batch = args[0] if args else kwargs['batch']
                if self._batch_grant(batch, kwargs['now_ms'], kwargs.get('identity_bridge')) is None:
                    return function(self, *args, **kwargs)
            from .source_guard import lease
            try:
                with lease(self.path):
                    return function(self, *args, **kwargs)
            except OSError:
                if unavailable is _RAISE:
                    raise
                return unavailable
        return call
    return decorate


def _observed(stage):
    def decorate(function):
        @wraps(function)
        def call(self, batch, *, now_ms, **kwargs):
            result = function(self, batch, now_ms=now_ms, **kwargs)
            self.note_delivery(batch, now_ms=now_ms, stage=stage,
                detail={'allowed': result[0], 'reason': result[1],
                        'phase': kwargs.get('phase', '')},
                identity_bridge=kwargs.get('identity_bridge'))
            return result
        return call
    return decorate


class WakeModelAdmissionDenied(RuntimeError):
    """A policy refusal is terminal for this batch, not a provider retry."""
    def __init__(self, reason):
        allowed = {'untrusted_or_expired_wake', 'wake_budget_unavailable'}
        self.reason = reason if reason in allowed else 'unknown_wake_denial'
        self.public_code = 'wake_model_admission_' + self.reason
        super().__init__(self.public_code)


class WakeModelBudgetExhausted(Exception):
    """An admitted wake has consumed its model requests."""


def _day(ms):
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def _night_key(ms):
    at = datetime.fromtimestamp(ms / 1000)
    if at.hour < 8:
        at -= timedelta(days=1)
    return at.strftime("%Y-%m-%d")


def _number(mapping, key):
    if not isinstance(mapping, dict):
        raise ValueError("invalid budget record")
    value = mapping.get(key, 0)
    if type(value) is not int or value < 0:
        raise ValueError("invalid budget counter")
    return value


class WakePolicy:
    def __init__(self, root, *, is_night):
        self.root = Path(root)
        self.path = self.root / FILE
        self.is_night = is_night
        self._grants = {}
        self._events = {}
        self.observer = None

    def note_delivery(self, batch, *, now_ms, stage, detail, identity_bridge=None):
        if self.observer is None and stage != "console_output_emitted":
            return
        grant = self._batch_grant(batch, now_ms, identity_bridge)
        if isinstance(grant, dict) and stage == "console_output_emitted":
            grant["output_emitted"] = True
        if self.observer is None:
            return
        if isinstance(grant, dict):
            self.observer(grant['timer_id'], now_ms, stage, detail)

    def previous_processing(self, issued_ms, *, now_ms):
        if issued_ms is None:
            return "unknown"
        try:
            row = self._load(now_ms).get("last_wake_processing")
            if isinstance(row, dict) and row.get("at") == issued_ms:
                state = row.get("state")
                if state in ("pending", "failed", "processed", "spoke", "interrupted"):
                    return state
        except (OSError, ValueError, TypeError):
            pass
        return "unknown"

    def finish_processing(self, batch, *, now_ms, state):
        if len(batch) != 1 or state not in ("failed", "processed", "interrupted"):
            return
        bound = self._events.get(id(batch[0]))
        if bound is None or bound[0] is not batch[0]:
            return
        grant = self._grants.get(bound[1])
        if grant is not None and grant["kind"] != "continuation":
            self._save_processing(grant, now_ms=now_ms, state=state)

    @_transaction()
    def _save_processing(self, grant, *, now_ms, state):
        book = self._load(now_ms)
        row = book.get("last_wake_processing")
        if (isinstance(row, dict) and row.get("at") == grant["issued_ms"]
                and row.get("timer") == grant["timer_id"] and row.get("state") == "pending"):
            row["state"] = "spoke" if state == "processed" and grant.get("output_emitted") else state
            self._save(book)

    def _load(self, now_ms):
        raw = _read_snapshot(self.path)
        exists = raw is not None
        book = _decode_snapshot(raw) if exists else {}
        required = {"version", "day", "lanes", "last_models", "attempts", "approvals",
                    "observed_ms", "speech_day", "speech_night", "night_key"}
        if (not isinstance(book, dict) or (exists and
                (book.get("version") != 2 or not required <= book.keys()))):
            raise ValueError("invalid wake policy ledger")
        book = _BudgetSnapshot(book, {self.path: raw})
        if exists:
            for key in ("day", "night_key"):
                datetime.strptime(book[key], "%Y-%m-%d")
            for key in ("observed_ms", "speech_day", "speech_night", "legacy_speech_reserve",
                        "legacy_night_reserve", "last_speech_ms", "last_reminder_speech_ms"):
                _number(book, key)
        today = _day(now_ms)
        if book.get("day", today) > today:
            raise ValueError("clock moved backwards")
        if _number(book, "observed_ms") > now_ms:
            raise ValueError("clock moved backwards")
        if book.get("day") != today:
            book["lanes"] = {}
            book["speech_day"] = 0
            book["legacy_speech_reserve"] = 0
        book.update(version=2, day=today)
        for key in ("lanes", "last_models", "attempts", "approvals"):
            if not isinstance(book.setdefault(key, {}), dict):
                raise ValueError("invalid wake ledger section")
        if any(not isinstance(v, dict) for v in book["lanes"].values()):
            raise ValueError("invalid lane")
        if any(type(v) is not int or v < 0 for v in book["last_models"].values()):
            raise ValueError("invalid cooldown")
        if any(type(v) is not int or v < 0 for v in book["attempts"].values()):
            raise ValueError("invalid attempt ledger")
        # Legacy aggregate totals cannot identify a lane. Only migrate the
        # last successful wake into heartbeat cooldown; never treat all six
        # legacy wakes as follow-ups or outbound sends, and never edit it here.
        if not exists:
            legacy = self.root / "\u5524\u9192.json"
            legacy_raw = _read_snapshot(legacy)
            book.sources[legacy] = legacy_raw
            previous = _decode_snapshot(legacy_raw) if legacy_raw is not None else None
            if legacy_raw is not None:
                stamp = _number(previous, "last_ok_ms")
                if stamp:
                    book["last_models"]["heartbeat"] = stamp
                    book["last_speech_ms"] = stamp
                if previous.get("day") == today:
                    # Unknown historical delivery is NOT reported as a send.
                    # Conservatively reserve old admissions against speech only
                    # for the migration day; model lanes remain independent.
                    book["legacy_speech_reserve"] = min(
                        _number(previous, "count"), AUTO_SPEECH_DAY)
        if book.get("night_key") != _night_key(now_ms):
            book["night_key"] = _night_key(now_ms)
            book["speech_night"] = 0
            book["legacy_night_reserve"] = 0
        if not exists and legacy_raw is not None:
            if previous.get("day") == today and self.is_night(
                    datetime.fromtimestamp(now_ms / 1000)):
                book["legacy_night_reserve"] = min(
                    _number(previous, "night"), AUTO_SPEECH_NIGHT)
        for key in ("recent_explicit_models", "recent_reminder_speech"):
            rows = book.setdefault(key, [])
            if not isinstance(rows, list) or any(type(v) is not int or v < 0 for v in rows):
                raise ValueError("invalid rolling budget")
            book[key] = [v for v in rows if v > now_ms - 3600000]
        book["attempts"] = {k: v for k, v in book["attempts"].items()
                            if type(v) is int and v > now_ms - 7 * 86400000}
        book["approvals"] = {k: v for k, v in book["approvals"].items()
                             if isinstance(v, dict) and v.get("expires_ms", 0) >= now_ms}
        book["observed_ms"] = now_ms
        book.verify()
        return book

    def _save(self, book):
        if not isinstance(book, _BudgetSnapshot) or self.path not in book.sources:
            raise ValueError('wake policy publication requires its loaded snapshot')
        raw = (snapshot_json.dumps(book, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        book.verify()
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix='.' + self.path.name + '.tmp-', dir=self.root)
        tmp = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(4):
                book.verify()
                try:
                    if book.sources[self.path] is None:
                        os.link(tmp, self.path)  # Never replace a newly appeared authority.
                    else:
                        os.replace(tmp, self.path)
                    break
                except PermissionError as error:
                    # Windows readers can briefly deny rename/delete sharing.
                    # Retry only known native denials, with the source lease and
                    # exact preimage rechecked each time. Never retry a changed or
                    # possibly already-published reservation, nor a sync failure.
                    if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 3:
                        raise
                    time.sleep(.01 * (2 ** attempt))
            if os.name != 'nt':
                directory = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass  # Preserve the original publication failure.

    @_transaction()
    def approve(self, timer_id, at, intent, *, now_ms):
        """Only the local human-control API may call this; payload cannot opt in.

        Consent is bound to exact ID+time and expires five minutes after due.
        Save before moving the timer: a failed timer write leaves inert consent,
        never an unguarded timer; both records must match to have any effect.
        """
        if intent not in ("user_test", "user_reminder"):
            raise ValueError("invalid explicit intent")
        book = self._load(now_ms)
        if len(book["approvals"]) >= 128 and timer_id not in book["approvals"]:
            raise ValueError("approval queue full")
        deadline = int(datetime.fromisoformat(at).timestamp() * 1000)
        book["approvals"][timer_id] = {
            "at": at, "intent": intent, "expires_ms": deadline + 300000}
        self._save(book)

    def kind(self, alarm, *, now_ms):
        payload = getattr(alarm, "payload", None)
        payload = payload if isinstance(payload, dict) else {}
        kind = ("follow_up" if payload.get("follow_up") else
                "heartbeat" if payload.get("heartbeat") else "timer")
        book = self._load(now_ms)
        approval = book["approvals"].get(getattr(alarm, "id", ""), {})
        if (kind != "follow_up" and approval.get("at") == getattr(alarm, "at", None)
                and approval.get("intent") in ("user_test", "user_reminder")):
            kind = approval["intent"]
        return kind

    def _model_reason(self, book, kind, now_ms):
        if kind not in KINDS:
            return "unknown_wake_kind"
        night = self.is_night(datetime.fromtimestamp(now_ms / 1000))
        lane = book["lanes"].get(kind, {})
        if kind in DAY_LIMIT and _number(lane, "admitted") >= DAY_LIMIT[kind]:
            return kind + "_model_daily_budget"
        if night and kind in ("follow_up", "timer"):
            return kind + "_night_deferred"
        gap = (120 if night else 45) * 60000 if kind == "heartbeat" else 15 * 60000
        if kind in ("user_test", "user_reminder"):
            if len(book["recent_explicit_models"]) >= 12:
                return "explicit_model_hourly_budget"
            gap = 5000  # Local explicit commands cannot create an unbounded burst.
        if kind == "continuation":
            gap = 0  # One ticket per host job/source; daily and per-ticket bounds still apply.
        last = _number(book["last_models"], kind)
        if last and now_ms - last < gap:
            return kind + "_model_cooldown"
        return ""

    def preview(self, kind, *, now_ms):
        try:
            reason = self._model_reason(self._load(now_ms), kind, now_ms)
            return not reason, reason or "model_lane_available"
        except (OSError, ValueError, TypeError, OverflowError):
            return False, "wake_budget_unavailable"

    @_transaction(unavailable=None)
    def record(self, kind, *, now_ms, allowed, reason, timer_id=""):
        """Reserve an admission or record a cheap denial. No model is called."""
        try:
            book = self._load(now_ms)
            if kind not in KINDS:
                return None
            if allowed:
                reason = self._model_reason(book, kind, now_ms)
                if reason:
                    allowed = False
                if timer_id in book["attempts"]:
                    return None
                if len(book["attempts"]) >= 10000:
                    return None
            lane = book["lanes"].setdefault(kind, {})
            lane["checks"] = _number(lane, "checks") + 1
            key = "admitted" if allowed else "denied"
            lane[key] = _number(lane, key) + 1
            lane.update(last_reason=reason, last_check_ms=now_ms)
            if allowed:
                book["attempts"][timer_id] = now_ms
                book["last_models"][kind] = now_ms
                if kind != "continuation":
                    book["last_wake_processing"] = {"at": now_ms, "timer": timer_id, "state": "pending"}
                book["approvals"].pop(timer_id, None)
                if kind in ("user_test", "user_reminder"):
                    book["recent_explicit_models"].append(now_ms)
            self._save(book)
            if not allowed:
                return None
            self._prune_grants(now_ms)
            if len(self._grants) >= 256:
                return None  # Reserved, but cannot enqueue safely; never refund.
            ticket = uuid.uuid4().hex
            if ticket in self._grants:
                return None  # Preserve existing bound authority; do not refund reservation.
            self._grants[ticket] = {"kind": kind, "timer_id": timer_id,
                                    "issued_ms": now_ms, "calls": 0, "speech": None,
                                    "tapped": False}
            return ticket
        except (OSError, ValueError, TypeError, OverflowError):
            return None

    def _prune_grants(self, now_ms):
        self._grants = {k: v for k, v in self._grants.items()
                        if 0 <= now_ms - v["issued_ms"] < 6 * 3600000}
        self._events = {k: v for k, v in self._events.items() if v[1] in self._grants}

    def bind(self, event, ticket):
        if ticket not in self._grants:
            raise ValueError("unissued wake ticket")
        if any(v[1] == ticket for v in self._events.values()):
            raise ValueError("wake ticket already bound")
        self._events[id(event)] = (event, ticket)  # retain identity; no id reuse

    def bind_continuation(self, event, *, now_ms, valid, pending_valid=lambda: True):
        from .continuation import is_continuation
        from .channel_admission import BatchIdentityBridge
        if not is_continuation(event) or not valid(now_ms) or not pending_valid():
            return False
        ticket = self.record('continuation', now_ms=now_ms, allowed=True,
                             reason='host_continuation', timer_id=uuid.uuid4().hex)
        if ticket is None:
            return False
        self.bind(event, ticket)
        grant = self._grants[ticket]
        grant['valid'] = valid
        grant['pending_valid'] = pending_valid
        grant['seal'] = BatchIdentityBridge._fingerprint((event,))
        return True

    def claim_continuation(self, event, lease, *, now_ms):
        if not self.continuation_valid(event, now_ms=now_ms):
            raise ValueError('continuation expired or unissued')
        grant = self._grants[self._events[id(event)][1]]
        if grant.get('consumer') is not None and grant['consumer'] is not lease:
            raise ValueError('continuation already consumed')
        grant['consumer'] = lease

    def continuation_valid(self, event, *, now_ms):
        from .continuation import is_continuation
        from .channel_admission import BatchIdentityBridge
        self._prune_grants(now_ms)
        entry = self._events.get(id(event))
        if not entry or entry[0] is not event or not is_continuation(event):
            return False
        grant = self._grants[entry[1]]
        return (grant['kind'] == 'continuation' and grant['valid'](now_ms)
                and (grant.get('consumer') is not None or grant['pending_valid']())
                and grant['seal'] == BatchIdentityBridge._fingerprint((event,)))

    def permits_self_observation(self, batch, *, now_ms, identity_bridge=None):
        """An already bound wake may use its own concerns, not human scopes.

        This does not reserve a model call or speech allowance. Normal dispatch
        still performs those admissions. Copied event metadata grants nothing.
        """
        grant = self._batch_grant(batch, now_ms, identity_bridge)
        return isinstance(grant, dict)

    def _batch_grant(self, batch, now_ms, identity_bridge=None):
        if identity_bridge is not None:
            from .channel_admission import BatchIdentityBridge
            if type(identity_bridge) is not BatchIdentityBridge:
                return False
            try:
                batch = identity_bridge.resolve(batch)
            except (ValueError, TypeError):
                return False
        self._prune_grants(now_ms)
        if not batch or all(getattr(e, "origin", None) == "external" for e in batch):
            return None  # ordinary human replies do not consume autonomous quotas
        if len(batch) != 1:
            return False
        event = batch[0]
        from .continuation import is_continuation
        if is_continuation(event):
            if not self.continuation_valid(event, now_ms=now_ms):
                return False
            grant = self._grants[self._events[id(event)][1]]
            if grant.get('consumer') is not identity_bridge or identity_bridge is None:
                return False
            return grant
        entry = self._events.get(id(event))
        if (not entry or entry[0] is not event or
                getattr(event, "origin", None) != "internal" or
                getattr(event, "type", None) != "tick" or
                getattr(event, "source", None) != "heartbeat" or
                getattr(event, "sender_key", None) is not None):
            return False
        return self._grants[entry[1]]

    def model_remaining(self, batch, *, now_ms, identity_bridge=None):
        """Read the live host ticket without spending or enlarging its quota.

        None is ordinary interaction; False is invalid authority. Only an exact
        integer grants a bounded number of remaining autonomous requests.
        """
        grant = self._batch_grant(batch, now_ms, identity_bridge)
        if grant is None: return None
        if grant is False: return False
        calls = grant.get('calls')
        if type(calls) is not int or calls < 0: return False
        return max(0, MAX_CALLS_PER_WAKE - calls)

    @_observed('model_admission')
    @_transaction(unavailable=(False, 'wake_budget_unavailable'), autonomous=True)
    def model_call(self, batch, *, now_ms, identity_bridge=None):
        grant = self._batch_grant(batch, now_ms, identity_bridge)
        if grant is None:
            return True, "interactive"
        if grant is False:
            return False, "untrusted_or_expired_wake"
        if grant["calls"] >= MAX_CALLS_PER_WAKE:
            return False, "wake_model_call_budget"
        try:
            book = self._load(now_ms)
            lane = book["lanes"].setdefault(grant["kind"], {})
            lane["model_request_attempts"] = _number(lane, "model_request_attempts") + 1
            self._save(book)
            grant["calls"] += 1
            return True, "model_request_reserved"
        except (OSError, ValueError, TypeError, OverflowError):
            return False, "wake_budget_unavailable"

    @_observed('speech_admission')
    @_transaction(unavailable=(False, 'wake_budget_unavailable'), autonomous=True)
    def speech(self, batch, *, now_ms, text="", phase="plain", action="",
               identity_bridge=None):
        if phase not in ("plain", "tap", "tool"):
            return False, "unknown_speech_phase"
        grant = self._batch_grant(batch, now_ms, identity_bridge)
        if grant is None:
            return True, "interactive"
        if grant is False:
            return False, "untrusted_or_expired_wake"
        # UI display + channel tap are one logical utterance, not two sends.
        # A SPEAK tool, another text, or another call ID can never reuse this.
        if (phase == "tap" and grant["speech"] == ("plain", text)):
            if grant["tapped"]:
                return False, "plain_delivery_already_attempted"
            grant["tapped"] = True
            return True, "same_plain_delivery"
        if grant["speech"] is not None:
            return False, "one_speech_per_wake"
        if grant["kind"] == "user_test":
            return False, "diagnostic_wake_has_no_outbound_permission"
        try:
            book = self._load(now_ms)
            night = self.is_night(datetime.fromtimestamp(now_ms / 1000))
            reminder = grant["kind"] == "user_reminder"
            if reminder:
                if len(book["recent_reminder_speech"]) >= 12:
                    return False, "reminder_speech_hourly_budget"
                last = _number(book, "last_reminder_speech_ms")
                if last and now_ms - last < 60000:
                    return False, "reminder_speech_cooldown"
                book["recent_reminder_speech"].append(now_ms)
                book["last_reminder_speech_ms"] = now_ms
            else:
                if (_number(book, "speech_day") + _number(book, "legacy_speech_reserve")
                        >= AUTO_SPEECH_DAY):
                    return False, "autonomous_speech_daily_budget"
                if night and (_number(book, "speech_night")
                              + _number(book, "legacy_night_reserve") >= AUTO_SPEECH_NIGHT):
                    return False, "autonomous_speech_night_budget"
                last = _number(book, "last_speech_ms")
                if last and now_ms - last < AUTO_SPEECH_GAP_MS:
                    return False, "autonomous_speech_cooldown"
                book["speech_day"] = _number(book, "speech_day") + 1
                if night:
                    book["speech_night"] = _number(book, "speech_night") + 1
                book["last_speech_ms"] = now_ms
            book["last_speech_attempt"] = {"kind": grant["kind"], "at_ms": now_ms}
            self._save(book)  # Reserve BEFORE plugin/outbox/UI side effects.
            grant["speech"] = ("plain", text) if phase != "tool" else ("tool", action)
            grant["tapped"] = phase == "tap"
            return True, "speech_attempt_reserved"
        except (OSError, ValueError, TypeError, OverflowError):
            return False, "wake_budget_unavailable"

    def snapshot(self, *, now_ms):
        book = self._load(now_ms)
        return {"version": 2, "day": book["day"], "lanes": deepcopy(book["lanes"]),
                "autonomous_speech_attempts": _number(book, "speech_day"),
                "night_speech_attempts": _number(book, "speech_night"),
                "legacy_speech_reserve": _number(book, "legacy_speech_reserve"),
                "legacy_night_reserve": _number(book, "legacy_night_reserve"),
                "limits": {"follow_up_model_daily": DAY_LIMIT["follow_up"],
                           "timer_model_daily": DAY_LIMIT["timer"],
                           "heartbeat_daily_cap": None, "calls_per_wake": MAX_CALLS_PER_WAKE,
                           "heartbeat_day_gap_minutes": 45,
                           "heartbeat_night_gap_minutes": 120,
                           "follow_up_timer_gap_minutes": 15,
                           "explicit_models_per_hour": 12,
                           "explicit_same_kind_gap_seconds": 5,
                           "reminder_speech_per_hour": 12,
                           "reminder_speech_gap_seconds": 60,
                           "autonomous_speech_gap_minutes": 60,
                           "autonomous_speech_daily": AUTO_SPEECH_DAY,
                           "autonomous_speech_night": AUTO_SPEECH_NIGHT}}
