"""Local explicit arming. No model, QQ, configuration toggle, or process control."""
from pathlib import Path
import json,secrets,os,uuid

def atomic_json(path,data):
 path=Path(path);tmp=path.with_name('.arm-'+uuid.uuid4().hex+'.tmp')
 try:
  with tmp.open('x',encoding='utf-8') as f:json.dump(data,f);f.flush();os.fsync(f.fileno())
  os.replace(tmp,path)
 finally:
  if tmp.exists():tmp.unlink()
def read_control(path):
 with Path(path).open('rb') as f:raw=f.read(4097)
 if len(raw)>4096:raise ValueError('arm_control_invalid')
 d=json.loads(raw)
 if type(d) is not dict or set(d)!={'version','nonce','generation','armed_at_ms','expires_at_ms'} or type(d['version']) is not int or d['version']!=1 or type(d['generation']) is not int or not 0<=d['generation']<=3 or type(d['nonce']) is not str or len(d['nonce'])!=32 or any(c not in '0123456789abcdef' for c in d['nonce']):raise ValueError('arm_control_invalid')
 return d
class ArmGate:
 def __init__(self,path,*,clock,monotonic):
  self.path=Path(path);self.clock=clock;self.monotonic=monotonic;self.nonce=secrets.token_hex(16);self.generation=0;self.deadline=None;self.start_ms=None;self.end_ms=None;self.last_wall=None
  data={'version':1,'nonce':self.nonce,'generation':0,'armed_at_ms':None,'expires_at_ms':None}
  with self.path.open('x',encoding='utf-8') as f:json.dump(data,f)
 def poll(self):
  d=read_control(self.path);now=self.clock();mono=self.monotonic()
  if type(now) is not int or self.last_wall is not None and now<self.last_wall:raise ValueError('arm_clock_rollback')
  self.last_wall=now
  if d['nonce']!=self.nonce or d['generation']<self.generation:raise ValueError('arm_control_invalid')
  if d['generation']==0:
   if d['armed_at_ms'] is not None or d['expires_at_ms'] is not None:raise ValueError('arm_control_invalid')
   return 'disarmed'
  start,end=d['armed_at_ms'],d['expires_at_ms']
  if type(start) is not int or type(end) is not int or start<0 or end-start!=180000 or now<start:raise ValueError('arm_control_invalid')
  if d['generation']>self.generation:
   self.generation=d['generation'];self.start_ms=start;self.end_ms=end;self.deadline=mono+max(0,(end-now)/1000)
  elif start!=self.start_ms or end!=self.end_ms:raise ValueError('arm_control_changed_same_generation')
  return 'armed' if now<end and mono<self.deadline else 'expired'
 def admits(self,event):
  return self.poll()=='armed' and type(event) is dict and type(event.get('time')) is int and event['time']>=self.start_ms//1000+1
 def summary(self,state):return {'state':state,'generation':self.generation,'remaining_seconds':max(0,int(self.deadline-self.monotonic())) if state=='armed' else 0}

def arm(path,*,now_ms,expected_generation):
 # An exclusive local lock serializes generation compare/update across CLI calls.
 path=Path(path);lock=path.with_name(path.name+'.lock')
 with lock.open('x') as held:
  try:
   d=read_control(path)
   if type(now_ms) is not int or type(expected_generation) is not int or d['generation']!=expected_generation or d['generation']>=3:raise ValueError('arm_generation_conflict_or_cap')
   new={**d,'generation':d['generation']+1,'armed_at_ms':now_ms,'expires_at_ms':now_ms+180000}
   atomic_json(path,new);return new['generation']
  finally:
   held.close();lock.unlink()
