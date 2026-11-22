"""Explicit, host-owned initialization; no account/config copies or processes.

Call only after regular host ownership checks, before wake. Baseline identity is
the established owner manifest identity, not a user-entered display name. Other
Bots never read legacy switches. Production wiring is a separate CAS change.
"""
import hashlib
import json
import os
from pathlib import Path
from telyincore.plugin_scope_store import ScopeError, identifier, plugin_name, path_key


def initialize_plugin_scope(scope, core_root, baseline_bot, *, initial_choices=None):
    core=Path(core_root)
    baseline=identifier(baseline_bot)
    if not core.is_absolute() or path_key(core.resolve())!=path_key(core.absolute()):
        raise ScopeError('absolute non-redirected authenticated Core root required')
    if path_key(scope.bots.absolute())!=path_key(core.parent/'bots'):
        raise ScopeError('Bot parent does not belong to Core host')
    choices={} if initial_choices is None else initial_choices
    if not isinstance(choices,dict):raise ScopeError('initial choices must be object')
    for name,on in choices.items():
        plugin_name(name)
        if type(on) is not bool:raise ScopeError('plugin switch must be boolean')
    # initialize() takes the lock itself; don't nest OS locks. Instead capture
    # immutable input then publish exactly once. An existing receipt short-circuits
    # before legacy IO, including after legacy corruption or package upgrades.
    current=scope.read_state()
    if 'initialization' in current:
        return scope.initialize({},provenance={})
    seed={}
    raw=None
    source={'kind':'explicit-initial-choices' if choices else 'conservative-empty',
            'baseline_bot':baseline,'legacy_sha256':None,
            'explicit_choices':dict(choices)}
    if scope.bot==baseline and not scope.state.exists():
        path=core/'extensions/state.json'
        if path_key(path.resolve())!=path_key(path.absolute()):raise ScopeError('redirected legacy state rejected')
        try:
            with path.open('rb') as stream:raw=stream.read(128*1024+1)
        except FileNotFoundError:raw=None
        if raw is not None:
            if len(raw)>128*1024:raise ScopeError('legacy switches exceed size limit')
            def pairs(items):
                result={}
                for name,on in items:
                    if name in result:raise ScopeError('duplicate legacy switch')
                    result[name]=on
                return result
            try:legacy=json.loads(raw.decode('utf-8-sig'),object_pairs_hook=pairs)
            except (UnicodeError,ValueError) as exc:raise ScopeError('malformed legacy switches') from exc
            if not isinstance(legacy,dict):raise ScopeError('legacy switches must be object')
            for name,on in legacy.items():
                plugin_name(name)
                if type(on) is not bool:raise ScopeError('legacy switch must be boolean')
            seed.update(legacy)
            source.update(kind='baseline-legacy-snapshot',legacy_sha256=hashlib.sha256(raw).hexdigest())
    # Explicit authorized startup selection beats shared history, but never
    # beats a private choice saved before/concurrently with initialization.
    return scope.initialize(dict(choices),provenance=source,legacy_seed=seed,legacy_raw=raw)
