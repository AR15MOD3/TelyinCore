"""Split a ready bus snapshot before session/model consumption.

Timing remains the bus's contract (including fixed/min/max/piggyback).
The first occurrence orders audiences; a finite snapshot cannot be extended
by a hot producer, so its later audiences cannot starve. Within each trusted
scope arrival order is preserved. Subsequent snapshots never overtake it.
Unscoped events, including autonomous system wakes, are singleton batches:
they neither inherit a recent user nor attach themselves to a foreign scope.
This does not grant an unknown envelope a valid outbound recipient.
"""
from __future__ import annotations

from typing import Any, Sequence

from .session_view import event_scope


def conversation_batches(items: Sequence[Any], *, isolate=None) -> list[list[Any]]:
    """Partition only by the existing trusted envelope contract, not text."""
    buckets: dict[object, list[Any]] = {}
    for item in items:
        scope = event_scope(item)
        # Autonomous events own individual wake grants, even when they share
        # the human's scope. Combining either two wakes or a wake and a human
        # message makes the whole batch inadmissible to WakePolicy.
        key = scope if scope and getattr(item, 'origin', None) == 'external' else object()
        buckets.setdefault(key, []).append(item)
    if isolate is None:
        return list(buckets.values())
    result = []
    for bucket in buckets.values():
        current = []
        for item in bucket:
            if isolate(item):
                if current:
                    result.append(current)
                    current = []
                result.append([item])
            else:
                current.append(item)
        if current:
            result.append(current)
    return result
