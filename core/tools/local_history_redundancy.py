"""Pure offline stored-record audit; never loads/repairs a Session or logs text.

Scope/binding checks and a stable read are the caller's responsibility. This
is not a projection of the next provider request or permission to delete.
"""
from collections import Counter
from dataclasses import asdict
import json
import re
from telyincore.session import _parse_line


def audit_history_bytes(raw):
    if not isinstance(raw, bytes) or len(raw) > 8 * 1024 * 1024:
        raise ValueError('bounded bytes snapshot required')
    if raw and not raw.endswith(b'\n'):
        raise ValueError('unterminated history; no repair performed')
    rows = []
    def unique(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                raise ValueError('duplicate history field')
            obj[k] = v
        return obj
    try:
        for line in raw.split(b'\n'):
            if not line.strip():
                continue
            if len(rows) >= 4096:
                raise ValueError('too many records')
            text = line.decode('utf8')
            json.loads(text, object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            row = asdict(_parse_line(text))
            if not isinstance(row['text'], str):
                raise ValueError('invalid record text')
            rows.append(row)
    except (ValueError, TypeError, UnicodeError, RecursionError, AttributeError):
        raise ValueError('invalid or oversized stored history; no repair performed') from None
    def key(v):
        return json.dumps(v, sort_keys=True, ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False)
    records = Counter()
    paragraphs = Counter()
    lengths = {}
    for row in rows:
        stable = {k: v for k, v in row.items() if k != 'ts_ms'}
        records[key(stable)] += 1
        # Other metadata, including speaker and flags, remains in the scope.
        context = key({k: v for k, v in stable.items() if k != 'text'})
        for paragraph in re.split(r'\n[ \t]*\n', row['text']):
            if len(paragraph) >= 80:
                identity = (context, paragraph)
                paragraphs[identity] += 1
                lengths[identity] = len(paragraph)
    return {'status': 'audited', 'scope': 'single_stored_history_snapshot',
            'records': len(rows),
            'exact_record_repeat_groups_ignoring_timestamp': sum(n > 1 for n in records.values()),
            'exact_record_extra_copies_ignoring_timestamp': sum(n - 1 for n in records.values()),
            'long_paragraph_min_characters': 80,
            'long_paragraph_repeat_groups_same_metadata': sum(n > 1 for n in paragraphs.values()),
            'long_paragraph_extra_copies_same_metadata': sum(n - 1 for n in paragraphs.values()),
            'repeated_long_paragraph_characters': sum((n - 1) * lengths[k] for k, n in paragraphs.items()),
            'head_records': sum(r['is_head'] is True for r in rows),
            'ephemeral_records': sum(r['is_ephemeral'] is True for r in rows),
            'records_with_images': sum(bool(r['images']) for r in rows),
            'actual_sent_body_reconstructed': False, 'native_input_tokens': None,
            'safe_to_remove_proven': False, 'savings_proven': False,
            'content_exported': False, 'history_modified': False}
