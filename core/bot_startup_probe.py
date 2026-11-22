"""Controlled launcher instance admission and OWN-child readiness only.

No account/runtime config reads, discovery/adoption, redirects or proxy use.
A ready result is not a complete-host or offline/migration authority.
"""
import json
from pathlib import Path
import urllib.request
from threading import Lock

from bot_host_registry import HostEvidenceRegistry
from bot_registry import bot_id
from runtime_identity import safe_path
from bot_migration_gate import assert_identity_allowed


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _binding(core, name, registry):
    core = Path(core).absolute()
    if (type(registry) is not HostEvidenceRegistry
            or registry.core.resolve() != core.resolve() or name not in registry.bots):
        raise ValueError('controlled launcher scope mismatch')
    bot_id(name)
    return core


def _path(core, name):
    return safe_path(core/'data', core/'data/instances'/(name+'.json'))


def _raw(core, name):
    with _path(core, name).open('rb') as f:
        raw = f.read(4097)
    if len(raw) > 4096:
        raise ValueError('bounded instance metadata required')
    return raw


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('duplicate metadata field')
            result[key] = value
        return result
    def constant(value): raise ValueError('nonfinite metadata')
    result = json.loads(raw, object_pairs_hook=unique, parse_constant=constant)
    if type(result) is not dict: raise ValueError('metadata object required')
    return result


def refuse_existing_instance(core, name, registry):
    core = _binding(core, name, registry)
    assert_identity_allowed(core/'data', name)
    try:
        if _path(core, name).exists():
            raise ValueError('preexisting marker')
    except Exception:
        registry.revoke_coverage()
        raise ValueError('controlled launcher cannot adopt existing instance evidence') from None


def owned_ready_port(core, name, process, registry):
    core = _binding(core, name, registry)
    try:
        assert_identity_allowed(core/'data', name)
        pid = process.pid
        if type(pid) is not int or process.poll() is not None: return None
        row = dict(registry._processes[pid])
        if row['bot'] != name: return None
        identity = {key: row[key] for key in ('pid','created_filetime','image')}
        raw = _raw(core, name); marker = _json(raw)
        if set(marker) != {'pid','port'} or type(marker['pid']) is not int or marker['pid'] != pid:
            return None
        port = marker['port']
        if type(port) is not int or not 1 <= port <= 65535: return None
        def current():
            assert_identity_allowed(core/'data', name)
            return (process.poll() is None and registry._processes.get(pid) == row
                    and registry._alive(pid) == identity and _raw(core,name) == raw
                    and registry.listeners(port) == [{'address':'127.0.0.1','pid':pid}]
                    and registry._alive(pid) == identity)
        if not current(): return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open(f'http://127.0.0.1:{port}/infinity/bots',timeout=1) as response:
            body = response.read(8193)
        if len(body) > 8192: return None
        live = _json(body)
        if (live.get('active') != name or type(live.get('pid')) is not int or live['pid'] != pid
                or live.get('coreRoot') != str(core.resolve()) or not current()):
            return None
        return port
    except Exception:
        return None  # unavailable/not-ready, NEVER an offline or empty-host proof


class OwnedLauncherControl:
    """Pinned host-created child only; stop uses its retained handle, NEVER HTTP.

    Cooperative Python binding, not a sandbox, descendant coverage or retirement.
    """
    def __init__(self, core, name, process, registry, port):
        self.core = _binding(core, name, registry)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('controlled watch port required')
        self.name, self.process, self.registry, self.port = name, process, registry, port
        self._original_process, self._original_registry = process, registry
        self._original_handle=getattr(process,'_handle',None)
        self.pid = process.pid
        if type(self.pid) is not int:
            raise ValueError('controlled watch child required')
        try:
            self._row = dict(registry._processes[self.pid])
            self._identity = {key:self._row[key] for key in ('pid','created_filetime','image')}
        except Exception:
            raise ValueError('controlled watch registration unavailable') from None
        self._stop_attempted = False
        self._stopping = Lock()
        self.check()

    def _observed_exit(self):
        if (self._stop_attempted or self.process is not self._original_process
                or self.registry is not self._original_registry
                or self.process.pid != self.pid or self._identity['pid'] != self.pid
                or self._row['bot'] != self.name
                or self.registry._processes.get(self.pid) != self._row
                or self.process.poll() is None):
            return False
        if self._original_handle is not None:
            from bot_host_observer import retained_process_exit
            return retained_process_exit(self.process,self._identity,self._original_handle)
        seen = self.registry.probe(self.pid)
        return (type(seen) is dict and type(seen.get('pid')) is int
                and seen['pid'] == self.pid and seen.get('state') == 'absent'
                and self.registry._processes.get(self.pid) == self._row)

    def _original_binding(self):
        return (not self._stop_attempted and self.process is self._original_process
                and self.registry is self._original_registry
                and self.process.pid==self.pid and self._identity['pid']==self.pid
                and self._row['bot']==self.name
                and self.registry._processes.get(self.pid)==self._row
                and getattr(self.process,'_handle',None) is self._original_handle)

    def check(self, *, allow_exited=False):
        try:
            if allow_exited and self._observed_exit():
                return False  # local observation only; retirement remains separate
            if (self._stop_attempted or self.process is not self._original_process
                    or self.registry is not self._original_registry or self.process.pid != self.pid
                    or (self._original_handle is not None
                        and getattr(self.process,'_handle',None) is not self._original_handle)
                    or self.process.poll() is not None or self._row['bot'] != self.name
                    or self.registry._processes.get(self.pid) != self._row
                    or self.registry._alive(self.pid) != self._identity):
                raise ValueError('owned child changed')
        except Exception as error:
            if allow_exited:
                try:
                    if self._observed_exit():return False
                except Exception:pass
                # A fresh PID query can fail while the original process remains
                # live or is exiting. Only its unchanged retained handle can
                # prove local liveness; no adoption or row removal.
                try:
                    from bot_host_observer import retained_process_alive
                    if (self._original_binding()
                            and retained_process_alive(self.process,self._identity,self._original_handle)
                            and self._original_binding() and self.process.poll() is None):
                        return True
                    if self._observed_exit():return False
                    # Windows can lose the image name before the original handle
                    # signals exit. Give that handle one bounded wait, then require
                    # the same full exit proof. Timeout/unknown never grants liveness.
                    if self._original_handle is not None and self._original_binding():
                        self.process.wait(timeout=0.1)
                        if self._original_binding() and self._observed_exit():return False
                except Exception:pass
            self.registry.revoke_coverage()
            raise ValueError('controlled watch identity unavailable') from error
        return True

    def ready_port(self):
        self.check()
        port = owned_ready_port(self.core,self.name,self.process,self.registry)
        self.check()
        if port != self.port:
            self.registry.revoke_coverage()
            raise ValueError('controlled watch endpoint unavailable')
        return port

    def stop(self):
        if not self._stopping.acquire(blocking=False):
            self.registry.revoke_coverage()
            raise ValueError('controlled stop busy')
        try:
            self.check()
            self._stop_attempted = True  # uncertain stop never retried through this capability
            self.process.terminate()  # original HOST-created process object, not a PID lookup
        except BaseException as error:
            self.registry.revoke_coverage()
            if isinstance(error,Exception):
                raise ValueError('controlled stop unavailable; handle retained') from None
            raise
        finally:
            self._stopping.release()
