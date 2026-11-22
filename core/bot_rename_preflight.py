"""Read-only rename planning. No production apply, process probe or data rewrite."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from bot_registry import BotRegistry, bot_id
from runtime_identity import LEDGER, MARKER, bindings, check_layout, safe_path

from bot_migration_gate import GATE, RETIRED, retired_names


def control(path: Path) -> dict:
    """Only callers' explicit identity manifests; bounded reads, no config/history."""
    with path.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('identity manifest exceeds size limit')
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError('identity manifest must be an object')
    return data


def plan_rename(core: Path, old: str, new: str) -> dict:
    old, new = bot_id(old), bot_id(new)
    core = Path(core).absolute()
    safe_path(core.parent, core)
    base, parent = core / 'data', core.parent / 'bots'
    safe_path(core.parent, parent)
    safe_path(core, base)
    blockers = []
    effects = []
    if safe_path(base, base / GATE).exists():
        blockers.append('persistent_migration_gate_present')
    if old.casefold() == new.casefold():
        blockers.append('same_or_case_only_name_requires_separate_design')
    if old.startswith('.') or new.startswith('.'):
        blockers.append('hidden_identity_not_supported_by_prototype')
    try:
        check_layout(base, parent)
    except (ValueError, OSError):
        blockers.append('existing_layout_requires_repair')
    book = BotRegistry(parent, core / 'templates/bot-default', old, base)
    source = book.folder(old)
    if not book.valid(source):
        blockers.append('source_missing_or_invalid')
    actual = [p.name for p in parent.iterdir()] if parent.is_dir() else []
    if old not in actual:
        blockers.append('source_case_mismatch')
    if any(name.casefold() == new.casefold() for name in actual):
        blockers.append('destination_folder_exists')
    owner_path = safe_path(base, base / 'bot-runtime-owner.json')
    try:
        owner = control(owner_path)
        baseline = bot_id(owner.get('baseline'))
        rows = bindings(base)
    except (OSError, ValueError, TypeError):
        baseline, rows = '', {}
        blockers.append('owner_or_bindings_invalid')
    if old not in rows:
        blockers.append('source_requires_verified_binding')
    if new.casefold() in {n.casefold() for n in rows}:
        blockers.append('destination_identity_reserved')
    retired_path = safe_path(base, base / RETIRED)
    names = retired_names(base)
    if new.casefold() in {n.casefold() for n in names}:
        blockers.append('destination_name_retired')
    legacy_path = safe_path(base, base / 'active-bot.json')
    if legacy_path.exists():
        legacy = control(legacy_path)
        if legacy.get('baseline') != baseline or legacy.get('active', baseline) != old:
            blockers.append('legacy_selection_requires_explicit_review')
    runtime = base if baseline == old else base / 'bot-runtimes' / old
    destination = base if baseline == old else base / 'bot-runtimes' / new
    safe_path(base, runtime); safe_path(base, destination)
    if baseline != old and not runtime.is_dir():
        blockers.append('source_runtime_missing')
    runtime_parent = safe_path(base, base / 'bot-runtimes')
    if runtime_parent.exists() and any(p.name.casefold() == new.casefold()
                                     for p in runtime_parent.iterdir()):
        blockers.append('destination_runtime_exists')
    # A PID/port file is neither proof of life nor proof that it is safe to move.
    instances = safe_path(base, base / 'instances')
    if instances.exists() and any(instances.iterdir()):
        blockers.append('instance_records_require_offline_clearance')
    external = [core / 'extensions', source / 'infinity', base / 'infinity-processes',
                base / 'sharing-preview-test.json']
    for path in external:
        safe_path(core.parent, path)
        if path.exists():
            blockers.append('plugin_account_or_process_ownership_not_supported')
            break
    # Never fabricate context IDs or regex-replace hashed replay indexes.
    for path in (runtime / 'provider-state', source / 'provider-state'):
        safe_path(core.parent, path)
        if path.exists():
            blockers.append('provider_context_adapter_required')
    known_runtime_files = {'session-main.jsonl', 'synthetic-history.bin',
                           '.telyincore-instance.lock'}
    if runtime.is_dir():
        shared = {'bot-runtime-owner.json', LEDGER, LEDGER + '.bak', RETIRED,
                  'active-bot.json', '.bot-identity.lock', 'bot-runtimes',
                  'instances', 'startup-port-allocation'} if baseline == old else set()
        for path in runtime.iterdir():
            safe_path(core.parent, path)
            if path.name not in known_runtime_files | shared:
                blockers.append('unclassified_runtime_resource_requires_adapter')
                break
    def rel(path):
        return path.relative_to(core.parent).as_posix()
    effects.append({'kind': 'move_profile', 'from': rel(source), 'to': rel(parent / new)})
    if baseline == old:
        effects.append({'kind': 'keep_shared_runtime_in_place', 'path': rel(base)})
        effects.append({'kind': 'update_field', 'path': rel(owner_path), 'field': 'baseline'})
    else:
        effects.append({'kind': 'move_runtime', 'from': rel(runtime), 'to': rel(destination)})
    effects += [
        {'kind': 'rekey_binding', 'path': rel(base / LEDGER)},
        {'kind': 'update_field', 'path': rel(parent / new / MARKER), 'field': 'name'},
        {'kind': 'reserve_old_name', 'path': rel(retired_path)},
        {'kind': 'preserve_display_name_and_history_bytes'}]
    if legacy_path.exists():
        effects.append({'kind': 'update_exact_identity_fields', 'path': rel(legacy_path),
                        'fields': ['baseline', 'active']})
    return {'version': 1, 'old': old, 'new': new, 'baseline': baseline,
            'core': str(core), 'runtime_from': rel(runtime), 'runtime_to': rel(destination),
            'blockers': sorted(set(blockers)), 'effects': effects,
            'fixture_eligible': not blockers, 'production_apply_supported': False,
            'production_gaps': ['all_instance_quiescence_proof', 'verified_gate_lifecycle_coordinator',
                                'provider_plugin_and_account_adapters',
                                'cross_process_crash_recovery_entry']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', required=True, type=Path)
    parser.add_argument('--old', required=True)
    parser.add_argument('--new', required=True)
    args = parser.parse_args(argv)
    result = plan_rename(args.core, args.old, args.new)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['fixture_eligible'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
