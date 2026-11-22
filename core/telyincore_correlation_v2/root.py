"""Exit-only root/lock/lifecycle retention. NOT native or dispatch admission.

No IO on import. Default bounded book pins roots for this process lifetime.
No release/reset/recheck/adopt-by-PID/force-unlock API while native is UNKNOWN.
"""
from pathlib import Path
import asyncio
from .work_bridge import ToolWorkIncomplete
from .codec import Binding
from .custody import ResponsibilityOwner

class RootBook:
 def __init__(self,*,limit=16):
  if type(limit) is not int or not 1<=limit<=32:raise ValueError('bounded_root_book_required')
  self._limit=limit;self._roots=[]
 def _register(self,root):
  if len(self._roots)>=self._limit or any(r._binding==root._binding or r.owner is root.owner or r._publisher is root._publisher for r in self._roots):raise ValueError('root_reservation_rejected')
  self._roots.append(root) # before lock acquisition; no eviction
 def snapshot(self):return {'retained_roots':len(self._roots),'native_completion':'UNKNOWN','release_allowed':False,'complete':False}
_DEFAULT_BOOK=RootBook()

class RootBinding:
 label='correlation_root_lifetime'
 def __init__(self,expected,owner,root_ref,bot,publisher_ref,shutdown_ref,lifecycle,*,book=None):
  if type(expected) is not Binding or type(owner) is not ResponsibilityOwner or owner._binding!=expected or not isinstance(root_ref,Path) or not root_ref.is_absolute() or str(root_ref).startswith(('\\\\','//')) or '\0' in str(root_ref) or type(bot) is not str or not bot or '\0' in bot or len(bot)>80 or type(lifecycle) is not dict or lifecycle or publisher_ref is None or shutdown_ref is None:raise ValueError('exact_local_root_binding_required')
  if book is None:book=_DEFAULT_BOOK
  if type(book) is not RootBook:raise ValueError('exact_root_book_required')
  self._binding=expected;self._owner=owner;self._root_ref=root_ref;self._root_text=str(root_ref);self._bot=bot;self._publisher=publisher_ref;self._shutdown=shutdown_ref;self._lifecycle=lifecycle;self._book=book
  self._scope=None;self._scope_bound=False;self._runtime_refs=None;self._handle=None;self._release=None;self._acquire=None;self._acquire_args=None;self._acquire_attempted=False;self._acquire_error=None;self._closed=False;self._faulted=False;self._mismatch_refs=None
  book._register(self)
 @property
 def owner(self):return self._owner
 @property
 def lifecycle_ref(self):return self._lifecycle
 def check_entry(self,publisher,shutdown,lifecycle):
  if self._closed or publisher is not self._publisher or shutdown is not self._shutdown or lifecycle is not self._lifecycle:self._reject()
 def check_selection(self,root,bot):
  if self._closed or str(root)!=self._root_text or bot!=self._bot:self._reject()
 def require_native_admission(self):
  # No qualified native issuer exists here. No snapshot/config/key can arm it.
  self._reject()
 def capture_scope(self,scope):
  if self._closed or self._scope_bound or self._lifecycle.get('scope') is not scope:self._reject()
  self._scope=scope;self._scope_bound=True
 def capture_runtime(self,tool_work,model_work,local_model):
  if self._closed or self._runtime_refs is not None:self._reject()
  self._runtime_refs=(tool_work,model_work,local_model)
 def acquire_lock(self,acquire,*args,release):
  if self._closed or self._acquire_attempted or not self._scope_bound or not callable(acquire) or not callable(release):self._reject()
  self._acquire_attempted=True;self._acquire=acquire;self._acquire_args=args;self._release=release
  try:self._handle=acquire(*args) # only original callback; no new file or fallback
  except BaseException as e:
   self._acquire_error=e;self.seal();self._retain(e);raise
  self._lifecycle['handle']=self._handle # retain before returning to caller
  return self._handle
 def seal(self):self._closed=True;self._owner.seal()
 def _reject(self):
  self._faulted=True;self.seal();raise ToolWorkIncomplete(self)
 def _retain(self,primary):
  try:
   prior=getattr(primary,'_v2_retained_roots',())
   if type(prior) is not tuple:raise ValueError('root_carrier_rejected')
   if not any(x is self for x in prior):
    if len(prior)>=32:raise ValueError('root_carrier_full')
    primary._v2_retained_roots=prior+(self,);primary.add_note('correlation_root_lifetime: original resources retained; native UNKNOWN')
  except BaseException:raise ToolWorkIncomplete(self) from primary
 def finish_sync(self,*,primary=None):
  self.seal()
  if primary is None or isinstance(primary,asyncio.CancelledError):raise ToolWorkIncomplete(self) from primary
  self._retain(primary)
 def block_release(self,handle,release,scope=None,*,primary=None):
  # Never execute release(handle) or scope.finish: root/native state unqualified.
  if handle is not self._handle or release is not self._release or scope is not self._scope or self._lifecycle.get('handle') is not self._handle or self._lifecycle.get('scope') is not self._scope:
   self._faulted=True
   if self._mismatch_refs is None:self._mismatch_refs=(handle,release,scope)
  self.finish_sync(primary=primary)
 def snapshot(self):
  return {'component':self.label,'closed':self._closed,'faulted':self._faulted,'root_registered_before_acquire':True,'scope_bound':self._scope_bound,'runtime_refs_captured':self._runtime_refs is not None,'lock_acquire_attempted':self._acquire_attempted,'lock_handle_retained':self._handle is not None,'original_release_retained':self._release is not None,'original_scope_retained':self._scope is not None,'acquire_failed':self._acquire_error is not None,'outer_custody':self._owner.snapshot(),'owned':{'pending':1,'failed':int(self._faulted or self._acquire_error is not None),'done':0,'cancelled':0},'binding_qualification':'LOCAL_REFERENCE_CHECK_ONLY','production_dispatch_admission':'NOT_IMPLEMENTED','native_completion':'UNKNOWN','release_allowed':False,'can_bind':False,'complete':False}
 def __repr__(self):return '<RootBinding original root/lock/lifecycle refs retained; native UNKNOWN>'
