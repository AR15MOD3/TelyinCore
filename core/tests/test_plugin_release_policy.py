import json
import pytest
from telyincore import plugin_releases as r, plugin_versions as v, market, github
from telyincore.plugin_updates import PluginUpdates, remote_manifest
from test_plugin_versions import package, archive
from test_plugin_updates import install


def test_only_stable_releases_and_no_tag_or_branch_fallback(monkeypatch):
    data=[{'tag_name':'v3.2.0','prerelease':True}, {'tag_name':'v4.0.0','draft':True},
          {'tag_name':'v2.0.0-rc.1'}, {'tag_name':'main'}, {'tag_name':'v1.0.0'}]
    calls=[]
    def ask(path):
        calls.append(path)
        return data
    assert r.latest_release('a/b',ask)['version']=='1.0.0'
    assert all(p.startswith('releases?') for p in calls)
    monkeypatch.setattr(github,'_ask',lambda *a:[])
    assert github._an_archive('a','b','main')==('','暂无正式发布')
    monkeypatch.setattr(github,'_ask',lambda *a:(_ for _ in ()).throw(OSError()))
    assert github._an_archive('a','b','main')[0]==''


def test_old_development_cache_invalidated(tmp_path):
    install(tmp_path,version='0.2.3')
    c=PluginUpdates(tmp_path,lambda repo:{'release_status':'none'})
    c._save({'schema':1,'cache':{'owner/demo':{'name':'demo','version':'0.3.2','checkedAt':c.clock()}},'attempts':[]})
    assert c.read()['available']==0
    c.check_batch()
    assert c.read()['rows'][0]['status']=='unreleased'
    assert c.read()['available']==0


def test_no_releases_does_not_read_development_manifest(tmp_path,monkeypatch):
    def ask(url,*a,**kw):
        assert '/releases?' in url
        return []
    monkeypatch.setattr('telyincore.download_sources.github_json',ask)
    assert remote_manifest('owner/demo',tmp_path)=={'release_status':'none'}


@pytest.mark.parametrize('row',[{'tag_name':'v1.0.0','prerelease':True},{'tag_name':'v1.0.0','draft':True},{}])
def test_install_rejects_unreleased_before_download(tmp_path,monkeypatch,row):
    monkeypatch.setattr(v,'_ask',lambda *a,**kw:row)
    monkeypatch.setattr(market,'_fetch',lambda *a:pytest.fail('must not download'))
    result=market.install_url(tmp_path,'https://github.com/owner/demo/archive/refs/tags/v1.0.0.tar.gz')
    assert not result['ok']
    with pytest.raises(ValueError):v.install_version(tmp_path,'owner/demo','v1.0.0','demo')


def test_first_install_is_pinned_and_manifest_matches_tag(tmp_path,monkeypatch):
    calls=[]
    def ask(root,repo,path,**kw):
        assert kw['official']
        return {'tag_name':'v1.0.0'} if path.startswith('releases/') else {'sha':'a'*40}
    monkeypatch.setattr(v,'_ask',ask)
    def fetch(url,*a):calls.append(url);return archive(package())
    monkeypatch.setattr(market,'_fetch',fetch)
    result=market.install_url(tmp_path,'https://github.com/owner/demo/archive/refs/tags/v1.0.0.tar.gz')
    assert result['ok']
    assert calls==['https://github.com/owner/demo/archive/'+'a'*40+'.tar.gz']
    assert (tmp_path/'extensions/demo/package.json').exists()
    other=tmp_path/'other'
    monkeypatch.setattr(market,'_fetch',lambda *a:archive(package(version='9.0.0')))
    assert not market.install_url(other,'https://github.com/owner/demo/archive/refs/tags/v1.0.0.tar.gz')['ok']
    assert not (other/'extensions/demo').exists()


def test_old_market_cache_and_development_urls_rejected(tmp_path):
    path=tmp_path/'cache.json';path.write_text(json.dumps({'found':[{'url':'https://github.com/a/b/archive/refs/heads/main.tar.gz'}]}))
    assert github._on_disk(path) is None
    assert not market.install_url(tmp_path,'https://github.com/a/b/archive/refs/heads/main.tar.gz')['ok']


def test_tag_history_not_exposed(tmp_path):
    with pytest.raises(ValueError):v.history(tmp_path,'owner/demo',kind='tags')
