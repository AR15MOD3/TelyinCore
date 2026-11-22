"""Read-only operator labels. Never used by prompts, tools or identity resolution."""
from __future__ import annotations

import re
from typing import Any, Iterable

_SOURCE = re.compile(r"^(group:qq:[0-9]+)\|sender=(qq:[0-9]+)$|^private:(qq:[0-9]+)$")
_ACCOUNT = re.compile(r"qq:([0-9]+)$")


def _observed_label(event: Any) -> tuple[str, str, str] | None:
    """Read the adapter's existing display prefix, not arbitrary message assertions."""
    if (getattr(event, "source", None) != "qq"
            or getattr(event, "origin", None) != "external"
            or getattr(event, "type", None) != "message"):
        return None
    account = str(getattr(event, "sender_key", "") or "")
    matched = _ACCOUNT.fullmatch(account)
    meta = getattr(event, "meta", None)
    if not matched or not isinstance(meta, dict):
        return None
    user = matched.group(1)
    if str(meta.get("user_id", "")) != user:
        return None
    room = str(meta.get("conversation", ""))
    if re.fullmatch(r"group:[0-9]+", room):
        group = room.partition(":")[2]
        if str(meta.get("group_id", "")) != group:
            return None
        origin, scope = "QQ群" + group, "group:qq:" + group
    elif room == "private:" + user:
        origin, scope = "QQ私聊:" + user, "private:" + account
    else:
        return None
    message_id = str(meta.get("message_id", ""))
    if not re.fullmatch(r"-?[0-9]+", message_id):
        return None
    prefix = f"[{origin}·消息#{message_id}] "
    text = str(getattr(event, "text", ""))
    if not text.startswith(prefix):
        return None
    label, separator, _ = text[len(prefix):].partition("：")
    if (not separator or not label.strip() or len(label) > 80
            or any(ord(ch) < 32 or 0x7f <= ord(ch) <= 0x9f for ch in label)):
        return None
    return scope, account, label.strip()


def operator_people(rows: Iterable[dict[str, Any]], store: Any) -> list[dict[str, Any]]:
    """Decorate only operator-facing copies; immutable portrait keys stay untouched.

    Only the last 2000 event cursors are inspected. Labels are observed, unverified
    channel display text, not evidence that two accounts belong to one person.
    Missing/expired labels fall back to the original stable portrait key.
    """
    result = [dict(row) for row in rows]
    needed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in result:
        sources = [row.get("plat", ""), *(s.get("from", "") for s in row.get("said", []))]
        if (row.get("who") == "Console-local-owner"
                and "private:console:local-owner" in sources):
            name = next((entry.get("text") for entry in reversed(row.get("said", []))
                         if entry.get("role") == "名字" and entry.get("from") == "private:console:local-owner"
                         and entry.get("text")), None)
            row.update(display_name=name or "本机控制台操作者", display_account="console:local-owner",
                       display_scope="private:console:local-owner")
        for source in set(str(s) for s in sources):
            match = _SOURCE.fullmatch(source)
            if match:
                scope = match.group(1) or "private:" + match.group(3)
                account = match.group(2) or match.group(3)
                needed.setdefault((scope, account), []).append(row)
    if not needed:
        return result
    try:
        latest = store.latest_cursor()
        for cursor in range(latest, max(0, latest - 2000), -1):
            observed = _observed_label(store.get(cursor))
            if observed is None:
                continue
            scope, account, label = observed
            for row in needed.pop((scope, account), []):
                # The newest matching room/account wins, without consulting nicknames.
                row.setdefault("display_name", label)
                row.setdefault("display_account", account)
                row.setdefault("display_scope", scope)
            if not needed:
                break
    except (OSError, ValueError, TypeError, AttributeError):
        # Optional decoration must never hide portraits or widen an identity scope.
        pass
    return result
