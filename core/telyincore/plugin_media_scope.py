"""QQ media resources scoped by trusted host, without changing model/voice root."""
from pathlib import Path
from .plugin_scope_store import ScopeError, identifier, path_key


def qq_media_paths(ctx):
    root=Path(ctx['root'])
    supplied=ctx.get('pluginDataDir')
    if supplied is None:
        if ctx.get('pluginSettingsBot') is not None or ctx.get('pluginConfigPath') is not None:
            raise ScopeError('incomplete QQ plugin resource scope')
        # Compatibility only for legacy hosts, never a scoped-host fallback.
        return root/'data/media/qq',root/'data/media/outbox'
    bot=identifier(ctx.get('bot_id'))
    if ctx.get('pluginSettingsBot')!=bot:
        raise ScopeError('QQ media owner mismatch')
    expected=root.parent/'bots'/bot/'infinity/plugins/core-infinity-qq/runtime'
    data=Path(supplied)
    if not root.is_absolute() or not data.is_absolute() or path_key(data)!=path_key(expected):
        raise ScopeError('QQ resource path does not match host Bot')
    if path_key(data.resolve())!=path_key(expected.absolute()):
        raise ScopeError('redirected QQ media scope rejected')
    paths=data/'media/qq',data/'media/outbox'
    for path in paths:
        if path_key(path.resolve())!=path_key(path.absolute()):
            raise ScopeError('redirected QQ media directory rejected')
    return paths


def validate_reference(scope, target, media_id, question):
    """Validate model arguments, NOT media ownership or permission to read."""
    import re
    if not isinstance(scope, str) or scope not in ('private', 'group'):
        raise ValueError('只支持 QQ 私聊或群聊图片')
    if not isinstance(target, str) or not re.fullmatch(r'[0-9]{1,20}', target):
        raise ValueError('QQ 会话编号无效')
    if not isinstance(media_id, str) or not re.fullmatch(r'[0-9a-f]{24}', media_id):
        raise ValueError('图片编号无效')
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500:
        raise ValueError('问题必须是 1 到 500 字')
    return f'plugin-media:core-infinity-qq:{scope}:{target}:{media_id}', question.strip()
