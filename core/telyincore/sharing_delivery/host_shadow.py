"""Read-only binding to installed Core preview authority. Not a real-send grant.
No hooks are replaced; no history is read; no exchange or scheduler is created.
"""
from dataclasses import replace
from .safe_diagnostics import diagnose
import hashlib,json,threading
from .live_broker import State,LiveBroker,DOMAIN
from telyincore.sharing_bridge.core_binding import CoreTestFactory
from telyincore.sharing_bridge.mount import BridgeMount
from telyincore.sharing_bridge.host_bridge import HostBridge,TransportEvent
from telyincore.sharing_bridge.contracts import parse_proposal
from telyincore.sharing_bridge.state_gate import GateState
from telyincore.bus import WakeBus
SHADOW_DOMAIN='host-shadow-preview-v1'
class HostShadowBinding:
 def __init__(self,*,factory,mount,console,quiet_at,enabled=False):
  if type(enabled) is not bool or type(factory) is not CoreTestFactory or type(mount) is not BridgeMount or not callable(quiet_at):raise ValueError('exact_host_dependencies_required')
  if mount.bot!='Telyin' or mount._bundle is None or mount._bundle.host is not factory.host:raise ValueError('host_owner_mismatch')
  self.factory=factory;self.mount=mount;self.console=console;self.quiet_at=quiet_at;self.enabled=enabled;self.closed=False;self.last_failure=None;self.last_transition=None;self.service_calls={'assess':0,'review':0};self._transitions=[]
  self._permission=mount._permission_gate;self._hooks=dict(mount._hooks);self._host_generation=factory.host._generation
  self._revision=0;self._projection=None;self._source_state=None;self._lock=threading.RLock();self._operation=threading.Lock();self._last_clock=None;self._busy_samples=[];self._busy_sequence=0;self._busy_start=0;self._busy_phase='idle'
 def _sample(self,event):
  if self.closed or not self.enabled or type(event) is not TransportEvent:raise ValueError('shadow_not_enabled')
  f=self.factory;m=self.mount;now=f.clock()
  if type(now) is not int or self._last_clock is not None and now<self._last_clock:raise ValueError('host_clock_rollback')
  self._last_clock=now
  with m._lock:
   if m._closed or not m._ready or not m._attached or m._unknown or m._bundle.host is not f.host or f.host._generation!=self._host_generation or m.dispatcher._permissions is not self._permission or any(m.plugin_host.get(k) is not v for k,v in self._hooks.items()):raise ValueError('mount_retired_or_rebuilt')
   mount_busy=m._busy
  gate=f._provider()
  # A provider stamps its snapshot at completion, not at our earlier entry.
  # Re-sample AFTER provider I/O; otherwise a legitimate 1 ms advance looks
  # like a future snapshot. Still reject future timestamps and clock rollback.
  sampled_now=f.clock()
  if type(sampled_now) is not int or sampled_now<now:raise ValueError('host_clock_rollback')
  self._last_clock=sampled_now
  if type(gate) is not GateState or gate.bot!='Telyin' or gate.destination!='private:local:sharing-preview' or type(gate.observed_ms) is not int or not 0<=sampled_now-gate.observed_ms<=30000 or not all(type(x) is bool for x in (gate.ready,gate.busy,gate.quiet,gate.history_verified)) or not gate.ready or not gate.history_verified:raise ValueError('host_gate_unknown')
  with f._lock:
   original=f._sources.get(event.message_id)
   exact=original==event and original is not None
  if not exact or event.scope not in gate.allowed_scopes or (event.bot,event.scope,event.message_id) in gate.revoked or f._authorize(event,SHADOW_DOMAIN) is not True:raise ValueError('source_authority_lost')
  # active_events alone is not an idle detector: Console retains its last batch.
  # Use the same loop.processing signal as Console.take_batch's guard.
  app=getattr(self.console,'app',None);loop=getattr(app,'loop',None)
  processing=getattr(loop,'processing',None);taking=getattr(self.console,'_taking_batch',None)
  pending=getattr(self.console,'_pending_batches',None)
  if type(processing) is not bool or type(taking) is not bool or type(pending) is not list:raise ValueError('console_readiness_unknown')
  quiet=self.quiet_at(now)
  if type(quiet) is not bool:raise ValueError('quiet_state_unknown')
  # _taking_batch stays true while pump awaits new input; it is a consumer
  # reentrancy guard, NOT an indication that a conversation is processing.
  # Queued debounce input is not yet in Console._pending_batches and
  # loop.processing is still false. Do not start a review in that gap.
  bus=getattr(self.console,'bus',None)
  if type(bus) is not WakeBus:raise ValueError('console_readiness_unknown')
  bus_pending=bus.pending()
  if type(bus_pending) is not int or bus_pending<0:raise ValueError('console_readiness_unknown')
  busy=processing or bool(pending) or bool(bus_pending) or mount_busy or f.host._busy or gate.busy
  # Diagnostic only: never use these snapshots to authorize or relax State.
  values={'processing':processing,'pending_batches':bool(pending),'mount_busy':mount_busy,'host_busy':f.host._busy,'gate_busy':gate.busy,'bus_pending':bool(bus_pending)}
  self._busy_sequence+=1
  self._busy_samples.append({'sequence':self._busy_sequence,'phase':self._busy_phase,'signals':{key:value if type(value) is bool else None for key,value in values.items()}})
  self._busy_samples=self._busy_samples[-16:]
  projection=(gate.revision,gate.ready,gate.history_verified,busy,quiet)
  if self._source_state is not None:
   old=self._source_state
   if gate.revision<old.revision or not gate.revoked.issuperset(old.revoked) or gate.revision==old.revision and replace(gate,observed_ms=0)!=replace(old,observed_ms=0):raise ValueError('host_state_rollback')
  self._source_state=gate
  if self._projection is not None and self._projection!=projection:
   names=('source_revision','ready','history_verified','busy','quiet')
   changed=tuple(name for name,old,new in zip(names,self._projection,projection) if old!=new)
   self._revision+=1;self._transitions.append((self._revision,changed));self._transitions=self._transitions[-16:]
  self._projection=projection
  return State(self._revision,True,False,True,busy,quiet)
 def provider(self,event):
  with self._lock:return self._sample(event)
 def authorize_source(self,event,domain):
  # Preview configuration MUST NOT become real-domain authorization.
  if domain!=SHADOW_DOMAIN:return False
  try:self.provider(event);return True
  except Exception:return False
 def resolve(self,source_ref):
  self.factory._provider()
  with self.factory._lock:
   matches=[self.factory._sources[k] for k,h in self.factory._hints.items() if k in self.factory._sources and h.get('source_ref')==source_ref]
   if type(source_ref) is not str or len(matches)!=1:raise ValueError('source_reference_not_owned')
   event=matches[0]
  self.provider(event);return event
 def _busy_diagnostic(self):
  samples=[{'sequence':r['sequence'],'phase':r['phase'],'signals':dict(r['signals'])} for r in self._busy_samples if r['sequence']>self._busy_start]
  return {'samples':samples,'history_truncated':bool(self._busy_samples and self._busy_start<self._busy_samples[0]['sequence']-1),'sampling':'non_atomic_observations_not_complete_event_history'}
 def _record_change(self,before,after):
  names=set()
  for revision,fields in self._transitions:
   if before.revision<revision<=after.revision:names.update(fields)
  for name in ('authorized','revoked','history_verified','busy','quiet'):
   if getattr(before,name)!=getattr(after,name):names.add(name)
  self.last_transition={'fields':sorted(names),'history_truncated':bool(self._transitions and before.revision<self._transitions[0][0]-1),'current_busy':after.busy,'current_quiet':after.quiet,'busy_diagnostics':self._busy_diagnostic()}
 def shadow_preview(self,source_ref,proposal):
  if not self._operation.acquire(blocking=False):return HostBridge.result('defer','shadow_operation_busy')
  bridge=None;self.last_failure=None;self.last_transition=None;self.service_calls={'assess':0,'review':0};self._busy_start=self._busy_sequence;self._busy_phase='before_preview'
  try:
   event=self.resolve(source_ref);before=self.provider(event)
   if before.busy:return HostBridge.result('defer','host_busy')
   if not self.authorize_source(event,SHADOW_DOMAIN):raise ValueError('source_authority_required')
   def checked(fn,kind):
    def call(value):
     self._busy_phase='before_'+kind
     current=self.provider(event)
     if current!=before:
      self._record_change(before,current);raise ValueError('source_or_readiness_changed')
     self.service_calls[kind]+=1
     return fn(value)
    return call
   bridge=HostBridge(bot='Telyin',destination='private:qq:1228600196',allowed_scopes=frozenset({'group:qq:290016343'}),clock=self.factory.clock,assess=checked(self.factory._assess,'assess'),review=checked(self.factory._review,'review'))
   result=bridge.preview(event,parse_proposal(proposal),ready=True,busy=False,quiet=before.quiet,history_verified=True,attempts=())
   self._busy_phase='after_preview'
   current=self.provider(event)
   if current!=before:
    self._record_change(before,current)
    return HostBridge.result('deny','source_or_readiness_changed')
   return result
  except Exception as exc:
   self.last_failure=diagnose(exc,'shadow_preview')
   return HostBridge.result('deny','host_shadow_unavailable')
  finally:
   if bridge is not None:bridge.close()
   self._busy_phase='idle'
   self._operation.release()
 def make_disabled_broker(self,budget,journal):
  # No API accepts a real exchange or enable flag in this binding.
  return LiveBroker(budget,journal,clock=self.factory.clock,provider=self.provider,assess=self.factory._assess,review=self.factory._review,authorize_source=self.authorize_source,exchange=None,enabled=False)
 def close(self):
  with self._lock:self.closed=True;self._projection=None;self._source_state=None
 def shutdown_complete(self):return self.closed and not self._operation.locked()
