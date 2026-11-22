"""Bounded short-substring hints keyed by immutable original-byte identities."""
from collections import OrderedDict
import sys


def _character_mask(text):
    value = 0
    for code in set(map(ord, text)):
        mixed = code ^ (code >> 11)
        value |= ((1 << (mixed * 2654435761 % 1024)) |
                  (1 << (((mixed >> 9) ^ (mixed * 2246822519)) % 1024)))
    return value


class ShortTerms:
    MAX_BYTES = 1024 * 1024
    MAX_ENTRIES = 2048
    MISS_SLOTS = 4

    def __init__(self):
        self.entries = OrderedDict()
        self.bytes = 0
        self._last_query = None
        self._last_mask = 0

    @staticmethod
    def _summarize(blocks, query):
        needle = query.casefold()
        overlap = len(needle) - 1
        tail = ''
        value = 0
        found = False
        for block in blocks:
            folded = block.casefold()
            if not found:
                candidate = tail + folded
                found = needle in candidate
                tail = candidate[-overlap:] if overlap else ''
            if value.bit_count() < 1024:
                value |= _character_mask(folded)
            if found and value.bit_count() == 1024:
                break
        return value, found

    @staticmethod
    def mask(blocks):
        value = 0
        for block in blocks:
            value |= _character_mask(block.casefold())
            if value.bit_count() == 1024:
                break
        return value

    def maybe(self, key, query, blocks):
        if not 0 < len(query) <= 2:
            return True
        if key in self.entries:
            value, _, misses = self.entries[key]
            self.entries.move_to_end(key)
            if query in misses:
                return False
            if query != self._last_query:
                self._last_mask = self.mask((query,))
                self._last_query = query
            return value & self._last_mask == self._last_mask

        # During the already necessary first read, also check adjacency across
        # decoded blocks. A folded hit still needs the caller's exact matcher
        # (event lower() in particular distinguishes final sigma).
        value, possible = self._summarize(blocks(), query)
        size = sys.getsizeof(key) + sys.getsizeof(value) + 192 + sys.getsizeof((None,) * self.MISS_SLOTS)
        size += self.MISS_SLOTS * sys.getsizeof(chr(0x10000) * 2)
        if size <= self.MAX_BYTES and self.MAX_ENTRIES > 0:
            while self.entries and (self.bytes + size > self.MAX_BYTES or len(self.entries) >= self.MAX_ENTRIES):
                _, (_, old_size, _) = self.entries.popitem(last=False)
                self.bytes -= old_size
            self.entries[key] = value, size, (() if possible else (query,))
            self.bytes += size
        return possible

    def remember_miss(self, key, query):
        if 0 < len(query) <= 2 and key in self.entries:
            mask, size, misses = self.entries[key]
            if query not in misses:
                self.entries[key] = mask, size, (*misses[-(self.MISS_SLOTS - 1):], query)
