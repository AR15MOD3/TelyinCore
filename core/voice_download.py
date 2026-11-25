"""Bounded, resumable downloads of the fixed, hash-pinned voice model manifests."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import shutil
from threading import Lock
from time import monotonic

import httpx
from runtime_identity import safe_path
from telyincore.download_sources import download_routes
from telyincore.owned_blocking import InstallationOwner


class InvalidTransfer(ValueError):
    pass


class DigestMismatch(InvalidTransfer):
    pass


class Interrupted(Exception):
    pass


@contextmanager
def stage_lock(parent):
    path = safe_path(parent, parent/'.voice-download.lock')
    with path.open('a+b') as f:
        if os.name == 'nt':
            import msvcrt
            if not f.tell():
                f.write(b'0'); f.flush()
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


class VoiceDownload(InstallationOwner):
    def __init__(self, parent, *, proxy_root=None, setup=None):
        self.parent = Path(parent)
        self.proxy_root = proxy_root
        self.setup = setup
        self.lock = Lock()
        self._init_install_owner()
        self.stage = self.parent/('.partial-' + self.name)
        self.intent = ''
        self.state = dict(controls=True, busy=False, message='', downloaded=0, total=sum(f[2] for f in self.files),
                          source='mirror', file='', phase='idle', bytesPerSecond=0)
        if self.stage.is_dir() and not self.stage.is_symlink():
            self.state.update(phase='paused', downloaded=self._size(), message='已保留下载进度。')

    def _path(self, name):
        path = safe_path(self.parent, self.stage/name)
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
            raise ValueError('模型暂存文件无效')
        return path

    def _size(self):
        return sum(min(p.stat().st_size, size) for n, _, size, _ in self.files
                   if (p := self._path(n)).is_file())

    def read(self):
        with self.lock:
            result = dict(self.state)
        if self.setup is not None:
            result["runtimeReady"] = self.setup.ready(self.parent / self.name)
        return result

    def control(self, action, source=None):
        if action in ('start', 'resume'):
            return self.start(source or getattr(self, 'preferred_source', 'mirror'))
        if action not in ('pause', 'cancel'):
            raise ValueError('未知下载操作')
        with self.lock:
            self._check_install_open_locked()
            if self.state['phase'] == 'publishing':
                raise ValueError('模型正在安装，请等待安装完成')
            if self.state['busy']:
                if self.intent != 'cancel':
                    self.intent = action
                self.state.update(phase='cancelling' if self.intent == 'cancel' else 'pausing')
            elif action == 'cancel':
                if self.stage.exists():
                    with stage_lock(self.parent):
                        shutil.rmtree(safe_path(self.parent, self.stage))
                self.state.update(downloaded=0, phase='cancelled', file='', message='已取消下载。',bytesPerSecond=0)
            return dict(self.state)

    def start(self, source='mirror'):
        if source not in self.sources:
            raise ValueError('不支持的下载源')
        with self.lock:
            self._check_install_open_locked()
            if self.state['busy'] or (self._install_thread and self._install_thread.is_alive()):
                return dict(self.state, busy=True)
            self.intent = ''
            self.preferred_source = source
            self.state.update(busy=True, file='', source=source, phase='download', route=None, bytesPerSecond=0,
                              message='正在准备下载…')
            self._launch_install_locked(source)
        return self.read()

    def _checkpoint(self):
        if self.intent:
            raise Interrupted()

    def _transfer(self, client, name, url, size, digest, base):
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        count = path.stat().st_size if path.exists() else 0
        if count > size:
            path.unlink()
            count = 0
        sha = hashlib.sha256()
        with self.lock:
            self.state.update(file=name, downloaded=base+count, phase='verify' if count==size else 'download')
        if count:
            with path.open('rb') as f:
                for chunk in iter(lambda:f.read(1024*1024), b''):
                    self._checkpoint()
                    sha.update(chunk)
        if count < size:
            options = {'headers': {'Range': f'bytes={count}-', 'Accept-Encoding': 'identity'}} if count else {}
            with client.stream('GET', url, **options) as response:
                response.raise_for_status()
                if getattr(response, 'status_code', 200) == 206:
                    if response.headers.get('content-range') != f'bytes {count}-{size-1}/{size}':
                        raise InvalidTransfer('下载源返回了错误的续传范围')
                elif count:
                    count, sha = 0, hashlib.sha256()
                last, previous = monotonic(), count
                with path.open('ab' if count else 'wb') as output:
                    for chunk in response.iter_bytes(128*1024):
                        self._checkpoint()
                        count += len(chunk)
                        if count > size:
                            raise InvalidTransfer('下载大小超过预期')
                        output.write(chunk)
                        sha.update(chunk)
                        now = monotonic()
                        with self.lock:
                            self.state['downloaded'] = base+count
                            if now-last >= 1:
                                self.state['bytesPerSecond'] = round((count-previous)/(now-last))
                                last, previous = now, count
        self._checkpoint()
        if count != size:
            raise InvalidTransfer('下载文件不完整，可继续下载')
        if sha.hexdigest() != digest:
            path.unlink()
            raise DigestMismatch('模型校验失败，该文件需要重新下载')

    def _model(self, target, source):
        if target.exists():
            if self.setup is None:
                raise FileExistsError('模型目录已存在，未覆盖。')
            if self.setup.ready(target):
                return
            with self.lock:
                self.state.update(phase='verify', message='正在校验已有模型…')
            for name, _, size, digest in self.files:
                self._checkpoint()
                path = safe_path(self.parent, target / name)
                if not path.is_file() or path.stat().st_size != size:
                    raise ValueError('已有模型文件缺失或不完整，请检查模型目录。')
                sha = hashlib.sha256()
                with path.open('rb') as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b''):
                        self._checkpoint()
                        sha.update(chunk)
                if sha.hexdigest() != digest:
                    raise ValueError('已有模型校验失败，请检查模型目录。')
            return
        safe_path(self.parent, self.stage).mkdir(exist_ok=True, mode=0o700)
        allowed = {row[0] for row in self.files}
        for p in self.stage.rglob('*'):
            if p.is_symlink() or (not p.is_dir() and p.relative_to(self.stage).as_posix() not in allowed):
                raise ValueError('暂存目录中存在意外文件，请检查后重试')
        if shutil.disk_usage(self.parent).free < self.state['total'] - self._size() + 16*1024*1024:
            raise ValueError('模型下载空间不足')
        base = 0
        for name, remote, size, digest in self.files:
            self._checkpoint()
            routes = download_routes(self.proxy_root, preferred=source)
            for index, (choice, proxy) in enumerate(routes):
                self._checkpoint()
                with self.lock:
                    self.state.update(source=choice, route='proxy' if proxy else 'direct')
                try:
                    with httpx.Client(follow_redirects=True, timeout=30, trust_env=False,
                                      **({'proxy': proxy} if proxy else {})) as client:
                        url = self.sources[choice] + remote
                        had_prefix = self._path(name).is_file()
                        try:
                            self._transfer(client, name, url, size, digest, base)
                        except DigestMismatch:
                            if choice != 'official' or not had_prefix:
                                raise
                            self._checkpoint()
                            self._transfer(client, name, url, size, digest, base)
                    break
                except (httpx.HTTPError, InvalidTransfer):
                    self._checkpoint()
                    if index == len(routes) - 1:
                        raise
            base += size
        with self.lock:
            self._checkpoint()
            self.state['phase'] = 'publishing'
        self.finish(self.stage)
        self.stage.rename(target)

    def _run(self, source='official'):
        phase, message = 'error', '下载失败，已保留进度，可重试或更换下载源。'
        try:
            self.parent.mkdir(parents=True, exist_ok=True)
            with stage_lock(self.parent):
                target = self.parent/self.name
                self._model(target, source)
                if self.setup is not None:
                    with self.lock:
                        self.state['downloaded'] = self.state['total']
                    self.setup(self, target, source)
                phase, message = 'done', self.success
                if self.setup is not None:
                    message = '模型、运行环境和实际语音自检已完成。可选择模型并保存语音设置。'
        except Interrupted:
            phase = 'cancelled' if self.intent == 'cancel' else 'paused'
            message = '已取消安装，已完成的模型和依赖保留。' if phase == 'cancelled' else '已暂停，进度已保留。'
            if phase == 'cancelled' and self.stage.exists():
                shutil.rmtree(safe_path(self.parent, self.stage))
                with self.lock:
                    self.state['downloaded'] = 0
        except (FileExistsError, ValueError) as error:
            message = str(error)
        except (OSError, httpx.HTTPError):
            pass
        finally:
            if self.intent and phase == 'error':
                phase = 'cancelled' if self.intent == 'cancel' else 'paused'
                message = '已取消安装，已完成的模型和依赖保留。' if phase == 'cancelled' else '已暂停，进度已保留。'
                if phase == 'cancelled' and self.stage.exists():
                    shutil.rmtree(safe_path(self.parent, self.stage))
            with self.lock:
                self.state.update(busy=False, phase=phase, message=message, bytesPerSecond=0)
