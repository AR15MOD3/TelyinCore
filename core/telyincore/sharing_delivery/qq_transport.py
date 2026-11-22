"""Host-only fixed-route QQ transport candidate. Disabled by default, no model tool or retry loop."""
from pathlib import Path
from dataclasses import dataclass,asdict
from contextlib import closing
from urllib.parse import urlsplit
import hashlib,json,sqlite3,threading,time
DOMAIN='real-qq-dispatch-v1'
RECIPIENT='1228600196'
BOT_SELF='2724955497'
def sha(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()
def is_hash(text):return type(text) is str and len(text)==64 and all(x in '0123456789abcdef' for x in text)
@dataclass(frozen=True)
class LivePermit:
 domain:str
 bot:str
 recipient:str
 source_fingerprint:str
 text_hash:str
 reservation_id:str
 review_binding:str
 expires_ms:int
 signature:str
class DispatchJournal:
 @staticmethod
 def initialize(path):
  p=Path(path)
  with p.open('xb'):pass
  with closing(sqlite3.connect(p)) as db,db:
   db.execute('CREATE TABLE meta(mode TEXT NOT NULL)');db.execute('INSERT INTO meta VALUES(?)',(DOMAIN,))
   db.execute('CREATE TABLE attempts(business_key TEXT PRIMARY KEY, reservation TEXT UNIQUE NOT NULL, status TEXT NOT NULL, receipt TEXT NOT NULL)')
 @staticmethod
 def connect(path):
  p=Path(path).resolve()
  if not p.is_file():raise ValueError('journal_missing_not_empty')
  db=sqlite3.connect(p.as_uri()+'?mode=rw',uri=True,timeout=3)
  try:
   if db.execute('PRAGMA quick_check').fetchone()!=('ok',) or db.execute('SELECT mode FROM meta').fetchall()!=[(DOMAIN,)]:raise ValueError('journal_invalid_or_wrong_domain')
   rows=db.execute('SELECT business_key,reservation,status,receipt FROM attempts').fetchall()
   if len(rows)>32 or any(not is_hash(k) or not is_hash(r) or status not in ('reserved','accepted','unknown','rejected','not_sent') or type(receipt) is not str for k,r,status,receipt in rows):raise ValueError('journal_entries_invalid')
   return db
  except BaseException:db.close();raise
 @staticmethod
 def reserve(path,permit):
  key=sha(json.dumps([permit.bot,permit.recipient,permit.source_fingerprint,permit.text_hash],separators=(',',':')))
  with closing(DispatchJournal.connect(path)) as db:
   db.execute('BEGIN IMMEDIATE')
   if db.execute('SELECT 1 FROM attempts WHERE business_key=? OR reservation=?',(key,permit.reservation_id)).fetchone():return None
   if db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]>=32:raise ValueError('journal_full')
   db.execute('INSERT INTO attempts VALUES(?,?,?,?)',(key,permit.reservation_id,'reserved',''));db.commit()
  return key
 @staticmethod
 def settle(path,key,status,receipt=''):
  if status not in ('accepted','unknown','rejected','not_sent'):raise ValueError('invalid_settlement')
  with closing(DispatchJournal.connect(path)) as db:
   db.execute('BEGIN IMMEDIATE')
   if db.execute('SELECT status FROM attempts WHERE business_key=?',(key,)).fetchone()!=('reserved',):raise ValueError('settlement_conflict')
   db.execute('UPDATE attempts SET status=?,receipt=? WHERE business_key=?',(status,receipt,key));db.commit()
class ConfiguredOneBot:
 """Only use after explicit host enable; settings are never exported to model/audit."""
 def __init__(self,core):self.path=Path(core)/'extensions/core-infinity-qq/config.json';self._config_hash=None
 def __call__(self,action,payload):
  if action not in ('get_login_info','send_private_msg'):raise ValueError('action_not_allowed')
  raw=self.path.read_bytes();digest=hashlib.sha256(raw).digest()
  if self._config_hash is not None and digest!=self._config_hash:raise ValueError('transport_config_changed')
  settings=json.loads(raw);url=urlsplit(settings.get('httpUrl',''))
  if (url.scheme not in ('http','https') or url.hostname not in ('127.0.0.1','localhost','::1') or url.path not in ('','/') or url.query or url.fragment or url.username or url.password or not isinstance(settings.get('token'),str) or not settings['token'] or settings.get('ownerBot') not in (None,'','Telyin') or RECIPIENT not in {str(x) for x in settings.get('privates',[])}):raise ValueError('fixed_authenticated_private_route_required')
  if action=='send_private_msg' and (payload.get('user_id')!=int(RECIPIENT) or set(payload)!={'user_id','message'}):raise ValueError('fixed_recipient_required')
  if action=='get_login_info' and payload!={}:raise ValueError('identity_payload_must_be_empty')
  if action=='send_private_msg':
   segments=payload.get('message')
   if type(segments) is not list or len(segments)!=1 or segments[0].get('type')!='text' or set(segments[0])!={'type','data'} or type(segments[0].get('data')) is not dict or set(segments[0]['data'])!={'text'} or type(segments[0]['data']['text']) is not str or not 0<len(segments[0]['data']['text'].encode())<=2048:raise ValueError('plain_text_only')
  self._config_hash=digest
  import httpx
  deadline=time.monotonic()+6;body=bytearray()
  with httpx.Client(timeout=httpx.Timeout(4,connect=3),trust_env=False,follow_redirects=False) as client:
   with client.stream('POST',settings['httpUrl'].rstrip('/')+'/'+action,json=payload,headers={'Authorization':'Bearer '+settings['token']}) as response:
    response.raise_for_status()
    for part in response.iter_bytes():
     if time.monotonic()>deadline or len(body)+len(part)>32768:raise ValueError('reply_time_or_size_limit')
     body.extend(part)
  return json.loads(body)
class QQTransport:
 def __init__(self,journal,*,clock,authorize_live=None,exchange=None,enabled=False):
  if type(enabled) is not bool or not callable(clock):raise ValueError('explicit_enable_and_clock_required')
  self.journal=Path(journal);self.clock=clock;self.authorize_live=authorize_live;self.exchange=exchange;self.enabled=enabled;self._lock=threading.Lock();self._closed=False
 def valid(self,permit,text):
  if self._closed:return False
  if (type(permit) is not LivePermit or permit.domain!=DOMAIN or permit.bot!='Telyin' or permit.recipient!=RECIPIENT or not all(is_hash(v) for v in (permit.source_fingerprint,permit.text_hash,permit.reservation_id,permit.review_binding,permit.signature)) or type(text) is not str or not text.strip() or len(text.encode('utf-8'))>2048 or sha(text)!=permit.text_hash or type(permit.expires_ms) is not int or not self.clock()<permit.expires_ms<=self.clock()+30000):return False
  return self.authorize_live is not None and self.authorize_live(permit,text) is True and not self._closed
 @staticmethod
 def reply(status,*,attempted=False,accepted=False,receipt=''):
  return {'status':status,'send_attempted':attempted,'accepted_by_protocol':accepted,'recipient_read_confirmed':False,'receipt_id':receipt}
 def send_once(self,permit,text):
  if not self.enabled:return self.reply('disabled')
  if self._closed:return self.reply('closed')
  with self._lock:
   key=None;attempted=False
   try:
    if self.exchange is None or not self.valid(permit,text):return self.reply('permission_denied')
    identity=self.exchange('get_login_info',{})
    if (type(identity) is not dict or type(identity.get('retcode')) is not int or identity['retcode']!=0 or identity.get('status')!='ok' or type(identity.get('data')) is not dict or type(identity['data'].get('user_id')) not in (str,int) or str(identity['data']['user_id'])!=BOT_SELF):return self.reply('bot_identity_unconfirmed')
    if not self.valid(permit,text):return self.reply('permission_changed_before_reservation')
    key=DispatchJournal.reserve(self.journal,permit)
    if key is None:return self.reply('duplicate_or_unresolved_attempt')
    if not self.valid(permit,text):
     DispatchJournal.settle(self.journal,key,'not_sent');return self.reply('permission_changed_before_send')
    # Plain text segment, never parse CQ markup, never arbitrary destination/action.
    body={'user_id':int(RECIPIENT),'message':[{'type':'text','data':{'text':text}}]}
    attempted=True;reply=self.exchange('send_private_msg',body)
    if type(reply) is not dict or type(reply.get('retcode')) is not int:raise ValueError('ambiguous_reply')
    if reply['retcode']!=0:
     DispatchJournal.settle(self.journal,key,'rejected');return self.reply('protocol_rejected',attempted=True)
    receipt=(reply.get('data') or {}).get('message_id') if isinstance(reply.get('data'),dict) else None
    digits=str(receipt).strip();number=digits[1:] if digits.startswith('-') else digits
    if reply.get('status')!='ok' or type(receipt) not in (str,int) or not number.isascii() or not number.isdecimal() or len(digits)>20 or int(digits)==0:raise ValueError('receipt_unconfirmed')
    DispatchJournal.settle(self.journal,key,'accepted',digits)
    return self.reply('accepted',attempted=True,accepted=True,receipt=digits)
   except Exception:
    if key is not None:
     try:DispatchJournal.settle(self.journal,key,'unknown' if attempted else 'not_sent')
     except Exception:pass  # An unresolved reservation remains durable and blocks retry.
    return self.reply('unknown' if attempted else 'failed_closed',attempted=attempted)

 def close(self):
  self._closed=True  # Admission only: cannot recall a network request already submitted.
 def shutdown_complete(self):
  return self._closed and not self._lock.locked()
