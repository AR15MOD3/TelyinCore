"""Owned async tool results and explicitly registered, single-slot blocking I/O.

No ambient tool tables, Bot selection, retries, or thread cancellation claims.
"""
from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass, field
import inspect
import threading
from typing import Any, Callable


@dataclass(frozen=True)
class Invocation:
    call_id: str
    name: str
    tool_ref: str = ""
    # An optional revocation fence, not part of the dispatcher's established I/O identity.
    batch_lease: Any = field(default=None, compare=False, repr=False, kw_only=True)


current_tool_references = contextvars.ContextVar("core_tool_references", default=None)


current_invocation: contextvars.ContextVar[Invocation | None] = contextvars.ContextVar(
    "core_tool_invocation", default=None)


# Actual consuming guards, not a newly minted tool/system invocation. A worker
# return abandoned by cancellation must remain attached to these existing leases
# even if an intermediate handler catches or replaces CancelledError.
_managed_scopes = contextvars.ContextVar("core_managed_consume_scopes", default=())


class ManagedAwaitable:
    """Keep a plugin lease until the entire returned dependency chain drains."""

    def __init__(self, value: Any, before: Callable, release: Callable[[], None]):
        self._value, self._before, self._release = value, before, release
        self._started = self._closed = self._released = False
        self._finished = False
        self._consumer_task = None
        self._dependencies = []
        self._unproven = False
        self._watched = set()
        self._listeners = []
        self._watch(value)

    def _retain_existing(self, value):
        if self._released:
            raise RuntimeError("cannot attach work to an already released consume scope")
        if not any(one is value for one in self._dependencies):
            self._dependencies.append(value)
            self._watch(value)

    def _watch(self, value):
        if asyncio.isfuture(value) and id(value) not in self._watched:
            self._watched.add(id(value))
            # A done container needs result inspection, not registration on a
            # possibly closed foreign loop. Pending callback failure cannot
            # turn an unknown live dependency into a clean release.
            try:
                if not value.done():
                    value.add_done_callback(lambda _future: self._release_if_done())
            except BaseException:
                self._unproven = True
        elif isinstance(value, ManagedAwaitable) and id(value) not in self._watched:
            self._watched.add(id(value))
            if not value._released:
                value._listeners.append(self._release_if_done)

    def _settled(self, value, seen):
        if not inspect.isawaitable(value):
            return True
        # A completed Future can contain another live Future/Task. Inspect its
        # payload before releasing ownership, even if nobody ever awaits us.
        ident = id(value)
        if ident in seen or len(seen) >= 64:
            self._unproven = True
            return False  # Cyclic/deeper unknown work is not a clean drain.
        seen.add(ident)
        if asyncio.isfuture(value):
            self._watch(value)
            try:
                if not value.done():
                    return False
            except BaseException:
                self._unproven = True
                return False
            try:
                nested = value.result()  # Observe late errors; never cancel it.
            except BaseException as exc:
                try:
                    if value.cancelled() or value.exception() is exc:
                        return True  # Genuine terminal error/cancellation observed.
                except BaseException:
                    pass
                self._unproven = True
                return False  # A broken result observer is not terminal proof.
            return (not inspect.isawaitable(nested)
                    or self._settled(nested, seen))
        if isinstance(value, ManagedAwaitable):
            self._watch(value)
            if not value._started:
                try:
                    value.close()
                except RuntimeError:
                    return False
            return value._released
        if inspect.iscoroutine(value):
            state = inspect.getcoroutinestate(value)
            if state == inspect.CORO_CREATED:
                value.close()  # A cold nested coroutine has not performed work.
                return True
            if state == inspect.CORO_CLOSED:
                return True
        self._unproven = True
        return False  # No proof that a suspended/opaque protocol has stopped.

    def _release_if_done(self) -> None:
        if self._released or self._unproven or (self._started and not self._finished):
            return
        if (not self._settled(self._value, set())
                or any(not self._settled(value, set()) for value in self._dependencies)):
            return
        self._released = True
        self._release()
        listeners, self._listeners = self._listeners, []
        for listener in listeners:
            listener()

    async def _consume(self):
        self._consumer_task = asyncio.current_task()
        token = _managed_scopes.set((*_managed_scopes.get(), self))
        try:
            await self._before()
            result = await self._value
            if inspect.isawaitable(result):
                self._value = result
                self._unproven = False  # The old root was actually consumed, not passively guessed.
                self._watch(result)  # Retain the new root; never execute it implicitly.
                raise TypeError("plugin returned a nested awaitable, not a final value")
            self._value = None  # A final value proves this await protocol completed.
            self._unproven = False  # Positive consumption proof supersedes a failed observer.
            return result
        except BaseException as exc:
            # An observer fault alone cannot prove drain. An actual native Future
            # await may, however, have raised its genuine terminal failure. Check
            # the base state/result, not an overridable observational method.
            value = self._value
            if isinstance(value, asyncio.Future) and asyncio.Future.done(value):
                try:
                    asyncio.Future.result(value)
                except BaseException as terminal:
                    if (terminal is exc or (asyncio.Future.cancelled(value)
                            and isinstance(exc, asyncio.CancelledError))):
                        self._value = None
                        self._unproven = False
            raise
        finally:
            self._finished = True
            _managed_scopes.reset(token)
            self._release_if_done()

    def __await__(self):
        if self._started or self._closed:
            raise RuntimeError("managed tool result can only be awaited once")
        self._started = True
        return self._consume().__await__()

    def close(self) -> None:
        if self._started:
            raise RuntimeError("join an active managed tool result before closing")
        if self._closed:
            return
        if inspect.iscoroutine(self._value):
            state = inspect.getcoroutinestate(self._value)
            if state not in (inspect.CORO_CREATED, inspect.CORO_CLOSED):
                raise RuntimeError("active coroutine requires explicit join/cleanup")
            self._value.close()
        elif not (asyncio.isfuture(self._value)
                  or isinstance(self._value, ManagedAwaitable)):
            raise RuntimeError("opaque tool awaitable requires explicit await/cleanup")
        self._closed = True
        self._release_if_done()

    def __del__(self):
        if not self._started:
            try:
                self.close()
            except Exception:
                pass  # Unsupported opaque awaitables retain their responsibility.


def _invocation_reference() -> str:
    invocation = current_invocation.get()
    return invocation.tool_ref if isinstance(invocation, Invocation) else ""


@dataclass(frozen=True)
class WorkOwner:
    bot: str
    scope: str
    call_id: str
    tool: str
    # Bind legacy four-argument plugin handlers to the same reference as the
    # dispatcher. Reusing a provider call ID in another round cannot transfer it.
    tool_ref: str = field(default_factory=_invocation_reference, init=False)


class OwnedBlockingIO:
    """One explicitly owned I/O worker; cancellation waits for real termination.

    Awaiting callers may outlive a bounded stop budget. In that case the owner,
    worker and plugin lease remain live; no success/clean drain is fabricated.
    Normal returned values are handed to the caller unchanged. If cancellation
    abandons an awaitable return, observe it passively and retain this slot plus
    the already active consume scopes until that entire dependency chain settles.
    Cold abandoned coroutines are closed, never started; opaque work stays pending.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._job = None
        self._retained_return = None

    def assert_idle(self) -> None:
        with self._lock:
            if self._job is not None:
                raise RuntimeError("owned blocking I/O has not actually drained")

    @property
    def owner(self) -> WorkOwner | None:
        with self._lock:
            return self._job[0] if self._job is not None else None

    async def run(self, owner: WorkOwner, operation: Callable[[threading.Event], Any]):
        invocation = current_invocation.get()
        if (not isinstance(owner, WorkOwner) or not owner.bot or not owner.scope
                or invocation != Invocation(owner.call_id, owner.tool, owner.tool_ref)):
            raise ValueError("blocking I/O requires a frozen dispatcher invocation owner")
        return await self._run(owner, operation)

    async def run_system(self, ticket, owner: WorkOwner, operation):
        from .plugins import _validate_system_call
        _validate_system_call(ticket, owner)
        return await self._run(owner, operation)

    async def _run(self, owner, operation):
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        loop = asyncio.get_running_loop()
        arrived = loop.create_future()
        cancel = threading.Event()
        box = {}
        context = contextvars.copy_context()
        parents = _managed_scopes.get()
        if any(parent._finished for parent in parents):
            raise RuntimeError("owned I/O cannot reuse a completed consume scope")

        def notify():
            if not arrived.done():
                arrived.set_result(None)

        def work():
            try:
                box["value"] = context.run(operation, cancel)
            except BaseException as exc:
                box["error"] = exc
            finally:
                try:
                    loop.call_soon_threadsafe(notify)
                except RuntimeError:
                    pass  # A dead owner loop is not evidence of a clean drain.

        thread = threading.Thread(target=work, name="core-owned-read-io", daemon=False)
        job = (owner, thread, cancel)
        with self._lock:
            if self._job is not None:
                raise RuntimeError("owned blocking I/O slot is busy; no work queued")
            self._job = job
        try:
            # A child inherits scopes but does not own the parent's lifetime.
            # Borrow its real Task before work starts, not just a returned value.
            for parent in parents:
                if parent._consumer_task is not task:
                    parent._retain_existing(task)
            thread.start()
        except BaseException:
            with self._lock:
                self._job = None
            raise

        def release_job():
            with self._lock:
                if self._job is job:
                    self._job = None
                    self._retained_return = None

        def retain_abandoned(value):
            if not inspect.isawaitable(value):
                return False

            async def no_admission():
                pass  # Passive observation of returned work, not fresh execution.

            dependency = ManagedAwaitable(value, no_admission, release_job)
            with self._lock:
                self._retained_return = dependency
            for parent in parents:
                parent._retain_existing(dependency)
            try:
                dependency.close()
            except RuntimeError:
                # A suspended/opaque dependency is not proven stopped. No new
                # observer task executes it; ownership deliberately stays pending.
                dependency._release_if_done()
            return True

        async def finish():
            await asyncio.shield(arrived)
            # Notification can race the final return of Thread.run. Never use
            # future.done() alone as evidence that the worker has truly exited.
            while thread.is_alive():
                await asyncio.sleep(0)
            if "error" in box:
                error = box["error"]
                if isinstance(error, Exception):
                    raise error
                raise RuntimeError("blocking worker aborted: " + type(error).__name__)
            return box.get("value")

        waiter = asyncio.create_task(finish(), name="core-owned-read-io-drain")
        cancelled = retained = False
        try:
            while True:
                try:
                    result = await asyncio.shield(waiter)
                    break
                except asyncio.CancelledError:
                    cancel.set()
                    cancelled = True
                    if waiter.cancelled():
                        # Broad shutdown may cancel only the observer. Rebuild
                        # observation without releasing the worker or its owner.
                        waiter = asyncio.create_task(finish(), name="core-owned-read-io-drain")
                        continue
                    if waiter.done():
                        # Cancellation can win after finish already returned.
                        # Retrieve its value as well as observing late exceptions.
                        try:
                            result = waiter.result()
                        except BaseException:
                            result = None
                        break
                except BaseException:
                    if cancelled:
                        raise asyncio.CancelledError from None
                    raise
            if cancelled or (task is not None and task.cancelling()):
                retained = retain_abandoned(result)
                raise asyncio.CancelledError
            return result
        finally:
            if not retained:
                release_job()
