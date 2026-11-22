"""One absolute budget across nested, synchronous owned sharing cleanup.

This limits cooperative waits; it cannot interrupt arbitrary Python/OS calls.
An expired wait never grants permission to discard an alive resource.
"""
from contextvars import ContextVar
from functools import wraps
import time

_deadline = ContextVar('sharing_close_deadline', default=None)
CLOSE_SECONDS = 30.0


def remaining():
    deadline = _deadline.get()
    if deadline is None:
        raise RuntimeError('sharing close wait outside owned budget')
    return max(0.0, deadline - time.monotonic())


def bounded_close(function):
    @wraps(function)
    def close(*args, **kwargs):
        token = None
        if _deadline.get() is None:
            token = _deadline.set(time.monotonic() + CLOSE_SECONDS)
        try:
            return function(*args, **kwargs)
        finally:
            if token is not None:
                _deadline.reset(token)
    return close
