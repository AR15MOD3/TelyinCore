"""Bounded metadata-only memory sink; no disk or UI endpoint."""
from collections import deque
import copy, threading, secrets
ENUMS = {'comparison': {'no_previous', 'settings_changed', 'item_changed', 'item_added_or_removed',
    'unchanged_through_item_limit', 'identical'}, 'comparison_order': {'transport_stream_open'},
    'role': {'system', 'user', 'assistant', 'tool', 'other'}}
INTS = {'sequence', 'previous_sequence', 'retained_partitions', 'item_limit', 'block_bytes',
    'identical_leading_items', 'first_changed_item', 'item_common_utf8_bytes_at_least',
    'old_item_bytes', 'new_item_bytes'}
BOOLS = {'evicted_other_partition', 'other_settings_changed', 'item_common_prefix_capped'}
FIELDS = {'model', 'tools', 'tool_choice', 'temperature', 'top_p', 'stream', 'max_tokens',
    'max_output_tokens', 'thinking'}

class DiagnosticBuffer:

    def __init__(self):
        self.instance_id = secrets.token_hex(16)
        self._rows = deque(maxlen=64)
        self._lock = threading.RLock()

    def append(self, row):
        if not isinstance(row, dict):
            raise ValueError('invalid diagnostic')
        required = {'partition', 'sequence', 'previous_sequence', 'comparison_order',
                    'comparison', 'retained_partitions', 'evicted_other_partition',
                    'item_limit', 'block_bytes', 'provider_token_prefix'}
        if not required.issubset(row):
            raise ValueError('missing diagnostic fields')
        for key, value in row.items():
            if key == 'partition':
                if (
                    not isinstance(value, str) or len(value) != 64 or
                        any((c not in '0123456789abcdef' for c in value))
                ):
                    raise ValueError('invalid partition')
            elif key in ENUMS:
                if value not in ENUMS[key]:
                    raise ValueError('invalid enum')
            elif key in INTS:
                if value is None and key == 'previous_sequence':
                    continue
                if type(value) is not int or value < 0:
                    raise ValueError('invalid count')
            elif key in BOOLS:
                if type(value) is not bool:
                    raise ValueError('invalid flag')
            elif key == 'provider_token_prefix':
                if value is not None:
                    raise ValueError('no token claim')
            elif key == 'known_changed_settings':
                if (
                    not isinstance(value, list) or len(value) > len(FIELDS) or
                        any((v not in FIELDS for v in value))
                ):
                    raise ValueError('invalid fields')
            else:
                raise ValueError('unknown diagnostic key')
        with self._lock:
            self._rows.append(copy.deepcopy(row))

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(list(self._rows))
