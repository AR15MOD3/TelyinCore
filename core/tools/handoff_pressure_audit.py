"""Offline-only diagnostic; no reset, clipping, retry, or production wiring.

The caller must identify mandatory items using fixture/provenance evidence.
Roles alone do not identify fixed overhead. A floor is relative to the local
heuristic, never proof of a provider's physical limit or tokenizer behavior.
"""
from telyincore.media_budget import _tool_declaration_tokens
from telyincore.tokens import estimate_messages_tokens
from tools.cache_budget_audit import _functions


def audit_pressure(body, hard_limit, *, mandatory_indices=None):
    if hard_limit is not None and (type(hard_limit) is not int or hard_limit < 0):
        raise ValueError('nonnegative integer or unknown hard limit required')
    fields = [key for key in ('input', 'messages') if key in body]
    if len(fields) != 1 or not isinstance(body[fields[0]], list):
        raise ValueError('one input/messages list required')
    items = body[fields[0]]
    if len(items) > 4096 or any(not isinstance(row, dict) for row in items):
        raise ValueError('invalid or oversized item list')
    declarations = body.get('tools', [])
    if not isinstance(declarations, list):
        raise ValueError('tool list required')
    declarations = list(declarations)
    messages = []
    positions = []
    for index, row in enumerate(items):
        if row.get('type') == 'additional_tools':
            if not isinstance(row.get('tools'), list):
                raise ValueError('additional_tools list required')
            declarations.extend(row['tools'])
        else:
            messages.append(row)
            positions.append(index)
    tools = _functions(declarations)
    if tools is None:
        return {'status': 'unknown_declarations', 'native_input_tokens': None}
    indices = None
    if mandatory_indices is not None:
        indices = list(mandatory_indices)
        if (not indices or any(type(i) is not int or i not in positions for i in indices)
                or len(set(indices)) != len(indices)):
            raise ValueError('unique attributed message indices required')
    declared = _tool_declaration_tokens(tools)
    total = estimate_messages_tokens(messages) + declared
    floor = (estimate_messages_tokens([items[i] for i in indices]) + declared
             if indices is not None else None)
    status = ('no_hard_limit' if hard_limit is None else
              'mandatory_attribution_unknown' if floor is None else
              'estimated_fixed_floor_exceeds_hard' if floor > hard_limit else
              'estimated_fixed_floor_fits')
    return {'status': status, 'hard_limit': hard_limit,
            'mandatory_attribution': 'caller_supplied' if indices is not None else 'unknown',
            'mandatory_items': len(indices) if indices is not None else None,
            'messages_estimated_tokens': estimate_messages_tokens(messages),
            'declarations_estimated_tokens': declared, 'total_estimated_tokens': total,
            'mandatory_floor_estimated_tokens': floor,
            'exceeds_hard': total > hard_limit if hard_limit is not None else None,
            'estimated_room_after_mandatory': hard_limit - floor
            if floor is not None and hard_limit is not None else None,
            'native_input_tokens': None, 'provider_fit_proven': False,
            'production_wired': False}
