"""Default model-generate task scope; no process/transport ownership grant."""
import asyncio
import math
from functools import wraps
from bot_tool_work import ToolWork, ToolWorkIncomplete
from bot_model_streams import ModelTransport


class ModelWork(ToolWork):
    label = 'model'

    def __init__(self):
        super().__init__(limit=32)
        self._streams=set();self._streams_closed=0;self._stream_closers={}
        self._task_streams={};self._classified_failures={};self._observed_failures=set()
        self._retired_failed=0;self._retired_failure_classes={}

    def _done(self, task):
        super()._done(task)
        if task not in self._tasks:
            self._task_streams.pop(task, None)
            self._classified_failures.pop(task, None)
            self._observed_failures.discard(task)

    def generate(self):
        from telyincore.generate import generate
        @wraps(generate)
        async def call(cfg,messages,transport,*,_record_replay_frame=None,**kwargs):
            # Bind only inside this scope's exact owned generate task, not its
            # caller. ContextVar inheritance alone grants no replay authority.
            from contextlib import nullcontext
            from telyincore.provider_record_replay import use
            binding = (use(_record_replay_frame) if _record_replay_frame is not None
                       else nullcontext())
            with binding:
                from telyincore.generate import EmptyResponseError
                try:
                    return await generate(cfg,messages,ModelTransport(self,transport),**kwargs)
                except Exception as error:
                    task=asyncio.current_task()
                    leases=self._task_streams.get(task, ())
                    # No reserved stream means this generate task failed before
                    # transport admission. ModelTransport reserves before invoking
                    # the producer, so a partial/unknown creation is NOT empty.
                    if (not leases or all(row.scope is self and row.owner_task is task
                                              and row.acknowledged and not row.failed
                                              and not row.closing for row in leases)):
                        # Candidate only. Ordinary errors still require an exact
                        # consumer disposition; classification does not settle them.
                        self._classified_failures[task]=error
                    raise
        callback = self.wrap(call)
        callback.owns_record_replay_frame = True
        @wraps(callback)
        async def observed(*args, **kwargs):
            self._retire_acknowledged_failures()
            try:
                return await callback(*args, **kwargs)
            except Exception as error:
                from telyincore.generate import EmptyResponseError
                for task, cause in self._classified_failures.items():
                    if (cause is error and type(error) is EmptyResponseError
                            and error.status in ('completed','incomplete','failed')
                            and task.done() and not task.cancelled()):
                        self._observed_failures.add(task)
                raise
        def acknowledge_failure(error):
            matches=[task for task,cause in self._classified_failures.items()
                     if cause is error and task in self._tasks and task.done()
                     and not task.cancelled() and task.exception() is error]
            if len(matches)!=1:return False
            task=matches[0];leases=self._task_streams.get(task, ())
            if any(row.scope is not self or row.owner_task is not task
                    or not row.acknowledged or row.failed or row.closing for row in leases):
                return False
            self._observed_failures.add(task)
            return True  # local resource disposition, never business success
        observed.acknowledge_failure=acknowledge_failure
        return observed

    def _retire_acknowledged_failures(self):
        # Only exact consumer-disposed failures with acknowledged stream cleanup
        # release admission capacity. Keep aggregate failure evidence, not tasks
        # retaining entire long-context request graphs for the process lifetime.
        for task in tuple(self._observed_failures):
            if (task not in self._tasks or not task.done() or task.cancelled()
                    or task.exception() is not self._classified_failures.get(task)):
                continue
            leases=self._task_streams.get(task, ())
            if any(row.scope is not self or row.owner_task is not task
                   or not row.acknowledged or row.failed or row.closing for row in leases):
                continue
            name=type(task.exception()).__name__
            if name not in self._retired_failure_classes and len(self._retired_failure_classes)>=32:
                name='other'
            self._retired_failure_classes[name]=self._retired_failure_classes.get(name,0)+1
            self._retired_failed+=1
            self._tasks.discard(task);self._task_streams.pop(task,None)
            self._classified_failures.pop(task,None);self._observed_failures.discard(task)

    def _settled_failures(self):
        if not self._closed:
            return set()
        return {task for task in self._observed_failures
                if task in self._tasks and task.done() and not task.cancelled()
                and task.exception() is self._classified_failures.get(task)
                and all(row.scope is self and row.owner_task is task
                                              and row.acknowledged and not row.failed and not row.closing
                        for row in self._task_streams.get(task, ())) }

    def snapshot(self):
        report=super().snapshot()
        settled=len(self._settled_failures())+self._retired_failed
        report['owned']['failed']+=self._retired_failed
        failures=dict(self._retired_failure_classes)
        for task in self._tasks:
            if task.done() and not task.cancelled() and task.exception() is not None:
                name=type(task.exception()).__name__
                failures[name]=failures.get(name,0)+1
        report.update(settled_failed=settled, unresolved_failed=report['owned']['failed']-settled,
                      failure_classes=failures)
        states={'open':0,'closing':0,'failed':0,'unbound':0}
        for stream in self._streams:
            state=('unbound' if stream.source is None else 'failed' if stream.failed
                   else 'closing' if stream.closing else 'open')
            states[state]+=1
        cleanup={'pending':0,'failed':0,'cancelled':0,'done':0}
        for task in self._stream_closers.values():
            state=('pending' if not task.done() else 'cancelled' if task.cancelled()
                   else 'failed' if task.exception() is not None else 'done')
            cleanup[state]+=1
        report.update(streams=states,stream_pending=len(self._streams),
            streams_closed=self._streams_closed,stream_cleanup=cleanup,
            stream_errors=sorted({stream.error for stream in self._streams if stream.error}))
        return report

    def finish_sync(self,*,primary=None):
        self.seal()
        report=self.snapshot()
        if (report['owned']['pending'] or report.get('unresolved_failed',report['owned']['failed'])
                or report['stream_pending'] or report['stream_cleanup']['pending']
                or report['stream_cleanup']['failed'] or report['stream_cleanup']['cancelled']):
            error=ToolWorkIncomplete(self)
            if primary is None or isinstance(primary,asyncio.CancelledError):raise error from primary
            primary.add_note(str(error))

    async def close(self,*,timeout=2.0):
        if type(timeout) not in (int,float) or not 0<=timeout<=30 or not math.isfinite(timeout):
            raise ValueError('bounded model drain timeout required')
        loop=self._bind_loop();deadline=loop.time()+timeout
        try:await super().close(timeout=timeout)
        except ToolWorkIncomplete:pass  # full resource-aware check below; never clears
        # Never aclose concurrently with a live request using its iterator.
        if not super().snapshot()['owned']['pending'] and loop.time()<deadline:
            for stream in tuple(self._streams):
                if stream.failed or stream.closing or stream in self._stream_closers:continue
                coroutine=stream.aclose()
                try:task=asyncio.create_task(coroutine)
                except BaseException as error:
                    coroutine.close();stream.failed=True;stream.error=type(error).__name__
                    continue
                self._stream_closers[stream]=task
                task.add_done_callback(self._take_close_result)
            owned=tuple(self._stream_closers.values())
            if owned:await asyncio.wait(owned,timeout=max(0,deadline-loop.time()))
        self.finish_sync()
        return self.snapshot()

    @staticmethod
    def _take_close_result(task):
        if task.done() and not task.cancelled():task.exception()


def work_pending(scope):
    if scope is None:return False
    report=scope.snapshot()
    cleanup=report.get('stream_cleanup',{})
    return bool(report['owned']['pending'] or report.get('unresolved_failed',report['owned']['failed'])
                or report.get('stream_pending',0)
                or cleanup.get('pending',0) or cleanup.get('failed',0)
                or cleanup.get('cancelled',0))


def finish_work_scopes(*scopes, primary=None):
    """Seal all before checking any; preserve every failure and original cause."""
    active = tuple(scope for scope in scopes if scope is not None)
    for scope in active:
        scope.seal()
    errors = []
    for scope in active:
        try:
            scope.finish_sync()
        except BaseException as error:
            errors.append(error)
    if errors:
        cancelled = isinstance(primary, asyncio.CancelledError)
        chosen = primary if primary is not None and not cancelled else errors[0]
        for error in errors:
            if error is not chosen:
                chosen.add_note(f"owned work cleanup failed: {error}")
        if cancelled:
            raise chosen from primary
        if primary is None:
            raise chosen
