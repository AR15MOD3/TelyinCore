"""Ground a due-item resolution in a current, independently stored user message."""
from datetime import datetime
import re

from .events import EventEnvelope
from .reminder_provenance import fingerprint
from .session_view import event_scope


ACK_POLICY = '“看到了”只确认读到了提醒。关闭待办需要本人明确确认这件事已完成或取消。'

# Match affirmative offers, not an arbitrary mention of these words in a reply.
_ACK = r'[「“『"]?(?:我)?(?:看到了|收到了|已读|收到)[」”』"]?'
_ALTERNATIVE = r'(?:或(?:者)?[「“『"]?(?:不用了|取消)[」”』"]?)?'
_CLOSE = (r'(?:(?:把|将)?(?:这[个条项件]?)?(?:提醒|待办|事项|它)(?:标记为完成|关闭|关掉|关了|关)|'
          r'(?:关闭|关掉|关了)(?:这[条项个件]?)?(?:提醒|待办|事项|它)|'
          r'按(?:完成或取消|完成)(?:把它)?(?:关掉|关闭|关)|算(?:这件事|事项|待办)?(?:已)?完成)')
_ACK_OFFER = re.compile(
    r'^(?:要(?:收口|收尾|关掉)(?:的话)?[，,：:]?\s*)?(?:(?:你|您)?(?:只要|只需|只需要)?'
    r'(?:给我?一句|回(?:我)?(?:一[句声])?|说(?:一[句声])?)?\s*)' + _ACK + _ALTERNATIVE +
    r'\s*[，,]?\s*(?:那)?(?:我|就|也|便|可以|能|会|可|直接|帮你|替你|把)*' + _CLOSE +
    r'(?:了|掉)?[。.!！\s]*$')


def ack_closure_offer(sentence, *, reminder_context=False):
    """Recognize direct promises about closing a task solely on acknowledgment."""
    refers_to_task = reminder_context or any(word in sentence for word in ('提醒', '待办', '事项', '完成'))
    return refers_to_task and _ACK_OFFER.fullmatch(sentence.strip()) is not None


def _matches(text, what, ident, resolution):
    if text == ('确认完成提醒 ' if resolution == 'completed' else '确认取消提醒 ') + ident:
        return True
    if text == ('我已完成' if resolution == 'completed' else '请取消提醒') + '【' + what + '】':
        return True
    # A single named reading task; never match merely overlapping subjects.
    task = re.fullmatch(
        r'(?:提醒(?:我)?[：: ]*)?(?:我(?:要|想))?(?:阅读|读完|看完|读|看)\s*'
        r'(?:《([^《》\n]{1,100})》(?:这本书)?|([\w· ]{1,100}?)这本书)', what.strip())
    if task is None:
        return False
    title = task[1] or task[2]
    forms = ['《' + title + '》']
    if re.fullmatch(r'[\w· ]{1,100}', title):
        forms.append(title)
    for book in forms:
        if resolution == 'cancelled':
            if text in ('请取消' + book + '的阅读提醒', '取消' + book + '的阅读提醒'):
                return True
            continue
        for prefix in ('我', '我已', '我已经'):
            for verb in ('读完', '看完'):
                if text in (prefix + verb + '了' + book, prefix + verb + book + '了',
                            prefix + verb + book, book + prefix + verb + '了', book + prefix + verb):
                    return True
    return False


def current_receipt(scope, scope_of, source_events, event_store, row, others, resolution):
    if scope_of() != scope or scope.current != row['scope'] or not scope.current.startswith('private:'):
        return None
    if source_events is None or event_store is None:
        return None
    last = None
    for event in source_events() or ():
        if getattr(event, 'origin', '') == 'external':
            last = event
    if (not isinstance(last, EventEnvelope) or last.type != 'message'
            or event_scope(last) != scope.current or last.sender_key != scope.current[8:]
            or not last.sender_key.startswith(last.source + ':') or not scope.event(last)):
        return None
    original_store = event_store()
    original = original_store.get(last.cursor) if original_store is not None else None
    if original is None:
        return None
    digest = fingerprint(original)
    if digest != fingerprint(last):
        return None
    try:
        said = datetime.fromisoformat(last.ts)
        due = datetime.fromisoformat(row['due_at'])
        if said.utcoffset() is None or due.utcoffset() is None or said < due:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    text = last.text.strip().removesuffix('。')
    if not _matches(text, row['what'], row['id'], resolution):
        return None
    if any(_matches(text, other['what'], other['id'], resolution) for other in others):
        return None
    # A lease or batch can change while source/uniqueness checks are running.
    if scope_of() != scope:
        return None
    current = [e for e in (source_events() or ()) if getattr(e, 'origin', '') == 'external']
    if not current or current[-1] != last:
        return None
    return {'event_ref': 'event:' + str(last.cursor), 'sha256': digest,
            'basis': 'current_direct_confirmation'}
