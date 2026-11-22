"""Offline binding/receipt reference candidate; no production ownership authority.

Ledger survives receiver reconstruction only while the separately trusted owner
retains this exact object. Losing owner state is NOT a restore grant. Native
birth/handle attestation and actual request fencing are deliberately not wired.
"""
from threading import RLock
from secrets import token_hex
from .codec import Binding,ProtocolError,decode,encode,digest,command_digest,fail
class BindingLedger:
 def __init__(self):self._lock=RLock();self._rows={};self._slots=set();self._closed=False;self._epoch=token_hex(16)
 @property
 def owner_epoch(self):return self._epoch
 def register(self,binding):
  with self._lock:
   if type(binding) is not Binding or binding.owner_epoch!=self._epoch or self._closed or binding in self._rows or len(self._rows)>=32:fail()
   slot=(binding.bot_scope,binding.role)
   if slot in self._slots:fail()
   self._slots.add(slot);self._rows[binding]=False
 def consume(self,binding):
  with self._lock:
   if self._closed or binding not in self._rows or self._rows[binding]:fail()
   self._rows[binding]=True
 def close(self):
  with self._lock:self._closed=True # retain tombstones; no reset/restore API
 def valid(self,binding):
  with self._lock:return not self._closed and self._rows.get(binding) is True
 def __repr__(self):return '<V2BindingLedger redacted>'
class Receiver:
 def __init__(self,binding,ledger,owner_public,receipt_private,*,clock):
  if type(binding) is not Binding or type(ledger) is not BindingLedger:fail()
  self._binding=binding;self._ledger=ledger;self._owner=owner_public;self._signer=receipt_private;self._clock=clock;self._lock=RLock();self._value=None;self._cache=None;self._queried=False;self._closed=False
 def bootstrap(self,frame):
  with self._lock:
   if self._closed or self._value is not None:fail()
   r=decode(frame,self._owner,self._binding,self._clock(),'bootstrap');self._ledger.consume(self._binding);self._value=r
 def apply(self,frame):
  with self._lock:
   if self._closed or self._value is None or not self._ledger.valid(self._binding) or self._value.get('operation')=='revoke':fail()
   r=decode(frame,self._owner,self._binding,self._clock(),'update')
   if r['prev']!=self._value['revision'] or r['issued']<self._value['issued'] or (r['operation']=='rotate' and r['token']==self._value['token']):fail()
   if self._cache and r['transaction']==self._cache['transaction']:fail()
   self._value=r;self._cache={'transaction':r['transaction'],'revision':r['revision'],'update_digest':digest(r),'command_digest':command_digest(r),'expires':r['expires']};self._queried=False
   # Return value intentionally contains no ACK or implicit usability claim.
 def receipt(self,query):
  with self._lock:
   if self._closed or self._value is None or not self._ledger.valid(self._binding) or self._queried:fail()
   now=self._clock();r=decode(query,self._owner,self._binding,now,'receipt_query')
   if r['revision']!=self._value['revision']:fail()
   self._queried=True
   cache=self._cache;status='APPLIED' if cache and now<cache['expires'] and all(r[k]==cache[k] for k in ('transaction','revision','update_digest','command_digest')) else 'UNKNOWN'
   reply={**r,'kind':'receipt','status':status,'issued':now,'expires':min(r['expires'],now+30000)}
   return encode(reply,self._signer)
 def close(self):
  with self._lock:self._closed=True;self._value=None;self._cache=None;self._signer=None
 def __repr__(self):return '<V2Receiver redacted>'
class ReceiptCheck:
 """One explicit reply attempt, no automatic retry. APPLIED != usable/terminal."""
 def __init__(self,binding,recipient_public,query_record,*,clock):
  # Clone and validate even caller-owned records. No caller-dictionary aliasing.
  from .codec import canonical,validate,binding_from
  import json
  q=json.loads(canonical(query_record));validate(q)
  if q['kind']!='receipt_query' or binding_from(q['binding'])!=binding:fail()
  self._binding=binding;self._key=recipient_public;self._q=q;self._clock=clock;self._used=False;self._lock=RLock();self._state='UNKNOWN'
 @property
 def state(self):return self._state
 def accept(self,frame):
  with self._lock:
   if self._used:fail()
   self._used=True
   r=decode(frame,self._key,self._binding,self._clock(),'receipt')
   if any(r[k]!=self._q[k] for k in ('transaction','revision','update_digest','command_digest','query_nonce')) or r['expires']>self._q['expires'] or r['issued']<self._q['issued']:fail()
   self._state='APPLIED' if r['status']=='APPLIED' else 'UNKNOWN';return self.state
 def __repr__(self):return '<V2ReceiptCheck redacted>'
def dual_status(host,bot):
 if type(host) is not ReceiptCheck or type(bot) is not ReceiptCheck or host._binding.role!='host' or bot._binding.role!='bot':fail()
 shared=('owner_epoch','instance_epoch','boot','gateway','instance','bot_scope')
 if any(getattr(host._binding,k)!=getattr(bot._binding,k) for k in shared) or any(host._q[k]!=bot._q[k] for k in ('transaction','revision','command_digest')):fail()
 return 'CONFIRMED_APPLIED_NOT_ACTIVATED' if host.state==bot.state=='APPLIED' else 'UNKNOWN'
