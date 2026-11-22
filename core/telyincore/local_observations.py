"""Durable local input admission, sharing the existing delivery state machine.

Only this host-owned journal can recover scheduling metadata. Message contents
and plugin metadata cannot request preemption. A restart never replays an old
interrupt; unconsumed work is queued, uncertain processing remains quarantined.
"""
from dataclasses import fields, replace

from .activity_control import DeliveryMode
from .ingress_delivery import DeliveryJournal

MARKER = 'local_observation_delivery'


class LocalObservations:
    def __init__(self, console, root):
        self.console = console
        self.journal = DeliveryJournal(root, console.app.store)
        recovered = self.journal.recover()
        # Validate the whole recovery set before offering any event.
        modes = [(event, self._mode(event)) for event in recovered]
        for event, mode in modes:
            if mode in (DeliveryMode.YIELD, DeliveryMode.INTERRUPT):
                mode = DeliveryMode.READY
            self.journal.offer(event, lambda event, mode=mode:
                console.activity_control.submit(event, mode=mode))
            console.cursors = max(console.cursors, event.cursor)

    @staticmethod
    def _mode(event):
        if (event.source != 'console' or event.origin != 'external' or event.type != 'message'
                or event.sender_key != 'console:local-owner'):
            raise ValueError('local observation source changed')
        meta = event.meta or {}
        mode = meta.get(MARKER)
        if not isinstance(mode, str):
            raise ValueError('local observation delivery metadata invalid')
        return DeliveryMode(mode)

    def submit(self, event, *, mode, round_id=None):
        mode = DeliveryMode(mode)
        event = replace(event, meta={**(event.meta or {}), MARKER: mode.value})
        self._mode(event)
        token = self.journal.prepare()
        event = self.console._record_event({field.name: getattr(event, field.name)
            for field in fields(event) if field.name != 'cursor' and getattr(event, field.name) is not None})
        self.journal.stored(token, event)
        result = []
        self.journal.offer(event, lambda event: result.append(
            self.console.activity_control.submit(event, mode=mode, round_id=round_id)))
        return event, result[0]

    def begin_batch(self, batch):
        return self.journal.begin_batch(batch)

    def finish_batch(self, tickets):
        self.journal.finish_batch(tickets)

    def status(self):
        return {**self.journal.status(), 'replay_uncertain': False}


def bind(console, root):
    if getattr(console, 'local_observations', None) is not None:
        raise RuntimeError('local observation owner already bound')
    owner = LocalObservations(console, root)
    console.local_observations = owner
    console.activity_control.observation_status = owner.status
    return owner
