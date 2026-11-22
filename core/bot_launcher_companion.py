"""Default parent's one fixed companion capability; never account/host authority.

Only the exact outer HOST creates and retains the native owner. No public
listener, PID adoption, client-supplied command/environment/configuration. HOST
retains the native owner through controller exit and drains its owned companion;
no reconnect, external-process adoption or crash-survival claim.
"""
from pathlib import Path
import hashlib
import json
import shutil
import time
import uuid

from bot_birth_process import BirthPopen
from bot_independent_owner import IndependentProcessOwner, IndependentOwnerFailure
from bot_host_observer import probe_process

OPS = frozenset(('companion-start','companion-status','companion-stop','companion-retire','companion-initial-password'))


class CompanionPreflightDenied(ValueError):
    """The first managed birth was refused before acquiring custody."""


class LauncherCompanionHost:
    def __init__(self, core, controller, *, account_bot=None, account_baseline=True):
        if type(controller) is not BirthPopen:
            raise ValueError('default retained native controller required')
        self.core = Path(core).resolve()
        self.controller = controller
        self._instance_bot=account_bot
        self._snow_instance=None
        self.first_account = None
        self.account_fence=None
        self.account_grant=None
        if account_bot is not None:
            from bot_default_account_fence import DefaultAccountFence
            self.account_fence=DefaultAccountFence(self.core,account_bot)
            from bot_parent_account_grant import ParentAccountGrant
            self.account_grant=ParentAccountGrant(self.core,account_bot,baseline=account_baseline)
        self.owner = None
        self.custody = None
        self.attempted = False
        self._prebirth_rejection = None
        self._identity = None
        self._plan_receipt = None
        from bot_parent_qq_launch import QQLaunchHost
        self.qq=QQLaunchHost(self)
        self._original_qq=self.qq
        self.generation=None
        self.retired_owners=[]
        self._local_retirement=None
        self._terminal_invalid=False
        self.initial_configuration=None
        self.known_configuration=None
        self.shutdown_failure=None
        self._generation_binding=None

    def _plan(self):
        # Fixed HOST policy, not a WebUI, argv, env or account-config input.
        from runtime_identity import safe_path
        from bot_snow_environment import snow_environment
        from service_layout import snowluma_directory
        entry = safe_path(self.core.parent, snowluma_directory(self.core)/'index.mjs')
        from service_layout import node_executable
        node = node_executable(self.core)
        if not entry.is_file() or node is None:
            raise ValueError('fixed SnowLuma/Node runtime unavailable')
        executable = Path(node).absolute()
        if executable.is_symlink() or executable.resolve()!=executable or not executable.is_file():
            raise ValueError('fixed Node runtime identity unavailable')
        shutdown_module=safe_path(self.core.parent,entry.parent/'telyin-owned-shutdown.mjs')
        if not shutdown_module.is_file():raise ValueError('cooperative SnowLuma runtime required')
        self._shutdown_module=(shutdown_module,hashlib.sha256(shutdown_module.read_bytes()).digest())
        self._plan_receipt = (entry,hashlib.sha256(entry.read_bytes()).digest(),
                              executable,hashlib.sha256(executable.read_bytes()).digest())
        env = snow_environment()
        cwd=entry.parent
        if self._instance_bot is not None:
            from snowluma_instance import load_instance, assert_spawn_available
            self._snow_instance=load_instance(self.core,self._instance_bot)
            if self._snow_instance is None:raise ValueError('private SnowLuma runtime required')
            assert_spawn_available(self._snow_instance)
            cwd=self._snow_instance.root
            env.update(self._snow_instance.environment())
            if self.account_grant is None:raise ValueError('fixed own account store required')
            env['SNOWLUMA_TARGET_CONFIG']=str(self.account_grant.store)
            from bot_snow_first_account import FirstSnowAccount
            self.first_account = FirstSnowAccount(self, self._snow_instance)
            env.update(self.first_account.environment())
        from snowluma_status_policy import protect_status_command
        protect_status_command(cwd)
        return dict(argv=[str(executable),str(entry)],cwd=str(cwd),env=env)

    def _check_plan(self):
        if getattr(self,'_shutdown_module',None) is not None:
            path,digest=self._shutdown_module
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).digest()!=digest:
                raise ValueError('cooperative runtime source changed before birth')
        if self._snow_instance is not None:
            from snowluma_instance import assert_spawn_available
            assert_spawn_available(self._snow_instance)
        if self._plan_receipt is None:return
        entry,source,node,image = self._plan_receipt
        from runtime_identity import safe_path
        safe_path(self.core.parent,entry)
        if (not entry.is_file() or node.is_symlink() or node.resolve()!=node
                or hashlib.sha256(entry.read_bytes()).digest()!=source
                or hashlib.sha256(node.read_bytes()).digest()!=image):
            raise ValueError('fixed companion image changed before birth')

    def dispatch(self, op, *, generation=None):
        from bot_parent_qq_launch import OPS as QQ_OPS
        if op in QQ_OPS:return self.qq.dispatch(op)
        if op in ('account-work-begin','account-work-drained','account-work-finished'):
            if self.account_grant is None or self.account_fence is None:
                raise ValueError('fixed HOST account work scope unavailable')
            if op=='account-work-begin':
                self.account_grant.begin_work(self.controller,self.owner,self.account_fence)
            elif op=='account-work-finished':
                self.qq.assert_retained()
                self.account_grant.check(self.controller,self.owner,self.account_fence)
                self.account_grant.drain_work(retained=True)
            else:
                self.qq.assert_drained()
                self.account_grant.drain_work()
            return dict(ok=True)
        if op=='account-configured':
            if self.initial_configuration is not None:self.initial_configuration.commit()
            if self.known_configuration is not None:self.known_configuration.commit()
            return dict(ok=True)
        if op in ('account-check','account-revoke'):
            if self.account_fence is None:raise ValueError('HOST account scope unavailable')
            if op=='account-check':
                self.account_fence.check()
                if self.account_grant is not None:
                    self.account_grant.check(self.controller,self.owner,self.account_fence)
            else:
                from bot_initial_account_configuration import InitialAccountConfiguration
                # Ordinary revoke always works. Only provably unused first settings
                # can later retire its own fresh gate; used scopes remain denied.
                from bot_known_account_configuration import KnownAccountConfiguration
                self.known_configuration=None
                try:self.known_configuration=KnownAccountConfiguration(self)
                except (ValueError,OSError):pass
                previous=self.initial_configuration
                self.initial_configuration=None
                try:self.initial_configuration=InitialAccountConfiguration(self,previous=previous)
                except ValueError:pass
                from bot_account_save_recovery import save_evidence
                self.account_fence.revoke(save_evidence=(lambda: save_evidence(self.account_grant))
                                          if self.account_grant is not None else None)
                if self.initial_configuration is not None:self.initial_configuration.bind_fresh_fence()
                if self.known_configuration is not None:self.known_configuration.bind_fresh_fence()
            return dict(ok=True)
        if op not in OPS:raise ValueError('unknown fixed companion operation')
        if generation is not None and generation!=self.generation:
            raise ValueError('stale Snow generation; no process touched')
        if op == 'companion-start':
            previous=self.owner
            if self.attempted and self.account_fence is not None:
                self.account_fence.check()
            if self.attempted:
                if generation is None or self._identity is None or previous is None:
                    raise ValueError('companion start is single use without retired generation')
                if len(self.retired_owners)>=16:raise ValueError('bounded Snow generation limit reached')
                previous.assert_releasable()
                if self.custody is None or not self.custody.released:raise ValueError('original custody not retired')
                if self.qq.attempted:self.qq.assert_retained()
                if self.account_grant is not None:
                    self.account_grant.check_process_rebind(previous,self.account_fence)
                self.retired_owners.append((self.generation,previous,self.custody))
            elif generation is not None:raise ValueError('no previous Snow generation')
            plan = None
            if self._instance_bot is not None and not self.attempted:
                from bot_parent_account_grant import AccountAdmissionConflict
                from launcher_exit_log import exit_event
                stage = 'account-fence'
                try:
                    from bot_account_save_recovery import recover_interrupted_save
                    if recover_interrupted_save(self.account_fence, self.account_grant):
                        exit_event('snowluma_interrupted_save_archived', bot=self._instance_bot)
                    self.account_fence.check()
                    stage = 'account-registration'
                    self.account_grant.preflight_new_host()
                    stage = 'runtime'
                    plan = self._plan()
                    self._check_plan()
                except AccountAdmissionConflict:
                    from qq_startup_diagnostics import failure_counts
                    exit_event('snowluma_preflight_denied', bot=self._instance_bot, check=stage,
                               admission_counts=failure_counts(self.core, self._instance_bot))
                    raise
                except (OSError, ValueError) as error:
                    exit_event('snowluma_preflight_denied', bot=self._instance_bot, check=stage, error=error)
                    raise CompanionPreflightDenied(stage) from error
            self.attempted = True
            self.generation=uuid.uuid4().hex
            self._identity=None
            from bot_companion_custody import CompanionCustody, CustodyAdmissionDenied
            try:
                custody=(CompanionCustody(self.core) if self._instance_bot is None else CompanionCustody(self.core, bot=self._instance_bot))  # durable denial BEFORE native birth
            except CustodyAdmissionDenied as error:
                if previous is None and self.owner is None and self.custody is None:
                    self._prebirth_rejection=(error,self.controller,self.generation)
                raise
            self.custody=custody
            if plan is None:
                plan=self._plan()
            self.owner=IndependentProcessOwner(self.controller,**plan,cooperative=True,
                **({} if custody.job_name is None else {'job_name': custody.job_name}))
            from launcher_exit_log import exit_event
            exit_event('snowluma_custody_created', bot=self._instance_bot)
            self.custody.bind(self.owner)
            self._generation_binding=(self.generation,self.owner,self.custody,None,None)
            self._check_plan()
            p=self.owner.start()
            row=probe_process(p.pid)
            if (row.get('state')!='alive' or row.get('pid')!=p.pid
                    or type(row.get('created_filetime')) is not int
                    or row.get('image')!=str(Path(plan['argv'][0]).resolve())
                    or p.poll() is not None):
                raise IndependentOwnerFailure(self.owner,'private birth receipt')
            self._identity=dict(pid=p.pid,created=str(row['created_filetime']),exe=row['image'])
            self._generation_binding=(self.generation,self.owner,self.custody,self._identity,dict(self._identity))
            exit_event('snowluma_birth_verified', bot=self._instance_bot, companion_pid=p.pid)
            if previous is not None and self.account_grant is not None:
                self.account_grant.rebind_process(self.controller,previous,self.owner,self.account_fence)
            return dict(ok=True,generation=self.generation,**self._identity)
        if self.owner is None or self._identity is None:
            raise ValueError('companion has no acknowledged birth')
        p=self.owner.process
        if p is None or p._handle is not p._birth_handle:
            raise IndependentOwnerFailure(self.owner,'retained process')
        if op=='companion-initial-password':
            from runtime_identity import safe_path
            password = None
            if self._snow_instance is not None and self.owner.state == 'owned' and p.poll() is None:
                self.owner.job.verify()
                try:
                    config = safe_path(self.core.parent, self._snow_instance.root / 'config/webui.json')
                    with config.open('rb') as stream:
                        state = json.loads(stream.read(65537))
                    if state.get('mustChangePassword') is True:
                        password = self.owner._initial_webui_password
                    else:
                        self.owner._initial_webui_password = None
                except (OSError, ValueError, AttributeError):
                    pass
            return dict(ok=True, password=password)
        if op=='companion-status':
            if self.owner.state=='pending':raise IndependentOwnerFailure(self.owner,'pending')
            if self.owner.state!='retired':self.owner.job.verify()
            return dict(ok=True,state='absent' if p.poll() is not None else 'alive')
        if op=='companion-retire' and p.poll() is None:
            raise ValueError('live companion cannot be retired')
        self._drain_original()
        self.custody.retire()
        return dict(ok=True)

    def _drain_original(self, *, terminal=False):
        bound=self._generation_binding
        original_custody=bound[2] if type(bound) is tuple and len(bound)==5 else None
        try:
            if (type(bound) is not tuple or len(bound)!=5 or self.qq is not self._original_qq
                    or bound[0]!=self.generation or bound[1] is not self.owner
                    or bound[2] is not self.custody or bound[3] is not self._identity
                    or (self._identity is not None and self._identity!=bound[4])):
                raise ValueError('original companion generation binding changed')
            if self.owner.cooperative and self.owner._drain_attempted:
                result=self.owner.observe_cooperative(timeout=5)
            else:
                try:
                    result=self.owner.drain()
                except IndependentOwnerFailure as error:
                    if not (terminal and self.owner.cooperative and self.owner._stop_requested
                            and type(error.__cause__) is TimeoutError):
                        raise
                    result=self._await_terminal_receipt(error)
            # Late physical retirement is not a fresh successful stop. Keep the
            # original HOST failure and its custody instead of returning ok.
            if self.shutdown_failure is not None:raise self.shutdown_failure
            return result
        except BaseException as error:
            if self.shutdown_failure is None:self.shutdown_failure=error
            if original_custody is not None:
                try:original_custody.record_failure(error)
                except BaseException as secondary:BaseException.add_note(error,'failure audit persistence failed: '+type(secondary).__name__)
            raise

    def _await_terminal_receipt(self, timeout_error):
        from launcher_exit_log import exit_event
        owner=self.owner
        if self.controller.poll() is None or self.shutdown_failure is not None:
            raise timeout_error
        print('  [shutdown] SnowLuma 仍在退出，等待原进程回执…',flush=True)
        while True:
            try:
                result=owner.observe_cooperative(timeout=5)
            except IndependentOwnerFailure as error:
                if type(error.__cause__) is not TimeoutError:
                    raise
            else:
                exit_event('snowluma_late_stop_receipt_accepted')
                return result

    def _rejected_before_birth(self):
        from bot_companion_custody import CustodyAdmissionDenied
        r=self._prebirth_rejection
        return (type(r) is tuple and len(r)==3 and type(r[0]) is CustodyAdmissionDenied
                and r[1] is self.controller and r[2]==self.generation
                and self.owner is None and self.custody is None and self._identity is None)

    def _retire_local_account(self):
        if self.account_grant is not None:
            self.account_grant.retire_local(self.controller,self.owner,self.qq)
        if self.controller.poll() is not None:
            self._local_retirement=(self.controller,self.owner,self.custody,self.qq,
                self.account_grant,tuple(self.retired_owners),self._prebirth_rejection)
            return self.assert_local_retired()
        return True  # historical no-account wait; NOT terminal local evidence

    def assert_local_retired(self):
        if self._terminal_invalid:raise ValueError('original HOST terminal binding revoked')
        try:
            b=self._local_retirement
            if (b is None or self.controller.poll() is None
                    or self.controller._handle is not self.controller._birth_handle
                    or any(a is not v for a,v in zip(b[:5],(self.controller,self.owner,self.custody,self.qq,self.account_grant)))
                    or tuple(self.retired_owners)!=b[5]
                    or b[6] is not self._prebirth_rejection
                    or (self.attempted and self.owner is None and not self._rejected_before_birth())):
                raise ValueError('original HOST/native/account terminal binding changed')
            pairs=[(o,c) for _,o,c in self.retired_owners]
            if self.owner is not None:pairs.append((self.owner,self.custody))
            elif self.custody is not None:raise ValueError('unbound custody remains')
            from runtime_identity import safe_path
            for owner,custody in pairs:
                owner.assert_releasable()
                if (custody is None or custody.owner is not owner or not custody.released
                        or safe_path(self.core.parent,custody.path).exists()):
                    raise ValueError('original companion custody retirement changed')
            self.qq.assert_drained()
            if self.account_grant is not None:
                self.account_grant.assert_local_retired(self.controller,self.owner,self.qq)
            return True
        except BaseException:
            self._terminal_invalid=True
            raise

    def wait_persistent(self):
        """Compatibility name: close this Bot's OWN resources after Core exits.

        Request cooperative shutdown once; a failed attempt retains this HOST
        until its original Job is physically safe, without extending success.
        A closed RPC channel alone is NOT shutdown authority: the original
        controller must have exited before draining a live owned resource.
        HOST crash/OS hard close still cannot promise Python cleanup execution.
        """
        from launcher_exit_log import exit_event
        exit_event('companion_cleanup_begin', companion_owned=self.owner is not None)
        binding=self._generation_binding
        original_owner=binding[1] if type(binding) is tuple and len(binding)==5 else self.owner
        owners=(original_owner,getattr(self.qq,'owner',None))
        if any(o is not None and o.state=='owned' for o in owners):
            if (self.controller._handle is not self.controller._birth_handle
                    or self.controller.poll() is None):
                raise ValueError('original controller must exit before companion shutdown')
        errors=[]
        # A QQ cleanup error must not leave an independently owned hidden Snow
        # running forever. Attempt both exact original resources, retaining all
        # failures; do not retire account admission unless BOTH checks succeed.
        try:
            if self.qq is not self._original_qq:raise ValueError('original QQ HOST object changed; no callback invoked')
            self.qq.close_after_controller()
            exit_event('qq_cleanup_finished')
        except BaseException as error:
            exit_event('qq_cleanup_failed', error=error)
            errors.append(error)
        try:
            if self.owner is None:
                if self.custody is not None or (self.attempted and not self._rejected_before_birth()):
                    raise ValueError('companion custody/birth remains pending')
            else:
                if self.owner is not original_owner:raise ValueError('original companion owner changed; no callback invoked')
                if self.owner.state not in ('owned','retired'):
                    raise IndependentOwnerFailure(self.owner,'pending HOST lifetime')
                exit_event('snowluma_drain_begin')
                self._drain_original(terminal=True)
                exit_event('snowluma_drain_finished')
                self.custody.retire()
                exit_event('snowluma_custody_retired')
                self.owner.assert_releasable()
        except BaseException as error:
            exit_event('snowluma_cleanup_failed', error=error)
            errors.append(error)
        if errors:
            chosen=next((e for e in errors if not isinstance(e,Exception)),errors[0])
            for error in errors:
                if error is not chosen:
                    chosen.add_note('additional owned-resource shutdown failure: '+type(error).__name__)
            # Exiting the original HOST with an active KILL_ON_JOB_CLOSE job
            # would itself kill the bridge. Retain HOST and original resources.
            # The original timeout/failure remains latched; this is NOT a longer
            # success budget. Public 30s supervision reports PENDING meanwhile.
            if original_owner is not None and original_owner.cooperative:
                chosen._pending_original_owner=original_owner
                print('  [shutdown] 协作退出未完成；原HOST保留资源，不强杀、不重发。',flush=True)
                while True:
                    try:
                        if original_owner.safe_host_exit():break
                        time.sleep(.1)
                        if original_owner._stop_requested:original_owner.observe_cooperative(timeout=.1)
                    except BaseException as retained:
                        if not isinstance(retained,Exception):BaseException.add_note(chosen,'HOST retention interrupted; resource ownership preserved')
                # Preserve the failed outcome even if a late receipt releases custody.
                if original_owner.state == 'retired' and self.custody is not None:
                    try:
                        self.custody.retire()
                        exit_event('snowluma_custody_retired_after_failure')
                    except Exception as error:
                        chosen.add_note('late custody retirement failed: ' + type(error).__name__)
            raise chosen
        try:
            result = self._retire_local_account()
        except BaseException as error:
            exit_event('account_retirement_failed', error=error)
            raise
        exit_event('account_retirement_finished')
        return result


class CompanionProcess:
    def __init__(self, publisher, receipt):
        self.publisher=publisher
        self.pid=receipt['pid']
        self.identity={k:receipt[k] for k in ('pid','created','exe')}
        self.retired=False
        self.generation=receipt['generation']

    def poll(self):
        if self.retired:return 0
        reply=self.publisher.request_companion('companion-status',generation=self.generation)
        return None if reply['state']=='alive' else 0

    def terminate(self):
        if not self.retired:
            self.publisher.request_companion('companion-stop',generation=self.generation)
            self.retired=True  # native subtree/output close ACK, not account offline

    def wait(self, timeout):
        import subprocess
        if type(timeout) not in (int,float) or not 0<timeout<=10:raise ValueError('bounded companion wait')
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if self.poll() is not None:
                if not self.retired:
                    self.publisher.request_companion('companion-retire',generation=self.generation);self.retired=True
                return 0
            time.sleep(.02)
        raise subprocess.TimeoutExpired('owned companion',timeout)
