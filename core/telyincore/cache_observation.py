"""Opt-in, content-free cache observations at the final provider transport seam.

Metrics describe HTTP attempts, not user turns. Hashes describe canonical JSON
items, NOT provider tokens, wire bytes or a promise of server-side cache reuse.
No requests are added and neither payloads nor stream bytes are modified.
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
from urllib.parse import urlsplit


_identity = contextvars.ContextVar("cache_observation_identity", default=None)
RECENT = 20
MAX_BUCKETS = 128
MAX_ITEMS = 256
MAX_EVENT = 256 * 1024


def _integer(value):
    return value if type(value) is int and value >= 0 else None


def normalize_usage(usage, *, anthropic=False):
    """Unknown stays unknown; DeepSeek native fields do not become fake zeroes."""
    if not isinstance(usage, dict):
        return {"input": None, "cached": None, "status": "unreported"}
    told = _integer(usage.get("input_tokens", usage.get("prompt_tokens")))
    details = usage.get("input_tokens_details", usage.get("prompt_tokens_details"))
    cached = _integer(details.get("cached_tokens")) if isinstance(details, dict) else None
    if cached is None:
        cached = _integer(usage.get("prompt_cache_hit_tokens"))
    if anthropic:
        # Anthropic input_tokens excludes cache reads and cache creation.
        cached = _integer(usage.get("cache_read_input_tokens"))
        created = _integer(usage.get("cache_creation_input_tokens"))
        told = told + cached + created if None not in (told, cached, created) else None
    invalid = told is not None and cached is not None and cached > told
    status = ("invalid" if invalid else "reported" if told is not None
              and cached is not None else "unreported")
    return {"input": told, "cached": None if invalid else cached, "status": status}


def summarize(rows):
    rows = list(rows)
    eligible = [r for r in rows if r["usage"]["status"] == "reported"]
    told = sum(r["usage"]["input"] for r in eligible)
    cached = sum(r["usage"]["cached"] for r in eligible)
    return {"attempts": len(rows), "reported_attempts": len(eligible),
            "unknown_attempts": sum(r["usage"]["status"] == "unreported" for r in rows),
            "invalid_attempts": sum(r["usage"]["status"] == "invalid" for r in rows),
            "input": told, "cached": cached, "uncached": told - cached,
            "share": 100 * cached / told if told else None}


class CacheLedger:
    """Single-owner sidecar, bounded per-bucket history, atomically persisted.

    Do not share this path between processes. Failure is observable but must not
    fail a model request. Existing legacy usage.json is neither read nor changed.
    """

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        self._key = secrets.token_bytes(32)
        self._buckets = {}
        self._sequence = 0
        self.last_bucket = None
        self.persistence_error = None
        self.observation_error = None
        self._load_failed = False
        if self.path is not None and self.path.exists():
            try:
                if self.path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("ledger too large")
                data = json.loads(self.path.read_text(encoding="utf-8"))
                # Validate by a strict schema before retaining any disk values.
                self._restore(data)
            except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
                self.persistence_error = "load_failed"
                self._load_failed = True

    def _restore(self, data):
        if set(data) != {"version", "key", "sequence", "last_bucket", "buckets"}:
            raise ValueError("invalid ledger")
        if type(data["version"]) is not int or data["version"] != 1 or _integer(data["sequence"]) is None:
            raise ValueError("invalid version")
        key = bytes.fromhex(data["key"])
        if len(key) != 32 or not isinstance(data["buckets"], dict) or len(data["buckets"]) > MAX_BUCKETS:
            raise ValueError("invalid buckets")
        for name, bucket in data["buckets"].items():
            if len(name) != 64 or set(bucket) != {"identity", "recent", "totals", "updated", "last"}:
                raise ValueError("invalid bucket")
            identity = bucket["identity"]
            if set(identity) != {"provider", "model", "auth", "bot", "scope", "epoch"}:
                raise ValueError("invalid identity")
            if any(not isinstance(v, str) or len(v) > 256 for v in identity.values()):
                raise ValueError("invalid identity value")
            if len(bucket["recent"]) > RECENT:
                raise ValueError("history too long")
            for row in [*bucket["recent"], bucket["last"]]:
                self._validate_row(row)
            keys = {"attempts", "reported_attempts", "unknown_attempts", "invalid_attempts", "input", "cached", "uncached", "share"}
            if set(bucket["totals"]) != keys:
                raise ValueError("invalid totals")
            if any(_integer(v) is None for k, v in bucket["totals"].items() if k != "share"):
                raise ValueError("invalid counter")
            t = bucket["totals"]
            if t["cached"] > t["input"] or t["uncached"] != t["input"] - t["cached"]:
                raise ValueError("invalid counts")
            if t["attempts"] != t["reported_attempts"] + t["unknown_attempts"] + t["invalid_attempts"]:
                raise ValueError("inconsistent coverage")
            t["share"] = 100 * t["cached"] / t["input"] if t["input"] else None
            if _integer(bucket["updated"]) is None:
                raise ValueError("invalid sequence")
        if data["last_bucket"] is not None and data["last_bucket"] not in data["buckets"]:
            raise ValueError("invalid last bucket")
        self._key, self._buckets = key, data["buckets"]
        self._sequence, self.last_bucket = data["sequence"], data["last_bucket"]

    @staticmethod
    def _validate_row(row):
        if set(row) != {"usage", "outcome", "first_output_ms", "elapsed_ms", "request", "comparison"}:
            raise ValueError("invalid observation")
        u = row["usage"]
        if set(u) != {"input", "cached", "status"} or u["status"] not in {"reported", "unreported", "invalid"}:
            raise ValueError("invalid usage")
        for k in ("input", "cached"):
            if u[k] is not None and _integer(u[k]) is None:
                raise ValueError("invalid count")
        if u["status"] == "reported" and (u["input"] is None or u["cached"] is None or u["cached"] > u["input"]):
            raise ValueError("invalid reported usage")
        if row["outcome"] not in {"completed", "incomplete", "failed", "closed", "error"}:
            raise ValueError("invalid outcome")
        for k in ("first_output_ms", "elapsed_ms"):
            if row[k] is not None and (type(row[k]) not in (int, float) or not math.isfinite(row[k]) or row[k] < 0):
                raise ValueError("invalid timing")
        r = row["request"]
        if set(r) != {"settings", "items", "item_count", "truncated"} or len(r["items"]) > MAX_ITEMS:
            raise ValueError("invalid request digest")
        if any(not isinstance(v, str) or len(v) != 64 or any(c not in "0123456789abcdef" for c in v) for v in [r["settings"], *r["items"]]):
            raise ValueError("invalid digest")
        if _integer(r["item_count"]) is None or type(r["truncated"]) is not bool:
            raise ValueError("invalid digest count")
        c = row["comparison"]
        if c is not None and (set(c) != {"settings_equal", "common_leading_items"} or type(c["settings_equal"]) is not bool or (c["common_leading_items"] is not None and _integer(c["common_leading_items"]) is None)):
            raise ValueError("invalid comparison")

    def digest(self, value):
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return hmac.new(self._key, raw, hashlib.sha256).hexdigest()

    def identity_for(self, base_url, model, labels):
        """One identity constructor shared by transport capture and UI selection."""
        labels = labels or {}
        url = urlsplit(base_url)
        origin = [url.scheme, url.hostname, url.port, url.path]
        identity = {"provider": self.digest(origin), "model": str(model)[:256],
                    "auth": str(labels.get("auth", "unknown"))[:256],
                    **{k: self.digest(labels[k]) if labels.get(k) is not None else "unknown"
                       for k in ("bot", "scope", "epoch")}}
        return identity

    def request(self, base_url, body, labels):
        identity = self.identity_for(base_url, body.get("model", "unknown"), labels)
        # Separate input items without inspecting/reordering them. Top-level
        # Anthropic system/tools/settings are in the settings digest.
        items = body.get("input", body.get("messages", []))
        items = items if isinstance(items, list) else [items]
        request = {"settings": self.digest({k: v for k, v in body.items() if k not in ("input", "messages")}),
                   "items": [self.digest(item) for item in items[:MAX_ITEMS]],
                   "item_count": len(items), "truncated": len(items) > MAX_ITEMS}
        return identity, request

    def record(self, identity, row):
        self._validate_row(row)
        with self._lock:
            name = self.digest(identity)
            if name not in self._buckets:
                if len(self._buckets) >= MAX_BUCKETS:
                    oldest = min(self._buckets, key=lambda k: self._buckets[k]["updated"])
                    del self._buckets[oldest]
                self._buckets[name] = {"identity": identity, "recent": [], "totals": summarize([]), "updated": 0, "last": row}
            bucket = self._buckets[name]
            if bucket["recent"]:
                previous = bucket["last"]["request"]
                same = previous["settings"] == row["request"]["settings"]
                common = 0
                if same:
                    for a, b in zip(previous["items"], row["request"]["items"]):
                        if a != b:
                            break
                        common += 1
                row["comparison"] = {"settings_equal": same, "common_leading_items": common if same else None}
            delta = summarize([row])
            totals = bucket["totals"]
            for key, value in delta.items():
                if key != "share":
                    totals[key] += value
            totals["share"] = 100 * totals["cached"] / totals["input"] if totals["input"] else None
            # Only the latest request needs item fingerprints. Retaining them
            # in every recent row would multiply disk work by twenty.
            history_row = {**row, "request": {**row["request"], "items": [],
                           "truncated": bool(row["request"]["item_count"])}}
            bucket["recent"] = [*bucket["recent"], history_row][-RECENT:]
            bucket["last"] = row
            self._sequence += 1
            bucket["updated"] = self._sequence
            self.last_bucket = name
            self._save()

    def _save(self):
        if self.path is None:
            return
        if self._load_failed:
            return  # Preserve a malformed original for explicit recovery.
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {"version": 1, "key": self._key.hex(), "sequence": self._sequence,
                    "last_bucket": self.last_bucket, "buckets": self._buckets}
            fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + ".tmp-")
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, allow_nan=False)
            os.replace(temporary, self.path)
            self.persistence_error = None
        except (OSError, ValueError):
            self.persistence_error = "save_failed"
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def snapshot(self):
        with self._lock:
            buckets = []
            for key, b in sorted(self._buckets.items(), key=lambda pair: pair[1]["updated"], reverse=True):
                last = {k: v for k, v in b["last"].items() if k != "request"}
                last["metrics"] = summarize([b["last"]])
                buckets.append({"bucket": key, "identity": b["identity"], "last": last,
                                "recent20": summarize(b["recent"]), "cumulative": b["totals"]})
            # Never expose the keyed hash salt or persisted item fingerprints.
            return json.loads(json.dumps({"version": 1, "unit": "http_attempt",
                "last_bucket": self.last_bucket, "persistence_error": self.persistence_error,
                "observation_error": self.observation_error,
                "bucket_limit": MAX_BUCKETS, "buckets": buckets}))


def observe_generate(generate, labels_of):
    """Freeze metadata at generation start, including across retries/hot swaps."""
    from functools import wraps

    @wraps(generate)
    async def observed(config, messages, transport, **kwargs):
        try:
            labels = dict(labels_of(config))
        except Exception:
            labels = {}  # Diagnostic metadata must not fail a generation.
        token = _identity.set(labels)
        try:
            return await generate(config, messages, transport, **kwargs)
        finally:
            _identity.reset(token)
    return observed


class _StreamProbe:
    def __init__(self, now):
        self.now = now
        self.buffer = b""
        self.dropping = False
        self.data = []
        self.size = 0
        self.overflow = False
        self.usage = {}
        self.anthropic = False
        self.outcome = "closed"
        self.first_output = None

    def feed(self, chunk):
        # A line-based bounded SSE observer, independent of the business parser.
        for piece in chunk.splitlines(keepends=True):
            if not self.dropping:
                if len(self.buffer) + len(piece) > MAX_EVENT:
                    self.buffer = b""
                    self.dropping = True
                else:
                    self.buffer += piece
            if piece.endswith(b"\n"):
                line = self.buffer.rstrip(b"\r\n")
                bad = self.dropping
                self.buffer = b""
                self.dropping = False
                if bad:
                    self.overflow = True
                elif not line:
                    self.flush()
                elif line.startswith(b"data:"):
                    value = line[5:].lstrip(b" ")
                    self.size += len(value)
                    if self.size <= MAX_EVENT:
                        self.data.append(value)
                    else:
                        self.overflow = True

    def flush(self):
        raw = b"\n".join(self.data) if not self.overflow else b""
        self.data, self.size, self.overflow = [], 0, False
        if raw == b"[DONE]":
            if self.outcome == "closed":
                self.outcome = "completed"
            return
        try:
            event = json.loads(raw)
            if not isinstance(event, dict):
                return
            kind = event.get("type", "")
            response = event.get("response")
            if kind in ("response.completed", "response.incomplete", "response.failed"):
                self.outcome = kind.partition(".")[2]
                if isinstance(response, dict) and isinstance(response.get("usage"), dict):
                    self.usage = dict(response["usage"])
            if isinstance(event.get("usage"), dict):
                self.usage.update(event["usage"])
            message = event.get("message")
            if kind == "message_start" and isinstance(message, dict):
                self.anthropic = True
                if isinstance(message.get("usage"), dict):
                    self.usage.update(message["usage"])
            if kind == "message_stop":
                self.outcome = "completed"
            output = kind == "response.output_text.delta" and bool(event.get("delta"))
            if kind == "content_block_delta":
                delta = event.get("delta")
                output = isinstance(delta, dict) and delta.get("type") == "text_delta" and bool(delta.get("text"))
            choices = event.get("choices", [])
            for choice in choices if isinstance(choices, list) else []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                output = output or (isinstance(delta, dict) and bool(delta.get("content")))
                if choice.get("finish_reason") is not None:
                    self.outcome = "incomplete" if choice["finish_reason"] == "length" else "completed"
            if output and self.first_output is None:
                self.first_output = self.now()
        except (ValueError, TypeError):
            pass


class WireCacheObserver:
    """Wrap HttpxTransport INSIDE ChatTransport, after all body projections."""
    def __init__(self, inner, ledger, *, clock=time.monotonic, local_prefix=None, labels_of=None):
        self._inner, self._ledger, self._clock = inner, ledger, clock
        self._local_prefix = local_prefix
        self._labels_of = labels_of

    def stream_chat(self, base_url, body, **kwargs):
        async def one():
            start = self._clock()
            try:
                from .owned_blocking import owned_thread
                labels = dict(_identity.get() or {})
                if self._labels_of is not None:labels.update(self._labels_of())
                identity, request = await owned_thread(self._ledger.request, base_url, body, labels)
            except Exception:
                identity = request = None
                self._ledger.observation_error = "fingerprint_failed"
            if identity is not None and self._local_prefix is not None:
                try:
                    await owned_thread(self._local_prefix.observe, identity, body, kwargs.get('path', ''))
                except Exception:
                    self._ledger.observation_error = 'local_prefix_failed'
            probe = _StreamProbe(self._clock)
            upstream = None
            failed = False
            try:
                upstream = self._inner.stream_chat(base_url, body, **kwargs)
                async for chunk in upstream:
                    try:
                        probe.feed(chunk)
                    except Exception:
                        self._ledger.observation_error = "stream_probe_failed"
                    yield chunk
            except Exception:
                failed = True
                raise
            finally:
                try:
                    if upstream is not None:
                        try:
                            await upstream.aclose()
                        except BaseException:
                            failed = True
                            raise
                finally:
                    if identity is not None:
                        try:
                            await owned_thread(self._ledger.record, identity, {
                                "usage": normalize_usage(probe.usage, anthropic=probe.anthropic),
                                "outcome": "error" if failed else probe.outcome,
                                "first_output_ms": (None if probe.first_output is None else max(0, (probe.first_output - start) * 1000)),
                                "elapsed_ms": max(0, (self._clock() - start) * 1000),
                                "request": request, "comparison": None})
                        except Exception:
                            # Observations are additive, never request admission.
                            self._ledger.observation_error = "record_failed"
        return one()

    async def aclose(self):
        await self._inner.aclose()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def aggregate_buckets(snapshot, identity, background_scope, *, current_epoch=False):
    """Current provider/model across this Bot's epochs, including unlabelled helpers.

    Never sum different providers/models, and never invent usage for failed attempts.
    Retained ledger totals are historical; pre-upgrade background omissions remain unknown.
    """
    counts = ("attempts", "reported_attempts", "unknown_attempts", "invalid_attempts",
              "input", "cached", "uncached")
    groups = {name: dict.fromkeys(counts, 0) for name in ("conversation", "consolidation", "auxiliary")}
    for bucket in snapshot["buckets"]:
        own = bucket["identity"]
        if (own["provider"] != identity["provider"] or own["model"] != identity["model"]
                or own["bot"] not in (identity["bot"], "unknown")):
            continue
        if current_epoch and own["epoch"] != identity["epoch"]:continue
        name = ("consolidation" if own["scope"] == background_scope else
                "auxiliary" if own["scope"] == "unknown" else "conversation")
        for key in counts:
            groups[name][key] += bucket["cumulative"][key]
    total = {key: sum(row[key] for row in groups.values()) for key in counts}
    for row in [total, *groups.values()]:
        row["share"] = 100 * row["cached"] / row["input"] if row["input"] else None
    return {"version": 1, "scope": "all_paths", "unit": "http_attempt",
            "status": "ready" if total["attempts"] else "no_sample", "model": identity["model"],
            "selected": {"cumulative": total}, "groups": groups,
            "coverage_note": "含对话、交接后续、后台整理和辅助请求；升级前漏记的用量无法补算。"}
