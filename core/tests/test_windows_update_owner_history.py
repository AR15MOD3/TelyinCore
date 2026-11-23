import json

import pytest

from bot_global_admission import ENTRY_FILES
import bot_lifecycle_boot
from telyincore.online_install import apply_pending


@pytest.mark.parametrize("created, expected", [(1, None), (6_000_000_000, "本次开机的启动记录")])
def test_windows_update_checks_boot_boundary_before_opening_package(tmp_path, monkeypatch, created, expected):
    core = tmp_path / "core"
    owners = core / "data/host-start-owners"
    owners.mkdir(parents=True)
    marker = owners / ("a" * 32 + ".json")
    raw = json.dumps({"version": 1, "owner": {"pid": 123, "state": "alive", "created_filetime": created,
                      "image": str(core / "python.exe")}, "entry_sources": {n: "a"*64 for n in ENTRY_FILES}}).encode()
    marker.write_bytes(raw)
    monkeypatch.setattr(bot_lifecycle_boot, "boot_filetime", lambda: 5_000_000_000)
    # A missing archive makes it observable that preflight allowed the older boot.
    with pytest.raises(ValueError if expected else FileNotFoundError, match=expected):
        apply_pending(tmp_path, "win", manual_package=tmp_path/"absent.zip")
    assert marker.read_bytes() == raw
    assert not (tmp_path/"runtime/package-updates/pending.json").exists()
