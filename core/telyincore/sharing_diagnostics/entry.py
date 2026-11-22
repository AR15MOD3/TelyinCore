"""Explicit dedicated-process diagnostic integration. Default is check-only.
No CLI run command; main is check-only. run_shadow() needs new operator consent.
"""
from pathlib import Path
from contextlib import contextmanager
import sys,threading,json
sys.dont_write_bytecode=True
from telyincore.sharing_topics import entry as topics
from telyincore.sharing_delivery import entry as delivery
from telyincore.console import Console
from telyincore.plugin_ingress import PluginIngress
from telyincore.events import EventStore
from telyincore.dispatch import Dispatcher
from .route_probe import ScopedRouteProbe
EXTRA='''    _sharing_diag_original_bind = _bind_plugin_ingress
    def _bind_plugin_ingress(running):
        _sharing_diag_original_bind(running)
        _sharing_route_diagnostics_bind(running, plugin_ingress, plugins)
'''
class DiagnosticEntry:
 def __init__(self,*,expected_text):
  if type(expected_text) is not str or not expected_text.startswith('[分享测试]') or len(expected_text.encode('utf-8'))>512:raise ValueError('new_exact_test_text_required')
  self._expected=expected_text;self.owner=None;self.probe=None;self.sink=None;self.folder=None
  self.bound=False;self.armed=False;self.closed=False;self.unknown=False;self.audit_failed=False;self.hooks_installed=False
  self.lock=threading.RLock();self.sink_lock=threading.RLock();self.modules=[];self.owner_after=None
 def summary(self):
  with self.lock:return {'diagnostic_entry_bound':self.bound,'diagnostic_entry_closed':self.closed,'diagnostic_sampling_armed':self.armed,'hooks_installed':self.hooks_installed,'ownership_unknown':self.unknown,'audit_failed':self.audit_failed,'route':None if self.probe is None else self.probe.summary(),'automatic_sharing_enabled_by_diagnostics':False}
 def publish(self,*args):
  try:
   with self.sink_lock:
    if self.sink is not None:self.sink(self.summary())
  except Exception:self.audit_failed=True
 def bind(self,console,ingress,registry):
  # Only references explicitly passed by the compiled controlled entry, no scan.
  if self.closed or self.bound or self.owner is None:raise RuntimeError('single_explicit_diagnostic_binding_required')
  mount=getattr(self.owner,'mount',None);dispatcher=getattr(mount,'dispatcher',None)
  if type(console) is not Console or type(ingress) is not PluginIngress or type(ingress.store) is not EventStore or type(dispatcher) is not Dispatcher:raise ValueError('exact_host_components_required')
  if mount.plugin_host.get('sharing_shadow_console')() is not console:raise ValueError('diagnostic_owner_console_mismatch')
  selected=[obj for name,obj in registry._active_items() if name=='core-infinity-qq']
  if len(selected)!=1:raise ValueError('one_active_qq_plugin_required')
  qq=selected[0]
  code=getattr(getattr(getattr(qq,'_post',None),'__func__',None),'__code__',None)
  core=Path(__import__('telyincore').__file__).resolve().parent.parent
  if code is None or Path(code.co_filename).resolve()!=core/'extensions/core-infinity-qq/index.py':raise ValueError('installed_qq_plugin_required')
  self.probe=ScopedRouteProbe(qq=qq,store=ingress.store,ingress=ingress,console=console,dispatcher=dispatcher,expected_text=self._expected,enabled=False,on_status=self.publish,max_seconds=180)
  self._expected=None;self.bound=True;self.publish()
 def arm(self):
  if self.closed or not self.bound or self.armed or self.probe is None:raise RuntimeError('single_ready_probe_required')
  rt=getattr(self.owner,'runtime',None)
  if rt is None or not rt.allowed():raise RuntimeError('approved_topic_runtime_not_ready')
  with rt.queue.lock:
   if len(rt.queue.items)!=1:raise RuntimeError('one_prepared_topic_required')
  self.probe.deadline=self.probe.clock()+180;self.probe.enabled=True;self.probe.attach();self.armed=True;self.publish()
 def close(self):
  self.closed=True;self._expected=None
  if self.probe is not None:self.probe.close()
  if self.owner_after is not None:
   owner,had,prior,wrapper=self.owner_after
   if getattr(owner,'_after_preview',None) is not wrapper:self.unknown=True
   elif had:owner._after_preview=prior
   else:delattr(owner,'_after_preview')
   self.owner_after=None
  self.publish()
 def shutdown_complete(self):return self.closed and not self.hooks_installed and not self.unknown and (self.probe is None or self.probe.shutdown_complete())
 @contextmanager
 def hooks(self):
  if self.closed or self.hooks_installed:raise RuntimeError('diagnostic_entry_lifecycle_invalid')
  original_insert=delivery.INSERT;original_load=delivery.load_core;original_owned=delivery.run_owned;original_owner=topics.TopicOwner
  marker=original_insert+EXTRA
  def owner_factory(*a,**kw):
   if self.owner is not None:raise RuntimeError('single_topic_owner_required')
   self.owner=original_owner(*a,**kw)
   old_after=self.owner._after_preview
   def after(*args,**kwargs):
    result=old_after(*args,**kwargs)
    if result.get('topic_queue_status')=='queued' and not self.armed:self.arm()
    return result
   self.owner_after=(self.owner,'_after_preview' in vars(self.owner),vars(self.owner).get('_after_preview'),after)
   self.owner._after_preview=after
   return self.owner
  def load(core):
   module=original_load(core)
   if hasattr(module,'_sharing_route_diagnostics_bind'):raise RuntimeError('diagnostic_callback_collision')
   callback=self.bind;module._sharing_route_diagnostics_bind=callback;self.modules.append((module,callback));return module
  def owned(core,module,kernel,folder,dependencies):
   if self.folder is not None:raise RuntimeError('single_diagnostic_run_required')
   self.folder=Path(folder);self.sink=delivery.writer(self.folder/'route-diagnostics.json');self.publish()
   try:return original_owned(core,module,kernel,folder,dependencies)
   finally:
    self.close() # Final proof is written only after ALL process hooks restore.
  bindings=[(delivery,'INSERT',original_insert,marker),(delivery,'load_core',original_load,load),(delivery,'run_owned',original_owned,owned),(topics,'TopicOwner',original_owner,owner_factory)]
  try:
   for obj,key,old,new in bindings:setattr(obj,key,new)
   self.hooks_installed=True
   yield self
  finally:
   self.close()
   for module,callback in self.modules:
    if getattr(module,'_sharing_route_diagnostics_bind',None) is callback:delattr(module,'_sharing_route_diagnostics_bind')
    else:self.unknown=True
   self.modules.clear()
   for obj,key,old,new in reversed(bindings):
    if getattr(obj,key) is new:setattr(obj,key,old)
    else:self.unknown=True
   self.hooks_installed=False;self.publish()
   if self.folder is not None:
    primary=sys.exception()
    try:delivery.writer(self.folder/'route-diagnostics-exit.json')({'diagnostic_resources_confirmed':self.shutdown_complete(),'audit_publication_failed':self.audit_failed,'automatic_sharing_enabled':False})
    except Exception:
     self.audit_failed=True
     if primary is not None:primary.add_note('diagnostic_exit_audit_unconfirmed')
     else:raise RuntimeError('diagnostic_exit_audit_unconfirmed') from None
   if not self.shutdown_complete():
    primary=sys.exception()
    if primary is not None:primary.add_note('diagnostic_resources_unconfirmed')
    elif not self.unknown:raise RuntimeError('diagnostic_resources_unconfirmed')
   if self.unknown:
    primary=sys.exception()
    if primary is not None:primary.add_note('diagnostic_entry_hook_ownership_lost')
    else:raise RuntimeError('diagnostic_entry_hook_ownership_lost')
 def run_shadow(self,core):
  core=Path(core)
  if not topics.load_topics(core/'data/sharing-topics.json')[0]['enabled']:raise ValueError('explicit_topic_opt_in_required')
  with self.hooks():return topics.run_shadow(core)
def inspect(core=None):
 return {**topics.inspect(core),'diagnostic_hooks_attached':False,'diagnostic_candidate_only':False,'diagnostic_package_installed':True,'new_operator_consent_required_for_run':True}
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');p.parse_args();state=inspect();print(json.dumps(state));raise SystemExit(0 if state['installed_core_matches'] else 2)
