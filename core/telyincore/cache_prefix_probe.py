"""Isolated diagnostic candidate. No transport hooks, persistence or network."""
from collections import OrderedDict
import hashlib, hmac, json, secrets, threading
BLOCK = 256
BLOCKS = 64
ITEMS = 16
PARTITIONS = 16
MAX_CHARS = 1024 * 1024
MAX_NODES = 8192
MAX_DEPTH = 32

class DiagnosticBudgetError(ValueError):
    pass

def within_budget(value):
    nodes = 0
    chars = 0

    def visit(v, depth):
        nonlocal nodes, chars
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise DiagnosticBudgetError('budget')
        if isinstance(v, str):
            chars += len(v)
            if chars > MAX_CHARS:
                raise DiagnosticBudgetError('budget')
        elif isinstance(v, dict):
            for k, item in v.items():
                visit(k, depth + 1)
                visit(item, depth + 1)
        elif isinstance(v, (list, tuple)):
            for item in v:
                visit(item, depth + 1)
        elif isinstance(v, int) and v.bit_length() > 64:
            raise DiagnosticBudgetError('budget')
    visit(value, 0)

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode()

class PrefixProbe:

    def __init__(self):
        self._key = secrets.token_bytes(32)
        self._previous = OrderedDict()
        self._lock = threading.RLock()
        self._sequence = 0

    def digest(self, value):
        return hmac.new(self._key, canonical(value), hashlib.sha256).hexdigest()

    def item(self, value):
        data = canonical(value)
        running = hmac.new(self._key, digestmod=hashlib.sha256)
        blocks = []
        for i in range(0, min(len(data), BLOCK * BLOCKS), BLOCK):
            running.update(data[i:min(i + BLOCK, BLOCK * BLOCKS)])
            blocks.append(running.hexdigest())
        role = value.get('role') if isinstance(value, dict) else None
        return {'digest': hmac.new(self._key, data, hashlib.sha256).hexdigest(), 'bytes': len(data),
            'blocks': blocks, 'truncated': len(data) > BLOCK * BLOCKS,
            'role': role if role in ('system', 'user', 'assistant', 'tool') else 'other'}

    def observe(self, provider, body, labels):
        with self._lock:
            return self._observe(provider, body, labels)

    def _observe(self, provider, body, labels):
        within_budget([provider, body.get('model'), labels])
        partition = self.digest([provider, body.get('model'), labels])
        fieldnames = ('model', 'tools', 'tool_choice', 'temperature', 'top_p', 'stream',
            'max_tokens', 'max_output_tokens', 'thinking')
        settings = {k: v for k, v in body.items() if k not in ('input', 'messages')}
        values = body.get('input', body.get('messages', []))
        if not isinstance(values, list):
            raise ValueError('input must be list')
        within_budget([settings, values[:ITEMS]])
        current = {'settings': self.digest(settings), 'other_settings': self.digest({k: v for k,
            v in settings.items() if k not in fieldnames}),
            'fields': {k: self.digest([k in settings, settings.get(k)]) for k in fieldnames},
            'items': [self.item(v) for v in values[:ITEMS]], 'count': len(values)}
        self._sequence += 1
        current['sequence'] = self._sequence
        previous = self._previous.pop(partition, None)
        evicted = False
        if len(self._previous) >= PARTITIONS:
            self._previous.popitem(last=False)
            evicted = True
        self._previous[partition] = current
        result = {'partition': partition, 'sequence': current['sequence'],
            'previous_sequence': previous['sequence'] if previous else None,
            'comparison_order': 'transport_stream_open', 'comparison': 'no_previous',
            'retained_partitions': len(self._previous), 'evicted_other_partition': evicted,
            'item_limit': ITEMS, 'block_bytes': BLOCK, 'provider_token_prefix': None}
        if previous is None:
            return result
        if previous['settings'] != current['settings']:
            fields = [k for k in fieldnames if previous['fields'][k] != current['fields'][k]]
            result.update(comparison='settings_changed', known_changed_settings=fields,
                other_settings_changed=previous['other_settings'] != current['other_settings'])
            return result
        common = 0
        for old, new in zip(previous['items'], current['items']):
            if old['digest'] == new['digest']:
                common += 1
                continue
            shared = 0
            for a, b in zip(old['blocks'], new['blocks']):
                if a != b:
                    break
                shared += 1
            bound = min(shared * BLOCK, old['bytes'], new['bytes'])
            result.update(comparison='item_changed', identical_leading_items=common,
                first_changed_item=common, role=new['role'], item_common_utf8_bytes_at_least=bound,
                item_common_prefix_capped=shared == BLOCKS, old_item_bytes=old['bytes'],
                new_item_bytes=new['bytes'])
            return result
        result.update(identical_leading_items=common)
        if previous['count'] != current['count'] and common < ITEMS:
            result.update(comparison='item_added_or_removed', first_changed_item=common)
        elif max(previous['count'], current['count']) > ITEMS:
            result.update(comparison='unchanged_through_item_limit')
        else:
            result.update(comparison='identical')
        return result
