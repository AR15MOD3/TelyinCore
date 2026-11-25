"""Read-only Windows previous-boot proof; never retires owners or adopts PIDs.

WMI LastBootUpTime: https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-operatingsystem
A current-boot or ambiguous owner is still a blocker. Sleep/hibernation is not a
reboot. This is not account-offline evidence and does not delete ownership files.
"""
import os
from pathlib import Path
import subprocess


def boot_filetime():
    if os.name != 'nt': raise ValueError('旧启动器责任需要在 Windows 本机核对')
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(executable), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
        "$ErrorActionPreference='Stop'; [Console]::Write((Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToFileTimeUtc())"],
        capture_output=True, timeout=15, check=False)
    raw = result.stdout.strip()
    if result.returncode or not raw.isdigit() or len(raw) > 18:
        raise ValueError('无法确认 Windows 本次开机时间，保留保护记录')
    value = int(raw)
    if value <= 0: raise ValueError('Windows 开机时间无效')
    return value


def belongs_to_current_boot(owner, boot):
    # Keep the same conservative margin in live and offline checks.
    return owner['created_filetime'] + 100_000_000 >= boot


def previous_boot_owners(rows):
    boot = boot_filetime()
    for row in rows:
        owner = row['owner']
        # Conservative margin against timing precision differences.
        if belongs_to_current_boot(owner, boot):
            raise ValueError(
                f'本次开机内仍有启动记录缺少完整的进程组退出凭据（原启动进程 PID {owner["pid"]}），暂不能操作档案。\n'
                '这是启动责任记录尚未核实，不表示档案损坏；仅看到原 PID 消失不足以确认全部子进程退出。\n'
                '1. 若仍有 Bot 服务或启动窗口，请先正常休止服务并退出这些窗口，再重试。\n'
                '2. 正常休止且收尾确认后不需要重启。若旧记录仍无法核实，请保存其他工作，从 Windows 开始菜单选择“重新启动”。\n'
                '3. 重启后不要先唤醒 Bot；直接打开启动菜单 → 档案，重新执行本次操作。\n'
                '请使用“重新启动”，不要用睡眠、休眠或关机再开机代替。无需手动删除保护记录。')
        # The boot boundary proves the original process and its descendants
        # cannot still be running. A numeric PID may now belong to an unrelated
        # protected process: do not query/adopt that replacement as the owner.
    return {'windows_boot_filetime': boot, 'previous_boot_owners': len(rows), 'owners_preserved': True}
