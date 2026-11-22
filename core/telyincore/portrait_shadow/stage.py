(
    'Offline shadow staging only. No inner writer,'
    ' grant minting, or live entry.'
)
import asyncio
from dataclasses import asdict
from .review import ReviewCoordinator, Decision, canonical, fingerprint
from telyincore.dispatch import Receipt

class ShadowProfileStage:

    def __init__(self, turn_of, coordinator, backend):
        self.turn_of, self.coordinator, self.backend = (turn_of, coordinator, backend)
        self.records = []
        self._seen = set()

    async def call(self, args, *, call_id):
        record = (
            dict(call_id=call_id, mode='shadow', native_write_calls=0, write_authorized=False)
        )
        self.records.append(record)

        def settle(decision):
            record.update(asdict(decision))
            return (
                Receipt(call_id, 'profile_add', '本次未写入画像（shadow审核：' + decision.reason +
                    '；审核意见不授权写入）。', 'receipt')
            )
        if not isinstance(call_id, str) or not call_id or call_id in self._seen:
            return settle(Decision('reject', 'invalid_or_replayed_call_id'))
        self._seen.add(call_id)
        try:
            captured = __import__('json').loads(canonical(args))
            turn = self.turn_of()
            req = self.coordinator.issue(turn, captured)
            record.update(request_id=req.request_id, request_sha256=req.request_sha256,
                proposal_sha256=req.proposal_sha256, turn_sha256=req.turn_sha256)
        except Exception as exc:
            return settle(Decision('reject', 'request_error:' + type(exc).__name__))
        try:
            decision = await self.coordinator.review(req, self.backend)
        except asyncio.CancelledError:
            record.update(status='cancelled', reason='cancelled')
            raise
        if decision.status != 'pass':
            return settle(decision)
        try:
            if fingerprint(self.turn_of()) != req.turn_sha256:
                return settle(Decision('reject', 'context_changed'))
        except Exception:
            return settle(Decision('needs_review', 'current_context_unavailable'))
        return settle(Decision('shadow', 'advisory_only', decision.source_id))
