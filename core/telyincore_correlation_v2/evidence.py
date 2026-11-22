"""Endpoint/cancellation evidence contract; no native issuer or release grant.

Even a valid observer signature is only protocol evidence until the actual
observer/owner and original native operation are qualified. Pins never release
through task completion, a close await, metadata or these signed observations.
"""
import base64,json
from asyncio import CancelledError
from .codec import Binding,binding_from,canonical,integer,pattern,Ed25519PrivateKey,Ed25519PublicKey
from .work_bridge import ToolWorkIncomplete
KINDS=('receiver_ready','sender_copy_closed','io_terminal')
FIELDS={'kind','binding','endpoint_digest','operation_nonce','buffer_set_digest','receipt_nonce','issued','expires','result'}
ZERO32='0'*32;ZERO64='0'*64
def fail():raise ValueError('v2_owned_evidence_rejected') from None
def validate(r):
 if type(r) is not dict or set(r)!=FIELDS or r['kind'] not in KINDS:fail()
 binding_from(r['binding'])
 if not pattern(r['endpoint_digest'],r'[0-9a-f]{64}') or not pattern(r['operation_nonce'],r'[0-9a-f]{32}') or not pattern(r['buffer_set_digest'],r'[0-9a-f]{64}') or not pattern(r['receipt_nonce'],r'[0-9a-f]{32}') or r['receipt_nonce']==ZERO32:fail()
 if not integer(r['issued']) or not integer(r['expires']) or not 0<r['expires']-r['issued']<=30000:fail()
 if r['kind']=='io_terminal':
  if r['operation_nonce']==ZERO32 or r['buffer_set_digest']==ZERO64 or r['result'] not in ('COMPLETED','ABORTED','UNKNOWN'):fail()
 elif r['operation_nonce']!=ZERO32 or r['buffer_set_digest']!=ZERO64 or r['result'] not in ('COMPLETE','UNKNOWN'):fail()
 return r
def message(r):return b'telyincore-owned-io-observation-v1\0'+r['kind'].encode()+b'\0'+canonical(r)
def encode_evidence(record,key):
 try:
  if not isinstance(key,Ed25519PrivateKey):fail()
  validate(record);sig=base64.urlsafe_b64encode(key.sign(message(record))).rstrip(b'=').decode('ascii');raw=canonical({'version':1,'evidence':record,'signature':sig})
  if not 0<len(raw)<=4092:fail()
  return len(raw).to_bytes(4,'big')+raw
 except Exception:fail()
def unique(pairs):
 out={}
 for k,v in pairs:
  if k in out:fail()
  out[k]=v
 return out
def decode_evidence(frame,key,expected,now,kind):
 try:
  if type(frame) is not bytes or not 4<len(frame)<=4096 or int.from_bytes(frame[:4],'big')!=len(frame)-4 or not isinstance(key,Ed25519PublicKey) or type(expected) is not Binding or not integer(now) or kind not in KINDS:fail()
  x=json.loads(frame[4:].decode('ascii'),object_pairs_hook=unique,parse_constant=lambda _:fail())
  if type(x) is not dict or set(x)!={'version','evidence','signature'} or type(x['version']) is not int or x['version']!=1 or canonical(x)!=frame[4:]:fail()
  r=validate(x['evidence'])
  if r['kind']!=kind or binding_from(r['binding'])!=expected or not r['issued']-1000<=now<r['expires'] or not pattern(x['signature'],r'[A-Za-z0-9_-]{86}'):fail()
  sig=base64.urlsafe_b64decode(x['signature']+'==')
  if base64.urlsafe_b64encode(sig).rstrip(b'=').decode()!=x['signature']:fail()
  key.verify(sig,message(r));return r
 except Exception:fail()
class _Ticket:
 __slots__=()
 def __repr__(self):return '<OwnedEvidenceTicket redacted>'
def ref(value):
 return value is not None and not isinstance(value,(bool,int,float,str,bytes,bytearray,dict,list,tuple))
class RetainedEvidenceLedger:
 """Bounded original-reference ledger, not native identity or OS sandbox.

 No buffer/endpoint release API exists while actual observer qualification is
 absent. Caller must retain this ledger on incomplete; no resets/eviction.
 """
 label='correlation_owned_evidence'
 def __init__(self,expected,observer_public,*,clock):
  if type(expected) is not Binding or not isinstance(observer_public,Ed25519PublicKey):fail()
  self._binding=expected;self._key=observer_public;self._clock=clock;self._endpoints={};self._operations={};self._nonces=set();self._closed=False
 def register_endpoint(self,owner_ref,sender_ref,receiver_ref,endpoint_digest):
  if self._closed or len(self._endpoints)>=32 or not all(ref(x) for x in (owner_ref,sender_ref,receiver_ref)) or sender_ref is receiver_ref or owner_ref is sender_ref or owner_ref is receiver_ref or not pattern(endpoint_digest,r'[0-9a-f]{64}'):fail()
  if any(v['digest']==endpoint_digest or any(x is y for x in (sender_ref,receiver_ref) for y in (v['sender'],v['receiver'])) for v in self._endpoints.values()):fail()
  t=_Ticket();self._endpoints[t]={'owner':owner_ref,'sender':sender_ref,'receiver':receiver_ref,'digest':endpoint_digest,'ready':None,'copy_closed':None,'attempts':set(),'local_close':set()};return t
 def _endpoint(self,t):
  if type(t) is not _Ticket or t not in self._endpoints:fail()
  return self._endpoints[t]
 def _evidence(self,frame,kind,digest):
  r=decode_evidence(frame,self._key,self._binding,self._clock(),kind)
  if r['endpoint_digest']!=digest or r['receipt_nonce'] in self._nonces or len(self._nonces)>=256:fail()
  self._nonces.add(r['receipt_nonce']);return r
 def observe_handoff(self,t,original_ref,kind,frame):
  e=self._endpoint(t)
  if kind not in ('receiver_ready','sender_copy_closed') or original_ref is not e['receiver' if kind=='receiver_ready' else 'sender'] or kind in e['attempts'] or (kind=='sender_copy_closed' and (not e['ready'] or e['ready']['result']!='COMPLETE')):fail()
  e['attempts'].add(kind);r=self._evidence(frame,kind,e['digest']);e['ready' if kind=='receiver_ready' else 'copy_closed']=r
  return {'signed_observation':r['result'],'ownership_transfer':'UNKNOWN','sender_release_allowed':False}
 def register_operation(self,t,original_operation,buffers,operation_nonce,buffer_set_digest):
  self._endpoint(t)
  if self._closed or len(self._operations)>=128 or not ref(original_operation) or type(buffers) is not tuple or not 1<=len(buffers)<=4 or any(not isinstance(x,(bytes,bytearray,memoryview)) and not ref(x) for x in buffers) or not pattern(operation_nonce,r'[0-9a-f]{32}') or operation_nonce==ZERO32 or not pattern(buffer_set_digest,r'[0-9a-f]{64}') or buffer_set_digest==ZERO64:fail()
  if any(v['original'] is original_operation or v['nonce']==operation_nonce for v in self._operations.values()):fail()
  ticket=_Ticket();self._operations[ticket]={'endpoint':t,'original':original_operation,'buffers':buffers,'nonce':operation_nonce,'buffer_digest':buffer_set_digest,'cancel_requested':False,'task_state':None,'attempted':False,'signed_terminal':None};return ticket
 def _operation(self,t,original):
  if type(t) is not _Ticket or t not in self._operations or self._operations[t]['original'] is not original:fail()
  return self._operations[t]
 def request_cancel(self,t,original):
  self._operation(t,original)['cancel_requested']=True
  return {'cancel_request_recorded':True,'native_completion':'UNKNOWN'}
 def mark_task_settled(self,t,original,state):
  if type(state) is not str or state not in ('DONE','CANCELLED','FAILED'):fail()
  self._operation(t,original)['task_state']=state
 def mark_local_wait_closed(self,t,original_ref):
  e=self._endpoint(t)
  if original_ref is e['sender']:e['local_close'].add('sender')
  elif original_ref is e['receiver']:e['local_close'].add('receiver')
  else:fail()
 def observe_terminal(self,t,original,frame):
  op=self._operation(t,original)
  if op['attempted']:fail()
  op['attempted']=True;e=self._endpoint(op['endpoint']);r=self._evidence(frame,'io_terminal',e['digest'])
  if r['operation_nonce']!=op['nonce'] or r['buffer_set_digest']!=op['buffer_digest']:fail()
  op['signed_terminal']=r
  return {'signed_observation':r['result'],'native_completion':'UNKNOWN','buffer_release_allowed':False}
 def seal(self):self._closed=True
 def snapshot(self):
  n=len(self._endpoints);m=len(self._operations)
  return {'component':self.label,'closed':self._closed,'retained_endpoint_records':n,'retained_operation_records':m,'retained_buffer_refs':sum(len(v['buffers']) for v in self._operations.values()),'cancel_requests_recorded':sum(v['cancel_requested'] for v in self._operations.values()),'task_settlements_recorded':sum(v['task_state'] is not None for v in self._operations.values()),'signed_ready_observations':sum(v['ready'] is not None for v in self._endpoints.values()),'signed_copy_close_observations':sum(v['copy_closed'] is not None for v in self._endpoints.values()),'local_wait_closed_observations':sum(len(v['local_close']) for v in self._endpoints.values()),'signed_terminal_observations':sum(v['signed_terminal'] is not None for v in self._operations.values()),'owned':{'pending':n+m,'failed':0,'done':0,'cancelled':0},'native_completion':'UNKNOWN','ownership_transfer':'UNKNOWN','actual_observer_qualification':'UNKNOWN','can_bind':False,'release_allowed':False,'complete':False}
 def finish_sync(self,*,primary=None):
  self.seal()
  if self._endpoints or self._operations:
   e=ToolWorkIncomplete(self)
   if primary is None or isinstance(primary,CancelledError):raise e from primary
   primary.add_note(str(e))
 def __repr__(self):return '<RetainedOwnedEvidenceLedger scoped redacted>'
