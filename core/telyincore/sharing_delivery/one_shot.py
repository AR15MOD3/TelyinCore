"""Host-only exact-content single-dispatch approval. No implicit approval from previews.
No production grant is created by import/construction. The operator must obtain
explicit approval for the fixed recipient, source body and final output first.
"""
from pathlib import Path
from contextlib import closing
import json,secrets,sqlite3
from .qq_transport import LivePermit,DOMAIN,RECIPIENT,sha,is_hash
from .live_broker import LiveBroker
from .host_shadow import HostShadowBinding,SHADOW_DOMAIN
from telyincore.sharing_bridge.host_bridge import TransportEvent
APPROVAL_DOMAIN='operator-single-qq-dispatch-v1'

def source_fingerprint(event):return sha(json.dumps([event.bot,event.scope,event.message_id],separators=(',',':')))
def valid_event(e):return type(e) is TransportEvent and e.bot=='Telyin' and e.scope=='group:qq:290016343' and e.sender=='qq:1228600196'
class OneShotApproval:
 @staticmethod
 def initialize(path,*,source_body_hash,final_text_hash,approved_ms,expires_ms,approval_ref):
  if not all(is_hash(x) for x in (source_body_hash,final_text_hash,approval_ref)) or type(approved_ms) is not int or type(expires_ms) is not int or not 0<=approved_ms<expires_ms<=approved_ms+600000:raise ValueError('explicit_bounded_exact_approval_required')
  with Path(path).open('xb'):pass
  with closing(sqlite3.connect(path)) as db,db:
   db.execute('CREATE TABLE meta(domain TEXT)');db.execute('INSERT INTO meta VALUES(?)',(APPROVAL_DOMAIN,))
   db.execute('CREATE TABLE approval(id TEXT,source_body TEXT,final_text TEXT,approved_ms INTEGER,expires_ms INTEGER,approval_ref TEXT,state TEXT,reservation TEXT,bound_source TEXT,last_clock INTEGER)')
   db.execute('INSERT INTO approval VALUES(?,?,?,?,?,?,?,?,?,?)',(secrets.token_hex(32),source_body_hash,final_text_hash,approved_ms,expires_ms,approval_ref,'free','','',approved_ms))
 @staticmethod
 def connect(path):
  if not Path(path).is_file():raise ValueError('approval_missing')
  db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=rw',uri=True,timeout=3)
  try:
   if db.execute('PRAGMA quick_check').fetchone()!=('ok',) or db.execute('SELECT domain FROM meta').fetchall()!=[(APPROVAL_DOMAIN,)]:raise ValueError('approval_domain_invalid')
   OneShotApproval.row(db);return db
  except BaseException:db.close();raise
 @staticmethod
 def row(db):
  rows=db.execute('SELECT * FROM approval').fetchall()
  if len(rows)!=1 or len(rows[0])!=10:raise ValueError('approval_state_invalid')
  row=rows[0];ident,src,txt,start,end,ref,state,res,bound,last=row
  if not all(is_hash(x) for x in (ident,src,txt,ref)) or any(type(x) is not int for x in (start,end,last)) or not 0<=start<end<=start+600000 or last<start or state not in ('free','bound','revoked') or not (res=='' or is_hash(res)) or not (bound=='' or is_hash(bound)) or (state=='free' and (res or bound)) or (state=='bound' and (not res or not bound)):raise ValueError('approval_state_invalid')
  return row
 @staticmethod
 def ready(row,event,now,reservation=None):
  _,src,txt,start,end,ref,state,res,bound,last=row
  if type(now) is not int or not start<=now<end or now<last or not valid_event(event) or sha(event.text)!=src or state=='revoked':return False
  return state=='free' or state=='bound' and reservation==res and source_fingerprint(event)==bound
 @staticmethod
 def available(path,event,now,reservation=None):
  with closing(OneShotApproval.connect(path)) as db:
   db.execute('BEGIN IMMEDIATE');row=OneShotApproval.row(db)
   ok=OneShotApproval.ready(row,event,now,reservation)
   if ok:db.execute('UPDATE approval SET last_clock=?',(now,));db.commit()
   return ok
 @staticmethod
 def bind(path,permit,event,text,now):
  if type(permit) is not LivePermit or permit.domain!=DOMAIN or permit.bot!='Telyin' or permit.recipient!=RECIPIENT or not is_hash(permit.reservation_id) or not valid_event(event) or permit.source_fingerprint!=source_fingerprint(event) or type(text) is not str or permit.text_hash!=sha(text):return False
  with closing(OneShotApproval.connect(path)) as db:
   db.execute('BEGIN IMMEDIATE');row=OneShotApproval.row(db)
   if not OneShotApproval.ready(row,event,now,permit.reservation_id) or row[2]!=permit.text_hash:return False
   # Persist BEFORE identity probing/sending; failure consumes the one-shot too.
   db.execute("UPDATE approval SET state='bound',reservation=?,bound_source=?,last_clock=?",(permit.reservation_id,permit.source_fingerprint,now));db.commit();return True
 @staticmethod
 def revoke(path):
  with closing(OneShotApproval.connect(path)) as db,db:db.execute("UPDATE approval SET state='revoked'")
class OneShotDelivery:
 def __init__(self,*,binding,approval,budget,journal,exchange=None,enabled=False):
  if type(binding) is not HostShadowBinding or type(enabled) is not bool or enabled and not callable(exchange):raise ValueError('explicit_host_binding_and_exchange_required')
  self.binding=binding;self.approval=Path(approval);self.enabled=enabled;self.closed=False
  self.broker=LiveBroker(budget,journal,clock=binding.factory.clock,provider=binding.provider,assess=binding.factory._assess,review=binding.factory._review,authorize_source=self._source_authority,exchange=exchange,enabled=enabled)
  original=self.broker.transport.authorize_live
  def authorize(permit,text):
   if original(permit,text) is not True:return False
   event=self.broker.record[1]
   return OneShotApproval.bind(self.approval,permit,event,text,self.broker.clock()) is True
  self.broker.transport.authorize_live=authorize
 def _source_authority(self,event,domain):
  if self.closed or domain!=DOMAIN or self.binding.authorize_source(event,SHADOW_DOMAIN) is not True:return False
  reservation=self.broker.record[0].reservation_id if self.broker.record is not None else None
  return OneShotApproval.available(self.approval,event,self.broker.clock(),reservation) is True
 def dispatch(self,source_ref,proposal):
  from .qq_transport import QQTransport
  if not self.enabled:return QQTransport.reply('disabled')
  if self.closed:return QQTransport.reply('closed')
  try:return self.broker.dispatch(self.binding.resolve(source_ref),proposal)
  except Exception:return QQTransport.reply('failed_closed')
 def close(self):self.closed=True;self.broker.close()
 def shutdown_complete(self):return self.closed and self.broker.shutdown_complete()
