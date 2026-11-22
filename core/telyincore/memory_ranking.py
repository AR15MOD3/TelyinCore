"""Exact lexical ranking without retaining the corpus or its token arrays.

Private temporary files separate matching originals, token statistics and scores. The
relevance floor is applied before top-k, preserving old recency/weight semantics.
Memory is bounded by one document, query terms, file traversal, and the winners.
Temporary disk usage can grow with matching originals; originals are unchanged.
"""
from contextlib import closing
from collections import Counter
import heapq
import json
import math
import re
from pathlib import Path
import struct
import tempfile
from . import original_spool


# Same token multiset as memory.tokens_of, without two corpus-sized token lists.
# Ranking uses frequencies, not token order. Non-ASCII punctuation is discarded
# before adjacency, just as the compatibility tokenizer does.
_UNITS = re.compile(r'[A-Za-z0-9_-]+|[^\x00-\x7f]')


def statistics(text, wanted):
    # Small documents keep the faster compatibility tokenizer behind a fixed
    # input bound; large documents never allocate a corpus-sized token array.
    if len(text) <= 4096:
        from .memory import tokens_of
        tokens = tokens_of(text)
        return len(tokens), Counter(token for token in tokens if token in wanted)
    counts = Counter()
    previous = None
    longest = max(map(len, wanted), default=0)
    units = ascii_units = 0
    for match in _UNITS.finditer(str(text)):
        # Only ASCII runs can span multiple characters in this regex. A run
        # longer than every wanted token cannot match alone or in a pair.
        # Count it, but do not copy/lower it or join across the skipped unit.
        start, end = match.span()
        if end - start > max(1, longest):
            ascii_units += 1
            units += 1
            previous = None
            continue
        unit = match.group()
        if unit.isascii():
            unit = unit.lower()
            ascii_units += 1
            if unit in wanted: counts[unit] += 1
        elif not unit.isalnum():
            continue
        if previous is not None and len(previous) + len(unit) <= longest:
            pair = previous + unit
            if pair in wanted: counts[pair] += 1
        previous = unit
        units += 1
    if units == 1:
        if not ascii_units and previous in wanted: counts[previous] += 1
        return 1, counts
    return max(0, units - 1) + ascii_units, counts


def search(home, query, *, k=5, now=None, visibility=None):
    from . import memory as m
    units = set(m.tokens_of(query))
    if not units:
        return []
    df = Counter()
    total = total_length = 0
    score_row = struct.Struct('<ddQQ')
    with (tempfile.TemporaryFile() as originals, tempfile.TemporaryFile() as stats,
          tempfile.TemporaryFile() as scores):
        from .temp_volumes import observe
        observe(tempfile.gettempdir())
        with closing(m._iter_documents(home, visibility=visibility)) as documents:
            for where, row in documents:
                if where.startswith(m.TREES[0] + '/') and row.role in ('system', 'developer'):
                    continue
                from .lexical_statistics import CACHE
                length, counts = CACHE.get(row.text, units, statistics)
                named = units.intersection(m.tokens_of(Path(where).stem))
                df.update(counts.keys())
                index = total
                total += 1
                total_length += length
                if len(counts.keys() | named) < min(m.ENOUGH_UNITS, len(units)):
                    continue
                item = dict(text=row.text, where=where, ts=row.ts, source=row.source,
                            role=row.role, memory_kind=row.memory_kind)
                offset = original_spool.write(originals,item)
                stats.write((json.dumps([index, length, counts, bool(named), row.weight, row.ts, offset],
                                        ensure_ascii=True) + '\n').encode('utf-8'))
        if not total:
            return []
        average = total_length / total
        highest_relevance = 0.0
        stats.seek(0)
        while True:
            raw = stats.readline()
            if not raw:
                break
            index, length, counts, named, weight, ts, offset = json.loads(raw)
            relevance = 0.0
            for unit in units:
                count = counts.get(unit, 0)
                if not count:
                    continue
                idf = math.log(1 + (total - df[unit] + 0.5) / (df[unit] + 0.5))
                norm = 1 - m.BM25_B + m.BM25_B * (length / average if average else 1.0)
                relevance += idf * (count * (m.BM25_K1 + 1)) / (count + m.BM25_K1 * norm)
            if named:
                relevance += m.BM25_TITLE_BONUS
            score = relevance * m._a_recency_weight(ts, now)
            score *= m._a_weight_in_the_ranking(weight)
            if score > 0:
                highest_relevance = max(highest_relevance, relevance)
                scores.write(score_row.pack(score, relevance, index, offset))
        limit = max(0, k)
        if not limit:
            return []
        floor = highest_relevance * m.SAME_LEAGUE
        winners = []
        scores.seek(0)
        while raw := scores.read(score_row.size):
            score, relevance, index, offset = score_row.unpack(raw)
            if relevance < floor:
                continue
            candidate = (score, -index, offset)
            if len(winners) < limit:
                heapq.heappush(winners, candidate)
            elif candidate > winners[0]:
                heapq.heapreplace(winners, candidate)
        result = []
        for score, _, offset in sorted(winners, reverse=True):
            item = original_spool.read(originals,offset)
            item['score'] = round(score, 4)
            result.append(item)
        return result
