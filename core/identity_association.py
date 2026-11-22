"""Operator-confirmed identity association, independent of nicknames and model text."""
from __future__ import annotations

import re
from typing import Any


def native_accounts(row: dict[str, Any]) -> list[str]:
    sources = {str(row.get("plat") or ""),
               *(str(s.get("from") or "") for s in row.get("said", []))}
    who = str(row.get("who") or "")
    if who == "Console-local-owner" and "private:console:local-owner" in sources:
        return ["console:local-owner"]
    match = re.fullmatch(r"QQ-([0-9]+)", who)
    if match:
        account = "qq:" + match.group(1)
        if "private:" + account in sources or any(
                re.fullmatch(r"group:qq:[0-9]+\|sender=" + re.escape(account), s)
                for s in sources):
            return [account]
    return []


def _native_index(rows):
    by_key = {str(row.get("who") or ""): native_accounts(row) for row in rows}
    by_account = {}
    for key, accounts in by_key.items():
        for account in accounts:
            by_account.setdefault(account, set()).add(key)
    return by_key, by_account


def plan_for_profiles(book, rows, keys, *, planner=None, index=None):
    by_key, by_account = index if index is not None else _native_index(rows)
    selected = set(keys)
    accounts = set()
    for _ in range(65):
        accounts.update(a for k in selected for a in by_key.get(k, ()))
        plan = (planner or book.plan)(sorted(selected), sorted(accounts))
        expanded = set(plan["profile_keys"])
        linked = set(plan["accounts"])
        # A native account key is not a nickname: show its retained legacy portrait
        # in the confirmation, rather than silently abandoning that portrait.
        expanded.update(k for account in linked for k in by_account.get(account, ()))
        if expanded == selected and linked == accounts:
            return plan
        selected, accounts = expanded, linked
    raise ValueError("关联范围不稳定，请刷新后重新确认")


def association_rows(rows, book):
    """Attach a complete confirmable plan to copies, including filtered UI views."""
    result = [dict(row) for row in rows]
    # Never retain caller-supplied/stale authority decorations on an unbound row.
    for row in result:
        row.pop("identity", None)
        row.pop("association", None)
    try:
        people, revision, planner = book.planning_snapshot()
        index = _native_index(result)
        members = {k: person for person in people for k in person.profile_keys}
        for row in result:
            key = str(row.get("who") or "")
            person = members.get(key)
            if person:
                from telyincore.identity import person_display_name
                row["identity"] = person.__dict__
                label = person_display_name(person.label, person.profile_keys)
                if label:
                    row["display_name"] = label
            plan = plan_for_profiles(book, rows, [key], planner=planner, index=index)
            if plan["revision"] != revision:
                raise ValueError("identity changed while reading")
            if person:
                other = {a for k in person.profile_keys if k != key for a in index[0].get(k, ())}
                plan["separate_accounts"] = sorted(
                    set(native_accounts(row)).intersection(person.accounts) - other)
            row["association"] = plan
    except (OSError, ValueError, TypeError, AttributeError):
        for row in result:
            row.pop("identity", None)
            row.pop("association", None)
    return result


def association_action(book, profiles, body):
    command = body.get("action")
    known = {str(row.get("who") or ""): row for row in profiles}
    if command == "associate":
        keys = body.get("profile_keys")
        if (not isinstance(keys, list) or not 1 <= len(keys) <= 16
                or any(not isinstance(k, str) or k not in known for k in keys)):
            raise ValueError("须选择当前仍存在的画像")
        plan = plan_for_profiles(book, profiles, keys)
        if (body.get("revision") != plan["revision"]
                or body.get("expected_profiles") != plan["profile_keys"]
                or body.get("expected_accounts") != plan["accounts"]):
            raise ValueError("确认范围已变化，请刷新后重新确认全部画像与账号")
        person = book.associate(plan["profile_keys"], plan["accounts"],
                                expected=plan, confirmed=body.get("confirm") is True)
        return {"ok": True, "id": person.id}
    if command == "separate":
        key = body.get("profile_key")
        if not isinstance(key, str) or key not in known:
            raise ValueError("须选择当前仍存在的画像")
        row = association_rows(list(known.values()), book)
        entry = next(p for p in row if p["who"] == key)
        plan = entry.get("association")
        if (not plan or body.get("revision") != plan["revision"]
                or body.get("expected_accounts") != plan.get("separate_accounts", [])):
            raise ValueError("分开范围已变化，请刷新后重新确认")
        book.separate(key, plan.get("separate_accounts", []), revision=plan["revision"],
                      confirmed=body.get("confirm") is True)
        return {"ok": True}
    raise ValueError("未知身份关联操作")
