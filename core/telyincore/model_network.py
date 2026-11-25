"""Model connection policy: one fallback, only before origin request headers start.

HTTP statuses, body/read errors and cancellation never trigger this fallback.
The trace guard distinguishes HTTPS proxy CONNECT from the model request itself.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import inspect
import io
import urllib.error
import httpx

@dataclass(frozen=True)
class ModelNetwork:
    proxy: str | None = None
    fallback: bool = False


def _origin_started(event, info):
    if not event.endswith('send_request_headers.started'):
        return False
    return getattr(info.get('request'), 'method', None) != b'CONNECT'

_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError)

class ModelFallbackTransport(httpx.BaseTransport):
    def __init__(self, proxy, *, primary=None, direct=None, check=None):
        self.primary = primary or httpx.HTTPTransport(proxy=proxy, trust_env=False, retries=0)
        self.direct = direct or httpx.HTTPTransport(trust_env=False, retries=0)
        self.check = check

    def handle_request(self, request):
        started = False
        previous = request.extensions.get('trace')
        def trace(event, info):
            nonlocal started
            started = started or _origin_started(event, info)
            if previous is not None:
                previous(event, info)
        request.extensions['trace'] = trace
        try:
            try:
                return self.primary.handle_request(request)
            except _ERRORS:
                if started:
                    raise
                before = request.extensions.get('telyin_before_model_fallback')
                if before is not None:
                    before()
                if self.check is not None:
                    self.check()
                return self.direct.handle_request(request)
        finally:
            if previous is None:
                request.extensions.pop('trace', None)
            else:
                request.extensions['trace'] = previous

    def close(self):
        try:
            self.primary.close()
        finally:
            self.direct.close()

class AsyncModelFallbackTransport(httpx.AsyncBaseTransport):
    def __init__(self, proxy, *, primary=None, direct=None, check=None):
        self.primary = primary or httpx.AsyncHTTPTransport(proxy=proxy, trust_env=False, retries=0)
        self.direct = direct or httpx.AsyncHTTPTransport(trust_env=False, retries=0)
        self.check = check

    async def handle_async_request(self, request):
        started = False
        previous = request.extensions.get('trace')
        async def trace(event, info):
            nonlocal started
            started = started or _origin_started(event, info)
            if previous is not None:
                result = previous(event, info)
                if inspect.isawaitable(result):
                    await result
        request.extensions['trace'] = trace
        try:
            try:
                return await self.primary.handle_async_request(request)
            except _ERRORS:
                if started:
                    raise
                before = request.extensions.get('telyin_before_model_fallback')
                if before is not None:
                    before()
                if self.check is not None:
                    self.check()
                return await self.direct.handle_async_request(request)
        finally:
            if previous is None:
                request.extensions.pop('trace', None)
            else:
                request.extensions['trace'] = previous

    async def aclose(self):
        try:
            await self.primary.aclose()
        finally:
            await self.direct.aclose()


def client_options(network, *, asynchronous=False, check=None):
    """Legacy explicit proxy strings retain proxy-only behavior."""
    policy = network if isinstance(network, ModelNetwork) else ModelNetwork(proxy=network)
    if policy.proxy and policy.fallback:
        cls = AsyncModelFallbackTransport if asynchronous else ModelFallbackTransport
        return {'transport': cls(policy.proxy, check=check)}
    return {'proxy': policy.proxy}

class ModelProbeOpener:
    """Bounded urllib-shaped adapter for existing model catalog pagination."""
    def __init__(self, network):
        self.network = network

    @contextmanager
    def open(self, request, timeout):
        with httpx.Client(**client_options(self.network), trust_env=False,
                          follow_redirects=False, timeout=timeout) as client:
            with client.stream(request.get_method(), request.full_url,
                               headers=dict(request.header_items()), content=request.data) as response:
                class Reader:
                    status = response.status_code
                    chunks = response.iter_bytes()
                    pending = b''
                    def read(self, size):
                        while len(self.pending) < size:
                            chunk = next(self.chunks, None)
                            if chunk is None:
                                break
                            self.pending += chunk
                        value, self.pending = self.pending[:size], self.pending[size:]
                        return value
                reader = Reader()
                if not 200 <= response.status_code < 300:
                    raise urllib.error.HTTPError(request.full_url, response.status_code,
                        response.reason_phrase, response.headers, io.BytesIO(reader.read(16385)))
                yield reader
