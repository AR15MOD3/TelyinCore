"""Real inherited Pipe + own Python controller; NEW synthetic store, no QQ IO."""
import json
import multiprocessing
from pathlib import Path
import tempfile

from bot_host_registry import HostEvidenceRegistry
from bot_lifecycle_channel import LifecycleBroker, LifecycleClient, private_channel


def worker(connection,key,session,core,done,release):
    from bot_lifecycle_entry import LifecycleProcessBridge
    from snowluma_recovery import Recovery
    client=LifecycleClient(connection,key,session,timeout=5)
    bridge=LifecycleProcessBridge(core,None,client,argv=['unused-synthetic-tts'],cwd=core)
    r=Recovery(Path(core)/'data/infinity-processes/snowluma-recovery.json',
        process_lifecycle=bridge,protect=lambda raw,**kw:raw[::-1])
    try:
        r.save({'targetUin':'10001','password':'SYNTHETIC-ONLY'})
        assert r.state()['healthStatus']=='unknown'
        done.set()
        if not release.wait(10):raise ValueError('synthetic parent unavailable')
    finally:client.close()


def demo():
    root=Path(tempfile.mkdtemp(prefix='bot-recovery-store-')).resolve()
    core=root/'core';(core/'data').mkdir(parents=True)
    ctx=multiprocessing.get_context('spawn')
    parent,child,key,session=private_channel(ctx)
    done,release=ctx.Event(),ctx.Event()
    controller=ctx.Process(target=worker,args=(child,key,session,str(core),done,release))
    registry=HostEvidenceRegistry(core,['Fixture'],'Fixture')
    # Only this synthetic realm: target never spawns descendants or runs Bot/QQ.
    # No real-host authority is inferred or published from this fixture claim.
    registry.bootstrap_empty(lambda _:dict(core=str(core),bots=['Fixture'],
        baseline='Fixture',complete=True,processes=[],entrypoints_guarded=True))
    started=False;broker=None
    try:
        with registry.start_window('Fixture') as birth:
            controller.start();started=True;birth.commit(controller.pid)
        child.close()
        broker=LifecycleBroker(registry,'Fixture',registry._alive(controller.pid),
            parent,key,session,{},timeout=5)
        broker.serve_once()  # authenticated scope description
        broker.serve_once()  # sticky unbound account-scope revoke, then ACK
        assert registry.inspect()['pending_account_changes']==1
        assert registry.inspect()['bound_configs']==0
        with registry.capture(core) as view:assert not view['complete']
        assert done.wait(5)
        path=core/'data/infinity-processes/snowluma-recovery.json'
        assert json.loads(path.read_bytes())['targetUin']=='10001'
        assert b'SYNTHETIC-ONLY' not in path.read_bytes()
        release.set();controller.join(10);assert controller.exitcode==0
        registry.retire(controller.pid)
        assert registry.inspect()['pending_account_changes']==1
        return dict(fixture_only=True,actual_recovery_save=True,
            parent_ack_before_store_write=True,pending_retained_after_exit=True,
            native_controller_retired=True,real_qq_or_model_requests=False,
            encryption='synthetic reversible fixture, NOT DPAPI proof',production_enabled=False)
    finally:
        release.set()
        if broker is not None:broker.close()
        else:parent.close()
        child.close()
        if started:
            controller.join(10)
            if controller.is_alive():controller.terminate();controller.join(10)
            controller.close()


if __name__=='__main__':print(json.dumps(demo()))
