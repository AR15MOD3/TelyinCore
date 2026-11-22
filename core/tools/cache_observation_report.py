"""Read-only metadata report from an existing cache ledger; never makes requests.

Retained rows are bounded history, not necessarily one benchmark run. Keep
partitions separate. Item equality is NOT a provider token-prefix measurement.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telyincore.cache_observation import CacheLedger, summarize


def report(ledger_path, *, model=None, auth=None, last=20):
    if type(last) is not int or not 1 <= last <= 20:
        raise ValueError('last must be between 1 and 20')
    path = Path(ledger_path)
    if not path.is_file():
        raise ValueError('existing ledger required')
    ledger = CacheLedger(path)
    if ledger.persistence_error:
        raise ValueError('ledger validation failed; source preserved')
    # The ledger owns validation; this read-only tool uses the retained rows,
    # which snapshot() intentionally reduces to aggregate coverage.
    buckets = []
    for key, bucket in sorted(ledger._buckets.items(),
                              key=lambda pair: pair[1]['updated'], reverse=True):
        identity = bucket['identity']
        if model is not None and identity['model'] != model:
            continue
        if auth is not None and identity['auth'] != auth:
            continue
        rows = bucket['recent'][-last:]
        buckets.append({'partition': key, 'model': identity['model'],
            'auth': identity['auth'], 'updated_sequence': bucket['updated'],
            'retained_sample': summarize(rows),
            'rows': [{'usage': dict(r['usage']), 'outcome': r['outcome'],
                      'first_output_ms': r['first_output_ms'],
                      'elapsed_ms': r['elapsed_ms'],
                      'comparison': r['comparison'],
                      'input_item_count': r['request']['item_count']}
                     for r in rows]})
    return {'version': 1, 'unit': 'http_attempt', 'source': 'validated_wire_ledger',
            'provider_token_prefix': None, 'retained_rows_per_partition': last,
            'partitions_combined': False, 'buckets': buckets}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', required=True)
    parser.add_argument('--model')
    parser.add_argument('--auth')
    parser.add_argument('--last', type=int, default=20)
    parser.add_argument('--output', required=True,
                        help='New report file; existing files are never overwritten')
    args = parser.parse_args(argv)
    output = Path(args.output)
    if output.resolve() == Path(args.ledger).resolve() or output.exists():
        parser.error('output must be a new file, not the source ledger')
    result = report(args.ledger, model=args.model, auth=args.auth, last=args.last)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids overwriting evidence, including concurrent runs.
    # Write complete bytes to a temporary then hard-link atomically without replace.
    fd, temporary = tempfile.mkstemp(dir=output.parent, prefix='.cache-report-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(json.dumps({'output': str(output.resolve()),
                      'partitions': len(result['buckets'])}))


if __name__ == '__main__':
    main()
