"""Local operator runtime selection. Never execute or download the selected program."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


_PICKER = r'''
import json
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.title("TelyinCore · 导入运行时")
root.geometry("360x80")
tk.Label(root, text="请在系统文件窗口选择 llama-mtmd-cli.exe").pack(pady=22)
root.update()
root.attributes("-topmost", True)
try:
    selected = filedialog.askopenfilename(
        parent=root, title="导入 llama.cpp 运行时 — 选择 llama-mtmd-cli.exe",
        filetypes=[("llama.cpp 多模态运行时", "llama-mtmd-cli.exe")])
    print(json.dumps({"path": selected or None}, ensure_ascii=False), flush=True)
finally:
    root.destroy()
'''


def choose_runtime_file() -> str | None:
    if os.name != "nt":
        raise ValueError("运行时导入目前需要在 Windows 本机打开 WebUI")
    try:
        result = subprocess.run(
            [sys.executable, "-c", _PICKER],
            capture_output=True, text=True, encoding="utf-8", errors="replace", shell=False,
            stdin=subprocess.DEVNULL, timeout=180, env=dict(os.environ, PYTHONUTF8="1"),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise ValueError("选择运行时已超时，未修改配置；请重新导入") from exc
    except OSError as exc:
        raise RuntimeError("无法打开 Windows 文件选择窗口") from exc
    if result.returncode:
        raise RuntimeError("无法打开 Windows 文件选择窗口，请在运行 Core 的电脑上操作")
    try:
        value = json.loads(result.stdout.lstrip("\ufeff"))["path"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("文件选择窗口返回无效结果，未修改配置") from exc
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError("未选择有效的运行时文件")
    return value


def runtime_directory(selected: str) -> Path:
    path = Path(selected).expanduser().resolve()
    if path.name.casefold() != "llama-mtmd-cli.exe" or not path.is_file():
        raise ValueError("请选择解压后的 llama-mtmd-cli.exe，不支持压缩包或其他程序")
    # Keep a distribution root containing bin/ and sibling cudart/ intact.
    return path.parent.parent if path.parent.name.casefold() == "bin" else path.parent
