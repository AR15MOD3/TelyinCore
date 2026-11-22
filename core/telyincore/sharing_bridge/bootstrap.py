"""Isolated host bootstrap, using copied Core dispatcher and resource scope.

Not production launch.py. No QQ clients, real ingress, credentials or send tool.
"""
from dataclasses import asdict
import json
import secrets
import threading
from .host_bridge import HostBridge, TransportEvent
from .contracts import parse_proposal
from ..dispatch import Dispatcher
from ..tools import ToolDef, ToolTag, build_table
from .resource_scope import ResourceScope
from .sharing import key,scope,Policy

# Explicit unresolved-resource retention. Never auto-retry or discard on failure.
RETAINED={}


class IsolatedHost:
    def __init__(self, *, factory=None, state=None):
        self._lock=threading.RLock()
        self._closed=False
        self._active=False
        self._changing=False
        self._generation=0
        self._bridge=None
        self._unknown=False
        self._retired=[]
        self._refs={}
        self._anchors={}
        self._factory,self._state=factory,state
        self.dispatcher=Dispatcher({})
        self.configure()  # default off: factory is not invoked

    def _drain(self):
        for old in tuple(self._retired):
            old.close()
            if old._closed is not True or old._in_call:
                raise RuntimeError('bridge_exit_unconfirmed')
            self._retired.remove(old)

    def configure(self, *, enabled=False):
        with self._lock:
            if type(enabled) is not bool:
                raise ValueError('exact_bool_required')
            if self._closed or self._active or self._changing or self._retired or self._unknown:
                raise RuntimeError('host_closed_busy_or_unresolved')
            self._changing=True
            old,self._bridge=self._bridge,None
            self._generation+=1
            self.dispatcher._table={}
            self._refs.clear();self._anchors.clear()
            if old:self._retired.append(old)
            try:
                self._drain()
                if not enabled:return
                if not callable(self._factory) or not callable(self._state):
                    raise ValueError('explicit_host_adapters_required')
                # Register acquisition uncertainty before invoking an arbitrary factory.
                self._unknown=True
                new=self._factory()
                if type(new) is not HostBridge:
                    raise ValueError('exact_bridge_required')
                self._unknown=False
                self._bridge=new
                if new._closed or self._closed:
                    raise ValueError('fresh_active_bridge_required')
                generation=self._generation
                def preview(source_ref,proposal):
                    with self._lock:
                        if self._closed or self._generation!=generation or self._bridge is not new:
                            return json.dumps(HostBridge.result('deny','session_retired'))
                        if self._active or self._changing:
                            return json.dumps(HostBridge.result('deny','host_reentrant'))
                        self._active=True
                        try:
                            if type(source_ref) is not str or source_ref not in self._refs:
                                return json.dumps(HostBridge.result('deny','source_unavailable'))
                            p=parse_proposal(json.dumps(proposal,ensure_ascii=False))
                            state=self._state()
                            if type(state) is not dict or set(state)!= {'ready','busy','quiet','history_verified','attempts'}:
                                return json.dumps(HostBridge.result('defer','host_state_unverified'))
                            result=new.preview(self._refs[source_ref],p,**state)
                            if self._closed:return json.dumps(HostBridge.result('deny','session_retired'))
                            return json.dumps(result,ensure_ascii=False)
                        except Exception:
                            return json.dumps(HostBridge.result('defer','host_adapter_failed_closed'))
                        finally:
                            self._active=False
                row=ToolDef('sharing_preview','只预览分享决定，不发送消息。',
                    {'type':'object','additionalProperties':False,'required':['source_ref','proposal'],
                     'properties':{'source_ref':{'type':'string'},'proposal':{'type':'object'}}},(ToolTag.READ,))
                self.dispatcher._table=build_table([row],{'sharing_preview':preview})
            except BaseException:
                self.dispatcher._table={}
                if self._bridge is not None:
                    self._retired.append(self._bridge);self._bridge=None
                self._drain()
                raise
            finally:
                self._changing=False

    def capture(self,event):
        """Host transport entry, not a tool. No automatic subscription or history load."""
        with self._lock:
            b=self._bridge
            if (self._closed or self._active or self._changing or b is None
                    or type(event) is not TransportEvent or event.bot!=b.bot
                    or event.scope not in b.allowed_scopes
                    or not key(event.message_id) or not scope('private:'+event.sender)
                    or not event.sender.startswith(event.scope.split(':')[1]+':')
                    or event.scope.startswith('private:') and event.scope!='private:'+event.sender
                    or type(event.observed_ms) is not int or type(event.text) is not str
                    or not 1<=len(event.text.encode())<=32768
                    or not 0<=b.clock()-event.observed_ms<Policy().max_age_ms):
                raise ValueError('host_capture_disabled_or_wrong_source')
            # Keyed by authoritative transport identity, never model-proposed event IDs.
            anchor=(event.bot,event.scope,event.message_id)
            if anchor in self._anchors:
                ref=self._anchors[anchor]
                if self._refs[ref]!=event:raise ValueError('transport_anchor_changed')
                return ref
            if len(self._refs)>=128:raise ValueError('source_cache_full')
            ref=secrets.token_hex(24)
            self._refs[ref]=event;self._anchors[anchor]=ref
            return ref

    def close(self):
        with self._lock:
            self._closed=True
            self._generation+=1
            self.dispatcher._table={}
            self._refs.clear();self._anchors.clear()
            if self._bridge is not None:
                self._retired.append(self._bridge);self._bridge=None
            if self._active or self._changing:raise RuntimeError('close_during_host_operation')
            self._drain()

    def shutdown_complete(self):
        with self._lock:
            return (self._closed and not self._active and not self._changing and not self._unknown
                and self._bridge is None and not self._retired and not self._refs
                and not self._anchors and not self.dispatcher._table)


def run_isolated(service, *, enabled=False, factory=None, state=None):
    """Explicit acquisition and finally-close with independent completion check."""
    scope=ResourceScope()
    host=IsolatedHost(factory=factory,state=state)
    scope.adopt('sharing_host',host,stop=host.close,current=lambda:host,gone=host.shutdown_complete)
    primary=None
    try:
        host.configure(enabled=enabled)
        return service(host)
    except BaseException as error:
        primary=error
        raise
    finally:
        try:
            scope.finish(primary=primary)
        finally:
            if not host.shutdown_complete():RETAINED[id(host)]=(host,scope)
