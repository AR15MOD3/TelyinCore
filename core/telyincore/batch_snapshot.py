"""Freeze trusted host event metadata without worker ambient reads."""
import json


def freeze_batch(active):
    if not callable(active):
        return None  # Legacy manual callers have no ambient turn contract.
    rows = []
    for event in tuple(active() or ()):
        def get(key):
            return event.get(key) if isinstance(event, dict) else getattr(event, key, None)
        meta = get('meta')
        frozen_meta = ({key: meta.get(key) for key in ['conversation', 'message_id', 'self_id']}
                       if isinstance(meta, dict) else None)
        if isinstance(meta, dict) and str(meta.get('conversation', '')).startswith('group:'):
            frozen_meta.update({key: meta.get(key) for key in ('user_id', 'group_id', 'has_user_text', 'voice_transcription')})
        if isinstance(meta, dict) and meta.get('notice_type') == 'poke':
            frozen_meta.update({key: meta.get(key) for key in ('notice_type', 'poke_target', 'user_id')})
        rows.append({'source': get('source'), 'origin': get('origin'), 'meta': frozen_meta,
                     'type': get('type'), 'sender_key': get('sender_key')})
    return json.dumps(rows, ensure_ascii=False, allow_nan=False)
