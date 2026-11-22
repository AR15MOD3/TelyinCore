"""Product context-index adapter. Witnesses describe data, NEVER authorization.

Only context metadata is opened. Session/RecordStore/branch bodies are opaque;
their inode/size/mtime and namespace are checked, not a malicious-host sandbox or
whole-file byte proof. No context IDs/scopes are inferred, reset or manufactured.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import stat

from bot_protocol_reindex import ContextWitness,context_filename,plan_context_reindex
from runtime_identity import safe_path

CONTEXT_BYTES=4*1024*1024
CONTROL_BYTES=16*1024*1024
MANIFEST_BYTES=32*1024*1024


def control_limit(relative):
    return CONTEXT_BYTES if re.search(r'/provider-state/contexts/[0-9a-f]{64}\.json$',relative) else 65536


def witnesses(value):
    if value is None:return None
    if type(value) not in (tuple,list) or len(value)>1024:raise ValueError('bounded context witnesses required')
    result=[]
    for item in value:
        if type(item) is dict:
            if set(item)!={'session_relative','scope','metadata_sha256','branch_context'}:
                raise ValueError('unknown context witness field')
            item=ContextWitness(**item)
        if (type(item) is not ContextWitness or type(item.session_relative) is not str
                or type(item.scope) is not str or len(item.scope)>1024
                or type(item.metadata_sha256) is not str or re.fullmatch('[0-9a-f]{64}',item.metadata_sha256) is None
                or item.branch_context is not None and (type(item.branch_context) is not str
                    or re.fullmatch('[0-9a-f]{64}',item.branch_context) is None)):
            raise ValueError('invalid context witness')
        path=Path(item.session_relative)
        if (path.is_absolute() or '..' in path.parts or path.suffix!='.jsonl'
                or path.as_posix()!=item.session_relative or '\x00' in item.session_relative):
            raise ValueError('canonical relative JSONL session required')
        result.append(item)
    return tuple(result)


def encoded_witnesses(value):
    return None if value is None else [asdict(item) for item in witnesses(value)]


def _names(root,relative):
    folder=safe_path(root,root/relative)
    result=[]
    for child in folder.iterdir():
        safe_path(root,child);result.append(child.name)
        if len(result)>1024:raise ValueError('bounded provider namespace required')
    return tuple(sorted(result))


def opaque_pin(root,relative,kind):
    path=safe_path(root,root/relative);info=path.lstat()
    if kind in ('payload-directory','context-directory'):
        if not stat.S_ISDIR(info.st_mode):raise ValueError('original provider directory required')
        base=(kind,info.st_dev,info.st_ino)
        return (*base,_names(root,relative)) if kind=='payload-directory' else base
    if kind!='payload-file' or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
        raise ValueError('original single-link opaque payload required')
    return (kind,info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)


def restore_opaque(value):
    if type(value) is not dict or len(value)>3076:raise ValueError('bounded opaque resource map required')
    def freeze(v):return tuple(freeze(x) for x in v) if isinstance(v,(list,tuple)) else v
    return {p:freeze(pin) for p,pin in value.items()}


def context_operations(root,runtime,destination,controls,items,opaque):
    """Pure derivation on original before-images; valid even after runtime move."""
    items=witnesses(items);prefix=runtime+'/provider-state/contexts/'
    actual={p for p in controls if p.startswith(prefix)}
    if items is None:
        if actual or opaque:raise ValueError('context inputs lack explicit witness collection')
        return ()
    state=runtime+'/provider-state';allowed={state,state+'/contexts',state+'/record-reasoning-v1',state+'/request-context-branches-v1.json',
        state+'/request-context-branches-v1.json.index-source.lock'}
    expected=set();moves=[];targets=set()
    for item in items:
        session=runtime+'/'+item.session_relative;allowed.add(session)
        source=prefix+context_filename(safe_path(root,root/session),item.scope,item.branch_context)
        target=destination+'/provider-state/contexts/'+context_filename(
            safe_path(root,root/destination/item.session_relative),item.scope,item.branch_context)
        if source in expected or source not in controls or target in targets:
            raise ValueError('missing, duplicate or colliding context witnesses')
        raw=controls[source]
        if len(raw)>CONTEXT_BYTES or hashlib.sha256(raw).hexdigest()!=item.metadata_sha256:
            raise ValueError('context metadata before-image changed')
        def unique(pairs):
            out={}
            for k,v in pairs:
                if k in out:raise ValueError('duplicate context metadata field')
                out[k]=v
            return out
        data=json.loads(raw,object_pairs_hook=unique)
        valid_id=lambda v:type(v) is str and re.fullmatch('[0-9a-f]{64}',v) is not None
        if (type(data) is not dict or set(data)!={'version','context','records'}
                or type(data['version']) is not int or data['version']!=1 or not valid_id(data['context'])
                or type(data['records']) is not list or len(data['records'])>65536
                or not all(valid_id(v) for v in data['records'])):
            raise ValueError('unsupported context metadata')
        expected.add(source);targets.add(target)
        after_move=destination+source[len(runtime):]
        if after_move!=target:moves.append((after_move,target))
        if session not in opaque:raise ValueError('session native metadata witness missing')
    if actual!=expected:raise ValueError('unwitnessed context metadata; never discard or guess scope')
    after_names={destination+p[len(runtime):] for p in actual}
    if any(b in after_names for a,b in moves):raise ValueError('context target occupies another source index')
    for relative,pin in opaque.items():
        if (relative not in allowed and not (relative.startswith(state+'/record-reasoning-v1/')
                and re.fullmatch(r'[0-9a-f]{64}\.json',relative[len(state+'/record-reasoning-v1/'):]))):
            raise ValueError('opaque resource outside fixed provider/session layout')
        directory=relative in (state,state+'/record-reasoning-v1')
        kind='payload-directory' if directory else 'context-directory' if relative==state+'/contexts' else 'payload-file'
        size=4 if directory else 3 if kind=='context-directory' else 5
        if (type(pin) is not tuple or len(pin)!=size or pin[0]!=kind
                or any(type(v) is not int or v<0 for v in pin[1:3])
                or kind=='payload-file' and any(type(v) is not int or v<0 for v in pin[3:])):
            raise ValueError('invalid opaque metadata pin shape')
        if directory and (type(pin[3]) is not tuple or len(pin[3])>1024
                or any(type(n) is not str or not n or n in ('.','..') or '/' in n or '\\' in n for n in pin[3])
                or tuple(sorted(set(pin[3])))!=pin[3]):
            raise ValueError('invalid opaque namespace pin')
    if actual and (state not in opaque or state+'/contexts' not in opaque):
        raise ValueError('provider directory identity witness missing')
    return tuple(sorted(moves))


def capture_contexts(root,runtime,destination,items):
    items=witnesses(items)
    if items is None:return {},{}
    state=runtime+'/provider-state';folder=safe_path(root,root/state)
    controls={};opaque={}
    if folder.exists():
        if not folder.is_dir():raise ValueError('provider-state directory required')
        top=_names(root,state)
        if set(top)-{'contexts','record-reasoning-v1','request-context-branches-v1.json',
                     'request-context-branches-v1.json.index-source.lock'}:
            raise ValueError('unclassified provider-state resource; adapter required')
        opaque[state]=opaque_pin(root,state,'payload-directory')
        for name in top:
            relative=state+'/'+name
            if name=='contexts':
                opaque[relative]=opaque_pin(root,relative,'context-directory')
                for filename in _names(root,relative):
                    if re.fullmatch(r'[0-9a-f]{64}\.json',filename) is None:
                        raise ValueError('unknown context index')
                    path=safe_path(root,root/relative/filename)
                    info=path.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('ordinary context file required')
                    with path.open('rb') as stream:raw=stream.read(CONTEXT_BYTES+1)
                    if len(raw)>CONTEXT_BYTES:raise ValueError('context metadata size limit')
                    controls[relative+'/'+filename]=raw
            elif name=='record-reasoning-v1':
                opaque[relative]=opaque_pin(root,relative,'payload-directory')
                for filename in _names(root,relative):
                    if re.fullmatch(r'[0-9a-f]{64}\.json',filename) is None:
                        raise ValueError('unknown RecordStore resource')
                    key=relative+'/'+filename;opaque[key]=opaque_pin(root,key,'payload-file')
            else:opaque[relative]=opaque_pin(root,relative,'payload-file')
    # Existing metadata planner validates complete explicit scope/branch witnesses;
    # never loads Session.records() or RecordStore.read().
    plan_context_reindex(root/runtime,root/destination,items)
    for item in items:
        relative=runtime+'/'+item.session_relative;opaque[relative]=opaque_pin(root,relative,'payload-file')
    context_operations(root,runtime,destination,controls,items,opaque)
    return controls,opaque


def check_payloads(root,controls,opaque):
    for relative,expected in opaque.items():
        if opaque_pin(root,relative,expected[0])!=expected:
            raise ValueError('opaque payload or namespace native metadata changed')
        if expected[0]=='context-directory':
            wanted=tuple(sorted(p[len(relative)+1:] for p in controls if p.startswith(relative+'/')))
            if _names(root,relative)!=wanted:raise ValueError('context namespace changed')
