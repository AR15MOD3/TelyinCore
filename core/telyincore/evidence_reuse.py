"""Bounded process-local reuse of fully validated, effect-free assessments.

This is not provider KV caching. Keys cover the complete request and binding;
new evidence is never replaced by a summary or a similarity match.
"""
from collections import OrderedDict
from dataclasses import asdict, is_dataclass
import hashlib
import json
import time


def request_key(config, messages, *, binding=()):
    base = config.peek() if callable(getattr(config, "peek", None)) else config
    if not is_dataclass(base):
        return None  # Unknown custom configurations cannot establish equivalence.
    values = getattr(config, "values", None)
    source = getattr(config, "_source", config)
    identity = getattr(source, "_follow_identity", None)
    try:
        payload = [asdict(base), values() if callable(values) else None,
                   identity() if callable(identity) else None, messages, binding]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(raw).hexdigest()


class EvidenceReuse:
    def __init__(self, *, capacity=16, ttl=300, clock=time.monotonic):
        self._entries = OrderedDict()
        self._capacity, self._ttl, self._clock = capacity, ttl, clock

    def get(self, key):
        now = self._clock()
        for old, (at, _) in tuple(self._entries.items()):
            if now - at >= self._ttl:
                del self._entries[old]
        if key is None or key not in self._entries:
            return None
        self._entries.move_to_end(key)
        return self._entries[key][1]

    def put(self, key, text):
        if key is None:
            return
        self._entries[key] = (self._clock(), text)
        self._entries.move_to_end(key)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)
