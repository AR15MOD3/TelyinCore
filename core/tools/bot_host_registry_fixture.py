"""NEW synthetic lifecycle/receipt demo. No supplied root, real service or requests.

Birth/native/listener/initial-coverage witnesses are explicit fixture stubs. HTTP
parsing uses MockTransport. They MUST NOT be used as production authority.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import uuid

import httpx

from bot_host_inventory import ReadonlyHostInventory
from bot_host_registry import HostEvidenceRegistry
from bot_joint_offline import coordinated_offline_lease
from bot_migration_gate import GATE,install_gate
from tools.bot_recovery_fixture import tree_state


def demo(*,baseline=True):
    home=Path(tempfile.mkdtemp(prefix='bot-registry-demo-')).resolve()
    root=home/'deployment';core=root/'core';base=core/'data';bots=root/'bots'
    base.mkdir(parents=True);bots.mkdir()
    owner='One' if baseline else 'Other';scoped='Other' if baseline else 'One'
    (base/'bot-runtimes'/scoped).mkdir(parents=True)
    for name in ('One','Other'): (bots/name).mkdir()
    for folder in (base,base/'bot-runtimes'/scoped): (folder/'.telyincore-instance.lock').touch()
    (base/'.bot-identity.lock').write_bytes(b'0')
    (base/'bot-runtime-owner.json').write_text(json.dumps({'baseline':owner}))
    (base/'bot-runtime-bindings.json').write_text(json.dumps({'version':1,'bindings':{'One':'a'*32,'Other':'c'*32}}))
    native={};ports={};targets={};configs=[]
    for i,name in enumerate(('One','Other')):
        http_port=19081+100*i;ws_port=http_port+1;pid=12001+i;target=str(10001+i)
        path=core/'extensions/core-infinity-qq/config.json' if name==owner else bots/name/'infinity/qq/config.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'ownerBot':name,'httpUrl':f'http://127.0.0.1:{http_port}',
            'wsUrl':f'ws://127.0.0.1:{ws_port}','token':'SYNTHETIC-PRIVATE'}))
        ports.update({http_port:pid,ws_port:pid});targets[http_port]=target
        configs.append((name,path.relative_to(root).as_posix(),pid,target))
    before=tree_state(root)
    def probe(pid): return dict(native.get(pid,{'pid':pid,'state':'absent'}))
    def listeners(port): return [{'address':'127.0.0.1','pid':ports[port]}] if port in ports else None
    registry=HostEvidenceRegistry(core,['One','Other'],owner,probe=probe,listeners=listeners,
        clock_ns=lambda:10**12,birth_clock=lambda:200)
    registry.bootstrap_empty(lambda requested:{'core':str(core.resolve()),'bots':['One','Other'],'baseline':owner,
        'complete':True,'processes':[],'entrypoints_guarded':True})
    requests=[]
    def handler(request):
        if request.method!='POST' or request.url.path!='/get_login_info': raise ValueError('non-readonly fixture request')
        requests.append(request.url.path)
        return httpx.Response(200,json={'status':'ok','retcode':0,'data':{'user_id':int(targets[request.url.port])}})
    def client(**options): return httpx.Client(transport=httpx.MockTransport(handler),**options)
    for name,relative,pid,target in configs:
        registry.bind_config(name,relative,target)
        with registry.start_window(name) as ticket:
            native[pid]={'pid':pid,'state':'alive','created_filetime':300,'image':str(home/(name+'-synthetic.exe'))}
            ticket.commit(pid)
        registry.refresh_account(relative,pid,client=client)
    adapter=ReadonlyHostInventory(capture=registry.capture,probe=probe,clock_ns=lambda:10**12)
    active=adapter.inspect(core)
    tx=uuid.uuid4().hex;install_gate(base,tx)
    live_blocked=False
    try:
        with coordinated_offline_lease(base,bots,tx,'One','Renamed',inventory=adapter,probe=probe):
            raise AssertionError('live fixture processes must block offline')
    except ValueError: live_blocked=True
    for _,_,pid,_ in configs:
        native[pid]={'pid':pid,'state':'absent'};registry.retire(pid)
    retired=adapter.inspect(core)
    with coordinated_offline_lease(base,bots,tx,'One','Renamed',inventory=adapter,probe=probe) as held:
        checked=held.review()['joint_preapply_checked']
    return {'fixture_only':True,'home':str(home),'active_report':active,'retired_report':retired,
            'live_processes_blocked':live_blocked,'joint_preapply_checked_after_exit':checked,
            'read_only_requests':requests,'registry':registry.inspect(),
            'original_tree_unchanged':tree_state(root)==before,'fence_retained':(base/GATE).exists(),
            'real_network_requests':False,'production_entrypoints_connected':False,'production_apply_supported':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nonbaseline',action='store_true')
    args=parser.parse_args()
    print(json.dumps(demo(baseline=not args.nonbaseline),ensure_ascii=False))


if __name__=='__main__': main()
