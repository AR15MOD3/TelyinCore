"""Per-Bot QQ group encounters. Only operator views can enumerate other rooms."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import logging
import re
import sqlite3

from portrait_display import _observed_label
from telyincore.session_view import batch_scope

# An ordinary room-local nickname request does not grant cross-room permission.
_NAME = r"([\w·'’-]+(?: [\w·'’-]+){0,2})"
_PUBLIC_NAME = re.compile(r"(?:以后|今后)?(?:请|你可以|你)?(?:在)?(?:所有群(?:里)?|各个群(?:里)?|任何群(?:里)?|哪[里儿]|哪个群(?:里)?)(?:都)?(?:请)?(?:叫我|称呼我为)" + _NAME)
_CANCEL_NAME = re.compile(r"(?:请)?(?:取消我的通用称呼|忘掉我的通用称呼|不要再用我的通用称呼)")
_LOG = logging.getLogger(__name__)


def _seen(event):
    observed = _observed_label(event)
    if observed is None:
        return None
    scope, account, label = observed
    meta = event.meta
    if str(meta.get("self_id", "")) == account[3:]:
        return None
    kinds = meta.get("segment_types")
    if not isinstance(kinds, list) or not kinds:
        return None
    try:
        dt = datetime.fromisoformat(event.ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        stamp = dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    except (ValueError, OverflowError):
        return None
    name = None
    text = meta.get("plain_text")
    if kinds == ["text"] and isinstance(text, str) and meta.get("has_user_text") is True:
        text = text.strip().rstrip("。.!！").strip()
        if text.endswith("吧"):
            text = text[:-1].rstrip()
        match = _PUBLIC_NAME.fullmatch(text)
        if match and len(match[1]) <= 24:
            name = match[1]
        elif _CANCEL_NAME.fullmatch(text):
            name = ""
    digits = str(event.cursor)
    order = f"{len(digits):06}:{digits}"
    return account, scope, label, stamp, order, meta["message_id"], name


class GroupEncounters:
    def __init__(self, home, *, readonly=False):
        self.path = home / "qq-group-encounters.sqlite3"
        self.readonly = readonly
        if readonly:
            return
        with closing(self._open()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS rooms (
                    account TEXT, room TEXT, first TEXT, first_cursor TEXT,
                    last TEXT, last_cursor TEXT, label TEXT, PRIMARY KEY(account,room));
                CREATE TABLE IF NOT EXISTS names (
                    account TEXT PRIMARY KEY, name TEXT, stamp TEXT, cursor TEXT,
                    source TEXT, message TEXT);
                CREATE TABLE IF NOT EXISTS checkpoint (
                    id INTEGER PRIMARY KEY CHECK (id=1), cursor TEXT, signature TEXT);
            """)

    def _open(self):
        if self.readonly:
            return sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        return sqlite3.connect(self.path, timeout=5)

    @staticmethod
    def _write(db, seen):
        account, scope, label, stamp, cursor, message, name = seen
        if scope.startswith("group:qq:"):
            db.execute("""INSERT INTO rooms VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(account,room) DO UPDATE SET
                  first=min(first,excluded.first),
                  first_cursor=CASE WHEN (excluded.first,excluded.first_cursor)<(first,first_cursor)
                                    THEN excluded.first_cursor ELSE first_cursor END,
                  last=max(last,excluded.last),
                  last_cursor=CASE WHEN (excluded.last,excluded.last_cursor)>(last,last_cursor)
                                   THEN excluded.last_cursor ELSE last_cursor END,
                  label=CASE WHEN (excluded.last,excluded.last_cursor)>=(last,last_cursor)
                             THEN excluded.label ELSE label END""",
                       (account, scope, stamp, cursor, stamp, cursor, label))
        if name is not None:
            db.execute("""INSERT INTO names VALUES (?,?,?,?,?,?) ON CONFLICT(account) DO UPDATE SET
                name=excluded.name, stamp=excluded.stamp, cursor=excluded.cursor,
                source=excluded.source, message=excluded.message
                WHERE (excluded.stamp,excluded.cursor)>=(stamp,cursor)""",
                       (account, name, stamp, cursor, scope, str(message)))

    def observe(self, event):
        seen = _seen(event)
        if seen and (seen[1].startswith("group:qq:") or seen[-1] is not None):
            try:
                with closing(self._open()) as db, db:
                    self._write(db, seen)
            except sqlite3.Error:
                # The event is already committed. Replay it from the checkpoint on reopen.
                _LOG.exception("QQ 群相遇记录暂未写入")

    def attach(self, store, *, subscribe=True):
        # Called before plugin admission. The journal also covers downtime and upgrades.
        upper = store.latest_cursor()
        with closing(self._open()) as db, db:
            row = db.execute("SELECT cursor,signature FROM checkpoint").fetchone()
            start = 0
            if row and (anchor := store.get(int(row[0]))) is not None:
                if hashlib.sha256(anchor.to_line().encode()).hexdigest() == row[1]:
                    start = int(row[0])
            if upper > start:
                for event in store.iter_after(start, upper):
                    seen = _seen(event)
                    if seen:
                        self._write(db, seen)
                anchor = store.get(upper)
                if anchor:
                    db.execute("INSERT OR REPLACE INTO checkpoint VALUES (1,?,?)",
                               (str(upper), hashlib.sha256(anchor.to_line().encode()).hexdigest()))
        if subscribe:
            store.on_append(self.observe)

    def hint(self, batch):
        scope = batch_scope(batch or ())
        if not scope.startswith("group:qq:"):
            return ""
        authors = {}
        for event in batch:
            seen = _seen(event)
            if seen and seen[1] == scope:
                authors.setdefault(seen[0], seen)
        notes = []
        if not authors:
            return ""
        with closing(self._open()) as db:
            for account, (_, _, _, stamp, cursor, _, _) in authors.items():
                rows = db.execute("SELECT room,first,first_cursor FROM rooms WHERE account=?", (account,))
                earlier = {room for room, first, seq in rows if (first, seq) < (stamp, cursor)}
                if scope in earlier:
                    status = "以前在本群见过"
                elif earlier:
                    status = "以前见过这个账号，这是首次在本群见到"
                else:
                    status = "这是首次记录到这个账号在本群发言"
                pref = db.execute("SELECT name FROM names WHERE account=?", (account,)).fetchone()
                if pref and pref[0]:
                    status += f"。对方明确允许各群通用称呼「{pref[0]}」"
                notes.append(account + "：" + status)
        if not notes:
            return ""
        return "〔群内相遇记录〕" + "。".join(notes) + "。平时按本群称呼自然接话；相遇不要求打招呼，不向群里透露其他群的经历。\n"

    def people(self, rows, book):
        by_key = {row["who"]: dict(row) for row in rows}
        try:
            people = {account: person for person in book.people() for account in person.accounts}
        except (OSError, ValueError):
            people = {}
        with closing(self._open()) as db:
            entries = db.execute("""SELECT r.account,room,first,last,label,n.name
                FROM rooms r LEFT JOIN names n ON n.account=r.account ORDER BY last DESC""")
            for account, scope, first, last, label, name in entries:
                person = people.get(account)
                key = person.profile_key if person and person.profile_key else "QQ-" + account[3:]
                row = by_key.setdefault(key, dict(who=key, standing=None, favor=None, blocked=False,
                    said=[], full=False, plat=scope + "|sender=" + account))
                row.setdefault("display_account", account)
                if name and row["display_account"] == account:
                    row["display_name"] = name
                else:
                    row.setdefault("display_name", label)
                encounters = row.setdefault("encounters", [])
                encounters.append(dict(account=account, room=scope[9:], label=label, first=first, last=last,
                                       common_name=name or ""))
                ms = int(datetime.fromisoformat(last).timestamp()*1000)
                row["recent_at"] = max(row.get("recent_at") or 0, ms)
        return sorted(by_key.values(), key=lambda row: row.get("recent_at") or 0)
