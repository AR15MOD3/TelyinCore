"""Presentation-only adapter for the launcher's live console stream.

Only recognized startup chatter is condensed. Unknown output, warnings and
tracebacks are never discarded. No log files or service configuration are used.
"""
from __future__ import annotations

import codecs
from datetime import datetime
import os
import re
import sys
import threading
import time

_TAG = re.compile(r'^\s*\[([A-Za-z][A-Za-z0-9_-]{0,31})\]\s*(.*)$')
_MEMORY = re.compile(r'家：.*／经历 (\d+) 条 ＋ 笔记 (\d+) 字节 ＋ 整理账 (\d+) 条')
_WEB_PREFIXES = ('那一页在：', '· 推的通道：', '· 说一句：', '· 那几个数：',
                 '只听回环 ⇒', 'Ctrl-C 收摊。')
_WEB_ADVERTISEMENT = re.compile(
    r'(?:那一页在：\s*https?://\S+/|· 推的通道：\s*https?://\S+/events|'
    r'· 说一句：\s*POST\s+https?://\S+/say|· 那几个数：\s*https?://\S+/state)')
_BAD = re.compile(r'没接上|失败|异常|⚠|还没执行段|退化|没起来|语义检索暂不可用|\b(?:error|failed|warning)\b', re.I)
_SUCCESS = re.compile(r'接上了|pending_read 改成读两本|另两块的上界|另一棵树')


class ConsoleOutput:
    """One bounded, serialized reader; child remains unbuffered and owned normally."""
    def __init__(self, process, *, stream=None, clock=None, verbose=None, monotonic=None):
        self.process = process
        self.stream = sys.stdout if stream is None else stream
        self.clock = clock or (lambda: datetime.now().strftime('%H:%M:%S'))
        self.verbose = (os.environ.get('TELYIN_CONSOLE_VERBOSE') == '1'
                        if verbose is None else verbose)
        self.lock = threading.RLock()
        self.wiring = 0
        self._monotonic = monotonic or time.monotonic
        self._tick_count = 0
        self._tick_last_report = None
        self.ready_shown = False
        self.thread = None
        self.error = None
        self._finished = threading.Event()
        self._eof = self._reader_closed = False
        self._retained_source = self._retained_process = self._retained_thread = None

    def _write(self, text):
        self.stream.write(text)
        self.stream.flush()

    def _record(self, area, message, level='INFO'):
        from startup_terminal import render_record
        self._write(render_record(self.clock(), area, message, level, stream=self.stream))

    def line(self, raw):
        with self.lock:
            text = raw.rstrip('\r\n').strip()
            if self.verbose:
                self._write(raw)
                return
            if getattr(self, '_large_receipt_source_line', False):
                self._large_receipt_source_line = False
                if text in ('receipt = await self._adispatch_one(', 'receipt = self._dispatch_one('):
                    return
            large = re.fullmatch(r'.*:\d+: UserWarning: ([A-Za-z_][A-Za-z0-9_.:-]*) 的结果有 (\d+) 字符 —— 超过 (\d+) 只记 warn，一个字都不截断（规格 6.6：体积归插件管）', text)
            if large:
                name, size, limit = large.groups()
                self._record('call', f'{name} · 回执较长（{size} 字符，提示阈值 {limit}）；内容完整保留', 'WARN')
                self._large_receipt_source_line = True
                return
            match = _TAG.fullmatch(text)
            if match:
                area, body = match.groups()
                if area == 'call-detail' and body == 'tool ／参数 [arguments redacted]':
                    return  # The grouped call above already represents this redacted detail.
                if area == 'call-detail' and re.fullmatch(
                        r"([A-Za-z_][A-Za-z0-9_.:-]*|\?) ／参数 (?:'.*'|\".*\")", body):
                    return  # Default view already shows the semantic group.
                if area == 'call':
                    # This is the model's call request, not proof of success.
                    # Fold only the known emitter shape; diagnostics fall through.
                    call = re.fullmatch(
                        r"([A-Za-z_][A-Za-z0-9_.:-]*|\?) ／参数 (?:'.*'|\".*\")", body)
                    if call:
                        self._record(area, '调用 · ' + call.group(1))
                        return
                if area == 'usage':
                    usage = re.fullmatch(
                        r'(\S+) input=(\d+|unknown) output=(\d+|unknown)'
                        r' reasoning=(\d+|unknown) cached=(\d+|unknown)', body)
                    if usage:
                        provider, incoming, outgoing, reasoning, cached = usage.groups()
                        def number(value):
                            return '未知' if value == 'unknown' else f'{int(value):,}'
                        self._record(area, f'{provider} · 输入 {number(incoming)}'
                                     f'（含缓存 {number(cached)}） · 输出 {number(outgoing)}'
                                     f'（含推理 {number(reasoning)}） token')
                        return
                if area == 'tick' and body == '滴了一次':
                    # This is printed BEFORE due() runs: count attempts, never
                    # claim successful completion or health from a heartbeat.
                    self._tick_count += 1
                    now = self._monotonic()
                    if self._tick_last_report is None or now - self._tick_last_report >= 600:
                        self._record('tick', f'定时检查轮询：累计触发 {self._tick_count} 次'
                                     '（常规记录每 10 分钟汇总）')
                        self._tick_last_report = now
                    return
                if area == 'tick' and body.startswith(('这一趟滴答出问题了：', '滴答没起起来：')):
                    self._record(area, body, 'WARN')
                    return
                if area == 'wiring':
                    if not body:
                        return
                    if _BAD.search(body):
                        self._record(area, body, 'WARN')
                        return
                    if _SUCCESS.search(body) and not self.ready_shown:
                        self.wiring += 1
                        return
                if area == 'memory':
                    memory = _MEMORY.fullmatch(body)
                    if memory:
                        written, noted, logged = memory.groups()
                        self._record(area, f'经历 {written} 条 · 笔记 {noted} B · 整理账 {logged} 条')
                        return
                self._record(area, body, 'WARN' if _BAD.search(body) else 'INFO')
                return
            if _WEB_ADVERTISEMENT.fullmatch(text) and not _BAD.search(text):
                return  # The ready banner supplies the single human-facing URL.
            if text.startswith(_WEB_PREFIXES[4]):
                self._record('web', '仅本机可访问（未启用鉴权）')
                return
            if text == _WEB_PREFIXES[5]:
                return  # The launcher already owns the keyboard instructions.
            self._write(raw)

    def _summary(self):
        if self.wiring:
            self._record('wiring', f'已合并 {self.wiring} 项初始化信息')
            self.wiring = 0

    def ready(self, name, port):
        from startup_terminal import running
        with self.lock:
            self._summary()
            self.ready_shown = True
            running(name, port)

    @staticmethod
    def _candidate(text):
        text = text.lstrip()
        return (not text or text.startswith('[')
                or any(p.startswith(text) or text.startswith(p) for p in _WEB_PREFIXES))

    def _read(self):
        pending = ''
        raw_line = False
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')

        def consume(text):
            nonlocal pending, raw_line
            pieces = text.split('\n')
            for index, part in enumerate(pieces):
                ended = index < len(pieces) - 1
                if ended:
                    part += '\n'
                elif not part:
                    continue
                with self.lock:
                    if raw_line:
                        self._write(part)
                    else:
                        pending += part
                        if ended:
                            self.line(pending)
                            pending = ''
                        elif len(pending) > 16384 or not self._candidate(pending):
                            self._write(pending)
                            pending = ''
                            raw_line = True
                    if ended:
                        raw_line = False
        try:
            source = self._retained_source
            while True:
                read = getattr(source, 'read1', None)
                if read is None:
                    read = source.read
                chunk = read(4096)
                if not chunk:
                    self._eof = True
                    break
                consume(decoder.decode(chunk))
            consume(decoder.decode(b'', final=True))
            if pending:
                self.line(pending)
        except Exception as exc:
            self.error = type(exc).__name__
            with self.lock:
                if pending:
                    self._write(pending)
                self._record('console', f'日志读取失败：{self.error}', 'ERROR')
        finally:
            try:
                with self.lock:
                    self._summary()
                # Never close a replacement stream supplied after reader birth.
                self._retained_source.close()
                self._reader_closed = True
            except Exception as exc:
                self.error = type(exc).__name__
            finally:
                self._finished.set()

    def start(self):
        with self.lock:
            if self.thread is not None or self._retained_source is not None:
                raise ValueError('console output reader is single use')
            source = self.process.stdout
            if source is None or source.closed:
                raise ValueError('original child output unavailable')
            self._retained_source, self._retained_process = source, self.process
            self.thread = self._retained_thread = threading.Thread(
                target=self._read, name='core-console-output', daemon=True)
            self.thread.start()
        return self

    def assert_drained(self, process, source):
        # Reader completion is not process/account/global-host absence. No JSON
        # or caller-supplied EOF boolean can stand in for this original reader.
        if (process is not self.process or process is not self._retained_process
                or source is not self._retained_source or process.stdout is not source
                or self.thread is None or self.thread is not self._retained_thread
                or self.thread.is_alive() or not self._finished.is_set()
                or not self._eof or not self._reader_closed or not source.closed
                or self.error is not None):
            raise ValueError('original output EOF/reader close unconfirmed')
        return True

    def finish(self):
        if self.thread is not None:
            self.thread.join(timeout=3)
            if self.thread.is_alive():
                # A blocked destination may hold self.lock. Neither acquire it
                # nor write a warning to that same blocked sink after the budget.
                self.finish_incomplete = True
                return False
        self.finish_incomplete = False
        return True
