"""Local-only QQ Infinity settings and owned SnowLuma/TTS subprocesses."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import uuid
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock, Event
from time import monotonic
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from bot_local_capability import (UnavailableInstaller, UnavailableRecovery,
                                  MediatedRecoverySettings,
                                  require_local_runtime)
from qq_environment import read_qq_environment
from asr_install import ASRInstaller
from tts_install import TTSInstaller
from qq_voice_services import ASRSession, tts_port, external_unit
from deployment_relocation import effective_asr_directory
from voice_setup import ASR_DEFAULT_THREADS, ASR_FILES, check_asr
from snowluma_recovery import Recovery, NeedsAction
from bot_private_file import open_private_new
from snowluma_connection import connection_settings, management_listener_owners
from snowluma_process import VerifiedProcess, discover as discover_snowluma


QQ_NAME = "core-infinity-qq"
TTS_URL = "http://127.0.0.1:18084"
_SENSITIVE = re.compile(r"(?i)(token|password|authorization|cookie)")
ASR_LANGUAGES = ("auto", "zh", "en")
ASR_THREADS = (2, 3)
TTS_SEND_MODES = ("on_demand", "reply_with_voice")
DEFAULT_PREVIEW_TEXT = "你好，这是一段语音试听。让我们听听当前音色的效果。"


def _open_port(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _names(parent: Path, required: tuple[str, ...]) -> list[str]:
    if not parent.is_dir():
        return []
    return sorted(one.name for one in parent.iterdir()
                  if not one.name.startswith(".") and one.is_dir() and all((one / file).is_file() for file in required))


def _open_folder(folder: Path) -> None:
    """Open a trusted local directory in the platform's file manager."""
    if os.name == "nt":
        os.startfile(str(folder))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open",
                          str(folder)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def _qq_ids(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} 必须是 QQ 号数组")
    ids: set[str] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            raise ValueError(f"{field} 只接受正整数 QQ 号")
        said = str(item)
        if (not said.isascii() or not said.isdecimal() or not 0 < len(said) <= 20
                or int(said) <= 0):
            raise ValueError(f"{field} 只接受正整数 QQ 号")
        ids.add(str(int(said)))
    return sorted(ids, key=int)


def _tail(path: Path, secrets: tuple[str, ...]) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - 32768))
        raw = stream.read().decode("utf-8", errors="replace")
    lines = raw.splitlines()[-80:]
    visible: list[str] = []
    for line in lines:
        # The console view is for service diagnostics, not a second chat history.
        if "[Event]" in line or "[MsgPush.UnknownElement]" in line:
            continue
        if _SENSITIVE.search(line):
            visible.append("[含凭据字段的日志行已隐藏]")
            continue
        clean = line
        for secret in secrets:
            if secret:
                clean = clean.replace(secret, "[已隐藏]")
        visible.append(clean[:500])
    return "\n".join(visible)


class QQControl:
    snow_instance = None
    management_port = 5099

    """Only accepts installed model names and only stops processes it started."""

    def __init__(self, root: Path, *, snowluma_root: Path | None = None, proxy_root=None,
                 tts_runtime: Path | None = None,
                 plugin_running: Callable[[], bool] | None = None,
                 plugin_enabled: Callable[[], bool] | None = None,
                 recovery: Any = None, bot_scope: Path | None = None,
                 protocol_health: bool = False,
                 plugin_connection: Callable[[dict], bool] | None = None,
                 process_lifecycle=None, recovery_store=False, companion_lifecycle=None, snow_instance=None,
                 owner_qq_route=None) -> None:
        self._owner_qq_route = owner_qq_route
        if type(recovery_store) is not bool or (recovery_store and process_lifecycle is None):
            raise ValueError('explicit mediated recovery store required')
        self._recovery_store_enabled = recovery_store
        self._process_lifecycle = process_lifecycle
        self._companion_lifecycle = companion_lifecycle
        if companion_lifecycle is not None:
            from bot_instance_publisher import InstancePublisher
            if type(companion_lifecycle) is not InstancePublisher or process_lifecycle is not None:
                raise ValueError('exact default companion publisher required')
            companion_lifecycle.bind(root, companion_lifecycle.bot)
            expected_scope = None if bot_scope is None else Path(bot_scope).resolve()
            if companion_lifecycle.scope != expected_scope or not companion_lifecycle.companion_supported:
                raise ValueError('default companion scope/capability mismatch')
        if process_lifecycle is not None:
            from bot_lifecycle_entry import LifecycleProcessBridge
            if type(process_lifecycle) is not LifecycleProcessBridge:
                raise ValueError("explicit lifecycle bridge required")
            process_lifecycle.bind(root, bot_scope)
            if protocol_health:
                raise ValueError("experimental lifecycle bridge forbids automatic QQ health")
        self.bot_scoped = bot_scope is not None
        self._plugin_enabled = plugin_enabled
        self._tts_start_error = ""
        self.plugin_running = plugin_running or (lambda: False)
        self.root = Path(root).resolve()
        self.snow_instance=snow_instance
        self.management_port=5099
        if snow_instance is not None:
            from snowluma_instance import SnowInstance, load_instance
            if (type(snow_instance) is not SnowInstance or snow_instance.core!=self.root
                    or load_instance(self.root,snow_instance.bot)!=snow_instance
                    or (companion_lifecycle is not None and companion_lifecycle.bot!=snow_instance.bot)):
                raise ValueError('fixed own-Bot SnowLuma instance required')
            self.management_port=snow_instance.webui
        self.config_path = self.root / "extensions" / QQ_NAME / "config.json"
        if bot_scope is not None:
            self.config_path = Path(bot_scope) / "qq" / "config.json"
            if process_lifecycle is None:
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._asr = ASRSession(self.config_path) if process_lifecycle is None else None
        self._asr_error = ""
        self._tts_health_cache = None
        self.models = self.root / "models" / "voice"
        self._voice_setup_lock = Lock()
        self._voice_setup_passed = set()
        self._node_probe_at = -100.0
        self._node_probe = {}
        self.asr_installer = (ASRInstaller(self.models / "asr", proxy_root=proxy_root) if process_lifecycle is None
                              else UnavailableInstaller())
        self.tts_installer = (TTSInstaller(self.models / "tts", proxy_root=proxy_root) if process_lifecycle is None
                              else UnavailableInstaller())
        from telyincore.owned_blocking import InstallationOwner
        self._installation_owners = {name: owner for name, owner in (
            ('asr', self.asr_installer), ('tts', self.tts_installer))
            if isinstance(owner, InstallationOwner)}
        from service_layout import cosyvoice_directory, snowluma_directory
        self.snowluma_root = (Path(snowluma_root) if snowluma_root is not None
                              else snowluma_directory(self.root)).resolve()
        self.tts_runtime = (Path(tts_runtime) if tts_runtime is not None
                            else cosyvoice_directory(self.root)).resolve()
        if process_lifecycle is None:
            from voice_runtime import RuntimeSetup
            entry = self._tts_entry()
            self.asr_installer.setup = RuntimeSetup("asr", self.tts_runtime, entry, proxy_root)
            self.tts_installer.setup = RuntimeSetup("tts", self.tts_runtime, entry, proxy_root)
        self.logs = self.root / "data" / "infinity-processes"
        if bot_scope is not None:
            self.logs = Path(bot_scope) / "processes"
        if snow_instance is not None and process_lifecycle is None:
            from service_layout import qq_recovery_file
            self.logs = qq_recovery_file(self.root, snow_instance.bot, baseline=bot_scope is None).parent
        self._owned: dict[str, Any] = {}  # local Popen or explicit remote-owned TTS facade
        self._lock = Lock()
        self._snow_lifecycle = RLock()
        self._closed = Event()
        self._closing = Lock()
        self._snow_stopping = False
        self._ws_evidence = None
        self._ws_probe_lock = Lock()
        # Do not expose a legacy recovery capability, even if injected by a
        # caller. The experimental bridge has no mediated login/credential path.
        self.recovery = ((MediatedRecoverySettings(process_lifecycle,
                self.logs / "snowluma-recovery.json") if recovery_store else UnavailableRecovery())
            if process_lifecycle is not None else
            recovery if recovery is not None else Recovery(
                self.logs / "snowluma-recovery.json",
                store_root=self.root.parent, identity_base=self.root / "data",
                mutation_root=self.root / "data", account_root=self.root / "data",
                validate_target=self._validate_scoped_target if self.bot_scoped else None,
                trusted_management=self._management_endpoint_trusted,
                account_lifecycle=companion_lifecycle, management_port=self.management_port,
                management_instance=snow_instance if companion_lifecycle is not None else None))
        self._owner_path = self.logs / "snowluma-owner.json"
        self._owner_receipt = None  # process object + cooperative file snapshot, not host authority
        self._snow_rejected_pins = []
        self._snow_restoring = False
        self._candidate: dict[str, Any] | None = None
        self._candidate_at = -100.0
        if self._process_lifecycle is None and self._companion_lifecycle is None:
            self._restore_snowluma()
        self._protocol_health = None
        if protocol_health:
            from qq_protocol_health import ProtocolHealth
            self._protocol_health = ProtocolHealth(
                self._protocol_snapshot, self._ws_endpoint_trusted,
                plugin_connection or (lambda data: False))

    def _require_open(self):
        if self._closed.is_set():
            raise ValueError("QQ control lifecycle closed")

    def _protocol_snapshot(self) -> tuple[dict, str, bool]:
        # Atomic config files and independent state read; never take _lock here:
        # the health worker must not wait on a status/lifecycle caller.
        self._require_open()
        require_local_runtime(self._process_lifecycle)
        return (self._config(), str(self.recovery.state().get("targetUin") or ""),
                bool(self.plugin_running()))

    def _validate_scoped_target(self, target: str, config: dict) -> None:
        """Validate the new account before probing or loading any QQ process."""
        self._require_open()
        require_local_runtime(self._process_lifecycle)
        old_recovery = self.root / "data/infinity-processes/snowluma-recovery.json"
        if old_recovery.is_file():
            old = json.loads(old_recovery.read_text(encoding="utf-8"))
            if str(old.get("targetUin") or "") == target:
                raise NeedsAction("目标 QQ 已属于原 Bot，未执行注入；请使用独立测试账号。")
        try:
            settings = connection_settings(config)
        except ValueError as exc:
            raise NeedsAction(str(exc)) from None
        ports = [urlsplit(settings[key]).port for key in ("httpUrl", "wsUrl")]
        if len(set(ports)) != len(ports):
            raise NeedsAction("当前账号的 HTTP 与 WebSocket 端口必须不同，未执行注入。")
        legacy = self.root / "extensions" / QQ_NAME / "config.json"
        if legacy.is_file():
            previous = json.loads(legacy.read_text(encoding="utf-8"))
            reserved = {urlsplit(str(previous.get(key) or "")).port
                        for key in ("httpUrl", "wsUrl")}
            if set(ports) & reserved:
                raise NeedsAction("OneBot 端口与原 Bot 冲突，请在管理页为当前账号设置独立端口；未执行注入。")

    def _trust_checkpoint(self):
        self._owner_checkpoint()
        if self._snow_rejected_pins:
            raise ValueError('SnowLuma observer cleanup pending')

    @staticmethod
    def _trust_ports(ports):
        if (type(ports) is not tuple or len(ports) not in (0, 2)
                or any(type(p) is not int or not 0 < p <= 65535 for p in ports)
                or len(set(ports)) != len(ports)):
            raise ValueError('fixed management or HTTP/WS listener ports required')

    def _listeners_trusted(self, pid, ports):
        # Native table snapshots only: never probe/connect to these endpoints.
        for port in (None, *ports):
            rows = (management_listener_owners() if self.snow_instance is None else management_listener_owners(self.management_port)) if port is None else management_listener_owners(port)
            self._trust_checkpoint()
            if (type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict
                    or set(rows[0]) != {'address', 'pid'}
                    or type(rows[0]['pid']) is not int or rows[0]['pid'] != pid
                    or rows[0]['address'] != '127.0.0.1'):
                return False
        return True

    def _release_snow_observer(self, pinned):
        if pinned is None:
            return
        primary = sys.exception()
        try:
            pinned.close()
        except BaseException as cleanup:
            self._snow_rejected_pins.append(pinned)
            self._snow_stopping = True
            if primary is not None:
                primary.add_note('Snow observer cleanup failed: ' + type(cleanup).__name__)
            else:
                raise

    def _shared_management_identity(self, ports=()) -> dict[str, Any] | None:
        """Conditional legacy process/listener snapshot, never stop ownership.

        File phases use nonblocking identity guard only. No control/recovery/
        lifecycle lock is acquired across CIM, listener or native callbacks.
        This is NOT cryptographic HTTP identity or an atomic credential fence.
        """
        if self._closed.is_set() or self._process_lifecycle is not None:
            return None
        if self.snow_instance is not None or not self.bot_scoped or self._owned.get('snowluma') is not None:
            return None
        record = self.root / 'data/infinity-processes/snowluma-owner.json'
        pinned = None
        try:
            self._trust_ports(ports)
            self._trust_checkpoint()
            from bot_guarded_store import GuardedStore
            with self._owner_guard():
                self._trust_checkpoint()
                store = GuardedStore(self.root.parent, record, checkpoint=self._trust_checkpoint)
                if store.raw is None:
                    return None
                saved = self._owner_identity(store.read())
            entry = self.snowluma_root / 'index.mjs'
            if not entry.is_file():
                return None
            actual = self._owner_identity(discover_snowluma(entry, pid=saved['pid']))
            if actual != saved:
                return None
            pinned = VerifiedProcess(saved)
            if pinned.poll() is not None or not self._listeners_trusted(saved['pid'], ports):
                return None
            if pinned.poll() is not None or self._owned.get('snowluma') is not None:
                return None
            with self._owner_guard():
                self._trust_checkpoint()
                store.read()  # bytes/inode, including the last HTTP/WS snapshot
                if self._owned.get('snowluma') is not None:
                    return None
            result = actual
        except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
            return None
        finally:
            self._release_snow_observer(pinned)

        # close() is itself a callback/native boundary. Recheck known file/
        # close/gate changes made while releasing the observer before positivity.
        try:
            with self._owner_guard():
                self._trust_checkpoint()
                store.read()
                if self._owned.get('snowluma') is not None:
                    return None
            return result
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _snow_service_identity(self, ports=()) -> dict[str, Any] | None:
        if self._closed.is_set() or self._process_lifecycle is not None:
            return None
        process = self._owned.get('snowluma')
        if process is None:
            return self._shared_management_identity(ports)
        pinned = None
        try:
            self._trust_ports(ports)
            self._trust_checkpoint()
            pid = getattr(process, 'pid', None)
            if type(pid) is not int or not 0 < pid <= 0xffffffff or process.poll() is not None:
                return None
            entry = self.snowluma_root / 'index.mjs'
            if not entry.is_file():
                return None
            identity = self._owner_identity(discover_snowluma(entry, pid=pid))
            if identity['pid'] != pid:
                return None
            pinned = VerifiedProcess(identity)
            if pinned.poll() is not None or not self._listeners_trusted(pid, ports):
                return None
            if (self._owned.get('snowluma') is not process
                    or process.poll() is not None or pinned.poll() is not None):
                return None
            with self._owner_guard():
                self._trust_checkpoint()
                if self._owned.get('snowluma') is not process:
                    return None
            result = identity
        except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.TimeoutExpired):
            return None
        finally:
            self._release_snow_observer(pinned)

        try:
            if process.poll() is not None:
                return None
            with self._owner_guard():
                self._trust_checkpoint()
                if self._owned.get('snowluma') is not process:
                    return None
            return result
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def _management_endpoint_trusted(self) -> bool:
        if self.snow_instance is not None and self._owned.get("snowluma") is None:
            return False  # Never issue account RPC against an unstarted own HOST.
        publisher=getattr(self,'_companion_lifecycle',None)
        if publisher is not None:
            try:publisher.check_account_scope()
            except Exception:return False  # revoked/unknown HOST never authorizes credentials
        return self._snow_service_identity() is not None

    @staticmethod
    def _owner_identity(value):
        if (type(value) is not dict or set(value) != {'pid', 'exe', 'created'}
                or type(value['pid']) is not int or not 0 < value['pid'] <= 0xffffffff
                or type(value['created']) is not str or not value['created'].isascii()
                or not value['created'].isdecimal() or not 1 <= len(value['created']) <= 20
                or type(value['exe']) is not str or not 1 <= len(value['exe']) <= 32768
                or any(ord(ch) < 32 for ch in value['exe'])
                or not Path(value['exe']).is_absolute() or Path(value['exe']).name.lower() != 'node.exe'):
            raise ValueError('invalid SnowLuma owner identity record')
        return dict(value)

    def _owner_checkpoint(self):
        self._require_open()
        from bot_migration_gate import assert_no_migration
        assert_no_migration(self.root / 'data')

    @contextmanager
    def _owner_guard(self):
        require_local_runtime(self._process_lifecycle)
        self._require_open()
        from runtime_identity import safe_path, identity_lock
        root, base = self.root.parent, self.root / 'data'
        safe_path(root, base)
        safe_path(root, self._owner_path)
        with identity_lock(base):
            self._owner_checkpoint()
            yield

    def _restore_snowluma(self) -> None:
        require_local_runtime(self._process_lifecycle)
        self._require_open()
        if not self._snow_lifecycle.acquire(blocking=False):
            return
        pinned = None
        try:
            if self._snow_restoring or self._snow_rejected_pins or 'snowluma' in self._owned:
                return
            self._snow_restoring = True
            try:
                from bot_guarded_store import GuardedStore
                with self._owner_guard():
                    store = GuardedStore(self.root.parent, self._owner_path,
                                         checkpoint=self._owner_checkpoint)
                    if store.raw is None:
                        return
                    saved = self._owner_identity(store.read())
                # No identity lock across CIM/native callbacks. File identity is
                # rechecked AFTER pinning; a stale record never admits a handle.
                actual = self._owner_identity(
                    discover_snowluma(self.snowluma_root / 'index.mjs', pid=saved['pid']))
                if actual != saved:
                    return
                pinned = VerifiedProcess(saved)
                if pinned.poll() is not None:
                    return
                with self._owner_guard():
                    store.read()
                    if 'snowluma' in self._owned:
                        return
                    self._owned['snowluma'] = pinned
                    self._owner_receipt = (pinned, store)
                    pinned = None  # admitted original native handle, never reopened by PID
            except (OSError, ValueError, KeyError, TypeError):
                pass  # UNKNOWN/stale is not process absence or production ownership.
            finally:
                self._snow_restoring = False
                self._release_snow_observer(pinned)
        finally:
            self._snow_lifecycle.release()

    def _snowluma_candidate(self) -> dict[str, Any] | None:
        require_local_runtime(self._process_lifecycle)
        if monotonic() - self._candidate_at >= 20:
            self._candidate = (discover_snowluma(self.snowluma_root / "index.mjs")
                               if (self.snowluma_root / "index.mjs").is_file() else None)
            self._candidate_at = monotonic()
        return self._candidate

    def _remember_snowluma(self) -> None:
        require_local_runtime(self._process_lifecycle)
        self._require_open()
        proc = self._owned.get('snowluma')
        pid = getattr(proc, 'pid', None)
        if type(pid) is not int or not 0 < pid <= 0xffffffff:
            raise ValueError('retained SnowLuma child identity required')
        from bot_guarded_store import GuardedStore
        def checkpoint():
            self._owner_checkpoint()
            if self._owned.get('snowluma') is not proc or proc.poll() is not None:
                raise ValueError('SnowLuma child changed during owner persistence')
        with self._owner_guard():
            checkpoint()
            store = GuardedStore(self.root.parent, self._owner_path, checkpoint=checkpoint)
        actual = discover_snowluma(self.snowluma_root / 'index.mjs', pid=pid)
        identity = self._owner_identity(actual)
        if identity['pid'] != pid:
            raise ValueError('SnowLuma discovered identity does not match retained child')
        with self._owner_guard():
            checkpoint()
            store.write(identity)
            # Birth liveness guards the install, not later absent-process cleanup.
            store.checkpoint = self._owner_checkpoint
            self._owner_receipt = (proc, store)

    def _clear_snow_owner(self, process) -> None:
        require_local_runtime(self._process_lifecycle)
        receipt = getattr(self, '_owner_receipt', None)
        if receipt is None or receipt[0] is not process:
            return  # no receipt: never delete an unrelated/uncertain record
        store = receipt[1]
        with self._owner_guard():
            if store.path != self._owner_path.absolute():
                raise ValueError('SnowLuma owner record path changed')
            store.remove()
            self._owner_receipt = None

    def _config(self) -> dict[str, Any]:
        if not self.config_path.is_file():
            return {}
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("QQ 配置不是 JSON 对象")
        return data

    def _models(self) -> tuple[list[str], list[str]]:
        asr = _names(self.models / "asr", ("model.int8.onnx", "tokens.txt"))
        tts = _names(self.models / "tts", ("cosyvoice3.yaml", "llm.pt", "flow.pt"))
        return asr, tts

    def _tts_entry(self) -> Path:
        installed = self.root / "scripts/voice/cosyvoice3_server.py"
        return installed if installed.is_file() else Path(__file__).parent / "scripts/voice/cosyvoice3_server.py"

    def _tts_python(self) -> Path:
        windows = self.tts_runtime / ".venv/Scripts/python.exe"
        if windows.is_file():
            return windows
        return self.tts_runtime / ".venv/bin/python"

    def _tts_runtime_issue(self, voice: str) -> str:
        entry = self._tts_entry()
        if not self._tts_python().is_file() or not entry.is_file():
            return "CosyVoice3 运行环境尚未就绪，请点击「补全运行环境」自动安装并自检。"
        if not (self.tts_runtime / "repo/asset/zero_shot_prompt.wav").is_file():
            return "缺少 CosyVoice3 官方通用参考音频，请补齐运行目录中的 repo/asset/zero_shot_prompt.wav。"
        if voice == "telyin" and not (self.tts_runtime / "references/Telyin.wav").is_file():
            return "缺少自定义音色 Telyin.wav，请补齐参考音频或改选默认音色。"
        if voice not in {"default", "telyin"}:
            return "请从本机可用音色中选择。"
        return ""

    def _voices(self) -> list[dict[str, str]]:
        voices = [{"id": "default", "label": "默认音色 · 通用"}]
        if (self.tts_runtime / "references" / "Telyin.wav").is_file():
            voices.append({"id": "telyin", "label": (self.tts_runtime / "references" / "Telyin.wav").name})
        return voices

    def _asr_runtime_issue(self) -> str:
        from importlib.util import find_spec
        if any(find_spec(name) is None for name in ("sherpa_onnx", "numpy", "scipy")):
            return "ASR 运行环境尚未就绪，请点击「补全运行环境」自动安装并自检。"
        return ""

    def voice_setup_check(self, values: dict) -> dict:
        self._require_open()
        require_local_runtime(self._process_lifecycle)
        self._require_voice_enabled()
        kind, model = values.get("kind"), values.get("model")
        if kind not in ("asr", "tts") or not isinstance(model, str):
            raise ValueError("语音模型选择无效")
        if model not in self._models()[0 if kind == "asr" else 1]:
            raise ValueError("请先下载并选择推荐模型。")
        directory = (self.models / kind / model).resolve()
        if not directory.is_relative_to(self.root / "models" / "voice" / kind):
            raise ValueError("模型目录不在 Core 的语音模型目录中")
        if not self._voice_setup_lock.acquire(blocking=False):
            raise ValueError("已有语音自检正在运行，请稍后再试。")
        try:
            if kind == "asr":
                issue = self._asr_runtime_issue()
                if issue:
                    raise ValueError(issue)
                if not all((directory / name).is_file() for name in ASR_FILES):
                    raise ValueError("识别模型、词表或 VAD 文件缺失，请补齐后重试。")
                try:
                    transcript = check_asr(directory, values.get("audio"))
                    self._voice_setup_passed.add(("asr", model, ""))
                    self._require_open()
                    return {"ok": True, "transcript": transcript}
                except ValueError:
                    raise
                except Exception as error:
                    raise ValueError("ASR 加载或推理失败，请检查本机依赖和模型文件。") from error
            voice = values.get("voice", "default")
            if not isinstance(voice, str):
                raise ValueError("音色选择无效")
            issue = self._tts_runtime_issue(voice)
            if issue:
                raise ValueError(issue)
            normalizer = self.tts_runtime / "text-normalization"
            if not all((normalizer / language / "tn/verbalizer.fst").is_file()
                       for language in ("zh", "en")):
                raise ValueError("缺少 WeText 文本规范化资源，请将中英文资源放入 text-normalization。")
            if _open_port(tts_port(self._config())) or self._running("tts"):
                raise ValueError("请先停止 TTS 再运行首次配置自检。")
            env = dict(os.environ, TELYIN_TTS_RUNTIME=str(self.tts_runtime),
                       TELYIN_TTS_MODEL_DIR=str(directory), HF_HUB_OFFLINE="1",
                       TRANSFORMERS_OFFLINE="1")
            entry = self._tts_entry()
            logs = self.logs
            logs.mkdir(parents=True, exist_ok=True)
            try:
                with (logs / "tts-setup.log").open("ab") as log:
                    subprocess.run([str(self._tts_python()), "-u", str(entry), "--check-voice", voice],
                                   cwd=self.tts_runtime, env=env, stdout=log, stderr=log,
                                   timeout=150, check=True)
            except (OSError, subprocess.SubprocessError) as error:
                raise ValueError("TTS 自检失败，请查看当前档案的 tts-setup.log。") from error
            self._require_open()
            self._voice_setup_passed.add(("tts", model, voice))
            return {"ok": True}
        finally:
            self._voice_setup_lock.release()

    def open_snowluma_folder(self) -> dict[str, Any]:
        """Open only the configured installation directory; never run an installer."""
        self._require_open()
        require_local_runtime(self._process_lifecycle)
        self.snowluma_root.mkdir(parents=True, exist_ok=True)
        _open_folder(self.snowluma_root)
        return {"ok": True}

    def open_voice_folder(self) -> dict[str, Any]:
        """Open the configured local reference-audio directory, never a caller path."""
        self._require_open()
        require_local_runtime(self._process_lifecycle)
        runtime = self.tts_runtime.resolve()
        if not runtime.is_dir():
            raise ValueError("TTS 运行目录不存在，请先安装本机 TTS 环境")
        folder = (runtime / "references").resolve()
        if not folder.is_relative_to(runtime):
            raise ValueError("音色目录不在本机 TTS 运行目录中")
        folder.mkdir(exist_ok=True)
        _open_folder(folder)
        return {"ok": True, "folder": str(folder)}

    def open_model_folder(self, kind: str, model: str) -> dict[str, Any]:
        """Only open a voice-model root or a model from the installed list."""
        self._require_open()
        require_local_runtime(self._process_lifecycle)
        if kind not in ("tts", "asr") or not isinstance(model, str):
            raise ValueError("模型类型或名称无效")
        asr_names, tts_names = self._models()
        names = tts_names if kind == "tts" else asr_names
        if model and model not in names:
            raise ValueError("模型必须从已安装列表中选择")
        base = (self.models / kind).resolve()
        if not base.is_relative_to(self.root):
            raise ValueError("模型目录不在项目中")
        base.mkdir(parents=True, exist_ok=True)
        folder = (base / model).resolve() if model else base
        if not folder.is_relative_to(base) or not folder.is_dir():
            raise ValueError("模型目录不在本地模型文件夹中")
        _open_folder(folder)
        return {"ok": True, "folder": str(folder)}

    def import_connection(self, *, reload: Callable[[], Any] | None = None) -> dict:
        self._require_open()
        if self._process_lifecycle is not None:
            raise ValueError("experimental lifecycle forbids unmediated credential import")
        from snowluma_connection import connection_settings
        settings = connection_settings(self.recovery.connection_config())
        with self._lock:
            if not self.config_path.parent.is_dir():
                raise ValueError("请先安装 QQ 插件")
            original = self.config_path.read_bytes() if self.config_path.exists() else None
            data = self._config()
            data.update(settings)
            has_routes = bool(data.get("privates") or data.get("groups"))
            self._commit_config(data, original, reload if has_routes else None)
        return {"ok": True, "message": (
            "连接配置已导入；请在会话路由填写允许的私聊或群名单并保存，之后才会启动插件。"
            if not has_routes else
            "已导入目标账号的本机接入配置；请检测接入。") }

    @staticmethod
    def _connection_revision(data: dict) -> str:
        import hashlib
        fields = {k: data.get(k) for k in ("httpUrl", "wsUrl", "token", "wsToken")}
        return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _fresh_management(state: dict) -> bool:
        age = state.get("healthSampleAgeSeconds")
        return (state.get("managementAuthenticated") is True
                and state.get("accountOnline") is True
                and state.get("healthStatus") == "online"
                and bool(state.get("targetUin"))
                and state.get("healthTarget") == state.get("targetUin")
                and type(age) in (int, float) and 0 <= age < 30)

    def _ws_endpoint_trusted(self, data: dict) -> bool:
        if self._closed.is_set() or self._process_lifecycle is not None:
            return False
        try:
            url = urlsplit(str(data.get('wsUrl') or ''))
            http = urlsplit(str(data.get('httpUrl') or ''))
            for endpoint, scheme in ((url, 'ws'), (http, 'http')):
                if (endpoint.scheme != scheme or endpoint.hostname != '127.0.0.1'
                        or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
                        or endpoint.path not in ('', '/') or type(endpoint.port) is not int
                        or not 0 < endpoint.port <= 65535):
                    return False
            if http.port == url.port:
                return False
            # ONE pinned identity spans management + both protocol listeners.
            return self._snow_service_identity((url.port, http.port)) is not None
        except (ValueError, OSError, TypeError):
            return False

    def _probe_websocket(self, data: dict, target: str, *, checked=None) -> bool:
        self._require_open()
        require_local_runtime(self._process_lifecycle)
        from websockets.sync.client import connect
        token = str(data.get("wsToken", data.get("token", "")) or "")
        checked = self.recovery.state() if checked is None else checked
        if not token or not self._same_probe_snapshot(data, checked):
            return False
        deadline = monotonic() + 5
        headers = {"Authorization": "Bearer " + token}
        with connect(data["wsUrl"], additional_headers=headers, proxy=None,
                     open_timeout=2, close_timeout=1, max_size=65536) as ws:
            for action in ("get_login_info", "get_status"):
                if not self._same_probe_snapshot(data, checked):
                    return False
                echo = uuid.uuid4().hex
                ws.send(json.dumps({"action": action, "params": {}, "echo": echo}))
                for _ in range(16):
                    left = deadline - monotonic()
                    if left <= 0:
                        return False
                    value = json.loads(ws.recv(timeout=left))
                    if not isinstance(value, dict) or value.get("echo") != echo:
                        continue
                    payload = value.get("data")
                    if (value.get("status") != "ok" or type(value.get("retcode")) is not int
                            or value["retcode"] != 0 or not isinstance(payload, dict)):
                        return False
                    if action == "get_login_info":
                        if str(payload.get("user_id")) != target:
                            return False
                    elif payload.get("online") is not True or payload.get("good") is not True:
                        return False
                    break
                else:
                    return False
        return monotonic() <= deadline

    def _websocket_sample(self, data: dict, state: dict) -> bool | None:
        if self._process_lifecycle is not None:
            return None
        sample = self._ws_evidence
        if not isinstance(sample, dict) or not self._fresh_management(state):
            return None
        if (sample["generation"] != state.get("generation")
                or sample["revision"] != state.get("revision")
                or sample["target"] != state.get("targetUin")
                or sample["connection"] != self._connection_revision(data)
                or not 0 <= monotonic() - sample["at"] < 30
                or not self._ws_endpoint_trusted(data)):
            return None
        return sample["verified"]

    def _same_probe_snapshot(self, data: dict, checked: dict) -> bool:
        if self._process_lifecycle is not None:
            return False
        current = self.recovery.state()
        return (self._config() == data and self._fresh_management(current)
                and all(current.get(k) == checked.get(k)
                        for k in ("generation", "revision", "targetUin"))
                and self._ws_endpoint_trusted(data))

    def check_connection(self, *, verify_websocket: bool = False) -> dict[str, Any]:
        self._require_open()
        if self._process_lifecycle is not None:
            raise ValueError("experimental lifecycle requires parent-owned account evidence")
        if self._protocol_health is not None:
            data, target, loaded = self._protocol_snapshot()
            sample = self._protocol_health.refresh(data, target, loaded)
            return {"ok": sample.get("status") == "online",
                    "protocolHealth": sample, "businessRoundtripVerified": None,
                    "message": "已请求只读刷新连接状态，请查看上方实时结果；不会登录、注入 QQ 或发送测试消息。"}
        if not verify_websocket:
            return self._check_http_connection()
        if not self._ws_probe_lock.acquire(blocking=False):
            return {"ok": False, "message": "检测已在进行；本次未追加探测。"}
        try:
            with self._lock:
                self._ws_evidence = None
                data = self._config()
            checked = self.recovery.state()
            if not self._fresh_management(checked) or not self._ws_endpoint_trusted(data):
                return {"ok": False, "websocketVerified": None,
                        "message": "管理目标采样未通过或已过期；请先验证目标接入。"}
            result = self._check_http_connection(snapshot=(data, checked))
            if result.get("ok") is not True:
                return result
            try:
                verified = self._probe_websocket(
                    data, str(checked["targetUin"]), checked=checked)
            except Exception:
                verified = False
            current = self.recovery.state()
            with self._lock:
                if (self._config() != data or not self._fresh_management(current)
                        or any(current.get(k) != checked.get(k)
                               for k in ("generation", "revision", "targetUin"))
                        or not self._ws_endpoint_trusted(data)):
                    return {"ok": False, "websocketVerified": None,
                            "message": "检测期间目标或接入已变化；结果作废。"}
                self._ws_evidence = {
                    "generation": current["generation"], "revision": current["revision"],
                    "target": current["targetUin"], "connection": self._connection_revision(data),
                    "at": monotonic(), "verified": verified}
            return {**result, "ok": verified, "websocketVerified": verified,
                    "businessRoundtripVerified": None,
                    "message": ("目标账号的只读 WS 身份与状态核验通过；业务收发未核验。"
                                if verified else "HTTP 核验通过，但 WS 身份或状态未通过。")}
        finally:
            self._ws_probe_lock.release()

    def _check_http_connection(self, *, snapshot=None) -> dict[str, Any]:
        """Explicit read-only probe; pinned checks never switch endpoint/config."""
        self._require_open()
        require_local_runtime(self._process_lifecycle)
        if snapshot is None:
            with self._lock:
                data = self._config()
            checked = self.recovery.state()
        else:
            data, checked = snapshot
            if not self._same_probe_snapshot(data, checked):
                return {"ok": False, "message": "检测配置已变化；未发送凭据。"}
        url = urlsplit(str(data.get("httpUrl") or ""))
        if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1")
                or url.username or url.password or url.query or url.fragment):
            return {"ok": False, "message": "仅检测已配置的本机 HTTP 地址；请先配置 OneBot。"}
        token = str(data.get("token") or "")
        headers = {"Authorization": "Bearer " + token} if token else {}
        target = str(checked.get("targetUin") or "")
        identity_verified = False
        try:
            with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
                if snapshot is not None and not self._same_probe_snapshot(data, checked):
                    return {"ok": False, "message": "检测配置已变化；未发送凭据。"}
                response = client.post(str(data["httpUrl"]).rstrip("/") + "/get_status",
                                       json={}, headers=headers)
                if target and response.status_code == 200:
                    if snapshot is not None and not self._same_probe_snapshot(data, checked):
                        return {"ok": False, "message": "检测配置已变化；已停止后续请求。"}
                    login = client.post(str(data["httpUrl"]).rstrip("/") + "/get_login_info",
                                        json={}, headers=headers)
                    info = login.json() if login.status_code == 200 else {}
                    identity_verified = (isinstance(info, dict) and info.get("status") == "ok"
                        and info.get("retcode") == 0 and isinstance(info.get("data"), dict)
                        and str(info["data"].get("user_id")) == target)
            current = self.recovery.state()
            if (current.get("generation") != checked.get("generation")
                    or current.get("revision") != checked.get("revision")
                    or self._config() != data):
                return {"ok": False, "identityVerified": False,
                        "message": "配置已改变，本次检测结果作废。"}
            if response.status_code in (401, 403):
                self.recovery.invalidate("unknown")
                return {"ok": False, "message": "OneBot 鉴权未通过，请核对两端令牌。"}
            if response.status_code != 200:
                self.recovery.invalidate("unknown")
                return {"ok": False, "message": f"OneBot HTTP 返回 {response.status_code}。"}
            result = response.json()
            status = result.get("data") if isinstance(result, dict) else None
            if (not isinstance(status, dict) or result.get("status") != "ok"
                    or result.get("retcode") != 0):
                self.recovery.invalidate("unknown")
                return {"ok": False, "message": "接口可达，但未返回有效的 OneBot 状态。"}
            current = self.recovery.state()
            if (current.get("generation") != checked.get("generation")
                    or current.get("revision") != checked.get("revision")):
                return {"ok": False, "identityVerified": False, "message": "配置已改变，本次检测结果作废。"}
            online = status.get("online") is True
            good = status.get("good") is True
            running = bool(self.plugin_running())
            if not (online and good and identity_verified and token):
                self.recovery.invalidate("unknown")
            return {"ok": online and good and running and identity_verified and bool(token),
                    "identityVerified": identity_verified, "websocketVerified": None,
                    "businessRoundtripVerified": None, "online": online,
                    "good": good, "pluginRunning": running,
                    "message": ("OneBot 自报 QQ 在线，插件运行；目标身份与 WebSocket/业务收发需分别核验。"
                                if online and good and running else
                                "状态请求通过；QQ 未就绪或插件未运行，请查看对应状态。")
                    + (" 当前未设置令牌；此结果不代表鉴权配置安全。" if not token else "")}
        except (httpx.HTTPError, ValueError):
            current = self.recovery.state()
            if (current.get("generation") == checked.get("generation")
                    and current.get("revision") == checked.get("revision")
                    and self._config() == data):
                self.recovery.invalidate("unknown")
            return {"ok": False, "message": "OneBot 连接失败或回包无效，请检查地址、服务和配置。"}

    def installation_status(self) -> dict[str, Any]:
        # Cache the executable probe; state polling must not spawn Node each second.
        self._require_open()
        require_local_runtime(self._process_lifecycle)
        if monotonic() - self._node_probe_at > 30:
            from service_layout import node_executable
            executable = node_executable(self.root)
            version = ""
            if executable:
                try:
                    result = subprocess.run(
                        [executable, "--version"], capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=3,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    if result.returncode == 0 and re.fullmatch(
                            r"v[0-9]+\.[0-9]+\.[0-9]+", result.stdout.strip()):
                        version = result.stdout.strip()
                except (OSError, subprocess.TimeoutExpired):
                    pass
            self._node_probe = {"nodeAvailable": bool(version), "nodeVersion": version}
            self._node_probe_at = monotonic()
        entry = (self.snowluma_root / "index.mjs").is_file()
        return {**self._node_probe, "entryPresent": entry,
                "ready": entry and self._node_probe.get("nodeAvailable", False)}

    def _latest_snow_log(self) -> Path:
        require_local_runtime(self._process_lifecycle)
        own = self.logs / "snowluma.out.log"
        if self.snow_instance is not None:
            folder=self.snow_instance.root/'logs'
            paths=sorted(folder.glob('snowluma-*.log')) if folder.is_dir() else []
            return max(paths,key=lambda p:p.stat().st_mtime) if paths else folder/'snowluma.log'
        if self.bot_scoped:
            return own
        native = self.snowluma_root / "logs"
        recent = sorted(native.glob("snowluma-*.log")) if native.is_dir() else []
        paths = [path for path in [own, *recent] if path.is_file()]
        return max(paths, key=lambda p: p.stat().st_mtime) if paths else own

    def _snow_log(self, secrets: tuple[str, ...]) -> str:
        require_local_runtime(self._process_lifecycle)
        output = _tail(self._latest_snow_log(), secrets)
        error = "" if self.snow_instance is not None else _tail(self.logs / "snowluma.err.log", secrets)
        return output + ("\n\n── stderr · 错误输出 ──\n" + error if error else "")

    def _running(self, name: str) -> bool:
        proc = self._owned.get(name)
        return proc is not None and proc.poll() is None

    def _snow_management_password_changed(self) -> bool | None:
        if self.snow_instance is not None:
            directory = self.snow_instance.root
        elif not self.bot_scoped:
            directory = self.snowluma_root
        else:
            return None
        return self._read_password_changed(self.root.parent, directory / "config/webui.json")

    @staticmethod
    def _read_password_changed(base, path):
        from runtime_identity import safe_path
        try:
            with safe_path(base, path).open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                return None
            pending = json.loads(raw).get("mustChangePassword")
        except (OSError, ValueError, AttributeError):
            return None
        return not pending if type(pending) is bool else None

    def _owner_routing_state(self, data):
        if self._owner_qq_route is None:
            if data.get("sharingOwner"):
                return {"sharingOwner": str(data["sharingOwner"]),
                        "sharingEnabled": data.get("sharingEnabled", True) is True}
            return {}
        try:
            route = self._owner_qq_route(str(data.get("proactiveTarget") or ""))
        except (OSError, ValueError):
            route = {"accounts": [], "target": "", "unavailable": True}
        return {"ownerRoute": route, "sharingEnabled": data.get("sharingEnabled", True) is True}

    def read(self) -> dict[str, Any]:
        self._require_open()
        if self._process_lifecycle is not None:
            # A capability view, not the legacy WebUI status schema or a health
            # sample. No config, logs, QQ environment, discovery or socket IO.
            return {"lifecycle": {"mode": "experimental", "legacyStatusAvailable": False,
                                  "productionConnected": False,
                                  "processCapabilitiesConfigured": list(self._process_lifecycle.configured_processes())},
                    "snowluma": {"available": False, "running": None,
                                  "processCapabilityConfigured": 'snowluma' in self._process_lifecycle.configured_processes(),
                                  "retainedRemoteHandle": "snowluma" in self._owned,
                                  "recovery": self.recovery.state()},
                    "tts": {"available": False, "running": None,
                            "retainedRemoteHandle": "tts" in self._owned}}
        with self._lock:
            data = self._config()
            asr_names, tts_names = self._models()
            asr_selected = Path(str(data.get("asrModelDir") or "")).name
            if asr_selected not in asr_names:
                asr_selected = "SenseVoiceSmall-onnx" if "SenseVoiceSmall-onnx" in asr_names else ""
            tts_selected = str(data.get("ttsModel") or "")
            if tts_selected not in tts_names:
                tts_selected = ""
            voices = self._voices()
            tts_voice = str(data.get("ttsVoice") or "default")
            if tts_voice not in {one["id"] for one in voices}:
                tts_voice = "default"
            asr_language = data.get("asrLanguage", "auto")
            asr_threads = data.get("asrThreads", ASR_DEFAULT_THREADS)
            tts_send_mode = data.get("ttsSendMode", "on_demand")
            ws = urlsplit(str(data.get("wsUrl") or ""))
            ws_port = ws.port or 3001
            snow_running = _open_port(ws_port)
            webui_running = _open_port(self.management_port)
            port = tts_port(data)
            external = data.get("ttsProvider") == "external"
            tts_running = self._tts_healthy(port)
            secrets = tuple(str(data.get(key) or "") for key in ("token", "wsToken"))
            privates = _qq_ids(data.get("privates", []), "privates")
            groups = _qq_ids(data.get("groups", []), "groups")
            http = urlsplit(str(data.get("httpUrl") or ""))
            configured = (self.config_path.is_file() and bool(privates or groups)
                          and http.scheme in ("http", "https") and bool(http.hostname)
                          and ws.scheme in ("ws", "wss") and bool(ws.hostname))
            loaded = bool(self.plugin_running())
            voice_enabled = self._plugin_enabled is None or self._plugin_enabled()
            asr_configured = bool(asr_selected)
            asr_path = effective_asr_directory(data.get("asrModelDir"), self.models.parent)
            asr_ready = (asr_configured and bool(asr_path)
                              and all((Path(asr_path) / name).is_file()
                                      for name in ("model.int8.onnx", "tokens.txt")))
            tts_configured = bool(tts_selected)
            recovery_state = self.recovery.state()
            # Config presentation must not depend on a healthy parent RPC.
            # Unknown ownership is NOT an unmanaged/external process or a grant
            # to retry startup. Action methods continue using strict _running.
            managed = {}
            publisher = getattr(self, '_companion_lifecycle', None)
            control_available = not bool(getattr(getattr(publisher, 'client', None), 'closed', False))
            for name in ('snowluma', 'tts'):
                try: managed[name] = self._running(name) if control_available else None
                except (ValueError, OSError):
                    managed[name] = None
                    control_available = False
            protocol_state = (self._protocol_health.read(
                data, str(recovery_state.get("targetUin") or ""), configured and loaded)
                if self._protocol_health is not None else None)
            return {
                "environment": read_qq_environment(self.root),
                "wired": self.config_path.is_file(),
                "plugin": {"configured": configured, "running": loaded},
                "routing": {"privates": privates, "groups": groups,
                            "proactiveTarget": str(data.get("proactiveTarget") or ""),
                            **self._owner_routing_state(data),
                            "historyBackfillMinutes": data.get("historyBackfillMinutes", 0),
                            "reactionWindowSeconds": data.get("reactionWindowSeconds", 3)},
                "asr": {"autoStart": data.get("asrAutoStart", data.get("transcribeVoice", False)),
                        "startError": self._asr_error or (
                            "保存的本地 ASR 模型缺少权重或词表，请重新选择模型。"
                            if data.get("transcribeVoice") and asr_path and not asr_ready else ""), "install": self.asr_installer.read(), "enabled": data.get("transcribeVoice", False) is True,
                        "configured": asr_configured, "runtimeIssue": self._asr_runtime_issue(),
                        "setupReady": (asr_ready and data.get("transcribeVoice", False) is True
                                       or ("asr", asr_selected, "") in self._voice_setup_passed
                                       or (asr_selected == self.asr_installer.name and self.asr_installer.read().get("runtimeReady", False))),
                        "running": self._asr.engine is not None,
                        "model": asr_selected, "models": asr_names,
                        "language": asr_language if asr_language in ASR_LANGUAGES else "auto",
                        "useItn": data.get("asrUseItn", True) is True,
                        "threads": asr_threads if type(asr_threads) is int
                        and asr_threads in ASR_THREADS else ASR_DEFAULT_THREADS},
                "tts": {"autoStart": data.get("ttsAutoStart", data.get("ttsEnabled", False)),
                        "port": port, "provider": "external" if external else "builtin",
                        "service": data.get("ttsService", ""), "install": self.tts_installer.read(), "enabled": data.get("ttsEnabled", False) is True,
                        "configured": tts_configured or external,
                        "model": tts_selected, "models": tts_names,
                        "voice": tts_voice, "voices": voices, "speed": 1.0,
                        "previewText": DEFAULT_PREVIEW_TEXT,
                        "voiceFolderAvailable": self.tts_runtime.is_dir(),
                        "runtimeIssue": "" if external else self._tts_runtime_issue(tts_voice),
                        "setupReady": (tts_configured and data.get("ttsEnabled") is True
                                       or ("tts", tts_selected, tts_voice) in self._voice_setup_passed
                                       or (tts_selected == self.tts_installer.name and tts_voice == "default" and self.tts_installer.read().get("runtimeReady", False))),
                        "sendMode": tts_send_mode if tts_send_mode in TTS_SEND_MODES
                        else "on_demand",
                        "running": tts_running, "managed": managed["tts"],
                        "startError": self._tts_start_error or (
                            "TTS 进程已退出，请查看运行日志。"
                            if "tts" in self._owned and managed["tts"] is False else ""),
                        "pluginEnabled": voice_enabled},
                "snowluma": {"healthSchema": "snow-health-v2",
                              "managementUrl": f"http://127.0.0.1:{self.management_port}",
                              "isolatedInstance": self.snow_instance is not None,
                              "managementPasswordChanged": self._snow_management_password_changed(),
                              "installation": self.installation_status(),
                              "configured": configured,
                              "installed": (self.snowluma_root / "index.mjs").is_file(),
                              "running": False, "transportReachable": snow_running,
                              "webuiRunning": webui_running,
                              "websocketVerified": self._websocket_sample(data, recovery_state),
                              "businessRoundtripVerified": None,
                              "managed": managed["snowluma"],
                              "controlAvailable": control_available,
                              "controlMessage": ("" if control_available else
                                  "父级控制通道不可用；已保留配置。请从原启动器正常退出后再启动，勿重复连接或覆盖配置。"),
                              "sharedService": (self.bot_scoped and webui_running
                                                and managed["snowluma"] is False and control_available),
                              "canStopInstallation": (self.snow_instance is None and not self.bot_scoped
                                                      and managed["snowluma"] is False and control_available
                                                      and self._snowluma_candidate() is not None),
                              "protocolHealth": protocol_state,
                              "recovery": recovery_state,
                              "log": self._snow_log(secrets)},
                # Response compatibility for already-open older web generations.
                "overlay": {"installed": False, "profile": "", "profiles": []},
            }

    def _commit_config(self, data: dict[str, Any], original: bytes | None,
                       reload: Callable[[], Any] | None) -> None:
        if self._process_lifecycle is None:
            return self._commit_config_local(data, original, reload)
        if reload is not None:
            raise ValueError("experimental account guard forbids unmediated plugin reload")
        with self._process_lifecycle.account_change_guard(self.config_path, self.logs):
            return self._commit_config_guarded(data, original)

    def _commit_config_guarded(self, data, original):
        """Cooperative guard required; no OS-atomic CAS or ACL guarantee.

        Never restore stale before-images, install a replaced pending inode,
        or clean up another writer's file at the same temporary name.
        """
        from runtime_identity import safe_path
        root = self._process_lifecycle.core.parent
        def current():
            safe_path(root, self.config_path)
            if not self.config_path.exists(): return None
            with self.config_path.open('rb') as stream: raw = stream.read(65537)
            if len(raw) > 65536: raise ValueError('account config exceeds guarded limit')
            return raw
        if type(data) is not dict:
            raise ValueError('account config payload must be an object')
        payload = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
        if len(payload) > 65536 or current() != original:
            raise ValueError('account config changed before guarded write')
        safe_path(root, self.logs)
        self.logs.mkdir(parents=True, exist_ok=True)
        nonce = uuid.uuid4().hex
        if original is not None:
            backup = self.logs / f'qq-config-{nonce}.json.bak'
            safe_path(root, backup)
            with open_private_new(backup) as stream:
                stream.write(original); stream.flush(); os.fsync(stream.fileno())
        temporary = self.config_path.with_name(self.config_path.name + '.' + nonce + '.pending')
        safe_path(root, temporary)
        identity = None
        try:
            with open_private_new(temporary) as stream:
                info = os.fstat(stream.fileno())
                identity = (info.st_dev, info.st_ino)
                stream.write(payload); stream.flush(); os.fsync(stream.fileno())
            if current() != original:
                raise ValueError('account config changed during guarded write')
            safe_path(root, temporary)
            info = temporary.lstat()
            if (info.st_dev, info.st_ino) != identity:
                raise ValueError('account config temporary changed before install')
            os.replace(temporary, self.config_path)
            identity = None  # consumed; a new file at this name is not ours
        finally:
            if identity is not None:
                try:
                    info = temporary.lstat()
                    if (info.st_dev, info.st_ino) == identity:
                        temporary.unlink()
                except FileNotFoundError:
                    pass

    def _commit_config_local(self, data: dict[str, Any], original: bytes | None,
                       reload: Callable[[], Any] | None) -> None:
        return self._commit_local_json(self.config_path, 'qq-config-', data, original, reload)

    def _commit_local_json(self, path: Path, backup_prefix: str,
                           data: dict[str, Any], original: bytes | None,
                           reload: Callable[[], Any] | None) -> None:
        """Cooperative default writer; never roll back another writer's version.

        Caller holds self._lock. Identity lock covers file phases, NOT plugin
        callbacks/RPC. This is not OS-atomic CAS, ACL or power-loss authority.
        """
        from runtime_identity import safe_path, identity_lock
        from bot_migration_gate import assert_no_migration
        from contextlib import contextmanager
        # Bind a destination for this operation; never mutate config_path for callbacks.
        if not (path == self.config_path and backup_prefix == 'qq-config-'):
            raise ValueError('unbound local config destination')
        require_local_runtime(self._process_lifecycle)
        root, base = self.root.parent, self.root / 'data'
        self._require_open()
        if type(data) is not dict or (original is not None and type(original) is not bytes):
            raise ValueError('account config object and byte preimage required')
        payload = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
        if len(payload) > 65536 or (original is not None and len(original) > 65536):
            raise ValueError('account config exceeds guarded limit')

        @contextmanager
        def guard():
            safe_path(root, base)
            with identity_lock(base):
                assert_no_migration(base)
                yield

        def snapshot():
            safe_path(root, path)
            try:
                with path.open('rb') as stream:
                    raw = stream.read(65537)
                    info = os.fstat(stream.fileno())
            except FileNotFoundError:
                return (None, None)
            if len(raw) > 65536:
                raise ValueError('account config exceeds guarded limit')
            return (raw, (info.st_dev, info.st_ino))

        def install(raw, expected):
            # Every install/rollback gets an exclusive, independently pinned name.
            temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
            safe_path(root, temporary)
            identity = None
            try:
                with open_private_new(temporary) as stream:
                    info = os.fstat(stream.fileno())
                    identity = (info.st_dev, info.st_ino)
                    stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                if snapshot() != expected:
                    raise ValueError('account config changed before local install')
                safe_path(root, temporary)
                info = temporary.lstat()
                if (info.st_dev, info.st_ino) != identity:
                    raise ValueError('account config temporary changed before local install')
                receipt = (raw, identity)
                os.replace(temporary, path)
                identity = None
                return receipt
            finally:
                if identity is not None:
                    primary = sys.exception()
                    try:
                        info = temporary.lstat()
                        if (info.st_dev, info.st_ino) == identity:
                            temporary.unlink()
                    except FileNotFoundError:
                        pass
                    except BaseException as cleanup:
                        if primary is None:
                            raise
                        primary.add_note('account temporary cleanup failed: ' + type(cleanup).__name__)

        with guard():
            self._require_open()
            before = snapshot()
            if before[0] != original:
                raise ValueError('account config changed before local write')
            safe_path(root, self.logs)
            self.logs.mkdir(parents=True, exist_ok=True)
            if original is not None:
                backup = self.logs / (backup_prefix + uuid.uuid4().hex + '.json.bak')
                safe_path(root, backup)
                with open_private_new(backup) as stream:
                    stream.write(original); stream.flush(); os.fsync(stream.fileno())
            installed = install(payload, before)
        try:
            if reload is not None:
                reload()  # no identity lock across arbitrary plugin callbacks
            with guard():
                self._require_open()
                if snapshot() != installed:
                    raise ValueError('account config changed during local reload')
        except BaseException as primary:
            restored = False
            try:
                with guard():
                    if snapshot() != installed:
                        raise ValueError('account rollback no longer owns installed version')
                    if original is None:
                        path.unlink()  # cooperative check, not OS CAS
                    else:
                        install(original, installed)
                    restored = True
            except BaseException as rollback:
                primary.add_note('account rollback skipped or failed: ' + type(rollback).__name__)
            if restored and reload is not None and isinstance(primary, Exception):
                try:
                    reload()
                except BaseException as repair:
                    primary.add_note('account rollback reload failed: ' + type(repair).__name__)
            raise

    def _route_config(self):
        return self._config()

    def save_routes(self, values: dict[str, Any], *,
                    reload: Callable[[], Any] | None = None) -> dict[str, Any]:
        self._require_open()
        required = {"privates", "groups", "proactiveTarget", "historyBackfillMinutes"}
        if not required <= set(values) or set(values) - required - {"reactionWindowSeconds"}:
            raise ValueError("会话路由字段不完整或含未知字段")
        privates = _qq_ids(values["privates"], "privates")
        groups = _qq_ids(values["groups"], "groups")
        if not privates and not groups:
            raise ValueError("私聊和群聊名单至少填写一个")
        proactive = values["proactiveTarget"]
        if not isinstance(proactive, str):
            raise ValueError("主动私聊目标必须是 QQ 号或空白")
        if proactive:
            proactive_ids = _qq_ids([proactive], "proactiveTarget")
            if proactive_ids[0] not in privates:
                raise ValueError("主动私聊目标必须在私聊名单内")
            proactive = proactive_ids[0]
        minutes = values["historyBackfillMinutes"]
        if type(minutes) is not int or not 0 <= minutes <= 30:
            raise ValueError("断线补读时长必须是 0 到 30 分钟的整数")
        window = values.get("reactionWindowSeconds")
        if "reactionWindowSeconds" in values and (
                type(window) is not int or not 0 <= window <= 10):
            raise ValueError("消息反应窗口必须是 0 到 10 秒的整数")
        with self._lock:
            if not (self.root / "extensions" / QQ_NAME).is_dir():
                raise ValueError("请先安装 QQ 插件")
            original = self.config_path.read_bytes() if self.config_path.is_file() else None
            data = self._route_config()
            sharing_owner = str(data.get("sharingOwner") or "")
            if self._owner_qq_route:
                if proactive and proactive not in self._owner_qq_route(proactive)["accounts"]:
                    raise ValueError("请在人物画像中确认这个 QQ 与本机控制台是同一个人")
            elif sharing_owner and data.get("sharingEnabled", True) is True:
                if sharing_owner not in privates or proactive != sharing_owner:
                    raise ValueError("主动消息仅发送给主人；不可在会话路由中改成其他人或移出私聊白名单")
            data.update(privates=privates, groups=groups, proactiveTarget=proactive,
                        historyBackfillMinutes=minutes)
            if window is not None:
                data["reactionWindowSeconds"] = window
            http = urlsplit(str(data.get("httpUrl") or ""))
            ws = urlsplit(str(data.get("wsUrl") or ""))
            connected_config = (http.scheme in ("http", "https") and bool(http.hostname)
                                and ws.scheme in ("ws", "wss") and bool(ws.hostname))
            # Route-first setup must save a draft, not require a connection file
            # that only exists after import. Do not reload an incomplete plugin.
            self._commit_config(data, original, reload if connected_config else None)
        return self.read()

    def save(self, values: dict[str, Any], *, reload: Callable[[], Any] | None = None
             ) -> dict[str, Any]:
        self._require_open()
        required = {"asrEnabled", "asrModel", "ttsEnabled", "ttsModel"}
        optional = {"ttsAutoStart", "asrAutoStart", "ttsPort", "ttsProvider", "ttsService", "ttsVoice", "ttsSendMode", "asrLanguage", "asrUseItn", "asrThreads"}
        if not required.issubset(values) or set(values) - (required | optional):
            raise ValueError("语音设置字段不完整或含未知字段")
        if type(values["asrEnabled"]) is not bool or type(values["ttsEnabled"]) is not bool:
            raise ValueError("语音开关必须是布尔值")
        if not isinstance(values["asrModel"], str) or not isinstance(values["ttsModel"], str):
            raise ValueError("模型名称必须是字符串")
        asr_names, tts_names = self._models()
        asr = values["asrModel"]
        tts = values["ttsModel"]
        voice = values.get("ttsVoice")
        available_voices = {one["id"] for one in self._voices()}
        if "ttsVoice" in values and (not isinstance(voice, str)
                                      or voice not in available_voices):
            raise ValueError("音色必须从本机可用预设中选择")
        if "ttsSendMode" in values and values["ttsSendMode"] not in TTS_SEND_MODES:
            raise ValueError("语音发送方式无效")
        if "asrLanguage" in values and values["asrLanguage"] not in ASR_LANGUAGES:
            raise ValueError("ASR 识别语言无效")
        if "asrUseItn" in values and type(values["asrUseItn"]) is not bool:
            raise ValueError("ASR 文本规范化必须是布尔值")
        if "asrThreads" in values and (type(values["asrThreads"]) is not int
                                        or values["asrThreads"] not in ASR_THREADS):
            raise ValueError("ASR 性能档仅支持 2 或 3 线程")
        if (values["asrEnabled"] and asr not in asr_names) or (tts and tts not in tts_names):
            raise ValueError("模型必须从已安装列表中选择")
        current = self._config()
        provider = values.get("ttsProvider", current.get("ttsProvider", "builtin"))
        if provider not in ("builtin", "external"):
            raise ValueError("无效的 TTS 服务类型")
        port = tts_port({"ttsPort": values.get("ttsPort", tts_port(current))})
        service = values.get("ttsService", current.get("ttsService", ""))
        if not isinstance(service, str) or (service and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_@.-]{0,127}", service)):
            raise ValueError("无效的 TTS 系统服务名")
        for key in ("asrAutoStart", "ttsAutoStart"):
            if key in values and type(values[key]) is not bool:
                raise ValueError("随唤醒启动必须是布尔值")
        if values["ttsEnabled"] and not tts and provider == "builtin":
            raise ValueError("先选择 TTS 模型再启用")
        with self._lock:
            if not (self.root / "extensions" / QQ_NAME).is_dir():
                raise ValueError("请先安装 QQ 插件")
            original = self.config_path.read_bytes() if self.config_path.is_file() else None
            data = self._config()
            if "ttsVoice" not in values:
                current_voice = data.get("ttsVoice")
                voice = (current_voice if isinstance(current_voice, str)
                         and current_voice in available_voices else "default")
            send_mode = values.get("ttsSendMode", data.get("ttsSendMode", "on_demand"))
            language = values.get("asrLanguage", data.get("asrLanguage", "auto"))
            use_itn = values.get("asrUseItn", data.get("asrUseItn", True))
            threads = values.get("asrThreads", data.get("asrThreads", ASR_DEFAULT_THREADS))
            if (send_mode not in TTS_SEND_MODES or language not in ASR_LANGUAGES
                    or type(use_itn) is not bool or type(threads) is not int
                    or threads not in ASR_THREADS):
                raise ValueError("现有语音配置无效，请检查安装副本")
            data["transcribeVoice"] = values["asrEnabled"]
            if asr in asr_names:
                data["asrModelDir"] = str((self.models / "asr" / asr).resolve())
            data["asrLanguage"] = language
            data["asrUseItn"] = use_itn
            data["asrThreads"] = threads
            data["ttsEnabled"] = values["ttsEnabled"]
            data["ttsModel"] = tts
            data["ttsVoice"] = voice
            data["ttsSendMode"] = send_mode
            if self._running("tts") and (port != tts_port(current) or provider != current.get("ttsProvider", "builtin")
                                          or tts != current.get("ttsModel") or service != current.get("ttsService", "")):
                raise ValueError("请先停止当前 TTS，再切换合成模型、服务或端口")
            data.update(ttsProvider=provider, ttsPort=port, ttsService=service)
            for key in ("ttsAutoStart", "asrAutoStart"):
                if key in values:
                    data[key] = values[key]
            data["ttsUrl"] = f"http://127.0.0.1:{port}" if values["ttsEnabled"] else ""
            http = urlsplit(str(data.get("httpUrl") or ""))
            ws = urlsplit(str(data.get("wsUrl") or ""))
            can_reload = (bool(data.get("privates") or data.get("groups"))
                          and http.scheme in ("http", "https") and bool(http.hostname)
                          and ws.scheme in ("ws", "wss") and bool(ws.hostname))
            self._commit_config(data, original, reload if can_reload else None)
        return self.read()

    def voice_corpus(self, values: dict) -> dict:
        """Local WebUI operator only; never registered as an Agent/QQ tool."""
        self._require_open()
        require_local_runtime(self._process_lifecycle)
        from telyincore.voice_corpus import VoiceCorpus
        corpus = VoiceCorpus(self.root, self.config_path)
        action = values.get("action", "status")
        if action == "configure":
            return {"ok": True, **corpus.configure(values.get("enabled"))}
        if action == "review":
            return {"ok": True, **corpus.review(values.get("id"), values.get("state"))}
        if action == "audio":
            return {"ok": True, **corpus.audio(values.get("id"))}
        if action == "list":
            return {"ok": True, **corpus.status(), "samples": corpus.listing(
                values.get("state", "pending"), values.get("offset", 0))}
        if action == "status":
            return {"ok": True, **corpus.status()}
        raise ValueError("素材库操作不存在")

    def preview_voice(self, model: str, voice: str,
                      text: str = DEFAULT_PREVIEW_TEXT) -> dict[str, Any]:
        """Audition only the currently loaded model and an installed voice preset."""
        self._require_open()
        require_local_runtime(self._process_lifecycle)
        self._require_voice_enabled()
        if not isinstance(model, str) or not isinstance(voice, str):
            raise ValueError("模型或音色无效")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 200:
            raise ValueError("试听文字需为 1—200 字")
        with self._lock:
            data = self._config()
            port = tts_port(data)
            selected = str(data.get("ttsModel") or "")
            if data.get("ttsProvider") != "external" and (not model or model != selected or model not in self._models()[1]):
                raise ValueError("请先保存并启动所选 TTS 模型")
            if voice not in {one["id"] for one in self._voices()}:
                raise ValueError("音色尚未在本机安装")
        if not _open_port(port):
            raise ValueError("请先启动 TTS 服务")
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False,
                              timeout=httpx.Timeout(90.0, connect=3.0)) as client:
                response = client.post("/api/generate", data={
                    "text": text.strip(),
                    "voice": voice, "instruction": "用自然、温柔的语气说。"
                    if voice == "telyin" else "用自然、平静的语气说。",
                    "seed": "42", "model_stream": "true",
                    "split_mode": "sentence" if voice == "telyin" else "paragraph",
                    "pause_ms": "360" if voice == "telyin" else "180"})
                response.raise_for_status()
                result = response.json()
                # An old TTS process ignores voice=default and could synthesize
                # with the private reference. Never play or send that output.
                if not isinstance(result, dict) or result.get("voice") != voice:
                    raise ValueError("TTS 服务尚未支持音色预设，请更新并重启服务")
                audio_url = result.get("audio_url")
                if not isinstance(audio_url, str) or not re.fullmatch(
                        r"/audio/[0-9a-f]{32}", audio_url):
                    raise ValueError("TTS 未返回有效的试听音频")
                with client.stream("GET", audio_url) as audio:
                    audio.raise_for_status()
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in audio.iter_bytes():
                        size += len(chunk)
                        if size > 8 * 1024 * 1024:
                            raise ValueError("试听音频超过 8 MB")
                        chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise ValueError("TTS 试听请求失败，请检查服务状态和日志") from exc
        wav = b"".join(chunks)
        if len(wav) < 44 or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
            raise ValueError("TTS 返回的试听音频不是有效 WAV")
        return {"ok": True, "audio": base64.b64encode(wav).decode("ascii")}

    def initial_snowluma_password(self):
        self._require_open()
        with self._snow_lifecycle:
            process = self._owned.get('snowluma')
            publisher = self._companion_lifecycle
            if publisher is None or process is None or process.retired:
                return {'ok': False, 'said': '请先启动当前 Bot 的 SnowLuma。'}
            result = publisher.request_companion('companion-initial-password', generation=process.generation)
            if not result.get('password'):
                return {'ok': False, 'said': '当前没有可查看的初始密码。若尚未改密，请正常休止并重新唤醒此 Bot。'}
            return {'ok': True, 'username': 'admin', 'password': result['password']}

    def _require_voice_enabled(self) -> None:
        if self._plugin_enabled is not None and not self._plugin_enabled():
            raise ValueError("请先在插件市场启用 QQ 插件。")

    def _tts_healthy(self, port):
        if not _open_port(port):
            self._tts_health_cache = None
            return False
        cached = self._tts_health_cache
        now = monotonic()
        if cached and cached[0] == port and now - cached[1] < 2:
            return cached[2]
        try:
            with httpx.Client(trust_env=False, timeout=0.4) as client:
                response = client.get(f"http://127.0.0.1:{port}/health")
                health = response.json() if response.status_code == 200 else {}
                ok = isinstance(health, dict) and health.get("status") == "ok" and bool(health.get("model"))
        except (httpx.HTTPError, ValueError):
            ok = False
        self._tts_health_cache = (port, now, ok)
        return ok

    def _start_asr(self):
        self._require_voice_enabled()
        data = self._config()
        model = Path(effective_asr_directory(data.get("asrModelDir"), self.models.parent) or "")
        if not data.get("transcribeVoice") or model.parent.resolve() != (self.models / "asr").resolve():
            raise ValueError("请先启用 ASR 并保存识别模型")
        if not all((model / name).is_file() for name in ASR_FILES):
            raise ValueError("ASR 模型或 VAD 不完整")
        self._asr.start(model, data.get("asrLanguage", "auto"), data.get("asrUseItn", True),
                        data.get("asrThreads", ASR_DEFAULT_THREADS))
        self._asr_error = ""

    def sync_voice_services(self, *, enabled, wake=False):
        if self._process_lifecycle is not None:
            return
        with self._lock:
            self._require_open()
            data = self._config()
            if not enabled or not data.get("transcribeVoice"):
                self._asr.stop()
                self._asr_error = ""
            elif self._asr.engine is not None or (wake and data.get("asrAutoStart", True)):
                try:
                    self._start_asr()
                except (ValueError, OSError, RuntimeError, ImportError) as error:
                    self._asr.stop()
                    self._asr_error = str(error)
        if not enabled or not data.get("ttsEnabled") or (wake and data.get("ttsAutoStart", True)):
            self.sync_tts_with_plugin(enabled=enabled)

    def sync_tts_with_plugin(self, *, enabled: bool) -> None:
        """Reconcile the current Bot's own service with its saved voice switch."""
        if self._process_lifecycle is not None:
            return
        with self._lock:
            self._require_open()
            self._tts_start_error = ""
            if not enabled or self._config().get("ttsEnabled") is not True:
                if "tts" in self._owned:
                    self._stop_owned_tts()
                return
            if self._running("tts"):
                return
            try:
                self._start_tts()
            except (ValueError, OSError, RuntimeError) as error:
                self._tts_start_error = str(error)

    def _stop_owned_tts(self) -> None:
        process = self._owned["tts"]
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            raise RuntimeError("TTS 尚未退出，请稍后重试。") from None
        close = getattr(process, "close", None)
        if callable(close):
            close()
        self._owned.pop("tts")

    def _start_tts(self) -> None:
        self._require_voice_enabled()
        if self._running("tts"):
            return
        if "tts" in self._owned:
            self._stop_owned_tts()
        data = self._config()
        model = str(data.get("ttsModel") or "")
        _, names = self._models()
        if data.get("ttsProvider") == "external" and self._process_lifecycle is None:
            if not data.get("ttsEnabled"):
                raise ValueError("请先启用 TTS")
            port = tts_port(data)
            if self._tts_healthy(port):
                return
            if _open_port(port):
                raise ValueError("端口已占用，但未检测到兼容的 TTS 服务")
            external_unit(data.get("ttsService"), "start", port)
            return
        if not data.get("ttsEnabled") or model not in names:
            raise ValueError("先启用并选择 TTS 模型")
        voice = str(data.get("ttsVoice") or "default")
        issue = self._tts_runtime_issue(voice)
        if issue:
            raise ValueError(issue)
        port = tts_port(data)
        if _open_port(port):
            raise ValueError("TTS 端口已占用，请选择外部服务或更换端口")
        py = self._tts_python()
        entry = self._tts_entry()
        # Experimental requests compare a fixed parent-approved plan;
        # never enumerate or forward this controller's whole environment.
        env = {} if self._process_lifecycle is not None else dict(os.environ)
        env["TELYIN_TTS_RUNTIME"] = str(self.tts_runtime)
        env["TELYIN_TTS_MODEL_DIR"] = str(self.models / "tts" / model)
        if self._process_lifecycle is None:
            env.pop("TELYIN_TTS_PORT", None)
            if port != 18084:
                env["TELYIN_TTS_PORT"] = str(port)
        self._start("tts", [str(py), "-u", str(entry)], self.tts_runtime, env)
        self._tts_start_error = ""

    def start_snowluma_with_bot(self, *, enabled: bool = False) -> None:
        """Remember first enablement and start this Bot's managed bridge."""
        instance = self.snow_instance
        publisher = self._companion_lifecycle
        if instance is None or publisher is None or self._process_lifecycle is not None:
            return
        from runtime_identity import safe_path
        from service_layout import node_executable
        with self._snow_lifecycle:
            self._require_open()
            marker = safe_path(self.root.parent, instance.root / 'start-with-bot')
            if enabled and not marker.exists():
                marker.touch(exist_ok=True)
            if not marker.is_file():
                return
            if self._snow_stopping or self._snow_rejected_pins:
                raise ValueError('SnowLuma 上一次操作尚未收尾')
            with self._lock:
                if self._running('snowluma'):
                    return
                if not (self.snowluma_root / 'index.mjs').is_file() or not node_executable(self.root):
                    raise ValueError('请先从启动器准备 Node 和 SnowLuma')
                self._owned['snowluma'] = publisher.start_companion()
                try:
                    self._remember_snowluma()
                except BaseException:
                    self._snow_stopping = True
                    raise

    def action(self, name: str) -> dict[str, Any]:
        self._require_open()
        if self._process_lifecycle is not None and name == "snowluma_start_service":
            name = "snowluma_start"
        if (self._process_lifecycle is not None and name.startswith("snowluma_")
                and 'snowluma' not in self._process_lifecycle.configured_processes()):
            raise ValueError("SnowLuma lifecycle is not mediated yet; no local fallback")
        if not name.startswith("snowluma_"):
            return self._action(name)
        if not self._snow_lifecycle.acquire(blocking=False):
            raise ValueError("接入生命周期操作正在进行，请等待")
        try:
            self._require_open()
            stopping = name in ("snowluma_stop", "snowluma_stop_installation")
            if self._snow_rejected_pins and not stopping:
                raise ValueError('SnowLuma observer cleanup pending')
            if self._snow_stopping and not stopping:
                if self.recovery.state().get("workerAlive"):
                    raise ValueError("恢复任务正在停止，不能重新恢复")
                # A reentrant action inside cancel must not start a new worker.
                raise ValueError("停止操作未完成，不能重新恢复")
            if stopping:
                self._snow_stopping = True
            result = self._action(name)
            self._snow_stopping = False
            return result
        finally:
            self._snow_lifecycle.release()

    def _mediated_fixed_process_action(self, name: str) -> dict[str, Any]:
        """Process-only opt-in. Never config/discovery/login/recovery or fallback."""
        self._require_open()
        bridge = self._process_lifecycle
        if (not bridge._fixed_contract or name not in (
                'snowluma_start', 'snowluma_stop', 'tts_start', 'tts_stop')):
            raise ValueError('process-only capability; login/recovery/adoption unavailable')
        kind, verb = name.rsplit('_', 1)
        if kind not in bridge.configured_processes():
            raise ValueError('process-only capability; requested slot unavailable')
        if not self._lock.acquire(blocking=False):
            raise ValueError('fixed process operation busy')
        try:
            self._require_open()
            if verb == 'start':
                if kind in self._owned:
                    raise ValueError('parent process handle retained; cleanup required')
                self._owned[kind] = bridge.start_approved_process(kind)
            else:
                process = self._owned.get(kind)
                if process is None:
                    raise ValueError('no retained parent-owned ' + kind + ' process')
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=8)  # parent independently retires native identity
                self._owned.pop(kind, None)
        finally:
            self._lock.release()
        return self.read()  # capability view, NEVER account/QQ online evidence

    def _action(self, name: str) -> dict[str, Any]:
        self._require_open()
        if name == "tts_start":
            self._require_voice_enabled()
        if self._process_lifecycle is not None:
            if self._process_lifecycle._metadata_only:
                raise ValueError('metadata-only lifecycle has no process capability')
            if (self._process_lifecycle.slot == 'snowluma'
                    or len(self._process_lifecycle.configured_processes()) > 1):
                return self._mediated_fixed_process_action(name)
            if name.startswith("snowluma_"):
                raise ValueError("SnowLuma lifecycle is not mediated yet; no local fallback")
        if name in ("asr_start", "asr_stop"):
            require_local_runtime(self._process_lifecycle)
            with self._lock:
                if name == "asr_start":
                    self._start_asr()
                else:
                    self._asr.stop()
                    self._asr_error = ""
            return self.read()
        confirmation=re.fullmatch(r'snowluma_confirm_([0-9a-f]{32})',name)
        if confirmation:
            self.recovery.confirm_process(confirmation.group(1))
            return self.read()
        if name not in ("snowluma_start", "snowluma_stop", "snowluma_stop_installation",
                        "snowluma_recover", "snowluma_start_service",
                        "tts_start", "tts_stop"):
            raise ValueError("不认识的进程动作")
        kind, verb = name.split("_", 1)
        if kind == "snowluma" and verb == "stop_installation" and getattr(self, '_companion_lifecycle', None) is not None:
            raise ValueError('default companion capability forbids external discovery/adoption')
        if kind == "snowluma" and verb == "stop_installation":
            if self.snow_instance is not None:raise ValueError("独立Bot不接管同安装目录的其他进程")
            if self.bot_scoped:
                raise ValueError("不能停止其他 Bot 或外部启动的 SnowLuma；仅可停止本 Bot 托管的进程")
            self.recovery.cancel()
            with self._lock:
                # Explicit user action, limited to the exact configured entry.
                # Discovery itself grants no process ownership.
                identity = discover_snowluma(self.snowluma_root / "index.mjs")
                if identity is None:
                    raise ValueError("无法唯一确认此安装目录的 SnowLuma，未停止任何进程")
                proc = VerifiedProcess(identity)
                try:
                    proc.terminate()
                    proc.wait(timeout=8)
                finally:
                    proc.close()
                # This explicit pin is not the retained parent's retirement
                # receipt. Leave existing handles/records for their own cleanup.
                self._candidate_at = -100.0
            return self.read()
        if (kind == "snowluma" and verb in ("start", "start_service", "recover") and self.bot_scoped
                and not self._running("snowluma") and _open_port(self.management_port)):
            state = self.recovery.state()
            if not state.get("hasCredentials") or not state.get("targetUin"):
                raise ValueError("管理端归属冲突：请先保存本 Bot 的目标 QQ 号与管理凭据，再验证接入")
        if kind == "snowluma" and verb == "recover":
            if not _open_port(self.management_port):
                raise ValueError("请先从插件页启动 SnowLuma")
            self.recovery.start()
            return self.read()
        if kind == "snowluma" and verb == "stop":
            self.recovery.cancel()
        with self._lock:
            if kind == "tts" and verb == "stop" and "tts" in self._owned:
                self._stop_owned_tts()
            elif kind == "tts" and verb == "stop" and self._process_lifecycle is None and self._config().get("ttsProvider") == "external":
                data = self._config()
                if not self._tts_healthy(tts_port(data)):
                    raise ValueError("配置端口没有就绪的 TTS")
                external_unit(data.get("ttsService"), "stop", tts_port(data))
                self._tts_health_cache = None
            elif verb == "stop":
                proc = self._owned.get(kind)
                if proc is None:
                    raise ValueError("这不是页面启动的进程，不能从这里停止")
                # A retained child can exit naturally or after an earlier timeout.
                # Still wait/retire that SAME handle; never adopt or rediscover.
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    raise RuntimeError("进程尚未退出，请稍后重试") from None
                if kind == "snowluma":
                    self._clear_snow_owner(proc)
                    self._candidate_at = -100.0
                close = getattr(proc, "close", None)
                if callable(close):
                    close()
                # Observation/wait/record/close failure retains cleanup authority.
                self._owned.pop(kind, None)
            elif kind == "snowluma":
                if self.snow_instance is not None and self._owned.get("snowluma") is None:
                    from snowluma_instance import assert_spawn_available
                    assert_spawn_available(self.snow_instance)
                ws = urlsplit(str(self._config().get("wsUrl") or ""))
                if not (_open_port(ws.port or 3001) or _open_port(self.management_port)
                        or self._running("snowluma")):
                    if getattr(self, '_companion_lifecycle', None) is not None:
                        prior=self._owned.get('snowluma')
                        if prior is not None:
                            self.recovery.cancel()
                            check=getattr(self.recovery,'assert_client_drained',None)
                            if callable(check):check()
                        from bot_parent_account_grant import AccountAdmissionConflict
                        try:self._owned['snowluma']=self._companion_lifecycle.start_companion()
                        except AccountAdmissionConflict as error:
                            raise ValueError(str(error)) from None
                        except (ValueError,OSError) as error:
                            raise ValueError('SnowLuma 启动未确认。请把 Bot 名称和这条提示发给维护者。') from error
                    else:
                        from service_layout import node_executable
                        node = node_executable(self.root)
                        entry = self.snowluma_root / "index.mjs"
                        if not entry.is_file():
                            raise ValueError("SnowLuma 未安装：请解压官方发行包，使 index.mjs 位于安装目录根部")
                        if node is None or not self.installation_status()["nodeAvailable"]:
                            raise ValueError("Node.js 不可用：请安装官方运行环境后重新打开 Core 启动窗口")
                        env = dict(os.environ)
                        # Recovery selects one process. Never enable upstream's
                        # indiscriminate 'inject every discovered process' option.
                        env["SNOWLUMA_HOOK_AUTOLOAD"] = "0"
                        cwd=self.snowluma_root
                        if self.snow_instance is not None:
                            from snowluma_instance import assert_spawn_available
                            assert_spawn_available(self.snow_instance)
                            cwd=self.snow_instance.root
                            env.update(self.snow_instance.environment())
                            env['SNOWLUMA_TARGET_CONFIG']=str(self.recovery.path)
                        self._start("snowluma", [node, str(entry)], cwd, env)
                    try:
                        self._remember_snowluma()
                    except BaseException:
                        self._snow_stopping = True  # retain original child for cleanup-only retry
                        raise
                if verb == "start":
                    self.recovery.start()
            else:
                self._start_tts()
        return self.read()

    def snowluma_session_status(self) -> dict[str, bool]:
        self._require_open()
        if self._process_lifecycle is not None or not isinstance(self.recovery, Recovery):
            return {"available": False, "hasSession": False}
        return {"available": True, "hasSession": self.recovery.has_management_session()}

    def clear_snowluma_session(self) -> dict[str, bool]:
        self._require_open()
        if self._process_lifecycle is not None or not isinstance(self.recovery, Recovery):
            raise ValueError("当前运行方式不支持清除 SnowLuma 登录会话")
        with self._snow_lifecycle:
            removed = self.recovery.clear_management_session()
        return {"ok": True, "cleared": removed}

    def save_recovery(self, values: dict[str, Any]) -> dict[str, Any]:
        self._require_open()
        if self._process_lifecycle is not None:
            if (not self._recovery_store_enabled
                    or type(self.recovery) is not MediatedRecoverySettings):
                raise ValueError("experimental lifecycle does not accept QQ credentials")
        publisher=getattr(self,'_companion_lifecycle',None)
        if publisher is not None:
            # HOST denial precedes local store read/encryption/backup. Failure
            # must not write; this is NOT a remote resource-drain acknowledgement.
            with self._snow_lifecycle:
                self._require_open()
                if self.snow_instance is not None and self._running('snowluma'):
                    state = self.recovery.state()
                    target = state.get('targetUin')
                    if target and values.get('targetUin') == target and not state.get('busy'):
                        # A new wake has no live account grant until the first
                        # authenticated operation. Pin the existing account before
                        # opening the known-account credential update transaction.
                        publisher.check_account_scope()
                publisher.revoke_account_scope()
                self.recovery.save(values)
                publisher.acknowledge_account_configuration()
        else:
            self.recovery.save(values)
        return self.read()

    def _start(self, kind: str, argv: list[str], cwd: Path,
               env: dict[str, str] | None = None) -> None:
        self._require_open()
        if self._running(kind):
            raise ValueError("页面启动的进程已经在运行")
        if self._process_lifecycle is not None:
            self._owned[kind] = self._process_lifecycle.start(kind, argv, cwd, env)
            return  # parent owns spawn/logs/handle; never fall through to local Popen
        self.logs.mkdir(parents=True, exist_ok=True)
        out = (self.logs / f"{kind}.out.log").open("ab")
        err = (self.logs / f"{kind}.err.log").open("ab")
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            startup = None
            if os.name == "nt":
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = subprocess.SW_HIDE
            self._require_open()
            self._owned[kind] = subprocess.Popen(
                argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=out, stderr=err, creationflags=flags, startupinfo=startup)
        finally:
            out.close()
            err.close()

    def retain_installer(self, name, owner):
        from telyincore.owned_blocking import InstallationOwner
        if not isinstance(owner, InstallationOwner):
            raise TypeError('owned installer required')
        with self._lock:
            self._require_open()
            if name in self._installation_owners and self._installation_owners[name] is not owner:
                raise ValueError('installer owner cannot be replaced')
            self._installation_owners[name] = owner

    def close(self) -> None:
        self._closed.set()
        if not self._closing.acquire(blocking=False):
            raise ValueError('QQ control close busy; ownership retained')
        try:
            if self._asr is not None:
                self._asr.close()
            self._finish_close()
        finally:
            self._closing.release()

    def _finish_close(self) -> None:
        """Permanently deny new lifecycle work; keep SnowLuma alive by policy.

        Cleanup failures retain owned handles, propagate, and permit cleanup-only
        retry. Closing this facade never attests account/descendant offline.
        """
        self._closed.set()  # before any user callback, join or lock acquisition
        primary = None
        failures = []
        def attempt(callback):
            nonlocal primary
            try:
                callback()
                return True
            except BaseException as error:
                failures.append(error)
                if primary is None:
                    primary = error
                return False
        for name, owner in tuple(self._installation_owners.items()):
            def drain_installer(owner=owner, name=name):
                if owner.close(timeout=0) is not True:
                    error = RuntimeError('installation cleanup incomplete: ' + name)
                    error.installation_owner = owner
                    raise error
            attempt(drain_installer)
        if self._protocol_health is not None:
            attempt(self._protocol_health.close)
        closer = getattr(self.recovery, 'close', None)
        attempt(closer if callable(closer) else self.recovery.cancel)
        drained = getattr(self.recovery, 'assert_client_drained', None)
        if callable(drained):attempt(drained)
        with self._lock:
            for pinned in list(getattr(self, '_snow_rejected_pins', [])):
                if attempt(pinned.close):
                    self._snow_rejected_pins.remove(pinned)
            for kind, proc in list(self._owned.items()):
                if kind == 'snowluma':
                    closer = getattr(proc, 'close', None)
                    if callable(closer) and not attempt(closer):
                        continue  # keep failed native observer; do not terminate SnowLuma
                    self._owned.pop(kind, None)  # intentionally kept alive; not retired/offline
                    self._owner_receipt = None
                    continue
                def cleanup():
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=8)
                    elif self._process_lifecycle is not None:
                        proc.wait(timeout=8)
                if attempt(cleanup):
                    self._owned.pop(kind, None)
        if primary is not None:
            primary._qq_cleanup_errors = tuple(failures)
            raise primary
