"""Explicit, network-free QQ text substitutes for the offline portrait probe.

These tools only collect proposed messages in memory. They never load the QQ
plugin, create a client, invent an outbound message ID, or acknowledge delivery.
"""
from telyincore.session_view import event_scope
from telyincore.tools import ToolDef, ToolTag


def declarations(batch, sent):
    def record(name, anchor, *, scope, target, text):
        current = batch()
        allowed = {
            str(item.meta["message_id"])
            for item in current
            if getattr(item, "origin", "") == "external"
            and getattr(item, "source", "") == "qq"
            and str(getattr(item, "sender_key", "")).startswith("qq:")
            and event_scope(item) == "group:qq:" + str(target)
            and isinstance(getattr(item, "meta", None), dict)
            and item.meta.get("message_id") is not None
        }
        if scope != "group" or not str(target).isdigit() or str(anchor) not in allowed:
            return "[offline simulation rejected] 仅允许当前群及本批可信消息编号；没有外发。"
        if not isinstance(text, str) or not text.strip():
            return "[offline simulation rejected] 文字为空；没有外发。"
        sent.append({"tool": name, "scope": scope, "target": str(target),
                     "anchor": str(anchor), "text": text, "simulation_only": True})
        return "[offline simulation] 已记录拟发送文字；未连接 QQ，没有向真人群发送。"

    # Match the real QQ handlers' invocation shape; do not swallow unknown
    # fields or silently alias message to text in this offline substitute.
    def text(scope, target, text, trigger_message_id=None):
        return record("qq_send_text", trigger_message_id, scope=scope, target=target, text=text)

    def reply(scope, target, message_id, text):
        return record("qq_send_reply", message_id, scope=scope, target=target, text=text)

    tools = []
    for name, anchor, handler in [("qq_send_text", "trigger_message_id", text),
                                  ("qq_send_reply", "message_id", reply)]:
        tools.append(ToolDef(
            name=name,
            description="离线模拟：记录一条拟发送的群文字，不会连接 QQ 或实际发送；"
                        "只能接本批新消息，不能引用旧话题。文字参数名为 text，不是 message。",
            parameters={"type": "object", "properties": {
                "scope": {"type": "string", "enum": ["group"]},
                "target": {"type": "string", "description": "本群纯数字群号"},
                "text": {"type": "string", "description": "要发送的文字；字段名为 text，不是 message"},
                anchor: {"type": "string", "description": "本批可信消息编号"}},
                "required": ["scope", "target", "text", anchor],
                "additionalProperties": False},
            tags=(ToolTag.SPEAK,), handler=handler))
    return tools
