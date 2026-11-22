"""Lossless, append-stable references within one authorized wire request.

Persisted records remain complete. No cross-request cache or lookup is used.
"""
from collections import Counter
from collections.abc import Mapping
import json
import re
from .original_evidence import is_original_receipt
from .tokens import estimate_tokens

READERS = frozenset({'skill_read', 'kb_read', 'pending_read', 'read_events',
                     'memory_search', 'kb_search'})


class SourceOutput(str):
    """Request-only coordinates carried through permission filters and trimming."""

    def __new__(cls, value, *, scope='', name='', batch=0):
        obj = super().__new__(cls, value)
        obj.source_reader = name
        obj.source_scope = scope
        obj.source_batch = batch
        obj.source_spans = value.wire_spans if is_original_receipt(name, value) else ()
        return obj


class CurrentBatchOutput(SourceOutput):
    """The existing local history folder must retain a live QQ batch receipt."""



class ReceiptLocations(str):
    """Freshly authorized locator text and its exact in-process result owners."""
    heading = ('〔本批已落盘工具结果定位〕按本轮工具调用编排。records 检索的 recorded_date 是原文入档日期。'
               '引用实际回执用 result_ref。定位及 function_call 请求不能证明业务成功。'
               '并列查询可能发生在另一工具回执落盘前，这里提供随后已落盘的结果定位。'
               '若没看到完整结果，先用 read_events(record_ref=...) 读取；检索回执本身不是新的历史用户事实，已完整展示的原文不要递归翻查其检索回执。写关注证据仍会重查来源和权限。')
    legend = 'excerpt_from 的两个数字分别是本清单的回执序号和节选序号，均从1开始。'

    @classmethod
    def from_rows(cls, records, rows, *, rolled=False, receipt_window_rolled=False):
        entries = tuple((record, json.dumps(row, ensure_ascii=False),
                         any('excerpt_from' in hint for hint in row.get('user_originals', ())))
                        for record, row in zip(records, rows, strict=True))
        text = cls(cls.section(entries, 0, len(entries)))
        text.entries = entries
        # The guard creates these dictionaries afresh for this request. Keep the
        # structured values so the official wire can encode them without parsing
        # its own JSON or stripping whitespace inside an excerpt.
        text._locator_rows = tuple(rows)
        text.rolled = rolled
        text.first_original = next((i for i, row in enumerate(rows, 1)
                                    if row.get('user_originals')), len(rows) + 1)
        # When only excerpt allocation has rolled, later receipts without
        # excerpts can still append after the last evidence-bearing group.
        text.latest_hint_end = (next((i for i in range(len(rows), 0, -1)
                                      if rows[i-1].get('user_originals')), 0)
                                if rolled and not receipt_window_rolled else 0)
        return text

    @classmethod
    def section(cls, entries, begin, end, *, compact_rows=None):
        group = entries[begin:end]
        legend = cls.legend if any(link for _, _, link in group) else ''
        if compact_rows is not None:
            heading = ('〔本批已落盘工具结果定位〕按本轮工具调用编排。records 检索的 recorded_date 是原文入档日期。'
                       '用 result_ref 核对实际回执。定位及 function_call 请求均不证明业务成功。'
                       '并列查询的回执可能稍后落盘。缺全文可用 read_events(record_ref=...)。'
                       '已见原文无需重查；检索回执不算用户事实。写关注仍复核来源和权限。'
                       if begin == 0 else f'〔本批已落盘工具结果定位·续〕从第 {begin + 1} 条接续，序号沿用。')
            encoded = json.dumps(compact_rows[begin:end], ensure_ascii=False, separators=(',', ':'))
            return heading + legend + '\n' + encoded
        heading = cls.heading if begin == 0 else f'〔本批已落盘工具结果定位·续〕本批清单从第 {begin + 1} 条回执接续，引用序号沿用此前各段。'
        return heading + legend + '\n[' + ', '.join(raw for _, raw, _ in group) + ']'


def fit_local_receipt_layout(messages, config, kind):
    """Prefer the ordinary complete locator when extra Chat section labels would strain the input budget."""
    from .batch_prompt import verified_local_qwen
    from .input_budget import hard_input_tokens
    from .media_budget import estimate_request_tokens
    if not verified_local_qwen(kind, getattr(config, 'base_url', ''), getattr(config, 'model', '')):
        return messages
    window, output = getattr(config, 'context_window', None), getattr(config, 'max_tokens', None)
    if type(window) is not int or type(output) is not int or output < 0:
        return messages
    hard = hard_input_tokens(window, output, config.base_url)
    locators = [(i, row['content']) for i, row in enumerate(messages) if isinstance(row, Mapping)
                and row.get('role') == 'system' and isinstance(row.get('content'), ReceiptLocations)]
    if len(locators) != 1:
        return messages
    index, locator = locators[0]
    if len(locator.entries) < 2:
        return messages
    # Pending calls, stale outputs and groups omitted by the current filter do not
    # create new rows. Count complete runs; the actual shaper still verifies owners.
    owners = {id(record) for record, _, _ in locator.entries}
    groups = 0
    previous_result = group_has_owner = False
    for item in messages[:index]:
        result = isinstance(item, Mapping) and item.get('type') == 'function_call_output'
        if result and not previous_result:
            group_has_owner = False
        if result and not group_has_owner and id(getattr(item.get('output'), 'record', None)) in owners:
            groups += 1
            group_has_owner = True
        previous_result = result
    if groups < 2:
        return messages
    # The local admission estimate already includes tool declarations. The measured
    # witness in Console applies only to official DeepSeek; avoid copying history.
    # The extra bytes cover the Chat user-turn framing, brackets and line breaks.
    header = locator.section(locator.entries, len(locator.entries)-1, len(locator.entries)).partition('\n')[0]
    if any(link for _, _, link in locator.entries) and not header.endswith(locator.legend):
        header += locator.legend
    added = (groups-1) * (len(header.encode('utf-8')) + 32)
    if estimate_request_tokens(messages, config) + added <= hard:
        return messages
    fallback = list(messages)
    fallback[index] = {**messages[index], 'content': str(locator)}
    return fallback


def _local_receipt_owners_match(messages):
    """Check current host-owned receipts against their actual visible call and output."""
    calls, results, owners = Counter(), Counter(), set()
    group = {}
    for message in messages:
        if not isinstance(message, Mapping):
            group.clear()
            continue
        role = message.get('role')
        if role != 'tool':
            group.clear()
        if role == 'assistant':
            declarations = message.get('tool_calls')
            for call in declarations if isinstance(declarations, (list, tuple)) else ():
                if not isinstance(call, Mapping) or not isinstance(call.get('id'), str):
                    continue
                cid = call['id']
                fn = call.get('function')
                calls[cid] += 1
                group[cid] = fn.get('name') if isinstance(fn, Mapping) else None
        elif role == 'tool':
            cid = message.get('tool_call_id')
            body = message.get('content')
            if isinstance(cid, str):
                results[cid] += 1
            if isinstance(body, CurrentBatchOutput) and hasattr(body, 'record'):
                if (not isinstance(cid, str) or not cid or cid != getattr(body, 'wire_call_id', None)
                        or group.get(cid) != body.source_reader
                        or getattr(body.record, 'name', None) != body.source_reader
                        or getattr(body.record, 'scope', None) != body.source_scope):
                    return False
                owners.add(cid)
    return all(calls[cid] == results[cid] == 1 for cid in owners)


def _place_receipt_locations(messages, *, local=False, rolling_tail=False):
    """Place each batch-local index section after its complete tool-result group.

    All entries were recomputed for this request. Never match by text, tool
    name or model-generated ID. If trimming removed an owner, use the ordinary
    tail layout. No historical hint is stored or replayed.
    """
    indexes = [(i, row['content']) for i, row in enumerate(messages)
               if isinstance(row, Mapping) and row.get('role') == 'system'
               and isinstance(row.get('content'), ReceiptLocations)]
    if len(indexes) != 1 or (local and not _local_receipt_owners_match(messages)):
        return messages
    index, locator = indexes[0]
    # Reallocating an excerpt or moving the 24-row window would rewrite an
    # earlier system message. The complete fresh index already exists at the tail.
    if rolling_tail and locator.rolled and not locator.latest_hint_end:
        return messages
    entries = locator.entries
    begin = end = 0
    groups = {}
    for i, row in enumerate(messages[:index]):
        if not isinstance(row, Mapping) or row.get('role') != 'tool':
            continue
        content = row.get('content')
        if (end < len(entries) and isinstance(content, CurrentBatchOutput)
                and getattr(content, 'record', None) is entries[end][0]):
            end += 1
        # In Chat there may be several outstanding calls in one assistant
        # envelope. A system row may only follow the complete tool group.
        following = messages[i + 1]
        if begin < end and (not isinstance(following, Mapping) or following.get('role') != 'tool'):
            groups[i] = (begin, end)
            begin = end
    if end != len(entries) or not groups:
        return messages
    omitted = getattr(locator, 'omitted_preview', None)
    if local and omitted and len(groups) > 1 and not any(link for _, _, link in entries):
        from .archive_preview_window import preserve_identical_preview
        hint_group = next((bounds for bounds in groups.values()
                           if bounds[0] < locator.latest_hint_end <= bounds[1]), None)
        if hint_group:
            entries = preserve_identical_preview(entries, omitted, hint_group, messages[:index])
    if local and not any(link for _, _, link in entries):
        from .parallel_archive_locations import omit_inline_first_lookup, share_parallel_originals
        entries = share_parallel_originals(locator, entries, groups, messages)
        entries = omit_inline_first_lookup(locator, entries, groups, messages)
    boundary = locator.latest_hint_end if rolling_tail and locator.rolled else 0
    if rolling_tail and not locator.rolled and len(groups) > 1:
        completed = iter(groups.values())
        _, first_end = next(completed)
        _, second_end = next(completed)
        # Combine a pair of catalogue results with just its immediate follow-up.
        # Wider initial indexes cost more to resend; waiting for an original
        # could move the index repeatedly across many empty searches.
        if (first_end == 2 and first_end < locator.first_original
                and all(record.name in ('kb_search', 'memory_search', 'memory_navigate')
                        for record, _, _ in entries[:first_end])):
            boundary = second_end
    compact_rows = locator._locator_rows if rolling_tail and not local else None
    if compact_rows:
        from .parallel_archive_locations import mixed_inline_catalogue
        reduced = mixed_inline_catalogue(locator, groups, messages)
        if reduced is not None:
            compact_rows = reduced
            # Newest receipts are allocated first. If the first group still has
            # a hint, later groups fit in full. Their sections can stay in place.
            boundary = 0
    if boundary:
        groups = {i: (0 if begin < boundary else begin, end)
                  for i, (begin, end) in groups.items() if end >= boundary}
    projected = []
    for i, row in enumerate(messages):
        if i != index:
            projected.append(row)
        if i in groups:
            begin, end = groups[i]
            content = locator.section(entries, begin, end, compact_rows=compact_rows)
            projected.append({'role': 'system', 'content': content})
    return projected

def project_sources(content, cid, anchors, *, record_previews=False):
    """Keep the first full excerpt visible. Only replace a second exact copy."""
    if not isinstance(content, SourceOutput) or not content.source_batch or not content.source_spans:
        return content
    from .retrieval_scope import valid_scope
    if not valid_scope(content.source_scope):
        return content
    pieces, pending = [], []
    last = 0
    previous_end = 0
    for span in content.source_spans:
        if not 0 <= previous_end <= span.start < span.end <= len(content) or span.source_offset < 0:
            return content
        previous_end = span.end
        source_key = span.key
        full_prefix = bool(source_key and source_key[0] == 'record_full_prefix')
        if source_key and source_key[0] in ('record_preview', 'record_full_prefix'):
            # An archive preview can point only to a live, uniquely paired read
            # in this request and in the same room as the original utterance.
            owner = getattr(content, 'record', None)
            if (not record_previews or not isinstance(content, CurrentBatchOutput)
                    or len(span.key) != 3 or span.key[1] != content.source_scope
                    or getattr(content, 'wire_call_id', None) != cid
                    or getattr(owner, 'name', None) != 'read_events'
                    or getattr(owner, 'scope', None) != content.source_scope
                    or getattr(getattr(owner, 'text', None), 'wire_spans', None) != content.source_spans):
                continue
            if full_prefix and (content != owner.text or span.source_offset or span.end - span.start != 240
                                or ('record:' + source_key[2], span.source_length) not in owner.text.complete_records):
                continue
            source_key = ('record_preview', *source_key[1:], span.source_length)
        text = content[span.start:span.end]
        key = (content.source_batch, content.source_scope, source_key)
        previous = anchors.get(key)
        if previous is None or full_prefix:
            pending.append((key, (cid, span.source_offset, text, full_prefix)))
            continue
        other_id, offset, original, anchored_full = previous
        # A QQ envelope can add a transport prefix to the exact speaker text.
        # Only reuse a complete original contained in the other view, at the
        # position attested by its reader. Never infer a match from a substring.
        left = max(offset, span.source_offset)
        right = min(offset + len(original), span.source_offset + len(text))
        length = right - left
        if length != min(len(original), len(text)) or length < 64:
            continue
        here, there = left - span.source_offset, left - offset
        if text[here:here+length] != original[there:there+length]:
            continue
        marker = f'〔本条完整原文见 {other_id}〕' if anchored_full else f'〔原文见 {other_id} 同一来源〕'
        if estimate_tokens(marker) >= estimate_tokens(text[here:here+length]):
            continue
        start = span.start + here
        pieces.extend((content[last:start], marker))
        last = start + length
    # Register only excerpts that actually went out verbatim in this request.
    for key, anchor in pending:
        anchors.setdefault(key, anchor)
    return ''.join((*pieces, content[last:])) if pieces else content


def compact_read_receipts(messages, *, full_repeats=True, group_locations=False, rolling_tail=False):
    """Retain every call and cite only an original still visible in this request."""
    messages = list(messages)
    if full_repeats or group_locations:
        messages = _place_receipt_locations(messages, local=group_locations, rolling_tail=rolling_tail)
    if not full_repeats and not any(isinstance(row, Mapping) and isinstance(row.get('content'), SourceOutput)
                                    and row['content'].source_spans for row in messages):
        return messages
    call_counts, result_counts = Counter(), Counter()
    # A later repeated identifier invalidates earlier references as well. Check
    # the complete request before shortening any receipt.
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        declarations = message.get('tool_calls')
        if message.get('role') == 'assistant' and isinstance(declarations, (list, tuple)):
            for call in declarations:
                if isinstance(call, Mapping) and isinstance(call.get('id'), str):
                    call_counts[call['id']] += 1
        if message.get('role') == 'tool' and isinstance(message.get('tool_call_id'), str):
            result_counts[message['tool_call_id']] += 1
    unique = {cid for cid, count in call_counts.items()
              if count == 1 and result_counts[cid] == 1
              and re.fullmatch(r'[A-Za-z0-9_-]+', cid)}
    calls = {}
    originals = {}
    anchors = {}
    result = []
    for message in messages:
        if not isinstance(message, Mapping):
            result.append(message)
            continue
        declarations = message.get('tool_calls')
        for call in (declarations if message.get('role') == 'assistant'
                     and isinstance(declarations, (list, tuple)) else ()):
            if not isinstance(call, Mapping):
                continue
            cid, function = call.get('id'), call.get('function')
            if not isinstance(cid, str) or cid not in unique or not isinstance(function, Mapping):
                continue
            # Duplicate IDs are not evidence for a new, unambiguous read.
            calls[cid] = None if cid in calls else (function.get('name'), function.get('arguments'))
        cid = message.get('tool_call_id')
        binding = calls.get(cid) if isinstance(cid, str) else None
        content = message.get('content')
        if (message.get('role') != 'tool' or not binding
                or not isinstance(binding[0], str) or (binding[0] not in READERS and binding[0] != 'profile_read')
                or not isinstance(binding[1], str) or not isinstance(content, str)
                or not content or '[tool failed]' in content):
            result.append(message)
            continue
        if isinstance(content, SourceOutput) and content.source_reader != binding[0]:
            result.append(message)
            continue
        typed_reader = binding[0] in ('profile_read', 'memory_search', 'read_events')
        # Evidence-bearing tools can return identical renderings from distinct
        # grants. Keep every heading and use only reader-attested source spans.
        whole_reader = full_repeats and binding[0] in READERS and not typed_reader
        key = (*binding, content, getattr(content, 'source_scope', ''))
        reference = originals.get(key) if whole_reader else None
        if reference is not None and reference != cid:
            replacement = ('[重复只读回执] 本次读取正文与本请求中此前 tool_call_id='
                           + reference + ' 的原始工具回执逐字相同，请沿用该正文。'
                           '这是新的读取，不表示执行了写入。')
            if estimate_tokens(replacement) < estimate_tokens(content):
                result.append({**message, 'content': replacement})
                continue
        projection = project_sources(content, cid, anchors, record_previews=group_locations)
        if projection is not content:
            message = {**message, 'content': projection}
        elif whole_reader and reference is None:
            originals[key] = cid
        result.append(message)
    return result
