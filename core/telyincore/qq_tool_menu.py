"""Clarify the paired, stock QQ declarations after the host has selected visible tools."""

_TEXT_DESCRIPTION = "向当前 QQ 会话发送文字。群消息填写本轮 trigger_message_id；本轮只有戳一戳且有人戳你时，省略编号发普通文字。"
_REPLY_DESCRIPTION = "引用 QQ 消息并发送文字；群聊只能引用当前这轮新收到的消息，不要回头补答旧问题。message_id 见入站消息的『消息#』标记。"
_PLAIN_AND_QUOTED = (
    "普通私聊填 scope、target、text。要引用某条消息，用 qq_send_reply 并填 message_id。"
    "群聊填本轮新消息的 trigger_message_id。仅收到戳一戳且有人戳你时，省略编号发普通文字。"
)


def clarify_qq_text(tools):
    """The returned menu owns its rows. Never name a sibling absent from this view."""
    text = next((row for row in tools if row.get('name') == 'qq_send_text'), None)
    if text is None or text.get('description') != _TEXT_DESCRIPTION:
        return
    reply = next((row for row in tools if row.get('name') == 'qq_send_reply'), None)
    if reply is None or reply.get('description') != _REPLY_DESCRIPTION:
        return
    for row, field, required in (
        (text, 'trigger_message_id', ['scope', 'target', 'text']),
        (reply, 'message_id', ['scope', 'target', 'message_id', 'text']),
    ):
        shape = row.get('parameters')
        if not isinstance(shape, dict) or shape.get('additionalProperties') is not False:
            return
        props = shape.get('properties')
        if not isinstance(props, dict) or set(props) != {'scope', 'target', 'text', field}:
            return
        if shape.get('required') != required:
            return
    text['description'] = _PLAIN_AND_QUOTED
