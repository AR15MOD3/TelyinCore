"""Prepare optional voice inference environments and verify actual inference."""
from pathlib import Path
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from zipfile import ZipFile

from runtime_identity import safe_path
from voice_download import stage_lock

CORE = Path(__file__).resolve().parent
ASSETS = CORE / "scripts/voice"
PYTHON_ARCHIVE = "python-3.11.12-linux-x64.tar.gz"
PYTHON_SHA256 = "a8c2320f1df72d02d58f3f7ab0899bff7946e5efcc723ae2ff3113ad8bb645c0"


def python_at(runtime):
    return runtime / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")


def model_stamp(model):
    return {p.relative_to(model).as_posix(): [p.stat().st_size, p.stat().st_mtime_ns]
            for p in sorted(model.rglob("*")) if p.is_file() and not p.name.startswith(".")
            and p.suffix in (".onnx", ".pt", ".yaml", ".safetensors", ".json", ".txt")}


def marker_path(kind, runtime, model):
    return (runtime if kind == "tts" else model) / (".core-" + kind + "-ready.json")


def ready(kind, runtime, model):
    try:
        marker = json.loads(marker_path(kind, runtime, model).read_bytes())
        return (marker["model"] == str(model.resolve()) and marker["files"] == model_stamp(model)
                and marker["installer"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                and (kind != "tts" or python_at(runtime).is_file()))
    except (OSError, ValueError, KeyError):
        return False


def network_env(source, proxy_root):
    from telyincore.model_proxy import auxiliary_proxy
    env = {k: v for k, v in os.environ.items()
           if "PROXY" not in k.upper() and not k.startswith(("PIP_", "UV_"))}
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1",
               PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    if source == "official" and proxy_root:
        proxy = auxiliary_proxy(proxy_root, "downloads")
        if proxy:
            env.update(HTTPS_PROXY=proxy, HTTP_PROXY=proxy)
    return env


class RuntimeSetup:
    def __init__(self, kind, runtime, entry, proxy_root=None):
        self.kind, self.runtime, self.entry = kind, Path(runtime), Path(entry)
        self.proxy_root = proxy_root

    def ready(self, model):
        return ready(self.kind, self.runtime, model)

    def status(self, owner, phase, message):
        owner._checkpoint()
        with owner.lock:
            owner.state.update(phase=phase, message=message, bytesPerSecond=0)

    def command(self, owner, argv, env, log, *, timeout=1800, required=True):
        owner._checkpoint()
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("ab") as output:
            proc = subprocess.Popen([str(a) for a in argv], env=env, stdout=output, stderr=output,
                                    start_new_session=os.name != "nt")
            deadline = time.monotonic() + timeout
            try:
                while proc.poll() is None:
                    owner._checkpoint()
                    if time.monotonic() >= deadline:
                        raise ValueError("语音环境准备超时，可重试。日志位于 " + str(log))
                    time.sleep(.2)
            finally:
                if proc.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        if os.name == "nt":
                            proc.kill()
                        else:
                            os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
        if proc.returncode and required:
            raise ValueError("语音环境准备失败，可继续重试。日志位于 " + str(log))
        return proc.returncode == 0

    def pip(self, owner, py, packages, env, log, source, *options):
        index = "https://pypi.tuna.tsinghua.edu.cn/simple" if source == "mirror" else "https://pypi.org/simple"
        self.command(owner, [py, "-m", "pip", "install", "--index-url", index,
                             "--retries", "3", *options, *packages], env, log)

    def prepare_python(self, owner, env, log):
        py = python_at(self.runtime)
        if py.exists():
            return py
        if os.name == "nt":
            if sys.version_info[:2] != (3, 11):
                raise ValueError("Windows 语音环境需要随 Core 启动器提供的 Python 3.11。")
            base_python = sys.executable
        else:
            if platform.machine().lower() not in ("amd64", "x86_64"):
                raise ValueError("随包 TTS Python 当前支持 Linux x86_64。")
            base_python = self.runtime / "python/bin/python3.11"
            if not base_python.exists():
                archive = CORE / "linux-components" / PYTHON_ARCHIVE
                if hashlib.sha256(archive.read_bytes()).hexdigest() != PYTHON_SHA256:
                    raise ValueError("随包 TTS Python 校验失败，请重新解压完整部署包。")
                with tempfile.TemporaryDirectory(dir=self.runtime, prefix=".python-") as stage:
                    with tarfile.open(archive) as packed:
                        packed.extractall(stage, filter="data")
                    Path(stage, "python").rename(self.runtime / "python")
        self.command(owner, [base_python, "-m", "venv", str(self.runtime / ".venv")], env, log)
        return py

    def install_assets(self, owner):
        spec = json.loads((ASSETS / "runtime-assets.json").read_bytes())
        archive = ASSETS / "runtime-assets.zip"
        if hashlib.sha256(archive.read_bytes()).hexdigest() != spec["sha256"]:
            raise ValueError("随包语音运行资源校验失败。")
        with ZipFile(archive) as packed:
            for name, digest in spec["files"].items():
                owner._checkpoint()
                raw = packed.read(name)
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise ValueError("语音运行资源校验失败")
                path = safe_path(self.runtime, self.runtime / name)
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists() and path.read_bytes() == raw:
                    continue
                if path.exists():
                    before = path.read_bytes()
                    backup = self.runtime / ".core-backups" / hashlib.sha256(before).hexdigest()
                    backup.parent.mkdir(exist_ok=True)
                    if not backup.exists():
                        backup.write_bytes(before)
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                    stream.write(raw)
                os.replace(stream.name, path)

    def __call__(self, owner, model, source):
        self.status(owner, "environment", "正在检查语音运行环境…")
        base = self.runtime if self.kind == "tts" else model
        base.mkdir(parents=True, exist_ok=True)
        log = base / ".core-setup.log"
        env = network_env(source, self.proxy_root)
        with stage_lock(base):
            if self.ready(model):
                return
            if self.kind == "asr":
                probe = "import numpy, scipy, sherpa_onnx"
                py = sys.executable
                if not self.command(owner, [py, "-c", probe], env, log, required=False, timeout=60):
                    self.status(owner, "environment", "正在安装 ASR 识别、VAD 和音频处理依赖…")
                    self.pip(owner, py, ["sherpa-onnx>=1.13,<2", "numpy>=1.26,<3", "scipy>=1.11,<2"],
                             env, log, source)
                code = ("import base64,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                        "from voice_setup import check_asr; print(check_asr(Path(sys.argv[2]), "
                        "base64.b64encode(Path(sys.argv[3]).read_bytes()).decode()))")
                argv = [py, "-c", code, CORE, model, ASSETS / "asr-self-check.wav"]
            else:
                env.update(TELYIN_TTS_RUNTIME=str(self.runtime), TELYIN_TTS_MODEL_DIR=str(model),
                           HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
                py = python_at(self.runtime)
                argv = [py, "-u", self.entry, "--check-voice", "default"]
                # Preserve an existing working environment (including newer GPU wheels).
                if py.is_file():
                    self.status(owner, "selfcheck", "正在验证已有 TTS 环境和实际语音合成…")
                    if self.command(owner, argv, env, log, timeout=900, required=False):
                        self.mark_ready(model)
                        return
                py = self.prepare_python(owner, env, log)
                self.install_assets(owner)
                self.status(owner, "environment", "正在安装 TTS 推理依赖，首次需要下载数 GB…")
                self.pip(owner, py, ["setuptools==70.3.0", "wheel==0.45.1"], env, log, source)
                torch_index, torch_version = self.torch_source()
                if os.name == "nt" or torch_index in ("cpu", "cu128"):
                    torch_env = network_env("official", self.proxy_root)
                    self.status(owner, "environment", "正在从 PyTorch 官方源安装推理引擎（遵循下载代理设置）…")
                    self.command(owner, [py, "-m", "pip", "install", "--index-url",
                        "https://download.pytorch.org/whl/" + torch_index,
                        "torch==" + torch_version, "torchaudio==" + torch_version], torch_env, log)
                else:
                    self.pip(owner, py, ["torch==" + torch_version, "torchaudio==" + torch_version],
                             env, log, source)
                self.status(owner, "environment", "正在安装语音处理依赖和文本规范化组件…")
                self.pip(owner, py, ["-r", str(ASSETS / "tts-requirements.txt")],
                         env, log, source, "--no-build-isolation")
            self.status(owner, "selfcheck", "正在进行实际语音转写自检…" if self.kind == "asr"
                        else "正在加载模型、预热优化并合成测试语音…")
            self.command(owner, argv, env, log, timeout=900)
            self.mark_ready(model)

    def mark_ready(self, model):
        marker = dict(model=str(model.resolve()), files=model_stamp(model),
                      installer=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        target = marker_path(self.kind, self.runtime, model)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            stream.write(json.dumps(marker).encode())
        os.replace(stream.name, target)

    @staticmethod
    def torch_source():
        try:
            answer = subprocess.run(["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
                                    capture_output=True, text=True, timeout=10)
            capabilities = [float(n.strip()) for n in answer.stdout.splitlines()]
            if answer.returncode == 0 and capabilities:
                return ("cu128", "2.7.1") if max(capabilities) >= 10 else ("cu124", "2.6.0")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        return "cpu", "2.6.0"
