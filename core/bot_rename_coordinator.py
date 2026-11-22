from physical_close import close_after_failure
"""Actual registry/phase coordination, distinct from admission authority.

The internal driver coordinates the existing fenced filesystem primitive with
one ORIGINAL live registry. It does not turn an empty/unsealed registry into
coverage. Public admission remains denied while production entry authority is
not connected. No fixture inventory, persisted complete flag, force or unfence.
"""
from copy import deepcopy
from pathlib import Path
from threading import Lock

from bot_host_registry import HostEvidenceRegistry
from runtime_identity import LEDGER, identity_lock
from bot_rename_projection import _json, project_identity
from bot_rename_transaction import _ControlledTransaction
from bot_rename_opaque_lease import _OpaqueLease


def _registry(core, registry):
    if type(registry) is not HostEvidenceRegistry:
        raise ValueError('original authoritative host registry required')
    core=Path(core).absolute()
    if core.resolve()!=registry.core.resolve():
        raise ValueError('original registry deployment mismatch')
    return core


class _RegistryPhaseCoordinator:
    """Internal phase driver, NOT an admission capability or production API.

    Like _ControlledTransaction this can be exercised on newly constructed
    synthetic roots without claiming external coverage. Its constructor accepts
    only actual objects, not a dict/callback or an on-disk authority receipt.
    The only public construction route is _begin, which requires admission.
    """
    def __init__(self, transaction, registry, *, creation=None):
        if type(transaction) is not _ControlledTransaction:
            raise ValueError('original controlled transaction required')
        self.core=_registry(transaction.core,registry)
        self.transaction,self.registry=transaction,registry
        self.creation=creation
        if creation is not None:
            from bot_new_deployment import NewDeploymentState
            if type(creation) is not NewDeploymentState or registry is not creation._registry:
                raise ValueError('original creation registry required')
            creation.check(self.core)
        self._busy=Lock();self._pending=False;self._expired=False;self._lease=None
        self._snapshot=None
        self._suspended=False;self._suspended_records=None
        with identity_lock(self.core/'data'):
            # inspect before capture: registry mutex is deliberately nonreentrant.
            state=registry.inspect()
            if (state['registered_processes'] or state['bound_configs']
                    or state['pending_account_changes'] or state['coverage_revoked']):
                raise ValueError('registry resources/denials remain; no coordination')
            with registry.capture(self.core) as capture:
                rows=_json(dict(transaction.phases[0].files)['core/data/'+LEDGER])['bindings']
                owner=_json(dict(transaction.phases[0].files)['core/data/bot-runtime-owner.json'])['baseline']
                if sorted(rows)!=capture['bots'] or owner!=capture['baseline']:
                    raise ValueError('original registry/transaction identity mismatch')
                if capture['processes'] or capture['accounts']:
                    raise ValueError('registry process/account resources remain')
                transaction._reload()
                if transaction.row['cursor']!=0:
                    raise ValueError('original pre-move opaque lease required; no reacquire')
                self._snapshot=deepcopy(capture)
                self._lease=_OpaqueLease(transaction)
                try:self._guard(0)
                except BaseException as primary:
                    close_after_failure(self._lease, primary)
                    primary.cleanup_owner = self
                    raise

    def _guard(self,cursor=None,*,payload=True):
        # Called only under identity lock and this registry's capture mutex.
        # Sticky fault is also settable lock-free by native/channel callbacks.
        if self._expired:raise ValueError('coordinator expired; original lease required')
        if self.creation is not None:self.creation.check(self.core)
        r=self.registry
        r._sync_revocations()
        s=self._snapshot
        if (r._fault.is_set() or r._tainted or r._account_changes
                or r._generation!=s['generation'] or r._processes or r._bindings
                or list(r.bots)!=s['bots'] or r.baseline!=s['baseline']
                or bool(r._sealed)!=s['complete']):
            raise ValueError('original registry changed/revoked; phase fenced')
        if self._lease is not None and payload:
            self._lease.check(self.transaction.row['cursor'] if cursor is None else cursor)

    def suspend(self):
        """Release payload handles at one ACK, retaining original live authority.

        Does not pause the automation or stop a process. Both migration fences
        stay. No serialization, origin adoption, reset or crash recovery claim.
        """
        if not self._busy.acquire(blocking=False):raise ValueError('coordinator busy')
        try:
            if self._expired or self._pending or self._suspended:
                raise ValueError('original active acknowledged coordinator required')
            try:
                with identity_lock(self.core/'data'):
                    with self.registry.capture(self.core) as capture:
                        if capture!=self._snapshot:raise ValueError('original registry changed')
                        self.transaction._reload();self._guard()
                        self._suspended_records=tuple(self.transaction.records)
                        self._lease.suspend(self.transaction.row['cursor'])
                        self._suspended=True
                        return True
            except BaseException:
                self._pending=True
                raise
        finally:self._busy.release()

    def resume(self):
        if not self._busy.acquire(blocking=False):raise ValueError('coordinator busy')
        try:
            if self._expired or self._pending or not self._suspended:
                raise ValueError('original suspended coordinator required; no retry')
            try:
                with identity_lock(self.core/'data'):
                    with self.registry.capture(self.core) as capture:
                        if capture!=self._snapshot:raise ValueError('original registry changed')
                        # Validate original origin/registry BEFORE opening payloads.
                        self._guard(payload=False)
                        self.transaction._reload()
                        if tuple(self.transaction.records)!=self._suspended_records:
                            raise ValueError('acknowledged journal advanced while suspended')
                        self._lease.resume(self.transaction.row['cursor'])
                        self._guard()
                        self._suspended=False;self._suspended_records=None
                        return True
            except BaseException:
                self._pending=True
                raise
        finally:self._busy.release()

    def step(self, action):
        if not self._busy.acquire(blocking=False):
            raise ValueError('coordinator busy; no concurrent phase')
        try:
            if self._expired:raise ValueError('coordinator expired; original lease required')
            if self._pending:raise ValueError('coordinator pending; blind retry denied')
            if self._suspended:raise ValueError('coordinator suspended; resume original session first')
            try:
                with identity_lock(self.core/'data'):
                    with self.registry.capture(self.core) as capture:
                        if capture!=self._snapshot:
                            raise ValueError('original registry generation changed')
                        self._guard()
                        return self.transaction._step_locked(action,guard=self._guard,effect_window=self._lease.effect_window)
            except BaseException:
                self._pending=True
                raise
        finally:self._busy.release()

    def close(self):
        if not self._busy.acquire(blocking=False):
            raise ValueError('coordinator busy; cannot release active lease')
        try:
            self._expired=True
            if self._lease is not None:self._lease.close()
        finally:self._busy.release()

    def status(self):
        return dict(pending=self._pending,admission_authority=False,
                    production_apply_supported=False,global_complete=False,
                    gate_retained=True)


def _begin(core,old,new,*,registry,**scope):
    core=_registry(core,registry)
    # This actual source is intentionally false today. Neither sealed callbacks
    # nor finite LauncherHost retirement replace complete deployment authority.
    state=registry.inspect()
    with identity_lock(core/'data'):
        with registry.capture(core) as capture:
            if capture['complete'] is not True or capture['processes'] or capture['accounts']:
                raise ValueError('original host coverage/account quiescence unproven; no apply')
            if (state['production_entrypoints_connected'] is not True
                    or state['production_apply_supported'] is not True
                    or state['bound_configs'] or state['pending_account_changes']):
                raise ValueError('production rename authority/adapters not connected; no apply')
            projection=project_identity(core,old,new,**scope)
            transaction=_ControlledTransaction._prepare_locked(projection)
            admission_snapshot=deepcopy(capture)
    # Retain original registry. No JSON recovery operation can create admission.
    coordinator=_RegistryPhaseCoordinator(transaction,registry)
    if coordinator._snapshot!=admission_snapshot:
        coordinator._pending=True
        coordinator.close()
        raise ValueError('registry changed after admission; transaction remains fenced')
    return coordinator
