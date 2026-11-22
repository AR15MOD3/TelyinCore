"""Read-only request-size estimate; never used for handoff decisions.

Only a source-message heuristic, not final provider tokens or a safe upper
bound. Per-read field capture makes image selection and totals share values;
it is not a cross-property transaction or the last sent request snapshot.
"""
from collections.abc import Mapping
from copy import deepcopy
from types import SimpleNamespace
from .media_budget import _tool_declaration_tokens, fit_images_to_context
from .tokens import estimate_messages_tokens, summarize_messages
from .tools import ToolDef, ToolSpec


def capture_size_config(config):
    missing = object()
    tools = getattr(config, 'tools', missing)
    if tools is missing:
        return None
    # Capture only declaration data, never deepcopy handlers or other runtime objects.
    captured = tools
    normalized = () if tools is None else tools
    if _supported(normalized):
        captured = []
        for tool in normalized:
            declaration = tool.get('function', tool) if isinstance(tool, Mapping) else tool
            def field(name, default=None):
                if isinstance(declaration, Mapping):
                    return declaration.get(name, default)
                return getattr(declaration, name, default)
            captured.append({'type': 'function', 'name': field('name'),
                             'description': field('description', ''),
                             'parameters': deepcopy(field('parameters'))})
    # Keep caller's outer tool overlay; never peek through it and lose tools.
    return SimpleNamespace(tools=captured,
                           context_window=getattr(config, 'context_window', None),
                           max_tokens=getattr(config, 'max_tokens', 0))


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


def estimate_request(messages, config, *, include_parts=False):
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
    result = {'status': 'estimated', 'message_count': len(selected),
            'declaration_count': len(tools),
            'messages_estimated_tokens': message_tokens,
            'declarations_estimated_tokens': tool_tokens,
            'total_estimated_tokens': message_tokens + tool_tokens,
            'native_input_tokens': None, 'provider_wrapper_overhead_known': False,
            'is_upper_bound': False, 'usage': 'readonly_request_size'}


    if include_parts:
        parts = summarize_messages(selected)
        parts['tools'] = tool_tokens
        result['parts'] = parts
    return result
