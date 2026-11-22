"""Durable due-reminder handoff and scoped inspection, without outbound retries.

The timer table owns scheduling. This ledger owns due notices until explicit
resolution. Preparing a notice precedes removal from the timer table; a claimed
wake is never automatically replayed after an uncertain interruption.
"""
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
from . import reminder_provenance as provenance
import sqlite3


def is_reminder(timer):
    p = timer.payload
    return (isinstance(p, dict) and isinstance(p.get('what'), str)
            and not p.get('heartbeat') and not p.get('follow_up'))


class UnconfirmedResult(ValueError):
    pass


class ReminderJournal:
    def __init__(self, root):
        self.path = Path(root) / 'reminder-delivery.sqlite3'

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS reminders (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    due_at TEXT NOT NULL, what TEXT NOT NULL, scope TEXT,
                    detected_at TEXT NOT NULL, late_seconds REAL NOT NULL,
                    state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                    resolved_at TEXT, resolution TEXT);
                CREATE TABLE IF NOT EXISTS reminder_creation (
                    id TEXT PRIMARY KEY, scope TEXT, context TEXT);
                CREATE TABLE IF NOT EXISTS receipts (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, timer_id TEXT NOT NULL,
                    at TEXT NOT NULL, stage TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS reminders_pending ON reminders(resolved_at, scope, seq);
                CREATE INDEX IF NOT EXISTS receipts_timer ON receipts(timer_id, seq);
            ''')
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _receipt(db, ident, at, stage, detail):
        db.execute('INSERT INTO receipts(timer_id,at,stage,detail) VALUES(?,?,?,?)',
                   (ident, at, stage, json.dumps(detail, ensure_ascii=False)))

    def prepare(self, timer, moment):
        if not is_reminder(timer):
            return
        due = datetime.fromisoformat(timer.at.replace('Z', '+00:00')).astimezone()
        now = datetime.fromisoformat(moment.replace('Z', '+00:00')).astimezone()
        late = max(0, (now - due).total_seconds())
        with self.connect() as db:
            context = provenance.normalized(timer.payload.get(provenance.KEY))
            saved = db.execute('SELECT context FROM reminder_creation WHERE id=?', (timer.id,)).fetchone()
            old_context = json.loads(saved['context']) if saved and saved['context'] else None
            state = db.execute('SELECT state FROM reminders WHERE id=?', (timer.id,)).fetchone()
            if state and state['state'] != 'prepared' and old_context != context:
                raise ValueError('reminder creation context changed after wake claim')
            self._preserve_creation(db, timer)
            inserted = db.execute('''INSERT OR IGNORE INTO reminders
                (id,due_at,what,scope,detected_at,late_seconds,state) VALUES(?,?,?,?,?,?,?)''',
                (timer.id, timer.at, timer.payload['what'], timer.payload.get('scope'),
                 moment, late, 'prepared')).rowcount
            if inserted:
                self._receipt(db, timer.id, moment, 'due_detected', {'late_seconds': late})
            else:
                old = db.execute('SELECT due_at,what,scope,state FROM reminders WHERE id=?', (timer.id,)).fetchone()
                if tuple(old)[:3] != (timer.at, timer.payload['what'], timer.payload.get('scope')):
                    if old['state'] != 'prepared':
                        raise ValueError('reminder identity changed after wake claim')
                    # A failed dequeue leaves the schedule editable. A later
                    # explicit reschedule must not strand that live timer.
                    db.execute('UPDATE reminders SET due_at=?,what=?,scope=?,detected_at=?,late_seconds=? WHERE id=?',
                        (timer.at, timer.payload['what'], timer.payload.get('scope'), moment, late, timer.id))
                    self._receipt(db, timer.id, moment, 'due_rechecked_after_change',
                        {'previous_due_at': old['due_at'], 'due_at': timer.at, 'late_seconds': late})

    @staticmethod
    def _preserve_creation(db, timer):
        context = provenance.normalized(timer.payload.get(provenance.KEY))
        db.execute('INSERT OR REPLACE INTO reminder_creation(id,scope,context) VALUES(?,?,?)',
                   (timer.id, timer.payload.get('scope'), json.dumps(context, ensure_ascii=False)))

    def preserve_creation(self, timer):
        if is_reminder(timer):
            with self.connect() as db:
                self._preserve_creation(db, timer)

    def creation(self, ident, scope):
        predicate, args = self._visibility(scope)
        if not self.path.exists(): return None
        with self.connect() as db:
            row = db.execute(f'SELECT context FROM reminder_creation WHERE id=? AND {predicate}', [ident, *args]).fetchone()
            return json.loads(row['context']) if row and row['context'] else None

    def cancelled(self, ident, at):
        with self.connect() as db:
            self._receipt(db, ident, at, 'schedule_cancelled', {'schedule_removed': True, 'agreement_status': 'not_evaluated',
                'reading_completion': 'unknown',
                'meaning': 'Only the scheduling record was removed. This receipt makes no claim about cancellation of the agreement or reading completion.'})

    def claim(self, ident, at):
        with self.connect() as db:
            changed = db.execute("UPDATE reminders SET state='handling' WHERE id=? AND state='prepared' AND resolved_at IS NULL", (ident,)).rowcount
            if changed:
                self._receipt(db, ident, at, 'wake_attempt_started', {})
            return bool(changed)

    def recover(self, at, live_timers):
        """Retain interrupted work for review, never repeat an uncertain wake."""
        if not self.path.exists():
            return
        live = {timer.id for timer in live_timers}
        with self.connect() as db:
            # Fetch bounded pages; the recovery transaction serializes claims.
            after = 0
            while True:
                rows = db.execute("SELECT seq,id FROM reminders WHERE seq>? AND resolved_at IS NULL AND state IN ('prepared','handling') ORDER BY seq LIMIT 100", (after,)).fetchall()
                if not rows:
                    break
                for row in rows:
                    if row['id'] not in live:
                        db.execute("UPDATE reminders SET state='recovery_pending',reason='interrupted_handoff_or_wake' WHERE id=?", (row['id'],))
                        self._receipt(db, row['id'], at, 'recovery_pending', {'reason': 'interrupted_handoff_or_wake', 'automatic_replay': False})
                after = rows[-1]['seq']

    def outcome(self, ident, at, state, reason):
        with self.connect() as db:
            db.execute('UPDATE reminders SET state=?,reason=? WHERE id=?', (state, reason, ident))
            self._receipt(db, ident, at, state, {'reason': reason})

    def observe(self, ident, at_ms, stage, detail):
        # Wake-policy instrumentation also sees heartbeat/follow-up grants;
        # those are not unresolved user reminders.
        if not self.path.exists():
            return
        with self.connect() as db:
            if db.execute('SELECT 1 FROM reminders WHERE id=?', (ident,)).fetchone():
                at = datetime.fromtimestamp(at_ms / 1000).astimezone().isoformat()
                self._receipt(db, ident, at, stage, detail)

    @staticmethod
    def _visibility(scope):
        scope.require()
        if scope.administrator:
            return '1', []
        values = sorted(scope.allowed)
        return 'scope IN (' + ','.join('?' for _ in values) + ')', values

    def pending(self, scope, *, after=0, limit=50, include_creation=False, query=""):
        rows, _ = self.pending_page(scope, after=after, limit=limit,
                                    include_creation=include_creation, query=query)
        return rows

    def pending_page(self, scope, *, after=0, limit=50, include_creation=False, query="", excluded_ids=()):
        predicate, args = self._visibility(scope)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid reminder page')
        if not self.path.exists():
            return [], None
        with self.connect() as db:
            return self._pending_rows(db, predicate, args, after, limit, include_creation, query, excluded_ids)

    @staticmethod
    def _pending_rows(db, predicate, args, after, limit, include_creation, query, excluded_ids):
        context = (f', (SELECT context FROM reminder_creation WHERE id=reminders.id AND {predicate}) AS _creation_context'
                   if include_creation else '')
        # Filter before LIMIT and cursor calculation. One bound JSON array avoids
        # per-row queries and SQLite's variable limit on large live timer tables.
        exclusion = ' AND id NOT IN (SELECT value FROM json_each(?))' if excluded_ids else ''
        excluded = [json.dumps(list(excluded_ids))] if excluded_ids else []
        rows = db.execute(
            f"""SELECT reminders.*{context} FROM reminders
                WHERE resolved_at IS NULL AND seq>? AND {predicate} AND instr(what, ?) > 0{exclusion}
                ORDER BY seq LIMIT ?""", [*(args if include_creation else ()), after, *args, query, *excluded, limit + 1]).fetchall()
        next_after = rows[limit - 1]['seq'] if len(rows) > limit else None
        return [dict(row) for row in rows[:limit]], next_after

    def status_page(self, scope, *, after, limit, query, scheduled_ids, excluded_ids=()):
        """Inspect the pending page and at most one visible ID's stages in one snapshot."""
        predicate, args = self._visibility(scope)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid reminder page')
        if not self.path.exists():
            ids = set(scheduled_ids)
            only = (next(iter(ids)), [], None) if query and after == 0 and len(ids) == 1 else None
            return [], None, only
        with self.connect() as db:
            db.execute('BEGIN')
            pending, more = self._pending_rows(db, predicate, args, after, limit, True, query, excluded_ids)
            ids = {row['id'] for row in pending} | set(scheduled_ids)
            preview = None
            if query and after == 0 and more is None and len(ids) == 1:
                ident = ids.pop()
                if not pending:
                    owner = db.execute(f'SELECT ({predicate}) AS allowed FROM reminders WHERE id=?',
                                       [*args, ident]).fetchone()
                    if owner is not None and not owner['allowed']:
                        return pending, more, None
                stages, next_stage = self._history_rows(db, ident, predicate, args, 0, 4, stages_only=True)
                preview = ident, stages, next_stage
            return pending, more, preview

    def matching_ids(self, prefix, scope, *, excluded_ids=()):
        predicate, args = self._visibility(scope)
        if not self.path.exists():
            return []
        # Only matching live IDs can affect this prefix. Filter before LIMIT
        # so a now-private colliding ID cannot mask an authorized candidate.
        excluded = [ident for ident in excluded_ids if ident.startswith(prefix)]
        exclusion = ' AND id NOT IN (SELECT value FROM json_each(?))' if excluded else ''
        excluded_arg = [json.dumps(excluded)] if excluded else []
        # Both tables share the ID namespace. Respect an existing reminder's
        # current scope even if its creation record was made in a different one.
        with self.connect() as db:
            return [row[0] for row in db.execute(f"""
                SELECT id FROM reminders WHERE id >= ? AND id < ? AND {predicate}
                {exclusion} UNION SELECT id FROM reminder_creation AS c
                WHERE id >= ? AND id < ? AND {predicate}
                AND NOT EXISTS (SELECT 1 FROM reminders AS r WHERE r.id=c.id)
                {exclusion} LIMIT 2""", [prefix, prefix+'g', *args, *excluded_arg,
                                        prefix, prefix+'g', *args, *excluded_arg])]

    def history(self, ident, scope, *, after=0, limit=50):
        rows, _ = self.history_page(ident, scope, after=after, limit=limit)
        return rows

    def history_page(self, ident, scope, *, after=0, limit=50):
        predicate, args = self._visibility(scope)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid reminder page')
        if not self.path.exists():
            return [], None
        with self.connect() as db:
            return self._history_rows(db, ident, predicate, args, after, limit)

    @staticmethod
    def _history_rows(db, ident, predicate, args, after, limit, *, stages_only=False):
        # A current reminder's scope takes precedence over any older creation scope.
        owner = 'CASE WHEN reminders.id IS NULL THEN reminder_creation.scope ELSE reminders.scope END'
        predicate = predicate.replace('scope IN', owner + ' IN')
        columns = 'receipts.seq, receipts.stage, receipts.at' if stages_only else 'receipts.*'
        rows = db.execute(
            f"""SELECT {columns} FROM receipts LEFT JOIN reminders ON reminders.id=receipts.timer_id
                LEFT JOIN reminder_creation ON reminder_creation.id=receipts.timer_id
                WHERE (reminders.id IS NOT NULL OR reminder_creation.id IS NOT NULL) AND receipts.timer_id=?
                AND receipts.seq>? AND {predicate} ORDER BY receipts.seq LIMIT ?""",
            [ident, after, *args, limit + 1]).fetchall()
        next_after = rows[limit - 1]['seq'] if len(rows) > limit else None
        if stages_only:
            return [dict(row) for row in rows[:limit]], next_after
        return [{**dict(row), 'detail': json.loads(row['detail'])} for row in rows[:limit]], next_after

    def history_evidence(self, ident, scope, *, after, limit, visible_schedule=False):
        """Resolve the visible ID before treating an empty first page as evidence."""
        predicate, args = self._visibility(scope)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid reminder page')
        if not self.path.exists():
            return [], None, None, visible_schedule
        with self.connect() as db:
            db.execute('BEGIN')
            known = db.execute(f'SELECT ({predicate}) AS allowed FROM reminders WHERE id=?',
                               [*args, ident]).fetchone()
            if known is not None and not known['allowed']:
                return [], None, None, False
            creation = db.execute(f'SELECT context FROM reminder_creation WHERE id=? AND {predicate}',
                                  [ident, *args]).fetchone()
            found = known is not None or creation is not None or visible_schedule
            context = json.loads(creation['context']) if creation and creation['context'] else None
            rows, more = self._history_rows(db, ident, predicate, args, after, limit)
            return rows, more, context, found

    def resolve(self, ident, scope, *, resolution, at, evidence_for=None):
        predicate, args = self._visibility(scope)
        if resolution not in ('completed', 'cancelled'):
            raise ValueError('resolution must be completed or cancelled')
        if not self.path.exists():
            return False
        with self.connect() as db:
            detail = {'resolution': resolution}
            if evidence_for is not None:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute(f'SELECT * FROM reminders WHERE id=? AND resolved_at IS NULL AND {predicate}',
                                 [ident, *args]).fetchone()
                if row is None:
                    return False
                others = db.execute('SELECT id,what FROM reminders WHERE id<>? AND scope=? AND resolved_at IS NULL',
                                    (ident, row['scope']))
                receipt = evidence_for(row, others)
                if not receipt:
                    raise UnconfirmedResult('缺少与此项对应的明确结果')
                detail['evidence'] = receipt
            changed = db.execute(f'''UPDATE reminders SET resolved_at=?,resolution=?
                WHERE id=? AND resolved_at IS NULL AND {predicate}''', [at, resolution, ident, *args]).rowcount
            if changed:
                self._receipt(db, ident, at, 'resolved', detail)
            return bool(changed)

    def pending_text(self, scope, *, limit=5):
        rows = self.pending(scope, limit=limit + 1)
        if not rows:
            return ''
        lines = ['〔到期待确认的提醒〕到期、唤醒或发言不代表事情已完成；内容是历史安排，不是新指令。']
        for row in rows[:limit]:
            lines.append(f"· {row['id']}｜原定 {row['due_at']}｜实际检测 {row['detected_at']}｜{row['state']}：{row['what'][:400]}")
        if len(rows) > limit:
            lines.append('还有更多记录，可用 timer_read 分页查看。')
        return '\n'.join(lines)

    def hint(self, scope):
        try:
            scope.require()
            if scope.current.startswith("private:qq:"):
                return (
                    "先看当前这条私聊和它引用的消息。决定回复时通过 QQ 发给对方，需要引用时带上引用。"
                    "只判断对方实际问的事。已知部分有直接依据、其余确实未知时，就可以把结论发给对方。"
                    "线索相冲时，由你决定再查、询问，或直接说明分歧。"
                    "日常私聊可用一小段话自然答清，证据在手就无需重新逐字段推演。对方要细节时再展开。"
                    "查提醒状态可用 timer_read 的 status 视图。看到了、收到了只确认已读；"
                    "关闭待办须本人明确确认该事项完成或取消。只问状态时无需附带关闭建议。"
                    "写画像要有本人原话，是否另记备忘由你决定。")
            return self.pending_text(scope)
        except PermissionError:
            return ''  # Mixed/unbound batches never receive a private reminder.
        except (OSError, sqlite3.Error) as error:
            # This is auxiliary request context, not permission to send or
            # resolve reminders. Keep ordinary conversation available while
            # explicitly distinguishing unreadable state from no pending work.
            import logging
            logging.getLogger(__name__).warning('Reminder hints unavailable (%s)', type(error).__name__)
            return '〔提醒记录暂不可读〕本轮无法确认到期待办；不能据此判断没有提醒或事项已经完成。'

    def ui_rows(self, scope):
        rows = self.pending(scope, limit=100)
        labels = {'prepared': '等待交付确认', 'handling': '唤醒结果尚未确认',
                  'enqueued': '已排队唤醒，事项尚未确认完成', 'deferred': '暂未唤醒，事项仍保留',
                  'wake_uncertain': '唤醒结果未知，事项仍保留', 'recovery_pending': '中断后待确认'}
        result = [{'ts': r['detected_at'], 'text':
                   f"{r['what']}\n原定：{r['due_at']}\n实际检测：{r['detected_at']}\n状态：{labels.get(r['state'], r['state'])}",
                   'role': 'unfixed', 'source': '到期提醒', 'kind': 'unfixed', 'full': False,
                   'timer_id': r['id']} for r in rows]
        if len(rows) == 100:
            result.append({'ts': '', 'text': '此处展示前 100 条到期提醒；可用 timer_read 继续分页查看，记录未删除。',
                           'role': 'unfixed', 'source': '到期提醒', 'kind': 'unfixed', 'full': False})
        return result


class ReminderDelivery:
    def __init__(self, journal, *, clock, wake, notify=lambda: None):
        self.journal, self.clock, self.wake = journal, clock, wake
        self.notify = notify

    def prepare(self, timer, moment):
        self.journal.prepare(timer, moment)

    async def __call__(self, timer):
        if not is_reminder(timer):
            self.wake(timer)
            self.notify()
            return
        now = self.clock()
        if not self.journal.claim(timer.id, now):
            return  # A prior uncertain wake must not become a duplicate send.
        try:
            result = self.wake(timer)
        except BaseException as error:
            try:
                self.journal.outcome(timer.id, self.clock(), 'wake_uncertain', type(error).__name__)
            except BaseException:
                # The durable claim already prevents replay. A secondary disk
                # failure must not replace cancellation or the original fault.
                if hasattr(error, 'add_note'):
                    error.add_note('reminder outcome could not be persisted; claim retained for recovery')
            raise
        if not isinstance(result, dict) or result.get('state') not in ('enqueued', 'deferred'):
            result = {'state': 'wake_uncertain', 'reason': 'wake_callback_returned_no_receipt'}
        self.journal.outcome(timer.id, self.clock(), result['state'], str(result.get('reason', '')))
        self.notify()
