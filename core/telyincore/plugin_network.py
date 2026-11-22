"""Verified HTTPS for public plugin metadata and archives."""
from contextlib import contextmanager
import ssl
import urllib.error
import urllib.parse
import urllib.request


GITHUB_HOSTS = frozenset({'api.github.com', 'github.com', 'codeload.github.com'})


def github_destination(url):
    p = urllib.parse.urlsplit(url)
    return (p.scheme == 'https' and p.hostname in GITHUB_HOSTS
            and p.port in (None, 443) and p.username is None and p.password is None)


def opener(proxy=None):
    # Keep system/enterprise roots and supplement them with the shipped CA bundle.
    import certifi
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    if proxy:
        # Proxy selection is explicit for these requests. Avoid urllib's
        # ambient no_proxy handling, which could silently bypass this choice.
        import httpx
        return GitHubProxyOpener(httpx, proxy, context)
    # Default ProxyHandler honors OS/environment settings, including no_proxy.
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))


class GitHubProxyOpener:
    def __init__(self, httpx, proxy, context):
        self.httpx, self.proxy, self.context = httpx, proxy, context

    @contextmanager
    def open(self, req, timeout):
        url = req.full_url
        with self.httpx.Client(proxy=self.proxy, trust_env=False, verify=self.context,
                               timeout=timeout) as client:
            try:
                for _ in range(6):
                    if not github_destination(url):
                        raise urllib.error.URLError('GitHub 跳转到了未允许的地址')
                    with client.stream('GET', url, headers=dict(req.header_items())) as response:
                        if response.is_redirect and response.has_redirect_location:
                            url = urllib.parse.urljoin(url, response.headers['location'])
                            continue
                        response.raise_for_status()
                        yield _ReadBody(response)
                        return
                raise urllib.error.URLError('GitHub 跳转次数过多')
            except self.httpx.HTTPStatusError as exc:
                raise urllib.error.HTTPError(url, exc.response.status_code, '', {}, None) from None
            except self.httpx.RequestError:
                raise urllib.error.URLError('GitHub 代理连接失败') from None


class _ReadBody:
    def __init__(self, response):
        self.response = response

    def read(self, limit):
        result = bytearray()
        for chunk in self.response.iter_bytes(65536):
            result.extend(chunk[:limit - len(result)])
            if len(result) == limit:
                break
        return bytes(result)


def error_text(exc, url):
    host = urllib.parse.urlsplit(url).hostname or '下载站'
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ssl.SSLCertVerificationError):
        return (f'{host} 的 HTTPS 证书校验失败；请检查系统时间、可信证书和代理的证书配置。'
                '已停止下载，未跳过证书校验。')
    if isinstance(exc, urllib.error.HTTPError):
        return f'{host} 返回 HTTP {exc.code}，请稍后重试或检查包是否仍然公开。'
    if isinstance(exc, (urllib.error.URLError, TimeoutError)):
        return f'无法连接 {host}，请检查网络和设置中的插件市场代理后重试。'
    return f'{type(exc).__name__}: {exc}'
