"""Desktop file-manager dispatch; path ownership is validated by the HTTP owner."""
from pathlib import Path
import shutil,subprocess,sys


def show_in_folder(target: Path) -> str:
    target=Path(target).resolve(strict=True)
    if sys.platform=='win32':
        subprocess.Popen(['explorer','/select,',str(target)],shell=False)
        front=("$ErrorActionPreference = 'SilentlyContinue';\n"
               "Start-Sleep -Milliseconds 350;\n"
               "$sh = New-Object -ComObject Shell.Application;\n"
               "$w = @($sh.Windows()) | Where-Object { $_.LocationURL -like 'file://*' } | Select-Object -First 1;\n"
               "if ($w) { $w.Visible = $true; try { $w.Document.Focus() } catch { } }")
        try:subprocess.run(['powershell','-NoProfile','-Command',front],capture_output=True,check=False,timeout=8)
        except (OSError,subprocess.TimeoutExpired):pass
        return '在文件夹里打开了：'+target.name
    if sys.platform=='darwin':
        subprocess.run(['open','-R',str(target)],check=True,timeout=5,capture_output=True)
        return '在文件夹里打开了：'+target.name
    folder=target if target.is_dir() else target.parent
    candidates=[['gio','open',str(folder)],['xdg-open',str(folder)]]
    for command in candidates:
        if shutil.which(command[0]) is None:continue
        try:
            subprocess.run(command,check=True,timeout=5,capture_output=True)
            return '已打开所在文件夹：'+folder.name
        except (OSError,subprocess.SubprocessError):continue
    raise ValueError('无法打开文件管理器，请检查桌面会话和默认文件管理器。')
