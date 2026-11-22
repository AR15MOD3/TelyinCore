"""Explicit, reversible account-to-person links.

This book is private deployment state, not an inference engine.  A nickname,
message timing, or conversational similarity must never create a link.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
import hashlib
import re
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_ACCOUNT = re.compile(r"^[a-z][a-z0-9_-]{1,31}:[^\s:]{1,128}$")
_PERSON = re.compile(r"^person_[0-9a-f]{32}$")


def person_display_name(label: str, profile_keys=()) -> str:
    """Legacy portrait keys are storage identifiers, not personal names."""
    name = label.strip()
    if name in profile_keys or re.fullmatch(r'(?:console[-:]local-owner|qq[-:]\d+)', name, re.I):
        return ''
    return name


@dataclass(frozen=True)
class Person:
    id: str
    label: str
    accounts: tuple[str, ...]
    profile_key: str | None = None
    profile_keys: tuple[str, ...] = ()


def _keys(row: dict[str, Any]) -> list[str]:
    return row.get("profile_keys", [row["profile_key"]] if row.get("profile_key") else [])


def _revision(data: dict[str, Any]) -> str:
    wire = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


class IdentitySnapshotChanged(ValueError):
    """The identity binding changed during the current batch."""


@dataclass(frozen=True)
class IdentityReadView:
    """One revision for related consumers; never grants mutation authority."""
    owner: Any
    rows: tuple[Person, ...]
    revision: str

    def assert_current(self) -> None:
        with self.owner._lock:
            if _revision(self.owner._read()) != self.revision:
                raise IdentitySnapshotChanged('identity snapshot changed; request must be retried explicitly')

    def read_view(self):
        self.assert_current()
        return self

    def people(self) -> list[Person]:
        self.assert_current()
        return list(self.rows)

    def resolve(self, account: str) -> Person | None:
        self.assert_current()
        if not _ACCOUNT.fullmatch(str(account)):
            return None
        return next((person for person in self.rows if account in person.accounts), None)


class IdentityBook:
    """One local book. Writes are atomic; corrupt state fails closed."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    @contextmanager
    def _mutation(self, *, timeout=5.0):
        """Serialize read/validate/write across cooperating local processes.

        The stable sidecar is never removed or replaced. Readers remain atomic
        file snapshots and do not create directories or lock files. This does
        not protect against writers ignoring the protocol or hostile path swaps.
        """
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self.path.with_name(self.path.name + '.lock')
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            with os.fdopen(descriptor, 'r+b') as handle:
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b'0')
                    handle.flush()
                deadline = time.monotonic() + max(0.0, timeout)
                while True:
                    handle.seek(0)
                    try:
                        if os.name == 'nt':
                            import msvcrt
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as error:
                        if time.monotonic() >= deadline:
                            raise TimeoutError('身份绑定写入繁忙；本次操作未提交') from error
                        time.sleep(.01)
                try:
                    yield
                finally:
                    handle.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "people": {}}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError("身份绑定记录的版本或结构不正确")
        people = raw.get("people")
        if not isinstance(people, dict):
            raise ValueError("身份绑定记录缺少人物表")
        seen: set[str] = set()
        profiles: set[str] = set()
        for person_id, row in people.items():
            if not isinstance(person_id, str) or not _PERSON.fullmatch(person_id):
                raise ValueError("身份绑定记录含无效人物 ID")
            if not isinstance(row, dict) or not isinstance(row.get("label"), str):
                raise ValueError("身份绑定记录含无效人物")
            profile_key = row.get("profile_key")
            if profile_key is not None:
                if (not isinstance(profile_key, str) or not profile_key.strip()
                        or len(profile_key) > 120 or any(ord(ch) < 32 for ch in profile_key)):
                    raise ValueError("身份绑定记录含无效画像键")
            keys = _keys(row)
            if (not isinstance(keys, list) or len(keys) > 64
                    or (profile_key is not None and profile_key not in keys)
                    or any(not isinstance(k, str) or not k.strip() or len(k) > 120
                           or any(ord(ch) < 32 for ch in k) for k in keys)):
                raise ValueError("身份绑定记录含无效画像关联")
            for key in keys:
                if key in profiles:
                    raise ValueError("一份画像被连接到了多个身份")
                profiles.add(key)
            accounts = row.get("accounts")
            if not isinstance(accounts, list):
                raise ValueError("身份绑定记录含无效账号表")
            for account in accounts:
                if not isinstance(account, str) or not _ACCOUNT.fullmatch(account):
                    raise ValueError("身份绑定记录含无效账号")
                if account in seen:
                    raise ValueError("一个账号被绑定到了多个身份")
                seen.add(account)
        return raw

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
        tmp.replace(self.path)

    def people(self) -> list[Person]:
        with self._lock:
            rows = self._read()["people"]
            return [Person(key, value["label"], tuple(value["accounts"]),
                           value.get("profile_key"), tuple(_keys(value)))
                    for key, value in rows.items()]

    def resolve(self, account: str) -> Person | None:
        if not _ACCOUNT.fullmatch(str(account)):
            return None
        for person in self.people():
            if account in person.accounts:
                return person
        return None

    def owner_qq_route(self, preferred: str = "") -> dict:
        """Resolve only accounts explicitly linked to this Bot's console operator."""
        person = self.resolve("console:local-owner")
        accounts = sorted(key[3:] for key in person.accounts
                          if re.fullmatch(r"qq:[1-9][0-9]{0,19}", key)) if person else []
        target = preferred if preferred in accounts else ""
        if len(accounts) == 1:
            target = accounts[0]
        return {"accounts": accounts, "target": target}

    def create(self, label: str, *, profile_key: str | None = None) -> Person:
        name = str(label).strip()
        if not name or len(name) > 80 or any(ord(ch) < 32 for ch in name):
            raise ValueError("人物显示名须为 1–80 个可见字符")
        if profile_key is not None and (not profile_key.strip() or len(profile_key) > 120
                or any(ord(ch) < 32 for ch in profile_key)):
            raise ValueError("画像键不正确")
        with self._mutation():
            data = self._read()
            if profile_key is not None and any(
                    profile_key in _keys(row)
                    for row in data["people"].values()):
                raise ValueError("这份画像已经连接到一个身份")
            person_id = "person_" + uuid.uuid4().hex
            data["people"][person_id] = {"label": name, "accounts": [],
                                         "profile_key": profile_key}
            self._write(data)
            return Person(person_id, name, (), profile_key, (profile_key,) if profile_key else ())

    def rename(self, person_id: str, label: str, *, revision: str, confirmed: bool = False) -> None:
        if not confirmed:
            raise PermissionError("请先确认人物称呼")
        name = str(label).strip()
        if not 1 <= len(name) <= 80 or not name.isprintable():
            raise ValueError("人物显示名须为 1–80 个可见字符")
        with self._mutation():
            data = self._read()
            if revision != _revision(data) or person_id not in data["people"]:
                raise ValueError("身份已变化，请刷新后重新确认")
            row = data["people"][person_id]
            if not person_display_name(name, _keys(row)):
                raise ValueError("请输入人物称呼，画像键不能用作显示名")
            row["label"] = name
            self._write(data)

    def bind(self, person_id: str, account: str, *, confirmed: bool = False) -> Person:
        """Only a confirmed administrative action may link an account."""
        if not confirmed:
            raise PermissionError("跨来源身份归并必须由管理者确认")
        if not _ACCOUNT.fullmatch(str(account)):
            raise ValueError("账号须为 platform:stable-id 格式")
        with self._mutation():
            data = self._read()
            row = data["people"].get(person_id)
            if row is None:
                raise KeyError("人物不存在")
            for key, other in data["people"].items():
                if account in other["accounts"]:
                    if key == person_id:
                        return Person(person_id, row["label"], tuple(row["accounts"]),
                                      row.get("profile_key"), tuple(_keys(row)))
                    raise ValueError("账号已经绑定到另一人物；须先显式解绑")
            row["accounts"].append(account)
            self._write(data)
            return Person(person_id, row["label"], tuple(row["accounts"]),
                          row.get("profile_key"), tuple(_keys(row)))

    def unbind(self, account: str, *, expected_person_id: str | None = None,
               revision: str | None = None, confirmed: bool = False) -> bool:
        """Revoke only the binding confirmed from a specific book snapshot."""
        if not confirmed:
            raise PermissionError("解绑必须由管理者确认")
        if not _ACCOUNT.fullmatch(str(account)):
            raise ValueError("账号须为 platform:stable-id 格式")
        with self._mutation():
            data = self._read()
            if (not isinstance(expected_person_id, str)
                    or not _PERSON.fullmatch(expected_person_id)
                    or not isinstance(revision, str) or revision != _revision(data)):
                raise ValueError("身份关联已变化，请刷新后重新确认")
            row = data["people"].get(expected_person_id)
            if row is None or account not in row["accounts"]:
                raise ValueError("账号归属已变化，请刷新后重新确认")
            row["accounts"].remove(account)
            self._write(data)
            return True


    def snapshot(self) -> tuple[list[Person], str]:
        people, revision, _planner = self.planning_snapshot()
        return people, revision

    def read_view(self) -> IdentityReadView:
        people, revision = self.snapshot()
        return IdentityReadView(self, tuple(people), revision)

    def planning_snapshot(self):
        """One read: people, revision and a pure plan function for that same read.

        The planner grants no write authority. Mutations must still compare their
        expected revision/plan against the current book under the write lock.
        """
        with self._lock:
            data = self._read()
            people = [Person(k, v["label"], tuple(v["accounts"]), v.get("profile_key"),
                             tuple(_keys(v))) for k, v in data["people"].items()]
            return people, _revision(data), lambda keys, accounts: self._plan(data, keys, accounts)

    @staticmethod
    def _plan(data: dict[str, Any], keys: list[str], accounts: list[str]) -> dict[str, Any]:
        if (not keys or len(keys) > 64 or any(not isinstance(k, str) or not k.strip()
                or len(k) > 120 or any(ord(c) < 32 for c in k) for k in keys)):
            raise ValueError("须选择有效画像")
        if any(not isinstance(a, str) or not _ACCOUNT.fullmatch(a) for a in accounts):
            raise ValueError("账号格式不正确")
        profiles, linked, involved = set(keys), set(accounts), set()
        changed = True
        while changed:
            changed = False
            for key, row in data["people"].items():
                if key not in involved and (profiles.intersection(_keys(row))
                                            or linked.intersection(row["accounts"])):
                    involved.add(key)
                    profiles.update(_keys(row))
                    linked.update(row["accounts"])
                    changed = True
        if len(profiles) > 64 or len(linked) > 128:
            raise ValueError("关联范围过大，请分次核对")
        return {"profile_keys": sorted(profiles), "accounts": sorted(linked),
                "person_ids": sorted(involved), "revision": _revision(data)}

    def plan(self, keys: list[str], accounts: list[str]) -> dict[str, Any]:
        with self._lock:
            return self._plan(self._read(), keys, accounts)

    def associate(self, keys: list[str], accounts: list[str], *,
                  expected: dict[str, Any], confirmed: bool = False) -> Person:
        """A symmetric, metadata-only union; the displayed direction grants nothing."""
        if not confirmed:
            raise PermissionError("须确认这些画像与账号属于同一个人")
        with self._mutation():
            data = self._read()
            plan = self._plan(data, keys, accounts)
            if expected != plan:
                raise ValueError("身份关联已变化，请刷新后重新确认完整范围")
            ids = plan["person_ids"]
            winner = ids[0] if ids else "person_" + uuid.uuid4().hex
            primary = plan["profile_keys"][0]
            labels = {name for key in ids if (name := person_display_name(
                data["people"][key]["label"], _keys(data["people"][key])))}
            label = next(iter(labels)) if len(labels) == 1 else ""
            for key in ids:
                del data["people"][key]
            data["people"][winner] = {"label": label, "profile_key": primary,
                                      "profile_keys": plan["profile_keys"],
                                      "accounts": plan["accounts"]}
            self._write(data)
            return Person(winner, label, tuple(plan["accounts"]), primary,
                          tuple(plan["profile_keys"]))

    def separate(self, profile_key: str, accounts: list[str], *, revision: str,
                 confirmed: bool = False) -> None:
        """Split an explicitly selected portrait without moving any portrait bytes."""
        if not confirmed:
            raise PermissionError("须明确确认分开这份画像")
        with self._mutation():
            data = self._read()
            if revision != _revision(data):
                raise ValueError("身份关联已变化，请刷新后重新确认")
            selected = [(k, v) for k, v in data["people"].items() if profile_key in _keys(v)]
            if len(selected) != 1 or len(_keys(selected[0][1])) < 2:
                raise ValueError("这份画像没有可分开的其他关联画像")
            _key, row = selected[0]
            if any(a not in row["accounts"] for a in accounts):
                raise ValueError("分开范围与当前账号记录不符")
            remaining = sorted(set(_keys(row)) - {profile_key})
            old_primary = row.get("profile_key")
            row["profile_keys"], row["profile_key"] = remaining, remaining[0]
            if row["label"] == old_primary:
                row["label"] = remaining[0]
            row["accounts"] = sorted(set(row["accounts"]) - set(accounts))
            new_id = "person_" + uuid.uuid4().hex
            data["people"][new_id] = {"label": profile_key, "profile_key": profile_key,
                                       "profile_keys": [profile_key], "accounts": sorted(accounts)}
            self._write(data)
