import io
import json
from urllib.error import URLError, HTTPError

import pytest
from telyincore import download_sources as ds, model_proxy as mp, online_install as oi

API = 'https://api.github.com/repos/example/core/releases'
PROXY = 'http://127.0.0.1:7893'


def settings(root, **options):
    value = dict(enabled=False, providers=['openai'], proxy_url=PROXY,
                 downloads_enabled=True, market_enabled=False)
    value.update(options)
    (root/'model-proxy.json').write_text(json.dumps(value))
    return value


@pytest.mark.parametrize('source', ['mirror', 'official'])
@pytest.mark.parametrize('mode', ['direct', 'required', 'fallback'])
def test_priority_and_transport_are_independent(tmp_path, source, mode):
    settings(tmp_path, downloads_source=source, downloads_enabled=mode!='direct', downloads_fallback=mode=='fallback')
    network = ds.public_network(tmp_path)
    official = [(API, None)] if mode=='direct' else [(API, PROXY)]
    if mode=='fallback': official.append((API, None))
    mirror = [(ds.GITHUB_API_MIRROR+API, None)]
    assert list(ds.github_routes(API, network)) == (mirror+official if source=='mirror' else official+mirror)
    assert list(ds.github_routes(API, network, official_only=True)) == official
    # Per-download explicit source does not unexpectedly select another source.
    assert all(s=='official' for s,p in ds.download_routes(tmp_path, preferred='official'))


def test_old_configuration_remains_strict_and_old_editor_preserves_routes(tmp_path):
    legacy = settings(tmp_path)
    assert ds.public_network(tmp_path) == ds.PublicNetwork(proxy=PROXY)
    live = mp.read_settings(tmp_path)
    live.update(downloads_fallback=True, downloads_source='official')
    saved = mp.save_settings(tmp_path, live)
    old_editor = {**legacy, 'revision': saved['revision']}
    saved = mp.save_settings(tmp_path, old_editor)
    assert saved['downloads_fallback'] and saved['downloads_source']=='official'
    old_editor.update(revision=saved['revision'], downloads_enabled=False)
    saved = mp.save_settings(tmp_path, old_editor)
    assert not saved['downloads_fallback']


def test_model_api_never_inherits_public_fallback(tmp_path):
    settings(tmp_path, enabled=True, downloads_fallback=True)
    assert mp.proxy_for(tmp_path, 'https://api.openai.com/v1/responses') == PROXY
    assert mp.proxy_for(tmp_path, 'http://127.0.0.1:1234/v1') is None
    assert ds.public_network(tmp_path, 'market').proxy is None


def test_bad_settings_fail_without_silent_direct_access(tmp_path):
    settings(tmp_path, downloads_fallback='yes')
    with pytest.raises(mp.ProxySettingsError): ds.public_network(tmp_path)


def test_official_first_can_fall_back_to_mirror(tmp_path, monkeypatch):
    settings(tmp_path, downloads_source='official', downloads_fallback=True)
    seen=[]
    def request(req, timeout, proxy):
        seen.append((req.full_url,proxy))
        if req.full_url==API: raise URLError(ConnectionRefusedError())
        return io.BytesIO(b'[]')
    monkeypatch.setattr(ds,'open_public',request)
    assert ds.github_json(API,{},ds.public_network(tmp_path)) == []
    assert seen==[(API,PROXY),(API,None),(ds.GITHUB_API_MIRROR+API,None)]


def test_update_digest_uses_official_direct_after_dead_proxy(tmp_path, monkeypatch):
    settings(tmp_path, downloads_fallback=True)
    tag='win-beta.0.5.4.7';name='example.zip';url='https://github.com/example/core/releases/download/'+tag+'/'+name
    seen=[]
    asset=dict(name=name,browser_download_url=url,state='uploaded',label='core-online-update-v1',size=3,digest='sha256:'+'a'*64)
    def request(req, timeout, proxy):
        seen.append((req.full_url,proxy))
        if proxy: raise URLError(ConnectionRefusedError())
        assert req.full_url==API  # no mirror-provided digest is trusted
        return io.BytesIO(json.dumps(dict(tag_name=tag,draft=False,assets=[asset])).encode())
    monkeypatch.setattr(ds,'open_public',request)
    assert oi.release_asset(API,tag,url,name,{'User-Agent':'test'},ds.public_network(tmp_path))==(3,'a'*64)
    assert seen==[(API,PROXY),(API,None)]


def test_unreachable_official_does_not_accept_mirror_digest(tmp_path, monkeypatch):
    settings(tmp_path, downloads_fallback=True)
    seen=[]
    def request(req, timeout, proxy):
        seen.append((req.full_url,proxy))
        assert not req.full_url.startswith(ds.GITHUB_API_MIRROR)
        raise URLError(ConnectionRefusedError())
    monkeypatch.setattr(ds,'open_public',request)
    with pytest.raises(ValueError,match='核验官方摘要失败'):
        oi.release_asset(API,'tag','https://github.com/example/core/a.zip','a.zip',{'User-Agent':'test'},ds.public_network(tmp_path))
    assert len(seen)==4 and [p for _,p in seen]==[PROXY,None,PROXY,None]


def test_explicit_proxy_only_does_not_fall_back(tmp_path, monkeypatch):
    settings(tmp_path)
    seen=[]
    def request(req,timeout,proxy):
        seen.append(proxy)
        raise URLError(ConnectionRefusedError())
    monkeypatch.setattr(ds,'open_public',request)
    with pytest.raises(URLError): ds.github_json(API,{},ds.public_network(tmp_path),official_only=True)
    assert seen==[PROXY]


def test_pip_proxy_fallback_cleans_inherited_proxy_environment(tmp_path, monkeypatch):
    settings(tmp_path, downloads_source='official', downloads_fallback=True)
    monkeypatch.setenv('ALL_PROXY','http://ambient:7890')
    monkeypatch.setenv('PIP_EXTRA_INDEX_URL','https://other.invalid')
    seen=[]
    def run(command,env):
        seen.append((command,env));return 1 if len(seen)==1 else 0
    assert ds.pip_install(['python','-m','pip','install','example'],run,tmp_path)==0
    assert len(seen)==2
    assert seen[0][1]['HTTPS_PROXY']==PROXY
    assert seen[1][1]['NO_PROXY']=='*'
    assert all('ALL_PROXY' not in env and 'PIP_EXTRA_INDEX_URL' not in env for _,env in seen)
    assert 'HTTPS_PROXY' not in seen[1][1]


def test_market_archive_official_proxy_falls_back_to_direct(monkeypatch):
    from telyincore import market, plugin_network
    url='https://github.com/example/repo/archive/refs/heads/main.tar.gz'
    seen=[]
    class DeadProxy:
        def open(self,request,timeout):
            seen.append((request.full_url,PROXY))
            raise URLError(ConnectionRefusedError())
    monkeypatch.setattr(plugin_network,'opener',lambda proxy: DeadProxy())
    def direct(request,timeout):
        seen.append((request.full_url,None))
        return io.BytesIO(b'official-archive')
    monkeypatch.setattr(ds,'open_public',direct)
    assert market._fetch(url,ds.PublicNetwork('official',PROXY,True))==b'official-archive'
    assert seen==[(url,PROXY),(url,None)]
