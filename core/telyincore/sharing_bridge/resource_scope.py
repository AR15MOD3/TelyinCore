"""Offline resource ownership contract. No discovery, OS kill, IO or auto-adoption.

Callbacks are host fixtures only. A successful callback is not proof of exit;
every owned resource needs an independent gone() check.
"""
from dataclasses import dataclass
import secrets
import threading


class CleanupIncomplete(RuntimeError):
    pass


@dataclass(frozen=True)
class Lease:
    token: str
    label: str
    identity: object
    stop: object
    current: object
    gone: object


class ResourceScope:
    def __init__(self):
        self._leases=[]
        self._pending=[]
        self._closed=False
        self._lock=threading.RLock()
        self._owner=threading.get_ident()
        self.events=[]

    def _thread(self):
        if threading.get_ident()!=self._owner:raise RuntimeError('scope_wrong_thread')

    def adopt(self,label,identity,*,stop,current,gone):
        """Explicit acquisition only. Never infer ownership from a process name."""
        with self._lock:
            self._thread()
            if self._closed or identity is None or not all(callable(v) for v in (stop,current,gone)):
                raise ValueError('resource_contract')
            if not isinstance(label,str) or not label or len(label)>48 or not label.isascii():
                raise ValueError('fixed_resource_label_required')
            if any(v.identity is identity for v in self._leases):raise ValueError('duplicate_resource')
            lease=Lease(secrets.token_hex(16),label,identity,stop,current,gone)
            self._leases.append(lease)
            return lease.token

    def finish(self,*,primary=None):
        with self._lock:
            self._thread()
            if self._closed:
                if self._pending:raise CleanupIncomplete('previous_cleanup_incomplete')
                return
            self._closed=True
            failures=[]
            for lease in reversed(self._leases):
                try:
                    if lease.gone() is True:
                        self.events.append((lease.label,'already_gone'));continue
                    if lease.current() is not lease.identity:
                        # Never stop a replacement sharing the old display name.
                        raise CleanupIncomplete('owned_instance_no_longer_current')
                    result=lease.stop()
                    if result is False or lease.gone() is not True:
                        raise CleanupIncomplete('resource_exit_unconfirmed')
                    self.events.append((lease.label,'closed'))
                except BaseException as error:
                    self._pending.append(lease)
                    failures.append((lease.label,error))
                    self.events.append((lease.label,'unresolved'))
            if primary is not None:
                for label,error in failures:primary.add_note('cleanup_'+label+': '+type(error).__name__)
                return
            if failures:
                err=CleanupIncomplete('resource_cleanup_incomplete')
                for label,error in failures:err.add_note('cleanup_'+label+': '+type(error).__name__)
                raise err

    def unresolved(self):
        return tuple(v.label for v in self._pending)

    def retry_unresolved(self,*,primary=None):
        """Explicit host request only; no timer and no retry of application sends."""
        with self._lock:
            self._thread()
            if not self._closed:raise RuntimeError('finish_scope_before_retry')
            pending,self._pending=self._pending,[]
            failures=[]
            for lease in pending:
                try:
                    if lease.gone() is True:
                        self.events.append((lease.label,'resolved'));continue
                    if lease.current() is not lease.identity:raise CleanupIncomplete('owned_instance_no_longer_current')
                    result=lease.stop()
                    if result is False or lease.gone() is not True:raise CleanupIncomplete('resource_exit_unconfirmed')
                    self.events.append((lease.label,'resolved'))
                except BaseException as error:
                    self._pending.append(lease);failures.append((lease.label,error))
                    self.events.append((lease.label,'unresolved'))
            if primary is not None:
                for label,error in failures:primary.add_note('cleanup_'+label+': '+type(error).__name__)
                return
            if failures:
                error=CleanupIncomplete('resource_cleanup_incomplete')
                for label,failure in failures:error.add_note('cleanup_'+label+': '+type(failure).__name__)
                raise error
