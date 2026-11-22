"""Own the background refresh lifecycle of the derived memory index."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import threading

from .skills import _atomic_json


class IndexMaintenance:
    def __init__(self, runtime, refresh, *, interval=15, semantic=None, paused=None):
        self.marker = Path(runtime) / "search" / ".needs-sync"
        self.refresh = refresh
        self.semantic = semantic
        self.paused = paused
        self.interval = interval
        self._lock = threading.Lock()
        self._status = {"state": "waiting"}

    def status(self):
        return dict(self._status)

    def run_once(self):
        with self._lock:
            last = self._status.get("indexed_at", "")
            self._status = {"state": "running", "indexed_at": last}
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                self.refresh()
                self.marker.unlink(missing_ok=True)
            except Exception as error:
                state = {"state": "retry", "indexed_at": last, "at": now, "reason": type(error).__name__}
                self._status = state
                try:
                    _atomic_json(self.marker, state)
                except OSError:
                    pass
            else:
                self._status = {"state": "ready", "indexed_at": now}
                if self.semantic is not None:
                    try:
                        self._status["semantic"] = self.semantic()
                    except Exception as error:
                        self._status["semantic"] = {"state": "retry", "why": type(error).__name__}

    async def _run(self, stop):
        while not stop.is_set():
            from .owned_blocking import owned_thread
            if self.paused is not None and self.paused():
                self._status = {"state": "paused", "indexed_at": self._status.get("indexed_at", "")}
                delay = min(1, self.interval)
            else:
                await owned_thread(self.run_once)
                building = self._status.get("semantic", {}).get("state") == "building"
                delay = min(1, self.interval) if building else self.interval
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                continue

    @asynccontextmanager
    async def running(self):
        stop = asyncio.Event()
        task = asyncio.create_task(self._run(stop))
        try:
            yield
        finally:
            stop.set()
            await task
