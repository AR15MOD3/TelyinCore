"""Private injected receipt assembly. Not a mounted/authenticated product entry.

Pinned Binding/keys must come from qualified ownership not yet implemented here.
Public probe cannot arm/dispatch this assembly, even with truthy config flags.
"""
import asyncio
from .work_bridge import ToolWork,ToolWorkIncomplete
from .codec import Binding,decode,ProtocolError
from .binding import ReceiptCheck,dual_status
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from .io import DedicatedIO,IOUnknown,timeout_value

def unmounted_entry(*args,**kwargs):
 return {'status':503,'state':'UNKNOWN','reason':'v2_owner_entry_not_mounted'}

class ReceiptSession:
 label='correlation_receipt_assembly' # required by existing ToolWorkIncomplete
 def __init__(self,binding,owner_public,recipient_public,io,*,clock):
  if type(binding) is not Binding or type(io) is not DedicatedIO:raise ValueError('exact_private_components_required')
  self._binding=binding;self._owner=owner_public;self._recipient=recipient_public;self._io=io;self._clock=clock
  self._work=ToolWork(limit=1);self._wrapped=self._work.wrap(self._run);self._request=None;self._used=False;self._closed=False;self._check=None;self._historical='UNKNOWN'
 def seal(self):
  self._closed=True;self._work.seal();self._io.scope.seal()
 async def read_receipt(self,signed_query,*,timeout=2.0):
  timeout_value(timeout)
  if self._closed or self._used:raise IOUnknown()
  self._used=True # no reissue after an admitted or failed single attempt
  try:
   query=decode(signed_query,self._owner,self._binding,self._clock(),'receipt_query')
   self._check=ReceiptCheck(self._binding,self._recipient,query,clock=self._clock)
  except Exception:self.seal();raise
  c=self._wrapped(bytes(signed_query),timeout)
  try:self._request=asyncio.create_task(c)
  except BaseException:c.close();self.seal();raise
  self._request.add_done_callback(lambda t:None if t.cancelled() else t.exception())
  return await self._request
 async def _run(self,signed_query,timeout):
  try:
   reply=await self._io.exchange(signed_query,timeout=timeout)
   if self._closed:raise IOUnknown()
   state=self._check.accept(reply)
   if self._closed:raise IOUnknown()
   self._historical=state
   return {'state':state,'activated':False,'provider_terminal':'UNKNOWN'}
  except BaseException:
   self.seal();raise
 def snapshot(self):
  core=self._work.snapshot();io=self._io.snapshot();owned={k:core['owned'][k]+io['owned'][k] for k in core['owned']}
  request_pending=self._request is not None and not self._request.done()
  return {'component':'v2_receipt_assembly','closed':self._closed,'attempt_used':self._used,'receipt_state':self._historical if not self._closed else 'UNKNOWN','historical_receipt':self._historical,'owned':owned,'request_wrapper_pending':request_pending,'receipt_work':core,'io_work':io,'local_io_drained':self._closed and not request_pending and not owned['pending'] and not owned['failed'] and io['local_io_drained'],'provider_terminal':'UNKNOWN','actual_owner_qualification':'UNKNOWN','entry_mounted':False,'complete':False}
 def finish_sync(self,*,primary=None):
  self.seal()
  if not self.snapshot()['local_io_drained']:
   error=ToolWorkIncomplete(self)
   if primary is None or isinstance(primary,asyncio.CancelledError):raise error from primary
   primary.add_note(str(error))
 async def close(self,*,timeout=2.0):
  timeout_value(timeout);deadline=asyncio.get_running_loop().time()+timeout;self.seal()
  try:await self._work.close(timeout=max(0,deadline-asyncio.get_running_loop().time()))
  except ToolWorkIncomplete:pass # retain exact failed tasks; final aggregate fails
  try:await self._io.close(timeout=max(0,deadline-asyncio.get_running_loop().time()))
  except ToolWorkIncomplete:pass
  # Wrapper references remain even when its nested scope has already settled.
  if self._request is not None and not self._request.done():await asyncio.wait({self._request},timeout=max(0,deadline-asyncio.get_running_loop().time()))
  self.finish_sync();return self.snapshot()
 def __repr__(self):return '<V2ReceiptSession private scoped redacted>'

def pair_status(host,bot):
 if type(host) is not ReceiptSession or type(bot) is not ReceiptSession or host._binding.role!='host' or bot._binding.role!='bot':raise ProtocolError('v2_pair_binding_rejected')
 common=('owner_epoch','instance_epoch','boot','gateway','instance','bot_scope')
 if any(getattr(host._binding,k)!=getattr(bot._binding,k) for k in common) or host._owner.public_bytes(Encoding.Raw,PublicFormat.Raw)!=bot._owner.public_bytes(Encoding.Raw,PublicFormat.Raw):raise ProtocolError('v2_pair_binding_rejected')
 if host._closed or bot._closed or host._check is None or bot._check is None or host._request is None or bot._request is None or not host._request.done() or not bot._request.done():return 'UNKNOWN'
 return dual_status(host._check,bot._check) # APPLIED version, never activation
