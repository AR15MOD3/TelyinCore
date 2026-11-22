"""Append-owned current evidence with bounded same-author historical context."""
import hashlib
from datetime import datetime
from dataclasses import asdict
from ..events import EventStore,EventEnvelope,RangeQuery
from ..session_view import batch_scope,event_scope
from ..portrait_shadow.evidence import Turn,Message
from ._frozen.contracts import MAX_SOURCE_CHARS,canonical,Refused
def message_time(event):
    try:
        value=datetime.fromisoformat(event.ts.replace('Z','+00:00'))
        return value.astimezone().isoformat() if value.tzinfo is not None else ''
    except (TypeError, ValueError, AttributeError):
        return ''

def actor_text(event):
    meta = event.meta
    if (event.source != 'qq' or event.blobs or not isinstance(meta, dict)
            or meta.get('segment_types') != ['text'] or meta.get('has_user_text') is not True):
        return event.text
    text = meta.get('plain_text')
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_SOURCE_CHARS:
        return event.text
    header, sep, attributed = event.text.partition('] ')
    if not sep:
        return event.text
    room = meta.get('conversation')
    mid = meta.get('message_id')
    expected = None
    if isinstance(room, str) and isinstance(mid, str):
        if room.startswith('private:'):
            expected = f"[QQ私聊:{room[8:]}·消息#{mid}"
        elif room.startswith('group:'):
            expected = f"[QQ群{room[6:]}·消息#{mid}"
    if header not in ('[QQ', expected):
        return event.text
    speaker, sep, body = attributed.partition('：')
    return text if speaker.strip() and sep and body == text else event.text


def capture(console, *, bridge=None, max_sources=2):
    if console.signal is not None and console.signal.is_set():raise Refused('portrait_cancelled')
    batch=tuple(console._active_batch or ())
    from ..channel_admission import BatchIdentityBridge
    try:
        current_bridge=console.current_batch_identity()
        if type(current_bridge) is not BatchIdentityBridge or (bridge is not None and current_bridge is not bridge):
            raise ValueError('foreign portrait bridge')
        originals=current_bridge.resolve(batch)
    except (ValueError, TypeError, AttributeError) as exc:
        raise Refused('portrait_identity_bridge') from exc
    scope=batch_scope(batch)
    if not scope or scope=='mixed' or not scope.startswith(('private:','group:')):raise Refused('portrait_scope')
    store=getattr(getattr(console,'app',None),'store',None)
    if not isinstance(store,EventStore):raise Refused('portrait_event_store_required')
    rows=[e for e in originals if e.origin=='external' and e.type=='message']
    if not rows or (max_sources is not None and len(rows)>max_sources):raise Refused('portrait_source_count')
    current=getattr(console,'event_filter',None)
    for e in rows:
        if type(e) is not EventEnvelope or store.get(e.cursor) is not e or event_scope(e)!=scope:raise Refused('portrait_append_identity')
        if callable(current) and current(e) is not True:raise Refused('portrait_retired_event')
        if not e.source or not e.sender_key or not e.sender_key.startswith(e.source+':'):raise Refused('portrait_author')
        if scope.startswith('private:') and scope!='private:'+e.sender_key:raise Refused('portrait_private_author')
    # Bounded, complete prior author messages, never assistant summaries or another room.
    # Current events anchor author/scope. The bounded source resolver may select
    # original prior messages; the entire snapshot is revalidated before writing.
    prior=[];chars=0
    authors={e.sender_key for e in rows}
    for e in reversed(store.range(RangeQuery(to_cursor=min(e.cursor for e in rows)-1,
                                             origin='external',limit=128))):
        if e.type!='message' or e.sender_key not in authors or event_scope(e)!=scope:
            continue
        if callable(current) and current(e) is not True:
            continue
        if len(prior)>=12 or chars+len(e.text)>16384:
            break  # do not cut the text or skip a newer correction to admit an older one
        prior.append(e);chars+=len(e.text)
    prior.reverse()
    raw=canonical(dict(scope=scope,events=[asdict(e) for e in batch],
                       prior=[asdict(e) for e in prior]))
    digest=hashlib.sha256(raw.encode()).hexdigest()
    snapshot_rows=[e for e in batch if e.origin=='external' and e.type=='message']
    turn=Turn(digest,scope,tuple(Message(str(e.cursor),e.sender_key,actor_text(e),True,recorded_at=message_time(e)) for e in snapshot_rows),
              tuple(Message(str(e.cursor),e.sender_key,actor_text(e),True,recorded_at=message_time(e)) for e in prior))
    # Filters are trusted callbacks but may reenter stop/rebind or mutate evidence.
    # Never silently recapture a replacement lease after materializing this turn.
    try:
        if console.current_batch_identity() is not current_bridge:
            raise ValueError('portrait bridge replaced during capture')
        active=tuple(console._active_batch or ())
        if len(active)!=len(batch) or any(a is not b for a,b in zip(active,batch)):
            raise ValueError('portrait active snapshot replaced')
        current_bridge.resolve(batch)
        if (getattr(getattr(console,'app',None),'store',None) is not store
                or getattr(console,'event_filter',None) is not current
                or any(store.get(e.cursor) is not e for e in rows+prior)):
            raise ValueError('portrait source binding changed during capture')
    except (ValueError, TypeError, AttributeError) as exc:
        raise Refused('portrait_identity_bridge') from exc
    return turn,raw
