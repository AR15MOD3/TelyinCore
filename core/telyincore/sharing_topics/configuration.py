from pathlib import Path
import json,hashlib
from .topic_runtime import TopicRuntime
def load_topics(path):
 path=Path(path);raw=path.read_bytes();cfg=json.loads(raw)
 if type(cfg) is not dict or set(cfg)!={'enabled','bot_id','recipient','mode'} or type(cfg['enabled']) is not bool or cfg['bot_id']!='Telyin' or cfg['recipient']!='1228600196' or cfg['mode'] not in ('disabled','natural-topic') or cfg['enabled']!=(cfg['mode']=='natural-topic'):raise ValueError('explicit_topic_mode_required')
 pinned=hashlib.sha256(raw).digest()
 def authority():
  try:return cfg['enabled'] is True and hashlib.sha256(path.read_bytes()).digest()==pinned
  except OSError:return False
 return cfg,authority
class ReviewedQuoteBuilder:
 def __init__(self,queue):self.queue=queue
 def __call__(self,base,ref):
  # No extra model call, tools, inference, anonymization or source expansion.
  # This is only a draft; the entire final text MUST pass independent review.
  with self.queue.lock:
   self.queue._active();now=self.queue._now()
   entries=[e for e in self.queue.items.values() if e.ref==ref and now<e.expires_ms]
   if len(entries)!=1:raise ValueError('source_reference_unknown')
   spans=entries[0].spans
  event=self.queue.binding.resolve(ref)
  if any(not 0<=a<b<=len(event.text) for a,b in spans):raise ValueError('source_span_unknown')
  text=base+'\n\n另外，刚才看到一个话题：'+' / '.join(event.text[a:b] for a,b in spans)
  # Canonical rendering happens BEFORE review, never after its signature.
  text=' '.join(text.split())
  if len(text.encode())>2048:raise ValueError('draft_size')
  return text
def configured_runtime(binding,path):
 cfg,authority=load_topics(path)
 rt=TopicRuntime(binding,enabled=cfg['enabled'],mode_authority=authority,draft_builder=lambda *a:None)
 rt.builder=ReviewedQuoteBuilder(rt.queue)
 return rt
