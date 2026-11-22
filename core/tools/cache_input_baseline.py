"""Offline final-body accounting. No transport wiring, I/O, or native token claims.

Labels must come from a trusted synthetic fixture/provenance map, never guessed
from roles. Unattributed and mixed items remain unknown. JSON sizes include item
wrappers, not model chat-template costs; estimates use the existing heuristic on
canonical JSON, not a provider tokenizer. Do not use this to estimate billing.
"""
from __future__ import annotations

import hashlib
import hmac
import json

from telyincore.tokens import estimate_tokens

LABELS = frozenset(('stable', 'history', 'identity', 'retrieval', 'facts', 'unknown', 'tool_declarations'))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'),
                      sort_keys=True, allow_nan=False).encode('utf-8')


def measure_body(body, *, key: bytes, item_labels=None):
    """Return only counts/enums/HMAC. Does not mutate the caller's body.

    HMAC key must be shared across samples to compare, kept out of reports.
    This is an explicit offline call, NOT a production observer or CLI reader.
    """
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError('ephemeral HMAC key must be at least 32 bytes')
    if not isinstance(body, dict):
        raise ValueError('JSON object required')
    fields = [name for name in ('input', 'messages') if name in body]
    if len(fields) != 1 or not isinstance(body[fields[0]], list):
        raise ValueError('exactly one input/messages list required')
    field = fields[0]
    items = body[field]
    if len(items) > 4096:
        raise ValueError('too many items')
    labels = ['unknown'] * len(items) if item_labels is None else list(item_labels)
    if len(labels) != len(items) or any(type(x) is not str or x not in LABELS for x in labels):
        raise ValueError('invalid attribution labels')
    encoded = canonical(body)
    if len(encoded) > 8 * 1024 * 1024:
        raise ValueError('body too large')
    def digest(value):
        return hmac.new(key, canonical(value), hashlib.sha256).hexdigest()
    groups = {}
    def add(label, value):
        raw = canonical(value)
        row = groups.setdefault(label, {'units': 0, 'canonical_json_bytes': 0,
                                        'estimated_json_tokens': 0})
        row['units'] += 1
        row['canonical_json_bytes'] += len(raw)
        row['estimated_json_tokens'] += estimate_tokens(raw.decode('utf-8'))
    for label, item in zip(labels, items):
        add(label, item)
    if 'tools' in body:
        add('tool_declarations', body['tools'])
    if 'instructions' in body:
        # No provenance for subscription's instructions: never call it persona.
        add('top_level_instructions', body['instructions'])
    settings = {k: v for k, v in body.items()
                if k not in (field, 'tools', 'instructions')}
    add('other_settings', settings)
    assigned = sum(row['canonical_json_bytes'] for row in groups.values())
    return {'schema': 1, 'protocol_shape': field, 'attribution':
            'caller_supplied' if item_labels is not None else 'unknown',
            'canonical_json_bytes': len(encoded), 'groups': groups,
            'json_framing_bytes': len(encoded) - assigned,
            'key_tag': digest('cache-input-baseline-key-domain-v1'),
            'body_hmac': digest(body), 'settings_hmac': digest({
                k: v for k, v in body.items() if k != field}),
            'item_hmacs': [digest(item) for item in items],
            'native_input_tokens': None, 'provider_token_prefix': None,
            'estimated_tokens_are_billing': False}


def compare_reports(before, after):
    """Item-prefix diagnostics, never a provider token-prefix length."""
    if before['key_tag'] != after['key_tag']:
        return {'status': 'incomparable_key', 'common_leading_items': None,
                'provider_token_prefix': None}
    if before['protocol_shape'] != after['protocol_shape']:
        return {'status': 'shape_changed', 'common_leading_items': None,
                'provider_token_prefix': None}
    common = 0
    for left, right in zip(before['item_hmacs'], after['item_hmacs']):
        if left != right:
            break
        common += 1
    return {'status': 'compared', 'common_leading_items': common,
            'settings_equal': before['settings_hmac'] == after['settings_hmac'],
            'provider_token_prefix': None}
