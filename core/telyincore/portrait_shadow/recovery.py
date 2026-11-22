(
    'Read-only shadow cancellation diagnostics, NO'
    'T a resume/repair authority.\nNever instantiat'
    'es Session/EventStore: their constructors may'
    ' repair torn tails.\nOnly stopped, explicitly '
    'selected temporary stores are supported.\n'
)
from pathlib import Path
import collections, hashlib, json, stat
CANCEL_TEXT = '本次未写入画像（shadow审核轮次已取消）。'

class InputRejected(ValueError):
    pass

class SnapshotChanged(RuntimeError):
    pass

def digest(data):
    return hashlib.sha256(data).hexdigest()

def strict_object(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise InputRejected('duplicate_json_key')
        result[k] = v
    return result

class ReadOnlyRecoveryAudit:

    def __init__(self, root, *, scope, max_file=1048576, max_total=4194304):
        root = Path(root)
        if (
            not root.is_absolute() or root.is_symlink() or getattr(root, 'is_junction', lambda:
                False)()
        ):
            raise InputRejected('unsafe_root')
        if not scope or not isinstance(scope, str):
            raise InputRejected('trusted_scope_required')
        self.root = root.resolve(strict=True)
        self.scope = scope
        self.max_file = max_file
        self.max_total = max_total
        self.files = {}

    def _path(self, path):
        path = Path(path)
        if not path.is_absolute():
            raise InputRejected('absolute_path_required')
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise InputRejected('linked_input')
        for ancestor in path.parents:
            if ancestor == self.root:
                break
            if ancestor.is_symlink() or getattr(ancestor, 'is_junction', lambda: False)():
                raise InputRejected('linked_parent')
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.root) or not stat.S_ISREG(resolved.stat().st_mode):
            raise InputRejected('outside_or_nonfile')
        return resolved

    def _bytes(self, path):
        path = self._path(path)
        if path.stat().st_size > self.max_file:
            raise InputRejected('file_budget')
        with path.open('rb') as f:
            data = f.read(self.max_file + 1)
        if len(data) > self.max_file:
            raise InputRejected('file_budget')
        if sum((len(v) for v in self.files.values())) + len(data) > self.max_total:
            raise InputRejected('total_budget')
        self.files[path] = data
        return data

    def _rows(self, path):
        data = self._bytes(path)
        if data and (not data.endswith(b'\n')):
            raise InputRejected('torn_tail_no_repair')
        rows = []
        for line in data.decode('utf-8', errors='strict').splitlines():
            if not line:
                raise InputRejected('blank_line')
            row = json.loads(line, object_pairs_hook=strict_object)
            if not isinstance(row, dict):
                raise InputRejected('object_required')
            rows.append(row)
        return rows

    def assert_unchanged(self):
        for p, data in self.files.items():
            resolved = self._path(p)
            if resolved.stat().st_size != len(data):
                raise SnapshotChanged('input_changed')
            with resolved.open('rb') as f:
                now = f.read(self.max_file + 1)
            if now != data:
                raise SnapshotChanged('input_changed')

    def inspect(self, session_path, event_paths):
        if self.files:
            raise InputRejected('one_snapshot_per_audit')
        try:
            events = tuple(event_paths)
            if len(events) > 32:
                raise InputRejected('shard_budget')
            resolved = [self._path(x) for x in events]
            if len(set(resolved)) != len(resolved):
                raise InputRejected('duplicate_event_path')
            records = self._rows(session_path)
            envelopes = []
            for path in resolved:
                envelopes.extend(self._rows(path))
            issues = []
            pending = []
            paired = 0
            cancelled = 0
            from telyincore.note import Record
            for row in records:
                rec = Record(**row)
                if rec.kind not in ('function_call', 'function_result'):
                    continue
                if not rec.scope:
                    issues.append('legacy_scope_unknown')
                    continue
                if rec.scope != self.scope:
                    continue
                if not rec.name:
                    raise InputRejected('empty_tool_name')
                if rec.kind == 'function_call':
                    pending.append(rec.name)
                else:
                    if rec.name not in pending:
                        issues.append('result_without_call')
                    else:
                        pending.remove(rec.name)
                        paired += 1
                    if rec.text == CANCEL_TEXT:
                        cancelled += 1
            if pending:
                issues.append('calls_without_results')
            seen = set()
            cancel_events = []
            for e in envelopes:
                cursor = e.get('cursor')
                if type(cursor) is not int or cursor < 1:
                    raise InputRejected('invalid_event_cursor')
                if cursor in seen:
                    raise InputRejected('duplicate_event_cursor')
                seen.add(cursor)
                if e.get('type') != 'shadow-review-cancelled':
                    continue
                meta = e.get('meta')
                if not isinstance(meta, dict) or not isinstance(meta.get('scope'), str):
                    raise InputRejected('invalid_cancel_event')
                if meta['scope'] != self.scope:
                    continue
                if e.get('origin') != 'internal':
                    raise InputRejected('cancel_event_not_internal')
                receipts = meta.get('receipts')
                if not isinstance(receipts, list) or not receipts:
                    raise InputRejected('invalid_cancel_receipts')
                for r in receipts:
                    if (
                        not isinstance(r, dict) or not all((isinstance(r.get(k), str) and r[k]
                            for k in ['call_id', 'name', 'stage']))
                    ):
                        raise InputRejected('invalid_cancel_receipt')
                cancel_events.append(e)
            if cancelled and (not cancel_events):
                issues.append('cancel_event_missing')
            ids = (
                [(e['meta'].get('round_no'), r['call_id']) for e in cancel_events for r in e[
                    'meta']['receipts']]
            )
            if len(ids) != len(set(ids)):
                issues.append('duplicate_cancel_receipt_identity')
            if cancel_events and (not cancelled):
                issues.append('cancel_event_without_cancel_receipt')
            self.assert_unchanged()
            return (
                dict(status='blocked_disk_inconsistency' if issues else
                    'disk_shape_consistent_delivery_unproven' if cancelled else
                        'no_cancel_artifact_observed', issues=sorted(set(issues)), pending_count
                            =len(pending), paired_count=paired, cancelled_receipts=cancelled,
                                cancel_events=len(cancel_events), ui_delivery='unproven',
                                    event_to_session_association=
                                        'unproven_no_shared_durable_call_id', resume_allowed=
                                            False, repair_performed=False,
                                                profile_write_authorized=False, input_hashes={
                                                    str(k): digest(v) for k, v in self.files.
                                                        items()})
            )
        except (OSError, UnicodeError, ValueError, TypeError, SnapshotChanged) as exc:
            return (
                dict(status='blocked_invalid_or_changed_input', issues=[type(exc).__name__ + ':'
                    + str(exc)], resume_allowed=False, repair_performed=False,
                        profile_write_authorized=False, input_hashes={str(k): digest(v) for k, v
                            in self.files.items()})
            )
