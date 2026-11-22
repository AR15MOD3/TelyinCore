"""Fenced product control-transaction core, separate from generated fixtures.

The internal engine implements known acknowledged stages and rollback. Interrupted
effects/receipts stay fenced and are NOT inferred from matching bytes. No forced
recovery/unfence, process operations, payload copy, account reads or network.
Public entry consumes the actual registry, and refuses until authoritative
production entry coverage is connected. Internal core tests are NOT that proof.
"""
from contextlib import nullcontext
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from bot_rename_contexts import (control_limit, CONTROL_BYTES, MANIFEST_BYTES, witnesses,
                                 encoded_witnesses, restore_opaque, context_operations, check_payloads)
from bot_rename_plugins import derive_plugins, restore_namespaces, config_path, check_namespaces
from bot_rename_accounts import ACCOUNT_NAMES, control_keys, derive_accounts, check_account_scope
from bot_rename_recovery import (derive_recovery, restore as restore_recovery, control_keys as recovery_control_keys,
                                 payload_paths, is_namespace, check_scope as check_recovery_scope)
from bot_private_file import open_private_new
from bot_registry import bot_id
from bot_rename_projection import (IdentityProjection, IdentityOperation, IdentityPhase,
                                   _json, _encoded, _pin, project_identity,
                                   INSTANCE_ROOT, _instance_paths, _validate_instance_controls,
                                   BINDING_BACKUP, _validate_binding_backup)
from bot_migration_gate import GATE, RETIRED
from runtime_identity import LEDGER, MARKER, identity_lock, safe_path


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _private(path, raw):
    with open_private_new(path) as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        info = os.fstat(stream.fileno())
    return (info.st_dev, info.st_ino)


def _read(root, path, limit=1024*1024):
    path = safe_path(root, path)
    with path.open('rb') as stream:
        raw = stream.read(limit+1); info = os.fstat(stream.fileno())
    if len(raw)>limit or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
        raise ValueError('bounded single-link transaction record required')
    return raw, (info.st_dev, info.st_ino)


def _derive(old, new, controls, *, instances=False, root=None, contexts=None, opaque=None, plugins=None, accounts=None, recovery=None):
    """Regenerate legal operations from fixed before-images, not journal commands."""
    namespaces=restore_namespaces({} if plugins is None else plugins)
    account_namespaces=restore_namespaces({} if accounts is None else accounts)
    recovery_namespaces=restore_recovery({} if recovery is None else recovery)
    contexts=witnesses(contexts);opaque=restore_opaque({} if opaque is None else opaque)
    old,new=bot_id(old),bot_id(new)
    if old.casefold()==new.casefold() or old.startswith('.') or new.startswith('.'):
        raise ValueError('distinct transaction identities required')
    required={'core/data/'+LEDGER,'core/data/bot-runtime-owner.json','bots/'+old+'/'+MARKER}
    allowed=required|{'core/data/'+RETIRED,'core/data/active-bot.json',BINDING_BACKUP}
    instance_keys={p for p in controls if p.startswith(INSTANCE_ROOT+'/')}
    context_keys={p for p in controls if '/provider-state/contexts/' in p}
    plugin_keys={p for p in controls if p=='core/extensions/core-infinity-qq/config.json' or re.fullmatch(r'bots/[^/]+/infinity/qq/config\.json',p)}
    account_keys=control_keys(controls)
    recovery_keys=recovery_control_keys(controls)
    if not required<=set(controls)<=allowed|instance_keys|context_keys|plugin_keys|account_keys|recovery_keys:
        raise ValueError('unclassified transaction controls')
    rows=_json(controls['core/data/'+LEDGER]);owner=_json(controls['core/data/bot-runtime-owner.json'])
    if (set(rows)!={'version','bindings'} or type(rows['version']) is not int or rows['version']!=1
            or type(rows['bindings']) is not dict or set(owner)!={'baseline'}):
        raise ValueError('invalid transaction before-state')
    baseline=bot_id(owner['baseline']);tokens=set();folded=set()
    for name,token in rows['bindings'].items():
        bot_id(name)
        if (name.casefold() in folded or type(token) is not str
                or re.fullmatch('[0-9a-f]{32}',token) is None or token in tokens):
            raise ValueError('invalid transaction binding')
        folded.add(name.casefold());tokens.add(token)
    if baseline not in rows['bindings'] or old not in rows['bindings'] or new.casefold() in folded:
        raise ValueError('transaction identities unbound or conflicting')
    if type(instances) is not bool or instance_keys and not instances:
        raise ValueError('instance scope missing')
    _validate_instance_controls(controls,rows['bindings'])
    if BINDING_BACKUP in controls:
        _validate_binding_backup(controls[BINDING_BACKUP],rows['bindings'],new)
    marker=_json(controls['bots/'+old+'/'+MARKER])
    if marker!={'name':old,'token':rows['bindings'][old]}:
        raise ValueError('transaction marker mismatch')
    names=[]
    if 'core/data/'+RETIRED in controls:
        retired=_json(controls['core/data/'+RETIRED])
        if (set(retired)!={'version','names'} or type(retired['version']) is not int
                or retired['version']!=1 or type(retired['names']) is not list or len(retired['names'])>1024):
            raise ValueError('invalid retired-name before-state')
        names=[bot_id(n) for n in retired['names']]
        if len({n.casefold() for n in names})!=len(names) or {old.casefold(),new.casefold()}&{n.casefold() for n in names}:
            raise ValueError('retired identity conflict')
    runtime='core/data' if baseline==old else 'core/data/bot-runtimes/'+old
    target=runtime if baseline==old else 'core/data/bot-runtimes/'+new
    if root is None and (contexts is not None or opaque or context_keys):
        raise ValueError('context deployment root required')
    if any(not p.startswith(runtime+'/provider-state/contexts/') for p in context_keys):
        raise ValueError('context outside original runtime')
    recovery_payloads={p:v for p,v in opaque.items() if p in payload_paths(rows['bindings'],baseline)}
    context_moves=context_operations(root,runtime,target,controls,contexts,{p:v for p,v in opaque.items() if p not in recovery_payloads})
    if plugin_keys-{config_path(n,baseline) for n in rows['bindings']}:
        raise ValueError('plugin config outside original bound layout')
    plugin_changes=derive_plugins(controls,rows['bindings'],baseline,old,new,namespaces,recovery=bool(recovery_namespaces))
    if account_namespaces and root is None:raise ValueError('account history root required')
    derive_accounts(root,controls,rows['bindings'],baseline,account_namespaces)
    namespaces.update(account_namespaces)
    if recovery_namespaces and root is None:raise ValueError('original recovery deployment root required')
    derive_recovery(root,controls,rows['bindings'],baseline,recovery_namespaces,recovery_payloads)
    namespaces.update(recovery_namespaces)
    files=dict(controls);roots={'bots/'+old,runtime};operations=[]
    if instances:roots.add(INSTANCE_ROOT)
    phases=[IdentityPhase(tuple(sorted(roots)),tuple(sorted(files.items())),tuple(sorted(opaque.items())),tuple(sorted(namespaces.items())))]
    def move(a,b):
        operations.append(IdentityOperation('move',a,b))
        for key in list(files):
            if key==a or key.startswith(a+'/'):files[b+key[len(a):]]=files.pop(key)
        for key in list(opaque):
            if key==a or key.startswith(a+'/'):opaque[b+key[len(a):]]=opaque.pop(key)
        for key in list(namespaces):
            if key==a or key.startswith(a+'/'):namespaces[b+key[len(a):]]=namespaces.pop(key)
        roots.remove(a);roots.add(b)
        phases.append(IdentityPhase(tuple(sorted(roots)),tuple(sorted(files.items())),tuple(sorted(opaque.items())),tuple(sorted(namespaces.items()))))
    def write(path,value):
        raw=_encoded(value);operations.append(IdentityOperation('write',path,before=files.get(path),after=raw))
        files[path]=raw;phases.append(IdentityPhase(tuple(sorted(roots)),tuple(sorted(files.items())),tuple(sorted(opaque.items())),tuple(sorted(namespaces.items()))))
    move('bots/'+old,'bots/'+new)
    if target!=runtime:move(runtime,target)
    for a,b in context_moves:
        operations.append(IdentityOperation('rekey',a,b));files[b]=files.pop(a)
        phases.append(IdentityPhase(tuple(sorted(roots)),tuple(sorted(files.items())),tuple(sorted(opaque.items())),tuple(sorted(namespaces.items()))))
    old_instance=INSTANCE_ROOT+'/'+old+'.json' 
    if old_instance in files:
        new_instance=INSTANCE_ROOT+'/'+new+'.json'
        operations.append(IdentityOperation('rekey',old_instance,new_instance))
        files[new_instance]=files.pop(old_instance)
        phases.append(IdentityPhase(tuple(sorted(roots)),tuple(sorted(files.items())),tuple(sorted(opaque.items())),tuple(sorted(namespaces.items()))))
    for path,before,after in plugin_changes:
        operations.append(IdentityOperation('write',path,before=before,after=after));files[path]=after
        phases.append(IdentityPhase(tuple(sorted(roots)),tuple(sorted(files.items())),tuple(sorted(opaque.items())),tuple(sorted(namespaces.items()))))
    if baseline==old:write('core/data/bot-runtime-owner.json',{'baseline':new})
    if 'core/data/active-bot.json' in controls:
        selected=_json(controls['core/data/active-bot.json'])
        if (set(selected)!={'baseline','active'} or selected['baseline']!=baseline or selected['active'] not in rows['bindings']):
            raise ValueError('unsupported legacy before-state')
        if old in selected.values():write('core/data/active-bot.json',{k:new if v==old else v for k,v in selected.items()})
    updated=dict(rows['bindings']);updated[new]=updated.pop(old)
    write('core/data/'+LEDGER,{'version':1,'bindings':updated})
    write('bots/'+new+'/'+MARKER,{'name':new,'token':marker['token']})
    write('core/data/'+RETIRED,{'version':1,'names':sorted(set(names)|{old})})
    return tuple(operations),tuple(phases)


class _ControlledTransaction:
    """Trusted coordinator primitive; NOT a public authority or apply capability."""
    @classmethod
    def _prepare_locked(cls, projection):
        if type(projection) is not IdentityProjection or projection.adapters_required:
            raise ValueError('product phase adapters remain required')
        projection.assert_source_current()
        core=Path(projection.core);base=core/'data';root=core.parent
        operations,phases=_derive(projection.old,projection.new,dict(projection.phases[0].files),
                                  instances=INSTANCE_ROOT in projection.phases[0].roots,root=root,
                                  contexts=projection.context_witnesses,opaque=dict(projection.phases[0].opaque),plugins={k:v for k,v in projection.phases[0].namespaces if k not in ACCOUNT_NAMES and not is_namespace(k)},
                                  accounts={k:v for k,v in projection.phases[0].namespaces if k in ACCOUNT_NAMES},
                                  recovery={k:v for k,v in projection.phases[0].namespaces if is_namespace(k)})
        if (operations,phases)!=(projection.operations,projection.phases):
            raise ValueError('projection differs from canonical transaction')
        token=uuid.uuid4().hex
        home=safe_path(root,base/'bot-rename-transactions'/token)
        dirs={p:list(_pin(root,p,read=False)[0][1:]) for p in phases[0].roots}
        files={p:list(_pin(root,p)[0][1:3]) for p,raw in phases[0].files}
        manifest=dict(version=5 if projection.recovery_stores else 4 if projection.account_history else 3 if projection.plugin_configs else 2 if projection.context_witnesses is not None else 1,core=str(core),transaction=token,old=projection.old,new=projection.new,
                      controls={p:base64.b64encode(raw).decode('ascii') for p,raw in phases[0].files},
                      roots=dirs,files=files)
        if manifest['version']>=2:
            manifest.update(contexts=encoded_witnesses(projection.context_witnesses),opaque=dict(phases[0].opaque))
        if manifest['version']>=3:manifest['plugins']={k:v for k,v in phases[0].namespaces if k not in ACCOUNT_NAMES and not is_namespace(k)}
        if manifest['version']>=4:manifest['accounts']={k:v for k,v in phases[0].namespaces if k in ACCOUNT_NAMES}
        if manifest['version']==5:manifest['recovery']={k:v for k,v in phases[0].namespaces if is_namespace(k)}
        raw=_encoded(manifest)
        if len(raw)>MANIFEST_BYTES:raise ValueError('bounded transaction manifest required')
        home.mkdir(parents=True,exist_ok=False)
        manifest_inode=_private(home/'manifest.json',raw)
        gate=_encoded(dict(version=1,transaction=token,state='fenced',manifest_sha256=_sha(raw),manifest_inode=list(manifest_inode)))
        # No effect precedes durable intent. A partial gate remains denial.
        gate_inode=_private(safe_path(root,base/GATE),gate)
        projection.assert_phase_current(0)
        cls._append(home,0,dict(version=1,manifest=_sha(raw),previous=None,cursor=0,
                              pending=None,files=files,state='prepared',gate=list(gate_inode)))
        return cls.open(core,token)

    @staticmethod
    def _append(home,index,row):
        return _private(home/(f'{index:04d}.json'),_encoded(row))

    @classmethod
    def open(cls,core,token):
        """Reopen acknowledged stages only; partial intent is not adopted/retried."""
        if type(token) is not str or re.fullmatch('[0-9a-f]{32}',token) is None:
            raise ValueError('invalid transaction id')
        self=cls();self.core=Path(core).absolute();self.base=self.core/'data';self.root=self.core.parent
        self.home=safe_path(self.root,self.base/'bot-rename-transactions'/token)
        self.manifest_raw,self.manifest_inode= _read(self.root,self.home/'manifest.json',MANIFEST_BYTES)
        self.manifest=_json(self.manifest_raw);m=self.manifest
        fields={'version','core','transaction','old','new','controls','roots','files'}
        if m.get('version') in (2,3,4,5):fields|={'contexts','opaque'}
        if m.get('version') in (3,4,5):fields|={'plugins'}
        if m.get('version') in (4,5):fields|={'accounts'}
        if m.get('version')==5:fields|={'recovery'}
        if (set(m)!=fields
                or type(m['version']) is not int or m['version'] not in (1,2,3,4,5) or m['core']!=str(self.core)
                or m['transaction']!=token or type(m['controls']) is not dict or len(m['controls'])>70662
                or type(m['roots']) is not dict):
            raise ValueError('invalid fixed transaction manifest')
        controls={p:base64.b64decode(raw,validate=True) for p,raw in m['controls'].items()}
        if any(len(raw)>control_limit(p) for p,raw in controls.items()) or sum(map(len,controls.values()))>CONTROL_BYTES:
            raise ValueError('oversized control before-image')
        self.operations,self.phases=_derive(m['old'],m['new'],controls,instances=INSTANCE_ROOT in m['roots'],root=self.root,
                                                       contexts=m.get('contexts'),opaque=m.get('opaque'),plugins=m.get('plugins'),accounts=m.get('accounts'),recovery=m.get('recovery'))
        self.history_limit=128 if m['version']==1 else min(8192,1+4*len(self.operations))
        self.gate_raw=_encoded(dict(version=1,transaction=token,state='fenced',manifest_sha256=_sha(self.manifest_raw),manifest_inode=list(self.manifest_inode)))
        self._check_gate()
        if set(m['roots'])!=set(self.phases[0].roots) or set(m['files'])!=set(controls):
            raise ValueError('manifest inode scope mismatch')
        self._reload()
        return self

    def _check_gate(self):
        gate,gate_inode=_read(self.root,self.base/GATE,65536)
        if gate!=self.gate_raw or (hasattr(self,'gate_inode') and list(gate_inode)!=self.gate_inode):
            raise ValueError('transaction gate changed; no recovery')
        if _read(self.root,self.home/'manifest.json',MANIFEST_BYTES)!=(self.manifest_raw,self.manifest_inode):
            raise ValueError('transaction manifest changed')

    def _reload(self):
        self._check_gate()
        for path,raw,inode in getattr(self,'records',[]):
            if _read(self.root,path)!=(raw,inode):raise ValueError('held phase receipt replaced')
        previous=None;last=None;self.records=[]
        paths=sorted(p for p in self.home.iterdir() if p.name!='manifest.json')
        if not 1<=len(paths)<=self.history_limit:raise ValueError('bounded nonempty phase history required')
        for index,path in enumerate(paths):
            if path.name!=f'{index:04d}.json':raise ValueError('partial or foreign phase record')
            raw,inode=_read(self.root,path);row=_json(raw)
            if (set(row)!={'version','manifest','previous','cursor','pending','files','state','gate'}
                    or type(row['version']) is not int or row['version']!=1
                    or row['manifest']!=_sha(self.manifest_raw) or row['previous']!=previous
                    or type(row['cursor']) is not int or not 0<=row['cursor']<len(self.phases)
                    or type(row['files']) is not dict
                    or set(row['files'])!={p for p,raw in self.phases[row['cursor']].files}):
                raise ValueError('invalid chained phase receipt')
            if index==0:
                self.gate_inode=row['gate'];self._check_gate()
            elif row['gate']!=self.gate_inode:
                raise ValueError('phase gate identity changed')
            if row['pending'] is not None:
                if row['pending'] not in ('apply','rollback') or row['state']!='intent':
                    raise ValueError('invalid pending transaction intent')
            elif row['state'] not in ('prepared','acknowledged'):
                raise ValueError('invalid acknowledged transaction phase')
            if index==0:
                if row['cursor']!=0 or row['pending'] is not None or row['state']!='prepared' or row['files']!=self.manifest['files']:
                    raise ValueError('invalid initial phase receipt')
            elif last['pending'] is None:
                if row['pending'] is None or row['cursor']!=last['cursor'] or row['files']!=last['files']:
                    raise ValueError('phase intent does not follow acknowledged state')
            else:
                wanted=last['cursor']+(1 if last['pending']=='apply' else -1)
                if row['pending'] is not None or row['cursor']!=wanted:
                    raise ValueError('phase acknowledgment does not match intent')
            self.records.append((path,raw,inode));previous=_sha(raw);last=row
        if last['pending'] is not None:
            raise ValueError('phase effect or receipt unresolved; gate retained, no blind replay')
        self.row=last;self._checkpoint(last['cursor'],last['files'])

    def _checkpoint(self,cursor,pins):
        self._check_gate();phase=self.phases[cursor]
        roots=dict(self.manifest['roots'])
        for op in self.operations[:cursor]:
            if op.kind=='move':roots[op.destination]=roots.pop(op.path)
        known={p for phase in self.phases for p in phase.roots}
        for relative in known:
            actual=_pin(self.root,relative,read=False)[0]
            if relative in roots:
                if actual!=('directory',*roots[relative]):raise ValueError('native directory identity changed')
            elif actual!=('absent',):raise ValueError('foreign old/destination directory present')
        observed=_instance_paths(self.root)
        wanted=tuple(sorted(p for p,raw in phase.files if p.startswith(INSTANCE_ROOT+'/')))
        if ((INSTANCE_ROOT in phase.roots)!=(observed is not None)
                or observed is not None and observed!=wanted):
            raise ValueError('phase instance roster changed')
        check_recovery_scope(self.root,dict(phase.namespaces),dict(self.phases[0].files))
        check_account_scope(self.root,dict(phase.namespaces))
        check_namespaces(self.root,dict(phase.namespaces),{p for phase in self.phases for p,_ in phase.namespaces})
        check_payloads(self.root,dict(phase.files),dict(phase.opaque))
        files=dict(phase.files);known={p for phase in self.phases for p,raw in phase.files}
        for relative in known:
            if relative not in files:
                if _pin(self.root,relative,read=False)[0]!=('absent',):raise ValueError('foreign control present')
            else:
                pin,raw=_pin(self.root,relative)
                if raw!=files[relative] or list(pin[1:3])!=pins[relative]:
                    raise ValueError('control bytes or inode changed')
        # No other writer may replace a previous acknowledgment or intent.
        for path,raw,inode in self.records:
            if _read(self.root,path)!=(raw,inode):raise ValueError('phase history changed concurrently')

    def _effect(self,op,reverse):
        path=safe_path(self.root,self.root/(op.destination if reverse and op.kind in ('move','rekey') else op.path))
        if op.kind in ('move','rekey'):
            destination=safe_path(self.root,self.root/(op.path if reverse else op.destination))
            if destination.exists():raise ValueError('move destination appeared')
            path.rename(destination)
            return None
        expected=op.after if reverse else op.before
        def current_target():
            if expected is None:
                if _pin(self.root,op.path,read=False)[0]!=('absent',):
                    raise ValueError('new control destination appeared')
            else:
                raw,info=_read(self.root,path,65536)
                if raw!=expected or list(info)!=self.row['files'][op.path]:
                    raise ValueError('control changed immediately before effect')
        current_target()
        raw=op.before if reverse else op.after
        if raw is None:
            path.unlink()  # only this receipt-bound newly created control
            return None
        temporary=safe_path(self.root,path.with_name('.rename-'+uuid.uuid4().hex))
        inode=_private(temporary,raw)
        # The exact temporary is retained on error. Never ordinary ACL fallback.
        actual,info=_read(self.root,temporary,65536)
        if actual!=raw or info!=inode:raise ValueError('private replacement changed')
        current_target()
        os.replace(temporary,path)
        return list(inode)

    def _step_locked(self,action,*,guard=None,effect_window=None):
        if action not in ('apply','rollback'):raise ValueError('invalid transaction action')
        self._reload();cursor=self.row['cursor'];delta=1 if action=='apply' else -1
        if guard is not None:guard(cursor)
        target=cursor+delta
        if not 0<=target<len(self.phases):return False
        if len(self.records)+2>self.history_limit:
            raise ValueError('phase history budget exhausted before intent; gate retained')
        op=self.operations[cursor if delta==1 else cursor-1]
        pins=dict(self.row['files'])
        intent=dict(self.row,previous=_sha(self.records[-1][1]),pending=action,state='intent')
        intent_inode=self._append(self.home,len(self.records),intent)
        # Recheck the exact acknowledged input before touching the deployment.
        self._checkpoint(cursor,pins)
        if _read(self.root,self.home/f'{len(self.records):04d}.json')!=(_encoded(intent),intent_inode):
            raise ValueError('intent replaced before effect')
        if guard is not None:guard(cursor)
        with (nullcontext() if effect_window is None else effect_window(op,delta<0,target)):
            new_inode=self._effect(op,delta<0)
        if op.kind in ('move','rekey'):
            a,b=(op.path,op.destination) if delta==1 else (op.destination,op.path)
            for key in list(pins):
                if key==a or key.startswith(a+'/'):pins[b+key[len(a):]]=pins.pop(key)
        elif new_inode is None:del pins[op.path]
        else:pins[op.path]=new_inode
        self._checkpoint(target,pins)
        raw,info=_read(self.root,self.home/f'{len(self.records):04d}.json')
        if raw!=_encoded(intent) or info!=intent_inode:raise ValueError('transaction intent changed')
        if guard is not None:guard(target)
        self._append(self.home,len(self.records)+1,dict(version=1,manifest=_sha(self.manifest_raw),
                     previous=_sha(raw),cursor=target,pending=None,files=pins,state='acknowledged',gate=self.row['gate']))
        self._reload()
        return True

    def step(self,action):
        with identity_lock(self.base):return self._step_locked(action)

    def status(self):
        with identity_lock(self.base):
            self._reload()
            return dict(cursor=self.row['cursor'],steps=len(self.operations),gate_retained=True,
                        production_apply_supported=False,global_complete=False)


def begin_rename(core,old,new,*,registry=None,creation=None,**scope):
    """Admission first: unsupported global authority cannot read private scopes."""
    if creation is not None:
        from bot_new_deployment import _begin_creation
        return _begin_creation(core,old,new,creation=creation,registry=registry,**scope)
    from bot_rename_coordinator import _begin
    return _begin(core,old,new,registry=registry,**scope)
