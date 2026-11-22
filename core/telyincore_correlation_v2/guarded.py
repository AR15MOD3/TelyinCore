"""Retain-only local callback ledger + exit linkage. No native owner authority.

Markers describe callback awaits, NOT OVERLAPPED/native operations. The sender
copy and receiving endpoint are distinct injected references, not duplex halves.
Caller must retain this guard or an incomplete/primary exception carrying it.
"""
import asyncio,secrets,hashlib
from .work_bridge import ToolWorkIncomplete
from .evidence import RetainedEvidenceLedger
from .message import make_message_session
from .io import IOUnknown,timeout_value

class _Slot:
 def __init__(self):self.data=None
class _Await:
 def __init__(self):self.awaitable=None;self.parent=asyncio.current_task();self.settled=False;self.state='PENDING'

class GuardedSession:
 label='correlation_guarded_exit'
 def __init__(self,expected,owner_public,recipient_public,observer_public,port,owner_ref,sender_copy_ref,endpoint_digest,local_cap,peer_cap,*,clock):
  self._refs=(owner_ref,sender_copy_ref,port);self._port=port;self._session=None;self._adapter=None;self._closed=False;self._setup_failed=False;self._ops=[]
  self._ledger=None;self._ep=None
  try:
   self._ledger=RetainedEvidenceLedger(expected,observer_public,clock=clock)
   self._ep=self._ledger.register_endpoint(owner_ref,sender_copy_ref,port,endpoint_digest)
   if not all(callable(getattr(port,n,None)) for n in ('send_message','receive_message','close','wait_closed')):raise ValueError('dedicated_port_required')
   self._session,self._adapter=make_message_session(expected,owner_public,recipient_public,_Observed(self),local_cap,peer_cap,clock=clock)
  except BaseException as e:
   self._setup_failed=True;self.seal();raise ToolWorkIncomplete(self) from e
 async def _call(self,direction,callback,data=None):
  if self._closed:raise IOUnknown()
  marker=_Await();slot=_Slot() if direction=='RECEIVE' else None
  nonce=secrets.token_hex(16);commit=hashlib.sha256(b'LOCAL_CALLBACK_SLOT_NOT_NATIVE_OPERATION\0'+direction.encode()+bytes.fromhex(nonce)).hexdigest()
  ticket=self._ledger.register_operation(self._ep,marker,(slot if slot is not None else data,),nonce,commit)
  self._ops.append((ticket,marker)) # registered before callback invocation
  state='FAILED'
  try:
   marker.awaitable=callback() if slot is not None else callback(data)
   result=await marker.awaitable
   if slot is not None:slot.data=result # original returned buffer, no copy
   state='DONE';return result
  except asyncio.CancelledError:
   state='CANCELLED';self._ledger.request_cancel(ticket,marker);raise
  finally:
   marker.settled=True;marker.state=state;self._ledger.mark_task_settled(ticket,marker,state)
 def seal(self):
  self._closed=True
  if self._ledger is not None:
   self._ledger.seal()
   for t,m in self._ops:
    if not m.settled:self._ledger.request_cancel(t,m) # bookkeeping only; IO scope cancels
  if self._session is not None:self._session.seal()
 async def read_receipt(self,wire,*,timeout=2.0):
  if self._closed or self._session is None:raise IOUnknown()
  try:return await self._session.read_receipt(wire,timeout=timeout)
  except BaseException as e:self.seal();self._retain_primary(e);raise
 def snapshot(self):
  work=self._session.snapshot() if self._session is not None else None
  return {'component':self.label,'closed':self._closed,'setup_failed':self._setup_failed,'callback_identity':'LOCAL_CALLBACK_SLOT_NOT_NATIVE_OPERATION','async_work':work,'resource_contract':self._ledger.snapshot() if self._ledger is not None else None,'owned':{k:sum(m.state==v for _,m in self._ops) for k,v in (('pending','PENDING'),('failed','FAILED'),('done','DONE'),('cancelled','CANCELLED'))},'retained_original_endpoint_refs':len(self._refs),'local_shutdown_complete':bool(work and work['local_io_drained']),'native_completion':'UNKNOWN','resource_contract_complete':False,'actual_root_registration':'NOT_MOUNTED','can_bind':False,'complete':False}
 def finish_sync(self,*,primary=None):
  self.seal();error=ToolWorkIncomplete(self)
  if primary is None or isinstance(primary,asyncio.CancelledError):raise error from primary
  self._retain_primary(primary)
 def _retain_primary(self,primary):
  # String notes alone do not retain original buffers/closer on exceptional exit.
  try:
   prior=getattr(primary,'_v2_retained_exit_scopes',())
   if type(prior) is not tuple:raise ValueError('exit_carrier_rejected')
   if not any(x is self for x in prior):
    if len(prior)>=32:raise ValueError('exit_carrier_full')
    setattr(primary,'_v2_retained_exit_scopes',prior+(self,))
    primary.add_note('correlation_guarded_exit: native completion UNKNOWN; retained scope attached')
  except BaseException:raise ToolWorkIncomplete(self) from primary
 async def close(self,*,timeout=2.0):
  timeout_value(timeout);self.seal()
  if self._session is not None:
   try:await self._session.close(timeout=timeout)
   except ToolWorkIncomplete:pass
   except BaseException as e:self.finish_sync(primary=e);raise
  self.finish_sync() # local drain never releases UNKNOWN native responsibility
 def __repr__(self):return '<GuardedSession local callback refs retained; native UNKNOWN>'

class _Observed:
 def __init__(self,guard):self._g=guard
 async def send_message(self,data):return await self._g._call('SEND',self._g._port.send_message,data)
 async def receive_message(self):return await self._g._call('RECEIVE',self._g._port.receive_message)
 def close(self):self._g._port.close()
 async def wait_closed(self):
  await self._g._port.wait_closed();self._g._ledger.mark_local_wait_closed(self._g._ep,self._g._port)
