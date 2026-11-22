"""Read a character window from an already validated JSON text value.

The byte span is derived during snapshot ingestion. Reads use the same private
SQLite body, never reopen an unvalidated source. Escape decoding uses stdlib
JSON; only chunk boundaries are handled here. Cold/backward offset seeking
remains linear; an optional bounded cursor avoids replaying warm continuations.
"""
from array import array
from functools import lru_cache
import codecs
import json
import re


_STRUCTURE = re.compile(r'["{}\[\]]')
_SCALAR_END = re.compile(r'[,}\]]')
_SPACE = re.compile(r'\s*', re.ASCII)


def _string_end(wire, opening):
    # The source has already been validated. The native string decoder locates
    # an actual closing quote or the artificial end of a <=4Ki character piece.
    # Its decoded value is discarded immediately, never retained as a body.
    position = opening + 1
    quote = wire.find('"', position)
    if wire.find('\\', position, quote) < 0:
        return quote + 1
    while True:
        fragment = wire[position:position + 4096]
        try:
            end = json.decoder.scanstring(fragment + '"', 0)[1]
        except json.JSONDecodeError:
            # Only a cut escape can be incomplete in this validated string.
            # Retry before that escape, leaving the whole token for the next
            # piece (including a lone backslash or partial Unicode escape).
            cut = fragment.rfind('\\')
            if cut < 0:
                raise
            fragment = fragment[:cut]
            end = json.decoder.scanstring(fragment + '"', 0)[1]
        if end <= len(fragment):
            return position + end
        position += len(fragment)


def _value_end(wire, position):
    if wire[position] == '"':
        return _string_end(wire, position)
    if wire[position] not in '[{':
        return _SCALAR_END.search(wire, position).start()
    depth = 1
    position += 1
    while depth:
        token = _STRUCTURE.search(wire, position)
        position = token.end()
        char = token[0]
        if char == '"':
            position = _string_end(wire, token.start())
        elif char in '[{':
            depth += 1
        else:
            depth -= 1
    return position


def span(wire, *, first=False):
    """Return UTF-8 bounds of the last top-level text string's contents.

    Canonical event encoders guarantee unique keys and may request the first
    occurrence, avoiding a second parse of unrelated trailing metadata. Raw
    historical inputs retain last-key semantics by default.
    """
    decoder = json.JSONDecoder()
    position = _SPACE.match(wire).end() + 1  # validated object opening
    small = len(wire) <= 8192
    bounds = None
    while True:
        while wire[position] in ' \t\r\n': position += 1
        if wire[position] == '}': break
        if small:
            key, position = decoder.raw_decode(wire, position)
        else:
            end = _string_end(wire, position)
            # Four decoded ASCII characters occupy at most four JSON Unicode
            # escapes. A huge unrelated key need never be decoded or copied.
            key = json.loads(wire[position:end]) if end - position <= 26 else None
            position = end
        while wire[position] in ' \t\r\n': position += 1
        position += 1  # colon
        while wire[position] in ' \t\r\n': position += 1
        start = position
        # Preserve the fast C decoder for bounded small records. Large values
        # are only traversed lexically: no second body/metadata allocation.
        position = decoder.raw_decode(wire, position)[1] if small else _value_end(wire, position)
        if key == 'text':
            bounds = start + 1, position - 1
            if first:
                break
        while wire[position] in ' \t\r\n': position += 1
        if wire[position] == '}': break
        position += 1  # comma
    if bounds is None: raise ValueError('validated record has no text')
    start, stop = bounds
    if wire.isascii():
        return start, stop - start
    # Offsets refer to the original UTF-8 bytes, not decoded character counts.
    # Bound the temporary slices/encodings even for a large literal-Unicode
    # body or prefix. Initial JSON parsing still scales with individual values.
    def byte_count(first, last):
        return sum(len(wire[pos:min(pos + 4096, last)].encode('utf-8'))
                   for pos in range(first, last, 4096))
    return byte_count(0, start), byte_count(start, stop)


def _unescaped(raw, position):
    start = position
    while start and raw[start - 1] == '\\': start -= 1
    return (position - start) % 2 == 0


def _boundary(raw, final):
    """Only the suffix can be incomplete in an already validated JSON value.

    Locate its last backslash in C instead of interpreting every escape in
    Python. An incomplete escape can follow a high surrogate, so trim that
    preceding token too; stdlib JSON must see both halves of any pair at once.
    """
    end = len(raw)
    last = raw.rfind('\\')
    if last >= 0 and _unescaped(raw, last):
        if last + 1 == end or (raw[last + 1] == 'u' and last + 6 > end):
            end = last
    if (not final and end >= 6 and raw[end - 6:end - 4] == '\\u'
            and _unescaped(raw, end - 6)
            and 0xD800 <= int(raw[end - 4:end], 16) <= 0xDBFF):
        end -= 6
    return end


def chunks(source, start, size, *, block_size=4096, _positioned=False):
    """Yield bounded decoded pieces of the validated JSON string."""
    source.seek(start)
    decoder = codecs.getincrementaldecoder('utf-8')()
    pending = ''
    remaining = size
    position = start
    while remaining:
        block = source.read(min(block_size, remaining))
        if not block: raise ValueError('incomplete cached JSON string')
        remaining -= len(block)
        pending += decoder.decode(block, final=not remaining)
        end = _boundary(pending, not remaining)
        if not end: continue
        text = json.loads('"' + pending[:end] + '"')
        pending = pending[end:]
        if _positioned:
            # Exclude both pending JSON escapes and incomplete UTF-8 bytes.
            # This is a fresh-decoder boundary, including surrogate pairs.
            following = start + size - remaining - len(pending.encode('utf-8')) - len(decoder.getstate()[0])
            yield text, position, following
            position = following
        else:
            yield text
    if pending: raise ValueError('incomplete cached JSON escape')


def window(source, start, size, offset, count, *, block_size=4096, cursor=None):
    """Decode at most offset+count characters, without a full-body allocation."""
    if count <= 0: return ''
    if cursor is not None:
        return cursor.read(source, start, size, offset, count, block_size=block_size)
    output = []
    for text in chunks(source, start, size, block_size=block_size):
        if offset >= len(text):
            offset -= len(text)
            continue
        piece = text[offset:offset + count]
        output.append(piece)
        count -= len(piece)
        offset = 0
        if not count: break
    return ''.join(output)


class WindowCursor:
    """Two decoder boundaries for one immutable body, never text or a lease.

    Keep the first and last output chunk starts. Sequential pages can resume
    near their offset, and repeating the last page can reuse its first point.
    Earlier random reads fall back to the beginning. The owner must isolate
    cursors by the validated body's identity and recheck access on every read.
    """
    __slots__ = ('points',)

    def __init__(self):
        self.points = ()

    def read(self, source, start, size, offset, count, *, block_size=4096):
        if count <= 0:return ''
        char_pos, byte_pos = max(((0, 0), *(p for p in self.points if p[0] <= offset)))
        skip = offset - char_pos
        output = []
        first = last = None
        for text, byte_start, _ in chunks(source, start + byte_pos, size - byte_pos,
                                          block_size=block_size, _positioned=True):
            point = (char_pos, byte_start - start)
            char_pos += len(text)
            if skip >= len(text):
                skip -= len(text)
                continue
            if first is None:first = point
            last = point
            piece = text[skip:skip + count]
            output.append(piece)
            count -= len(piece)
            skip = 0
            if not count:break
        result = ''.join(output)
        # An incomplete/failed read must not publish provisional boundaries.
        if first is not None:self.points = tuple(dict.fromkeys((first, last)))
        return result


class FoldedMatcher:
    """Reusable exact matcher; long queries use KMP instead of recopying tails."""
    def __init__(self, folded):
        self.folded = folded
        self.failure = None
        if len(folded) > 65536:
            # Once per query, reused across records. A packed table avoids one
            # Python integer object per query character. It scales with query
            # input, never with historical bodies.
            failure = array('Q', [0]) * len(folded)
            matched = 0
            for i in range(1, len(folded)):
                while matched and folded[i] != folded[matched]:
                    matched = failure[matched - 1]
                if folded[i] == folded[matched]: matched += 1
                failure[i] = matched
            self.failure = failure

    def contains(self, source, start, size, *, block_size=4096):
        folded = self.folded
        if not folded: return True
        # Casefold is context independent, unlike lower's final sigma.
        # Per-piece folding therefore preserves expansions and whole-string
        # matching. Up to64Ki query characters keep a bounded native substring
        # overlap; Python KMP is much slower at those sizes. Larger queries
        # retain the packed table rather than repeatedly copying a long tail.
        tail = ''
        overlap = len(folded) - 1
        matched = 0
        for text in chunks(source, start, size, block_size=block_size):
            if self.failure is not None:
                for char in text.casefold():
                    while matched and char != folded[matched]:
                        matched = self.failure[matched - 1]
                    if char == folded[matched]: matched += 1
                    if matched == len(folded): return True
            else:
                candidate = tail + text.casefold()
                if folded in candidate: return True
                tail = candidate[-overlap:] if overlap else ''
        return False


def contains(source, start, size, folded, *, block_size=4096):
    return FoldedMatcher(folded).contains(source, start, size, block_size=block_size)


class _LowerState:
    __slots__ = ('pattern', 'tail', 'matched', 'found')

    def __init__(self, pattern):
        self.pattern = pattern
        self.tail = ''
        self.matched = 0
        self.found = not pattern.folded

    def copy(self):
        other = _LowerState(self.pattern)
        other.tail, other.matched, other.found = self.tail, self.matched, self.found
        return other

    def feed(self, text):
        if self.found:
            return
        needle, failure = self.pattern.folded, self.pattern.failure
        if failure is None:
            candidate = self.tail + text
            self.found = needle in candidate
            overlap = len(needle) - 1
            self.tail = candidate[-overlap:] if overlap else ''
        else:
            for char in text:
                while self.matched and char != needle[self.matched]:
                    self.matched = failure[self.matched - 1]
                if char == needle[self.matched]:
                    self.matched += 1
                if self.matched == len(needle):
                    self.found = True
                    return


@lru_cache(maxsize=1024)
def _case_kind(char):
    # Use this interpreter's Unicode data, including characters which are
    # both cased and case-ignorable. No copied property table can go stale.
    # 1: cased/non-ignorable; 0: ignorable; -1: uncased boundary.
    if ('AΣ' + char).lower()[1] == 'σ':
        return 1
    return 0 if ('AΣ' + char + 'A').lower()[1] == 'σ' else -1


class LowerMatcher(FoldedMatcher):
    """Exact whole-string lower() matching with bounded historical-body memory.

    Unicode's language-independent contextual lowercase rule is Final_Sigma.
    An undecided trailing sigma keeps two matcher states, never its following
    ignorable run. The next non-ignorable character (or EOF) selects a state.
    Ordinary chunks retain the C substring/lower paths. Queries up to 64Ki
    characters use bounded overlap; larger queries share one packed KMP table
    between both states and subsequent records, avoiding unbounded re-copying.
    """
    def __init__(self, lowered):
        super().__init__(lowered)

    def contains(self, source, start, size, *, block_size=4096):
        state = _LowerState(self)
        if state.found:
            return True
        cased = False
        pending = None
        for text in chunks(source, start, size, block_size=block_size):
            if not text:
                continue
            if pending is not None:
                kind = _case_kind(text[0])
                if kind == 0:
                    if ('AΣ' + text).lower()[1] == 'σ':
                        kind = 1
                    elif ('AΣ' + text + 'A').lower()[1] != 'σ':
                        kind = -1
                if kind == 0:  # This entire piece is case-ignorable.
                    lowered = text.lower()
                    for candidate in pending:
                        candidate.feed(lowered)
                    if all(candidate.found for candidate in pending):
                        return True
                    continue
                state = pending[1 if kind == 1 else 0]
                pending = None
                if state.found:
                    return True
            prefix = 'A' if cased else ' '
            if 'Σ' not in text:
                state.feed(text.lower())
                if state.found:
                    return True
                kind = _case_kind(text[-1])
                cased = (prefix + text + 'Σ').lower()[-1] == 'ς' if kind == 0 else kind == 1
                continue
            terminal = (prefix + text).lower()[1:]
            continued = (prefix + text + 'Σ').lower()
            cased = continued[-1] == 'ς'
            continued = continued[1:-1]
            if terminal == continued:
                state.feed(terminal)
                if state.found:
                    return True
            else:
                alternative = state.copy()
                state.feed(terminal)
                alternative.feed(continued)
                if state.found and alternative.found:
                    return True
                pending = state, alternative
        return (pending[0] if pending is not None else state).found
