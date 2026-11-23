import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request
from telyincore import online_install as install
from telyincore.package_updates import PackageUpdates, overlay_name
from telyincore.web import WebService


def pack(root):
    base, latest = 'beta.0.1.0', 'beta.0.1.1'
    stub = {'platform':'linux','schema':'linux-core-acceptance-v1','version':base}
    old = json.dumps(stub).encode()
    new = json.dumps({**stub, 'version':latest}).encode()
    (root/'candidate.json').write_bytes(old)
    (root/'core').mkdir()
    (root/'core/main.py').write_bytes(b'old')
    files = [('candidate.json',old,new),('core/main.py',b'old',b'new')]
    blob = io.BytesIO()
    with zipfile.ZipFile(blob,'w') as z:
        for n,_,data in files:z.writestr('payload/'+n,data)
        z.writestr('UPDATE.json',json.dumps({'schema':'core-online-update-v1',
            'platform':'linux','from_version':base,'to_version':latest,'files':[
            dict(path=n,old_sha256=install.sha(a),new_sha256=install.sha(b)) for n,a,b in files]}))
    return base,latest,blob.getvalue()


def test_control_download_baseline_check_restart_and_conflict(tmp_path,monkeypatch):
    base,latest,data=pack(tmp_path)
    name=overlay_name('linux',base,latest)
    repo='test/Core'
    tag='linux-'+latest
    url=f'https://github.com/{repo}/releases/download/{tag}/{name}'
    asset=dict(name=name,browser_download_url=url,state='uploaded',label='core-online-update-v1',size=len(data),
               digest='sha256:'+hashlib.sha256(data).hexdigest())
    release={'tag_name':tag,'draft':False,'assets':[asset]}
    def upstream(req,timeout,proxy):
        if req.full_url.startswith("https://gh-proxy.com/"):
            return io.BytesIO(b"corrupt mirror")
        assert req.full_url in (f'https://api.github.com/repos/{repo}/releases/tags/{tag}',url)
        return io.BytesIO(data if req.full_url==url else json.dumps(release).encode())
    monkeypatch.setattr('telyincore.download_sources.open_public',upstream)
    checker=PackageUpdates(tmp_path,'linux',repo,lambda *_:dict(latest=latest,assets=[name],native_assets=[name]))
    op=build_opener(ProxyHandler({}))
    with WebService(port=0,package_updates=checker) as server:
        endpoint=server.url('/package-update/stage')
        def post(origin):
            req=Request(endpoint,data=b'{}',headers={'Origin':origin,'Content-Type':'application/json'})
            return json.load(op.open(req,timeout=5))
        with pytest.raises(HTTPError):post('https://foreign.invalid')
        assert not install.pending(tmp_path)
        with pytest.raises(HTTPError):
            backup=asset['digest'];asset['digest']='sha256:'+'0'*64
            post(server.url().rstrip('/'))
        asset['digest']=backup
        assert not install.pending(tmp_path)
        assert post(server.url().rstrip('/'))['pendingVersion']==latest
        assert json.load(op.open(server.url('/package-update')))['pendingVersion']==latest
    p=tmp_path/'core/main.py';p.write_bytes(b'my edits')
    with pytest.raises(ValueError,match='本机程序文件'):install.apply_pending(tmp_path,'linux')
    assert json.loads((tmp_path/'candidate.json').read_bytes())['version']==base
    p.write_bytes(b'old')
    assert install.apply_pending(tmp_path,'linux')
    assert p.read_bytes()==b'new' and not install.pending(tmp_path)
    assert json.loads((tmp_path/'candidate.json').read_bytes())['version']==latest


def test_web_checksum_can_authenticate_mirrored_metadata(monkeypatch):
    from telyincore import download_sources
    name = 'Telyincore_linux_update.zip'
    tag = 'linux-beta.0.1.3'
    url = 'https://github.com/test/Core/releases/download/' + tag + '/' + name
    api = 'https://api.github.com/repos/test/Core/releases/tags/' + tag
    digest = 'a' * 64
    asset = dict(name=name, browser_download_url=url, state='uploaded', label='core-online-update-v1',
                 size=3, digest='sha256:' + digest)
    def read(req, timeout, proxy):
        if req.full_url == api:
            raise HTTPError(api, 429, 'limit', {}, None)
        if req.full_url == url[:-4] + '.sha256':
            return io.BytesIO(f'{digest}  {name}\n'.encode())
        assert req.full_url == download_sources.GITHUB_API_MIRROR + api
        return io.BytesIO(json.dumps({'tag_name':tag,'draft':False,'assets':[asset]}).encode())
    monkeypatch.setattr(download_sources, 'open_public', read)
    assert install.release_asset(api, tag, url, name, {'User-Agent':'test'}, None) == (3, digest)
    asset['digest'] = 'sha256:' + 'b' * 64
    with pytest.raises(ValueError, match='校验文件不符'):
        install.release_asset(api, tag, url, name, {'User-Agent':'test'}, None)
