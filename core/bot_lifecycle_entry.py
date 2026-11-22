"""Explicit, scope/plan-bound process facade for QQControl's actual entrypoint.

No bootstrap, production singleton or discovery. SnowLuma is fixed-plan process
only: no credentials, recovery, login, account health or adoption capability.
"""
import copy
from contextlib import contextmanager
from pathlib import Path
import subprocess
import time
from threading import local

from bot_lifecycle_channel import LifecycleClient
from runtime_identity import identity_lock, safe_path
from bot_migration_gate import assert_no_migration


class RemoteProcess:
    def __init__(self, client, slot):
        self.client, self.slot = client, slot
        self.retired = False

    def poll(self):
        if self.retired: return 0
        return None if self.client.request('status', self.slot)['state'] == 'alive' else 0

    def terminate(self):
        if not self.retired: self.client.request('stop', self.slot)

    def wait(self, timeout):
        if type(timeout) not in (int, float) or not 0 < timeout <= 10:
            raise ValueError('bounded owned-process wait required')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.poll() is not None:
                if not self.retired:
                    self.client.request('retire', self.slot)
                    self.retired = True
                return 0  # absence, NOT the child's original exit code
            time.sleep(0.02)
        raise subprocess.TimeoutExpired('mediated owned process', timeout)


class LifecycleProcessBridge:
    def __init__(self, core, bot_scope, client, *, argv=None, cwd=None, metadata_only=False, env=None, slot='tts', fixed_contract=False, companion_plan=None):
        if type(metadata_only) is not bool:
            raise ValueError('explicit metadata-only boolean required')
        if metadata_only:
            if (type(client) is not LifecycleClient or argv is not None or cwd is not None
                    or env is not None or slot != 'tts' or fixed_contract is not False
                    or companion_plan is not None):
                raise ValueError('metadata-only bridge cannot carry process plans')
            self.core = Path(core).resolve()
            self.scope = None if bot_scope is None else Path(bot_scope).resolve()
            self.client, self.slot = client, slot
            self._metadata_only = True
            self._plan = self._companion_plan = None
            self._fixed_contract = False
            self._started_slots = set()
            self.started = False
            self._change_state = local()
            return
        self._metadata_only = False
        if (type(fixed_contract) is not bool or type(client) is not LifecycleClient or type(argv) is not list
                or not argv or not all(type(v) is str for v in argv)
                or (env is not None and (type(env) is not dict
                    or not all(type(k) is str and type(v) is str for k,v in env.items())))
                or (companion_plan is not None and (slot != 'tts' or not fixed_contract
                    or type(companion_plan) is not dict
                    or set(companion_plan) != {'argv', 'cwd', 'env'}))
                or slot not in ('tts', 'snowluma')
                or (slot == 'snowluma' and (not fixed_contract or env is None
                    or env.get('SNOWLUMA_HOOK_AUTOLOAD') != '0'))):
            raise ValueError('invalid mediated process plan')
        self.core = Path(core).resolve()
        self.scope = None if bot_scope is None else Path(bot_scope).resolve()
        self.client, self.slot = client, slot
        self._plan = (copy.deepcopy(argv), str(Path(cwd).resolve()), copy.deepcopy(env))
        self._companion_plan = None
        if companion_plan is not None:
            from bot_parent_launch import FixedProcessFactory
            frozen = FixedProcessFactory(**copy.deepcopy(companion_plan)).plan()
            if frozen['env'].get('SNOWLUMA_HOOK_AUTOLOAD') != '0':
                raise ValueError('fixed SnowLuma companion requires no-autoload plan')
            self._companion_plan = (frozen['argv'], str(Path(frozen['cwd']).resolve()), frozen['env'])
        self._fixed_contract = fixed_contract
        if fixed_contract:
            for kind in self.configured_processes():
                self._contract_digest(kind)  # both plans validated before any RPC
        self._started_slots = set()
        self.started = False  # compatibility: any attempted slot, never a retry grant
        self._change_state = local()

    def configured_processes(self):
        if self._metadata_only:
            return ()
        return ('tts', 'snowluma') if self._companion_plan is not None else (self.slot,)

    def _approved_plan(self, kind):
        if self._metadata_only:
            raise ValueError('metadata-only lifecycle has no process capability')
        if kind == self.slot:
            return self._plan
        if kind == 'snowluma' and self._companion_plan is not None:
            return self._companion_plan
        raise ValueError('unapproved mediated process slot')

    def _contract_digest(self, kind=None):
        from bot_parent_launch import launch_contract
        kind = self.slot if kind is None else kind
        argv, cwd, env = self._approved_plan(kind)
        return launch_contract(self.client.tx._key, self.client.tx.session,
            str(self.core), None if self.scope is None else str(self.scope), kind,
            dict(argv=argv, cwd=cwd, env=env))

    def _require_rpc_unlocked(self):
        if getattr(self._change_state, 'held', False):
            raise ValueError('lifecycle RPC forbidden under local identity guard')

    @contextmanager
    def _locked_change(self):
        self._require_rpc_unlocked()
        with identity_lock(self.core/'data'):
            self._change_state.held = True
            try:
                assert_no_migration(self.core/'data')
                yield
            finally:
                self._change_state.held = False

    def bind(self, core, bot_scope):
        self._require_rpc_unlocked()
        scope = None if bot_scope is None else Path(bot_scope).resolve()
        if Path(core).resolve() != self.core or scope != self.scope:
            raise ValueError('mediated lifecycle scope mismatch')
        description = self.client.request('describe')
        if description.get('instance_only') is True:
            self.client.close()
            raise ValueError('instance-only parent cannot bind process/account bridge')
        if (description['core'] != str(self.core) or description['bot_scope'] != (
                None if self.scope is None else str(self.scope))):
            self.client.close()
            raise ValueError('authenticated parent lifecycle scope mismatch')

    def publish_instance(self, port):
        self._require_rpc_unlocked()
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('explicit owned instance port required')
        self.bind(self.core, self.scope)
        self.client.request('publish-instance', port=port)

    def start(self, kind, argv, cwd, env):
        self._require_rpc_unlocked()
        plan = (argv, str(Path(cwd).resolve()), env)
        approved = self._approved_plan(kind)
        if kind in self._started_slots or plan != approved:
            raise ValueError('unapproved mediated process plan; no local fallback')
        self._started_slots.add(kind)  # each uncertain slot stays single use
        self.started = True
        self.bind(self.core, self.scope)
        if self._fixed_contract:
            self.client.request('start-fixed', kind, contract=self._contract_digest(kind))
        else:
            self.client.request('start', kind)
        return RemoteProcess(self.client, kind)

    def start_approved_snow_process(self):
        """Use only the sealed HOST contract, never WebUI/config/env inputs."""
        return self.start_approved_process('snowluma')

    def start_approved_process(self, kind):
        self._require_rpc_unlocked()
        if not self._fixed_contract:
            raise ValueError('fixed process capability required')
        argv, cwd, env = copy.deepcopy(self._approved_plan(kind))
        return self.start(kind, argv, cwd, env)

    @contextmanager
    def account_change_guard(self, config_path, logs):
        """Host revocation ACK first; then local global lock, gate & path checks.

Never hold the local identity lock while waiting for a parent needing that lock.
No success/rollback callback restores account authority; pending stays sticky.
"""
        self._require_rpc_unlocked()
        expected = (self.core/'extensions/core-infinity-qq/config.json' if self.scope is None
                    else self.scope/'qq/config.json')
        expected_logs = self.core/'data/infinity-processes' if self.scope is None else self.scope/'processes'
        if Path(config_path).absolute() != expected or Path(logs).absolute() != expected_logs:
            raise ValueError('mediated account config scope mismatch')
        self.bind(self.core, self.scope)
        self.client.request('prepare-account-change')
        with self._locked_change():
            safe_path(self.core.parent, expected)
            safe_path(self.core.parent, Path(logs))
            yield

    def validate_recovery_path(self, path):
        logs = self.core/'data/infinity-processes' if self.scope is None else self.scope/'processes'
        expected = logs/'snowluma-recovery.json'
        if Path(path).absolute() != expected:
            raise ValueError('mediated recovery store scope mismatch')
        return expected

    @contextmanager
    def recovery_change_guard(self, path):
        self._require_rpc_unlocked()
        expected = self.validate_recovery_path(path)
        # Fresh authenticated parent scope, then sticky revoke ACK. Neither RPC
        # may be issued while this helper holds the child identity lock.
        self.bind(self.core, self.scope)
        self.client.request('prepare-recovery-change')
        with self._locked_change():
            for candidate in (expected, expected.with_suffix('.tmp'),
                              expected.with_suffix('.json.bak')):
                safe_path(self.core.parent, candidate)
            yield
