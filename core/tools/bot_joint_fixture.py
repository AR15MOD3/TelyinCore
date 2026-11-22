"""Create a NEW synthetic deployment and demonstrate joint pre-apply checks only.

No supplied deployment/home, production discovery, apply, rollback or unfence CLI.
Inventory accounts below are invented test witnesses, not actual login evidence.
"""
import argparse
import hashlib
import json

from bot_joint_offline import coordinated_offline_lease
from bot_migration_gate import GATE, install_gate
from tools.bot_adapter_fixture import AdapterFixture


def demo(*, baseline=True):
    f = AdapterFixture.create(baseline=baseline)
    core = f.root/'core'
    rows = []
    source = None
    for name, account in [('One', '10001'), ('Other', '10002')]:
        shared = name == ('One' if baseline else 'Other')
        path = core/'extensions/core-infinity-qq/config.json' if shared else f.bots/name/'infinity/qq/config.json'
        raw = path.read_bytes()  # generated synthetic files ONLY
        rows.append({'path': path.relative_to(f.root).as_posix(), 'plugin': 'core-infinity-qq',
                     'kind': 'qq-config', 'owner': name, 'account': account,
                     'sha256': hashlib.sha256(raw).hexdigest()})
        if name == 'One': source = raw
    plugin = {'version': 1, 'complete': True, 'generation': 'synthetic-joint-1',
              'core': str(core.resolve()), 'bots': ['One','Other'],
              'baseline': 'One' if baseline else 'Other', 'resources': rows}
    process = {'core': plugin['core'], 'complete': True, 'generation': plugin['generation'], 'processes': []}
    snapshot = {key: plugin[key] for key in ('version','complete','generation','core','bots','baseline')}
    snapshot.update(plugins=plugin, processes=process)
    tx = f.manifest['transaction']
    install_gate(f.base, tx)
    def no_pid_expected(pid):
        raise ValueError('synthetic demo has no process claims')
    with coordinated_offline_lease(f.base,f.bots,tx,'One','Renamed',
            inventory=lambda _:snapshot, probe=no_pid_expected) as held:
        review = held.review()
        plan = held.plan_qq(source,baseline=baseline,config_relative=rows[0]['path'])
        held.release_for_directory_move()
        stage = held.review()['stage']
    return {'fixture_only': True, 'home': str(f.home), 'review': review, 'qq_plan': plan,
            'last_checked_stage': stage, 'fence_retained': (f.base/GATE).exists(),
            'preapply_tree_unchanged': f.status()['observed_prefix'] == 0,
            'transaction_apply_supported': False, 'production_apply_supported': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nonbaseline', action='store_true')
    args = parser.parse_args()
    print(json.dumps(demo(baseline=not args.nonbaseline),ensure_ascii=False))


if __name__ == '__main__':
    main()
