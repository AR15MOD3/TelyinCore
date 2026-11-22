"""Trusted launch plumbing: one Bot's private state, explicit legacy mappings.

Aliases preserve existing config in place; never copy credentials or infer a
Bot from display names. This module creates no authority and starts nothing.
"""
from pathlib import Path
from telyincore.plugin_scope_store import PluginScopeStore,identifier


def make_plugin_scope(core_root,bot,baseline_bot):
    core=Path(core_root)
    if not core.is_absolute():raise ValueError('absolute authenticated Core root required')
    bot,baseline_bot=identifier(bot),identifier(baseline_bot)
    private=core.parent/'bots'/bot/'infinity'
    # Baseline is already established by the host's existing owner manifest.
    # Preserve its old QQ file as that baseline's explicit alias only.
    # Non-baseline Bot must NEVER fall back to this shared file.
    aliases={
        'core-infinity-qq':core/'extensions/core-infinity-qq/config.json' if bot==baseline_bot else private/'qq/config.json',
    }
    from telyincore.plugin_runtime_policy import PluginRuntimePolicy
    policy = PluginRuntimePolicy.bind(
        core, core.parent/'bots', bot,
        scope_required=('core-infinity-qq', 'core-infinity-wavesuid'),
        dependencies=())
    return PluginScopeStore(core.parent/'bots',bot,config_aliases=aliases,
                           runtime_policy=policy)
