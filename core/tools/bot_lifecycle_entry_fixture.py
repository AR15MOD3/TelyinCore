"""Actual QQControl parent process start/stop/close paths, exclusively generated test realms."""
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile

from bot_host_registry import HostEvidenceRegistry
from bot_parent_launch import FixedProcessFactory
from bot_lifecycle_channel import LifecycleBroker, LifecycleClient, private_channel


class RecoveryStub:
    def cancel(self): pass


def fixed_plan(core, *, snow=False):
    env = {'BOT_FIXTURE_KIND': 'owned-sleeper'}
    if snow: env['SNOWLUMA_HOOK_AUTOLOAD'] = '0'
    return dict(argv=[sys.executable, '-c', 'import time;time.sleep(60)'],
                cwd=core, env=env)


def worker(connection, key, session, core, done, release, operation):
    import infinity_control
    from infinity_control import QQControl
    from bot_lifecycle_entry import LifecycleProcessBridge
    core = Path(core)
    runtime = core/'test-runtime'
    py = runtime/'.venv/Scripts/python.exe'
    entry = core/'scripts/voice/cosyvoice3_server.py'
    dual_mode = operation in ('dual-snow-stop', 'dual-tts-stop')
    snow_mode = operation in ('snow-fixed-stop', 'dual-snow-stop')
    fixed_mode = dual_mode or operation in ('fixed-stop', 'snow-fixed-stop')
    slot = 'snowluma' if snow_mode else 'tts'
    env = dict(TELYIN_TTS_RUNTIME=str(runtime),
               TELYIN_TTS_MODEL_DIR=str(core/'models/voice/tts/test-model'))
    argv = [str(py), '-u', str(entry)]
    cwd = runtime
    if fixed_mode:
        plan = fixed_plan(core, snow=snow_mode)
        argv, cwd, env = plan['argv'], plan['cwd'], plan['env']
    client = LifecycleClient(connection, key, session, timeout=5)
    if dual_mode:
        bridge = LifecycleProcessBridge(core, None, client,
            **fixed_plan(core), fixed_contract=True,
            companion_plan=fixed_plan(core, snow=True))
    else:
        bridge = LifecycleProcessBridge(core, None, client,
            argv=argv, cwd=cwd, env=env, fixed_contract=fixed_mode, slot=slot)
    control = QQControl(core, tts_runtime=runtime, process_lifecycle=bridge, recovery=RecoveryStub())
    # Only fixture preflight inputs are stubbed; action/_start/close are real code.
    control._config = lambda: {'ttsEnabled': True, 'ttsModel': 'test-model', 'ttsVoice': 'default'}
    control._models = lambda: ([], ['test-model'])
    control._voices = lambda: [{'id': 'default'}]
    control.read = lambda: {'fixture_only': True}
    infinity_control._open_port = lambda port: False
    try:
        if snow_mode or dual_mode:
            control._config = lambda: (_ for _ in ()).throw(AssertionError('legacy config read'))
            assert control.action(slot+'_start') == {'fixture_only': True}
        elif fixed_mode:
            control._start('tts', argv, cwd, env)
        else:
            assert control.action('tts_start') == {'fixture_only': True}
        assert control._running(slot)
        if operation in ('stop', 'fixed-stop', 'snow-fixed-stop') or dual_mode: control.action(slot+'_stop')
        else: control.close()
        assert not control._owned
        done.set()
        if not release.wait(timeout=10): raise ValueError('fixture parent unavailable')
    finally:
        client.close()


def demo(operation='stop'):
    if operation not in ('stop', 'close', 'fixed-stop', 'snow-fixed-stop', 'dual-snow-stop', 'dual-tts-stop'): raise ValueError('invalid fixture operation')
    dual_mode = operation in ('dual-snow-stop', 'dual-tts-stop')
    fixed_mode = dual_mode or operation in ('fixed-stop', 'snow-fixed-stop')
    slot = 'snowluma' if operation in ('snow-fixed-stop', 'dual-snow-stop') else 'tts'
    home = Path(tempfile.mkdtemp(prefix='bot-entry-demo-')).resolve()
    core = home/'core'; (core/'data').mkdir(parents=True)
    for rel in ['test-runtime/.venv/Scripts/python.exe',
                'test-runtime/repo/asset/zero_shot_prompt.wav',
                'scripts/voice/cosyvoice3_server.py']:
        path = core/rel; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic fixture, never executed')
    registry = HostEvidenceRegistry(core, ['Fixture'], 'Fixture')
    registry.bootstrap_empty(lambda _: dict(core=str(core), bots=['Fixture'], baseline='Fixture',
        complete=True, processes=[], entrypoints_guarded=True))
    context = multiprocessing.get_context('spawn')
    parent, child, key, session = private_channel(context)
    done, release = context.Event(), context.Event()
    controller = context.Process(target=worker, args=(child,key,session,str(core),done,release,operation))
    broker = None; leaves = []; started = False; fixed = None; factories = {}
    try:
        with registry.start_window('Fixture') as birth:
            controller.start(); started = True; birth.commit(controller.pid)
        child.close()
        factories = {kind: FixedProcessFactory(**fixed_plan(core, snow=kind == 'snowluma'))
                     for kind in (('tts', 'snowluma') if dual_mode else (slot,))}
        fixed = factories[slot]
        def factory():
            # Trusted parent allowlist creates ONLY this short-lived test sleeper.
            leaf = fixed()
            leaves.append(leaf); return leaf
        broker = LifecycleBroker(registry,'Fixture',registry._alive(controller.pid),
                                 parent,key,session,factories if dual_mode else {slot:fixed if fixed_mode else factory},
                                 timeout=5, fixed_only=fixed_mode)
        for _ in range(40):
            broker.serve_once()
            if broker.retired_slots == {slot}: break
        else: raise AssertionError('fixture lifecycle did not retire')
        assert done.wait(timeout=5)
        if fixed_mode:
            leaves.append(fixed.owned_process)
        release.set();controller.join(timeout=10)
        assert controller.exitcode == 0 and len(leaves) == 1
        leaves[0].wait(timeout=5)
        broker.close();registry.retire(controller.pid)
        assert registry.inspect()['registered_processes'] == 0
        return {'fixture_only':True,'operation':operation,'actual_qqcontrol_action':True,
                'parent_owned_children_created':1,'controller_exitcode':0,
                'native_retirement_confirmed':True,'coverage_revoked':True,
                'real_tts_or_bot_started':False,'real_snowluma_started':False,
                'process_slot':slot,'configured_process_slots':list(factories),
                'production_enabled':False}
    finally:
        release.set()
        if broker is not None: broker.close()
        else: parent.close()
        child.close()
        for retained in factories.values():
            if retained.owned_process is not None and retained.owned_process not in leaves:
                leaves.append(retained.owned_process)
        for leaf in leaves:
            if leaf.poll() is None: leaf.terminate()
            leaf.wait(timeout=10)
        if started:
            controller.join(timeout=10)
            if controller.is_alive():controller.terminate();controller.join(timeout=10)
            controller.close()


if __name__ == '__main__':
    print(json.dumps([demo('stop'),demo('close')]))
