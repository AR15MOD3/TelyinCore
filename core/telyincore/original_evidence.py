"""Ephemeral typed original metadata; never parsed from model/tool prose.

Only host readers construct this string after their ordinary source/permission
checks. Its visible string is unchanged; metadata survives local composition,
not JSON serialization or persisted history. It grants no future read access.
"""
from dataclasses import dataclass, replace
from datetime import datetime
from itertools import islice


@dataclass(frozen=True)
class OriginalSpan:
    """Character coordinates in this receipt and in one host-authorized source."""
    key: tuple
    start: int
    end: int
    source_offset: int = 0
    source_length: int = 0

    def shifted(self, by):
        return replace(self, start=self.start + by, end=self.end + by)


def event_span(row, raw, visible, start):
    """Only whole attributed speech or the complete wire body can be referenced."""
    from .session_view import event_scope
    room = event_scope(row)
    if (row.origin != 'external' or row.type != 'message' or not room or not row.sender_key
            or not visible or len(raw) > 2000 or not raw.endswith(visible)):
        return ()
    digest = getattr(row, '_snapshot_wire_digest', None)
    if not isinstance(digest, bytes) or len(digest) != 32:
        return ()
    key = ('event', room, row.cursor, row.source, row.sender_key, row.ts, digest)
    return (OriginalSpan(key, start, start + len(visible), len(raw) - len(visible)),)


def _spans(parts, separator=''):
    offset = 0
    for part in parts:
        if isinstance(part, OriginalText):
            for span in part.wire_spans:
                yield span.shifted(offset)
        offset += len(part) + len(separator)


def source(when, text, reference):
    if not isinstance(when, str) or len(when) > 64:
        return ()
    if not isinstance(text, str) or not isinstance(reference, str):
        return ()
    try:
        moment = datetime.fromisoformat(when.replace('Z', '+00:00'))
    except (ValueError, OverflowError):
        return ()
    days = [moment.date().isoformat()]
    if moment.tzinfo is not None:
        try:
            local = moment.astimezone().date().isoformat()
        except (OSError, OverflowError, ValueError):
            # Some hosts cannot convert old instants to local time.
            pass
        else:
            if local != days[0]:
                days.append(local)
    return tuple((day, text[:2048], reference) for day in days)


class OriginalText(str):
    def __new__(cls, value, originals=(), *, event_sources=(), event_times=(), document_reports=(), wire_spans=(), record_scopes=(), complete_records=(), record_times=()):
        obj = super().__new__(cls, value)
        obj.originals = tuple(islice(originals, 32))
        obj.event_sources = tuple(islice(event_sources, 32))
        obj.event_times = tuple(islice(event_times, 32))
        obj.document_reports = tuple(islice(document_reports, 16))
        obj.wire_spans = tuple(islice(wire_spans, 32))
        obj.record_times = tuple(islice(record_times, 32))
        obj.record_scopes = tuple(islice(record_scopes, 32))
        obj.complete_records = tuple(islice(complete_records, 32))
        return obj

    def __add__(self, other):
        if not isinstance(other, str):return NotImplemented
        return OriginalText.join((self, other), separator='')

    def __radd__(self, other):
        if not isinstance(other, str):return NotImplemented
        return OriginalText.join((other, self), separator='')

    @classmethod
    def join(cls, parts, separator='\n'):
        parts = list(parts)
        typed = [p for p in parts if isinstance(p, OriginalText)]
        return cls(separator.join(parts), (s for p in typed for s in p.originals),
                   event_sources=(s for p in typed for s in p.event_sources),
                   event_times=(s for p in typed for s in p.event_times),
                   document_reports=(s for p in typed for s in p.document_reports),
                   wire_spans=_spans(parts, separator),
                   record_scopes=(s for p in typed for s in p.record_scopes),
                   record_times=(item for p in typed for item in p.record_times),
                   complete_records=(s for p in typed for s in getattr(p, 'complete_records', ())))


class PortraitSourceText(OriginalText):
    """Visible original excerpts on an explicitly authorized portrait source page."""

    def __new__(cls, value, originals=(), *, source_scope, single_source=False, event_sources=(), event_times=(), wire_spans=()):
        page = super().__new__(cls, value, originals, event_sources=event_sources, event_times=event_times, wire_spans=wire_spans)
        page.source_scope = source_scope
        page.single_source = single_source
        return page

    def __getnewargs_ex__(self):
        # Record identity uses dataclasses.asdict/deepcopy before JSON encoding.
        return (str(self),), {'source_scope': self.source_scope}


def is_original_receipt(name, text):
    if isinstance(text, PortraitSourceText):
        return name == 'profile_read'
    return name in ('read_events', 'memory_search') and isinstance(text, OriginalText)
