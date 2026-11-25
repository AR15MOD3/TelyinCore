"""Real isolated service processes and package locks; no production Bot/launcher."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import httpx
from telyincore.plugin_service_restart import offer,consume

CHILD = r'''
from pathlib import Path
import sys,threading,asyncio
from telyincore import plugins
from telyincore.plugin_service_restart import PluginServiceRestart
from telyincore.web import WebService
root=Path(sys.argv[1]);port=int(sys.argv[2]);stop=threading.Event()
plugins.wake(root)
restart=PluginServiceRestart(root,'Fixture',plugins.reader(root))
restart.capability=sys.argv[3]  # Explicit fixture parent on Linux, not production capability detection.
svc=WebService(port=port,read_plugins=plugins.reader(root),switch_plugin=plugins.switcher(root),
 remove_plugin=lambda name:plugins.remove(root,name),plugin_restart=restart)
async def run():
 svc.bind_shutdown(stop.set)
 svc.start()
 if not await asyncio.to_thread(stop.wait,25):raise RuntimeError('fixture timeout')
 plugins.shutdown_root(root)
 if not await asyncio.to_thread(svc.stop):raise RuntimeError('fixture did not drain')
asyncio.run(run())
'''


def test_same_page_endpoint_survives_real_enable_disable_uninstall_restarts(tmp_path):
    root=tmp_path/'core';package=root/'extensions/demo';package.mkdir(parents=True)
    (package/'package.json').write_text(json.dumps({'name':'demo','version':'1.0.0','main':'index.py',
        'keywords':['core-infinity'],'telyincore':{'kind':'infinity','api':1,'reload':'restart'}}))
    (package/'index.py').write_text('class P:\n def stop(self): pass\ndef create(ctx): return P()\n')
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    url=f'http://127.0.0.1:{port}';procs=[];cap=offer(os.getpid())
    with httpx.Client(trust_env=False,timeout=5) as client:
        def start():
            p=subprocess.Popen([sys.executable,'-c',CHILD,str(root),str(port),cap],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE);procs.append(p)
            until=time.monotonic()+8
            while time.monotonic()<until:
                assert p.poll() is None, p.stderr.read().decode()
                try:return p,client.get(url+'/plugins').json()
                except httpx.TransportError:time.sleep(.05)
            raise AssertionError('fixture did not listen')
        def request(path,body,state):
            return client.post(url+path,json=body,headers={'Origin':url,'X-Telyin-Shutdown-Instance':state['runtime']['instance']})
        try:
            proc,state=start()
            initial=state['runtime']['instance']
            # Each restart keeps the same URL, but produces a proven new service instance.
            for action in ['enable','disable','enable','remove']:
                row=state['plugins'][0]
                path='/plugins/remove' if action=='remove' else '/plugins/switch'
                body={'name':'demo'} if action=='remove' else {'name':'demo','on':action=='enable'}
                answer=request(path,body,state).json()
                assert answer['restart_required'] and package.exists()
                response=request('/plugins/restart',{'name':'demo','action':action},state)
                assert response.status_code==202
                assert proc.wait(timeout=8)==0
                assert consume(root,proc.pid,'Fixture',port,cap,exit_code=0)
                proc,new=start()
                assert new['runtime']['instance']!=state['runtime']['instance']
                state=new
                if action=='remove':
                    answer=request('/plugins/remove',{'name':'demo'},state).json()
                    assert answer['ok'] and not package.exists()
                else:
                    assert state['plugins'][0]['loaded']==(action=='enable')
                    assert not state['plugins'][0]['restart_required']
            assert state['runtime']['instance']!=initial
        finally:
            for p in procs:
                if p.poll() is None:
                    try:client.post(url+'/shutdown',headers={'Origin':url})
                    except httpx.TransportError:pass
                    try:p.wait(timeout=5)
                    except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=5)
                p.stderr.close()
