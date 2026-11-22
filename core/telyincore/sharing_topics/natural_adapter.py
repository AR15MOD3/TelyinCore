"""Natural-dialogue output preparation, not proactive dispatch authority.
Only the already-processing, exact recipient-owned QQ private batch is eligible.
The general shadow busy check is NOT altered. All foreign work/queues still veto.
A caller cannot turn a topic/result dict into permission. Nothing sends here.
"""
from ..frozen_json import FrozenDict

from dataclasses import dataclass,field
import threading,json,hashlib
from telyincore.console import Console
from telyincore.loop import Loop
from telyincore.bus import WakeBus
from telyincore.sharing_bridge.host_bridge import HostBridge
from .topic_queue import TopicQueue,DESTINATION
@dataclass(frozen=True)
class ReviewedNaturalOutput:
 text:str=field(repr=False)
 batch_key:str=field(repr=False)
 expires_ms:int
class NaturalTopicAdapter:
 def __init__(self,queue,*,enabled=False):
  if type(queue) is not TopicQueue or type(enabled) is not bool:raise ValueError('trusted_queue_required')
  self.queue=queue;self.binding=queue.binding;self.enabled=enabled;self.closed=False;self.operation=threading.Lock();self.used_batches=set()
  self._owner_thread=threading.get_ident();self._review_local=threading.local();self._review_inputs=None
 def capture(self,ticket):
  if threading.get_ident()!=self._owner_thread:raise RuntimeError('owner_thread_snapshot_required')
  context=self._context()
  with self.queue.lock:
   self.queue._active();entry=self.queue.items.get(ticket)
   if entry is None or self.queue._now()>=entry.expires_ms:raise ValueError('ticket_expired_or_unknown')
  event=self.binding.resolve(entry.ref);state=self._state(event,context)
  self._review_inputs=(entry,event,state,context)
  return self._review_inputs
 def _context(self):
  frozen=getattr(self._review_local,'snapshot',None)
  if frozen is not None:return frozen[3]
  if threading.get_ident()!=self._owner_thread:raise RuntimeError('owner_thread_snapshot_required')
  c=self.binding.console
  if type(c) is not Console:raise ValueError('actual_console_required')
  loop=getattr(getattr(c,'app',None),'loop',None)
  if type(loop) is not Loop or loop.processing is not True or type(loop.generation) is not int or loop.generation<0:raise ValueError('active_owned_batch_required')
  if type(c._taking_batch) is not bool or c._taking_batch or type(c._pending_batches) is not list or c._pending_batches:raise ValueError('other_work_pending')
  if type(c.bus) is not WakeBus or c.bus.pending()!=0:raise ValueError('other_work_pending')
  batch=c._active_batch
  if type(batch) is not tuple or not 1<=len(batch)<=16:raise ValueError('owned_batch_required')
  rows=[]
  for item in batch:
   source=item.get('source') if type(item) is dict else getattr(item,'source',None)
   meta=item.get('meta') if type(item) is dict else getattr(item,'meta',None)
   if source!='qq' or type(meta) not in (dict, FrozenDict) or meta.get('conversation')!='private:1228600196':raise ValueError('audience_mismatch')
   mid=meta.get('message_id')
   if type(mid) is not str or not mid or len(mid)>80:raise ValueError('batch_identity_unknown')
   rows.append((source,meta['conversation'],mid))
  if len({r[2] for r in rows})!=len(rows):raise ValueError('batch_identity_unknown')
  key=hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()
  return (id(c),id(loop),loop.generation,id(batch),tuple(rows),key)
 def _state(self,event,context):
  if self.closed or not self.enabled:raise ValueError('adapter_not_active')
  frozen=getattr(self._review_local,'snapshot',None)
  if frozen is not None:
   if event is not frozen[1] or context!=frozen[3]:raise ValueError('review_snapshot_changed')
   with self.queue.lock:
    self.queue._active()
    if self.queue._now()>=frozen[0].expires_ms:raise ValueError('review_snapshot_expired')
   return frozen[2]
  with self.queue.lock:self.queue._active();self.queue._now()
  if self._context()!=context:raise ValueError('conversation_changed')
  state=self.binding.provider(event)
  # Processing belongs to THIS immutable private batch. Never ignore mount/
  # host/gate busy or a bus/Console queue because they are other work.
  f=self.binding.factory;m=self.binding.mount
  with m._lock:
   if type(m._busy) is not bool or m._busy:raise ValueError('other_work_busy')
  gate=f._provider()
  if type(f.host._busy) is not bool or f.host._busy or gate.busy:raise ValueError('other_work_busy')
  if not state.authorized or state.revoked or not state.history_verified:raise ValueError('source_not_authorized')
  if self._context()!=context:raise ValueError('conversation_changed')
  return state
 def prepare(self,ticket,*,final_text,captured=None):
  """Exact FULL outgoing message must be reviewed, not just appended fragment.
  Only host callbacks may call this. No source text goes into the ordinary model
  session. This adapter is off until explicitly installed and opted in by host.
  """
  if not self.operation.acquire(False):return {'status':'operation_busy'}
  bridge=None;queue_locked=False;binding_locked=False
  try:
   if captured is not None:
    if captured is not self._review_inputs:raise ValueError('unknown_review_snapshot')
    self._review_inputs=None
    self._review_local.snapshot=captured
   elif threading.get_ident()!=self._owner_thread:
    raise RuntimeError('owner_thread_snapshot_required')
   queue_locked=self.queue.operation.acquire(False)
   if not queue_locked:return {"status":"operation_busy"}
   binding_locked=self.binding._operation.acquire(False)
   if not binding_locked:return {"status":"operation_busy"}
   if self.closed or not self.enabled:return {'status':'disabled'}
   context=self._context()
   if context[-1] in self.used_batches:return {'status':'batch_already_attempted'}
   if len(self.used_batches)>=32:return {'status':'capacity_reached'}
   if type(final_text) is not str or not final_text.strip() or len(final_text.encode())>2048:return {'status':'draft_invalid'}
   with self.queue.lock:
    self.queue._active();now=self.queue._now();self.queue._prune(now)
    if type(ticket) is not str:return {'status':'ticket_unknown'}
    entry=self.queue.items.pop(ticket,None)
    if entry is None:return {'status':'ticket_unknown'}
    if captured is not None and entry is not captured[0]:raise ValueError('ticket_snapshot_changed')
   self.used_batches.add(context[-1])
   event=captured[1] if captured is not None else self.binding.resolve(entry.ref)
   before=self._state(event,context)
   def checked(fn):
    def call(value):
     if self._state(event,context)!=before:raise ValueError('source_or_conversation_changed')
     return fn(value)
    return call
   bridge=HostBridge(bot='Telyin',destination=DESTINATION,allowed_scopes=frozenset({'group:qq:290016343'}),clock=self.binding.factory.clock,assess=checked(self.binding.factory._assess),review=checked(self.binding.factory._review))
   # Recipient is already in this private conversation; unrelated busy work is
   # rejected above. This is NOT the proactive-dispatch busy policy.
   proposal=json.dumps({'spans':[{'start':a,'end':b} for a,b in entry.spans],'mode':'paraphrase','text':final_text,'desire':entry.desire},ensure_ascii=False)
   from telyincore.sharing_bridge.contracts import parse_proposal
   decision=bridge.preview(event,parse_proposal(proposal),ready=True,busy=False,quiet=before.quiet,history_verified=True,attempts=())
   with self.queue.lock:
    self.queue._active();now=self.queue._now()
    if self.closed or self._state(event,context)!=before or decision.get('action') not in ('topic','allow'):return {'status':'review_rejected'}
    expiry=min(entry.expires_ms,now+30000)
    if now>=expiry:return {'status':'expired'}
   with self.queue.lock:
    self.queue._active()
    if self.closed:return {'status':'rejected'}
    output=ReviewedNaturalOutput(final_text,context[-1],expiry)
    # Registry never accepts dataclass copies or arbitrary caller-provided grants.
    self._prepared=(output,event,before,context)
    return {'status':'prepared','output':output,'will_send':False}
  except Exception:return {'status':'rejected'}
  finally:
   self._review_local.snapshot=None
   if bridge is not None:bridge.close()
   if binding_locked:self.binding._operation.release()
   if queue_locked:self.queue.operation.release()
   self.operation.release()
 def take(self,output):
  # Must be invoked immediately at the pinned host output boundary. This returns
  # bytes but is not wire authorization; outer router/recall guard still applies.
  if threading.get_ident()!=self._owner_thread:raise RuntimeError('owner_thread_consumption_required')
  with self.queue.lock:
   record=getattr(self,'_prepared',None);self._prepared=None
  if record is None or output is not record[0]:return None
  try:
   value,event,state,context=record
   with self.queue.lock:
    self.queue._active();now=self.queue._now()
    if now>=value.expires_ms or self._state(event,context)!=state:return None
   return value.text
  except Exception:return None
 def close(self):
  with self.queue.lock:self.closed=True;self.enabled=False;self._prepared=None;self._review_inputs=None
 def shutdown_complete(self):return self.closed and not self.operation.locked() and getattr(self,'_prepared',None) is None
 def summary(self):return {'enabled':self.enabled and not self.closed,'attempted_batch_count':len(self.used_batches),'pending_output':getattr(self,'_prepared',None) is not None,'real_sending_enabled':False,'closed':self.closed}
