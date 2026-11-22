"""Host-owned continuation leases. Metadata describes scope, never grants it.

A queued callback keeps the scope/generation captured when its job was scheduled.
No prior speaker is inferred from text. Original event identity is checked by
WakePolicy before consumption and again before model/speech admission.
"""
from contextvars import ContextVar
from dataclasses import dataclass, field
import re

current_job = ContextVar('host_continuation_job', default=None)
MARKER = 'host_continuation'
TTL_MS = 6 * 3600000


def is_continuation(event):
    return (getattr(event, 'origin', None) == 'internal'
            and getattr(event, 'type', None) == 'continuation'
            and getattr(event, 'source', None) in ('handoff', 'consolidate', 'activity')
            and getattr(event, 'sender_key', None) is None)


def route(event):
    if not is_continuation(event):
        return ''
    meta = getattr(event, 'meta', None)
    binding = meta.get(MARKER) if isinstance(meta, dict) else None
    scope = binding.get('scope', '') if isinstance(binding, dict) else ''
    return scope if isinstance(scope, str) and re.fullmatch(r'(?:private|group):[^\s]+', scope) else ''


@dataclass(frozen=True)
class JobLease:
    owner: object
    generation: int
    epoch: int
    scope: str
    issued_ms: int
    claimed_sources: set = field(default_factory=set, repr=False)

    def valid(self, console, now_ms):
        owner = getattr(getattr(console, 'app', None), 'loop', None)
        return (owner is self.owner and owner is not None
                and owner.generation == self.generation
                and not getattr(owner, 'sealed', False)
                and not getattr(owner, '_identity_retired', False)
                and not getattr(console, '_operator_shutdown_requested', False)
                and self.epoch == getattr(console, '_continuation_epoch', 0)
                and 0 <= now_ms - self.issued_ms < TTL_MS)


def capture(console, now_ms, *, scope=None):
    if scope is None:
        from .session_view import batch_scope
        try:
            scope = batch_scope(console.original_batch())
        except ValueError:
            scope = ''
    if scope == 'mixed':
        scope = ''
    owner = getattr(getattr(console, 'app', None), 'loop', None)
    return JobLease(owner, getattr(owner, 'generation', -1),
                    getattr(console, '_continuation_epoch', 0), scope, now_ms)
