import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from deployment_relocation import path_digest, relocate_deliveries
from telyincore import download_sources as ds
from telyincore.package_updates import PackageUpdates


@pytest.mark.parametrize("mirror, proxy, origin, expected", [
    (True, True, False, "镜像直连成功"),
    (False, True, True, "镜像直连：超时 → GitHub 官方经下载代理 127.0.0.1:18888成功"),
    (False, False, True, "镜像直连：超时 → GitHub 官方直连成功"),
    (False, True, False, "镜像直连：超时 → GitHub 官方经下载代理 127.0.0.1:18888：连接被拒绝"),
])
def test_check_reports_actual_request_route(tmp_path, monkeypatch, mirror, proxy, origin, expected):
    (tmp_path/'candidate.json').write_text('{"schema":"windows-core-acceptance-v1","version":"beta.0.5.4.2"}')
    address = "http://127.0.0.1:18888" if proxy else None
    monkeypatch.setattr(ds, "selected_proxy", lambda *args: address)
    calls = []
    def request(req, timeout, via):
        calls.append(via)
        if len(calls) == 1 and not mirror:
            raise URLError(TimeoutError())
        if len(calls) == 2 and not origin:
            raise URLError(ConnectionRefusedError())
        return io.BytesIO(b'[{"tag_name":"win-beta.0.5.4.2","draft":false}]')
    monkeypatch.setattr(ds, "open_public", request)
    checker = PackageUpdates(tmp_path, 'win', 'official/test')
    assert expected in checker.launcher_check()
    cached = PackageUpdates(tmp_path, 'win', 'official/test').check()
    assert expected in cached.get('checkRoute', cached.get('message',''))
    assert calls == ([None] if mirror else [None, address])


def setup_moved(tmp_path):
    bot = 'Marble'
    old, new = tmp_path/'original folder', tmp_path/'new location'
    core = new/'core'
    (core/'.venv').mkdir(parents=True)
    (core/'.venv/pyvenv.cfg').write_text('command = python -m venv '+str(old/'core/.venv'))
    base = core/'data'
    base.mkdir()
    home = new/'bots'/bot/'memory'
    home.mkdir(parents=True)
    (home.parent/'.runtime-binding.json').write_text(json.dumps(dict(name=bot,token='a'*32)))
    (base/'bot-runtime-bindings.json').write_text(json.dumps(dict(version=1,bindings={bot:'a'*32})))
    (base/'bot-runtime-owner.json').write_text(json.dumps(dict(baseline=bot)))
    store = SimpleNamespace(log=SimpleNamespace(root=base/'events'),get=lambda cursor: SimpleNamespace(to_line=lambda:'line'))
    old_binding = path_digest(old/'core/data/events')
    return core, bot, home, store, old_binding


def seed(home, source, binding, records):
    p=home/source/'pending.json'
    p.parent.mkdir()
    p.write_text(json.dumps(dict(schema=2,binding=binding,generation='1'*32,records=records)))
    return p


def test_moved_install_retains_queued_and_processing_without_offering(tmp_path):
    core, bot, home, store, old = setup_moved(tmp_path)
    rows={('2'*32):dict(state='QUEUED',cursor=1,digest=hashlib.sha256(b'line').hexdigest()),
          ('3'*32):dict(state='PROCESSING',cursor=2,digest=hashlib.sha256(b'line').hexdigest())}
    p=seed(home,'ingress-delivery',old,rows)
    seed(home,'local-observation-delivery',old,{})
    assert len(relocate_deliveries(core,bot,home,store))==2
    assert json.loads(p.read_bytes())['records']==rows
    assert json.loads(p.read_bytes())['binding']==path_digest(store.log.root)
    assert len(list((core.parent/'runtime/relocation-backups').rglob('*.json')))==2
    assert not relocate_deliveries(core,bot,home,store)


@pytest.mark.parametrize('entry',[dict(state='APPENDING'),dict(state='QUEUED',cursor=2,digest='4'*64)])
def test_moved_journals_do_not_partially_rebind_on_uncertainty(tmp_path,entry):
    core,bot,home,store,old=setup_moved(tmp_path)
    first=seed(home,'ingress-delivery',old,{})
    second=seed(home,'local-observation-delivery',old,{'3'*32:entry})
    before=first.read_bytes(),second.read_bytes()
    with pytest.raises(ValueError):relocate_deliveries(core,bot,home,store)
    assert (first.read_bytes(),second.read_bytes())==before


def test_unrelated_install_not_rebound(tmp_path):
    core,bot,home,store,old=setup_moved(tmp_path)
    p=seed(home,'ingress-delivery','0'*64,{})
    before=p.read_bytes()
    with pytest.raises(ValueError,match='原安装路径'):relocate_deliveries(core,bot,home,store)
    assert p.read_bytes()==before


def test_relocated_companion_cannot_stop_a_job(tmp_path, monkeypatch):
    import os
    from bot_companion_custody import check_previous_companion
    import bot_companion_custody as custody
    import bot_host_observer
    from snowluma_instance import instance_root

    core, bot, _, _, _ = setup_moved(tmp_path)
    path = instance_root(core, bot)/'companion-custody.json'
    path.parent.mkdir(parents=True)
    previous = tmp_path/'original folder'/'services/SnowLuma'
    path.write_text(json.dumps(dict(version=2, nonce='a'*32, host_pid=123, host_created=1,
                                    installation=hashlib.sha256(os.path.normcase(str(previous)).encode()).hexdigest())))
    monkeypatch.setattr(bot_host_observer, 'probe_process', lambda pid: {'state':'absent'})
    def still_exists(name, *, stop_orphan=False):
        assert not stop_orphan
        return False
    monkeypatch.setattr(custody, '_job_gone', still_exists)
    original=path.read_bytes()
    assert '尚未确认退出' in check_previous_companion(core, bot)
    assert path.read_bytes()==original


def test_existing_previous_deployment_keeps_records(tmp_path):
    core,bot,home,store,old=setup_moved(tmp_path)
    old_program=tmp_path/'original folder/core/launch.py'
    old_program.parent.mkdir(parents=True);old_program.touch()
    p=seed(home,'ingress-delivery',old,{})
    data=p.read_bytes()
    with pytest.raises(ValueError,match="原安装目录仍有 Core"):
        relocate_deliveries(core,bot,home,store)
    assert p.read_bytes()==data


def test_only_known_moved_managed_asr_models_are_remapped(tmp_path):
    from deployment_relocation import effective_asr_directory
    core, *_ = setup_moved(tmp_path)
    root = core/'models'
    new = root/'voice/asr/example'
    new.mkdir(parents=True)
    old = str(tmp_path/'original folder/core/models/voice/asr/example')
    external = str(tmp_path/'other/core/models/voice/asr/example')
    assert effective_asr_directory(old, root) == old
    for f in ('model.int8.onnx', 'tokens.txt'):
        (new/f).touch()
    assert effective_asr_directory(old, root) == str(new)
    assert effective_asr_directory(external, root) == external


def test_missing_asr_keeps_qq_and_disables_transcription(tmp_path):
    import importlib.util
    package = Path(__file__).resolve().parents[1]/'builtin/infinity/core-infinity-qq'
    spec = importlib.util.spec_from_file_location('qa_qq_config', package/'index.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    settings = json.loads((package/'config.example.json').read_bytes())
    settings.update(privates=["12345"], transcribeVoice=True, asrModelDir=str(tmp_path/'missing'))
    values = module._config(tmp_path/'config.json', model_root=tmp_path/'models', snapshot=settings)
    assert values[7] is False and values[9] == ''
