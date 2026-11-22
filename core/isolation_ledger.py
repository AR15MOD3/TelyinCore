"""Pre-debit ledger for the isolated acceptance path. No network/model.

Revision 3 (batch59 semantic review): the tool budget is charged **per
dispatched call**, the read-only boundary is enforced in code, and a dispatcher
``ended`` signal is propagated to the remaining calls as barrier receipts instead
of being dropped.

Scope note: the isolated whitelist is only ``kb_search`` / ``kb_read``
(read-only, no ``barrier_after`` / ``ends_turn``).  The wrapper still honours an
``ended`` result from the dispatcher, so the signal is not lost even on a
future table that grows a finishing tool.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

#: Model-visible text for a call refused by the isolated tool budget.
TOOL_BUDGET_REFUSAL = "未执行：隔离验收的工具预算已用尽（上限 1 次）；这一条没有派发。"
#: Model-visible text for a call outside the read-only whitelist.
TOOL_NOT_ALLOWED_REFUSAL = (
    "未执行：隔离验收只允许 kb_search / kb_read；{name} 不在只读白名单。")
#: Model-visible text for calls behind a dispatcher ``ended`` signal.
TOOL_STOPPED_REFUSAL = (
    "未执行：这一批前面的调用已经发出停止信号（finish_turn）；后面的调用没有派发。")

DEFAULT_TOOL_NAMES = ("kb_search", "kb_read")


class LedgerRefused(RuntimeError):
    pass


@dataclass
class IsolationLedger:
    max_generate: int = 2
    max_transport: int = 4
    max_tools: int = 1
    counts: dict[str, int] = field(default_factory=lambda: {
        "generate": 0, "transport": 0, "tools": 0})
    refused: dict[str, int] = field(default_factory=lambda: {
        "generate": 0, "transport": 0, "tools": 0})
    entries: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock,
                                   repr=False, compare=False)

    def limit(self, kind: str) -> int:
        return {"generate": self.max_generate, "transport": self.max_transport,
                "tools": self.max_tools}[kind]

    def _refuse_locked(self, kind: str, count: int, detail: str) -> dict[str, Any]:
        self.refused[kind] += count
        entry = {"kind": kind, "detail": detail, "count": count,
                 "status": "refused", "executed": False}
        self.entries.append(entry)
        return entry

    def reserve(self, kind: str, count: int = 1, detail: str = "") -> dict[str, Any]:
        if type(count) is not int or count < 1:
            raise ValueError("count must be positive")
        with self._lock:
            if self.counts[kind] + count > self.limit(kind):
                self._refuse_locked(kind, count, detail)
                raise LedgerRefused(f"budget:{kind}")
            self.counts[kind] += count
            entry = {"kind": kind, "detail": detail, "count": count,
                     "status": "started", "executed": True}
            self.entries.append(entry)
            return entry

    def refuse(self, kind: str, detail: str, count: int = 1) -> dict[str, Any]:
        """Record a policy refusal that is not a budget refusal."""
        if type(count) is not int or count < 1:
            raise ValueError("count must be positive")
        with self._lock:
            return self._refuse_locked(kind, count, detail)

    def finish(self, entry: dict[str, Any], status: str) -> None:
        with self._lock:
            entry["status"] = status

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "limits": {"generate": self.max_generate,
                           "transport": self.max_transport,
                           "tools": self.max_tools},
                "started": dict(self.counts), "refused": dict(self.refused),
                "entries": [dict(one) for one in self.entries],
                "consistent": (
                    sum(one["count"] for one in self.entries if one["executed"])
                    == sum(self.counts.values())
                    and sum(one["count"] for one in self.entries if not one["executed"])
                    == sum(self.refused.values())
                ),
            }


class BudgetTransport:
    def __init__(self, inner: Any, ledger: IsolationLedger):
        self.inner = inner
        self.ledger = ledger

    async def stream_chat(self, *args, **kwargs):
        entry = self.ledger.reserve(
            "transport", detail=str(kwargs.get("path") or "stream_chat"))
        try:
            async for chunk in self.inner.stream_chat(*args, **kwargs):
                yield chunk
        except GeneratorExit:
            # The consumer closed the stream after the terminal event (normal
            # success) or because it aborted.  "closed" is the honest state;
            # calling it "error" made every successful transport look failed.
            self.ledger.finish(entry, "closed")
            raise
        except BaseException:
            self.ledger.finish(entry, "error")
            raise
        else:
            self.ledger.finish(entry, "ok")

    async def aclose(self):
        close = getattr(self.inner, "aclose", None)
        if callable(close):
            return await close()
        return None


class BudgetGenerate:
    def __init__(self, inner: Any, ledger: IsolationLedger):
        self.inner = inner
        self.ledger = ledger

    async def __call__(self, *args, **kwargs):
        entry = self.ledger.reserve("generate", detail="outer")
        try:
            got = await self.inner(*args, **kwargs)
        except BaseException:
            self.ledger.finish(entry, "error")
            raise
        else:
            self.ledger.finish(entry, "ok")
            return got


class BudgetRunTools:
    """Run one dispatcher batch while charging **one tool unit per call**.

    A refused call never reaches ``inner``; it becomes a ``Receipt`` with
    ``stage="receipt"`` (the same stage the dispatcher uses for close-out
    receipts).  Returning a result — instead of raising — is load-bearing:
    ``round.py`` calls ``run_tools`` outside its own try/except, so an escaping
    ``LedgerRefused`` aborts the batch and then the whole service.

    The read-only boundary is **enforced here**, not only by the launch-side
    table: a name outside ``allowed_names`` is refused before any handler can
    run.  Dispatcher ``ended`` is propagated: calls after it get barrier
    receipts and are never dispatched.  Receipts from successful inner calls are
    merged, not dropped.
    """

    def __init__(self, inner: Any, ledger: IsolationLedger,
                 allowed_names: tuple[str, ...] = DEFAULT_TOOL_NAMES):
        self.inner = inner
        self.ledger = ledger
        self.allowed_names = tuple(allowed_names)
        self._receipt_lock = threading.RLock()
        self._receipt_batches = []
        self._receipt_busy = False
        self._receipt_incomplete = False
        self._inner_receipt_state = getattr(inner, 'receipt_shutdown_state', None)

    def receipt_shutdown_state(self):
        observer = self._inner_receipt_state
        state = observer() if callable(observer) else {'pending': False, 'incomplete': False}
        with self._receipt_lock:
            return {'pending': self._receipt_busy or state['pending'],
                    'incomplete': self._receipt_incomplete or state['incomplete']}

    def settled_receipts(self, calls):
        asked = tuple(calls)
        with self._receipt_lock:
            for batch, identity, receipts in self._receipt_batches:
                if len(batch) == len(asked) and all(a is b for a, b in zip(batch, asked)):
                    if tuple((c.call_id, c.name) for c in asked) != identity:
                        raise ValueError('changed budget invocation')
                    return tuple(receipts)
        raise ValueError('foreign budget invocation')

    async def __call__(self, calls, *args, **kwargs):
        batch = tuple(calls)
        with self._receipt_lock:
            if self._receipt_busy:
                raise RuntimeError('budget invocation busy')
            if (len({id(c) for c in batch}) != len(batch)
                    or len({c.call_id for c in batch}) != len(batch)
                    or any(c is old for c in batch for previous, _, _ in self._receipt_batches for old in previous)):
                raise ValueError('duplicate or reused budget call')
            inner = self.inner
            query = getattr(inner, 'settled_receipts', None)
            receipts = []
            self._receipt_batches.append((batch, tuple((c.call_id, c.name) for c in batch), receipts))
            self._receipt_busy = True
        try:
            return await self._run_batch(batch, receipts, inner, query, *args, **kwargs)
        except BaseException:
            # An interrupted batch may have unknown suffix work or failed finish.
            # Retain responsibility even if a caller consumes the exception.
            with self._receipt_lock:
                self._receipt_incomplete = True
            raise
        finally:
            with self._receipt_lock:
                self._receipt_busy = False

    async def _run_batch(self, batch, receipts, inner, query, *args, **kwargs):
        from telyincore.dispatch import DispatchResult, Receipt

        ended = False
        for call in batch:
            name = str(getattr(call, "name", "") or "")
            call_id = str(getattr(call, "call_id", "") or "")
            if ended:
                receipts.append(Receipt(
                    call_id=call_id, name=name,
                    text=TOOL_STOPPED_REFUSAL, stage="barrier"))
                continue
            if name not in self.allowed_names:
                self.ledger.refuse("tools", detail="not-allowed:" + name)
                receipts.append(Receipt(
                    call_id=call_id, name=name,
                    text=TOOL_NOT_ALLOWED_REFUSAL.format(name=name or "<empty>"),
                    stage="receipt"))
                continue
            try:
                entry = self.ledger.reserve(
                    "tools", count=1, detail="dispatch:" + name)
            except LedgerRefused:
                receipts.append(Receipt(
                    call_id=call_id, name=name,
                    text=TOOL_BUDGET_REFUSAL, stage="receipt"))
                continue
            try:
                got = await inner((call,), *args, **kwargs)
            except BaseException as primary:
                try:
                    if not callable(query):
                        raise RuntimeError('inner settled receipts unavailable')
                    receipts.extend(query((call,)))
                except BaseException as error:
                    primary._receipt_harvest_error = error
                    primary.add_note('budget prefix harvest incomplete; no replay')
                try:
                    self.ledger.finish(entry, "error")
                except BaseException as error:
                    primary._budget_finish_error = error
                    primary.add_note('budget finish incomplete; no replay')
                raise
            receipts.extend(getattr(got, "receipts", ()) or ())
            self.ledger.finish(entry, "ok")
            ended = ended or bool(getattr(got, "ended", False))
        return DispatchResult(tuple(receipts), ended)
