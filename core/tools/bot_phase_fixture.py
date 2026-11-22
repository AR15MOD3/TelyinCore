"""Signed-phase joint execution/recovery for NEW generated synthetic fixtures only.

Not a production migrator. Synthetic account/process witnesses do not establish
real host completeness. Test keys/before-images are not production secret storage.
No arbitrary deployment root, process kill or production unfence is exposed.
"""
import argparse
import base64
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil

from bot_host_observer import HostObserver, probe_process
from bot_joint_offline import _control
from bot_migration_gate import GATE, install_gate
from bot_offline_lease import _scopes, acquire_runtime_lock, release_runtime_lock
from bot_plugin_ownership import PluginOwnershipObserver, _account
from runtime_identity import identity_lock, safe_path
from tools.bot_adapter_fixture import AdapterFixture
from tools.bot_recovery_fixture import tree_state
from tools.bot_rename_fixture import atomic, json_bytes


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class PhaseFixture(AdapterFixture):
    EXECUTION_PROFILE = 'signed-phase-joint-fixture-v1'

    @classmethod
    def create(cls, *, baseline=True):
        f = super().create(baseline=baseline)
        if f.progress is not None:
            raise ValueError('phase progress already exists')
        f._write_progress('prepared', 0, None)
        return f

    def __init__(self, home):
        super().__init__(home)  # authenticates manifest; rejects non-generated roots
        if type(self.manifest['version']) is not int:
            raise ValueError('invalid phase manifest version')
        self.manifest_digest = digest((self.home/'recovery-manifest.json').read_bytes())
        if self._read('recovery-manifest.json') != self.manifest:
            raise ValueError('manifest changed while opening fixture')
        self.stages = self._derive_stages()
        path = safe_path(self.home, self.home/'phase-progress.json')
        self.progress_hash = digest(path.read_bytes()) if path.exists() else None
        self.progress = self._read('phase-progress.json') if path.exists() else None
        if self.progress is not None:
            self._validate_progress(self._prefix())

    def _derive_stages(self):
        """Derive, do not adopt, every legal transition from authenticated inputs."""
        backup = safe_path(self.home, self.home/'recovery-backup')
        before = self.manifest['before']
        without_locks = lambda state: {k:v for k,v in state.items() if v != 'cooperative-lock'}
        if without_locks(tree_state(backup)) != without_locks(before):
            raise ValueError('backup differs from signed before-state')
        files = {}
        total = 0
        for key, value in before.items():
            if value in ('directory', 'cooperative-lock'):
                continue
            path = safe_path(backup, backup/key)
            raw = path.read_bytes()
            total += len(raw)
            if total > 16*1024*1024 or digest(raw) != value:
                raise ValueError('bounded fixture backup verification failed')
            files[key] = raw
        stages = []
        for prefix, state in enumerate(self.expected):
            actual_files = {key:digest(raw) for key,raw in files.items()}
            if actual_files != {key:value for key,value in state.items()
                                if value not in ('directory', 'cooperative-lock')}:
                raise ValueError('operation images do not match signed state')
            stages.append(self._bundle(state, files, prefix))
            if prefix == len(self.manifest['operations']):
                break
            op = self.manifest['operations'][prefix]
            if op['kind'] in ('move','index'):
                for key in list(files):
                    if key == op['from'] or key.startswith(op['from']+'/'):
                        target = op['to'] + key[len(op['from']):]
                        if target in files:
                            raise ValueError('operation image collision')
                        files[target] = files.pop(key)
            else:
                raw_before = None if op['before'] is None else base64.b64decode(op['before'],validate=True)
                if files.get(op['path']) != raw_before:
                    raise ValueError('operation before-image inconsistent with signed prefix')
                files[op['path']] = base64.b64decode(op['after'],validate=True)
        return stages

    def _bundle(self, state, files, prefix):
        owner = json.loads(files['core/data/bot-runtime-owner.json'])['baseline']
        ledger = json.loads(files['core/data/bot-runtime-bindings.json'])['bindings']
        names = {owner, *ledger}
        roots = {'core/data'}
        records = {}
        for key, value in state.items():
            parts = key.split('/')
            if value == 'directory' and len(parts) == 2 and parts[0] == 'bots':
                names.add(parts[1])
            if value == 'directory' and len(parts) == 4 and parts[:3] == ['core','data','bot-runtimes']:
                names.add(parts[3]); roots.add(key)
            if len(parts) == 4 and parts[:3] == ['core','data','instances'] and key in files:
                if not parts[3].endswith('.json'):
                    raise ValueError('unclassified signed instance record')
                name = parts[3][:-5]; names.add(name); records[name] = json.loads(files[key])
        resources = []
        for path, raw in sorted(files.items()):
            if path == 'core/extensions/core-infinity-qq/config.json' or path.endswith('/infinity/qq/config.json'):
                logical_owner = json.loads(raw)['ownerBot']
                # Explicit invented fixture witnesses; NOT inference of real QQ
                # identity from ownerBot. Real host integration must not do this.
                account = {'One':'10001','Renamed':'10001','Other':'10002'}.get(logical_owner)
                resources.append({'path':path,'plugin':'core-infinity-qq','kind':'qq-config',
                                  'owner':logical_owner,'account':account,'sha256':digest(raw)})
        generation = 'fixture-host-' + self.manifest['transaction']
        common = {'core':str((self.root/'core').resolve()),'complete':True,'generation':generation}
        return {'version':1,'transaction':self.manifest['transaction'],'prefix':prefix,
                'tree_sha256':digest(json_bytes(state)), 'roots':sorted(roots),'records':records,
                'bots':sorted(names),'baseline':owner,
                'plugins':dict(common,version=1,bots=sorted(names),baseline=owner,resources=resources),
                'processes':dict(common,processes=[])}

    def fixture_inventory(self, core):
        """Fresh observation of this generated realm, not a production provider."""
        if Path(core).resolve() != (self.root/'core').resolve():
            raise ValueError('fixture host scope mismatch')
        # Independently observe plugin/process metadata, not an adopted expected
        # prefix. Whole-tree checks belong to the signed transaction boundary.
        names,roots,records = _scopes(self.base,self.bots)
        owner = _control(self.root,self.base/'bot-runtime-owner.json')['baseline']
        selected = ['core/extensions/core-infinity-qq/config.json'] + [
            'bots/'+name+'/infinity/qq/config.json' for name in ('One','Renamed','Other')]
        resources = []
        for key in selected:
            path = self._path(key)
            if not path.exists(): continue
            if path.stat().st_size > 65536:
                raise ValueError('oversized fixture control observation')
            raw = path.read_bytes()
            logical_owner = _control(self.root,path)['ownerBot']
            resources.append({'path':key,'plugin':'core-infinity-qq','kind':'qq-config','owner':logical_owner,
                'account':{'One':'10001','Renamed':'10001','Other':'10002'}.get(logical_owner),'sha256':digest(raw)})
        common = {'core':str((self.root/'core').resolve()),'complete':True,
                  'generation':'fixture-host-'+self.manifest['transaction']}
        return {'version':1,'transaction':self.manifest['transaction'],
                'roots':[p.relative_to(self.root).as_posix() for p in roots],'records':records,
                'bots':names,'baseline':owner,
                'plugins':dict(common,version=1,bots=names,baseline=owner,
                               resources=sorted(resources,key=lambda row:row['path'])),
                'processes':dict(common,processes=[])}

    def _validate_progress(self, prefix):
        p = self.progress
        count = len(self.manifest['operations'])
        if (not isinstance(p,dict) or set(p) != {'version','transaction','manifest_sha256','phase','cursor','pending'}
                or type(p['version']) is not int or p['version'] != 1
                or p['transaction'] != self.manifest['transaction'] or p['manifest_sha256'] != self.manifest_digest
                or type(p['cursor']) is not int or not 0 <= p['cursor'] <= count):
            raise ValueError('invalid authenticated phase progress')
        phase,cursor,pending = p['phase'],p['cursor'],p['pending']
        if phase in ('prepared','committed','rolled_back'):
            target = count if phase == 'committed' else 0
            good = pending is None and cursor == target and prefix == target
        elif phase in ('applying','rolling_back'):
            if pending is None:
                good = prefix == cursor
            else:
                delta = 1 if phase == 'applying' else -1
                wanted = cursor if delta == 1 else cursor-1
                good = (type(pending) is int and pending == wanted and 0 <= pending < count
                        and prefix in (cursor,cursor+delta))
        else:
            good = False
        if not good:
            raise ValueError('observed prefix is not acknowledged or pending signed phase')

    def _write_progress(self, phase, cursor, pending):
        path = safe_path(self.home,self.home/'phase-progress.json')
        if self.progress_hash is None:
            if path.exists(): raise ValueError('phase progress appeared concurrently')
        elif digest(path.read_bytes()) != self.progress_hash:
            raise ValueError('phase progress changed concurrently')
        body = {'version':1,'transaction':self.manifest['transaction'],'manifest_sha256':self.manifest_digest,
                'phase':phase,'cursor':cursor,'pending':pending}
        raw = json_bytes({'body':body,'mac':hmac.new(self.key,json_bytes(body),hashlib.sha256).hexdigest()})
        if path.exists():
            shutil.copy2(path,safe_path(self.home,self.home/'phase-progress.previous.json'))
        atomic(path,raw)
        self.progress,self.progress_hash = body,digest(raw)

    def _checkpoint(self, prefix, provider, host):
        self._own_gate()
        if digest((self.home/'recovery-manifest.json').read_bytes()) != self.manifest_digest:
            raise ValueError('signed manifest changed concurrently')
        if digest((self.home/'phase-progress.json').read_bytes()) != self.progress_hash:
            raise ValueError('phase progress changed concurrently')
        if self._prefix() != prefix:
            raise ValueError('unexpected phase; never adopt another prefix')
        stage = self.stages[prefix]
        expected = {key:value for key,value in stage.items() if key not in ('prefix','tree_sha256')}
        names,roots,records = _scopes(self.base,self.bots)
        if (names != expected['bots'] or [p.relative_to(self.root).as_posix() for p in roots] != expected['roots']
                or records != expected['records']):
            raise ValueError('signed phase scope mismatch')
        try:
            bundle = copy.deepcopy(provider(self.root/'core'))
        except Exception:
            raise ValueError('phase host inventory unavailable') from None
        if json_bytes(bundle) != json_bytes(expected):
            raise ValueError('host inventory differs from exact signed phase')
        # Intermediate owner/path mismatches are allowed ONLY as exact signed
        # prefixes while fenced. The ordinary ownership checker is not weakened.
        if prefix in (0,len(self.stages)-1):
            source = 'One' if prefix == 0 else 'Renamed'
            review = PluginOwnershipObserver(inventory=lambda _:bundle['plugins']).review(
                self.root/'core',names,source,'PhaseValidationOnly')
            if review['blockers']:
                raise ValueError('endpoint plugin policy blocked')
        accounts = [_account(row['account']) for row in bundle['plugins']['resources']]
        if len(accounts) != len(set(accounts)):
            raise ValueError('phase QQ account collision')
        host.roster = lambda _:bundle['processes']
        result = host(self.root/'core',names,records)
        if (result['complete'] is not True or result['unresolved'] or result['processes']):
            raise ValueError('phase process absence not established')
        try:
            again = copy.deepcopy(provider(self.root/'core'))
        except Exception:
            raise ValueError('phase host inventory unavailable') from None
        if json_bytes(again) != json_bytes(expected) or self._prefix() != prefix:
            raise ValueError('phase changed during joint observation')
        if (digest((self.home/'recovery-manifest.json').read_bytes()) != self.manifest_digest
                or digest((self.home/'phase-progress.json').read_bytes()) != self.progress_hash):
            raise ValueError('authenticated phase inputs changed during observation')
        self._own_gate()
        return roots

    def _hold_stage(self,prefix,provider,host):
        handles = []
        try:
            # Acquire only the exact signed roots; no adoption of discovered new
            # directories. Then independently recheck the whole tree + host.
            for relative in self.stages[prefix]['roots']:
                root = self._path(relative)
                if (not root.is_dir() or not self._path(relative+'/.telyincore-instance.lock').is_file()
                        or self.expected[prefix].get(relative+'/.telyincore-instance.lock') != 'cooperative-lock'):
                    raise ValueError('signed runtime lock path missing')
                handle = acquire_runtime_lock(root)
                if handle is None: raise ValueError('phase runtime lock busy')
                handles.append(handle)
            self._checkpoint(prefix,provider,host)
            return handles
        except BaseException:
            self._release(handles)
            raise

    @staticmethod
    def _release(handles):
        while handles: release_runtime_lock(handles.pop())

    def _apply(self, op, reverse=False):
        self._own_gate()
        if (digest((self.home/'recovery-manifest.json').read_bytes()) != self.manifest_digest
                or digest((self.home/'phase-progress.json').read_bytes()) != self.progress_hash):
            raise ValueError('phase inputs changed immediately before mutation')
        if op['kind'] in ('write','qq'):
            path = self._path(op['path'])
            image = op['after'] if reverse else op['before']
            expected = None if image is None else base64.b64decode(image,validate=True)
            if expected is None:
                matches = not path.exists()
            else:
                matches = path.is_file() and path.stat().st_size <= 65536 and path.read_bytes() == expected
            if not matches:
                raise ValueError('phase write preimage changed; refuse overwrite')
        super()._apply(op,reverse)

    def status(self):
        prefix = self._prefix()
        if self.progress is None: raise ValueError('missing authenticated phase progress')
        self._validate_progress(prefix)
        return {'fixture_only':True,'home':str(self.home),'phase':self.progress['phase'],
                'observed_prefix':prefix,'cursor':self.progress['cursor'],'pending':self.progress['pending'],
                'gate_present':(self.base/GATE).exists(),'coordinator':'signed-phase-joint-fixture-v1',
                'production_apply_supported':False}

    def run(self, action, *, crash_after=None, crash_terminal=False, crash_before=None,
            inventory=None, probe=probe_process):
        if action not in ('apply','rollback'): raise ValueError('invalid phase action')
        if self.progress is None: raise ValueError('missing authenticated phase progress')
        prefix = self._prefix(); self._validate_progress(prefix)
        count = len(self.manifest['operations']); phase = self.progress['phase']
        if phase == 'committed' and action == 'rollback':
            raise ValueError('committed phase requires a new reviewed transaction')
        if phase in ('committed','rolled_back') and not (self.base/GATE).exists():
            return self.status()
        if not (self.base/GATE).exists():
            if phase != 'prepared' or prefix != 0:
                raise ValueError('missing durable phase fence')
            install_gate(self.base,self.manifest['transaction'])
        provider = self.fixture_inventory if inventory is None else inventory
        if not callable(provider): raise ValueError('trusted fixture inventory callable required')
        host = HostObserver(roster=lambda _:None,probe=probe)
        handles = []
        with identity_lock(self.base):
            try:
                handles = self._hold_stage(prefix,provider,host)
                if phase not in ('committed','rolled_back'):
                    indices = list(range(prefix,count) if action == 'apply' else range(prefix-1,-1,-1))
                    for option in (crash_before,crash_after):
                        if option is not None and (type(option) is not int or not 0 <= option < len(indices)):
                            raise ValueError('invalid crash injection step')
                    for step,i in enumerate(indices):
                        self._release(handles)
                        self._checkpoint(prefix,provider,host)
                        self._write_progress('applying' if action == 'apply' else 'rolling_back',prefix,i)
                        if crash_before == step: os._exit(90)
                        self._apply(self.manifest['operations'][i],reverse=action == 'rollback')
                        if crash_after == step: os._exit(91)
                        prefix = i+1 if action == 'apply' else i
                        handles = self._hold_stage(prefix,provider,host)
                        self._write_progress('applying' if action == 'apply' else 'rolling_back',prefix,None)
                    phase = 'committed' if action == 'apply' else 'rolled_back'
                    self._write_progress(phase,prefix,None)
                if crash_terminal: os._exit(92)
                self._validate_progress(prefix)
                self._checkpoint(prefix,provider,host)
                target = safe_path(self.home,self.home/'completed-fence.json')
                if target.exists() and target.read_bytes() != self._gate_bytes():
                    raise ValueError('phase fence backup changed')
                if not target.exists():
                    with target.open('xb') as stream:
                        stream.write(self._gate_bytes());stream.flush();os.fsync(stream.fileno())
                self._checkpoint(prefix,provider,host)
                (self.base/GATE).unlink()  # exact owned marker in a generated fixture only
            finally:
                self._release(handles)
        return self.status()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['create','status','apply','rollback'])
    parser.add_argument('--home')
    parser.add_argument('--nonbaseline',action='store_true')
    parser.add_argument('--crash-before',type=int)
    parser.add_argument('--crash-after',type=int)
    parser.add_argument('--crash-terminal',action='store_true')
    args = parser.parse_args()
    if args.action == 'create':
        if args.home is not None: parser.error('create does not accept a deployment path')
        f = PhaseFixture.create(baseline=not args.nonbaseline)
    else:
        if args.home is None: parser.error('generated fixture --home required')
        f = PhaseFixture(args.home)
    result = f.status() if args.action in ('create','status') else f.run(args.action,
        crash_before=args.crash_before,crash_after=args.crash_after,crash_terminal=args.crash_terminal)
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__': main()
