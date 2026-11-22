(
    'Conservative, isolated bookkeeping sidecar. N'
    'o native write grants or recovery.\nA reservat'
    'ion file blocks concurrent/reconstructed wrap'
    'pers after any uncertainty.\nNot a cross-store'
    ' transaction, security boundary, power-loss o'
    'r fsync guarantee.\n'
)
from pathlib import Path
import dataclasses, hashlib, json, os, re, uuid

class JournalRefused(RuntimeError):
    pass

class JournalChanged(JournalRefused):
    pass
PHASES = {'prepared', 'normal_complete', 'cancelled_blocked', 'failed_blocked'}

def wire(value):
    return (
        (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n').
            encode('utf-8')
    )

def sha(b):
    return hashlib.sha256(b).hexdigest()

def assert_tool_history_shape(records):
    pending = []
    seen = False
    for r in records:
        if r.kind not in ('function_call', 'function_result'):
            continue
        seen = True
        if not r.scope:
            raise JournalRefused('legacy_tool_scope_unknown')
        key = (r.scope, r.name)
        if r.kind == 'function_call':
            pending.append(key)
        else:
            if key not in pending:
                raise JournalRefused('unmatched_tool_result')
            pending.remove(key)
            if r.text == '本次未写入画像（shadow审核轮次已取消）。':
                raise JournalRefused('cancelled_tool_history')
    if pending:
        raise JournalRefused('unmatched_tool_calls')
    return seen

def strict_pairs(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise JournalRefused('duplicate_key')
        d[k] = v
    return d

class DurableShadowJournal:

    def __init__(self, root, *, approved_root, fault=None):
        root = Path(root)
        approved = Path(approved_root).resolve(strict=True)
        if not root.is_absolute():
            raise JournalRefused('absolute_path_required')
        for parent in [root, *root.parents]:
            if parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)():
                raise JournalRefused('linked_path')
            if parent == approved:
                break
        if not root.resolve().is_relative_to(approved):
            raise JournalRefused('outside_approved_root')
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'shadow-round-state.json'
        self.reservation = root / 'shadow-round-reservation.json'
        self.fault = fault or (lambda _: None)
        self.known = None
        self.token = None

    def _read(self):
        if not self.path.exists():
            return None
        if self.path.is_symlink() or getattr(self.path, 'is_junction', lambda: False)():
            raise JournalRefused('linked_journal')
        with self.path.open('rb') as f:
            b = f.read(262145)
        if len(b) > 262144:
            raise JournalRefused('journal_budget')
        if not b.endswith(b'\n'):
            raise JournalRefused('torn_journal')
        try:
            v = json.loads(b.decode('utf-8'), object_pairs_hook=strict_pairs)
        except (ValueError, UnicodeError) as exc:
            raise JournalRefused('invalid_journal') from exc
        if (
            not isinstance(v, dict) or set(v) != {'version', 'entries'} or (type(v['version'])
                is not int or v['version'] != 1) or (not isinstance(v['entries'], list)) or (not
                    1 <= len(v['entries']) <= 64)
        ):
            raise JournalRefused('journal_schema')
        tokens = []
        for i, e in enumerate(v['entries']):
            if (
                not isinstance(e, dict) or set(e) != {'token', 'scope', 'round_no',
                    'session_fingerprint', 'phase', 'error'}
            ):
                raise JournalRefused('entry_schema')
            if not isinstance(e['token'], str) or not re.fullmatch('[0-9a-f]{32}', e['token']):
                raise JournalRefused('token_schema')
            if (
                not isinstance(e['scope'], str) or not e['scope'] or type(e['round_no']) is not
                    int or (not isinstance(e['error'], str))
            ):
                raise JournalRefused('context_schema')
            if (
                not isinstance(e['session_fingerprint'], str) or not re.fullmatch('[0-9a-f]{64}'
                    , e['session_fingerprint'])
            ):
                raise JournalRefused('fingerprint_schema')
            if (
                not isinstance(e['phase'], str) or e['phase'] not in PHASES or (i < len(v[
                    'entries']) - 1 and e['phase'] != 'normal_complete')
            ):
                raise JournalRefused('phase_schema')
            tokens.append(e['token'])
        if len(tokens) != len(set(tokens)):
            raise JournalRefused('duplicate_token')
        return (b, v)

    def check_start(self):
        if (
            self.reservation.exists() or self.reservation.is_symlink() or any(self.root.glob(
                '*.tmp'))
        ):
            raise JournalRefused('reservation_or_staging_present')
        read = self._read()
        if read is not None and read[1]['entries'][-1]['phase'] != 'normal_complete':
            raise JournalRefused('previous_round_blocked')
        return read

    def _assert_current(self):
        read = self._read()
        now = None if read is None else read[0]
        if now != self.known:
            raise JournalChanged('journal_bytes_changed')

    def _reserve(self, token):
        with self.reservation.open('xb') as f:
            f.write(wire(dict(token=token)))
            f.flush()
            os.fsync(f.fileno())

    def _owns(self, token):
        try:
            b = self.reservation.read_bytes()
        except OSError as exc:
            raise JournalChanged('reservation_unavailable') from exc
        if b != wire(dict(token=token)):
            raise JournalChanged('reservation_changed')

    def _save(self, value, stage):
        self._assert_current()
        b = wire(value)
        if len(b) > 262144:
            raise JournalRefused('journal_budget')
        tmp = self.root / ('write-' + uuid.uuid4().hex + '.tmp')
        with tmp.open('xb') as f:
            f.write(b)
            f.flush()
            os.fsync(f.fileno())
        self.fault(stage + ':before_replace')
        self._assert_current()
        os.replace(tmp, self.path)
        self.known = b
        self.fault(stage + ':after_replace')

    def begin(self, scope, round_no, records):
        if not isinstance(scope, str) or not scope or type(round_no) is not int:
            raise JournalRefused('trusted_context_required')
        read = self.check_start()
        self.known = None if read is None else read[0]
        value = dict(version=1, entries=[] if read is None else read[1]['entries'])
        if len(value['entries']) >= 64:
            raise JournalRefused('round_budget')
        self.token = uuid.uuid4().hex
        try:
            self._reserve(self.token)
        except FileExistsError as exc:
            raise JournalRefused('concurrent_reservation') from exc
        self.fault('begin:after_reserve')
        self._assert_current()
        fp = sha(wire([dataclasses.asdict(r) for r in records]))
        value['entries'].append(dict(token=self.token, scope=scope, round_no=round_no,
            session_fingerprint=fp, phase='prepared', error=''))
        self._save(value, 'begin')
        return self.token

    def _value(self, token):
        if token != self.token:
            raise JournalChanged('token_mismatch')
        self._owns(token)
        self._assert_current()
        read = self._read()
        if read is None or read[1]['entries'][-1]['token'] != token:
            raise JournalChanged('entry_mismatch')
        return read[1]

    def complete(self, token):
        value = self._value(token)
        value['entries'][-1]['phase'] = 'normal_complete'
        self._save(value, 'complete')
        self.fault('complete:before_release')
        self._owns(token)
        self.reservation.unlink()
        self.fault('complete:after_release')

    def block(self, token, error, cancelled=False):
        if token != self.token:
            raise JournalChanged('token_mismatch')
        if not self.reservation.exists():
            self._reserve(token)
        value = self._value(token)
        value['entries'][-1].update(phase='cancelled_blocked' if cancelled else 'failed_blocked'
            , error=type(error).__name__)
        self._save(value, 'block')

class DurableShadowRound:

    def __init__(self, inner, journal, context):
        self.inner = inner
        self.journal = journal
        self.context = context
        self.blocked = False
        self.audit = []

    async def __call__(self, round_no):
        import asyncio
        if self.blocked:
            raise JournalRefused('wrapper_blocked')
        scope = self.context['scope_of']()
        records = self.context['session'].records()
        assert_tool_history_shape(records)
        token = self.journal.begin(scope, round_no, records)
        row = dict(round_no=round_no, token=token, status='prepared')
        self.audit.append(row)
        try:
            result = await self.inner(round_no)
            if (
                any((getattr(result, flag, False) for flag in ['failed', 'overflowed',
                    'over_budget']))
            ):
                raise JournalRefused('round_outcome_not_complete')
            assert_tool_history_shape(self.context['session'].records())
            self.journal.complete(token)
            row['status'] = 'normal_complete'
            return result
        except BaseException as primary:
            self.blocked = True
            row['status'] = (
                'cancelled_blocked' if isinstance(primary, asyncio.CancelledError) else
                    'failed_blocked'
            )
            try:
                self.journal.block(token, primary, isinstance(primary, asyncio.CancelledError))
            except Exception as secondary:
                row['journal_error'] = type(secondary).__name__
                primary.add_note('shadow_journal_cleanup_failed:' + type(secondary).__name__)
            raise

# Migrated source notes (non-executable):
# Migrated source notes (non-executable):
# Completion acknowledgement may fail after reservation release. Re-quarantine.
