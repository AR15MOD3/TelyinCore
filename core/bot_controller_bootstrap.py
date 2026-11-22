"""Explicit experimental parent bootstrap; NOT an empty-host authority.

Trusted callers supply an already sealed registry and a spawn-picklable Python
entrypoint. No CLI/env enablement, discovery, adoption, credentials or network.
The child's arbitrary descendant paths remain UNTRACKED, so coverage is revoked
at birth. This helper does not make TTS/SnowLuma/production starts available.
"""
import multiprocessing
from threading import Event, Lock

from bot_host_registry import HostEvidenceRegistry
from bot_parent_launch import FixedProcessFactory
from bot_lifecycle_channel import LifecycleBroker, LifecycleClient, private_channel


def _controller_entry(connection, key, session, release, timeout, target, args, shutdown_receiver=None):
    # No target code and no lifecycle request before the parent's registration
    # and broker construction succeed. Parent failure leaves only a bounded wait.
    if not release.wait(timeout):
        connection.close()
        raise SystemExit(2)
    client = LifecycleClient(connection, key, session, timeout=timeout)
    try:
        if shutdown_receiver is None:
            target(client, *args)
        else:
            target(client, *args, shutdown_receiver=shutdown_receiver)
    finally:
        client.close()


class ControllerBootstrap:
    """One trusted spawn, explicit pump, retained handle, independent retirement.

Failure/close never kills, adopts or forgets a child. The caller retains this
object and may wait_and_retire after its own controller exits. Timeout is not a
whole-target deadline. Only bootstrap/IO waits and explicit join are bounded.
"""
    def __init__(self, registry, bot, target, *, args=(), factories=None, timeout=2, fixed_only=False, private_shutdown=False):
        factories = {} if factories is None else factories
        if (type(private_shutdown) is not bool or type(fixed_only) is not bool
                or type(registry) is not HostEvidenceRegistry or bot not in registry.bots
                or not callable(target) or type(args) is not tuple
                or type(factories) is not dict or len(factories) > 16
                or type(timeout) not in (int, float) or not 0 < timeout <= 5):
            raise ValueError('invalid trusted controller bootstrap')
        # Validate the same slot grammar before any child is created.
        from bot_lifecycle_channel import _SLOT
        if any(type(k) is not str or not _SLOT.fullmatch(k) or not callable(v)
               for k, v in factories.items()):
            raise ValueError('invalid trusted controller factories')
        self.registry, self.bot = registry, bot
        self.target, self.args = target, args
        self.factories, self.timeout = dict(factories), timeout
        self._fixed_only = fixed_only
        self._private_shutdown = private_shutdown
        self._shutdown_event = self._shutdown_identity = self._shutdown_row = None
        self._shutdown_process = None
        self._shutdown_attempted = False
        self._requesting_shutdown = Lock()
        self._validate_fixed_factories()
        self.process = self.broker = self.connection = None
        self.attempted = self.registered = self.retired = self.closed = False
        self.pid = None
        self._exit_recorded = False  # separate native retirement from handle cleanup
        self._release = None  # retain spawn SemLock until child has exited
        self._supervising = Lock()
        self._starting = Lock()
        self._retiring = Lock()

    def _validate_fixed_factories(self):
        # Opt-in strictness must be enforced BEFORE a child exists, not just
        # when constructing its broker. Trusted Python is not a sandbox.
        if self._fixed_only and (type(self.factories) is not dict or any(
                type(v) is not FixedProcessFactory for v in self.factories.values())):
            raise ValueError('fixed controller factories required')

    def _require_open(self):
        if self.closed:
            raise ValueError('trusted controller bootstrap closed')

    @staticmethod
    def _close_endpoint(endpoint):
        try:
            endpoint.close()
        except Exception:
            pass  # logical denial is not proof that OS cleanup succeeded

    def start(self):
        if not self._starting.acquire(blocking=False):
            self.close()
            raise ValueError('controller bootstrap busy')
        try:
            return self._start()
        finally:
            self._starting.release()

    def _start(self):
        if self.attempted or self.closed:
            raise ValueError('controller bootstrap is single use')
        self.attempted = True
        child = None
        try:
            self._validate_fixed_factories()  # recheck mutable trusted host plan
            context = multiprocessing.get_context('spawn')
            # Window checks sealing/fence before pipe creation or spawn.
            with self.registry.start_window(self.bot) as birth:
                self.connection, child, key, session = private_channel(context)
                self._require_open()
                release = self._release = context.Event()
                receiver = None
                if self._private_shutdown:
                    from bot_controller_shutdown import ShutdownReceiver
                    self._shutdown_event = context.Event()
                    receiver = ShutdownReceiver(self._shutdown_event)
                self.process = context.Process(target=_controller_entry,
                    args=(child, key, session, release, self.timeout, self.target, self.args, receiver))
                self._require_open()
                try:
                    self.process.start()
                finally:
                    # start may be interrupted AFTER the OS child was created.
                    # Retain only the PID from our own process handle, not a claim
                    # supplied by the worker. This is NOT native registration.
                    try:
                        pid = self.process.pid
                    except Exception:
                        pid = None
                    if type(pid) is int and 0 < pid < 0xFFFFFFFF:
                        self.pid = pid
                self._require_open()
                child.close()
                self._require_open()
                birth.commit(self.pid, untracked_descendants=True)
                self.registered = True
            self._require_open()
            controller = self.registry._alive(self.pid)
            self._require_open()
            self.broker = LifecycleBroker(self.registry, self.bot,
                controller, self.connection, key, session,
                self.factories, timeout=self.timeout, fixed_only=self._fixed_only)
            self._require_open()
            if self._private_shutdown:
                self._shutdown_process = self.process
                self._shutdown_identity = dict(controller)
                self._shutdown_row = dict(self.registry._processes[self.pid])
            release.set()  # private gate, never argv/env/files
            self._require_open()
            return self
        except BaseException as error:
            self.close()
            if isinstance(error, Exception):
                raise ValueError('trusted controller bootstrap unavailable') from None
            raise  # preserve cancellation after revocation and endpoint cleanup
        finally:
            if child is not None:
                self._close_endpoint(child)

    def request_shutdown(self):
        """Signal this retained controller only. Never wait/kill/retire or ACK.

        Event capability is inherited during this spawn, not found by endpoint.
        Failure revokes and retains ownership; no uncertain automatic retry.
        """
        if not self._requesting_shutdown.acquire(blocking=False):
            self.close()
            raise ValueError('controller shutdown busy')
        try:
            self._require_open()
            if (not self._private_shutdown or self._shutdown_event is None
                    or self._shutdown_attempted or not self.registered or self.retired
                    or self.process is None or self.process is not self._shutdown_process
                    or self.process.pid != self.pid
                    or self.process.exitcode is not None
                    or self._shutdown_row is None
                    or self.registry._processes.get(self.pid) != self._shutdown_row
                    or self.registry._alive(self.pid) != self._shutdown_identity):
                raise ValueError('owned controller shutdown unavailable')
            self._require_open()
            self.registry.revoke_coverage()  # request never grants lifecycle starts
            self._require_open()
            if (self.process is not self._shutdown_process or self.process.pid != self.pid
                    or self.process.exitcode is not None
                    or self.registry._processes.get(self.pid) != self._shutdown_row):
                raise ValueError('controller changed during shutdown check')
            self._shutdown_attempted = True
            self._shutdown_event.set()  # exact inherited capability, no HTTP
        except BaseException as error:
            self.close()
            if isinstance(error, Exception):
                raise ValueError('owned controller shutdown unavailable; handle retained') from None
            raise
        finally:
            self._requesting_shutdown.release()

    def serve_once(self):
        if self.closed or self.broker is None:
            raise ValueError('controller bootstrap unavailable')
        try:
            self.broker.serve_once()
        except BaseException:
            self.close()
            raise

    def tick(self):
        """One explicit supervision step; never renews or auto-restarts a session."""
        if self.closed or self.broker is None:
            raise ValueError('controller bootstrap unavailable')
        try:
            return self.broker.tick()
        except BaseException:
            self.close()
            raise

    def supervise(self, *, stop, interval=0.05, max_ticks=100):
        """Explicit owner-thread bounded pump; ALWAYS revokes on return/exit.

        Not a background watchdog or whole-method wall-clock bound: native
        probes/host callbacks can block. A ready frame uses the normal bounded
        IO path. No renewal, restart, kill, retirement, or empty-host authority.
        The caller must retain this object and independently retire its child.
        """
        if not self._supervising.acquire(blocking=False):
            self.close()
            raise ValueError('controller supervision busy')
        try:
            if (type(stop) is not Event or type(max_ticks) is not int
                    or not 1 <= max_ticks <= 10000
                    or type(interval) not in (int, float) or not 0 < interval <= 0.25):
                raise ValueError('invalid bounded supervision plan')
            self._require_open()
            if self.broker is None:
                raise ValueError('controller bootstrap unavailable')
            ticks = handled = 0
            while True:
                self._require_open()
                stopped = stop.is_set()
                self._require_open()
                if stopped:
                    return dict(reason='stop', ticks=ticks, handled=handled)
                result = self.tick()
                if type(result) is not bool:
                    raise ValueError('invalid supervision tick result')
                self._require_open()
                ticks += 1
                handled += int(result)
                if ticks == max_ticks:
                    return dict(reason='budget', ticks=ticks, handled=handled)
                stop.wait(interval)  # interruptible idle wait; no background thread
        finally:
            try:
                self.close()  # even normal budget/stop is denial, not health proof
            finally:
                self._supervising.release()

    def close(self):
        self.closed = True
        self.registry.revoke_coverage()
        if self.broker is not None:
            self._close_endpoint(self.broker)
        elif self.connection is not None:
            self._close_endpoint(self.connection)

    def wait_and_retire(self, timeout=5):
        # Keep join, native retirement and handle cleanup in ONE owner span.
        # A competing caller may revoke but never joins/closes/forgets twice.
        if not self._retiring.acquire(blocking=False):
            self.close()
            raise ValueError('controller retirement busy')
        try:
            return self._wait_and_retire(timeout)
        finally:
            self._retiring.release()

    def _wait_and_retire(self, timeout):
        if type(timeout) not in (int, float) or not 0 < timeout <= 10:
            raise ValueError('bounded controller wait required')
        if self.retired:
            return self.exitcode
        if self.pid is None:
            raise ValueError('no started controller')
        if not self._exit_recorded:
            self.process.join(timeout)
            code = self.process.exitcode
            if code is None:
                raise ValueError('controller still running; handle retained')
            # Native absence, not exitcode alone, authorizes forgetting a row.
            if self.registered:
                self.registry.retire(self.pid)
            self.exitcode = code
            self._exit_recorded = True
        self.close()
        try:
            self.process.close()
        except Exception:
            # Keep the handle and confirmed exit record for cleanup-only retry.
            raise ValueError('controller handle cleanup unavailable') from None
        self.retired = True
        return self.exitcode

    def inspect(self):
        return dict(attempted=self.attempted, registered=self.registered,
                    retired=self.retired, closed=self.closed,
                    fixed_only=self._fixed_only,
                    production_connected=False, production_apply_supported=False)
