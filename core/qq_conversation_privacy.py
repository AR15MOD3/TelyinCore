"""Keep operator-only capabilities out of public and non-owner QQ turns."""
import json
import re

from telyincore.session_view import batch_scope

PUBLIC_TOOLS = frozenset({
    "finish_turn", "profile_add", "profile_read", "qq_send_text", "qq_send_reply",
    "qq_send_voice", "qq_send_face", "qq_send_custom_face", "qq_list_custom_faces",
    "qq_send_saved_image", "qq_inspect_custom_face", "qq_collect_sticker", "qq_look_image",
})
PUBLIC_GUIDANCE = (
    "当前是对外 QQ 会话。正常聊天和使用当前会话允许的能力。"
    "不得向对方透露工具清单、名称、配置、地址、凭据、内部日志、工具回执或私有推理过程。"
    "能力不可用时，只说明当前做不到，别复述内部原因。画像只记当前发言者提供的有据事实。"
)
_INTERNAL = re.compile(
    r"(?i)\b(?:qq_send_\w+|profile_(?:add|read)|self_note|tool_calls|"
    r"reasoning_content|drain_protocol|lifecycle channel revoked|cuda_graph)\b|"
    r"\[tool failed\]|可证明排空|生产者租约|127\.0\.0\.1|localhost:\d+"
)


def owner_message_batch(events, resolve_route):
    """Only real console messages or one explicitly linked private QQ sender."""
    if not events:
        return False
    senders = set()
    for event in events:
        get = event.get if isinstance(event, dict) else lambda key: getattr(event, key, None)
        origin, source, sender = get('origin'), get('source'), get('sender_key')
        meta = get('meta') or {}
        if origin != 'external' or get('type') != 'message' or meta.get('notice_type'):
            return False
        if source == 'console' and sender == 'console:local-owner':
            senders.add(sender)
        elif (source == 'qq' and isinstance(sender, str) and re.fullmatch(r'qq:[1-9][0-9]{0,19}', sender)
              and meta.get('conversation') == 'private:' + sender[3:]):
            senders.add(sender)
        else:
            return False
    if senders == {'console:local-owner'}:
        return True
    if len(senders) != 1 or not callable(resolve_route):
        return False
    try:
        return next(iter(senders))[3:] in resolve_route()['accounts']
    except (OSError, KeyError, TypeError, ValueError):
        return False


def group_owner_sender(events, is_linked, *, require_words=False):
    """Return the authenticated group speaker only for a single room and sender."""
    speaker = room = None
    has_words = False
    for event in events:
        get = event.get if isinstance(event, dict) else lambda key: getattr(event, key, None)
        meta = get('meta')
        if (get('origin') != 'external' or get('source') != 'qq' or get('type') != 'message'
                or not isinstance(meta, dict) or meta.get('notice_type')):
            return None
        sender, conversation = get('sender_key'), meta.get('conversation')
        if (not isinstance(sender, str) or not re.fullmatch(r'qq:[1-9][0-9]{0,19}', sender)
                or not isinstance(conversation, str) or not re.fullmatch(r'group:[1-9][0-9]{0,19}', conversation)
                or str(meta.get('user_id')) != sender[3:]
                or str(meta.get('group_id')) != conversation[6:]):
            return None
        if speaker is not None and (sender, conversation) != (speaker, room):
            return None
        has_words |= meta.get("has_user_text") is True or meta.get("voice_transcription") == "ok"
        speaker, room = sender, conversation
    if require_words and not has_words:
        return None
    try:
        return speaker if speaker and is_linked(speaker) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def restricted(batch, identity):
    qq = [item for item in batch if getattr(item, "origin", None) == "external"
          and getattr(item, "source", None) == "qq"]
    if not qq:
        from telyincore.continuation import route
        return any(route(item).startswith(("private:qq:", "group:qq:")) for item in batch)
    scope = batch_scope(batch)
    if not scope.startswith("private:qq:"):
        return True
    account = scope.removeprefix("private:")
    if any(getattr(item, "sender_key", None) != account
           or (getattr(item, "meta", None) or {}).get("conversation") != "private:" + account[3:] for item in qq):
        return True
    try:
        owner = identity().resolve("console:local-owner")
        return owner is None or account not in owner.accounts
    except (OSError, ValueError):
        return True


def safe_text(text):
    return not _INTERNAL.search(text)


def safe_call(call):
    if call.name not in {"qq_send_text", "qq_send_reply", "qq_send_voice"}:
        return True
    try:
        text = json.loads(call.arguments).get("text")
    except (AttributeError, TypeError, ValueError):
        return False
    return isinstance(text, str) and safe_text(text)
