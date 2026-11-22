"""Spawn the actual Windows CPython process, retaining its real native handle.

CPython's Windows venv redirector creates a second process. Starting the base
interpreter with the same __PYVENV_LAUNCHER__ environment that CPython uses keeps
venv sys.prefix/site-packages while making Popen.pid the interpreter PID. No PID
discovery, process adoption, listener trust or identity-check exception is added.
See https://github.com/python/cpython/blob/main/Modules/getpath.py and
https://github.com/python/cpython/blob/main/PC/venvlauncher.c .
"""
import os
from pathlib import Path
import sys


def python_command(arguments, *, python=None, env=None):
    """Return command + fresh environment; caller retains normal Popen ownership.

    Optimize only this running CPython venv, not arbitrary configured runtimes.
    Paths come from interpreter metadata, never from model/tool parameters.
    """
    executable = sys.executable if python is None else os.fspath(python)
    environment = dict(os.environ if env is None else env)
    command = [executable, *arguments]
    if (os.name != 'nt' or sys.implementation.name != 'cpython'
            or sys.prefix == sys.base_prefix
            or os.path.normcase(os.path.abspath(executable))
               != os.path.normcase(os.path.abspath(sys.executable))):
        return command, environment
    base = Path(sys._base_executable)
    launcher = Path(sys.executable)
    if (not base.is_absolute() or not base.is_file() or base.suffix.lower() != '.exe'
            or not launcher.is_absolute() or not launcher.is_file()
            or not (Path(sys.prefix)/'pyvenv.cfg').is_file()
            or not base.is_relative_to(Path(sys.base_prefix))):
        raise ValueError('native CPython venv spawn metadata unavailable')
    environment['__PYVENV_LAUNCHER__'] = str(launcher)
    return [str(base), *arguments], environment
