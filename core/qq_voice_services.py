"""Bot-scoped, resident SenseVoice instances shared with QQ input workers."""
from pathlib import Path
from threading import RLock

_sessions = {}
_registry_lock = RLock()


def session_for(config_path):
    with _registry_lock:
        return _sessions.get(Path(config_path).resolve())


class ASRSession:
    def __init__(self, config_path):
        self.path = Path(config_path).resolve()
        self.lock = RLock()
        self.engine = None
        self.settings = None
        with _registry_lock:
            _sessions[self.path] = self

    def start(self, model, language, use_itn, threads):
        settings = (model, language, use_itn, threads)
        with self.lock:
            if self.engine is not None and self.settings == settings:
                return
            self.engine = self._load(*settings)
            self.settings = settings

    @staticmethod
    def _load(model, language, use_itn, threads):
        import sherpa_onnx
        vad_path = model / "silero_vad.onnx"
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(vad_path)
        config.silero_vad.threshold = 0.5
        config.silero_vad.min_speech_duration = 0.1
        config.silero_vad.min_silence_duration = 0.5
        config.silero_vad.max_speech_duration = 30
        config.sample_rate = 16000
        config.num_threads = 1
        config.provider = "cpu"
        vad = sherpa_onnx.VoiceActivityDetector(
            config, buffer_size_in_seconds=31)
        recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model / "model.int8.onnx"),
            tokens=str(model / "tokens.txt"), num_threads=threads,
            provider="cpu", sample_rate=16000, language=language,
            use_itn=use_itn)

        return vad, recognizer

    def stop(self):
        with self.lock:
            self.engine = self.settings = None

    def close(self):
        self.stop()
        with _registry_lock:
            if _sessions.get(self.path) is self:
                del _sessions[self.path]

    def decode(self, samples):
        import numpy as np
        with self.lock:
            if self.engine is None:
                raise RuntimeError("ASR 已停止")
            vad, recognizer = self.engine
            speech = False
            vad.reset()
            try:
                for offset in range(0, len(samples), 512):
                    frame = samples[offset:offset + 512]
                    if len(frame) < 512:
                        frame = np.pad(frame, (0, 512 - len(frame)))
                    vad.accept_waveform(frame)
                    if not vad.empty():
                        speech = True
                        break
                if not speech:
                    vad.flush()
                    speech = not vad.empty()
            finally:
                vad.reset()
            if not speech:
                return None
            stream = recognizer.create_stream()
            stream.accept_waveform(16000, samples)
            recognizer.decode_stream(stream)
            return stream.result.text.strip()


_OLD_QQ_ENTRIES = frozenset({
    "6657f66364341d7d8e5394d2f94cb260fdbb28bc3dbef9850a2b6d5b10c80dbd",  # Linux before typed media IDs
    "c882d08f6e0035f59591dac73b7a3967fe60d26369bf188982143576a7d44c23",  # shared plugin before typed media IDs
    "ef4355ddf7d4c6e0e2f11c9da677daec4855d0aad2125342177326aa97fde842",  # stock beta.0.5.4, Windows and Linux
    "66eec7b52117bf47d4a8146362f5aa9f27ddc3631cfb824194def53f05c09d87",
    "0ce550cd44aaa5f1393e17f503ac819078af45439be14b684243009ac19d5ae2",
})


def _stock_entry(root, installed):
    import hashlib
    from runtime_identity import safe_path

    try:
        safe_path(root, installed)
        if not installed.is_file() or installed.stat().st_nlink != 1:
            return None
        raw = installed.read_bytes()
    except (FileNotFoundError, ValueError):
        return None
    return raw if hashlib.sha256(raw).hexdigest() in _OLD_QQ_ENTRIES else None


def upgrade_qq_voice_entry(root, bundled):
    """Refresh a recognized distributed QQ entry before the plugin loader acquires its lease."""
    from contextlib import ExitStack
    import os
    import stat
    import tempfile
    from telyincore.plugin_package_lock import PackageLock

    installed = root / "extensions/core-infinity-qq/index.py"
    before = _stock_entry(root, installed)
    if before is None:
        return
    replacement = bundled.read_bytes()
    if replacement == before:
        return
    with ExitStack() as stack:
        try:
            stack.enter_context(PackageLock(root, "core-infinity-qq", exclusive=True))
        except (BlockingIOError, ValueError):
            # A running plugin keeps its entry until a later wake.
            return
        if _stock_entry(root, installed) != before:
            return
        mode = stat.S_IMODE(installed.stat().st_mode)
        with tempfile.NamedTemporaryFile(dir=installed.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stack.callback(temporary.unlink, missing_ok=True)
            stream.write(replacement)
        temporary.chmod(mode)
        if _stock_entry(root, installed) == before:
            os.replace(temporary, installed)


def tts_port(data):
    from urllib.parse import urlsplit
    value = data.get("ttsPort")
    if value is None:
        value = urlsplit(data.get("ttsUrl") or "http://127.0.0.1:18084").port or 18084
    if type(value) is not int or not 1024 <= value <= 65535:
        raise ValueError("TTS 端口须为 1024 到 65535 的整数")
    return value


def external_unit(name, action, port):
    """Manage only the local service explicitly named by the operator."""
    import os
    import re
    import subprocess
    if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_@.-]{0,127}", name):
        raise ValueError("请填写已注册的本机 TTS 服务名")
    if os.name == "nt":
        argv = ["sc.exe", action, name]
    else:
        unit = name if name.endswith(".service") else name + ".service"
        argv = ["systemctl", "--user", action, unit]
    if action == "stop" and not unit_listens(name, port):
        raise ValueError("此端口的监听进程与所填系统服务不匹配，请检查服务名和端口")
    try:
        subprocess.run(argv, timeout=20, capture_output=True, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("TTS 系统服务操作失败，请核对服务名和当前用户权限") from exc


def unit_listens(name, port):
    """Tie an explicit stop to the selected system service and the configured endpoint."""
    import os
    import re
    import subprocess
    try:
        if os.name == "nt":
            from snowluma_connection import management_listener_owners
            result = subprocess.run(["sc.exe", "queryex", name], timeout=3, capture_output=True)
            match = re.search(rb"(?im)^\s*PID\s*:\s*(\d+)", result.stdout)
            if result.returncode or not match:
                return False
            owners = management_listener_owners(port) or []
            return any(row["pid"] == int(match[1]) and row["address"] in ("127.0.0.1", "0.0.0.0")
                       for row in owners)
        unit = name if name.endswith(".service") else name + ".service"
        result = subprocess.run(["systemctl", "--user", "show", "--property=ControlGroup",
                                 "--value", unit], timeout=3, capture_output=True, text=True, check=True)
        group = result.stdout.strip()
        if not group.startswith("/") or ".." in Path(group).parts or group == "/":
            return False
        group_path = Path("/sys/fs/cgroup") / group.lstrip("/")
        inodes = {"socket:[" + fields[9] + "]" for row in Path("/proc/net/tcp").read_text().splitlines()[1:]
                  if (fields := row.split()) and fields[3] == "0A"
                  and fields[1] in (f"0100007F:{port:04X}", f"00000000:{port:04X}")}
        for procs in group_path.rglob("cgroup.procs"):
            for pid in procs.read_text().split():
                for fd in Path("/proc", pid, "fd").glob("*"):
                    try:
                        if os.readlink(fd) in inodes:
                            return True
                    except FileNotFoundError:
                        continue
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return False
