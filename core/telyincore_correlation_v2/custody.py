"""Explicit local outer responsibility owner, not native owner or daemon.

Reserve before constructing GuardedSession. No release/transfer/eviction API.
An actual root must retain this owner (or its exception); root mount is absent.
"""
import asyncio
from .work_bridge import ToolWorkIncomplete
from .codec import Binding
from .guarded import GuardedSession
from .io import IOUnknown,timeout_value
class _Ticket:
 __slots__=()
 def __repr__(self):return '<CustodyTicket redacted>'
class ResponsibilityOwner:
 label='correlation_outer_custody'
 def __init__(self,expected,*,limit=16):
  if type(expected) is not Binding or type(limit) is not int or not 1<=limit<=32:raise ValueError('bounded_exact_custody_required')
  self._binding=expected;self._limit=limit;self._records={};self._closed=False;self._draining=False
 def create(self,expected,*args,**kwargs):
  if self._closed or self._draining or len(self._records)>=self._limit or type(expected) is not Binding or expected!=self._binding:raise IOUnknown()
  t=_Ticket();r={'inputs':(expected,args,kwargs),'guard':None,'setup_error':None,'exit_error':None};self._records[t]=r
  try:r['guard']=GuardedSession(expected,*args,**kwargs)
  except BaseException as e:
   r['setup_error']=e
   if isinstance(e,ToolWorkIncomplete) and type(e.scope) is GuardedSession:r['guard']=e.scope
   self.seal();self._retain(e);raise
  return t
 def _record(self,t):
  if type(t) is not _Ticket or t not in self._records:raise IOUnknown()
  return self._records[t]
 async def read_receipt(self,t,wire,*,timeout=2.0):
  r=self._record(t)
  if self._closed or r['guard'] is None:raise IOUnknown()
  try:return await r['guard'].read_receipt(wire,timeout=timeout)
  except BaseException as e:self.seal();self._retain(e);raise
 def seal(self):
  self._closed=True
  for r in self._records.values():
   if r['guard'] is not None:r['guard'].seal()
 def _retain(self,primary):
  try:
   prior=getattr(primary,'_v2_retained_owners',())
   if type(prior) is not tuple:raise ValueError('carrier_rejected')
   if not any(x is self for x in prior):
    if len(prior)>=32:raise ValueError('carrier_full')
    primary._v2_retained_owners=prior+(self,)
    primary.add_note('correlation_outer_custody: local responsibility retained; native UNKNOWN')
  except BaseException:raise ToolWorkIncomplete(self) from primary
 def snapshot(self):
  rows=[r['guard'].snapshot() if r['guard'] is not None else None for r in self._records.values()]
  return {'component':self.label,'closed':self._closed,'draining':self._draining,'retained_records':len(rows),'setup_failures':sum(r['setup_error'] is not None for r in self._records.values()),'exit_observations':sum(r['exit_error'] is not None for r in self._records.values()),'owned':{'pending':sum(x is None or not x['local_shutdown_complete'] for x in rows),'failed':sum(r['setup_error'] is not None for r in self._records.values()),'done':sum(bool(x and x['local_shutdown_complete']) for x in rows),'cancelled':0},'sessions':rows,'root_responsibility_receiver':'LOCAL_EXPLICIT_OBJECT_ONLY','production_root_retention':'NOT_MOUNTED','native_completion':'UNKNOWN','release_allowed':False,'can_bind':False,'complete':False}
 def finish_sync(self,*,primary=None):
  self.seal()
  if not self._records:return
  if primary is None or isinstance(primary,asyncio.CancelledError):raise ToolWorkIncomplete(self) from primary
  self._retain(primary)
 async def close(self,*,timeout=2.0):
  timeout_value(timeout)
  if self._draining:raise ToolWorkIncomplete(self)
  self.seal();self._draining=True;deadline=asyncio.get_running_loop().time()+timeout
  try:
   for r in self._records.values():
    if r['guard'] is not None:
     try:await r['guard'].close(timeout=max(0,deadline-asyncio.get_running_loop().time()))
     except BaseException as e:
      r['exit_error']=e
      cancelled=isinstance(e,asyncio.CancelledError) or (isinstance(e,ToolWorkIncomplete) and isinstance(e.__cause__,asyncio.CancelledError))
      if cancelled:raise ToolWorkIncomplete(self) from e
      if not isinstance(e,ToolWorkIncomplete):
       self._retain(e);raise # unexpected primary is never swallowed
   self.finish_sync();return self.snapshot()
  finally:self._draining=False
 def __repr__(self):return '<ResponsibilityOwner explicit local retained responsibility>'
