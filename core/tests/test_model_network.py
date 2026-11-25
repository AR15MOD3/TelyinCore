import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
from types import SimpleNamespace
import httpx
import pytest
from telyincore import model_proxy as policy
from telyincore.model_network import ModelNetwork, ModelFallbackTransport, AsyncModelFallbackTransport, client_options, ModelProbeOpener


def test_policy_scope_and_legacy_save(tmp_path):
    state=policy.read_settings(tmp_path)
    assert not state['model_fallback']
    state=policy.save_settings(tmp_path,{**state,'enabled':True,'model_fallback':True,'proxy_url':'http://127.0.0.1:7893','providers':['openai']})
    assert policy.model_network(tmp_path,'https://api.openai.com/v1')==ModelNetwork(state['proxy_url'],True)
    for url in ['http://localhost:8000/v1','https://relay.example/v1','https://api.anthropic.com/v1','https://api.openai.com:444/v1']:
        assert policy.model_network(tmp_path,url)==ModelNetwork()
    state.pop('model_fallback');state=policy.save_settings(tmp_path,state)
    assert state['model_fallback']
    before=(tmp_path/'model-proxy.json').read_bytes()
    for changes in [{'model_fallback':'true'},{'enabled':False,'model_fallback':True}]:
        with pytest.raises(policy.ProxySettingsError):policy.save_settings(tmp_path,{**state,**changes})
        assert (tmp_path/'model-proxy.json').read_bytes()==before
    state.pop('model_fallback');state['enabled']=False
    assert not policy.save_settings(tmp_path,state)['model_fallback']

@pytest.mark.parametrize('error,allowed',[(httpx.ConnectError,True),(httpx.ConnectTimeout,True),(httpx.ProxyError,True),(httpx.ReadError,False),(httpx.ReadTimeout,False),(httpx.WriteError,False),(httpx.WriteTimeout,False),(httpx.RemoteProtocolError,False)])
@pytest.mark.parametrize('asynchronous',[False,True])
def test_error_boundary(error,allowed,asynchronous):
    seen=[]
    def primary(req):raise error('fixture')
    def direct(req):seen.append('direct');assert req.headers['authorization']=='Bearer fixture';return httpx.Response(200)
    cls=AsyncModelFallbackTransport if asynchronous else ModelFallbackTransport
    transport=cls('unused',primary=httpx.MockTransport(primary),direct=httpx.MockTransport(direct))
    async def run():
        async with httpx.AsyncClient(transport=transport,trust_env=False) as c:
            if allowed:assert (await c.post('https://api.openai.com/v1/responses',headers={'Authorization':'Bearer fixture'},json={})).status_code==200
            else:
                with pytest.raises(error):await c.post('https://api.openai.com/v1/responses',json={})
    if asynchronous:asyncio.run(run())
    else:
        with httpx.Client(transport=transport,trust_env=False) as c:
            if allowed:assert c.post('https://api.openai.com/v1/responses',headers={'Authorization':'Bearer fixture'},json={}).status_code==200
            else:
                with pytest.raises(error):c.post('https://api.openai.com/v1/responses',json={})
    assert bool(seen)==allowed

@pytest.mark.parametrize('method,allowed',[(b'POST',False),(b'CONNECT',True),(None,False)])
@pytest.mark.parametrize('asynchronous',[False,True])
def test_headers_boundary(method,allowed,asynchronous):
    seen=[];traces=[]
    def direct(req):seen.append('direct');return httpx.Response(200)
    def primary(req):
        req.extensions['trace']('http11.send_request_headers.started',{'request':SimpleNamespace(method=method)})
        raise httpx.ConnectError('fixture')
    async def async_primary(req):
        await req.extensions['trace']('http2.send_request_headers.started',{'request':SimpleNamespace(method=method)})
        raise httpx.ConnectError('fixture')
    async def old_trace(event,info):traces.append(event)
    async def run():
        async with httpx.AsyncClient(transport=AsyncModelFallbackTransport('unused',primary=httpx.MockTransport(async_primary),direct=httpx.MockTransport(direct)),trust_env=False) as c:
            req=c.build_request('POST','https://api.openai.com/v1/responses',extensions={'trace':old_trace})
            if allowed:await c.send(req)
            else:
                with pytest.raises(httpx.ConnectError):await c.send(req)
    if asynchronous:asyncio.run(run())
    else:
        with httpx.Client(transport=ModelFallbackTransport('unused',primary=httpx.MockTransport(primary),direct=httpx.MockTransport(direct)),trust_env=False) as c:
            req=c.build_request('POST','https://api.openai.com/v1/responses',extensions={'trace':lambda e,i:traces.append(e)})
            if allowed:c.send(req)
            else:
                with pytest.raises(httpx.ConnectError):c.send(req)
    assert bool(seen)==allowed and len(traces)==1

@pytest.mark.parametrize('status',[401,403,429,500,503])
def test_http_status_never_falls_back(status):
    seen=[]
    with httpx.Client(transport=ModelFallbackTransport('unused',primary=httpx.MockTransport(lambda r:httpx.Response(status)),direct=httpx.MockTransport(lambda r:seen.append(r))),trust_env=False) as c:
        assert c.get('https://api.openai.com/v1/models').status_code==status
    assert not seen

def test_fallback_rechecks_host_admission():
    seen=[]
    def primary(req):raise httpx.ConnectError('fixture')
    def check():raise RuntimeError('owner closed')
    with httpx.Client(transport=ModelFallbackTransport('unused',primary=httpx.MockTransport(primary),direct=httpx.MockTransport(lambda r:seen.append(r)),check=check),trust_env=False) as c:
        with pytest.raises(RuntimeError,match='owner closed'):c.get('https://api.openai.com/v1/models')
    assert not seen

def test_real_refused_proxy_delivers_one_post_and_probe_adapter():
    from urllib.request import Request
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        url=f'http://127.0.0.1:{server.server_address[1]}/test'
        with socket.socket() as dead:
            dead.bind(('127.0.0.1',0));network=ModelNetwork(f'http://127.0.0.1:{dead.getsockname()[1]}',True)
            async def run():
                async with httpx.AsyncClient(**client_options(network,asynchronous=True),trust_env=False,timeout=2) as c:
                    assert (await c.post(url,content=b'fixture')).status_code==200
            asyncio.run(run())
            with ModelProbeOpener(network).open(Request(url,data=b'probe'),timeout=2) as r:
                assert r.status==200 and r.read(1)==b'{' and r.read(10)==b'}'
        assert seen==[b'fixture',b'probe']
    finally:server.shutdown();server.server_close();worker.join()

@pytest.mark.parametrize('cancel',[False,True])
def test_core_transport_uses_policy_and_checks_cancellation(tmp_path,monkeypatch,cancel):
    from telyincore import model_network as network
    from telyincore.engine import Engine
    from telyincore.transport import HttpxTransport, PreOpenCancelled
    policy.save_settings(tmp_path,{**policy.read_settings(tmp_path),'enabled':True,'model_fallback':True,'proxy_url':'http://127.0.0.1:7893'})
    signal=asyncio.Event();seen=[]
    async def primary(req):
        if cancel:signal.set()
        raise httpx.ConnectError('fixture')
    async def direct(req):seen.append(req.url);return httpx.Response(200,content=b'fixture')
    monkeypatch.setattr(network.httpx,'AsyncHTTPTransport',lambda **kw:httpx.MockTransport(primary if kw.get('proxy') else direct))
    async def run():
        transport=HttpxTransport(proxy_of=Engine(tmp_path,env={}).model_network)
        try:
            if cancel:
                with pytest.raises(PreOpenCancelled):
                    async for _ in transport.stream_chat('https://api.openai.com/v1',{},frame_idle_ms=1000,signal=signal):pass
            else:
                assert b''.join([v async for v in transport.stream_chat('https://api.openai.com/v1',{},frame_idle_ms=1000,signal=signal)])==b'fixture'
        finally:await transport.aclose()
    asyncio.run(run());assert bool(seen)!=cancel
