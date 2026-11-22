"""Compare ISO instants without changing persisted timestamp spelling.

Naive ISO timestamps retain the application's existing host-local interpretation.
Opaque legacy clocks (e.g. injected '01'/'02') retain their original comparison.
"""
from datetime import datetime, timezone


def instant(value):
    if not isinstance(value, str): return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def clock_key(value):
    parsed = instant(value)
    return value if parsed is None else parsed


def compare(left, right):
    first, second = instant(left), instant(right)
    if first is not None and second is not None:
        left, right = first, second
    return (left > right) - (left < right)
