"""Cross-process crash recovery for generated synthetic fixtures ONLY.

The persisted fixture key is NOT a production credential/ACL design. No production
process observer, arbitrary deployment root, or real rename apply is exposed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from bot_migration_gate import GATE, install_gate
from bot_offline_lease import managed_offline_lease
from runtime_identity import safe_path
from tools.bot_rename_fixture import Fixture, atomic, json_bytes


def tree_state(root):
    result = {}
    def walk(folder):
        for path in sorted(folder.iterdir()):
            safe_path(root, path)
            key = path.relative_to(root).as_posix()
            if key == 'core/data/' + GATE:
                continue  # owned marker is independently verified, never ignored blindly
            if path.is_dir():
                result[key] = 'directory'; walk(path)
            elif path.name in ('.bot-identity.lock', '.telyincore-instance.lock'):
                result[key] = 'cooperative-lock'
            elif path.is_file() and path.stat().st_size <= 1024*1024:
                result[key] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                raise ValueError('unsupported fixture entry')
            if len(result) > 1000:
                raise ValueError('fixture entry limit')
    walk(root)
    return result


def synthetic_inventory(core, names, records):
    # Only for this generated realm. This is NOT a native process scanner.
    return {'complete': True, 'core': str(core.resolve()), 'unresolved': [], 'processes': []}


class RecoveryFixture:
    @classmethod
    def create(cls, *, baseline=True):
        f = Fixture(baseline=baseline)
        for root in [f.base, *(f.base/'bot-runtimes').iterdir()]:
            (root / '.telyincore-instance.lock').touch()
        prepared = f.prepare()
        assert prepared['plan']['fixture_eligible']
        ops = f._operations(prepared['plan'])
        ops = cls._extend_operations(f, ops)
        key = os.urandom(32)
        key_path = f.home/'recovery-fixture.key'
        fd = os.open(key_path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(key); stream.flush(); os.fsync(stream.fileno())
        manifest = {'version':1,'transaction':uuid.uuid4().hex,'fixture_nonce':f._proof,
                    'old':'One','new':'Renamed','baseline':baseline,
                    'before':tree_state(f.root),'operations':ops}
        profile = getattr(cls, 'EXECUTION_PROFILE', None)
        if profile is not None: manifest['execution_profile'] = profile
        def envelope(body):
            return json_bytes({'body':body,'mac':hmac.new(key,json_bytes(body),hashlib.sha256).hexdigest()})
        for name, body in [('recovery-manifest.json',manifest),
                           ('recovery-journal.json',{'version':1,'transaction':manifest['transaction'],
                                                    'phase':'prepared','pending':None})]:
            with (f.home/name).open('xb') as stream:
                stream.write(envelope(body));stream.flush();os.fsync(stream.fileno())
        shutil.copytree(f.root,f.home/'recovery-backup',
                        ignore=shutil.ignore_patterns('.bot-identity.lock','.telyincore-instance.lock'))
        return cls(f.home)

    @staticmethod
    def _extend_operations(f, ops):
        return ops

    @staticmethod
    def _next_state(before, op):
        return Fixture._next(before, op)

    def __init__(self, home):
        self.home = Path(home).absolute()
        temp = Path(tempfile.gettempdir()).resolve()
        if (self.home.parent.resolve()!=temp or not self.home.name.startswith('bot-rename-fixture-')):
            raise ValueError('only generated temporary fixture homes may be reopened')
        safe_path(temp,self.home)
        self.root=self.home/'deployment';self.base=self.root/'core/data';self.bots=self.root/'bots'
        for name in ['deployment','recovery-fixture.key','recovery-manifest.json',
                     'recovery-journal.json','synthetic-only.json']:
            safe_path(self.home,self.home/name)
        key_path=self.home/'recovery-fixture.key'
        if key_path.stat().st_size!=32:raise ValueError('fixture key invalid')
        self.key=key_path.read_bytes()
        self.manifest=self._read('recovery-manifest.json')
        m=self.manifest
        fields = {'version','transaction','fixture_nonce','old','new','baseline','before','operations'}
        profile = getattr(type(self), 'EXECUTION_PROFILE', None)
        if profile is not None: fields.add('execution_profile')
        if (set(m)!=fields or m.get('execution_profile')!=profile
                or m['version']!=1 or m['old']!='One' or m['new']!='Renamed'
                or type(m['baseline']) is not bool or not isinstance(m['before'],dict)):
            raise ValueError('unsupported fixture manifest')
        import re
        if re.fullmatch('[0-9a-f]{32}',m['transaction']) is None:
            raise ValueError('invalid fixture transaction')
        proof=json.loads((self.home/'synthetic-only.json').read_bytes())
        if proof!={'nonce':m['fixture_nonce']}:raise ValueError('fixture origin mismatch')
        self._validate_operations()
        self.journal=self._read('recovery-journal.json')
        if (set(self.journal)!={'version','transaction','phase','pending'}
                or self.journal['transaction']!=m['transaction'] or self.journal['version']!=1
                or self.journal['phase'] not in ['prepared','applying','rolling_back','committed','rolled_back']):
            raise ValueError('journal transaction/state invalid')
        self.journal_hash=hashlib.sha256((self.home/'recovery-journal.json').read_bytes()).hexdigest()
        self.expected=[m['before']]
        for op in m['operations']:
            self.expected.append(self._next_state(self.expected[-1],op))

    def _read(self,name):
        path=safe_path(self.home,self.home/name)
        with path.open('rb') as stream:raw=stream.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:raise ValueError('fixture metadata too large')
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ValueError('duplicate fixture key')
                result[key]=value
            return result
        data=json.loads(raw,object_pairs_hook=unique)
        if not isinstance(data,dict) or set(data)!={'body','mac'}:raise ValueError('invalid envelope')
        mac=hmac.new(self.key,json_bytes(data['body']),hashlib.sha256).hexdigest()
        if not isinstance(data['mac'],str) or not hmac.compare_digest(mac,data['mac']):
            raise ValueError('fixture metadata authentication failed')
        return data['body']

    def _path(self,name):return safe_path(self.root,self.root/name)

    def _validate_operations(self, ops=None):
        ops=self.manifest['operations'] if ops is None else ops
        if not isinstance(ops,list) or not 4<=len(ops)<=7:raise ValueError('fixture operation limit')
        allowed_writes={'core/data/bot-runtime-owner.json','core/data/active-bot.json',
                        'core/data/bot-runtime-bindings.json','core/data/bot-retired-names.json',
                        'bots/Renamed/.runtime-binding.json'}
        allowed_moves={('bots/One','bots/Renamed'),
                       ('core/data/bot-runtimes/One','core/data/bot-runtimes/Renamed')}
        for op in ops:
            if op.get('kind')=='move':
                if set(op)!={'kind','from','to'} or (op['from'],op['to']) not in allowed_moves:
                    raise ValueError('unapproved fixture move')
                self._path(op['from']);self._path(op['to'])
            elif op.get('kind')=='write':
                if set(op)!={'kind','path','before','after'} or op['path'] not in allowed_writes:
                    raise ValueError('unapproved fixture write')
                self._path(op['path'])
                for key in ['before','after']:
                    if op[key] is not None and len(base64.b64decode(op[key],validate=True))>65536:
                        raise ValueError('oversized fixture control image')
            else:raise ValueError('unsupported fixture operation')

    def _save(self,phase,pending=None):
        path=safe_path(self.home,self.home/'recovery-journal.json')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=self.journal_hash:
            raise ValueError('journal changed concurrently')
        shutil.copy2(path,safe_path(self.home,self.home/'recovery-journal.previous.json'))
        self.journal={'version':1,'transaction':self.manifest['transaction'],'phase':phase,'pending':pending}
        body=self.journal
        raw=json_bytes({'body':body,'mac':hmac.new(self.key,json_bytes(body),hashlib.sha256).hexdigest()})
        atomic(path,raw);self.journal_hash=hashlib.sha256(raw).hexdigest()

    def _gate_bytes(self):
        return json.dumps({'version':1,'transaction':self.manifest['transaction'],'state':'fenced'}).encode()

    def _own_gate(self):
        path=self._path('core/data/'+GATE)
        if path.read_bytes()!=self._gate_bytes():raise ValueError('fence ownership/content changed')

    def _prefix(self):
        actual=tree_state(self.root)
        matches=[i for i,value in enumerate(self.expected) if value==actual]
        if len(matches)!=1:raise ValueError('foreign changes: refuse migration or rollback')
        return matches[0]

    def _apply(self,op,reverse=False):
        if op['kind']=='move':
            source,target=(op['to'],op['from']) if reverse else (op['from'],op['to'])
            source,target=self._path(source),self._path(target)
            if not source.is_dir() or target.exists():raise ValueError('move precondition changed')
            source.rename(target)
        else:
            path=self._path(op['path'])
            value=op['before'] if reverse else op['after']
            if value is None:path.unlink()  # exactly one synthetic control file, never recursive
            else:atomic(path,base64.b64decode(value))

    def status(self):
        return {'fixture_only':True,'phase':self.journal['phase'],'observed_prefix':self._prefix(),
                'gate_present':(self.base/GATE).exists(),'home':str(self.home)}

    def run(self, action, *, crash_after=None, crash_terminal=False):
        if action not in ['apply','rollback']:raise ValueError('invalid fixture action')
        phase=self.journal['phase'];count=len(self.manifest['operations'])
        prefix=self._prefix(); gate=self.base/GATE
        if phase in ['committed','rolled_back']:
            wanted=count if phase=='committed' else 0
            if prefix!=wanted:raise ValueError('terminal state changed; no automatic recovery')
            if action=='rollback' and phase=='committed':
                raise ValueError('committed transaction requires a new reviewed transaction, not rollback')
            if not gate.exists():return self.status()  # read-only idempotence, no new offline claim
        if not gate.exists():
            if phase!='prepared' or prefix!=0:raise ValueError('missing persistent fence; refuse recovery')
            install_gate(self.base,self.manifest['transaction'])
        self._own_gate()
        with managed_offline_lease(self.base,self.bots,self.manifest['transaction'],observer=synthetic_inventory) as lease:
            lease.release_for_directory_move()
            self._own_gate();prefix=self._prefix()
            if phase not in ['committed','rolled_back']:
                indices=range(prefix,count) if action=='apply' else range(prefix-1,-1,-1)
                for step,i in enumerate(indices):
                    self._own_gate()
                    if self._prefix()!=(i if action=='apply' else i+1):raise ValueError('unexpected prefix')
                    self._save('applying' if action=='apply' else 'rolling_back',i)
                    self._apply(self.manifest['operations'][i],reverse=action=='rollback')
                    if crash_after==step:os._exit(91)  # deliberate test-process death, not exception simulation
                    self._save('applying' if action=='apply' else 'rolling_back')
                phase='committed' if action=='apply' else 'rolled_back'
                wanted=count if action=='apply' else 0
                if self._prefix()!=wanted:raise ValueError('final fixture verification failed')
                self._save(phase)
            if crash_terminal:os._exit(92)  # terminal journal durable, fence still held
            self._own_gate()
            expected_prefix=count if phase=='committed' else 0
            if self._prefix()!=expected_prefix:raise ValueError('state changed before unfence')
            backup=safe_path(self.home,self.home/'completed-fence.json')
            if backup.exists() and backup.read_bytes()!=self._gate_bytes():raise ValueError('fence backup changed')
            if not backup.exists():
                with backup.open('xb') as stream:
                    stream.write(self._gate_bytes());stream.flush();os.fsync(stream.fileno())
            self._own_gate();gate.unlink()  # only the verified owned marker in a generated fixture
        return self.status()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['create','status','apply','rollback'])
    parser.add_argument('--home',type=Path)
    parser.add_argument('--nonbaseline',action='store_true')
    parser.add_argument('--crash-after',type=int)
    parser.add_argument('--crash-terminal',action='store_true')
    args=parser.parse_args(argv)
    if args.action=='create':
        if args.home is not None:parser.error('create never accepts a deployment path')
        fixture=RecoveryFixture.create(baseline=not args.nonbaseline)
        result=fixture.status()
    else:
        if args.home is None:parser.error('generated temporary --home is required')
        fixture=RecoveryFixture(args.home)
        result=fixture.status() if args.action=='status' else fixture.run(args.action,crash_after=args.crash_after,crash_terminal=args.crash_terminal)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
