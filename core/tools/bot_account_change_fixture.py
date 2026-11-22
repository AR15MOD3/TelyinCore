"""Actual writer + real private Pipe in a NEW synthetic root. HTTP is MOCK only."""
import json
import multiprocessing
from pathlib import Path
import tempfile

import httpx

from bot_host_registry import HostEvidenceRegistry
from bot_lifecycle_channel import LifecycleBroker, LifecycleClient, private_channel
from bot_lifecycle_entry import LifecycleProcessBridge
from tools.bot_lifecycle_entry_fixture import RecoveryStub


def worker(connection,key,session,core,done,release):
    from infinity_control import QQControl
    client = LifecycleClient(connection,key,session,timeout=5)
    bridge = LifecycleProcessBridge(core,None,client,argv=['synthetic-unused-tts'],cwd=core)
    control = QQControl(Path(core),process_lifecycle=bridge,recovery=RecoveryStub())
    control.read = lambda: {'fixture':True}
    try:
        control.save_routes({'privates':['10001'],'groups':[],
            'proactiveTarget':'','historyBackfillMinutes':0})
        done.set()
        if not release.wait(timeout=10): raise ValueError('test parent unavailable')
    finally:
        client.close()


def demo():
    root = Path(tempfile.mkdtemp(prefix='bot-account-change-')).resolve()
    core = root/'core'; (core/'data').mkdir(parents=True)
    relative = 'core/extensions/core-infinity-qq/config.json'
    path = root/relative; path.parent.mkdir(parents=True)
    path.write_text(json.dumps(dict(ownerBot='Fixture',httpUrl='http://127.0.0.1:19081',
        wsUrl='ws://127.0.0.1:19082',token='SYNTHETIC-ONLY')),encoding='utf-8')
    before = path.read_bytes()
    context = multiprocessing.get_context('spawn')
    parent,child,key,session = private_channel(context)
    done,release = context.Event(),context.Event()
    controller = context.Process(target=worker,args=(child,key,session,str(core),done,release))
    registry = HostEvidenceRegistry(core,['Fixture'],'Fixture',listeners=lambda port:
        [{'address':'127.0.0.1','pid':controller.pid}] if port in (19081,19082) else None)
    registry.bootstrap_empty(lambda _:dict(core=str(core),bots=['Fixture'],baseline='Fixture',
        complete=True,processes=[],entrypoints_guarded=True))
    broker = None; started = False; requests = []
    try:
        with registry.start_window('Fixture') as birth:
            controller.start(); started = True; birth.commit(controller.pid)
        child.close()
        registry.bind_config('Fixture',relative,'10001')
        def respond(request):
            requests.append(request.url.path)
            return httpx.Response(200,json={'status':'ok','retcode':0,'data':{'user_id':10001}})
        registry.refresh_account(relative,controller.pid,client=lambda **kw:
            httpx.Client(transport=httpx.MockTransport(respond),**kw))
        assert registry.inspect()['cached_account_proofs'] == 1
        broker = LifecycleBroker(registry,'Fixture',registry._alive(controller.pid),parent,key,session,{},timeout=5)
        broker.serve_once()  # authenticated scope description
        broker.serve_once()  # fresh account writer scope description
        broker.serve_once()  # sticky revocation ACK before cooperative child write
        assert registry.inspect()['pending_account_changes'] == 1
        assert registry.inspect()['cached_account_proofs'] == 0
        assert done.wait(timeout=5)
        assert json.loads(path.read_bytes())['privates'] == ['10001']
        backup = list((core/'data/infinity-processes').glob('qq-config-*.json.bak'))
        assert len(backup) == 1 and backup[0].read_bytes() == before
        release.set(); controller.join(timeout=10)
        assert controller.exitcode == 0
        broker.close(); registry.retire(controller.pid)
        assert registry.inspect()['registered_processes'] == 0
        return {'fixture_only':True,'actual_save_routes':True,'native_controller_exitcode':0,
                'old_proof_revoked':True,'pending_barrier_retained':True,
                'original_config_backed_up':True,'http_transport':'MockTransport',
                'readonly_requests':requests,'production_enabled':False}
    finally:
        release.set()
        if broker is not None: broker.close()
        else: parent.close()
        child.close()
        if started:
            controller.join(timeout=10)
            if controller.is_alive():controller.terminate();controller.join(timeout=10)
            controller.close()


if __name__ == '__main__':
    print(json.dumps(demo()))
