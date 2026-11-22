"""Content-free request diagnostics. Representation characters are NOT tokens/bytes."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping


def request_log_lines(body: Mapping, *, verbose: bool = False) -> list[str]:
    items = body.get("input") or body.get("messages") or ""
    counts = Counter()
    images = encoded = embedded_tools = 0
    shapes = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            counts["other"] += 1
            shapes.append(type(item).__name__)
            continue
        role, kind = item.get("role"), item.get("type")
        shapes.append(f"{role if role in ('system','developer','user','assistant','tool') else 'other'}/{kind if kind in (None,'message','function_call','function_call_output','additional_tools') else 'other'}/{type(item.get('content')).__name__}")
        if kind == "additional_tools":
            embedded_tools += len(item.get("tools") or [])
        elif kind == "function_call":
            counts["call"] += 1
        elif kind == "function_call_output":
            counts["result"] += 1
        elif role == "tool":
            counts["result"] += 1
        elif role in ("system", "developer"):
            counts["instruction"] += 1
        elif role in ("user", "assistant", "tool") and kind in (None, "message"):
            counts["message"] += 1
            if isinstance(item.get("tool_calls"), list):
                counts["call"] += len(item["tool_calls"])
        else:
            counts["other"] += 1
        # Only actual content parts, never tool schemas or arbitrary text.
        parts = item.get("content")
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, Mapping):
                    continue
                if part.get("type") not in ("input_image", "image_url", "image"):
                    continue
                images += 1
                url = part.get("image_url")
                if isinstance(url, Mapping):
                    url = url.get("url")
                if isinstance(url, str) and url.startswith("data:image/") and ";base64," in url:
                    encoded += len(url)
                source = part.get("source")
                if isinstance(source, Mapping) and source.get("type") == "base64":
                    data = source.get("data")
                    if isinstance(data, str):
                        encoded += len(data)
    top_instructions = body.get("instructions")
    if isinstance(top_instructions, str) and top_instructions:
        counts["instruction"] += 1
    tools = len(body.get("tools") or []) + embedded_tools
    model = "模型已指定" if body.get("model") else "模型未指定"
    summary = (f"发出 · {model} · 指令 {counts['instruction']} · 消息 {counts['message']}"
               f" · 调用 {counts['call']} · 返回 {counts['result']} · 工具 {tools}")
    if counts["other"]:
        summary += f" · 其他 {counts['other']}"
    size = len(str(items))
    volume = f"输入表示 {size:,} 字符（非 token） · 图片 {images} 张"
    if encoded:
        volume += f" · 图片编码 {encoded:,} · 其余 {size - encoded:,}"
    # This metric covers message representation only, not provider tokens or
    # the whole request. Keep top-level instructions separate and content-free.
    if top_instructions is not None:
        volume = volume.replace("输入表示 ", "消息表示 ", 1)
    lines = [summary, volume]
    if isinstance(top_instructions, str):
        lines.append(f"顶层指令 {len(top_instructions):,} 字符（非 token；未计入上行消息表示）")
    elif top_instructions is not None:
        lines.append("顶层指令类型非字符串（未估算）")
    if verbose:
        lines += ["输入结构 => " + str(shapes), "请求字段 => " + str(len(body))]
    return lines


def request_structure(body):
    """Bounded allowlisted wire shape, without text, keys, IDs or arguments."""
    allowed = ('model', 'messages', 'input', 'tools', 'stream', 'stream_options',
               'max_tokens', 'max_completion_tokens', 'max_output_tokens',
               'temperature', 'top_p', 'top_k', 'thinking', 'reasoning_effort',
               'reasoning', 'chat_template_kwargs', 'tool_choice')
    rows = body.get('messages') or body.get('input') or []
    shapes = []
    for row in rows if isinstance(rows, list) else []:
        if len(shapes) >= 128:
            break
        if not isinstance(row, Mapping):
            shapes.append({'type': 'other'}); continue
        role = row.get('role')
        item = {'role': role if role in ('system','developer','user','assistant','tool') else 'other',
                'content_type': type(row.get('content')).__name__}
        calls = row.get('tool_calls')
        if isinstance(calls, list):
            item['tool_calls'] = len(calls)
        for field in ('reasoning_content', 'reasoning', 'extra_content', 'tool_call_id'):
            if field in row:
                item[field + '_present'] = True
        shapes.append(item)
    return {'fields': [k for k in allowed if k in body],
            'unknown_field_count': sum(k not in allowed for k in body),
            'message_count': len(rows) if isinstance(rows, list) else 0,
            'messages': shapes, 'messages_omitted': max(0, len(rows)-len(shapes)) if isinstance(rows,list) else 0,
            'tool_count': len(body.get('tools') or [])}
