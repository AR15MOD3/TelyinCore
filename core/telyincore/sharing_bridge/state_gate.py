"""Host-only, before/after-preview access/history/revocation gate.

It does not synchronize actual transport state, persist ledgers or authorize any
send. The host must provide authenticated, fresh, complete snapshots.
"""
from dataclasses import dataclass,replace
import json
import hashlib
import threading
from .bootstrap import IsolatedHost
from .host_bridge import HostBridge,TransportEvent
from .sharing import Attempt,key,scope,Policy


@dataclass(frozen=True)
class GateState:
    bot: str
    destination: str
    revision: int
    observed_ms: int
    allowed_scopes: frozenset
    revoked: frozenset = frozenset()
    ready: bool = True
    busy: bool = False
    quiet: bool = False
    history_verified: bool = False
    attempts: tuple = ()


class GuardedHost:
    def __init__(self, *, bot, destination, clock, provider, factory):
        if (not key(bot) or not scope(destination) or not callable(clock)
                or not callable(provider) or not callable(factory)):
            raise ValueError('explicit_host_dependencies_required')
        self.bot,self.destination,self.clock,self.provider=bot,destination,clock,provider
        self._last=None
        self._projection=None
        self._generation=0
        self._seen_previews={}  # digest -> expiry only; never cache an allow decision or plaintext
        self._busy=False
        self._lock=threading.RLock()
        self.inner=IsolatedHost(factory=factory,state=lambda:self._projection)

    @property
    def dispatcher(self):return self.inner.dispatcher

    def _snapshot(self,event):
        s=self.provider();now=self.clock()
        if (type(s) is not GateState or s.bot!=self.bot or s.destination!=self.destination
                or type(s.revision) is not int or s.revision<0
                or type(now) is not int or type(s.observed_ms) is not int
                or not 0<=now-s.observed_ms<=30000
                or type(s.allowed_scopes) is not frozenset or len(s.allowed_scopes)>256
                or not all(scope(v) for v in s.allowed_scopes)
                or type(s.revoked) is not frozenset or len(s.revoked)>4096
                or not all(type(v) is tuple and len(v)==3 and v[0]==self.bot
                           and scope(v[1]) and key(v[2]) for v in s.revoked)
                or not all(type(v) is bool for v in (s.ready,s.busy,s.quiet,s.history_verified))
                or type(s.attempts) is not tuple or len(s.attempts)>4096
                or not all(type(a) is Attempt and a.bot==self.bot
                           and a.destination==self.destination for a in s.attempts)):
            raise ValueError('host_snapshot_invalid')
        old=self._last
        if old and (s.revision<old.revision or not s.revoked.issuperset(old.revoked)
                or not set(s.attempts).issuperset(old.attempts)
                or s.revision==old.revision and replace(s,observed_ms=0)!=replace(old,observed_ms=0)):
            raise ValueError('snapshot_rollback_or_history_loss')
        self._last=s  # observe revocations even when the current source is rejected
        # Drop our own raw-source references as soon as a verified snapshot revokes
        # access. Other host/provider copies are outside this object's ownership.
        with self.inner._lock:
            for ref,stored in tuple(self.inner._refs.items()):
                anchor=(stored.bot,stored.scope,stored.message_id)
                if stored.scope not in s.allowed_scopes or anchor in s.revoked:
                    del self.inner._refs[ref]
                    self.inner._anchors.pop(anchor,None)
        if (type(event) is not TransportEvent or event.bot!=s.bot
                or event.scope not in s.allowed_scopes
                or (event.bot,event.scope,event.message_id) in s.revoked):
            raise PermissionError('source_access_revoked')
        if not s.history_verified:raise ValueError('history_unverified')
        return s

    def capture(self,event):
        with self._lock:
            if self._busy or self.inner._closed or self.inner._bridge is None:
                raise RuntimeError('gate_closed_disabled_or_reentrant')
            self._busy=True
            try:
                self._snapshot(event)
                return self.inner.capture(event)
            finally:
                self._busy=False

    def configure(self, *, enabled=False):
        with self._lock:
            if self._busy:raise RuntimeError('gate_reentrant')
            self._generation+=1
            self._seen_previews.clear()
            self.inner.configure(enabled=enabled)
            if not enabled:return
            bridge=self.inner._bridge
            if (bridge.bot,bridge.destination)!=(self.bot,self.destination):
                self.inner.configure(enabled=False)
                raise ValueError('factory_identity_mismatch')
            row=self.inner.dispatcher._table['sharing_preview']
            raw=row.handler;generation=self._generation
            def guarded(source_ref,proposal):
                with self._lock:
                    if self._busy:return json.dumps(HostBridge.result('deny','gate_reentrant'))
                    if generation!=self._generation or self.inner._closed:
                        return json.dumps(HostBridge.result('deny','session_retired'))
                    self._busy=True
                    try:
                        event=self.inner._refs.get(source_ref) if type(source_ref) is str else None
                        before=self._snapshot(event)
                        now=self.clock()
                        lifetime=min(180000,Policy().max_age_ms)
                        if not 0<=now-event.observed_ms<lifetime:
                            return json.dumps(HostBridge.result('deny','source_expired'))
                        self._seen_previews={k:v for k,v in self._seen_previews.items() if now<v}
                        # Bind suppression to this source, session, state revision and policy.
                        # No previously approved result is ever returned on a duplicate.
                        payload=[generation,before.revision,Policy().version,self.bot,self.destination,
                                 source_ref,event.message_id,event.observed_ms,proposal]
                        encoded=json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False)
                        if len(encoded)>16384:
                            return json.dumps(HostBridge.result('deny','proposal_too_large'))
                        fingerprint=hashlib.sha256(encoded.encode('utf-8')).hexdigest()
                        if fingerprint in self._seen_previews:
                            return json.dumps(HostBridge.result('defer','duplicate_preview_suppressed'))
                        # Never evict a live entry just to admit a review storm.
                        if len(self._seen_previews)>=32:
                            return json.dumps(HostBridge.result('defer','preview_suppression_capacity'))
                        self._projection={k:getattr(before,k) for k in
                            ('ready','busy','quiet','history_verified','attempts')}
                        result=json.loads(raw(source_ref,proposal))
                        after=self._snapshot(event)
                        if generation!=self._generation or self.inner._closed:
                            return json.dumps(HostBridge.result('deny','session_retired'))
                        if before.revision!=after.revision:
                            return json.dumps(HostBridge.result('defer','host_state_changed_during_review'))
                        if result.get('action')=='allow' and result.get('dry_run') is True and result.get('will_send') is False:
                            self._seen_previews[fingerprint]=event.observed_ms+lifetime
                        return json.dumps(result,ensure_ascii=False)
                    except PermissionError:
                        return json.dumps(HostBridge.result('deny','source_access_revoked'))
                    except Exception:
                        return json.dumps(HostBridge.result('defer','host_snapshot_unverified'))
                    finally:
                        self._projection=None;self._busy=False
            self.inner.dispatcher._table['sharing_preview']=replace(row,handler=guarded)

    def close(self):
        with self._lock:
            self._generation+=1
            self._seen_previews.clear()
            self.inner.close()

    def shutdown_complete(self):
        with self._lock:return not self._busy and self.inner.shutdown_complete()
