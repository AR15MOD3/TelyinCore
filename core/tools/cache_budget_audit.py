"""Explicit offline budget audit, not wired to handoff or request selection.

All numeric token values are existing heuristics, never provider measurements.
The final-body message estimator alone misses additional_tools payloads. Count
declarations once from their actual location, and report unsupported shapes as
unknown instead of treating them as free. No content or tool names are returned.
"""
from collections.abc import Mapping

from telyincore.media_budget import _tool_declaration_tokens, fit_images_to_context
from telyincore.protocol import flatten_tools
from telyincore.tokens import estimate_messages_tokens


def _functions(tools):
    if not isinstance(tools, (list, tuple)):
        return None
    if any(not isinstance(t, Mapping) or t.get('type') != 'function'
           for t in tools):
        return None
    rows = flatten_tools(tools)
    if any(not isinstance(t.get('name'), str) or not t['name']
           or not isinstance(t.get('parameters', {}), Mapping) for t in rows):
        return None
    return rows


def audit_budget(messages, config, final_body):
    """Read-only; caller supplies the same config/messages as the shaped body.

    Payload equality is a local consistency check, not proof that the provider
    accepted/billed any declaration. JSON wrapper costs are not calibrated.
    """
    fields = [key for key in ('input', 'messages') if key in final_body]
    if len(fields) != 1 or not isinstance(final_body[fields[0]], list):
        raise ValueError('one final input/messages list required')
    rows = final_body[fields[0]]
    wire_tools = list(final_body.get('tools') or ())
    additional = 0
    for row in rows:
        if isinstance(row, Mapping) and row.get('type') == 'additional_tools':
            additional += 1
            value = row.get('tools')
            if not isinstance(value, list):
                return {'status': 'unknown_declaration_shape', 'native_input_tokens': None}
            wire_tools.extend(value)
    source_tools = getattr(config, 'tools', ()) or ()
    source = _functions(source_tools)
    final = _functions(wire_tools)
    if source is None or final is None:
        return {'status': 'unknown_declaration_shape', 'native_input_tokens': None}
    selected = fit_images_to_context(messages, config)
    messages_est = estimate_messages_tokens(selected)
    declarations_est = _tool_declaration_tokens(source)
    final_est = _tool_declaration_tokens(final)
    return {
        'status': 'estimated', 'native_input_tokens': None,
        'source_message_count': len(selected), 'final_item_count': len(rows),
        'additional_tools_items': additional,
        'configured_declaration_count': len(source),
        'final_declaration_count': len(final),
        'declarations_equal_after_projection': source == final,
        'source_messages_estimated_tokens': messages_est,
        'configured_declarations_estimated_tokens': declarations_est,
        'source_total_estimated_tokens': messages_est + declarations_est,
        'final_messages_only_estimated_tokens': estimate_messages_tokens(rows),
        'final_declarations_estimated_tokens': final_est,
        'final_total_estimated_tokens': estimate_messages_tokens(rows) + final_est,
        'provider_wrapper_overhead_known': False,
        'billing_or_cache_savings_proven': False,
    }
