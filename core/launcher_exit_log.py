"""Bounded, best-effort Windows launcher diagnostics without account secrets."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

_log = ContextVar('launcher_exit_log', default=None)


@contextmanager
def launcher_exit_log(core):
    path = None
    if os.name == 'nt':
        try:
            from runtime_identity import safe_path
            core = Path(core)
            # Diagnostics precede Bot ownership admission: keep them outside data.
            folder = safe_path(core, core / 'logs/launcher')
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f'launcher-exit-{os.getpid()}-{uuid4().hex}.jsonl'
        except (OSError, ValueError):
            pass
    token = _log.set(path)
    try:
        exit_event('launcher_enter')
        yield
    finally:
        _log.reset(token)


def exit_event(stage, *, error=None, **fields):
    path = _log.get()
    if path is None:
        return
    record = dict(at=datetime.now(timezone.utc).isoformat(), pid=os.getpid(), stage=stage, **fields)
    if error is not None:
        causes = []
        while error is not None and len(causes) < 6:
            causes.append(type(error).__name__)
            error = error.__cause__
        record['errors'] = causes
    try:
        if path.exists() and path.stat().st_size >= 65536:
            return
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        pass
