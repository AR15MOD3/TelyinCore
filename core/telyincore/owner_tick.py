"""Explicit host-only route for console follow-up; never a human envelope.

This is a visibility/routing restriction, not an outbound authorization token.
No private QQ account or group is inferred from text or metadata.
"""
from .frozen_json import FrozenDict

OWNER_SCOPE = "private:console:local-owner"
MARKER = "owner_follow_up"
REMINDER_MARKER = "owner_reminder"


def has_marker(event):
    meta = getattr(event, "meta", None)
    return (getattr(event, "origin", None) == "internal"
            and getattr(event, "type", None) == "tick"
            and getattr(event, "source", None) == "heartbeat"
            and isinstance(meta, dict) and MARKER in meta)


def route(event):
    meta = getattr(event, 'meta', None)
    reminder = (getattr(event, 'origin', None) == 'internal'
                and getattr(event, 'type', None) == 'tick'
                and getattr(event, 'source', None) == 'heartbeat'
                and isinstance(meta, dict) and REMINDER_MARKER in meta)
    if not (has_marker(event) or reminder) or getattr(event, "sender_key", None) is not None:
        return ""
    meta = event.meta
    marker = REMINDER_MARKER if reminder else MARKER
    if set(meta) != {marker}:
        return ""
    binding = meta[marker]
    if type(binding) not in (dict, FrozenDict) or set(binding) != {"scope", "source_key", "source_ms"}:
        return ""
    key, stamp = binding["source_key"], binding["source_ms"]
    if (binding["scope"] != OWNER_SCOPE or type(key) is not str or len(key) != 64
            or any(c not in "0123456789abcdef" for c in key)
            or type(stamp) is not int or stamp <= 0):
        return ""
    return OWNER_SCOPE
