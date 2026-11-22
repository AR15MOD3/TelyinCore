"""One-shot curated isolation acceptance; never deployment/apply or a scheduler."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from tools.bot_source_snapshot import source_hashes

CORE = Path(__file__).resolve().parents[1]
EXPECTED_CASES = None
GROUPS = {
    'entry': '''bot_account_credential_revision bot_known_account_configuration bot_parent_grant_retirement bot_snow_restart bot_prelaunch_account_configuration bot_initial_account_configuration bot_parent_work_finish bot_parent_qq_launch bot_parent_account_work bot_parent_account_grant bot_default_account_fence bot_companion_custody bot_launcher_companion bot_independent_owner bot_recovery_http_lease bot_login_lifecycle bot_launcher_host bot_birth_process bot_launch_job_gate bot_private_qq_config bot_private_recovery_backups bot_qq_account_drift bot_snow_online_native bot_model_stream_close bot_local_model_owner bot_model_work bot_tool_work bot_plugin_assembly_stop bot_plugin_root_stop bot_console_parent bot_configured_account bot_programmatic_web_parent bot_recovery_cross_store bot_recovery_mutation_journal bot_recovery_native_target bot_recovery_launch_retention bot_qq_worker_retirement bot_direct_web_parent bot_default_instance_parent bot_owned_instance bot_launch_confined_runtime bot_launcher_parent_handoff bot_recovery_runtime_snapshot bot_default_snow_trust bot_default_snow_owner bot_default_recovery_store bot_default_config_cas bot_parent_multi_capability bot_snow_parent_process bot_recovery_singleflight bot_snow_native_retirement bot_launcher_package bot_snow_terminal_close bot_default_private_shutdown bot_private_shutdown bot_startup_default_stop bot_startup_owned_watch bot_startup_owned_probe bot_builtin_runtime_guard bot_recovery_settings_entry bot_release_archive bot_bootstrap_exclusivity bot_account_pending bot_bounded_supervision bot_parent_tick bot_fixed_contract bot_parent_launch bot_tts_environment bot_bridge_scope bot_plugin_host_fixture bot_plugin_confinement bot_entrypoint_audit bot_bootstrap_failure bot_broker_revocation bot_revocation_journal bot_client_exclusivity bot_guarded_store bot_recovery_store snow_recovery_boundaries bot_source_snapshot bot_lifecycle_acceptance bot_local_capability bot_controller_bootstrap bot_account_change bot_lifecycle_entry bot_channel_transport bot_lifecycle_channel bot_host_registry bot_startup_hooks''',
    'related': '''bot_new_deployment bot_rename_opaque_lease bot_rename_coordinator bot_rename_recovery bot_rename_accounts bot_rename_plugins bot_rename_binding_backup bot_rename_contexts bot_rename_instances bot_rename_transaction bot_rename_projection qq_text_plugin qq_media_order qq_recall plugin_hot_assembly plugin_lifecycle_f05 bot_host_inventory bot_joint_offline bot_plugin_ownership bot_adapter_fixture
        bot_host_observer bot_protocol_reindex bot_offline_lease bot_recovery_fixture
        bot_migration_gate bot_rename_preflight runtime_identity_guard startup_bots bot_registry
        bot_bootstrap bot_profile_docs startup_console console_output startup_menu_redraw memory
        provider_origin_replay provider_record_replay qq_protocol_health snow_shared_management
        snow_management_trust snow_management_contract snowluma_connection qq_connection_check''',
    'phase': 'bot_phase_fixture',
    'controls': '''snowluma_recovery qq_control snow_state_lifecycle snow_ws_evidence snowluma_import
        iris_snow_isolation_candidate plugin_signals qq_route_first reaction_window
        voice_corpus tts_install tts_voice_presets''',
}
SOURCES = ['start.ps1','start.bat','scripts/ensure_runtime.py','launch.py','startup.py','infinity_control.py','bot_lifecycle_entry.py',
    'bot_lifecycle_channel.py','bot_channel_transport.py','bot_host_registry.py','bot_local_capability.py',
    'bot_host_inventory.py','bot_joint_offline.py','README.md',
    'src/web/client/infinity-page.ts','tools/bot_lifecycle_acceptance.py',
    'tools/bot_lifecycle_entry_fixture.py','tools/bot_channel_deadline_fixture.py']
SOURCES += ['tools/bot_account_change_fixture.py','bot_controller_bootstrap.py',
            'tools/bot_controller_bootstrap_fixture.py','tools/bot_recovery_store_fixture.py',
            'tools/bot_default_shutdown_fixture.py','tools/bot_launch_confined_fixture.py','tools/bot_default_instance_fixture.py']
# File-loaded plugin entrypoints do not appear in ordinary import statements.
SOURCES += ['builtin/infinity/core-infinity-qq/index.py',
            'pyproject.toml']
SOURCES += sorted(p.relative_to(CORE).as_posix() for p in (CORE/'templates/bot-default').rglob('*') if p.is_file())
SOURCES += sorted({f'tests/test_{name}.py' for names in GROUPS.values() for name in names.split()})


def hashes():
    return source_hashes(CORE, SOURCES)


def expected_identities(value):
    if (not isinstance(value, dict) or set(value) != set(GROUPS)):
        raise ValueError('independent expected test identities required for every group')
    result = {}; all_ids = set()
    for group, rows in value.items():
        if not isinstance(rows, (list, tuple)) or not rows:
            raise ValueError('empty expected group')
        identities = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 2 or any(type(v) is not str or not v for v in row):
                raise ValueError('invalid expected testcase identity')
            key = tuple(row)
            if key in all_ids: raise ValueError('duplicate expected testcase identity')
            all_ids.add(key); identities.append(key)
        result[group] = frozenset(identities)
    return result


def run(destination, *, expected_cases=None):
    expected = expected_identities(EXPECTED_CASES if expected_cases is None else expected_cases)
    destination = Path(destination).absolute()
    destination.mkdir(parents=True, exist_ok=False)  # refuse overwriting prior evidence
    try:
        before = hashes()
    except Exception as error:
        (destination/'source-error.json').write_text(json.dumps(
            {'phase':'before','error':type(error).__name__}),encoding='utf-8')
        raise
    (destination/'source-before.json').write_text(json.dumps(before,indent=2),encoding='utf-8')
    (destination/'expected-cases.json').write_text(json.dumps({k:sorted(v) for k,v in expected.items()},indent=2),encoding='utf-8')
    def batch(item):
        group, names = item
        # Fresh owned short root: --basetemp avoids pytest's global retention
        # pruning of unrelated conversations' older temporary evidence.
        test_root = str(Path(tempfile.mkdtemp(prefix='blc-' + group + '-')).resolve())
        command = [sys.executable,'-m','pytest',*[f'tests/test_{n}.py' for n in names.split()],
                   '-q',f'--basetemp={test_root}',f'--junitxml={destination/group}.xml']
        with (destination/f'{group}.log').open('wb') as log:
            try:
                completed = subprocess.run(command,cwd=CORE,stdout=log,stderr=subprocess.STDOUT,timeout=900)
                return {'group':group,'returncode':completed.returncode,'command':command,'test_root':test_root}
            except subprocess.TimeoutExpired:
                return {'group':group,'returncode':-1,'timeout':True,'command':command,'test_root':test_root}
    with ThreadPoolExecutor(max_workers=2) as executor:
        runs = list(executor.map(batch,GROUPS.items()))
    snapshot_error = None
    try:
        after = hashes()
    except Exception as error:
        after = None
        snapshot_error = type(error).__name__
    aggregate = ET.Element('testsuites'); seen = set(); failed = []; errors = []; skipped = []
    for result in runs:
        try:
            root = ET.parse(destination/f"{result['group']}.xml").getroot()
            suites = [root] if root.tag == 'testsuite' else list(root)
            if (root.tag not in ('testsuite','testsuites') or not suites
                    or any(s.tag != 'testsuite' for s in suites)):
                raise ValueError('invalid test suite evidence')
            cases = list(root.iter('testcase'))
            if not cases or len(cases) != sum(len(s.findall('testcase')) for s in suites):
                raise ValueError('empty test evidence')
            actual_ids = [(case.get('classname'),case.get('name')) for case in cases]
            if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != expected[result['group']]:
                raise ValueError('missing, unexpected or duplicate testcase evidence')
            for suite in suites:
                direct = list(suite.findall('testcase'))
                counts = dict(tests=len(direct), failures=sum(c.find('failure') is not None for c in direct),
                              errors=sum(c.find('error') is not None for c in direct),
                              skipped=sum(c.find('skipped') is not None for c in direct))
                if any(suite.get(k) != str(v) for k,v in counts.items()):
                    raise ValueError('inconsistent suite counters')
            for case in cases:
                key = (case.get('classname'),case.get('name'))
                if not all(type(v) is str and v for v in key):
                    raise ValueError('missing test identity')
                if key in seen: raise ValueError('duplicate test identity')
                seen.add(key)
                if case.find('failure') is not None: failed.append(key)
                if case.find('error') is not None: errors.append(key)
                if case.find('skipped') is not None: skipped.append(key)
            for suite in ([root] if root.tag == 'testsuite' else list(root)): aggregate.append(suite)
        except Exception as error:
            result['evidence_error'] = type(error).__name__
    all_passed = (before == after and all(r['returncode'] == 0 and 'evidence_error' not in r for r in runs)
                  and not failed and not errors and not skipped)
    aggregate.set('tests',str(len(seen)))
    ET.ElementTree(aggregate).write(destination/'all-tests.xml',encoding='utf-8',xml_declaration=True)
    manifest = {'all_curated_tests_passed':all_passed,'unique_tests':len(seen),
        'failures':failed,'errors':errors,'skipped':skipped,'source_hash_guard':before == after,'runs':runs,
        'scope':'curated isolation suites, not full repository or real deployment acceptance',
        'source_snapshot_error':snapshot_error,
        'fingerprint_scope':'bounded static local imports plus explicit file-loaded seeds; not runtime/host coverage',
        'production_apply_supported':False,'production_enabled':False,
        'remaining_production_blockers':['complete host/legacy entrypoint coverage',
            'SnowLuma/account lifecycle mediation','trusted empty-host bootstrap',
            'phase-aware production projection and durability/ACL/release acceptance']}
    (destination/'source-after.json').write_text(json.dumps(after,indent=2),encoding='utf-8')
    (destination/'verification.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,help='NEW evidence directory; existing path is rejected')
    parser.add_argument('--expected-cases',required=True,help='Independent approved group -> [classname,name] manifest')
    parser.add_argument('--expected-sha256',required=True)
    args = parser.parse_args()
    raw = Path(args.expected_cases).read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.expected_sha256:
        raise ValueError('expected testcase manifest changed')
    result = run(args.output, expected_cases=json.loads(raw))
    print(json.dumps({k:result[k] for k in ['all_curated_tests_passed','unique_tests','source_hash_guard','production_enabled']}))
    sys.exit(0 if result['all_curated_tests_passed'] else 1)
