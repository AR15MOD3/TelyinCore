import hashlib
import io
import json
from contextlib import contextmanager

import httpx
import pytest

import asr_install
import tts_install
from telyincore import download_sources as sources
from telyincore import market


def proxy_settings(root):
    (root / "model-proxy.json").write_text(json.dumps({
        "enabled": False, "providers": [], "proxy_url": "http://localhost:12910",
        "market_enabled": False, "downloads_enabled": True,
    }))


def test_pip_mirror_bypasses_ambient_then_uses_download_proxy(tmp_path, monkeypatch):
    proxy_settings(tmp_path)
    monkeypatch.setenv("https_proxy", "http://other:1234")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://other.invalid")
    calls = []
    def run(command, env):
        calls.append((command, env))
        return int(len(calls) == 1)
    assert sources.pip_install(["python", "-m", "pip", "install", "pkg"], run, tmp_path) == 0
    assert [c[c.index("--index-url")+1] for c, _ in calls] == list(sources.PYPI.values())
    assert calls[0][1]["NO_PROXY"] == "*" and "HTTPS_PROXY" not in calls[0][1]
    assert calls[1][1]["HTTPS_PROXY"] == "http://localhost:12910"
    assert all("https_proxy" not in e and "PIP_EXTRA_INDEX_URL" not in e for _, e in calls)


@pytest.mark.parametrize("module,installer", [(asr_install, asr_install.ASRInstaller),
                                             (tts_install, tts_install.TTSInstaller)])
def test_real_voice_installer_resumes_automatically_on_official(tmp_path, monkeypatch, module, installer):
    proxy_settings(tmp_path)
    raw = b"abcdef"
    monkeypatch.setattr(module, "FILES", (("weights", "test/resolve/revision/file", 6,
                                           hashlib.sha256(raw).hexdigest()),))
    requests = []
    @contextmanager
    def stream(client, method, url, headers=None):
        mirror = url.startswith(module.SOURCES["mirror"])
        requests.append((url, client.options, headers or {}))
        class Response:
            status_code = 200 if mirror else 206
            headers = {"content-range": "bytes 3-5/6"}
            def raise_for_status(self):
                pass
            def iter_bytes(self, size):
                if mirror:
                    yield raw[:3]
                    raise httpx.ReadError("interrupted")
                yield raw[3:]
        yield Response()

    class Client:
        def __init__(self, **options):
            self.options = options
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    Client.stream = stream
    monkeypatch.setattr(module.httpx, "Client", Client)
    job = installer(tmp_path, proxy_root=tmp_path)
    job.start()
    assert job.close(timeout=5)
    assert job.read()["phase"] == "done"
    assert (tmp_path / module.NAME / "weights").read_bytes() == raw
    assert len(requests) == 2 and all(not opts["trust_env"] for _, opts, _ in requests)
    assert "proxy" not in requests[0][1]
    assert requests[1][1]["proxy"] == "http://localhost:12910"
    assert requests[1][2]["Range"] == "bytes=3-"


def test_market_rejects_mirror_files_missing_from_official_tree(monkeypatch):
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"unexpected"
        item = tarfile.TarInfo("repo/index.py")
        item.size = len(data)
        tar.addfile(item, io.BytesIO(data))
    url = "https://github.com/example/repo/archive/refs/heads/main.tar.gz"
    calls = []
    def open_request(req, timeout, proxy=None):
        calls.append(req.full_url)
        if req.full_url == url:
            return io.BytesIO(b"official")
        if req.host == "api.github.com":
            return io.BytesIO(b'{"truncated":false,"tree":[]}')
        return io.BytesIO(buf.getvalue())
    monkeypatch.setattr(sources, "open_public", open_request)
    assert market._fetch(url) == b"official"
    assert len(calls) == 3 and calls[0].startswith(sources.GITHUB_MIRROR)
