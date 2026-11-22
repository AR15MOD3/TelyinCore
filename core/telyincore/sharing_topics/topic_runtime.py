"""Opt-in host output hook. Default off; never registers a send/model tool.
Normal output uses the original router exactly once; no retry/fallback after wire.
Review runs in one owned worker; shutdown must prove that worker's future is done.
"""
import asyncio,concurrent.futures,inspect,threading
from .topic_queue import TopicQueue
from .natural_adapter import NaturalTopicAdapter
class TopicRuntime:
 def __init__(self,binding,*,enabled=False,mode_authority=None,draft_builder=None,review_runner=None):
  if type(enabled) is not bool or enabled and (not callable(mode_authority) or not callable(draft_builder)):raise ValueError('explicit_host_dependencies_required')
  self.on_status=None;self.audit_unknown=False
  self.binding=binding;self.queue=TopicQueue(binding,enabled=enabled);self.adapter=NaturalTopicAdapter(self.queue,enabled=enabled)
  self.enabled=enabled;self.authority=mode_authority;self.builder=draft_builder;self.review_runner=review_runner
  self.console=binding.console;self.old=None;self.hook=None;self.closed=False;self.unknown=False
  self.operation=threading.Lock();self.executor=None;self.futures=[];self.attempted=set();self.counts={'queued':0,'reviewed':0,'augmented':0,'plain':0,'delivery_unknown':0}
  self._output_loop=None
 def _notify(self):
  if self.on_status is not None:
   try:self.on_status()
   except Exception:self.audit_unknown=True
 def allowed(self):
  if self.closed or not self.enabled:return False
  try:return self.authority() is True
  except Exception:return False
 def observe(self,ref,proposal):
  if not self.allowed():return {'status':'disabled'}
  result=self.queue.enqueue(ref,proposal)
  if result.get('status')=='queued':self.counts['queued']+=1
  if not self.allowed():self.queue.close();return {'status':'disabled'}
  return result
 def attach(self):
  if not self.enabled:return {'status':'disabled'}
  if not self.allowed():raise ValueError('explicit_mode_required')
  if self.hook is not None or not callable(self.console.output_tap):raise ValueError('single_existing_router_required')
  self.old=self.console.output_tap
  async def hook(batch,text):return await self.output(batch,text)
  self.hook=hook;self.console.output_tap=hook
  return {'status':'attached'}
 async def _review(self,fn,*args,**kw):
  if self.review_runner is not None:return await self.review_runner(fn,*args,**kw)
  if self.executor is None:self.executor=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='natural-topic-owned')
  # One output operation at a time. No unbounded executor backlog.
  self.futures=[f for f in self.futures if not f.done()]
  future=self.executor.submit(fn,*args,**kw);self.futures.append(future)
  return await asyncio.shield(asyncio.wrap_future(future))
 def _owned(self):return self.hook is not None and self.console.output_tap is self.hook
 async def output(self,batch,text):
  if not self._owned():raise RuntimeError('topic_hook_ownership_lost')
  loop=asyncio.get_running_loop()
  if self._output_loop is None:
   self._output_loop=loop;self.adapter._owner_thread=threading.get_ident()
  elif self._output_loop is not loop:raise RuntimeError('topic_output_loop_changed')
  original=text;out=text;ticket=None;augmented=False
  acquired=self.operation.acquire(False)
  try:
   if acquired and self.allowed() and type(text) is str and batch is self.console._active_batch:
    try:
     context=self.adapter._context()
     if context[-1] not in self.attempted and len(self.attempted)<32:
      self.attempted.add(context[-1])
      with self.queue.lock:
       self.queue._active();self.queue._prune(self.queue._now())
       ticket=next(iter(self.queue.items),None)
       entry=self.queue.items.get(ticket)
      if entry is not None:
       # Host-supplied builder may resolve the ephemeral source in its own
       # sandbox. It is not a model-callable tool and never gets routing power.
       draft=self.builder(original,entry.ref)
       if inspect.isawaitable(draft):draft=await draft
       if self.allowed() and self.adapter._context()==context:
        captured=self.adapter.capture(ticket)
        prepared=await self._review(self.adapter.prepare,ticket,final_text=draft,captured=captured)
        self.counts['reviewed']+=1
        if self.allowed() and prepared.get('status')=='prepared':
         value=self.adapter.take(prepared['output'])
         if value is not None and self.allowed():out=value;augmented=True
    except asyncio.CancelledError:
     # Finally burns the topic, no output is dispatched after cancellation.
     raise
    except Exception:
     # Draft/review failure may retain the normal reply, never unreviewed text.
     out=original;augmented=False
   if not self._owned():raise RuntimeError('topic_hook_ownership_lost')
   # Original router remains responsible for its source/current-recipient gate.
   # There is no await between final mode check and the single router invocation.
   if augmented and not self.allowed():out=original;augmented=False
   try:
    result=self.old(batch,out)
    if inspect.isawaitable(result):result=await result
   except BaseException:
    self.counts['delivery_unknown']+=1
    raise # NO second call with the original reply after an uncertain result.
   self.counts['augmented' if augmented else 'plain']+=1
   return result
  finally:
   if ticket is not None:
    with self.queue.lock:self.queue.items.pop(ticket,None)
   if acquired:self.operation.release()
   self._notify()
 def close(self):
  self.closed=True;self.enabled=False
  if self.hook is not None:
   if self.console.output_tap is self.hook:self.console.output_tap=self.old
   elif self.console.output_tap is not self.old:self.unknown=True
  bridge=getattr(self,'tool_bridge',None)
  if bridge is not None:bridge.close()
  self.adapter.close();self.queue.close()
  if self.executor is not None:self.executor.shutdown(wait=False,cancel_futures=True)
  self._notify()
 def shutdown_complete(self):return self.closed and not self.unknown and not self.operation.locked() and all(f.done() for f in self.futures) and (self.executor is None or all(not t.is_alive() for t in self.executor._threads)) and self.adapter.shutdown_complete() and self.queue.shutdown_complete() and (getattr(self,'tool_bridge',None) is None or self.tool_bridge.shutdown_complete())
 def summary(self):
  data={'enabled':self.allowed(),'closed':self.closed,'counts':dict(self.counts),'pending_worker_count':sum(not f.done() for f in self.futures),'automatic_proactive_sharing_enabled':False,'audit_publication_failed':self.audit_unknown}
  if getattr(self,'tool_bridge',None) is not None:data['tool_boundary']=self.tool_bridge.summary()
  return data
