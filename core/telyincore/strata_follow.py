"""Opt-in loopback Strata capabilities; no persisted profile overwrites.

The round caller refreshes off-loop, then pins one snapshot. No background job,
inference request, API secret, port scanning, or redirect/proxy is involved.
"""
import json
from pathlib import Path
import urllib.parse
import urllib.request


class StrataFollowError(ValueError):
    pass


def settings(root, profile):
    path = Path(root) / "strata-follow.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise StrataFollowError("strata_follow_unreadable_settings") from None
    if type(data) is not dict or data.get("version") != 1 or type(data.get("profiles")) is not dict:
        raise StrataFollowError("strata_follow_bad_settings")
    cfg = data["profiles"].get(profile)
    if cfg is None:
        return None
    if (type(cfg) is not dict or set(cfg) - {"context_window", "multimodal"}
            or any(type(v) is not bool for v in cfg.values())):
        raise StrataFollowError("strata_follow_bad_fields")
    return cfg if any(cfg.values()) else None


def context_policy(root, identity):
    """Known Strata gateway needs no opt-in for capacity checking.

    Other ports are accessed only with the explicit existing integration policy.
    Vision remains a separate opt-in; discovering context never enables vision.
    """
    cfg = settings(root, identity[0])
    url = urllib.parse.urlsplit(identity[1] or "")
    try:
        known = url.port == 47831 and endpoint(identity[1])
    except (ValueError, StrataFollowError):
        known = False
    if known:
        return {**(cfg or {}), "context_window": True}
    return cfg


def endpoint(base_url):
    url = urllib.parse.urlsplit(base_url)
    if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1")
            or not url.port or url.username or url.password or url.query or url.fragment
            or url.path.rstrip("/") != "/v1"):
        raise StrataFollowError("strata_follow_requires_explicit_loopback_gateway")
    return urllib.parse.urlunsplit((url.scheme, url.netloc, "/api/native", "", ""))


def read_native(url):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(url, timeout=2) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise StrataFollowError("strata_follow_response_too_large")
    return json.loads(raw)


def capabilities(data, model, follow):
    if type(data) is not dict or type(data.get("instances")) is not list:
        raise StrataFollowError("strata_follow_not_native_inventory")
    # Exact API selection, never display-name guessing or another model's caps.
    rows = [r for r in data["instances"] if type(r) is dict
            and r.get("model") == model]
    if len(rows) != 1 or rows[0].get("status") != "ready":
        raise StrataFollowError("strata_follow_model_not_uniquely_ready")
    row = rows[0]
    result = {}
    if follow.get("context_window"):
        context = row.get("context")
        if type(context) is not int or not 128 <= context <= 10000000:
            raise StrataFollowError("strata_follow_unknown_context")
        actual = row.get("metrics", {}).get("engine", {}).get("max_context")
        if type(actual) is not int or actual != context:
            raise StrataFollowError("strata_follow_context_not_confirmed")
        result["context_window"] = context
    if follow.get("multimodal"):
        vision = row.get("vision")
        if type(vision) is not bool:
            raise StrataFollowError("strata_follow_unknown_vision")
        result["multimodal"] = vision
    return result


class FollowState:
    def __init__(self):
        self.identity = None
        self.values = {}

    def refresh(self, root, identity, *, loader=read_native):
        self.identity, self.values = None, {}
        name, base, model = identity
        follow = context_policy(root, identity)
        if not follow:
            return
        try:
            values = capabilities(loader(endpoint(base)), model, follow)
        except Exception as exc:
            raise StrataFollowError("strata_follow_refresh_failed:" + type(exc).__name__) from None
        # A concurrent policy/config change invalidates the fetched result.
        if context_policy(root, identity) != follow:
            raise StrataFollowError("strata_follow_policy_changed")
        self.identity, self.values = identity, values

    def get(self, root, identity):
        follow = context_policy(root, identity)
        if not follow or identity != self.identity:
            return {}
        return {k: v for k, v in self.values.items() if follow.get(k)}
