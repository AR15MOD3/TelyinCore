"""Content-addressed token statistics, bounded and independent of visibility.

Only called after the ordinary source reader and live permission filtering.
No source bytes, authorization, document frequency or ranking is cached.
Oversized/high-diversity documents use the existing query-specific scanner.
"""
from collections import Counter, OrderedDict
import hashlib
import sys
import threading

class StatisticsCache:
    MAX_BYTES = 4 * 1024 * 1024
    MAX_DOCUMENT_CHARS = 65536
    MAX_ENTRY_BYTES = 256 * 1024
    MAX_TERMS = 4096
    VERSION = b'core-lexical-units-v1\0'
    def __init__(self):
        self.entries = OrderedDict(); self.bytes = 0; self.lock = threading.RLock()

    def build(self, text):
        from .memory_ranking import _UNITS
        counts = Counter(); previous = None; units = 0; length = 0; size = 0
        def add(token):
            nonlocal size, length
            if token not in counts: size += sys.getsizeof(token) + 96
            counts[token] += 1; length += 1
        for match in _UNITS.finditer(text):
            unit = match.group()
            if unit.isascii(): unit = unit.lower(); add(unit)
            elif not unit.isalnum(): continue
            if previous is not None: add(previous + unit)
            previous = unit; units += 1
            if size > self.MAX_ENTRY_BYTES or len(counts) > self.MAX_TERMS: return None
        if units == 1 and not previous.isascii(): add(previous)
        return length, counts, size + 256

    def get(self, text, wanted, fallback):
        if len(text) > self.MAX_DOCUMENT_CHARS or self.MAX_BYTES <= 0: return fallback(text, wanted)
        digest = hashlib.sha256(self.VERSION)
        for start in range(0, len(text), 4096): digest.update(text[start:start+4096].encode('utf-8','surrogatepass'))
        key = digest.digest()
        with self.lock:
            entry = self.entries.pop(key, None)
            if entry is not None:
                self.entries[key] = entry
            else:
                entry = self.build(text)
                # Remember content that cannot fit, within the same LRU byte
                # budget. The query-specific scan is still required each time.
                if entry is None or entry[2] > self.MAX_BYTES:entry = (None,None,256)
                if entry[2] <= self.MAX_BYTES:
                    while self.entries and self.bytes + entry[2] > self.MAX_BYTES:
                        _, old = self.entries.popitem(last=False); self.bytes -= old[2]
                    self.entries[key] = entry; self.bytes += entry[2]
            if entry is not None and entry[0] is not None:
                length, counts, _ = entry
                return length, Counter({term:counts[term] for term in wanted if term in counts})
        return fallback(text, wanted)

CACHE = StatisticsCache()
