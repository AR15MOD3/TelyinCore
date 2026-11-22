"""Offline candidate only: no production wiring and no provider token claims.

Accept pre-adapter messages + the current request config. Reuse current image
selection unchanged, then add declarations exactly once. Do not pass a final
subscription input containing additional_tools: that would mix two seams.
The estimate is neither an upper bound nor a billing/caching measurement.
"""
from collections.abc import Mapping

from telyincore.media_budget import _tool_declaration_tokens, fit_images_to_context
from telyincore.tokens import estimate_messages_tokens
from telyincore.tools import ToolDef, ToolSpec


class UnknownBudgetEstimate(ValueError):
    """Candidate cannot honestly provide a numeric budget; never return zero."""


def _supported(tools):
    if not isinstance(tools, (tuple, list)):
        return False
    for tool in tools:
        if isinstance(tool, Mapping):
            if tool.get('type') != 'function':
                return False
            declaration = tool.get('function', tool)
            if not isinstance(declaration, Mapping):
                return False
            name = declaration.get('name')
            description = declaration.get('description', '')
            parameters = declaration.get('parameters')
        elif isinstance(tool, (ToolDef, ToolSpec)):
            name, description, parameters = tool.name, tool.description, tool.parameters
        else:
            return False
        if (not isinstance(name, str) or not name
                or not isinstance(description, str)
                or (parameters is not None and not isinstance(parameters, Mapping))):
            return False
    return True


def estimate_request(messages, config):
    """Only numeric/enumerated diagnostics, no prompt/tool content returned."""
    unknown = {'status': 'unknown', 'total_estimated_tokens': None,
               'native_input_tokens': None}
    if config is None or not hasattr(config, 'tools'):
        return {**unknown, 'reason': 'missing_config_tools'}
    tools = config.tools
    if tools is None:
        tools = ()  # same explicit no-declaration semantics as current selection
    if not _supported(tools):
        return {**unknown, 'reason': 'unsupported_declaration_shape'}
    if (not isinstance(messages, (tuple, list))
            or any(not isinstance(row, Mapping) for row in messages)):
        return {**unknown, 'reason': 'unsupported_message_shape'}
    if any(row.get('type') == 'additional_tools' for row in messages):
        return {**unknown, 'reason': 'final_projection_not_source_messages'}
    selected = fit_images_to_context(messages, config)
    message_tokens = estimate_messages_tokens(selected)
    tool_tokens = _tool_declaration_tokens(tools)
    return {'status': 'estimated', 'message_count': len(selected),
            'declaration_count': len(tools),
            'messages_estimated_tokens': message_tokens,
            'declarations_estimated_tokens': tool_tokens,
            'total_estimated_tokens': message_tokens + tool_tokens,
            'native_input_tokens': None, 'provider_wrapper_overhead_known': False,
            'is_upper_bound': False, 'production_wired': False}


def estimate_callback(config_of):
    """Explicit fixture injection; re-read current config once per invocation.

    config_of must return an immutable request snapshot, not a live property proxy.
    This helper does not pin an Engine or provide a cross-property lock.
    Unknown raises out to the fixture/operator; no fallback to legacy or zero.
    Do not wire this into production until the caller defines unknown handling.
    """
    def estimate(messages):
        report = estimate_request(messages, config_of())
        if report['status'] != 'estimated':
            raise UnknownBudgetEstimate(report['reason'])
        return report['total_estimated_tokens']
    return estimate
