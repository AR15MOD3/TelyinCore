"""Finite opaque Recovery store preservation, no credential-body or network reads.

Only closed management-login metadata is interpreted. HTTP/pending/mutation
uncertainty refuses. Original account reservations and path-bound denial data
are NOT rewritten to authorize a new HOST after a scoped directory relocation.
"""
import json,re
from bot_rename_accounts import stores,sha,_json,valid,H32,H64
from bot_rename_plugins import namespace_pin
from bot_rename_contexts import opaque_pin
from runtime_identity import LEDGER,safe_path

FILE='snowluma-recovery.json'
LOGIN=FILE+'.login-attempts'
MUTATION=FILE+'.process-attempts'
def roots(bindings,baseline):return {p.rsplit('/',1)[0] for p in stores(bindings,baseline).values()}
def namespace_paths(bindings,baseline):return {r+s for r in roots(bindings,baseline) for s in ('','/'+LOGIN,'/'+MUTATION)}
def payload_paths(bindings,baseline):return {r+'/'+n for r in roots(bindings,baseline) for n in (FILE,FILE+'.bak','.bot-identity.lock')}
def is_namespace(path):
    return re.fullmatch(r'(core/data/infinity-processes|bots/[^/]+/infinity/processes)(/snowluma-recovery\.json\.(login-attempts|process-attempts))?',path) is not None
def control_keys(controls):
    return {p for p in controls if '/'+LOGIN+'/' in p and is_namespace(p.rsplit('/',1)[0])}
def restore(value):
    if type(value) is not dict or len(value)>3072:raise ValueError('bounded recovery namespace required')
    def freeze(v):return tuple(freeze(x) for x in v) if type(v) in (list,tuple) else v
    return {p:freeze(v) for p,v in value.items()}


def _names(ns,bindings,baseline):
    if set(ns)!=namespace_paths(bindings,baseline):raise ValueError('fixed original recovery namespace required')
    names={}
    for p,pin in ns.items():
        if pin==('namespace-absent',):names[p]=();continue
        if (type(pin) is not tuple or len(pin)!=4 or pin[0]!='namespace-directory'
                or any(type(v) is not int or v<0 for v in pin[1:3]) or type(pin[3]) is not tuple
                or any(type(n) is not str for n in pin[3]) or tuple(sorted(set(pin[3])))!=pin[3]):
            raise ValueError('invalid recovery directory metadata')
        names[p]=pin[3]
    for r in roots(bindings,baseline):
        allowed={FILE,FILE+'.bak','.bot-identity.lock',LOGIN,MUTATION}
        if set(names[r])-allowed:raise ValueError('active HTTP, pending or unknown recovery resource')
        if FILE+'.bak' in names[r] and FILE not in names[r]:raise ValueError('orphan opaque recovery backup')
        for child in (LOGIN,MUTATION):
            if (child in names[r])!=(ns[r+'/'+child]!=('namespace-absent',)):
                raise ValueError('recovery namespace topology mismatch')
        if names[r+'/'+MUTATION]:raise ValueError('QQ mutation history needs original reconciliation; never discard')
        entries=names[r+'/'+LOGIN]
        if len(entries)>64 or any(re.fullmatch(H32+r'\.(intent|committed)\.json',n) is None for n in entries):
            raise ValueError('pending or unknown login lifecycle resource')
        intents={n[:32] for n in entries if n.endswith('.intent.json')}
        commits={n[:32] for n in entries if n.endswith('.committed.json')}
        if intents!=commits:raise ValueError('unresolved or orphan management login')
        if entries and FILE not in names[r]:raise ValueError('login metadata without original recovery store')
    return names


def derive_recovery(root,controls,bindings,baseline,namespaces,payloads):
    ns=restore(namespaces);keys=control_keys(controls)
    if not ns:
        if keys or payloads:raise ValueError('recovery resources lack explicit finite scope')
        return
    names=_names(ns,bindings,baseline);expected=set();expected_payloads=set()
    for r in roots(bindings,baseline):
        for n in names[r+'/'+LOGIN]:expected.add(r+'/'+LOGIN+'/'+n)
        for n in names[r]:
            if n in (FILE,FILE+'.bak','.bot-identity.lock'):expected_payloads.add(r+'/'+n)
    if keys!=expected or set(payloads)!=expected_payloads:raise ValueError('recovery before-image scope mismatch')
    for p,pin in payloads.items():
        if (type(pin) is not tuple or len(pin)!=5 or pin[0]!='payload-file'
                or any(type(v) is not int or v<0 for v in pin[1:])):
            raise ValueError('invalid original opaque recovery pin')
    for r in roots(bindings,baseline):
        binding=sha(str(root/r/FILE).encode());rows={}
        for name in names[r+'/'+LOGIN]:
            raw=controls[r+'/'+LOGIN+'/'+name];row=_json(raw)
            nonce,phase,_=name.split('.')
            if (set(row)!={'version','store','owner','revision','phase'} or row['store']!=binding
                    or not valid(row['owner'],H32) or not valid(row['revision']) or row['phase']!=phase):
                raise ValueError('login metadata original binding mismatch')
            rows[(nonce,phase)]=row
        for (nonce,phase),row in rows.items():
            if phase=='committed' and dict(row,phase='intent')!=rows.get((nonce,'intent')):
                raise ValueError('login commit differs from original intent')
    # No credential parse, account release, login/offline/complete claim or rebind.


def capture_recovery(root,bindings,baseline):
    ns={p:namespace_pin(root,p) for p in sorted(namespace_paths(bindings,baseline))}
    names=_names(ns,bindings,baseline);controls={};payloads={}
    for r in sorted(roots(bindings,baseline)):
        for name in names[r]:
            if name in (FILE,FILE+'.bak','.bot-identity.lock'):
                key=r+'/'+name;payloads[key]=opaque_pin(root,key,'payload-file')
        for name in names[r+'/'+LOGIN]:
            relative=r+'/'+LOGIN+'/'+name;p=safe_path(root,root/relative)
            pin=opaque_pin(root,relative,'payload-file')
            with p.open('rb') as f:raw=f.read(1025)
            if len(raw)>1024 or opaque_pin(root,relative,'payload-file')!=pin:
                raise ValueError('login metadata changed or oversized')
            controls[relative]=raw
    derive_recovery(root,controls,bindings,baseline,ns,payloads)
    return controls,ns,payloads


def check_scope(root,namespaces,original_controls):
    if any(is_namespace(p) for p in namespaces):return
    rows=json.loads(original_controls['core/data/'+LEDGER]);owner=json.loads(original_controls['core/data/bot-runtime-owner.json'])
    for r in sorted(roots(rows['bindings'],owner['baseline'])):
        if namespace_pin(root,r)!=('namespace-absent',):
            raise ValueError('recovery resources require explicit finite adapter')
