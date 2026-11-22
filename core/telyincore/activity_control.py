"""Host scheduling decisions, separate from envelope content and speech.

Idle permits autonomous work. Operator pause does not. An event's urgency can
never reopen that gate. Background model work yields at generation boundaries;
already-started writes retain their existing transaction/cleanup ownership.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from enum import StrEnum
from weakref import WeakValueDictionary

from .bus import default_trigger
from .events import EventEnvelope


class DeliveryMode(StrEnum):
    ATTACH = 'attach'
    COLLECT = 'collect'
    READY = 'ready'
    YIELD = 'yield'
    INTERRUPT = 'interrupt'


LABELS = {
    DeliveryMode.ATTACH: '随附', DeliveryMode.COLLECT: '汇集唤醒',
    DeliveryMode.READY: '即时唤醒', DeliveryMode.YIELD: '暂歇转接',
    DeliveryMode.INTERRUPT: '中断转接',
}


MODE_MEANINGS = {
    DeliveryMode.ATTACH: "跟随其他事件一同投递",
    DeliveryMode.COLLECT: "等待同批事件汇集",
    DeliveryMode.READY: "直接排入可处理的批次",
    DeliveryMode.YIELD: "请求当前轮在安全边界交接",
    DeliveryMode.INTERRUPT: "请求中断指定轮次，等待清理后交接",
}


class BackgroundDeferred(asyncio.CancelledError):
    """A safe boundary declined new background model work during shutdown."""


class ActivityControl:
    def __init__(self, *, bus, control, paused, processing, stopping, record=lambda value:None):
        self.bus, self.control = bus, control
        self.paused, self.processing, self.stopping = paused, processing, stopping
        self.record = record
        self._changed = asyncio.Event()
        self.background_waiters = 0
        self.background_running = 0
        self.foreground_waiters = 0
        self.foreground_reserved = False
        self._transfers = []
        self._last_transfer = False
        self._admitted_modes = WeakValueDictionary()

    def current_state(self):
        if self.stopping():
            state = 'stopping'
        elif self.paused():
            state = 'paused'
        elif self.processing():
            state = 'active'
        else:
            state = 'idle'
        return state

    def status(self):
        return {'state':self.current_state(), 'background_waiters':self.background_waiters,
                'background_running':self.background_running,
                'idle_is_paused':False,
                **({'local_observations': self.observation_status()}
                   if hasattr(self, 'observation_status') else {})}

    def changed(self):
        self._changed.set()

    @property
    def has_transfer(self):
        return bool(self._transfers)

    def push(self, event, *, trigger=None, window_ms=None):
        """Existing ingress adapter; arrival alone never requests preemption."""
        if self.stopping():
            raise RuntimeError('activity admission is stopping')
        self.bus.push(event, trigger=trigger, window_ms=window_ms)
        self._remember_mode(event, {"flush": DeliveryMode.READY, "debounce": DeliveryMode.COLLECT,
                                    "piggyback": DeliveryMode.ATTACH}[trigger or default_trigger(event)])
        self.changed()

    def _remember_mode(self, event, mode):
        if isinstance(event, EventEnvelope):
            self._admitted_modes[id(event), mode] = event

    def model_hint(self, originals):
        """Report only host admissions belonging to this exact current audience."""
        modes = [f"{LABELS[mode]}（{MODE_MEANINGS[mode]}）" for mode in DeliveryMode if any(
            self._admitted_modes.get((id(event), mode)) is event for event in originals)]
        if not modes:
            return ""
        state = {"stopping":"正在休止", "paused":"已暂停", "active":"正在处理当前批次",
                 "idle":"静候"}[self.current_state()]
        return ("〔宿主调度〕当前状态：" + state + "。本批投递方式：" + "、".join(modes)
                + "。投递方式只说明事件怎样进入本批；是否查阅、回应或静默由你判断。")

    def select_batch(self, batches):
        """Prefer an explicitly requested transfer, keeping an audience intact.

        After one transfer, give the oldest waiting batch a turn. A hot urgent
        producer cannot indefinitely starve the finite normal snapshot.
        """
        if not batches:
            raise ValueError('no pending activity batch')
        index = 0
        priority = False
        if not self._last_transfer:
            for i, batch in enumerate(batches):
                if any(any(event is wanted for wanted in self._transfers) for event in batch):
                    index, priority = i, True
                    break
        chosen = batches.pop(index)
        self._transfers = [wanted for wanted in self._transfers
                           if not any(wanted is event for event in chosen)]
        self._last_transfer = priority
        return chosen

    async def wait_paused(self):
        """Operator gate only; waiting without input reserves no model slot."""
        while True:
            self._changed.clear()
            if self.stopping():
                return False
            if not self.paused():
                return True
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=.25)
            except TimeoutError:
                pass

    async def wait_foreground_admission(self):
        """Leave queued events unconsumed while paused or settling generation."""
        self.foreground_waiters += 1
        try:
            while True:
                self._changed.clear()
                if self.stopping():
                    return False
                if not self.paused() and not self.background_running:
                    self.foreground_reserved = True
                    return True
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=.25)
                except TimeoutError:
                    pass
        finally:
            self.foreground_waiters -= 1

    def finish_foreground(self):
        self.foreground_reserved = False
        self.changed()

    def submit(self, event, *, mode=DeliveryMode.COLLECT, round_id=None):
        """Host API. No mode/round identity is inferred from message text/meta.

        Cancellation targets a supplied current round, never a later successor.
        Queue admission happens first: rejected events cannot interrupt work.
        This does not grant model/speech access to the admitted event.
        """
        mode = DeliveryMode(mode)
        if self.stopping():
            raise RuntimeError('activity admission is stopping')
        if mode in (DeliveryMode.YIELD, DeliveryMode.INTERRUPT):
            snapshot = self.control.status()
            if not round_id or snapshot['round_id'] != round_id or not snapshot['processing']:
                raise ValueError('transfer requires the current round identity')
            if len(self._transfers) >= 64:
                raise RuntimeError('too many pending activity transfers')
        trigger = {DeliveryMode.ATTACH:'piggyback', DeliveryMode.COLLECT:'debounce'}.get(mode, 'flush')
        self.bus.push(event, trigger=trigger)
        self._remember_mode(event, mode)
        transferred = False
        if not self.paused():
            if mode == DeliveryMode.YIELD:
                transferred = self.control.yield_after_round(round_id)['accepted']
            elif mode == DeliveryMode.INTERRUPT:
                transferred = self.control.interrupt(round_id)['accepted']
        if transferred:
            self._transfers.append(event)
        self.changed()
        return {'queued':True, 'mode':mode.value, 'label':LABELS[mode],
                'transfer_requested':transferred, 'processing_allowed':not self.paused(),
                'speech_required':False}

    async def before_background_generation(self):
        """Wait without occupying a model slot; never pause a file commit."""
        self.background_waiters += 1
        try:
            while True:
                self._changed.clear()
                if self.stopping():
                    raise BackgroundDeferred('background generation deferred during shutdown')
                if (not self.paused() and not self.processing() and not self.foreground_waiters
                        and not self.foreground_reserved and not self.background_running):
                    return
                # The existing operator gate can be changed by legacy adapters
                # without a notification. Bounded fallback observes it as well.
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=.25)
                except TimeoutError:
                    pass
        finally:
            self.background_waiters -= 1

    @asynccontextmanager
    async def background_generation(self):
        """A started generation settles before a foreground generation starts."""
        await self.before_background_generation()
        self.background_running += 1
        try:
            yield
        finally:
            self.background_running -= 1
            self.changed()

    def heartbeat_available(self):
        """Cheap periodic opportunity: never build a backlog behind real work."""
        return (not self.stopping() and not self.paused() and not self.processing()
                and not self.background_running and not self.bus.pending())
