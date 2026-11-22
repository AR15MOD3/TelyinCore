"""Local-only Linux development using the existing isolated acceptance entry.

No production Bot/config import, Windows-launcher fallback or real model access.
The audit policy is an accidental-network guard for this trusted development
process, not a security sandbox for arbitrary Python or subprocesses.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import threading
import urllib.request
import webbrowser

CORE = Path(__file__).resolve().parents[1]
BOT = "LinuxDev"
BASELINE = "DevelopmentBaseline"


def _plain_path(core: Path, path: Path) -> None:
    """Reject links throughout the development tree before reading or writing it."""
    relative = path.relative_to(core)
    cursor = core
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("开发目录不能包含符号链接：" + str(cursor))
    if not path.resolve().is_relative_to(core):
        raise ValueError("开发目录超出项目范围")


def prepare_workspace(core: Path = CORE) -> Path:
    core = core.resolve(strict=True)
    workspace = core / ".linux-dev"
    marker = workspace / "development.json"
    expected = {"version": 1, "mode": "isolated-linux-dev", "source_core": str(core)}
    _plain_path(core, marker)
    if workspace.exists():
        if not marker.is_file() or json.loads(marker.read_text()) != expected:
            raise ValueError("开发目录缺少匹配的隔离标记，拒绝复用或覆盖")
    else:
        workspace.mkdir(mode=0o700)
        with marker.open("x", encoding="utf-8") as stream:
            json.dump(expected, stream, ensure_ascii=False)
        (workspace / ".trackerignore").touch()

    data = workspace / "core" / "data"
    # launch.py's acceptance root is <workspace>/core/data; its neutral Bot
    # lives at <workspace>/bots/LinuxDev, never at the production bots root.
    for target in (data, workspace / "bots" / BOT,
                   workspace / "core" / "bots" / BOT):
        _plain_path(core, target)
    for target in (data, workspace / "bots" / BOT):
        if target.exists():
            for folder, dirs, files in os.walk(target, followlinks=False):
                for name in dirs + files:
                    _plain_path(core, Path(folder) / name)
    data.mkdir(parents=True, exist_ok=True)
    owner = data / "bot-runtime-owner.json"
    if owner.exists():
        if json.loads(owner.read_text()) != {"baseline": BASELINE}:
            raise ValueError("隔离开发数据归属不匹配，未修改原文件")
    else:
        with owner.open("x", encoding="utf-8") as stream:
            json.dump({"baseline": BASELINE}, stream)
    if (data / "extensions").exists() or (workspace / "bots" / BOT / "infinity").exists():
        raise ValueError("隔离开发目录不能含插件安装或 QQ 配置")
    engine = data / "engine.json"
    if not engine.exists():
        with engine.open("x", encoding="utf-8") as stream:
            json.dump({"current": "OfflineDev", "profiles": {"OfflineDev": {
                "name": "OfflineDev", "kind": "chat",
                "base_url": "http://127.0.0.1:1/v1", "model": "offline-development",
                "max_tokens": 128, "thinking": False, "context_window": 4096,
            }}}, stream)
    return data


def network_guard(port: int):
    """Only the launcher's own loopback readiness check may connect outward."""
    def audit(event, args):
        if event == "socket.connect":
            address = args[1]
            if not (isinstance(address, tuple) and len(address) == 2
                    and address == ("127.0.0.1", port)):
                raise PermissionError("隔离开发模式不连接真实模型或外部服务")
        elif event == "socket.getaddrinfo":
            if args[0] != "127.0.0.1" or args[1] != port:
                raise PermissionError("隔离开发模式不解析外部服务地址")
    return audit


def clean_model_environment() -> None:
    for name in list(os.environ):
        upper = name.upper()
        if (upper.startswith(("TELYIN_", "OPENAI_", "ANTHROPIC_", "DEEPSEEK_"))
                or upper in {"HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"}):
            os.environ.pop(name)


def check_runtime(core: Path = CORE) -> None:
    if sys.platform != "linux" or sys.version_info < (3, 11):
        raise ValueError("需要 Linux 和 Python 3.11 或更新版本")
    for relative in ("launch.py", "dist/web/index.html", "templates/bot-default"):
        if not (core / relative).exists():
            raise ValueError("缺少启动文件：" + relative)


def _open_when_ready(port: int, stop: threading.Event) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(100):
        if stop.wait(.2):
            return
        try:
            with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=.3) as response:
                if response.status != 200:
                    continue
            webbrowser.open(f"http://127.0.0.1:{port}/")
            return
        except (OSError, ValueError):
            continue


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="TelyinCore Linux 隔离开发启动（不接入正式 Bot）")
    parser.add_argument("--port", type=int, default=7809, help="本地端口，默认 7809")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--check", action="store_true", help="只检查环境，不创建开发数据或启动服务")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("端口必须在 1024–65535 之间")
    check_runtime()
    if args.check:
        print("Linux 开发环境及前端产物就绪；现有 Bot 数据未读取。")
        return 0
    # Refuse a busy port; never adopt or stop a production service at that port.
    with socket.socket() as probe:
        # Match the HTTP server's restart policy: TIME_WAIT from a clean exit
        # is not a live listener, while an actually running server still fails.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            parser.error(f"端口 {args.port} 已被占用；请使用已有开发页面或换一个端口")
    data = prepare_workspace()
    clean_model_environment()
    sys.path.insert(0, str(CORE))
    sys.addaudithook(network_guard(args.port))
    print("TelyinCore · Linux 隔离开发", flush=True)
    print(f"页面：http://127.0.0.1:{args.port}/", flush=True)
    print(f"开发数据：{data.parent.parent}", flush=True)
    print("使用中立模板；真实模型连接已禁用，QQ 与现有 Bot 数据未接入。Ctrl-C 退出。", flush=True)
    stopped = threading.Event()
    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(args.port, stopped), daemon=True).start()
    from launch import main as launch_main
    try:
        return launch_main([
            "--acceptance-isolated", "--acceptance-root", str(data),
            "--acceptance-bot", BOT, "--port", str(args.port), "--start-paused",
        ])
    finally:
        stopped.set()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print("启动未完成：" + str(error), file=sys.stderr)
        raise SystemExit(2)
