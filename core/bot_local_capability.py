"""Unavailable runtime views and an explicit metadata-only store facade.

Unavailable views hold no paths, secrets, threads or callbacks. The opt-in
settings facade owns a guarded Recovery store, never a login/runtime capability.
Unknown is not evidence that a real service or account is offline.
"""


def require_local_runtime(lifecycle):
    if lifecycle is not None:
        raise ValueError('experimental lifecycle forbids this unmediated local capability')


class UnavailableInstaller:
    def read(self):
        return dict(available=False, busy=False, phase='unavailable',
                    message='Installation is not mediated by the lifecycle host.')

    def control(self, *args, **kwargs):
        require_local_runtime(self)

    def start(self, *args, **kwargs):
        require_local_runtime(self)


class UnavailableRecovery:
    def state(self):
        return dict(available=False, healthSchema='snow-health-v2',
            generation=0, revision=None, healthSampleExpired=False,
            healthTarget='', healthSampleAgeSeconds=None, healthStatus='unknown',
            accountEvidence='none', workerAlive=False, loginCompleted=False,
            busy=False, phase='unavailable', accountOnline=False,
            managementAuthenticated=False, qqPath='', username='', targetUin='',
            hasCredentials=False, message='SnowLuma/account recovery is not mediated.')

    def cancel(self):
        pass  # there is no worker and no local/remote cancellation capability

    def start(self, *args, **kwargs):
        require_local_runtime(self)

    def save(self, *args, **kwargs):
        require_local_runtime(self)

    def connection_config(self):
        require_local_runtime(self)


class MediatedRecoverySettings(UnavailableRecovery):
    """Metadata-only store facade. No login, secret, runtime or health capability.

    Private store is trusted same-process Python, not a sandbox boundary.
    """
    def __init__(self, bridge, path):
        from snowluma_recovery import Recovery
        self._store = Recovery(path, process_lifecycle=bridge)

    def close(self):
        self._store.close()  # local denial only; no parent RPC or credential read

    def state(self):
        if self._store._closed.is_set():
            result = self._store.state()
            result.update(available=False, settingsStoreAvailable=False)
            return result
        result = super().state()
        result['settingsStoreAvailable'] = True
        return result

    def save(self, values):
        return self._store.save_identity(values)
