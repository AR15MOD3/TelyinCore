"""Offline exact-copy audit. Never removes context or executes tools.

Only indices, counts and enums are returned; no content, names or content
hashes. Repetition is a review candidate, not proof of unnecessary context.
Canonical JSON byte sizes are not token counts or a savings forecast.
"""
from collections import defaultdict
from collections.abc import Mapping
import json

_MAX_BYTES = 8 * 1024 * 1024
_MAX_ITEMS = 4096


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf8')


def _copies(values):
    buckets = defaultdict(list)
    for index, value in enumerate(values):
        buckets[_encoded(value)].append(index)
    return [{'indices': indices,
             'extra_copies': len(indices) - 1,
             'repeated_canonical_json_bytes': len(key) * (len(indices) - 1)}
            for key, indices in buckets.items() if len(indices) > 1]


def audit_redundancy(body):
    """Inspect a supplied final wire body without mutating or logging it.

    Every message field (role/call identity/media/metadata) participates in
    equality. Tool declarations may live at top level or additional_tools.
    Only flat function declarations and chat's nested function form are
    compared; custom/namespace/hosted shapes remain explicitly unknown.
    """
    if not isinstance(body, dict):
        raise ValueError('JSON body object required')
    try:
        size = len(_encoded(body))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError('finite JSON body required') from None
    if size > _MAX_BYTES:
        raise ValueError('body exceeds audit size bound')
    fields = [k for k in ('input', 'messages') if k in body]
    if len(fields) != 1 or not isinstance(body[fields[0]], list):
        raise ValueError('one input/messages list required')
    items = body[fields[0]]
    if len(items) > _MAX_ITEMS or any(not isinstance(x, dict) for x in items):
        raise ValueError('invalid or oversized item list')
    declarations = body.get('tools', [])
    if not isinstance(declarations, list):
        raise ValueError('tool list required')
    declarations = list(declarations)
    locations = [{'location': 'top_level', 'index': i}
                 for i in range(len(declarations))]
    additional = []
    for i, item in enumerate(items):
        if item.get('type') == 'additional_tools':
            tools = item.get('tools')
            if not isinstance(tools, list):
                raise ValueError('additional tool list required')
            additional.append(i)
            declarations.extend(tools)
            locations.extend({'location': 'additional_tools', 'item_index': i,
                              'index': j} for j in range(len(tools)))
    if len(declarations) > _MAX_ITEMS:
        raise ValueError('too many declarations')
    # Keep source indices, excluding declaration containers from message totals.
    ordinary = [i for i in range(len(items)) if i not in additional]
    groups = _copies([items[i] for i in ordinary])
    for group in groups:
        group['indices'] = [ordinary[i] for i in group['indices']]
    normalized = []
    unknown = []
    for i, tool in enumerate(declarations):
        if not isinstance(tool, dict) or tool.get('type') != 'function':
            unknown.append(i)
            continue
        function = tool.get('function', tool)
        # Do not discard outer fields/strictness when comparing declarations.
        if (not isinstance(function, dict)
                or not isinstance(function.get('name'), str)
                or not function['name']):
            unknown.append(i)
            continue
        normalized.append((i, function['name'], tool))
    copies = _copies([x[2] for x in normalized])
    for group in copies:
        group['indices'] = [normalized[i][0] for i in group['indices']]
    named = defaultdict(list)
    for i, name, tool in normalized:
        named[name].append((i, _encoded(tool)))
    conflicts = [{'indices': [i for i, _ in rows]}
                 for rows in named.values() if len({v for _, v in rows}) > 1]
    return {'status': 'audited', 'scope': 'offline_final_wire_exact_copies',
            'item_count': len(items), 'message_items': len(ordinary),
            'repeated_message_groups': groups,
            'message_extra_copies': sum(g['extra_copies'] for g in groups),
            'declaration_count': len(declarations),
            'declaration_locations': locations,
            'declaration_status': 'unknown_partial' if unknown else 'audited',
            'unsupported_declaration_indices': unknown,
            'repeated_declaration_groups': copies,
            'declaration_extra_copies': sum(g['extra_copies'] for g in copies),
            'same_name_conflicting_declaration_groups': conflicts,
            'native_input_tokens': None, 'safe_to_remove_proven': False,
            'token_or_cost_savings_proven': False, 'production_wired': False}
