"""Joint pre-apply offline coordination; no production inventory or executor.

A HOST-owned callable supplies one atomic plugin/process inventory bundle. Client
JSON, two unrelated empty lists and matching generation strings are not proof.
No gate installation/removal, process termination or migration is performed here.
"""
import copy
from contextlib import contextmanager
from pathlib import Path
import json
import re

from bot_host_observer import HostObserver, probe_process
from bot_migration_gate import GATE
from bot_offline_lease import managed_offline_lease
from bot_plugin_ownership import PluginOwnershipObserver
from runtime_identity import safe_path


def _control(root, path):
    path = safe_path(root, path)
    with path.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('joint control manifest size limit')
    def unique(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError('duplicate joint control field')
            data[key] = value
        return data
    data = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(data, dict):
        raise ValueError('invalid joint control manifest')
    return data


class _JointObserver:
    def __init__(self, base, transaction, old, new, provider, probe):
        if not callable(provider) or not callable(probe):
            raise ValueError('trusted atomic host inventory and native probe required')
        self.base, self.transaction = base, transaction
        self.old, self.new, self.provider = old, new, provider
        self.pinned = None
        self.current = None
        self.names = None
        self.summary = None
        self.plugins = PluginOwnershipObserver(inventory=lambda _: self._sample()['plugins'])
        self.host = HostObserver(roster=lambda _: self.current['processes'], probe=probe)

    def _sample(self):
        try:
            gate = _control(self.base, self.base/GATE)
            if (gate != {'version': 1, 'transaction': self.transaction, 'state': 'fenced'}
                    or type(gate['version']) is not int):
                raise ValueError()
            baseline = _control(self.base, self.base/'bot-runtime-owner.json')['baseline']
            data = copy.deepcopy(self.provider(self.base.parent.resolve()))
            if (not isinstance(data, dict) or set(data) != {
                    'version', 'complete', 'core', 'generation', 'bots', 'baseline', 'plugins', 'processes'}
                    or type(data['version']) is not int or data['version'] != 1
                    or data['complete'] is not True or data['core'] != str(self.base.parent.resolve())
                    or not isinstance(data['generation'], str) or not 1 <= len(data['generation']) <= 64
                    or not isinstance(data['bots'], list) or sorted(data['bots']) != sorted(self.names)
                    or data['baseline'] != baseline):
                raise ValueError()
            for key in ('plugins', 'processes'):
                child = data[key]
                if (not isinstance(child, dict) or child.get('core') != data['core']
                        or child.get('generation') != data['generation'] or child.get('complete') is not True):
                    raise ValueError()
            if data['plugins'].get('bots') != data['bots'] or data['plugins'].get('baseline') != baseline:
                raise ValueError()
            if self.pinned is None:
                self.pinned = copy.deepcopy(data)
            elif self.pinned != data:
                raise ValueError()
            self.current = data
            return data
        except Exception:
            raise ValueError('joint host inventory or fenced scope invalid, incomplete or changed') from None

    def __call__(self, core, names, records):
        if Path(core).resolve() != self.base.parent.resolve():
            raise ValueError('joint process scope mismatch')
        self.names = tuple(names)
        self._sample()
        self.summary = self.plugins.review(core, names, self.old, self.new)
        if self.summary['blockers']:
            raise ValueError('joint plugin ownership blocked: ' + ','.join(self.summary['blockers']))
        result = self.host(core, names, records)
        self._sample()  # revoke if inventory changed during native PID probes
        return result


class JointOfflineLease:
    """Ephemeral pre-apply capability. Not serializable offline authorization.

Inventory is pinned to the pre-rename layout; actual transaction steps require a
separate reviewed, phase-aware coordinator. No production apply is enabled.
"""
    def __init__(self, lease, observer):
        self._lease, self._observer = lease, observer
        self.active = True

    def validate(self):
        if not self.active:
            raise ValueError('joint lease expired')
        self._lease.validate()

    def review(self):
        self.validate()
        return dict(copy.deepcopy(self._observer.summary),
                    joint_preapply_checked=True, production_apply_supported=False,
                    stage=self._lease.stage)

    def plan_qq(self, raw, *, baseline, config_relative):
        self.validate()
        observer = self._observer
        plan = observer.plugins.plan_qq(raw, observer.old, observer.new, baseline=baseline,
            core=observer.base.parent, bots=self._lease.names, config_relative=config_relative)
        self.validate()
        return dict(plan, joint_preapply_checked=True)

    def release_for_directory_move(self):
        self.validate()
        self._lease.release_for_directory_move()
        self.validate()


@contextmanager
def coordinated_offline_lease(base, bots, transaction, old, new, *, inventory=None, probe=probe_process):
    """Existing exact fence + global identity lock + runtime locks + joint evidence.

Normal context exit revalidates before releasing locks; failure never clears the
fence. No default production inventory, persisted boolean receipt or force path.
"""
    if not isinstance(transaction, str) or not re.fullmatch('[0-9a-f]{32}', transaction):
        raise ValueError('invalid joint transaction')
    base, bots = Path(base).absolute(), Path(bots).absolute()
    joint = _JointObserver(base, transaction, old, new, inventory, probe)
    with managed_offline_lease(base, bots, transaction, observer=joint) as offline:
        capability = JointOfflineLease(offline, joint)
        try:
            yield capability
            capability.validate()
        finally:
            capability.active = False
