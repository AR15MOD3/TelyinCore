"""Deployment-only policy for persisted plain-text QQ ingress drafts.

No plugin construction, QQ/network login, transcription or image interpretation.
Identity is anchored to the host publication boundary, not proved by metadata alone.
"""
import re

from portrait_shadow_wiring import ContextRefused, TrustedBatchContext
from telyincore.frozen_json import FrozenDict, FrozenList
from telyincore.events import EventEnvelope

_REQUIRED = frozenset(('conversation', 'message_id', 'user_id', 'group_id',
                       'segment_types', 'has_user_text'))
_OPTIONAL = frozenset(('self_id', 'plain_text'))


def _id(value):
    return type(value) is str and re.fullmatch(r'[1-9][0-9]{0,19}', value) is not None


def qq_plain_scope(event):
    if (type(event) is not EventEnvelope or type(event.cursor) is not int
            or event.cursor < 1 or event.type != 'message' or event.origin != 'external'
            or event.source != 'qq' or type(event.sender_key) is not str
            or type(event.text) is not str or not event.text.strip()
            or event.blobs or event.context_delivery is not None
            or type(event.meta) not in (dict, FrozenDict)):
        raise ContextRefused('qq_plain_envelope_required')
    m = event.meta
    if not _REQUIRED <= m.keys() or not m.keys() <= _REQUIRED | _OPTIONAL:
        raise ContextRefused('qq_plain_metadata_keys')
    if (not _id(m['user_id']) or not _id(m['group_id'])
            or event.sender_key != 'qq:' + m['user_id']
            or m['conversation'] != 'group:' + m['group_id']):
        raise ContextRefused('qq_plain_identity_mismatch')
    mid = m['message_id']
    if (type(mid) is not str or len(mid) > 20
            or re.fullmatch(r'-?[0-9]+', mid) is None):
        raise ContextRefused('qq_plain_message_id')
    self_id = m.get('self_id')
    if 'self_id' in m and (not _id(self_id) or self_id == m['user_id']):
        raise ContextRefused('qq_plain_self_identity')
    kinds = m['segment_types']
    if (type(kinds) not in (list, FrozenList) or not kinds
            or any(type(k) is not str or k != 'text' for k in kinds)
            or m['has_user_text'] is not True):
        raise ContextRefused('qq_plain_segments_only')
    # New QQ ingress preserves a bounded plain body for short text messages.
    # It is content, never identity evidence, and must agree with the stored display.
    if 'plain_text' in m:
        plain = m['plain_text']
        if (type(plain) is not str or not plain.strip() or len(plain) > 64
                or kinds != ['text'] or not event.text.endswith('：' + plain)):
            raise ContextRefused('qq_plain_body_mismatch')
    expected = 'qq:' + (self_id or 'unknown') + ':' + m['conversation'] + ':' + mid
    if event.dedup_key != expected:
        raise ContextRefused('qq_plain_dedup_mismatch')
    prefix = '[QQ群' + m['group_id'] + '·消息#' + mid + '] '
    if not event.text.startswith(prefix) or not event.text[len(prefix):].strip():
        raise ContextRefused('qq_plain_display_origin')
    return 'group:qq:' + m['group_id']


class QQPlainBatchContext(TrustedBatchContext):
    """Retains every persisted field; only the identity policy is specialized.

    at/reply/media/voice and unknown metadata are refused, never stripped.
    Message.text remains the exact stored decorated ingress text, not raw OneBot text.
    """
    def _source_scope(self, event):
        return qq_plain_scope(event)
