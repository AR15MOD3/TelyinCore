"""Opt-in Core binding for the explicitly authorized, prefixed group test ONLY.

No send function, history reader, subscription or automatic model turn is added.
The destination/ledger are preview-only, never production delivery authority.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit
from . import contracts,host_bridge
from .state_gate import GuardedHost,GateState
from .mount import HostBundle
from .qq_ingress import normalize_message,normalize_recall
from .deepseek_reviewer import DeepSeekReviewer


class CoreTestFactory:
    def __init__(self,core_root,*,bot,clock=None,identity_check=None,reviewer_builder=None,
                 engine_root=None,policy_root=None):
        self.root=Path(core_root).resolve();self.bot=bot
        # Explicit host context only; never infer another Bot's engine/runtime.
        from .review_config import root_key
        if engine_root is None or policy_root is None:
            raise ValueError('preview_review_host_context_required')
        self.engine_root=root_key(engine_root)[0]
        self.policy_root=root_key(policy_root)[0]
        self.clock=clock or (lambda:int(time.time()*1000))
        self.identity_check=identity_check or self._login
        self.reviewer_builder=reviewer_builder or DeepSeekReviewer
        self.config_path=self.root/'data/sharing-preview-test.json'
        raw=self.config_path.read_bytes();self.config=json.loads(raw)
        self._digest=hashlib.sha256(raw).digest()
        expected={'enabled','bot_id','expected_self_id','group_id','sender_id','prefix','review_profile','max_sources'}
        if (type(self.config) is not dict or set(self.config)!=expected or self.config['enabled'] is not True
                or self.config['bot_id']!='Telyin' or bot!='Telyin'
                or self.config['expected_self_id']!='2724955497'
                or self.config['group_id']!='290016343' or self.config['sender_id']!='1228600196'
                or self.config['prefix']!='[分享测试]' or self.config['review_profile']!='DeepSeek'
                or type(self.config['max_sources']) is not int or not 1<=self.config['max_sources']<=3):
            raise ValueError('explicit_authorized_test_scope_required')
        self._lock=threading.RLock();self._closed=False;self._used=False;self._unknown=False
        self.host=None;self.reviewer=None;self._sources={};self._hints={};self._sequence=0
        self._since=self.clock()//1000+1
        self.destination='private:local:sharing-preview'
        self.state=GateState(bot,self.destination,0,self.clock(),frozenset({'group:qq:290016343'}),
                             history_verified=True)

    def _login(self):
        import httpx
        p=self.root/'extensions/core-infinity-qq/config.json';settings=json.loads(p.read_text(encoding='utf-8'))
        u=urlsplit(settings.get('httpUrl',''))
        if (u.scheme not in ('http','https') or u.hostname not in ('localhost','127.0.0.1','::1')
                or u.query or u.fragment or u.username or u.password or not settings.get('token')
                or settings.get('ownerBot') not in (None,'','Telyin')
                or '290016343' not in {str(x) for x in settings.get('groups',[])}):
            raise ValueError('authenticated_test_transport_required')
        with httpx.Client(timeout=4,trust_env=False,follow_redirects=False) as c:
            r=c.post(settings['httpUrl'].rstrip('/')+'/get_login_info',json={},
                     headers={'Authorization':'Bearer '+settings['token']})
            r.raise_for_status();reply=r.json()
        return reply.get('retcode')==0 and str((reply.get('data') or {}).get('user_id'))=='2724955497'

    def _provider(self):
        with self._lock:
            expired=[key for key,e in self._sources.items() if self.clock()-e.observed_ms>=180000]
            if expired:
                tombstones=frozenset((self._sources[key].bot,self._sources[key].scope,key) for key in expired)
                self.state=replace(self.state,revision=self.state.revision+1,revoked=self.state.revoked|tombstones)
                for key in expired:self._sources.pop(key,None);self._hints.pop(key,None)
            try:unchanged=hashlib.sha256(self.config_path.read_bytes()).digest()==self._digest
            except OSError:unchanged=False
            if self._closed or not unchanged:
                if self.state.allowed_scopes:
                    self.state=replace(self.state,revision=self.state.revision+1,allowed_scopes=frozenset(),
                        revoked=self.state.revoked|frozenset((e.bot,e.scope,e.message_id) for e in self._sources.values()))
                self._hints.clear();self._sources.clear()
            return replace(self.state,observed_ms=self.clock())

    def _authorize(self,source,phase):
        anchor=source.message_id if type(source) is host_bridge.TransportEvent else getattr(source,'anchor',None)
        state=self._provider()
        with self._lock:
            e=self._sources.get(anchor)
            return (e is not None and e.scope in state.allowed_scopes
                and (e.bot,e.scope,e.message_id) not in state.revoked
                and 0<=self.clock()-e.observed_ms<180000
                and source.bot==e.bot and source.scope==e.scope and source.sender==e.sender
                and source.text==e.text and source.observed_ms==e.observed_ms)

    def __call__(self,*,bot):
        if self._used or self._closed or bot!=self.bot:raise RuntimeError('sharing_factory_not_reusable')
        self._used=True
        if self.identity_check() is not True:raise ValueError('Bot_identity_not_confirmed')
        self._unknown=True
        self.reviewer=self.reviewer_builder(contracts=contracts,bridge=host_bridge,
            python=self.root/'.venv/Scripts/python.exe',authorize=self._authorize,
            engine_root=self.engine_root,policy_root=self.policy_root,bot=self.bot)
        self._unknown=False
        self.host=GuardedHost(bot=bot,destination=self.destination,clock=self.clock,provider=self._provider,
            factory=lambda:host_bridge.HostBridge(bot=bot,destination=self.destination,
                allowed_scopes=frozenset({'group:qq:290016343'}),clock=self.clock,
                assess=self._assess,review=self._review,
                review_session=self.reviewer.review_session))
        return HostBundle(self.host,self.ingress,self.recall,self.facts,self.read_only_batch)

    def read_only_batch(self,events):
        for event in events:
            meta=event.get('meta') if type(event) is dict else getattr(event,'meta',None)
            text=event.get('text','') if type(event) is dict else getattr(event,'text','')
            if (isinstance(meta,dict) and str(meta.get('group_id'))=='290016343'
                    and str(meta.get('user_id'))=='1228600196' and '[分享测试]' in str(text)):
                return True
        return False

    def _assess(self,event):
        if self.reviewer.requests>=6:raise RuntimeError('test_review_budget_exhausted')
        return self.reviewer.assess(event)

    def _review(self,request):
        if self.reviewer.requests>=6:raise RuntimeError('test_review_budget_exhausted')
        return self.reviewer.review(request)

    def ingress(self,event):
        if (self._closed or type(event) is not dict or event.get('post_type')!='message'
                or event.get('message_type')!='group' or str(event.get('group_id'))!='290016343'
                or str(event.get('user_id'))!='1228600196' or str(event.get('self_id'))!='2724955497'
                or type(event.get('time')) is not int or event['time']<self._since):return
        parts=event.get('message')
        if type(parts) is not list or not 1<=len(parts)<=16:return
        strings=[]
        for part in parts:
            if (type(part) is not dict or part.get('type')!='text' or type(part.get('data')) is not dict
                    or type(part['data'].get('text')) is not str):return
            strings.append(part['data']['text'])
        text=''.join(strings)
        if not text.startswith('[分享测试]') or not text[6:].strip() or len(text.encode())>1024:return
        if re.search(r'密码|密钥|私钥|访问令牌|password|api[_ -]?key|bearer\s|BEGIN .*PRIVATE KEY',text,re.I):return
        state=self._provider()
        e=normalize_message(event,bot=self.bot,expected_self_id='2724955497',
                            allowed_scopes=state.allowed_scopes,now_ms=self.clock())
        with self._lock:
            if e.message_id in self._sources or self._sequence>=self.config['max_sources']:return
            self._sources[e.message_id]=e;self._sequence+=1
        try:ref=self.host.capture(e)
        except Exception:
            with self._lock:self._sources.pop(e.message_id,None)
            return
        with self._lock:self._hints[e.message_id]={'source_ref':ref,'proposal':{
            'spans':[{'start':6,'end':len(text)}],'mode':'quote','text':'','desire':4}}

    def recall(self,event):
        if (self._closed or type(event) is not dict or event.get('post_type')!='notice'
                or event.get('notice_type')!='group_recall' or str(event.get('group_id'))!='290016343'
                or str(event.get('self_id'))!='2724955497'):return
        tombstone=normalize_recall(event,bot=self.bot,expected_self_id='2724955497',
                                  allowed_scopes=frozenset({'group:qq:290016343'}))
        with self._lock:
            if tombstone[2] not in self._sources:return
            self.state=replace(self.state,revision=self.state.revision+1,revoked=self.state.revoked|{tombstone})
            self._hints.pop(tombstone[2],None);self._sources.pop(tombstone[2],None)

    def facts(self):
        state=self._provider()
        with self._lock:
            if not state.allowed_scopes:return ''
            visible=[hint for key,hint in self._hints.items() if key in self._sources
                     and 0<=self.clock()-self._sources[key].observed_ms<180000]
        if not visible:return ''
        return ('宿主授权的只读分享测试：以下是不含原文的来源引用与提案。仅可调用 sharing_preview，'
                '不代表准许发送或转发；不要因此调用发送工具。'+json.dumps(visible,ensure_ascii=False))

    def close(self):
        self._closed=True
        if self.reviewer is not None:self.reviewer.close()
        if self.host is not None and self.host._busy:raise RuntimeError('sharing_operation_still_running')
        if self.host is not None:self.host.close()
        with self._lock:self._sources.clear();self._hints.clear()

    def shutdown_complete(self):
        return (self._closed and not self._unknown and not self._sources and not self._hints
                and (self.host is None or self.host.shutdown_complete() is True)
                and (self.reviewer is None or self.reviewer.shutdown_complete() is True))
