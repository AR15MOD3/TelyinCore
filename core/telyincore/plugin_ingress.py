"""External plugin events use the application's event store and wake bus."""

from __future__ import annotations

import asyncio
import contextvars
from concurrent.futures import Future
from threading import Lock, Thread, Timer, Event, current_thread
from contextlib import nullcontext
from typing import Any, Callable, TypeVar


T = TypeVar("T")

# Host-only thread birth -> exact inner asyncio task relation. Not a caller
# completion flag; cancelled raw/returned tasks without this proof stay failed.
_native_task_scope=contextvars.ContextVar('plugin_native_task_scope',default=None)
_native_task_records={}
_native_task_lock=Lock()

def native_cancellation_retired(task,name,instance):
    with _native_task_lock:row=_native_task_records.get(task)
    if row is None:return False
    owner,thread,work,plugin,original,coro=row
    return (plugin==name and original is instance and task.get_coro() is coro
        and task.done() and task.cancelled() and not thread.is_alive()
        and work.done() and not work.cancelled() and work.exception() is None)

def retire_native_task_relations(name,instance):
    with _native_task_lock:
        rows=[(task,row) for task,row in _native_task_records.items() if row[3]==name and row[4] is instance]
        for task,row in rows:
            owner,thread,work,_,_,coro=row
            if (not task.done() or task.get_coro() is not coro or thread.is_alive()
                    or not work.done() or work.cancelled() or work.exception() is not None
                    or (not task.cancelled() and task.exception() is not None)):
                raise RuntimeError('native task relation still unresolved')
        for task,row in rows:
            _native_task_records.pop(task)
            if task.cancelled():
                with row[0]._lock:row[0]._native_cancelled_retired+=1


class PluginIngress:
    """Store an external event once, then offer that same envelope to the bus.

    Plugins may start before the console has assembled its bus. Events arriving
    during that interval are durable and are delivered when ``bind`` is called.
    Once bound, store access from plugin worker threads runs on the console loop.

    Delivery buffering belongs to this live instance. Reopening EventStore
    recovers stored envelopes, not bus-delivery acknowledgements. Never replay
    every stored envelope on restart: a previous offer may already have caused
    side effects. With recovery_root, persisted envelopes not yet claimed by a consumer
    are handed to the new bus; uncertain attempts remain quarantined. Bus
    acceptance is not model/tool completion and never grants a business ACK.
    """

    def __init__(self, store: Any, *, recovery_root: Any = None) -> None:
        self.store = store
        self._lock = Lock()
        self._pending: list[Any] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bus: Any = None
        self._closing = False
        self._inflight: set[Future[Any]] = set()
        self._failed = 0  # sticky responsibility; never cleared by observation
        self._native_cancelled_retired=0
        self._runtime_evidence = None
        self._pre_observation_buffered = 0
        self._background_work: dict[Future[Any], Any] = {}
        self._background_observation = {}
        self._native_stoppers = {}
        self._native_signal_failures = []
        self._pre_observation_background = 0
        self._delivery_journal = None
        if recovery_root is not None:
            from .ingress_delivery import DeliveryJournal
            self._delivery_journal = DeliveryJournal(recovery_root, store)
            self._pending.extend(self._delivery_journal.recover())
            self._failed += self._delivery_journal.status()['uncertain']

    def bind_runtime_evidence(self, owner: Any) -> None:
        """Attach once before loop work; do not pretend earlier acceptance was observed."""
        if owner is None or not all(callable(getattr(owner,name,None)) for name in
                ('begin','finish','enter','leave','scope','capture','active_kind','bind_event','parents_of')):
            raise TypeError('runtime evidence owner required')
        with self._lock:
            if self._runtime_evidence is owner:return
            if (self._runtime_evidence is not None or self._closing
                    or self._inflight.difference(self._background_work)):
                raise RuntimeError('ingress evidence binding unavailable')
            self._pre_observation_buffered=len(self._pending)
            self._runtime_evidence=owner
            # This is a tail observation, never a backdated acceptance claim.
            for record in self._background_observation.values():
                if not record[2] and record[0] is None:
                    self._pre_observation_background+=1
                    token=owner.begin('plugin_background_tail')
                    record[0],record[1]=owner,token

    def evidence_state(self) -> dict[str, Any]:
        with self._lock:
            return {'bound':self._runtime_evidence is not None,
                    'pre_observation_buffered':self._pre_observation_buffered,
                    **({'pre_observation_background':self._pre_observation_background} if self._pre_observation_background else {}),
                    'pre_observation_acceptance_proven':False}

    def background_evidence_reference(self):
        with self._lock:owner=self._runtime_evidence
        if owner is None:return None
        from pathlib import Path
        import hashlib
        from . import runtime_evidence
        path=Path(runtime_evidence.__file__).resolve()
        return {'source':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

    def _observation(self):
        owner=self._runtime_evidence
        return nullcontext() if owner is None else owner.scope('plugin_work')

    def spawn_background(self, factory: Callable[[], Any]) -> Future[Any]:
        """Create a coroutine Task only AFTER admission, on its running loop."""
        def start():
            loop = asyncio.get_running_loop()
            coroutine = factory()
            try:
                return loop.create_task(coroutine)
            except BaseException:
                if asyncio.iscoroutine(coroutine):
                    coroutine.close()
                raise
        return self.start_background(start)

    def spawn_system_background(self, factory: Callable[[], Any]) -> Future[Any]:
        """Admit independently owned host work, without borrowing a tool lease.

        The actual Task remains in start_background's shutdown accounting.
        Each later plugin I/O still requires its own registered SystemInvocation;
        this context boundary creates no tool or system-call authority.
        Runtime evidence and other Bot context are preserved.
        """
        def start():
            from .async_tools import _managed_scopes, current_invocation
            from .system_calls import current_system_call
            loop = asyncio.get_running_loop()
            context = contextvars.copy_context()
            context.run(_managed_scopes.set, ())
            context.run(current_invocation.set, None)
            context.run(current_system_call.set, None)
            coroutine = context.run(factory)
            try:
                return loop.create_task(coroutine, context=context)
            except BaseException:
                if asyncio.iscoroutine(coroutine):
                    coroutine.close()
                raise
        return self.start_background(start)

    def submit_background(self, executor: Any, function: Callable[..., Any],
                          *args: Any, **kwargs: Any) -> Future[Any]:
        """Admit before submit; observe the actual concurrent Future until done."""
        return self.start_background(lambda: executor.submit(function, *args, **kwargs))

    def start_background(self, factory: Callable[[], Any]) -> Future[Any]:
        """Admit before invoking factory; observe the actual Task/executor Future.

        Returns a non-cancellable receipt, not the work itself. Plugins must keep
        their own stopping mechanism; cancelling a shielded shutdown observer
        does not cancel work. A plain asyncio Future is not a completion proof.
        """
        if not callable(factory):raise TypeError('background factory required')
        with self._lock:
            self._check_admission()
            if len(self._background_work)>=64:raise RuntimeError('background capacity')
            receipt=Future();receipt.set_running_or_notify_cancel()
            self._inflight.add(receipt);self._background_work[receipt]=None
            owner=self._runtime_evidence
            record=[owner,None,False]
            self._background_observation[receipt]=record
        token=None
        def retain(primary,secondary):
            try:previous=BaseException.__getattribute__(primary,'cleanup_errors')
            except AttributeError:previous=()
            if not isinstance(previous,tuple):previous=()
            BaseException.__setattr__(primary,'cleanup_errors',previous+(secondary,))
            BaseException.__setattr__(primary,'cleanup_owner',owner)
        def settle(value=None,error=None):
            with self._lock:
                record[2]=True
                settled_owner,settled_token=record[:2]
            if settled_owner is not None and settled_token is not None:
                outcome=('RETURNED' if error is None else 'CANCELLED' if isinstance(error,asyncio.CancelledError)
                         else 'FAILED' if isinstance(error,Exception) else 'INTERRUPTED')
                try:settled_owner.finish(settled_token,outcome)
                except BaseException as failure:
                    if error is None:error=failure
                    else:retain(error,failure)
            with self._lock:
                if error is not None:self._failed+=1
            if error is None:receipt.set_result(value)
            else:receipt.set_exception(error)
            with self._lock:
                self._background_work.pop(receipt,None);self._inflight.discard(receipt)
                self._background_observation.pop(receipt,None)
        context=None;work=None;setup_error=None
        try:
            if owner is not None:
                token=owner.begin('plugin_background');record[1]=token;context=owner.enter(token)
            work=factory()
            if not isinstance(work,(asyncio.Task,Future)) or work is receipt:
                raise TypeError('factory must return an actual Task or executor Future')
        except BaseException as error:
            setup_error=error
        finally:
            if context is not None:
                try:owner.leave(context)
                except BaseException as cleanup:
                    if setup_error is None:setup_error=cleanup
                    else:retain(setup_error,cleanup)
        if not isinstance(work,(asyncio.Task,Future)) or work is receipt:
            settle(error=setup_error)
            raise setup_error
        # A factory may already have started real work before context teardown
        # fails. Keep its actual observer until completion, even though setup
        # must report the failure immediately to the caller.
        with self._lock:self._background_work[receipt]=work
        def completed(done):
            try:value=done.result()
            except BaseException as error:
                if setup_error is not None:
                    retain(setup_error,error);error=setup_error
                settle(error=error)
            else:settle(value=value,error=setup_error)
        try:work.add_done_callback(completed)
        except BaseException as error:
            # The actual work may still run. Retain both it and the unfulfilled
            # receipt; never manufacture a completion when observer binding fails.
            with self._lock:self._failed+=1
            if setup_error is not None:
                retain(setup_error,error);error=setup_error
            BaseException.__setattr__(error,'background_receipt',receipt)
            raise error
        if setup_error is not None:
            BaseException.__setattr__(setup_error,'background_receipt',receipt)
            raise setup_error
        return receipt

    def bind_native_task(self):
        scope=_native_task_scope.get()
        task=asyncio.current_task()
        if scope is None or scope[0] is not self or current_thread() is not scope[1] or task is None:
            raise RuntimeError('original host-owned native thread task required')
        _,thread,work,owner=scope
        if (type(owner) is not tuple or len(owner)!=2 or type(owner[0]) is not str
                or owner[1] is None or task.get_loop() is not asyncio.get_running_loop()):
            raise RuntimeError('exact plugin owner required')
        with _native_task_lock:
            if task in _native_task_records or len(_native_task_records)>=512:
                raise RuntimeError('native task already bound or capacity reached')
            _native_task_records[task]=(self,thread,work,owner[0],owner[1],task.get_coro())

    def start_background_thread(self, thread: Thread, *, stop: Callable[[], Any], owner=None) -> Future[Any]:
        """Start the exact native thread after admission; completion requires join.

        stop is an idempotent nonblocking signal, not a release assertion. It is
        invoked when admission closes, before public drain waits for this work.
        The plugin retains its native handle and its existing private barrier.
        """
        if type(thread) not in (Thread,Timer) or thread.ident is not None or thread.is_alive():
            raise TypeError('fresh exact Thread or Timer required')
        if not callable(stop):raise TypeError('nonblocking stop signal required')
        ready=Event();outcome=[];execution_context=[];work=Future();work.set_running_or_notify_cancel()
        original_run=thread.run
        def invoke():
            token=_native_task_scope.set((self,thread,work,owner))
            try:return original_run()
            finally:_native_task_scope.reset(token)
        def run():
            try:execution_context[0].run(invoke)
            except BaseException as error:outcome.append(error)
        def observe():
            ready.wait()
            if thread.ident is not None:thread.join()
            if outcome:work.set_exception(outcome[0])
            else:work.set_result(None)
        def factory():
            execution_context.append(contextvars.copy_context())
            # Observer starts first: no accepted native thread without a waiter.
            monitor=Thread(target=observe,name='plugin-native-observer',daemon=False)
            monitor.start()
            thread.run=run
            try:thread.start()
            except BaseException as error:
                outcome.append(error)
                if thread.ident is None:thread.run=original_run
            finally:ready.set()
            return work
        receipt=self.start_background(factory)
        with self._lock:
            self._native_stoppers[receipt]=(thread,stop)
            closing=self._closing
        def retired(done):
            with self._lock:self._native_stoppers.pop(done,None)
        receipt.add_done_callback(retired)
        if closing:
            try:self._signal_native(receipt)
            except BaseException as error:
                error.background_receipt=receipt
                raise
        if thread.ident is None and outcome:
            outcome[0].background_receipt=receipt
            raise outcome[0]
        return receipt

    def _signal_native(self,receipt):
        with self._lock:entry=self._native_stoppers.pop(receipt,None)
        if entry is None:return
        try:entry[1]()
        except BaseException as error:
            with self._lock:
                self._failed+=1
                self._native_signal_failures.append((entry,error))
            raise

    def begin_shutdown(self) -> None:
        """Irreversibly close admission on this instance; do not cancel work."""
        with self._lock:
            self._closing = True
            signals=tuple(self._native_stoppers)
        errors=[]
        for receipt in signals:
            try:self._signal_native(receipt)
            except BaseException as error:errors.append(error)
        if errors:
            primary=errors[0]
            for error in errors[1:]:primary.add_note('another native stop signal failed: '+type(error).__name__)
            raise primary

    def shutdown_state(self) -> dict[str, Any]:
        """Read-only evidence, with no event bodies or exception messages.

        ``drained`` is terminal execution, NOT successful retirement. Consumers
        must also require ``failed == 0``. Unbound durable events remain owned.
        """
        with self._lock:
            pending = len(self._inflight)
            buffered = len(self._pending)
            return {"accepting": not self._closing, "pending": pending,
                    "buffered": buffered, "failed": self._failed,
                    "drained": self._closing and pending == 0 and buffered == 0}

    def shutdown_tasks(self) -> tuple[asyncio.Future[Any], ...]:
        """Observe an accepted-work snapshot on the caller's running loop.

        Call begin_shutdown first. These shielded asyncio Futures adapt retained
        concurrent Futures; cancelling an observer never cancels/replays work.
        Await without blocking the console loop, then re-read shutdown_state.
        An empty tuple is not proof of success (buffered/failed may be nonzero).
        This method does not launch tasks, services or new ingress operations.
        """
        loop = asyncio.get_running_loop()
        with self._lock:
            if not self._closing:
                raise RuntimeError("plugin ingress shutdown has not begun")
            accepted = tuple(self._inflight)
        return tuple(asyncio.shield(asyncio.wrap_future(work, loop=loop))
                     for work in accepted)

    def native_task_retirement_state(self):
        with self._lock:count=self._native_cancelled_retired
        with _native_task_lock:pending=sum(row[0] is self for row in _native_task_records.values())
        return {'cancelled_retired':count,'retained_relations':pending,'business_acknowledged':False}

    def _check_admission(self) -> None:
        # Caller holds _lock, shared with begin_shutdown and registration.
        if self._closing:
            raise RuntimeError("plugin ingress is shutting down")

    def bind(self, bus: Any, loop: asyncio.AbstractEventLoop) -> None:
        if asyncio.get_running_loop() is not loop:
            raise RuntimeError("plugin ingress must be bound on the console loop")
        with self._lock:
            if self._loop is not None:
                raise RuntimeError("plugin ingress is already bound")
            self._loop = loop
            self._bus = bus
            # bind may finish delivery of already accepted events after shutdown.
            # An attempted push may have escaped: record failure, never replay it.
            while self._pending:
                event = self._pending.pop(0)
                try:
                    self._push(event)
                except BaseException:
                    self._failed += 1
                    raise

    def _push(self, event: Any) -> None:
        owner=self._runtime_evidence
        if owner is None:return self._push_owned(event)
        parent=owner.capture()
        if owner.active_kind(parent)=='plugin_work':
            # Delivery is part of the already-admitted append, not new work.
            owner.bind_event(event,parent)
            return self._push_owned(event)
        parents=owner.parents_of((event,))
        with owner.scope('plugin_delivery',parents=parents or None):
            owner.bind_event(event,owner.capture())
            return self._push_owned(event)

    def _push_owned(self, event: Any) -> None:
        if self._delivery_journal is not None:
            return self._delivery_journal.offer(event, self._offer_bus)
        return self._offer_bus(event)

    def delivery_recovery_status(self) -> dict[str, Any]:
        """Counts only: uncertain attempts are never reported as business ACKs."""
        if self._delivery_journal is None:
            return {'enabled': False, 'business_acknowledged': False}
        return {'enabled': True, **self._delivery_journal.status()}

    def begin_batch(self, events):
        if self._delivery_journal is None:return ()
        return self._delivery_journal.begin_batch(events)

    def finish_batch(self, tickets):
        if self._delivery_journal is not None:
            self._delivery_journal.finish_batch(tickets)

    def _offer_bus(self, event: Any) -> None:
        meta = getattr(event, "meta", None)
        window = meta.get("reaction_window_ms") if isinstance(meta, dict) else None
        if type(window) is int and 0 <= window <= 10000:
            self._bus.push(event, window_ms=window)
        else:
            self._bus.push(event)

    def _on_console_loop(self, action: Callable[[], T]) -> T:
        with self._lock:
            self._check_admission()
            loop = self._loop
            if loop is None:
                raise RuntimeError("plugin ingress is not bound")
            if not loop.is_running():
                raise RuntimeError("plugin ingress console loop has stopped")
            result: Future[T] = Future()
            # This is accepted work, not a cancellable observer. Registration
            # precedes scheduling and shares the shutdown admission lock.
            result.set_running_or_notify_cancel()
            self._inflight.add(result)
            owner=self._runtime_evidence
        token=None

        def settle(value: Any = None, error: BaseException | None = None) -> None:
            if owner is not None and token is not None:
                outcome=('RETURNED' if error is None else 'CANCELLED' if isinstance(error,asyncio.CancelledError)
                         else 'FAILED' if isinstance(error,Exception) else 'INTERRUPTED')
                try:owner.finish(token,outcome)
                except BaseException as recording_error:
                    if error is None:error=recording_error
                    else:
                        try:previous=BaseException.__getattribute__(error,'cleanup_errors')
                        except AttributeError:previous=()
                        if not isinstance(previous,tuple):previous=()
                        BaseException.__setattr__(error,'cleanup_errors',previous+(recording_error,))
                        BaseException.__setattr__(error,'cleanup_owner',owner)
                        # add_note itself invokes a subclass __setattr__ when
                        # creating __notes__; evidence metadata must not mask
                        # the accepted action's primary failure.
                        try:notes=BaseException.__getattribute__(error,'__notes__')
                        except AttributeError:notes=[]
                        if not isinstance(notes,list):notes=[]
                        BaseException.__setattr__(error,'__notes__',list(notes)+[
                            'plugin evidence incomplete; original exception retained'])
            if error is not None:
                with self._lock:
                    self._failed += 1
                result.set_exception(error)
            else:
                result.set_result(value)
            with self._lock:
                self._inflight.discard(result)

        def invoke() -> None:
            context=None
            value=None
            primary=None
            try:
                if token is not None:context=owner.enter(token)
                value = action()
            except BaseException as exc:
                primary=exc
            finally:
                if context is not None:
                    try:owner.leave(context)
                    except BaseException as cleanup:
                        if primary is None:primary=cleanup
                        else:
                            try:previous=BaseException.__getattribute__(primary,'cleanup_errors')
                            except AttributeError:previous=()
                            if not isinstance(previous,tuple):previous=()
                            BaseException.__setattr__(primary,'cleanup_errors',previous+(cleanup,))
                            BaseException.__setattr__(primary,'cleanup_owner',owner)
            # Include context retirement in accepted work; never publish success
            # before it finishes or strand a loop callback exception unobserved.
            settle(value=value,error=primary)

        if owner is not None:
            try:token=owner.begin('plugin_work')
            except BaseException as exc:
                settle(error=exc)
                raise

        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is loop:
            invoke()
            return result.result()
        try:
            loop.call_soon_threadsafe(invoke)
        except RuntimeError as exc:
            settle(error=exc)
            raise
        except BaseException:
            # Scheduling was interrupted with an unknown outcome. Do not
            # fabricate retirement: the callback may already be queued.
            with self._lock:
                self._failed += 1
            raise
        # Timeout is only a caller wait boundary. Keep queued/running work until
        # invoke really settles, even if the loop later stops with work queued.
        return result.result(timeout=30)

    def _store_event(self, draft: Any) -> tuple[Any, int]:
        before = self.store.latest_cursor()
        journal = self._delivery_journal
        token = journal.prepare() if journal is not None else None
        event = self.store.append(draft)
        if journal is not None:
            if event.cursor > before: journal.stored(token, event)
            else: journal.discard_duplicate(token)
        return event, before

    def _append_bound(self, draft: Any) -> Any:
        event, before = self._store_event(draft)
        if event.cursor > before:
            self._push(event)
        return event

    def append(self, draft: Any) -> Any:
        with self._lock:
            self._check_admission()
            if self._loop is None:
                try:
                    with self._observation():
                        event, before = self._store_event(draft)
                        if event.cursor > before:
                            if self._runtime_evidence is not None:
                                self._runtime_evidence.bind_event(event,self._runtime_evidence.capture())
                            self._pending.append(event)
                        return event
                except BaseException:
                    self._failed += 1
                    raise
        return self._on_console_loop(lambda: self._append_bound(draft))

    def range(self, query: Any) -> Any:
        with self._lock:
            self._check_admission()
            if self._loop is None:
                try:
                    with self._observation():return self.store.range(query)
                except BaseException:
                    self._failed += 1
                    raise
        return self._on_console_loop(lambda: self.store.range(query))

    def latest_cursor(self) -> int:
        with self._lock:
            self._check_admission()
            if self._loop is None:
                try:
                    with self._observation():return self.store.latest_cursor()
                except BaseException:
                    self._failed += 1
                    raise
        return self._on_console_loop(self.store.latest_cursor)
