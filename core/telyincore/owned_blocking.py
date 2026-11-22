"""Offload blocking work while retaining its lifetime through caller cancellation."""
import asyncio


class InstallationOwner:
    """Retain the actual installation thread; stopping never kills a download.

    Subclasses provide lock/state/_run. Admission and thread publication share
    that lock; close returns False while accepted work or cleanup is unresolved.
    """
    def _init_install_owner(self):
        self._install_thread = None
        self._install_stopping = False
        self._install_error = None

    def _check_install_open_locked(self):
        if self._install_stopping:
            raise RuntimeError('installation owner is stopping')

    def _launch_install_locked(self, source):
        import threading
        self._check_install_open_locked()
        if self._install_thread is not None and self._install_thread.is_alive():
            raise RuntimeError('previous installation thread still running')
        def run():
            try:
                self._run(source)
            except BaseException as error:
                with self.lock:
                    self._install_error = error
                    self.state.update(busy=False, phase='cleanup_failed',
                                      message='安装线程异常退出，责任保留；需检查后恢复。')
        self._install_thread = threading.Thread(target=run, name='owned-model-install', daemon=False)
        try:
            self._install_thread.start()
        except BaseException as error:
            self._install_error = error
            self.state.update(busy=False, phase='cleanup_failed')
            raise

    def installation_shutdown_state(self):
        with self.lock:
            pending = self._install_thread is not None and self._install_thread.is_alive()
            return {'accepting': not self._install_stopping, 'pending': pending,
                    'cleanup_failed': self._install_error is not None or self.state.get('phase') == 'cleanup_failed',
                    'phase': self.state.get('phase')}

    def close(self, *, timeout=0.0):
        import math
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError('finite nonnegative installation drain budget required')
        with self.lock:
            self._install_stopping = True
            thread = self._install_thread
        if thread is not None and thread.ident is not None:
            thread.join(timeout)
        state = self.installation_shutdown_state()
        return not state['pending'] and not state['cleanup_failed']


async def owned_thread(function, /, *args, **kwargs):
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancelled = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancelled = cancelled or error
        except BaseException:
            break
    try:
        result = task.result()
    except BaseException as error:
        if cancelled is not None:
            cancelled.worker_error = error
            cancelled.add_note('owned worker also failed: ' + type(error).__name__)
            raise cancelled
        raise
    if cancelled is not None:
        raise cancelled
    return result
