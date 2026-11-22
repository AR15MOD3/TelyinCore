"""SnowLuma OneBot poke notices and their current-conversation reply boundary."""
from datetime import datetime, timezone


def poke_notice(event, privates, groups, qq_id):
    if (event.get("post_type"), event.get("notice_type"), event.get("sub_type")) != (
            "notice", "notify", "poke"):
        return None
    own = qq_id(event.get("self_id"))
    peer = qq_id(event.get("user_id"))
    target = qq_id(event.get("target_id"))
    if not own or not peer:
        return None
    if "group_id" in event:
        group = qq_id(event["group_id"])
        if group not in groups:
            return None
        actor = peer
        room, label = f"group:{group}", f"QQ群{group}"
    else:
        # SnowLuma retains peerUin in user_id even for our outgoing actions.
        actor = qq_id(event.get("sender_id"))
        if actor != peer or peer not in privates or (target is not None and target != own):
            return None
        group = None
        room, label = f"private:{peer}", f"QQ私聊:{peer}"
    if actor == own:
        return None
    stamp = event.get("time")
    if type(stamp) is not int or stamp <= 0:
        return None
    try:
        ts = datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        return None
    recipient = f"qq:{target}" if target else "目标未确认"
    if target == own:
        recipient = "你（本 Bot）"
    meta = {"conversation": room, "self_id": own, "user_id": actor, "notice_type": "poke",
            "poke_target": target, "has_user_text": False}
    if group:
        meta["group_id"] = group
    return {"source": "qq", "origin": "external", "type": "notice", "ts": ts,
            "sender_key": f"qq:{actor}", "meta": meta,
            "text": f"[{label}·戳一戳] 发起人 qq:{actor}；被戳者：{recipient}。"}


def poke_only_addressed(batch, scope, target):
    """True/False for a poke-only external turn in this room, None otherwise."""
    addressed = False
    seen = False
    for event in batch:
        get = event.get if isinstance(event, dict) else lambda k: getattr(event, k, None)
        if get("origin") != "external":
            continue
        meta = get("meta")
        if (get("source") != "qq" or not isinstance(meta, dict) or meta.get("notice_type") != "poke"
                or meta.get("conversation") != f"{scope}:{target}"):
            return None
        seen = True
        if (meta.get("self_id") and meta.get("poke_target") == meta["self_id"]
                and meta.get("user_id") and get("sender_key") == "qq:" + meta["user_id"]):
            addressed = True
    return addressed if seen else None
