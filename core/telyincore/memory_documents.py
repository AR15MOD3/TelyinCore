"""Validated JSONL snapshots with memory bounded by a spool and one record.

Validate the complete file before exposing any row, matching the legacy rule
that malformed files cannot supply a seemingly valid prefix. Original files
remain unchanged. Large individual records still require their decoded size.
"""
from contextlib import contextmanager
import hashlib
import io
import os
from pathlib import Path
import stat
import tempfile

from .file_signature import descriptor_signature, signature


@contextmanager
def snapshot(path):
    path = Path(path).absolute()
    if path.resolve() != path:
        raise ValueError('memory source redirected')
    before = signature(path)
    with path.open('rb') as source, tempfile.SpooledTemporaryFile(max_size=1024*1024) as copy:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode) or descriptor_signature(source.fileno()) != before:
            raise ValueError('memory source changed during open')
        digest = hashlib.sha256()
        remaining = before[2]
        while remaining:
            block = source.read(min(65536, remaining))
            if not block:
                raise ValueError('memory source shortened')
            copy.write(block); digest.update(block); remaining -= len(block)
        if (source.read(1) or descriptor_signature(source.fileno()) != before
                or signature(path) != before or path.resolve() != path):
            raise ValueError('memory source changed during snapshot')
        copy.seek(0)
        yield copy, digest.hexdigest()


def transcript_rows(path, granted):
    from .memory import _transcript_rows
    with snapshot(path) as (source, digest):
        allowed = granted(path, content_sha256=digest) if granted is not None else False
        text = io.TextIOWrapper(source, encoding='utf-8', newline='\n')
        try:
            for line in text:
                _transcript_rows(line)  # Full validity before the first yield.
            text.seek(0)
            for line in text:
                for row in _transcript_rows(line):
                    yield row, allowed
        finally:
            text.detach()  # The enclosing snapshot owns the spool.
