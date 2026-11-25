"""Routes for public downloads. Usable before any third-party dependencies exist."""
import http.client
import json
import os
from pathlib import Path
import sys
import ssl
from urllib.parse import urlsplit
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

HF = {"mirror": "https://hf-mirror.com", "official": "https://huggingface.co"}
PYPI = {"mirror": "https://pypi.tuna.tsinghua.edu.cn/simple", "official": "https://pypi.org/simple"}
TORCH = {"mirror": "https://mirrors.nju.edu.cn/pytorch/whl", "official": "https://download.pytorch.org/whl"}
GITHUB_MIRROR = "https://gh-proxy.com/"
GITHUB_API_MIRROR = "https://gh-proxy.org/"
TRANSFER_ERRORS = (OSError, http.client.HTTPException, ValueError)


def source_order(preferred="mirror"):
    if preferred == "official":
        return ("official",)
    if preferred == "mirror":
        return ("mirror", "official")
    raise ValueError("不支持的下载源")


def settings_root(core):
    core = Path(core)
    linux = core / ".linux-acceptance"
    if sys.platform == "linux" and (linux / "acceptance.json").is_file():
        return linux / "core/data"
    return core / "data"


def selected_proxy(source, root, purpose="downloads"):
    if root is None or source != "official":
        return None
    from .model_proxy import auxiliary_proxy
    return auxiliary_proxy(root, purpose)


def pip_environment(source, root):
    env = {k: v for k, v in os.environ.items()
           if not k.upper().endswith("_PROXY") and not k.upper().startswith(("PIP_", "UV_"))}
    proxy = selected_proxy(source, root)
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1",
               PYTHONUTF8="1", PYTHONUNBUFFERED="1", NO_PROXY="" if proxy else "*")
    if proxy:
        env.update(HTTPS_PROXY=proxy, HTTP_PROXY=proxy)
    return env


def pip_install(command, run, root, *, preferred="mirror", indexes=PYPI):
    for source in source_order(preferred):
        env = pip_environment(source, root)
        index = indexes[source]
        route = "代理" if "HTTPS_PROXY" in env else "直连"
        print(f"依赖下载 · {index} · {route}", flush=True)
        result = run([*command, "--index-url", index, "--retries", "1", "--timeout", "15"], env)
        if result == 0:
            break
    return result


class _ExplicitProxy(ProxyHandler):
    # Requests here use HTTPS and an already validated, credential-free proxy.
    def proxy_open(self, req, proxy, scheme):
        endpoint = urlsplit(proxy)
        req.set_proxy(endpoint.netloc, endpoint.scheme)


class _PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if parts.scheme != "https" or parts.username is not None or parts.password is not None:
            raise URLError("公共下载仅允许 HTTPS 跳转")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_public(request, timeout, proxy=None):
    handler = _ExplicitProxy({"https": proxy}) if proxy else ProxyHandler({})
    context = ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        pass  # The first environment check runs before pip dependencies exist.
    else:
        context.load_verify_locations(cafile=certifi.where())
    return build_opener(handler, _PublicRedirect(), HTTPSHandler(context=context)).open(request, timeout=timeout)


def github_routes(url, proxy=None):
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.netloc not in {"github.com", "api.github.com", "codeload.github.com"}
            or parts.fragment):
        raise ValueError("不是公开 GitHub 下载地址")
    mirror = GITHUB_API_MIRROR if parts.netloc == "api.github.com" else GITHUB_MIRROR
    return ((mirror + url, None), (url, proxy))


def github_route_text(target, official, proxy, error=None):
    import socket
    from urllib.error import HTTPError, URLError
    label = ("镜像直连" if target != official else "GitHub 官方直连" if not proxy
             else "GitHub 官方经下载代理 " + urlsplit(proxy).netloc)
    if error is None:
        return label + "成功"
    cause = error.reason if isinstance(error, URLError) and not isinstance(error, HTTPError) else error
    if isinstance(error, HTTPError):
        reason = f"HTTP {error.code}"
        if error.code == 429 or (error.code == 403 and error.headers and error.headers.get("X-RateLimit-Remaining") == "0"):
            reason += "（限流）"
            if error.headers and error.headers.get("X-RateLimit-Remaining") == "0":
                reason += "，" + ("镜像上游的" if target != official else "") + "GitHub API 配额已用尽"
    elif isinstance(cause, (TimeoutError, socket.timeout)):
        reason = "超时"
    elif isinstance(cause, ConnectionRefusedError) or getattr(cause, "winerror", None) == 10061:
        reason = "连接被拒绝"
        if proxy:
            reason += "，请检查下载代理是否启动及端口设置"
    elif isinstance(cause, socket.gaierror):
        reason = "域名解析失败"
    elif isinstance(cause, ssl.SSLError):
        reason = "TLS 验证或握手失败"
    elif isinstance(cause, ValueError):
        reason = "返回内容未通过校验"
    else:
        reason = "连接失败"
    return label + "：" + reason


def github_json(url, headers, proxy=None, *, limit=4_000_000, timeout=8, expected=None,
                official_only=False, report=None):
    routes = github_routes(url, proxy)
    for target, via in (routes[-1:] if official_only else routes):
        try:
            with open_public(Request(target, headers=headers), timeout, via) as response:
                raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("GitHub 响应过大")
            value = json.loads(raw)
            if expected is not None and not expected(value):
                raise ValueError("GitHub 响应格式无效")
            if report:
                report(github_route_text(target, url, via))
            return value
        except TRANSFER_ERRORS as error:
            if report:
                report(github_route_text(target, url, via, error))
            if target == url:
                raise
