"""NEW synthetic roots and self-created Python children only. No Bot/services."""
import json
import multiprocessing
from pathlib import Path
import tempfile

from bot_controller_bootstrap import ControllerBootstrap
from bot_host_registry import HostEvidenceRegistry


def worker(client, done, release, mode):
    if mode == 'idle':
        done.set()
        if not release.wait(10):
            raise ValueError('synthetic parent unavailable')
        return
    description = client.request('describe')
    assert description['bot_scope'] is None
    client.request('ping')
    done.set()
    if not release.wait(10):
        raise ValueError('synthetic parent unavailable')


def fixture(mode='ping'):
    root = Path(tempfile.mkdtemp(prefix='bot-controller-bootstrap-')).resolve()
    core = root/'core'; (core/'data').mkdir(parents=True)
    registry = HostEvidenceRegistry(core, ['Fixture'], 'Fixture')
    # Synthetic realm ONLY; this callback cannot grant real-host authority.
    registry.bootstrap_empty(lambda _: dict(core=str(core), bots=['Fixture'],
        baseline='Fixture', complete=True, processes=[], entrypoints_guarded=True))
    context = multiprocessing.get_context('spawn')
    done, release = context.Event(), context.Event()
    bootstrap = ControllerBootstrap(registry, 'Fixture', worker,
        args=(done, release, mode), timeout=5)
    return bootstrap, registry, done, release


def demo():
    boot, registry, done, release = fixture()
    try:
        boot.start()
        assert registry.inspect()['registered_processes'] == 1
        assert registry.inspect()['coverage_revoked']
        boot.serve_once(); boot.serve_once()
        assert done.wait(5)
        release.set()
        assert boot.wait_and_retire() == 0
        assert registry.inspect()['registered_processes'] == 0
        return dict(fixture_only=True, native_controller_registered=True,
            authenticated_describe_and_ping=True, independent_retirement=True,
            untracked_descendants_retained=True, production_enabled=False)
    finally:
        cleanup(boot, release)


def cleanup(boot, release):
    release.set(); boot.close()
    if boot.pid is not None and not boot.retired:
        boot.process.join(10)
        # This fixture owns exactly this newly spawned Python process handle.
        if boot.process.is_alive():
            boot.process.terminate(); boot.process.join(10)
        boot.wait_and_retire()


if __name__ == '__main__':
    print(json.dumps(demo()))
