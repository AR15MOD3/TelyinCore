"""Verified Windows installation process handles; never stop by port/name alone."""
from __future__ import annotations

import ctypes
import json
import math
from threading import RLock
import os
import subprocess
from pathlib import Path
from typing import Any


def _argv(command: str) -> list[str]:
    from ctypes import wintypes

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    parsed = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not parsed:
        return []
    try:
        return [parsed[i] for i in range(count.value)]
    finally:
        kernel.LocalFree(parsed)


def matching_installation(row: dict[str, Any], entry: Path,
                          argv: list[str]) -> bool:
    if type(row.get("pid")) is not int or row["pid"] <= 0:
        return False
    if not isinstance(row.get("created"), str) or not row["created"].isdigit():
        return False
    if len(argv) != 2 or not isinstance(row.get("exe"), str):
        return False
    exe = Path(row["exe"]).resolve()
    return (exe.name.lower() == "node.exe" and Path(argv[0]).resolve() == exe
            and Path(argv[1]).is_absolute() and Path(argv[1]).resolve() == entry.resolve())


def discover(entry: Path, *, pid: int | None = None) -> dict[str, Any] | None:
    if os.name != "nt":
        return None
    filter_text = f"ProcessId={pid}" if type(pid) is int and pid > 0 else "Name='node.exe'"
    script = ("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
              "$ErrorActionPreference='Stop'; "
              f"@(Get-CimInstance Win32_Process -Filter \"{filter_text}\" | "
              "ForEach-Object { @{pid=[int]$_.ProcessId; exe=$_.ExecutablePath; "
              "command=$_.CommandLine; created=$_.CreationDate.ToFileTimeUtc().ToString()} }) "
              "| ConvertTo-Json -Compress")
    try:
        output = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        if output.returncode:
            return None
        rows = json.loads(output.stdout.strip() or "[]")
        rows = [rows] if isinstance(rows, dict) else rows
        matches = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("command"), str):
                continue
            if matching_installation(row, entry, _argv(row["command"])):
                matches.append({key: row[key] for key in ("pid", "exe", "created")})
        return matches[0] if len(matches) == 1 else None
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return None


class VerifiedProcess:
    """Pin a kernel handle before checking creation time; PID reuse fails closed."""

    def __init__(self, identity: dict[str, Any]) -> None:
        from ctypes import wintypes

        if os.name != "nt":
            raise ValueError("此系统暂不支持重新接管进程")
        self._handle_lock = RLock()
        self._handle = None
        self.pid = identity["pid"]
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        k = self._kernel
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                              wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.CloseHandle.restype = wintypes.BOOL
        k.GetProcessTimes.restype = wintypes.BOOL
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.GetExitCodeProcess.restype = wintypes.BOOL
        k.TerminateProcess.restype = wintypes.BOOL
        self._handle = k.OpenProcess(0x100000 | 0x1000 | 1, False, self.pid)
        if not self._handle:
            raise ValueError("无法取得已确认的 SnowLuma 进程句柄")
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not k.GetProcessTimes(self._handle, *(ctypes.byref(t) for t in times)):
                raise ValueError("无法核对进程创建时间")
            created = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
            name = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(name))
            if not k.QueryFullProcessImageNameW(self._handle, 0, name, ctypes.byref(size)):
                raise ValueError("无法核对进程可执行文件")
            # CIM serializes creation times at microsecond precision (10 ticks).
            if int(created) // 10 != int(identity["created"]) // 10 or Path(name.value).resolve() != Path(
                    identity["exe"]).resolve():
                raise ValueError("SnowLuma 进程身份已变化，拒绝停止")
        except BaseException as primary:
            try:
                self.close()
            except BaseException as cleanup:
                primary.add_note('native handle cleanup failed: ' + type(cleanup).__name__)
            raise

    def _wait_result(self, milliseconds: int) -> int:
        # Caller holds the handle lock. WAIT_FAILED/unknown is NEVER absence.
        if not self._handle:
            raise OSError("SnowLuma process handle is closed")
        result = self._kernel.WaitForSingleObject(self._handle, milliseconds)
        if result not in (0, 258):
            raise OSError("SnowLuma native process observation unavailable")
        return result

    def poll(self) -> int | None:
        with self._handle_lock:
            return None if self._wait_result(0) == 258 else 0

    def terminate(self) -> None:
        with self._handle_lock:
            if self.poll() is None and not self._kernel.TerminateProcess(self._handle, 1):
                raise OSError("停止 SnowLuma 失败")

    def wait(self, timeout: float = 8) -> int:
        if (type(timeout) not in (int, float) or not 0 <= timeout <= 10
                or not math.isfinite(timeout)):
            raise ValueError("bounded SnowLuma process wait required")
        with self._handle_lock:
            if self._wait_result(int(timeout * 1000)) == 258:
                raise subprocess.TimeoutExpired("SnowLuma", timeout)
            return 0  # native signalled handle, NOT account/descendant offline

    def close(self) -> None:
        with self._handle_lock:
            if self._handle:
                if not self._kernel.CloseHandle(self._handle):
                    raise OSError("SnowLuma process handle close failed")
                self._handle = None  # failed close retains the original handle

    def __del__(self) -> None:
        # Best effort only; explicit callers must retain/handle close failures.
        try:
            if getattr(self, "_handle", None):
                self.close()
        except BaseException:
            pass
