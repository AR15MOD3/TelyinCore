"""Default parent/actual launch fixture: fresh synthetic Bot only; plugin/web boundaries stubbed."""
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace


def run(root,bot):
    root=Path(root).absolute();assert (root/'.synthetic-default-instance').read_text()=='SYNTHETIC'
    source=Path(__file__).resolve().parents[1];blocked=[];pair_active=[False];pair=socket.socketpair
    def own_pair(*a,**k):
        pair_active[0]=True
        try:return pair(*a,**k)
        finally:pair_active[0]=False
    socket.socketpair=own_pair
    forbidden=[source/'data',source/'extensions',source.parent/'bots',source.parent/'SnowLuma',Path('D:/AI/TTS'),Path('D:/AI/local-service')]
    def audit(event,args):
        if event=='open' and isinstance(args[0],(str,bytes)):
            path=Path(os.fsdecode(args[0])).absolute()
            if any(path==p or p in path.parents for p in forbidden):
                blocked.append(event);raise RuntimeError('real runtime/config forbidden')
        if event=='socket.connect' and pair_active[0] and args[1][0]=='127.0.0.1':return
        if event in ('socket.connect','socket.getaddrinfo','subprocess.Popen','os.system'):
            blocked.append(event);raise RuntimeError('external IO forbidden')
    sys.addaudithook(audit)
    import launch
    import bot_launcher_child
    from telyincore import plugins
    calls=[];owned_workers=[]
    def wake(runtime_root, **kwargs):
        calls.append('wake')
        if (root/'.synthetic-plugin-root-stop').exists():
            from threading import Event, Thread
            class Worker:
                def __init__(self):
                    self.done=Event();self.stops=0
                    self.thread=Thread(target=self.done.wait,name='OWN-SYNTHETIC-PLUGIN',daemon=True)
                def start(self):self.thread.start()
                def stop(self):
                    self.stops+=1;self.done.set();self.thread.join(3)
            folder=Path(runtime_root)/'extensions'/'synthetic-root-stop';folder.mkdir(parents=True)
            (folder/'package.json').write_text(json.dumps({'name':'synthetic-root-stop','main':'index.py'}))
            (folder/'index.py').write_text("def create(ctx):\n    return ctx['synthetic_worker']()\n")
            def make():
                worker=Worker();owned_workers.append(worker);return worker
            host=dict(kwargs.get('host') or {},synthetic_worker=make)
            result=plugins.carry(runtime_root,'synthetic-root-stop',host=host)
            assert result['ok'],result
        return {'up':[],'down':[]}
    plugins.wake=wake
    plugins.gathered=lambda *a,**k:{'specs':[],'said':[]}
    plugins.reader=lambda *a,**k:lambda:{'plugins':[],'wired':True}
    plugins.every_market_reader=lambda *a,**k:lambda:{'plugins':[]}
    plugins.warm_market=lambda *a,**k:None
    launch._arm_the_next_beat=launch._may_dream_tonight=launch._arm_the_follow_up=lambda *a,**k:None
    def report(name,data):
        p=root/name;t=p.with_suffix('.new')
        with t.open('x',encoding='utf-8') as f:json.dump(data,f);f.flush();os.fsync(f.fileno())
        os.replace(t,p)
    finished=[]
    async def serve(console,**kw):
        assert not plugins._lifecycle_confined.is_set() and calls==['wake']
        async def read():await asyncio.Event().wait()
        console.read=read
        import inspect
        fields=inspect.signature(console.assemble).parameters
        console.assemble(read=read, **{k:v for k,v in kw.items() if k in fields and k!='read'})
        assert console.app.timers is kw['timers']
        stop_requested = asyncio.Event()
        kw['on_start'](SimpleNamespace(begin_operator_shutdown=stop_requested.set))
        assert kw['read_infinity']('/infinity/bots')['active']==bot
        report('default-assembled.json',{'actual_launch_main':True,'web_stub':True,'plugin_boundaries_stubbed':True,
            'confinement_enabled':False,'blocked_io':blocked,
            'direct_options':[x for x in sys.argv[3:] if x.startswith('--counterpart=') or x in ('--channel-context-v1','--cache-prefix-diagnostics')]})
        try:
            await stop_requested.wait()
            return 130  # synthetic parent-requested exit; final drain remains below
        finally:
            await console.stop()
            finished.append('serve-finally')
    async def terminal(root_arg,**kw):
        assert not plugins._lifecycle_confined.is_set() and calls==['wake']
        # Use the real binding surface: a two-field namespace omits _record_event
        # and crashes before stdin assembly, looking like a broken private pipe.
        from telyincore.console import Console
        from telyincore.bus import WakeBus
        import inspect
        running=Console(ready=asyncio.Event())
        running._prepare_plugin_media=kw['prepare_plugin_media']
        fields=inspect.signature(running.assemble).parameters
        running.assemble(root=root_arg,**{k:v for k,v in kw.items() if k in fields and k!='root'})
        assert running.app.timers is kw['timers']
        kw['on_assembled'](running)  # actual launch callback installs yielding stdin reader
        line=await running.read()
        assert line=='Q SYNTHETIC USER INPUT\n'
        report('default-console-assembled.json',{'actual_launch_main':True,'console_boundary_stubbed':True,
            'original_user_stdin':True,'confinement_enabled':False,'blocked_io':blocked,
            'real_console':type(running) is Console,'real_wake_bus':type(running.bus) is WakeBus,
            'event_record_bound':running.affect_prepare.record.__self__ is running,
            'active_batch_empty':running._active_batch==(),
            'real_ingress_binding':launch.PluginIngress.bind.__module__=='telyincore.plugin_ingress'})
        try:
            if (root/'.synthetic-console-natural').exists():
                await asyncio.sleep(.02)  # finite synthetic stdin producer gets a loop turn
                return 1
            await asyncio.Event().wait()
        finally:
            await running.stop()
            finished.append('console-finally')
    launch.console_main=terminal
    launch.serve=serve
    try:return bot_launcher_child.main(sys.argv[3:])
    except BaseException:
        import traceback
        report('default-error.json',{'traceback':traceback.format_exc()})
        raise
    finally:
        assert not blocked,blocked
        if sys.exception() is None:assert finished==(['console-finally'] if '--console' in sys.argv else ['serve-finally'])
        worker_rows=[{'stops':w.stops,'alive':w.thread.is_alive()} for w in owned_workers]
        report('default-finished.json',{'serve_finally':finished==['serve-finally'],'console_finally':finished==['console-finally'],'blocked_io':blocked,'plugin_workers':worker_rows})
        if (root/'.synthetic-plugin-root-stop').exists():
            assert worker_rows==[{'stops':1,'alive':False}],worker_rows


if __name__=='__main__':raise SystemExit(run(sys.argv[1],sys.argv[2]))
