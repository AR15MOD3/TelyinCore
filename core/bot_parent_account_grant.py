"""Finite parent admission for one configured account/store and owned process.

Positive local operation permission only, NOT login, external-account ownership,
offline or complete-host evidence. Never clear stale/unknown grants. HOST scope
and paths are fixed by bootstrap; remote request contains no account or secrets.
"""
from pathlib import Path
import hashlib,json,os,stat,uuid
from bot_private_file import open_private_new
from bot_plugin_ownership import _account
from runtime_identity import identity_lock,safe_path


ACCOUNT_ADMISSION_GUIDANCE = (
    '请在启动器休止使用此 QQ 号的旧 Bot，再到「QQ 插件」点「启动 SnowLuma」。'
    '旧 Bot 已退出仍有提示时，可在原安装的 core 目录运行「QQ 准入恢复.bat」核验。若提示有未完成操作，请把 Bot 名称和日志发给维护者。'
)


class AccountAdmissionConflict(ValueError):
    """Definite pre-birth denial, not an uncertain native/account operation."""


class ParentAccountGrant:
    def __init__(self,core,bot,*,baseline):
        from bot_registry import bot_id
        self.core=Path(core).resolve();self.bot=bot_id(bot)
        if type(baseline) is not bool:raise ValueError('fixed HOST baseline required')
        self.base=self.core/'data'
        scope=self.core if baseline else self.core.parent/'bots'/self.bot/'infinity'
        from service_layout import qq_recovery_file
        self.store=qq_recovery_file(self.core,self.bot,baseline=baseline)
        self.owner=self.controller=None
        self.revision=self.path=self.raw=self.inode=None
        self.pending=False
        self.local_retirement="open"
        self.retirement_path=None
        self._retirement_binding=None
        self._retirement_raw=self._retirement_inode=None
        self._terminal_invalid=False
        self.work_state="idle"
        self.work_path=self.work_raw=self.work_inode=None
        self._nonce=uuid.uuid4().hex

    def _snapshot(self, *, include_path=False):
        p=safe_path(self.core.parent,self.store)
        with p.open('rb') as f:
            info=os.fstat(f.fileno());raw=f.read(65537)
        if len(raw)>65536 or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise ValueError('bounded fixed recovery store required')
        def unique(pairs):
            out={}
            for k,v in pairs:
                if k in out:raise ValueError('duplicate account intent')
                out[k]=v
            return out
        data=json.loads(raw,object_pairs_hook=unique)
        if type(data) is not dict:raise ValueError('account intent object required')
        account=_account(data.get('targetUin'))
        selected={k:data.get(k) for k in ('targetUin','username','qqPath','credential')}
        # Session rotates during our legitimate login. It is not a new grant to
        # change the target/credentials; those fields remain exactly committed.
        revision=hashlib.sha256(json.dumps(selected,sort_keys=True,allow_nan=False).encode()).hexdigest()
        digest=hashlib.sha256(b'parent-account-admission-v1\0'+account.encode()).hexdigest()
        return (digest,revision,data.get("qqPath")) if include_path else (digest,revision)

    def preflight_new_host(self):
        """No grant import/release. Detect retained denial BEFORE native birth.

        A missing store is valid for first-time setup; this is NOT admission.
        The actual native-bound check still runs before all account work.
        """
        if self.pending or self.local_retirement!='open' or self.path is not None:
            raise ValueError('new HOST preflight requires unused grant')
        with identity_lock(self.base):
            store=safe_path(self.core.parent,self.store)
            if not store.exists():return
            digest,_=self._snapshot()
            path=self.base/'parent-account-admissions'/(digest+'.json')
            for candidate in (path,path.with_suffix('.work.json'),path.with_suffix('.local-retired.json')):
                if safe_path(self.core.parent,candidate).exists():
                    raise AccountAdmissionConflict(ACCOUNT_ADMISSION_GUIDANCE)

    def _native(self,controller,owner):
        from bot_birth_process import BirthPopen
        from bot_independent_owner import IndependentProcessOwner
        if (type(controller) is not BirthPopen or type(owner) is not IndependentProcessOwner
                or owner.controller is not controller or controller.poll() is not None
                or owner.state!='owned' or owner.process is None or owner.process.poll() is not None
                or owner.process._handle is not owner._handle or owner.job.handle!=owner._job_handle):
            raise ValueError('live exact HOST-owned account resource required')
        owner.job.verify()
        if owner._member(owner.process._handle,controller.birth_job.handle):
            raise ValueError('independent account process required')
        if self.owner is not None and (self.owner is not owner or self.controller is not controller):
            raise ValueError('account grant resource changed')

    def check(self,controller,owner,fence):
        if self.pending or self.local_retirement!='open':raise ValueError('parent account grant pending/retired; no retry')
        try:
            fence.check();self._native(controller,owner)
            with identity_lock(self.base):
                digest,revision=self._snapshot()
                if self.path is None:
                    self.owner,self.controller=owner,controller
                    self.revision=revision
                    self.path=self.base/'parent-account-admissions'/(digest+'.json')
                    self.raw=(json.dumps(dict(version=1,owner=self._nonce,account=digest,
                        bot=hashlib.sha256(self.bot.encode()).hexdigest(),revision=revision,
                        store=hashlib.sha256(str(self.store).encode()).hexdigest()),sort_keys=True)+'\n').encode()
                    safe_path(self.core.parent,self.path);self.path.parent.mkdir(parents=True,exist_ok=True)
                    with open_private_new(self.path) as f:
                        i=os.fstat(f.fileno());self.inode=(i.st_dev,i.st_ino)
                        f.write(self.raw);f.flush();os.fsync(f.fileno())
                if self.revision!=revision or self.path.name!=digest+'.json':
                    raise ValueError('parent account intent changed')
                p=safe_path(self.core.parent,self.path)
                with p.open('rb') as f:
                    raw=f.read(1025);i=os.fstat(f.fileno())
                if (raw!=self.raw or (i.st_dev,i.st_ino)!=self.inode
                        or i.st_nlink!=1 or not stat.S_ISREG(i.st_mode)):
                    raise ValueError('parent account grant metadata changed')
                fence.check();self._native(controller,owner)
            return True
        except BaseException:
            self.pending=True  # including failed native binding/partial publication
            raise

    def begin_work(self,controller,owner,fence):
        if self.work_state not in ('idle','drained','finished'):
            raise ValueError('parent account work unresolved; no new admission')
        self.check(controller,owner,fence)
        self.work_state='pending'  # before exclusive persistence, never retry on uncertainty
        self.work_path=self.path.with_suffix('.work.json')
        self.work_raw=(json.dumps(dict(version=1,grant=self._nonce,operation=uuid.uuid4().hex),sort_keys=True)+'\n').encode()
        with identity_lock(self.base):
            safe_path(self.core.parent,self.work_path)
            with open_private_new(self.work_path) as f:
                i=os.fstat(f.fileno());self.work_inode=(i.st_dev,i.st_ino)
                f.write(self.work_raw);f.flush();os.fsync(f.fileno())
            fence.check()
        self.work_state='active'
        return True

    def drain_work(self, *, retained=False):
        # Private trusted controller reports ONLY its own local resources drained.
        # This never deletes the account grant/revocation/login/mutation ledgers.
        if self.work_state!='active':raise ValueError('account work has no fresh drain acknowledgement')
        self.work_state='pending'  # failed identity/unlink cannot be automatically retried
        with identity_lock(self.base):
            p=safe_path(self.core.parent,self.work_path)
            with p.open('rb') as f:
                raw=f.read(1025);i=os.fstat(f.fileno())
            if (raw!=self.work_raw or (i.st_dev,i.st_ino)!=self.work_inode
                    or i.st_nlink!=1 or not stat.S_ISREG(i.st_mode)):
                raise ValueError('account work marker changed; not drained')
            p.unlink()
        self.work_state='finished' if retained else 'drained'
        return True

    def check_process_rebind(self,old,fence):
        if self.pending or self.local_retirement!='open' or self.work_state not in ('idle','drained','finished'):
            raise ValueError('account work/grant unresolved; Snow restart denied')
        old.assert_releasable()
        fence.check()
        if self.path is None:
            if self.owner is not None:raise ValueError('unpublished account binding')
            return True
        if self.owner is not old:raise ValueError('original account process changed')
        with identity_lock(self.base):
            digest,revision=self._snapshot()
            with safe_path(self.core.parent,self.path).open('rb') as stream:
                raw=stream.read(1025);i=os.fstat(stream.fileno())
            if (revision!=self.revision or self.path.name!=digest+'.json' or raw!=self.raw
                    or (i.st_dev,i.st_ino)!=self.inode or i.st_nlink!=1 or not stat.S_ISREG(i.st_mode)):
                self.pending=True
                raise ValueError('account intent/admission changed; no rebind')
        return True

    def rebind_process(self,controller,old,new,fence):
        self.check_process_rebind(old,fence)
        if self.path is None:return  # no account operation was admitted
        self.owner=new  # retain new native binding even if validation fails
        self.check(controller,new,fence)  # same grant bytes/account reservation; no logout/reset

    def retire_local(self,controller,owner,qq):
        """End this original local capability, NOT external account ownership.

        Retain admission history and all work/login/mutation/fence files. A fixed private terminal
        receipt records only original native resources drained after controller
        exit. Only the original native-verified object archives a clean local grant;
        this is no external account release, pending reset or reconnection.
        """
        if self.local_retirement!='open':raise ValueError('account local retirement unresolved; no retry')
        self.local_retirement='pending'  # denial precedes native/file operations
        self._retirement_binding=(controller,owner,qq)
        from bot_birth_process import BirthPopen
        if (type(controller) is not BirthPopen or controller.poll() is None
                or controller._handle is not controller._birth_handle):
            raise ValueError('original controller exit unconfirmed')
        if self.controller is not None and controller is not self.controller:
            raise ValueError('account controller binding changed')
        if self.owner is not None and owner is not self.owner:
            raise ValueError('account native owner binding changed')
        if owner is not None:owner.assert_releasable()
        qq.assert_drained()
        if self.pending or self.work_state not in ('idle','drained','finished'):
            raise ValueError('account work uncertainty retained after native retirement')
        if self.path is not None:
            with identity_lock(self.base):
                p=safe_path(self.core.parent,self.path)
                with p.open('rb') as stream:
                    raw=stream.read(1025);i=os.fstat(stream.fileno())
                if (raw!=self.raw or (i.st_dev,i.st_ino)!=self.inode
                        or i.st_nlink!=1 or not stat.S_ISREG(i.st_mode)):
                    raise ValueError('original account admission changed')
                if self.work_path is not None and safe_path(self.core.parent,self.work_path).exists():
                    raise ValueError('account work marker still present')
                self.retirement_path=self.path.with_suffix('.local-retired.json')
                safe_path(self.core.parent,self.retirement_path)
                receipt=(json.dumps(dict(version=1,grant=self._nonce,
                    admission_sha256=hashlib.sha256(self.raw).hexdigest(),
                    local_resources_drained=True,external_account_offline=False),sort_keys=True)+'\n').encode()
                with open_private_new(self.retirement_path) as stream:
                    stream.write(receipt);stream.flush();os.fsync(stream.fileno())
                    i=os.fstat(stream.fileno())
                    self._retirement_raw=receipt;self._retirement_inode=(i.st_dev,i.st_ino)
        self.local_retirement='retired'
        if self.path is not None:
            try:self._archive_original_retirement(controller,owner,qq)
            except BaseException:
                self.pending=True
                raise
        return True

    def _archive_original_retirement(self,controller,owner,qq):
        # Original live object ends local admission, not account reservation.

        # Receipt moves first, admission LAST under the same identity lock.
        # Failure before the final no-clobber rename leaves admission denying
        # new HOSTs. No reader imports a retired JSON as capability. A new HOST
        # must perform fresh native/account checks; external offline is not asserted.

        self.assert_local_retired(controller,owner,qq)
        with identity_lock(self.base):
            paths=((self.path,self.raw,self.inode),
                   (self.retirement_path,self._retirement_raw,self._retirement_inode))
            for p,expected,inode in paths:
                with safe_path(self.core.parent,p).open('rb') as stream:
                    seen=stream.read(1025);i=os.fstat(stream.fileno())
                if (seen!=expected or (i.st_dev,i.st_ino)!=inode
                        or i.st_nlink!=1 or not stat.S_ISREG(i.st_mode)):
                    raise ValueError('original terminal archive source changed')
            # Check the canonical marker even when this HOST never began work.
            work=safe_path(self.core.parent,self.path.with_suffix('.work.json'))
            if work.exists():raise ValueError('account work prevents terminal archive')
            directory=safe_path(self.core.parent,self.base/'parent-account-history'/self._nonce)
            directory.mkdir(parents=True,exist_ok=False)
            # The UUID directory already isolates this original grant. Repeating
            # its scope digest could make retirement exceed Windows path limits.
            # The receipt content binds the original bytes, not this basename.
            admission=directory/'admission.json'
            receipt=admission.with_suffix('.local-retired.json')
            safe_path(self.core.parent,admission);safe_path(self.core.parent,receipt)
            # Windows rename is no-clobber. Destinations are in our fresh UUID dir.
            if os.name!='nt':raise ValueError('native no-clobber archive requires Windows')
            os.rename(self.retirement_path,receipt)
            os.rename(self.path,admission)  # final publication: canonical grant no longer active
            self.path,self.retirement_path=admission,receipt
            if self.work_path is not None:self.work_path=admission.with_suffix('.work.json')

    def assert_local_retired(self,controller,owner,qq):
        """Recheck THIS original terminal grant, never import a JSON authority.

        Keep all admission/reservation/history bytes. This predicate grants no
        external offline, cross-HOST reauthorization or global rename coverage.
        """
        if self._terminal_invalid:raise ValueError('original local terminal binding revoked')
        try:
            from bot_birth_process import BirthPopen
            from bot_parent_qq_launch import QQLaunchHost
            binding=self._retirement_binding
            if (self.local_retirement!='retired' or self.pending or binding is None
                    or any(a is not b for a,b in zip(binding,(controller,owner,qq)))
                    or type(controller) is not BirthPopen or controller.poll() is None
                    or controller._handle is not controller._birth_handle
                    or type(qq) is not QQLaunchHost or qq.host.controller is not controller
                    or qq.host.account_grant is not self or qq.host.core!=self.core
                    or self.work_state not in ('idle','drained','finished')):
                raise ValueError('original grant/native/QQ terminal binding changed')
            if owner is not None:owner.assert_releasable()
            qq.assert_drained()
            with identity_lock(self.base):
                if self.work_path is not None and safe_path(self.core.parent,self.work_path).exists():
                    raise ValueError('account work reappeared after retirement')
                if self.path is None:
                    if self.owner is not None or self.retirement_path is not None:
                        raise ValueError('unpublished terminal grant changed')
                else:
                    if (self.controller is not controller or self.owner is not owner
                            or self.retirement_path!=self.path.with_suffix('.local-retired.json')
                            or self._retirement_raw is None):
                        raise ValueError('original terminal grant path changed')
                    for p,raw,inode in ((self.path,self.raw,self.inode),
                            (self.retirement_path,self._retirement_raw,self._retirement_inode)):
                        p=safe_path(self.core.parent,p)
                        with p.open('rb') as f:seen=f.read(1025);i=os.fstat(f.fileno())
                        if (seen!=raw or (i.st_dev,i.st_ino)!=inode or i.st_nlink!=1
                                or not stat.S_ISREG(i.st_mode)):
                            raise ValueError('original terminal admission/receipt changed')
            return True
        except BaseException:
            self._terminal_invalid=True
            raise

    def _revise_configuration_locked(self,expected,new):
        """Original HOST local credential revision only; caller holds identity lock.

        Same account reservation/nonce/owner. Exclusive immutable before-image
        precedes private atomic replacement; never remove original account name.
        Any uncertainty terminally denies this object. No external login proof.
        """
        if (self.pending or self.local_retirement!='open' or self.revision!=expected
                or self.work_state not in ('idle','drained','finished') or self.path is None
                or type(new) is not str or len(new)!=64 or any(c not in '0123456789abcdef' for c in new)):
            raise ValueError('admitted credential revision unavailable')
        try:
            p=safe_path(self.core.parent,self.path)
            with p.open('rb') as stream:raw=stream.read(1025);i=os.fstat(stream.fileno())
            if (raw!=self.raw or (i.st_dev,i.st_ino)!=self.inode or i.st_nlink!=1 or not stat.S_ISREG(i.st_mode)):
                raise ValueError('original account admission changed before revision')
            digest,current=self._snapshot()
            if current!=new or p.name!=digest+'.json':raise ValueError('credential revision changed again')
            row=json.loads(self.raw);row['revision']=new
            updated=(json.dumps(row,sort_keys=True)+'\n').encode()
            token=uuid.uuid4().hex
            backup=p.with_name('revision-'+token+'.json')
            temporary=p.with_name('revision-'+token+'.pending')
            safe_path(self.core.parent,backup);safe_path(self.core.parent,temporary)
            with open_private_new(backup) as stream:
                stream.write(self.raw);stream.flush();os.fsync(stream.fileno())
            with open_private_new(temporary) as stream:
                stream.write(updated);stream.flush();os.fsync(stream.fileno())
                info=os.fstat(stream.fileno());new_inode=(info.st_dev,info.st_ino)
            with p.open('rb') as stream:raw=stream.read(1025);i=os.fstat(stream.fileno())
            if (raw!=self.raw or (i.st_dev,i.st_ino)!=self.inode or i.st_nlink!=1 or not stat.S_ISREG(i.st_mode)):
                raise ValueError('concurrent original admission change')
            info=temporary.lstat()
            if (info.st_dev,info.st_ino)!=new_inode or info.st_nlink!=1 or not stat.S_ISREG(info.st_mode):
                raise ValueError('credential revision temporary changed')
            os.replace(temporary,p)  # no unlink gap for the reserved account name
            self.revision,self.raw,self.inode=new,updated,new_inode
        except BaseException:
            self.pending=True
            raise
