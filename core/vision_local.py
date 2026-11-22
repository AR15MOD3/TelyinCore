"""Bounded, on-demand llama.cpp inference. No listener, shell or cloud upload."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import subprocess
import tempfile
from pathlib import Path
from threading import Lock

from vision_install import FILES, model_present
from telyincore.config import load_config

# Per-process resource boundary, not a second user-editable output setting.
LOCAL_CONTEXT_TOKENS = 16384
LOCAL_OUTPUT_CEILING = 8192

def output_limit(requested=None):
    requested = load_config().max_tokens if requested is None else requested
    if type(requested) is not int or requested <= 0:
        raise ValueError("核心最大输出 token 设置无效")
    return min(requested, LOCAL_OUTPUT_CEILING)


_inference_lock = Lock()
from telyincore.image_policy import MAX_IMAGE_BYTES, normalize_image
# Fixed Qwen ChatML non-thinking prefix: the mtmd CLI does not expose
# chat-template-kwargs and otherwise may spend the entire output cap on reasoning.
CHAT_TEMPLATE = (
    "{% for message in messages %}{{ '<|im_start|>' + message['role'] + '\n' + "
    "message['content'] + '<|im_end|>\n' }}{% endfor %}"
    "{% if add_generation_prompt %}{{ '<|im_start|>assistant\n<think>\n\n</think>\n\n' }}{% endif %}"
)


def validate(config: dict) -> dict:
    if not isinstance(config, dict):
        raise ValueError("视觉配置必须是对象")
    result = {"backend": config.get("backend", "profile"),
              "profile": config.get("profile", ""),
              "modelDir": config.get("modelDir", ""),
              "runtimeDir": config.get("runtimeDir", ""),
              "device": config.get("device", "gpu")}
    if result["backend"] not in ("local", "profile"):
        raise ValueError("请选择本地模型或模型档案")
    if result["device"] not in ("gpu", "cpu"):
        raise ValueError("运行设备无效")
    for key in ("profile", "modelDir", "runtimeDir"):
        if not isinstance(result[key], str) or len(result[key]) > 2048 or "\x00" in result[key]:
            raise ValueError("档案名称或目录无效")
        result[key] = result[key].strip()
    return result


def executable(config: dict) -> Path:
    root = Path(config["runtimeDir"])
    filename = "llama-mtmd-cli.exe" if os.name == "nt" else "llama-mtmd-cli"
    return root / "bin" / filename if (root / "bin" / filename).is_file() else root / filename


def readiness(config: dict) -> dict:
    config = validate(config)
    model = bool(config["modelDir"]) and model_present(Path(config["modelDir"]))
    runtime = bool(config["runtimeDir"]) and executable(config).is_file()
    try:
        from PIL import Image  # noqa: F401
        pillow = True
    except ImportError:
        pillow = False
    missing = []
    if not model:
        missing.append("模型或视觉投影文件缺失 / 大小不符")
    if not runtime:
        missing.append("未找到 llama-mtmd-cli 运行时")
    if not pillow:
        missing.append("缺少 Pillow 图片依赖")
    return {"modelReady": model, "runtimeReady": runtime, "ready": not missing,
            "message": "；".join(missing) if missing else "文件就绪 · 按需加载，识别结束后释放"}


def verify_weights(directory: Path):
    """ 校验模型文件的**大小 ＋ SHA256**。

    ⚠️ 2026-10-18 修复：这里**不再有"验过一次就跳过"的缓存**。
       旧实现的缓存键是（路径，mtime_ns，size）—— 把文件改成**同样大小**、
       再把 mtime 调回原值，键就一模一样 ⇒ **校验被跳过**（同尺寸篡改能蒙混过关）。
       在 Windows 上 `st_ctime` 是"创建时间"、`os.utime` 也改不动它 ⇒ 加 ctime 补不住这个洞。
       真正能判"内容变没变"的只有**读一遍**；而这个函数今天**没有生产调用点**
       （只有判据与加载前的显式检查），所以按正确性优先：每次都真校验。
    """
    for name, size, digest in FILES:
        path = directory / name
        if path.stat().st_size != size:
            raise ValueError("模型文件大小不符，请重新下载")
        with path.open("rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() != digest:
                raise ValueError("模型 SHA256 校验失败，请重新下载")

def prepare_image(image_url: str, output: Path):
    from PIL import Image
    if not isinstance(image_url, str) or len(image_url) > MAX_IMAGE_BYTES * 4 // 3 + 128:
        raise ValueError("图片过大，最大 6 MB")
    match = re.match(r"^data:(image/(?:png|jpeg|webp|gif));base64,", image_url)
    if not match:
        raise ValueError("只接受已缓存图片数据，不读取网址或任意文件路径")
    try:
        data = base64.b64decode(image_url.split(",", 1)[1], validate=True)
    except ValueError as exc:
        raise ValueError("图片数据无效") from exc
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("图片过大，最大 6 MB")
    data, _ = normalize_image(data, match.group(1))
    output.write_bytes(data)
    with Image.open(io.BytesIO(data)) as image:
        return image.size


def inspect_local(config: dict, image_url: str, question: str, *, max_tokens=None,
                  cancelled=None) -> str:
    def _check_cancelled():
        if cancelled is not None:
            from telyincore.vision_owned import check_cancelled
            check_cancelled(cancelled)
    _check_cancelled()
    config = validate(config)
    max_tokens = output_limit(max_tokens)
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500:
        raise ValueError("问题必须是 1 到 500 字")
    status = readiness(config)
    if not status["ready"]:
        raise ValueError(status["message"])
    _check_cancelled()
    if not _inference_lock.acquire(blocking=False):
        raise ValueError("本地视觉模型正在识别，请稍后重试")
    try:
        model = Path(config["modelDir"])
        verify_weights(model)
        _check_cancelled()
        exe = executable(config).resolve()
        env = dict(os.environ, PYTHONUTF8="1")
        env["PATH"] = os.pathsep.join([str(exe.parent), str(exe.parent.parent / "cudart"),
                                         str(exe.parent / "cudart"), env.get("PATH", "")])
        with tempfile.TemporaryDirectory(prefix="telyin-vision-") as work:
            image = Path(work) / "image.png"
            prepare_image(image_url, image)
            args = [str(exe), "-m", str(model / FILES[0][0]), "--mmproj",
                    str(model / FILES[1][0]), "--image", str(image), "-p", question.strip(),
                    "-n", str(max_tokens), "--temp", "0.2", "-c", str(LOCAL_CONTEXT_TOKENS),
                    "-ngl", "99" if config["device"] == "gpu" else "0", "--no-warmup",
                    "--jinja", "--chat-template", CHAT_TEMPLATE]
            if config["device"] == "cpu":
                args.append("--no-mmproj-offload")
            try:
                _check_cancelled()
                result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                    text=True, encoding="utf-8", errors="replace", shell=False, env=env,
                    timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("视觉识别超时（120 秒），进程已结束；可缩小图片后重试") from exc
            _check_cancelled()
            if result.returncode:
                raise RuntimeError("本地视觉推理失败；请检查运行时 DLL、设备或显存，尝试 CPU 模式")
            # This CLI writes inference to stdout and runtime diagnostics to stderr.
            answer = re.sub(r"<think>.*?</think>", "", result.stdout, flags=re.S)
            answer = re.sub(r"<\|[^>]+\|>", "", answer).strip()
            if "<think>" in answer:
                raise RuntimeError("视觉模型输出未完成，请简化问题后重试")
            if not answer:
                raise RuntimeError("视觉模型没有返回可读文字")
            _check_cancelled()
            return answer
    finally:
        _inference_lock.release()
