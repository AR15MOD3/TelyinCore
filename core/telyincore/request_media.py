"""Request-local native media pre-resolution. Shape itself remains synchronous.

Only handles actually visited by the existing pure shape policy are prepared.
No global cache, cross-request fallback, retries, or invented model invocation.
"""
from dataclasses import dataclass
import asyncio
import copy
import inspect
from types import MappingProxyType
from typing import Callable


@dataclass(frozen=True)
class RequestImageResolver:
    prepare: Callable  # synchronous owner-loop snapshot, returns cold native callable


async def shape_request(shape, kind, body, *, base_url, image_resolver, values, signal=None):
    if type(image_resolver) is not RequestImageResolver:
        # Explicit old standalone/test resolver contract, not claimed migrated.
        return shape(kind, body, base_url=base_url, image_resolver=image_resolver, **values)
    frozen = copy.deepcopy(body)
    refs = []

    def collect(reference):
        if reference not in refs:
            refs.append(reference)
        return None  # Pure policy probe: no filesystem, model or HTTP operation.

    shape(kind, frozen, base_url=base_url, image_resolver=collect, **values)
    resolved = {}
    task = asyncio.current_task()
    if (signal is not None and signal.is_set()) or (task is not None and task.cancelling()):
        raise asyncio.CancelledError
    if refs:
        try:
            prepared = image_resolver.prepare(tuple(refs))
            if not inspect.iscoroutinefunction(prepared):
                raise TypeError('native media prepare must return a cold async callable')
            result = await prepared()
            if not isinstance(result, (dict, MappingProxyType)) or set(result) != set(refs):
                raise TypeError('native media result does not match this request')
            for reference, value in result.items():
                if value is not None and (not isinstance(value, str)
                                          or not value.startswith('data:image/')):
                    raise ValueError('native media returned invalid image bytes')
                resolved[reference] = value
        except Exception:
            resolved = {}  # Optional media failure cannot remove the text turn.
    if (signal is not None and signal.is_set()) or (task is not None and task.cancelling()):
        raise asyncio.CancelledError
    lookup = MappingProxyType(resolved)
    return shape(kind, frozen, base_url=base_url, image_resolver=lookup.get, **values)
