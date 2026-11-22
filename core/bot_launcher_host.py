"""Finite default-launcher host session, not a global host/account authority.

Trusted Python can bypass application calls; this is not a hostile-Python
sandbox. Local retirement is only of the retained birth job and controller.
"""
from pathlib import Path
import time
import hashlib
from threading import Lock
from bot_birth_process import BirthPopen, PROFILE
from bot_launcher_options import normalize_options, options_argv
from bot_registry import bot_id

# Public CLI/programmatic launch route through startup's two canonical modes.
ENTRIES = ('startup.web', 'startup.console')
ROUTES = ('startup.main', 'launch.cli', 'launch.main')


class LauncherHostFailure(OSError):
    def __init__(self, session, operation):
        self.session = session  # retain exact job/process after uncertain cleanup
        super().__init__('owned launcher host '+operation+' unconfirmed')


class LauncherHost:
    def __init__(self, entry, command, core, bot, *, launch_options=None):
        self.process = self.job = None
        self.state = 'planned'
        self._busy = Lock()
        self.profile = PROFILE
        self.entry = entry
        self.core = Path(core).resolve()
        self.bot = bot_id(bot)
        opts = normalize_options(launch_options)
        if entry not in ENTRIES or (opts.get('console') is True) != (entry == 'startup.console'):
            raise ValueError('unsupported launcher entry')
        if type(command) is not list or not all(type(v) is str for v in command):
            raise ValueError('fixed launcher command required')
        # Never admit -c/-m/arbitrary scripts as a default Bot controller.
        offset = 5 if entry == 'startup.console' else 4
        if (len(command) != offset+6+len(options_argv(opts))
                or not Path(command[0]).is_absolute() or command[1] != '-u'
                or Path(command[2]).absolute() != self.core/'bot_launcher_child.py'
                or command[offset:offset+5] != ['--root',str(self.core/'data'),'--bot',self.bot,'--port']
                or command[offset+6:] != options_argv(opts)
                or not command[offset+5].isascii() or not command[offset+5].isdecimal()
                or not 0 < int(command[offset+5]) <= 65535):
            raise ValueError('unsupported launcher command')
        labels=[]
        for label,prefix in [(command[3],'--parent-lifecycle-handle=')] + ([(command[4],'--parent-control-handle=')] if offset==5 else []):
            if not label.startswith(prefix) or not label[len(prefix):].isascii() or not label[len(prefix):].isdecimal() or int(label[len(prefix):]) <= 0:
                raise ValueError('private launcher endpoint required')
            labels.append(int(label[len(prefix):]))
        if len(set(labels)) != len(labels):raise ValueError('separate private launcher endpoints required')
        self._labels=tuple(labels)
        self._entry_file = self.core/'bot_launcher_child.py'
        authority = Path(__file__).resolve().parent/'bot_launcher_child.py'
        self._entry_hash = hashlib.sha256(authority.read_bytes()).digest()
        self._check_entry()
        self._command = tuple(command)
        self._job_handle = self._process_handle = None
        self._close_attempted = False
        self._stdout = self._output = None
        self._parent = None
        self._launch_options = opts

    def _check_entry(self):
        if (self._entry_file.is_symlink() or self._entry_file.resolve()!=self._entry_file
                or hashlib.sha256(self._entry_file.read_bytes()).digest()!=self._entry_hash):
            raise ValueError('unsupported or changed launcher entry source')

    def bind(self, process):
        if not self._busy.acquire(blocking=False):raise LauncherHostFailure(self,'binding busy')
        try:
            if self.state != 'planned':raise LauncherHostFailure(self,'single-use binding')
            self.state = 'binding'
            if (type(process) is not BirthPopen or process._birth_command != self._command
                    or process._birth_cwd != self.core or process.birth_job is not process._birth_job
                    or process._handle is not process._birth_handle
                    or process._birth_stdin_mode != ('inherited' if self.entry=='startup.console' else 'pipe')
                    or set(process._birth_inherited) != set(process._birth_stdio)|set(self._labels)):
                self.state = 'pending'
                raise LauncherHostFailure(self,'birth provenance')
            # Capture exact resources before the fallible native check.
            self.process,self.job = process,process.birth_job
            self._stdout = process.stdout
            self._job_handle,self._process_handle = self.job.handle,process._handle
            try:
                self._check_entry()
                if self.job.process is not process or process.poll() is not None or not self.job.verify():
                    raise ValueError('controller not live in its birth job')
            except BaseException:
                self.state='pending'
                raise LauncherHostFailure(self,'birth binding') from None
            self.state='admitted'
            return self
        finally:self._busy.release()

    def bind_parent(self, parent):
        from bot_launcher_lifecycle import ParentLauncherChannel
        if not self._busy.acquire(blocking=False):raise LauncherHostFailure(self,'parent binding busy')
        try:
            if (self.state!='planned' or self._parent is not None
                    or type(parent) is not ParentLauncherChannel or not parent.instance_only
                    or parent.closed or parent.attempted or parent.child is None
                    or parent.child.fileno()!=self._labels[0] or parent.bot!=self.bot
                    or parent.registry.core.resolve()!=self.core
                    or parent.launch_options!=self._launch_options):
                raise LauncherHostFailure(self,'original pre-birth parent binding')
            self._parent=parent
            return parent
        finally:self._busy.release()

    def assert_lifecycle_retired(self):
        self.assert_local_retired()
        parent=self._parent
        if parent is None or parent.process is not self.process:
            raise LauncherHostFailure(self,'original lifecycle parent retirement')
        try:parent.assert_local_retired()
        except BaseException:raise LauncherHostFailure(self,'local account/companion retirement') from None
        return True  # finite original chain, NEVER global or external authority

    def bind_output(self, output):
        from console_output import ConsoleOutput
        if not self._busy.acquire(blocking=False):raise LauncherHostFailure(self,'output binding busy')
        try:
            if (self.state!='admitted' or self._output is not None
                    or type(output) is not ConsoleOutput or output.process is not self.process
                    or self.process.stdout is not self._stdout or self._stdout is None
                    or self._stdout.closed or output.thread is not None):
                raise LauncherHostFailure(self,'original pre-reader output binding')
            self._output=output
            return output
        finally:self._busy.release()

    def assert_local_retired(self):
        # Native job drain alone does NOT prove output retirement. This finite
        # predicate retains the original birth and reader; it grants no rename,
        # global coverage or external account authority, even when successful.
        if not self._busy.acquire(blocking=False):raise LauncherHostFailure(self,'retirement busy')
        try:
            if (self.state!='retired' or self.process is None or self._output is None
                    or self.process._handle is not self._process_handle
                    or self.process.birth_job is not self.job or self.job.process is not self.process
                    or self.job.handle is not None or self.process.poll() is None):
                raise LauncherHostFailure(self,'native/output retirement')
            try:self._output.assert_drained(self.process,self._stdout)
            except BaseException:raise LauncherHostFailure(self,'output retirement') from None
            return True
        finally:self._busy.release()

    def inspect(self):
        # No paths/account identity/endpoint keys and no complete-host claim.
        return dict(profile=self.profile,entry=self.entry,state=self.state,
                    controller_retired=self.state=='retired',
                    retained_job=self.job is not None and self.job.handle is not None,
                    complete=False,untracked_descendants=True)

    def close(self, *, timeout=5):
        if type(timeout) not in (int,float) or not 0<timeout<=5:raise ValueError('bounded host drain required')
        if not self._busy.acquire(blocking=False):raise LauncherHostFailure(self,'drain busy')
        try:
            if self.state=='retired':return True
            if self.state!='admitted' or self._close_attempted:
                raise LauncherHostFailure(self,'pending drain; automatic retry denied')
            self._close_attempted=True;self.state='draining'
            try:
                if (self.process._handle is not self._process_handle or self.job.handle != self._job_handle
                        or self.process.birth_job is not self.job or self.job.process is not self.process):
                    raise ValueError('retained host binding changed')
                import ctypes
                from ctypes import wintypes
                kernel=self.job.kernel
                kernel.TerminateJobObject.argtypes=[wintypes.HANDLE,wintypes.UINT]
                kernel.TerminateJobObject.restype=wintypes.BOOL
                deadline=time.monotonic()+timeout
                # Query BEFORE close: CloseHandle alone cannot prove subtree absence.
                if self.job.inspect()['active_processes']:
                    from bot_global_admission import note_forced_cleanup
                    note_forced_cleanup(self.job)
                    if not kernel.TerminateJobObject(self._job_handle,1):raise ValueError('job termination uncertain')
                while self.job.inspect()['active_processes'] != 0:
                    if time.monotonic()>=deadline:raise TimeoutError('job descendants unresolved')
                    time.sleep(.005)
                self.process.wait(timeout=max(.001,deadline-time.monotonic()))
                self.job.close()  # only after acknowledged empty accounting + root wait
            except BaseException:
                self.state='pending'
                raise LauncherHostFailure(self,'drain') from None
            self.state='retired'
            return True
        finally:self._busy.release()
