"""Immutable web generations; a single pointer selects each new document.

Local cooperating publisher contract. Never delete an older generation while
clients may reference it. Digest verification fails closed on external edits.
"""
import hashlib
import json
from pathlib import Path
import re

_ID = re.compile(r'[0-9a-f]{32}')
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')


def read_asset(root, name):
    root = Path(root)
    explicit = name.startswith('__web/')
    if explicit:
        parts = name.split('/')
        if len(parts) != 3:
            raise ValueError('asset route')
        _, generation, name = parts
        expected_manifest = None
    else:
        pointer = root / 'current.json'
        if not pointer.exists():
            return None
        if pointer.is_symlink() or pointer.stat().st_size > 1024:
            raise ValueError('invalid pointer')
        chosen = json.loads(pointer.read_bytes())
        if set(chosen) != {'generation', 'manifest_sha256'}:
            raise ValueError('pointer fields')
        generation, expected_manifest = chosen['generation'], chosen['manifest_sha256']
    if not isinstance(generation, str) or not _ID.fullmatch(generation) or not _NAME.fullmatch(name):
        raise ValueError('asset identity')
    directory = root / 'generations' / generation
    for path in (root, root / 'generations', directory, directory / 'manifest.json', directory / name):
        if path.is_symlink():
            raise ValueError('asset link')
    with (directory / 'manifest.json').open('rb') as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024 or (expected_manifest is not None and hashlib.sha256(raw).hexdigest() != expected_manifest):
        raise ValueError('manifest digest')
    manifest = json.loads(raw)
    if manifest['generation'] != generation:
        raise ValueError('generation mismatch')
    pin = manifest['files'][name]
    path = directory / name
    if not isinstance(pin['bytes'], int) or not 0 <= pin['bytes'] <= 32 * 1024 * 1024:
        raise ValueError('asset capacity')
    with path.open('rb') as stream:
        body = stream.read(pin['bytes'] + 1)
    if len(body) != pin['bytes'] or hashlib.sha256(body).hexdigest() != pin['sha256']:
        raise ValueError('asset digest')
    return body
