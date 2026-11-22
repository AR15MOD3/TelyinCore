"""Local, opt-in, per-Bot synthetic voice review library. Never trains or deletes.

WAV and provenance commit in one SQLite transaction. No QQ addresses, incoming
ASR recordings, credentials, or shared TTS-server history are imported.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid
import wave


class VoiceCorpus:
    MAX_BYTES = 512 * 1024 * 1024
    MAX_SAMPLES = 5000
    MAX_WAV = 8 * 1024 * 1024

    def __init__(self, root: Path, config_path: Path):
        self.root = Path(root).resolve()
        # Both plugin and control use the effective config path. The baseline
        # and every non-baseline Bot have different stable config locations.
        identity = os.path.normcase(str(Path(config_path).resolve()))
        self.scope = hashlib.sha256(identity.encode('utf-8')).hexdigest()
        self.folder = self.root / 'data' / 'qq-voice-corpus' / self.scope
        self.db = self.folder / 'corpus.sqlite3'

    @staticmethod
    def _plain(path: Path, directory: bool):
        s = path.lstat()
        if (getattr(s, 'st_file_attributes', 0) & 0x400
                or stat.S_ISLNK(s.st_mode)
                or (not stat.S_ISDIR(s.st_mode) if directory else
                    not stat.S_ISREG(s.st_mode) or s.st_nlink != 1)):
            raise ValueError('素材库路径不安全')

    def _connect(self, create=False):
        current = self.root
        self._plain(current, True)
        for part in ('data', 'qq-voice-corpus', self.scope):
            current = current / part
            if not current.exists():
                if not create:
                    return None
                current.mkdir(exist_ok=True)
            self._plain(current, True)
        for suffix in ('', '-journal', '-wal', '-shm'):
            p = Path(str(self.db) + suffix)
            if p.exists() or p.is_symlink():
                self._plain(p, False)
        if not self.db.exists() and not create:
            return None
        c = sqlite3.connect(self.db, timeout=2)
        c.row_factory = sqlite3.Row
        try:
            c.execute('PRAGMA synchronous=FULL')
            c.executescript('''
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL);
                INSERT OR IGNORE INTO settings VALUES (1,0);
                CREATE TABLE IF NOT EXISTS samples (
                    id TEXT PRIMARY KEY, created REAL NOT NULL,
                    text TEXT NOT NULL, voice TEXT NOT NULL,
                    metadata TEXT NOT NULL, wav BLOB NOT NULL,
                    sha256 TEXT NOT NULL, fingerprint TEXT UNIQUE NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending','approved','rejected')),
                    reviewed REAL);
            ''')
            return c
        except BaseException:
            c.close()
            raise

    def status(self):
        out = {'enabled': False, 'counts': {'pending': 0, 'approved': 0, 'rejected': 0},
               'bytes': 0, 'maxBytes': self.MAX_BYTES, 'maxSamples': self.MAX_SAMPLES,
               'full': False, 'automaticDeletion': False, 'automaticTraining': False}
        c = self._connect()
        if c is None:
            return out
        try:
            out['enabled'] = bool(c.execute('SELECT enabled FROM settings WHERE id=1').fetchone()[0])
            for row in c.execute('SELECT state,count(*) FROM samples GROUP BY state'):
                out['counts'][row[0]] = row[1]
            out['bytes'] = c.execute('SELECT coalesce(sum(length(wav)),0) FROM samples').fetchone()[0]
            out['full'] = (out['bytes'] >= self.MAX_BYTES or
                           sum(out['counts'].values()) >= self.MAX_SAMPLES)
            return out
        finally:
            c.close()

    def configure(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('收集开关必须是布尔值')
        c = self._connect(create=True)
        try:
            with c:
                c.execute('UPDATE settings SET enabled=? WHERE id=1', (int(enabled),))
        finally:
            c.close()
        return self.status()

    def capture(self, text, voice, wav, metadata):
        """Best-effort caller; capture failure must never retry QQ delivery."""
        if not self.status()['enabled']:
            return False
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000:
            raise ValueError('素材文字无效')
        if not isinstance(voice, str) or not 1 <= len(voice) <= 100:
            raise ValueError('素材音色无效')
        if not isinstance(wav, bytes) or not 44 <= len(wav) <= self.MAX_WAV:
            raise ValueError('素材 WAV 大小无效')
        try:
            with wave.open(io.BytesIO(wav), 'rb') as audio:
                frames, rate, channels, width = (audio.getnframes(), audio.getframerate(),
                                                audio.getnchannels(), audio.getsampwidth())
                if (audio.getcomptype() != 'NONE' or channels not in (1, 2)
                        or width != 2 or not 8000 <= rate <= 96000 or frames <= 0
                        or len(audio.readframes(frames)) != frames * channels * width):
                    raise ValueError('素材不是完整 PCM16 WAV')
        except (wave.Error, EOFError) as exc:
            raise ValueError('素材 WAV 无效') from exc
        # Allowlist scalars: never persist arbitrary service dictionaries/paths.
        fields = {'model', 'service_version', 'job', 'instruction', 'seed',
                  'model_stream', 'split_mode', 'pause_ms', 'plugin_sha256'}
        clean = {k: v for k, v in metadata.items() if k in fields
                 and type(v) in (str, int, float, bool, type(None))
                 and len(str(v)) <= 500}
        clean.update({'source': 'synthetic_qq_tts', 'delivery': 'api_acknowledged',
                      'sample_rate': rate, 'channels': channels,
                      'duration': frames / rate, 'schema': 1})
        encoded = json.dumps(clean, ensure_ascii=False, allow_nan=False, sort_keys=True)
        digest = hashlib.sha256(wav).hexdigest()
        fingerprint = hashlib.sha256((digest + '\0' + text + '\0' + voice + '\0' + encoded).encode()).hexdigest()
        c = self._connect(create=True)
        try:
            with c:
                c.execute('BEGIN IMMEDIATE')
                if not c.execute('SELECT enabled FROM settings WHERE id=1').fetchone()[0]:
                    return False
                count, size = c.execute('SELECT count(*),coalesce(sum(length(wav)),0) FROM samples').fetchone()
                if count >= self.MAX_SAMPLES or size + len(wav) > self.MAX_BYTES:
                    return False
                row = c.execute('INSERT OR IGNORE INTO samples VALUES (?,?,?,?,?,?,?,?,?,NULL)',
                                (uuid.uuid4().hex, time.time(), text, voice, encoded,
                                 wav, digest, fingerprint, 'pending'))
                return bool(row.rowcount)
        finally:
            c.close()

    def listing(self, state='pending', offset=0):
        if state not in ('pending', 'approved', 'rejected'):
            raise ValueError('评审状态无效')
        if type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError('分页无效')
        c = self._connect()
        if c is None:
            return []
        try:
            return [dict(row) for row in c.execute(
                'SELECT id,created,text,voice,state,reviewed,metadata FROM samples '
                'WHERE state=? ORDER BY created DESC,id DESC LIMIT 20 OFFSET ?', (state, offset))]
        finally:
            c.close()

    @staticmethod
    def _id(sample):
        import re
        if not isinstance(sample, str) or not re.fullmatch('[0-9a-f]{32}', sample):
            raise ValueError('素材编号无效')

    def review(self, sample, state):
        self._id(sample)
        if state not in ('pending', 'approved', 'rejected'):
            raise ValueError('评审状态无效')
        c = self._connect()
        if c is None:
            raise ValueError('素材不存在')
        try:
            with c:
                row = c.execute('UPDATE samples SET state=?,reviewed=? WHERE id=?',
                                (state, time.time(), sample))
                if row.rowcount != 1:
                    raise ValueError('素材不存在')
        finally:
            c.close()
        return self.status()

    def audio(self, sample):
        self._id(sample)
        c = self._connect()
        if c is None:
            raise ValueError('素材不存在')
        try:
            row = c.execute('SELECT wav,sha256 FROM samples WHERE id=?', (sample,)).fetchone()
            if row is None:
                raise ValueError('素材不存在')
            raw = row[0]
            if len(raw) > self.MAX_WAV or hashlib.sha256(raw).hexdigest() != row[1]:
                raise ValueError('素材校验失败')
            return {'audio': base64.b64encode(raw).decode('ascii')}
        finally:
            c.close()
