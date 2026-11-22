"""Opt-in, per-host channel provenance and immutable accepted-event contexts.

This is presentation context only, never identity/scope/outbound authorization.
Accepted receipts remain valid after stop; new ingress is revoked immediately.
"""
from __future__ import annotations
import hashlib,hmac,json,secrets,time
from dataclasses import dataclass
from threading import RLock
from .channel_context_contract import ContextRegistry,ContextSelection,ContextError,declarations,_BUILTINS,_RESERVED
from .events import DRAFT_FIELDS

_RECEIPT='_host_channel_context_v1'

def _payload(value):
    def field(k):return value.get(k) if isinstance(value,dict) else getattr(value,k,None)
    fields={k:field(k) for k in DRAFT_FIELDS}
    fields['meta']={k:v for k,v in (fields.get('meta') or {}).items() if k!=_RECEIPT}
    return json.dumps(fields,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()

@dataclass
class ChannelLease:
    owner: object
    provider: str
    token: str
    contexts: dict
    state: str='staged'

    def push(self,push,draft):
        with self.owner.lock:
            if self.state not in ('staged','active'):
                raise ContextError('provider_ingress_retired')
            box=dict(draft or {})
            if box.get('source') not in self.contexts or box.get('origin')!='external':
                raise ContextError('provider_source_mismatch')
            box['meta']=dict(box.get('meta') or {})
            box['meta'].pop(_RECEIPT,None)
            if not box.get('ts'):box['ts']=time.strftime('%Y-%m-%dT%H:%M:%S',time.localtime())
            signature=hmac.new(self.owner.secret,self.token.encode()+b'\0'+_payload(box),hashlib.sha256).hexdigest()
            box['meta'][_RECEIPT]={'lease':self.token,'mac':signature}
        # Never hold the registry lock while ingress marshals onto another loop.
        return push(box)

    def activate(self):
        with self.owner.lock:
            if self.state!='staged':raise ContextError('invalid_provider_phase')
            if any(source in self.owner.active for source in self.contexts):
                raise ContextError('source_conflict')
            self.owner.active.update({source:self.token for source in self.contexts})
            self.state='active'

    def retire(self):
        with self.owner.lock:
            self.owner.active={s:t for s,t in self.owner.active.items() if t!=self.token}
            if self.state!='aborted':self.state='retired'

    def abort(self):
        self.retire()
        with self.owner.lock:
            self.state='aborted'

class ChannelContextHost:
    def __init__(self):
        self.lock=RLock();self.secret=secrets.token_bytes(32);self.leases={};self.active={}

    def prepare(self,provider,root,manifest):
        parsed=declarations(manifest)
        if not parsed:return None  # old display-only manifests stay compatible
        staged=ContextRegistry()
        staged.register(provider,root,manifest,enabled=True,loaded=True)
        contexts={source:text for source,(name,text) in staged._providers.items()}
        with self.lock:
            # No unbounded retirement history; fail explicitly rather than drop
            # an already accepted event's description. Host restart resets it.
            if len(self.leases)>=128:raise ContextError('provider_generation_limit')
            if any(source in self.active for source in contexts):raise ContextError('source_conflict')
            lease=ChannelLease(self,provider,secrets.token_hex(32),contexts)
            self.leases[lease.token]=lease
            return lease

    def push_unconfigured(self, push, draft):
        # Legacy plugins remain loadable but may not impersonate host ingress
        # or borrow another provider's receipt through the supported SDK.
        box = dict(draft or {})
        source = box.get('source')
        if (box.get('origin') != 'external' or not isinstance(source, str)
                or not source or source in _RESERVED):
            raise ContextError('unconfigured_provider_envelope')
        box['meta'] = dict(box.get('meta') or {})
        box['meta'].pop(_RECEIPT, None)
        return push(box)

    def select(self,batch):
        external=[item for item in batch if getattr(item,'origin',None)=='external']
        if not external:return ContextSelection('builtin','internal','host',_BUILTINS['internal'])
        if all(getattr(item,'source',None)=='console' for item in external):
            return ContextSelection('builtin','console','host',_BUILTINS['console'])
        sources={getattr(item,'source',None) for item in external}
        if any(not isinstance(s,str) or not s for s in sources):raise ContextError('invalid_envelope_source')
        if len(sources)>1:return ContextSelection('mixed','mixed','host',_BUILTINS['mixed'])
        source=next(iter(sources));selected=[]
        with self.lock:
            for event in external:
                receipt=(getattr(event,'meta',None) or {}).get(_RECEIPT)
                if not isinstance(receipt,dict):return ContextSelection('unconfigured',source,'host',_BUILTINS['unknown'])
                token=receipt.get('lease')
                if not isinstance(token,str):raise ContextError('untrusted_provider_receipt')
                lease=self.leases.get(token)
                expected=hmac.new(self.secret,token.encode()+b'\0'+_payload(event),hashlib.sha256).hexdigest()
                given=receipt.get('mac')
                if (lease is None or lease.state not in ('active','retired')
                        or source not in lease.contexts or not isinstance(given,str)
                        or not hmac.compare_digest(given,expected)):
                    raise ContextError('untrusted_provider_receipt')
                selected.append((lease.provider,lease.contexts[source]))
        if len(set(selected))!=1:return ContextSelection('mixed','mixed','host',_BUILTINS['mixed'])
        provider,text=selected[0]
        return ContextSelection('plugin',source,provider,text)
