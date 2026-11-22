"""Offline candidate: compatibility gate, never a service/rollback command.

Requires a pre-existing exact fence and a trusted complete host observer. There
is deliberately no production observer or CLI. The commit callback is trusted
coordinator code, not an authorization supplied by JSON. Cooperative writers
only; no guarantee against malicious same-user edits or crash-atomic publication.
"""
from contextlib import ExitStack, contextmanager
import hashlib
from pathlib import Path

from bot_offline_lease import managed_offline_lease, read_control
from bot_guarded_store import GuardedStore
from bot_stable_file import read_stable
from runtime_identity import bindings, safe_path, _check_layout_fenced, MARKER, LEDGER
from telyincore.ingress_review import validate
from telyincore.source_guard import lease

REQUIRED = frozenset(('telyincore/ingress_delivery.py',
                      'telyincore/ingress_review.py', 'tools/ingress_review.py'))


def _snapshot(core, offline, pins):
    base, bots = offline.base, offline.bots
    offline.validate()
    if offline.stage != 'runtime_locks_held':
        raise ValueError('retained runtime locks required')
    _check_layout_fenced(base, bots)
    owner = read_control(base / 'bot-runtime-owner.json')
    baseline = owner.get('baseline')
    tokens = bindings(base)
    names = tuple(offline.names)
    if not names or set(tokens) != set(names) or baseline not in tokens:
        raise ValueError('unbound, omitted or unknown consumer')
    expected_roots = {base if n == baseline else base/'bot-runtimes'/n for n in names}
    if set(offline.roots) != expected_roots or offline.records:
        raise ValueError('unknown runtime or instance marker remains')
    files = {}

    def bind(path, required=True):
        safe_path(core.parent, path)
        if not path.exists() and not required:
            files[str(path)] = None
            return None
        raw, identity = read_stable(core.parent, path, 65536)
        files[str(path)] = (hashlib.sha256(raw).hexdigest(), identity)
        return raw

    bind(base/'bot-runtime-owner.json')
    bind(base/LEDGER)
    # Identity, runtime roots and journal snapshots stay bound until commit.
    for n in names:
        runtime = base if n == baseline else base/'bot-runtimes'/n
        if not runtime.is_dir():
            raise ValueError('missing consumer runtime')
        bind(bots/n/MARKER)
        root = safe_path(bots, bots/n/'memory/ingress-delivery')
        if not root.is_dir():
            raise ValueError('missing consumer journal scope')
        binding = hashlib.sha256(str((runtime/'events').resolve()).encode()).hexdigest()
        disk = GuardedStore(root, root/'pending.json')
        state = disk.read()
        validate(state, binding)
        if any(row['state'] == 'RESOLVED' for row in state['records'].values()):
            raise ValueError('old reader cannot interpret operator disposition')
        bind(root/'pending.json')
        backup = bind(root/'pending.json.bak', required=False)
        if backup is not None:
            from bot_guarded_store import _object
            old = _object(backup)
            validate(old, binding)
            if any(row['state'] == 'RESOLVED' for row in old['records'].values()):
                raise ValueError('backup still requires current reader')
        local = safe_path(bots, bots/n/'memory/local-observation-delivery')
        if local.is_dir():
            for filename in ('pending.json', 'pending.json.bak'):
                raw = bind(local/filename, required=filename == 'pending.json')
                if raw is not None:
                    from bot_guarded_store import _object
                    observed = _object(raw)
                    validate(observed, binding)
                    if observed['records']:
                        raise ValueError('old runtime cannot recover retained local observations')
    if type(pins) is not dict or not REQUIRED.issubset(pins):
        raise ValueError('complete source pins required')
    for rel, digest in pins.items():
        if (type(rel) is not str or Path(rel).is_absolute() or '..' in Path(rel).parts
                or type(digest) is not str or len(digest) != 64
                or any(c not in '0123456789abcdef' for c in digest)):
            raise ValueError('invalid source pin')
        raw = bind(safe_path(core, core/rel))
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('source changed')
    offline.validate()
    return (names, files)


class _Permit:
    def __init__(self, core, offline, pins):
        self.core, self.offline, self.pins = core, offline, dict(pins)
        self.snapshot = _snapshot(core, offline, self.pins)
        self.active = True
        self.attempted = False

    def commit(self, action):
        if not self.active or self.attempted or not callable(action):
            raise ValueError('single live coordinator action required')
        self.attempted = True  # Failure or cancellation never grants a retry.
        if _snapshot(self.core, self.offline, self.pins) != self.snapshot:
            raise ValueError('consumer, identity, journal or source changed')
        # Exact locks and fence remain held, including throughout this callback.
        return action()


@contextmanager
def compatible_reader_lease(core, transaction, *, pins, observer):
    core = Path(core).absolute()
    base, bots = core/'data', core.parent/'bots'
    # Do not create runtime/data paths when called on an unknown installation.
    if not base.is_dir() or not bots.is_dir():
        raise ValueError('existing complete layout required')
    with managed_offline_lease(base, bots, transaction, observer=observer) as offline:
        with ExitStack() as stack:
            for name in offline.names:
                root = safe_path(bots, bots/name/'memory/ingress-delivery')
                if not root.is_dir():
                    raise ValueError('missing journal scope')
                stack.enter_context(lease(root, timeout=0))
                local = safe_path(bots, bots/name/'memory/local-observation-delivery')
                if local.is_dir():
                    stack.enter_context(lease(local, timeout=0))
            permit = _Permit(core, offline, pins)
            try:
                yield permit
            finally:
                permit.active = False
