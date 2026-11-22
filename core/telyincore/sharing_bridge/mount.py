"""Full-main host mount. Trusted dependencies only; no automatic live configuration."""
from dataclasses import dataclass
from types import SimpleNamespace
from contextlib import nullcontext
import threading
from ..dispatch import Dispatcher
from .state_gate import GuardedHost
from ..tools import ToolTag


@dataclass(frozen=True)
class HostBundle:
    host: GuardedHost
    ingress: object  # authenticated-host adapter, not a model callable
    recall: object   # host publishes a new revision/tombstone snapshot
    facts: object = None
    read_only_batch: object = None


class BridgeMount:
    def __init__(self, *, bot, plugin_host, factory):
        self._lock=threading.RLock()
        self._closed=False;self._busy=False;self._ready=False;self._unknown=False;self._callbacks=0
        self._attached=False;self._bundle=None;self._rows=()
        self.bot,self.plugin_host=bot,plugin_host
        self.dispatcher=self.body=self.current=self.handlers=None
        self.rewrite=self.transaction=None
        self._hooks={}
        self._receipt_batches=[]
        self._receipt_owners=[]
        self._permission_gate=None;self._old_permissions=None
        names=('sharing_preview_enabled','sharing_ingress','sharing_recall')
        if not callable(factory) or any(k in plugin_host for k in names):
            raise ValueError('explicit_factory_and_unclaimed_host_keys_required')
        self._unknown=True
        bundle=factory(bot=bot)
        if type(bundle) is not HostBundle or type(bundle.host) is not GuardedHost:
            raise TypeError('exact_host_bundle_required')
        self._bundle=bundle;self._unknown=False
        if bundle.host.bot!=bot or not all(callable(f) for f in (bundle.ingress,bundle.recall)):
            raise ValueError('same_bot_host_callbacks_required')
        def callback(fn, *, recall=False):
            def accept(event):
                with self._lock:
                    if self._closed or (not recall and not self._ready):return None
                    self._callbacks+=1
                try:return fn(event)
                finally:
                    with self._lock:self._callbacks-=1
            return accept
        self._hooks={'sharing_preview_enabled':True,
                     'sharing_ingress':callback(bundle.ingress),
                     'sharing_recall':callback(bundle.recall,recall=True)}
        plugin_host.update(self._hooks)

    def attach(self, *, dispatcher, body, current, handlers, rewrite, transaction):
        with self._lock:
            if self._closed or self._attached or self._busy:raise RuntimeError('mount_attach_not_allowed')
            self.dispatcher,self.body,self.current,self.handlers=dispatcher,body,current,handlers
            self.rewrite,self.transaction=rewrite,transaction
            self._old_permissions=dispatcher._permissions
            def permission(tool):
                if self._closed:return False
                predicate=self._bundle.read_only_batch
                if callable(predicate):
                    active=self.plugin_host.get('active_events')
                    if not callable(active):return False
                    try:read_only=predicate(tuple(active()))
                    except Exception:return False
                    if read_only and (not tool.tags or any(tag!=ToolTag.READ for tag in tool.tags)):
                        return False
                return self._old_permissions(tool) if self._old_permissions is not None else True
            self._permission_gate=permission;dispatcher._permissions=permission
            self._attached=True
        self.rebuild(tuple(current))

    def rebuild(self,fresh):
        with self.transaction(),self._lock:
            if self._closed or self._busy or self._callbacks or not self._attached:raise RuntimeError('mount_not_ready')
            self._busy=True;self._ready=False
            try:
                rows=list(fresh)
                if any(r.name=='sharing_preview' for r in rows):raise ValueError('preview_name_conflict')
                self._bundle.host.configure(enabled=True)
                preview=self._bundle.host.dispatcher._table['sharing_preview']
                handlers={k:v for k,v in self.handlers.items() if k!='sharing_preview'}
                def guarded(**args):
                    # Never hold lifecycle/publication locks across independent review:
                    # a concurrent recall must be able to publish its tombstone.
                    with self._lock:
                        if self._closed or self._busy or not self._ready:
                            return '{"dry_run":true,"will_send":false,"action":"deny","reason":"mount_not_ready"}'
                        self._busy=True
                    try:
                        result=preview.handler(**args)
                        with self._lock:
                            if self._closed:
                                return '{"dry_run":true,"will_send":false,"action":"deny","reason":"session_retired"}'
                        return result
                    finally:
                        with self._lock:self._busy=False
                handlers['sharing_preview']=guarded
                staged=Dispatcher({});body=SimpleNamespace(tools=());current=[]
                self.rewrite(fresh=rows+[preview],dispatcher=staged,config_for_body=body,
                             current=current,handlers=handlers)
                if set(staged._table)!={r['name'] for r in body.tools}:raise ValueError('table_mismatch')
                self.dispatcher._table=staged._table
                object.__setattr__(self.body,'tools',body.tools)
                self.current[:]=current
                self.handlers['sharing_preview']=guarded
                self._ready=True
            finally:self._busy=False

    def facts(self):
        with self._lock:
            if self._closed or not self._ready or self._busy:return ''
            fn=self._bundle.facts
        return fn() if callable(fn) else ''

    async def run_tools(self,calls):
        batch=tuple(calls)
        with self._lock:
            if (len({id(c) for c in batch})!=len(batch)
                    or len({c.call_id for c in batch})!=len(batch)
                    or any(c is old for c in batch for previous, _, _ in self._receipt_batches for old in previous)):
                raise ValueError('duplicate or reused mount invocation')
            actual=self.dispatcher
            if not any(owner is actual for owner, _ in self._receipt_owners):
                observer=getattr(actual,'receipt_shutdown_state',None)
                if not callable(observer):raise RuntimeError('mount retirement observer unavailable')
                self._receipt_owners.append((actual,observer))
            query=getattr(actual,'settled_receipts',None)
            if not callable(query):raise RuntimeError('mount receipt producer unavailable')
            self._receipt_batches.append((batch,tuple((c.call_id,c.name) for c in batch),query))
        return await actual.arun(batch)

    def receipt_shutdown_state(self):
        with self._lock:
            owners=tuple(self._receipt_owners)
        states=[observer() for _,observer in owners]
        return {'pending':any(state['pending'] for state in states),
                'incomplete':any(state['incomplete'] for state in states)}

    def settled_receipts(self,calls):
        asked=tuple(calls)
        with self._lock:
            for batch,identity,query in self._receipt_batches:
                if len(batch)==len(asked) and all(a is b for a,b in zip(batch,asked)):
                    if tuple((c.call_id,c.name) for c in asked)!=identity:
                        raise ValueError('changed mount invocation')
                    break
            else:raise ValueError('foreign mount invocation')
        return query(asked)

    def fallback(self,table,wire,current):
        with self.transaction(),self._lock:
            if self._closed or self._busy or self._callbacks:raise RuntimeError('mount_busy_or_closed')
            if ('sharing_preview' in table or any(r['name']=='sharing_preview' for r in wire)
                    or any(r.name=='sharing_preview' for r in current)):
                raise ValueError('preview_free_fallback_required')
            self._ready=False
            self._bundle.host.configure(enabled=False)
            self.handlers.pop('sharing_preview',None)
            self.dispatcher._table=dict(table)
            object.__setattr__(self.body,'tools',tuple(wire));self.current[:]=current

    def close(self,timeout=None):
        with (self.transaction() if self.transaction is not None else nullcontext()),self._lock:
            self._closed=True;self._ready=False
            if self._busy or self._callbacks:raise RuntimeError('close_during_mount_operation')
            for name,value in tuple(self._hooks.items()):
                if self.plugin_host.get(name) is value:self.plugin_host.pop(name,None)
                else:self._unknown=True
            self._hooks.clear()
            if self._bundle is not None:self._bundle.host.close()
            if self._attached:
                if self.dispatcher._permissions is self._permission_gate:
                    self.dispatcher._permissions=self._old_permissions
                else:self._unknown=True
                self.handlers.pop('sharing_preview',None)
                self.dispatcher._table={k:v for k,v in self.dispatcher._table.items() if k!='sharing_preview'}
                object.__setattr__(self.body,'tools',tuple(r for r in self.body.tools if r['name']!='sharing_preview'))
                self.current[:]=[r for r in self.current if r.name!='sharing_preview']

    def shutdown_complete(self):
        receipts=self.receipt_shutdown_state()
        with (self.transaction() if self.transaction is not None else nullcontext()),self._lock:
            return (not receipts['pending'] and not receipts['incomplete']
                    and self._closed and not self._busy and not self._callbacks and not self._ready and not self._unknown
                    and not self._hooks and self._bundle is not None and self._bundle.host.shutdown_complete()
                    and (not self._attached or ('sharing_preview' not in self.dispatcher._table
                         and 'sharing_preview' not in self.handlers
                         and not any(r['name']=='sharing_preview' for r in self.body.tools)
                         and not any(r.name=='sharing_preview' for r in self.current))))
