"""Keep the bundled SL process with its Linux Core owner, including on abrupt exit."""
import contextlib
import fcntl
import http.client
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
from scripts.linux_snowluma import listener_pid, program


def owner_pid(fdinfo):
    for line in fdinfo.read_text().splitlines():
        if line.startswith('Pid:'):
            return int(line.split()[1])
    raise ValueError('无法确认 Core 进程句柄')


def web_port(state):
    config = state / 'config/runtime.json'
    saved = json.loads(config.read_bytes()) if config.exists() else {}
    value = os.environ.get('SNOWLUMA_WEBUI_PORT') or saved.get('webuiPort', 5099)
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError('SL 管理页端口无效')
    return port


def ready(core, state, port, owner):
    try:
        pid = int((state / 'process.pid').read_text())
        proc = Path('/proc') / str(pid)
        if listener_pid(core, {port}) is None:
            return False
        # A previous Core may have just exited; wait for its watcher to release SL.
        ppid = int((proc / 'stat').read_text().rsplit(')', 1)[1].split()[1])
        parent = Path('/proc') / str(ppid)
        args = (parent / 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
        if args[1:3] == [os.fsencode(core / 'scripts/linux_sl_companion.py'), b'--watch']:
            if owner_pid(parent / 'fdinfo' / str(int(args[3]))) != owner:
                return False
        with contextlib.closing(http.client.HTTPConnection('127.0.0.1', port, timeout=.6)) as conn:
            conn.request('GET', '/')
            return conn.getresponse().status == 200
    except (OSError, ValueError, IndexError, http.client.HTTPException):
        return False


@contextlib.contextmanager
def managed_watcher(core, port):
    """Pin this Core's watcher before exposing the panel stop action."""
    state = core.parent / 'runtime/linux-snowluma'
    handle = None
    try:
        server = listener_pid(core, {port})
        if server is not None:
            leader = int((state/'process.pid').read_text())
            parent = int(Path(f'/proc/{leader}/stat').read_text().rsplit(')', 1)[1].split()[1])
            candidate = os.pidfd_open(parent)
            try:
                proc = Path('/proc') / str(parent)
                argv = (proc/'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
                if (argv[1:3] == [os.fsencode(core/'scripts/linux_sl_companion.py'), b'--watch']
                        and owner_pid(proc/'fdinfo'/str(int(argv[3]))) == os.getpid()
                        and listener_pid(core, {port}) == server):
                    handle = candidate
            finally:
                if handle is None:
                    os.close(candidate)
    except (OSError, ValueError, IndexError):
        pass
    try:
        yield handle
    finally:
        if handle is not None:
            os.close(handle)


def watch(owner_fd, lock_fd):
    state = CORE.parent / 'runtime/linux-snowluma'
    root = program(CORE)
    os.umask(0o077)
    if select.select([owner_fd], [], [], 0)[0]:
        return
    def shutdown(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, shutdown)
    parent = os.getpid()
    def stop_if_watcher_dies():
        # Linux parent-death signals are cleared on fork and must be armed in the child.
        import ctypes
        if ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0) != 0 or os.getppid() != parent:
            os._exit(1)
    from snowluma_status_policy import protect_status_command
    protect_status_command(state)
    child = subprocess.Popen([root / 'node', root / 'index.mjs'], cwd=state,
                             start_new_session=True, preexec_fn=stop_if_watcher_dies,
                             pass_fds=(lock_fd,), env=dict(os.environ, SNOWLUMA_WEBUI_HOST='127.0.0.1'))
    try:
        (state / 'process.pid').write_text(str(child.pid))
        child_fd = os.pidfd_open(child.pid)
        try:
            select.select([owner_fd, child_fd], [], [])
        finally:
            os.close(child_fd)
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        (state / 'process.pid').unlink(missing_ok=True)


def ensure(core, owner_fd):
    import platform
    if platform.machine() not in ('x86_64', 'amd64') or not (core / 'linux-components').is_dir():
        return
    owner = owner_pid(Path('/proc/self/fdinfo') / str(owner_fd))
    state = core.parent / 'runtime/linux-snowluma'
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    log = state / 'launcher.log'
    port = web_port(state)
    deadline = time.monotonic() + 20
    helper = None
    while not select.select([owner_fd], [], [], 0)[0] and time.monotonic() < deadline:
        if helper is not None and helper.poll() is not None:
            break
        if helper is None:
            with (state / 'process.lock').open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    # The watcher and Node inherit this same open file description.
                    # Manual --snowluma and concurrent wake attempts use the same lock.
                    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
                    with os.fdopen(os.open(log, flags, 0o600), 'ab') as output:
                        os.fchmod(output.fileno(), 0o600)
                        helper = subprocess.Popen(
                            [sys.executable, str(core / 'scripts/linux_sl_companion.py'), '--watch',
                             str(owner_fd), str(lock.fileno())], pass_fds=(owner_fd, lock.fileno()),
                            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                            start_new_session=True)
        if ready(core, state, port, owner):
            verb = '已随 Bot 启动' if helper is not None else '已复用'
            print(f'[SL] SnowLuma {verb}，管理页 http://127.0.0.1:{port}/', flush=True)
            if helper is not None:
                print(f'[SL] 登录提示 · {log.relative_to(core.parent)}', flush=True)
            return
        time.sleep(.15)
    if helper is not None and helper.poll() is None:
        helper.terminate()
        helper.wait(timeout=12)
    raise ValueError(f'SL 管理页未就绪，请核对端口 {port} 与日志 {log}')


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--watch':
        watch(int(sys.argv[2]), int(sys.argv[3]))
    elif len(sys.argv) == 3 and sys.argv[1] == '--with-parent':
        parent = int(sys.argv[2])
        if parent != os.getppid():
            raise SystemExit('Core 启动已取消')
        fd = os.pidfd_open(parent)
        try:
            if parent != os.getppid():
                raise SystemExit('Core 启动已取消')
            ensure(CORE, fd)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print('[SL] ' + str(error), file=sys.stderr)
            raise SystemExit(1)
        finally:
            os.close(fd)
    else:
        raise SystemExit('需要 Core 进程句柄')
