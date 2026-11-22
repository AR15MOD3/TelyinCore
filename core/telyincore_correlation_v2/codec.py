"""Isolated v2 wire codec. No transport, owner admission, file/key discovery or v1 fallback.

Public keys and Binding must originate from a separately qualified owner channel.
Constructing these Python values is NOT native ownership/authentication evidence.
"""
import base64,hashlib,json,re
from dataclasses import dataclass,asdict
from urllib.parse import urlsplit
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey,Ed25519PublicKey
MAX_FRAME=16384
class ProtocolError(ValueError):pass
def fail():raise ProtocolError('v2_protocol_rejected')
def integer(x,low=0,high=9007199254740991):return type(x) is int and low<=x<=high
def pattern(x,p):return type(x) is str and re.fullmatch(p,x) is not None
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
def digest(record):return hashlib.sha256(canonical(record)).hexdigest()
def command_digest(record):
 # Both recipients must confirm the same command, not merely tx/revision.
 shared=('owner_epoch','instance_epoch','boot','gateway','instance','bot_scope')
 x={k:v for k,v in record.items() if k!='binding'};x['binding']={k:record['binding'][k] for k in shared}
 return digest(x)
@dataclass(frozen=True)
class Binding:
 owner_epoch:str
 instance_epoch:str
 boot:str
 gateway:str
 instance:str
 bot_scope:str
 role:str
 incarnation:str
 channel_nonce:str
 def __post_init__(self):
  for k in ('owner_epoch','instance_epoch','boot','incarnation','channel_nonce'):
   if not pattern(getattr(self,k),r'[0-9a-f]{32}'):fail()
  if not pattern(self.bot_scope,r'[0-9a-f]{64}') or self.role not in ('host','bot') or not pattern(self.instance,r'[A-Za-z0-9_-]{1,80}'):fail()
  try:
   u=urlsplit(self.gateway)
   if u.scheme!='http' or u.hostname!='127.0.0.1' or not u.port or u.port==80 or u.path!='/v1' or u.query or u.fragment or u.username or u.password or self.gateway!=f'http://127.0.0.1:{u.port}/v1':fail()
  except Exception:fail()
 def wire(self):return asdict(self)
 def __repr__(self):return '<V2Binding scoped>'
def binding_from(value):
 if type(value) is not dict or set(value)!=set(Binding.__dataclass_fields__):fail()
 return Binding(**value)
COMMON={'kind','binding','issued','expires'}
EXTRA={'bootstrap':{'revision','token'},'update':{'transaction','prev','revision','operation','token'},'receipt_query':{'transaction','revision','update_digest','command_digest','query_nonce'},'receipt':{'transaction','revision','update_digest','command_digest','query_nonce','status'}}
def validate(record):
 if type(record) is not dict or record.get('kind') not in EXTRA or set(record)!=COMMON|EXTRA[record['kind']]:fail()
 binding_from(record['binding'])
 if not integer(record['issued']) or not integer(record['expires']) or not 0<record['expires']-record['issued']<=300000:fail()
 if not integer(record['revision'],1,2147483647):fail()
 kind=record['kind']
 if kind in ('bootstrap','update'):
  if kind=='bootstrap' and record['revision']!=1:fail()
  if kind=='update':
   if not integer(record['prev'],1,2147483646) or record['revision']!=record['prev']+1 or record['operation'] not in ('rotate','revoke') or not pattern(record['transaction'],r'[0-9a-f]{32}'):fail()
  if kind=='update' and record['operation']=='revoke':
   if record['token'] is not None:fail()
  elif not pattern(record['token'],r'[A-Za-z0-9_-]{43}'):fail()
 else:
  if not pattern(record['transaction'],r'[0-9a-f]{32}') or not pattern(record['update_digest'],r'[0-9a-f]{64}') or not pattern(record['command_digest'],r'[0-9a-f]{64}') or not pattern(record['query_nonce'],r'[0-9a-f]{32}'):fail()
  if kind=='receipt' and record['status'] not in ('APPLIED','UNKNOWN'):fail()
 return record
def message(record):return b'telyincore-correlation-v2\0'+record['kind'].encode('ascii')+b'\0'+canonical(record)
def encode(record,key):
 try:
  if not isinstance(key,Ed25519PrivateKey):fail()
  validate(record);sig=base64.urlsafe_b64encode(key.sign(message(record))).rstrip(b'=').decode('ascii')
  raw=canonical({'version':2,'record':record,'signature':sig})
  if not 0<len(raw)<=MAX_FRAME:fail()
  return len(raw).to_bytes(4,'big')+raw
 except Exception:fail()
def unique(pairs):
 result={}
 for k,v in pairs:
  if k in result:fail()
  result[k]=v
 return result
def decode(frame,key,expected,now,kind):
 try:
  if type(frame) is not bytes or not 4<len(frame)<=MAX_FRAME+4 or int.from_bytes(frame[:4],'big')!=len(frame)-4 or not isinstance(key,Ed25519PublicKey) or type(expected) is not Binding or kind not in EXTRA or not integer(now):fail()
  wire=json.loads(frame[4:].decode('ascii'),object_pairs_hook=unique,parse_constant=lambda _:fail())
  if type(wire) is not dict or set(wire)!={'version','record','signature'} or type(wire['version']) is not int or wire['version']!=2 or canonical(wire)!=frame[4:]:fail()
  r=validate(wire['record'])
  if r['kind']!=kind or binding_from(r['binding'])!=expected or not r['issued']-30000<=now<r['expires'] or not pattern(wire['signature'],r'[A-Za-z0-9_-]{86}'):fail()
  sig=base64.urlsafe_b64decode(wire['signature']+'==')
  if base64.urlsafe_b64encode(sig).rstrip(b'=').decode()!=wire['signature']:fail()
  key.verify(sig,message(r));return r
 except Exception:fail()
