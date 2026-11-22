"""Restore only paired quotation glyphs inside a rejected, dated archival quotation."""
import json
import re

PAIRS = {'「': '」', '『': '』', '“': '”', '‘': '’'}
CLOSE = frozenset(PAIRS.values())
FOLD = str.maketrans({**dict.fromkeys(PAIRS, '“'), **dict.fromkeys(CLOSE, '”')})
RECORD = re.compile(r'record:[0-9a-f]{64}:[1-9][0-9]*')
SOURCE_LABEL = re.compile(r'《[^《》\n]{1,100}》|(?<![A-Za-z0-9])[A-Z]{1,12}-[0-9]{2,12}(?![A-Za-z0-9])')


def balanced(text):
    stack = []
    for character in text:
        if character in PAIRS:
            stack.append(PAIRS[character])
        elif character in CLOSE and (not stack or stack.pop() != character):
            return False
    return not stack


def restore(draft, issue, visible, complete):
    """Return canonical prose and its grant scope only for one unambiguous complete record."""
    day, quoted, target = issue
    if not day or not any(mark in quoted for mark in PAIRS) or not balanced(quoted):
        return None
    start = draft.find(quoted)
    end = start + len(quoted)
    if (start < 1 or end >= len(draft) or draft.find(quoted, start + 1) >= 0
            or {**PAIRS, '"': '"'}.get(draft[start-1]) != draft[end]):
        return None

    needle = quoted.translate(FOLD)
    matches = set()
    for date, original, ref, _, scope in visible():
        if date != day:
            continue
        flat = original.translate(FOLD)
        offset = flat.find(needle)
        if offset < 0:
            continue
        if flat.find(needle, offset + 1) >= 0:
            return None
        raw = original[offset:offset + len(quoted)]
        matches.add((raw, ref, scope))
        if len(matches) > 1:
            return None
    if not matches:
        return None
    canonical, ref, scope = matches.pop()
    if (not isinstance(ref, str) or not RECORD.fullmatch(ref) or (target and target != ref)
            or canonical == quoted or not balanced(canonical)):
        return None
    # If the attribution explicitly names a document, its identifier or title
    # must agree with the original's heading. A cross-reference in the body
    # cannot qualify a different document as the author of this quotation.
    attribution = re.split(r'[。；;！？\n]', draft[:start - 1])[-1]
    labels = SOURCE_LABEL.findall(attribution)
    if any(label != ref for label in RECORD.findall(attribution)):
        return None
    for date, original, source, _, origin in complete():
        if (date, source, origin) != (day, ref, scope) or canonical not in original:
            continue
        heading = re.split(r'[。；;！？\n]', original, maxsplit=1)[0]
        if all(label in SOURCE_LABEL.findall(heading) for label in labels):
            return draft[:start] + canonical + draft[end:], scope
    return None


def replace_text_argument(payload, before, after):
    """Edit one top-level string in valid JSON, preserving every other argument byte."""
    decoder = json.JSONDecoder()
    space = re.compile(r'[ \r\n\t]*')

    def skip(pos):
        return space.match(payload, pos).end()

    pos = skip(0) + 1  # apply() has already decoded and checked the root object.
    seen = set()
    span = None
    while payload[skip(pos)] != '}':
        key, end = decoder.raw_decode(payload, skip(pos))
        if key in seen:
            return None
        seen.add(key)
        start = skip(skip(end) + 1)
        value, end = decoder.raw_decode(payload, start)
        if key == 'text' and value == before:
            span = (start, end)
        pos = skip(end)
        if payload[pos] != ',':
            break
        pos += 1
    if span is None:
        return None
    start, end = span
    # Keep literal-vs-escaped spelling of the existing string, including all
    # newline/Unicode escapes. Only the two known differing code points change.
    encoded = payload[start:end]
    chunks = []
    i = 1
    for old, new in zip(before, after, strict=True):
        if encoded[i] == '\\':
            length = (12 if ord(old) > 0xffff else 6) if encoded[i+1] == 'u' else 2
        else:
            length = 1
        unit = encoded[i:i + length]
        if old != new:
            unit = new if length == 1 else json.dumps(new, ensure_ascii=True)[1:-1]
        chunks.append(unit)
        i += length
    if i != len(encoded) - 1:
        return None
    return payload[:start+1] + ''.join(chunks) + payload[end-1:]
