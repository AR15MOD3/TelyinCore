"""Loop-owned interruption of one identified round, separate from application shutdown."""
import asyncio
import uuid
from .loop import RoundOutcome


class RoundControl:
    def __init__(self, record_event=None):
        self._task = None
        self._record_event = record_event
        self._id = None
        self._stopping = False
        self.last_interrupted = False
        self.last_failed = False
        self._yielding = False
        self.last_yielded = False
        self.on_change = None

    def status(self):
        return {'processing': self._task is not None, 'round_id': self._id,
                'interrupting': self._stopping, 'yielding': self._yielding}

    def yield_after_round(self, round_id):
        """Let the current round settle; do not start its next tool continuation.

        Unlike interrupt(), this never cancels an in-flight operation. The
        caller owns the checkpoint and decides whether/when to resume.
        """
        if (not isinstance(round_id, str) or not round_id or round_id != self._id
                or self._task is None or self._task.done() or self._stopping):
            return {'accepted':False, **self.status()}
        self._yielding = True
        self._notify()
        return {'accepted':True, **self.status()}

    def _notify(self):
        if callable(self.on_change):
            try:
                self.on_change(self.status())
            except Exception:
                pass  # A display observer cannot change the outcome of owned work.

    def interrupt(self, round_id):
        # Called only on the owning asyncio loop. Stale/repeated requests never cancel a successor.
        if not isinstance(round_id, str) or not round_id or round_id != self._id or self._task is None:
            return {'accepted': False, **self.status()}
        if not self._stopping and not self._task.done():
            if self._record_event is not None:
                from datetime import datetime, timezone
                self._record_event({'type': 'round-interrupt-requested',
                    'ts': datetime.now(timezone.utc).isoformat(), 'source': 'host',
                    'origin': 'internal', 'text': '操作者请求停止当前处理；已完成的工具操作不会撤销。',
                    'meta': {'round_id': self._id}})
            self._stopping = True
            self._task.cancel()
            self._notify()
        return {'accepted': True, **self.status()}

    async def run(self, action, number):
        if self._task is not None:
            raise RuntimeError('another round still owns its cleanup')
        self._id = uuid.uuid4().hex
        self._task = asyncio.create_task(action(number))
        self._stopping = False
        self.last_failed = False
        self._yielding = False
        self.last_yielded = False
        self._notify()
        try:
            result = await asyncio.shield(self._task)
            self.last_failed = bool(result.failed) and not self._stopping
            if self._yielding and not self._stopping and not result.ended and result.called_tool:
                self.last_yielded = True
                # Preserve failure/overflow facts; only end continuation here.
                from dataclasses import replace
                return replace(result, ended=True)
            return RoundOutcome(ended=True) if self._stopping else result
        except Exception:
            self.last_failed = True
            raise
        except asyncio.CancelledError:
            parent = asyncio.current_task()
            if parent is not None and parent.cancelling():
                if not self._stopping and not self._task.done():
                    self._task.cancel()
                # Repeated caller cancellation cannot cancel cleanup a second time.
                while not self._task.done():
                    try:
                        await asyncio.shield(self._task)
                    except asyncio.CancelledError:
                        continue
                    except BaseException:
                        break
                if self._task.done() and not self._task.cancelled():
                    self._task.exception()  # Retrieve any secondary work failure.
                raise
            if self._stopping:
                return RoundOutcome(ended=True)
            if self._task.cancelled():
                # shield replaces a child's typed cancellation with a fresh
                # CancelledError. Preserve the original owner-bound rejection
                # so the caller can distinguish orderly shutdown from faults.
                self._task.result()
            raise
        finally:
            self.last_interrupted = self._stopping
            self._task = None
            self._id = None
            self._stopping = False
            self._yielding = False
            self._notify()
