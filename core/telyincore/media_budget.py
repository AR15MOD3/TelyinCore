"""Request-only image selection for a configured model context window.



Ingress limits protect downloads and uploads; this layer protects the model

request. Session records and cached media remain untouched so an older image

can still be selected in a later request after a handoff.

"""

from __future__ import annotations



from typing import Any, Mapping, Sequence

from types import SimpleNamespace



from .tokens import IMAGE_PART_TYPES, estimate_messages_tokens, estimate_tokens





_OMITTED = "[图片未随本次请求送入视觉模型：上下文预算不足]"





def _tool_declaration_tokens(tools: Sequence[Any]) -> int:

    """Count the wire declarations, which are dictionaries in the live config.



    The usage panel receives ToolDef objects instead. Passing the wire-shaped

    dictionaries to its object-only estimator silently counts every tool as

    zero, so request budgeting must read both shapes explicitly.

    """

    total = 0

    for tool in tools:

        if isinstance(tool, Mapping):

            declaration = tool.get("function", tool)

            if not isinstance(declaration, Mapping):

                declaration = tool

            name = declaration.get("name", "")

            description = declaration.get("description", "")

            parameters = declaration.get("parameters")

        else:

            name = getattr(tool, "name", "")

            description = getattr(tool, "description", "")

            parameters = getattr(tool, "parameters", None)

        total += estimate_tokens(f"{name or ''}{description or ''}"

                                 f"{parameters if parameters is not None else ''}")

    return total





def fit_images_to_context(messages: Sequence[Mapping[str, Any]], config: Any

                          ) -> list[Mapping[str, Any]]:

    """Prefer newer pictures; never infer a limit when the window is unknown.



    The 4200-token image estimate is deliberately conservative. Keep an extra

    5% (at least 1024 tokens) for estimator/model differences, in addition to

    max_tokens reserved for the answer. If text alone is too large, this

    function cannot repair it; the existing handoff mechanism handles that.

    """

    original = list(messages)

    window = getattr(config, "context_window", None)

    if type(window) is not int or window <= 0:

        return original

    output_tokens = getattr(config, "max_tokens", 0)

    if type(output_tokens) is not int or output_tokens < 0:

        return original

    # Plain text and tool receipts give the image selector nothing to change.
    # The handoff estimator still budgets all text and declarations separately.
    for row in original:
        content = row.get("content")
        if isinstance(content, list) and any(
            isinstance(part, Mapping) and str(part.get("type") or "") in IMAGE_PART_TYPES
            for part in content
        ):
            break
    else:
        return original

    budget = max(0, window - output_tokens - max(1024, window // 20))

    tools = getattr(config, "tools", ()) or ()



    declared = _tool_declaration_tokens(tools)



    def cost(rows: Sequence[Mapping[str, Any]]) -> int:

        return estimate_messages_tokens(rows) + declared



    if cost(original) <= budget:

        return original

    result: list[Mapping[str, Any]] = []

    positions: list[tuple[int, int, Any]] = []

    for row in original:

        content = row.get("content")

        if not isinstance(content, list):

            result.append(row)

            continue

        parts = list(content)

        for index, part in enumerate(parts):

            if (isinstance(part, Mapping)

                    and str(part.get("type") or "") in IMAGE_PART_TYPES):

                positions.append((len(result), index, part))

                parts[index] = {"type": "input_text", "text": _OMITTED}

        result.append({**row, "content": parts})

    # Keep the exact existing newest-first selection, but update cost only for
    # the changed row. The estimator is additive over messages; its per-call
    # base/wrapper cancel in the row delta. No provider-token claim or persistent
    # cache: all estimates stay local to this request.
    selected_cost = cost(result)
    row_costs: dict[int, int] = {}
    for row_index, part_index, part in reversed(positions):
        parts = result[row_index]["content"]
        previous_cost = row_costs.get(row_index)
        if previous_cost is None:
            previous_cost = estimate_messages_tokens([result[row_index]])
            row_costs[row_index] = previous_cost
        parts[part_index] = part
        candidate_cost = estimate_messages_tokens([result[row_index]])
        delta = candidate_cost - previous_cost
        if selected_cost + delta > budget:
            parts[part_index] = {"type": "input_text", "text": _OMITTED}
        else:
            selected_cost += delta
            row_costs[row_index] = candidate_cost
    return result





def estimate_request_tokens(messages: Sequence[Mapping[str, Any]], config: Any) -> int:

    """Prospective source-message budget plus the configured declarations.



    Reuse existing media/tool heuristics without changing image selection.

    Capture relevant fields for this calculation; not an atomic Engine read.

    This is not a provider tokenizer or a safe upper bound. Tool kinds beyond

    the existing declaration estimator retain its limitations, not new support.

    """

    snapshot = SimpleNamespace(

        tools=getattr(config, 'tools', ()) or (),

        context_window=getattr(config, 'context_window', None),

        max_tokens=getattr(config, 'max_tokens', 0))

    selected = fit_images_to_context(messages, snapshot)

    return estimate_messages_tokens(selected) + _tool_declaration_tokens(snapshot.tools)

