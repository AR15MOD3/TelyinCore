"""Interactive Linux frontend for the shared Core menu and console."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import termios
import time
import tty
import webbrowser

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))

from console_output import ConsoleOutput
from scripts import core_menu
from startup_terminal import menu_notice, phase_heading, running


def profile(core):
    marker = core / '.linux-acceptance/acceptance.json'
    if not marker.exists():
        return 'TelyinLinux'
    from bot_registry import bot_id
    row = json.loads(marker.read_bytes())
    if row.get('source_core') != str(core) or row.get('mode') != 'linux-profile-acceptance':
        raise ValueError('档案登记与本部署不匹配')
    return bot_id(row['bot'])


def service_port(core, pid):
    """Only recognize the venv entrypoint holding this deployment's flock."""
    proc = Path('/proc') / str(pid)
    try:
        if proc.stat().st_uid != os.getuid():
            return None
        argv = (proc / 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
        entries = [os.fsencode(core / 'scripts' / name) for name in ('linux_acceptance.py', 'linux_qq.py')]
        if len(argv) < 2 or argv[1] not in entries or argv[0] != os.fsencode(core / '.venv-linux/bin/python'):
            return None
        if not (proc / 'fd/9').samefile(core / '.linux-acceptance-launch.lock'):
            return None
        if 'FLOCK  ADVISORY  WRITE' not in (proc / 'fdinfo/9').read_text():
            return None
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        parser.add_argument('--port', type=int, default=7810)
        parser.add_argument('--no-browser', action='store_true')
        args, other = parser.parse_known_args(list(map(os.fsdecode, argv[2:])))
        if not other and 1024 <= args.port <= 65535:
            return args.port
    except (OSError, ValueError):
        pass
    return None


def listening(pid, port):
    try:
        address = f'0100007F:{port:04X}'
        inodes = {'socket:[' + row[9] + ']' for line in Path('/proc/net/tcp').read_text().splitlines()[1:]
                  if (row := line.split())[1] == address and row[3] == '0A'}
        for fd in (Path('/proc') / str(pid) / 'fd').iterdir():
            try:
                if os.readlink(fd) in inodes:
                    return True
            except FileNotFoundError:
                continue
    except OSError:
        pass
    return False


@contextlib.contextmanager
def instance(core):
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        pid = int(proc.name)
        if service_port(core, pid) is None:
            continue
        try:
            handle = os.pidfd_open(pid)
        except ProcessLookupError:
            continue
        try:
            port = service_port(core, pid)
            if port is not None and not select.select([handle], [], [], 0)[0]:
                yield (pid, port, handle)
                return
        finally:
            os.close(handle)
    yield None


def stop(core):
    with instance(core) as target:
        if target is None:
            menu_notice('shutdown', '当前没有可休止的 Core 服务。')
            return True
        pid, port, handle = target
        name = profile(core)
        if core_menu.choose('休止 / 选择服务', [name, '返回主菜单']) != 0:
            return False
        if service_port(core, pid) != port:
            raise ValueError('服务状态已变化，请重新选择。')
        signal.pidfd_send_signal(handle, signal.SIGINT)
        menu_notice('shutdown', '正在等待 Core 正常收尾…')
        if select.select([handle], [], [], 30)[0]:
            menu_notice('shutdown', name + ' 服务已休止。')
        else:
            menu_notice('shutdown', '仍在收尾，请查看原启动窗口。', level='WARN')
        return True


def open_page(port):
    if not webbrowser.open(f'http://127.0.0.1:{port}/'):
        menu_notice('web', f'请在浏览器打开 http://127.0.0.1:{port}/')


def run_service(core, name):
    port = 7810
    proc = subprocess.Popen(['bash', str(core / 'start.sh'), '--acceptance', '--launch-selected', '--port', str(port), '--no-browser'],
                            env=dict(os.environ, PYTHONUNBUFFERED='1'), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    output = ConsoleOutput(proc).start()
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    shown = False
    stopping = False
    try:
        tty.setcbreak(fd, termios.TCSANOW)
        while proc.poll() is None:
            try:
                if not shown and service_port(core, proc.pid) == port and listening(proc.pid, port):
                    output.ready(name, port)
                    open_page(port)
                    shown = True
                if select.select([fd], [], [], .25)[0]:
                    key = os.read(fd, 1).lower()
                    if key == b'b' and shown:
                        open_page(port)
                    elif key in (b'q', b'', b'\x03'):
                        raise KeyboardInterrupt
            except KeyboardInterrupt:
                if not stopping and proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGINT)
                    stopping = True
                    menu_notice('shutdown', '正在等待 Core 正常收尾…')
        return 0 if stopping and proc.returncode in (-signal.SIGINT, 130) else proc.returncode
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, saved)
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGINT)
            proc.wait()
        output.finish()


def wake(core):
    phase_heading('01  环境自检')
    from telyincore.package_updates import package_updates
    menu_notice('check', package_updates.launcher_check())
    result = subprocess.call(['bash', str(core / 'start.sh'), '--acceptance', '--prepare'])
    if result:
        menu_notice('check', '自检未完成，请查看上方提示后重试。', level='ERROR')
        return result
    name = profile(core)
    if core_menu.choose('唤醒 / 选择 Bot', [name, '返回主菜单'], hero=False) != 0:
        return 0
    with instance(core) as target:
        if target:
            pid, port, owner_fd = target
            if listening(pid, port):
                from scripts.linux_sl_companion import ensure
                try:
                    ensure(core, owner_fd)
                    if (core/'linux-components').is_dir():
                        from scripts.linux_qq_wake import intent, wake as wake_qq
                        if intent(core):
                            message = wake_qq(core, owner_fd)
                            if message:
                                menu_notice('QQ', message)
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    menu_notice('SL', str(error), level='WARN')
                running(name, port, reused=True)
                open_page(port)
            else:
                menu_notice('launcher', '服务正在启动或收尾，请查看原启动窗口。')
            return 1
    phase_heading('02  服务启动')
    return run_service(core, name)


def profiles(core):
    with instance(core) as target:
        if target:
            menu_notice('archives', '请先在主菜单选择「休止」，再维护档案。')
            return True
    if not (core / '.linux-acceptance/acceptance.json').is_file():
        menu_notice('archives', '首次部署请先选择「唤醒」创建档案。')
        return True
    return bool(subprocess.call(['bash', str(core / 'start.sh'), '--acceptance', '--manage-bots']))


def main():
    def close_window(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGHUP, close_window)
    signal.signal(signal.SIGTERM, close_window)
    return core_menu.main(CORE, lambda _: wake(CORE), profiles=profiles, stop=stop)


if __name__ == '__main__':
    raise SystemExit(main())
