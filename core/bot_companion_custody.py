"""Durable fail-closed custody for one fixed default companion installation.

Private Windows instances tie crash recovery to a named kernel Job and the
original launcher's birth identity. Legacy markers require separate evidence.
"""
from pathlib import Path
import hashlib,json,os,re,stat,time,uuid
from bot_private_file import open_private_new
from runtime_identity import identity_lock,safe_path


def _job_name(nonce):
    return r'Global\TelyinCore-SnowLuma-' + nonce


def _job_gone(name, *, stop_orphan=False):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    access = 0x0004 | (0x0008 if stop_orphan else 0)  # QUERY, optionally TERMINATE
    handle = kernel.OpenJobObjectW(access, False, name)
    if not handle:
        return ctypes.get_last_error() == 2  # ERROR_FILE_NOT_FOUND
    try:
        if stop_orphan:
            kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel.TerminateJobObject.restype = wintypes.BOOL
            if not kernel.TerminateJobObject(handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    return False


def _recover_private_marker(core, path, installation, *, stop_orphan=False):
    from bot_host_observer import probe_process
    from bot_stable_file import read_stable
    from bot_startup_probe import _json
    from launcher_exit_log import exit_event

    snapshot = read_stable(core.parent, path, 1024)
    row = _json(snapshot[0])
    if (set(row) != {'version', 'nonce', 'installation', 'host_pid', 'host_created'}
            or type(row['version']) is not int or row['version'] != 2
            or row['installation'] != installation
            or not isinstance(row['nonce'], str) or not re.fullmatch('[0-9a-f]{32}', row['nonce'])
            or type(row['host_pid']) is not int or not 0 < row['host_pid'] < 0xffffffff
            or type(row['host_created']) is not int or row['host_created'] <= 0):
        raise ValueError('unknown companion custody identity')
    host = probe_process(row['host_pid'])
    if not (host.get('state') == 'absent' or
            (host.get('state') == 'alive' and
             type(host.get('created_filetime')) is int and host['created_filetime'] > row['host_created'])):
        raise ValueError('original companion HOST may still be active')
    # Windows keeps the named Job object until its handles AND processes are
    # gone. It is created before CreateProcess and the process is born in it.
    job = _job_name(row['nonce'])
    if stop_orphan:
        _job_gone(job, stop_orphan=True)
    deadline = time.monotonic() + .5
    while not _job_gone(job):
        if time.monotonic() >= deadline:
            raise ValueError('original companion Job still exists or cannot be queried')
        time.sleep(.025)
    if read_stable(core.parent, path, 1024) != snapshot:
        raise ValueError('companion custody changed during recovery')
    archive = safe_path(core.parent, path.with_name('companion-custody.retired-' + row['nonce'] + '.json'))
    path.rename(archive)  # Windows rename refuses an existing destination.
    exit_event('snowluma_orphan_custody_archived')


def _installation(core):
    from service_layout import snowluma_directory
    return hashlib.sha256(os.path.normcase(str(snowluma_directory(core))).encode()).hexdigest()


def check_previous_companion(core, bot):
    """Check the selected Bot before Core birth; dispatch still rechecks on admission."""
    from snowluma_instance import instance_root
    from bot_stable_file import read_stable
    from bot_startup_probe import _json
    from launcher_exit_log import exit_event

    core = Path(core).resolve()
    base = core / 'data'
    path = instance_root(core, bot) / 'companion-custody.json'
    if not path.exists():
        return None
    with identity_lock(base):
        if not path.exists():
            return None
        try:
            snapshot = read_stable(core.parent, path, 1024)
            row = _json(snapshot[0])
            if row.get('version') == 1:
                return '当前 Bot 有旧版 SnowLuma 托管登记，缺少进程身份；请先核对旧登记。'
            installation = _installation(core)
            if row.get('installation') != installation:
                from deployment_relocation import previous_installation, verify_bot
                verify_bot(core, bot)
                previous = previous_installation(core)
                if previous is None:
                    return 'SnowLuma 托管登记的安装路径已变化；请核对原安装目录。原登记已保留。'
                old_ids = {hashlib.sha256(os.path.normcase(str(previous / layout)).encode()).hexdigest()
                           for layout in ('SnowLuma', 'services/SnowLuma')}
                if row.get('installation') not in old_ids:
                    return 'SnowLuma 托管登记与原安装路径不匹配，已保留登记。'
                # A relocated installation has no authority to stop an old Job.
                # Archive only after the original HOST and the named Job are gone.
                _recover_private_marker(core, path, row['installation'], stop_orphan=False)
                return '已核验原安装的 SnowLuma 托管进程和进程组均已退出，旧登记已归档。'
            _recover_private_marker(core, path, installation, stop_orphan=True)
        except (OSError, ValueError) as error:
            exit_event('snowluma_preflight_retained', bot=bot, error=error)
            return '当前 Bot 的旧 SnowLuma 尚未确认退出；若原启动器还在，请先正常休止。'
        return '已核验并归档当前 Bot 的 SnowLuma 旧登记。'


class CustodyAdmissionDenied(FileExistsError):
    """Exclusive create denied before obtaining a file/native owner."""


class CompanionCustody:
    def __init__(self,core,*,bot=None):
        self.core=Path(core).resolve()
        self.base=self.core/'data'
        self.path=self.base/'snowluma-companion-custody.json'
        if bot is not None:
            from snowluma_instance import instance_root, load_instance
            if load_instance(self.core,bot) is None:raise ValueError('private SnowLuma instance required')
            self.path=instance_root(self.core,bot)/'companion-custody.json'
        self.owner=None
        self.released=False
        self._attempted=False
        self._inode=None
        self.failure_records=[]
        installation = _installation(self.core)
        nonce = uuid.uuid4().hex
        row = dict(version=1, nonce=nonce, installation=installation)
        self.job_name = None
        if bot is not None and os.name == 'nt':
            from bot_host_observer import probe_process
            host = probe_process(os.getpid())
            if host.get('state') != 'alive':
                raise ValueError('original companion HOST identity unavailable')
            row.update(version=2, host_pid=host['pid'], host_created=host['created_filetime'])
            self.job_name = _job_name(nonce)
        self._raw = (json.dumps(row, sort_keys=True) + '\n').encode()
        with identity_lock(self.base):
            if bot is not None and (self.base/'snowluma-companion-custody.json').exists():
                raise CustodyAdmissionDenied('legacy shared companion custody still retained')
            safe_path(self.core.parent,self.path)
            self.base.mkdir(parents=True,exist_ok=True)
            if self.job_name is not None and self.path.exists():
                try:
                    _recover_private_marker(self.core, self.path, installation)
                except (OSError, ValueError) as error:
                    raise CustodyAdmissionDenied('existing companion custody; no birth admitted') from error
            try:stream=open_private_new(self.path)
            except FileExistsError:
                raise CustodyAdmissionDenied('existing companion custody; no birth admitted') from None
            with stream as f:
                info=os.fstat(f.fileno());self._inode=(info.st_dev,info.st_ino)
                f.write(self._raw);f.flush();os.fsync(f.fileno())

    def bind(self,owner):
        from bot_independent_owner import IndependentProcessOwner
        if self.owner is not None or self.released or type(owner) is not IndependentProcessOwner or owner.state!='planned':
            raise ValueError('exact unstarted companion owner required')
        self.owner=owner

    def retire(self):
        if self.released:return True
        if self._attempted:raise ValueError('custody retirement uncertain; retry denied')
        self._attempted=True
        if self.owner is None:raise ValueError('custody without native owner remains pending')
        self.owner.assert_releasable()
        with identity_lock(self.base):
            p=safe_path(self.core.parent,self.path)
            with p.open('rb') as f:
                info=os.fstat(f.fileno());raw=f.read(1025)
            if (raw!=self._raw or (info.st_dev,info.st_ino)!=self._inode
                    or info.st_nlink!=1 or not stat.S_ISREG(info.st_mode)):
                raise ValueError('custody marker changed; not retired')
            self.owner.assert_releasable()
            p.unlink()
            self.released=True
        return True

    def record_failure(self,error):
        """Append evidence, never clear or mutate original custody/failure."""
        if len(self.failure_records)>=16:raise ValueError('failure audit capacity; original responsibility retained')
        from bot_stable_file import read_stable
        with identity_lock(self.base):
            raw,identity=read_stable(self.core.parent,self.path,1024)
            if raw!=self._raw or identity!=self._inode:raise ValueError('original custody changed before failure audit')
            path=self.path.with_name(self.path.stem+'.failure-'+uuid.uuid4().hex+'.json')
            row={'version':1,'kind':'cooperative-shutdown-failed','error_type':type(error).__name__,
                 'original_custody_sha256':hashlib.sha256(self._raw).hexdigest(),
                 'business_success':False,'release_grant':False}
            causes=[];seen=set();cause=error
            while cause is not None and id(cause) not in seen and len(causes)<8:
                seen.add(id(cause));causes.append(type(cause).__name__);cause=cause.__cause__
            row['cause_types']=causes
            with open_private_new(path) as f:
                f.write((json.dumps(row,sort_keys=True)+'\n').encode());f.flush();os.fsync(f.fileno())
            self.failure_records.append(path)
