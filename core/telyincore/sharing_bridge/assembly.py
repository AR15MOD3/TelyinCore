"""Own only sharing-preview resources; do not adopt unrelated Core services."""
import secrets
from .mount import BridgeMount
from telyincore.sharing_close_budget import bounded_close


class SharingCleanupIncomplete(RuntimeError):pass


class HostAssembly:
    retained={}

    def __init__(self,factory):
        if not callable(factory) or not all(callable(getattr(factory,n,None)) for n in ('close','shutdown_complete')):
            raise ValueError('owned_sharing_factory_required')
        self.factory=factory;self.mount=None;self.unknown=False;self.finished=False
        self.token=secrets.token_hex(16)

    def acquire(self,*,bot,plugin_host):
        if self.finished or self.mount is not None or self.unknown:raise RuntimeError('sharing_acquisition_not_allowed')
        self.unknown=True
        self.mount=BridgeMount(bot=bot,plugin_host=plugin_host,factory=self.factory)
        self.unknown=False
        return self.mount

    @bounded_close
    def finish(self,*,handle,release,primary=None):
        if self.finished:
            if self.token in self.retained:raise SharingCleanupIncomplete('sharing_cleanup_unresolved')
            return
        self.finished=True;failures=[]
        if self.mount is not None:
            try:self.mount.close()
            except BaseException:failures.append('sharing_mount_stop')
        try:self.factory.close()
        except BaseException:failures.append('sharing_factory_stop')
        try:
            complete=(not self.unknown and (self.mount is None or self.mount.shutdown_complete() is True)
                      and self.factory.shutdown_complete() is True)
        except BaseException:complete=False
        if not complete:failures.append('sharing_exit_unconfirmed')
        if not failures and handle is not None:
            try:release(handle)
            except BaseException:failures.append('instance_lock_release')
        if failures:
            self.retained[self.token]=(self,handle,release)
            if primary is not None:
                primary.add_note('sharing_cleanup_unresolved: '+','.join(failures));return
            raise SharingCleanupIncomplete('sharing_cleanup_unresolved')
        self.retained.pop(self.token,None)

    def recheck(self):
        """Explicit host action only, never an automatic send or cleanup retry."""
        record=self.retained.get(self.token)
        if record is None:raise SharingCleanupIncomplete('no_retained_sharing_resources')
        _,handle,release=record
        self.finished=False
        self.finish(handle=handle,release=release)
