"""Recoverable protocol/QQ adapter experiment, generated temporary realms only.

No arbitrary root, production inventory, plugin approval, or production apply.
The fixture key and journal before-images are test-only, not secret storage.
"""
import argparse
import base64
import hashlib
import json
import re

from bot_protocol_reindex import ContextWitness, context_filename, plan_context_reindex
from bot_qq_ownership import plan_qq_owner, fixture_owner_bytes
from bot_plugin_ownership import PluginOwnershipObserver
from tools.bot_recovery_fixture import RecoveryFixture
from tools.bot_rename_fixture import encoded, json_bytes


class AdapterFixture(RecoveryFixture):
    @staticmethod
    def _extend_operations(f, ops):
        baseline = not any(op.get('from') == 'core/data/bot-runtimes/One' for op in ops)
        old = f.base if baseline else f.base / 'bot-runtimes/One'
        new = old if baseline else f.base / 'bot-runtimes/Renamed'
        contexts = old / 'provider-state/contexts'
        contexts.mkdir(parents=True)
        meta = json_bytes({'version': 1, 'context': 'a'*64, 'records': ['b'*64]})
        name = context_filename(old / 'session-main.jsonl', 'private:synthetic')
        (contexts / name).write_bytes(meta)
        records = old / 'provider-state/records'; records.mkdir()
        (records / ('b'*64 + '.json')).write_bytes(b'{"synthetic":"One must remain literal"}')
        plan = plan_context_reindex(old, new, [ContextWitness(
            'session-main.jsonl', 'private:synthetic', hashlib.sha256(meta).hexdigest())])
        for item in plan['index_changes']:
            ops.append({'kind': 'index', 'from': (new / item['old_relative']).relative_to(f.root).as_posix(),
                        'to': (new / item['new_relative']).relative_to(f.root).as_posix(),
                        'sha256': item['sha256']})
        path = (f.core / 'extensions/core-infinity-qq/config.json' if baseline
                else f.bots / 'One/infinity/qq/config.json')
        path.parent.mkdir(parents=True)
        raw = b'{ "ownerBot": "One", "token": "SYNTHETIC-ONLY", "port": 12345, "literal": "One" }\n'
        path.write_bytes(raw)
        other = (f.bots / 'Other/infinity/qq/config.json' if baseline else
                 f.core / 'extensions/core-infinity-qq/config.json')
        other.parent.mkdir(parents=True, exist_ok=True)
        other_raw = json_bytes({'ownerBot': 'Other', 'token': 'SYNTHETIC-OTHER'})
        other.write_bytes(other_raw)
        # Only these freshly created fixture accounts, never actual login IDs.
        resources = [{'path': p.relative_to(f.root).as_posix(), 'plugin': 'core-infinity-qq',
                      'kind': 'qq-config', 'owner': name, 'account': account,
                      'sha256': hashlib.sha256(value).hexdigest()}
                     for p, name, account, value in [(path, 'One', '10001', raw),
                                                     (other, 'Other', '10002', other_raw)]]
        snapshot = {'version': 1, 'core': str(f.core.resolve()), 'complete': True,
                    'generation': 'synthetic-v1', 'bots': ['One', 'Other'],
                    'baseline': 'One' if baseline else 'Other', 'resources': resources}
        observer = PluginOwnershipObserver(inventory=lambda core: snapshot)
        owner = observer.plan_qq(raw, 'One', 'Renamed', baseline=baseline, core=f.core,
                                bots=['One', 'Other'], config_relative=path.relative_to(f.root).as_posix())
        target = path if baseline else f.bots / 'Renamed/infinity/qq/config.json'
        ops.append({'kind': 'qq', 'path': target.relative_to(f.root).as_posix(),
                    'before': encoded(raw), 'after': encoded(fixture_owner_bytes(raw, owner))})
        return ops

    def _validate_operations(self, ops=None):
        ops = self.manifest['operations'] if ops is None else ops
        if not isinstance(ops, list) or not 5 <= len(ops) <= 9:
            raise ValueError('adapter operation limit')
        base = [op for op in ops if isinstance(op, dict) and op.get('kind') in ('move', 'write')]
        super()._validate_operations(base)
        extras = ops[len(base):]
        if ops[:len(base)] != base or [op.get('kind') for op in extras] != (
                ['qq'] if self.manifest['baseline'] else ['index', 'qq']):
            raise ValueError('invalid adapter order or coverage')
        for op in extras:
            if op['kind'] == 'index':
                if set(op) != {'kind', 'from', 'to', 'sha256'}:
                    raise ValueError('invalid index operation')
                prefix = 'core/data/bot-runtimes/Renamed/provider-state/contexts/'
                for field in ('from', 'to'):
                    path = op[field]
                    if not isinstance(path, str) or not path.startswith(prefix) or not re.fullmatch(
                            r'[0-9a-f]{64}\.json', path[len(prefix):]):
                        raise ValueError('unapproved index path')
                    self._path(path)
                if op['from'] == op['to'] or not re.fullmatch('[0-9a-f]{64}', op['sha256']):
                    raise ValueError('invalid index digest or destination')
                old = self.root / 'core/data/bot-runtimes/One/session-main.jsonl'
                new = self.root / 'core/data/bot-runtimes/Renamed/session-main.jsonl'
                if (op['from'] != prefix + context_filename(old, 'private:synthetic')
                        or op['to'] != prefix + context_filename(new, 'private:synthetic')):
                    raise ValueError('index witness scope/path mismatch')
            else:
                path = ('core/extensions/core-infinity-qq/config.json' if self.manifest['baseline']
                        else 'bots/Renamed/infinity/qq/config.json')
                if set(op) != {'kind', 'path', 'before', 'after'} or op['path'] != path:
                    raise ValueError('unapproved QQ config path')
                before, after = [base64.b64decode(op[k], validate=True) for k in ('before', 'after')]
                plan = plan_qq_owner(before, 'One', 'Renamed', baseline=self.manifest['baseline'], inventory={
                    'complete': True, 'plugins': ['core-infinity-qq'], 'process_records': [],
                    'sharing_preview': False, 'other_resources': []})
                if after != fixture_owner_bytes(before, plan):
                    raise ValueError('QQ adapter changed unrelated config fields')

    @staticmethod
    def _next_state(before, op):
        if op['kind'] == 'index':
            state = dict(before)
            if state.get(op['from']) != op['sha256'] or op['to'] in state:
                raise ValueError('index snapshot precondition failed')
            state[op['to']] = state.pop(op['from'])
            return state
        if op['kind'] == 'qq':
            op = dict(op, kind='write')
        return RecoveryFixture._next_state(before, op)

    def _apply(self, op, reverse=False):
        if op['kind'] == 'index':
            source, target = (op['to'], op['from']) if reverse else (op['from'], op['to'])
            source, target = self._path(source), self._path(target)
            if (not source.is_file() or target.exists()
                    or hashlib.sha256(source.read_bytes()).hexdigest() != op['sha256']):
                raise ValueError('index move precondition failed')
            source.rename(target)
        else:
            super()._apply(dict(op, kind='write') if op['kind'] == 'qq' else op, reverse)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'status', 'apply', 'rollback'])
    parser.add_argument('--home')
    parser.add_argument('--nonbaseline', action='store_true')
    parser.add_argument('--crash-after', type=int)
    parser.add_argument('--crash-terminal', action='store_true')
    args = parser.parse_args()
    f = AdapterFixture.create(baseline=not args.nonbaseline) if args.action == 'create' else AdapterFixture(args.home)
    print(json.dumps(f.status() if args.action in ('create', 'status') else f.run(
        args.action, crash_after=args.crash_after, crash_terminal=args.crash_terminal)))


if __name__ == '__main__':
    main()
