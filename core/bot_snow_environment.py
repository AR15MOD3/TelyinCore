"""Fixed non-secret OS command environment for the HOST-owned SnowLuma only.

Do not inherit ambient PATH, credentials, profiles or QQ account stores. Core and
QQ launch environments are deliberately unchanged. This grants no process/account
ownership; automatic hook loading remains explicitly disabled.
"""
import os
from pathlib import Path
from bot_launcher_child import child_environment


def snow_environment():
    env=child_environment()
    if os.name=='nt':
        system=Path(env['SystemRoot'])/'System32'
        powershell=system/'WindowsPowerShell'/'v1.0'
        for executable in (system/'cmd.exe',system/'netstat.exe',
                           system/'tasklist.exe',powershell/'powershell.exe'):
            if not executable.is_file() or executable.is_symlink():
                raise ValueError('native SnowLuma OS command runtime unavailable')
        # node:child_process.exec needs ComSpec and PATH even when the parent
        # can create Node directly via its fixed absolute executable path.
        env['ComSpec']=str(system/'cmd.exe')
        env['PATH']=os.pathsep.join(str(p) for p in (system,system/'Wbem',powershell))
    env['SNOWLUMA_HOOK_AUTOLOAD']='0'
    return env
