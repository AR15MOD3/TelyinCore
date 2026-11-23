"""Exercise the distributed plugin without an account, bridge, or remote endpoint."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from qq_host_runtime import QQPanel
from telyincore import plugins

CORE = Path(__file__).resolve().parents[1]
QQ = "core-infinity-qq"


class OptionalQQTests(unittest.TestCase):
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
