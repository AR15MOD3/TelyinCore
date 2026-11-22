"""One HOST-fixed QQ birth; no PID adoption, remote command or offline claim."""
from pathlib import Path
import hashlib
from bot_independent_owner import IndependentProcessOwner,IndependentOwnerFailure
from bot_host_observer import probe_process
from runtime_identity import identity_lock

OPS=frozenset(('qq-start','qq-status','qq-stop'))

class QQLaunchHost:
    def __init__(self,host):
        self.host=host
        self.owner=None
        self.attempted=False
        self.receipt=None
        self.image=None

    def _plan(self):
        from bot_launcher_child import child_environment
        g=self.host.account_grant
        with identity_lock(g.base):
            digest,revision,path=g._snapshot(include_path=True)
            if revision!=g.revision or g.path.name!=digest+'.json':
                raise ValueError('QQ intent changed before birth')
            if type(path) is not str or not path:raise ValueError('explicit QQ image required')
            p=Path(path)
            if (not p.is_absolute() or p.name.lower()!='qq.exe' or p.is_symlink()
                    or p.resolve()!=p or not p.is_file()):
                raise ValueError('fixed canonical QQ.exe required')
            self.image=(p,hashlib.sha256(p.read_bytes()).digest())
        return dict(argv=[str(p)],cwd=str(p.parent),env=child_environment())

    def _check_plan(self):
        if self.image is None:return
        p,digest=self.image
        if p.is_symlink() or p.resolve()!=p or hashlib.sha256(p.read_bytes()).digest()!=digest:
            raise ValueError('fixed QQ image changed before birth')

    def dispatch(self,op):
        h=self.host;g=h.account_grant
        if op not in OPS:raise ValueError('unknown fixed QQ operation')
        if op=='qq-start':
            if self.attempted:raise ValueError('QQ birth is single use; no retry')
            if g is None or h.account_fence is None or g.work_state!='active':
                raise ValueError('active admitted account work required')
            g.check(h.controller,h.owner,h.account_fence)
            self.attempted=True  # before plan/native effect, retained on any failure
            plan=self._plan()
            self.owner=IndependentProcessOwner(h.controller,**plan)
            self._check_plan();g.check(h.controller,h.owner,h.account_fence)
            p=self.owner.start();row=probe_process(p.pid)
            if (row.get('state')!='alive' or row.get('pid')!=p.pid
                    or type(row.get('created_filetime')) is not int
                    or row.get('image')!=str(Path(plan['argv'][0]).resolve()) or p.poll() is not None):
                raise IndependentOwnerFailure(self.owner,'QQ birth receipt')
            self.receipt=dict(ok=True,pid=p.pid,created=str(row['created_filetime']),exe=row['image'])
            return self.receipt
        if self.owner is None or self.receipt is None:raise ValueError('QQ has no acknowledged birth')
        if op=='qq-stop':
            self.owner.drain()  # whole native job + output, never just root exit
            return dict(ok=True)
        if self.owner.state=='pending':raise IndependentOwnerFailure(self.owner,'QQ pending')
        if self.owner.state!='retired':self.owner.job.verify()
        return dict(ok=True,state='absent' if self.owner.process.poll() is not None else 'alive')

    def assert_retained(self):
        # An operation may finish while this original live QQ stays HOST-owned.
        # This is never process drain, account offline, or transferable ownership.
        o=self.owner
        if (not self.attempted or self.receipt is None or o is None or o.state!='owned'
                or o.controller is not self.host.controller or o.process is None
                or o.process._handle is not o._handle or o.job.handle!=o._job_handle
                or o.process.pid!=self.receipt['pid'] or o.process.poll() is not None):
            raise ValueError('original live HOST QQ retention unconfirmed')
        o.job.verify()
        if o._member(o.process._handle,self.host.controller.birth_job.handle):
            raise ValueError('QQ retention separation changed')
        return True

    def assert_drained(self):
        if not self.attempted:return True
        if self.owner is None:raise ValueError('QQ birth uncertainty retained')
        return self.owner.assert_releasable()

    def close_after_controller(self):
        # Only our exact newly born job, never preexisting/manual QQ.
        if self.owner is not None and self.owner.state=='owned':self.owner.drain()
        return self.assert_drained()

class ParentQQProcess:
    def __init__(self,publisher,receipt):
        self.publisher=publisher
        self.pid=receipt['pid']
        self.retired=False
    def terminate(self):
        if not self.retired:
            self.publisher.request_qq('qq-stop')
            self.retired=True
    def poll(self):
        if self.retired:return 0
        return None if self.publisher.request_qq('qq-status')['state']=='alive' else 0
