"""Process-owned, bounded shadow observer. Never registers a send tool or exchange."""
from dataclasses import replace
from contextlib import contextmanager
import threading,queue,time,json
from .host_shadow import HostShadowBinding
from .safe_diagnostics import diagnose
from telyincore.sharing_bridge.mount import BridgeMount
from telyincore.sharing_close_budget import bounded_close, remaining
class ShadowOwner:
 def __init__(self,kernel,*,monotonic=time.monotonic,on_status=None,idle_wait_s=45,idle_stable_s=1):
  if type(idle_wait_s) not in (int,float) or type(idle_stable_s) not in (int,float) or not 0<idle_wait_s<=45 or not 0<=idle_stable_s<=idle_wait_s:raise ValueError('bounded_idle_wait_required')
  self.idle_wait_s=idle_wait_s;self.idle_stable_s=idle_stable_s
  self.on_status=on_status;self.kernel=kernel;self.monotonic=monotonic;self.lock=threading.RLock();self.stop=threading.Event();self.queue=queue.Queue(maxsize=3)
  self.thread=None;self.mount=None;self.binding=None;self.closed=False;self.unknown=False;self.deadline=None;self.accepted=set();self.records=[];self.phase='new'
 def __call__(self,*,bot):
  bundle=self.kernel(bot=bot)
  return replace(bundle,ingress=self.ingress,recall=self.recall,facts=lambda:'')
 def bind(self,mount):
  with self.lock:
   if self.closed or self.thread is not None:raise ValueError('single_owner_binding_required')
   if not callable(mount.plugin_host.get('sharing_shadow_console')) or not callable(mount.plugin_host.get('sharing_shadow_quiet_at')):raise ValueError('explicit_runtime_seams_required')
   self.mount=mount;self.deadline=self.monotonic()+180;self.unknown=True
   self.thread=threading.Thread(target=self._work,name='sharing-shadow-owned',daemon=False)
   self.thread.start();self.unknown=False;self.phase='ready_for_source';self._publish()
 def ingress(self,event):
  with self.lock:
   if self.closed or self.thread is None or self.monotonic()>=self.deadline:return
  self.kernel.ingress(event)
  # Only references captured by the authenticated host adapter can enter the queue.
  with self.kernel._lock:
   hints=[(k,h.copy()) for k,h in self.kernel._hints.items() if k in self.kernel._sources]
  with self.lock:
   if self.closed:return
   for key,hint in hints:
    if key not in self.accepted:
     self.accepted.add(key)
     if len(self.accepted)>3:raise ValueError('source_cap_exceeded')
     self.queue.put_nowait((hint['source_ref'],hint['proposal']))
 def recall(self,event):
  # No worker lock across model I/O: recall publication must remain immediate.
  if not self.closed:self.kernel.recall(event)
 def _await_idle(self,ref):
  # Only readiness is sampled again. No model call has happened, no budget is
  # reserved, and no failed review or delivery is ever retried here.
  until=self.monotonic()+self.idle_wait_s;stable_at=None;stable_state=None
  with self.lock:self.phase='waiting_for_idle';self._publish()
  while not self.stop.is_set():
   now=self.monotonic()
   if now>=until:return {'dry_run':True,'will_send':False,'action':'defer','reason':'host_idle_wait_timeout'}
   if self.binding is None:
    console=self.mount.plugin_host['sharing_shadow_console']()
    if console is None:self.stop.wait(.05);continue
    self.binding=HostShadowBinding(factory=self.kernel,mount=self.mount,console=console,quiet_at=self.mount.plugin_host['sharing_shadow_quiet_at'],enabled=True)
   # The real provider rechecks config, exact source, recall, expiry and mount
   # ownership on every sample. Losing authority terminates, not postpones.
   event=self.binding.resolve(ref);state=self.binding.provider(event)
   if state.busy:stable_at=None;stable_state=None
   else:
    if state!=stable_state:stable_at=now;stable_state=state
    if now-stable_at>=self.idle_stable_s:return None
   self.stop.wait(.05)
  return {'dry_run':True,'will_send':False,'action':'defer','reason':'shadow_owner_closed'}
 def _after_preview(self,ref,proposal,result):return {}
 def _on_idle(self):pass
 def _work(self):
  while not self.stop.is_set():
   try:ref,proposal=self.queue.get(timeout=.1)
   except queue.Empty:self._on_idle();continue
   stage='readiness_wait'
   try:
    if self.stop.is_set() or self.monotonic()>=self.deadline:continue
    idle=self._await_idle(ref)
    if idle is not None:
     with self.lock:self.records.append(idle);self.phase='shadow_deferred';self._publish()
     continue
    stage='shadow_preview'
    import json
    result=self.binding.shadow_preview(ref,json.dumps(proposal))
    # Persist no raw source, reference, proposal, profile secrets, or model output.
    record={k:result[k] for k in ('dry_run','will_send','action','reason') if k in result}
    record['service_calls']=dict(self.binding.service_calls)
    if self.binding.last_transition is not None:record['state_change']={**self.binding.last_transition,'fields':list(self.binding.last_transition['fields'])}
    if self.binding.last_failure is not None:record['diagnostic']=dict(self.binding.last_failure)
    record.update(self._after_preview(ref,proposal,result))
    with self.lock:self.records.append(record);self.phase='shadow_complete';self._publish()
   except Exception as exc:
    with self.lock:self.records.append({'dry_run':True,'will_send':False,'action':'deny','reason':'shadow_owner_unavailable','diagnostic':diagnose(exc,stage)});self.phase='failed_closed'
    self._publish()
   finally:self.queue.task_done()
 def _publish(self):
  if self.on_status is not None:self.on_status(self.summary())
 def summary(self):
  with self.lock:return {'phase':self.phase,'records':json.loads(json.dumps(self.records)),'real_sending_enabled':False,'owned_worker_alive':self.thread is not None and self.thread.is_alive()}
 @bounded_close
 def close(self):
  with self.lock:self.closed=True;self.stop.set()
  # Stop admission first. Do not pretend an in-flight model request was cancelled.
  if self.binding is not None:self.binding.close()
  if self.thread is not None:
   self.thread.join(remaining())
   if self.thread.is_alive():raise RuntimeError('shadow_worker_cleanup_unconfirmed')
  while True:
   try:self.queue.get_nowait();self.queue.task_done()
   except queue.Empty:break
  if self.binding is not None:self.binding.close()
  with self.lock:self.accepted.clear()
  self.kernel.close()
  self._publish()
 def shutdown_complete(self):
  return self.closed and not self.unknown and (self.thread is None or not self.thread.is_alive()) and self.queue.empty() and not self.accepted and (self.binding is None or self.binding.shutdown_complete() is True) and self.kernel.shutdown_complete() is True
@contextmanager
def mounted_shadow(owner):
 old=BridgeMount.attach
 def attach(mount,**kw):
  old(mount,**kw)
  if mount._bundle is None or mount._bundle.host is not owner.kernel.host:raise ValueError('shadow_owner_mismatch')
  owner.bind(mount)
 BridgeMount.attach=attach
 try:yield
 finally:
  if BridgeMount.attach is not attach:raise RuntimeError('shadow_attach_hook_changed')
  BridgeMount.attach=old
