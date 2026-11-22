"""Read-only terminal feedback for potentially slow offline archive work."""
import threading
import time
from startup_terminal import menu_notice


class ArchiveProgress:
    def __init__(self):
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.stage = ''
        self.started = time.monotonic()

    def __call__(self, stage):
        with self.lock:
            self.stage = stage
            self._show(stage)

    def _show(self, text):
        # A disconnected output must never interrupt a durable transaction.
        try:
            menu_notice('archives', text)
        except (OSError, ValueError):
            pass

    def _heartbeat(self):
        while not self.stop.wait(5):
            with self.lock:
                self._show(f'处理中 · {self.stage} · 已用时 {int(time.monotonic()-self.started)} 秒')

    def __enter__(self):
        self('准备档案维护，请保持窗口打开…')
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, kind, value, traceback):
        self.stop.set()
        self.thread.join()
        if kind is not None:
            self._show('操作已停止 · 最后阶段：' + self.stage)
