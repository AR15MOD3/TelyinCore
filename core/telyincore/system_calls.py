"""Typed host system hooks, distinct from model tool invocations.

Trusted cooperative Python, not a security sandbox. Tickets are valid only
while registered by the loader for its exact live instance, task and loop.
"""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SystemHook:
    prepare: Callable  # owner-loop snapshot only: no I/O, await or side effects


@dataclass(frozen=True)
class PreparedSystemCall:
    bot: str
    scope: str
    body: Callable  # distinct native async body, already frozen by prepare


@dataclass(frozen=True, eq=False)
class SystemInvocation:
    plugin: str
    instance: Any
    root: str
    operation: str
    call_id: str
    bot: str
    scope: str


current_system_call = ContextVar('core_host_system_call', default=None)
