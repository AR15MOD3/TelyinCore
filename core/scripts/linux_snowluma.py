"""Install the bundled Linux release; keep mutable SnowLuma state outside it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile

CORE = Path(__file__).resolve().parents[1]
VERSION = '2.4.2'
ARCHIVE = f'SnowLuma-v{VERSION}-linux-x64.tar.gz'
SHA256 = '2ed2cbcf826657fcbaf9c58c48718a44bb0e432bac7387e3b53be96803b1f6a3'
FILES_SHA256 = '678c7cb02e22d519a84c66c0123e73f21c3e892be2b483e2a2e7067b2a28815e'


# The native release stays pinned. This local UI correction applies only to its
# exact v2.4.2 router bundle. Both valid byte sequences are checked below.
ROUTER = 'client/assets/router-_TYMdJui.js'
ORIGINAL_ROUTER = '2024ef8681327b219c6ef47ce2d7c33fa3023d60bb47dd05110e306194574643'
FIXED_ROUTER = 'd605cdaa19138a785c2b13be0261f3c1544bf2732f6321ad1da6216b2aeb575d'


def corrected_router(original):
    code = original.decode('utf-8')
    changes = (
        ('已向进程 ${e} 注入 SnowLuma，等待管道连接…',
         '已提交进程 ${e} 的加载请求，请查看进程状态。'),
        ('},r);t===`unload`&&a.process?.status',
         """},async()=>{
             const response = await r();
             const process = response?.process;
             if (t === 'load' && (process?.error || process?.status === 'error')) {
                 throw new Error(process.error || 'QQ 组件加载失败');
             }
             return response;
         });t===`unload`&&a.process?.status"""),
        ('卸载失败`)):o(i.ok(e)),await d.current?.()',
         '卸载失败`)):o(t===`load`?"":i.ok(e)),await d.current?.()'),
    )
    for old, new in changes:
        if code.count(old) != 1:
            raise ValueError('无法识别 SL 管理页版本')
        code = code.replace(old, new)
    return code.encode('utf-8')


def apply_router_correction(root):
    path = root / ROUTER
    original = path.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    if digest == FIXED_ROUTER:
        return
    if digest != ORIGINAL_ROUTER:
        raise ValueError('SL 管理页校验失败')
    fixed = corrected_router(original)
    if hashlib.sha256(fixed).hexdigest() != FIXED_ROUTER:
        raise ValueError('SL 管理页修正校验失败')
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        pending = Path(stream.name)
        try:
            stream.write(fixed)
            stream.flush()
            os.fchmod(stream.fileno(), path.stat().st_mode & 0o777)
            os.replace(pending, path)
        finally:
            pending.unlink(missing_ok=True)


def program(core):
    return core.parent / 'services' / ('SnowLuma-linux-x64-' + VERSION)


def listener_pid(core, ports):
    """Identify this release's SL supervisor or its native server child."""
    try:
        root = program(core)
        state = core.parent / 'runtime/linux-snowluma'
        leader = int((state / 'process.pid').read_text())
        def is_sl(pid):
            proc = Path('/proc') / str(pid)
            return ((proc / 'exe').resolve(strict=True) == root / 'node'
                    and (proc / 'cwd').resolve(strict=True) == state
                    and (proc / 'cmdline').read_bytes().split(b'\0')[1:2] == [os.fsencode(root / 'index.mjs')])
        if not is_sl(leader):
            return None
        sockets = {}
        for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
            row = line.split()
            address, raw_port = row[1].split(':')
            port = int(raw_port, 16)
            if row[3] == '0A' and port in ports:
                if address != '0100007F' or port in sockets:
                    return None
                sockets[port] = 'socket:[' + row[9] + ']'
        if not ports or sockets.keys() != ports:
            return None
        children = Path(f'/proc/{leader}/task/{leader}/children')
        for pid in [leader, *map(int, children.read_text().split())]:
            if not is_sl(pid):
                continue
            owned = set()
            for fd in Path(f'/proc/{pid}/fd').iterdir():
                try:
                    owned.add(os.readlink(fd))
                except FileNotFoundError:
                    continue
            if set(sockets.values()) <= owned and (pid == leader or str(pid) in children.read_text().split()):
                return pid
    except (OSError, ValueError, IndexError):
        pass
    return None


def probe(root):
    release = json.loads((root / 'release.json').read_bytes())
    if (release['target'], release['version']) != ('linux-x64', VERSION):
        raise ValueError('SL 组件平台或版本错误')
    cmd = [str(root / 'node')]
    subprocess.run(cmd + ['check-node-version.cjs'], cwd=root, check=True, timeout=15)
    subprocess.run(cmd + ['--check', 'index.mjs'], cwd=root, check=True, timeout=20)
    subprocess.run(cmd + ['-e', """
        for (const file of ['snowluma-linux-x64.node', 'websocket-linux-x64.node',
                'ffmpeg/ffmpegAddon.linux.x64.node']) require('./native/' + file);
    """], cwd=root, check=True, timeout=15)


def verify(root, manifest):
    for name, expected in manifest.items():
        file = root / name
        if file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(root.resolve()):
            raise ValueError('缺少或重定向的 SL 文件 ' + name)
        with file.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if name == ROUTER and expected == ORIGINAL_ROUTER and digest == FIXED_ROUTER:
                continue
            if digest != expected:
                raise ValueError('SL 文件校验失败 ' + name)


def prepare(core, *, check=False):
    if sys.platform != 'linux' or platform.machine() not in ('x86_64', 'amd64'):
        raise ValueError('此 SL 组件需要 Linux x86_64')
    components = core / 'linux-components'
    raw = (components / 'snowluma-files.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != FILES_SHA256:
        raise ValueError('Linux SL 文件清单校验失败')
    manifest = json.loads(raw)
    root = program(core)
    if root.parent.is_symlink() or root.is_symlink():
        raise ValueError('SL 程序目录不能是符号链接')
    if root.is_dir() or check:
        verify(root, manifest)
        if not check:
            apply_router_correction(root)
        probe(root)
        return root
    root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    import fcntl
    with (root.parent / '.snowluma-install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if root.exists():
            verify(root, manifest)
            apply_router_correction(root)
            probe(root)
            return root
        with (components / ARCHIVE).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != SHA256:
                raise ValueError('随包 Linux SL 压缩包校验失败')
            stream.seek(0)
            with tempfile.TemporaryDirectory(dir=root.parent, prefix='.sl-stage-') as tmp:
                stage = Path(tmp) / 'program'
                stage.mkdir()
                with tarfile.open(fileobj=stream, mode='r:gz') as archive:
                    for member in archive:
                        if member.isdir():
                            continue
                        name = member.name.removeprefix('./')
                        if not member.isfile() or name not in manifest or '..' in Path(name).parts or Path(name).is_absolute():
                            raise ValueError('SL 包文件列表错误')
                        target = stage / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.extractfile(member) as source, target.open('xb') as dest:
                            shutil.copyfileobj(source, dest)
                        target.chmod(0o755 if name in ('node', 'launcher.sh') else 0o644)
                verify(stage, manifest)
                apply_router_correction(stage)
                probe(stage)
                stage.rename(root)
    return root


def main(argv=None):
    p = argparse.ArgumentParser()
    opts = p.add_mutually_exclusive_group()
    opts.add_argument('--check', action='store_true')
    opts.add_argument('--start', action='store_true')
    a = p.parse_args(argv)
    try:
        root = prepare(CORE, check=a.check)
        print('Linux SnowLuma ' + VERSION + ' 程序和原生组件自检通过。', flush=True)
        if a.start:
            state = CORE.parent / 'runtime' / 'linux-snowluma'
            state.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chdir(state)
            os.umask(0o077)
            # Retain the lock across exec so a second launcher cannot steal the PID.
            import fcntl
            lock = (state / 'process.lock').open('a')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('此部署的 SL 已在运行') from None
            if str(CORE) not in sys.path:
                sys.path.insert(0, str(CORE))
            from snowluma_status_policy import protect_status_command
            protect_status_command(state)
            os.set_inheritable(lock.fileno(), True)
            (state / 'process.pid').write_text(str(os.getpid()))
            env = dict(os.environ, SNOWLUMA_WEBUI_HOST='127.0.0.1')
            os.execve(root / 'node', [str(root / 'node'), str(root / 'index.mjs')], env)
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError) as error:
        print('Linux SL 组件未就绪：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
