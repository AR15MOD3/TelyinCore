# telyincore/transport.py
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, AsyncExitStack
from dataclasses import dataclass
from typing import AsyncGenerator, Mapping, Protocol

import httpx

#: 发出去报的那个名字（与 `web.py::USER_AGENT` **逐字相同**）。
USER_AGENT = "core-kernel/1.0 (+local)"

#: 引擎的错误码 —— 出处在 generation_service.cpp:193-226，见设计稿 07 第五节。
RETRYABLE_CODES = frozenset({
    "server_overloaded", "request_queue_timeout", "service_unavailable",
})
FATAL_CODES = frozenset({
    "context_length_exceeded", "media_budget_exceeded",
})


class PreOpenCancelled(RuntimeError):
    """Cooperative cancellation observed before this HTTP stream was opened.

    Not a retryable transport failure or an authorization/scope decision.
    """


class OpenStreamCancelled(RuntimeError):
    """Cooperative cancellation observed after HTTP open; never an EOF."""


class TransportError(RuntimeError):
    """连不上 / 读断 —— **默认可重试**。

    ⚠️ `retryable` 与 `LLMError.retryable` 是**同一个形状**（回评 I3 要求的镜像）：
    调用方（`generate()`）只看这一个属性决定要不要进重试桶，
    **不按异常文本猜**。

    ⚠️ **默认是 `True`** —— 重试预算（1 + 3、`[1000, 4000, 10000]`）本来就是给
    "读断 / 连不上"这类**临时**故障的。只有**知道它重试也没用**的那些才显式传 `False`
    （目前只有一种：URL 解析不出来，见 `stream_chat` 里 `InvalidURL` 那一支）。
    """

    def __init__(self, message: str = "", *, retryable: bool = True) -> None:
        super().__init__(message)
        #: 再试一次有没有意义。`generate()` 按它决定是否重试。
        self.retryable = retryable


@dataclass
class LLMError(RuntimeError):
    status: int
    code: str
    message: str
    diagnostic: dict | None = None

    def __str__(self) -> str:
        return f"LLMError({self.status}, {self.code}): {self.message}"

    @property
    def retryable(self) -> bool:
        # llama.cpp may classify a deterministic template parameter error as
        # HTTP 500. Repeating identical input cannot fix that incompatibility.
        if (self.status == 500 and isinstance(self.message, str)
                and "Jinja Exception" in self.message
                and "Unexpected reasoning effort " in self.message
                and "Supported types are " in self.message):
            return False
        if self.code in FATAL_CODES:
            return False
        if self.code in RETRYABLE_CODES:
            return True
        return self.status in (0, 408, 429) or 500 <= self.status < 600


SUBSCRIPTION_LIMIT = "subscription_sharing_usage_limit_exceeded"
SUBSCRIPTION_LIMIT_HINT = "ChatGPT 订阅共享额度已用尽。请等额度恢复，或在模型页切换模型档案。"


def stream_failure(detail):
    """Classify an error carried inside an accepted Responses stream."""
    code = detail.get("code") if isinstance(detail, dict) else None
    if not isinstance(code, str) or code not in (FATAL_CODES | RETRYABLE_CODES | {SUBSCRIPTION_LIMIT}):
        code = "upstream_stream_error"
    message = SUBSCRIPTION_LIMIT_HINT if code == SUBSCRIPTION_LIMIT else "模型服务报告本次生成失败。"
    return LLMError(200, code, message, {"upstream_code": code, "upstream_message": message})


class Transport(Protocol):
    def stream_chat(self, base_url: str, body: Mapping, *, frame_idle_ms: int,
                    signal: asyncio.Event | None = None, path: str = "/responses",
                    request_headers: Mapping[str, str] | None = None
                    ) -> AsyncGenerator[bytes, None]:
        """Cancellation raises PreOpenCancelled before open, OpenStreamCancelled after open.

        ⚠️ 返回的必须是**异步生成器** —— 调用方会 `aclose()` 它。

        ⚠️ `signal`（可选）是一条**协作式**的取消接缝 —— **置上即"别再往下读了"**。
        它**不抢占**：这条链上没有"杀掉 socket"这回事，实现只在**下一个 chunk 边界**
        查一次，抛出 `OpenStreamCancelled` 后由 `async with` 关流（那正是 `aclose()` 那条已有出口）。
        ⚠️ 所以它**不是瞬时的**：上游一个字节都不发时（引擎 prefill 期正是那种形态），
        "下一个 chunk"可能很久才来 —— 那由 `frame_idle_ms` 那条 read timeout 管。

        ⚠️ 参数名是 **`frame_idle_ms`**（不是笼统的 `timeout_ms`）：调用方传进来的就是
        **帧空闲预算**（`cfg.frame_idle_ms`），它落在 socket 的 read timeout 上 ——
        管的是"**连一个字节块都不来**"。首响应 / 内容空闲那两条判据在**调用方**那一层，
        它们看得见事件，这一层看不见。（名字改准是 Task 7 的 M1。）

        注解写成 `AsyncIterator[bytes]` 是**不够**的：一个纯异步迭代器在注解上合法，
        却没有 `.aclose()`，于是会在**正常路径**上炸出 `AttributeError`（而 `generate()`
        的 `try/finally` 会去关它）。它底层包着 `async with self._client.stream(...)`，
        **`aclose()` 才是释放响应与 socket 的那个动作**（异步生成器没有 `close()`）。

         **`path`（2026-10-03 加，加性）**：端点那一段路径，**缺省 `/responses`** ——
        既有的每一个调用方**逐字节不变**。它存在是因为 `protocol.ChatTransport`
        要走到另一个端点（`/chat/completions`），而端点从前是**写死在这一层里**的
        （一个包在外面的适配器没有别的路可走：把路径塞进 `base_url` 会变成
        `…/chat/completions/responses` ⇒ 404 —— 烟测里真的撞上了）。

        ⚠️ **它是异步生成器，不是协程** —— `generate()` **不 `await` 它**，直接当异步迭代器用
        （异步生成器是"调用即返回"的）。⇒ 连不上、URL 解析不出来这些异常是在**第一次迭代**
        时才抛的，不在调用那一刻。
        """
        ...

    async def aclose(self) -> None:
        """释放这个 transport 占着的资源（连接池 / socket）。

        ⚠️ **它是契约的一部分，不是可选装饰**（回评 I4）：`cli._run` 会关掉
        **它自己造的那个** transport（注入进来的归调用方，由调用方关）。

        为什么要紧：实现底下包着一个 `httpx.AsyncClient`，它带**连接池**。
        没人关的话，进程退出靠操作系统回收；嵌进别的进程里（或被反复调用）
        就是**泄漏**。

        ⚠️ 幂等：关两次不许炸（`httpx.AsyncClient.aclose()` 本身幂等）。
        """
        ...


class HttpxTransport:
    """真 HTTP。测试里换成注入的 client。"""

    def __init__(self, client: httpx.AsyncClient | None = None,
                 headers_of: Any = None, proxy_of: Any = None, *,
                 send_fence: Any = None, request_check: Any = None) -> None:
        # ⚠️ `trust_env=False` **不是可选项**（以下全是 2026-09-26 的**实测**，不是推断）：
        #  · 引擎是**硬编码的 loopback** —— 路由该由**代码**决定，不该由**环境**决定。
        #    实测本机设着 `HTTP_PROXY=http://127.0.0.1:7892`；把 `NO_PROXY` 里的 `127.0.0.1`
        #    去掉，引擎 URL 就解析到 **`proxy = http://127.0.0.1:7892`** ⇒ **提示词会走代理**。
        #    **而今天它不会** —— 因为 `NO_PROXY` 恰好含 `127.0.0.1`。
        #    ⇒ **泄露离一次环境编辑，不是正在发生。**（别把这句话写成"现在就在泄露"。）
        #    `trust_env=False` 之后 `mounts = []`，**结构上对环境内容免疫**。
        #  · 本机 `NO_PROXY` 还含 `[::1]` ⇒ httpx 0.28.1 生成挂载键 `all://*[::1]` 后**解析不了**，
        #    于是**构造就炸**（`httpx.InvalidURL: Invalid port: ':1]'`，且在 `__init__` 里，
        #    本模块的 except 接不住）。
        # **发出去要报个名字**（2026-10-05 量出来的）：httpx 缺省报的是
        #    `python-httpx/x.y`，而 Cloudflare 那一类**专挡这一串** —— 实测某家第三方
        #    对**每一个路径**都回 `403 error code: 1010`；把这个名字设上之后它**立刻变成
        #    `401 Unauthorized`**（那才是"要鉴权"）⇒ 那不是"钥匙不对"，是**对面把机器人
        #    挡在门外**。⚠️ 这一句与 `web.py::USER_AGENT` **必须逐字相同**（该模块零同胞
        #    import ⇒ 各写一份；`test_the_two_places_say_the_same_user_agent` 盯着漂移）。
        if send_fence is not None and not callable(send_fence):
            raise TypeError("send_fence must be a trusted host context factory")
        self._send_fence = send_fence
        if request_check is not None and not callable(request_check):
            raise TypeError("request_check must be a trusted host callback")
        self._request_check = request_check
        self._client = client or httpx.AsyncClient(trust_env=False,
                                                   headers={"User-Agent": USER_AGENT})
        if request_check is not None:
            self._client.event_hooks['request'].append(self._check_request)
        #: **2026-10-12**：鉴权头**每一次请求现算**（一个零参可调用，交回 `{头名: 值}`）。
        #:   ⚠️ **为什么是"现算"而不是构造时定死**：档案可以中途换，而 `kind` 本身就是
        #:      **每次请求现读**的（`protocol.ChatTransport` 那一边有一条判据钉着这件事）
        #:      ⇒ 一家 `anthropic` 与一家 `chat` 要的**不是一个头**；构造时定死会变成
        #:      "换了一家还带着上一家的头"——而那是**静默的 401**。
        #:   ⚠️ **不递它 ⇒ 逐字节与从前一样**（一颗头都不加；本机那条路就是如此）。
        #: Frozen request_headers take precedence, including an explicit empty map.
        #: Legacy callback failures are non-retryable; never send anonymously on error.
        self._headers_of = headers_of
        self._proxy_of = proxy_of
        self._injected_client = client is not None

    async def _check_request(self, request):
        # Runs inside httpx.send, after stream/client entry callbacks. This is
        # a final cooperative check, not recall of an already in-flight request.
        self._request_check()

    @asynccontextmanager
    async def _request_client(self, url):
        try:
            proxy = self._proxy_of(url) if self._proxy_of is not None else None
        except ValueError:
            raise TransportError("模型代理配置无效，请检查通用设置", retryable=False) from None
        from .model_network import ModelNetwork
        active_proxy = proxy.proxy if isinstance(proxy, ModelNetwork) else proxy
        if active_proxy and not self._injected_client:
            from .model_network import client_options
            async with httpx.AsyncClient(**client_options(proxy, asynchronous=True, check=self._request_check), trust_env=False, follow_redirects=False,
                                         headers={"User-Agent": USER_AGENT},
                                         event_hooks={"request": [self._check_request]}
                                         if self._request_check is not None else None) as client:
                yield client
        else:
            yield self._client

    def _fallback_extensions(self, signal):
        def check():
            if signal is not None and signal.is_set():
                raise PreOpenCancelled("request cancelled before direct fallback")
        return {"telyin_before_model_fallback": check}

    async def model_is_strata(self, base_url, model, headers, *, signal=None):
        """Read provider identity from the selected endpoint using its own credentials."""
        # The existing host fence admits model POST bodies. It does not grant
        # an authenticated GET on a sibling URL. Leave discovery off there.
        if self._send_fence is not None:
            return False
        if signal is not None and signal.is_set():
            raise PreOpenCancelled("request cancelled before provider discovery")
        url = base_url.rstrip('/') + '/models'
        try:
            async with asyncio.timeout(2), self._request_client(url) as client:
                if signal is not None and signal.is_set():
                    raise PreOpenCancelled("request cancelled before provider discovery")
                async with client.stream('GET', url, headers=headers, follow_redirects=False,
                                         extensions=self._fallback_extensions(signal)) as response:
                    if response.status_code != 200:
                        return False
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if signal is not None and signal.is_set():
                            raise PreOpenCancelled("request cancelled during provider discovery")
                        if len(raw) + len(chunk) > 65536:
                            return False
                        raw.extend(chunk)
            data = json.loads(raw)
        except (TimeoutError, ValueError, httpx.HTTPError, TransportError):
            return False
        rows = data.get('data') if isinstance(data, dict) else None
        return isinstance(rows, list) and any(
            isinstance(row, dict) and row.get('id') == model and row.get('owned_by') == 'strata'
            for row in rows
        )

    async def aclose(self) -> None:
        """关掉底下的 `httpx.AsyncClient`（它带连接池）—— ⚠️ 幂等。

        不关的代价是**泄漏**：`cli.py` 每次跑都造一个 transport，而进程退出
        靠操作系统回收 —— 嵌进别的进程里（或被反复调用）就没有那一层兜底了。
        """
        await self._client.aclose()

    @asynccontextmanager
    async def _response_stream(self, client, url, body, extra, frame_idle_ms, signal):
        if self._send_fence is None:
            async with client.stream("POST", url, json=dict(body),
                                     headers=extra or None,
                                     timeout=frame_idle_ms / 1000,
                                     extensions=self._fallback_extensions(signal)) as response:
                yield response
            return
        from .request_admission import admit, bind_request
        frozen, binding = bind_request(url, body)
        async with AsyncExitStack() as opened:
            async with admit(self._send_fence, binding):
                if signal is not None and signal.is_set():
                    raise PreOpenCancelled("request cancelled before HTTP stream open")
                response = await opened.enter_async_context(client.stream(
                    "POST", url, json=frozen, headers=extra or None,
                    timeout=frame_idle_ms / 1000,
                    extensions=self._fallback_extensions(signal)))
            # Host admission is released; HTTP response still belongs to this stack.
            yield response

    async def stream_chat(self, base_url: str, body: Mapping, *, frame_idle_ms: int,
                          signal: asyncio.Event | None = None, path: str = "/responses",
                          request_headers: Mapping[str, str] | None = None
                          ) -> AsyncGenerator[bytes, None]:
        # Adaptation/auth may await before this generator is first iterated.
        # Do not open a POST when cooperative cancellation is already visible.
        if signal is not None and signal.is_set():
            raise PreOpenCancelled("request cancelled before HTTP stream open")
        # ⚠️ 端点必须是 **`/responses`**，不是 `/chat/completions`（2026-09-26 实测）：
        # 引擎两个都有，但是**两套不同的 API** —— `/chat/completions` 流出来的是 OpenAI 的
        # `{"choices":[{"delta":{"content":…}}],"object":"chat.completion.chunk"}`，
        # **没有 `type`、没有 `sequence_number`** ⇒ 喂进 `ResponseAccumulator.feed()`
        # 在**第一个事件**上就抛 `ResponseProtocolError`。
        # 我们要的 `reasoning` 与 `status` **只在 `/responses` 里**（chat chunk 的 delta 只有 `content`）。
        # ⚠️ 默认那一段仍然是 `/responses`（既有的调用方一个字没变）；
        #    `path` 是给适配器那一层用的（见协议那段 docstring）。
        # 2026-10-07 装表：generate 抛错之后 loop 只记「一轮失败」、不打日志，
        # 于是「它不回」在日志里完全看不见。这三行就是那只手电。
        import sys as _sys
        import time as _t
        _t0 = _t.monotonic()
        from .request_log import request_log_lines
        import os as _os
        for _line in request_log_lines(
                body, verbose=_os.environ.get("TELYIN_CONSOLE_VERBOSE") == "1"):
            print("   [http] " + _line, file=_sys.stderr, flush=True)
        url = f"{base_url.rstrip('/')}{path}"
        # **2026-10-12：那几颗鉴权头现算一次**（没递 `headers_of` ⇒ `{}` ⇒ 照旧）。
        extra: dict[str, str] = {}
        if request_headers is not None:
            extra = dict(request_headers)
        elif self._headers_of is not None:
            try:
                extra = {str(k): str(v)
                         for k, v in dict(self._headers_of() or {}).items()}
            except Exception:
                raise TransportError("request authentication unavailable", retryable=False) from None
        try:
            async with self._request_client(url) as client:
                # Recheck after asynchronous client/proxy acquisition, before
                # opening HTTP. This is not a permission fence or request recall.
                if signal is not None and signal.is_set():
                    raise PreOpenCancelled("request cancelled before HTTP stream open")
                async with self._response_stream(client, url, body, extra,
                                                 frame_idle_ms, signal) as resp:
                    # ⚠️ 是 `>= 300`，**不是 `>= 400`**：httpx 0.28.1 **默认不跟随重定向**
                    # （实测两代都是 `False`：`Client` 与 `AsyncClient` 的 `follow_redirects`），
                    # 所以 3xx 会**原样返回** ——
                    # 一个 302 的 HTML 会被当成"SSE 字节"吐给下游，最后表现成
                    # 「没有终态事件」，离现场很远。这里 fail fast。
                    print("   [http] 回来 => " + str(resp.status_code)
                          + "（" + str(round(_t.monotonic() - _t0, 2)) + " 秒）",
                          file=_sys.stderr, flush=True)
                    if resp.status_code >= 300:
                        # Error bodies are untrusted. Never print them or read to EOF.
                        error_body = await _read_error_body(resp)
                        error_response = httpx.Response(resp.status_code, content=error_body)
                        error = _to_llm_error(error_response, secrets=(*extra.values(), *client.headers.values()))
                        from .request_log import request_structure
                        error.diagnostic = {**(error.diagnostic or {}),
                                            'request_structure': request_structure(body)}
                        raise error
                    async for chunk in resp.aiter_bytes():
                        # **协作式**：在下一个 chunk 边界停下，而不是杀掉 socket。
                        #   抛出取消之后 `async with` 会关掉流 —— 那正是 `aclose()` 那条已有出口。
                        #   ⚠️ 而它**不是瞬时的**：引擎 prefill 期"一个字节都不发"时，
                        #   "下一个 chunk"可能很久才来（那由 `frame_idle_ms` 那条 read timeout 管）。
                        if signal is not None and signal.is_set():
                            raise OpenStreamCancelled("request cancelled after HTTP stream open")
                        yield chunk
                    if signal is not None and signal.is_set():
                        raise OpenStreamCancelled("request cancelled after HTTP stream open")
        except httpx.HTTPError as exc:
            raise TransportError(type(exc).__name__) from exc
        except httpx.InvalidURL as exc:
            # ⚠️ `httpx.InvalidURL` **不是** `httpx.HTTPError`（实测 `issubclass` 为 False）——
            # 所以上面那一支接不住它。而它正是**配置写错**的路径：
            # `http://127.0.0.1:notaport/v1` 会裸抛一个本模块从未声明的类型。
            #
            # ⚠️ **`retryable=False`：这个 URL 永远解析不出来**（回评 I3）。
            # 不标的话，`generate()` 会白试 4 次、退避 [1000, 4000, 10000]、
            # 多等 **15.0 秒**（实测），然后报的还是同一个错。
            # ⇒ 它是这一层唯一**知道它永久**的故障类别。
            #
            # ⚠️ **"拒绝连接"故意不在这里** —— 拒绝不等于永久：同一个请求在引擎起来之后
            # 就会成功，而"引擎还没起来"正是那个重试预算存在的理由。
            # 那条腿由 `test_a_refused_loopback_is_still_retryable` 钉着。
            raise TransportError("invalid request URL", retryable=False) from exc


ERROR_BODY_LIMIT = 65536
ERROR_BODY_SECONDS = 5.0

async def _read_error_body(resp):
    # Error diagnostics never decompress untrusted bodies. Normal successful
    # streaming is unchanged. Limits cover this application's read/retention.
    encoding = resp.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in ("", "identity"):
        # Preserve the known HTTP status without reading/decompressing the body.
        # In particular, compressed 402 must remain a terminal HTTP rejection.
        return b""
    if resp.is_stream_consumed:
        return resp.content[:ERROR_BODY_LIMIT]
    body = bytearray()
    try:
        async with asyncio.timeout(ERROR_BODY_SECONDS):
            async for part in resp.aiter_raw():
                body.extend(part[:ERROR_BODY_LIMIT-len(body)])
                if len(body) >= ERROR_BODY_LIMIT:
                    break
    except TimeoutError as error:
        raise TransportError("upstream error body deadline exceeded", retryable=False) from error
    return bytes(body)


def _safe_error_text(value, secrets=(), limit=600):
    import re
    if not isinstance(value, str):
        return None
    # Redact before truncation, including the token part of Authorization.
    for secret in sorted((str(s) for s in secrets if s), key=len, reverse=True):
        value = value.replace(secret, '[redacted]')
        if secret.lower().startswith('bearer '):
            value = value.replace(secret[7:], '[redacted]')
    value = re.sub(r'(?i)\bBearer\s+[^\s,;\"<>]+', 'Bearer [redacted]', value)
    value = re.sub(r'(?i)\b(?:sk-|sess-)[a-z0-9_-]+', '[redacted]', value)
    value = re.sub(r"(?i)(api[_ -]?key|token|password|secret|authorization)([\"']?\s*[:=]\s*[\"']?)[^\s,;\"'<>]+",
                   r'\1\2[redacted]', value)
    value = re.sub(r'https?://[^\s<>\"\']+', '[URL omitted]', value)
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', value)
    return ' '.join(value.split())[:limit]


def _to_llm_error(resp: httpx.Response, *, secrets=()) -> LLMError:
    code, message = "unknown", "upstream request rejected"
    detail = {'http_status': resp.status_code, 'response_format': 'non_json',
              'upstream_message': 'Upstream returned a non-JSON error; raw body omitted.'}
    try:
        err = resp.json().get("error") or {}
        if not isinstance(err, dict):
            err = {'message': err} if isinstance(err, str) else {}
        detail['response_format'] = 'json'
        detail['upstream_message'] = (_safe_error_text(err.get('message'), secrets)
                                      or 'Upstream did not provide an error message.')
        for field in ('code', 'type', 'param'):
            safe = _safe_error_text(err.get(field), secrets, 120)
            if safe:
                detail['upstream_' + field] = safe
        candidate = err.get("code")
        if isinstance(candidate, str) and candidate in FATAL_CODES | RETRYABLE_CODES:
            code = candidate
        raw_message = err.get("message")
        if isinstance(raw_message, str) and all(token in raw_message for token in
                ("Jinja Exception", "Unexpected reasoning effort ", "Supported types are ")):
            message = "Jinja Exception: Unexpected reasoning effort (redacted). Supported types are (redacted)"
    except Exception:
        pass
    return LLMError(status=resp.status_code, code=code, message=message, diagnostic=detail)


def http_failure_diagnostic(error):
    """Find the transport error through wrappers without exporting arbitrary text."""
    seen = set()
    for _ in range(8):
        if error is None or id(error) in seen:
            break
        seen.add(id(error))
        if isinstance(error, LLMError):
            detail = dict(error.diagnostic or {})
            detail['http_status'] = error.status
            detail['retryable'] = error.retryable
            return detail
        error = error.__cause__ or error.__context__
    return {}
