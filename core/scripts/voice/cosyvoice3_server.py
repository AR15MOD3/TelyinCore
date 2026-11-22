"""CosyVoice3 host: runtime stays external, selected weights live in TelyinCore."""
from contextlib import asynccontextmanager
from pathlib import Path
import hashlib
import io
import json
import os
import re
import sys
import threading
import queue
import time
import uuid
from cosyvoice3_graph_backend import Backend, settings as backend_settings
from typing import Literal

ROOT = Path(os.environ["TELYIN_TTS_RUNTIME"]).resolve()
MODEL_DIR = Path(os.environ["TELYIN_TTS_MODEL_DIR"]).resolve()
if not (ROOT / "repo").is_dir() or not (MODEL_DIR / "cosyvoice3.yaml").is_file():
    raise RuntimeError("CosyVoice3 运行环境或所选模型不完整")
sys.path[:0] = [str(ROOT / "repo"), str(ROOT / "repo/third_party/Matcha-TTS")]

import numpy as np
import soundfile as sf
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
import wetext.wetext
# Use the already downloaded FSTs; startup must not depend on ModelScope.
wetext.wetext.snapshot_download = lambda *args, **kwargs: str(ROOT / "text-normalization")
from cosyvoice.cli.cosyvoice import AutoModel
from cosyvoice.utils.common import set_all_random_seed

torch.set_num_threads(3)
OUT = ROOT / "outputs"
UPLOADS = ROOT / "references/uploads"
PUBLIC_REF = ROOT / "repo/asset/zero_shot_prompt.wav"
PRIVATE_REF = ROOT / "references/Telyin.wav"
VOICE_REFS = {"default": PUBLIC_REF, "telyin": PRIVATE_REF}
VOICE_INSTRUCTIONS = {"default": "用自然、平静的语气说。",
                      "telyin": "用自然、温柔的语气说。"}
for directory in (OUT, UPLOADS):
    directory.mkdir(parents=True, exist_ok=True)
"""One service process owns one GPU scheduler; clients may be many processes.

Tokens are opaque random capabilities, not QQ/user identities. Pure stdlib.
"""
import time
import threading
from collections import deque

class QueueError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

class Ticket:
    def __init__(self, owner, batch, sequence, now, wait):
        self.owner, self.batch, self.sequence = owner, batch, sequence
        self.entered, self.deadline = now, now + wait
        self.state, self.reason = 'queued', None
        self.cancelled = False
        self.started = None

class FairScheduler:
    def __init__(self, capacity=16, per_owner=3, clock=time.monotonic):
        self.capacity, self.per_owner, self.clock = capacity, per_owner, clock
        self.condition = threading.Condition()
        self.queues, self.ring = {}, deque()
        self.active = None
        self.last_owner = None
        self.sequence = 0
        self.closed = False
        self.instance_id = uuid.uuid4().hex
        self.producers = {}
        self.revoked = {}  # bounded TTL capabilities, never text or account IDs

    def register_producer(self, instance, owner, batch):
        with self.condition:
            if (instance != self.instance_id or self.closed or
                    any(not re.fullmatch(r"[0-9a-f]{32}", value) for value in (owner, batch))):
                raise QueueError("cancelled", "TTS 服务或批次已失效")
            if len(self.producers) >= 1024:
                raise QueueError("global_full", "TTS 租约已满")
            lease = uuid.uuid4().hex
            self.producers[lease] = (owner, batch, "new")
            return self._receipt(lease, "new")

    def _receipt(self, lease, state):
        return {"drain_protocol": 1, "instance_id": self.instance_id, "lease": lease,
                "admission_closed": state != "new", "drained": state == "done"}

    def producer(self, action, instance, owner, batch, lease):
        with self.condition:
            record = self.producers.get(lease)
            if instance != self.instance_id or record is None or record[:2] != (owner, batch):
                raise QueueError("cancelled", "TTS 租约无效")
            state = record[2]
            if action == "claim":
                if state != "new":
                    raise QueueError("duplicate", "TTS 租约已使用")
                state = "running"
            elif action == "finish":
                state = "done"
            elif action == "cancel":
                if state == "new":
                    state = "done"
                elif state == "running":
                    for ticket in self.queues.get(owner, ()):
                        if ticket.batch == batch:
                            ticket.state, ticket.reason, ticket.cancelled = "cancelled", "cancelled", True
                    if self.active and (self.active.owner, self.active.batch) == (owner, batch):
                        self.active.cancelled = True
                    self.condition.notify_all()
                    state = "cancelled"
            elif action == "ack":
                if state != "done":
                    raise QueueError("cancelled", "TTS 工作尚未排空")
                del self.producers[lease]
                return self._receipt(lease, state)
            self.producers[lease] = (owner, batch, state)
            return self._receipt(lease, state)

    def _purge(self):
        now = self.clock()
        self.revoked = {key: end for key, end in self.revoked.items() if end > now}
        for owner in list(self.queues):
            q = self.queues[owner]
            kept = deque()
            for ticket in q:
                if ticket.deadline <= now:
                    ticket.state, ticket.reason = 'expired', 'queue_expired'
                else:
                    kept.append(ticket)
            if kept:
                self.queues[owner] = kept
            else:
                del self.queues[owner]
        self.ring = deque(owner for owner in self.ring if owner in self.queues)

    def enqueue(self, owner, batch, wait=15, lease=""):
        if not (0 < wait <= 60):
            raise QueueError('invalid_wait', '排队期限须为 0 到 60 秒之间')
        with self.condition:
            self._purge()
            if self.closed:
                raise QueueError('shutdown', 'TTS 服务正在停止')
            if lease and self.producers.get(lease) != (owner, batch, "running"):
                raise QueueError('cancelled', 'TTS 租约已取消')
            if (owner, None) in self.revoked or (owner, batch) in self.revoked:
                raise QueueError('cancelled', 'TTS 任务已取消')
            count = sum(len(q) for q in self.queues.values()) + bool(self.active)
            own = len(self.queues.get(owner, ())) + bool(self.active and self.active.owner == owner)
            if count >= self.capacity:
                raise QueueError('global_full', 'TTS 服务队列已满')
            if own >= self.per_owner:
                raise QueueError('owner_full', '当前 Bot 的 TTS 队列已满')
            if any(t.batch == batch for t in self.queues.get(owner, ())) or (self.active and self.active.owner == owner and self.active.batch == batch):
                raise QueueError('duplicate', '重复的 TTS 批次')
            self.sequence += 1
            ticket = Ticket(owner, batch, self.sequence, self.clock(), wait)
            if owner not in self.queues:
                self.queues[owner] = deque()
                self.ring.append(owner)
            self.queues[owner].append(ticket)
            self.condition.notify_all()
            return ticket

    def _dispatch(self):
        self._purge()
        if self.active or not self.ring or self.closed:
            return
        if len(self.ring) > 1 and self.ring[0] == self.last_owner:
            self.ring.rotate(-1)
        owner = self.ring.popleft()
        q = self.queues[owner]
        ticket = q.popleft()
        if q:
            self.ring.append(owner)
        else:
            del self.queues[owner]
        ticket.state, ticket.started = 'active', self.clock()
        self.active, self.last_owner = ticket, owner

    def acquire(self, ticket):
        with self.condition:
            while True:
                self._dispatch()
                if ticket.state == 'active':
                    return ticket
                if ticket.state != 'queued':
                    raise QueueError(ticket.reason or 'cancelled', 'TTS 任务已取消或排队超时')
                self.condition.wait(timeout=max(.001, min(.1, ticket.deadline - self.clock())))

    def check(self, ticket):
        with self.condition:
            if ticket.cancelled or self.closed:
                raise QueueError('cancelled', 'TTS 结果已取消，不再交付')

    def release(self, ticket):
        with self.condition:
            if self.active is ticket:
                self.active = None
                ticket.state = 'cancelled' if ticket.cancelled else 'done'
            elif ticket.state == 'queued':
                q = self.queues.get(ticket.owner, deque())
                self.queues[ticket.owner] = deque(t for t in q if t is not ticket)
                ticket.state, ticket.reason = 'cancelled', 'cancelled'
            self._purge()
            self.condition.notify_all()

    def cancel(self, owner, batch=None):
        with self.condition:
            self._purge()
            key = (owner, batch)
            # Never evict live revocations: doing so could revive stopped work.
            if key not in self.revoked and len(self.revoked) >= 1024:
                raise QueueError('cancel_capacity', '取消记录已满，请稍后重试')
            self.revoked[key] = self.clock() + 300
            waiting = 0
            for ticket in self.queues.get(owner, ()):
                if batch is None or ticket.batch == batch:
                    ticket.state, ticket.reason = 'cancelled', 'cancelled'
                    ticket.cancelled = True
                    waiting += 1
            if owner in self.queues:
                self.queues[owner] = deque(t for t in self.queues[owner] if t.state == 'queued')
            active = bool(self.active and self.active.owner == owner and (batch is None or self.active.batch == batch))
            if active:
                self.active.cancelled = True
            self._purge()
            self.condition.notify_all()
            return {'waiting_cancelled': waiting, 'active_result_cancelled': active}

    def close(self):
        with self.condition:
            self.closed = True
            for q in self.queues.values():
                for ticket in q:
                    ticket.state, ticket.reason, ticket.cancelled = 'cancelled', 'shutdown', True
            self.queues.clear()
            self.ring.clear()
            if self.active:
                self.active.cancelled = True
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            self._purge()
            return {'protocol': 1, 'capacity': self.capacity, 'per_owner': self.per_owner,
                    'waiting': sum(len(q) for q in self.queues.values()),
                    'active': int(self.active is not None), 'owners_waiting': len(self.queues),
                    'closed': self.closed}


scheduler = FairScheduler()
STREAM_BUFFER_WAIT_SECONDS = 5.0

"""Bounded provenance + read leases. No cleanup executor or output deletion."""
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time


class RegistryReadLimit(RuntimeError):
    pass


class ReadLease:
    def __init__(self, registry, job):
        self.registry, self.job, self.closed = registry, job, False

    def release(self):
        with self.registry.lock:
            if not self.closed:
                count = self.registry.readers[self.job] - 1
                if count:
                    self.registry.readers[self.job] = count
                else:
                    del self.registry.readers[self.job]
                self.closed = True


class ResultRegistry:
    fields = {'schema', 'job', 'origin', 'retention', 'state', 'pinned', 'completed_at', 'registered_at', 'files'}

    def __init__(self, outputs, max_records=10000, max_reads=1024, clock=time.time):
        self.outputs, self.index = Path(outputs), Path(outputs) / '.retention-index'
        self.max_records, self.max_reads, self.clock = max_records, max_reads, clock
        self.lock = threading.RLock()
        self.records, self.readers = {}, {}
        self.available, self.initialized = True, False
        self.managed_since = None
        self.invalid_records = self.errors = 0
        self.root_identity = None
        self.index_identity = self.header_identity = None
        try:
            s = self.outputs.lstat()
            if not self._directory(s):
                raise ValueError('unsafe output root')
            self.root_identity = (s.st_dev, s.st_ino)
            if os.path.lexists(self.index):
                self._load()
        except Exception:
            self.available = False
            self.errors += 1

    @staticmethod
    def _number(value):
        try:
            return type(value) in (int, float) and math.isfinite(value)
        except (OverflowError, TypeError):
            return False

    @staticmethod
    def _directory(s):
        return stat.S_ISDIR(s.st_mode) and not getattr(s, 'st_file_attributes', 0) & 0x400

    def _root_ok(self):
        s = self.outputs.lstat()
        if not self._directory(s) or (s.st_dev, s.st_ino) != self.root_identity:
            raise ValueError('output root changed')

    def _index_ok(self):
        self._root_ok()
        info = self.index.lstat()
        if not self._directory(info):
            raise ValueError('unsafe index directory')
        if self.index_identity is not None and (info.st_dev, info.st_ino) != self.index_identity:
            raise ValueError('index directory replaced')
        if self.header_identity is not None and self._identity(self.index / 'header.json') != self.header_identity:
            raise ValueError('registry header changed')

    @staticmethod
    def _identity(path, single_link=True):
        s = path.lstat()
        if (not stat.S_ISREG(s.st_mode) or getattr(s, 'st_file_attributes', 0) & 0x400
                or (single_link and s.st_nlink != 1)):
            raise ValueError('unsafe file identity')
        return {'size': s.st_size, 'mtime_ns': s.st_mtime_ns, 'inode': s.st_ino, 'device': s.st_dev}

    def _read_json(self, path):
        info = self._identity(path)
        if info['size'] > 65536:
            raise ValueError('oversized registry record')
        with path.open('rb') as f:
            raw = f.read(65537)
        if len(raw) > 65536:
            raise ValueError('registry grew')
        if self._identity(path) != info:
            raise ValueError('registry changed while reading')
        return json.loads(raw)

    def _write_new(self, path, data):
        # Only the temporary file created by this operation may be removed.
        # Final records are never replaced, and output WAV/JSON never deleted.
        self._index_ok()
        temporary = path.with_name('.' + path.name + '.partial')
        owned = False
        try:
            with temporary.open('x', encoding='utf-8') as f:
                owned = True
                json.dump(data, f, ensure_ascii=True, separators=(',', ':'))
                f.flush()
                os.fsync(f.fileno())
            temporary.rename(path)  # Native Windows rename refuses collisions.
            owned = False
        except BaseException:
            if owned:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    # Do not keep generating staging remnants after a cleanup fault.
                    self.available = False
            raise

    def _load(self):
        self._index_ok()
        info = self.index.lstat()
        self.index_identity = (info.st_dev, info.st_ino)
        header = self._read_json(self.index / 'header.json')
        if (not isinstance(header, dict) or set(header) != {'schema', 'managed_since'}
                or type(header['schema']) is not int or header['schema'] != 1
                or not self._number(header['managed_since']) or not 0 <= header['managed_since'] <= self.clock()):
            raise ValueError('invalid registry header or clock rollback')
        self.managed_since, self.initialized = header['managed_since'], True
        self.header_identity = self._identity(self.index / 'header.json')
        seen = 0
        with os.scandir(self.index) as entries:
            for i, entry in enumerate(entries):
                if i >= self.max_records * 2 + 100:
                    self.available = False
                    break
                if entry.name == 'header.json':
                    continue
                if not re.fullmatch(r'[0-9a-f]{32}\.json', entry.name):
                    # Unknown/partial index files remain untouched; stop new claims.
                    self.invalid_records += 1
                    self.available = False
                    continue
                seen += 1
                if seen > self.max_records:
                    self.available = False
                    break
                try:
                    row = self._read_json(self.index / entry.name)
                    if not self._valid(row, entry.name[:-5]):
                        raise ValueError('invalid provenance')
                    self.records[row['job']] = row
                except Exception:
                    self.invalid_records += 1
                    self.available = False

    def _valid(self, row, job):
        if (not isinstance(row, dict) or set(row) != self.fields or row.get('job') != job
                or not re.fullmatch(r'[0-9a-f]{32}', job)
                or type(row.get('schema')) is not int or row['schema'] != 1
                or row.get('origin') != 'qq' or row.get('retention') != 'temporary'
                or row.get('state') != 'complete' or row.get('pinned') is not False):
            return False
        completed, registered = row['completed_at'], row['registered_at']
        if (not self._number(completed) or not self._number(registered)
                or not self.managed_since <= completed <= registered <= self.clock()):
            return False
        names = {job + '.wav', job + '.json'}
        if not isinstance(row['files'], dict) or set(row['files']) != names:
            return False
        for name in names:
            proof = row['files'][name]
            if (not isinstance(proof, dict) or set(proof) != {'size', 'mtime_ns', 'inode', 'device'}
                    or any(type(value) is not int or value < 0 for value in proof.values())
                    or proof != self._identity(self.outputs / name)
                    or proof['size'] < (44 if name.endswith('.wav') else 1)
                    or proof['mtime_ns'] < int(self.managed_since * 1e9)):
                return False
        return True

    def begin(self):
        with self.lock:
            try:
                if not self.available or len(self.records) >= self.max_records:
                    return False
                self._root_ok()
                if not self.initialized:
                    if os.path.lexists(self.index):
                        # Never overwrite/reconstruct an unexplained existing index.
                        raise ValueError('index appeared concurrently')
                    self.index.mkdir()
                    info = self.index.lstat()
                    self.index_identity = (info.st_dev, info.st_ino)
                    self.managed_since = self.clock()
                    self._write_new(self.index / 'header.json', {'schema': 1, 'managed_since': self.managed_since})
                    self.header_identity = self._identity(self.index / 'header.json')
                    self.initialized = True
                self._index_ok()
                if self.clock() < self.managed_since:
                    raise ValueError('clock rollback')
                return True
            except Exception:
                self.available = False
                self.errors = min(self.errors + 1, 2**63 - 1)
                return False

    def register(self, job, completed_at):
        with self.lock:
            try:
                if (not self.available or not self.initialized or len(self.records) >= self.max_records
                        or not isinstance(job, str) or not re.fullmatch(r'[0-9a-f]{32}', job)
                        or job in self.records):
                    return False
                self._index_ok()
                row = {'schema': 1, 'job': job, 'origin': 'qq', 'retention': 'temporary',
                       'state': 'complete', 'pinned': False, 'completed_at': completed_at,
                       'registered_at': self.clock(),
                       'files': {job + suffix: self._identity(self.outputs / (job + suffix)) for suffix in ('.wav', '.json')}}
                if not self._valid(row, job) or self._read_json(self.outputs / (job + '.json')).get('job') != job:
                    raise ValueError('publication identity mismatch')
                self._write_new(self.index / (job + '.json'), row)
                self.records[job] = row
                return True
            except Exception:
                self.errors = min(self.errors + 1, 2**63 - 1)
                return False

    def acquire(self, job):
        if not isinstance(job, str) or not re.fullmatch(r'[0-9a-f]{32}', job):
            raise ValueError('invalid job')
        with self.lock:
            if sum(self.readers.values()) >= self.max_reads:
                raise RegistryReadLimit('read limit')
            self.readers[job] = self.readers.get(job, 0) + 1
            return ReadLease(self, job)

    def readable(self, job, suffix):
        with self.lock:
            try:
                if suffix not in ('.wav', '.json') or not re.fullmatch(r'[0-9a-f]{32}', job):
                    return False
                self._root_ok()
                self._identity(self.outputs / (job + suffix), single_link=False)
                return True
            except (OSError, ValueError):
                return False

    def snapshot(self):
        with self.lock:
            return {'retention_protocol': 1, 'cleanup_enabled': False, 'deletion_supported': False,
                    'registration_available': self.available and len(self.records) < self.max_records,
                    'registry_initialized': self.initialized, 'registered_jobs': len(self.records),
                    'registry_capacity': self.max_records, 'invalid_records': self.invalid_records,
                    'registration_errors': self.errors, 'reading_jobs': len(self.readers),
                    'active_reads': sum(self.readers.values()), 'read_capacity': self.max_reads}


retention = ResultRegistry(OUT)
backend = Backend(*backend_settings(ROOT / "lm-backend.json", os.environ))
engine = None
lock = threading.Lock()
prompt_cache = {}
last_metrics = None

def acoustic_prompt(path):
    key = hashlib.sha256(path.read_bytes()).hexdigest()
    if key not in prompt_cache:
        data = engine.frontend.frontend_zero_shot("", "", str(path), engine.sample_rate, "")
        for name in ("text", "text_len", "prompt_text", "prompt_text_len"):
            data.pop(name, None)
        prompt_cache[key] = data
    return prompt_cache[key]

def text_pieces(text, split_mode):
    if split_mode == "sentence":
        normalized = engine.frontend.text_normalize(text, split=False, text_frontend=True)
        raw = [part.strip() for part in re.findall(r'.+?(?:[。！？!?；;]+[”’」』]*|$)', normalized, re.S) if part.strip()]
        # Extremely short isolated utterances can end while the final phoneme
        # is still voiced. Preserve context by grouping them with a neighbour;
        # never truncate waveforms to manufacture a sentence boundary.
        groups, pending = [], ""
        for part in raw:
            pending += part
            if len(re.sub(r'[\W_]', '', pending)) >= 12:
                groups.append(pending)
                pending = ""
        if pending:
            if groups:
                groups[-1] += pending
            else:
                groups.append(pending)
        return groups
    return engine.frontend.text_normalize(text, split=True, text_frontend=True)

def chunks(text, reference, ref_text, instruction, seed, stream=True, split_mode="paragraph", pause_ms=180):
    backend.begin_request()
    set_all_random_seed(seed)
    base = acoustic_prompt(reference)
    prompt = "You are a helpful assistant." + (" " + instruction if instruction else "") + "<|endofprompt|>"
    if not instruction:
        prompt += ref_text
    pieces = text_pieces(text, split_mode) if isinstance(text, str) else (text,)
    for index, piece in enumerate(pieces):
        if index and split_mode == "sentence" and pause_ms:
            yield np.zeros(round(engine.sample_rate * pause_ms / 1000), dtype=np.float32)
        # Upstream grows this field during streaming. Reset for each utterance
        # so subsequent requests retain their first-chunk latency.
        engine.model.token_hop_len = 25
        model_input = dict(base)
        model_input["text"], model_input["text_len"] = engine.frontend._extract_text_token(piece)
        model_input["prompt_text"], model_input["prompt_text_len"] = engine.frontend._extract_text_token(prompt)
        if instruction:
            # Official frontend_instruct2 removes only the LM audio prompt.
            # Keep the cached acoustic reference but update the instruction for
            # each request, so changing emotion cannot reuse stale prompt text.
            model_input.pop("llm_prompt_speech_token", None)
            model_input.pop("llm_prompt_speech_token_len", None)
        try:
            for result in engine.model.tts(**model_input, stream=stream, speed=1.0):
                # Drain the upstream generator to its join/cleanup after an LM
                # fault; never abandon an active model at its first yield.
                if backend.request_error is None:
                    yield result["tts_speech"].detach().float().cpu().numpy().reshape(-1)
            backend.check()
        except Exception as exc:
            if backend.requested == "cuda_graph":
                backend.fail(exc)
                backend.drain_failed()
            raise

@asynccontextmanager
async def lifespan(app):
    global engine
    use_cuda = torch.cuda.is_available()
    engine = AutoModel(model_dir=str(MODEL_DIR), fp16=use_cuda)
    # Convert only the GPU LM weights. Flow/vocoder and CPU retain their precision.
    if use_cuda:
        engine.model.llm.half()
    with lock:
        if not PUBLIC_REF.is_file():
            raise RuntimeError("缺少 CosyVoice 官方通用参考音频")
        list(chunks("你好，欢迎使用语音服务。", PUBLIC_REF, "",
                    VOICE_INSTRUCTIONS["default"], 42))
        backend.install(engine.model, torch)
        if backend.active == "cuda_graph" or (backend.reason or "").startswith("startup_failed:"):
            try:
                list(chunks("你好，欢迎使用语音服务。", PUBLIC_REF, "",
                            VOICE_INSTRUCTIONS["default"], 42))
            except Exception:
                if backend.unhealthy or backend.active != "eager":
                    raise
                # Startup-only downgrade verification. No user request retry.
                list(chunks("你好，欢迎使用语音服务。", PUBLIC_REF, "",
                            VOICE_INSTRUCTIONS["default"], 42))
    print("CosyVoice3 ready; reference cached; ready for local requests", flush=True)
    try:
        yield
    finally:
        scheduler.close()

app = FastAPI(title="CosyVoice 3 · 本地试听", lifespan=lifespan)
LAB = ROOT / "comparison/emotion-lab"
LAB.mkdir(parents=True, exist_ok=True)
app.mount("/lab", StaticFiles(directory=str(LAB), html=True), name="emotion-lab")

@app.get("/")
def index():
    return HTMLResponse((ROOT / "index.html").read_text(encoding="utf-8"))

@app.get("/health")
def health():
    return {"status": "error" if backend.unhealthy else ("ok" if engine else "loading"), "model": "Fun-CosyVoice3-0.5B-2512", "device": "cuda" if torch.cuda.is_available() else "cpu",
            "sample_rate": engine.sample_rate if engine else None, "cached_references": len(prompt_cache),
            "torch_allocated_MiB": round(torch.cuda.memory_allocated() / 2**20, 1) if torch.cuda.is_available() else 0,
            "torch_reserved_MiB": round(torch.cuda.memory_reserved() / 2**20, 1) if torch.cuda.is_available() else 0,
            "last_metrics": last_metrics, "lm_backend": backend.status()}

@app.get("/api/voices")
def voices():
    return {"voices": [{"id": name, "label": "默认音色" if name == "default" else path.name}
                       for name, path in VOICE_REFS.items() if path.is_file()],
            "speed_supported": False}

def prepare(text, reference, ref_text, instruction, voice):
    if not text.strip() or len(text) > 1000:
        raise HTTPException(422, "请输入 1–1000 字的试听文字。")
    if voice not in VOICE_REFS:
        raise HTTPException(422, "不认识的音色预设。")
    path = VOICE_REFS[voice]
    if reference and reference.filename:
        if not instruction.strip() and not ref_text.strip():
            raise HTTPException(422, "无语气指令的克隆模式需要填写参考音频原文。")
        data = reference.file.read(30 * 1024 * 1024 + 1)
        if len(data) > 30 * 1024 * 1024:
            raise HTTPException(413, "参考音频请小于 30 MB。")
        suffix = Path(reference.filename).suffix.lower()
        if suffix not in (".wav", ".flac", ".mp3", ".ogg"):
            raise HTTPException(422, "请上传 WAV、FLAC、MP3 或 OGG 音频。")
        path = UPLOADS / (uuid.uuid4().hex + suffix)
        path.write_bytes(data)
        return text, path, instruction.strip(), "upload"
    if not path.is_file():
        raise HTTPException(422, "所选音色参考音频尚未安装。")
    # The private preset owns its instruction and pronunciation; UI text stays unchanged.
    spoken = text.replace("特琳", "[t][è][l][ín]") if voice == "telyin" else text
    return spoken, path, VOICE_INSTRUCTIONS[voice], voice

def _publish_result(job, whole, result_metrics):
    # Each job owns two exclusively reserved temporary files. Never truncate
    # a pre-existing output. Native Windows rename also refuses destination
    # collisions; publication is per-file, not a two-file transaction.
    wave_path, metrics_path = OUT / (job + ".wav"), OUT / (job + ".json")
    temporary_wave = OUT / ("." + job + ".partial.wav")
    temporary_metrics = OUT / ("." + job + ".partial.json")
    owned = []
    try:
        if wave_path.exists() or metrics_path.exists():
            raise FileExistsError("TTS output identifier already exists")
        for path in (temporary_wave, temporary_metrics):
            with path.open("xb"):
                pass
            owned.append(path)
        sf.write(temporary_wave, whole, engine.sample_rate, subtype="PCM_16")
        temporary_metrics.write_text(json.dumps(result_metrics, indent=2), encoding="utf-8")
        temporary_wave.rename(wave_path)
        owned.remove(temporary_wave)
        owned.append(wave_path)
        temporary_metrics.rename(metrics_path)
        owned.remove(temporary_metrics)
        owned.append(metrics_path)
    except BaseException:
        failed = 0
        for path in reversed(owned):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                failed += 1
        if failed:
            try:
                print("TTS output cleanup incomplete; files=" + str(failed), flush=True)
            except Exception:
                pass
        raise


def synthesize(job, text, path, ref_text, instruction, seed, split_mode="paragraph",
               pause_ms=180, model_stream=True, voice="default", ticket=None, emit_pcm=True):
    global last_metrics
    waves = []
    queued = ticket.entered if ticket else time.monotonic()
    with lock:
        if ticket:
            scheduler.check(ticket)
        queue_wait_ms = (time.monotonic() - queued) * 1000
        start = time.perf_counter()
        first = None
        for wave in chunks(text, path, ref_text, instruction, seed, stream=model_stream, split_mode=split_mode, pause_ms=pause_ms):
            if first is None:
                first = time.perf_counter() - start
            # Cancellation cannot preempt the model. Drain it without keeping
            # discarded audio or converting it to PCM; retain the GPU slot.
            if ticket and ticket.cancelled:
                waves.clear()
                continue
            waves.append(wave)
            # Complete-WAV clients do not consume streaming PCM. Keep the
            # soundfile encoding path unchanged to preserve the final waveform.
            if emit_pcm:
                yield (np.clip(wave, -1, 1) * 32767).astype("<i2").tobytes()
        if ticket:
            scheduler.check(ticket)
        elapsed = time.perf_counter() - start
        whole = np.concatenate(waves) if waves else np.empty(0, dtype=np.float32)
        seconds = len(whole) / engine.sample_rate
        if not len(whole):
            raise RuntimeError("模型没有返回音频。")
        result_metrics = {"job": job, "first_chunk_ms": round((first or 0) * 1000, 1), "synthesis_ms": round(elapsed * 1000, 1),
                        "queue_ms": round(queue_wait_ms, 1), "audio_seconds": round(seconds, 3),
                        "rtf": round(elapsed / seconds, 4), "chunks": len(waves), "sample_rate": engine.sample_rate,
                        "lm_backend": backend.status(), "torch_peak_reserved_MiB": round(torch.cuda.max_memory_reserved() / 2**20, 1) if torch.cuda.is_available() else 0,
                        "split_mode": split_mode, "pause_ms": pause_ms if split_mode == "sentence" else 0,
                         "model_stream": model_stream, "voice": voice,
                         "queue_protocol": 1, "queue_sequence": ticket.sequence if ticket else None}
        _publish_result(job, whole, result_metrics)
        # Health reports the latest successfully published result, not a job
        # whose WAV/metadata write failed part-way through.
        last_metrics = result_metrics
        # Audition uploads do not accumulate GPU reference features forever.
        if path.parent == UPLOADS:
            prompt_cache.pop(hashlib.sha256(path.read_bytes()).hexdigest(), None)
            path.unlink(missing_ok=True)

def _queue_error(exc):
    status = 429 if exc.code in ("global_full", "owner_full", "cancel_capacity") else 504 if exc.code == "queue_expired" else 409
    return HTTPException(status, {"code": exc.code, "message": str(exc)})

def _admit(owner, batch, wait, lease=""):
    if owner and not re.fullmatch(r"[0-9a-f]{32}", owner):
        raise HTTPException(422, "无效队列所有者")
    if batch and not re.fullmatch(r"[0-9a-f]{32}", batch):
        raise HTTPException(422, "无效队列批次")
    ticket = None
    try:
        ticket = scheduler.enqueue(owner or "legacy", batch or uuid.uuid4().hex, wait, lease)
        scheduler.acquire(ticket)
        return ticket
    except QueueError as exc:
        if ticket:
            scheduler.release(ticket)
        raise _queue_error(exc) from None
    except BaseException:
        if ticket:
            scheduler.release(ticket)
        raise

def _finish(ticket, path):
    # Cancelled uploads may have cached features; clean under the engine lock.
    try:
        if path.parent == UPLOADS:
            with lock:
                if path.is_file():
                    prompt_cache.pop(hashlib.sha256(path.read_bytes()).hexdigest(), None)
                    path.unlink(missing_ok=True)
    finally:
        scheduler.release(ticket)

from cosyvoice3_phrase import install as install_phrase_route
install_phrase_route(app, sys.modules[__name__])

@app.get("/api/queue/status")
def queue_status():
    return {**scheduler.snapshot(), "phrase_protocol": 1, "retention_protocol": 1,
            "drain_protocol": 1, "instance_id": scheduler.instance_id}

@app.post("/api/queue/lease")
def queue_lease(instance_id: str = Form(...), queue_owner: str = Form(...),
                queue_batch: str = Form(...)):
    try:
        return scheduler.register_producer(instance_id, queue_owner, queue_batch)
    except QueueError as exc:
        raise _queue_error(exc) from None


def drain_route(action):
    def handle(instance_id: str = Form(...), queue_owner: str = Form(...),
               queue_batch: str = Form(...), lease: str = Form(...)):
        try:
            return scheduler.producer(action, instance_id, queue_owner, queue_batch, lease)
        except QueueError as exc:
            raise _queue_error(exc) from None
    return handle


for drain_action in ("status", "cancel", "ack"):
    app.post("/api/queue/drain-" + drain_action)(drain_route(drain_action))


@app.get("/api/cache/status")
def cache_status():
    # Aggregate metadata only: no text, file names, job IDs, owner tokens or QQ IDs.
    return retention.snapshot()

@app.post("/api/queue/cancel")
def queue_cancel(queue_owner: str = Form(...), queue_batch: str = Form("")):
    if not re.fullmatch(r"[0-9a-f]{32}", queue_owner) or (queue_batch and not re.fullmatch(r"[0-9a-f]{32}", queue_batch)):
        raise HTTPException(422, "无效取消标识")
    try:
        return {"protocol": 1, **scheduler.cancel(queue_owner, queue_batch or None)}
    except QueueError as exc:
        raise _queue_error(exc) from None

@app.post("/api/generate")
def generate(text: str = Form(...), reference: UploadFile | None = File(None), ref_text: str = Form(""),
             instruction: str = Form("用自然、平静的语气说。"), seed: int = Form(42),
             split_mode: Literal["paragraph", "sentence"] = Form("paragraph"),
             pause_ms: int = Form(180, ge=0, le=1000), model_stream: bool = Form(True),
             voice: Literal["default", "telyin"] = Form("default"),
             queue_owner: str = Form(""), queue_batch: str = Form(""),
             queue_wait_seconds: float = Form(15, gt=0, le=60),
             result_kind: Literal["archive", "qq_temporary"] = Form("archive"),
             queue_lease: str = Form("")):
    if result_kind == "qq_temporary" and (not re.fullmatch(r"[0-9a-f]{32}", queue_owner) or not re.fullmatch(r"[0-9a-f]{32}", queue_batch)):
        raise HTTPException(422, "临时结果需要明确的请求所有者与批次")
    binding = (scheduler.instance_id, queue_owner, queue_batch, queue_lease)
    if queue_lease:
        try:
            scheduler.producer("claim", *binding)
        except QueueError as exc:
            raise _queue_error(exc) from None
    try:
        registration_requested = result_kind == "qq_temporary" and retention.begin()
        spoken, path, instruction, preset = prepare(text, reference, ref_text, instruction, voice)
        ticket = None
        try:
            ticket = _admit(queue_owner, queue_batch, queue_wait_seconds, queue_lease)
            job = uuid.uuid4().hex
            for _ in synthesize(job, spoken, path, ref_text, instruction, seed,
                                split_mode, pause_ms, model_stream, preset, ticket, emit_pcm=False):
                pass
            scheduler.check(ticket)
            result = json.loads((OUT / (job + ".json")).read_text())
            registered = retention.register(job, time.time()) if registration_requested else False
            return {"audio_url": "/audio/" + job, **result, "retention_protocol": 1,
                    "retention_registered": registered,
                    "retention_kind": "qq_temporary" if registered else "archive"}
        except QueueError as exc:
            raise _queue_error(exc) from None
        finally:
            if ticket:
                _finish(ticket, path)
            elif path.parent == UPLOADS:
                path.unlink(missing_ok=True)
    finally:
        if queue_lease:
            scheduler.producer("finish", *binding)


@app.post("/api/stream")
def stream(text: str = Form(...), reference: UploadFile | None = File(None), ref_text: str = Form(""),
           instruction: str = Form("用自然、平静的语气说。"), seed: int = Form(42),
           split_mode: Literal["paragraph", "sentence"] = Form("paragraph"),
           pause_ms: int = Form(180, ge=0, le=1000),
           voice: Literal["default", "telyin"] = Form("default"),
           queue_owner: str = Form(""), queue_batch: str = Form(""),
           queue_wait_seconds: float = Form(15, gt=0, le=60)):
    spoken, path, instruction, preset = prepare(text, reference, ref_text, instruction, voice)
    try:
        ticket = _admit(queue_owner, queue_batch, queue_wait_seconds)
    except BaseException:
        if path.parent == UPLOADS:
            path.unlink(missing_ok=True)
        raise
    job = uuid.uuid4().hex
    channel = queue.Queue(maxsize=2)
    abandoned = threading.Event()
    completed = threading.Event()
    failure = []
    def abandon():
        abandoned.set()
        # Do not release the GPU slot until producer has drained inference.
        with scheduler.condition:
            if ticket.state == "active":
                ticket.cancelled = True
    def offer(value):
        deadline = time.monotonic() + STREAM_BUFFER_WAIT_SECONDS
        while not abandoned.is_set():
            if ticket.cancelled:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Keep draining synthesize in this producer. Raising out of
                # its loop could abandon still-running model inference.
                failure.append(QueueError("slow_consumer", "流式输出缓冲等待超时"))
                with scheduler.condition:
                    ticket.cancelled = True
                return
            try:
                channel.put(value, timeout=min(.05, remaining))
                return
            except queue.Full:
                pass
    def produce():
        try:
            for pcm in synthesize(job, spoken, path, ref_text, instruction,
                                  seed, split_mode, pause_ms, voice=preset, ticket=ticket):
                offer(pcm)
        except BaseException as exc:
            if not failure:
                failure.append(exc)
        finally:
            try:
                _finish(ticket, path)
            except BaseException as exc:
                if not failure:
                    failure.append(exc)
            finally:
                # Completion is out-of-band: even a full buffer cannot keep
                # the model slot waiting for delivery of an error/done marker.
                completed.set()
    def body():
        try:
            while True:
                try:
                    value = channel.get(timeout=.1)
                except queue.Empty:
                    if not completed.is_set():
                        continue
                    if failure:
                        raise failure[0]
                    return
                if not ticket.cancelled:
                    yield value
        finally:
            abandon()
    # Construct response before launching GPU work; startup failure must also
    # release the reservation and uploaded reference.
    try:
        response = StreamingResponse(body(), media_type="application/octet-stream",
                                     background=BackgroundTask(abandon),
                                     headers={"X-Job-Id": job, "X-Sample-Rate": str(engine.sample_rate), "X-Format": "pcm_s16le", "X-Channels": "1", "X-Queue-Protocol": "1"})
        threading.Thread(target=produce, daemon=True, name="tts-stream-producer").start()
        return response
    except BaseException:
        _finish(ticket, path)
        raise


def valid_job(job):
    if len(job) != 32 or any(c not in "0123456789abcdef" for c in job):
        raise HTTPException(404)

class LeasedFileResponse(FileResponse):
    def __init__(self, job, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.job = job

    async def __call__(self, scope, receive, send):
        # Acquire only when ASGI starts consuming the response. A constructed
        # but never-called response cannot leak a read lease.
        try:
            lease = retention.acquire(self.job)
        except RegistryReadLimit:
            raise HTTPException(503, "TTS 文件读取并发已满") from None
        try:
            if not retention.readable(self.job, ".wav"):
                raise HTTPException(404)
            # Keep file IO inside this response's lifetime, even if a future
            # server advertises pathsend/offload support.
            safe_scope = {**scope, "extensions": {k: v for k, v in scope.get("extensions", {}).items() if k != "http.response.pathsend"}}
            await super().__call__(safe_scope, receive, send)
        finally:
            lease.release()


@app.get("/audio/{job}")
def audio(job: str):
    valid_job(job)
    path = OUT / (job + ".wav")
    return LeasedFileResponse(job, path, media_type="audio/wav", filename="CosyVoice3-" + job[:8] + ".wav")

@app.get("/metrics/{job}")
def metrics(job: str):
    valid_job(job)
    try:
        lease = retention.acquire(job)
    except RegistryReadLimit:
        raise HTTPException(503, "TTS 文件读取并发已满") from None
    try:
        path = OUT / (job + ".json")
        if not retention.readable(job, ".json"):
            raise HTTPException(404, "正在合成")
        # JSON is fully buffered before releasing; response serialization no
        # longer needs this file, unlike the streamed WAV response.
        return json.loads(path.read_text())
    finally:
        lease.release()

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--check-voice" and sys.argv[2] in VOICE_REFS:
        import asyncio

        async def check_voice(voice):
            async with lifespan(app):
                if engine.frontend.text_frontend not in ("wetext", "ttsfrd"):
                    raise RuntimeError("文本规范化环境未成功加载")
                normalized = engine.frontend.text_normalize("今天是2026年，温度25度。", split=False)
                if not normalized or "2026" in normalized:
                    raise RuntimeError("文本规范化自检失败")
                ref = VOICE_REFS[voice]
                audio = chunks("你好，这是语音配置自检。", ref, "",
                               VOICE_INSTRUCTIONS[voice], 42)
                try:
                    samples = 0
                    for chunk in audio:
                        if not np.isfinite(chunk).all():
                            raise RuntimeError("TTS 自检生成了无效音频")
                        samples += chunk.size
                    if not samples:
                        raise RuntimeError("TTS 自检没有生成音频")
                finally:
                    audio.close()
                print("TTS self-check passed " + json.dumps({"samples": samples, **health()}, ensure_ascii=False), flush=True)

        asyncio.run(check_voice(sys.argv[2]))
    elif len(sys.argv) == 1:
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("TELYIN_TTS_PORT", "18084")))
    else:
        raise SystemExit("Invalid CosyVoice3 server arguments")
