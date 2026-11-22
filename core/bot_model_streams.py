"""Exact per-generate stream close acknowledgements, not borrowed client ownership."""
import asyncio
import inspect


class StreamLease:
    def __init__(self,scope):
        self.scope=scope;self.source=None;self.iterator=None
        self.owner_task=asyncio.current_task()
        self.failed=False;self.closing=False;self.acknowledged=False;self.error=''
        self.close_attempted=False;self.close_error=None

    def __aiter__(self):return self

    async def __anext__(self):
        if self.acknowledged:raise StopAsyncIteration
        return await self.iterator.__anext__()

    async def aclose(self):
        if self.acknowledged:return
        self.scope._bind_loop()
        if self.closing:raise RuntimeError('model stream close already in flight')
        if self.close_attempted:
            if self.close_error is not None:raise self.close_error
            raise RuntimeError('prior model stream close failure remains unresolved')
        self.close_attempted=True
        self.closing=True
        try:
            if self.source is None:raise RuntimeError('model stream creation unconfirmed')
            result=self.source.aclose()
            if not inspect.isawaitable(result):raise TypeError('async stream close required')
            await result
        except BaseException as error:
            self.failed=True;self.error=type(error).__name__;self.close_error=error
            error.stream_lease=self
            raise
        else:
            if self.failed:raise RuntimeError('prior model stream close failure remains unresolved')
            self.acknowledged=True;self.scope._streams.discard(self)
            self.scope._streams_closed+=1
        finally:self.closing=False


class ModelTransport:
    """Observe only streams returned to this owned task; never close its client."""
    def __init__(self,scope,transport):self.scope,self.transport=scope,transport

    def stream_chat(self,*args,**kwargs):
        scope=self.scope
        if scope._closed:raise RuntimeError('model stream admission closed')
        scope._bind_loop()
        if asyncio.current_task() not in scope._tasks:
            raise RuntimeError('model stream requires this scopes owned generate task')
        if len(scope._streams)>=128:raise RuntimeError('model stream capacity')
        lease=StreamLease(scope);scope._task_streams.setdefault(asyncio.current_task(), []).append(lease);scope._streams.add(lease)  # reserve before callback
        try:
            source=self.transport.stream_chat(*args,**kwargs)
            if any(row is not lease and row.source is source for row in scope._streams):
                raise RuntimeError('model stream already held by another live lease')
            lease.source=source
            if not callable(getattr(source,'aclose',None)):
                raise TypeError('model stream must support async close')
            lease.iterator=source.__aiter__()
            if not callable(getattr(lease.iterator,'__anext__',None)):
                raise TypeError('model async iterator required')
            return lease
        except BaseException as error:
            lease.failed=True;lease.error=type(error).__name__
            raise  # unknown creation/binding is retained, not an empty inventory
