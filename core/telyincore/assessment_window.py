"""Opt-in, bounded append windows over the existing authorized projection.

Only count/digest/boundary metadata is retained. Source text stays in Session;
rewrites, truncation, scope changes and budget changes cannot reuse old evidence.
The token estimate is a planning bound, never billed provider usage.
"""
import hashlib
import json
from .evidence_reuse import EvidenceReuse
from .tokens import estimate_tokens


def _json(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),sort_keys=True)


def _digest(rows):
    return hashlib.sha256(_json(rows).encode()).hexdigest()


class AppendWindow:
    def __init__(self):
        self._states=EvidenceReuse(ttl=float('inf'))
        self.last={}

    def select(self, rows, *, scope, budget=8192):
        if type(budget) is not int or not 8192<=budget<=16384:
            raise ValueError('assessment_history_tokens_requires_8192_to_16384')
        rows=list(rows)
        key=_digest([scope,budget])
        previous=json.loads(self._states.get(key) or 'null')
        same=(previous is not None and previous['count']<=len(rows)
              and _digest(rows[:previous['count']])==previous['digest'])
        start=previous['start'] if same else max(0,len(rows)-6)
        reason='append' if same else 'new_or_changed_source'
        size=lambda selected:estimate_tokens(_json(selected))
        if size(rows[start:])>budget:
            # Preserve at least the same recent projection as rolling mode
            # when it fits; never pad with expired records for cache hits.
            start=max(start,len(rows)-6)
            reason='budget_rollover'
        while start<len(rows) and size(rows[start:])>budget:
            start+=1
        kept=rows[start:]
        self.last=dict(reason=reason,source_rows=len(rows),start=start,
                       kept_rows=len(kept),estimated_history_tokens=size(kept),budget=budget)
        self._states.put(key,_json(dict(count=len(rows),start=start,digest=_digest(rows))))
        return kept
