"""Pinned, verified Qwen3.5 vision weights; never downloads executable code."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from threading import Lock
from telyincore.owned_blocking import InstallationOwner

from telyincore.download_sources import HF, selected_proxy, settings_root, source_order

import httpx

NAME = "Qwen3.5-0.8B-GGUF"
REVISION = "6ab461498e2023f6e3c1baea90a8f0fe38ab64d0"
SOURCES = HF
FILES = (
    ("Qwen3.5-0.8B-Q4_K_M.gguf", 532517120,
     "bd258782e35f7f458f8aced1adc053e6e92e89bc735ba3be89d38a06121dc517"),
    ("mmproj-F16.gguf", 204987232,
     "56e4c6cfe73b0c82e3e82bc518d7591997e61d81f723fc41a586f4fa69ea2453"),
)


def model_present(path: Path) -> bool:
    try:
        return all((path / name).is_file() and (path / name).stat().st_size == size
                   for name, size, _ in FILES)
    except OSError:
        return False


class VisionInstaller(InstallationOwner):
    def __init__(self, parent: Path, proxy_root=None):
        self.proxy_root = proxy_root if proxy_root is not None else settings_root(Path(__file__).resolve().parent)
        self.parent = Path(parent)
        self.lock = Lock()
        self._init_install_owner()
        self.state = dict(busy=False, phase="idle", message="", downloaded=0,
                          total=sum(row[1] for row in FILES), source="mirror", file="")

    def read(self) -> dict:
        with self.lock:
            return dict(self.state)

    def start(self, source="mirror") -> dict:
        if not isinstance(source, str) or source not in SOURCES:
            raise ValueError("不支持的下载源")
        with self.lock:
            self._check_install_open_locked()
            if self.state["busy"] or (self._install_thread is not None and self._install_thread.is_alive()):
                return {**self.state, 'busy': True}
            if model_present(self.parent / NAME):
                self.state.update(phase="done", message="推荐模型已安装，无需重复下载。")
                return dict(self.state)
            self.state.update(busy=True, phase="download", downloaded=0, file="",
                              source=source, message="正在下载推荐视觉模型…")
            self._launch_install_locked(source)
        return self.read()

    def _run(self, source="official"):
        stage = None
        lock_path = self.parent / ".qwen35-download.lock"
        owns_lock = False
        try:
            if source not in SOURCES:
                raise ValueError("不支持的下载源")
            self.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            owns_lock = True
            os.close(fd)
            if (self.parent / NAME).exists():
                raise FileExistsError("模型目录已存在，未覆盖；请检查文件完整性。")
            stage = Path(tempfile.mkdtemp(prefix=".vision-download-", dir=self.parent))
            completed = 0
            for name, size, digest in FILES:
                for choice in source_order(source):
                    proxy = selected_proxy(choice, self.proxy_root)
                    with self.lock:
                        self.state.update(file=name, phase="download", source=choice,
                                          route="proxy" if proxy else "direct", downloaded=completed)
                    url = f"{SOURCES[choice]}/unsloth/{NAME}/resolve/{REVISION}/{name}"
                    try:
                        actual, count = hashlib.sha256(), 0
                        with httpx.Client(follow_redirects=True, timeout=45, trust_env=False,
                                          **({"proxy": proxy} if proxy else {})) as client:
                            with client.stream("GET", url) as response:
                                response.raise_for_status()
                                with (stage / name).open("wb") as output:
                                    for chunk in response.iter_bytes(1024 * 1024):
                                        count += len(chunk)
                                        if count > size:
                                            raise ValueError("下载大小超过预期")
                                        output.write(chunk)
                                        actual.update(chunk)
                                        with self.lock:
                                            self.state["downloaded"] = completed + count
                        with self.lock:
                            self.state.update(phase="verify", message="正在校验大小与 SHA256…")
                        if count != size or actual.hexdigest() != digest:
                            raise ValueError("模型校验未通过")
                        completed += size
                        break
                    except (httpx.HTTPError, ValueError):
                        if choice == "official":
                            raise
            (stage / "SOURCE.txt").write_text(
                f"unsloth/{NAME} @ {REVISION}\nQ4_K_M + F16 mmproj; Apache-2.0\n",
                encoding="utf-8")
            stage.rename(self.parent / NAME)
            with self.lock:
                self.state.update(phase="done", message="推荐模型已安装；选择本地模型并保存即可。")
        except FileExistsError:
            with self.lock:
                self.state.update(phase="error", message="目录已存在或另一个实例正在下载，未覆盖文件。")
        except Exception:
            with self.lock:
                self.state.update(phase="error", message="下载失败或校验未通过，未安装；可切换来源重试。")
        finally:
            cleanup_errors = []
            try:
                if stage is not None and stage.exists():
                    shutil.rmtree(stage)
            except Exception as cleanup:
                cleanup_errors.append(type(cleanup).__name__)
            try:
                if owns_lock:
                    lock_path.unlink(missing_ok=True)
            except Exception as cleanup:
                cleanup_errors.append(type(cleanup).__name__)
            with self.lock:
                self.state["busy"] = False
                if cleanup_errors:
                    self.state.update(phase="cleanup_failed", cleanup_path=str(stage),
                                      cleanup_errors=cleanup_errors,
                                      message="清理失败，保留暂存/锁责任；需处理后重试。")
