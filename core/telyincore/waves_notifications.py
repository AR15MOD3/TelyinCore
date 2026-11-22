"""One owned login-result notice, bound to the original accepted QQ event.

Never fabricate a new inbound message, replay an account command or retry an
uncertain send. Persist the claim BEFORE model or QQ effects. A crash may lose
a notice; it must not silently resend one whose delivery is uncertain.
"""
import asyncio
import hashlib
import json
import os
import uuid
import time
from pathlib import Path


FACTS={
 'success':'鸣潮网页登录成功，登录信息已保存到本次发起者账号。尚不代表所有游戏查询已验证。',
 'failure':'本次鸣潮登录明确失败，未确认新的登录保存成功；不要声称原有登录已失效。',
 'timeout':'本次鸣潮登录等待超时，结果尚未确认；不要重复提交验证码或自动重新登录。',
 'unknown':'本次鸣潮登录结果尚未确认；不要宣称成功或失败，不要自动重新登录。',
}


REASON_FACTS={
 'public_service_unavailable':'外置登录服务暂时不可用，本次未确认新的登录保存成功；不要重复提交验证码。',
 'public_session_expired':'外置网页登录会话提前失效，本次未确认新的登录保存成功；不要重复提交验证码，不自动重新登录。',
 'public_page_unconfirmed':'外置登录页面状态无法确认，本次没有发出可能失效的入口；请检查服务。',
 'public_result_timeout':'网页登录等待已结束，新的登录保存尚未确认；不要自动重新登录或重复提交验证码。',
}


class NoticeLedger:
 def __init__(self,root):
  self.root=Path(root)
  self.path=self.root/'notices.json'
 def _read(self):
  if not self.path.exists():return {}
  value=json.loads(self.path.read_text(encoding='utf8'))
  if type(value) is not dict or len(value)>4096:raise ValueError('notice ledger invalid')
  return value
 def _write(self,value):
  from bot_private_file import open_private_new
  self.root.mkdir(parents=True,exist_ok=True)
  temp=self.root/('.notice-'+uuid.uuid4().hex+'.tmp')
  with open_private_new(temp) as f:
   f.write((json.dumps(value,sort_keys=True)+'\n').encode());f.flush();os.fsync(f.fileno())
  os.replace(temp,self.path)
 def reserve(self,key,binding):
  from runtime_identity import identity_lock
  self.root.mkdir(parents=True,exist_ok=True)
  with identity_lock(self.root):
   rows=self._read()
   if key in rows:return False
   if len(rows)>=4096:raise ValueError('notice ledger requires archival')
   rows[key]={'state':'armed','binding':binding};self._write(rows);return True
 def advance(self,key,expected,state,**facts):
  from runtime_identity import identity_lock
  with identity_lock(self.root):
   rows=self._read()
   if rows[key]['state']!=expected:raise ValueError('notice ledger state changed')
   rows[key].update(state=state,**facts);self._write(rows)


def original_qq_batch(events):
 """Only transport-accepted metadata, without message body or credentials."""
 events=tuple(events)
 outside=[e for e in events if getattr(e,'origin',None)=='external']
 if len(outside)!=1:raise ValueError('one actual QQ initiator required')
 event=outside[0];meta=getattr(event,'meta',None) or {};sender=getattr(event,'sender_key','')
 if getattr(event,'source',None)!='qq' or not sender.startswith('qq:') or not sender[3:].isdigit():
  raise ValueError('actual QQ sender required')
 room=meta.get('conversation');mid=meta.get('message_id')
 if not mid or not isinstance(room,str):raise ValueError('QQ message identity required')
 if room.startswith('private:'):
  if room!='private:'+sender[3:]:raise ValueError('private caller mismatch')
 elif room.startswith('group:'):
  if not room[6:].isdigit():raise ValueError('group identity invalid')
 else:raise ValueError('QQ room required')
 # This is the already accepted event's outbound routing snapshot, never
 # appended as a fresh external event and never used to re-run a user command.
 return ({'origin':'external','source':'qq','sender_key':sender,
          'meta':{'conversation':room,'message_id':str(mid),'self_id':meta.get('self_id')}},)


class LoginNotifications:
 def __init__(self,*,bot,ledger,spawn,prepare_delivery,watch,compose,current,wait_seconds=240,poll_seconds=2):
  self.bot=bot;self.ledger=ledger;self.spawn=spawn
  self.prepare_delivery=prepare_delivery;self.watch=watch
  self.compose=compose;self.current=current
  self.pending=0
  self.wait_seconds=wait_seconds;self.poll_seconds=poll_seconds
 def prepare(self,events):
  batch=original_qq_batch(events)
  binding=hashlib.sha256(json.dumps([self.bot,batch],sort_keys=True).encode()).hexdigest()
  # Pin the current QQ adapter NOW, rather than rediscovering a different
  # account/instance after browser login. Prepare performs no outbound I/O.
  deliver=self.prepare_delivery(batch,self.bot)
  def arm(job_id,frozen,identity):
   if not isinstance(job_id,str) or not job_id or len(job_id)>128:raise ValueError('job identity invalid')
   sender=batch[0]['sender_key'][3:];room=batch[0]['meta']['conversation']
   audience='private:qq:'+sender if room.startswith('private:') else 'group:qq:'+room[6:]
   if identity!=(sender,audience,batch[0]['meta']['message_id']):raise ValueError('notice owner mismatch')
   key=hashlib.sha256(json.dumps([self.bot,audience,sender,job_id]).encode()).hexdigest()
   if not self.current():raise RuntimeError('notice admission closed')
   if self.pending>=64:raise RuntimeError('notice capacity reached; query existing task')
   if not self.ledger.reserve(key,binding):return False
   self.pending+=1
   try:
    read=self.watch(job_id,frozen,identity)  # registered owned system call
    self.spawn(lambda:self._run(key,read,deliver))
   except BaseException:
    self.pending-=1;self.ledger.advance(key,'armed','admission_failed');raise
   return True
  return arm
 async def _run(self,key,read,deliver):
  phase='armed'
  try:
   if not self.current():raise asyncio.CancelledError
   deadline=time.monotonic()+self.wait_seconds
   while True:
    if not self.current():raise asyncio.CancelledError
    try:result=await read()
    except asyncio.CancelledError:raise
    except TimeoutError:result={'state':'timeout'}
    except Exception:result={'state':'unknown'}
    if type(result) is not dict or result.get('state')!='pending':break
    if time.monotonic()>=deadline:result={'state':'timeout'};break
    # No plugin system-call lease is held while waiting for browser action.
    await asyncio.sleep(self.poll_seconds)
   state=result.get('state') if type(result) is dict else None
   if state not in FACTS:raise ValueError('unconfirmed semantic result')
   # No token/cookie/phone/code/URL or arbitrary upstream text goes to model.
   reason=result.get('reason')
   if not isinstance(reason,str) or reason not in REASON_FACTS:reason=None
   facts={'state':state,'fact':REASON_FACTS.get(reason,FACTS[state])}
   if not self.current():raise asyncio.CancelledError
   details={'outcome':state}
   if reason is not None:details['reason']=reason
   self.ledger.advance(key,phase,'rendering',**details);phase='rendering'
   fallback=False
   try:text=await self.compose(facts)
   except asyncio.CancelledError:raise
   except Exception:text=FACTS[state];fallback=True
   if not isinstance(text,str) or not text.strip() or len(text)>1000:
    text=FACTS[state];fallback=True
   if not self.current():raise asyncio.CancelledError
   # One attempted send, persisted before I/O. Failure/unknown receipt cannot
   # be retried under another incoming message, Bot restart or duplicate job.
   self.ledger.advance(key,phase,'sending',fallback=fallback);phase='sending'
   await deliver(text)
   self.ledger.advance(key,phase,'sent');phase='sent'
  except asyncio.CancelledError:
   if phase!='sent':self.ledger.advance(key,phase,'delivery_uncertain' if phase=='sending' else 'cancelled')
   raise
  except Exception:
   if phase!='sent':self.ledger.advance(key,phase,'delivery_uncertain' if phase=='sending' else 'failed')
   # The durable ledger retains failure. No model/send or login replay.
  finally:self.pending-=1

