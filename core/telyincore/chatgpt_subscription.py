"""Local Sign in with ChatGPT client; no Codex/browser credential harvesting.

R8 ISOLATED CANDIDATE: owned HTTP actions and OAuth resources must both drain.
False retains responsibility; only True confirms stop. Not approved for deployment.

Credentials never cross the Core web API. Windows uses user-bound DPAPI;
POSIX uses private directories/files. Network dependencies are injectable in tests.
See docs/chatgpt-subscription.txt for the official protocol references.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import base64
import ctypes
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

import httpx

API_BASE = "https://api.openai.com/v1"
ISSUER = "https://auth.openai.com"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
_INSTANCES: dict[tuple[str, str, str, str], Subscription] = {}
_INSTANCES_LOCK = threading.Lock()


_LOGIN_ACTION = '请在 Core 模型页打开对应模型档案，点「Continue with ChatGPT」，在浏览器完成授权。'
_EXCHANGE_DENIED = 'ChatGPT 未接受本次登录授权。' + _LOGIN_ACTION
_REFRESH_DENIED = 'Core 未能续用已保存的 ChatGPT 登录。' + _LOGIN_ACTION
_MODELS_UNAUTHENTICATED = '获取模型列表时，ChatGPT 未接受当前登录。' + _LOGIN_ACTION
_MODELS_FORBIDDEN = 'ChatGPT 拒绝了模型列表请求（HTTP 403）。请在模型页核对登录账号和订阅授权，再点「获取模型列表」。'
_MODELS_BAD_REQUEST = 'ChatGPT 未接受模型列表请求（HTTP 400）。请将这条提示和 Core 版本发给维护者核对。'
_KEYS_UNAVAILABLE = '未能从 ChatGPT 读取身份验证公钥。请稍后在模型页重新发起授权。'
_REGION_DENIED = ('OpenAI 拒绝了来自当前网络地区的请求（HTTP 403）。请先核对 OpenAI 支持的地区。'
                  '若你位于支持地区，请在 Core「设置 → 网络代理 → OpenAI / ChatGPT」核对网络出口。')
_REQUEST_DENIED = 'ChatGPT 未接受本次请求。请将这条提示和出现时正在进行的操作发给维护者核对。'


class SubscriptionError(ValueError):
    """Safe to display: never includes upstream bodies, codes or tokens."""


def _region_denied(response):
    if response.status_code != 403 or len(response.content) > 16 * 1024:
        return False
    try:
        body = response.json()
    except (ValueError, RecursionError):
        return False
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else error
    return code == "unsupported_country_region_territory"


def public_subscription_error(error):
    """Only trusted fixed messages can enter round logs; never upstream bodies."""
    allowed = {
        "本地 ChatGPT 凭证文件过大", "无法读取 ChatGPT 登录信息，请重新登录",
        "无法保护或读取本机 ChatGPT 凭证",
        _EXCHANGE_DENIED, _REFRESH_DENIED, _MODELS_UNAUTHENTICATED,
        _MODELS_FORBIDDEN, _MODELS_BAD_REQUEST, _KEYS_UNAVAILABLE, _REQUEST_DENIED, _REGION_DENIED,
        "ChatGPT 服务暂时不可用，请稍后重试", "ChatGPT 返回数据过大",
        "无法连接 ChatGPT 授权服务或响应格式无效",
        "ChatGPT 身份校验未通过，未保存此次凭证",
        "未获得订阅调用权限，请在授权页允许 ChatGPT 套餐使用",
        "登录账号与原绑定不符；请使用另一条模型档案添加账号",
        "授权响应缺少有效的到期时间",
        "请先在模型页登录 ChatGPT 并允许订阅调用",
        "ChatGPT 登录已过期，请重新登录",
        "订阅凭证只允许发送到 OpenAI 官方接口",
        "订阅调用必须传入完整的消息数组",
        "订阅通道暂不支持此工具类型，请使用本地函数工具",
        "ChatGPT 订阅认证未正确装配",
    }
    message = str(error)
    return message if type(error) is SubscriptionError and message in allowed else "订阅调用失败（未分类，请检查本机登录及网络设置）"


class LoginScope:
    """One WebService owns its pending logins, NOT the shared credential store.

    Closing admission precedes draining. A failed drain remains retryable and
    cannot report success. This never logs out or revokes an already saved account.
    """
    def __init__(self):
        self._stopping = threading.Event()
        self._closed = threading.Event()
        self._commit_lock = threading.Lock()
        self._settled = threading.Event()
        self._drained = threading.Event()
        self._published = threading.Event()
        self._lock = threading.Lock()
        self._managers = set()
        self._request_condition = threading.Condition()
        self._requests = set()

    @contextmanager
    def request_activity(self):
        """One whole admitted operation; never hold the control lock over I/O.

        Stop closes admission atomically. Already admitted work may finish, but
        remains owned until its actual context exits, including cancellation.
        """
        token = object()
        with self._request_condition:
            if self._stopping.is_set():
                raise SubscriptionError("登录所属的 Web 服务已停止")
            self._requests.add(token)
        try:
            yield
        finally:
            with self._request_condition:
                self._requests.remove(token)
                self._request_condition.notify_all()

    @property
    def inflight_requests(self):
        with self._request_condition:
            return len(self._requests)

    def shutdown_status(self):
        """Nonblocking owner snapshot; False stop never means credentials unchanged.

        Outcome covers this scope's login publication, not prior saved accounts.
        Repeated stop is required to confirm drain after an incomplete return.
        """
        complete = self._settled.is_set()
        stopping = self._stopping.is_set()
        return {"state": "stopped" if complete else "stopping" if stopping else "open",
                "complete": complete,
                "login_save_outcome": "saved" if self._published.is_set() else
                    "unchanged" if complete else "unconfirmed",
                "ownership_retained": stopping and not complete}

    def _register_locked(self, manager):
        if self._stopping.is_set():
            raise SubscriptionError("登录所属的 Web 服务已停止")
        # All actions can own storage/network work, even without starting a login.
        self._managers.add(manager)
        manager._claim_scope(self)

    def register(self, manager):
        """Bind an admitted subscription probe to this service, not Engine's direct scope."""
        with self._lock:
            self._register_locked(manager)

    def command(self, manager, action):
        with self._lock:
            self._register_locked(manager)
            if action == "start":
                result = manager.start(owner=self)
                if self._stopping.is_set():
                    manager.close_owner(self, timeout=0)
                    raise SubscriptionError("登录所属的 Web 服务已停止")
                return result
            if action == "cancel":
                return manager.cancel(owner=self)
        if action == "logout":
            return manager.logout(owner=self)
        return getattr(manager, action)()

    def confirm_stopped(self):
        """Release profile ownership only after the enclosing service confirms stop."""
        if not self._drained.is_set():
            return False
        self._settled.set()
        return True

    def close(self, *, timeout=10.0, retain_ownership=False):
        # Approved contract: True confirms complete drain; False retains ownership.
        # Stop admission immediately. A replace already admitted under commit_lock
        # may finish after False; no new replace may be admitted after stopping.
        with self._request_condition:
            self._stopping.set()
        deadline = time.monotonic() + max(0.0, timeout)
        if not self._commit_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            return False
        try:
            self._closed.set()
        finally:
            self._commit_lock.release()
        if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            return False
        try:
            managers = tuple(self._managers)
        finally:
            self._lock.release()
        settled = True
        for manager in managers:
            try:
                done = manager.close_owner(self, timeout=max(0.0, deadline - time.monotonic()))
            except Exception:
                done = False  # Report incomplete cleanup; never mask it as success.
            settled = (done is True) and settled
        with self._request_condition:
            while self._requests:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._request_condition.wait(remaining)
        if settled:
            self._drained.set()
            if not retain_ownership:
                self.confirm_stopped()
        return settled


class _CallbackServer(ThreadingHTTPServer):
    """Track accepted callback connections too, including incomplete HTTP headers."""
    daemon_threads = True
    block_on_close = False

    def __init__(self, *args):
        self._connections_lock = threading.Lock()
        self._connections = set()
        self.quiet = threading.Event()
        self.quiet.set()
        super().__init__(*args)

    def get_request(self):
        request, address = super().get_request()
        # Local redirect headers arrive together. A short header-read bound also
        # drains Windows select/read waits that socket.shutdown need not wake.
        request.settimeout(.2)
        return request, address

    def process_request(self, request, address):
        with self._connections_lock:
            self._connections.add(request)
            self.quiet.clear()
        try:
            super().process_request(request, address)
        except Exception:
            self.shutdown_request(request)
            raise

    def shutdown_request(self, request):
        try:
            super().shutdown_request(request)
        finally:
            with self._connections_lock:
                self._connections.discard(request)
                if not self._connections:
                    self.quiet.set()

    def abort_connections(self):
        with self._connections_lock:
            for request in tuple(self._connections):
                try:
                    request.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # Peer may already have closed; shutdown_request still drains.
                request.close()

    def handle_error(self, request, address):
        pass  # Never emit callback query strings/codes in a server traceback.


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, target = Blob(len(data), buf), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN; never machine-wide protection.
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise SubscriptionError("无法保护或读取本机 ChatGPT 凭证")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def _atomic(path: Path, data: bytes, *, publish=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.parent.chmod(0o700)
    fd, name = tempfile.mkstemp(prefix=".oauth-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if os.name != "nt":
            os.chmod(name, 0o600)
        # Encoding, staging, flush and fsync precede the publication fence.
        replace = lambda: os.replace(name, path)
        if publish is None:
            replace()
        else:
            publish(replace)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def subscription(root: Path, name: str, *, policy_root: Path | None = None, owner_root: Path | None = None) -> Subscription:
    # Profile names double as storage keys, not arbitrary filesystem paths.
    from .engine import is_a_good_name
    if not is_a_good_name(name) or len(name) > 128:
        raise SubscriptionError("请先填写有效的模型供应商名称（字母、数字、-、_）")
    policy_root = Path(root) if policy_root is None else Path(policy_root)
    owner_root = policy_root if owner_root is None else Path(owner_root)
    key = (str(Path(root).resolve()), name, str(policy_root.resolve()), str(owner_root.resolve()))
    with _INSTANCES_LOCK:
        if key not in _INSTANCES:
            _INSTANCES[key] = Subscription(Path(root), name, policy_root=policy_root)
        return _INSTANCES[key]


class Subscription:
    def __init__(self, root: Path, name: str, *, client_factory=None, clock=time.time, policy_root: Path | None = None):
        # Credentials and model proxy follow Core; manager ownership follows the Bot.
        self.policy_root = Path(root) if policy_root is None else Path(policy_root)
        self.directory = Path(root) / "chatgpt-subscription"
        self.path = self.directory / (name + ".credentials")
        self.lock_path = self.directory / (name + ".lock")
        self._lock = threading.RLock()
        from .model_proxy import model_network
        from .model_network import client_options
        self._client_factory = client_factory or (lambda: httpx.Client(
            **client_options(model_network(self.policy_root, ISSUER)), trust_env=False, follow_redirects=False, timeout=15,
            headers={"User-Agent": "TelyinCore-ChatGPT/0.1"}))
        self._clock = clock
        self._pending = None
        self._login_completed = False
        self._capacity_cache = None
        self._sessions = []  # Includes consumed callbacks and closing listeners until drained.
        self._start_lock = threading.Lock()
        self._scope_owners = set()
        self._message = "尚未登录；订阅资格与模型访问权限以授权结果为准"

    def _read(self):
        if not self.path.exists():
            return {}
        if self.path.stat().st_size > 131072:
            raise SubscriptionError("本地 ChatGPT 凭证文件过大")
        try:
            raw = self.path.read_bytes()
            if os.name == "nt":
                if not raw.startswith(b"DPAPI1\n"):
                    raise ValueError()
                raw = _dpapi(raw[7:], decrypt=True)
            record = json.loads(raw)
            if not isinstance(record, dict):
                raise ValueError()
            return record
        except (ValueError, OSError):
            raise SubscriptionError("无法读取 ChatGPT 登录信息，请重新登录") from None

    def capacity_binding(self):
        """Identify an authorization grant across its normal access-token renewals."""
        def stamp():
            info = self.path.stat()
            return info.st_mtime_ns, info.st_size, info.st_ino, info.st_ctime_ns

        try:
            before = stamp()
        except FileNotFoundError:
            return None
        cached = self._capacity_cache
        if cached is not None and cached[0] == before:
            return cached[1]
        # Old credentials have no grant ID. Their verified subject, client and
        # scopes still identify the login; an interactive login gets a new ID.
        try:
            record = self._read()
            scopes = record.get("scopes")
            value = ("file", before)
            if (record.get("access_token") and record.get("subject") and record.get("client_id")
                    and isinstance(scopes, list) and all(isinstance(s, str) for s in scopes)
                    and PLAN_SCOPE in scopes):
                binding = [record["subject"], record["client_id"], sorted(set(scopes)),
                           record.get("grant_id")]
                value = ("grant", hashlib.sha256(json.dumps(binding).encode()).hexdigest())
            if stamp() == before:
                self._capacity_cache = (before, value)
                return value
        except (SubscriptionError, OSError):
            pass
        return ("unconfirmed", before)

    def _write(self, record, *, publish=None):
        raw = json.dumps(record, ensure_ascii=False).encode("utf-8")
        if os.name == "nt":
            raw = b"DPAPI1\n" + _dpapi(raw)
        _atomic(self.path, raw, publish=publish)

    def _disk_lock(self):
        from .engine import _config_lock
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return _config_lock(self.lock_path)

    def status(self):
        with self._lock:
            record = self._read()
            signed_in = bool(record.get("access_token") and PLAN_SCOPE in record.get("scopes", []))
            return {"connected": signed_in, "pending": self._pending is not None
                    and not self._pending.get("consumed", False),
                    "account": record.get("email", "") if signed_in else "",
                    "expires_at": record.get("expires_at") if signed_in else None,
                    "login_in_progress": self._pending is not None
                        and not self._pending["exchange_done"].is_set(),
                    "login_completed": self._login_completed,
                    "message": self._message,
                    "storage": "Windows DPAPI" if os.name == "nt" else "本机私有文件（0600）"}

    def _host_id(self):
        with self._disk_lock_host():
            path = self.directory / "host-id"
            if not path.exists():
                _atomic(path, ("urn:uuid:" + str(uuid.uuid4())).encode())
            return path.read_text(encoding="utf-8")

    def _disk_lock_host(self):
        from .engine import _config_lock
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return _config_lock(self.directory / "host.lock")

    def _json(self, method, url, **kwargs):
        try:
            with self._client_factory() as client:
                response = client.request(method, url, **kwargs)
            if response.status_code >= 300:
                if _region_denied(response):
                    raise SubscriptionError(_REGION_DENIED)
                if response.status_code in (400, 401, 403):
                    message = _REQUEST_DENIED
                    if url == TOKEN_URL and method == "POST":
                        grant = kwargs.get("data", {}).get("grant_type")
                        if grant == "authorization_code":
                            message = _EXCHANGE_DENIED
                        elif grant == "refresh_token":
                            message = _REFRESH_DENIED
                    elif url == API_BASE + "/models":
                        message = {400: _MODELS_BAD_REQUEST, 401: _MODELS_UNAUTHENTICATED,
                                   403: _MODELS_FORBIDDEN}[response.status_code]
                    elif url == ISSUER + "/.well-known/jwks.json":
                        message = _KEYS_UNAVAILABLE
                    raise SubscriptionError(message)
                raise SubscriptionError("ChatGPT 服务暂时不可用，请稍后重试")
            if len(response.content) > 2 * 1024 * 1024:
                raise SubscriptionError("ChatGPT 返回数据过大")
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, SubscriptionError):
                raise
            raise SubscriptionError("无法连接 ChatGPT 授权服务或响应格式无效") from None

    def _validate_identity(self, tokens, client_id, nonce=None):
        try:
            import jwt
        except ImportError:
            raise SubscriptionError("缺少 PyJWT[crypto] 依赖，请安装项目依赖后重试") from None
        try:
            token = tokens["id_token"]
            header = jwt.get_unverified_header(token)
            # Explicit asymmetric algorithm allowlist; never trust alg/jku supplied by the token.
            if header.get("alg") != "RS256":
                raise ValueError()
            jwks = self._json("GET", ISSUER + "/.well-known/jwks.json")
            keys = [k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")]
            if len(keys) != 1:
                raise ValueError()
            key = jwt.PyJWK.from_dict(keys[0], algorithm="RS256").key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=client_id,
                                issuer=ISSUER, options={"require": ["exp", "iss", "aud", "sub"]})
            if nonce is not None and not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
                raise ValueError()
            return claims
        except SubscriptionError:
            raise
        except Exception:
            raise SubscriptionError("ChatGPT 身份校验未通过，未保存此次凭证") from None

    def _token_record(self, tokens, old, client_id, nonce=None):
        scopes = str(tokens.get("scope", "")).split() if "scope" in tokens else old.get("scopes", [])
        if PLAN_SCOPE not in scopes or not tokens.get("access_token"):
            raise SubscriptionError("未获得订阅调用权限，请在授权页允许 ChatGPT 套餐使用")
        claims = self._validate_identity(tokens, client_id, nonce) if tokens.get("id_token") else None
        if nonce is not None and claims is None:
            raise SubscriptionError("授权未返回可验证的身份信息")
        if claims and old.get("subject") and claims["sub"] != old["subject"]:
            raise SubscriptionError("登录账号与原绑定不符；请使用另一条模型档案添加账号")
        expires = tokens.get("expires_in")
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not 0 < expires <= 86400:
            raise SubscriptionError("授权响应缺少有效的到期时间")
        return {**old, "client_id": client_id,
                "subject": claims["sub"] if claims else old.get("subject", ""),
                "email": claims.get("email", "") if claims else old.get("email", ""),
                "access_token": tokens["access_token"],
                "refresh_token": tokens.get("refresh_token", old.get("refresh_token", "")),
                "id_token": tokens.get("id_token", old.get("id_token", "")),
                "scopes": scopes, "expires_at": self._clock() + expires}

    def _claim_scope(self, owner):
        with self._lock:
            # Successful owners are retired on the next acquisition; incomplete
            # owners remain strong references until their stop is confirmed.
            self._scope_owners = {s for s in self._scope_owners if not s._settled.is_set()}
            if owner._stopping.is_set():
                raise SubscriptionError("登录所属服务正在停止，不能开始新操作")
            if any(s is not owner and s._stopping.is_set() for s in self._scope_owners):
                raise SubscriptionError("此前所属服务尚未确认停止，请先完成原服务清理")
            self._scope_owners.add(owner)

    def _other_stopping_scope(self, owner):
        return any(s is not owner and s._stopping.is_set() and not s._settled.is_set()
                   for s in self._scope_owners)

    def start(self, *, owner=None):
        # Only acquisition is serialized here; close_owner must never wait for HTTP exchange.
        with self._start_lock:
            with self._lock:
                if isinstance(owner, LoginScope) and owner._stopping.is_set():
                    raise SubscriptionError("登录所属服务正在停止，不能开始新登录")
                if self._other_stopping_scope(owner):
                    raise SubscriptionError("此前所属服务尚未确认停止，请先完成原服务清理")
                if any(isinstance(p["owner"], LoginScope)
                        and p["owner"]._stopping.is_set()
                        and not p["owner"]._settled.is_set()
                        for p in self._sessions):
                    raise SubscriptionError("此前所属服务尚未确认停止，请先完成原服务清理")
            self.cancel(owner=owner)
            try:
                return self._start(owner)
            except Exception:
                if not self.close_owner(owner, timeout=2):
                    raise SubscriptionError("登录启动失败且清理未完成，请稍后检查停止状态") from None
                raise

    def _start(self, owner):
        with self._lock:
            old = self._read()
            host_id = self._host_id()
            verifier = secrets.token_urlsafe(48)
            pending = {"state": secrets.token_urlsafe(32), "nonce": secrets.token_urlsafe(32),
                       "verifier": verifier, "deadline": self._clock() + 300,
                       "client_id": old.get("client_id", "dynamic_agent_client"), "old": old,
                       "owner": owner, "cancelled": False, "consumed": False,
                       "commit_lock": owner._commit_lock if isinstance(owner, LoginScope)
                       else threading.Lock(), "committed": False,
                       "closed": threading.Event(), "exchange_done": threading.Event()}
            pending["exchange_done"].set()
            manager = self
            class Callback(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass  # Never log code, state, or callback URL.
                def do_GET(self):
                    self.connection.settimeout(5)
                    parts = urlsplit(self.path)
                    if parts.path != "/auth/callback" or len(self.path) > 16384:
                        self.send_error(404)
                        return
                    expected = "127.0.0.1:" + str(self.server.server_port)
                    if self.headers.get("Host") != expected:
                        self.send_error(400)
                        return
                    query = parse_qs(parts.query)
                    if any(len(v) != 1 for v in query.values()):
                        self.send_error(400)
                        return
                    code, message = manager._callback(pending, {k: v[0] for k, v in query.items()})
                    raw = message.encode("utf-8")
                    content_type = "text/plain; charset=utf-8"
                    script = "document.getElementById('close').addEventListener('click'," \
                             "()=>window.close());window.close();"
                    if code == 200:
                        content_type = "text/html; charset=utf-8"
                        raw = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
                               "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                               "<title>ChatGPT 授权完成</title><main><h1>授权完成</h1><p>"
                               + html.escape(message) + "</p><p>正在关闭授权页。若未自动关闭，"
                               "请手动关闭此页，返回原来的 Core 页面。</p>"
                               "<button id='close' type='button'>关闭授权页</button></main>"
                               "<script>" + script + "</script></html>").encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", content_type)
                    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
                    self.send_header("Content-Security-Policy", "default-src 'none'; "
                                     "script-src 'sha256-" + script_hash + "'; "
                                     "base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
            server = _CallbackServer(("127.0.0.1", 0), Callback)
            pending.update(server=server, worker=None, timer=None)
            self._sessions.append(pending)
            try:
                pending["redirect_uri"] = f"http://127.0.0.1:{server.server_port}/auth/callback"
                params = {"client_id": pending["client_id"], "ext_agent_host_id": host_id,
                          "response_type": "code", "redirect_uri": pending["redirect_uri"],
                          "scope": "openid profile email offline_access resource.invoke " + PLAN_SCOPE,
                          "resource": API_BASE, "state": pending["state"], "nonce": pending["nonce"],
                          "code_challenge_method": "S256",
                          "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")}
                if pending["client_id"] == "dynamic_agent_client":
                    params["agent_name_hint"] = "TelyinCore"
                # Deliberately omit id_token_hint: no token-bearing URL goes to the UI.
                self._pending = pending
                self._login_completed = False
                self._message = "等待浏览器授权（5 分钟内有效）"
                worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05},
                                          name="telyincore-oauth-listener", daemon=True)
                pending["worker"] = worker
                timer = threading.Timer(300, self._expire, args=(pending,))
                timer.daemon = True
                pending["timer"] = timer
                worker.start()
                timer.start()
                return {**self.status(), "authorization_url": ISSUER + "/api/accounts/authorize?" + urlencode(params)}
            except Exception:
                self._detach(pending)
                raise SubscriptionError("无法启动本机授权监听器；请检查登录清理状态后重试") from None

    def _begin_close(self, pending):
        """Called under _lock. No network wait/join while holding the commit lock."""
        if pending.get("closer") is not None or pending["closed"].is_set():
            return
        timer = pending.get("timer")
        if timer is not None:
            timer.cancel()

        def close():
            try:
                worker, server = pending["worker"], pending["server"]
                try:
                    if worker is not None and worker.ident is not None:
                        server.shutdown()
                finally:
                    server.server_close()
                    if pending["cancelled"]:
                        server.abort_connections()
                if worker is not None and worker.ident is not None:
                    worker.join()
                if timer is not None and timer.ident is not None:
                    timer.join()
            except Exception:
                pending["cleanup_error"] = True
            finally:
                pending["closed"].set()

        try:
            closer = threading.Thread(target=close, name="telyincore-oauth-close", daemon=True)
            pending["closer"] = closer
            closer.start()
        except Exception:
            # Still close the listening descriptor; do not claim a drained worker/timer.
            try:
                pending["server"].server_close()
            finally:
                pending["cleanup_error"] = True
                pending["closed"].set()

    def _detach(self, pending):
        pending["cancelled"] = True
        if self._pending is pending:
            self._pending = None
        self._begin_close(pending)
        pending["server"].abort_connections()

    @staticmethod
    def _cancelled(pending):
        owner = pending["owner"]
        # Stop intent prevents new publication admission; a replace already admitted
        # before intent may still finish after an incomplete (False) stop.
        return pending["cancelled"] or isinstance(owner, LoginScope) and owner._stopping.is_set()

    def _publish(self, pending, replace):
        """Publication fence, not a claim that an entered OS syscall is cancellable.

        Closing this fence wins over all staged IO. An already admitted replace
        may finish after False stop. Only True promises no later owned writes.
        """
        with pending["commit_lock"]:
            if (self._cancelled(pending) or self._pending is not pending
                    or self._clock() > pending["deadline"]):
                raise SubscriptionError("登录已取消或所属服务已停止，未保存凭证。")
            replace()
            pending["committed"] = True
            if isinstance(pending["owner"], LoginScope):
                pending["owner"]._published.set()

    def _drain(self, sessions, deadline):
        settled = True
        for pending in sessions:
            for event in (pending["closed"], pending["exchange_done"], pending["server"].quiet):
                settled = event.wait(max(0.0, deadline - time.monotonic())) and settled
            closer = pending.get("closer")
            if closer is not None and closer.ident is not None:
                closer.join(max(0.0, deadline - time.monotonic()))
                settled = not closer.is_alive() and settled
            settled = not pending.get("cleanup_error", False) and settled
        return settled

    def close_owner(self, owner, *, timeout=10.0):
        """Invalidate only this owner's callbacks, then boundedly drain owned resources."""
        deadline = time.monotonic() + max(0.0, timeout)
        if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            return False
        try:
            sessions = [p for p in self._sessions if p["owner"] is owner]
            for pending in sessions:
                self._detach(pending)
            if sessions and self._pending is None:
                self._message = "登录正在停止；清理未完成前不能确认凭证未改变"
        finally:
            self._lock.release()
        if not self._drain(sessions, deadline):
            return False
        if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            return False
        try:
            if sessions and self._pending is None:
                self._message = ("登录所属服务已停止；已保存的本次登录凭证保留"
                    if any(p["committed"] for p in sessions) else
                    "登录所属服务已停止；本次登录未保存，原账号未改变")
            return True
        finally:
            self._lock.release()

    def _expire(self, pending):
        with self._lock:
            if self._pending is pending:
                self._detach(pending)
                self._message = "登录已超时，请重新开始"

    def cancel(self, *, owner=None, timeout=2.0):
        deadline = time.monotonic() + max(0.0, timeout)
        if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise SubscriptionError("登录清理未完成，生命周期锁仍被占用，请稍后重试")
        try:
            if owner is not None and self._other_stopping_scope(owner):
                raise SubscriptionError("此前所属服务尚未确认停止，不能取消或退出其账号")
            if owner is not None and any(p["owner"] is not owner
                    and isinstance(p["owner"], LoginScope)
                    and p["owner"]._stopping.is_set()
                    and not p["owner"]._settled.is_set() for p in self._sessions):
                raise SubscriptionError("此前所属服务尚未确认停止，不能取消或退出其账号")
            pending = self._pending
            if pending:
                if owner is not None and pending["owner"] is not owner:
                    raise SubscriptionError("另一个服务正在进行此档案的登录，请在原服务中取消")
                self._detach(pending)
                self._message = "已取消登录，原账号未改变"
            sessions = [p for p in self._sessions if owner is None or p["owner"] is owner]
        finally:
            self._lock.release()
        if not self._drain(sessions, deadline):
            raise SubscriptionError("登录清理未完成，请稍后重试；未确认监听器及交换已全部退出")
        if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise SubscriptionError("登录清理未完成，无法确认最终状态，请稍后重试")
        try:
            self._sessions = [p for p in self._sessions if p not in sessions]
            return self.status()
        finally:
            self._lock.release()

    def _callback(self, pending, query):
        with self._lock:
            if (self._pending is not pending or pending["consumed"] or self._cancelled(pending)
                    or self._clock() > pending["deadline"]
                    or not hmac.compare_digest(query.get("state", ""), pending["state"])):
                return 400, "登录请求已过期或校验不符，请回到模型页重试。"
            # Consume once, but retain the owner while exchange is in flight.
            pending["consumed"] = True
            pending["exchange_done"].clear()
            self._begin_close(pending)
        try:
            return self._exchange(pending, query)
        finally:
            with self._lock:
                if self._pending is pending:
                    self._pending = None
                pending["exchange_done"].set()

    def _exchange(self, pending, query):
        # No lifecycle lock across upstream IO: stop/cancel can invalidate commit immediately.
        with self._lock:
            if query.get("error"):
                self._message = "未完成授权，原账号未改变"
                return 400, self._message
            client_id = query.get("client_id") or pending["client_id"]
            if (client_id == "dynamic_agent_client" or not client_id.startswith("oaiapp_")
                    or (pending["client_id"] != "dynamic_agent_client" and client_id != pending["client_id"])
                    or not query.get("code")):
                self._message = "授权注册信息不完整，请重试"
                return 400, self._message
        try:
            with self._lock, self._disk_lock():
                if self._cancelled(pending):
                    return 400, "登录已取消或所属服务已停止，未保存凭证。"
                if self._read() != pending["old"]:
                    raise SubscriptionError("登录信息已在别处改变，请重新开始")
            tokens = self._json("POST", TOKEN_URL, data={"grant_type": "authorization_code",
                "client_id": client_id, "code": query["code"],
                "code_verifier": pending["verifier"], "redirect_uri": pending["redirect_uri"],
                "resource": API_BASE})
            record = self._token_record(tokens, pending["old"], client_id, pending["nonce"])
            record["grant_id"] = uuid.uuid4().hex
            # Same lock order as refresh/logout. Recheck both cancellation and disk CAS
            # AFTER exchange, never hold the commit/lifecycle lock across network IO.
            with self._lock, self._disk_lock():
                if (self._cancelled(pending) or self._pending is not pending
                        or self._clock() > pending["deadline"]):
                    return 400, "登录已取消或所属服务已停止，未保存凭证。"
                if self._read() != pending["old"]:
                    raise SubscriptionError("登录信息已在别处改变，请重新开始")
                self._write(record, publish=lambda replace: self._publish(pending, replace))
            with self._lock:
                # A real publication must not be reported as cancelled/no-save.
                # An admitted replace may complete after False, never after True.
                self._login_completed = True
                self._message = "已登录，可以测试连通。"
                return 200, "ChatGPT 授权完成。请返回 TelyinCore 的模型页。"
        except (SubscriptionError, OSError) as exc:
            with self._lock:
                if pending["committed"]:
                    self._message = "凭证已保存，但本机存储收尾异常；请确认状态后再操作"
                    return 200, "凭证已保存，请返回模型页确认本机存储状态。"
                if self._cancelled(pending):
                    return 400, "登录已取消或所属服务已停止，未保存凭证。"
                self._message = str(exc) if isinstance(exc, SubscriptionError) else "无法保存本机凭证"
                return 400, self._message

    def access_token(self):
        with self._lock, self._disk_lock():
            record = self._read()
            if not record.get("access_token") or PLAN_SCOPE not in record.get("scopes", []):
                raise SubscriptionError("请先在模型页登录 ChatGPT 并允许订阅调用")
            if record.get("expires_at", 0) <= self._clock() + 60:
                if not record.get("refresh_token"):
                    raise SubscriptionError("ChatGPT 登录已过期，请重新登录")
                tokens = self._json("POST", TOKEN_URL, data={"grant_type": "refresh_token",
                    "client_id": record["client_id"], "refresh_token": record["refresh_token"],
                    "resource": API_BASE})
                record = self._token_record(tokens, record, record["client_id"])
                self._write(record)
            return str(record["access_token"])

    def logout(self, *, owner=None):
        self.cancel(owner=owner)
        with self._lock, self._disk_lock():
            record = self._read()
            confirmed = not bool(record.get("refresh_token"))
            if not confirmed:
                try:
                    discovery = self._json("GET", ISSUER + "/.well-known/openid-configuration")
                    endpoint = discovery.get("revocation_endpoint", "")
                    parsed = urlsplit(endpoint)
                    if parsed.scheme != "https" or parsed.netloc != "auth.openai.com" or parsed.fragment:
                        raise SubscriptionError("撤销端点校验失败")
                    with self._client_factory() as client:
                        resp = client.post(endpoint, data={"token": record["refresh_token"],
                            "token_type_hint": "refresh_token", "client_id": record["client_id"]})
                    confirmed = resp.status_code == 200
                except (SubscriptionError, httpx.HTTPError):
                    confirmed = False
            # Retain registration identity, not any tokens or login hints.
            self._write({k: record[k] for k in ("client_id", "subject", "email") if k in record})
            self._message = ("已退出登录并撤销授权" if confirmed else
                "已在本机退出；远端撤销未确认，请到 ChatGPT 设置中断开此应用")
            return {**self.status(), "revocation_confirmed": confirmed}

    def models(self):
        token = self.access_token()
        result = self._json("GET", API_BASE + "/models", headers={"Authorization": "Bearer " + token})
        rows = result.get("models")
        if not isinstance(rows, list):
            raise SubscriptionError("模型目录格式不符，未获得订阅模型列表")
        specs = [_catalog_spec(row) for row in rows
                 if isinstance(row, dict) and row.get("visibility") == "list"
                 and isinstance(row.get("slug"), str) and row["slug"]]
        return {"ok": True, "status": 200, "models": [s["id"] for s in specs],
                "specs": specs, "error": None, "url": API_BASE + "/models"}


def _catalog_spec(row):
    """Normalize reported capabilities only; absent or malformed means unknown."""
    def positive(key):
        value = row.get(key)
        return value if type(value) is int and value > 0 else None

    levels = row.get("supported_reasoning_levels")
    efforts = []
    for level in levels if isinstance(levels, list) else []:
        effort = level.get("effort") if isinstance(level, dict) else None
        if isinstance(effort, str) and effort in {
                "none", "minimal", "low", "medium", "high", "xhigh", "max"}:
            if effort not in efforts:
                efforts.append(effort)
    default = row.get("default_reasoning_level")
    modalities = row.get("input_modalities")
    images = ("image" in modalities if isinstance(modalities, list)
              and modalities and all(isinstance(m, str) for m in modalities) else None)
    display = row.get("display_name")
    return {"id": row["slug"],
            "display_name": display if isinstance(display, str) and display else row["slug"],
            "context_window": positive("context_window"),
            "max_output_tokens": positive("max_output_tokens"), "input_images": images,
            "effort_default": default if default in efforts else None,
            "effort_levels": efforts}


def subscription_body(body, base_url):
    """Apply only to an explicitly subscription-authenticated profile, fail closed."""
    if base_url.rstrip("/") != API_BASE:
        raise SubscriptionError("订阅凭证只允许发送到 OpenAI 官方接口")
    unsupported = {"background", "conversation", "max_output_tokens", "max_tool_calls", "metadata",
        "moderation", "multi_agent", "prompt", "prompt_cache_retention", "safety_identifier",
        "temperature", "top_logprobs", "top_p", "truncation", "user", "previous_response_id",
        "chat_template_kwargs"}
    out = {k: v for k, v in body.items() if k not in unsupported}
    out.update(store=False, stream=True)
    items = out.get("input", [])
    if not isinstance(items, list):
        raise SubscriptionError("订阅调用必须传入完整的消息数组")
    out["input"] = [{**item, "role": "developer"} if isinstance(item, dict)
                    and item.get("role") == "system" else item for item in items]
    tools = out.pop("tools", [])
    if tools:
        if any(t.get("type") not in ("function", "custom") for t in tools):
            raise SubscriptionError("订阅通道暂不支持此工具类型，请使用本地函数工具")
        out["input"].append({"type": "additional_tools", "role": "developer", "tools": tools})
    return out


async def request_headers(manager):
    from .owned_blocking import owned_thread
    return {"Authorization": "Bearer " + await owned_thread(manager.access_token)}
