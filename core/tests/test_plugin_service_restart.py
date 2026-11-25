import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from telyincore import plugin_service_restart as r
from telyincore.web import WebService


def manager(tmp_path):
    row={'name':'demo','restart_required':True,'restart_action':'enable'}
    restart=r.PluginServiceRestart(tmp_path,'One',lambda:{'plugins':[row]})
    restart.capability=r.offer(os.getppid())  # isolated supported-parent fixture
    return restart,row


def test_request_is_bound_to_current_process_and_consumed_once(tmp_path,monkeypatch):
    svc,row=manager(tmp_path);svc.request('demo','enable',7799)
    path=r._path(tmp_path,os.getpid());assert path.exists()
    pid=os.getpid();parent=os.getppid()
    monkeypatch.setattr(r,'os',SimpleNamespace(getpid=lambda:parent))
    assert r.consume(tmp_path,pid,'One',7799,svc.capability,exit_code=0)
    assert not r.consume(tmp_path,pid,'One',7799,svc.capability,exit_code=0)


@pytest.mark.parametrize('change',['failed-exit','wrong-bot','wrong-port','wrong-token','expired'])
def test_bad_handoff_never_relaunches(tmp_path,monkeypatch,change):
    svc,_=manager(tmp_path);svc.request('demo','enable',7799);pid=os.getpid();parent=os.getppid()
    path=r._path(tmp_path,pid)
    if change=='expired':
        data=json.loads(path.read_bytes());data['at']-=r.TTL+1;path.write_text(json.dumps(data))
    monkeypatch.setattr(r,'os',SimpleNamespace(getpid=lambda:parent))
    assert not r.consume(tmp_path,pid,'Other' if change=='wrong-bot' else 'One',
        7800 if change=='wrong-port' else 7799,r.offer(parent) if change=='wrong-token' else svc.capability,
        exit_code=1 if change=='failed-exit' else 0)
    assert not path.exists()


def test_unsupported_or_changed_state_does_not_write(tmp_path):
    svc,row=manager(tmp_path);row['restart_action']='disable'
    with pytest.raises(ValueError,match='状态已变化'):svc.request('demo','enable',7799)
    svc.capability=''
    with pytest.raises(ValueError):svc.request('demo','disable',7799)
    assert not (tmp_path/'data/plugin-restarts').exists()


def test_http_instance_and_origin_guard_and_shutdown_receipt(tmp_path):
    restart,_=manager(tmp_path)
    service=WebService(port=0,read_plugins=lambda:{'wired':True,'plugins':[]},plugin_restart=restart)
    service._shutdown_callback=lambda:None
    seen=[];service.request_shutdown=lambda **kw:seen.append(kw) or True
    service.start();host,port=service.bound;url=f'http://{host}:{port}'
    try:
        with httpx.Client(trust_env=False) as client:
            state=client.get(url+'/plugins').json()
            assert state['runtime']['bot']=='One'
            token=state['runtime']['instance']
            body={'name':'demo','action':'enable'}
            assert client.post(url+'/plugins/restart',json=body).status_code==403
            assert client.post(url+'/plugins/restart',json=body,headers={'Origin':url,'X-Telyin-Shutdown-Instance':'old'}).status_code==409
            assert not seen
            response=client.post(url+'/plugins/restart',json=body,headers={'Origin':url,'X-Telyin-Shutdown-Instance':token})
            assert response.status_code==202 and response.json()['accepted']
            assert len(seen)==1
    finally:service.stop()


def test_launcher_restarts_only_after_owned_cleanup_and_does_not_open_browser(monkeypatch):
    import startup
    import bot_global_admission
    import launcher_exit_log
    from contextlib import contextmanager
    calls=[]
    @contextmanager
    def logging(*a):yield
    class Admission:
        def __init__(self,*a):pass
        def __enter__(self):calls.append('enter');return self
        def finish(self,code):calls.append('finish')
        def __exit__(self,*a):calls.append('retired')
    def owned(argv,**kw):
        calls.append(argv)
        if len([x for x in calls if isinstance(x,list)])==1:
            kw['plugin_handoff'].append(('One',7799))
        return 0
    monkeypatch.setattr(bot_global_admission,'StartupAdmission',Admission)
    monkeypatch.setattr(launcher_exit_log,'launcher_exit_log',logging)
    monkeypatch.setattr(startup,'_main_owned',owned)
    monkeypatch.setattr(startup.subprocess,'call',lambda *a,**kw:pytest.fail('must not apply Core update'))
    assert startup.main(['--bot','One'])==0
    assert calls==['enter',['--bot','One'],'finish','retired','enter',['--bot','One','--port','7799','--no-browser'],'finish','retired']


def test_launcher_cleanup_failure_prevents_second_birth(monkeypatch):
    import startup,bot_global_admission,launcher_exit_log
    from contextlib import nullcontext
    calls=[]
    class Admission:
        def __init__(self,*a):pass
        def __enter__(self):return self
        def finish(self,code):raise RuntimeError('cleanup not confirmed')
        def __exit__(self,*a):pass
    def owned(*a,**kw):calls.append(1);kw['plugin_handoff'].append(('One',7799));return 0
    monkeypatch.setattr(bot_global_admission,'StartupAdmission',Admission)
    monkeypatch.setattr(launcher_exit_log,'launcher_exit_log',lambda *a:nullcontext())
    monkeypatch.setattr(startup,'_main_owned',owned)
    with pytest.raises(RuntimeError,match='cleanup'):startup.main([])
    assert calls==[1]


def test_first_install_is_disabled_and_does_not_import(tmp_path):
    from telyincore import market,plugins
    source=tmp_path/'source';source.mkdir()
    (source/'package.json').write_text(json.dumps({'name':'first-demo','main':'index.py','version':'1.0.0',
        'keywords':['core-infinity'],'telyincore':{'kind':'infinity','api':1,'reload':'restart'}}))
    (source/'index.py').write_text("raise RuntimeError('install must not import')")
    root=tmp_path/'core'
    assert market.install_from(root,source)['ok']
    row=plugins.installed(root)['plugins'][0]
    assert not row['enabled'] and not row['loaded'] and not row['restart_required']
