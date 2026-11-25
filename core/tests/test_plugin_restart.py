"""Restart-only plugins: real import boundary, persistent intent and package leases."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from telyincore import plugins
from telyincore.plugin_package_lock import PackageLock


@pytest.fixture
def case(tmp_path, monkeypatch):
    for field in ("_loaded", "_lifecycle_states", "_lifecycle_roots", "_instance_setting_scopes",
                  "_package_leases", "_restart_imports", "_restart_pending", "_restart_errors"):
        monkeypatch.setattr(plugins, field, {})
    monkeypatch.setattr(plugins, "_restart_booted_roots", set())
    monkeypatch.setattr(plugins, "_restart_configs", set())
    root = tmp_path / "core"
    folder = root / "extensions" / "demo"
    folder.mkdir(parents=True)
    manifest = {"name": "demo", "version": "1.0.0", "main": "index.py",
                "keywords": ["core-infinity"],
                "telyincore": {"kind": "infinity", "api": 1, "reload": "restart"}}
    (folder / "package.json").write_text(json.dumps(manifest))
    (folder / "index.py").write_text("from pathlib import Path\nPath(__file__).with_name('imported').touch()\nclass P:\n def stop(self): pass\ndef create(ctx): return P()\n")
    yield root, folder, manifest
    plugins.shutdown_root(root)
    for lease in plugins._restart_imports.values():
        lease.close()  # fixture process owns all these pins


def row(root):
    return plugins.installed(root)["plugins"][0]


def test_queue_and_cancel_without_import(case):
    root, folder, _ = case
    plugins.wake(root)
    result = plugins.switch(root, "demo", on=True)
    assert result["ok"] and not result["applied"] and result["restart_required"]
    assert row(root)["enabled"] and not row(root)["loaded"]
    assert row(root)["restart_action"] == "enable"
    assert not (folder / "imported").exists()
    assert not plugins.carry(root, "demo")["ok"]
    assert plugins.wake(root)["down"]  # a repeated wake is not a fresh process
    assert not (folder / "imported").exists()
    assert plugins.switch(root, "demo", on=False)["applied"]
    assert not row(root)["restart_required"]


def boot(root):
    plugins._save_scope_switch(root, None, "demo", True)
    assert plugins.wake(root)["up"] == ["demo"]


def test_disable_stays_running_and_can_be_cancelled(case):
    root, folder, _ = case
    boot(root)
    instance = plugins._loaded["demo"]
    result = plugins.switch(root, "demo", on=False)
    assert result["ok"] and result["restart_required"] and not result["applied"]
    assert row(root)["loaded"] and not row(root)["enabled"]
    assert plugins._loaded["demo"] is instance
    assert plugins.switch(root, "demo", on=True)["applied"]
    assert not row(root)["restart_required"]
    assert not plugins.put_out(root, "demo")["ok"]


def test_uninstall_preserves_files_and_pin_survives_shutdown(case):
    root, folder, _ = case
    boot(root)
    result = plugins.remove(root, "demo")
    assert not result["ok"] and result["restart_required"] and folder.exists()
    assert plugins.shutdown_root(root)["ok"]
    with pytest.raises((OSError, ValueError)):
        with PackageLock(root, "demo", exclusive=True):
            pass


def test_failed_import_also_pins_package(case):
    root, folder, _ = case
    (folder / "index.py").write_text("raise RuntimeError('broken')")
    plugins._save_scope_switch(root, None, "demo", True)
    assert plugins.wake(root)["down"]
    assert not row(root)["loaded"]
    assert not plugins.remove(root, "demo")["ok"]
    assert folder.exists()
    with pytest.raises((OSError, ValueError)):
        with PackageLock(root, "demo", exclusive=True): pass


@pytest.mark.parametrize("mode", [False, None, "unknown", {}, []])
def test_invalid_reload_is_not_silently_hot(case, mode):
    root, folder, manifest = case
    manifest["telyincore"]["reload"] = mode
    (folder / "package.json").write_text(json.dumps(manifest))
    assert not plugins.switch(root, "demo", on=True)["ok"]
    assert not plugins.carry(root, "demo")["ok"]
    assert row(root)["contract"] and not row(root)["enabled"]
    assert not (folder / "imported").exists()
    assert plugins.remove(root, "demo")["ok"]


def test_legacy_remains_hot(case):
    root, folder, manifest = case
    del manifest["telyincore"]["reload"]
    (folder / "package.json").write_text(json.dumps(manifest))
    assert plugins.switch(root, "demo", on=True)["ok"]
    assert row(root)["loaded"] and row(root)["reload_mode"] == "hot"
    assert plugins.switch(root, "demo", on=False)["ok"]
    assert not row(root)["loaded"] and not row(root)["restart_required"]


def test_persistence_across_three_real_processes(case):
    root, folder, _ = case
    common = "from pathlib import Path; from telyincore import plugins; root=Path(" + repr(str(root)) + "); "
    stages = [
        "plugins.wake(root); assert plugins.switch(root,'demo',on=True)['restart_required']",
        "assert plugins.wake(root)['up']==['demo']; assert plugins.switch(root,'demo',on=False)['restart_required']; assert not plugins.remove(root,'demo')['ok']",
        "assert not plugins.wake(root)['up']; assert not plugins.installed(root)['plugins'][0]['restart_required']; assert plugins.remove(root,'demo')['ok']",
    ]
    for stage in stages:
        subprocess.run([sys.executable, "-c", common + stage], check=True, timeout=20)
    assert not folder.exists()


def test_saved_config_is_deferred_without_restarting_instance(case):
    from plugin_config import PluginConfig
    root, folder, _ = case
    (folder / 'config.example.json').write_text('{"count": 1}')
    boot(root)
    instance = plugins._loaded['demo']
    api = PluginConfig(root, lambda: plugins.installed(root),
                       lambda name: plugins.defer_config_reload(root, name))
    reply = api.save('demo', {'revision': api.read('demo')['revision'],
                             'changes': [{'path': ['count'], 'value': 2}]})
    assert reply['ok'] and reply['restart_required']
    assert '重启' in reply['message'] and '重载插件' not in reply['message']
    assert json.loads((folder / 'config.json').read_text())['count'] == 2
    assert plugins._loaded['demo'] is instance
    assert row(root)['restart_action'] == 'config'
    assert plugins.switch(root, 'demo', on=False)['restart_action'] == 'disable'
    assert plugins.switch(root, 'demo', on=True)['restart_action'] == 'config'


def test_shared_uninstall_checks_deferred_peer_even_if_ok(case, monkeypatch):
    from telyincore import plugin_uninstall as u
    from plugin_scope_host import make_plugin_scope
    root, folder, _ = case
    (root.parent / 'bots' / 'One').mkdir(parents=True)
    scope = make_plugin_scope(root, 'One', 'One')
    monkeypatch.setattr(u, '_peers', lambda *a: [('Two', 9999, lambda: None,
        {'plugins': [{'name': 'demo', 'installed': True}]})])
    monkeypatch.setattr(u, '_request', lambda *a, **kw: {'ok': True, 'applied': False,
                                                       'restart_required': True})
    result = u.uninstall_shared(root, 'demo', scope=scope,
        stop_local=lambda *a: pytest.fail('must stop before local mutation'),
        roster=lambda: {'bots': [{'id': 'One'}]})
    assert not result['ok'] and folder.exists() and '重启' in result['said']
    assert result['stopped_bots'] == []


def test_scoped_state_write_failure_cannot_queue_or_import(case, monkeypatch):
    from plugin_scope_host import make_plugin_scope
    root, folder, _ = case
    (root.parent / 'bots' / 'One').mkdir(parents=True)
    scope = make_plugin_scope(root, 'One', 'One')
    def failed(*a, **kw): raise OSError('read only')
    monkeypatch.setattr(plugins, '_save_scope_switch', failed)
    with pytest.raises(OSError):
        plugins.switch(root, 'demo', on=True, scope=scope, host={'bot_id': 'One'})
    assert not plugins._restart_pending and not (folder / 'imported').exists()


def test_pending_disable_preserves_live_tools(case):
    from telyincore.tools import ToolDef, ToolTag
    root, _, _ = case
    boot(root)
    plugins._loaded['demo'].tools = lambda: [ToolDef(name='restart_probe', description='fixture',
        parameters=None, tags=(ToolTag.READ,), handler=lambda: 'running')]
    before = plugins.gathered(root)
    assert len(before['specs']) == 1
    plugins.switch(root, 'demo', on=False)
    after = plugins.gathered(root)
    assert len(after['specs']) == 1 and after['specs'][0].handler() == 'running'


def test_http_reports_saved_intent_not_applied(case):
    import httpx
    from telyincore.web import WebService
    root, folder, _ = case
    plugins.wake(root)
    service = WebService(port=0, read_plugins=plugins.reader(root),
                         switch_plugin=plugins.switcher(root))
    service.start()
    host, port = service.bound
    url = f'http://{host}:{port}'
    try:
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.post(url + '/plugins/switch', json={'name': 'demo', 'on': True},
                                   headers={'Origin': url})
            assert response.status_code == 200
            assert response.json()['ok'] and response.json()['applied'] is False
            state = client.get(url + '/plugins').json()['plugins'][0]
            assert state['enabled'] and not state['loaded'] and state['restart_required']
            assert not (folder / 'imported').exists()
    finally:
        service.stop()


def test_shared_uninstall_of_running_restart_plugin_keeps_code(case):
    from plugin_scope_host import make_plugin_scope
    from telyincore.plugin_uninstall import uninstall_shared
    root, folder, _ = case
    (root.parent / 'bots' / 'One').mkdir(parents=True)
    scope = make_plugin_scope(root, 'One', 'One')
    host = {'bot_id': 'One'}
    assert plugins.switch(root, 'demo', on=True, scope=scope, host=host)['restart_required']
    assert plugins.wake(root, scope=scope, host=host)['up'] == ['demo']
    result = uninstall_shared(root, 'demo', scope=scope,
        stop_local=lambda name, on: plugins.switch(root, name, on=on, scope=scope, host=host),
        roster=lambda: {'bots': [{'id': 'One'}]})
    assert not result['ok'] and '重启' in result['said'] and folder.exists()
    assert scope.read_state()['enabled']['demo'] is False
    assert plugins.installed(root, scope=scope)['plugins'][0]['loaded']
