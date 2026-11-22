"""One bounded, read-only loopback /usage capture. No model or lifecycle calls.

Exports only validated counters, enums and keyed fingerprints. Never exports
the raw /usage response, which can contain private context details.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telyincore.cache_prefix_buffer import DiagnosticBuffer

MAX_RESPONSE = 2 * 1024 * 1024


def integer(value):
    if type(value) is not int or not 0 <= value <= 2**63-1:
        raise ValueError('count')
    return value


def fingerprint(value, length=64):
    if not isinstance(value, str) or len(value) != length or any(
            c not in '0123456789abcdef' for c in value):
        raise ValueError('fingerprint')
    return value


def totals(value):
    keys = ('attempts', 'reported_attempts', 'unknown_attempts', 'invalid_attempts',
            'input', 'cached', 'uncached')
    result = {k: integer(value[k]) for k in keys}
    if (result['attempts'] != sum(result[k] for k in keys[1:4])
            or result['cached'] > result['input']
            or result['uncached'] != result['input'] - result['cached']):
        raise ValueError('inconsistent totals')
    result['share'] = 100 * result['cached'] / result['input'] if result['input'] else None
    return result


def cache_view(value):
    if not isinstance(value, dict) or value.get('scope') != 'console':
        raise ValueError('console scope required')
    status = value.get('status')
    if status in ('unavailable', 'no_sample', 'selection_failed'):
        return {'status': status}
    if status != 'ready':
        raise ValueError('cache status')
    selected = value['selected']
    return {'status': 'ready', 'partition': fingerprint(selected['bucket']),
            'recent20': totals(selected['recent20']),
            'cumulative': totals(selected['cumulative'])}


def prefix_view(value):
    if not isinstance(value, dict):
        raise ValueError('prefix required')
    if value.get('status') not in ('active', 'disabled', 'unavailable'):
        raise ValueError('prefix status')
    result = {'status': value['status']}
    for name in ('requested', 'active', 'persistent'):
        if type(value.get(name)) is not bool:
            raise ValueError('prefix flag')
        result[name] = value[name]
    if value['persistent'] or value.get('provider_token_prefix') is not None:
        raise ValueError('unexpected prefix contract')
    result['provider_token_prefix'] = None
    error = value.get('error')
    if error not in (None, 'setup_failed', 'snapshot_failed', 'diagnostic_failed',
                     'diagnostic_budget_exceeded'):
        raise ValueError('diagnostic error')
    result['error'] = error
    rows = value.get('rows')
    if not isinstance(rows, list) or len(rows) > 64:
        raise ValueError('row bound')
    sink = DiagnosticBuffer()
    for row in rows:
        sink.append(row)  # Strict key/enum allowlist, no prompt/content field.
        for key, count in row.items():
            if type(count) is int:
                integer(count)
    result['rows'] = sink.snapshot()
    if value['active']:
        if not value['requested'] or value['status'] != 'active':
            raise ValueError('prefix activation')
        result['instance_id'] = fingerprint(value['instance_id'], 32)
        coverage = value['coverage']
        result['coverage'] = {k: integer(coverage[k]) for k in
                              ('stream_opens', 'emitted', 'skipped')}
        c = result['coverage']
        if c['stream_opens'] != c['emitted'] + c['skipped'] or len(rows) > c['emitted']:
            raise ValueError('coverage')
    elif rows:
        raise ValueError('inactive rows')
    return result


def capture(fetch, *, bot, pid, core, port):
    """fetch is injectable for offline fixtures; all failures are safe enums."""
    integer(pid); integer(port)
    if pid == 0 or not 1 <= port <= 65535 or not isinstance(bot, str) or not bot:
        raise ValueError('explicit expected identity required')
    expected_root = os.path.normcase(str(Path(core).resolve()))
    key = secrets.token_bytes(32)
    digest = lambda x: hmac.new(key, str(x).encode(), hashlib.sha256).hexdigest()
    result = {'version': 1, 'unit': 'http_attempt', 'status': 'unavailable',
              'new_model_requests': 0, 'pid': pid, 'port': port,
              'expected_bot_hmac': digest(bot), 'expected_core_hmac': digest(expected_root),
              'identity_hmac_scope': 'capture_only', 'provider_token_prefix': None,
              'request_alignment_verified': False, 'snapshot_atomic': False}

    def matches(identity):
        return (isinstance(identity, dict) and identity.get('active') == bot
                and type(identity.get('pid')) is int and identity['pid'] == pid
                and type(identity.get('port')) is int and identity['port'] == port
                and isinstance(identity.get('coreRoot'), str)
                and os.path.normcase(str(Path(identity['coreRoot']).resolve())) == expected_root)

    try:
        if not matches(fetch('/infinity/bots')):
            return {**result, 'status': 'identity_mismatch'}
        usage = fetch('/usage')
        if not matches(fetch('/infinity/bots')):
            return {**result, 'status': 'identity_changed'}
    except (OSError, ValueError, TypeError, http.client.HTTPException):
        return {**result, 'status': 'read_failed'}
    if not isinstance(usage, dict):
        return {**result, 'status': 'usage_invalid'}
    try:
        result['cache'] = cache_view(usage.get('cache_view'))
    except (KeyError, TypeError, ValueError, AttributeError):
        result['cache'] = {'status': 'invalid'}
    try:
        result['prefix'] = prefix_view(usage.get('cache_prefix_diagnostics'))
    except (KeyError, TypeError, ValueError, AttributeError):
        result['prefix'] = {'status': 'invalid'}
    prefix = result['prefix']
    if result['cache']['status'] != 'ready':
        result['status'] = 'cache_unavailable'
    elif prefix['status'] == 'invalid':
        result['status'] = 'diagnostics_invalid'
    elif not prefix.get('active') or not prefix.get('rows'):
        result['status'] = 'diagnostics_missing'
    elif prefix.get('error') or prefix['coverage']['skipped']:
        result['status'] = 'partial'
    else:
        result['status'] = 'captured'
    return result


def loopback_fetch(port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('port')
    def fetch(path):
        if path not in ('/infinity/bots', '/usage'):
            raise ValueError('read-only route')
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        try:
            connection.request('GET', path, headers={'Accept': 'application/json'})
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError('HTTP status')  # Never follow redirects or log bodies.
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise ValueError('response bound')
            return json.loads(raw)
        finally:
            connection.close()
    return fetch


def write_new(output, result):
    output = Path(output)
    if output.exists():
        raise FileExistsError('evidence exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=output.parent, prefix='.gray-capture-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.link(name, output)  # Complete, exclusive publication; never replace evidence.
    finally:
        Path(name).unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--expect-pid', required=True, type=int)
    parser.add_argument('--expect-bot', required=True)
    parser.add_argument('--expect-core', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    if Path(args.output).exists():
        parser.error('output must not exist')
    result = capture(loopback_fetch(args.port), bot=args.expect_bot,
                     pid=args.expect_pid, core=args.expect_core, port=args.port)
    write_new(args.output, result)
    print(json.dumps({'status': result['status'], 'new_model_requests': 0}))
    return 0 if result['status'] == 'captured' else 2


if __name__ == '__main__':
    raise SystemExit(main())
