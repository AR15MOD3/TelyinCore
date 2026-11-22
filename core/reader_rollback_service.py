"""Trusted administrative HOST attachment. Not exposed as a model/plugin tool.

An actual complete registry must exist before construction. This service does
not create one, adopt PIDs, accept a client coverage flag, stop Bots or unfence.
"""
from pathlib import Path
from threading import Lock
from types import MappingProxyType
from bot_host_registry import HostEvidenceRegistry
from reader_rollback_coordinator import rollback

class HostReaderRollbackService:
    def __init__(self,registry,transaction,*,current_pins,replacements,evidence):
        if type(registry) is not HostEvidenceRegistry:
            raise ValueError('original trusted HOST registry required')
        self.registry=registry;self.core=registry.core;self.transaction=transaction
        self.pins=MappingProxyType(dict(current_pins))
        self.targets=MappingProxyType({key:(bytes(value[0]),value[1]) for key,value in replacements.items()})
        self.evidence=Path(evidence).absolute()
        self._lock=Lock();self._closed=False;self._attempted=False;self._active=False
        self.failure=None;self.result=None
        with registry.capture(self.core) as snapshot:
            if snapshot['complete'] is not True:raise ValueError('global HOST coverage not ready')
            self.generation=snapshot['generation']

    def execute(self):
        with self._lock:
            if self._closed or self._attempted:raise ValueError('single live HOST administrative attempt required')
            self._attempted=True;self._active=True
        try:
            self.result=rollback(self.core,self.transaction,registry=self.registry,
                current_pins=dict(self.pins),replacements=dict(self.targets),evidence=self.evidence,
                host_generation=self.generation)
            return dict(self.result)
        except BaseException as error:
            self.failure=error  # exact original, including cancellation, retained
            raise
        finally:
            with self._lock:self._active=False

    def close(self):
        with self._lock:
            self._closed=True  # irreversible denial of new work; does not cancel old
            return not self._active and self.failure is None

    def shutdown_state(self):
        with self._lock:
            return {'accepting':not self._closed and not self._attempted,'pending':self._active,
                    'failed':self.failure is not None,'attempted':self._attempted,
                    'result_phase':None if self.result is None else self.result['phase'],
                    'release_safe':self._closed and not self._active and self.failure is None}
