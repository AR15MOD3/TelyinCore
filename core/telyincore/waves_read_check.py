"""Two fixed operator reads through the existing live Dispatcher and ledger.

No model turn, fabricated channel event, arbitrary tool/arguments or new host.
"""
import asyncio
import copy
import hashlib
import json
import re
import threading
import uuid
from .stream import ToolCall
from .tools import ToolTag


class _SerialRunner:
    def __init__(self, check, inner=None):
        self._check = check
        self._inner = check._inner if inner is None else inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def with_inner_guard(self, guard):
        factory = getattr(self._inner, 'with_inner_guard', None)
        guarded = factory(guard) if callable(factory) else guard(self._inner)
        return _SerialRunner(self._check, guarded)

    async def __call__(self, calls):
        check = self._check
        check._waiting += 1
        try:
            async with check._gate:
                return await self._inner(calls)
        finally:
            check._waiting -= 1

    def receipt_shutdown_state(self):
        check = self._check
        state = check._ledger.receipt_shutdown_state()
        return dict(pending=bool(state['pending'] or check._waiting or check._active),
                    incomplete=bool(state['incomplete']))


class WavesReadCheck:
    _plans = {
        'status': ('waves_status', {}),
        # A nonempty topic takes /api/commands, never /api/chat or card rendering.
        'help': ('waves_help', {'topic': '帮助', 'card_only': False}),
    }

    def __init__(self, *, bot, pid, dispatcher, runner, ledger, can_start):
        self.bot, self.pid = bot, pid
        self._dispatcher, self._inner, self._can_start = dispatcher, runner, can_start
        self._ledger = ledger
        self._gate = asyncio.Lock()
        self._lock = threading.RLock()
        self._records = {}
        self._used = set()
        self._active = False
        self._waiting = 0
        self._owner_loop = None
        self._closed = False
        self.runner = _SerialRunner(self)

    def bind_loop(self, loop):
        if self._owner_loop is not None and self._owner_loop is not loop:
            raise ValueError('read check owner loop changed')
        self._owner_loop = loop

    def close(self):
        with self._lock:
            self._closed = True

    def snapshot(self):
        with self._lock:
            return {'available': True, 'active': self.bot, 'pid': self.pid,
                    'budget': 2, 'used': len(self._used), 'pending': self._active,
                    'admission_closed': self._closed,
                    'checks': copy.deepcopy(list(self._records.values()))}

    async def __call__(self, body):
        if self._closed or self._owner_loop is not asyncio.get_running_loop():
            return 503, {'error': 'read_check_runtime_unavailable'}
        if (type(body) is not dict or set(body) != {'active', 'pid', 'check', 'request_id'}
                or not isinstance(body['active'], str) or type(body['pid']) is not int
                or not isinstance(body['check'], str)
                or body['check'] not in self._plans
                or not isinstance(body['request_id'], str)
                or re.fullmatch('[0-9a-f]{32}', body['request_id']) is None):
            return 400, {'error': 'invalid_fixed_read_check'}
        if body['active'] != self.bot or body['pid'] != self.pid:
            return 409, {'error': 'running_bot_changed'}
        request_id, operation = body['request_id'], body['check']
        with self._lock:
            existing = self._records.get(request_id)
            if existing is not None:
                if existing['check'] != operation:
                    return 409, {'error': 'read_check_id_conflict'}
                return (202 if existing['state'] == 'pending' else 200), copy.deepcopy(existing)
            if operation in self._used or len(self._used) >= 2:
                return 409, {'error': 'read_check_already_consumed'}
        # No wait queue for operator reads. Normal tool calls share the gate,
        # so an incoming conversation is serialized, never rejected by overlap.
        if self._gate.locked() or self._dispatcher._active_batch or not self._can_start():
            return 409, {'error': 'tool_runtime_busy'}
        async with self._gate:
            name, arguments = self._plans[operation]
            tool = self._dispatcher._table.get(name)
            if (tool is None or set(tool.tags or ()) != {ToolTag.READ}
                    or tool.effect != 'immediate' or tool.ends_turn or tool.barrier_after):
                return 503, {'error': 'expected_read_tool_unavailable'}
            if getattr(self._ledger, 'receipt_ledger_incomplete', None) is not False:
                return 503, {'error': 'receipt_ledger_unavailable_or_incomplete'}
            call = ToolCall(call_id='operator_read_' + uuid.uuid4().hex, name=name,
                            arguments=json.dumps(arguments, ensure_ascii=False))
            record = dict(request_id=request_id, check=operation, tool=name,
                          call_id=call.call_id, state='pending', active=self.bot, pid=self.pid)
            with self._lock:
                self._used.add(operation)
                self._records[request_id] = record
                self._active = True
            try:
                # The real runner establishes Invocation and all lifecycle /
                # permission / parameter / receipt gates. Never set it here.
                result = await self._inner((call,))
                receipts = tuple(getattr(result, 'receipts', ()) or ())
                if len(receipts) != 1 or receipts[0].call_id != call.call_id:
                    raise RuntimeError('read check receipt pairing failed')
                receipt = receipts[0]
                text = str(receipt.text)
                stable = self._dispatcher._table.get(name) is tool
                ledger_ok = self._ledger.receipt_ledger_incomplete is False
                semantic = self._summary(operation, text)
                with self._lock:
                    record.update(state='completed', stage=receipt.stage,
                        receipt_bytes=len(text.encode()), receipt_sha256=hashlib.sha256(text.encode()).hexdigest(),
                        tool_generation_stable=stable, receipt_ledger_complete=ledger_ok,
                        result=semantic, ok=receipt.stage == 'handler' and stable and ledger_ok and semantic['ok'])
            except asyncio.CancelledError:
                with self._lock:
                    record.update(state='cancelled', ok=False, error='read_check_cancelled_no_retry')
                raise
            except Exception:
                with self._lock:
                    record.update(state='failed', ok=False, error='read_check_failed_no_retry')
            finally:
                with self._lock:
                    self._active = False
            return 200, copy.deepcopy(record)

    def _summary(self, operation, text):
        if operation == 'status':
            match = re.fullmatch(r'waves service 在线：Bot=' + re.escape(self.bot)
                + r'；版本=([0-9.]+)；命令 (\d+) 条；自有进程=是', text)
            return {'ok': match is not None,
                    'service_version': match.group(1) if match else None,
                    'registered_commands': int(match.group(2)) if match else None}
        match = re.match(r'与「帮助」有关的命令 (\d+) 条：', text)
        count = int(match.group(1)) if match else 0
        return {'ok': count > 0, 'matching_public_commands': count}
