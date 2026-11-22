"""Denied plugin projection for a confined launcher, NOT an empty host inventory."""
from telyincore import plugins


class ConfinedPlugins:
    def __init__(self):
        if not plugins._lifecycle_confined.is_set():
            raise ValueError('explicit plugin confinement required')

    @staticmethod
    def deny(*args, **kwargs):
        raise ValueError('parent plugin runtime capability unavailable; no local fallback')

    def read(self):
        # No installed manifests/configs/imports. Empty rows are NOT an OS inventory.
        return dict(plugins=[], broken=[], wired=False, lifecycle='blocked',
                    installedInventoryRead=False, complete=False, productionEnabled=False)

    def market(self):
        return dict(plugins=[], wired=False, lifecycle='blocked', complete=False,
                    productionEnabled=False)
