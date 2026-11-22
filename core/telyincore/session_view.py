"""Recipient-scoped model view over a shared, unchanged event session.

The persisted timeline is not split. An external request may read only its
room or a person's explicitly linked private accounts. Old unscoped records
are never retroactively assigned to a recipient by inspecting their text.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence

from .note import Record


GROUP_RECENT_MESSAGES = 12


def recent_group_records(rows: Sequence[Record], scope: str) -> tuple[Record, ...]:
    """Request-local group context; the persisted group timeline stays whole.

    Count incoming messages rather than tool calls, so a busy agent cannot
    evict the current human exchange merely by producing tool receipts.
    """
    group = [row for row in rows if row.scope == scope]
    incoming = [index for index, row in enumerate(group)
                if row.kind == "message" and row.role == "user"]
    if len(incoming) <= GROUP_RECENT_MESSAGES:
        return tuple(group)
    cutoff = incoming[-GROUP_RECENT_MESSAGES]
    # A batch can contain more than one message at the same timestamp. Keep
    # the whole edge batch instead of silently dropping its first speaker.
    edge_ms = group[cutoff].ts_ms
    while cutoff > 0 and edge_ms > 0 and group[cutoff - 1].ts_ms == edge_ms:
        cutoff -= 1
    return tuple(group[cutoff:])


@dataclass(frozen=True)
class GroupViewPolicy:
    """An explicit adapter contract for exactly one trusted conversation."""

    scope: str
    message_pattern: str
    send_anchor_fields: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scope, str) or not self.scope.startswith("group:"):
            raise ValueError("group_policy_scope_invalid")
        if not self.scope.rpartition(":")[2]:
            raise ValueError("group_policy_scope_invalid")
        if not isinstance(self.message_pattern, str) or not self.message_pattern.startswith("^"):
            raise ValueError("group_policy_pattern_invalid")
        try:
            marker = re.compile(self.message_pattern)
        except re.error as error:
            raise ValueError("group_policy_pattern_invalid") from error
        if marker.groups != 1:
            raise ValueError("group_policy_anchor_group_invalid")
        fields = self.send_anchor_fields
        if not isinstance(fields, tuple) or not fields:
            raise ValueError("group_policy_tools_invalid")
        names = []
        for pair in fields:
            if (not isinstance(pair, tuple) or len(pair) != 2
                    or any(not isinstance(x, str) or not x for x in pair)):
                raise ValueError("group_policy_tools_invalid")
            names.append(pair[0])
        if len(names) != len(set(names)):
            raise ValueError("group_policy_tools_duplicate")


def group_request_records(rows: Sequence[Record], scope: str, *,
                          policy: GroupViewPolicy | None = None) -> tuple[Record, ...]:
    """Keep sends only when their triggering message is in this group slice.

    A delayed answer to an older @ must not become fresh conversational
    context merely because the send itself happened recently.
    """
    if not isinstance(policy, GroupViewPolicy) or policy.scope != scope:
        raise ValueError("explicit_matching_group_policy_required")
    recent = recent_group_records(rows, scope)
    marker = re.compile(policy.message_pattern)
    anchor_fields = dict(policy.send_anchor_fields)
    current_ids = {match.group(1) for row in recent
                   if row.kind == "message" and row.role == "user"
                   if (match := marker.match(row.text))}
    kept: list[Record] = []
    call_decisions: dict[str, list[bool]] = {}
    reference_decisions: dict[str, tuple[str, bool]] = {}
    for row in recent:
        if row.kind == "function_call" and row.name in anchor_fields:
            try:
                args = json.loads(row.text)
            except (TypeError, ValueError):
                args = {}
            anchor = args.get(anchor_fields[row.name]) if isinstance(args, dict) else None
            keep = str(anchor) in current_ids
            reference = getattr(row, 'tool_ref', '')
            if reference:
                if reference in reference_decisions:
                    raise ValueError('duplicate scoped tool reference')
                reference_decisions[reference] = (row.name, keep)
            else:
                call_decisions.setdefault(row.name, []).append(keep)
            if not keep:
                continue
        if row.kind == "function_result" and row.name in anchor_fields:
            reference = getattr(row, 'tool_ref', '')
            if reference:
                decision = reference_decisions.pop(reference, None)
                if decision is None or decision != (row.name, True): continue
            else:
                decisions = call_decisions.get(row.name, [])
                if not decisions or not decisions.pop(0): continue
        kept.append(row)
    return tuple(kept)


def event_scope(item: Any) -> str:
    """Derive a conversation boundary from trusted envelope fields."""
    if str(getattr(item, "origin", "") or "") != "external":
        from .owner_tick import route
        from .continuation import route as continuation_route
        return route(item) or continuation_route(item)
    account = str(getattr(item, "sender_key", "") or "")
    source = str(getattr(item, "source", "") or "")
    meta = getattr(item, "meta", None)
    room = meta.get("conversation") if isinstance(meta, dict) else None
    if isinstance(room, str) and room.startswith("group:") and source:
        group = room.partition(":")[2]
        return "group:" + source + ":" + group if group else ""
    if account and (not isinstance(room, str) or room.startswith("private:")):
        return "private:" + account
    return ""


def batch_scope(batch: Sequence[Any]) -> str:
    from .owner_tick import route, OWNER_SCOPE
    if any(route(item) for item in batch):
        return OWNER_SCOPE if all(route(item) for item in batch) else "mixed"
    from .continuation import is_continuation, route as continuation_route
    if any(is_continuation(item) for item in batch):
        return continuation_route(batch[0]) if len(batch) == 1 else 'mixed'
    external = [item for item in batch
                if str(getattr(item, "origin", "") or "") == "external"]
    if not external:
        return ""  # autonomous wake has no external recipient
    scopes = {event_scope(item) for item in external}
    return scopes.pop() if len(scopes) == 1 and "" not in scopes else "mixed"

def owner_visible(item: Any, linked: Callable[[str], Sequence[str]]) -> bool:
    """Only confirmed private accounts may appear in the owner's terminal.

    The group boundary wins even when the sender owns an account in that
    group. Legacy records without a trustworthy scope cannot be attributed.
    """
    owner = "console:local-owner"
    try:
        accounts = {str(account) for account in linked(owner)}
    except Exception:  # a broken identity book must not widen visibility
        accounts = set()
    accounts.add(owner)
    allowed = {"private:" + account for account in accounts}
    if isinstance(item, Record):
        return item.scope in allowed
    if str(getattr(item, "origin", "") or "") == "internal":
        return True
    return event_scope(item) in allowed


#: **机器自己那几句运行提示** —— 它们在**任何场景**都留着（2026-10-17 修）。
#:
#: ⚠️⚠️ **修的是什么**（离线复现过）：注入走的是 `console._inject_a_note` ——
#:   它推的事件 `origin="internal"`、`sender_key=None`、没有会话 meta
#:    ⇒ `event_scope()` 交回**空串** ⇒ 落进会话时 `Record.scope == ""`
#:    ⇒ 而这一层的三条路都只认「`is_head` 的 system」或「`scope == 本房间`」——
#:    注入那几条**两条都不占** ⇒ **被筛掉了**。
#:    实测的后果：跑到第 10 轮那句提醒、交接前后那两句、整理态那条，**全都到不了它眼前**；
#:    而生产里一定走这条（`launch.py` 递了 `linked_accounts` ⇒ 用的是 `ScopedSession`）。
#:
#: ⚠️ **为什么只放这两种 `kind`**（这一格是"可见性"，放宽要说得清）：
#:   · `"notice"` —— 软阈那句（`SOFT_ROUNDS_NOTE`，**固定文案**）；
#:   · `"handoff"` —— 交接前那句与醒来那 8 句（`PRESSURE_NOTE` ／ `WAKE_NOTE_LINES`，**固定文案**）。
#:   ⚠️⚠️ **`"handoff_note"` 与 `"consolidate"` 故意不在里面** —— 那两样是
#:   **私人内容的摘要或模型写的话**（交接笔记正文 ／ 整理态那条「浮现」，后者读的就是它的
#:   私人材料）⇒ 放进群请求就等于**把私事复述进群**（设计文档 25 明写不准）。
#:   ⇒ 以后谁要往这两族之外加一种"机器的话"，先回答"它里面会不会有私事"。
THE_MACHINES_OWN_KINDS = ("notice", "handoff")


def _the_machine_said_it(row: Record) -> bool:
    """ 这一条是不是**机器自己那几句运行提示**（不属于任何房间 ⇒ 哪个场景都留着）。"""
    return (str(getattr(row, "kind", "")) in THE_MACHINES_OWN_KINDS
            and str(getattr(row, "role", "")) == "system"
            and not str(getattr(row, "scope", "") or ""))


class ScopedSession:
    """The model's view; writes still go to the one durable session. """

    def __init__(self, session: Any, current: Callable[[], str],
                 linked: Callable[[str], Sequence[str]], *,
                 group_policy: Callable[[str], GroupViewPolicy] | None = None,
                 request_filter: Callable[[], Any] | None = None,
                 recall_available: Callable[[], bool] | None = None,
                 request_revision: Callable[[], Any] | None = None,
                 on_append: Callable[[Record], None] | None = None,
                 append_transaction: Callable[[Record], None] | None = None) -> None:
        self._session = session
        self._current = current
        self._linked = linked
        self._group_policy = group_policy
        self._request_filter = request_filter
        self._request_revision = request_revision
        self._recall_available = recall_available
        self._on_append = on_append
        self._append_transaction = append_transaction

    def _visible(self, *, outbound: bool = False) -> Callable[[Record], bool] | None:
        base = self._recipient_visible(outbound=outbound)
        branch = self._request_filter() if callable(self._request_filter) else None
        if branch is None:
            return base
        return lambda row: ((base is None or base(row))
                            and (bool(row.is_head and row.role == "system")
                                 or _the_machine_said_it(row) or branch(row)))

    def _recipient_visible(self, *, outbound: bool = False) -> Callable[[Record], bool] | None:
        scope = self._current()
        if not scope:
            return None  # internal work, not a reply to an external recipient
        if scope.startswith("group:"):
            if outbound:
                if not callable(self._group_policy):
                    raise ValueError("explicit_group_policy_provider_required")
                policy = self._group_policy(scope)
                recent = group_request_records(self._session.records(), scope, policy=policy)
                selected = {id(row) for row in recent}
                return lambda row: (_the_machine_said_it(row)
                                    or bool(row.is_head and row.role == "system")
                                    or id(row) in selected)
            return lambda row: (_the_machine_said_it(row)
                                or bool(row.is_head and row.role == "system")
                                or row.scope == scope)
        if scope.startswith("private:"):
            account = scope[len("private:"):]
            accounts = self._private_accounts(account)
            allowed = {"private:" + one for one in accounts}
            return lambda row: (_the_machine_said_it(row)
                                or bool(row.is_head and row.role == "system")
                                or row.scope in allowed)
        return lambda row: (_the_machine_said_it(row)
                            or bool(row.is_head and row.role == "system"))

    def outbound(self, head: Any, tail: Any = None) -> Any:
        visible = self._visible(outbound=True)
        scope = self._current() or ""
        from .batch_prompt import BatchTail
        from .owner_tick import OWNER_SCOPE
        owner_layout = (scope == OWNER_SCOPE and isinstance(tail, BatchTail) and tail.provider_replay
                        and tail.incoming.record.scope == scope and tail.observation_slots is not None)
        owner_links = self._private_recall_links(scope, owner_layout=owner_layout)
        if owner_links is None:
            return self._session.outbound(head, tail, visible=visible)
        if owner_layout:
            rows = self._session.outbound(head, tail, visible=visible, anchor_owner=True)
        else:
            rows = self._session.outbound(head, tail, visible=visible, compact_history=True)
        from .host_observations import ObservationRequest
        if isinstance(rows, ObservationRequest) and getattr(tail, 'provider_replay', False):
            rows.check_authority = self._observation_authority(scope, owner_links, rows, owner_layout=owner_layout)
        return rows

    def _private_accounts(self, account):
        try:
            return frozenset([account, *(str(one) for one in self._linked(account))])
        except Exception:
            return frozenset([account])

    def _private_recall_links(self, scope, *, owner_layout=False):
        if (owner_layout or scope.startswith('private:qq:')) and self._recall_available and self._recall_available():
            try:
                links = frozenset(self._linked('console:local-owner'))
                if owner_layout or scope[8:] in links:
                    return links
            except Exception:
                pass
        return None

    def _observation_authority(self, scope, owner_links, rows, *, owner_layout=False):
        # Keep no retrieved text. The production branch revision is independent
        # of history length. Custom views can instead recheck their anchored rows.
        from .batch_prompt import AnchoredHost
        session = self._session
        epoch, boundary = rows.epoch, rows.boundary
        accounts = self._private_accounts(scope[8:])
        revision = self._request_revision() if self._request_revision else None
        owners = () if self._request_revision else tuple({
            id(row.record): row.record for row in rows if isinstance(row, AnchoredHost)}.values())

        def check():
            if (self._current() != scope or self._private_recall_links(scope, owner_layout=owner_layout) != owner_links
                    or self._private_accounts(scope[8:]) != accounts
                    or session._tool_evidence_epoch is not epoch or len(session._records) != boundary):
                raise ValueError('provider observation authority changed')
            if self._request_revision:
                if self._request_revision() != revision:
                    raise ValueError('provider observation branch changed')
            elif self._request_filter:
                allowed = self._request_filter()
                if allowed is not None and not all(allowed(row) for row in owners):
                    raise ValueError('provider observation source changed')
        return check

    def records(self) -> tuple[Record, ...]:
        visible = self._visible()
        rows = self._session.records()
        return rows if visible is None else tuple(row for row in rows if visible(row))

    def quote_source_visible(self, receipt_scope, source_scope):
        """Recheck the recipient and read capability before inserting original text."""
        from .retrieval_scope import OWNER, valid_scope
        if not valid_scope(source_scope) or receipt_scope != self._current():
            return False
        if self._recall_available is not None and not self._recall_available():
            return False
        if receipt_scope == OWNER or receipt_scope == source_scope:
            return True
        if receipt_scope.startswith('private:') and source_scope.startswith('private:'):
            return source_scope[8:] in self._private_accounts(receipt_scope[8:])
        return False

    def tool_evidence_snapshot(self):
        epoch, count, entries = self._session.tool_evidence_snapshot()
        visible = self._visible(outbound=True)
        return epoch, count, tuple(entry for entry in entries
                                   if visible is None or visible(entry[1]))

    def scoped_record(self, record: Record) -> Record:
        if record.role == "assistant":
            return replace(record, scope=self._current())
        return record

    def append(self, record: Record) -> None:
        record = self.scoped_record(record)
        if self._append_transaction is not None:
            self._append_transaction(record)
        else:
            self._session.append(record)
        if callable(self._on_append):
            self._on_append(record)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)
