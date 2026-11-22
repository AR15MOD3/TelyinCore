"""Known host-owned metadata in private plugin/SnowLuma directories.

Opaque configuration, logs, backups and plugin payloads move with the Bot without
text replacement. Only documented identity fields and the port registry change.
"""
from runtime_identity import safe_path


def prepare(core, old, new, patch):
    core = core.resolve()
    root = core.parent
    plugins = safe_path(root, root / 'bots' / old / 'infinity')
    if not plugins.exists():
        return
    if any(p.name not in ('qq', 'processes', 'plugins', 'snowluma') for p in plugins.iterdir()):
        raise ValueError('此档案有尚未适配改名的扩展，未移动档案')
    state = safe_path(root, plugins / 'plugins/state.json')
    if state.exists():
        from telyincore.plugin_scope_store import PluginScopeStore
        value = PluginScopeStore(root / 'bots', old).read_state()
        value['bot_id'] = new
        value['revision'] += 1
        if 'initialization' in value:
            value['initialization']['bot_id'] = new
        patch(state.relative_to(root).as_posix(), value)
    snow = safe_path(root, plugins / 'snowluma')
    if not snow.exists():
        return
    # Custody is native/account authority, not a display name. Never rewrite it.
    if (snow / 'companion-custody.json').exists():
        raise ValueError('SnowLuma仍有外部进程归属记录，未改名')
    from snowluma_instance import load_instance, _allocations, _free
    instance = load_instance(core, old)
    if instance is None:
        raise ValueError('SnowLuma目录缺少有效实例登记，未改名')
    registry, _, allocations = _allocations(core)
    if allocations['bots'].get(old) != list(instance.ports):
        raise ValueError('SnowLuma实例与端口登记不一致，未改名')
    if new in allocations['bots']:
        raise ValueError('目标名称已有SnowLuma端口登记，未覆盖')
    if not all(_free(port) for port in instance.ports):
        raise ValueError('SnowLuma端口仍被占用，请先正常退出接入服务；未改名')
    allocations['bots'][new] = allocations['bots'].pop(old)
    patch(registry.relative_to(root).as_posix(), allocations)
    patch((snow / 'instance.json').relative_to(root).as_posix(),
          {'version': 1, 'bot': new, 'ports': list(instance.ports)})
