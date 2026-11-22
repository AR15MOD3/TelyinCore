"""Local launcher entry point for a verified, pending Core update."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

CORE = Path(__file__).resolve().parents[1]
ROOT = CORE.parent
sys.path.insert(0, str(CORE))
from telyincore.online_install import apply_pending, folder, pending


def original_exited(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 87
        try:
            return kernel.WaitForSingleObject(handle, 120000) == 0
        finally:
            kernel.CloseHandle(handle)
    import select
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return True
    try:
        return bool(select.select([fd], [], [], 120)[0])
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--after-exit', type=int)
    parser.add_argument('--port', type=int, default=7810)
    args = parser.parse_args()
    platform = 'win' if os.name == 'nt' else 'linux'
    if args.after_exit and not original_exited(args.after_exit):
        raise ValueError('原 Core 尚未退出，已保留待安装更新')
    if not pending(ROOT):
        return
    if not apply_pending(ROOT, platform):
        return
    print('更新安装完成', flush=True)
    if args.after_exit:
        if platform == 'win':
            os.startfile(str(ROOT/'启动 TelyinCore.bat'))
        else:
            os.chdir(ROOT)
            os.execlp('bash', 'bash', str(ROOT/'启动 Telyincore.sh'), '--port', str(args.port), '--no-browser')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        try:
            (folder(ROOT)/'last-error.txt').write_text(str(exc), encoding='utf-8')
        except OSError:
            pass
        sys.exit(1)
