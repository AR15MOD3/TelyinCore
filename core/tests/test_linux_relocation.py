import json
import os
from pathlib import Path
import subprocess

import pytest
from scripts.linux_relocation import interpreter_script, reconcile_marker


def test_move_and_second_move(tmp_path,monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path/'xdg'))
    core=tmp_path/'first/core'; ws=core/'.linux-acceptance'; marker=ws/'acceptance.json'
    store=ws/'bots/IRIS/infinity/processes/snowluma-recovery.json'
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({'targetUin':'123456'}))
    base=ws/'core/data'; base.mkdir(parents=True)
    (base/'bot-runtime-owner.json').write_text('{"baseline":"AcceptanceBaseline"}')
    from bot_configured_account import ConfiguredAccountReservation
    ConfiguredAccountReservation(ws,base).reserve('123456',store,lambda:None)
    def desc(core):
        return dict(version=1,mode='linux-profile-acceptance',bot='IRIS',source_core=str(core))
    marker.write_text(json.dumps(desc(core)))
    for new in ('with spaces','third'):
        moved=tmp_path/new/'core'
        core.parent.rename(moved.parent)
        ws=moved/'.linux-acceptance';marker=ws/'acceptance.json'
        reconcile_marker(moved,ws,marker,desc(moved))
        assert json.loads(marker.read_bytes())==desc(moved)
        ConfiguredAccountReservation(ws,ws/'core/data').reserve('123456',ws/'bots/IRIS/infinity/processes/snowluma-recovery.json',lambda:None)
        before=marker.stat().st_mtime_ns
        reconcile_marker(moved,ws,marker,desc(moved))
        assert marker.stat().st_mtime_ns==before
        core=moved


def test_copied_profile_keeps_original(tmp_path):
    old=tmp_path/'old/core';old.mkdir(parents=True);(old/'launch.py').touch()
    core=tmp_path/'new/core';ws=core/'.linux-acceptance';ws.mkdir(parents=True)
    marker=ws/'acceptance.json'
    orig={'version':1,'mode':'linux-profile-acceptance','bot':'Telyin','source_core':str(old)}
    marker.write_text(json.dumps(orig))
    with pytest.raises(ValueError):reconcile_marker(core,ws,marker,dict(orig,source_core=str(core)))
    assert json.loads(marker.read_bytes())==orig


def test_relocated_scripts_execute_with_spaces(tmp_path):
    import sys
    first=tmp_path/'first';second=tmp_path/'a $space';third=tmp_path/'third'
    for folder in (second,third):
        folder.mkdir();(folder/'python').symlink_to(sys.executable)
    raw=f'#!{first}/python\nprint(42)\n'.encode()
    for old,new in ((first,second),(second,third)):
        raw=interpreter_script(raw,old,new)
        p=new/'helper';p.write_bytes(raw);p.chmod(0o755)
        assert subprocess.check_output([p]).strip()==b'42'
