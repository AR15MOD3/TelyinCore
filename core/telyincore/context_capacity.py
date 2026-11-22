"""Owned, bounded model-directory lookup. Never generates text or guesses aliases."""
import time


def select_capacity(result, model):
    if not isinstance(result, dict) or not result.get('ok'):
        return None
    rows = [r for r in result.get('specs') or []
            if isinstance(r, dict) and r.get('id') == model]
    if len(rows) != 1:
        return None
    value = rows[0].get('context_window')
    return value if type(value) is int and 128 <= value <= 100_000_000 else None


def lookup(engine, profile, base, model):
    asked = dict(name=profile.name, auth=profile.auth, kind=profile.kind,
                 base_url=base, model=model, mode='models', secret_name=profile.secret)
    if profile.auth == 'chatgpt':
        scope = engine.chatgpt_scope()
        try:
            return engine.web_subscription_probe(asked, scope=scope)
        finally:
            if not scope.close(timeout=10):
                raise RuntimeError('capacity_subscription_cleanup_unconfirmed')
    import httpx
    from .web import _model_specs, USER_AGENT
    from .protocol import ANTHROPIC_VERSION
    snap = engine.web_probe_snapshot(asked)
    headers = {'User-Agent': USER_AGENT}
    key = snap['api_key']
    if profile.kind == 'anthropic':
        headers.update({'x-api-key': key, 'anthropic-version': ANTHROPIC_VERSION})
    elif key:
        headers['Authorization'] = 'Bearer ' + key
    deadline = time.monotonic() + 5
    with httpx.Client(proxy=snap['proxy'], trust_env=False, follow_redirects=False,
                      timeout=5) as client:
        with client.stream('GET', base.rstrip('/') + '/models', headers=headers) as response:
            response.raise_for_status()
            chunks = []; size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 2 * 1024 * 1024 or time.monotonic() > deadline:
                    raise ValueError('capacity_catalog_limit')
                chunks.append(chunk)
    return {'ok': True, 'status': response.status_code, 'specs': _model_specs(b''.join(chunks).decode('utf-8'))}
