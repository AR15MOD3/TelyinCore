"""Windows inherited message-Pipe IO with absolute, cancellable IO deadlines.

No blocking recv_bytes/GetOverlappedResult(True), draining oversized messages,
reader threads or TCP fallback. One write == one bounded length-prefixed message.
Only local overlapped message-mode handles from multiprocessing.Pipe are supported.
Transport deadlines do NOT bound trusted factory execution or OS identity probes.
"""
import math
import os
import struct
import time
from threading import Lock
from physical_close import close_once, close_after_failure

MAX_FRAME = 4096
ERROR_IO_INCOMPLETE = 996  # Win32 constant not exported by Python 3.11 _winapi
_QUARANTINE = []  # retain buffers if cancellation does not finish within 50 ms
_QUARANTINE_LOCK = Lock()


def _api():
    if os.name != 'nt':
        raise ValueError('deadline transport requires Windows message Pipe')
    import _winapi
    return _winapi


def _reap(api):
    # Never destroy a still-pending OVERLAPPED: its buffer may remain kernel-owned.
    with _QUARANTINE_LOCK:
        _QUARANTINE[:] = [ov for ov in _QUARANTINE
                         if api.WaitForMultipleObjects([ov.event], False, 0) != 0]


def _pin_pending(ov):
    with _QUARANTINE_LOCK:
        _QUARANTINE.append(ov)


class DeadlinePipe:
    def __init__(self, connection):
        self.api = _api()
        from multiprocessing.connection import PipeConnection
        if type(connection) is not PipeConnection:
            raise ValueError('private overlapped Pipe required; no stream fallback')
        _reap(self.api)
        if _QUARANTINE:
            raise ValueError('pending cancelled IO quarantined; transport unavailable')
        self.connection = connection
        self.closed = False
        self._lock = Lock()

    def ready(self):
        """Readiness only; never consumes a header/body or grants peer authority.

        Native poll(0) may report EOF as ready. Actual reads ALWAYS use the
        existing bounded overlapped message IO; no recv_bytes fallback.
        """
        if self.closed or not self._lock.acquire(blocking=False):
            self.close()
            raise ValueError('transport closed or busy')
        try:
            return self.connection.poll(0)
        except BaseException as primary:
            close_after_failure(self, primary)
            raise
        finally:
            self._lock.release()

    def _remaining(self, deadline):
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or not 0 < deadline - time.monotonic() <= 5.001):
            raise TimeoutError('channel IO deadline expired or invalid')
        return max(0, math.ceil((deadline - time.monotonic()) * 1000))

    def _cancel(self, ov):
        try:
            ov.cancel()
        finally:
            if self.api.WaitForMultipleObjects([ov.event], False, 50) != 0:
                _pin_pending(ov)

    def _io(self, payload, deadline):
        if self.closed or not self._lock.acquire(blocking=False):
            self.close()
            raise ValueError('transport closed or busy')
        ov = None
        finished = False
        try:
            _reap(self.api)
            if _QUARANTINE:
                raise ValueError('cancelled IO quarantine active')
            self._remaining(deadline)
            handle = self.connection.fileno()
            if payload is None:
                ov, error = self.api.ReadFile(handle, MAX_FRAME + 5, overlapped=True)
            else:
                ov, error = self.api.WriteFile(handle, payload, overlapped=True)
            if error == self.api.ERROR_IO_PENDING:
                wait = self.api.WaitForMultipleObjects([ov.event], False, self._remaining(deadline))
                if wait != 0:
                    raise TimeoutError('channel IO deadline')
            elif error not in (0, self.api.ERROR_MORE_DATA):
                raise OSError('channel IO unavailable')
            count, error = ov.GetOverlappedResult(False)
            if error == ERROR_IO_INCOMPLETE:
                raise TimeoutError('channel IO incomplete')
            finished = True
            self._remaining(deadline)  # no late successes / per-chunk timeout renewal
            if error != 0:
                raise OSError('truncated or oversized channel message')
            if payload is not None:
                if count != len(payload):
                    raise OSError('partial channel write')
                return None
            return bytes(ov.getbuffer())[:count]
        except BaseException as primary:
            try:
                if ov is not None and not finished:
                    self._cancel(ov)
            except BaseException as secondary:
                primary.cleanup_errors = (secondary,)
            close_after_failure(self, primary)
            raise
        finally:
            self._lock.release()

    def send_frame(self, raw, deadline):
        try:
            if type(raw) is not bytes or not 0 < len(raw) <= MAX_FRAME:
                raise ValueError('invalid transport frame')
            self._io(struct.pack('!I', len(raw)) + raw, deadline)
        except BaseException as primary:
            close_after_failure(self, primary)
            raise

    def recv_frame(self, deadline):
        try:
            raw = self._io(None, deadline)
            if len(raw) < 4:
                raise ValueError('partial channel header')
            size = struct.unpack('!I', raw[:4])[0]
            if not 0 < size <= MAX_FRAME or len(raw) != size + 4:
                raise ValueError('partial or oversized channel body')
            return raw[4:]
        except BaseException as primary:
            close_after_failure(self, primary)
            raise

    def close(self):
        close_once(self, self.connection.close)


def deadline_transport(connection):
    """Native handles auto-wrap; trusted test transports must implement deadlines.

No legacy poll()+recv_bytes() duck-typed fallback is allowed. As with injected
host factories, Python object injection is trusted host code, not an RPC surface.
"""
    if isinstance(connection, DeadlinePipe):
        return connection
    if all(callable(getattr(connection, name, None))
           for name in ('send_frame', 'recv_frame', 'close')):
        return connection
    return DeadlinePipe(connection)
