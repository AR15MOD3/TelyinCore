"""Bounded host creation context, never a claim about the user's agreement.

Stored with the schedule atomically. References are checked against original
bytes and fresh retrieval authority on every read; no message body is copied.
"""
from datetime import datetime
import hashlib
from itertools import islice
import json
import re

from .async_tools import current_invocation
from .events import EventEnvelope

KEY = 'creation_context'
MAX_EVENTS = 16
HEX = re.compile(r'[0-9a-f]{64}\Z')
REF = re.compile(r'[0-9a-f]{32}\Z')
BOUNDARY = ('仅证明创建提醒时本轮看到了这些事件，不证明其中任何一句就是原始约定，'
            '也不证明是用户要求创建；须读取原文、核对后续更正及对应工具回执。'
            'context_check=unavailable_or_changed表示曾记录的来源当前无法核实，不是从未记录。')


def fingerprint(row):
    digest = hashlib.sha256()
    for chunk in json.JSONEncoder(ensure_ascii=False, sort_keys=True,
                                  separators=(',', ':')).iterencode(row._as_wire()):
        digest.update(chunk.encode('utf-8', errors='surrogatepass'))
    return digest.hexdigest()


def normalized(value):
    """Reject malformed persisted metadata; never upgrade legacy free text."""
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1:
        return None
    refs = value.get('events')
    if not isinstance(refs, list) or len(refs) > MAX_EVENTS:
        return None
    if type(value.get('truncated')) is not bool:
        return None
    at = value.get('created_at')
    if not isinstance(at, str) or len(at) > 64:
        return None
    try:
        if datetime.fromisoformat(at).tzinfo is None: return None
    except ValueError:
        return None
    tool_ref = value.get('tool_ref')
    if tool_ref is not None and (not isinstance(tool_ref, str) or REF.fullmatch(tool_ref) is None):
        return None
    result = []
    for item in refs:
        if not isinstance(item, dict): return None
        cursor, digest = item.get('cursor'), item.get('sha256')
        if type(cursor) is not int or cursor < 1 or not isinstance(digest, str) or not HEX.fullmatch(digest):
            return None
        if cursor in {r['cursor'] for r in result}: return None
        result.append({'cursor': cursor, 'sha256': digest})
    return {'version': 1, 'created_at': at, 'tool_ref': tool_ref,
            'events': result, 'truncated': value['truncated']}


def capture(scope, events, store):
    scope.require()
    batch = list(islice(iter(events), MAX_EVENTS + 1))
    refs = []
    for event in batch[:MAX_EVENTS]:
        if not isinstance(event, EventEnvelope) or event.origin != 'external' or not scope.event(event):
            continue
        row = store.get(event.cursor) if store is not None else None
        if row is None or not scope.event(row): continue
        digest = fingerprint(event)
        if fingerprint(row) == digest and event.cursor not in {r['cursor'] for r in refs}:
            refs.append({'cursor': event.cursor, 'sha256': digest})
    invocation = current_invocation.get()
    ref = invocation.tool_ref if invocation is not None and invocation.name == 'set_timer' else None
    return {'version': 1, 'created_at': datetime.now().astimezone().isoformat(),
            'tool_ref': ref if isinstance(ref, str) and REF.fullmatch(ref) else None,
            'events': refs, 'truncated': len(batch) > MAX_EVENTS}


def project(value, scope, store):
    scope.require()
    result = {'original_request_ref': None, 'origin_linkage': 'not_recorded',
              'boundary': BOUNDARY,
              'creation_receipt_existence': 'not_established_by_creation_context',
              'creator': 'unknown', 'creation_reason': 'unknown',
              'request_attribution': 'unverified',
              'creation_intent_status': 'unverified',
              'intent_boundary': '远期日期、来源缺失、记录仍保留都不能证明创建意图；更像、可能等措辞不把猜测变成创建原因。可提出后续建议，不能将未核实的历史提醒解释成只是占位、备忘或并不打算提醒。',
              'attribution_boundary': '来源未核实的提醒称为该提醒或相关事项，不预设是你托的约定；本批明确用户请求及真实成功工具回执仍可独立证明本次创建。'}
    context = normalized(value)
    if context is None: return result
    # No inherited permissions and no unvalidated cursor/digest in tool output.
    visible = []
    for ref in context['events']:
        row = store.get(ref['cursor']) if store is not None else None
        if row is not None and scope.event(row) and fingerprint(row) == ref['sha256']:
            visible.append({'event_ref': 'event:' + str(ref['cursor']), 'cursor': ref['cursor']})
    result.update(origin_linkage='creation_context_only', created_at=context['created_at'],
                  context_recorded=bool(context['events']),
                  context_check='verified' if len(visible)==len(context['events']) else 'unavailable_or_changed',
                  context_events=visible, context_incomplete=context['truncated'] or len(visible) != len(context['events']),
                  creation_tool_ref=context['tool_ref'])
    return result
