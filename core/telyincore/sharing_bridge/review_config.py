"""Per-Bot review policy, bound to the existing shared Engine. No writes or inference.

Host roots are cooperative in-process inputs, not new native identity credentials.
Only the worker's request_snapshot reads credentials; Selection never contains them.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

MODEL_ENV = ('TELYIN_UPSTREAM', 'TELYIN_MODEL', 'TELYIN_MAX_TOKENS')


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_review_policy_key')
        result[key] = value
    return result


def settings(raw):
    value = json.loads(raw, object_pairs_hook=unique) if raw else {}
    allowed = {'version', 'mode', 'profile', 'allow_external', 'max_tokens', 'timeout_seconds'}
    if (type(value) is not dict or set(value) - allowed
            or type(value.get('version', 1)) is not int or value.get('version', 1) != 1):
        raise ValueError('invalid_review_policy')
    result = dict(mode=value.get('mode', 'follow'), profile=value.get('profile', ''),
                  allow_external=value.get('allow_external', False),
                  max_tokens=value.get('max_tokens', 1024),
                  timeout_seconds=value.get('timeout_seconds', 12))
    if (result['mode'] not in ('follow', 'profile') or type(result['profile']) is not str
            or len(result['profile']) > 160
            or any(ord(c) < 32 for c in result['profile'])
            or (result['mode'] == 'profile' and not result['profile'].strip())
            or (result['mode'] == 'follow' and result['profile'])):
        raise ValueError('ambiguous_review_profile')
    if (type(result['allow_external']) is not bool
            or type(result['max_tokens']) is not int or not 128 <= result['max_tokens'] <= 1024
            or type(result['timeout_seconds']) is not int
            or not 1 <= result['timeout_seconds'] <= 12):
        raise ValueError('invalid_review_budget_or_external_permission')
    return result


def read(path, limit):
    if path.is_symlink():
        raise ValueError('review_configuration_link_refused')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('review_configuration_too_large')
    return raw


def policy_bytes(root):
    try:
        return read(root / 'sharing-review.json', 16384)
    except FileNotFoundError:
        # A dangling link is invalid, never equivalent to absent configuration.
        if (root / 'sharing-review.json').is_symlink():
            raise ValueError('review_configuration_link_refused') from None
        return b''


def optional_bytes(path):
    try:
        return read(path, 16384)
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError('review_configuration_link_refused') from None
        return b''


def root_key(value):
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError('explicit_review_root_required')
    path = path.resolve(strict=True)
    stat = path.stat()
    return path, (os.path.normcase(str(path)), stat.st_dev, stat.st_ino)


def endpoint_allowed(base, external):
    try:
        url = urlsplit(base)
        if (type(base) is not str or any(c.isspace() or ord(c) < 32 for c in base)
                or not url.hostname or url.username is not None or url.password is not None
                or url.query or url.fragment or url.path.rstrip('/') != '/v1'):
            return False
        url.port
        local = url.hostname in ('localhost', '127.0.0.1', '::1')
        return (url.scheme in ('http', 'https') if local else external and url.scheme == 'https')
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class Selection:
    bot: str
    engine_identity: tuple
    policy_identity: tuple
    engine_sha256: str
    policy_sha256: str
    selection_sha256: str
    proxy_sha256: str
    mode: str
    profile: str
    kind: str
    base_url: str
    model: str
    max_tokens: int
    timeout_seconds: int
    allow_external: bool
    model_env: tuple

    @property
    def binding(self):
        raw = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(raw).hexdigest()


def select(engine_root, policy_root, bot, *, env=None):
    from telyincore.engine import Engine
    if type(bot) is not str or not bot or len(bot) > 160 or any(ord(c) < 32 for c in bot):
        raise ValueError('explicit_review_bot_required')
    engine_path, engine_identity = root_key(engine_root)
    policy_path, policy_identity = root_key(policy_root)
    first = read(engine_path / 'engine.json', 2 * 1024 * 1024)
    raw_policy = policy_bytes(policy_path)
    cfg = settings(raw_policy)
    def selection_bytes():
        return (optional_bytes(policy_path / 'engine-selection.json')
                if cfg['mode'] == 'follow' and policy_path != engine_path else b'')
    first_selection = selection_bytes()
    first_proxy = optional_bytes(engine_path / 'model-proxy.json')
    environment = os.environ if env is None else env
    model_env = tuple((k, environment[k]) for k in MODEL_ENV if k in environment)
    effective_env = dict(model_env) if cfg['mode'] == 'follow' else {}
    engine = Engine(engine_path, env=effective_env,
                    state_root=policy_path if cfg['mode'] == 'follow' else None)
    if engine.read_error():
        raise ValueError('review_engine_unavailable')
    name = engine.current_name() if cfg['mode'] == 'follow' else cfg['profile']
    if not name:
        raise ValueError('review_profile_not_selected')
    profile = engine.profile(name)
    if profile.auth != 'api_key' or profile.kind not in ('chat', 'responses'):
        raise ValueError('configured_profile_not_supported')
    base = engine.effective('base_url', profile=profile).value
    model = engine.effective('model', profile=profile).value
    total = engine.effective('max_tokens', profile=profile).value
    if type(model) is not str or not model.strip() or not endpoint_allowed(base, cfg['allow_external']):
        raise ValueError('review_endpoint_not_authorized')
    if type(total) is not int or total < 128:
        raise ValueError('review_parent_output_too_small')
    if (first != read(engine_path / 'engine.json', 2 * 1024 * 1024)
            or raw_policy != policy_bytes(policy_path)
            or first_selection != selection_bytes()
            or first_proxy != optional_bytes(engine_path / 'model-proxy.json')
            or root_key(engine_root)[1] != engine_identity
            or root_key(policy_root)[1] != policy_identity):
        raise ValueError('review_configuration_changed_during_selection')
    return Selection(bot, engine_identity, policy_identity,
        hashlib.sha256(first).hexdigest(), hashlib.sha256(raw_policy).hexdigest(),
        hashlib.sha256(first_selection).hexdigest(), hashlib.sha256(first_proxy).hexdigest(),
        cfg['mode'], name, profile.kind, base.rstrip('/'), model,
        min(total, cfg['max_tokens']), cfg['timeout_seconds'], cfg['allow_external'],
        model_env if cfg['mode'] == 'follow' else ())


def verify(engine_root, policy_root, bot, selection, *, env=None):
    current = select(engine_root, policy_root, bot, env=env)
    if current.binding != selection.binding:
        raise ValueError('review_configuration_changed')
    return current


def worker_context(selection):
    # Kept outside the model payload. No credential values or secret variable names.
    return dict(bot=selection.bot, engine_root=selection.engine_identity[0],
                policy_root=selection.policy_identity[0], binding=selection.binding,
                model_env=dict(selection.model_env))


def capture_request(engine_root, context):
    from telyincore.engine import Engine, ENV_NAMES
    if (type(context) is not dict
            or set(context) != {'bot', 'engine_root', 'policy_root', 'binding', 'model_env'}
            or type(context['model_env']) is not dict or set(context['model_env']) - set(MODEL_ENV)
            or any(type(v) is not str for v in context['model_env'].values())
            or root_key(engine_root)[1][0] != context['engine_root']):
        raise ValueError('review_host_context_required')
    selection = select(engine_root, context['policy_root'], context['bot'], env=context['model_env'])
    if selection.binding != context['binding']:
        raise ValueError('review_context_binding_changed')
    environment = dict(os.environ)
    for key in ENV_NAMES.values():
        environment.pop(key, None)
    if selection.mode == 'follow':
        environment.update(context['model_env'])
    engine = Engine(Path(engine_root), env=environment,
                    state_root=Path(context['policy_root']) if selection.mode == 'follow' else None)
    # This is a private, fresh Engine instance; use() only changes its draft.
    # No save(), no shared current-profile change, no service or model discovery.
    engine.use(selection.profile)
    captured = engine.live().request_snapshot()
    if (captured.config.model != selection.model
            or captured.config.base_url.rstrip('/') != selection.base_url
            or captured.kind != selection.kind):
        raise ValueError('review_request_selection_mismatch')
    verify(engine_root, context['policy_root'], context['bot'], selection, env=context['model_env'])
    return selection, captured, engine.model_network(selection.base_url)
