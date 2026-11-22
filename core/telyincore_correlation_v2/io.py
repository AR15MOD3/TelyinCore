"""Dedicated, injected asynchronous byte IO; isolated candidate, not OS authority.

No stdin, file descriptor, socket, lifecycle RPC, process discovery or spawning.
Reader/writer must be separately qualified and exclusively owned by the caller.
Synchronous callbacks must be bounded; Python cannot hard-preempt native code.
"""
import asyncio,math
from .work_bridge import ToolWork,ToolWorkIncomplete
from .codec import MAX_FRAME

class IOUnknown(RuntimeError):
 def __init__(self):super().__init__('v2_io_unknown')
class IOBusy(RuntimeError):
 def __init__(self):super().__init__('v2_io_busy_no_new_work')
def timeout_value(value):
 if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=30:raise ValueError('bounded_io_timeout_required')
 return value
class IOWork(ToolWork):
 label='correlation_io'
 def __init__(self,endpoint):super().__init__(limit=1);self.endpoint=endpoint;self._cancel_sent=set()
 def seal(self):
  super().seal();self.endpoint._seal()
 def start(self,coroutine):
  self._bind_loop()
  if self._closed or self._tasks:
   coroutine.close();raise IOUnknown()
  try:task=asyncio.create_task(coroutine)
  except BaseException:coroutine.close();raise
  self._tasks.add(task);task.add_done_callback(self._done);return task
 def cancel_once(self,task):
  if not task.done() and task not in self._cancel_sent:
   self._cancel_sent.add(task);task.cancel()
 def snapshot(self):
  r=super().snapshot();e=self.endpoint;c=e._closer
  r.update(endpoint_fenced=e._fenced,close_started=c is not None,
   close_state='NOT_STARTED' if c is None else 'PENDING' if not c.done() else 'CANCELLED' if c.cancelled() else 'FAILED' if c.exception() is not None else 'CLOSED',
   remote_state='UNKNOWN',actual_owner_qualification='UNKNOWN',frame_state=e._frame_state)
  r['local_io_drained']=bool(self._closed and r['close_state']=='CLOSED' and not r['owned']['pending'] and not r['owned']['failed'])
  return r
 def finish_sync(self,*,primary=None):
  self.seal();r=self.snapshot()
  if not r['local_io_drained']:
   error=ToolWorkIncomplete(self)
   if primary is None or isinstance(primary,asyncio.CancelledError):raise error from primary
   primary.add_note(str(error))
 async def close(self,*,timeout=2.0):
  loop=self._bind_loop();deadline=loop.time()+timeout_value(timeout);self.seal()
  if asyncio.current_task() in self._tasks:raise RuntimeError('io_cannot_drain_itself')
  owned=tuple(self._tasks)
  for task in owned:self.cancel_once(task)
  if owned:await asyncio.wait(owned,timeout=max(0,deadline-loop.time()))
  # Never close concurrently with a live reader/writer operation.
  if not any(not t.done() for t in self._tasks):
   closer=self.endpoint._begin_close()
   await asyncio.wait({closer},timeout=max(0,deadline-loop.time()))
  self.finish_sync();return self.snapshot()

class DedicatedIO:
 def __init__(self,reader,writer):
  # Interface checks are not trusted-owner admission. No raw handle API exists.
  if not callable(getattr(reader,'readexactly',None)) or not all(callable(getattr(writer,k,None)) for k in ('write','drain','close','wait_closed')):raise ValueError('dedicated_async_endpoints_required')
  self._reader=reader;self._writer=writer;self._current=None;self._closer=None;self._fenced=False;self._loop=None;self._frame_state='UNKNOWN';self.scope=IOWork(self)
 def _bind(self):
  loop=asyncio.get_running_loop()
  if self._loop is None:self._loop=loop
  elif loop is not self._loop:raise RuntimeError('v2_io_loop_changed')
  return loop
 def _seal(self):self._fenced=True;self._frame_state='UNKNOWN'
 def _check(self):
  if self._fenced:raise IOUnknown()
 async def _exchange(self,frame):
  # This task is registered in IOWork before its first step.
  self._check();self._writer.write(frame);await self._writer.drain();self._check()
  header=await self._reader.readexactly(4);self._check()
  if type(header) is not bytes or len(header)!=4:raise IOUnknown()
  size=int.from_bytes(header,'big')
  if not 0<size<=MAX_FRAME:raise IOUnknown()
  body=await self._reader.readexactly(size);self._check()
  if type(body) is not bytes or len(body)!=size:raise IOUnknown()
  return header+body # unverified bytes, never an APPLIED/terminal claim
 async def exchange(self,frame,*,timeout=2.0):
  loop=self._bind();duration=timeout_value(timeout);self._check()
  if self._current is not None:raise IOBusy()
  if type(frame) is not bytes or not 4<len(frame)<=MAX_FRAME+4 or int.from_bytes(frame[:4],'big')!=len(frame)-4:raise ValueError('bounded_complete_frame_required')
  if duration==0:raise IOUnknown() # no write when budget is already empty
  deadline=loop.time()+duration;task=self.scope.start(self._exchange(frame));self._current=task
  try:
   done,_=await asyncio.wait({task},timeout=max(0,deadline-loop.time()))
   if not done:
    self.scope.seal();self.scope.cancel_once(task);raise IOUnknown()
   if self._fenced or task.cancelled():self.scope.seal();raise IOUnknown()
   if task.exception() is not None:self.scope.seal();raise IOUnknown() from None
   self._frame_state='FRAME_RECEIVED_UNVERIFIED';return task.result()
  except asyncio.CancelledError:
   self.scope.seal();self.scope.cancel_once(task);raise
  finally:
   if task.done():self._current=None
 async def _close(self):
  # Only called after all operations cease; close ownership belongs to this
  # dedicated endpoint, not to a borrowed stdin/lifecycle stream.
  self._writer.close();await self._writer.wait_closed()
 def _begin_close(self):
  self._bind()
  if self._closer is None:
   c=self._close()
   try:self._closer=asyncio.create_task(c)
   except BaseException:c.close();raise
   self._closer.add_done_callback(lambda t:None if t.cancelled() else t.exception())
  return self._closer
 async def close(self,*,timeout=2.0):
  self._bind();return await self.scope.close(timeout=timeout)
 def snapshot(self):return self.scope.snapshot()
 def __repr__(self):return '<V2DedicatedIO scoped redacted>'
