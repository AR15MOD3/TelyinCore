"""Read-only filesystem/host evidence adapter requiring an explicit host source.

Local companion/controller wiring does not prove a complete cross-Bot inventory.
This module does not install that global source; the caller must supply and verify it.

capture(core) MUST hold an authoritative host registry READ LOCK for its entire
context, including native probes. Wrapping client JSON in a contextmanager is NOT
an authority. Account receipts must originate in a verified read-only OneBot reader.
No network calls, command-line/memory reads, process enumeration, start/stop,
configuration writes, history/persona reads or migration apply are implemented.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import time

from bot_host_observer import probe_process
from bot_joint_offline import _control
from bot_plugin_ownership import _account
from bot_registry import bot_id
from runtime_identity import identity_lock, safe_path


def _encoded(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _raw(root,path):
    path=safe_path(root,path)
    with path.open('rb') as stream: raw=stream.read(65537)
    if len(raw)>65536: raise ValueError('bounded host control limit')
    return raw


def _entries(root,path):
    path=safe_path(root,path)
    if not path.exists(): return []
    if not path.is_dir(): raise ValueError('host scope directory required')
    items=[]
    for child in path.iterdir():
        safe_path(root,child);items.append(child)
        if len(items)>4096: raise ValueError('host directory entry limit')
    return sorted(items)


def _scope(core):
    root=core.parent;base=core/'data';bots=root/'bots'; hashes={}
    def control(path):
        raw=_raw(root,path);hashes[path.relative_to(root).as_posix()]=_sha(raw)
        data=_control(root,path)
        if _sha(_raw(root,path))!=_sha(raw): raise ValueError('host control changed during read')
        return data
    baseline=bot_id(control(base/'bot-runtime-owner.json').get('baseline'))
    ledger_path=safe_path(root,base/'bot-runtime-bindings.json')
    ledger=control(ledger_path) if ledger_path.exists() else {'version':1,'bindings':{}}
    if type(ledger.get('version')) is not int or ledger['version']!=1 or not isinstance(ledger.get('bindings'),dict):
        raise ValueError('invalid host bindings')
    names={baseline};tokens=set();folded=set()
    for name,token in ledger['bindings'].items():
        bot_id(name)
        if (name.casefold() in folded or not isinstance(token,str) or not re.fullmatch('[0-9a-f]{32}',token)
                or token in tokens): raise ValueError('ambiguous host bindings')
        names.add(name);tokens.add(token);folded.add(name.casefold())
    roots={base}
    for path in _entries(root,base/'bot-runtimes'):
        if not path.is_dir(): raise ValueError('unclassified host runtime')
        names.add(bot_id(path.name));roots.add(path)
    records={}
    for path in _entries(root,base/'instances'):
        if not path.is_file() or path.suffix!='.json': raise ValueError('unclassified host instance')
        name=bot_id(path.stem);record=control(path)
        if type(record.get('pid')) is not int or not 0<record['pid']<0xFFFFFFFF:
            raise ValueError('invalid host instance PID')
        names.add(name);records[name]=record
    for path in _entries(root,bots):
        if path.name=='.deleted' or path.name.startswith('.bot-init-'): continue
        if path.is_dir(): names.add(bot_id(path.name))
    if len(names)>1024 or len({name.casefold() for name in names})!=len(names):
        raise ValueError('ambiguous or oversized host scope')
    return sorted(names),baseline,roots,records,hashes


def _resources(core,names,baseline):
    root=core.parent;rows=[];configs={};fingerprint={};visits=0
    def walk(path,plugin,owner,scoped=False,forced=None,depth=0):
        nonlocal visits
        visits+=1
        if visits>4096 or depth>12: raise ValueError('host resource traversal limit')
        path=safe_path(root,path)
        if not path.exists(): return
        relative=path.relative_to(root).as_posix();stat=path.stat()
        # Metadata ONLY for blocked/unknown resources. Do not read media, logs,
        # package code, private history or unrelated binary contents.
        signature=_sha(_encoded(['metadata-only-not-content',relative,stat.st_mode,
                                stat.st_size,stat.st_mtime_ns,stat.st_dev,stat.st_ino]))
        fingerprint[relative]=signature
        if path.is_dir():
            children=_entries(root,path)
            if children:
                for child in children: walk(child,plugin,owner,scoped,forced,depth+1)
                return
            kind=forced or 'unknown-resource'
        elif path.is_file():
            expected=('bots/'+owner+'/infinity/qq/config.json' if scoped
                      else 'core/extensions/core-infinity-qq/config.json')
            if plugin=='core-infinity-qq' and relative==expected and forced is None:
                raw=_raw(root,path);data=_control(root,path)
                if _sha(_raw(root,path))!=_sha(raw): raise ValueError('host config changed during read')
                declared=data.get('ownerBot')
                if declared is None or declared=='': declared=owner
                if not isinstance(declared,str) or declared not in names:
                    raise ValueError('unresolved host config owner')
                rows.append({'path':relative,'plugin':plugin,'kind':'qq-config','owner':declared,
                             'account':None,'sha256':_sha(raw)})
                configs[relative]=rows[-1];fingerprint[relative]=_sha(raw)
                return
            kind=forced or ('process-record' if '/processes/' in relative else
                            'voice-corpus' if '/voice' in relative else 'unknown-resource')
        else: raise ValueError('unsupported host resource entry')
        rows.append({'path':relative,'plugin':plugin,'kind':kind,'owner':owner,
                     'account':None,'sha256':signature})
    for package in _entries(root,core/'extensions'):
        walk(package,package.name,baseline)
    for name in names:
        walk(root/'bots'/name/'infinity','core-infinity-qq',name,True)
    walk(core/'data/infinity-processes','core-infinity-qq',baseline,forced='process-record')
    walk(core/'data/sharing-preview-test.json','core-infinity-qq',baseline,forced='sharing-preview')
    return sorted(rows,key=lambda row:row['path']),configs,fingerprint


def _identity(row):
    if (not isinstance(row,dict) or set(row)!={'pid','created_filetime','image'}
            or type(row['pid']) is not int or not 0<row['pid']<0xFFFFFFFF
            or type(row['created_filetime']) is not int or row['created_filetime']<=0
            or not isinstance(row['image'],str) or not Path(row['image']).is_absolute()):
        raise ValueError('invalid native host identity')
    return dict(row,image=os.path.normcase(os.path.normpath(row['image'])))


class ReadonlyHostInventory:
    def __init__(self,*,capture,probe=probe_process,clock_ns=time.monotonic_ns,max_age_seconds=60):
        if (not callable(capture) or not callable(probe) or not callable(clock_ns)
                or type(max_age_seconds) is not int or not 1<=max_age_seconds<=60):
            raise ValueError('trusted host read-context/probe/clock required')
        self.capture,self.probe,self.clock=capture,probe,clock_ns
        self.max_age_ns=max_age_seconds*1_000_000_000

    def _collect(self,core,source):
        names,baseline,roots,records,scope_hashes=_scope(core)
        if (not isinstance(source,dict) or set(source)!={'version','core','complete','generation','bots','baseline','processes','accounts'}
                or type(source['version']) is not int or source['version']!=1 or source['complete'] is not True
                or source['core']!=str(core.resolve()) or not isinstance(source['generation'],str)
                or not 1<=len(source['generation'])<=64 or source['bots']!=names or source['baseline']!=baseline
                or not isinstance(source['processes'],list) or len(source['processes'])>1024
                or not isinstance(source['accounts'],list) or len(source['accounts'])>4096):
            raise ValueError('host source scope incomplete')
        roster={};observations={}
        def observe(pid):
            seen=self.probe(pid)
            if (not isinstance(seen,dict) or type(seen.get('pid')) is not int or seen['pid']!=pid
                    or seen.get('state') not in ('alive','absent')):
                raise ValueError('native host process observation unresolved')
            normalized={'pid':pid,'state':seen['state']}
            if seen['state']=='alive':
                normalized.update(_identity({key:seen.get(key) for key in ('pid','created_filetime','image')}))
            return normalized
        def first(pid):
            if pid not in observations: observations[pid]=observe(pid)
            return observations[pid]
        for row in source['processes']:
            if not isinstance(row,dict) or set(row)!={'pid','bot','created_filetime','image'} or row['bot'] not in names:
                raise ValueError('invalid host process roster')
            expected=_identity({key:row[key] for key in ('pid','created_filetime','image')})
            pid=expected['pid']
            if pid in roster: raise ValueError('duplicate host process PID')
            seen=first(pid)
            if seen['state']=='alive' and {k:seen[k] for k in expected}!=expected:
                raise ValueError('host process identity reused or changed')
            roster[pid]=row
        for name,record in records.items():
            seen=first(record['pid'])
            if seen['state']=='alive' and (record['pid'] not in roster or roster[record['pid']]['bot']!=name):
                raise ValueError('live instance missing from authoritative roster')
        rows,configs,resource_hashes=_resources(core,names,baseline)
        covered=set();now=self.clock()
        if type(now) is not int or now<=0: raise ValueError('invalid host monotonic clock')
        for claim in source['accounts']:
            fields={'path','config_sha256','bot','target_account','observed_account','source','generation',
                    'sampled_at_ns','endpoint_verified','endpoint_state','endpoint'}
            if (not isinstance(claim,dict) or set(claim)!=fields or not isinstance(claim['path'],str)
                    or claim['path'] not in configs or claim['path'] in covered
                    or claim['source']!='get_login_info' or claim['generation']!=source['generation']
                    or claim['endpoint_verified'] is not True or claim['endpoint_state'] not in ('active','retired')
                    or type(claim['sampled_at_ns']) is not int or not 0<claim['sampled_at_ns']<=now
                    or now-claim['sampled_at_ns']>self.max_age_ns):
                raise ValueError('account evidence missing, stale or untrusted')
            row=configs[claim['path']]
            if row['owner']!=claim['bot'] or row['sha256']!=claim['config_sha256']:
                raise ValueError('account evidence bound to different config')
            account=_account(claim['observed_account'])
            if account!=_account(claim['target_account']): raise ValueError('account login identity mismatch')
            expected=_identity(claim['endpoint']);pid=expected['pid'];seen=first(pid)
            if claim['endpoint_state']=='active':
                if (seen['state']!='alive' or {key:seen.get(key) for key in expected}!=expected
                        or pid not in roster or roster[pid]['bot']!=row['owner']):
                    raise ValueError('active account endpoint provenance mismatch')
                if _identity({k:roster[pid][k] for k in expected})!=expected:
                    raise ValueError('account endpoint roster mismatch')
            elif seen['state']!='absent' or pid in roster:
                raise ValueError('retired account endpoint not independently absent')
            row['account']=account;covered.add(claim['path'])
        if covered!=set(configs): raise ValueError('unwitnessed QQ config; never infer an account')
        # A host read lock cannot stop native process exit/reuse or noncooperating
        # filesystem writes. Observe all native identities and disk metadata again.
        for pid,first_seen in observations.items():
            if observe(pid)!=first_seen: raise ValueError('native identity changed during capture')
        later=self.clock()
        if type(later) is not int or later<now: raise ValueError('host clock changed')
        if any(later-c['sampled_at_ns']>self.max_age_ns for c in source['accounts']):
            raise ValueError('account evidence expired during capture')
        after_scope=_scope(core)
        if (after_scope[:4]!=(names,baseline,roots,records) or after_scope[4]!=scope_hashes
                or _resources(core,names,baseline)[2]!=resource_hashes):
            raise ValueError('host filesystem scope or resources changed during capture')
        common={'core':str(core.resolve()),'complete':True,'generation':source['generation']}
        return dict(common,version=1,bots=names,baseline=baseline,
                    plugins=dict(common,version=1,bots=names,baseline=baseline,resources=rows),
                    processes=dict(common,processes=copy.deepcopy(source['processes'])))

    def __call__(self,core):
        """Internal consumer API. Caller holds global identity lock; output PRIVATE.

Accounts are returned only to the ownership checker, never logged or persisted.
Use inspect() for a redacted, non-authorizing user-facing result.
"""
        try:
            core=Path(core).absolute();safe_path(core.parent,core)
            if not (core/'data').is_dir(): raise ValueError()
            with self.capture(core.resolve()) as live_source:
                source=copy.deepcopy(live_source)
                result=self._collect(core,source)
                if _encoded(live_source)!=_encoded(source): raise ValueError('host registry changed while read-locked')
                return result
        except Exception:
            raise ValueError('read-only host evidence incomplete, invalid or changed') from None

    def inspect(self,core):
        """Redacted standalone read; only cooperative identity-lock housekeeping."""
        core=Path(core).absolute()
        if not (core/'data').is_dir(): raise ValueError('host deployment missing')
        with identity_lock(core/'data'):
            result=self(core)
        rows=result['plugins']['resources']
        accounts=[row['account'] for row in rows if row['kind']=='qq-config']
        return {'version':1,'bots':result['bots'],'resource_count':len(rows),
                'resources':[{k:row[k] for k in ('path','plugin','kind','owner')} for row in rows],
                'duplicate_account_claim':len(accounts)!=len(set(accounts)),
                'unadapted_resource_count':sum(row['kind']!='qq-config' or row['plugin']!='core-infinity-qq' for row in rows),
                'process_claim_count':len(result['processes']['processes']),
                'offline_authorization':False,'production_apply_supported':False}
