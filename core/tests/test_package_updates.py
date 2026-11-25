import io
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler

import pytest

from telyincore import package_updates as updates
from telyincore.web import WebService


def test_new_release_requires_three_segments_and_legacy_versions_still_upgrade():
    from telyincore.distribution_version import validate_release_version

    validate_release_version("beta.0.5.5")
    validate_release_version("beta.0.2.1")
    for old in ("beta.0.5.4.9", "beta.0.5.4.10"):
        with pytest.raises(ValueError, match="三段"):
            validate_release_version(old)
        assert updates.version_key(old) < updates.version_key("beta.0.5.5")


def installed(root, platform, version):
    schema = ("windows" if platform == "win" else platform) + "-core-acceptance-v1"
    (root / "candidate.json").write_text(json.dumps({"schema": schema, "version": version}))


def release(tag, *, draft=False):
    return {"tag_name": tag, "draft": draft, "prerelease": True}


@pytest.mark.parametrize(("platform", "current", "expected"), [
    ("win", "beta.0.5.4.9", "available"), ("win", "beta.0.5.4.10", "current"),
    ("linux", "beta.0.1.1", "ahead"), ("linux", "beta.0.1.0.0", "current")])
def test_published_platform_versions(tmp_path, monkeypatch, platform, current, expected):
    installed(tmp_path, platform, current)
    pages = [[release("win-beta.0.5.4.9"), release("linux-beta.0.1.0"), release("win-beta.9.9.9", draft=True)],
             [release("win-beta.0.5.4.10"), release("unrelated")]]
    calls = []
    def open_page(request, timeout, proxy):
        calls.append(request.full_url)
        assert 'Authorization' not in request.headers
        return io.BytesIO(json.dumps(pages[len(calls) - 1]).encode())
    monkeypatch.setattr(updates, "_PAGE_SIZE", 3)
    monkeypatch.setattr("telyincore.download_sources.open_public", open_page)
    checker = updates.PackageUpdates(tmp_path, platform, "fixture/Core")
    result = checker.check()
    assert result["status"] == expected
    assert result["releaseUrl"].startswith(f"https://github.com/fixture/Core/releases/tag/{platform}-")
    assert len(calls) == 2 and checker.check() == result and len(calls) == 2


def test_starts_without_browser_and_serves_cached_status(tmp_path, monkeypatch):
    installed(tmp_path, "linux", "beta.0.1.0")
    started, finish = Event(), Event()
    calls = []
    def remote(repository, platform):
        calls.append(1)
        started.set()
        assert finish.wait(3)
        return {"latest": "beta.0.1.1"}
    checker = updates.PackageUpdates(tmp_path, "linux", "fixture/Core", remote)
    with WebService(port=0, package_updates=checker) as service:
        try:
            assert started.wait(1)
        finally:
            finish.set()
        def read(_):
            op = build_opener(ProxyHandler({}))
            return json.load(op.open(service.url("/package-update"), timeout=3))
        with ThreadPoolExecutor(4) as workers:
            assert all(row["status"] == "available" for row in workers.map(read, range(4)))
        assert len(calls) == 1


def test_unavailable_is_distinct_from_up_to_date(tmp_path):
    installed(tmp_path, "win", "beta.0.5.4.1")
    def offline(*_):
        raise HTTPError("https://api.github.com/", 403, "limited", {}, None)
    assert updates.PackageUpdates(tmp_path, "win", "fixture/Core", offline).check()["status"] == "unavailable"
    assert updates.PackageUpdates(tmp_path, "win", "", offline).check()["status"] == "unconfigured"
    assert updates.PackageUpdates(tmp_path, "linux", "fixture/Core", offline).check()["status"] == "unknown"


def test_launcher_handoff_and_wake_and_manual_refresh(tmp_path):
    import time
    from urllib.request import Request

    installed(tmp_path, "linux", "beta.0.1.0")
    seen = []
    def fetch(repo, platform):
        seen.append(platform)
        return {"latest": f"beta.0.1.{len(seen)}"}
    launcher = updates.PackageUpdates(tmp_path, "linux", "fixture/Core", fetch)
    assert "发现新版 beta.0.1.1" in launcher.launcher_check()
    checker = updates.PackageUpdates(tmp_path, "linux", "fixture/Core", fetch)
    gated = []
    op = build_opener(ProxyHandler({}))
    with WebService(port=0, package_updates=checker, set_gate=gated.append) as svc:
        read = lambda: json.load(op.open(svc.url("/package-update"), timeout=3))
        assert read()["latest"] == "beta.0.1.1"
        assert len(seen) == 1
        checker._checked_at -= 10
        for path in ("/pause", "/run"):
            url = svc.url(path)
            with op.open(Request(url, data=b"", headers={"Origin":svc.url("/")[:-1]}), timeout=3):
                pass
        for _ in range(100):
            if read()["latest"] == "beta.0.1.2":
                break
            time.sleep(.01)
        assert gated == [True, False] and len(seen) == 2
        checker._checked_at -= 10
        with op.open(svc.url("/package-update?refresh=1"), timeout=3) as response:
            assert json.load(response)["latest"] == "beta.0.1.3"


def test_periodic_without_browser_stops_with_service(tmp_path, monkeypatch):
    import time
    assert updates.CHECK_INTERVAL == 1800
    monkeypatch.setattr(updates, "CHECK_INTERVAL", .10)
    installed(tmp_path, "win", "beta.0.5.4")
    seen, second = [], Event()
    def fetch(repo, platform):
        seen.append(platform)
        if len(seen) == 2:
            second.set()
        return {"latest": "beta.0.5.4.1"}
    with WebService(port=0, package_updates=updates.PackageUpdates(tmp_path, "win", "fixture/Core", fetch)):
        assert second.wait(2)
    count = len(seen)
    time.sleep(.25)
    assert len(seen) == count


def test_download_matches_published_asset_and_survives_launcher_handoff(tmp_path, monkeypatch):
    installed(tmp_path, "linux", "beta.0.1.0")
    tag = "linux-beta.0.1.1"
    name = updates.overlay_name("linux", "beta.0.1.0", "beta.0.1.1")
    row = {**release(tag), "assets": [{"name": name, "state": "uploaded", "size": 100}]}
    monkeypatch.setattr("telyincore.download_sources.open_public", lambda req, timeout, proxy: io.BytesIO(json.dumps([row]).encode()))
    def checker():
        return updates.PackageUpdates(tmp_path, "linux", "fixture/Core")
    launcher = checker()
    launcher.launcher_check()
    assert checker().check()["downloadUrl"] == f"https://github.com/fixture/Core/releases/download/{tag}/{name}"
    row["assets"][0]["state"] = "starter"
    assert "downloadUrl" not in checker().check(force=True)
    row["assets"][0].update(state="uploaded", name=updates.overlay_name("win", "beta.0.1.0", "beta.0.1.1"))
    assert "downloadUrl" not in checker().check(force=True)


def test_acceptance_is_opt_in_and_cannot_replace_stable(tmp_path,monkeypatch):
    installed(tmp_path, 'linux', 'beta.0.1.0')
    rows=[release('linux-beta.0.1.2'),release('linux-acceptance-beta.0.1.1')]
    rows[1]['assets']=[dict(name=updates.overlay_name('linux','beta.0.1.0','beta.0.1.1'),state='uploaded',size=1,label='core-online-update-v1')]
    monkeypatch.setattr('telyincore.download_sources.open_public',lambda req,timeout,proxy:io.BytesIO(json.dumps(rows).encode()))
    def read():return updates.PackageUpdates(tmp_path,'linux','fixture/Core').check()
    assert read()['latest']=='beta.0.1.2'
    (tmp_path/'runtime').mkdir(exist_ok=True)
    (tmp_path/'runtime/release-channel.json').write_text('{"channel":"acceptance"}')
    result=read()
    assert result['latest']=='beta.0.1.1' and result['installable']
    assert '/linux-acceptance-beta.0.1.1/' in result['downloadUrl']


def test_acceptance_build_channel_survives_manual_update(tmp_path):
    (tmp_path / 'candidate.json').write_text('{"release_channel":"acceptance"}')
    assert updates.installed_channel(tmp_path) == "acceptance"
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/release-channel.json").write_text('{"channel":"stable"}')
    assert updates.installed_channel(tmp_path) == "stable"
