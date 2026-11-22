"""Small append-only JSON journals with preserved damaged lines.

Cooperating writers serialize the tail check and append. A missing newline is
inserted before the new record; existing bytes are never truncated or rewritten.
Readers inspect a finite prefix and skip malformed records, as these journals
have always done. They do not create files or acquire a write lease.
"""
import json
import os
from pathlib import Path

from .source_guard import lease


def append(path, row):
    append_line(path, json.dumps(row, ensure_ascii=False))


def append_line(path, line):
    # Encode before opening: an unencodable row must not touch an existing tail.
    if '\n' in line or '\r' in line or not isinstance(json.loads(line), dict):
        raise ValueError('one JSON object line required')
    raw = (line + '\n').encode('utf-8')
    path = Path(path)
    with lease(path):
        with path.open('a+b') as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell():
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b'\n':
                    stream.write(b'\n')
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != 'nt':
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def objects(path):
    """Read complete JSON objects without changing number/container types."""
    try:
        stream = Path(path).open('rb')
    except FileNotFoundError:
        return
    with stream:
        remaining = os.fstat(stream.fileno()).st_size
        while remaining:
            raw = stream.readline(remaining)
            if not raw:
                break
            remaining -= len(raw)
            try:
                row = json.loads(raw.decode('utf-8'))
            except (UnicodeError, ValueError):
                continue
            if isinstance(row, dict):
                yield row


def rows(path):
    # Compatibility for the existing string-valued journal consumers.
    for row in objects(path):
        yield {str(key): str(value) for key, value in row.items()}
