"""Bounded, read-only diagnostic evidence from recent immutable session snapshots."""
import hashlib
import json
import re
from pathlib import Path
from .record_history import DIRECTORY, _safe, _lines, _record_stamp

MAX_FILES = 3
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 500
MAX_TEXT = 128 * 1024
MAX_TEXT_TOTAL = 4 * 1024 * 1024
MAX_METADATA = 512


def _timestamp(row):
    value = row.get('ts_ms')
    # Empty timestamps occur in older display-only records.
    return 0 if value is None or value == '' else _record_stamp(row)


def redact(text):
    # Best-effort recognized credentials, not a promise to anonymize conversation.
    text = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+', r'\1[REDACTED]', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]{16,}\b', '[REDACTED]', text)
    return re.sub(r'(?i)(["\']?(?:api[_-]?key|access_token|refresh_token|password|authorization)["\']?\s*[:=]\s*)(["\'])(.*?)\2',
                  lambda m: m[1] + m[2] + '[REDACTED]' + m[2], text)


def collect(session):
    session = Path(session)
    _safe(session)
    folder = session.parent / DIRECTORY / session.name
    _safe(folder)
    discovery_complete = True
    unreadable_entries = 0
    try:
        paths = [path for path in folder.iterdir() if path.name.endswith('.jsonl')]
    except FileNotFoundError:
        paths = []
    except OSError:
        paths = []
        discovery_complete = False
    dated = []
    for path in paths:
        try:
            dated.append((path.stat().st_mtime_ns, path.name, path))
        except OSError:
            unreadable_entries += 1
            discovery_complete = False
    files = [row[2] for row in sorted(dated, reverse=True)]
    report = {'source': 'recent_archived_sessions', 'limits': {'archives': MAX_FILES,
        'file_bytes': MAX_FILE_BYTES, 'records': MAX_RECORDS, 'text_chars': MAX_TEXT,
        'total_text_chars': MAX_TEXT_TOTAL, 'metadata_field_chars': MAX_METADATA}, 'archives_available': len(paths),
        'archive_discovery_complete': discovery_complete,
        'unreadable_archive_entries': unreadable_entries,
        'older_archives_omitted': max(0, len(files) - MAX_FILES), 'archives': [],
        'records': [], 'omitted_records': 0, 'truncated_records': 0,
        'metadata_truncated_records': 0,
        'metadata_redacted_records': 0,
        'redaction': 'recognized_credentials_only; conversation_content_included'}
    rows = []
    seen = set()
    for path in reversed(files[:MAX_FILES]):
        item = {'snapshot': path.stem, 'status': 'ok'}
        report['archives'].append(item)
        try:
            _safe(path)
            if path.stat().st_size > MAX_FILE_BYTES:
                item['status'] = 'file_size_limit'; continue
            with path.open('rb') as source:
                raw = source.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES or hashlib.sha256(raw).hexdigest() != path.stem:
                item['status'] = 'integrity_check_failed'; continue
            digest = hashlib.sha256(); batch = None
            batch_truncated = batch_redacted = False
            parsed = list(_lines(raw))
            # Validate before publishing any rows from this archive. A malformed
            # timestamp must not crash sorting or leak a partially accepted file.
            for _, row in parsed:
                _timestamp(row)
            for number, (line, row) in enumerate(parsed, 1):
                digest.update(line)
                ref = row.get('record_ref') or f'{digest.hexdigest()}:{number}'
                kind = row.get('kind')
                if kind in ('batch', 'ui_batch'):
                    label = row.get('name') or row.get('text')
                    clean = redact(label) if isinstance(label, str) else None
                    batch = clean[:MAX_METADATA] if clean is not None else None
                    batch_truncated = clean is not None and len(clean) > MAX_METADATA
                    batch_redacted = clean is not None and clean != label
                if row.get('is_head') or kind not in ('message', 'function_call', 'function_result', 'batch', 'batch-status', 'system-notice', 'ui_batch', 'ui_batch_end', 'handoff', 'ui_handoff_note', 'continuation'):
                    continue
                # Prefix/persona and hidden reasoning are never diagnostic conversation.
                if kind == 'message' and row.get('role') == 'system': continue
                if ref in seen: continue
                seen.add(ref)
                safe = {k: row[k] for k in ('kind', 'role', 'ts_ms', 'name', 'text', 'scope', 'who', 'call_id', 'display_id')
                        if isinstance(row.get(k), (str, int, bool))}
                truncated, redacted = [], []
                for key, value in safe.items():
                    if key in ('text', 'ts_ms') or not isinstance(value, str):
                        continue
                    clean = redact(value)
                    if clean != value:
                        redacted.append(key)
                    if len(clean) > MAX_METADATA:
                        truncated.append(key)
                    safe[key] = clean[:MAX_METADATA]
                if batch_truncated:
                    truncated.append('batch')
                if batch_redacted:
                    redacted.append('batch')
                safe.update(ts_ms=_timestamp(row), record_ref=ref, snapshot=path.stem,
                            line=number, batch=batch, metadata_truncated=truncated,
                            metadata_redacted=redacted)
                rows.append(safe)
        except (OSError, ValueError, UnicodeError):
            item['status'] = 'unreadable'
    # Stable chronological order; preserve same-timestamp call/result order.
    rows.sort(key=lambda r: r['ts_ms'])
    selected = rows[-MAX_RECORDS:]
    report['omitted_records'] = len(rows) - len(selected)
    used = 0
    output = []
    for row in reversed(selected):
        original = row.get('text', '')
        clean = redact(original)
        available = min(MAX_TEXT, MAX_TEXT_TOTAL - used)
        if available <= 0:
            report['omitted_records'] += 1; continue
        row['text_chars_original'] = len(original)
        row['redacted'] = clean != original
        row['text_truncated'] = len(clean) > available
        row['text'] = clean[:available]
        report['truncated_records'] += int(row['text_truncated'])
        report['metadata_truncated_records'] += bool(row['metadata_truncated'])
        report['metadata_redacted_records'] += bool(row['metadata_redacted'])
        used += len(row['text']); output.append(row)
    report['records'] = list(reversed(output))
    report['complete_within_selected_archives'] = discovery_complete and not (report['omitted_records'] or report['truncated_records'] or report['metadata_truncated_records'] or any(x['status'] != 'ok' for x in report['archives']))
    return report
