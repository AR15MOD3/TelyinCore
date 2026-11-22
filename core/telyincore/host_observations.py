"""In-memory chronology of admitted observations for one eligible private-chat route.

Only a clock and source-specific digests survive a request. Text always comes from the
current host providers, after the recipient and branch have been checked.
"""
from dataclasses import dataclass
from hashlib import sha256
from weakref import ref


def _digest(value):
    return sha256(value.encode('utf-8', 'surrogatepass')).digest() if value else None


@dataclass(frozen=True)
class Observation:
    route: tuple
    stamp: str
    activity: bytes | None
    facts: bytes | None
    contexts: tuple


class ObservationRequest(list):
    """Request-only observation admission and planning data, invisible on the wire."""

    def __init__(self, rows, session, incoming, estimated, observation=None):
        super().__init__(rows)
        self.epoch = session._tool_evidence_epoch
        self.boundary = len(session._records)
        self.incoming = incoming
        self.estimated = estimated
        self.check_authority = None
        self.observation = observation
        self.session = ref(session)

    def admit(self, incoming, route):
        snapshot = self.observation
        session = self.session()
        if (snapshot is not None and incoming is self.incoming and snapshot.route == route
                and session is not None and session._tool_evidence_epoch is self.epoch):
            session._host_observations.setdefault(id(incoming.record), snapshot)
            incoming.first_clock = (route, snapshot.stamp)


class HostObservations:
    def __init__(self, route, stamp, activity, facts, contexts):
        self.activity = activity
        self.facts = facts
        self.contexts = dict(contexts)
        self.now = Observation(route, stamp, _digest(activity), _digest(facts),
                               tuple((slot, _digest(text)) for slot, text in contexts))

    def _at(self, snapshot, displayed, previous_stamp):
        # The source texts can contain private reminders, send instructions or
        # identity hints. A digest alone cannot supply them. An exact match
        # permits only this fresh, currently visible text to occupy its old slot.
        lines = []
        if previous_stamp is None:
            lines.append('本条私聊的宿主报时：' + snapshot.stamp +
                         '。同日只报时分；未变的报时和宿主说明沿用。')
        elif snapshot.stamp != previous_stamp:
            same_day = snapshot.stamp[:10] == previous_stamp[:10] and snapshot.stamp[16:] == previous_stamp[16:]
            stamp = snapshot.stamp[11:16] if same_day else snapshot.stamp
            lines.append('宿主报时 ' + stamp + '。')
        for field in ('activity', 'facts'):
            seen = getattr(snapshot, field)
            if seen is None or seen != getattr(self.now, field):
                displayed.discard(field)
            elif field not in displayed:
                lines.append(getattr(self, field))
                displayed.add(field)
        return [{'role':'system', 'content':'\n'.join(lines)}] if lines else []

    def _contexts_at(self, snapshot, displayed):
        # Keep channel/identity/navigation as separate system messages. Each
        # indexed producer must have emitted the same text on this request.
        rows = []
        previous = dict(snapshot.contexts)
        for slot, digest in self.now.contexts:
            if previous.get(slot) != digest:
                displayed.discard(slot)
            elif slot not in displayed:
                rows.append({'role':'system', 'content':self.contexts[slot]})
                displayed.add(slot)
        return rows

    def _sequence(self, snapshot, displayed, previous_stamp):
        return [*self._at(snapshot, displayed, previous_stamp), *self._contexts_at(snapshot, displayed)]

    def project(self, past, incoming, ledger, *, first_input):
        """Chronological reports and live updates, with unchanged observations implicit."""
        inserts = {}
        displayed = set()
        previous_stamp = None
        def at(snapshot):
            nonlocal previous_stamp
            rows = self._sequence(snapshot, displayed, previous_stamp)
            previous_stamp = snapshot.stamp
            return rows

        for pos, snapshot in past:
            rows = at(snapshot)
            if rows:
                inserts[pos] = rows

        snapshot = ledger.get(id(incoming.record))
        if snapshot is not None and snapshot.route != self.now.route:
            snapshot = None
        if snapshot is None:
            if not first_input:
                return inserts, [], at(self.now)
            return inserts, at(self.now), []

        initial = at(snapshot)
        if snapshot == self.now:
            return inserts, initial, []
        return inserts, initial, self._updates(snapshot, displayed)

    def _updates(self, snapshot, displayed):
        updates = []
        if snapshot.stamp != self.now.stamp:
            updates.append('现在是 ' + self.now.stamp + '。')
        for field in ('activity', 'facts'):
            if getattr(snapshot, field) != getattr(self.now, field) and getattr(self, field):
                # State may have changed since an action in this batch. Keep
                # its fresh replacement after all subsequent results.
                updates.append(getattr(self, field))
        return [*({'role':'system','content':s} for s in updates), *self._contexts_at(self.now, displayed)]

class OrderedHostObservations(HostObservations):
    """Render admitted facts in the relative order of their live host producers."""

    def _updates(self, snapshot, displayed):
        # Publish newly visible values after the receipt in the same order
        # used on the initial request. Unchanged sources stay at their anchors.
        return self._sequence(self.now, displayed, snapshot.stamp)

    def _sequence(self, snapshot, displayed, previous_stamp):
        rows = []
        old_contexts = dict(snapshot.contexts)
        for slot in self.producer_order:
            text = ''
            if slot == 'facts':
                fresh = snapshot.facts is not None and snapshot.facts == self.now.facts
                show_body = fresh and slot not in displayed
                clock = '现在是 ' + snapshot.stamp + '。' if snapshot.stamp != previous_stamp else ''
                body = self.facts.partition('\n')[2] if show_body else ''
                if body or clock:
                    text = '\n'.join(part for part in (self.heading, clock, body) if part)
                if fresh:
                    displayed.add(slot)
                else:
                    displayed.discard(slot)
            else:
                if slot == 'activity':
                    old, current, value = snapshot.activity, self.now.activity, self.activity
                else:
                    old, current, value = old_contexts.get(slot), dict(self.now.contexts)[slot], self.contexts[slot]
                if old is None or old != current:
                    displayed.discard(slot)
                elif slot not in displayed:
                    text = value
                    displayed.add(slot)
            if text:
                rows.append({'role':'system', 'content':text})
        return rows
