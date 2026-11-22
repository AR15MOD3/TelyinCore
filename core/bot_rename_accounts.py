"""Immutable local account-history preservation, NEVER retirement authority.

Only fixed hash/nonce metadata. No recovery/credential/session/account config
body is opened. A matching local-retired record is data, not proof of native
absence or external offline. Original reservations are NEVER cleared/rekeyed.
"""
import hashlib,json,os,re,stat
from bot_rename_plugins import namespace_pin,restore_namespaces
from runtime_identity import safe_path

ADMISSIONS='core/data/parent-account-admissions'
RESERVATIONS='core/data/qq-configured-account-reservations'
REVOCATIONS='core/data/default-account-revocations'
CUSTODY='core/data/snowluma-companion-custody.json'
ACCOUNT_NAMES=frozenset((ADMISSIONS,RESERVATIONS,REVOCATIONS,CUSTODY))
H32=r'[0-9a-f]{32}';H64=r'[0-9a-f]{64}'


def sha(raw):return hashlib.sha256(raw).hexdigest()
def valid(value,pattern=H64):return type(value) is str and re.fullmatch(pattern,value) is not None
def stores(bindings,baseline):
    return {n:('core/data/infinity-processes/snowluma-recovery.json' if n==baseline
               else 'bots/'+n+'/infinity/processes/snowluma-recovery.json') for n in bindings}
def normalized_digest(path):
    return sha(os.path.normcase(os.path.normpath(str(path.resolve()))).encode())


def _json(raw,limit=1024):
    if type(raw) is not bytes or len(raw)>limit:raise ValueError('bounded account history metadata required')
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out:raise ValueError('duplicate account history metadata')
            out[k]=v
        return out
    data=json.loads(raw,object_pairs_hook=unique)
    if type(data) is not dict or type(data.get('version')) is not int or data.get('version')!=1:
        raise ValueError('unsupported account history metadata')
    return data


def control_keys(controls):
    return {p for p in controls if p.startswith(ADMISSIONS+'/') or p.startswith(RESERVATIONS+'/')}


def _names(ns):
    result={}
    if set(ns)!=ACCOUNT_NAMES:raise ValueError('fixed account history scope required')
    for path,pin in ns.items():
        if pin==('namespace-absent',):result[path]=();continue
        if (path==CUSTODY or type(pin) is not tuple or len(pin)!=4 or pin[0]!='namespace-directory'
                or any(type(v) is not int or v<0 for v in pin[1:3]) or type(pin[3]) is not tuple
                or any(type(n) is not str for n in pin[3]) or len(pin[3])>1024
                or tuple(sorted(set(pin[3])))!=pin[3]):
            raise ValueError('invalid account namespace or outstanding companion custody')
        result[path]=pin[3]
    if result[REVOCATIONS]:raise ValueError('account revocation requires original reconciliation')
    for name in result[ADMISSIONS]:
        if not (re.fullmatch(H64+r'(\.local-retired)?\.json',name) or re.fullmatch('revision-'+H32+r'\.json',name)):
            raise ValueError('active work, pending or unknown account metadata; no migration')
    for name in result[RESERVATIONS]:
        if re.fullmatch(H64+r'\.json',name) is None:
            raise ValueError('pending or unknown configured-account reservation')
    return result


def derive_accounts(root,controls,bindings,baseline,namespaces):
    ns=restore_namespaces(namespaces);keys=control_keys(controls)
    if not ns:
        if keys:raise ValueError('account controls lack explicit historical scope')
        return
    names=_names(ns)
    expected={folder+'/'+n for folder in (ADMISSIONS,RESERVATIONS) for n in names[folder]}
    if keys!=expected:raise ValueError('account history controls/namespace mismatch')
    paths=stores(bindings,baseline)
    own=root/'bots'/baseline/'infinity/processes/snowluma-recovery.json'
    if own.exists():
        from service_layout import qq_recovery_file
        paths[baseline]=qq_recovery_file(root/'core',baseline,baseline=True).relative_to(root).as_posix()
    by_bot={sha(n.encode()):sha(str(root/p).encode()) for n,p in paths.items()}
    grants={};receipts={};revisions=[]
    for name in names[ADMISSIONS]:
        raw=controls[ADMISSIONS+'/'+name];row=_json(raw)
        if name.endswith('.local-retired.json'):
            if (set(row)!={'version','grant','admission_sha256','local_resources_drained','external_account_offline'}
                    or not valid(row['grant'],H32) or not valid(row['admission_sha256'])
                    or row['local_resources_drained'] is not True or row['external_account_offline'] is not False):
                raise ValueError('local terminal data invalid; never external offline')
            receipts[name[:64]]=row
        else:
            if (set(row)!={'version','owner','account','bot','revision','store'} or not valid(row['owner'],H32)
                    or not all(valid(row[k]) for k in ('account','bot','revision','store'))
                    or row['bot'] not in by_bot or row['store']!=by_bot[row['bot']]):
                raise ValueError('account admission historical binding mismatch')
            if name.startswith('revision-'):revisions.append(row)
            elif name!=row['account']+'.json':raise ValueError('account reservation filename mismatch')
            else:grants[row['account']]=(row,raw)
    if set(grants)!=set(receipts):raise ValueError('active or orphan account admission; not locally terminal')
    for account,(row,raw) in grants.items():
        receipt=receipts[account]
        if receipt['grant']!=row['owner'] or receipt['admission_sha256']!=sha(raw):
            raise ValueError('local terminal record does not bind original admission')
    for row in revisions:
        original=grants.get(row['account'])
        if original is None or any(row[k]!=original[0][k] for k in ('owner','account','bot','store')):
            raise ValueError('revision history lacks original reserved account')
    scope=normalized_digest(root/'core/data');allowed_stores={normalized_digest(root/p) for p in paths.values()}
    for name in names[RESERVATIONS]:
        row=_json(controls[RESERVATIONS+'/'+name],512)
        if (set(row)!={'version','scope','account','store'} or not all(valid(row[k]) for k in ('scope','account','store'))
                or row['scope']!=scope or row['store'] not in allowed_stores or name!=row['account']+'.json'):
            raise ValueError('configured-account reservation binding mismatch')
    # NOTHING authorizes a new HOST/account. All files remain original denial data.


def capture_accounts(root,bindings,baseline):
    ns={}
    # Name classification precedes all body reads. Active custody/work/revocations
    # and unknown records fail before opening any account/resource payload.
    for relative in sorted(ACCOUNT_NAMES):ns[relative]=namespace_pin(root,relative)
    names=_names(ns);controls={}
    for folder in (ADMISSIONS,RESERVATIONS):
        for name in names[folder]:
            relative=folder+'/'+name;p=safe_path(root,root/relative);i=p.lstat()
            if not stat.S_ISREG(i.st_mode) or i.st_nlink!=1:raise ValueError('single-link account metadata required')
            with p.open('rb') as f:raw=f.read(1025);opened=os.fstat(f.fileno())
            if (len(raw)>1024 or opened.st_nlink!=1 or not stat.S_ISREG(opened.st_mode)
                    or (i.st_dev,i.st_ino)!=(opened.st_dev,opened.st_ino)):
                raise ValueError('account history changed or oversized')
            controls[relative]=raw
    derive_accounts(root,controls,bindings,baseline,ns)
    return controls,ns


def check_account_scope(root,namespaces):
    """Legacy/default transactions never silently ignore late account resources."""
    if not ACCOUNT_NAMES.intersection(namespaces):
        for relative in sorted(ACCOUNT_NAMES):
            if namespace_pin(root,relative)!=('namespace-absent',):
                raise ValueError('account resources require explicit historical adapter')
