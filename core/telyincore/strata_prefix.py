"""Explicit checkpoint helper for an owner-managed Strata worker.

Core's shared chat path uses the provider's automatic cache. Persistent pins
can keep entire parked conversations out of the worker's eviction queue.
"""
from collections.abc import Mapping
from contextlib import aclosing
from hashlib import sha256
import json
from time import monotonic
from types import SimpleNamespace
from urllib.parse import urlsplit

from .batch_prompt import local_chat
from .tokens import estimate_tokens
from .transport import LLMError, PreOpenCancelled


class PrefixBoundary(str):
    """Host mark on a rendered input. Its wire representation is just the original text."""


def _message_count(messages):
    boundary = None
    for index, row in enumerate(messages):
        if not isinstance(row, Mapping) or isinstance(row.get('content'), list):
            return None
        if type(row.get('content')) is PrefixBoundary:
            if boundary is not None or row.get('role') != 'user':
                return None
            boundary = index + 1
    if boundary is None or boundary >= len(messages):
        return None

    # The native branch cache targets long prompts. Tokenize precisely on the
    # server, after the actual template; here a cheap text estimate suffices.
    tokens = 0
    for row in messages[:boundary]:
        content = row.get('content')
        if isinstance(content, str):
            tokens += estimate_tokens(content)
            if tokens >= 8192:
                return boundary
    return None


def _prefix_rejected(error):
    detail = error.diagnostic or {}
    return error.status in (400, 422) and (
        detail.get('upstream_param') == 'strata_prefix'
        or 'strata_prefix' in str(detail.get('upstream_message', ''))
        or 'strata_prefix' in error.message)


class StrataPrefix:
    def __init__(self):
        self._checked = None

    async def select(self, inner, request, messages, signal):
        config = request.config
        if not local_chat(SimpleNamespace(kind=request.kind, base_url=config.base_url)):
            return None
        url = urlsplit(config.base_url)
        probe = getattr(inner, 'model_is_strata', None)
        if url.query or url.fragment or not callable(probe):
            return None
        count = _message_count(messages)
        if count is None:
            return None
        # Model visibility can differ by credential. Retain only a fingerprint
        # in this transport's bounded cache; do not persist prompts or headers.
        if signal is not None and signal.is_set():
            raise PreOpenCancelled("request cancelled before provider discovery")
        headers = dict(request.headers)
        fingerprint = sha256(json.dumps(sorted(headers.items())).encode()).digest()
        route = (config.base_url, config.model, fingerprint)
        cached = self._checked
        if cached is None or cached[0] != route or cached[1] <= monotonic():
            try:
                enabled = await probe(config.base_url, config.model, headers, signal=signal) is True
            except PreOpenCancelled:
                raise
            except Exception:
                enabled = False
            cached = self._checked = (route, monotonic() + 300, enabled)
        return (route, count) if cached[2] else None

    async def stream(self, inner, base_url, body, choice, **kwargs):
        route, count = choice
        hinted = {**body, 'strata_prefix': {'messages': count}}
        started = False
        try:
            async with aclosing(inner.stream_chat(base_url, hinted, **kwargs)) as stream:
                async for chunk in stream:
                    started = True
                    yield chunk
            return
        except LLMError as error:
            if started or not _prefix_rejected(error):
                raise
            if self._checked is not None and self._checked[0] == route:
                self._checked = (route, monotonic() + 300, False)

        # Only replay an unopened request whose optional parameter was rejected.
        async with aclosing(inner.stream_chat(base_url, body, **kwargs)) as stream:
            async for chunk in stream:
                yield chunk
