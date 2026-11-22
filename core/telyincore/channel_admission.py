"""Fail closed before an unconfigured external batch enters role generation.

Presentation admission never grants identity, scope, tools or outbound access.
Unverified provenance receives only a local fixed operator notice, not a reply
routed from its untrusted metadata. A verified provider without a Bot scene may
use the existing guarded output tap for the same fixed text.
"""
from dataclasses import dataclass
from .session_view import batch_scope

CHANNEL_UNAVAILABLE_NOTICE = (
    "当前通道环境尚未配置或来源未验证，暂不启用角色回复。"
    "请完成通道配置与验证后重试。"
)


@dataclass(frozen=True)
class ChannelAdmission:
    admitted: bool
    reason: str = "verified"
    remote_notice: bool = False

    def __post_init__(self):
        if type(self.admitted) is not bool or type(self.remote_notice) is not bool:
            raise TypeError('admission flags must be bool')
        if type(self.reason) is not str or self.reason not in {
                'verified', 'internal', 'platform_unconfigured',
                'provenance_unverified', 'scene_unconfigured', 'admission_failed'}:
            raise ValueError('invalid admission reason')


def decide_channel_batch(host, scenes, batch, *, identity_bridge=None):
    if identity_bridge is not None:
        if type(identity_bridge) is not BatchIdentityBridge:
            return ChannelAdmission(False, 'provenance_unverified')
        try:
            batch = identity_bridge.resolve(batch)
        except (ValueError, TypeError):
            return ChannelAdmission(False, 'provenance_unverified')
    external = [e for e in batch if getattr(e, 'origin', None) == 'external']
    if not external:
        return ChannelAdmission(True, 'internal')
    scene_missing = False
    plugin_only = True
    try:
        for event in external:
            # Check every receipt even when the combined batch is mixed.
            context = host.select([event])
            if context.status == 'unconfigured':
                return ChannelAdmission(False, 'platform_unconfigured')
            if context.status not in {'plugin', 'builtin'}:
                return ChannelAdmission(False, 'provenance_unverified')
            if context.status == 'builtin' and not (
                    context.source == 'console' and context.provider == 'host'):
                return ChannelAdmission(False, 'provenance_unverified')
            plugin_only = plugin_only and context.status == 'plugin'
            selected = scenes.select(context)
            scene_missing = scene_missing or selected.route == 'unknown'
        if scene_missing:
            scope = batch_scope(external)
            # This is a restriction only. The output tap must still authorize
            # its own destination. No room/user is taken from message text.
            remote = plugin_only and bool(scope) and scope != 'mixed'
            return ChannelAdmission(False, 'scene_unconfigured', remote)
        combined = scenes.select(host.select(batch))
        if combined.route == 'unknown':
            return ChannelAdmission(False, 'scene_unconfigured')
        return ChannelAdmission(True)
    except Exception:
        return ChannelAdmission(False, 'provenance_unverified')

class BatchIdentityBridge:
    """One host-owned snapshot lease, not an event-provenance grant.

    Console creates this BEFORE admission from the actual delivered objects and
    revokes it at every batch retirement/replacement. Consumers resolve the exact
    snapshot and then apply their existing source/ticket/store/TTL checks to the
    originals. Equal bytes or cursors never substitute for registered identity.
    Trusted in-process Python is not a sandbox boundary.
    """
    def __init__(self, batch):
        from copy import deepcopy
        from .events import EventEnvelope
        self._originals = tuple(batch)
        if any(type(event) is not EventEnvelope for event in self._originals):
            raise ValueError('event envelope batch required')
        self.snapshot = deepcopy(self._originals)
        self._sealed = self._fingerprint(self.snapshot)
        self._active = True

    @staticmethod
    def _fingerprint(batch):
        from dataclasses import asdict
        import json
        return json.dumps([asdict(event) for event in batch], sort_keys=True,
                          ensure_ascii=False, allow_nan=False, separators=(',', ':'))

    def resolve(self, batch):
        batch = tuple(batch)
        if (not self._active or len(batch) != len(self.snapshot)
                or any(a is not b for a, b in zip(batch, self.snapshot))):
            raise ValueError('inactive or foreign event snapshot')
        if (self._fingerprint(batch) != self._sealed
                or self._fingerprint(self._originals) != self._sealed):
            raise ValueError('event snapshot changed')
        return self._originals

    def revoke(self):
        self._active = False
        self._originals = ()
