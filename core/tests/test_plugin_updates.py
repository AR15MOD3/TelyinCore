import json
from pathlib import Path
from threading import Event
from urllib.error import HTTPError
from urllib.request import build_opener,ProxyHandler
import pytest
from telyincore.plugin_updates import PluginUpdates,Version,repository,TTL,remote_manifest
from telyincore.web import WebService


def install(root,name='demo',version='0.2.0',repo='owner/demo'):
    path=root/'extensions'/name;path.mkdir(parents=True,exist_ok=True)
    (path/'package.json').write_text(json.dumps({'name':name,'version':version,'repository':{'url':'https://github.com/'+repo+'.git'},'telyincore':{'kind':'infinity','api':1}}))
    return path

@pytest.mark.parametrize('a,b',[('0.2.9','0.2.10'),('1.0.0-beta.2','1.0.0-beta.11'),('1.0.0-rc.1','1.0.0'),('1.0.0-1','1.0.0-a')])
def test_semver(a,b): assert Version(a)<Version(b)

def test_metadata_does_not_change_order(): assert Version('1.0.0+build.1')==Version('1.0.0+build.2')

@pytest.mark.parametrize('value',['1.2','01.2.3','1.0.0-beta.01','1.2.3.4',''])
def test_invalid_versions(value):
    with pytest.raises(ValueError):Version(value)

@pytest.mark.parametrize('url',['https://github.com.evil/owner/repo','http://github.com/a/b','https://user:pass@github.com/a/b','file:///tmp/a'])
def test_no_arbitrary_sources(url): assert not repository(url)

def test_origin_conflict_and_builtin_are_not_requested(tmp_path):
    install(tmp_path);install(tmp_path,'native',repo='owner/native')
    b=tmp_path/'builtin/infinity/native';b.mkdir(parents=True);(b/'package.json').write_text('{"name":"native"}')
    (tmp_path/'extensions/.origins.json').write_text(json.dumps({'demo':{'url':'https://github.com/other/repo/archive/refs/heads/main.tar.gz'}}))
    checker=PluginUpdates(tmp_path,lambda r:pytest.fail('must not fetch'))
    checker.check_batch()
    assert {r['status'] for r in checker.read()['rows']}=={'managed','unsupported'}

def test_bundled_qq_is_managed_even_with_old_independent_update_cache(tmp_path):
    name = 'core-infinity-qq'
    bundled = tmp_path/'builtin/infinity'/name
    bundled.mkdir(parents=True)
    (bundled/'package.json').write_text(json.dumps({'name':name,'version':'0.9.0'}))
    install(tmp_path,name,version='0.9.0',repo='AR15MOD3/core-infinity-qq')
    checker=PluginUpdates(tmp_path,lambda repo:pytest.fail('must not fetch independent QQ'))
    checker._save({'schema':1,'attempts':[], 'cache':{'ar15mod3/core-infinity-qq':
        {'name':name,'version':'0.9.2','checkedAt':checker.clock(),'attemptAt':checker.clock()}}})
    assert checker.read()['available']==0
    row=checker.read()['rows'][0]
    assert row['status']=='managed' and not row['repository']
    checker.check_batch()
    assert not checker._state()['cache']


def test_cached_comparison_and_failed_refresh_do_not_claim_latest(tmp_path):
    install(tmp_path);clock=[100000];calls=[]
    def fetch(repo):calls.append(repo);return {'name':'demo','version':'0.2.4'}
    checker=PluginUpdates(tmp_path,fetch,lambda:clock[0]);checker.check_batch()
    assert checker.read()['rows'][0]['status']=='available'
    checker.check_batch();assert len(calls)==1
    clock[0]+=TTL
    checker.fetch=lambda r:(_ for _ in ()).throw(OSError())
    checker.check_batch();row=checker.read()['rows'][0]
    assert row['status']=='available' and row['stale'] and row['error']
    install(tmp_path,version='0.2.4');assert checker.read()['rows'][0]['status']=='current'
    assert checker.read()['rows'][0]['stale']

def test_wrong_remote_name_and_prerelease_not_updates(tmp_path):
    install(tmp_path)
    checker=PluginUpdates(tmp_path,lambda r:{'name':'wrong','version':'1.0.0'});checker.check_batch()
    assert checker.read()['rows'][0]['status']=='unavailable'
    checker=PluginUpdates(tmp_path,lambda r:{'name':'demo','version':'1.0.0-beta'},clock=lambda:9999999999)
    checker.check_batch();assert checker.read()['rows'][0]['status']=='prerelease'

def test_shared_cache_prevents_duplicate_requests_and_limits_batches(tmp_path):
    for n in range(55):install(tmp_path,f'p{n}',repo=f'o/p{n}')
    calls=[];now=[1000000]
    def fetch(repo):calls.append(repo);return {'name':repo.split('/')[1],'version':'1.0.0'}
    one=PluginUpdates(tmp_path,fetch,lambda:now[0]);two=PluginUpdates(tmp_path,fetch,lambda:now[0])
    for _ in range(10): one.check_batch();two.check_batch()
    assert len(calls)==40 and len(set(calls))==40
    now[0]+=3601
    for _ in range(2):two.check_batch()
    assert len(calls)==55 and len(set(calls))==55

def test_corrupt_cache_and_offline_inventory(tmp_path):
    install(tmp_path);p=tmp_path/'data/plugin-updates';p.mkdir(parents=True);(p/'state.json').write_text('bad')
    checker=PluginUpdates(tmp_path,lambda r:(_ for _ in ()).throw(OSError()))
    checker.check_batch();assert checker.read()['rows'][0]['status']=='unavailable'

def test_rate_limit_stops_following_repositories(tmp_path):
    for n in range(4):install(tmp_path,f'p{n}',repo=f'o/p{n}')
    calls=[]
    def limited(repo):calls.append(repo);raise HTTPError('https://api.github.com',403,'limit',{},None)
    checker=PluginUpdates(tmp_path,limited);checker.check_batch();assert len(calls)==1

def test_service_start_without_browser_and_nonblocking_endpoint(tmp_path):
    install(tmp_path);started,finish=Event(),Event()
    def fetch(repo):started.set();assert finish.wait(3);return {'name':'demo','version':'0.2.4'}
    checker=PluginUpdates(tmp_path,fetch)
    with WebService(port=0,plugin_updates=checker) as service:
        try:
            assert started.wait(1)
            result=json.load(build_opener(ProxyHandler({})).open(service.url('/plugin-updates'),timeout=1))
            assert result['checking'] and result['rows'][0]['status']=='unavailable'
        finally: finish.set()

def test_remote_manifest_is_bounded_and_validated(tmp_path,monkeypatch):
    import base64
    raw=json.dumps({'name':'demo','version':'0.2.4','telyincore':{'kind':'infinity','api':1},'keywords':['core-infinity']}).encode()
    def ask(url,*args,**kw):
        if '/releases?' in url: return [{'tag_name':'v0.2.4'}]
        if '/commits/' in url: return {'sha':'a'*40}
        assert url.endswith('contents/package.json?ref='+'a'*40)
        return {'type':'file','size':len(raw),'encoding':'base64','content':base64.b64encode(raw).decode()}
    monkeypatch.setattr('telyincore.download_sources.github_json',ask)
    assert remote_manifest('owner/demo',tmp_path)=={'name':'demo','version':'0.2.4','tag':'v0.2.4','release_status':'stable'}


def test_removal_and_source_change_invalidate_visible_update(tmp_path):
    import shutil
    install(tmp_path)
    c=PluginUpdates(tmp_path,lambda repo:{'name':'demo','version':'0.2.4'})
    c.check_batch();assert c.read()['available']==1
    install(tmp_path,repo='different/demo');assert c.read()['available']==0
    shutil.rmtree(tmp_path/'extensions/demo');assert c.read()['rows']==[]


def test_update_read_never_imports_plugin_code(tmp_path):
    folder=install(tmp_path);(folder/'index.py').write_text('raise RuntimeError("must not execute")')
    c=PluginUpdates(tmp_path,lambda r:{'name':'demo','version':'0.2.4'})
    c.check_batch();assert c.read()['available']==1


def test_busy_service_waits_for_explicit_refresh(tmp_path):
    install(tmp_path);hit=Event();stopped=Event()
    def fetch(repo):hit.set();return {'name':'demo','version':'0.2.4'}
    c=PluginUpdates(tmp_path,fetch);c.warm(stopped,idle=lambda:False)
    assert not hit.wait(.05)
    c.read(refresh=True)
    try:assert hit.wait(2)
    finally:stopped.set();c._thread.join(2)
    assert not c._thread.is_alive()
