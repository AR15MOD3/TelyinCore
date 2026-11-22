"""Keep the context-cache identity of a session across an offline runtime rename.

The receipt is only a hash-key origin; it never redirects a filesystem read,
changes a record scope, or supplies provider context/record IDs.
"""
import json
from pathlib import Path

FILE = 'session-path-origin-v1.json'


def session_identity(session, provider_root):
    session = Path(session).resolve()
    root = Path(provider_root).resolve()
    receipt = root / FILE
    if not receipt.exists():
        return str(session)
    if receipt.is_symlink() or receipt.stat().st_size > 8192:
        raise ValueError('invalid session path identity receipt')
    row = json.loads(receipt.read_bytes())
    if (not isinstance(row, dict) or set(row) != {'version', 'original_runtime'}
            or type(row['version']) is not int or row['version'] != 1
            or not isinstance(row['original_runtime'], str)
            or not Path(row['original_runtime']).is_absolute()):
        raise ValueError('invalid session path identity receipt')
    # Provider state belongs to this runtime; sessions elsewhere retain their
    # own identity rather than borrowing this receipt.
    try:
        relative = session.relative_to(root.parent)
    except ValueError:
        return str(session)
    return str(Path(row['original_runtime']) / relative)
