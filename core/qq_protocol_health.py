"""Nonblocking, read-only OneBot health. Never logs in, injects or sends messages.

Management-session TTL and protocol liveness are intentionally independent.
Polling starts at the first explicit status read, only for an enabled, configured
Bot. One bounded worker per control; no timer, login, process launch or retries.
"""
from __future__ import annotations
import hashlib
import json
from threading import Event, Lock, Thread
from time import monotonic
from urllib.parse import urlsplit
import httpx

FIELDS=('httpUrl','wsUrl','token','wsToken')


def revision(data, target):
    return hashlib.sha256(json.dumps([target, {k:data.get(k) for k in FIELDS}],
        sort_keys=True).encode()).hexdigest()


def plugin_connected(items, data):
    """Compare the live instance's loaded endpoints/auth, not just 'loaded'."""
    matches=[instance for name,instance in items if name=='core-infinity-qq']
    if len(matches)!=1:return False
    p=matches[0];client=getattr(p,'client',None)
    try:
        return (p.connected is True and not p._stopping.is_set() and client is not None
            and not client.is_closed
            and str(client.base_url).rstrip('/')==str(data.get('httpUrl','')).rstrip('/')
            and p.ws_url.rstrip('/')==str(data.get('wsUrl','')).rstrip('/')
            and client.headers.get('Authorization')=='Bearer '+str(data.get('token',''))
            and p.ws_headers.get('Authorization')=='Bearer '+str(data.get('wsToken',data.get('token',''))))
    except (AttributeError,TypeError,ValueError):return False


def eligible(data, target):
    if not isinstance(target,str) or not target.isascii() or not target.isdecimal() or int(target)<=0:
        return False
    if not isinstance(data.get('token'),str) or not data['token'].strip():return False
    try:
        for key,scheme in [('httpUrl','http'),('wsUrl','ws')]:
            u=urlsplit(str(data.get(key,'')))
            if (u.scheme!=scheme or u.hostname!='127.0.0.1' or u.path not in ('','/')
                or u.username or u.password or u.query or u.fragment or not u.port):return False
        return urlsplit(data['httpUrl']).port!=urlsplit(data['wsUrl']).port
    except ValueError:return False


def probe(data, target, *, current, trusted, client=httpx.Client):
    """Only get_login_info/get_status; replies must attest the configured target."""
    unknown={'identityVerified':False,'online':False,'good':False}
    def result(status,**kw):return {**unknown,'status':status,**kw}
    if not eligible(data,target):return result('unconfigured')
    if not current():return result('changed')
    if not trusted():return result('untrusted')
    deadline=monotonic()+6
    def read(c,action):
        if not current() or monotonic()>deadline:raise ValueError('changed')
        with c.stream('POST',data['httpUrl'].rstrip('/')+'/'+action,json={}) as r:
            if r.status_code in (401,403):raise PermissionError('auth')
            if r.status_code!=200:raise ValueError('response')
            raw=bytearray()
            for chunk in r.iter_bytes():
                if len(raw)+len(chunk)>65536 or monotonic()>deadline or not current():
                    raise ValueError('bounds')
                raw.extend(chunk)
        value=json.loads(raw)
        if (not isinstance(value,dict) or value.get('status')!='ok'
            or type(value.get('retcode')) is not int or value['retcode']!=0
            or not isinstance(value.get('data'),dict)):raise ValueError('response')
        return value['data']
    try:
        with client(timeout=2,trust_env=False,follow_redirects=False,
                    headers={'Authorization':'Bearer '+data['token']}) as c:
            identity=read(c,'get_login_info')
            if str(identity.get('user_id'))!=target:return result('identity_mismatch')
            state=read(c,'get_status')
        if not current():return result('changed')
        if not trusted():return result('untrusted')
        if not current():return result('changed')
        online=state.get('online') is True;good=state.get('good') is True
        return result('online' if online and good else 'offline',identityVerified=True,online=online,good=good)
    except PermissionError:return result('auth_failed')
    except (httpx.HTTPError,ValueError,TypeError):return result('unavailable')


class ProtocolHealth:
    def __init__(self, snapshot, trusted, connected, *, clock=monotonic, run_probe=probe):
        self.snapshot=snapshot;self.trusted=trusted;self.connected=connected
        self.clock=clock;self.run_probe=run_probe;self.lock=Lock();self.stop=Event()
        self.key=None;self.sample=None;self.at=None;self.last=None;self.thread=None

    def read(self,data,target,enabled):
        key=revision(data,target)
        valid=enabled and eligible(data,target)
        with self.lock:
            if key!=self.key or not valid:
                self.key=key;self.sample=None;self.at=None;self.last=None
            if (valid and not self.stop.is_set()
                and (self.thread is None or not self.thread.is_alive())
                and (self.last is None or self.clock()-self.last>=15)):
                self.last=self.clock()
                self.thread=Thread(target=self._run,args=(dict(data),target,key),
                                   name='onebot-readonly-health',daemon=True)
                self.thread.start()
            age=None if self.at is None else max(0,self.clock()-self.at)
            fresh=age is not None and age<30
            value=dict(self.sample) if fresh and self.sample else {'status':'checking' if valid else 'unconfigured'}
            busy=bool(self.thread and self.thread.is_alive())
        try:connected=self.connected(data) is True if valid else False
        except Exception:connected=False
        return {'schema':'onebot-readonly-v1','targetUin':target,'sampleAgeSeconds':age,
                'fresh':fresh,'checking':busy,'pluginConnected':connected,**value,
                'businessRoundtripVerified':None}

    def refresh(self,data,target,enabled):
        with self.lock:
            if self.thread is None or not self.thread.is_alive():self.last=None
        return self.read(data,target,enabled)

    def _run(self,data,target,key):
        def current():
            if self.stop.is_set():return False
            try:
                now,t,enabled=self.snapshot()
                return enabled is True and revision(now,t)==key
            except Exception:return False
        try:
            value=self.run_probe(data,target,current=current,trusted=lambda:self.trusted(data))
        except Exception:
            value={'status':'unavailable','identityVerified':False,'online':False,'good':False}
        with self.lock:
            if self.key==key and current():
                self.sample=value;self.at=self.clock()

    def close(self):
        self.stop.set()
        thread=self.thread
        if thread is not None:
            thread.join(timeout=12)
            if thread.is_alive():
                raise RuntimeError('protocol health thread did not drain; ownership retained')
