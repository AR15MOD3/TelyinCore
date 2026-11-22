"""A portrait's authored conclusion and its review-time source events are distinct."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re

from ..events import EventEnvelope
from ..reminder_provenance import fingerprint
from ..session_view import event_scope
from .context import actor_text, message_time
from ._frozen.contracts import Refused

_ROLES = {'primary': '核对原话', 'selected': '共同核对的原话', 'context': '复核时的上下文'}
_LIMIT = 14  # capture() admits at most two current messages and twelve historical ones.
_UNAVAILABLE = '画像原话来源当前无法核实。请重新读取当前可见画像。'


@dataclass(frozen=True)
class Source:
    cursor: int
    sha256: str
    role: str
    recorded_at: str


def restored(raw) -> tuple[Source, ...]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= _LIMIT:
        return ()
    result = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {'cursor', 'sha256', 'role', 'recorded_at'}:
            return ()
        row = Source(**item)
        if (type(row.cursor) is not int or not 0 < row.cursor < 2**63 or row.cursor in seen
                or not isinstance(row.sha256, str) or not re.fullmatch('[0-9a-f]{64}', row.sha256)
                or not isinstance(row.role, str) or row.role not in _ROLES
                or not isinstance(row.recorded_at, str) or len(row.recorded_at) > 64):
            return ()
        result.append(row)
        seen.add(row.cursor)
    return tuple(result) if sum(row.role == 'primary' for row in result) == 1 else ()


def captured(turn, author, primary, get, *, include_context=True) -> tuple[Source, ...]:
    """Bind only the admitted author's source material to exact event bytes."""
    classified = [(m, 'primary' if m.message_id == primary else 'selected') for m in turn.messages]
    if include_context:
        classified.extend((m, 'context') for m in turn.prior_messages)
    result = []
    for message, role in classified:
        if message.author != author:
            continue
        event = get(int(message.message_id))
        if (not isinstance(event, EventEnvelope) or event.origin != 'external' or event.type != 'message'
                or event.cursor != int(message.message_id) or not message.trusted or message.origin != 'external' or event.sender_key != author
                or event_scope(event) != turn.scope or actor_text(event) != message.text
                or message_time(event) != message.recorded_at):
            raise Refused('portrait_context_changed')
        result.append(Source(event.cursor, fingerprint(event), role, event.ts if message.recorded_at else ''))
    if not restored([asdict(ref) for ref in result]):
        raise Refused('portrait_context_changed')
    return tuple(sorted(result, key=lambda ref: ref.cursor))


def reference(row):
    """Return an opaque handle for the particular conclusion and its review sources."""
    refs = [(ref.cursor, ref.sha256, ref.role, ref.recorded_at) for ref in row.portrait_sources]
    data = [row.ts, row.source, row.text, row.memory_kind, refs]
    return 'portrait-' + hashlib.sha256(json.dumps(data, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def source_page(conclusion, visibility, get, *, offset, snapshot, evidence_ref):
    """Release no partial or unbound original when the scope, event or one qualifier is unavailable."""
    if visibility is None or get is None:
        raise PermissionError(_UNAVAILABLE)
    visibility.require()
    room, _, sender = conclusion.source.partition('|sender=')
    author = sender if room.startswith('group:') else room.removeprefix('private:')
    from .. import memory_receipt
    from ..original_evidence import PortraitSourceText, event_span, source
    from ..source_attribution import project
    introduction = ('画像结论：' + conclusion.text
                    + '\n以下是写入时核对的消息。源消息记录时间可能是接收时间，事实发生时间以正文为准。')
    parts = [introduction]
    length = len(introduction)
    excerpts = []
    for ref in conclusion.portrait_sources:
        event = get(ref.cursor)
        if (not isinstance(event, EventEnvelope) or event.origin != 'external' or event.type != 'message'
                or event.cursor != ref.cursor or not visibility.event(event) or event_scope(event) != room or event.sender_key != author
                or fingerprint(event) != ref.sha256 or (ref.recorded_at and ref.recorded_at != event.ts)):
            raise PermissionError(_UNAVAILABLE)
        when = ref.recorded_at or '未记录'
        locator = f'event:{ref.cursor}'
        heading = (f'〔{_ROLES[ref.role]} · 源消息记录时间：{when} · {locator}'
                   f' · 渠道 {event.source} · 发言者 {author} · 外部消息〕')
        original = actor_text(event)
        start = length + len(heading) + 2
        _, attribution = project(event, visibility, locator)
        excerpts.append((length + 1, start, original, heading, ref.recorded_at, locator, attribution, event))
        parts.extend(('\n', heading, '\n', original))
        length = start + len(original)

    page = memory_receipt.profile_page(''.join(parts), offset=offset, snapshot=snapshot,
                                       evidence_ref=evidence_ref)
    end = min(length, offset + memory_receipt.PROFILE_PAGE_CHARS)
    originals, times, sources, continued = [], [], [], []
    body_at = page.index('\n') + 1
    spans = []
    for heading_at, start, original, heading, stamp, locator, attribution, event in excerpts:
        left, right = max(offset, start), min(end, start + len(original))
        if left >= right:
            continue
        if offset > heading_at:
            continued.append(heading)
        # A page or the general 2048-character receipt limit can cut off a
        # negation just before the visible words. Keep such text readable,
        # while only whole originals can serve as semantic evidence.
        if left == start and right == start + len(original) and len(original) <= 2048:
            originals.extend(source(stamp, original, locator))
            spans.extend(event_span(event, event.text, original, body_at + left - offset))
        if stamp:
            times.append((locator, stamp))
        sources.append(attribution)
    if continued:
        page += '\n本页续引：' + '\n'.join(continued)
    return PortraitSourceText(page, originals, event_sources=sources, event_times=times,
                              wire_spans=spans, source_scope=room, single_source=len(conclusion.portrait_sources) == 1)


def selected(refs):
    return tuple(ref for ref in refs if ref.role != 'context')
