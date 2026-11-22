"""Prepare/check a fresh Windows core acceptance install; real Bot data excluded.

The existing Windows birth backend supports CPython 3.11 x64. Do not silently
choose a different interpreter or bypass startup's native ownership checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import runpy
import tomllib
import tempfile

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
from launcher_presentation import notice, startup_header
PROBE = (
    "import os,sys,sysconfig; "
    "assert os.name=='nt' and sys.implementation.name=='cpython' "
    "and sys.version_info[:2]==(3,11) and sysconfig.get_platform()=='win-amd64' "
    "and sys.getwindowsversion().major>=10"
)


READY_FILE = '.telyincore-environment-ready.json'


def environment_signature() -> dict | None:
    """Cheap change detector, not a replacement for startup ownership checks."""
    venv = (CORE / '.venv').resolve()
    if Path(sys.prefix).resolve() != venv:
        return None
    paths = [CORE / 'requirements-windows-acceptance.txt', CORE / 'pyproject.toml',
             CORE / 'scripts/ensure_runtime.py', venv / 'pyvenv.cfg']
    files = {str(p.relative_to(CORE)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in paths}
    site = venv / 'Lib/site-packages'
    def stamp(path):
        item = Path(path).stat()
        return [item.st_size, item.st_mtime_ns, item.st_ctime_ns]
    return dict(schema=1, core=str(CORE.resolve()), prefix=str(venv),
                base_prefix=sys.base_prefix, version=list(sys.version_info[:3]),
                executable=stamp(sys.executable),
                base_executable=stamp(getattr(sys, '_base_executable', sys.executable)),
                site_packages=stamp(site), files=files)


def fast_environment_ready() -> bool:
    try:
        marker = CORE / '.venv' / READY_FILE
        if not marker.is_file() or marker.stat().st_size > 32768:
            return False
        before = environment_signature()
        if before is None or json.loads(marker.read_text(encoding='utf-8')) != before:
            return False
        helpers = runpy.run_path(str(CORE / 'scripts/ensure_runtime.py'))
        project = tomllib.loads((CORE / 'pyproject.toml').read_text(encoding='utf-8'))
        exec(helpers['dependency_probe'](project['project']['dependencies']), {})
        from scripts.setup_semantic import ready
        from scripts.setup_qq_runtime import ready as qq_ready
        return ready() and qq_ready(CORE) and environment_signature() == before
    except Exception:
        return False


def remember_verified_environment() -> None:
    signature = environment_signature()
    if signature is None:
        return
    marker = CORE / '.venv' / READY_FILE
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=marker.parent,
                                     prefix='.environment-ready-', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps(signature, sort_keys=True))
    try:
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)


def supported_runtime() -> bool:
    return (os.name == "nt" and sys.implementation.name == "cpython"
            and sys.version_info[:2] == (3, 11)
            and sysconfig.get_platform() == "win-amd64"
            and sys.getwindowsversion().major >= 10)


def run(command: list[str]) -> int:
    environment = dict(os.environ, PYTHONUTF8="1")
    checking = ('--verify-packages' in command or any(str(p).endswith('ensure_runtime.py') for p in command))
    installing = 'pip' in command and 'install' in command
    if not checking and not installing:
        return subprocess.call(command, cwd=CORE, env=environment)
    done = subprocess.run(command, cwd=CORE, env=environment, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
    if installing or done.returncode:
        from datetime import datetime
        folder = CORE / 'data/diagnostics'
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / ('setup-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.log')
            path.write_text(done.stdout or '', encoding='utf-8')
            notice('setup', ('依赖安装完成。' if done.returncode == 0 else '依赖安装未完成。' if installing else '环境检查需要进一步准备。')
                   + '详细记录：' + str(path), level='WARN' if installing and done.returncode else 'INFO')
        except OSError:
            if done.stdout:
                print(done.stdout, end='')
    return done.returncode


def verify_packages() -> int:
    from importlib.metadata import PackageNotFoundError, version

    (CORE / '.venv' / READY_FILE).unlink(missing_ok=True)
    locked = CORE / "requirements-windows-acceptance.txt"
    for line in locked.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, expected = line.split("==", 1)
        try:
            installed = version(name)
        except PackageNotFoundError:
            notice('check', '缺少依赖：' + name)
            return 1
        if installed != expected:
            notice('check', f'依赖版本需调整：{name}，当前 {installed}，需要 {expected}')
            return 1
    code = run([sys.executable, str(CORE / "scripts/ensure_runtime.py")])
    if code == 0:
        remember_verified_environment()
    return code


def retire_legacy_manager(core=CORE):
    """Remove only the exact formerly shipped shortcut, never a user script."""
    path = Path(core).parent / '管理 Bot.bat'
    if not path.exists() or path.is_symlink(): return
    try:
        if path.stat().st_size > 65536: return
        from scripts.build_windows_acceptance import batch_file
        expected = batch_file().replace(b'%script%" %*', b'%script%" --manage-bots %*')
        if path.read_bytes() == expected: path.unlink()
    except OSError:
        notice('launcher', '旧“管理 Bot.bat”未移除，可在退出旧入口后手动删除；请统一使用启动入口。', level='WARN')


def main(argv=None) -> int:
    if (CORE.parent / 'runtime/package-updates/pending.json').is_file():
        worker = CORE / 'scripts/online_update.py'
        result = subprocess.call([sys.executable, str(worker)], cwd=CORE)
        if result:
            return result
        # Reload the newly installed launcher and imports.
        return subprocess.call([sys.executable, str(Path(__file__).resolve()), *(sys.argv[1:] if argv is None else argv)], cwd=CORE)
    retire_legacy_manager()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments and sys.stdin.isatty():
        from scripts.core_menu import main as launcher_menu
        return launcher_menu(CORE, main)
    if "--manage-bots" in arguments:
        sys.path.insert(0, str(CORE))
        from scripts.manage_bots import main as manage_bots
        return manage_bots(arguments[arguments.index("--manage-bots") + 1:], core=CORE)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true", help="直接进入唤醒流程")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--install", action="store_true", help="Create Windows venv and install core dependencies")
    mode.add_argument("--check", action="store_true", help="Check runtime without launching a Bot")
    mode.add_argument("--verify-packages", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--check-documents", action="store_true",
                        help="Check persona, memory and text indexes on a temporary Bot")
    parser.add_argument("--port", type=int, default=7799)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if args.check_documents and (args.check or args.install or args.verify_packages):
        parser.error("--check-documents is a separate acceptance mode")
    if not supported_runtime():
        startup_header('环境检查未通过')
        notice('check', '需要 Windows 10/11 与 CPython 3.11 x64。', level='ERROR')
        notice('setup', '请安装兼容的 Python 后重新打开启动入口。')
        return 2
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not (CORE / "dist/web/index.html").is_file():
        notice('check', '缺少网页产物，请核对分发包是否完整解压。', level='ERROR')
        return 2
    if args.verify_packages:
        return verify_packages()
    python = CORE / ".venv/Scripts/python.exe"
    if not (args.run or args.check or args.install) and fast_environment_ready():
        startup_header('文档验收 · 快速自检' if args.check_documents else '启动 · 快速自检')
        if not args.check_documents:
            from telyincore.package_updates import package_updates
            notice('check', package_updates.launcher_check())
        notice('check', '本地环境与依赖可用，快速自检通过。')
        return launch(args)
    if not args.run:
        startup_header('首次准备与环境检查')
        if not (args.check or args.install or args.check_documents):
            from telyincore.package_updates import package_updates
            notice('check', package_updates.launcher_check())
        notice('check', '1 / 4  检查 Windows 与 Python 3.11 x64')
        if not python.is_file():
            if args.check:
                notice('check', '本地环境尚未创建，请正常启动一次完成准备。', level='ERROR')
                return 2
            if (CORE / ".venv").exists():
                notice('check', '检测到不完整的 .venv，请核对该环境后再试。', level='ERROR')
                return 2
            notice('setup', '正在创建本地 Python 环境…')
            code = run([sys.executable, "-m", "venv", str(CORE / ".venv")])
            if code:
                return code
        code = run([str(python), "-c", PROBE])
        if code:
            notice('check', '现有 .venv 与所需 Python 不兼容，请核对本地环境。', level='ERROR')
            return code

        notice('check', '2 / 4  检查所需依赖')
        locked = CORE / "requirements-windows-acceptance.txt"
        check = [str(python), str(Path(__file__).resolve()), "--verify-packages"]
        if run(check):
            if args.check:
                notice('check', '依赖检查未通过，请查看详细记录。', level='ERROR')
                return 1
            wheels = CORE.parent / "runtime" / "wheels"
            install = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
            if wheels.is_dir():
                notice('setup', '正在从离线包安装固定版本依赖…')
                install += ["--no-index", "--only-binary=:all:", "--find-links", str(wheels)]
            else:
                notice('setup', '正在联网安装固定版本依赖…')
            code = run(install + ["-r", str(locked)])
            if code:
                notice('setup', '依赖安装未完成，请查看上方诊断记录后重试。', level='ERROR')
                return code
            if run(check):
                return 1
        notice('check', '3 / 4  检查本地语义检索环境')
        semantic_command = [str(python), str(CORE / 'scripts/setup_semantic.py')]
        if args.check:
            semantic_command.append('--check')
        code = run(semantic_command)
        if code:
            return code
        qq_command = [str(python), str(CORE / 'scripts/setup_qq_runtime.py')]
        if args.check: qq_command.append('--check')
        if run(qq_command): return 1
        if args.check or args.install:
            notice('check', '环境检查通过。')
            return 0
        notice('startup', '4 / 4  运行组件准备完成，请选择要唤醒的 Bot。')
        command = [str(python), "-u", str(Path(__file__).resolve()), "--run", "--port", str(args.port)]
        if args.no_browser:
            command.append("--no-browser")
        if args.check_documents:
            command.append("--check-documents")
        return run(command)
    return launch(args)


def launch(args) -> int:
    if getattr(args, 'check_documents', False):
        sys.path.insert(0, str(CORE))
        from scripts.check_document_storage import main as check_documents
        return check_documents(CORE)
    # Explicit fresh-install acceptance, sharing the unchanged Windows launcher.
    # No QQ, real model profile, credentials or production Bot is bundled.
    for name in list(os.environ):
        if name.upper().startswith("TELYIN_"):
            os.environ.pop(name)
    (CORE / "data").mkdir(exist_ok=True)
    sys.path.insert(0, str(CORE))
    from startup import main as startup_main
    arguments = ["--port", str(args.port)]
    if args.no_browser:
        arguments.append("--no-browser")
    # Pause a newly launched Bot; a verified existing Bot keeps its current state.
    return startup_main(arguments, launch_options={"start_paused": True}, reuse_start_paused=True)


def run_entrypoint(argv=None) -> int:
    try:
        return main(argv)
    except KeyboardInterrupt:
        notice('startup', '操作已中断；退出收尾信息以原控制器为准。', level='WARN')
        return 130
    except Exception as error:
        from launcher_presentation import report_failure
        return report_failure(CORE, error)


if __name__ == "__main__":
    raise SystemExit(run_entrypoint())
