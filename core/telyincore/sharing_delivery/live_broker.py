"""Isolated fixed test-source broker candidate. Never registered with Core; default off.
Two durable ledgers intentionally fail closed across crashes; no automatic recovery/retry.
"""
from dataclasses import dataclass,asdict
from contextlib import closing
from pathlib import Path
import hashlib,hmac,json,secrets,sqlite3,threading
from .qq_transport import LivePermit,QQTransport,DispatchJournal,DOMAIN,RECIPIENT,sha,is_hash
from telyincore.sharing_bridge.host_bridge import HostBridge,TransportEvent,ReviewDecision
DESTINATION='private:qq:'+RECIPIENT
BUDGET_DOMAIN='real-qq-budget-v1'
@dataclass(frozen=True)
class State:
 revision:int
 authorized:bool
 revoked:bool
 history_verified:bool
 busy:bool=False
 quiet:bool=False
@dataclass(frozen=True)
class Policy:
 max_per_24h:int=3
 cooldown_ms:int=1800000
 quiet_extra_ms:int=1800000
 def digest(self):
  if any(type(v) is not int for v in asdict(self).values()) or not 1<=self.max_per_24h<=32 or not 0<=self.cooldown_ms<=86400000 or not 0<=self.quiet_extra_ms<=86400000:raise ValueError('policy_invalid')
  return sha(json.dumps(asdict(self),sort_keys=True))
class Budget:
 @staticmethod
 def initialize(path,policy=Policy()):
  fingerprint=policy.digest()
  with Path(path).open('xb'):pass
  with closing(sqlite3.connect(path)) as db,db:
   db.execute('CREATE TABLE meta(domain TEXT)');db.execute('INSERT INTO meta VALUES(?)',(BUDGET_DOMAIN,))
   db.execute('CREATE TABLE control(revision INTEGER,paused INTEGER,last_clock INTEGER,policy TEXT)');db.execute('INSERT INTO control VALUES(0,0,0,?)',(fingerprint,))
   db.execute('CREATE TABLE attempts(reservation TEXT PRIMARY KEY,business TEXT UNIQUE,status TEXT,at_ms INTEGER)')
 @staticmethod
 def connect(path):
  if not Path(path).is_file():raise ValueError('budget_missing')
  db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=rw',uri=True,timeout=3)
  try:
   if db.execute('PRAGMA quick_check').fetchone()!=('ok',) or db.execute('SELECT domain FROM meta').fetchall()!=[(BUDGET_DOMAIN,)]:raise ValueError('budget_domain')
   Budget.control(db)
   rows=db.execute('SELECT reservation,business,status,at_ms FROM attempts').fetchall()
   if len(rows)>32 or any(not is_hash(r) or not is_hash(b) or s not in ('reserved','accepted','unknown','rejected','not_sent') or type(t) is not int or t<0 for r,b,s,t in rows):raise ValueError('budget_history_invalid')
   return db
  except BaseException:db.close();raise
 @staticmethod
 def control(db):
  rows=db.execute('SELECT revision,paused,last_clock,policy FROM control').fetchall()
  if len(rows)!=1:raise ValueError('control_invalid')
  r,p,t,h=rows[0]
  if type(r) is not int or r<0 or type(p) is not int or p not in (0,1) or type(t) is not int or t<0 or not is_hash(h):raise ValueError('control_invalid')
  return r,p,t,h
 @staticmethod
 def pause(path,paused,expected_revision):
  if type(paused) is not bool or type(expected_revision) is not int:raise ValueError('control_update_invalid')
  with closing(Budget.connect(path)) as db:
   db.execute('BEGIN IMMEDIATE');r,_,_,_=Budget.control(db)
   if r!=expected_revision:raise ValueError('revision_conflict')
   db.execute('UPDATE control SET revision=?,paused=?',(r+1,int(paused)));db.commit();return r+1
 @staticmethod
 def reserve(path,business,now,quiet,policy):
  if type(now) is not int or now<0 or type(quiet) is not bool:raise ValueError('clock_or_quiet_invalid')
  with closing(Budget.connect(path)) as db:
   db.execute('BEGIN IMMEDIATE');r,p,last,h=Budget.control(db)
   if p or now<last or h!=policy.digest():raise ValueError('budget_control_denied')
   rows=db.execute('SELECT at_ms FROM attempts').fetchall()
   if len(rows)>=32 or any(t>now for (t,) in rows):raise ValueError('budget_capacity_or_clock')
   recent=[t for (t,) in rows if t>now-86400000]
   if len(recent)>=policy.max_per_24h or rows and now-max(t for (t,) in rows)<policy.cooldown_ms+(policy.quiet_extra_ms if quiet else 0):raise ValueError('budget_or_cooldown')
   reservation=secrets.token_hex(32)
   db.execute('INSERT INTO attempts VALUES(?,?,?,?)',(reservation,business,'reserved',now));db.execute('UPDATE control SET last_clock=?',(now,));db.commit();return reservation,r
 @staticmethod
 def valid(path,reservation,revision,now,policy):
  with closing(Budget.connect(path)) as db:
   r,p,last,h=Budget.control(db)
   return r==revision and not p and now>=last and h==policy.digest() and db.execute('SELECT status FROM attempts WHERE reservation=?',(reservation,)).fetchone()==('reserved',)
 @staticmethod
 def settle(path,reservation,status):
  if status not in ('accepted','unknown','rejected','not_sent'):raise ValueError('status_invalid')
  with closing(Budget.connect(path)) as db,db:
   if db.execute("UPDATE attempts SET status=? WHERE reservation=? AND status='reserved'",(status,reservation)).rowcount!=1:raise ValueError('settlement_conflict')
class LiveBroker:
 def __init__(self,budget,journal,*,clock,provider,assess,review,authorize_source=None,exchange=None,enabled=False,policy=Policy()):
  if type(enabled) is not bool or not all(callable(x) for x in (clock,provider,assess,review)):raise ValueError('dependencies_required')
  self.budget=budget;self.clock=clock;self.provider=provider;self.assess=assess;self.review=review;self.authorize_source=authorize_source;self.enabled=enabled;self.policy=policy;policy.digest()
  self.secret=secrets.token_bytes(32);self.record=None;self.closed=False;self.lock=threading.Lock()
  self.transport=QQTransport(journal,clock=clock,authorize_live=self._valid,exchange=exchange,enabled=enabled)
 def state(self,event):
  now=self.clock()
  if type(now) is not int or type(event) is not TransportEvent or event.bot!='Telyin' or event.scope!='group:qq:290016343' or event.sender!='qq:1228600196' or type(event.message_id) is not str or not event.message_id or type(event.observed_ms) is not int or not 0<=now-event.observed_ms<180000 or type(event.text) is not str or not event.text.startswith('[分享测试]') or len(event.text.encode())>1024:raise ValueError('test_source_required')
  if self.closed or not self.enabled or self.authorize_source is None or self.authorize_source(event,DOMAIN) is not True:raise ValueError('real_domain_source_authority_required')
  state=self.provider(event)
  if type(state) is not State or type(state.revision) is not int or state.revision<0 or state.authorized is not True or state.revoked is not False or state.history_verified is not True or type(state.busy) is not bool or type(state.quiet) is not bool or state.busy:raise ValueError('source_unavailable')
  return state
 def sign(self,permit):
  fields=asdict(permit);fields.pop('signature');return hmac.new(self.secret,json.dumps(fields,sort_keys=True,separators=(',',':')).encode(),hashlib.sha256).hexdigest()
 def _valid(self,permit,text):
  try:
   if self.closed or type(permit) is not LivePermit or self.record is None:return False
   original,event,state,revision=self.record
   return permit==original and hmac.compare_digest(permit.signature,self.sign(permit)) and sha(text)==permit.text_hash and self.clock()<permit.expires_ms and self.state(event)==state and Budget.valid(self.budget,permit.reservation_id,revision,self.clock(),self.policy)
  except Exception:return False
 def dispatch(self,event,proposal):
  if not self.enabled:return QQTransport.reply('disabled')
  with self.lock:
   reservation=None;bridge=None;wire_started=False
   try:
    before=self.state(event);captured=[]
    def review(request):
     self.state(event);verdict=self.review(request);captured.append((request,verdict));return verdict
    bridge=HostBridge(bot='Telyin',destination=DESTINATION,allowed_scopes=frozenset({'group:qq:290016343'}),clock=self.clock,assess=self.assess,review=review)
    outcome=bridge.preview(event,proposal,ready=True,busy=before.busy,quiet=before.quiet,history_verified=True,attempts=())
    if outcome.get('action')!='allow' or len(captured)!=1 or self.state(event)!=before:return QQTransport.reply('review_or_source_denied')
    req,verdict=captured[0]
    if type(verdict) is not ReviewDecision or verdict.approved is not True or verdict.supported is not True or verdict.binding!=req.binding or not self.clock()<verdict.expires_ms<=req.expires_ms:return QQTransport.reply('review_denied')
    source=sha(json.dumps([event.bot,event.scope,event.message_id],separators=(',',':')))
    business=sha(json.dumps([event.bot,RECIPIENT,source,sha(req.final_text)],separators=(',',':')))
    reservation,revision=Budget.reserve(self.budget,business,self.clock(),before.quiet,self.policy)
    raw=LivePermit(DOMAIN,'Telyin',RECIPIENT,source,sha(req.final_text),reservation,req.binding,min(verdict.expires_ms,event.observed_ms+180000),'')
    permit=LivePermit(**{**asdict(raw),'signature':self.sign(raw)})
    self.record=(permit,event,before,revision)
    wire_started=True;result=self.transport.send_once(permit,req.final_text)
    status='accepted' if result['accepted_by_protocol'] else 'unknown' if result['send_attempted'] else 'not_sent'
    if result['status']=='protocol_rejected':status='rejected'
    try:Budget.settle(self.budget,reservation,status)
    except Exception:
     # Protocol acceptance must not be hidden: durable reservation blocks retries.
     return {**result,'budget_settlement':'unresolved'}
    return {**result,'budget_settlement':status}
   except Exception:return QQTransport.reply('failed_closed' if not wire_started else 'unknown')
   finally:
    self.record=None
    if bridge is not None:bridge.close()
    # An exception after reservation conservatively leaves it reserved and charged.
 def close(self):self.closed=True;self.transport.close()
 def shutdown_complete(self):return self.closed and not self.lock.locked() and self.record is None and self.transport.shutdown_complete()
