"""Cooperative default tool-batch ownership, not model/OS sandbox or host proof."""
import asyncio
import inspect
import math
from functools import wraps


class ToolWorkIncomplete(RuntimeError):
    def __init__(self, scope):
        self.scope = scope  # retained exact task handles, including timeout/failure
        self.report = scope.snapshot()
        super().__init__(f"{scope.label}_work_unresolved: {self.report}")


class ToolWork:
    label = 'tool'

    def __init__(self, *, limit=128):
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError('bounded tool work capacity required')
        self._limit = limit
        self._loop = None
        self._closed = False
        self._tasks = set()
        self._completed = self._cancelled = 0

    def _bind_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError(f'{self.label}_work_loop_changed')
        return loop

    def seal(self):
        self._closed = True  # no reset/reopen; retained callback cannot admit work

    def _done(self, task):
        if task.cancelled():
            self._cancelled += 1
            self._tasks.discard(task)
        elif task.exception() is None:
            self._completed += 1
            self._tasks.discard(task)
        # Failure stays pinned; never erase it to claim a successful drain.

    def wrap(self, handler):
        if not callable(handler):
            raise ValueError('tool callback required')
        @wraps(handler)
        async def call(*args, **kwargs):
            if self._closed:
                raise RuntimeError(f'{self.label}_work_closed')
            self._bind_loop()
            if len(self._tasks) >= self._limit:
                raise RuntimeError(f'{self.label}_work_capacity')
            async def invoke():
                result = handler(*args, **kwargs)
                if not inspect.isawaitable(result):
                    raise TypeError('async tool callback required')
                return await result
            coroutine = invoke()
            try:
                task = asyncio.create_task(coroutine)
            except BaseException:
                coroutine.close()
                raise
            self._tasks.add(task)
            task.add_done_callback(self._done)
            return await task
        call.with_inner_guard = lambda guard: self.wrap(guard(handler))
        return call

    def snapshot(self):
        states = {'pending': 0, 'failed': 0, 'done': 0, 'cancelled': 0}
        for task in self._tasks:
            state = ('pending' if not task.done() else 'cancelled' if task.cancelled()
                     else 'failed' if task.exception() is not None else 'done')
            states[state] += 1
        return {'component': self.label, 'closed': self._closed, 'owned': states,
                'completed': self._completed, 'cancelled': self._cancelled,
                'complete': False}  # no model/descendant/host authority

    def finish_sync(self, *, primary=None):
        self.seal()
        state = self.snapshot()['owned']
        if state['pending'] or state['failed']:
            error = ToolWorkIncomplete(self)
            if primary is None or isinstance(primary, asyncio.CancelledError):
                raise error from primary
            primary.add_note(str(error))

    async def close(self, *, timeout=2.0):
        if type(timeout) not in (int, float) or not 0 <= timeout <= 30 or not math.isfinite(timeout):
            raise ValueError('bounded tool drain timeout required')
        self.seal()
        self._bind_loop()
        if asyncio.current_task() in self._tasks:
            raise RuntimeError(f'{self.label}_work_cannot_drain_itself')
        owned = tuple(self._tasks)
        for task in owned:
            if not task.done():
                task.cancel()
        if owned:
            await asyncio.wait(owned, timeout=timeout)  # timeout does not drop handles
        self.finish_sync()
        return self.snapshot()
