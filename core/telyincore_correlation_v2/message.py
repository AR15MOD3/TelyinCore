"""Whole-v2-frame message contract for new dedicated ports, not lifecycle Pipe.

No native port factory or ownership authority is implemented. Capability records
are only negotiated metadata; their real provenance remains UNKNOWN.
"""
from dataclasses import dataclass
from .codec import Binding,binding_from,ProtocolError
from .io import DedicatedIO,IOUnknown
from .assembly import ReceiptSession
HARD_MESSAGE_PAYLOAD=4096
def reject():raise ProtocolError('v2_message_contract_rejected')
@dataclass(frozen=True)
class MessageLimits:
 binding:Binding
 message_payload:int
 body:int
 def __post_init__(self):
  if type(self.binding) is not Binding or type(self.message_payload) is not int or type(self.body) is not int or not 256<=self.message_payload<=HARD_MESSAGE_PAYLOAD or self.body!=self.message_payload-4:reject()
def capability(binding,max_payload=4096):
 return {'version':2,'mode':'dedicated_message','framing':'one_complete_v2_frame','max_message_payload':max_payload,'binding':binding.wire()}
def negotiate(expected,local,peer):
 if type(expected) is not Binding:reject()
 sizes=[]
 for x in (local,peer):
  if type(x) is not dict or set(x)!={'version','mode','framing','max_message_payload','binding'} or type(x['version']) is not int or x['version']!=2 or x['mode']!='dedicated_message' or x['framing']!='one_complete_v2_frame' or binding_from(x['binding'])!=expected:reject()
  n=x['max_message_payload']
  if type(n) is not int or not 256<=n<=16388:reject()
  sizes.append(n)
 n=min(HARD_MESSAGE_PAYLOAD,*sizes);return MessageLimits(expected,n,n-4)
def check_frame(frame,limits):
 if type(limits) is not MessageLimits or type(frame) is not bytes or not 4<len(frame)<=limits.message_payload or int.from_bytes(frame[:4],'big')!=len(frame)-4:reject()
 return frame
class MessageAdapter:
 """One whole message send/receive; readexactly is an in-memory view only."""
 def __init__(self,port,limits):
  if type(limits) is not MessageLimits or not all(callable(getattr(port,n,None)) for n in ('send_message','receive_message','close','wait_closed')):raise ValueError('new_dedicated_message_port_required')
  self._port=port;self._limits=limits;self._stage='IDLE';self._waiting=None;self._faulted=False;self._closing=False;self._closed=False;self._tx=None;self._rx=None
 def _check(self):
  if self._faulted or self._closing:raise IOUnknown()
 def write(self,frame):
  self._check()
  if self._stage!='IDLE':raise IOUnknown()
  self._tx=check_frame(frame,self._limits);self._stage='QUEUED'
 async def drain(self):
  self._check()
  if self._stage!='QUEUED' or self._waiting:raise IOUnknown()
  self._waiting='SEND'
  try:await self._port.send_message(self._tx);self._stage='SENT'
  except BaseException:self._faulted=True;raise
  finally:self._waiting=None
 async def readexactly(self,n):
  self._check()
  if self._waiting:raise IOUnknown()
  if self._stage=='SENT' and n==4:
   self._waiting='RECEIVE'
   try:self._rx=check_frame(await self._port.receive_message(),self._limits);self._stage='BODY';return self._rx[:4]
   except BaseException:self._faulted=True;raise
   finally:self._waiting=None
  if self._stage=='BODY' and type(n) is int and n==len(self._rx)-4:
   body=self._rx[4:];self._tx=self._rx=None;self._stage='IDLE';return body
  self._faulted=True;raise IOUnknown()
 def close(self):
  if self._waiting:raise IOUnknown() # no closing concurrent with live message IO
  if not self._closing:self._closing=True;self._port.close()
 async def wait_closed(self):
  if not self._closing:raise IOUnknown()
  await self._port.wait_closed();self._closed=True;self._tx=self._rx=None
 def snapshot(self):return {'message_payload_limit':self._limits.message_payload,'v2_body_limit':self._limits.body,'stage':self._stage,'waiting':self._waiting,'faulted':self._faulted,'closing':self._closing,'local_port_closed':self._closed,'retained_frame_buffers':int(self._tx is not None)+int(self._rx is not None),'native_completion':'UNKNOWN','owner_qualification':'UNKNOWN','can_bind':False}
 def __repr__(self):return '<V2DedicatedMessageAdapter scoped redacted>'
def make_message_session(expected,owner_public,recipient_public,port,local_cap,peer_cap,*,clock):
 limits=negotiate(expected,local_cap,peer_cap);adapter=MessageAdapter(port,limits);io=DedicatedIO(adapter,adapter);session=ReceiptSession(expected,owner_public,recipient_public,io,clock=clock)
 return session,adapter
