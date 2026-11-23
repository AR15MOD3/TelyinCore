"""Exercise the distributed plugin without an account, bridge, or remote endpoint."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from qq_host_runtime import QQPanel
from telyincore import plugins

CORE = Path(__file__).resolve().parents[1]
QQ = "core-infinity-qq"


class OptionalQQTests(unittest.TestCase):
    def test_first_install_is_off_and_preserves_operator_choice(self):
        from types import SimpleNamespace
        from scripts.linux_profile_start import prepare_qq
        from scripts.linux_acceptance import BASELINE, BOT
        from plugin_scope_bootstrap import initial_plugin_choices
        from plugin_scope_host import make_plugin_scope
        from plugin_scope_migration import initialize_plugin_scope

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            root = workspace / "core"
            def prepare():
                prepare_qq(CORE, root, workspace, BOT, SimpleNamespace(setup=False))
                scope = make_plugin_scope(root, BOT, BASELINE)
                initialize_plugin_scope(scope, root, BASELINE,
                                        initial_choices=initial_plugin_choices(root, BOT, BASELINE))
                return scope
            scope = prepare()
            self.assertTrue((root / "extensions" / QQ / "package.json").is_file())
            self.assertIs(scope.read_state()["enabled"][QQ], False)
            for expected in (True, False):
                scope.update({QQ: expected})
                self.assertIs(prepare().read_state()["enabled"][QQ], expected)

    @unittest.skipUnless(sys.platform == "linux", "Linux service adapter")
    def test_linux_wake_reuses_or_starts_the_service(self):
        from types import SimpleNamespace
        from scripts import linux_qq_lifecycle as linux_qq
        with tempfile.TemporaryDirectory() as tmp:
            core = Path(tmp) / "core"
            (core / "linux-components").mkdir(parents=True)
            control = SimpleNamespace(release_core=core, management_port=5099, action=Mock())
            ensure = linux_qq.LinuxQQControl.ensure_snowluma_on_wake
            with patch.object(linux_qq, "listener_pid", side_effect=[12, None, 24, None, None]):
                self.assertTrue(ensure(control))
                control.action.assert_not_called()
                self.assertTrue(ensure(control))
                control.action.assert_called_once_with("snowluma_start_service")
                with self.assertRaisesRegex(ValueError, "尚未确认"):
                    ensure(control)

    def test_windows_wake_reuses_running_service_and_honors_disable(self):
        from threading import RLock
        from types import SimpleNamespace
        from infinity_control import QQControl
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "index.mjs").touch()
            control = object.__new__(QQControl)
            control.root = control.snowluma_root = root
            control.snow_instance = SimpleNamespace(root=root)
            control._process_lifecycle = None
            control._companion_lifecycle = Mock()
            control._snow_lifecycle = control._lock = RLock()
            control._owned = {}
            control._snow_stopping = False
            control._snow_rejected_pins = []
            control._require_open = control._remember_snowluma = Mock()
            start = control._companion_lifecycle.start_companion
            with patch("service_layout.node_executable", return_value="node"):
                control.start_snowluma_with_bot(enabled=True)
                handle = start.return_value
                handle.poll.return_value = None
                control.ensure_snowluma_on_wake()
                start.assert_called_once()
                handle.poll.return_value = 0
                control.start_snowluma_with_bot(enabled=False)
                start.assert_called_once()
                control.ensure_snowluma_on_wake()
                self.assertEqual(start.call_count, 2)

    def test_switch_and_fault_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "core"
            config = Path(tmp) / "test-account.json"
            probe = root / "extensions" / "demo-probe"
            probe.mkdir(parents=True)
            manifest = dict(name=probe.name, version="1.0.0", main="index.py",
                            keywords=["core-infinity"], telyincore=dict(kind="infinity", api=1))
            (probe / "package.json").write_text(json.dumps(manifest))
            (probe / "index.py").write_text(
                "def create(ctx):\n"
                "    assert 'qq_config_path' not in ctx\n"
                "    assert '_plugin_start_checks' not in ctx and '_plugin_contexts' not in ctx\n"
                "    return object()\n")
            prepare = Mock()
            host = {"_plugin_contexts": {QQ: {"qq_config_path": str(config)}},
                    "_plugin_start_checks": {QQ: prepare}}
            def switch(name, on):
                return plugins.switch(root, name, on=on, host=host)
            def live():
                return dict(plugins._active_items())
            def tools():
                return {tool.name for tool in plugins.gathered(root)["specs"]}

            self.assertTrue(switch(probe.name, True)["ok"])
            self.addCleanup(plugins.put_out, root, probe.name)
            self.assertIn(probe.name, plugins.wake(root, host=host)["up"])
            other = live()[probe.name]
            prepare.assert_not_called()
            shutil.copytree(CORE / "builtin/infinity" / QQ, probe.parent / QQ)
            self.assertNotIn("qq_send_text", tools())
            config.write_text("{invalid")
            self.assertFalse(switch(QQ, True)["ok"])
            self.assertIn(QQ, dict(plugins.wake(root, host=host)["down"]))
            self.assertIs(live()[probe.name], other)
            self.assertNotIn("qq_send_text", tools())
            config.write_text(json.dumps(dict(httpUrl="http://127.0.0.1:1", privates=["10001"],
                                              groups=[], ttsEnabled=False, transcribeVoice=False)))
            for _ in range(2):
                up = switch(QQ, True)
                self.assertTrue(up["ok"], up["said"])
                try:
                    self.assertIn("qq_send_text", tools())
                finally:
                    self.assertTrue(switch(QQ, False)["ok"])
                self.assertNotIn("qq_send_text", tools())
                self.assertIs(live()[probe.name], other)
            self.assertTrue(switch(probe.name, False)["ok"])

    def test_panel_lifetime(self):
        factory = Mock()
        panel = QQPanel(factory)
        panel.disable()
        panel.close()
        factory.assert_not_called()
        control = panel.get()
        panel.disable()
        control.stop_owned_bridge.assert_called_once_with()
        control.close.assert_called_once_with()
        panel.close()
        self.assertEqual(factory.call_count, 1)

    def test_disabled_panel_does_not_start_services(self):
        factory = Mock()
        panel = QQPanel(factory)
        for action in ("snowluma_start_service", "snowluma_recover", "asr_start", "tts_start"):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "请先启用"):
                panel.action("/process", {"action": action}, bot="Test", enabled=lambda: False,
                             reload_adapter=None, describe_upload=None)
        factory.assert_not_called()

    def test_optional_windows_setup_failure(self):
        from contextlib import ExitStack
        from scripts import windows_acceptance as launcher
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp) / "core"
            for file in ("dist/web/index.html", ".venv/Scripts/python.exe"):
                path = root / file
                path.parent.mkdir(parents=True)
                path.touch()
            def run(command):
                return 9 if any(str(item).endswith("setup_qq_runtime.py") for item in command) else 0
            for name, value in {"CORE":root, "retire_legacy_manager": Mock(),
                                "supported_runtime":lambda: True, "fast_environment_ready":lambda: False,
                                "startup_header":Mock(), "notice":Mock()}.items():
                stack.enter_context(patch.object(launcher, name, value))
            stack.enter_context(patch("telyincore.package_updates.package_updates.launcher_check", return_value="ok"))
            calls = stack.enter_context(patch.object(launcher, "run", side_effect=run))
            self.assertEqual(launcher.main(["--start"]), 0)
            self.assertIn("--run", calls.call_args.args[0])
            self.assertEqual(launcher.main(["--check"]), 9)


if __name__ == "__main__":
    unittest.main()
