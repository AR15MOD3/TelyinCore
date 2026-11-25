import json
from urllib.error import HTTPError, URLError
from telyincore import package_updates as updates
from telyincore.download_sources import github_route_text


def candidate(root):
    (root / 'candidate.json').write_text(json.dumps({
        'schema': 'windows-core-acceptance-v1', 'version': 'beta.0.5.4.6'}))


def test_failed_check_retries_earlier_and_recovers(tmp_path, monkeypatch):
    candidate(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(updates.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(updates.time, 'time', lambda: clock[0])
    calls = []
    def fetch(*_):
        calls.append(1)
        if len(calls) == 1: raise ValueError('镜像上游配额已用尽')
        return {'latest': 'beta.0.5.4.7'}
    checker = updates.PackageUpdates(tmp_path, 'win', 'fixture/Core', fetch)
    failed = checker.check()
    assert failed['status'] == 'unavailable'
    assert failed['nextCheckAt'] - failed['checkedAt'] == 300
    clock[0] += 299
    assert checker.check() == failed and len(calls) == 1
    clock[0] += 2
    recovered = checker.check()
    assert recovered['status'] == 'available' and len(calls) == 2
    assert recovered['nextCheckAt'] - recovered['checkedAt'] == 1800
    clock[0] += 301
    assert checker.check() == recovered and len(calls) == 2


def test_launcher_failed_check_keeps_retry_time(tmp_path, monkeypatch):
    candidate(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(updates.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(updates.time, 'time', lambda: clock[0])
    def offline(*_): raise ValueError('暂时无法连接')
    updates.PackageUpdates(tmp_path, 'win', 'fixture/Core', offline).launcher_check()
    calls = []
    def recovered(*_):
        calls.append(1)
        return {'latest': 'beta.0.5.4.7'}
    checker = updates.PackageUpdates(tmp_path, 'win', 'fixture/Core', recovered)
    assert checker.check()['status'] == 'unavailable' and not calls
    clock[0] += 301
    assert checker.check()['status'] == 'available' and len(calls) == 1


def test_only_identified_rate_limit_is_described_as_exhausted():
    url = 'https://api.github.com/repos/fixture/Core/releases'
    limited = HTTPError(url, 403, 'Forbidden', {'X-RateLimit-Remaining': '0'}, None)
    text = github_route_text('https://gh-proxy.org/' + url, url, None, limited)
    assert '镜像上游的GitHub API 配额已用尽' in text
    forbidden = HTTPError(url, 403, 'Forbidden', {}, None)
    assert '限流' not in github_route_text(url, url, None, forbidden)
    text = github_route_text(url, url, 'http://127.0.0.1:7893', URLError(ConnectionRefusedError()))
    assert '检查下载代理' in text


def test_generic_forbidden_is_not_reported_as_rate_limiting(tmp_path):
    candidate(tmp_path)
    def forbidden(*_): raise HTTPError('https://api.github.com', 403, 'Forbidden', {}, None)
    state = updates.PackageUpdates(tmp_path, 'win', 'fixture/Core', forbidden).check()
    assert state['status'] == 'unavailable'
    assert 'HTTP 403' in state['message'] and '频率' not in state['message']


def test_monitor_notices_manual_failure_during_success_wait(tmp_path, monkeypatch):
    candidate(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(updates.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(updates.time, 'time', lambda: clock[0])
    class InlineThread:
        def __init__(self, target, **kwargs): self.target = target
        def start(self): self.target()
    monkeypatch.setattr(updates, 'Thread', InlineThread)
    calls = []
    def fetch(*_):
        calls.append(clock[0])
        if len(calls) == 2: raise ValueError('offline')
        return {'latest': 'beta.0.5.4.7'}
    checker = updates.PackageUpdates(tmp_path, 'win', 'fixture/Core', fetch)
    class Stopped:
        waits = 0
        def is_set(self): return len(calls) >= 3
        def wait(self, delay):
            self.waits += 1
            assert delay <= 5
            clock[0] += delay
            if self.waits == 1:
                assert checker.check(force=True)['status'] == 'unavailable'
            return self.is_set()
    checker.warm(Stopped())
    assert calls == [1000.0, 1005.0, 1305.0]
