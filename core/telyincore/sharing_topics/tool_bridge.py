"""Async review before existing synchronous Dispatcher; last gate in original handler.
Does not replace model arguments, schemas, permissions, barriers or receipt pairing.
Plain text, quoted replies and voice-text tools use the same current-batch review.
"""
from dataclasses import replace
from contextlib import contextmanager
import inspect,json,threading,sys
from telyincore.dispatch import Dispatcher
from telyincore.stream import ToolCall
from telyincore.tools import ToolDef,ToolTag
from telyincore.console import Console
SUPPORTED=frozenset({'qq_send_text','qq_send_reply','qq_send_voice'})
def unique(pairs):
 result={}
 for key,value in pairs:
  if key in result:raise ValueError('duplicate_argument')
  result[key]=value
 return result
class ToolReplyBridge:
 def __init__(self,runtime,dispatcher):
  if type(dispatcher) is not Dispatcher or dispatcher is not runtime.binding.mount.dispatcher:raise ValueError('owned_dispatcher_required')
  self.runtime=runtime;self.dispatcher=dispatcher;self.operation=threading.Lock();self.closed=False;self.unknown=False;self.active=None
  self.counts={'tool_batches':0,'eligible':0,'guarded_handler':0,'unsupported_speech':0,'permission_or_shape_skipped':0}
 def candidate(self,calls):
  for call in calls:
   if type(call) is not ToolCall:return None
   tool=self.dispatcher._table.get(call.name)
   if type(tool) is not ToolDef:return None
   if ToolTag.SPEAK in (tool.tags or ()):
    if call.name not in SUPPORTED:self.counts['unsupported_speech']+=1;return None
    if not callable(tool.handler) or tool.effect!='immediate' or not call.call_id or type(call.arguments) is not str or len(call.arguments.encode())>16384:return None
    try:
     args=json.loads(call.arguments,object_pairs_hook=unique)
     if type(args) is not dict or args.get('scope')!='private' or args.get('target')!='1228600196' or type(args.get('text')) is not str:return None
     inspect.signature(tool.handler,follow_wrapped=False).bind(**args)
     context=self.runtime.adapter._context()
     if call.name=='qq_send_reply' and args.get('message_id') not in {r[2] for r in context[4]}:return None
     permission=self.dispatcher._permissions
     if permission is not None and permission(tool) is not True:return None
    except Exception:return None
    return call,tool,args,context
   if tool.barrier_after or tool.ends_turn:return None
  return None
 async def run(self,calls,delegate):
  self.counts['tool_batches']+=1
  rt=self.runtime
  if self.closed or not rt.allowed():return await delegate(calls)
  if not self.operation.acquire(False):return await delegate(calls)
  runtime_locked=False;ticket=None;output=None;changed=None
  try:
   candidate=self.candidate(calls)
   if candidate is None:self.counts['permission_or_shape_skipped']+=1;return await delegate(calls)
   call,original,args,context=candidate
   runtime_locked=rt.operation.acquire(False)
   if not runtime_locked:return await delegate(calls)
   if context[-1] in rt.attempted or len(rt.attempted)>=32:return await delegate(calls)
   rt.attempted.add(context[-1]);self.counts['eligible']+=1
   try:
    with rt.queue.lock:
     rt.queue._active();rt.queue._prune(rt.queue._now());ticket=next(iter(rt.queue.items),None);entry=rt.queue.items.get(ticket)
    if entry is not None:
     draft=rt.builder(args['text'],entry.ref)
     if inspect.isawaitable(draft):draft=await draft
     if call.name=='qq_send_voice' and (type(draft) is not str or len(draft)>1000):draft=None
     if rt.allowed() and rt.adapter._context()==context:
      result=await rt._review(rt.adapter.prepare,ticket,final_text=draft);rt.counts['reviewed']+=1
      if result.get('status')=='prepared':output=result['output']
   except Exception:output=None
   if self.closed:raise RuntimeError('tool_boundary_closed')
   if output is None or not rt.allowed() or self.dispatcher._table.get(call.name) is not original:return await delegate(calls)
   def guarded(**actual):
    # Dispatcher still performs signature, scope permission, effect and barrier
    # checks. This handler is entered only after those original checks pass.
    self.counts['guarded_handler']+=1
    updated=dict(actual);augmented=False
    if not self.closed and rt.allowed() and actual==args and self.dispatcher._table.get(call.name) is changed:
     value=rt.adapter.take(output)
     if value is not None and rt.allowed():updated['text']=value;augmented=True
    try:receipt=original.handler(**updated)
    except BaseException:
     rt.counts['delivery_unknown']+=1;raise # Never retry or fall back after I/O.
    rt.counts['augmented' if augmented else 'plain']+=1
    return receipt
   guarded.__signature__=inspect.signature(original.handler,follow_wrapped=False)
   changed=replace(original,handler=guarded)
   self.active=(call.name,original,changed)
   self.dispatcher._table[call.name]=changed
   return await delegate(calls) # ORIGINAL call objects, IDs and JSON arguments.
  finally:
   if changed is not None:
    if self.dispatcher._table.get(call.name) is changed:self.dispatcher._table[call.name]=original
    elif self.dispatcher._table.get(call.name) is not original:self.unknown=True
   self.active=None
   with rt.queue.lock:
    if ticket is not None:rt.queue.items.pop(ticket,None)
    record=getattr(rt.adapter,'_prepared',None)
    if output is not None and record is not None and record[0] is output:rt.adapter._prepared=None
   if runtime_locked:rt.operation.release()
   self.operation.release()
   rt._notify()
 def close(self):
  self.closed=True
  if self.active is not None:
   name,original,changed=self.active
   if self.dispatcher._table.get(name) is changed:self.dispatcher._table[name]=original
   elif self.dispatcher._table.get(name) is not original:self.unknown=True
 def shutdown_complete(self):return self.closed and not self.unknown and not self.operation.locked() and self.active is None
 def summary(self):return {'closed':self.closed,'counts':dict(self.counts),'ownership_unknown':self.unknown}
@contextmanager
def console_tool_boundary(owner_provider):
 """Process-local explicit entry only; ordinary Core is never monkey-patched."""
 original=Console.assemble
 def assemble(console,*a,**kw):
  delegate=kw.get('run_tools')
  if callable(delegate):
   async def run_tools(calls):
    owner=owner_provider();rt=getattr(owner,'runtime',None)
    if rt is None or rt.console is not console or not rt.allowed():return await delegate(calls)
    bridge=getattr(rt,'tool_bridge',None)
    if bridge is None:bridge=rt.tool_bridge=ToolReplyBridge(rt,rt.binding.mount.dispatcher)
    return await bridge.run(calls,delegate)
   kw={**kw,'run_tools':run_tools}
  return original(console,*a,**kw)
 Console.assemble=assemble
 try:yield
 finally:
  if Console.assemble is not assemble:
   primary=sys.exception()
   if primary is not None:primary.add_note('console_assembly_hook_ownership_lost')
   else:raise RuntimeError('console_assembly_hook_ownership_lost')
  else:Console.assemble=original
