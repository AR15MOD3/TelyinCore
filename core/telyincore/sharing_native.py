"""Native QQ owner-only sharing. Sources follow plugin allowlists; no test window.

No source text is persisted. Separate assessor + exact-output review remain mandatory.

"""

import json,hashlib,time,threading,secrets,os,datetime,sys

from pathlib import Path

from dataclasses import asdict

from .sharing_bridge import contracts,host_bridge

from .sharing_bridge.deepseek_reviewer import DeepSeekReviewer

from .sharing_bridge.qq_ingress import normalize_message,normalize_recall

from .sharing_bridge.sharing import Attempt, Policy

def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def ordinary_owner_turn(events, owner):
    """Only the host's frozen batch can authorize direct speech to its operator."""
    if not owner or events is None:
        return False
    for event in events:
        source, origin = event['source'], event['origin']
        if origin == 'internal' and source in ('heartbeat', 'timer'):
            continue
        if origin == 'external' and source == 'console' and event['sender_key'] == 'console:local-owner':
            continue
        if (origin == 'external' and source == 'qq' and event['sender_key'] == 'qq:' + owner
                and (event.get('meta') or {}).get('conversation') == 'private:' + owner):
            continue
        return False
    return True


class NativeSharing:

 @property
 def owner(self):
  return self.qq.proactive_target if self.managed_owner else self._configured_owner

 def disconnect(self):
  # Recalls may have happened while the transport was offline.
  with self.lock:
   self.sources.clear()
   self.pending.clear()
   self._since=self.clock()//1000+1


 def __init__(self,qq,ctx,config_path,*,clock=None,reviewer_builder=None,config_snapshot=None):

  self.qq=qq;self.ctx=ctx;self.path=Path(config_path)
  raw=self.path.read_bytes() if config_snapshot is None else config_snapshot
  if type(raw) is not bytes:raise TypeError('immutable QQ config bytes required')
  self.config_hash=hashlib.sha256(raw).digest();cfg=json.loads(raw)

  self.managed_owner=callable(ctx.get('owner_qq_route'))
  self.source_lifetime=Policy().max_age_ms if self.managed_owner else 180000
  self._configured_owner=str(cfg.get('sharingOwner',''));self.bot=str(ctx.get('bot_id') or cfg.get('ownerBot') or 'Telyin')

  if reviewer_builder is None:
   if type(ctx.get('bot_id')) is not str or not ctx['bot_id'] or any(not ctx.get(k) for k in ('engine_root','runtime_root')):
    raise ValueError('explicit_host_review_context_required')
   from .sharing_bridge.review_config import root_key
   root_key(ctx['engine_root']);root_key(ctx['runtime_root'])

  if cfg.get('sharingEnabled',True) is not True or (not self.managed_owner and (not self.owner.isascii() or not self.owner.isdigit() or self.owner not in qq.privates or qq.proactive_target!=self.owner)):raise ValueError('fixed_owner_sharing_route_required')

  self.clock=clock or (lambda:time.time_ns()//1000000);self.lock=threading.RLock();self.operation=threading.Lock();self.closed=False;self.sources={};self.pending={};self.outputs={};self.review_ticks=[];self._seen={};self._since=self.clock()//1000;self.last_clock=0

  self.root=Path(ctx.get('runtime_root') or (Path(ctx['root'])/'data'/'bot-runtimes'/self.bot))/'sharing-native';self.root.mkdir(parents=True,exist_ok=True);self.ledger=self.root/'ledger.json';self.status=self.root/'status.json'

  with self._filelock():

   if not self.ledger.exists():self._atomic(self.ledger,{'version':1,'attempts':[]})

   self._history()

  self.reviewer=(reviewer_builder or DeepSeekReviewer)(contracts=contracts,bridge=host_bridge,python=sys.executable,authorize=self._authorize, **({'engine_root':ctx['engine_root'],'policy_root':ctx['runtime_root'],'bot':ctx['bot_id']} if reviewer_builder is None else {}))

  self.counts={'captured':0,'reviewed':0,'queued':0,'augmented':0,'sent':0,'unknown':0};self._publish()

 def _atomic(self,p,data):

  t=p.with_name(p.name+'.'+secrets.token_hex(6)+'.tmp')

  try:

   with t.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())

   os.replace(t,p)

  finally:t.unlink(missing_ok=True)

 def _filelock(self):

  from contextlib import contextmanager

  @contextmanager

  def held():

   p=self.ledger.with_suffix('.lock')

   with p.open('x') as lock:

    try:yield

    finally:lock.close();p.unlink(missing_ok=True)

  return held()

 def _now(self):

  n=self.clock()

  if type(n) is not int or n<self.last_clock:raise ValueError('sharing_clock_rollback')

  self.last_clock=n;return n

 def _active(self):

  self.qq._check_tool_frame()

  if self.closed or self.qq.client is None or self.owner not in self.qq.privates or self.qq.proactive_target!=self.owner or hashlib.sha256(self.path.read_bytes()).digest()!=self.config_hash:raise ValueError('sharing_config_or_route_changed')

 def scopes(self):return frozenset({'group:qq:'+x for x in self.qq.groups}|{'private:qq:'+x for x in self.qq.privates})

 def capture(self,event):

  try:

   owner=self.owner
   self._active();now=self._now()

   if type(event) is not dict or event.get('post_type')!='message' or type(event.get('time')) is not int or event['time']<self._since:return

   e=normalize_message(event,bot=self.bot,expected_self_id=str(event.get('self_id','')),allowed_scopes=self.scopes(),now_ms=now)

   if e.sender=='qq:'+str(event.get('self_id')) or len(e.text.encode())>4096:return

   key=(e.scope,e.message_id)

   with self.lock:

    self._prune(now)

    if key in self._seen:return

    if len(self.sources)>=128:
     oldest=next(iter(self.sources))
     self.sources.pop(oldest)
     self.pending.pop(oldest,None)

    self.sources[key]=(e,str(event['self_id']),owner);self._seen[key]=now;self.counts['captured']+=1

   self._publish()

  except Exception:return

 def recall(self,event):

  try:

   tomb=normalize_recall(event,bot=self.bot,expected_self_id=str(event.get('self_id','')),allowed_scopes=self.scopes());key=tomb[1:]

   with self.lock:self.sources.pop(key,None);self.pending.pop(key,None)

   self._publish()

  except Exception:return

 def _prune(self,now):

  for k,(e,_,_) in tuple(self.sources.items()):

   if not 0<=now-e.observed_ms<self.source_lifetime:self.sources.pop(k,None);self.pending.pop(k,None)

  for k,t in tuple(self._seen.items()):

   if now-t>=86400000:self._seen.pop(k,None)

  if len(self._seen)>4096:raise ValueError('source_dedupe_capacity')

  for k,v in tuple(self.pending.items()):

   if now>=v[2]:self.pending.pop(k,None)

 def _current(self,e):

  self._active()

  with self.lock:

   self._prune(self._now());item=self.sources.get((e.scope,e.message_id))

   return item is not None and item[0]==e and item[2]==self.owner and e.scope in self.scopes()

 def _authorize(self,source,phase):

  anchor=getattr(source,'message_id',getattr(source,'anchor',None))

  with self.lock:item=self.sources.get((getattr(source,'scope',None),anchor))

  return item is not None and source.bot==self.bot and source.sender==item[0].sender and source.text==item[0].text and source.observed_ms==item[0].observed_ms and self._current(item[0])

 def _history(self):

  if self.ledger.is_symlink() or self.ledger.stat().st_size>1024*1024:raise ValueError('sharing_ledger_invalid')

  d=json.loads(self.ledger.read_bytes())

  if set(d)!={'version','attempts'} or d['version']!=1 or type(d['attempts']) is not list or len(d['attempts'])>512:raise ValueError('sharing_ledger_invalid')

  result=[]

  for v in d['attempts']:

   fields={'source','content','at_ms','status','token'}
   if not isinstance(v,dict) or set(v) not in (fields,fields|{'receipt_hash'}):raise ValueError('sharing_ledger_invalid')
   if 'receipt_hash' in v and (v.get('status')!='accepted' or type(v['receipt_hash']) is not str or len(v['receipt_hash'])!=64):raise ValueError('sharing_receipt_invalid')
   if v['status'] not in ('pending','accepted','unknown','failed') or type(v['at_ms']) is not int or any(type(v[k]) is not str or len(v[k])!=64 for k in ('source','content','token')):raise ValueError('sharing_ledger_invalid')

   result.append(v)

  return result

 def _budget(self,e,text,history):

  now=self._now();fp=digest([e.bot,e.scope,e.message_id,e.text]);th=digest(text)

  if any(v['at_ms']>now for v in history):raise ValueError('future_ledger')

  recent=[v for v in history if now-v['at_ms']<86400000]

  if any(v['source']==fp or v['content']==th for v in recent):raise ValueError('duplicate_share')

  if len(recent)>=3 or any(now-v['at_ms']<1800000 for v in recent):raise ValueError('sharing_cooldown_or_budget')

 def _event(self,source_scope,source_target,message_id):

  if source_scope not in ('group','private') or type(source_target) is not str or not source_target.isascii() or not source_target.isdigit() or type(message_id) is not str:raise ValueError('source_fields_invalid')

  key=(source_scope+':qq:'+source_target,message_id)

  with self.lock:item=self.sources.get(key)

  if item is None or not self._current(item[0]):raise ValueError('source_unavailable_or_recalled')

  return key,item[0]

 def _judge(self,e,text,desire,history,mode="paraphrase"):

  if type(text) is not str or not text.strip() or len(text.encode())>2048 or type(desire) is not int or not 0<=desire<=4:raise ValueError('sharing_proposal_invalid')

  now_ms=self._now();self.review_ticks=[t for t in self.review_ticks if now_ms-t<3600000]

  if len(self.review_ticks)>=30:raise ValueError('hourly_review_budget')

  self.review_ticks.append(now_ms)

  if mode not in ('paraphrase','feelings'):raise ValueError('invalid_expression_mode')

  p=contracts.Proposal(((0,len(e.text)),),mode,text,desire)

  now=datetime.datetime.fromtimestamp(self._now()/1000);quiet=(now.hour==23 and now.minute>=30) or now.hour<8

  # Older ledgers have no verifiable receipt. Keep their dedupe and budget
  # entries, but do not invent an acceptance receipt when reconstructing them.
  h=tuple(Attempt(self.bot,'private:qq:'+self.owner,v['source'],v['token'],v['content'],
                  (),v['at_ms'],'unknown' if v['status']=='accepted' and not v.get('receipt_hash')
                  else v['status'],v.get('receipt_hash','')) for v in history)

  bridge=host_bridge.HostBridge(bot=self.bot,destination='private:qq:'+self.owner,allowed_scopes=self.scopes(),clock=self._now,assess=self.reviewer.assess,review=self.reviewer.review)

  try:
   with self.reviewer.review_session():
    r=bridge.preview(e,p,ready=True,busy=False,quiet=quiet,history_verified=True,attempts=h)

  finally:bridge.close()

  self.counts['reviewed']+=1

  if not self._current(e):raise ValueError('source_changed_during_review')

  return r

 def preview(self,source_scope,source_target,message_id,text,desire=2,mode="paraphrase"):return self._operation(source_scope,source_target,message_id,text,desire,False,mode)

 def share(self, source_scope="", source_target="", message_id="", text="", desire=4,
           mode="paraphrase", remembered_text=""):
  if remembered_text:
   # An optional anchor from the Bot's own memory. Never expose search results
   # or let a remote conversation query other rooms.
   from .batch_snapshot import freeze_batch
   batch=freeze_batch(self.qq._active_tool_events())
   events=json.loads(batch) if batch is not None else None
   owner=self.owner
   if (not self.managed_owner or not ordinary_owner_turn(events,owner)
       or any(row['source']=='qq' for row in events)
       or source_scope or source_target or message_id or not isinstance(remembered_text,str)
       or not 4 <= len(remembered_text.strip()) <= 160):
    return json.dumps({'action':'defer','reason':'unverified_memory_source'})
   with self.lock:
    self._prune(self._now())
    matches=[event for event,_,recipient in self.sources.values()
             if recipient==owner and remembered_text.strip() in event.text]
   if len(matches)!=1:
    return json.dumps({'action':'defer','reason':'unverified_memory_source'})
   source_scope,_,source_target=matches[0].scope.split(':',2)
   message_id=matches[0].message_id
  return self._operation(source_scope,source_target,message_id,text,desire,True,mode)

 def proactive_send(self,text,*,active_events=None):
  """Legacy send_private must use the same independent review, never raw wire.

  The legacy signature has no source reference. Only one authenticated, current
  QQ input can provide an unambiguous binding. Autonomous/mixed turns must use
  qq_share_to_owner with explicit cached source identifiers instead.
  """
  def field(event,name,default=None):
   return event.get(name,default) if isinstance(event,dict) else getattr(event,name,default)
  active=tuple(active_events if active_events is not None
               else self.ctx.get('active_events',lambda:())() or ())
  if len(active)!=1 or field(active[0],'origin')!='external' or field(active[0],'source')!='qq':
   raise ValueError('主动分享须用 qq_share_to_owner 指定可信来源；无来源不能绕过独立审核')
  item=active[0];meta=field(item,'meta',{})
  conversation=meta.get('conversation','') if isinstance(meta,dict) else ''
  scope,sep,target=conversation.partition(':')
  if not sep or not self.qq.event_is_current(item):
   raise ValueError('主动分享来源已撤回或无法绑定')
  result=json.loads(self.share(scope,target,str(meta.get('message_id','')),text))
  if result.get('action')!='sent':
   raise ValueError('主动分享未通过审核或暂缓；不得改走其他发送入口或自动重试')
  return result['receipt']

 def _operation(self,scope,target,mid,text,desire,send,mode):

  if not self.operation.acquire(False):return json.dumps({'action':'defer','reason':'review_busy'})

  try:

   key,e=self._event(scope,target,mid)

   with self._filelock():

    history=self._history();self._budget(e,text,history);r=self._judge(e,text,desire,history,mode)

    if not self.managed_owner and r.get('action')=='topic':

     with self.lock:

      if len(self.pending)<8:self.pending[key]=(e,text,min(self._now()+120000,e.observed_ms+180000),mode);self.counts['queued']+=1

    if send and r.get('action')=='allow':

     receipt=self._dispatch(e,text,history);return json.dumps({'action':'sent','protocol_accepted':True,'recipient':'owner','receipt':receipt},ensure_ascii=False)

    return json.dumps({k:r[k] for k in ('action','reason')},ensure_ascii=False)

  except Exception:return json.dumps({'action':'defer','reason':'sharing_failed_closed'},ensure_ascii=False)

  finally:self.operation.release();self._publish()

 def _reserve(self,e,text,history):

  if not self._current(e):raise ValueError('source_revoked')

  self._budget(e,text,history);now=self._now();token=secrets.token_hex(32);v={'source':digest([e.bot,e.scope,e.message_id,e.text]),'content':digest(text),'at_ms':now,'status':'pending','token':token};history=[v for v in history if now-v['at_ms']<86400000];history.append(v);self._atomic(self.ledger,{'version':1,'attempts':history});return token

 def _settle(self,token,status,receipt_hash=None):

  history=self._history();found=False

  for v in history:

   if v['token']==token:
    v['status']=status
    v.pop('receipt_hash',None)
    if receipt_hash:v['receipt_hash']=receipt_hash
    found=True

  if not found:raise ValueError('attempt_lost')

  self._atomic(self.ledger,{'version':1,'attempts':history})

 def _dispatch(self,e,text,history):

  self._active()

  with self.lock:identity=self.sources[(e.scope,e.message_id)][1]

  actual=self.qq._post('get_login_info',{})

  if str(actual.get('data',{}).get('user_id'))!=identity:raise ValueError('bot_identity_changed')

  token=self._reserve(e,text,history)

  try:

   if not self._current(e):self._settle(token,'failed');raise ValueError('source_revoked_before_send')

   owner=self.owner
   result=self.qq._post('send_private_msg',{'user_id':int(owner),'message':[{'type':'text','data':{'text':text}}]})
   receipt=self.qq._receipt(result,'private',owner)
   self._settle(token,'accepted',digest([identity,owner,result['data']['message_id']]))
   self.counts['sent']+=1
   return receipt

  except BaseException:

   self._settle(token,'unknown');self.counts['unknown']+=1;raise

 def augment(self,base,*,active_events=None):

  if self.managed_owner:return base,None

  if not self.operation.acquire(False):return base,None

  try:

   self._active();active=tuple(active_events if active_events is not None
                             else self.ctx.get('active_events',lambda:())())

   def meta(e):return e.get('meta',{}) if type(e) is dict else getattr(e,'meta',{})

   if not active or any(meta(e).get('conversation')!='private:'+self.owner or not self.qq.event_is_current(e) for e in active):return base,None

   with self.lock:self._prune(self._now());item=next(iter(self.pending.items()),None)

   if item is None:return base,None

   key,(e,topic,expires,mode)=item

   with self.lock:self.pending.pop(key,None)

   text=' '.join((base+'\n\n另外，刚才想到一个话题：'+topic).split())

   with self._filelock():

    history=self._history();self._budget(e,text,history);r=self._judge(e,text,4,history,mode)

    if r.get('action') not in ('allow','topic'):return base,None

    token=self._reserve(e,text,history)

    with self.lock:self.outputs[token]=(e,base,text,min(self._now()+30000,e.observed_ms+180000),False)

    return text,token

  except Exception:return base,None

  finally:self.operation.release();self._publish()

 def take_augmentation(self,token):

  with self.lock:

   value=self.outputs.get(token)

   if value is None:raise ValueError('unknown_output')

   e,base,text,expiry,used=value

   if used:raise ValueError('output_already_taken')

   try:good=self._now()<expiry and self._current(e)

   except Exception:good=False

   self.outputs[token]=(e,base,text,expiry,True)

   return (text if good else base),good

 def finish_augmentation(self,token,accepted,augmented=True):

  if token is None:return

  try:

   with self._filelock():self._settle(token,'accepted' if accepted and augmented else ('failed' if accepted else 'unknown'))

   if accepted and augmented:self.counts['augmented']+=1

  finally:

   with self.lock:self.outputs.pop(token,None)

   self._publish()

 def _publish(self):

  try:self._atomic(self.status,{'enabled':not self.closed,'owner_locked':True,'sources_follow_qq_whitelist':True,'source_count':len(self.sources),'topic_count':len(self.pending),'counts':dict(self.counts),'closed':self.closed})

  except Exception:pass

 def close(self,timeout=None):

  import math
  allowance=30.0 if timeout is None else timeout
  if type(allowance) not in (int,float) or not math.isfinite(allowance) or not 0<=allowance<=30:
   raise ValueError('sharing_close_budget_must_be_within_30_seconds')
  now=time.monotonic()
  self._close_deadline=min(getattr(self,'_close_deadline',now+allowance),now+allowance)
  self.closed=True

  self.reviewer.close()
  if not self.operation.acquire(timeout=max(0,self._close_deadline-time.monotonic())):
   self._publish()
   raise RuntimeError('sharing_review_exit_unconfirmed')

  self.operation.release()

  if self.reviewer.shutdown_complete() is not True:raise RuntimeError('sharing_reviewer_exit_unconfirmed')

  with self.lock:self.sources.clear();self.pending.clear();self.outputs.clear();self._seen.clear()

  self._publish()

 def tools(self):

  from .tools import ToolDef,ToolTag

  fields={'source_scope':{'type':'string','enum':['private','group']},'source_target':{'type':'string','description':'来源 QQ 号或群号，只能来自 QQ 插件白名单'},'message_id':{'type':'string','description':'来源消息#编号'},'text':{'type':'string','description':'拟对主人表达的完整文字，必须有来源依据，不得包含未审核的隐私'},'mode':{'type':'string','enum':['paraphrase','feelings'],'description':'普通转述或仅谈不暴露来源事实的自身感受；秘密不能直接转述'},'desire':{'type':'integer','minimum':0,'maximum':4}}

  schema={'type':'object','properties':fields,'required':['source_scope','source_target','message_id','text'],'additionalProperties':False}

  send_schema=schema
  if self.managed_owner:
   send_schema={**schema,'required':['text'],'properties':{**fields,'remembered_text':{
    'type':'string','minLength':4,'maxLength':160,
    'description':'自己想起近期经历时可用记得的原话片段代替三项来源编号。只在自主轮次或本机控制台使用，须唯一匹配，随后照常审核。'}}}

  return [ToolDef('qq_sharing_preview','主人分享审核（不是历史读取工具；私聊被问起群聊时优先使用 qq_read_group_history 按权限回查）：从白名单会话选择值得分享的事，独立评估隐私与分数；只评估这次表达，是否开口由你决定。',schema,(ToolTag.READ,),handler=self.preview),ToolDef('qq_share_to_owner','主动分享（不用于回答历史查询，也不授予额外历史读取权限）：将白名单来源的事，经独立隐私审核、去重和频率保护后只私聊发送给主人；没有可指定的收件人参数。可用来源消息编号，或在支持时用记忆片段核对来源。暂缓就放下。',send_schema,(ToolTag.SPEAK,),handler=self.share)]

