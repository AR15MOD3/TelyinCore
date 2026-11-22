"""Placement of freshly rendered host context within a single private chat batch."""
from hashlib import sha256
from ipaddress import ip_address, ip_network
import re
from urllib.parse import urlsplit


_LAN_NETWORKS = tuple(map(ip_network, ('10.0.0.0/8', '172.16.0.0/12',
                                      '192.168.0.0/16', 'fc00::/7')))


def local_chat(config):
    return local_chat_url(getattr(config, 'kind', None), getattr(config, 'base_url', None))


def local_chat_url(kind, base_url):
    if kind != 'chat':
        return False
    try:
        url = urlsplit(base_url)
        if url.scheme not in ('http', 'https') or url.username is not None or url.password is not None:
            return False
        if url.hostname == 'localhost':
            return True
        address = ip_address(url.hostname)
        address = getattr(address, 'ipv4_mapped', None) or address
        return address.is_loopback or any(address in subnet for subnet in _LAN_NETWORKS)
    except (TypeError, ValueError, AttributeError):
        return False


def verified_local_qwen(kind, base_url, model):
    if model != "iq3_xxs" or not local_chat_url(kind, base_url):
        return False
    try:
        url = urlsplit(base_url)
        return (url.port in (47831, 47832) and url.path.rstrip("/") == "/v1"
                and not url.query and not url.fragment)
    except ValueError:
        return False


def official_deepseek_chat(config):
    return official_deepseek_url(getattr(config, 'kind', None), getattr(config, 'base_url', None))


def official_deepseek_url(kind, base_url):
    if kind != 'chat':
        return False
    try:
        url = urlsplit(base_url)
        return (url.scheme == 'https' and url.hostname == 'api.deepseek.com'
                and url.port in (None, 443) and url.username is None and url.password is None)
    except (AttributeError, TypeError, ValueError):
        return False


class AnchoredHost(dict):
    """Fresh host instruction inserted between visible records on an official Chat request."""

    def __init__(self, content, record):
        super().__init__(role='system', content=content)
        self.record = record


class IncomingContext:
    """One visible input, its fingerprints and one admitted host clock observation."""

    def __init__(self, record):
        self.record = record
        self.fingerprints = None
        self.layout_route = None
        self.first_clock = None

    def admit_clock(self, messages, config):
        """A size preview or a failed/cancelled generation cannot confirm an observation."""
        from .host_observations import ObservationRequest
        route = (getattr(config, 'base_url', None), getattr(config, 'model', None))
        if isinstance(messages, ObservationRequest) and messages.observation is not None:
            messages.admit(self, route)
            return
        if self.first_clock is None and self.fingerprints is not None:
            for row in reversed(messages):
                if isinstance(row, _FirstClock) and row.incoming is self:
                    if row.route == (getattr(config, 'base_url', None), getattr(config, 'model', None)):
                        self.first_clock = (row.route, row.stamp)
                    return


_HOST_CLOCK = re.compile(r'现在是 ([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}（周[一二三四五六日]）)。')


class _FirstClock(dict):
    """Only role/content go over the wire. This marker is never persisted."""

    def __init__(self, incoming, route, stamp):
        super().__init__(role='system', content=f'本批首次宿主报时为 {stamp}。本轮若未另列当前时间，当前仍是这一分钟。')
        self.incoming = incoming
        self.route = route
        self.stamp = stamp


class BatchTail(list):
    """A plain ordered tail unless its exact input record passed the current view."""

    def __init__(self, rows, incoming, guard_start, guard_end, *, remember=True,
                 clock_facts_at=None, clock_route=None, layout_route=None,
                 provider_replay=False, pin_boundary=False, private_rule=None, observation_slots=None, observation_config=None):
        super().__init__(rows)
        self.incoming = incoming
        self.guard = range(guard_start, guard_end)
        self.remember = remember
        self.clock_facts_at = clock_facts_at
        self.clock_route = clock_route
        self.layout_route = layout_route
        self.provider_replay = provider_replay
        self.pin_boundary = pin_boundary
        self.private_rule = private_rule
        self.observation_slots = observation_slots
        self.observation_config = observation_config

    def for_preview(self):
        return BatchTail(self, self.incoming, self.guard.start, self.guard.stop, remember=False,
                         clock_facts_at=self.clock_facts_at, clock_route=self.clock_route,
                         layout_route=self.layout_route, provider_replay=self.provider_replay,
                         private_rule=self.private_rule, observation_slots=self.observation_slots,
                         observation_config=self.observation_config)

    def current_observations_only(self):
        # All rows came from this request's original host calls. Rebuilding the
        # scoped projection must not ask a provider for fresh authority or data.
        return BatchTail(self, self.incoming, self.guard.start, self.guard.stop,
                         remember=self.remember, clock_facts_at=self.clock_facts_at,
                         clock_route=self.clock_route, layout_route=self.layout_route,
                         pin_boundary=self.pin_boundary, private_rule=self.private_rule,
                         provider_replay=self.provider_replay)

    def dated_observations(self):
        # Use only slots supplied by the host scheduler and clock/facts
        # provider. Other rows may contain current grants or retrieved evidence.
        if self.observation_slots is None:
            return None
        activity_at, facts_at, context_slots = self.observation_slots
        content = self[facts_at]['content']
        heading, _, body = content.partition('\n')
        clock, _, facts = body.partition('\n')
        match = _HOST_CLOCK.fullmatch(clock)
        if not match:
            return None
        if any(not isinstance(self[i], dict) or self[i].keys() != {'role', 'content'}
               or self[i]['role'] != 'system' or type(self[i]['content']) is not str
               for _, i in context_slots):
            return None
        from .host_observations import HostObservations, OrderedHostObservations
        observation_type = OrderedHostObservations if self.provider_replay else HostObservations
        live = observation_type(self.layout_route, match[1],
                                self[activity_at]['content'] if activity_at is not None else '',
                                heading + '\n' + facts if facts else '',
                                [(key, self[i]['content']) for key, i in context_slots])
        if self.provider_replay:
            slots = [(pos, key) for key, pos in context_slots]
            if activity_at is not None:
                slots.append((activity_at, 'activity'))
            slots.append((facts_at, 'facts'))
            live.producer_order = tuple(key for _, key in sorted(slots, key=lambda slot:slot[0]))
            live.heading = heading
        removed = {facts_at, *(i for _, i in context_slots)}
        if activity_at is not None:
            removed.add(activity_at)
        rows = [row for i, row in enumerate(self) if i not in removed]
        guard_start = self.guard.start - sum(i < self.guard.start for i in removed)
        guard_end = self.guard.stop - sum(i < self.guard.stop for i in removed)
        return live, BatchTail(rows, self.incoming, guard_start, guard_end,
                               remember=self.remember, layout_route=self.layout_route,
                               private_rule=self.private_rule)

    def _clock_rows(self, clock, first_input):
        current = {'role': 'system', 'content': clock}
        match = _HOST_CLOCK.fullmatch(clock) if self.clock_route is not None else None
        if not match:
            return [current]
        now = match[1]
        observed = self.incoming.first_clock
        if observed is None:
            return [_FirstClock(self.incoming, self.clock_route, now)] if first_input else [current]
        if observed[0] != self.clock_route:
            return [current]
        first = _FirstClock(self.incoming, self.clock_route, observed[1])
        return [first] if observed[1] == now else [first, current]

    def partition(self, *, first_input):
        # Guards refer to tool receipts and the current budget. They always
        # follow the latest tool results. Move only a consecutive prefix of
        # plain host text that still matches the first input at this position.
        # A size preview may run before the first generation. While no model
        # output has been appended, the latest projection supplies the baseline.
        initial = None if first_input else self.incoming.fingerprints
        if not first_input and (initial is None or self.incoming.layout_route != self.layout_route):
            return (), self
        rows = self
        if self.clock_facts_at is not None:
            # Only a provider explicitly declaring a leading host clock can
            # enter this path. Split its freshly produced text, never an old
            # message or a colon/date pattern found in user or tool content.
            pos = self.clock_facts_at
            content = self[pos]['content']
            heading, _, body = content.partition('\n')
            clock, separator, facts = body.partition('\n')
            if separator and clock and facts:
                rows = [*self[:pos], {'role': 'system', 'content': heading + '\n' + facts},
                        *self[pos+1:], *self._clock_rows(clock, first_input)]
        positions = []
        signatures = []
        for i, row in enumerate(rows):
            if i in self.guard:
                if first_input:
                    break  # Keep initial guard precedence and exact row order.
                continue
            if (not isinstance(row, dict) or row.keys() != {'role', 'content'}
                    or row['role'] != 'system' or not isinstance(row['content'], str)):
                break
            signature = sha256(row['content'].encode('utf-8', 'surrogatepass')).digest()
            if initial is not None and (len(signatures) >= len(initial)
                                        or signature != initial[len(signatures)]):
                break
            positions.append(i)
            signatures.append(signature)
        if first_input and self.remember:
            self.incoming.fingerprints = tuple(signatures)
            self.incoming.layout_route = self.layout_route
        anchored = set(positions)
        return ([rows[i] for i in positions], [row for i, row in enumerate(rows) if i not in anchored])
