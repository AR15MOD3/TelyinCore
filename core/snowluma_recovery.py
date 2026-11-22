"""Explicitly triggered local QQ/SnowLuma recovery. Never runs at Core boot."""
from __future__ import annotations

from contextlib import contextmanager

import base64
import hashlib
import ctypes
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from threading import Event, Lock, RLock, Thread, local, current_thread
from time import monotonic
from typing import Any, Callable

import httpx
from bot_host_observer import probe_process
from bot_host_inventory import _identity


class RecoveryCancelled(Exception):
    """A cancelled or superseded generation; never publish late results."""


class NeedsAction(ValueError):
    """A known management gate, reported without upstream response content."""


def _crypt(raw: bytes, *, decrypt: bool = False) -> bytes:
    """Windows DPAPI, bound to this OS user. Never fall back to clear text."""
    if os.name != "nt":
        raise ValueError("此系统暂不支持安全保存管理凭据；仍可手动登录")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not operation(ctypes.byref(source), None, None, None, None, 1,
                     ctypes.byref(result)):
        raise ValueError("Windows 凭据保护操作失败")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def qq_processes() -> list[dict[str, Any]] | None:
    """None is UNKNOWN, not 'no QQ'; do not launch on a failed probe."""
    if os.name != "nt":
        return None
    command = ("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
               "$p=@(Get-CimInstance Win32_Process -Filter \"Name='QQ.exe'\" "
               "-ErrorAction Stop); if(@($p | Where-Object {!$_.CommandLine}).Count) "
               "{exit 2}; @($p | Where-Object { "
               "$_.CommandLine -notmatch '--type=' } | "
               "Select-Object ProcessId,ExecutablePath) | ConvertTo-Json -Compress")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        if result.returncode:
            return None
        rows = json.loads(result.stdout.strip() or "[]")
        return [rows] if isinstance(rows, dict) else rows if isinstance(rows, list) else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def find_qq() -> str:
    candidates: list[Path] = []
    if os.name == "nt":
        import winreg

        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive,
                            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\QQ.exe",
                            0, winreg.KEY_READ | view) as key:
                        value, _ = winreg.QueryValueEx(key, "")
                        candidates.append(Path(str(value).strip('"')))
                except OSError:
                    pass
    for name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(name)
        if base:
            candidates.extend(Path(base) / relative for relative in
                              ("Tencent/QQNT/QQ.exe", "Tencent/QQ/QQ.exe"))
    found = {str(path.resolve()) for path in candidates if path.is_file()}
    return next(iter(found)) if len(found) == 1 else ""


def launch_qq(path: str) -> subprocess.Popen:
    target = Path(path).resolve()
    if target.name.lower() != "qq.exe" or not target.is_file():
        raise ValueError("请选择已安装的 QQ.exe")
    return subprocess.Popen([str(target)], cwd=target.parent, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


class Recovery:
    """One bounded job per manual click; login/load attempts are not replayed."""

    def valid_qq_path(self, path):
        return Path(path).name.lower() == self.qq_image_name and Path(path).is_file()

    qq_image_name = 'qq.exe'
    learn_process_path = True

    def __init__(self, path: Path, *, processes: Callable = qq_processes,
                 discover: Callable = find_qq, launch: Callable = launch_qq,
                 protect: Callable = _crypt, client: Callable | None = None,
                 validate_target: Callable | None = None,
                 trusted_management: Callable[[], bool] | None = None,
                 process_lifecycle=None, store_root=None, identity_base=None, qq_process_probe=None, mutation_root=None, account_root=None, account_lifecycle=None, management_port=5099, management_instance=None) -> None:
        if qq_process_probe is not None and not callable(qq_process_probe):
            raise ValueError("trusted QQ process probe callable required")
        self._qq_process_probe = probe_process if qq_process_probe is None else qq_process_probe
        self._process_lifecycle = process_lifecycle
        self._account_lifecycle=account_lifecycle
        self._parent_work_active=False
        self._management_instance=management_instance
        if management_instance is not None:
            from snowluma_instance import SnowInstance,load_instance
            if (type(management_instance) is not SnowInstance or account_lifecycle is None
                    or account_lifecycle.core!=management_instance.core or account_lifecycle.bot!=management_instance.bot
                    or load_instance(management_instance.core,management_instance.bot)!=management_instance):
                raise ValueError('authenticated own management instance required')
        if account_lifecycle is not None:
            from bot_instance_publisher import InstancePublisher
            if type(account_lifecycle) is not InstancePublisher or process_lifecycle is not None:
                raise ValueError('exact default parent account lifecycle required')
        if process_lifecycle is not None:
            from bot_lifecycle_entry import LifecycleProcessBridge
            if type(process_lifecycle) is not LifecycleProcessBridge:
                raise ValueError("explicit recovery store lifecycle bridge required")
            process_lifecycle.validate_recovery_path(path)
        self._store_job = local()
        self.validate_target = validate_target
        # A compatible public JSON body is not endpoint ownership evidence.
        # Callers must supply an independently verified, live trust predicate.
        # No predicate (including the current product wiring) fails closed.
        self.trusted_management = trusted_management or (lambda: False)
        self.path = Path(path).absolute()
        self._store_root = Path(store_root).absolute() if store_root is not None else self.path.parent
        self._identity_base = Path(identity_base).absolute() if identity_base is not None else self.path.parent
        # Trusted in-process wiring only; never a saved/UI override. Shared core
        # namespace is denial metadata, NOT account/host ownership authority.
        self._mutation_root = None if mutation_root is None else Path(mutation_root).absolute()
        if self._mutation_root is not None:
            from runtime_identity import safe_path
            safe_path(self._store_root, self._mutation_root)
        self._account_root = None if account_root is None else Path(account_root).absolute()
        if self._account_root is not None:
            from runtime_identity import safe_path
            safe_path(self._store_root, self._account_root)
        self.processes, self.discover, self.launch = processes, discover, launch
        self.protect = protect
        if type(management_port) is not int or not 1024 <= management_port <= 65535:
            raise ValueError('fixed local management port required')
        self.management_port=management_port
        self.client = client or (lambda: httpx.Client(
            base_url=f"http://127.0.0.1:{self.management_port}", timeout=3, trust_env=False,
            follow_redirects=False))
        if process_lifecycle is not None:
            self.processes = self.discover = self.launch = self.client = self._runtime_unavailable
            self.trusted_management = lambda: False
        self._lock = RLock()
        # Lifecycle -> state lock; worker never takes lifecycle while exiting.
        self._lifecycle = RLock()
        self._job = local()
        self._generation = 0
        self._process_choices = {}
        self._health_at = None
        self._sample_expired = False
        self._expired_revision = None
        self._health_revision = None
        self._health_target = ""
        self._health_ttl = 30.0
        self._cancel = Event()
        self._closed = Event()
        self._thread: Thread | None = None
        self._busy = False
        self._launch_attempted = False  # sticky: no exit/descendant authority here
        self._launched_process = None  # keep returned handle even after cancel/close
        self._login_lifecycle_state = "unobserved"
        self._http_lease = None
        self._http_client_state = "unobserved"
        self._http_cleanup_failed = False
        self._active_job = None  # runtime operation owner, including direct calls
        self._job_done = Event()
        self._job_done.set()
        self._phase = "idle"
        self._account_online = False
        self._management_authenticated = False
        self._message = "等待确认 QQ 接入状态。"

    def _begin_runtime(self):
        # Worker admission only takes state lock: cancel may hold lifecycle while
        # joining that worker. Direct calls share the exact same single-flight.
        with self._lock:
            self._require_open()
            owner = current_thread()
            if (self._active_job is not None
                    or (self._busy and self._thread is not owner)):
                raise ValueError("recovery runtime operation busy")
            self._active_job = owner
            self._busy = True
            self._job_done.clear()
            self._job.generation = self._generation
            self._job.mutation_owner = uuid.uuid4().hex
            self._job.refresh_confirmed = False
            self._job.store_authorized = False  # first snapshot only after management trust
        if self._account_lifecycle is not None:
            try:
                if self._parent_work_active:
                    raise ValueError('prior parent account work unresolved')
                self._parent_work_active=True  # uncertain ACK also keeps parent work unresolved
                self._account_lifecycle.begin_account_work()
            except BaseException:
                self._end_runtime()  # no local resource admitted; HOST uncertainty retained
                raise

    def _end_runtime(self):
        with self._lock:
            # Called only after this thread successfully acquired admission.
            del self._job.generation
            if hasattr(self._job, "revision"):
                del self._job.revision
            for field in ('runtime_snapshot', 'runtime_binding', 'store_authorized', 'qq_identity', 'mutation_owner', 'refresh_confirmed', 'login_intent', 'login_revision', 'login_saved_revision', 'login_deferred_auth', 'login_deferred_online', 'management_resource', 'deferred_auth', 'deferred_online', 'confirmed_process'):
                if hasattr(self._job, field):
                    delattr(self._job, field)
            self._active_job = None
            if self._thread is not current_thread():
                self._busy = False
                if self._closed.is_set():
                    self._phase = "closed"
                elif self._cancel.is_set():
                    self._phase = "cancelled"
            self._job_done.set()  # operation ended, NOT the caller thread's life

    def _worker_alive(self):
        return (self._active_job is not None
                or bool(self._thread and self._thread.is_alive()))

    def _require_open(self):
        if self._closed.is_set():
            raise ValueError('recovery lifecycle closed')

    def close(self):
        # Permanent denial BEFORE join/callbacks. cancel() alone is reusable.
        self._closed.set()
        if self._process_lifecycle is not None:
            self.invalidate('closed')
            return
        self.cancel()
        if self._account_lifecycle is not None and self._launched_process is not None:
            from bot_parent_qq_launch import ParentQQProcess
            p=self._launched_process
            if type(p) is not ParentQQProcess or p.publisher is not self._account_lifecycle:
                raise ValueError("retained QQ capability mismatch")
            p.terminate()
            if self._parent_work_active and self._http_lease is None and self._job_done.is_set():
                self._account_lifecycle.acknowledge_account_work_drained()
                self._parent_work_active=False

    @staticmethod
    def _runtime_unavailable(*args, **kwargs):
        raise ValueError("experimental recovery store has no local runtime capability")

    @staticmethod
    def _revision(data: dict) -> str:
        # Opaque config identity, not a credential/token returned to the page.
        selected = {k: data.get(k) for k in ("targetUin", "username", "qqPath", "credential", "session")}
        return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()

    def _invalidate_locked(self, phase="unknown") -> None:
        self._generation += 1
        self._process_choices.clear()
        self._sample_expired = False
        self._expired_revision = None
        self._health_at = None
        self._health_target = ""
        self._health_revision = None
        self._account_online = False
        self._management_authenticated = False
        self._phase = phase
        self._message = "健康采样已失效；请重新验证目标接入。"

    def invalidate(self, reason="unknown") -> None:
        with self._lock:
            self._cancel.set()
            self._invalidate_locked(reason)

    def _assert_current(self) -> None:
        if self._closed.is_set():
            raise RecoveryCancelled()
        generation = getattr(self._job, "generation", None)
        if generation is not None and (self._cancel.is_set() or generation != self._generation):
            raise RecoveryCancelled()
        revision = getattr(self._job, "revision", None)
        if revision is not None and self._revision(self._read()) != revision:
            raise RecoveryCancelled()

    def _update(self, **values) -> None:
        with self._lock:
            self._assert_current()
            for key, value in values.items():
                setattr(self, "_" + key, value)

    def _auth_sample(self, data) -> None:
        if self._login_lifecycle_state == 'pending' or self._http_lease is not None:
            self._job.deferred_auth = True
            return
        with self._lock:
            self._assert_current()
            self._sample_expired = False
            self._management_authenticated = True
            self._health_at = monotonic()
            self._health_target = str(data.get("targetUin") or "")
            self._health_revision = self._revision(data)

    def _online(self, data, row) -> None:
        if self._login_lifecycle_state == 'pending' or self._http_lease is not None:
            self._job.deferred_online = {'uin': row.get('uin')}
            return
        if hasattr(self._job, "qq_identity"):
            self._pin_qq_target(self._job.qq_identity["pid"])
        with self._lock:
            self._assert_current()
            target = str(data.get("targetUin") or "")
            if self._revision(self._read()) != self._revision(data):
                raise RecoveryCancelled()
            # Management evidence is NOT a OneBot/WebSocket handshake.
            self._account_online = bool(target and str(row.get("uin")) == target)
            self._phase = "online" if self._account_online else "unknown"
            self._health_at = monotonic()
            self._health_target = target
            self._health_revision = self._revision(data)

    def _read(self) -> dict:
        self._require_open()
        if self._process_lifecycle is not None:
            if not getattr(self._store_job, "active", False):
                raise ValueError("recovery store access requires the host revocation guard")
            return self._store_job.snapshot.read()
        if getattr(self._store_job, 'local_active', False):
            return self._store_job.local_snapshot.read()
        if getattr(self._job, 'store_authorized', False):
            try:
                with self._local_store_change():
                    return self._store_job.local_snapshot.read()
            except (OSError, ValueError) as changed:
                raise RecoveryCancelled() from changed
        from runtime_identity import safe_path
        from bot_guarded_store import MAX_BYTES, _object
        safe_path(self._store_root, self.path)
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(MAX_BYTES + 1)
        except FileNotFoundError:
            raw = None
        if raw is not None and len(raw) > MAX_BYTES:
            raise ValueError('recovery store exceeds guarded limit')
        return _object(raw)

    def _storage_checkpoint(self):
        self._require_open()
        if getattr(self._store_job, 'local_active', False):
            from bot_migration_gate import assert_no_migration
            assert_no_migration(self._identity_base)
        generation = getattr(self._job, 'generation', None)
        if generation is not None and (self._cancel.is_set() or generation != self._generation):
            raise RecoveryCancelled()

    @contextmanager
    def _local_store_change(self):
        if self._process_lifecycle is not None:
            raise ValueError('local recovery store requires default lifecycle')
        if getattr(self._store_job, 'local_active', False):
            raise ValueError('reentrant local recovery store mutation forbidden')
        from runtime_identity import safe_path, identity_lock
        from bot_migration_gate import assert_no_migration
        from bot_guarded_store import GuardedStore
        runtime = getattr(self._job, 'store_authorized', False)
        try:
            with self._lock:
                self._storage_checkpoint()
                binding = (self._store_root, self.path.absolute(), self._identity_base, self._mutation_root, self._account_root)
                if (runtime and hasattr(self._job, 'runtime_binding')
                        and self._job.runtime_binding != binding):
                    raise ValueError('recovery runtime store binding changed')
                safe_path(self._store_root, self.path)
                safe_path(self._store_root, self._identity_base)
                with identity_lock(self._identity_base):
                    assert_no_migration(self._identity_base)
                    snapshot = getattr(self._job, 'runtime_snapshot', None) if runtime else None
                    if snapshot is None:
                        snapshot = GuardedStore(self._store_root, self.path,
                                                checkpoint=self._storage_checkpoint)
                        if runtime:
                            self._job.runtime_snapshot = snapshot
                            self._job.runtime_binding = binding
                    self._store_job.local_snapshot = snapshot
                    self._store_job.local_active = True
                    try:
                        snapshot.read()  # never silently rebind to a newer file
                        yield
                    finally:
                        del self._store_job.local_snapshot
                        self._store_job.local_active = False
        except BaseException:
            if runtime:
                with self._lock:
                    phase = ('closed' if self._closed.is_set() else
                             'cancelled' if self._cancel.is_set() else 'unknown')
                    self._invalidate_locked(phase)  # stale health is never retained
            raise

    @contextmanager
    def _store_change(self):
        if getattr(self._store_job, "active", False):
            raise ValueError("reentrant recovery store mutation forbidden")
        with self._process_lifecycle.recovery_change_guard(self.path):
            self._store_job.active = True
            try:
                from bot_guarded_store import GuardedStore
                self._store_job.snapshot = GuardedStore(self._process_lifecycle.core.parent, self.path)
                yield
            finally:
                if hasattr(self._store_job, "snapshot"):
                    del self._store_job.snapshot
                self._store_job.active = False

    def _write(self, data: dict) -> None:
        self._require_open()
        if self._process_lifecycle is None:
            return self._write_local(data)
        if getattr(self._store_job, "active", False):
            self._process_lifecycle.validate_recovery_path(self.path)
            return self._write_guarded(data)
        with self._store_change():
            return self._write_guarded(data)

    def _write_guarded(self, data):
        if self._process_lifecycle is None or not getattr(self._store_job, "active", False):
            raise ValueError("guarded recovery writer requires active host guard")
        self._process_lifecycle.validate_recovery_path(self.path)
        self._store_job.snapshot.write(data)

    def _write_local(self, data: dict) -> None:
        if self._process_lifecycle is not None:
            raise ValueError("recovery store access requires the host revocation guard")
        if not getattr(self._store_job, 'local_active', False):
            with self._local_store_change():
                return self._write_local(data)
        with self._lock:
            self._assert_current()
            self._store_job.local_snapshot.write(data)
            if getattr(self._job, "revision", None) is not None:
                self._job.revision = self._revision(data)

    def state(self) -> dict:
        launch_observation = {"httpClientLifecycle": self._http_client_state, "loginLifecycle": self._login_lifecycle_state, "launchAttempted": self._launch_attempted,
            "launchOutcome": ("unattempted" if not self._launch_attempted else
                              "returned" if self._launched_process is not None else "uncertain")}
        if self._closed.is_set():
            # No config/credential read. UNKNOWN is not an offline attestation.
            return dict(**launch_observation, healthSchema='snow-health-v2', phase='closed',
                busy=self._busy, workerAlive=self._worker_alive(),
                accountOnline=False, managementAuthenticated=False,
                healthStatus='unknown', accountEvidence='none', loginCompleted=False,
                qqPath='', username='', targetUin='', hasCredentials=False,
                message='恢复入口已关闭；不代表真实账号离线。')
        if self._process_lifecycle is not None:
            from bot_local_capability import UnavailableRecovery
            return UnavailableRecovery().state()
        with self._lock:
            try:
                data = self._read()
            except (OSError, ValueError):
                self._invalidate_locked("unknown")
                return {**launch_observation, "healthSchema": "snow-health-v2", "loginCompleted": False,
                    "busy": self._busy, "phase": self._phase,
                    "workerAlive": self._worker_alive(),
                        "accountOnline": self._account_online,
                        "managementAuthenticated": self._management_authenticated, "message": "恢复配置读不到，请重新保存设置。",
                        "qqPath": "", "username": "", "targetUin": "",
                        "hasCredentials": False}
            revision = self._revision(data)
            if self._sample_expired and revision != self._expired_revision:
                self._invalidate_locked("unknown")
            if self._health_at is not None:
                if revision != self._health_revision:
                    self._invalidate_locked("unknown")
                elif monotonic() - self._health_at >= self._health_ttl and self._process_choices:
                    self._health_at=None
                    self._management_authenticated=False
                    self._account_online=False
                elif monotonic() - self._health_at >= self._health_ttl:
                    self._invalidate_locked("unknown")
                    self._sample_expired = True
                    self._expired_revision = revision
                    self._message = "管理在线采样已过期（30 秒）；这不代表 QQ 掉线，点击验证接入可重新采样。"
            return {**launch_observation, "healthSchema": "snow-health-v2",
                    "generation": self._generation, "revision": revision,
                    "healthSampleExpired": self._sample_expired,
                    "healthTarget": self._health_target,
                    "healthSampleAgeSeconds": (None if self._health_at is None
                                               else max(0, monotonic() - self._health_at)),
                    "healthStatus": "online" if self._account_online else (
                        "checking" if self._busy and not self._cancel.is_set() else "unknown"),
                    "accountEvidence": "management_process" if self._account_online else "none",
                    "workerAlive": self._worker_alive(),
                    "processChoices": [dict(ticket=t,pid=v['identity']['pid'],
                        executable=v['identity']['image'],created=v['identity']['created_filetime'],
                        target=v['target']) for t,v in self._process_choices.items()
                        if monotonic()<v['expires'] and revision==v['revision']],
                    "loginCompleted": data.get("loginCompleted") is True,
                    "busy": self._busy, "phase": self._phase,
                        "accountOnline": self._account_online,
                        "managementAuthenticated": self._management_authenticated, "message": self._message,
                    "qqPath": data.get("qqPath", ""),
                    "username": data.get("username", "admin"),
                    "targetUin": data.get("targetUin", ""),
                    "hasCredentials": bool(data.get("credential"))}

    def save_identity(self, values: dict) -> None:
        """Experimental metadata-only entry; never validate paths or decrypt.

        Target changes discard primary account/session claims, not host denial.
        Explicit clear also removes the pinned fixed backup under the guard.
        """
        self._require_open()
        if self._process_lifecycle is None:
            raise ValueError('identity settings require the mediated store')
        if (type(values) is not dict or not values
                or set(values) - {'targetUin', 'clearCredentials'}):
            raise ValueError('metadata-only recovery settings required')
        target = values.get('targetUin')
        if 'targetUin' in values:
            if (type(target) is not str or not target.isascii()
                    or not target.isdecimal() or not 1 <= len(target) <= 20
                    or int(target) <= 0):
                raise ValueError('positive bounded account identifier required')
        if 'clearCredentials' in values and type(values['clearCredentials']) is not bool:
            raise ValueError('explicit credential-clear boolean required')
        with self._store_change():
            with self._lock:
                if self._busy:
                    raise ValueError('recovery settings busy')
                data = self._read()
                changed = 'targetUin' in values and target != data.get('targetUin')
                if changed or values.get('clearCredentials') is True:
                    for field in ('credential', 'session', 'loginCompleted'):
                        data.pop(field, None)
                if 'targetUin' in values:
                    data['targetUin'] = target
                self._invalidate_locked()
                self._write(data)
                if values.get('clearCredentials') is True:
                    self._store_job.snapshot.clear_backup()

    def has_management_session(self) -> bool:
        from bot_guarded_store import _object
        with self._local_store_change():
            snapshot = self._store_job.local_snapshot
            return any("session" in _object(raw) for raw in (snapshot.raw, snapshot.backup_raw))

    def clear_management_session(self) -> bool:
        """Discard the local session while preserving the active account settings."""
        with self._local_store_change():
            if self._busy:
                raise ValueError("SnowLuma 正在处理账号操作，请稍后再清除会话")
            snapshot = self._store_job.local_snapshot
            data = snapshot.read()
            removed = data.pop("session", None) is not None
            if removed:
                snapshot.write(data)
            if snapshot.backup_raw is not None:
                from bot_guarded_store import _object
                if "session" in _object(snapshot.backup_raw):
                    snapshot.clear_backup()
                    removed = True
            if removed:
                self._invalidate_locked()
            return removed

    def save(self, values: dict) -> None:
        self._require_open()
        if self._process_lifecycle is None:
            if getattr(self._store_job, 'local_active', False):
                raise ValueError('reentrant local recovery store mutation forbidden')
            return self._save_local(values)
        # Includes old-store read, encryption, backup and clearCredentials cleanup.
        with self._store_change():
            return self._save_local(values)

    def _save_local(self, values: dict) -> None:
        if self._process_lifecycle is None and not getattr(self._store_job, 'local_active', False):
            with self._local_store_change():
                return self._save_local(values)
        if self._process_lifecycle is not None and not getattr(self._store_job, "active", False):
            raise ValueError("recovery store access requires the host revocation guard")
        path = values.get("qqPath", "")
        username = values.get("username", "admin")
        password = values.get("password", "")
        target = values.get("targetUin", "")
        if not all(isinstance(one, str) for one in (path, username, password, target)):
            raise ValueError("恢复设置字段须为文字")
        path, username, target = path.strip(), username.strip(), target.strip()
        if len(path) > 2048 or not 1 <= len(username) <= 128 or len(password) > 1024:
            raise ValueError("恢复设置字段长度不正确")
        if target and (not target.isascii() or not target.isdecimal()
                       or len(target) > 20 or int(target) <= 0):
            raise ValueError("目标账号须为正整数 QQ 号")
        if path and not self.valid_qq_path(path):
            raise ValueError(f"QQ 路径须指向已安装的 {self.qq_image_name}")
        with self._lock:
            if self._busy:
                raise ValueError("正在恢复接入，请结束后再修改设置")
            data = self._read()
            if (values.get("clearCredentials") is True or password
                    or username != data.get("username", "admin")):
                data.pop("loginCompleted", None)
            if values.get("clearCredentials") is True:
                data.pop("credential", None)
                data.pop("session", None)
            if username != data.get("username", "admin"):
                data.pop("credential", None)
                data.pop("session", None)
            if password:
                raw = self.protect(password.encode("utf-8"))
                data["credential"] = base64.b64encode(raw).decode("ascii")
                data.pop("session", None)
            data.update(qqPath=path, username=username, targetUin=target)
            self._invalidate_locked()
            self._write(data)
            if values.get("clearCredentials") is True:
                if self._process_lifecycle is not None:
                    self._store_job.snapshot.clear_backup()
                else:
                    self._store_job.local_snapshot.clear_backup()

    def _secret(self, data: dict, key: str) -> str:
        if self._process_lifecycle is not None:
            raise ValueError("experimental recovery store does not mediate login or process lifecycle")
        if not data.get(key):
            return ""
        return self.protect(base64.b64decode(data[key], validate=True),
                            decrypt=True).decode("utf-8")

    def _say(self, message: str) -> None:
        with self._lock:
            self._assert_current()
            self._message = message

    def connection_config(self) -> dict:
        """Read OneBot settings for one Bot account after management authentication."""
        self._require_open()
        if self._process_lifecycle is not None:
            raise ValueError("experimental recovery store does not import credentials")
        if hasattr(self._job, "generation"):
            raise ValueError("reentrant recovery operation forbidden")
        self._begin_runtime()
        try:
            self._require_trusted_management()
            self._job.store_authorized = True
            self._check_login_lifecycle()
            self._admit_http_client()
            with self._lock:
                self._assert_current()
                data = self._read()
                self._job.revision = self._revision(data)
                target = str(data.get("targetUin") or "")
                if not target.isascii() or not target.isdecimal() or int(target) <= 0:
                    raise ValueError("请先保存明确的目标 QQ 号")
                token = self._secret(data, "session")
                generation = self._generation
            try:
                with self._http_lease.open(self.client) as client:
                    self._require_trusted_management()
                    if token:
                        client.headers["Authorization"] = "Bearer " + token
                        auth = client.get("/api/auth/state")
                        self._assert_current()
                        self._require_trusted_management()
                        if auth.status_code == 401:
                            token = ""
                            client.headers.pop("Authorization", None)
                        elif auth.status_code != 200:
                            raise ValueError("请在 SnowLuma 管理页确认管理登录和条款状态")
                        else:
                            self._management_body(auth, "auth")
                    if not token:
                        self._login_for_config(client, data)
                    response = self._request(client, "GET", "/api/config/" + target)
            finally:
                self._finish_http_client()
                if not self._cancel.is_set():
                    self._commit_login_lifecycle()
            config = response.get("config")
            if not isinstance(config, dict):
                raise ValueError("管理端未返回目标账号配置")
            if self.validate_target is not None:
                self.validate_target(target, config)
            with self._lock:
                current = self._read()
                if (generation != self._generation or self._cancel.is_set()
                        or self._revision(current) != self._revision(data)):
                    raise ValueError("配置已改变或恢复已取消，请重新导入")
                if current.get("session") == data.get("session"):
                    current["loginCompleted"] = True
                    self._write(current)
                self._auth_sample(data)
            return config
        except RecoveryCancelled:
            raise ValueError("配置已改变或恢复已取消，请重新导入") from None
        except NeedsAction as exc:
            raise ValueError(str(exc)) from None
        except httpx.HTTPError:
            raise ValueError("无法读取协议配置，请检查 SnowLuma 管理端和目标 QQ") from None
        finally:
            self._end_http_runtime()

    def _login_for_config(self, client: httpx.Client, data: dict) -> None:
        password = self._secret(data, "credential")
        if not password:
            raise NeedsAction("请先保存 SnowLuma 管理凭据和目标 QQ 号，再同步协议配置。")
        login = self._request(client, "POST", "/api/login", json={
            "username": data.get("username", "admin"), "password": password})
        if login.get("needsTotp"):
            raise NeedsAction("SnowLuma 管理端要求二次验证，请在管理页处理。")
        token = login["token"]
        client.headers["Authorization"] = "Bearer " + token
        data["session"] = base64.b64encode(self.protect(token.encode("utf-8"))).decode("ascii")
        with self._lock:
            self._assert_current()
            self._write(data)
            self._job.login_saved_revision = self._revision(data)

    def _offer_process_choices(self, rows, data):
        choices={}
        for row in rows[:16]:
            uin=str(row.get('uin') or '')
            if uin not in ('','0') or row.get('status')=='online':continue
            pid=row.get('pid')
            if type(pid) is not int or not 0<pid<=4194304:continue
            try:identity=self._choice_identity(pid,data)
            except NeedsAction:continue
            choices[uuid.uuid4().hex]=dict(identity=identity,generation=self._generation,
                revision=self._revision(data),target=str(data.get('targetUin') or ''),expires=monotonic()+120)
        self._require_trusted_management()
        with self._lock:
            self._assert_current()
            self._process_choices=choices
        return bool(choices)

    def _choice_identity(self,pid,data):
        self._require_trusted_management()
        seen=self._qq_process_probe(pid)
        self._require_trusted_management()
        try:
            if type(seen) is not dict or seen.get('state')!='alive' or seen.get('pid')!=pid:raise ValueError()
            identity=_identity({k:seen.get(k) for k in ('pid','created_filetime','image')})
            image=Path(identity['image']);expected=data.get('qqPath')
            if image.name.lower()!=self.qq_image_name or (expected and image.resolve()!=Path(expected).resolve()):raise ValueError()
            return identity
        except (ValueError,TypeError,OSError):raise NeedsAction('候选 QQ 原生身份无法核验；未加载。') from None

    def confirm_process(self,ticket):
        # The operator selects an opaque, in-memory, one-use ticket, NOT a PID.
        self._require_open()
        with self._lifecycle:
            with self._lock:
                if self._busy or self._worker_alive():raise ValueError('恢复尚未结束，请稍候确认')
                choice=self._process_choices.pop(ticket,None) if type(ticket) is str else None
                if choice is None:raise ValueError('进程确认已失效或使用过，请重新点击连接或恢复')
                if monotonic()>=choice['expires'] or choice['generation']!=self._generation:
                    raise ValueError('进程确认已过期，请重新获取候选')
                self._require_trusted_management()
                data=self._read()
                if choice['revision']!=self._revision(data) or choice['target']!=str(data.get('targetUin') or ''):
                    raise ValueError('账号或配置已改变；未加载')
                if self._choice_identity(choice['identity']['pid'],data)!=choice['identity']:
                    raise ValueError('QQ 进程已更换；未加载')
                if (self._cancel.is_set() or choice['generation']!=self._generation
                        or monotonic()>=choice['expires'] or choice['revision']!=self._revision(self._read())):
                    raise ValueError('确认期间状态已变化；未加载，请重新获取候选')
                self.start(_confirmation=choice)

    def start(self, *, _confirmation=None) -> None:
        self._require_open()
        if self._process_lifecycle is not None:
            raise ValueError("experimental recovery store does not mediate login or process lifecycle")
        if not self._lifecycle.acquire(blocking=False):
            raise ValueError("recovery lifecycle operation busy")
        try:
            with self._lock:
                self._require_open()
                if self._busy or self._worker_alive():
                    raise ValueError("恢复任务尚未退出，请等待停止完成")
                self._invalidate_locked("checking")
                self._busy = True
                self._cancel.clear()
                self._message = "正在检测 QQ 和管理会话…"
                self._thread = Thread(target=self._run, args=(_confirmation,), daemon=True, name="snowluma-recovery")
                self._thread.start()
        finally:
            self._lifecycle.release()

    def cancel(self) -> None:
        if self._process_lifecycle is not None:
            return
        with self._lifecycle:
            with self._lock:
                self._cancel.set()
                self._invalidate_locked("stopping")
                thread = self._thread
                active = self._active_job
            # One bounded budget, no state lock while waiting. Direct imports/
            # runs complete via Event; never join their unrelated caller thread.
            deadline = monotonic() + 5
            if active is not None and active is not current_thread():
                if not self._job_done.wait(timeout=max(0, deadline - monotonic())):
                    raise RuntimeError("恢复任务尚未退出，停止未完成；禁止开始新恢复")
            if thread is not None and thread is not current_thread():
                thread.join(timeout=max(0, deadline - monotonic()))
                if thread.is_alive():
                    raise RuntimeError("恢复任务尚未退出，停止未完成；禁止开始新恢复")
            with self._lock:
                self._phase = "closed" if self._closed.is_set() else "cancelled"

    def _require_trusted_management(self) -> None:
        self._require_open()
        if self._process_lifecycle is not None:
            raise ValueError("experimental recovery store does not mediate management requests")
        # The trust predicate may itself cancel/supersede the operation. A true
        # result must never authorize credentials or a load for an old revision.
        self._assert_current()
        trusted = self.trusted_management()
        self._assert_current()
        if trusted is not True:
            raise NeedsAction("管理端归属未核验或已变化；已停止后续操作，须先人工确认可信端点。")

    @staticmethod
    def _management_body(response: httpx.Response, kind: str) -> dict:
        # Public readiness is separate from protected auth-state evidence.
        if response.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            raise NeedsAction("管理接口格式不符；未继续自动恢复。")
        try:
            value = response.json()
        except ValueError:
            raise NeedsAction("管理接口格式不符；未继续自动恢复。") from None
        if not isinstance(value, dict):
            raise NeedsAction("管理接口格式不符；未继续自动恢复。")
        if kind == "public":
            valid = isinstance(value.get("appearance"), dict)
        elif kind == "auth":
            valid = type(value.get("mustChangePassword")) is bool
        else:
            valid = (value.get("needsTotp") is True or (
                value.get("success") is True and isinstance(value.get("token"), str)
                and bool(value["token"]) and type(value.get("mustChangePassword")) is bool))
        if not valid:
            raise NeedsAction("管理接口格式不符；未继续自动恢复。")
        if value.get("mustChangePassword") is True:
            raise NeedsAction("请先在 SnowLuma 修改初始密码，再保存新的管理凭据。")
        return value

    def _pin_qq_target(self, pid):
        """Read-only native identity around remote mutations, NOT process ownership.

        Per-query handle snapshots detect identity changes; the remote PID API
        is not atomic with this check. A post-effect mismatch is uncertain.
        """
        if type(pid) is not int or not 0 < pid <= 4194304:
            raise NeedsAction("目标 QQ 进程号无效；未继续接入。")
        if not hasattr(self._job, "revision"):
            raise RecoveryCancelled()
        self._require_trusted_management()
        try:
            seen = self._qq_process_probe(pid)  # ONLY the selected PID, no census
        except RecoveryCancelled:
            raise
        except Exception:
            self._require_trusted_management()  # a failing probe cannot hide cancellation
            raise NeedsAction("无法核验目标 QQ 原生身份；未继续接入操作。") from None
        self._require_trusted_management()
        try:
            if type(seen) is not dict or seen.get("state") != "alive" or seen.get("pid") != pid:
                raise ValueError()
            identity = _identity({k: seen.get(k) for k in ("pid", "created_filetime", "image")})
            image = Path(identity["image"])
            expected = self._read().get("qqPath")
            if image.name.casefold() != self.qq_image_name or (expected and (
                    type(expected) is not str or image.resolve() != Path(expected).resolve())):
                raise ValueError()
        except (ValueError, TypeError, OSError):
            raise NeedsAction("目标 QQ 原生身份不符或不可用；未继续接入操作。") from None
        self._require_trusted_management()
        with self._lock:
            self._assert_current()
            bound = getattr(self._job, "qq_identity", None)
            if bound is not None and bound != identity:
                raise NeedsAction("目标 QQ 原生身份已变化；先前操作结果不确定，未继续接入。")
            self._job.qq_identity = identity

    def mutation_store(self, target):
        return self.path

    def _record_process_mutation(self, operation):
        identity = getattr(self._job, "qq_identity", None)
        owner = getattr(self._job, "mutation_owner", None)
        if identity is None or owner is None:
            raise RecoveryCancelled()
        if operation == "load" and not getattr(self._job, "refresh_confirmed", False):
            raise NeedsAction("本恢复任务尚无可信刷新响应；未执行加载。")
        if operation == "refresh" and self._management_instance is not None:
            # Reviewed/pinned upstream refresh ONLY observes existing hook pipes;
            # it never injects, unloads, logs into QQ or sends QQ messages. Keep
            # all historical denial records; actual load remains journal-guarded.
            self._management_resource()
            self._require_trusted_management()
            return
        after_observation = operation == "load" and self._management_instance is not None
        if after_observation:
            self._management_resource()
            self._require_trusted_management()
        from bot_recovery_mutation_journal import MutationJournal
        target = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        def checkpoint():
            # No config read/nested identity lock while holding journal mutex.
            if (self._closed.is_set() or self._cancel.is_set()
                    or getattr(self._job, "generation", None) != self._generation):
                raise RecoveryCancelled()
        self._assert_current()
        try:
            if self._mutation_root is not None:
                # Same native identity across Bot stores shares ONE fixed slot.
                # Publish shared denial first. Partial local publication never
                # rolls this back or grants retry; both must finish before HTTP.
                shared = self._mutation_root / ('qq-target-' + target)
                MutationJournal(self._store_root,shared).record(owner,target,operation,checkpoint,after_observation=after_observation)
            MutationJournal(self._store_root,self.mutation_store(target)).record(owner,target,operation,checkpoint,after_observation=after_observation)
        except RecoveryCancelled:
            raise
        except Exception:
            self._require_trusted_management()
            raise NeedsAction("存在未完成或不可核验的 QQ 操作记录；拒绝重复刷新/加载，需可信宿主核验。") from None
        self._require_trusted_management()  # failed/cancelled record never authorizes HTTP

    @contextmanager
    def _operator_confirmation(self, mutation, method, path):
        choice = getattr(self._job, 'confirmed_process', None)
        if choice is None or mutation is None or method.upper() != 'POST':
            yield {}
            return
        from secrets import token_hex
        from time import time
        from bot_private_file import open_private_new
        from runtime_identity import safe_path
        pid = int(mutation.group(1))
        data = self._read()
        if (choice['identity'] != getattr(self._job, 'qq_identity', None)
                or choice['identity']['pid'] != pid or choice['target'] != data.get('targetUin')
                or choice['revision'] != self._revision(data)):
            raise NeedsAction('确认的账号或 QQ 进程已改变；未执行操作。')
        self._assert_current()
        self._require_trusted_management()
        proof = safe_path(self._store_root, self.path.with_name(self.path.name+'.process-confirmation.json'))
        nonce = token_hex(32)
        row = dict(version=1, target=choice['target'], pid=pid,
                   created=str(choice['identity']['created_filetime']), image=choice['identity']['image'],
                   operation=path.rsplit('/',1)[1], nonce=nonce, expires=int(time()*1000)+15000)
        raw = json.dumps(row, sort_keys=True).encode()
        with open_private_new(proof) as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        try:
            self._assert_current()
            yield {'X-Core-Process-Confirmation': nonce}
        finally:
            # Only our unchanged proof; never remove another job's confirmation.
            if proof.exists() and proof.read_bytes() == raw: proof.unlink()

    def _request(self, client: httpx.Client, method: str, path: str, **kwargs) -> dict:
        self._assert_current()
        self._require_trusted_management()
        if type(method) is not str or method.upper() not in ("GET", "POST") or type(path) is not str:
            raise NeedsAction("不支持的恢复请求；未执行操作。")
        mutation = re.fullmatch(r"/api/processes/([1-9][0-9]{0,9})/(?:refresh|load)", path)
        if method.upper() == "POST" and path != "/api/login" and mutation is None:
            raise NeedsAction("不支持的恢复变更路径；未执行操作。")
        if method.upper() == "POST" and path == "/api/login":
            self._begin_login_lifecycle()
        if method.upper() == "POST" and mutation:
            self._pin_qq_target(int(mutation.group(1)))
            self._record_process_mutation(path.rsplit("/",1)[1])
        elif hasattr(self._job, "qq_identity"):
            self._pin_qq_target(self._job.qq_identity["pid"])
        with self._operator_confirmation(mutation, method, path) as proof_headers:
            if proof_headers:
                kwargs['headers'] = {**kwargs.get('headers', {}), **proof_headers}
            result = client.request(method, path, **kwargs)
        self._assert_current()
        if hasattr(self._job, "qq_identity"):
            self._pin_qq_target(self._job.qq_identity["pid"])
        if result.status_code == 401:
            raise NeedsAction("管理凭据或登录态无效，请重新确认；未回显管理端内容。")
        if result.status_code == 403:
            try:
                gate = result.json()
            except ValueError:
                gate = {}
            if isinstance(gate, dict) and gate.get("consentRequired") is True:
                raise NeedsAction("请先在 SnowLuma 阅读并确认协议与隐私条款，再点击恢复。")
            if isinstance(gate, dict) and gate.get("mustChangePassword") is True:
                raise NeedsAction("请先在 SnowLuma 修改初始密码，再保存新的管理凭据。")
        result.raise_for_status()
        self._require_trusted_management()
        if path == "/api/login":
            return self._management_body(result, "login")
        value = result.json()
        if not isinstance(value, dict):
            raise ValueError("管理端返回格式不正确")
        if mutation and method.upper() == "POST" and path.endswith("/refresh") and value.get("success") is True:
            self._job.refresh_confirmed = True
        return value

    def _run(self, confirmation=None) -> None:
        try:
            if confirmation is None:self.run()
            else:self.run(_confirmation=confirmation)
        except RecoveryCancelled:
            pass
        except NeedsAction as exc:
            if not self._cancel.is_set():
                self._say(str(exc))
        except Exception as exc:
            import traceback
            import sys
            locations = [f"{Path(frame.filename).name}:{frame.lineno}" for frame in traceback.extract_tb(exc.__traceback__)]
            print("[SL recovery] " + type(exc).__name__ + " " + " > ".join(locations), file=sys.stderr, flush=True)
            # Never expose passwords, bearer tokens or upstream response bodies.
            if not self._cancel.is_set():
                self._say("接入恢复未完成；请检查 QQ、SnowLuma 状态及保存的管理凭据。")
        finally:
            with self._lock:
                self._busy = False
                if self._closed.is_set():
                    self._phase = "closed"
                elif self._cancel.is_set():
                    self._update(phase="cancelled")
                elif self._phase != "online":
                    self._update(phase="needs_action")

    def run(self, *, _confirmation=None) -> None:
        self._require_open()
        if self._process_lifecycle is not None:
            raise ValueError("experimental recovery store does not mediate login or process lifecycle")
        if self._cancel.is_set():
            return
        if hasattr(self._job, "generation"):
            raise ValueError("reentrant recovery operation forbidden")
        self._begin_runtime()
        if _confirmation is not None:self._job.confirmed_process=_confirmation
        try:
            # Wait only on the original HOST's native resource; no credentials,
            # HTTP client, QQ discovery or account reads before listener ownership.
            self._await_owned_management()
            # Gate before the first config read or QQ process enumeration.
            self._require_trusted_management()
            self._job.store_authorized = True
            with self._lock:
                self._assert_current()
                self._job.revision = self._revision(self._read())
                if _confirmation is not None and (_confirmation['revision']!=self._job.revision
                        or monotonic()>=_confirmation['expires']):
                    raise NeedsAction('启动确认任务前配置已变化或确认已过期；未执行接入。')
            self._check_login_lifecycle()
            self._admit_http_client()
            self._reserve_configured_account()
            try:self._recover()
            except BaseException:
                if (self._management_instance is not None and hasattr(self._job,'login_saved_revision')
                        and self._login_lifecycle_state=='pending' and not self._cancel.is_set()):
                    # Commit known admin login after HTTP drain, before the
                    # later QQ failure invalidates this task's generation.
                    self._finish_http_client()
                    if self._http_lease is None and not self._http_cleanup_failed:
                        self._commit_login_lifecycle()
                raise
            self._finish_http_client()
            self._commit_login_lifecycle()
            self._publish_drained_samples()
        except RecoveryCancelled:
            pass
        except NeedsAction as exc:
            with self._lock:
                self._assert_current()
                self._invalidate_locked("needs_action")
                self._message = str(exc)
        finally:
            self._end_http_runtime()

    def _admit_http_client(self):
        from bot_recovery_http_lease import RecoveryClientLease
        self._require_trusted_management()
        with self._lock:
            self._account_online = self._management_authenticated = False
            self._health_at = self._health_revision = None
            self._health_target = ''
        if self._http_lease is not None:
            self._http_client_state='pending'
            raise NeedsAction('先前管理客户端未确认排空；不重新连接。')
        self._http_client_state='pending'
        try:
            self._http_lease=RecoveryClientLease(self._store_root,self.path,
                self._job.mutation_owner,self._login_checkpoint)
        except OSError:raise
        except RecoveryCancelled:raise
        except Exception:
            raise NeedsAction('管理客户端生命周期未确认；未继续枚举、解密或请求，需可信宿主核验。') from None
        self._http_client_state='active'

    def _finish_http_client(self):
        lease=self._http_lease
        if lease is None:return
        if self._http_cleanup_failed:
            self._http_client_state='pending'
            return
        try:
            if not lease.attempted:lease.release_unused()
        except BaseException:
            self._http_cleanup_failed=True
            self._http_client_state='pending'
            raise
        if lease.released:
            self._http_lease=None
            self._http_client_state='drained'
        else:
            self._http_client_state='pending'
            # Retain exact client on this facade; never retry close or release.
            with self._lock:
                self._account_online=self._management_authenticated=False
                self._health_at=self._health_revision=None
                self._health_target=''

    def assert_client_drained(self):
        # Called by the owning facade's cleanup after close/cancel. No config IO,
        # no retry/forget and no account/host offline claim.
        if self._http_lease is not None:
            error=ValueError('Recovery HTTP client lifetime unresolved; exact resource retained')
            error._recovery_http_lease=self._http_lease
            raise error
        if getattr(self,'_parent_work_active',False):
            error=ValueError('parent account work drain unresolved; exact lifecycle retained')
            error._parent_account_lifecycle=self._account_lifecycle
            raise error

    def _end_http_runtime(self):
        import sys
        primary=sys.exception()
        try:
            self._finish_http_client()
            if self._parent_work_active:
                if self._http_lease is not None:raise ValueError("local client not drained")
                if self._launch_attempted or self._launched_process is not None:
                    from bot_parent_qq_launch import ParentQQProcess
                    p=self._launched_process
                    if type(p) is not ParentQQProcess or p.publisher is not self._account_lifecycle or p.retired:
                        raise ValueError('QQ launch remains retained; parent work not drained')
                    # Distinct ACK: client work ended, QQ remains owned, NOT drained.
                    self._account_lifecycle.acknowledge_account_work_finished()
                else:self._account_lifecycle.acknowledge_account_work_drained()
                self._parent_work_active=False
        except BaseException as cleanup:
            if primary is None:raise
            primary._recovery_http_lease=self._http_lease
            primary.add_note('Recovery HTTP lease cleanup unconfirmed: '+type(cleanup).__name__)
        finally:self._end_runtime()

    def _publish_drained_samples(self):
        if self._http_lease is not None or self._login_lifecycle_state=='pending':return
        self._require_trusted_management()
        data=self._read()
        if getattr(self._job,'deferred_auth',False):self._auth_sample(data)
        if hasattr(self._job,'deferred_online'):self._online(data,self._job.deferred_online)

    def _login_checkpoint(self):
        # No config read/nested identity lock while the fixed ledger lock is held.
        if (self._closed.is_set() or self._cancel.is_set()
                or getattr(self._job, 'generation', None) != self._generation):
            raise RecoveryCancelled()

    def _await_owned_management(self):
        if self._management_instance is None:return
        from snowluma_connection import management_listener_owners
        deadline=monotonic()+10
        while True:
            self._assert_current()
            resource=self._management_resource()[0]
            rows=management_listener_owners(self.management_port)
            if rows==[dict(address='127.0.0.1',pid=resource.pid)]:return
            if rows!=[]:
                raise NeedsAction('管理端口被其他进程占用或不可核验；未读取凭据或连接。')
            if monotonic()>=deadline:
                raise NeedsAction('本 Bot 管理端口尚未就绪；本次恢复结束，未读取凭据或连接。')
            if self._cancel.wait(.1):raise RecoveryCancelled()

    def _management_resource(self):
        if self._management_instance is None:return None
        from bot_management_resource import owned_management_resource
        resource=owned_management_resource(self._management_instance,self._account_lifecycle)
        previous=getattr(self._job,'management_resource',None)
        if previous is not None and (previous[0] is not resource[0] or previous[1]!=resource[1]):
            raise RecoveryCancelled()
        self._job.management_resource=resource
        return resource

    def _login_ledger(self):
        from bot_login_lifecycle import LoginLifecycle
        resource=self._management_resource()
        return LoginLifecycle(self._store_root,self.path if resource is None else resource[1])

    def _check_login_lifecycle(self):
        self._require_trusted_management()
        try:self._login_ledger().check(self._login_checkpoint)
        except RecoveryCancelled:raise
        except OSError:
            self._login_lifecycle_state='pending'
            with self._lock:self._invalidate_locked('needs_action')
            raise  # preserve the original storage failure; worker hides details
        except Exception:
            self._login_lifecycle_state='pending'
            raise NeedsAction('存在未确认的管理登录或记录不可核验；未继续账号操作，不盲目重试，需可信宿主对账。') from None
        self._login_lifecycle_state='clear'
        self._require_trusted_management()

    def _begin_login_lifecycle(self):
        self._require_trusted_management()
        if hasattr(self._job,'login_intent'):
            raise NeedsAction('本恢复任务已尝试管理登录；未重复发送。')
        self._login_lifecycle_state='pending'  # before any file/network effect
        with self._lock:
            self._account_online = self._management_authenticated = False
            self._health_at = self._health_revision = None
            self._health_target = ''
        revision=self._revision(self._read())
        try:
            nonce=self._login_ledger().begin(self._job.mutation_owner,revision,self._login_checkpoint)
        except RecoveryCancelled:raise
        except OSError:
            self._login_lifecycle_state='pending'
            with self._lock:self._invalidate_locked('needs_action')
            raise  # preserve the original storage failure; worker hides details
        except Exception:
            raise NeedsAction('管理登录记录未获确认；未发送新的登录请求，需可信宿主对账。') from None
        self._job.login_intent,self._job.login_revision=nonce,revision
        self._require_trusted_management()

    def _commit_login_lifecycle(self):
        if not hasattr(self._job,'login_intent'):return
        saved=getattr(self._job,'login_saved_revision',None)
        if saved is None:return  # denied/TOTP/unknown response remains pending
        self._require_trusted_management()
        if saved!=self._revision(self._read()):
            raise NeedsAction('登录态写入后配置已变化；未确认本次管理登录完成。')
        try:
            self._login_ledger().commit(self._job.login_intent,self._job.mutation_owner,
                self._job.login_revision,self._login_checkpoint)
        except RecoveryCancelled:raise
        except OSError:
            self._login_lifecycle_state='pending'
            with self._lock:self._invalidate_locked('needs_action')
            raise  # preserve the original storage failure; worker hides details
        except Exception:
            raise NeedsAction('管理登录提交未获确认；保留未决记录，不自动重试。') from None
        # _recover returned only AFTER its HTTP context exited successfully.
        self._login_lifecycle_state='committed'
        self._require_trusted_management()


    def _reserve_configured_account(self):
        """Configured intent only, not verified QQ account/host ownership.

        Default QQControl supplies one fixed core namespace. Do this before
        QQ enumeration/launch, client construction or credential decryption.
        No release on cancel, success, setting changes or public online claims.
        """
        if self._account_root is None:
            return  # standalone explicit Recovery has no cross-store namespace
        self._require_trusted_management()
        data = self._read()
        def checkpoint():
            if (self._closed.is_set() or self._cancel.is_set()
                    or getattr(self._job, 'generation', None) != self._generation):
                raise RecoveryCancelled()
        try:
            from bot_configured_account import ConfiguredAccountReservation
            ConfiguredAccountReservation(self._store_root,self._account_root).reserve(
                data.get('targetUin'),self.path,checkpoint)
        except RecoveryCancelled:
            raise
        except Exception:
            self._require_trusted_management()
            raise NeedsAction('目标账号未明确、已被其他恢复 store 预留或记录不可核验；未启动登录/接入，需可信宿主对账。') from None
        self._require_trusted_management()

    def _launch_once(self, path):
        """Record before effect; never retry an uncertain default QQ launch.

        A returned Popen is retained, not an account/host offline receipt. Even
        its exit cannot prove all QQ descendants are gone. No stop/adoption here.
        """
        if not hasattr(self._job, "revision"):
            raise RecoveryCancelled()
        self._require_trusted_management()
        with self._lock:
            self._assert_current()
            if self._launch_attempted:
                raise NeedsAction("本恢复实例已尝试唤起 QQ；结果未获完整生命周期确认，不会重复启动。请手动确认 QQ 状态。")
            self._launch_attempted = True
        # Callback may raise or cancel after a side effect. Preserve the latch;
        # if it returns after cancellation, retain its result BEFORE rechecking.
        process = (self._account_lifecycle.start_qq() if self._account_lifecycle is not None
                   else self.launch(path))
        with self._lock:
            self._launched_process = process
        self._require_trusted_management()

    def allow_unidentified_process(self, pid, target):
        return False

    def _recover(self) -> None:
        if not hasattr(self._job, "revision"):
            raise RecoveryCancelled()
        self._require_trusted_management()
        deadline = monotonic() + 90
        data = self._read()
        self._require_trusted_management()
        rows = self.processes()
        self._require_trusted_management()
        if rows is None:
            self._say("无法可靠检测 QQ 主进程，已停止自动唤起；请手动确认 QQ。")
            return
        self._require_trusted_management()
        if self.learn_process_path and len(rows) == 1 and not data.get("qqPath"):
            learned = Path(str(rows[0].get("ExecutablePath") or ""))
            if learned.name.lower() == self.qq_image_name and learned.is_file():
                data["qqPath"] = str(learned.resolve())
                with self._lock:
                    self._write(data)
        if not rows:
            path = data.get("qqPath")
            if not path:
                self._require_trusted_management()
                path = self.discover()
                self._require_trusted_management()
            if not path:
                self._say(f"未检测到 QQ，请先启动 {self.qq_image_name} 并登录，再点击恢复接入。")
                return
            if self._cancel.is_set():
                return
            self._require_trusted_management()
            self._launch_once(path)
            self._require_trusted_management()
            self._say("已尝试唤起 QQ；需要扫码时请在 QQ 窗口完成登录。")
        self._require_trusted_management()
        with self._http_lease.open(self.client) as client:
            self._require_trusted_management()
            ready = False
            for _ in range(40):
                if self._cancel.is_set():
                    return
                if monotonic() >= deadline:
                    break
                try:
                    self._assert_current()
                    self._require_trusted_management()
                    response = client.get("/api/ui/public")
                    self._assert_current()
                    ready = response.status_code == 200
                    if ready:
                        self._management_body(response, "public")
                        self._require_trusted_management()
                except httpx.HTTPError:
                    pass
                if ready:
                    break
                if self._cancel.wait(0.5):
                    return
            if not ready:
                self._say("SnowLuma 管理端启动超时；不会反复启动或注入 QQ。")
                return
            self._say("正在验证当前 Bot 的管理凭据…")
            self._update(phase="authenticating")
            token = self._secret(data, "session")
            if token:
                client.headers["Authorization"] = "Bearer " + token
                self._require_trusted_management()
                response = client.get("/api/auth/state")
                self._assert_current()
                self._require_trusted_management()
                if response.status_code != 200:
                    token = ""
                else:
                    self._management_body(response, "auth")
            if not token:
                client.headers.pop("Authorization", None)
                password = self._secret(data, "credential")
                if not password:
                    self._say("QQ 已检查；请首次保存管理凭据，之后可自动恢复登录与接入。")
                    return
                if self._cancel.is_set():
                    return
                login = self._request(client, "POST", "/api/login", json={
                    "username": data.get("username", "admin"), "password": password})
                if login.get("needsTotp"):
                    self._say("管理端需要二次验证，请在 SnowLuma 页面完成；不会绕过验证。")
                    return
                token = login.get("token")
                if login.get("success") is not True or not isinstance(token, str) or not token:
                    self._say("管理登录失败，请重新保存凭据；本次不会重复尝试登录。")
                    return
                if login.get("mustChangePassword"):
                    self._say("请先在 SnowLuma 修改初始密码，再保存新的管理凭据。")
                    return
                client.headers["Authorization"] = "Bearer " + token
                data["session"] = base64.b64encode(
                    self.protect(token.encode("utf-8"))).decode("ascii")
                with self._lock:
                    self._write(data)
                    self._job.login_saved_revision = self._revision(data)
            self._auth_sample(data)
            with self._lock:
                data["loginCompleted"] = True
                self._write(data)
            self._update(phase="validating")
            self._say("管理鉴权通过，正在检查目标账号与 OneBot 端口…")
            acted: set[int] = set()
            selected_pid: int | None = None
            target = data.get("targetUin", "")
            if self.validate_target is not None:
                if not target:
                    raise NeedsAction("请先保存当前 Bot 明确的目标 QQ 号；未执行注入。")
                config = self._request(client, "GET", "/api/config/" + str(target)).get("config")
                self.validate_target(str(target), config)
            self._update(phase="connecting")
            self._say("目标配置检查通过，正在定位并接入目标 QQ…")
            for _ in range(60):
                if self._cancel.is_set():
                    return
                if monotonic() >= deadline:
                    break
                listing = self._request(client, "GET", "/api/processes").get("list", [])
                if not isinstance(listing, list):
                    raise ValueError("进程列表格式不正确")
                candidates = [row for row in listing if isinstance(row, dict)]
                if len(candidates) > 16:
                    self._say("QQ 候选进程过多，请在管理页手动选择；未自动注入。")
                    return
                confirmation=getattr(self._job,'confirmed_process',None)
                if confirmation is not None:
                    if confirmation['target']!=str(target) or confirmation['revision']!=self._revision(data):
                        raise NeedsAction('确认后账号配置已变化；未加载，请重新确认。')
                    pid=confirmation['identity']['pid']
                    if self._choice_identity(pid,data)!=confirmation['identity']:
                        raise NeedsAction('确认的 QQ 进程已改变；未继续加载。')
                    selected=[row for row in candidates if row.get('pid')==pid]
                    if len(selected)!=1:raise NeedsAction('确认的 QQ 已退出或不再唯一；未继续加载。')
                    actual=str(selected[0].get('uin') or '')
                    if actual not in ('','0',str(target)) or (selected[0].get('status')=='online' and actual!=str(target)):
                        raise NeedsAction('加载后实际 QQ 账号与目标不符，已停止接入；未切换其他进程，请人工检查已选 QQ。')
                    if actual in ('','0') and pid not in acted:
                        checked=self._request(client,'GET',f'/api/processes/{pid}/probe-login').get('info')
                        observed=str(checked.get('uin') or '') if isinstance(checked,dict) else ''
                        if observed not in ('','0',str(target)):
                            raise NeedsAction('确认的进程现已识别为其他账号；未加载，未切换进程。')
                    candidates=selected
                elif target:
                    matched = [row for row in candidates if str(row.get("uin")) == target]
                    unresolved=[]
                    if not matched:
                        for row in candidates:
                            if self._cancel.is_set():
                                return
                            if monotonic() >= deadline:
                                self._say("等待账号确认超时，请在 QQ 登录后再次点击恢复。")
                                return
                            pid = row.get("pid")
                            if type(pid) is not int or not 0 < pid <= 4194304:
                                continue
                            if (str(row.get("uin") or "") in ("", "0")
                                    and self.allow_unidentified_process(pid, target)):
                                matched.append(row)
                                continue
                            info = self._request(client, "GET",
                                                 f"/api/processes/{pid}/probe-login").get("info")
                            if isinstance(info, dict) and str(info.get("uin")) == target:
                                matched.append(row)
                            elif not isinstance(info,dict) or str(info.get('uin') or '') in ('','0'):
                                unresolved.append(row)
                    if not matched and self._offer_process_choices(unresolved,data):
                        self._say('无法自动识别目标账号；请在本页核对 QQ 主进程并确认加载一次，随后会核验实际账号。')
                        return
                    candidates = matched
                if len(candidates) > 1:
                    self._say("检测到多个候选 QQ 主进程，请填写目标账号或在管理页选择；未自动注入。")
                    return
                if candidates:
                    row = candidates[0]
                    pid = row.get("pid")
                    if type(pid) is not int or not 0 < pid <= 4194304:
                        raise ValueError("进程号不正确")
                    if selected_pid is not None and selected_pid != pid:
                        self._say("QQ 主进程已更换，请确认目标后再次点击恢复；未自动注入新进程。")
                        return
                    selected_pid = pid
                    status = row.get("status")
                    if status == "online":
                        # Initial already-online management rows bypass refresh/load.
                        # Pin their selected native target too; a public row cannot
                        # authorize health for an unknown/reused/non-QQ PID. _online
                        # rechecks the exact identity before publishing the sample.
                        if not hasattr(self._job, "qq_identity"):
                            self._pin_qq_target(pid)
                        self._online(data, row)
                        self._say("QQ 已在线；已复用现有接入，无需重复登录或注入。")
                        return
                    if pid not in acted and status not in ("loading", "connecting"):
                        if self._cancel.is_set():
                            return
                        # Refresh first: a hook can survive a SnowLuma restart
                        # before its initial watcher snapshot has caught up.
                        result = self._request(client, "POST",
                                               f"/api/processes/{pid}/refresh")
                        acted.add(pid)
                        if result.get("success") is not True:
                            self._say("QQ 接入操作失败；已停止重复注入，请查看 SnowLuma。")
                            return
                        refreshed = result.get("process")
                        if (not row.get("injected") and status != "loaded"
                                and isinstance(refreshed, dict)
                                and refreshed.get("injected") is False
                                and refreshed.get("status") == "available"):
                            if self._cancel.is_set():
                                return
                            self._say("请通过 QQ 连接服务完成账号接入，再检测连接。")
                            return
                    self._say("正在等待 QQ 登录及协议接入；不会重复加载同一进程。")
                else:
                    self._say("等待目标 QQ 主进程或账号登录；不会注入未确认的其他账号。")
                if self._cancel.wait(1):
                    return
            self._say("等待 QQ 接入超时；请完成 QQ 登录后手动点击恢复接入。")
