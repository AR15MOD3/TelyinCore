"""Explicit public deployment choices, consumed by the regular verified host.

No Bot launches, account copies, ownership claims or lifecycle capabilities.
This policy is only a first-start seed; a private receipt always wins later.
"""
import json
from pathlib import Path
from telyincore.plugin_scope_store import ScopeError,identifier,plugin_name,path_key


def initial_plugin_choices(core_root,bot,baseline_bot):
    core=Path(core_root)
    bot,baseline_bot=identifier(bot),identifier(baseline_bot)
    if not core.is_absolute() or path_key(core.resolve())!=path_key(core.absolute()):
        raise ScopeError('authenticated non-redirected Core root required')
    path=core/'plugin-scope-bootstrap.json'
    if path_key(path.resolve())!=path_key(path.absolute()):
        raise ScopeError('redirected plugin bootstrap rejected')
    try:
        with path.open('rb') as stream:raw=stream.read(65537)
    except FileNotFoundError:return {}
    if len(raw)>65536:raise ScopeError('plugin bootstrap exceeds size limit')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ScopeError('duplicate plugin bootstrap field')
            result[key]=value
        return result
    try:value=json.loads(raw.decode('utf-8-sig'),object_pairs_hook=pairs)
    except (UnicodeError,ValueError) as exc:raise ScopeError('malformed plugin bootstrap') from exc
    if (not isinstance(value,dict) or set(value)!={'schema','baseline_bot','bots'}
            or type(value['schema']) is not int or value['schema']!=1
            or value['baseline_bot']!=baseline_bot or not isinstance(value['bots'],dict)):
        raise ScopeError('bootstrap baseline/schema mismatch')
    for owner,choices in value['bots'].items():
        identifier(owner)
        if not isinstance(choices,dict):raise ScopeError('initial plugin choices must be object')
        for name,on in choices.items():
            plugin_name(name)
            if type(on) is not bool:raise ScopeError('initial plugin choices must be boolean')
    return dict(value['bots'].get(bot,{}))
