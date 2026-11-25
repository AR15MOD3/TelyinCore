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
        assert req.full_url.split('?')[0] in (f'https://api.github.com/repos/{repo}/releases/tags/{tag}',url)
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
    audit = json.loads((install.folder(tmp_path)/'last-install.json').read_bytes())
    assert (audit['base'], audit['version'], audit['source'], audit['route']) == (base, latest, 'official', 'direct')
    assert json.loads((tmp_path/'candidate.json').read_bytes())['version']==latest
    assert checker.installation()['lastInstall'] == {'base': base, 'version': latest, 'source': 'official', 'route': 'direct'}


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


@pytest.mark.parametrize('local,allowed', [(b'reviewed patch', True), (b'other local edit', False)])
def test_exact_reviewed_patch_can_upgrade_without_accepting_other_edits(tmp_path, local, allowed):
    base, latest, data = pack(tmp_path)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        files = {n: z.read(n) for n in z.namelist()}
    meta = json.loads(files['UPDATE.json'])
    meta['files'][1]['old_sha256_alternatives'] = [install.sha(b'reviewed patch')]
    files['UPDATE.json'] = json.dumps(meta).encode()
    package = tmp_path/'patch.zip'
    with zipfile.ZipFile(package, 'w') as z:
        for n, raw in files.items(): z.writestr(n, raw)
    (tmp_path/'core/main.py').write_bytes(local)
    if allowed:
        assert install.apply_pending(tmp_path, 'linux', manual_package=package)
        assert (tmp_path/'core/main.py').read_bytes() == b'new'
    else:
        with pytest.raises(ValueError, match='本机程序文件'):
            install.apply_pending(tmp_path, 'linux', manual_package=package)
        assert (tmp_path/'core/main.py').read_bytes() == local


@pytest.mark.parametrize('alternatives', ['any', [None], ['*'], ['a'*64]*9])
def test_invalid_patch_baselines_rejected(tmp_path, alternatives):
    base, latest, data = pack(tmp_path)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        files = {n: z.read(n) for n in z.namelist()}
    meta = json.loads(files['UPDATE.json'])
    meta['files'][1]['old_sha256_alternatives'] = alternatives
    files['UPDATE.json'] = json.dumps(meta).encode()
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        for n,raw in files.items():z.writestr(n,raw)
    with zipfile.ZipFile(buffer) as z:
        with pytest.raises(ValueError,match='补丁基线'):
            install.inspect_zip(tmp_path,z,'linux',base,latest)


@pytest.mark.parametrize('new_publication', ['none', 'version', 'digest'])
def test_install_rechecks_once_after_baseline_mismatch(tmp_path, monkeypatch, new_publication):
    from telyincore import download_sources
    from telyincore.package_updates import overlay_url

    base, first, original = pack(tmp_path)
    newer = 'beta.0.1.2' if new_publication == 'version' else first
    repo = 'test/Core'
    (tmp_path/'core/main.py').write_bytes(b'reviewed variant')

    def archive(version, repair):
        with zipfile.ZipFile(io.BytesIO(original)) as old:
            data = {n: old.read(n) for n in old.namelist()}
        metadata = json.loads(data['UPDATE.json'])
        metadata['to_version'] = version
        candidate = json.loads(data['payload/candidate.json'])
        candidate['version'] = version
        data['payload/candidate.json'] = json.dumps(candidate).encode()
        for row in metadata['files']:
            if repair and row['path'] == 'core/main.py':
                row['old_sha256'] = install.sha(b'reviewed variant')
            row['new_sha256'] = install.sha(data['payload/'+row['path']])
        data['UPDATE.json'] = json.dumps(metadata).encode()
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, 'w') as z:
            for n, payload in data.items():
                z.writestr(n, payload)
        return blob.getvalue()

    first_archive = archive(first, False)
    second_archive = first_archive if new_publication == 'none' else archive(newer, True)
    reads, transfers, tags = [], [], []
    def network(req, timeout, proxy):
        if '/releases?' in req.full_url:
            assert req.full_url.startswith('https://api.github.com/')
            reads.append(req.full_url)
        second = len(reads) > 1
        version = newer if second else first
        payload = second_archive if second else first_archive
        name = overlay_name('linux', base, version)
        release = {'tag_name': 'linux-'+version, 'draft': False, 'assets': [{
            'name': name, 'label': 'core-online-update-v1', 'state': 'uploaded',
            'digest': 'sha256:'+install.sha(payload), 'size': len(payload),
            'browser_download_url': overlay_url(repo, 'linux', base, version)}]}
        if '/releases?' in req.full_url:
            return io.BytesIO(json.dumps([release]).encode())
        if '/releases/tags/' in req.full_url:
            assert req.full_url.startswith('https://api.github.com/')
            tags.append(req.full_url)
            return io.BytesIO(json.dumps(release).encode())
        assert req.full_url.startswith(download_sources.GITHUB_MIRROR)
        transfers.append(version)
        return io.BytesIO(payload)

    monkeypatch.setattr(download_sources, 'open_public', network)
    checker = PackageUpdates(tmp_path, 'linux', repo)
    if new_publication == 'none':
        with pytest.raises(ValueError, match='core/main.py.*摘要均未变化'):
            checker.prepare_install()
        assert len(transfers) == 1 and not install.pending(tmp_path)
    else:
        assert checker.prepare_install()['version'] == newer
        assert len(transfers) == 2
        assert install.apply_pending(tmp_path, 'linux')
        assert json.loads((tmp_path/'candidate.json').read_bytes())['version'] == newer
    assert len(tags) == len(reads) == 2 and reads[0] != reads[1]


def test_official_unavailable_offers_only_explicit_verified_cached_version(tmp_path):
    base, latest, payload = pack(tmp_path)
    cache = install.folder(tmp_path)
    cache.mkdir(parents=True)
    digest = install.sha(payload)
    archive = cache / (digest + '.zip')
    archive.write_bytes(payload)
    receipt = dict(platform='linux', base=base, version=latest, sha256=digest)
    (cache/'pending.json').write_text(json.dumps(receipt))

    def offline(*_):
        raise OSError('network unavailable')

    checker = PackageUpdates(tmp_path, 'linux', 'fixture/Core', offline)
    opener = build_opener(ProxyHandler({}))
    with WebService(port=0, package_updates=checker) as service:
        service._shutdown_callback = lambda: None
        req = Request(service.url('/package-update/install'), data=b'{}',
                      headers={'Origin':service.url('/').rstrip('/')})
        with pytest.raises(HTTPError) as error:
            opener.open(req,timeout=4)
        answer = json.load(error.value)
        assert '暂时无法确认最新版' in answer['error'] and answer['cachedVersion'] == latest
        assert not service.shutdown_requested and not checker._restart_requested
    assert install.verified_pending(tmp_path, 'linux', latest) == receipt
    with pytest.raises(ValueError, match='已下载的更新已变化'):
        install.verified_pending(tmp_path,'linux','beta.0.1.2')
    archive.write_bytes(b'broken')
    with pytest.raises(ValueError, match='暂存包校验失败'):
        install.verified_pending(tmp_path,'linux',latest)
