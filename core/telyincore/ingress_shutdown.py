"""Same-instance ingress custody; observing never cancels or replays business work."""
import asyncio
from .plugin_ingress import PluginIngress


class IngressShutdown:
    def __init__(self, ingress):
        if type(ingress) is not PluginIngress:
            raise TypeError('exact plugin ingress required')
        self._ingress = ingress
        self._observers = None
        self._loop = None
        self._retained = []
        self._release_failures = []
        self._receipt_sources = []
        self._failed_receipt_sources = []
        self._failure_journal = None
        self._evidence_attempted = []
        self._evidence_errors = []

    def bind_failure_journal(self, journal):
        from .receipt_incidents import ReceiptFailureJournal
        if (type(journal) is not ReceiptFailureJournal or self._failure_journal is not None
                or self._ingress.shutdown_state()['accepting'] is not True):
            raise RuntimeError('original accepting ingress failure journal required')
        self._failure_journal = journal

    def _record_failure(self, error, reason):
        if (self._failure_journal is None or any(error is old for old in self._evidence_attempted)
                or any(error is old for old in self._evidence_errors)):return
        self._evidence_attempted.append(error)  # Unknown persistence outcome never replays.
        try:self._failure_journal.record(reason,error=error,lane='ingress')
        except BaseException as secondary:
            self._evidence_errors.append(secondary)
            self._release_failures.append(secondary)
            from .receipt_incidents import safe_note
            note_error=safe_note(error,'receipt failure evidence write failed: '+type(secondary).__name__)
            if note_error is not None:
                self._evidence_errors.append(note_error);self._release_failures.append(note_error)

    def failure_diagnostics(self):
        from .receipt_incidents import error_facts
        return {'release_failures':[error_facts(e) for e in self._release_failures[:16]],
            'failed_receipt_sources':len(self._failed_receipt_sources),
            'evidence_errors':[error_facts(e) for e in self._evidence_errors[:16]],
            'original_failure_count':len(self._release_failures)}

    def retain_failure(self, error):
        # Never cleared by a later successful drain or by observing the error.
        if all(error is not old for old in self._release_failures):
            self._release_failures.append(error)
            self._record_failure(error,'RELEASE_FAILURE_RETAINED')

    def bind_receipts(self, runner):
        if self._ingress.shutdown_state()['accepting'] is not True:
            raise RuntimeError('receipt owner binding after shutdown')
        query = getattr(runner, 'receipt_shutdown_state', None)
        if not callable(query):
            raise TypeError('receipt shutdown producer required')
        if all(runner is not old for old, _ in self._receipt_sources):
            self._receipt_sources.append((runner, query))

    def retain(self, resource):
        if all(resource is not old for old in self._retained):
            self._retained.append(resource)

    @property
    def ingress(self):
        return self._ingress

    def state(self):
        state = dict(self._ingress.shutdown_state())
        receipt_pending = False
        for runner, query in self._receipt_sources:
            try:
                evidence = query()  # Captured producer of the original runner.
                if (type(evidence) is not dict
                        or type(evidence.get('pending')) is not bool
                        or type(evidence.get('incomplete')) is not bool):
                    raise RuntimeError('invalid receipt shutdown evidence')
                receipt_pending = receipt_pending or evidence['pending']
                if evidence['incomplete']:
                    raise RuntimeError('receipt ledger responsibility unresolved')
            except BaseException as error:
                # Record once per source, retaining original query and runner.
                if all(runner is not old for old in self._failed_receipt_sources):
                    self._failed_receipt_sources.append(runner)
                    # No mutation of an arbitrary producer exception object.
                    self._release_failures.append(error)
        state['receipt_pending'] = receipt_pending
        state['release_failed'] = bool(self._release_failures or self._failed_receipt_sources)
        return state

    def begin(self):
        self._ingress.begin_shutdown()

    @staticmethod
    def _consume(future):
        if future.done() and not future.cancelled():
            future.exception()  # Responsibility remains sticky in producer state.

    def observers(self):
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError('ingress observer loop changed')
        if self._observers is None:
            self._loop = loop
            self._observers = self._ingress.shutdown_tasks()
            for future in self._observers:
                future.add_done_callback(self._consume)
        return self._observers

    def assert_releasable(self):
        state = self.state()
        if (state['accepting'] or state['pending'] != 0 or state['buffered'] != 0
                or state['failed'] != 0 or state['drained'] is not True
                or state['receipt_pending'] or state['release_failed']):
            error=RuntimeError('plugin ingress responsibility unresolved')
            BaseException.add_note(error,'ingress failure diagnostics: '+repr(self.failure_diagnostics()))
            raise error

    async def wait(self, *, deadline):
        loop = asyncio.get_running_loop()
        if loop.time() >= deadline:
            self.assert_releasable()  # Expired budget is observation-only.
            return
        try:
            self.begin()
            pending = {f for f in self.observers() if not f.done()}
            if pending:
                await asyncio.wait(pending, timeout=max(0.0, deadline-loop.time()))
            self.assert_releasable()
        except BaseException as error:
            # Diagnostic evidence is an explicit cleanup effect, never a write
            # from state()/observers or a new effect after the deadline expired.
            if loop.time()<deadline:
                for failure in tuple(self._release_failures):
                    self._record_failure(failure,'RECEIPT_OBSERVER_FAILURE_RETAINED')
            BaseException.add_note(error,'ingress failure diagnostics: '+repr(self.failure_diagnostics()))
            raise
