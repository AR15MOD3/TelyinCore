"""Per-Bot sticker labels and serialized, content-deduplicated QQ collection."""
import hashlib
import io
import json
from pathlib import Path
import sqlite3

from PIL import Image


def content_key(raw):
    """Normalize still pixels. Preserve distinct animations even with equal first frames."""
    with Image.open(io.BytesIO(raw)) as image:
        if getattr(image, 'is_animated', False):
            return hashlib.sha256(b'animated\0' + raw).hexdigest()
        image = image.convert('RGBA')
        return hashlib.sha256(str(image.size).encode() + b'\0' + image.tobytes()).hexdigest()


def labels(description, tags):
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 300:
        raise ValueError('表情描述需要 1 到 300 字')
    if (not isinstance(tags, list) or not 1 <= len(tags) <= 10
            or any(not isinstance(t, str) or not 1 <= len(t.strip()) <= 24 for t in tags)):
        raise ValueError('需要 1 到 10 个标签，每个标签 1 到 24 字')
    return description.strip(), list(dict.fromkeys(t.strip() for t in tags))


def operator_labels(description, tags):
    # A quick upload can be labelled later; leave unknown fields genuinely empty.
    if not isinstance(description, str) or not isinstance(tags, list):
        raise ValueError('描述或标签格式无效')
    desc, normalized = labels(description.strip() or '待观察', tags or ['待观察'])
    return (desc if description.strip() else ''), (normalized if tags else [])


class StickerStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    def connect(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.directory/'stickers.sqlite3', timeout=35)
        db.execute('''CREATE TABLE IF NOT EXISTS stickers (
            digest TEXT PRIMARY KEY, md5 TEXT NOT NULL, emoji_id TEXT UNIQUE,
            description TEXT NOT NULL, tags TEXT NOT NULL, source TEXT NOT NULL,
            reason TEXT NOT NULL, status TEXT NOT NULL)''')
        db.execute('CREATE TABLE IF NOT EXISTS aliases (emoji_id TEXT PRIMARY KEY, digest TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS sources (digest TEXT, source TEXT, PRIMARY KEY(digest,source))')
        db.execute('CREATE TABLE IF NOT EXISTS observations (digest TEXT PRIMARY KEY, description TEXT, tags TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS operator_reasons (digest TEXT PRIMARY KEY)')
        db.execute('CREATE TABLE IF NOT EXISTS corrections (digest TEXT PRIMARY KEY, description TEXT, tags TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS preferences (name TEXT PRIMARY KEY, enabled INTEGER NOT NULL)')
        db.commit()
        return db

    def auto_collect(self, enabled=None):
        if enabled is not None and type(enabled) is not bool:
            raise ValueError('自主收藏开关需要布尔值')
        db = self.connect()
        try:
            with db:
                if enabled is not None:
                    db.execute("INSERT OR REPLACE INTO preferences VALUES ('auto_collect', ?)", (int(enabled),))
                row = db.execute("SELECT enabled FROM preferences WHERE name='auto_collect'").fetchone()
                return bool(row[0]) if row else True
        finally:
            db.close()

    def get(self, emoji_id):
        db = self.connect()
        try:
            row = db.execute(
                "SELECT description,tags FROM stickers WHERE status='ready' AND "
                "(emoji_id=? OR digest IN (SELECT digest FROM aliases WHERE emoji_id=?))",
                (emoji_id, emoji_id)).fetchone()
            return {'description': row[0], 'tags': json.loads(row[1])} if row and row[0] else None
        finally:
            db.close()

    @staticmethod
    def _known_labels(db, key):
        row = db.execute("""SELECT description,tags FROM stickers WHERE digest=? AND status='ready' AND description!=''
                UNION ALL SELECT description,tags FROM observations WHERE digest=? LIMIT 1""", (key, key)).fetchone()
        return {'description': row[0], 'tags': json.loads(row[1])} if row and row[0] else None

    def recognition(self, raw):
        if not (self.directory / 'stickers.sqlite3').exists():
            return None
        key = content_key(raw)
        db = self.connect()
        try:
            return self._known_labels(db, key)
        finally:
            db.close()

    @staticmethod
    def _remember_correction(db, key, desc, encoded):
        db.execute('INSERT OR REPLACE INTO corrections VALUES (?,?,?)', (key, desc, encoded))
        db.execute('INSERT OR REPLACE INTO observations VALUES (?,?,?)', (key, desc, encoded))
        db.execute('UPDATE stickers SET description=?, tags=? WHERE digest=?', (desc, encoded, key))

    def correct(self, description, tags, *, raw=None, emoji_id=None):
        desc, tags = labels(description, tags)
        key = content_key(raw) if raw is not None else None
        db = self.connect()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                if key is None:
                    row = db.execute("""SELECT digest FROM stickers WHERE emoji_id=? AND status='ready'
                        UNION SELECT a.digest FROM aliases a JOIN stickers s USING(digest)
                        WHERE a.emoji_id=? AND s.status='ready'""", (emoji_id, emoji_id)).fetchone()
                    if not row:
                        return None
                    key = row[0]
                self._remember_correction(db, key, desc, json.dumps(tags, ensure_ascii=False))
            return dict(description=desc, tags=tags)
        finally:
            db.close()

    def corrected_labels(self, raw):
        if not (self.directory / 'stickers.sqlite3').is_file():
            return None
        return self.correction_for_digest(content_key(raw))

    def correction_for_digest(self, key):
        path = self.directory/'stickers.sqlite3'
        if not path.is_file():
            return None
        db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=.1)
        try:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='corrections'").fetchone():
                return None
            row = db.execute('SELECT description,tags FROM corrections WHERE digest=?', (key,)).fetchone()
            return dict(description=row[0], tags=json.loads(row[1])) if row else None
        finally:
            db.close()

    def observe(self, raw, description, tags):
        description, tags = labels(description, tags)
        key, encoded = content_key(raw), json.dumps(tags, ensure_ascii=False)
        db = self.connect()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                corrected = db.execute('SELECT description,tags FROM corrections WHERE digest=?', (key,)).fetchone()
                if corrected:
                    return dict(description=corrected[0], tags=json.loads(corrected[1]))
                known = self._known_labels(db, key)
                if known:
                    return known
                db.execute('INSERT OR REPLACE INTO observations VALUES (?,?,?)', (key, description, encoded))
                db.execute("UPDATE stickers SET description=?, tags=? WHERE digest=? AND status='ready'",
                           (description, encoded, key))
            return dict(description=description, tags=tags)
        finally:
            db.close()

    def ready_by_digest(self, key):
        db = self.connect()
        try:
            row = db.execute("SELECT emoji_id,description,tags FROM stickers WHERE digest=? AND status='ready'",
                             (key,)).fetchone()
            return dict(emoji_id=row[0], description=row[1], tags=json.loads(row[2])) if row else None
        finally:
            db.close()

    def fill_upload(self, key, desc, tags, reason):
        db = self.connect()
        try:
            with db:
                db.execute("""UPDATE stickers SET description=CASE WHEN description='' THEN ? ELSE description END,
                    tags=CASE WHEN tags='[]' THEN ? ELSE tags END, reason=CASE WHEN reason='' THEN ? ELSE reason END,
                    source=CASE WHEN reason='' AND source='operator' THEN 'operator-upload' ELSE source END
                    WHERE digest=? AND status='ready'""", (desc, json.dumps(tags, ensure_ascii=False), reason, key))
        finally:
            db.close()

    def details(self, ids):
        db = self.connect()
        try:
            rows = db.execute("""SELECT s.emoji_id,s.description,s.tags,s.reason,s.source,
                (s.source='operator' OR o.digest IS NOT NULL) FROM stickers s
                LEFT JOIN operator_reasons o USING(digest) WHERE s.status='ready'
                UNION ALL SELECT a.emoji_id,s.description,s.tags,s.reason,s.source,
                (s.source='operator' OR o.digest IS NOT NULL) FROM aliases a
                JOIN stickers s USING(digest) LEFT JOIN operator_reasons o USING(digest)
                WHERE s.status='ready'""")
            origins = {'account-favorite': '账号原有·来源未记录', 'operator': '用户添加',
                       'operator-upload': '用户添加'}
            return {key: dict(description=desc, tags=json.loads(tags), reason=reason,
                              editedReason=bool(edited), addedBy=origins.get(source, 'Bot 自主收藏'))
                    for key, desc, tags, reason, source, edited in rows if key in ids}
        finally:
            db.close()

    def forget_favorite(self, emoji_id):
        db = self.connect()
        try:
            with db:
                db.execute('DELETE FROM aliases WHERE emoji_id=?', (emoji_id,))
                for (key,) in db.execute('SELECT digest FROM stickers WHERE emoji_id=?', (emoji_id,)).fetchall():
                    alias = db.execute('SELECT emoji_id FROM aliases WHERE digest=? LIMIT 1', (key,)).fetchone()
                    if alias:
                        db.execute('UPDATE stickers SET emoji_id=? WHERE digest=?', (alias[0], key))
                    else:
                        db.execute('DELETE FROM stickers WHERE digest=?', (key,))
                        db.execute('DELETE FROM sources WHERE digest=?', (key,))
                        db.execute('DELETE FROM operator_reasons WHERE digest=?', (key,))
        finally:
            db.close()

    def annotate(self, raw, emoji_id, description, tags, *, reason=None, corrected=False):
        description, tags = (operator_labels(description, tags) if reason is not None else labels(description, tags))
        key = content_key(raw)
        db = self.connect()
        try:
            with db:
                if reason is not None and (not isinstance(reason, str) or len(reason) > 300):
                    raise ValueError('收藏理由最多 300 字')
                db.execute('BEGIN IMMEDIATE')
                previous = db.execute('SELECT description,tags FROM corrections WHERE digest=?', (key,)).fetchone()
                edited = False
                if reason is not None:
                    old = db.execute("SELECT description,tags FROM stickers WHERE digest=?", (key,)).fetchone()
                    edited = not old or (old[0], json.loads(old[1])) != (description, tags)
                if corrected or (reason is not None and (previous or edited)):
                    self._remember_correction(db, key, description, json.dumps(tags, ensure_ascii=False))
                elif previous:
                    description, tags = previous[0], json.loads(previous[1])
                elif reason is None:
                    known = self._known_labels(db, key)
                    if known:
                        description, tags = known['description'], known['tags']
                db.execute('INSERT OR IGNORE INTO aliases SELECT emoji_id,digest FROM stickers '
                           'WHERE digest=? AND emoji_id IS NOT NULL', (key,))
                db.execute('INSERT OR REPLACE INTO aliases VALUES (?,?)', (emoji_id, key))
                db.execute('''INSERT INTO stickers VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(digest) DO UPDATE SET emoji_id=excluded.emoji_id,
                    description=excluded.description,tags=excluded.tags,status='ready' ''',
                           (key, hashlib.md5(raw).hexdigest(), emoji_id, description,
                            json.dumps(tags, ensure_ascii=False), 'account-favorite', '', 'ready'))
                if reason is not None:
                    changed = db.execute('UPDATE stickers SET reason=? WHERE digest=? AND reason!=?',
                                         (reason.strip(), key, reason.strip()))
                    if changed.rowcount:
                        db.execute('INSERT OR IGNORE INTO operator_reasons VALUES (?)', (key,))
        finally:
            db.close()
        return self.get(emoji_id)

    def collect(self, raw, description, tags, source, reason, *, favorites, add, check, operator=False):
        description, tags = operator_labels(description, tags) if operator else labels(description, tags)
        if not isinstance(reason, str) or len(reason.strip()) > 300 or (not operator and not reason.strip()):
            raise ValueError('请记录自己喜欢或想收藏这个表情的理由')
        key, md5 = content_key(raw), hashlib.md5(raw).hexdigest()
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            corrected = db.execute('SELECT description,tags FROM corrections WHERE digest=?', (key,)).fetchone()
            if corrected:
                description, tags = corrected[0], json.loads(corrected[1])
            row = db.execute('SELECT emoji_id,status,source,reason FROM stickers WHERE digest=?', (key,)).fetchone()
            if row and row[1] == 'ready':
                db.execute('INSERT OR IGNORE INTO sources VALUES (?,?)', (key, source))
                db.commit()
                return {'emoji_id': row[0], 'duplicate': True, **(self.get(row[0]) or {'description':'', 'tags':[]})}
            # Reconcile a previous uncertain add with QQ's current favorites.
            known = next((x['emoji_id'] for x in favorites()
                          if str(x.get('md5', '')).lower() == md5
                          or md5.upper() in str(x.get('emoji_id', '')).upper()), None)
            if row and not known:
                raise ValueError('上次收藏结果尚未确认，未重复提交；请先检查 QQ 收藏列表')
            existed_in_qq = bool(known)
            origin = row[2] if row else ('account-favorite' if known else source)
            recorded_reason = row[3] if row else reason.strip()
            check()
            db.execute('INSERT OR IGNORE INTO sources VALUES (?,?)', (key, source))
            db.execute('INSERT OR REPLACE INTO stickers VALUES (?,?,?,?,?,?,?,?)',
                       (key, md5, known, description, json.dumps(tags, ensure_ascii=False),
                        origin, recorded_reason, 'pending'))
            db.commit()  # Intent precedes the remote effect, including timeouts.
            if not known:
                known = add(raw)
            check()
            if not isinstance(known, str) or not known:
                raise ValueError('QQ 收藏结果未返回表情编号，已保留待核验记录')
            with db:
                db.execute("UPDATE stickers SET emoji_id=?,status='ready' WHERE digest=?", (known, key))
            return {'emoji_id': known, 'duplicate': bool(row) or existed_in_qq,
                    'description': description, 'tags': tags}
        finally:
            db.close()
