from physical_close import close_once, close_after_failure
"""Opt-in private inherited-Pipe lifecycle RPC. No listener, pickle or PID adoption.

Trusted parent creates BOTH pipe endpoints and transfers exactly one via Windows
spawn handle duplication. Key/session travel only as private bootstrap arguments,
never files/env/argv. Possession is a capability, not protection from same-user
debuggers or compromised trusted host code. Native identity is independently pinned.

The caller must pump serve_once periodically. bot_controller_bootstrap supplies a
local broker entry; this module alone neither installs a pump nor grants global
cross-Bot migration authority.
Native inherited message Pipes use cancellable overlapped IO with absolute
deadlines. Trusted host callbacks/probes are NOT covered by that IO deadline.
"""
import hashlib
import hmac
import json
import re
import secrets
import time
from threading import Lock
from bot_parent_launch import FixedProcessFactory, launch_contract

from bot_host_inventory import _identity
from bot_host_registry import HostEvidenceRegistry
from bot_channel_transport import MAX_FRAME, deadline_transport

_SLOT = re.compile(r'[a-z][a-z0-9-]{0,31}\Z')


_COMPANION_PREFLIGHT_MESSAGES = {
    'account-fence': '当前 Bot 的 QQ 账号保护仍在，需核对上次的账号操作。',
    'account-registration': '当前 Bot 的 QQ 账号登记无法核验。',
    'runtime': '运行文件或专用端口自检未通过。',
}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


class FrameCodec:
    """One direction, one session, exact monotonically increasing sequence."""
    def __init__(self, key, session, direction):
        if (type(key) is not bytes or len(key) != 32 or type(session) is not str
                or not re.fullmatch('[0-9a-f]{32}', session)
                or direction not in ('request', 'response')):
            raise ValueError('invalid channel bootstrap')
        self._key, self.session, self.direction = key, session, direction
        self.seq = 0

    def _mac(self, body):
        return hmac.new(self._key, self.direction.encode() + b'\0' + _json(body),
                        hashlib.sha256).hexdigest()

    def encode(self, payload):
        if type(payload) is not dict:
            raise ValueError('invalid channel payload')
        body = dict(session=self.session, seq=self.seq + 1, payload=payload)
        raw = _json(dict(body, mac=self._mac(body)))
        if len(raw) > MAX_FRAME:
            raise ValueError('oversized channel frame')
        self.seq += 1
        return raw

    def decode(self, raw):
        try:
            if type(raw) is not bytes or not 0 < len(raw) <= MAX_FRAME:
                raise ValueError()
            frame = json.loads(raw, object_pairs_hook=_unique)
            if (type(frame) is not dict or set(frame) != {'session', 'seq', 'payload', 'mac'}
                    or frame['session'] != self.session or type(frame['seq']) is not int
                    or frame['seq'] != self.seq + 1 or type(frame['payload']) is not dict
                    or type(frame['mac']) is not str or len(frame['mac']) != 64):
                raise ValueError()
            body = {k: frame[k] for k in ('session', 'seq', 'payload')}
            if not hmac.compare_digest(frame['mac'], self._mac(body)):
                raise ValueError()
            self.seq += 1
            return frame['payload']
        except Exception:
            raise ValueError('invalid lifecycle frame') from None


def private_channel(context):
    """Call before spawning the trusted worker; close parent's child-end copy.

No server address/name is published and no TCP port is bound. Bootstrap is a
trusted host action; this function never establishes complete host coverage.
"""
    parent, child = context.Pipe(duplex=True)
    return parent, child, secrets.token_bytes(32), secrets.token_hex(16)


class LifecycleClient:
    def __init__(self, connection, key, session, *, timeout=2):
        if type(timeout) not in (int, float) or not 0 < timeout <= 5:
            raise ValueError('invalid channel timeout')
        self.connection, self.timeout = deadline_transport(connection), timeout
        self.tx = FrameCodec(key, session, 'request')
        self.rx = FrameCodec(key, session, 'response')
        self.closed = False
        self._requesting = Lock()

    def request(self, op, slot=None, *, contract=None, port=None):
        # The codec sequences and send/receive pair form one transaction.
        # Individual transport locks cannot prevent cross-thread response theft.
        if not self._requesting.acquire(blocking=False):
            self.close()
            raise ValueError('lifecycle client busy; channel revoked')
        try:
            if self.closed:
                raise ValueError('lifecycle channel closed')
            payload = {'op': op} if slot is None else {'op': op, 'slot': slot}
            if contract is not None:
                payload['contract'] = contract
            if port is not None:
                payload['port'] = port
            # Only fixed native birth gets the transport's existing 5s ceiling.
            budget = 5 if op == 'companion-start' else self.timeout
            deadline = time.monotonic() + budget
            self.connection.send_frame(self.tx.encode(payload), deadline)
            result = self.rx.decode(self.connection.recv_frame(deadline))
            confirmed_admission_denial = False
            if (not self.closed and op=='companion-start' and slot is None
                    and type(result.get('ok')) is bool
                    and result == {'ok':False,'denied':'account-admission-conflict'}):
                from bot_parent_account_grant import ACCOUNT_ADMISSION_GUIDANCE, AccountAdmissionConflict
                confirmed_admission_denial = True
                raise AccountAdmissionConflict(ACCOUNT_ADMISSION_GUIDANCE)
            if (not self.closed and op == 'companion-start' and slot is None
                    and result == {'ok': False, 'denied': 'companion-custody-retained'}
                    and result.get('ok') is False):
                from bot_companion_custody import CustodyAdmissionDenied
                confirmed_admission_denial = True
                raise CustodyAdmissionDenied('SnowLuma 上次托管登记仍在，本次未启动；请先核对旧登记。')
            if (not self.closed and op == 'companion-start' and slot is None
                    and set(result) == {'ok', 'denied', 'check'}
                    and result.get('ok') is False and result['denied'] == 'companion-preflight'
                    and isinstance(result['check'], str) and result['check'] in _COMPANION_PREFLIGHT_MESSAGES):
                from bot_launcher_companion import CompanionPreflightDenied
                confirmed_admission_denial = True
                raise CompanionPreflightDenied('SnowLuma 未启动：' + _COMPANION_PREFLIGHT_MESSAGES[result['check']])
            if op in ('status','companion-status','qq-status'):
                shape = set(result) == {'ok', 'state'} and result['state'] in ('alive', 'absent')
            elif op == 'companion-initial-password':
                password = result.get('password')
                shape = set(result) == {'ok', 'password'} and (password is None or (
                    isinstance(password, str) and len(password) == 16
                    and all(c in '0123456789abcdef' for c in password)))
            elif op in ('companion-start','qq-start'):
                shape = (set(result)==({'ok','pid','created','exe','generation'} if op=='companion-start' else {'ok','pid','created','exe'})
                    and (op!='companion-start' or (type(result.get('generation')) is str and len(result['generation'])==32 and all(c in '0123456789abcdef' for c in result['generation'])))
                    and type(result['pid']) is int and 0<result['pid']<=0xffffffff
                    and type(result['created']) is str and result['created'].isascii()
                    and result['created'].isdecimal() and 0<len(result['created'])<=20
                    and type(result['exe']) is str and 0<len(result['exe'])<=32768
                    and not any(ord(c)<32 for c in result['exe']))
            elif op == 'describe':
                fields=set(result)-({'companion_supported'} if result.get('companion_supported') is True else set())
                shape = ((fields == {'ok','core','bot_scope'} or
                          (fields in ({'ok','core','bot_scope','instance_only','bot'},
                                           {'ok','core','bot_scope','instance_only','bot','launch_options'})
                           and result['instance_only'] is True and type(result['bot']) is str
                           and _valid_web_options(result)))
                         and type(result['core']) is str
                         and (result['bot_scope'] is None or type(result['bot_scope']) is str))
            else:
                shape = set(result) == {'ok'}
            valid = type(result) is dict and result.get('ok') is True and shape
            if self.closed or not valid:
                raise ValueError()
            return result
        except BaseException as error:
            from bot_parent_account_grant import AccountAdmissionConflict
            from bot_companion_custody import CustodyAdmissionDenied
            from bot_launcher_companion import CompanionPreflightDenied
            if type(error) in (AccountAdmissionConflict, CustodyAdmissionDenied, CompanionPreflightDenied) and locals().get('confirmed_admission_denial') is True:
                raise  # authenticated fixed pre-birth denial; transport is healthy
            close_after_failure(self, error)
            if isinstance(error, Exception):
                raise ValueError('lifecycle request unavailable') from None
            raise  # cancellation/interrupt is not success or a retry signal
        finally:
            self._requesting.release()

    def close(self):
        close_once(self, self.connection.close)


class LifecycleBroker:
    """One authenticated worker; remote requests name slots, NEVER commands/PIDs.

factories is a trusted HOST allowlist of zero-arg spawn callables; each returns
its newly created Popen-like owned child. Remote payload cannot choose argv, cwd,
Bot, root, config, account or PID. Actual spawn/commit run under registry locks.
Untracked descendants ALWAYS revoke complete coverage, just like startup.main.
Neither disconnect nor close kills/forgets/auto-retires owned processes.
"""
    def __init__(self, registry, bot, controller, connection, key, session, factories,
                 *, timeout=2, lifetime=30, clock=time.monotonic, fixed_only=False, instance_markers=False, instance_only=False, launch_options=None, companion_host=None):
        if (type(instance_only) is not bool or (instance_only and (instance_markers is not True or factories != {} or fixed_only is not True))
                or type(instance_markers) is not bool or type(fixed_only) is not bool
                or (fixed_only and (type(factories) is not dict or any(
                    type(v) is not FixedProcessFactory for v in factories.values())))
                or type(registry) is not HostEvidenceRegistry or bot not in registry.bots
                or type(factories) is not dict or len(factories) > 16
                or any(type(k) is not str or not _SLOT.fullmatch(k) or not callable(v)
                       for k, v in factories.items())
                or type(timeout) not in (int, float) or not 0 < timeout <= 5
                or type(lifetime) not in (int, float) or not 0 < lifetime <= 30):
            raise ValueError('invalid lifecycle broker scope')
        if companion_host is not None:
            from bot_launcher_companion import LauncherCompanionHost
            if (type(companion_host) is not LauncherCompanionHost or not instance_only
                    or companion_host.core!=registry.core.resolve()
                    or companion_host.controller.pid!=controller.get('pid')):
                raise ValueError('exact default companion HOST required')
        self.companion_host=companion_host
        self.registry, self.bot = registry, bot
        self.controller = _identity(controller)
        with registry.capture(registry.core) as view:
            expected = dict(self.controller, bot=bot)
            if expected not in view['processes']:
                raise ValueError('controller not registered by trusted host')
        self.connection, self.timeout, self.clock = deadline_transport(connection), timeout, clock
        self.tx = FrameCodec(key, session, 'response')
        self.rx = FrameCodec(key, session, 'request')
        self.factories = dict(factories)
        self.fixed_only = fixed_only
        from bot_launcher_options import normalize_options
        self.launch_options = normalize_options(launch_options)
        if self.launch_options and not instance_only:raise ValueError('web options require instance-only parent')
        self.instance_only = instance_only
        self._fixed_factories = {k:v for k,v in factories.items()
                                 if type(v) is FixedProcessFactory}
        self._contract_core = str(registry.core.resolve())
        self._contract_scope = (None if bot == registry.baseline else
            str((registry.core.parent/'bots'/bot/'infinity').resolve()))
        self._fixed_contracts = {k:launch_contract(key, session, self._contract_core,
            self._contract_scope, k, v.plan()) for k,v in self._fixed_factories.items()}
        self.deadline = clock() + lifetime
        self.children = {}  # retained even if birth commit or response fails
        self.identities = {}
        self.retired_slots = set()
        self.used_slots = set()
        self.closed = False
        self._serving = Lock()
        self.instance_marker = None
        if instance_markers:
            from bot_owned_instance import OwnedInstanceMarker
            self.instance_marker = OwnedInstanceMarker(registry, bot, self.controller)
        try:
            self._peer()
        except Exception:
            self.close()
            raise ValueError('controller identity unavailable') from None

    def _peer(self):
        if self.closed:
            raise ValueError('lifecycle channel closed')
        if self.registry._alive(self.controller['pid']) != self.controller:
            raise ValueError('controller identity changed')
        if self.companion_host is None and not self.clock() < self.deadline:
            raise ValueError('channel lifetime expired')
        # Native probes/clocks may yield or re-enter trusted host callbacks.
        # Logical close cannot authorize dispatch or a receipt.
        if self.closed:
            raise ValueError('lifecycle channel closed')

    def serve_once(self):
        self._serve(wait=True)

    def tick(self):
        """Explicit owner-driven health/readiness pump, NOT a watchdog thread.

        False means no frame available, NOT an offline/empty host or account.
        No lifetime renewal: the original <=30s session deadline still applies.
        """
        return self._serve(wait=False)

    def _serve(self, *, wait):
        if self.closed:
            raise ValueError('lifecycle channel closed')
        if not self._serving.acquire(blocking=False):
            self.close()
            raise ValueError('lifecycle broker busy')
        try:
            self._peer()
            if not wait:
                ready = getattr(self.connection, 'ready', None)
                if not callable(ready):
                    raise ValueError('nonblocking readiness transport required')
                available = ready()
                if type(available) is not bool:
                    raise ValueError('invalid readiness evidence')
                if not available:
                    self._peer()  # recheck after potentially reentrant probe
                    return False
                self._peer()
            response = {'ok': True}
            operation = 'unparsed'
            io_deadline = (min(self.deadline, self.clock() + self.timeout) if self.companion_host is None
                           else self.clock() + self.timeout)
            request = self.rx.decode(self.connection.recv_frame(io_deadline))
            operation = request.get('op') if request.get('op') in ('companion-start','companion-stop','companion-status','companion-retire','companion-initial-password','qq-start','qq-stop','qq-status','account-check','account-revoke','account-work-begin','account-work-finished','account-work-drained','account-configured','ping','describe','publish-instance') else 'unknown'
            self._peer()
            companion_op = self.companion_host is not None and request.get('op') in (
                'companion-start','companion-status','companion-stop','companion-retire','companion-initial-password','account-check','account-revoke','account-work-begin','account-work-drained','account-work-finished','account-configured','qq-start','qq-status','qq-stop')
            if self.clock()>=self.deadline and not companion_op:
                raise ValueError('instance publication lease expired')
            if self.instance_only and not companion_op and request.get('op') not in ('ping','describe','publish-instance'):
                raise ValueError('instance-only worker has no account/process capability')
            if companion_op:
                if request['op'].startswith('companion-'):
                    first=request['op']=='companion-start' and set(request)=={'op'}
                    bound=(set(request)=={'op','slot'} and type(request['slot']) is str
                        and len(request['slot'])==32 and all(c in '0123456789abcdef' for c in request['slot']))
                    if not first and not bound:raise ValueError('fixed Snow generation frame required')
                    if request['op']=='companion-start':
                        io_deadline = self.clock() + 5
                    from bot_parent_account_grant import AccountAdmissionConflict
                    from bot_companion_custody import CustodyAdmissionDenied
                    from bot_launcher_companion import CompanionPreflightDenied
                    try:
                        response=self.companion_host.dispatch(request['op'],generation=request.get('slot'))
                    except CompanionPreflightDenied as error:
                        host = self.companion_host
                        check = str(error)
                        if not (first and not host.attempted and host.owner is None and host.custody is None
                                and check in _COMPANION_PREFLIGHT_MESSAGES):
                            raise
                        response = {'ok': False, 'denied': 'companion-preflight', 'check': check}
                    except AccountAdmissionConflict:
                        host=self.companion_host
                        if not (first and not host.attempted and host.owner is None and host.custody is None):raise
                        response={'ok':False,'denied':'account-admission-conflict'}
                    except CustodyAdmissionDenied:
                        if not (first and self.companion_host._rejected_before_birth()):
                            raise
                        response={'ok':False,'denied':'companion-custody-retained'}
                else:
                    if set(request)!={'op'}:raise ValueError('fixed companion frame has no parameters')
                    response=self.companion_host.dispatch(request['op'])
            elif request == {'op': 'ping'}:
                pass
            elif request == {'op': 'describe'}:
                response.update(core=str(self.registry.core.resolve()), bot_scope=(None
                    if self.bot == self.registry.baseline else
                    str((self.registry.core.parent/'bots'/self.bot/'infinity').resolve())))
                if self.instance_only:
                    response.update(instance_only=True,bot=self.bot)
                    if self.companion_host is not None:response['companion_supported']=True
                    if self.launch_options:response['launch_options']=dict(self.launch_options)
            elif (set(request) == {'op','port'} and request['op'] == 'publish-instance'
                    and self.instance_marker is not None):
                self.instance_marker.publish(request['port'])
            elif request == {'op': 'prepare-recovery-change'}:
                self.registry.prepare_recovery_change(self.bot)
            elif request == {'op': 'prepare-account-change'}:
                # Scope is derived exclusively from the trusted HOST binding.
                relative = ('core/extensions/core-infinity-qq/config.json'
                    if self.bot == self.registry.baseline else
                    'bots/'+self.bot+'/infinity/qq/config.json')
                self.registry.prepare_account_change(relative)
            elif (set(request) in ({'op', 'slot'}, {'op', 'slot', 'contract'})
                  and type(request['slot']) is str
                  and request['slot'] in self.factories):
                slot = request['slot']
                if request['op'] == 'start-fixed':
                    factory = self.factories[slot]
                    if (set(request) != {'op','slot','contract'}
                            or type(request['contract']) is not str
                            or not re.fullmatch('[0-9a-f]{64}', request['contract'])
                            or factory is not self._fixed_factories.get(slot)
                            or not hmac.compare_digest(request['contract'], self._fixed_contracts[slot])
                            or not hmac.compare_digest(request['contract'], launch_contract(
                                self.tx._key, self.tx.session, self._contract_core,
                                self._contract_scope, slot, factory.plan()))):
                        raise ValueError('fixed parent launch contract mismatch')
                elif set(request) != {'op','slot'}:
                    raise ValueError('invalid lifecycle operation fields')
                if request['op'] in ('start', 'start-fixed') and slot not in self.used_slots:
                    if request['op'] == 'start' and (self.fixed_only or
                            slot in self._fixed_factories or type(self.factories[slot]) is FixedProcessFactory):
                        raise ValueError('fixed launch contract required')
                    self.used_slots.add(slot)
                    with self.registry.start_window(self.bot) as birth:
                        child = self.factories[slot]()
                        self.children[slot] = child
                        self._peer()
                        birth.commit(child.pid, untracked_descendants=True)
                        self.identities[slot] = {k: self.registry._processes[child.pid][k]
                                                for k in ('pid', 'created_filetime', 'image')}
                elif request['op'] == 'status' and slot in self.retired_slots:
                    response['state'] = 'absent'
                elif request['op'] in ('status', 'stop') and slot in self.identities:
                    with self.registry._write(allow_fenced=True):
                        expected = self.identities[slot]
                        seen = self.registry.probe(expected['pid'])
                        if (not isinstance(seen, dict) or type(seen.get('pid')) is not int
                                or seen['pid'] != expected['pid']):
                            raise ValueError('owned child identity unavailable')
                        state = seen.get('state')
                        if state == 'alive':
                            observed = _identity({k: seen.get(k) for k in expected})
                            if observed != expected:
                                raise ValueError('owned child identity changed')
                            if request['op'] == 'stop':
                                if self.registry._alive(expected['pid']) != expected:
                                    raise ValueError('owned child identity changed')
                                # Only the retained HOST-created process handle.
                                self.children[slot].terminate()
                        elif state != 'absent':
                            raise ValueError('owned child state unknown')
                        if request['op'] == 'status': response['state'] = state
                elif request['op'] == 'retire' and slot in self.children:
                    # Popen returncode or remote claims confer no exit authority.
                    factory = self._fixed_factories.get(slot)
                    if factory is not None:
                        factory.retire_resources()  # exact independent owner, if configured
                    self.registry.retire(self.children[slot].pid)
                    del self.children[slot]
                    self.identities.pop(slot, None)
                    self.retired_slots.add(slot)
                else:
                    raise ValueError('invalid lifecycle transition')
            else:
                raise ValueError('invalid lifecycle operation')
            self._peer()
            self.connection.send_frame(self.tx.encode(response), io_deadline)
            return True
        except BaseException as error:
            # No payload, paths, credentials or raw exception text in diagnostics.
            self.last_failure = {'operation': locals().get('operation', 'unparsed'),
                                 'kind': type(error).__name__}
            from launcher_exit_log import exit_event
            exit_event('lifecycle_revoked', operation=self.last_failure['operation'],
                       failure_kind=self.last_failure['kind'])
            close_after_failure(self, error)
            if isinstance(error, Exception):
                raise ValueError('lifecycle channel revoked') from None
            raise  # interruption is not an ordinary retryable RPC failure
        finally:
            self._serving.release()

    def close(self):
        self.registry.revoke_coverage()
        close_once(self, self.connection.close)

    def inspect(self):
        return {'closed': self.closed, 'physical_close_complete': getattr(self, 'close_complete', False),
                'physical_close_failed': getattr(self, 'close_error', None) is not None,
                'owned_children': len(self.children),
                'production_connected': False, 'production_apply_supported': False}


def _valid_web_options(description):
    if 'launch_options' not in description:return True
    from bot_launcher_options import normalize_options
    value=description['launch_options']
    return type(value) is dict and bool(value) and normalize_options(value)==value
