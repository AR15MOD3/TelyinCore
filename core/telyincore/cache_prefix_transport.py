"""Optional diagnostic seam; errors never alter upstream delivery."""
import threading
from .cache_prefix_probe import DiagnosticBudgetError


class PrefixDiagnosticTransport:
    def __init__(self, inner, probe, labels_of, emit):
        self.inner = inner
        self.probe = probe
        self.labels_of = labels_of
        self.emit = emit
        self.error = None
        self._lock = threading.RLock()
        self._seen = 0
        self._emitted = 0
        self._skipped = 0

    def coverage(self):
        with self._lock:
            return {"stream_opens": self._seen, "emitted": self._emitted,
                    "skipped": self._skipped}

    def stream_chat(self, base_url, body, **kwargs):
        with self._lock:
            self._seen += 1
        error = None
        try:
            labels = self.labels_of()
            if not isinstance(labels, dict) or any(
                    labels.get(k) is None for k in ('bot', 'scope', 'epoch', 'auth')):
                raise ValueError('missing partition labels')
            diagnostic = self.probe.observe(base_url, body, labels)
            self.emit(diagnostic)
        except DiagnosticBudgetError:
            error = 'diagnostic_budget_exceeded'
        except Exception:
            error = 'diagnostic_failed'
        with self._lock:
            if error is None:
                self._emitted += 1
            else:
                self._skipped += 1
                self.error = error
        return self.inner.stream_chat(base_url, body, **kwargs)

    async def aclose(self):
        await self.inner.aclose()

    def __getattr__(self, name):
        return getattr(self.inner, name)
