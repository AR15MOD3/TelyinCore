"""Authenticated own-instance publisher. No plugin/account/process capabilities or inventory."""
from pathlib import Path
from bot_lifecycle_channel import LifecycleClient
from bot_registry import bot_id


class InstancePublisher:
    def __init__(self,core,scope,bot,client):
        if type(client) is not LifecycleClient or type(bot) is not str or bot_id(bot)!=bot:
            raise ValueError('explicit private instance publisher required')
        self.core,self.bot,self.client=Path(core).resolve(),bot,client
        from threading import Lock
        self._companion_rpc=Lock()
        self.companion_supported=False
        self._companion_attempted=False
        self._companion_start_pending=False
        self._companion=None
        self._qq_attempted=False
        self._qq=None
        self.scope=None if scope is None else Path(scope).resolve()
        if self.scope is not None and self.scope!=self.core.parent/'bots'/bot/'infinity':
            raise ValueError('private instance publisher scope mismatch')

    def bind(self,core,bot):
        if Path(core).resolve()!=self.core or bot!=self.bot:
            raise ValueError('private instance launcher root/Bot mismatch')
        description=self._request('describe')
        if (description.get('instance_only') is not True or description.get('bot')!=self.bot
                or description['core']!=str(self.core)
                or description['bot_scope']!=(None if self.scope is None else str(self.scope))):
            self.client.close();raise ValueError('authenticated instance-only parent required')
        self.companion_supported=description.get('companion_supported') is True

    def publish_instance(self,port):
        if type(port) is not int or not 1<=port<=65535:raise ValueError('owned instance port required')
        self.bind(self.core,self.bot)
        self._request('publish-instance',port=port)

    def start_companion(self):
        if not self._companion_rpc.acquire(timeout=2):
            raise ValueError('companion start busy; no effect admitted')
        try:
            if not self.companion_supported or self._companion_start_pending:
                raise ValueError('SnowLuma previous start unconfirmed; no retry or local fallback')
            old=self._companion
            if self._companion_attempted and old is None:
                raise ValueError('SnowLuma original receipt unavailable; no retry')
            if old is not None and not old.retired:
                status=self.client.request('companion-status',slot=old.generation)
                if status['state']=='alive':raise ValueError('SnowLuma original generation still running')
                self.client.request('companion-retire',slot=old.generation)
                old.retired=True  # exact original tree/output/custody ACK
            self._companion_attempted=True
            self._companion_start_pending=True  # BEFORE new effect, even for restart
            from bot_launcher_companion import CompanionProcess
            from bot_parent_account_grant import AccountAdmissionConflict
            try:
                receipt = self.client.request('companion-start', slot=None if old is None else old.generation)
            except AccountAdmissionConflict:
                # The authenticated first-start refusal precedes native process creation.
                # A later button click must check the current registration again.
                if old is None:
                    self._companion_attempted = False
                    self._companion_start_pending = False
                raise
            self._companion=CompanionProcess(self,receipt)
            self._companion_start_pending=False
            return self._companion
        finally:self._companion_rpc.release()

    def request_companion(self, op, *, generation=None):
        from bot_launcher_companion import OPS
        if not self.companion_supported or op not in OPS:
            raise ValueError('fixed companion operation unavailable')
        current=self._companion
        if current is None or (generation is not None and generation!=current.generation):
            raise ValueError('stale SnowLuma generation; no request sent')
        return self._request(op,slot=current.generation)

    def _request(self, op, **kwargs):
        if not self._companion_rpc.acquire(timeout=2):
            raise ValueError('private launcher request busy; no effect admitted')
        try:return self.client.request(op, **kwargs)
        finally:self._companion_rpc.release()

    def check_account_scope(self):
        if not self.companion_supported:raise ValueError('default HOST account scope unavailable')
        return self._request('account-check')

    def revoke_account_scope(self):
        if not self.companion_supported:raise ValueError('default HOST account scope unavailable')
        return self._request('account-revoke')

    def begin_account_work(self):
        if not self.companion_supported:raise ValueError('default HOST account work unavailable')
        return self._request('account-work-begin')

    def acknowledge_account_work_drained(self):
        if not self.companion_supported:raise ValueError('default HOST account work unavailable')
        return self._request('account-work-drained')

    def start_qq(self):
        if not self._companion_rpc.acquire(timeout=2):raise ValueError('private QQ start busy')
        try:
            if not self.companion_supported or self._qq_attempted:
                raise ValueError('default QQ capability unavailable; no local fallback')
            self._qq_attempted=True
            from bot_parent_qq_launch import ParentQQProcess
            receipt=self.client.request('qq-start')
            self._qq=ParentQQProcess(self,receipt)
            return self._qq
        finally:self._companion_rpc.release()

    def request_qq(self,op):
        from bot_parent_qq_launch import OPS
        if not self.companion_supported or op not in OPS:raise ValueError('fixed QQ operation unavailable')
        return self._request(op)

    def acknowledge_account_work_finished(self):
        if not self.companion_supported:raise ValueError('default HOST account work unavailable')
        return self._request('account-work-finished')

    def acknowledge_account_configuration(self):
        if not self.companion_supported:raise ValueError('default HOST configuration scope unavailable')
        return self._request('account-configured')
