"""Completed visible batch timing, separate from the last model request receipt."""
import json
import math
import os
from pathlib import Path


def valid(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def load_batch_timing(root):
    if root is None:
        return None
    try:
        value = json.loads((Path(root) / 'batch-timing.json').read_text())
        if not isinstance(value, dict) or not valid(value.get('total')):
            return None
        return {key: value.get(key) if valid(value.get(key)) else None
                for key in ('total', 'thinking', 'output', 'model_total', 'requests')}
    except (OSError, ValueError):
        return None


def finish_batch_timing(root, elapsed, timings):
    if not valid(elapsed):
        return None
    def total(key):
        values = [row.get(key) for row in timings]
        return sum(values) if values and all(valid(v) for v in values) else None
    result = dict(total=elapsed, thinking=total('thinking'), output=total('output'),
                  model_total=total('total'), requests=len(timings))
    if root is not None:
        path = Path(root) / 'batch-timing.json'
        temporary = path.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps(result), encoding='utf-8')
            os.replace(temporary, path)
        except OSError:
            pass
    return result
