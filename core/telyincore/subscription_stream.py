"""Normalize one observed subscription-only empty terminal output variant.

Only completed output_item.done snapshots can supply the omitted terminal list.
Never manufacture text from deltas, relax the accumulator, or log payloads.
"""
from __future__ import annotations

import contextlib
import json
from typing import AsyncIterable, AsyncIterator

from .stream import ResponseProtocolError, iter_sse_events


async def subscription_events(chunks: AsyncIterable[bytes]) -> AsyncIterator[bytes]:
    response_id = None
    added: dict[int, str] = {}
    done: dict[int, dict] = {}
    size = 0
    parser = iter_sse_events(chunks)
    async with contextlib.aclosing(parser):
        async for event in parser:
            if not isinstance(event, dict):
                raise ResponseProtocolError('subscription event must be an object')
            kind = event.get('type')
            if kind == 'response.created':
                response = event.get('response')
                if isinstance(response, dict):
                    response_id = response.get('id')
            if kind in ('response.output_item.added', 'response.output_item.done'):
                index, item = event.get('output_index'), event.get('item')
                if (type(index) is not int or not 0 <= index < 256
                        or not isinstance(item, dict)
                        or not isinstance(item.get('id'), str) or not item['id']):
                    raise ResponseProtocolError('subscription item identity/index invalid')
                if kind.endswith('.added'):
                    if index in added or item['id'] in added.values():
                        raise ResponseProtocolError('duplicate subscription item admission')
                    added[index] = item['id']
                else:
                    if index in done or added.get(index) != item['id']:
                        raise ResponseProtocolError('subscription item done has no exact admission')
                    size += len(json.dumps(item, ensure_ascii=False).encode('utf-8'))
                    if size > 4 * 1024 * 1024:
                        raise ResponseProtocolError('subscription item snapshots exceed bound')
                    done[index] = item
            if kind == 'response.completed':
                response = event.get('response')
                if isinstance(response, dict):
                    _log_usage(response.get('usage'))
                if (isinstance(response, dict) and response.get('status') == 'completed'
                        and response.get('output') == [] and done):
                    if (not isinstance(response_id, str) or not response_id
                            or response.get('id') != response_id
                            or set(added) != set(done)
                            or sorted(done) != list(range(len(done)))):
                        raise ResponseProtocolError('subscription terminal snapshot incomplete')
                    # Preserve terminal usage/status/identity/sequence; the common
                    # accumulator still checks done IDs and terminal text vs deltas.
                    event = {**event, 'response': {**response,
                        'output': [done[i] for i in sorted(done)]}}
            yield ('data: ' + json.dumps(event, ensure_ascii=False) + '\n\n').encode('utf-8')


def _log_usage(usage):
    """Numeric terminal usage only; absent values are unknown, never zero."""
    import sys
    def count(value):
        return str(value) if type(value) is int and value >= 0 else 'unknown'
    u = usage if isinstance(usage, dict) else {}
    details = u.get('output_tokens_details')
    details = details if isinstance(details, dict) else {}
    cached = u.get('input_tokens_details')
    cached = cached if isinstance(cached, dict) else {}
    print('[usage] subscription input=' + count(u.get('input_tokens'))
          + ' output=' + count(u.get('output_tokens'))
          + ' reasoning=' + count(details.get('reasoning_tokens'))
          + ' cached=' + count(cached.get('cached_tokens')), file=sys.stderr, flush=True)
