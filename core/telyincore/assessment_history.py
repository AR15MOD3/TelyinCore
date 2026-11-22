"""Lossless packing of current scoped history, without extending its window."""
import hashlib
import json

from .evidence_reuse import EvidenceReuse


HISTORY_ENCODING = (
    '\n历史编码：visible_history每条消息的seq表示真实先后顺序，序号越大越晚；'
    '数组存储顺序不代表发生顺序。仍只评估current_message，不把历史消息当作本次新事件。'
)

REFERENCE_ENCODING = (
    '\n若current_message为引用对象，完整原文=visible_history中history_seq对应的text'
    '再加suffix（未给则为空串）；证据引用拼接后的原文，不引用序号。'
)


def reference_current(packed):
    """Encode an exact duplicate by reference; never truncate or infer text."""
    current = packed['current_message']
    if isinstance(current, str):
        matches = [row for row in packed['visible_history']
                   if row['role'] == 'user' and row['text']
                   and current.startswith(row['text'])]
        if matches:
            # Prefer the smallest exact representation, then the latest equal
            # match. A fixed 160-character cutoff duplicated ordinary messages
            # even when the existing reference encoding was already shorter.
            candidates = []
            for row in matches:
                reference = {'history_seq': row['seq']}
                suffix = current[len(row['text']):]
                if suffix:
                    reference['suffix'] = suffix
                size = len(json.dumps(reference, ensure_ascii=False, separators=(',', ':')))
                candidates.append((size, -row['seq'], reference))
            size, _, reference = min(candidates, key=lambda item: item[:2])
            if size < len(json.dumps(current, ensure_ascii=False)):
                return dict(packed, current_message=reference)
    return packed


class HistoryPacking:
    def __init__(self):
        # Only hashes, sequence numbers and storage order persist, never source text.
        # This is ordering metadata, not a cached model decision. An idle pause
        # must not renumber identical surviving evidence after five minutes.
        # Bound scopes by LRU; exact live hashes still remove expired messages.
        self._orders = EvidenceReuse(ttl=float('inf'))

    def pack(self, data, *, scope):
        current = [hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True,
                    separators=(',', ':')).encode('utf-8')).hexdigest()
                   for row in data['visible_history']]
        binding = hashlib.sha256(json.dumps(scope, ensure_ascii=False).encode('utf-8')).hexdigest()
        prior = json.loads(self._orders.get(binding) or '{"hashes":[],"ids":[],"order":[]}')
        overlap = max([0] + [n for n in range(1, min(len(current), len(prior['hashes'])) + 1)
                            if prior['hashes'][-n:] == current[:n]])
        ids = prior['ids'][-overlap:] if overlap else []
        start = prior['ids'][-1] + 1 if overlap else 1
        ids += list(range(start, start + len(current) - overlap))
        if not prior['order'] or prior['order'][0] not in ids:
            order = list(reversed(ids))
        else:
            order = [n for n in prior['order'] if n in ids]
            order.extend(n for n in ids if n not in order)
        records = {n: dict(seq=n, **row) for n, row in zip(ids, data['visible_history'])}
        packed = dict(visible_history=[records[n] for n in order],
                      previous_emotion=data['previous_emotion'], current_message=data['current_message'])
        restored = [{k: v for k, v in row.items() if k != 'seq'}
                    for row in sorted(packed['visible_history'], key=lambda row: row['seq'])]
        if restored != data['visible_history']:
            raise ValueError('assessment_history_not_lossless')
        self._orders.put(binding, json.dumps(dict(hashes=current, ids=ids, order=order)))
        return packed
