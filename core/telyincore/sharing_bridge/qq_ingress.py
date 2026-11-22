"""Pure mapping of authenticated-host OneBot envelopes; NOT authentication itself.

Call only after the transport host authenticates the connection. Never use model
arguments, rendered transcript text or nickname strings as transport identity.
Text-only candidate: rich messages are rejected, not silently stripped.
"""
from .host_bridge import TransportEvent
from .qq_identity import _qq_id,_message_id
from .sharing import Policy,key


def normalize_message(event, *, bot, expected_self_id, allowed_scopes, now_ms):
    if type(event) is not dict or event.get('post_type')!='message' or not key(bot):
        raise ValueError('invalid_transport_message')
    expected=_qq_id(expected_self_id)
    own=_qq_id(event.get('self_id'));user=_qq_id(event.get('user_id'))
    mid=_message_id(event.get('message_id'));kind=event.get('message_type')
    if not expected or own!=expected or not user or user==own or mid is None:
        raise ValueError('transport_account_or_message_identity')
    if kind not in ('group','private'):raise ValueError('unsupported_message_type')
    target=_qq_id(event.get('group_id')) if kind=='group' else user
    origin=f'{kind}:qq:{target}'
    if not target or origin not in allowed_scopes:raise ValueError('source_not_allowlisted')
    stamp=event.get('time')
    if type(stamp) is not int or type(now_ms) is not int or not 0<=now_ms-stamp*1000<Policy().max_age_ms:
        raise ValueError('unverified_transport_time')
    segments=event.get('message')
    if type(segments) is not list or not 1<=len(segments)<=64:
        raise ValueError('text_segments_required')
    parts=[]
    for segment in segments:
        if (type(segment) is not dict or segment.get('type')!='text'
                or type(segment.get('data')) is not dict
                or type(segment['data'].get('text')) is not str):
            raise ValueError('rich_or_unverified_content_requires_separate_adapter')
        parts.append(segment['data']['text'])
    text=''.join(parts)
    if not text.strip() or len(text.encode())>32768:raise ValueError('invalid_source_text')
    return TransportEvent(bot,origin,f'qq:{user}',mid,stamp*1000,text)


def normalize_recall(event, *, bot, expected_self_id, allowed_scopes):
    if (type(event) is not dict or event.get('post_type')!='notice'
            or not key(bot) or _qq_id(expected_self_id) is None
            or _qq_id(event.get('self_id'))!=_qq_id(expected_self_id)):
        raise ValueError('unverified_recall_transport')
    kind=event.get('notice_type')
    if kind not in ('group_recall','friend_recall'):raise ValueError('unsupported_notice')
    origin_kind='group' if kind=='group_recall' else 'private'
    target=_qq_id(event.get('group_id' if origin_kind=='group' else 'user_id'))
    message=_message_id(event.get('message_id'))
    origin=f'{origin_kind}:qq:{target}'
    if not target or message is None or origin not in allowed_scopes:
        raise ValueError('recall_scope_or_identity')
    return bot,origin,message  # host must publish a new snapshot with this tombstone
