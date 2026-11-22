"""Actual launch assembly fixture. Only generated root/config, stub web boundary; no Bot service."""
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace


def run(root, bot):
    root=Path(root).absolute()
    assert (root/'.synthetic-launch-fixture').read_text()=='SYNTHETIC'
    source=Path(__file__).resolve().parents[1]
    blocked=[]
    socketpair_active=[False]
    original_pair=socket.socketpair
    def owned_pair(*a,**kw):
        socketpair_active[0]=True
        try:return original_pair(*a,**kw)
        finally:socketpair_active[0]=False
    socket.socketpair=owned_pair
    forbidden=[source/'data',source/'extensions',source.parent/'bots',source.parent/'SnowLuma',
               Path('D:/AI/TTS'),Path('D:/AI/local-service')]
    def audit(event,args):
        if event=='open' and isinstance(args[0],(str,bytes)):
            path=Path(os.fsdecode(args[0])).absolute()
            if any(path==x or x in path.parents for x in forbidden):
                blocked.append('private filesystem');raise RuntimeError('private filesystem forbidden')
        if event=='socket.connect' and socketpair_active[0] and args[1][0]=='127.0.0.1':
            return  # asyncio's own fresh socketpair, not a QQ/model endpoint
        if event in ('socket.connect','socket.getaddrinfo','subprocess.Popen','os.system'):
            blocked.append(event);raise RuntimeError('external IO forbidden in assembly fixture')
    sys.addaudithook(audit)
    import launch
    import bot_launcher_child
    from telyincore import plugins, market
    seen=[]
    def report(name,values):
        target=root/name;temporary=target.with_suffix('.new')
        with temporary.open('x',encoding='utf-8') as f:
            json.dump(values,f);f.flush();os.fsync(f.fileno())
        os.replace(temporary,target)
    def never(*a,**k):
        blocked.append('unmediated plugin/tick/market');raise AssertionError('unmediated assembly IO')
    for name in ('wake','gathered','reader','every_market_reader','warm_market','switch','carry','remove','recalc','installed','_read_manifest','the_place'):
        setattr(plugins,name,never)
    for name in ('install','install_from','install_url'):setattr(market,name,never)
    launch._arm_the_next_beat=launch._may_dream_tonight=launch._arm_the_follow_up=never
    async def serve(console,**kw):
        projection=kw['read_plugins']()
        assert projection['lifecycle']=='blocked' and projection['wired'] is False and projection['complete'] is False
        assert not projection['installedInventoryRead']
        assert kw['read_market']()['lifecycle']=='blocked'
        assert kw['read_infinity']('/infinity/bots')['active']==bot
        mutations=[lambda:kw['switch_plugin']('synthetic',True),
            lambda:kw['remove_plugin']('synthetic'),
            lambda:kw['install_plugin'](where='NEVER'),
            lambda:kw['install_plugin'](url='https://invalid.invalid/NEVER'),
            lambda:kw['install_plugin'](name='NEVER')]
        routes=['/infinity/qq/settings','/infinity/qq/process','/infinity/qq/recovery',
                '/infinity/qq/check','/infinity/qq/import-connection','/infinity/qq/tts-preview',
                '/infinity/vision/install','/infinity/vision/runtime-import','/infinity/vision/preview',
                '/infinity/plugin-config/synthetic','/infinity/bots/rename']
        mutations += [lambda r=r:kw['act_infinity'](r,object()) for r in routes]
        mutations += [lambda r=r:kw['read_infinity'](r) for r in ['/infinity/qq','/infinity/vision','/infinity/plugin-config/synthetic']]
        for call in mutations:
            try:call()
            except ValueError as error:assert 'parent plugin runtime capability' in str(error)
            else:raise AssertionError('unmediated route accepted')
        await asyncio.sleep(.05)  # any wrongly-started tick gets an opportunity to run
        assert not blocked,blocked
        async def web_read():await asyncio.Event().wait()
        console.read=web_read  # real serve installs its own reader before on_start
        import inspect
        fields=inspect.signature(console.assemble).parameters
        console.assemble(read=web_read, **{k:v for k,v in kw.items() if k in fields and k!='read'})
        assert console.app.timers is kw['timers']
        stop_requested = asyncio.Event()
        kw['on_start'](SimpleNamespace(begin_operator_shutdown=stop_requested.set))
        report('assembled.json',{'actual_launch_main':True,'web_stub':True,
            'denied_routes':len(mutations),'blocked_io':blocked,'plugin_projection':projection})
        # Parent Q invokes the bound phase-one callback; serve owns its final drain.
        try:
            await stop_requested.wait()
            assert stop_requested.is_set()
            return 0
        finally:
            await console.stop()
            seen.append('serve-finally')
    launch.serve=serve
    try:result=bot_launcher_child.main(sys.argv[3:])
    finally:
        assert not blocked,blocked
        if sys.exception() is None:assert seen==['serve-finally'],seen
        report('finished.json',{'actual_launch_main':True,'web_stub':True,
            'serve_finally':seen==['serve-finally'],'blocked_io':blocked})
    return result


if __name__=='__main__':
    raise SystemExit(run(sys.argv[1],sys.argv[2]))
