"""Share only the identical user-original list of two current, sibling archive searches."""
import json
import re
from collections.abc import Mapping

from .archive_preview_window import _proof
from .original_evidence import OriginalText
from .receipt_projection import CurrentBatchOutput, _local_receipt_owners_match
from .retrieval_scope import valid_scope


def _search_terms(group, bodies, stores=('records', 'records')):
    if not isinstance(group, Mapping):
        return False
    calls = group.get('tool_calls', ())
    if group.get('role') != 'assistant' or not isinstance(calls, (list, tuple)) or len(calls) != 2:
        return False
    terms = []
    for call, body, store in zip(calls, bodies, stores):
        fn = call.get('function') if isinstance(call, Mapping) else None
        if not isinstance(fn, Mapping):
            return False
        arguments = fn.get('arguments')
        if (fn.get('name') != 'read_events' or call.get('id') != body.wire_call_id
                or not isinstance(arguments, str) or len(arguments) > 4096):
            return False
        try:
            args = json.loads(arguments)
        except (ValueError, RecursionError):
            return False
        if (not isinstance(args, dict) or args.get('store', 'events') != store or args.get('record_ref')
                or not isinstance(args.get('grep'), str) or not args['grep'].strip()):
            return False
        terms.append(args['grep'].strip().casefold())
    return (terms[0] != terms[1]) if stores[0] == stores[1] else (terms[0] == terms[1])


def _proofs(body, record, hints):
    if (not isinstance(body, CurrentBatchOutput) or not body.source_batch or getattr(body, 'record', None) is not record
            or body.source_reader != 'read_events' or body.source_scope != record.scope
            or not valid_scope(record.scope) or not isinstance(record.text, OriginalText)
            or body != record.text or body.source_spans != record.text.wire_spans):
        return None
    originals = record.text.originals
    if len(hints) != len(originals):
        return None
    proofs = {}
    for hint, (date, text, ref) in zip(hints, originals):
        if (not isinstance(ref, str) or not re.fullmatch(r'record:[0-9a-f]{64}:[1-9][0-9]*', ref)
                or ref in proofs or not text or text not in body
                or hint != {'recorded_date': date, 'excerpt': text[:240], 'source_ref': ref}
                or {scope for key, scope in record.text.record_scopes if key == ref} != {record.scope}):
            return None
        if (ref, len(text)) in record.text.complete_records:
            proofs[ref] = record.scope, body.source_batch, len(text)
        else:
            proof = _proof(body, record, hint)
            if not proof:
                return None
            proofs[ref] = proof
    return proofs


def share_parallel_originals(locator, entries, groups, messages):
    changed = None
    for last, (begin, end) in groups.items():
        if end - begin != 2 or last < 2:
            continue
        # The ninth-excerpt rule may already have moved one preview between
        # these two rows and a later authorized owner. Preserve that allocation.
        first, second = (locator._locator_rows[i] if entries[i] is locator.entries[i]
                         else json.loads(entries[i][1]) for i in range(begin, end))
        hints = second.get('user_originals', ())
        prefix = first.get('user_originals', ())
        if (first['name'] != 'read_events' or second['name'] != 'read_events'
                or first['result_ref'] == second['result_ref'] or not prefix or not 1 <= len(hints) <= 4
                or any(set(h) != {'recorded_date', 'excerpt', 'source_ref'} for h in hints)
                or len({h['source_ref'] for h in hints}) != len(hints)):
            continue
        bodies = [messages[i].get('content') for i in (last - 1, last)]
        if not all(isinstance(body, CurrentBatchOutput) for body in bodies):
            continue
        if not _search_terms(messages[last - 2], bodies):
            continue
        records = [entries[i][0] for i in (begin, begin + 1)]
        # The ninth-slot rule can leave only the beginning of an otherwise
        # identical list. Expand that case only after proving the full list.
        # An exact-read header can also put the same excerpts in a different
        # order. Retain that order explicitly for the second locator.
        same_order = prefix == hints[:len(prefix)]
        anchor_hints = hints if same_order else prefix
        if not same_order and (len(prefix) != len(hints) or any(h not in prefix for h in hints)):
            continue
        proof = _proofs(bodies[0], records[0], anchor_hints)
        if not proof or proof != _proofs(bodies[1], records[1], hints):
            continue
        anchor = dict(first, user_originals=anchor_hints)
        other = dict(second)
        del other['user_originals']
        other['user_originals_same_as'] = first['result_ref']
        if not same_order:
            other['user_originals_positions_1based'] = [prefix.index(h) + 1 for h in hints]
        pair = [(r, json.dumps(row, ensure_ascii=False), False) for r, row in zip(records, (anchor, other))]
        if sum(len(raw) for _, raw, _ in pair) >= sum(len(raw) for _, raw, _ in entries[begin:end]):
            continue
        if changed is None:
            changed = list(entries)
        changed[begin:end] = pair
    return entries if changed is None else changed


def complete_inline_listing(visible, body, record, hints):
    """The authorized receipt already renders each short source with its date and ID."""
    if not isinstance(body, CurrentBatchOutput) or body.source_spans:
        return False
    if not isinstance(record.text, OriginalText) or not 2 <= len(record.text.originals) <= 4:
        return False
    sources = []
    for day, text, ref in record.text.originals:
        if not 0 < len(text) < 96 or (ref, len(text)) not in record.text.complete_records:
            return False
        sources.append(dict(recorded_date=day, source_ref=ref, excerpt=text))
    if hints != sources[:len(hints)] or not _proofs(body, record, sources) or not visible(record.scope):
        return False
    # The complete listing with an authenticated time and source is present in
    # this very request. Short listed originals have no span to be substituted
    # later in the local prompt projection.
    stamped = dict(record.text.record_times)
    if len(stamped) != len(record.text.record_times):
        return False
    for item in sources:
        ref = item['source_ref']
        stamp = stamped.get(ref)
        if (not isinstance(stamp, str) or stamp[:10] != item['recorded_date']
                or f'[{ref}] record_ref="{ref[7:]}"\n{stamp}｜user｜message｜' not in body):
            return False
    return True


def omit_inline_first_lookup(locator, entries, groups, messages):
    """A first short history listing already supplies scoped, dated text inline.

    Leave its result_ref in place. The simultaneous event lookup is a separate
    result. No expired excerpt, position or source_ref is appended to the wire.
    """
    visible = getattr(locator, 'current_inline_scope', None)
    if not callable(visible):
        return entries
    last, bounds = next(iter(groups.items()))
    if bounds != (0, 2) or last < 2 or entries[0] is not locator.entries[0]:
        return entries
    first = locator._locator_rows[0]
    hints = first.get('user_originals')
    if not hints or locator._locator_rows[1].get('user_originals'):
        return entries
    body = messages[last - 1].get('content')
    second = messages[last].get('content')
    if (not isinstance(body, CurrentBatchOutput) or body.source_spans
            or not isinstance(second, CurrentBatchOutput) or getattr(second, 'record', None) is not entries[1][0]
            or body.source_scope != second.source_scope or body.source_batch != second.source_batch):
        return entries
    if not _search_terms(messages[last - 2], (body, second), ('records', 'events')):
        return entries
    record, _, _ = entries[0]
    if not complete_inline_listing(visible, body, record, hints):
        return entries
    reduced = dict(first)
    del reduced['user_originals']
    changed = list(entries)
    changed[0] = record, json.dumps(reduced, ensure_ascii=False), False
    return changed


def mixed_inline_catalogue(locator, groups, messages):
    """Return the official compact rows when one mixed search already shows all short originals."""
    rows = locator._locator_rows
    last, (begin, end) = next(iter(groups.items()))
    if begin or end not in (2, 3) or last < end or any(link for _, _, link in locator.entries):
        return None
    names = [row['name'] for row in rows[:end]]
    if (names.count('read_events') != 1 or len(set(names)) != end
            or not set(names) <= {'read_events', 'kb_search', 'memory_search', 'memory_navigate'}):
        return None
    call = messages[last-end]
    if (not isinstance(call, Mapping) or call.get('role') != 'assistant'
            or not isinstance(call.get('tool_calls'), (list, tuple)) or len(call['tool_calls']) != end):
        return None
    slot = names.index('read_events')
    hints = rows[slot].get('user_originals')
    if not hints or any(i != slot and row.get('user_originals') for i, row in enumerate(rows[:end])):
        return None
    visible = getattr(locator, 'current_inline_scope', None)
    if not callable(visible):
        return None
    bodies = [r.get('content') for r in messages[last-end+1:last+1]]
    record = locator.entries[slot][0]
    body = bodies[slot]
    if not complete_inline_listing(visible, body, record, hints):
        return None
    if any(not isinstance(b, CurrentBatchOutput) or getattr(b, 'record', None) is not locator.entries[i][0]
           or b.source_scope != body.source_scope or b.source_batch != body.source_batch
           for i, b in enumerate(bodies)):
        return None
    # The original must occur under its own authenticated header; an occurrence
    # elsewhere in a combined page cannot stand in for that record's body.
    stamps = dict(record.text.record_times)
    for _, text, ref in record.text.originals:
        header = f'[{ref}] record_ref="{ref[7:]}"\n{stamps[ref]}｜user｜message｜'
        if body.count(header) != 1:
            return None
        block = body.partition(header)[2].partition('\n[record:')[0]
        if '\n' + text + '\n〔此条正文完整' not in block:
            return None
    if not _local_receipt_owners_match(messages):
        return None
    reduced = dict(rows[slot])
    del reduced['user_originals']
    return (*rows[:slot], reduced, *rows[slot+1:])
