"""Navigation from a stored search receipt to the user records it really returned."""
from contextlib import closing
import json
import re

from .original_evidence import OriginalText


def _live_receipt(context, grant, ref, row):
    if context is None or row.get('record_ref') or row.get('scope') != grant.current:
        return None
    console, owner = context
    guard = getattr(console, 'reply_evidence_guard', None)
    marker = getattr(guard, 'receipt_marker', None)
    view = getattr(console, '_request_session', None)
    snapshot = getattr(view, 'tool_evidence_snapshot', None)
    allows = getattr(view, 'quote_source_visible', None)
    if (owner is None or marker is None or getattr(guard, 'owner', None) is not owner
            or console.current_batch_identity() is not owner or not callable(snapshot) or not callable(allows)):
        return None
    try:
        epoch, _, entries = snapshot()
        if epoch is not marker[0] or not allows(grant.current, grant.current):
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    matches = [record for position, record, locator in entries
               if position > marker[1] and locator == 'record:' + ref]
    if len(matches) != 1:
        return None
    record = matches[0]
    if (record.kind != 'function_result' or record.role != 'assistant' or record.name != 'read_events'
            or record.scope != grant.current or not record.tool_ref or record.tool_ref != row.get('tool_ref')
            or not isinstance(record.text, OriginalText) or len(record.text) != row['_text_length']
            or not record.text.startswith(row['text'])):
        return None
    return record.text


def navigation(session, grant, ref, row, context, alias):
    """Offer bounded current-room original pointers; all reads still use normal archive validation."""
    from .record_history import rows
    live = _live_receipt(context, grant, ref, row)
    links = {}
    if live is not None:
        for _, excerpt, locator in live.originals:
            if (not isinstance(locator, str) or not re.fullmatch(r'record:[0-9a-f]{64}:[1-9][0-9]{0,18}', locator)
                    or locator in links or not excerpt or excerpt not in live
                    or {scope for key, scope in live.record_scopes if key == locator} != {grant.current}):
                continue
            with closing(rows(session, reference=locator[7:], _visible=grant.permits, _window=(0, min(len(excerpt),240)))) as found:
                match = next(found, None)
            if match is None:
                continue
            source = match[1]
            if (source.get('scope') != grant.current or source.get('role') != 'user'
                    or (source.get('kind') or 'message') != 'message'
                    or any(source.get(flag) for flag in ('is_head', 'is_flow', 'is_system', 'is_ephemeral'))
                    or source.get('record_ref') or source['text'] != excerpt[:240]):
                continue
            label = json.dumps(excerpt[:36], ensure_ascii=False)
            key = alias(locator[7:]) if alias is not None else locator
            links[locator] = f'{label} → record_ref="{key}"'
            if len(links) >= 4:
                break
    if links:
        return '本回执列出的用户发言（开头 → 用 read_events 打开原件）：\n' + '\n'.join(links.values()) + '\n'
    return '查用户发言可按关键词检索 store="records"、category="user"。\n'
