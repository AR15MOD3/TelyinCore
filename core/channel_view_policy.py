"""Deployment binding of the existing channel markers; never imported by core."""
from functools import lru_cache
import re

from telyincore.session_view import GroupViewPolicy


@lru_cache(maxsize=256)
def qq_group_policy(scope: str) -> GroupViewPolicy:
    """Explicitly declare the current marker/tool protocol for this exact scope.

    The caller supplies a trusted scope, never one inferred from message text.
    Synthetic scope keys remain supported when explicitly wired by a fixture.
    """
    if not isinstance(scope, str) or not scope.startswith("group:"):
        raise ValueError("group_scope_required")
    room = scope.rpartition(":")[2]
    if not room:
        raise ValueError("group_room_required")
    return GroupViewPolicy(
        scope=scope,
        message_pattern=r"^\[" + "QQ" + "群" + re.escape(room)
                        + r"·消息#(-?\d+)\]",
        send_anchor_fields=(("qq_send_text", "trigger_message_id"),
                            ("qq_send_reply", "message_id")),
    )
