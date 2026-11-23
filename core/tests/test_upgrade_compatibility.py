import json

import pytest

from qq_poke import poke_notice, poke_only_addressed
from telyincore import market, plugins
from telyincore.package_updates import PackageUpdates


@pytest.mark.parametrize("minimum, expected", [
    (None, False), ("beta.0.5.4.1", False), ("beta.0.5.4.3", True),
])
def test_declared_wake_floor(tmp_path, minimum, expected):
    (tmp_path/"candidate.json").write_text(json.dumps(dict(schema="windows-core-acceptance-v1", version="beta.0.5.4.2")))
    checker = PackageUpdates(tmp_path, "win", "example/core",
                             lambda *_: dict(latest="beta.0.5.4.99", minimum=minimum))
    assert bool(checker.wake_block()) is expected


def test_unreachable_release_does_not_block_wake(tmp_path):
    def unavailable(*_):
        raise OSError("unreachable")
    (tmp_path/"candidate.json").write_text(json.dumps(dict(schema="windows-core-acceptance-v1", version="beta.0.5.4.2")))
    checker = PackageUpdates(tmp_path, "win", "example/core", unavailable)
    assert not checker.wake_block()


def test_plugin_bounds_and_legacy():
    assert plugins.incompatible_plugin({"name": "legacy"}) == ""
    for current, compatible in [("beta.0.5.4", False), ("beta.0.5.4.2", True),
                                ("beta.0.5.4.7", True), ("beta.0.5.4.9", False)]:
        assert (plugins.incompatible_plugin({"telyincore":{"kind":"infinity", "api":1,
                "coreVersions":{"min":"beta.0.5.4.2","max":"beta.0.5.4.7"}}}, current) == "") is compatible


def test_plugin_rejected_before_install_and_autoload(tmp_path):
    manifest = dict(name="core-infinity-qa", main="index.py", keywords=["core-infinity"],
                    telyincore={"kind":"infinity","api":2})
    source = tmp_path/"source"
    source.mkdir()
    (source/"package.json").write_text(json.dumps(manifest))
    (source/"index.py").write_text("raise AssertionError('incompatible entry executed')")
    core = tmp_path/"core"
    assert not market.install_from(core, source)["ok"]
    installed = core/"extensions"/manifest["name"]
    installed.parent.mkdir(parents=True)
    source.rename(installed)
    row = plugins.switch(core, manifest["name"], on=True)
    assert not row["ok"] and "不兼容" in row["said"]


def test_poke_preserves_sender_and_target():
    normalize = lambda value: str(value) if value else ""
    base = dict(post_type="notice", notice_type="notify", sub_type="poke", group_id=44,
                user_id=11, self_id=22, time=1700000000)
    for target, expected in [(22,True), (33,False)]:
        event = poke_notice(dict(base, target_id=target), [], ["44"], normalize)
        assert event["sender_key"] == "qq:11"
        assert event["meta"]["poke_target"] == str(target)
        assert poke_only_addressed([event], "group", "44") is expected
