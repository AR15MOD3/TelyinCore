"""Product identity-phase projection, not a fixture or rename authorization.

Reads only fixed bounded identity metadata. Directory moves preserve opaque
profile/runtime payloads; no chat, memory, account or provider bodies are read.
The future transaction must additionally bind byte-preserving payload snapshots,
native quiescence, adapters, private backup and durable phase receipts. This
projection grants NONE of those capabilities and exposes no apply/unfence API.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat

from bot_rename_contexts import (control_limit, CONTROL_BYTES, witnesses, capture_contexts,
                                 context_operations, opaque_pin, check_payloads)
from bot_rename_plugins import capture_plugins, derive_plugins, namespace_pin, check_namespaces
from bot_rename_accounts import ACCOUNT_NAMES, capture_accounts, check_account_scope
from bot_rename_recovery import capture_recovery, check_scope as check_recovery_scope
from bot_registry import bot_id
from bot_migration_gate import GATE, RETIRED, retired_names
from runtime_identity import LEDGER, MARKER, check_layout, safe_path


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate identity field')
            result[key] = value
        return result
    result = json.loads(raw, object_pairs_hook=unique)
    if type(result) is not dict:
        raise ValueError('identity object required')
    return result


def _encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       allow_nan=False) + '\n').encode()


def _pin(root, relative, *, read=True):
    path = safe_path(root, root / relative)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return ('absent',), None
    if stat.S_ISDIR(info.st_mode):
        return ('directory', info.st_dev, info.st_ino), None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('ordinary single-link identity metadata required')
    if not read:
        return ('opaque-file', info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns), None
    with path.open('rb') as stream:
        raw = stream.read(control_limit(relative)+1)
        opened = os.fstat(stream.fileno())
    if (len(raw) > control_limit(relative) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
            or opened.st_nlink != 1 or not stat.S_ISREG(opened.st_mode)):
        raise ValueError('identity metadata changed or oversized')
    return ('file', info.st_dev, info.st_ino, _sha(raw)), raw


INSTANCE_ROOT = 'core/data/instances'


def _instance_paths(root):
    """Bounded names only. PID/port metadata is NEVER native absence proof."""
    folder = safe_path(root, root / INSTANCE_ROOT)
    if not folder.exists():
        return None
    if not folder.is_dir():
        raise ValueError('instance metadata directory required')
    paths = []
    for child in folder.iterdir():
        safe_path(root, child)
        name = child.name
        if not name.endswith('.json') or name != bot_id(name[:-5]) + '.json':
            raise ValueError('unclassified instance metadata entry')
        relative = INSTANCE_ROOT + '/' + name
        if _pin(root, relative, read=False)[0][0] != 'opaque-file':
            raise ValueError('ordinary instance metadata file required')
        paths.append(relative)
        if len(paths) > 1024:
            raise ValueError('bounded instance metadata roster required')
    return tuple(sorted(paths))


def _validate_instance_controls(controls, bindings):
    seen_pids = set()
    for relative, raw in controls.items():
        if not relative.startswith(INSTANCE_ROOT + '/'):
            continue
        name = relative[len(INSTANCE_ROOT)+1:]
        if not name.endswith('.json') or name != bot_id(name[:-5]) + '.json' or name[:-5] not in bindings:
            raise ValueError('instance identity unbound or aliased')
        if len(raw) > control_limit(relative):
            raise ValueError('bounded instance record required')
        row = _json(raw)
        if (set(row) != {'pid', 'port'} or type(row['pid']) is not int
                or not 0 < row['pid'] <= 0xffffffff or type(row['port']) is not int
                or not 1 <= row['port'] <= 65535):
            raise ValueError('unsupported instance metadata shape')
        if row['pid'] in seen_pids:
            raise ValueError('duplicate instance PID requires reconciliation')
        seen_pids.add(row['pid'])



BINDING_BACKUP = 'core/data/' + LEDGER + '.bak'


def _validate_binding_backup(raw, current, new):
    """Validate historical identity data, never restore/promote it as authority.

    Retain its exact original bytes/inode through all phases. A historical
    destination or name/token alias must be reconciled, not silently recycled.
    This is one prior ledger, NOT proof of an exhaustive identity history.
    """
    data = _json(raw)
    if (len(raw)>65536 or set(data)!={'version','bindings'}
            or type(data['version']) is not int or data['version']!=1
            or type(data['bindings']) is not dict or len(data['bindings'])>1024):
        raise ValueError('unsupported historical binding backup')
    import re
    folded=set();tokens=set();current_tokens={v:k for k,v in current.items()}
    current_names={k.casefold():k for k in current}
    for name, token in data['bindings'].items():
        if (type(name) is not str or bot_id(name)!=name or name.startswith('.')
                or name.casefold() in folded or type(token) is not str
                or re.fullmatch('[0-9a-f]{32}',token) is None or token in tokens):
            raise ValueError('invalid historical binding identity')
        folded.add(name.casefold());tokens.add(token)
        if name.casefold()==new.casefold():
            raise ValueError('destination appears in historical binding backup')
        canonical=current_names.get(name.casefold())
        if (canonical is not None and (canonical!=name or current[canonical]!=token)
                or token in current_tokens and current_tokens[token]!=name):
            raise ValueError('historical binding alias/reuse requires reconciliation')
    return data

def _observe_pin(root, relative, expected):
    if expected[0] in ('namespace-absent','namespace-directory'):
        return namespace_pin(root, relative)
    if expected[0] in ('payload-directory','context-directory','payload-file'):
        return opaque_pin(root, relative, expected[0])
    if expected[0] == 'adapter':
        info = safe_path(root, root / relative).lstat()
        return ('adapter', info.st_dev, info.st_ino)
    if expected[0] == 'entries':
        info = _pin(root, relative, read=False)[0]
        if info[0] != 'directory':
            return info
        return ('entries', info[1], info[2], _instance_paths(root))
    return _pin(root, relative, read=expected[0] == 'file')[0]


@dataclass(frozen=True)
class IdentityPhase:
    """Exact controlled projection, not a whole-tree or offline proof."""
    roots: tuple
    files: tuple
    opaque: tuple = ()
    namespaces: tuple = ()

    def describe(self):
        return {'roots': list(self.roots), 'files': {key: _sha(raw) for key, raw in self.files},
                'payloads_opaque': True, 'authority': False}


@dataclass(frozen=True)
class IdentityOperation:
    kind: str
    path: str
    destination: str | None = None
    before: bytes | None = None
    after: bytes | None = None


@dataclass(frozen=True)
class IdentityProjection:
    core: str
    old: str
    new: str
    operations: tuple
    phases: tuple
    adapters_required: tuple
    pins: tuple
    context_witnesses: tuple | None = None
    plugin_configs: bool = False
    account_history: bool = False
    recovery_stores: bool = False
    creation: object = None

    def assert_source_current(self):
        root = Path(self.core).parent
        for relative, expected in self.pins:
            actual = _observe_pin(root, relative, expected)
            if actual != expected:
                raise ValueError('identity projection source changed; replan required')
        _layout(Path(self.core), self.creation)
        self.assert_phase_current(0)

    def assert_phase_current(self, cursor):
        """Check only derived controls at one prefix; never advance a journal.

        Opaque payloads, adapters and native/offline authority remain unproven.
        Intermediate identity mismatch is confined here, not allowed by startup.
        """
        if type(cursor) is not int or not 0 <= cursor < len(self.phases):
            raise ValueError('invalid identity phase cursor')
        root = Path(self.core).parent
        expected = self.phases[cursor]
        roots = {p for phase in self.phases for p in phase.roots}
        files = {p for phase in self.phases for p, raw in phase.files}
        observed = _instance_paths(root)
        records = tuple(sorted(p for p, raw in expected.files if p.startswith(INSTANCE_ROOT + '/')))
        if ((INSTANCE_ROOT in expected.roots) != (observed is not None)
                or observed is not None and observed != records):
            raise ValueError('phase instance roster changed')
        for relative in roots:
            kind = _pin(root, relative, read=False)[0][0]
            if kind != ('directory' if relative in expected.roots else 'absent'):
                raise ValueError('phase directory projection mismatch')
        if not self.adapters_required:  # unsupported read-only plans never reach prepare
            check_recovery_scope(root, dict(expected.namespaces), dict(self.phases[0].files))
        check_account_scope(root, dict(expected.namespaces))
        check_namespaces(root, dict(expected.namespaces), {p for phase in self.phases for p,_ in phase.namespaces})
        check_payloads(root, dict(expected.files), dict(expected.opaque))
        values = dict(expected.files)
        for relative in files:
            if relative not in values:
                if _pin(root, relative, read=False)[0] != ('absent',):
                    raise ValueError('old identity metadata still present')
            elif _pin(root, relative)[1] != values[relative]:
                raise ValueError('phase metadata projection mismatch')

    def describe(self):
        return {'version': 1, 'old': self.old, 'new': self.new,
                'phases': [phase.describe() for phase in self.phases],
                'adapters_required': list(self.adapters_required),
                'production_apply_supported': False, 'quiescence_proven': False}


def _layout(core, creation):
    if creation is None:
        check_layout(core/'data',core.parent/'bots')
        return
    from bot_new_deployment import NewDeploymentState
    from runtime_identity import _check_layout_fenced
    if type(creation) is not NewDeploymentState:
        raise ValueError('original live creation origin required')
    creation.check(core)
    if (core/'data'/GATE).exists():
        raise ValueError('existing transaction fence; no new creation plan')
    _check_layout_fenced(core/'data',core.parent/'bots')


def project_identity(core, old, new, *, context_witnesses=None, binding_backup=False, plugin_configs=False, account_history=False, recovery_stores=False, _creation=None):
    """Derive fixed legal intermediate metadata, never adopt caller phase JSON."""
    if type(recovery_stores) is not bool or recovery_stores and not (plugin_configs is True and account_history is True):
        raise ValueError('finite recovery adapter requires explicit plugin/account scopes')
    if type(account_history) is not bool:
        raise ValueError('explicit account history scope required')
    if type(plugin_configs) is not bool:
        raise ValueError('explicit plugin config scope required')
    if type(binding_backup) is not bool:
        raise ValueError('explicit binding backup scope required')
    context_witnesses = witnesses(context_witnesses)
    old, new = bot_id(old), bot_id(new)
    if old.casefold() == new.casefold() or old.startswith('.') or new.startswith('.'):
        raise ValueError('distinct visible identity names required')
    core = Path(core).absolute()
    root, base = core.parent, core / 'data'
    safe_path(root, core)
    if core.name != 'core':
        raise ValueError('canonical deployment core required')
    _layout(core, _creation)
    pins, files = {}, {}

    def inspect(relative, *, required=False, directory=False, control=False):
        pin, raw = _pin(root, relative, read=control or required and not directory)
        if required and pin[0] != ('directory' if directory else 'file'):
            raise ValueError('fixed identity input missing')
        pins[relative] = pin
        if raw is not None:
            files[relative] = raw
        return raw

    def absent(relative):
        inspect(relative)
        if pins[relative] != ('absent',):
            raise ValueError('destination identity already exists')

    absent('core/data/' + GATE)
    rows = _json(inspect('core/data/' + LEDGER, required=True))
    owner = _json(inspect('core/data/bot-runtime-owner.json', required=True))
    if (set(rows) != {'version', 'bindings'} or type(rows['version']) is not int
            or rows['version'] != 1 or set(owner) != {'baseline'}):
        raise ValueError('unsupported identity metadata shape')
    baseline = bot_id(owner['baseline'])
    if old not in rows['bindings'] or new.casefold() in {n.casefold() for n in rows['bindings']}:
        raise ValueError('source unbound or destination reserved')
    inspect('core/data/' + RETIRED, control=True)
    names = retired_names(base)
    if new.casefold() in {n.casefold() for n in names}:
        raise ValueError('destination name retired')
    profiles = [p.name for p in (root / 'bots').iterdir()]
    if old not in profiles or any(n.casefold() == new.casefold() for n in profiles):
        raise ValueError('source case or destination profile conflict')
    inspect('bots/' + old, required=True, directory=True)
    absent('bots/' + new)
    marker_path = 'bots/' + old + '/' + MARKER
    marker = _json(inspect(marker_path, required=True))
    if marker != {'name': old, 'token': rows['bindings'][old]}:
        raise ValueError('source binding mismatch')
    runtime = 'core/data' if baseline == old else 'core/data/bot-runtimes/' + old
    destination = runtime if baseline == old else 'core/data/bot-runtimes/' + new
    inspect(runtime, required=True, directory=True)
    if destination != runtime:
        absent(destination)
    # A different-case runtime path must not become a distinct destination on POSIX.
    folder = safe_path(root, base / 'bot-runtimes')
    if folder.is_dir() and any(p.name.casefold() == new.casefold() for p in folder.iterdir()):
        raise ValueError('destination runtime alias exists')
    legacy = inspect('core/data/active-bot.json', control=True)
    if legacy is not None:
        selected = _json(legacy)
        if (set(selected) != {'baseline', 'active'} or selected['baseline'] != baseline
                or selected['active'] not in rows['bindings']):
            raise ValueError('legacy identity selection requires adapter')
    adapters = []
    # Existence pins only. Do not open account/plugin bodies or backup payloads.
    for relative in ('core/extensions', 'bots/' + old + '/infinity',
                     'core/data/infinity-processes',
                     BINDING_BACKUP, runtime + '/provider-state',
                     'bots/' + old + '/provider-state') + tuple(sorted(ACCOUNT_NAMES)):
        if recovery_stores and relative == 'core/data/infinity-processes':
            continue
        if account_history and relative in ACCOUNT_NAMES:
            continue
        if plugin_configs and relative in ('core/extensions','bots/' + old + '/infinity'):
            continue
        if relative == BINDING_BACKUP and binding_backup:
            continue
        if relative == runtime + '/provider-state' and context_witnesses is not None:
            continue
        path = safe_path(root, root / relative)
        if path.exists():
            info = path.lstat()
            pins[relative] = ('adapter', info.st_dev, info.st_ino)
            adapters.append(relative)
        else:
            pins[relative] = ('absent',)
    context_controls, opaque = capture_contexts(root, runtime, destination, context_witnesses)
    for relative, raw in context_controls.items():
        if inspect(relative, required=True) != raw:
            raise ValueError('context metadata changed during capture')
    pins.update(opaque)
    context_moves = context_operations(root, runtime, destination, files, context_witnesses, opaque)
    if binding_backup:
        backup = inspect(BINDING_BACKUP, control=True)
        if backup is not None:
            _validate_binding_backup(backup, rows['bindings'], new)
    namespaces = {}
    if plugin_configs:
        plugin_controls, namespaces = capture_plugins(root, rows['bindings'], baseline, recovery=recovery_stores)
        for relative, raw in plugin_controls.items():
            if inspect(relative, required=True) != raw:
                raise ValueError('QQ config changed during capture')
        pins.update(namespaces)
    plugin_changes = derive_plugins(files, rows['bindings'], baseline, old, new, namespaces, recovery=recovery_stores)
    if account_history:
        account_controls, account_namespaces = capture_accounts(root, rows['bindings'], baseline)
        for relative, raw in account_controls.items():
            if inspect(relative, required=True) != raw:
                raise ValueError('account history changed during capture')
        namespaces.update(account_namespaces)
        pins.update(account_namespaces)
    if recovery_stores:
        recovery_controls, recovery_namespaces, recovery_payloads = capture_recovery(root, rows['bindings'], baseline)
        for relative, raw in recovery_controls.items():
            if inspect(relative, required=True) != raw:
                raise ValueError('recovery lifecycle metadata changed during capture')
        namespaces.update(recovery_namespaces)
        pins.update(recovery_namespaces)
        opaque.update(recovery_payloads)
        pins.update(recovery_payloads)
    instance_paths = _instance_paths(root)
    if instance_paths is None:
        pins[INSTANCE_ROOT] = ('absent',)
    else:
        directory = _pin(root, INSTANCE_ROOT, read=False)[0]
        pins[INSTANCE_ROOT] = ('entries', directory[1], directory[2], instance_paths)
        for relative in instance_paths:
            inspect(relative, required=True)
        _validate_instance_controls(files, rows['bindings'])
    if sum(map(len, files.values())) > CONTROL_BYTES:
        raise ValueError('aggregate identity metadata size limit')
    roots = {'bots/' + old, runtime}
    if instance_paths is not None:
        roots.add(INSTANCE_ROOT)
    phases = [IdentityPhase(tuple(sorted(roots)), tuple(sorted(files.items())), tuple(sorted(opaque.items())), tuple(sorted(namespaces.items())))]
    operations = []

    def move(source, target):
        operations.append(IdentityOperation('move', source, target))
        for key in list(files):
            if key == source or key.startswith(source + '/'):
                files[target + key[len(source):]] = files.pop(key)
        for key in list(opaque):
            if key == source or key.startswith(source + '/'):
                opaque[target + key[len(source):]] = opaque.pop(key)
        for key in list(namespaces):
            if key == source or key.startswith(source + '/'):
                namespaces[target + key[len(source):]] = namespaces.pop(key)
        roots.remove(source)
        roots.add(target)
        phases.append(IdentityPhase(tuple(sorted(roots)), tuple(sorted(files.items())), tuple(sorted(opaque.items())), tuple(sorted(namespaces.items()))))

    def write(relative, value):
        raw = _encoded(value)
        operations.append(IdentityOperation('write', relative, before=files.get(relative), after=raw))
        files[relative] = raw
        phases.append(IdentityPhase(tuple(sorted(roots)), tuple(sorted(files.items())), tuple(sorted(opaque.items())), tuple(sorted(namespaces.items()))))

    move('bots/' + old, 'bots/' + new)
    if runtime != destination:
        move(runtime, destination)
    for source, target in context_moves:
        operations.append(IdentityOperation('rekey', source, target))
        files[target] = files.pop(source)
        phases.append(IdentityPhase(tuple(sorted(roots)), tuple(sorted(files.items())), tuple(sorted(opaque.items())), tuple(sorted(namespaces.items()))))
    old_instance = INSTANCE_ROOT + '/' + old + '.json' 
    if old_instance in files:
        new_instance = INSTANCE_ROOT + '/' + new + '.json'
        operations.append(IdentityOperation('rekey', old_instance, new_instance))
        files[new_instance] = files.pop(old_instance)
        phases.append(IdentityPhase(tuple(sorted(roots)), tuple(sorted(files.items())), tuple(sorted(opaque.items())), tuple(sorted(namespaces.items()))))
    for relative, before, after in plugin_changes:
        operations.append(IdentityOperation('write', relative, before=before, after=after))
        files[relative] = after
        phases.append(IdentityPhase(tuple(sorted(roots)), tuple(sorted(files.items())), tuple(sorted(opaque.items())), tuple(sorted(namespaces.items()))))
    if baseline == old:
        write('core/data/bot-runtime-owner.json', {'baseline': new})
    if legacy is not None and old in selected.values():
        write('core/data/active-bot.json', {k: new if v == old else v for k, v in selected.items()})
    updated = dict(rows['bindings'])
    updated[new] = updated.pop(old)
    write('core/data/' + LEDGER, {'version': 1, 'bindings': updated})
    write('bots/' + new + '/' + MARKER, {'name': new, 'token': marker['token']})
    write('core/data/' + RETIRED, {'version': 1, 'names': sorted(names | {old})})
    projection = IdentityProjection(str(core), old, new, tuple(operations), tuple(phases),
                                    tuple(adapters), tuple(sorted(pins.items())), context_witnesses, plugin_configs, account_history, recovery_stores, _creation)
    # Revalidate captured controls; adapter content is deliberately not read here.
    for relative, expected in projection.pins:
        actual = _observe_pin(root, relative, expected)
        if actual != expected:
            raise ValueError('projection inputs changed while planning')
    return projection
