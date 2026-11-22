"""Reconcile an admitted account and QQ scope on its original live HOST.

Credential revision is local admission only, not account change or stale reset. Unknown login,
mutation, HTTP and work state stays denied. No network, account data in RPC, or
account reservation deletion.
"""
import os,stat,hashlib
from runtime_identity import identity_lock,safe_path
from bot_initial_account_configuration import InitialAccountConfiguration

class KnownAccountConfiguration:
    def __init__(self,host):
        self.host=host;self.state='ineligible';self.inode=None
        g=host.account_grant
        if g is None or g.path is None:return
        g.check(host.controller,host.owner,host.account_fence)
        self._resources()
        self._clear_ledgers()
        self.digest,self.revision,self.qq_path=g._snapshot(include_path=True)
        self.before=self._pin(g.store)
        if self.before is None:raise ValueError('admitted recovery store missing')
        self.grant=self._pin(g.path)
        self.state='prepared'

    def _pin(self,path):
        # Reuse fixed bounded file shape; no content leaves HOST.
        return InitialAccountConfiguration._pin(self,path)

    def _resources(self):
        h=self.host;g=h.account_grant
        if g.pending or g.local_retirement!='open' or g.work_state not in ('idle','drained','finished'):
            raise ValueError('admitted account work uncertain')
        g._native(h.controller,h.owner)
        if h.qq.attempted:h.qq.assert_retained()
        if g.work_path is not None and safe_path(h.core.parent,g.work_path).exists():
            raise ValueError('account work marker remains')

    def _clear_ledgers(self):
        from bot_recovery_http_lease import RecoveryClientLease
        from bot_login_lifecycle import LoginLifecycle
        from bot_recovery_mutation_journal import MutationJournal
        h=self.host;store=h.account_grant.store
        RecoveryClientLease.check(h.core.parent,store)
        LoginLifecycle(h.core.parent,store).check(lambda:None)
        if MutationJournal(h.core.parent,store)._records():
            raise ValueError('QQ mutation history requires reconciliation')

    def bind_fresh_fence(self):
        if self.state!='prepared':return
        self.inode=self.host.account_fence.created_inode
        if self.inode is None:raise ValueError('new settings fence missing')
        self.state='fenced'

    def commit(self):
        if self.state=='ineligible':return False
        if self.state!='fenced':raise ValueError('known settings commit unresolved; no retry')
        self.state='pending'
        h=self.host;g=h.account_grant;f=h.account_fence
        self._resources();self._clear_ledgers()
        with identity_lock(g.base):
            digest,revision,qq_path=g._snapshot(include_path=True)
            if digest!=self.digest or qq_path!=self.qq_path:
                raise ValueError('account or QQ image scope changed; reconciliation required')
            if self._pin(g.path)!=self.grant:raise ValueError('account admission changed')
            after=self._pin(g.store);backup=self._pin(g.store.with_suffix('.json.bak'))
            if after is None or backup is None or backup[0]!=self.before[0]:
                raise ValueError('original recovery before-image backup missing')
            p=safe_path(h.core.parent,f.path)
            with p.open('rb') as stream:raw=stream.read(1025);i=os.fstat(stream.fileno())
            if raw!=f.raw or (i.st_dev,i.st_ino)!=self.inode or i.st_nlink!=1 or not stat.S_ISREG(i.st_mode):
                raise ValueError('fresh admitted-settings fence identity changed')
            self._resources()
            if self._pin(g.store)!=after or self._pin(g.path)!=self.grant or self._pin(g.store.with_suffix('.json.bak'))!=backup:
                raise ValueError('concurrent settings/admission change')
            if revision!=self.revision:
                g._revise_configuration_locked(self.revision,revision)
            p.unlink()  # only our fresh local settings gate; same account reservation retained
        self.state='committed'
        g.check(h.controller,h.owner,f)
        return True
