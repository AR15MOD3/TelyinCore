"""Private-channel demo in a NEW temporary root, only self-created test processes."""
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

from bot_host_registry import HostEvidenceRegistry
from bot_lifecycle_channel import LifecycleBroker, LifecycleClient, private_channel


def read_ready(leaf, *, timeout=5.0):
    finished = threading.Event(); result = []
    def read():
        try: result.append(leaf.stdout.readline())
        except BaseException as error: result.append(error)
        finally: finished.set()
    reader = threading.Thread(target=read, name='owned-fixture-ready', daemon=True)
    leaf._ready_reader = reader
    reader.start()
    if not finished.wait(timeout):
        raise TimeoutError('owned test leaf readiness deadline; reader still owned')
    reader.join()
    if isinstance(result[0], BaseException): raise result[0]
    if result[0].strip() != 'ready': raise ValueError('test leaf unavailable')


def close_leaf(leaf):
    if leaf.poll() is None:
        try: leaf.communicate('exit\n', timeout=10)
        except subprocess.TimeoutExpired:
            leaf.kill()  # only a synthetic process directly created by this fixture
            leaf.communicate(timeout=5)
    reader = getattr(leaf, '_ready_reader', None)
    if reader is not None:
        reader.join(timeout=1)
        if reader.is_alive(): raise RuntimeError('owned readiness reader did not retire')


def worker(connection, key, session):
    client = LifecycleClient(connection, key, session, timeout=5)
    try:
        client.request('ping')
        client.request('start', 'test-leaf')
        client.request('retire', 'test-leaf')
    finally:
        client.close()


def disconnect_worker(connection, release):
    connection.close()
    release.wait(timeout=10)  # remain alive so the parent observes EOF, not exit


def disconnect_demo():
    home = Path(tempfile.mkdtemp(prefix='bot-lifecycle-disconnect-')).resolve()
    core = home / 'core'
    (core / 'data').mkdir(parents=True)
    registry = HostEvidenceRegistry(core, ['Fixture'], 'Fixture')
    registry.bootstrap_empty(lambda _: dict(core=str(core), bots=['Fixture'],
        baseline='Fixture', complete=True, processes=[], entrypoints_guarded=True))
    context = multiprocessing.get_context('spawn')
    parent, child, key, session = private_channel(context)
    release = context.Event()
    controller = context.Process(target=disconnect_worker, args=(child, release))
    broker = None
    started = False
    try:
        with registry.start_window('Fixture') as birth:
            controller.start(); started = True
            birth.commit(controller.pid)
        child.close()
        broker = LifecycleBroker(registry, 'Fixture', registry._alive(controller.pid),
                                 parent, key, session, {}, timeout=5)
        try:
            broker.serve_once()
        except ValueError:
            pass
        else:
            raise AssertionError('unexpected EOF accepted')
        assert controller.is_alive()
        assert registry.inspect()['registered_processes'] == 1
        assert registry.inspect()['coverage_revoked']
        release.set(); controller.join(timeout=10)
        assert controller.exitcode == 0
        registry.retire(controller.pid)
        return {'fixture_only': True, 'native_controller_alive_at_eof': True,
                'coverage_revoked': True, 'row_retained_until_verified_exit': True,
                'production_connected': False, 'production_apply_supported': False}
    finally:
        release.set()
        if broker is not None: broker.close()
        else: parent.close()
        child.close()
        if started:
            controller.join(timeout=10)
            if controller.is_alive():
                controller.terminate(); controller.join(timeout=10)
            controller.close()


def demo():
    home = Path(tempfile.mkdtemp(prefix='bot-lifecycle-channel-')).resolve()
    core = home / 'core'
    (core / 'data').mkdir(parents=True)
    registry = HostEvidenceRegistry(core, ['Fixture'], 'Fixture')
    # This attestation covers ONLY this newly created synthetic test realm.
    registry.bootstrap_empty(lambda _: dict(core=str(core), bots=['Fixture'],
        baseline='Fixture', complete=True, processes=[], entrypoints_guarded=True))
    context = multiprocessing.get_context('spawn')
    parent, child, key, session = private_channel(context)
    controller = context.Process(target=worker, args=(child, key, session))
    leaves = []
    broker = None
    started = False
    primary = None
    try:
        with registry.start_window('Fixture') as birth:
            controller.start()
            started = True
            birth.commit(controller.pid)
        child.close()  # sole writer is now the spawned controller
        identity = registry._alive(controller.pid)
        def factory():
            leaf = subprocess.Popen([sys.executable, '-u', '-c',
                'print("ready",flush=True);input()'], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, text=True)
            leaves.append(leaf)
            read_ready(leaf)
            return leaf
        broker = LifecycleBroker(registry, 'Fixture', identity, parent, key, session,
                                 {'test-leaf': factory}, timeout=5)
        broker.serve_once()  # authenticated ping
        broker.serve_once()  # parent-owned spawn, not child PID adoption
        assert registry.inspect()['registered_processes'] == 2
        assert registry.inspect()['coverage_revoked']
        leaves[0].communicate('exit\n', timeout=10)
        broker.serve_once()  # independent native absence authorizes retirement
        controller.join(timeout=10)
        assert controller.exitcode == 0
        broker.close()  # disconnect never grants completeness
        registry.retire(controller.pid)
        assert registry.inspect()['registered_processes'] == 0
        return {'fixture_only': True, 'home': str(home), 'authenticated_requests': 3,
                'native_processes_created': 2, 'controller_exitcode': 0,
                'children_remaining': broker.inspect()['owned_children'],
                'coverage_revoked': registry.inspect()['coverage_revoked'],
                'real_bot_started': False, 'real_network_requests': False,
                'production_connected': False, 'production_apply_supported': False}
    except BaseException as error:
        primary = error
        if broker is not None:
            error.add_note('synthetic broker diagnostic: '+json.dumps(broker.last_failure))
        raise
    finally:
        failures = []
        def cleanup(action):
            try: action()
            except BaseException as error: failures.append(error)
        cleanup(broker.close if broker is not None else parent.close)
        cleanup(child.close)
        for leaf in leaves: cleanup(lambda leaf=leaf: close_leaf(leaf))
        def close_controller():
            if controller.is_alive():
                controller.terminate()  # only this fixture's own synthetic worker
                controller.join(timeout=10)
            controller.close()
        if started: cleanup(close_controller)
        if failures:
            if primary is not None:
                primary._fixture_cleanup_errors = tuple(failures)
                primary.add_note('fixture cleanup incomplete; all cleanup paths attempted')
            else:
                raise BaseExceptionGroup('fixture cleanup incomplete', failures)


if __name__ == '__main__':
    print(json.dumps(demo(), ensure_ascii=False))
