from pathlib import Path
import json,time
from telyincore.sharing_delivery.formal_owner import FormalDeliveryOwner
from .configuration import load_topics,configured_runtime
from telyincore.sharing_close_budget import bounded_close, remaining
class TopicOwner(FormalDeliveryOwner):
 def __init__(self,kernel,*,topics_config,**kw):
  cfg,_=load_topics(topics_config)
  if kw.get('delivery_enabled',False):raise ValueError('natural_topics_and_proactive_dispatch_are_separate')
  self.topics_config=Path(topics_config);self.runtime=None;self.topics_closing=False
  super().__init__(kernel,**kw)
 def _after_preview(self,ref,proposal,result):
  base=super()._after_preview(ref,proposal,result)
  cfg,_=load_topics(self.topics_config)
  if not cfg['enabled'] or result.get('action')!='topic':return {**base,'topic_queue_status':'disabled_or_not_topic'}
  with self.lock:
   if self.closed or self.topics_closing:return {**base,'topic_queue_status':'closed'}
   if self.runtime is None:
    self.runtime=configured_runtime(self.binding,self.topics_config)
    self.runtime.on_status=self._publish
    self.runtime.attach()
   runtime=self.runtime
  queued=runtime.observe(ref,json.dumps(proposal,ensure_ascii=False))
  # Opaque queue tickets stay inside this owner; audit contains status/counts only.
  return {**base,'topic_queue_status':queued.get('status','rejected')}
 @bounded_close
 def close(self):
  with self.lock:
   self.topics_closing=True;self.closed=True;self.stop.set();rt=self.runtime
   if rt is not None:rt.close()
  # Join only exact threads owned by this runtime. Never kill named processes.
  if rt is not None and rt.executor is not None:
   for thread in tuple(rt.executor._threads):thread.join(remaining())
  super().close()
  if rt is not None and not rt.shutdown_complete():raise RuntimeError('natural_topic_resources_unconfirmed')
 def shutdown_complete(self):return super().shutdown_complete() and (self.runtime is None or self.runtime.shutdown_complete())

 def summary(self):
  data=super().summary()
  if self.runtime is not None:data["natural_topics"]={"runtime":self.runtime.summary(),"queue":self.runtime.queue.summary()}
  return data
