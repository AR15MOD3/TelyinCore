"""Bounded diagnostic operations on the owning event loop's live TimerStore.

No paths, store construction, model calls, or wake-policy bypasses here.
"""
from datetime import datetime, timedelta, timezone
import math

from .seal import SealedError
from .timers import TimerConflict, TimerError

MAX_DELAY_SECONDS = 7 * 24 * 60 * 60


def _kind(timer):
    payload = timer.payload
    if isinstance(payload, dict):
        if payload.get("follow_up") is True:
            return "follow_up"
        if payload.get("heartbeat") is True:
            return "heartbeat"
    return "timer"


def _row(timer):
    return {"id": timer.id, "at": timer.at, "kind": _kind(timer)}


def manage(store, operation, body=None, *, now=None, follow_up_delay_ms=None,
           wake=None, approve=None):
    """Return (HTTP status, JSON result). Must execute without await on owner loop."""
    now = now or datetime.now().astimezone()
    if operation == "list":
        return 200, {"timers": [_row(t) for t in store.list()],
                     "now": now.isoformat(), "max_delay_seconds": MAX_DELAY_SECONDS,
                     "policy": "One alarm only; wake gates and automatic rearming remain active."}
    if operation not in ("reschedule", "cancel"):
        return 400, {"error": "unknown_operation"}
    if not isinstance(body, dict):
        return 400, {"error": "JSON_object_required"}
    required = {"id", "expected_at"}
    allowed = required | ({"at", "delay_seconds", "intent"}
                          if operation == "reschedule" else set())
    if not required <= body.keys() or body.keys() - allowed:
        return 400, {"error": "invalid_fields"}
    if any(not isinstance(body[k], str) or not body[k] or len(body[k]) > 200 for k in required):
        return 400, {"error": "invalid_id_or_expected_at"}
    target = None
    intent = body.get("intent")
    if "intent" in body:
        if intent not in ("user_test", "user_reminder"):
            return 400, {"error": "invalid_explicit_intent"}
        if not callable(approve):
            return 503, {"error": "explicit_wake_policy_unavailable"}
    if operation == "reschedule":
        if ("at" in body) == ("delay_seconds" in body):
            return 400, {"error": "provide_exactly_one_of_at_or_delay_seconds"}
        if "delay_seconds" in body:
            delay = body["delay_seconds"]
            if type(delay) is not int or not 1 <= delay <= MAX_DELAY_SECONDS:
                return 400, {"error": "invalid_delay_seconds"}
            target = now + timedelta(seconds=delay)
        else:
            try:
                if not isinstance(body["at"], str) or len(body["at"]) > 80:
                    raise ValueError()
                target = datetime.fromisoformat(body["at"].replace("Z", "+00:00"))
                if target.tzinfo is None:
                    raise ValueError()
            except ValueError:
                return 400, {"error": "at_requires_ISO8601_with_timezone"}
        if not 0 < (target - now).total_seconds() <= MAX_DELAY_SECONDS:
            return 400, {"error": "at_out_of_range"}
    live = next((t for t in store.list() if t.id == body["id"]), None)
    if live is None:
        return 404, {"error": "timer_not_found_or_already_consumed"}
    if live.at != body["expected_at"]:
        return 409, {"error": "timer_changed", "timer": _row(live)}
    if intent and _kind(live) == "follow_up":
        return 409, {"error": "follow_up_cannot_be_reclassified"}
    encoded = None
    if target is not None:
        # Keep the displayed timestamp dialect and precision. TimerStore orders
        # ISO timestamps by their actual instant, including different offsets.
        try:
            previous = datetime.fromisoformat(live.at)
            target = (target.astimezone(previous.tzinfo) if previous.tzinfo else
                      target.astimezone().replace(tzinfo=None))
            precision = "microseconds" if "." in live.at else "seconds"
            encoded = target.isoformat(timespec=precision)
            normalized = datetime.fromisoformat(encoded)
            if normalized.tzinfo is None:
                normalized = normalized.astimezone()
            if normalized <= now:
                return 400, {"error": "at_rounds_to_past"}
        except (ValueError, TypeError):
            return 409, {"error": "unsupported_existing_clock_format"}
        if _kind(live) == "follow_up":
            source = live.payload.get("source_ms")
            if (type(source) is not int or source <= 0 or
                    type(follow_up_delay_ms) is not int or follow_up_delay_ms < 0):
                return 409, {"error": "follow_up_policy_unavailable"}
            floor = (source + follow_up_delay_ms) / 1000
            if not math.isfinite(floor) or normalized.timestamp() < floor:
                return 409, {"error": "before_follow_up_minimum",
                             "earliest_at": datetime.fromtimestamp(floor, timezone.utc).isoformat()}
    try:
        if intent:
            # Bound consent is inert until this exact ID+time is persisted.
            # Never infer explicit user intent from timer payload or model text.
            approve(live.id, encoded, intent, now_ms=int(now.timestamp() * 1000))
        if operation == "cancel":
            store.cancel(live.id)
            result = {"cancelled": _row(live),
                      "automatic_rearm_possible": _kind(live) != "timer"}
        else:
            moved = store.reschedule(live.id, encoded, expected_at=body["expected_at"])
            result = {"timer": _row(moved), "one_off": True, "wake_limits_unchanged": True}
    except KeyError:
        return 404, {"error": "timer_not_found_or_already_consumed"}
    except TimerConflict:
        return 409, {"error": "timer_changed"}
    except SealedError:
        return 503, {"error": "timer_store_sealed"}
    except (OSError, ValueError, TypeError, OverflowError):
        return 500, {"error": "timer_persist_failed", "refresh_required": True}
    if intent:
        result["explicit_intent"] = intent
    if wake is not None:
        wake()
    return 200, result
