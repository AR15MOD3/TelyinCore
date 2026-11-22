"""Read-only adapter demonstration in NEW generated synthetic realms only.

Invented account/native receipts below are TEST STUBS, not a real host registry,
verified OneBot transport or production account/absence evidence. No supplied root.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import time

from bot_host_inventory import ReadonlyHostInventory
from bot_joint_offline import coordinated_offline_lease
from bot_migration_gate import GATE,install_gate
from tools.bot_adapter_fixture import AdapterFixture
from tools.bot_recovery_fixture import tree_state


def demo(*,baseline=True):
    f=AdapterFixture.create(baseline=baseline);core=f.root/'core'
    before=tree_state(f.root);claims=[]
    for index,(name,account) in enumerate([('One','10001'),('Other','10002')]):
        shared=name==('One' if baseline else 'Other')
        path=core/'extensions/core-infinity-qq/config.json' if shared else f.bots/name/'infinity/qq/config.json'
        claims.append({'path':path.relative_to(f.root).as_posix(),'config_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'bot':name,'target_account':account,'observed_account':int(account),'source':'get_login_info',
            'generation':'fixture-registry-v1','sampled_at_ns':time.monotonic_ns(),
            'endpoint_verified':True,'endpoint_state':'retired',
            'endpoint':{'pid':11001+index,'created_filetime':123,'image':str(f.home/f'synthetic-client-{index}.exe')}})
    source={'version':1,'core':str(core.resolve()),'complete':True,'generation':'fixture-registry-v1',
            'bots':['One','Other'],'baseline':'One' if baseline else 'Other','processes':[],'accounts':claims}
    @contextmanager
    def capture(requested):
        if requested!=core.resolve(): raise ValueError('synthetic registry scope mismatch')
        yield source
    def synthetic_probe(pid):
        if pid not in (11001,11002): raise ValueError('unknown synthetic process claim')
        return {'pid':pid,'state':'absent'}
    adapter=ReadonlyHostInventory(capture=capture,probe=synthetic_probe)
    report=adapter.inspect(core)
    tx=f.manifest['transaction'];install_gate(f.base,tx)
    with coordinated_offline_lease(f.base,f.bots,tx,'One','Renamed',inventory=adapter,probe=synthetic_probe) as held:
        review=held.review()
    return {'fixture_only':True,'home':str(f.home),'read_only_report':report,
            'joint_preapply_checked':review['joint_preapply_checked'],
            'original_tree_unchanged':tree_state(f.root)==before,'fence_retained':(f.base/GATE).exists(),
            'real_authoritative_source_connected':False,'production_apply_supported':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nonbaseline',action='store_true')
    args=parser.parse_args()
    print(json.dumps(demo(baseline=not args.nonbaseline),ensure_ascii=False))


if __name__=='__main__': main()
