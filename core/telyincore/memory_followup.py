"""Bounded literal original context for sparse lexical memory hits.

One additional authorized streaming pass, at most four explicit titles and
first/latest original per title. No model, semantic guesses, or corpus cache.
The caller's k remains a total result limit, not a per-query multiplier.
"""
from contextlib import closing
from datetime import datetime, timezone
import re


def expand_titles(home, hits, *, limit, visibility):
    from .memory import _iter_documents, TREES
    from .memory_receipt import reference
    if not hits or len(hits) >= limit:
        return hits
    anchors = []
    for hit in hits[:10]:
        # Derived notes and old assistant claims must not choose the expansion.
        if not hit['where'].startswith(TREES[0] + '/') or hit.get('role') != 'user':
            continue
        for title in re.findall(r'《([^《》\n]{1,80})》', hit['text'][:4096]):
            if title not in anchors and len(anchors) < 4:
                anchors.append(title)
    if not anchors:
        return hits
    selected = {}
    known = {reference(h['where'], h['ts'], h['source'], h['text']) for h in hits}
    with closing(_iter_documents(home, visibility=visibility)) as documents:
        for where, row in documents:
            if not where.startswith(TREES[0] + '/') or row.role != 'user':
                continue
            names = [title for title in anchors if '《' + title + '》' in row.text]
            if not names:
                continue
            try:
                stamp = datetime.fromisoformat(row.ts)
                if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
                order = stamp.timestamp()
            except (ValueError, TypeError, OverflowError):
                continue  # Do not invent chronology for undated originals.
            ref = reference(where, row.ts, row.source, row.text)
            if ref in known: continue
            item = dict(text=row.text, where=where, ts=row.ts, source=row.source,
                        role=row.role, memory_kind=row.memory_kind, score=0,
                        literal_followup=True)
            for title in names:
                for side in ('first', 'last'):
                    key = (title, side);previous = selected.get(key)
                    if previous is None or (order < previous[0] if side == 'first' else order > previous[0]):
                        selected[key] = (order, ref, item)
    result = list(hits)
    # Round-robin both ends, stable title order, deduplicated by exact original.
    for side in ('first', 'last'):
        for title in anchors:
            match = selected.get((title, side))
            if match and match[1] not in known and len(result) < limit:
                known.add(match[1]);result.append(match[2])
    return result


def single_character_originals(home, query, *, limit, visibility):
    """Chinese one-character queries cannot match the corpus bigram index.

    Use an explicit literal fallback only for that gap, with bounded winners.
    It does not change BM25 ranking or treat arbitrary letters as substrings.
    """
    from .memory import _iter_documents, TREES
    import heapq
    if len(query) != 1 or query.isascii() or not query.isalnum() or limit <= 0:
        return []
    winners=[]
    with closing(_iter_documents(home, visibility=visibility)) as documents:
        for index,(where,row) in enumerate(documents):
            if not where.startswith(TREES[0]+'/') or row.role!='user' or query not in row.text:
                continue
            try:
                stamp=datetime.fromisoformat(row.ts)
                if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
                order=stamp.timestamp()
            except (ValueError,TypeError,OverflowError):order=float('-inf')
            item=dict(text=row.text,where=where,ts=row.ts,source=row.source,role=row.role,memory_kind=row.memory_kind,score=0)
            candidate=(order,-index,item)
            if len(winners)<limit:heapq.heappush(winners,candidate)
            elif candidate[:2]>winners[0][:2]:heapq.heapreplace(winners,candidate)
    return [item for _,_,item in sorted(winners,reverse=True)]
