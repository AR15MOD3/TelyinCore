"""Durable local-owner due cards, independent of persona speech and its budget.

The journal is the only persistence owner: staging and acknowledgement are
separate transactions. A crash can repeat the same display_id, never invent an
acknowledgement or resolve the underlying reminder. No outbound channel tool,
model request, user-read receipt, or session-history rewrite is involved.
"""
from datetime import datetime
import hashlib
from .retrieval_scope import RetrievalScope, OWNER

OWNER_VIEW = RetrievalScope(OWNER, frozenset([OWNER]))


class ReminderNotices:
    def __init__(self, journal):
        self.journal = journal

    @staticmethod
    def _schema(db):
        db.execute('''CREATE TABLE IF NOT EXISTS due_notices (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, timer_id TEXT UNIQUE NOT NULL,
            scope TEXT NOT NULL, display_id TEXT UNIQUE NOT NULL, text TEXT NOT NULL,
            staged_at TEXT NOT NULL, emitted_at TEXT)''')
        db.execute('CREATE INDEX IF NOT EXISTS due_notices_scope ON due_notices(scope,seq)')

    @staticmethod
    def _frame(row):
        return {'kind': 'system-notice', 'who': 'host', 'host_notice': True,
                'at': datetime.fromisoformat(row['staged_at']).strftime('%Y-%m-%dT%H:%M'),
                'text': row['text'], 'display_id': row['display_id'],
                'timer_id': row['timer_id']}

    def flush(self, *, at, allowed, write, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid notification page')
        if not self.journal.path.exists() or not allowed():
            return 0
        with self.journal.connect() as db:
            self._schema(db)
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('''SELECT id,due_at,what FROM reminders r
                WHERE r.resolved_at IS NULL AND r.scope=? AND r.state!='prepared'
                AND NOT EXISTS (SELECT 1 FROM due_notices n WHERE n.timer_id=r.id)
                ORDER BY r.seq LIMIT ?''', (OWNER, limit)).fetchall()
            for row in rows:
                display_id = 'reminder-notice:' + hashlib.sha256(row['id'].encode()).hexdigest()
                # This is quoted historical content, not an instruction or IRIS's voice.
                text = ('〔Core 系统到期通知〕\n事项（历史安排）：' + row['what'][:2000]
                        + ('…（完整内容请查看提醒记录）' if len(row['what']) > 2000 else '')
                        + '\n原定：' + row['due_at']
                        + '\n事项仍待确认。此卡片不代表你已读或事情已完成；IRIS 自主决定是否回应。')
                db.execute('INSERT INTO due_notices(timer_id,scope,display_id,text,staged_at) VALUES(?,?,?,?,?)',
                           (row['id'], OWNER, display_id, text, at))
                self.journal._receipt(db, row['id'], at, 'system_notice_staged',
                    {'display_id': display_id, 'human_read_confirmed': False, 'task_completed': False})
            if not allowed():
                db.rollback()
                return 0
        # Outbox is durable before attempting the UI. Serialize with resolution
        # and recheck pause/visibility at the actual publication boundary.
        emitted = 0
        for _ in range(limit):
            with self.journal.connect() as db:
                self._schema(db)
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('''SELECT n.* FROM due_notices n JOIN reminders r ON r.id=n.timer_id
                    WHERE n.scope=? AND r.scope=? AND r.resolved_at IS NULL AND n.emitted_at IS NULL
                    ORDER BY n.seq LIMIT 1''', (OWNER, OWNER)).fetchone()
                if row is None or not allowed():
                    return emitted
                write(self._frame(row))
                db.execute('UPDATE due_notices SET emitted_at=? WHERE seq=?', (at, row['seq']))
                self.journal._receipt(db, row['timer_id'], at, 'system_notice_emitted',
                    {'display_id': row['display_id'], 'human_read_confirmed': False,
                     'task_completed': False, 'meaning': 'Local UI accepted the card; not a delivery/read receipt from a person.'})
                emitted += 1
        return emitted

    def history(self, *, visible, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid notification history limit')
        if not self.journal.path.exists() or not visible():
            return []
        with self.journal.connect() as db:
            # Merely opening history does not stage or publish a new notice.
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='due_notices'").fetchone():
                return []
            rows = db.execute('''SELECT n.* FROM due_notices n JOIN reminders r ON r.id=n.timer_id
                WHERE n.scope=? AND r.scope=? AND n.emitted_at IS NOT NULL ORDER BY n.seq DESC LIMIT ?''',
                (OWNER, OWNER, limit + 1)).fetchall()
            if not visible():
                return []
        frames = [self._frame(row) for row in reversed(rows[:limit])]
        if len(rows) > limit:
            frames.insert(0, {'kind': 'system-notice', 'who': 'host', 'host_notice': True,
                'at': frames[0]['at'], 'display_id': 'reminder-notice:older-history',
                'text': '这里只展示最近 100 张系统到期通知；更早的通知回执仍保存在提醒记录中。'})
        return frames


async def watch(console, *, interval=1):
    """One host lifecycle task; resume/recovery needs no user message or model.

    Stat is only a wake-up hint. Actual selection and permissions are always
    checked inside the SQLite transactions. Busy/disk/UI errors retain the
    outbox and retry; a stopped runtime never publishes more cards.
    """
    import asyncio
    from .file_signature import signature
    seen = object()
    while not console.activity_control.stopping():
        if console.activity_control.paused() or not console._reminder_notice_visible():
            seen = object()
        else:
            path = console.reminder_notices.journal.path
            try:
                try: current = signature(path)
                except FileNotFoundError: current = None
                if current != seen or getattr(console, '_reminder_notice_retry', False):
                    count = console.flush_reminder_notices()
                    if count >= 20 or getattr(console, '_reminder_notice_retry', False):
                        seen = object()
                    else:
                        try: seen = signature(path)
                        except FileNotFoundError: seen = None
            except (OSError, ValueError, RuntimeError):
                seen = object()  # Retry the auxiliary read; never infer no reminders.
        await asyncio.sleep(interval)
