"""Synthetic-only rename transaction experiment; no caller-supplied deployment root.

Run: python -m tools.bot_rename_fixture
Not a production migrator. Journal authentication is process-local; no real restart entry.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from bot_rename_preflight import RETIRED, control, plan_rename
from runtime_identity import LEDGER, MARKER, check_layout, identity_lock, safe_path
from startup import runtime_root


class SimulatedCrash(BaseException):
    pass


def encoded(raw):
    return None if raw is None else base64.b64encode(raw).decode('ascii')


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')


def atomic(path, raw):
    temp = path.with_name('.fixture-write-' + uuid.uuid4().hex)
    try:
        with temp.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def snapshot(root):
    result = {}
    def walk(folder):
        for path in sorted(folder.iterdir()):
            safe_path(root, path)
            key = path.relative_to(root).as_posix()
            if path.is_dir():
                result[key] = 'directory'
                walk(path)
            elif path.name == '.bot-identity.lock':
                result[key] = 'cooperative-lock'
            elif path.is_file():
                if path.stat().st_size > 1024 * 1024:
                    raise ValueError('fixture file exceeds bounded experiment limit')
                result[key] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                raise ValueError('unsupported fixture filesystem object')
            if len(result) > 1000:
                raise ValueError('fixture exceeds bounded experiment limit')
    walk(root)
    return result


class Fixture:
    """Owns a newly generated temporary deployment; never opens real Bot data."""
    def __init__(self, *, baseline=True):
        self.home = Path(tempfile.mkdtemp(prefix='bot-rename-fixture-')).resolve()
        self._original_home = self.home
        self.root = self.home / 'deployment'
        self.root.mkdir()
        self.core = self.root / 'core'; self.core.mkdir()
        self.base = self.core / 'data'
        self.bots = self.root / 'bots'; self.bots.mkdir()
        self._secret = os.urandom(32)
        self._proof = uuid.uuid4().hex
        (self.home / 'synthetic-only.json').write_bytes(json_bytes({'nonce': self._proof}))
        for name in ('One', 'Other'):
            folder = self.bots / name
            persona = folder / 'persona/你定的'; persona.mkdir(parents=True)
            for item in ('存在方式', '人设', '形象', '世界', '关系', '破甲'):
                (persona / (item + '.md')).write_text('synthetic ' + item, encoding='utf-8')
            (folder / 'memory').mkdir()
            (folder / 'memory/fixture.txt').write_text('One is literal history, never replace', encoding='utf-8')
            (folder / 'bot.json').write_bytes(json_bytes({'displayName': 'Visible ' + name}))
        first, second = ('One', 'Other') if baseline else ('Other', 'One')
        for name in (first, second):
            runtime = runtime_root(self.base, name, self.bots)
            runtime.mkdir(parents=True, exist_ok=True)
            (runtime / 'session-main.jsonl').write_bytes(b'{"text":"synthetic One"}\n')
            (runtime / 'synthetic-history.bin').write_bytes(b'\x00One\xff')
        self.journal = self.home / 'transaction.json'

    def _check(self):
        temp = Path(tempfile.gettempdir()).resolve()
        if (self.home != self._original_home or self.home.parent != temp
                or not self.home.name.startswith('bot-rename-fixture-')
                or self.root != self.home / 'deployment'
                or self.core != self.root / 'core' or self.base != self.core / 'data'
                or self.bots != self.root / 'bots'
                or self.journal != self.home / 'transaction.json'):
            raise ValueError('not an owned temporary fixture')
        safe_path(self.home, self.root)
        safe_path(self.home, self.journal)
        safe_path(self.home, self.home / 'synthetic-only.json')
        if control(self.home / 'synthetic-only.json') != {'nonce': self._proof}:
            raise ValueError('fixture proof changed')

    def prepare(self, old='One', new='Renamed'):
        self._check()
        return {'plan': plan_rename(self.core, old, new), 'snapshot': snapshot(self.root)}

    def _save(self, data):
        body = json_bytes(data)
        mac = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        atomic(self.journal, json_bytes({'body': data, 'mac': mac}))

    def _load(self):
        saved = control(self.journal)
        body = saved['body']
        actual = hmac.new(self._secret, json_bytes(body), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(actual, saved.get('mac', '')):
            raise ValueError('fixture journal authentication failed')
        return body

    def _path(self, rel):
        return safe_path(self.root, self.root / rel)

    def _operations(self, plan):
        old, new = plan['old'], plan['new']
        ops = [{'kind': 'move', 'from': 'bots/' + old, 'to': 'bots/' + new}]
        if plan['baseline'] != old:
            ops.append({'kind': 'move', 'from': plan['runtime_from'], 'to': plan['runtime_to']})
        def write(rel, value, read_rel=None):
            path = self._path(read_rel or rel)
            before = path.read_bytes() if path.exists() else None
            ops.append({'kind': 'write', 'path': rel, 'before': encoded(before),
                        'after': encoded(json_bytes(value))})
        if plan['baseline'] == old:
            owner = control(self.base / 'bot-runtime-owner.json'); owner['baseline'] = new
            write('core/data/bot-runtime-owner.json', owner)
        legacy = self.base / 'active-bot.json'
        if legacy.exists():
            data = control(legacy)
            for key in ('baseline', 'active'):
                if data.get(key) == old:
                    data[key] = new
            write('core/data/active-bot.json', data)
        data = control(self.base / LEDGER)
        data['bindings'][new] = data['bindings'].pop(old)
        write('core/data/' + LEDGER, data)
        marker = control(self.bots / old / MARKER); marker['name'] = new
        write('bots/' + new + '/' + MARKER, marker, 'bots/' + old + '/' + MARKER)
        retired = self.base / RETIRED
        data = control(retired) if retired.exists() else {'version': 1, 'names': []}
        data['names'] = list(dict.fromkeys([*data['names'], old]))
        write('core/data/' + RETIRED, data)
        return ops

    def execute(self, prepared, *, crash_after=None):
        self._check()
        if self.journal.exists():
            raise ValueError('existing transaction must be recovered first')
        with identity_lock(self.base):
            if prepared['snapshot'] != snapshot(self.root):
                raise ValueError('fixture changed after preflight')
            plan = prepared['plan']
            if plan != plan_rename(self.core, plan['old'], plan['new']):
                raise ValueError('plan changed or was tampered with')
            if not plan['fixture_eligible']:
                raise ValueError('preflight blockers: ' + ', '.join(plan['blockers']))
            # Whole backup is permitted ONLY because this tree was generated above.
            shutil.copytree(self.root, self.home / 'backup',
                            ignore=shutil.ignore_patterns('.bot-identity.lock'))
            data = {'version': 1, 'state': 'prepared', 'before': prepared['snapshot'],
                    'operations': self._operations(plan), 'pending': None, 'applied': 0}
            self._save(data)
            for i, op in enumerate(data['operations']):
                data.update(state='applying', pending=i); self._save(data)
                self._apply(op)
                if crash_after == i:
                    raise SimulatedCrash('after operation ' + str(i))
                data.update(applied=i + 1, pending=None); self._save(data)
            check_layout(self.base, self.bots)
            data['state'] = 'committed'; self._save(data)
            return {'fixture_only': True, 'state': 'committed', 'steps': len(data['operations']),
                    'deployment': str(self.root), 'journal': str(self.journal)}

    def _apply(self, op):
        if op['kind'] == 'move':
            source, target = self._path(op['from']), self._path(op['to'])
            if not source.is_dir() or target.exists():
                raise ValueError('move precondition changed')
            source.rename(target)
        else:
            path = self._path(op['path'])
            actual = encoded(path.read_bytes()) if path.exists() else None
            if actual != op['before']:
                raise ValueError('manifest changed before write')
            atomic(path, base64.b64decode(op['after']))

    @staticmethod
    def _next(before, op):
        result = dict(before)
        if op['kind'] == 'move':
            for key in list(result):
                if key == op['from'] or key.startswith(op['from'] + '/'):
                    result[op['to'] + key[len(op['from']):]] = result.pop(key)
        else:
            result[op['path']] = hashlib.sha256(base64.b64decode(op['after'])).hexdigest()
        return result

    def rollback(self, *, crash_after=None):
        """Infer pending completion from durable state; refuse ANY foreign changes."""
        self._check()
        with identity_lock(self.base):
            data = self._load()
            expected = [data['before']]
            for op in data['operations']:
                expected.append(self._next(expected[-1], op))
            actual = snapshot(self.root)
            matches = [i for i, state in enumerate(expected) if state == actual]
            if len(matches) != 1:
                raise ValueError('concurrent changes: rollback refuses to overwrite')
            completed = matches[0]
            if completed == 0 and data['state'] == 'rolled_back':
                return {'fixture_only': True, 'state': 'rolled_back'}
            for count, i in enumerate(range(completed - 1, -1, -1)):
                if snapshot(self.root) != expected[i + 1]:
                    raise ValueError('concurrent changes during rollback')
                data.update(state='rolling_back', pending=i); self._save(data)
                op = data['operations'][i]
                if op['kind'] == 'move':
                    self._path(op['to']).rename(self._path(op['from']))
                else:
                    path = self._path(op['path'])
                    if op['before'] is None:
                        path.unlink()  # one known synthetic file; never recursive delete
                    else:
                        atomic(path, base64.b64decode(op['before']))
                if crash_after == count:
                    raise SimulatedCrash('during rollback')
            if snapshot(self.root) != data['before']:
                raise ValueError('rollback verification failed')
            check_layout(self.base, self.bots)
            data.update(state='rolled_back', pending=None, applied=0); self._save(data)
            return {'fixture_only': True, 'state': 'rolled_back'}


def main():
    report = []
    for baseline in (True, False):
        fixture = Fixture(baseline=baseline)
        prepared = fixture.prepare()
        result = fixture.execute(prepared)
        result['baseline_case'] = baseline
        result['rollback'] = fixture.rollback()['state']
        report.append(result)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
