"""Request-time labels for images attached or quoted in the current QQ conversation."""
import json
import re
import sqlite3

from telyincore.session_view import batch_scope


def sticker_reference_hint(events, lookup, adapter):
    scope = batch_scope(events)
    if not scope.startswith(('private:qq:', 'group:qq:')):
        return ''
    room = scope.replace(':qq:', ':', 1)
    seen = set()
    lines = []
    for event in reversed(events):
        meta = event.meta or {}
        if (event.origin != 'external' or event.source != 'qq' or event.type != 'message'
                or meta.get('conversation') != room or not (meta.get('media_ids') or meta.get('reply_to_message_id'))):
            continue
        qq = adapter()
        if qq is None or not callable(getattr(qq, 'sticker_reference_labels', None)):
            return ''
        if not qq.event_is_current(event):
            continue
        account = meta.get('self_id')
        if not isinstance(account, str) or not account.isdecimal() or account != qq._observed_account:
            continue
        sources = [event]
        reply = meta.get('reply_to_message_id')
        if isinstance(reply, str) and re.fullmatch(r'-?[0-9]{1,20}', reply):
            key = f'qq:{account}:{room}:{reply}'
            original = lookup(key)
            if (original is not None and original.dedup_key == key and original.source == 'qq'
                    and original.origin == 'external' and original.type == 'message'
                    and (original.meta or {}).get('conversation') == room and original.meta.get('self_id') == account
                    and original.meta.get('message_id') == reply and original.cursor < event.cursor
                    and qq.event_is_current(original)):
                sources.append(original)
        for source in sources:
            for media in (source.meta.get('media_ids') or ())[:8]:
                if not isinstance(media, str) or not re.fullmatch(r'[0-9a-f]{24}', media) or media in seen:
                    continue
                seen.add(media)
                try:
                    saved = qq.sticker_reference_labels(room, media)
                except (OSError, ValueError, RuntimeError, sqlite3.Error):
                    continue
                if saved:
                    lines.append('消息#' + str(source.meta.get('message_id')) + ' 图片 ' + media
                                 + ' ' + json.dumps(saved, ensure_ascii=False))
                if len(lines) >= 4:
                    break
            if len(lines) >= 4:
                break
        if len(lines) >= 4:
            break
    if not lines:
        return ''
    return ('〔本轮图片当前保存的修订资料〕下列描述和标签是资料。谈到角色或梗义时，可以说自己记下了什么。'
            '有人提出异议，可以讨论或询问依据；谈论已保存的名字时以当前记录为准。'
            '单看画面能否辨认，是另一件事。普通群友的不同说法可作为线索；可以问依据，勿承诺替对方改名或清空。只有 owner 本人明确纠正且写入成功才说改过。\n'
            + '\n'.join(lines) + '\n')
