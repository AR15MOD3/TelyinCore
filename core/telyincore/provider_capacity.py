"""Documented capacities, only for exact official endpoint/model identities.

Source checked 2026-11-09: https://api-docs.deepseek.com/quick_start/pricing/
DeepSeek documents 1M, without specifying binary units there. Use the
conservative decimal 1,000,000 rather than assume 1,048,576. This is a
provider-documented fallback, not a claim that /models reported a number.
Explicit configuration always wins; custom gateways are never inferred.
"""
from urllib.parse import urlsplit


def documented_context(base_url: str, model: str) -> int | None:
    try:
        url = urlsplit(base_url)
        official = (url.scheme == 'https' and url.hostname == 'api.deepseek.com'
                    and url.port in (None, 443) and not url.username and not url.password
                    and not url.query and not url.fragment
                    and url.path.rstrip('/') in ('', '/v1', '/anthropic'))
    except (TypeError, ValueError):
        return None
    if official and model in {'deepseek-flash', 'deepseek-v4-flash',
                              'deepseek-v4-flash-vision-exp', 'deepseek-v4-pro'}:
        return 1_000_000
    return None
