"""Opt-in process-local diagnostics candidate. No startup, network, config or files.
A caller must pin one NEW approved private test message ID; not a sender/grant.
Snapshots contain fixed counters only, never content, account/message IDs or errors.
"""
from ..frozen_json import FrozenDict

import json,threading,functools,time
class ScopedRouteProbe:
 STAGES=('private_seen','private_accepted','private_rejected','accept_error','store_appended','store_error','bus_offered','bus_error','batch_taken','take_error','batch_processing_started','batch_processing_error','batch_deliver_entered','batch_delivered','batch_rejected','batch_deliver_error','gate_checked','gate_closed','gate_non_boolean','round_entered','round_completed','round_failed','round_ended','round_overflowed','round_over_budget','round_error','model_plain_reply','model_tool_reply','model_empty_reply','tool_dispatch','tool_dispatch_error','wire_attempt','wire_protocol_return','wire_error','wire_receipt','receipt_error')
 def __init__(self,*,qq,store,ingress,console,dispatcher,message_id=None,expected_text=None,recipient='1228600196',self_id='2724955497',enabled=False,on_status=None,clock=time.monotonic,max_seconds=180):
  if type(enabled) is not bool:raise ValueError('explicit_boolean_required')
  if (message_id is None)==(expected_text is None):raise ValueError('exactly_one_scope_selector_required')
  if message_id is not None and (type(message_id) is not str or not message_id or len(message_id)>24 or not message_id.lstrip('-').isdigit() or message_id=='0'):raise ValueError('exact_message_id_required')
  if expected_text is not None and (type(expected_text) is not str or not expected_text or len(expected_text.encode('utf-8'))>512):raise ValueError('exact_test_text_required')
  if recipient!='1228600196' or self_id!='2724955497':raise ValueError('fixed_test_scope_required')
  if on_status is not None and not callable(on_status):raise ValueError('status_callback_required')
  if type(max_seconds) is not int or not 1<=max_seconds<=180 or not callable(clock):raise ValueError('bounded_probe_window_required')
  self.clock=clock;self.deadline=clock()+max_seconds
  self.qq=qq;self.store=store;self.ingress=ingress;self.console=console;self.dispatcher=dispatcher
  self._expected=expected_text;self._mid=message_id;self._recipient=recipient;self._self=self_id;self.enabled=enabled;self.callback=on_status
  self.counts=dict.fromkeys(self.STAGES,0);self.lock=threading.RLock();self.owned=[];self.closed=False;self.attached=False;self.audit_failed=False;self.ownership_lost=False;self.inflight=0;self.loop=None;self.loop_take_hook_available=False
 def summary(self):
  with self.lock:return {'enabled':self.enabled,'attached':self.attached,'closed':self.closed,'counts':dict(self.counts),'audit_failed':self.audit_failed,'ownership_lost':self.ownership_lost,'inflight':self.inflight,'loop_take_hook_available':self.loop_take_hook_available,'loop_binding_current':self.loop is None or self.loop is getattr(getattr(self.console,'app',None),'loop',None),'permission_to_send':False,'contains_message_content':False}
 def _live(self):return not self.closed and self.clock()<self.deadline
 def _mark(self,stage):
  with self.lock:
   if not self._live():return
   self.counts[stage]=min(4096,self.counts[stage]+1)
  if self.callback is not None:
   try:self.callback(self.summary())
   except Exception:self.audit_failed=True # Audit must not trigger a retry/fallback.
 def _event(self,event):
  get=event.get if type(event) is dict else lambda k,d=None:getattr(event,k,d)
  meta=get('meta')
  return self._mid is not None and get('source')=='qq' and get('origin')=='external' and type(meta) in (dict, FrozenDict) and meta.get('conversation')=='private:'+self._recipient and str(meta.get('message_id'))==self._mid
 def _batch(self,batch):return type(batch) in (tuple,list) and bool(batch) and all(self._event(e) for e in batch)
 def _active(self):return self._batch(getattr(self.console,'_active_batch',()))
 def _raw(self,raw):
  try:
   if not isinstance(raw,(str,bytes)) or len(raw)>65536:return False
   v=json.loads(raw)
   if not (type(v) is dict and v.get('post_type')=='message' and v.get('message_type')=='private' and str(v.get('self_id'))==self._self and str(v.get('user_id'))==self._recipient):return False
   mid=str(v.get('message_id'))
   if not mid or len(mid)>24 or not mid.lstrip('-').isdigit() or mid=='0':return False
   with self.lock:
    if self._mid is not None:return mid==self._mid
    parts=v.get('message')
    if not (type(parts) is list and len(parts)==1 and type(parts[0]) is dict and parts[0].get('type')=='text' and type(parts[0].get('data')) is dict and parts[0]['data'].get('text')==self._expected):return False
    self._mid=mid;self._expected=None # Latch once, never rearm or persist selector.
    return True
  except (ValueError,TypeError):return False
 def _set(self,obj,name,make):
  old=getattr(obj,name);own=name in vars(obj);prior=vars(obj).get(name);wrapper=make(old)
  setattr(obj,name,wrapper);self.owned.append((obj,name,old,own,prior,wrapper))
 def _sync(self,match,enter=None,success=None,error=None):
  def make(old):
   @functools.wraps(old)
   def wrapper(*a,**kw):
    observed=False
    try:observed=self._live() and match(*a,**kw)
    except Exception:pass
    with self.lock:self.inflight+=1
    try:
     if observed and enter:self._mark(enter)
     try:result=old(*a,**kw)
     except BaseException:
      if observed and error:self._mark(error)
      raise
     if observed and success:self._mark(success(result) if callable(success) else success)
     return result
    finally:
     with self.lock:self.inflight-=1
   return wrapper
  return make
 def attach(self):
  if self.closed or self.attached:raise RuntimeError('probe_lifecycle_invalid')
  if not self.enabled:return self
  try:
   self._set(self.qq,'accept',self._sync(lambda raw:self._raw(raw),'private_seen',lambda r:'private_accepted' if r is True else 'private_rejected','accept_error'))
   self._set(self.store,'append',self._sync(lambda draft:self._event(draft),success='store_appended',error='store_error'))
   self._set(self.ingress,'_push',self._sync(lambda event:self._event(event),success='bus_offered',error='bus_error'))
   def take_make(old):
    @functools.wraps(old)
    async def take(*a,**kw):
     with self.lock:self.inflight+=1
     try:
      try:result=await old(*a,**kw)
      except BaseException:
       if self._active():self._mark('take_error')
       raise
      if self._batch(result):self._mark('batch_taken')
      return result
     finally:
      with self.lock:self.inflight-=1
    return take
   # Console.assemble saved the BOUND callback into Loop before this observer
   # was armed. Replacing only Console's attribute cannot observe that call.
   from telyincore.loop import Loop
   loop=getattr(getattr(self.console,'app',None),'loop',None)
   if type(loop) is Loop:
    self.loop=loop
    if callable(getattr(loop,'_take_batch',None)):
     self._set(loop,'_take_batch',take_make);self.loop_take_hook_available=True
    def async_make(match,enter=None,success=None,error=None):
     def make(old):
      @functools.wraps(old)
      async def wrapper(*a,**kw):
       observed=False
       try:observed=self._live() and match(*a,**kw)
       except Exception:pass
       with self.lock:self.inflight+=1
       try:
        if observed and enter:self._mark(enter)
        try:result=await old(*a,**kw)
        except BaseException:
         if observed and error:self._mark(error)
         raise
        if observed and success:success(result)
        return result
       finally:
        with self.lock:self.inflight-=1
      return wrapper
     return make
    if callable(getattr(loop,'process_one_batch',None)):
     self._set(loop,'process_one_batch',async_make(lambda batch:self._batch(batch),'batch_processing_started',error='batch_processing_error'))
    if callable(getattr(loop,'_deliver',None)):
     self._set(loop,'_deliver',async_make(lambda batch:self._batch(batch),'batch_deliver_entered',lambda result:self._mark('batch_delivered' if result is True else 'batch_rejected'),'batch_deliver_error'))
    if callable(getattr(loop,'gated',None)):
     
     old_gate=loop.gated
     @functools.wraps(old_gate)
     def gated(*a,**kw):
      observed=self._live() and self._active()
      with self.lock:self.inflight+=1
      try:
       result=old_gate(*a,**kw)
       if observed:
        self._mark('gate_checked')
        if result is True:self._mark('gate_closed')
        elif type(result) is not bool:self._mark('gate_non_boolean')
       return result
      finally:
       with self.lock:self.inflight-=1
     self._set(loop,'gated',lambda old:gated)
    if callable(getattr(loop,'_run_round',None)):
     def round_done(result):
      self._mark('round_completed')
      for name in ('failed','ended','overflowed','over_budget'):
       if getattr(result,name,None) is True:self._mark('round_'+name)
     self._set(loop,'_run_round',async_make(lambda *a,**kw:self._active(),'round_entered',round_done,'round_error'))
   if not self.loop_take_hook_available:self._set(self.console,'_take_a_batch',take_make)
   def reply_make(old):
    @functools.wraps(old)
    async def reply(result):
     with self.lock:self.inflight+=1
     try:
      if self._active():
       stage='model_tool_reply' if getattr(result,'calls',()) else ('model_plain_reply' if (getattr(result,'text','') or getattr(result,'refusal','')) else 'model_empty_reply')
       self._mark(stage)
      return await old(result)
     finally:
      with self.lock:self.inflight-=1
    return reply
   self._set(self.console,'_tap_reply',reply_make)
   self._set(self.dispatcher,'run',self._sync(lambda *a,**kw:self._active(),'tool_dispatch',error='tool_dispatch_error'))
   self._set(self.qq,'_post',self._sync(lambda action,payload:self._active() and action=='send_private_msg' and str(payload.get('user_id'))==self._recipient,'wire_attempt','wire_protocol_return','wire_error'))
   self._set(self.qq,'_receipt',self._sync(lambda result,scope,target:self._active() and scope=='private' and target==self._recipient,success='wire_receipt',error='receipt_error'))
   self.attached=True;return self
  except BaseException:self.close();raise
 def close(self):
  with self.lock:self.closed=True;self._expected=None;self._mid=None
  for obj,name,old,own,prior,wrapper in reversed(self.owned):
   if getattr(obj,name) is not wrapper:self.ownership_lost=True;continue
   if own:setattr(obj,name,prior)
   else:delattr(obj,name)
  self.owned=[];self.attached=False
 def shutdown_complete(self):
  with self.lock:return self.closed and not self.ownership_lost and self.inflight==0
 def __enter__(self):return self.attach()
 def __exit__(self,*exc):self.close();return False
