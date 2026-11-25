import pytest
from telyincore.engine import Engine
from telyincore.model_proxy import read_settings,save_settings

def test_live_view_keeps_shared_proxy_root(tmp_path):
    shared=tmp_path/'shared';runtime=tmp_path/'bot';runtime.mkdir()
    save_settings(shared,{**read_settings(shared),'enabled':True,'proxy_url':'http://127.0.0.1:7891'})
    view=Engine(shared,env={}).live()
    assert view.proxy_for('https://api.openai.com/v1')=='http://127.0.0.1:7891'
    assert not (runtime/'model-proxy.json').exists()

@pytest.mark.anyio
async def test_console_uses_config_engine_instead_of_runtime_root(tmp_path):
    from telyincore.console import Console
    shared=tmp_path/'shared';runtime=tmp_path/'bot';runtime.mkdir()
    save_settings(shared,{**read_settings(shared),'enabled':True,'proxy_url':'http://127.0.0.1:7891'})
    c=Console()
    # Standalone fixture binds its explicit non-network media resolver; do not
    # weaken the real launcher-only native media preparation guard.
    c._resolve_plugin_image = lambda ref: pytest.fail('proxy-only fixture must not resolve media')
    c.assemble(root=runtime,config=Engine(shared,env={}).live())
    try:
        assert c.transport._proxy_of('https://api.openai.com/v1').proxy=='http://127.0.0.1:7891'
        assert c.transport._proxy_of('http://127.0.0.1:8091/v1').proxy is None
    finally:await c.stop()
