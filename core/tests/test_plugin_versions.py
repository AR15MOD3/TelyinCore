import io
import json
import tarfile
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
import pytest
from telyincore import plugin_versions as v
from telyincore.plugin_updates import PluginUpdates
from telyincore.plugin_package_lock import PackageLock
from telyincore.web import WebService


def package(name='demo',version='1.0.0'):
    return {'name':name,'version':version,'repository':'https://github.com/owner/demo',
        'main':'index.py','keywords':['core-infinity'],'telyincore':{'kind':'infinity','api':1}}


def archive(spec):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w:gz') as tar:
        for name,value in [('package.json',json.dumps(spec)),('index.py','raise RuntimeError("must not import")'),('config.json','{"default":true}')]:
            raw=value.encode();info=tarfile.TarInfo('package/'+name);info.size=len(raw);tar.addfile(info,io.BytesIO(raw))
    return output.getvalue()


def setup(tmp_path,version='2.0.0',name='demo'):
    folder=tmp_path/'extensions'/name;folder.mkdir(parents=True)
    (folder/'package.json').write_text(json.dumps(package(name,version)))
    (folder/'index.py').write_text('old executable')
    (folder/'config.json').write_text('{"mine":true}')
    (folder/'cache').mkdir();(folder/'cache/image.png').write_bytes(b'image')
    return folder


def network(monkeypatch,spec=None):
    calls=[]
    def ask(root,repo,path,**kwargs):
        calls.append((repo,path,kwargs));return {'sha':'a'*40}
    monkeypatch.setattr(v,'_ask',ask)
    monkeypatch.setattr('telyincore.market._fetch',lambda *args:archive(spec or package()))
    return calls


def test_history_pagination_filters_drafts_and_builds_own_links(tmp_path,monkeypatch):
    monkeypatch.setattr(v,'_ask',lambda *a,**kw:[{'tag_name':'v1/old','body':'<script>hi</script>','html_url':'https://evil.test','prerelease':True},{'tag_name':'draft','draft':True}])
    data=v.history(tmp_path,'owner/demo')
    assert len(data['rows'])==1
    row=data['rows'][0]
    assert row['tag']=='v1/old' and row['prerelease']
    assert row['source']=='https://github.com/owner/demo/releases/tag/v1%2Fold'
    assert row['notes']=='<script>hi</script>'
    with pytest.raises(ValueError):v.history(tmp_path,'owner/..')
    with pytest.raises(ValueError):v.history(tmp_path,'owner/demo',0)


def test_version_downgrade_retains_data_and_backup_without_import(tmp_path,monkeypatch):
    folder=setup(tmp_path);calls=network(monkeypatch)
    result=v.install_version(tmp_path,'owner/demo','v1.0.0','demo','2.0.0')
    assert result['ok'] and result['commit']=='a'*40
    assert calls[0][1]=='commits/refs%2Ftags%2Fv1.0.0' and calls[0][2]['official']
    assert json.loads((folder/'package.json').read_text())['version']=='1.0.0'
    assert (folder/'config.json').read_text()=='{"mine":true}'
    assert (folder/'cache/image.png').read_bytes()==b'image'
    backups=list((tmp_path/'data/plugin-retained/demo').glob('*/package'))
    assert len(backups)==1 and (backups[0]/'index.py').read_text()=='old executable'


def test_initial_version_install(tmp_path,monkeypatch):
    network(monkeypatch)
    assert v.install_version(tmp_path,'owner/demo','v1.0.0','demo')['ok']


@pytest.mark.parametrize('reason',['wrong-name','source','changed','running','ready'])
def test_failure_never_replaces_current_package(tmp_path,monkeypatch,reason):
    folder=setup(tmp_path);original=(folder/'index.py').read_bytes()
    network(monkeypatch,package('wrong' if reason=='wrong-name' else 'demo'))
    def call():
        return v.install_version(tmp_path,'other/repo' if reason=='source' else 'owner/demo',
            'v1','demo','0.0.0' if reason=='changed' else '2.0.0',
            ready=(lambda:(_ for _ in ()).throw(ValueError('enabled'))) if reason=='ready' else lambda:None)
    if reason=='source':
        with pytest.raises(ValueError):call()
    elif reason=='running':
        with PackageLock(tmp_path,'demo'):
            assert not call()['ok']
    else: assert not call()['ok']
    assert (folder/'index.py').read_bytes()==original
    assert not list((tmp_path/'extensions').glob('.装到一半-*'))


def test_rename_failure_rolls_back(tmp_path,monkeypatch):
    folder=setup(tmp_path);network(monkeypatch);real=v.os.replace
    def fail(src,dst):
        if Path(src).name.startswith('.装到一半-'):raise OSError('disk error')
        real(src,dst)
    monkeypatch.setattr(v.os,'replace',fail)
    assert not v.install_version(tmp_path,'owner/demo','v1','demo','2.0.0')['ok']
    assert (folder/'index.py').read_text()=='old executable'


def test_builtin_cannot_be_installed_from_independent_version(tmp_path,monkeypatch):
    p=tmp_path/'builtin/infinity/demo';p.mkdir(parents=True);(p/'package.json').write_text(json.dumps(package()))
    monkeypatch.setattr(v,'_ask',lambda *a,**k:pytest.fail('no network'))
    with pytest.raises(ValueError,match='随 Core'):v.install_version(tmp_path,'owner/demo','v1','demo')


def test_core_sync_updates_whole_package_without_changing_bot_choices(tmp_path):
    from telyincore.builtin_plugin_sync import sync_bundled
    folder=setup(tmp_path,version='1.0.0');p=tmp_path/'builtin/infinity/demo';p.mkdir(parents=True)
    (p/'package.json').write_text(json.dumps(package(version='2.0.0')));(p/'index.py').write_text('new executable')
    choices=tmp_path/'extensions/.enabled.json';choices.write_text('{"demo":true}')
    assert sync_bundled(tmp_path)[0]['ok']
    assert (folder/'index.py').read_text()=='new executable'
    assert (folder/'config.json').read_text()=='{"mine":true}' and choices.read_text()=='{"demo":true}'
    assert sync_bundled(tmp_path)==[]


def test_core_sync_waits_for_package_lease(tmp_path):
    from telyincore.builtin_plugin_sync import sync_bundled
    folder=setup(tmp_path,version='1.0.0');p=tmp_path/'builtin/infinity/demo';p.mkdir(parents=True)
    (p/'package.json').write_text(json.dumps(package(version='2.0.0')))
    with PackageLock(tmp_path,'demo'):assert not sync_bundled(tmp_path)[0]['ok']
    assert (folder/'index.py').read_text()=='old executable'


def test_http_history_and_explicit_install_contract(tmp_path,monkeypatch):
    monkeypatch.setattr(v,'_ask',lambda *a,**kw:[])
    calls=[]
    with WebService(port=0,plugin_updates=PluginUpdates(tmp_path),install_plugin=lambda **kw:calls.append(kw) or {'ok':True}) as service:
        op=build_opener(ProxyHandler({}))
        assert json.load(op.open(service.url('/plugin-versions?repository=owner/demo')))['rows']==[]
        selection={'repository':'owner/demo','tag':'v1','name':'demo','current':''}
        req=Request(service.url('/market/install'),data=json.dumps({'version':selection}).encode(),headers={'Origin':service.url(''),'Content-Type':'application/json'})
        assert json.load(op.open(req))['ok'] and calls==[{'version':selection}]
        req=Request(service.url('/market/install'),data=json.dumps({'version':selection,'url':'evil'}).encode(),headers={'Origin':service.url(''),'Content-Type':'application/json'})
        with pytest.raises(HTTPError):op.open(req)
        assert len(calls)==1
