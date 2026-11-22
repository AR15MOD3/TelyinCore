"""Finite QQ-config ownership adapter; data scope NEVER grants account authority.

Opt-in only. Unknown plugin/resources and sharing/corpus need separate adapters.
No discovery of account stores, login, network, loading plugins or secret export.
"""
import json,os,stat
from runtime_identity import safe_path


def config_path(name,baseline):
    return ('core/extensions/core-infinity-qq/config.json' if name==baseline
            else 'bots/'+name+'/infinity/qq/config.json')


def namespace_specs(bindings,baseline,*,recovery=False):
    out={'core/extensions':('core-infinity-qq',),
         'core/extensions/core-infinity-qq':('config.json',)}
    for name in bindings:
        out['bots/'+name+'/infinity']=() if name==baseline else ('qq','processes') if recovery else ('qq',)
        out['bots/'+name+'/infinity/qq']=() if name==baseline else ('config.json',)
    return out


def namespace_pin(root,relative):
    p=safe_path(root,root/relative)
    try:i=p.lstat()
    except FileNotFoundError:return ('namespace-absent',)
    if not stat.S_ISDIR(i.st_mode):raise ValueError('plugin namespace directory required')
    names=[]
    for c in p.iterdir():
        safe_path(root,c);names.append(c.name)
        if len(names)>1024:raise ValueError('plugin namespace bounded')
    return ('namespace-directory',i.st_dev,i.st_ino,tuple(sorted(names)))


def restore_namespaces(value):
    if type(value) is not dict or len(value)>2050:raise ValueError('bounded plugin namespace map required')
    def freeze(v):return tuple(freeze(x) for x in v) if type(v) in (tuple,list) else v
    return {k:freeze(v) for k,v in value.items()}


def _object(raw):
    if type(raw) is not bytes or len(raw)>65536:raise ValueError('bounded QQ config required')
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out:raise ValueError('duplicate QQ config field')
            out[k]=v
        return out
    def bad_number(_):raise ValueError('nonfinite QQ config number')
    data=json.loads(raw,object_pairs_hook=unique,parse_constant=bad_number)
    if type(data) is not dict:raise ValueError('QQ config object required')
    return data


def owner_bytes(raw,owner,new):
    """Replace just the top-level owner value token; all other bytes survive."""
    obj=_object(raw);before=obj.get('ownerBot')
    if before is not None and (type(before) is not str or before not in ('',owner)):
        raise ValueError('QQ config owner conflicts with original layout')
    if (obj.get('sharingEnabled') not in (None,False)
            or obj.get('sharingOwner') not in (None,'')
            or obj.get('sharingPreview') not in (None,False)):
        raise ValueError('sharing authorization needs a separate adapter')
    # Reject bool-as-zero permissiveness; no sharing means absent/null/false only.
    if any(k in obj and obj[k] is not None and type(obj[k]) is not bool
           for k in ('sharingEnabled','sharingPreview')):
        raise ValueError('invalid sharing state')
    def finish(text):
        out=text.encode('utf-8')
        if len(out)>65536:raise ValueError('QQ owner after-image exceeds config limit')
        return out
    text=raw.decode('utf-8');decoder=json.JSONDecoder();i=0
    def ws(i):
        while i<len(text) and text[i] in ' \r\n\t':i+=1
        return i
    i=ws(i)
    if text[i]!='{':raise ValueError('canonical UTF-8 QQ object required')
    i=ws(i+1);replacement=json.dumps(new,ensure_ascii=False)
    while text[i]!='}':
        key,end=decoder.raw_decode(text,i);i=ws(end)
        if text[i]!=':':raise ValueError('QQ config colon required')
        start=ws(i+1);_,end=decoder.raw_decode(text,start)
        if key=='ownerBot':return finish(text[:start]+replacement+text[end:])
        i=ws(end)
        if text[i]==',':i=ws(i+1)
        elif text[i]!='}':raise ValueError('QQ config separator required')
    return finish(text[:i]+(',' if obj else '')+'"ownerBot":'+replacement+text[i:])


def derive_plugins(controls,bindings,baseline,old,new,namespaces,*,recovery=False):
    """Canonical fixed-layout derivation from before-images, never journal commands."""
    namespaces=restore_namespaces(namespaces)
    specs=namespace_specs(bindings,baseline,recovery=recovery)
    paths={config_path(n,baseline):n for n in bindings}
    actual={p for p in controls if p in paths}
    if not namespaces:
        if actual:raise ValueError('QQ controls lack explicit plugin namespace scope')
        return ()
    if set(namespaces)!=set(specs):raise ValueError('plugin namespace scope differs from bound names')
    expected=set()
    for relative,pin in namespaces.items():
        if pin==('namespace-absent',):continue
        if (type(pin) is not tuple or len(pin)!=4 or pin[0]!='namespace-directory'
                or any(type(v) is not int or v<0 for v in pin[1:3])
                or type(pin[3]) is not tuple or tuple(sorted(set(pin[3])))!=pin[3]
                or any(n not in specs[relative] for n in pin[3])):
            raise ValueError('unknown plugin or resource; adapter required')
        for name in pin[3]:
            child=relative+'/'+name
            if name=='config.json':expected.add(child)
            elif not (recovery and name=='processes') and namespaces.get(child,('namespace-absent',))==('namespace-absent',):
                raise ValueError('plugin namespace topology mismatch')
        parent=relative.rsplit('/',1)[0]
        if parent in namespaces and (namespaces[parent]==('namespace-absent',)
                or relative.rsplit('/',1)[1] not in namespaces[parent][3]):
            raise ValueError('plugin parent namespace mismatch')
    if actual!=expected:raise ValueError('QQ config roster differs from actual plugin namespace')
    changes=[]
    for path in sorted(actual):
        owner=paths[path];after=owner_bytes(controls[path],owner,new if owner==old else owner)
        if owner==old:
            target=('bots/'+new+path[len('bots/'+old):]) if path.startswith('bots/'+old+'/') else path
            changes.append((target,controls[path],after))
    return tuple(changes)


def capture_plugins(root,bindings,baseline,*,recovery=False):
    specs=namespace_specs(bindings,baseline,recovery=recovery);namespaces={};controls={}
    # Validate ALL names before opening any config; unknown resource bodies unread.
    for relative,allowed in specs.items():
        pin=namespace_pin(root,relative);namespaces[relative]=pin
        if pin[0]=='namespace-directory' and any(n not in allowed for n in pin[3]):
            raise ValueError('unknown plugin or resource; adapter required')
    for name in bindings:
        relative=config_path(name,baseline);parent=relative.rsplit('/',1)[0]
        if namespaces[parent][0]=='namespace-directory' and 'config.json' in namespaces[parent][3]:
            p=safe_path(root,root/relative);i=p.lstat()
            if not stat.S_ISREG(i.st_mode) or i.st_nlink!=1:raise ValueError('single-link QQ config required')
            with p.open('rb') as f:raw=f.read(65537);opened=os.fstat(f.fileno())
            if (len(raw)>65536 or not stat.S_ISREG(opened.st_mode) or opened.st_nlink!=1
                    or (opened.st_dev,opened.st_ino)!=(i.st_dev,i.st_ino)):
                raise ValueError('QQ config changed or oversized')
            controls[relative]=raw
    return controls,namespaces


def check_namespaces(root,current,known=None):
    for relative in (set(current) if known is None else known):
        expected=current.get(relative,('namespace-absent',))
        if namespace_pin(root,relative)!=expected:raise ValueError('plugin namespace changed')
