"""Shared input ceiling; model context is not all available for payload.

The eight-token reserve is verified for the local Strata gateway. Do not
infer it for arbitrary providers/ports. Token estimates remain estimates.
"""
from urllib.parse import urlsplit


def context_reserve_tokens(base_url: str = "") -> int:
    try:
        url = urlsplit(base_url or "")
        known = (url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")
                 and url.port == 47831 and not url.username and not url.password
                 and not url.query and not url.fragment and url.path.rstrip("/") == "/v1")
    except (TypeError, ValueError, AttributeError):
        return 0
    return 8 if known else 0


def hard_input_tokens(window: int | None, output: int, base_url: str = "") -> int | None:
    """None retains the existing unknown-window semantics; never go negative."""
    if window is None:
        return None
    return max(0, window - output - context_reserve_tokens(base_url))
