"""Host-only ephemeral topic queue. No files, tools, prompt injection, or transport.
Topic is NOT send permission. Draft review and one-use handoff are separate.
On restart/close all references and handoffs disappear; nothing restores authority.
"""
from dataclasses import dataclass,field
import json,hashlib,secrets,threading
from telyincore.sharing_delivery.host_shadow import HostShadowBinding
from telyincore.sharing_bridge.contracts import parse_proposal
DESTINATION='private:qq:1228600196'
def sha(value):return hashlib.sha256(value.encode()).hexdigest()
@dataclass(frozen=True)
class NaturalHandoff:
 token:str=field(repr=False)
 destination:str
 text:str=field(repr=False)
 expires_ms:int
@dataclass(frozen=True)
class _Topic:
 ref:str
 spans:tuple
 desire:int
 expires_ms:int
 source_hash:str
class TopicQueue:
 def __init__(self,binding,*,enabled=False):
  if type(binding) is not HostShadowBinding or type(enabled) is not bool:raise ValueError('trusted_binding_required')
  self.binding=binding;self.enabled=enabled;self.lock=threading.RLock();self.operation=threading.Lock()
  self.closed=False;self.broken=False;self.last_clock=None;self.items={};self.seen=set();self.handoffs={}
 def _now(self):
  now=self.binding.factory.clock()
  if type(now) is not int or self.last_clock is not None and now<self.last_clock:
   self.broken=True;self.items.clear();self.handoffs.clear();raise ValueError('clock_unknown')
  self.last_clock=now
  return now
 def _active(self):
  if self.closed or self.broken or not self.enabled:raise ValueError('queue_not_active')
 def _prune(self,now):
  for k,e in tuple(self.items.items()):
   if now>=e.expires_ms:self.items.pop(k,None)
  for k,e in tuple(self.handoffs.items()):
   if now>=e[0].expires_ms:self.handoffs.pop(k,None)
 def summary(self):
  with self.lock:
   return {'enabled':self.enabled and not self.closed and not self.broken,'queued_count':len(self.items),'pending_handoff_count':len(self.handoffs),'closed':self.closed,'real_sending_enabled':False,'persistent':False}
 def enqueue(self,source_ref,proposal):
  if not self.operation.acquire(False):return {'status':'operation_busy'}
  try:
   with self.lock:
    self._active();now=self._now();self._prune(now)
    if len(self.items)>=8 or len(self.seen)>=32:return {'status':'capacity_reached'}
   p=parse_proposal(proposal);event=self.binding.resolve(source_ref);before=self.binding.provider(event)
   identity=sha(json.dumps([event.bot,event.scope,event.sender,event.message_id,event.observed_ms,event.text],ensure_ascii=False))
   with self.lock:
    if identity in self.seen:return {'status':'duplicate'}
   # No caller-supplied result/allow dict can populate this queue.
   reviewed=self.binding.shadow_preview(source_ref,proposal)
   with self.lock:
    self._active();now=self._now();after=self.binding.provider(event)
    if before!=after or after.busy or reviewed.get('action')!='topic':return {'status':'not_topic_or_state_changed'}
    expiry=min(event.observed_ms+180000,now+120000)
    if now>=expiry:return {'status':'expired'}
    ticket=secrets.token_hex(24);self.seen.add(identity)
    self.items[ticket]=_Topic(source_ref,p.spans,p.desire,expiry,identity)
    return {'status':'queued','ticket':ticket,'expires_ms':expiry,'will_send':False}
  except Exception:return {'status':'rejected'}
  finally:self.operation.release()
 def prepare(self,ticket,*,destination,final_text):
  """Host prepares an exact reviewed draft while idle. Not a live-send permit.
  The entry is consumed before review, so uncertain/failing attempts never retry.
  No raw source text is returned. A failed attempt requires a new source, not replay.
  """
  if not self.operation.acquire(False):return {'status':'operation_busy'}
  try:
   with self.lock:
    self._active();now=self._now();self._prune(now)
    if type(destination) is not str or destination!=DESTINATION:return {'status':'audience_mismatch'}
    if type(final_text) is not str or not final_text.strip() or len(final_text.encode())>2048:return {'status':'draft_invalid'}
    if type(ticket) is not str:return {'status':'ticket_unknown'}
    entry=self.items.pop(ticket,None)
    if entry is None:return {'status':'ticket_unknown'}
   event=self.binding.resolve(entry.ref);before=self.binding.provider(event)
   if before.busy:return {'status':'host_busy'}
   p=json.dumps({'spans':[{'start':a,'end':b} for a,b in entry.spans],'mode':'paraphrase','text':final_text,'desire':entry.desire},ensure_ascii=False)
   reviewed=self.binding.shadow_preview(entry.ref,p)
   with self.lock:
    self._active();now=self._now();after=self.binding.provider(event)
    if before!=after or after.busy or reviewed.get('action') not in ('topic','allow'):return {'status':'review_or_state_rejected'}
    expiry=min(entry.expires_ms,now+30000)
    if now>=expiry:return {'status':'expired'}
    handoff=NaturalHandoff(secrets.token_hex(24),DESTINATION,final_text,expiry)
    self.handoffs[handoff.token]=(handoff,event,after)
    return {'status':'prepared','handoff':handoff,'will_send':False}
  except Exception:return {'status':'rejected'}
  finally:self.operation.release()
 def take(self,handoff,*,destination):
  """Trusted host boundary only; consumes token BEFORE checking current authority.
  Returned text is for a future natural-dialogue adapter, not QQTransport authority.
  No automatic output is performed here. A later output layer must revalidate
  its own current source/recipient state; this does not authorize delayed sends.
  """
  with self.lock:
   try:
    self._active();now=self._now();self._prune(now)
    if type(handoff) is not NaturalHandoff or type(destination) is not str or destination!=DESTINATION:return None
    record=self.handoffs.pop(handoff.token,None)
    if record is None or record[0]!=handoff:return None
    _,event,state=record
    current=self.binding.provider(event)
    if current!=state or current.busy or now>=handoff.expires_ms:return None
    return handoff.text
   except Exception:return None
 def close(self):
  # Binding and reviewer belong to the outer host, never close them here.
  with self.lock:self.closed=True;self.enabled=False;self.items.clear();self.handoffs.clear();self.seen.clear()
 def shutdown_complete(self):
  with self.lock:return self.closed and not self.operation.locked() and not self.items and not self.handoffs
