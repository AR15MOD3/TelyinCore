"""Keep one already-visible occurrence of an identical authorized archive preview."""
import json
import re
from collections.abc import Mapping


def _proof(body, record, hint):
    """Verify the displayed owner's full-length, same-room archive coordinates."""
    from .original_evidence import OriginalSpan, OriginalText
    from .receipt_projection import CurrentBatchOutput
    from .retrieval_scope import valid_scope
    if (not isinstance(body, CurrentBatchOutput) or not body.source_batch
            or getattr(body, 'record', None) is not record or body.source_reader != 'read_events'
            or record.name != 'read_events' or not valid_scope(record.scope) or record.scope != body.source_scope
            or not isinstance(record.text, OriginalText) or body.source_spans != record.text.wire_spans):
        return None
    ref, date, text = hint['source_ref'], hint['recorded_date'], hint['excerpt']
    if (not re.fullmatch(r'record:[0-9a-f]{64}:[1-9][0-9]*', ref)
            or (date,text,ref) not in record.text.originals
            or {scope for key,scope in record.text.record_scopes if key == ref} != {record.scope}):
        return None
    spans = [span for span in body.source_spans if isinstance(span, OriginalSpan)
             and span.key == ('record_preview',record.scope,ref[7:])]
    if len(spans) != 1:
        return None
    span = spans[0]
    if (type(span.source_length) is not int or span.source_length <= 240 or span.source_offset
            or span.start < 0 or span.end-span.start != 240 or len(text) != 240
            or body[span.start:span.end] != text or record.text[span.start:span.end] != text):
        return None
    return record.scope, body.source_batch, span.source_length


def preserve_identical_preview(entries, omitted, latest_hint_group, messages):
    """Exchange two equivalent hint positions without changing either receipt's own source."""
    first, hint = omitted
    final_group_start, final_group_end = latest_hint_group
    if first >= final_group_start:
        return entries
    bodies = {id(record): content for row in messages if isinstance(row, Mapping) and row.get('role') == 'tool'
              and (record := getattr(content := row.get('content'), 'record', None)) is not None}
    record, raw, _ = entries[first]
    proof = _proof(bodies.get(id(record)), record, hint)
    if proof is None:
        return entries
    for at in range(final_group_end-1, final_group_start-1, -1):
        other, value, _ = entries[at]
        row = json.loads(value)
        selected = row.get('user_originals', ())
        if len(selected) < 2 or hint not in selected or _proof(bodies.get(id(other)), other, hint) != proof:
            continue
        earlier = json.loads(raw)
        earlier['user_originals'].append(hint)
        selected.remove(hint)
        changed = list(entries)
        changed[first] = record, json.dumps(earlier, ensure_ascii=False), False
        changed[at] = other, json.dumps(row, ensure_ascii=False), False
        return changed
    return entries
