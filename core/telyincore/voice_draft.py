"""Private pre-rendering for one QQ model attempt. Only final QQ dispatch can send."""
from concurrent.futures import TimeoutError as FutureTimeout
from contextvars import ContextVar
from json import JSONDecoder, JSONDecodeError, dumps, loads
from queue import Empty, Queue
import re
from threading import Event, Lock
from time import monotonic
from .batch_snapshot import freeze_batch

from .session_view import batch_scope

current = ContextVar("qq_phrase_attempt", default=None)
_lock = Lock()
_decoder = JSONDecoder()
_boundary = re.compile(r"[，。！？；、：!?;\n]|(?<!\d)[,.](?=\s|$)")


def tool_text(arguments):
    """Read just the top-level text string, tolerating SSE splits inside escapes."""
    if len(arguments) > 12000 or not arguments.lstrip().startswith("{"):
        return ""
    rest = arguments.lstrip()[1:].lstrip()
    try:
        while rest:
            key, end = _decoder.raw_decode(rest)
            rest = rest[end:].lstrip()
            if not rest.startswith(":"):
                return ""
            rest = rest[1:].lstrip()
            if key == "text" and rest.startswith('"'):
                try:
                    value, _ = _decoder.raw_decode(rest)
                except JSONDecodeError:
                    # Keep a split escape (including a surrogate pair) until complete.
                    tail = re.sub(r'\\(?:u[0-9a-fA-F]{0,3})?$', '', rest)
                    try:
                        value = loads(tail + '"')
                    except ValueError:
                        return ""
                return re.sub(r'[\ud800-\udfff]+$', '', value)
            _, end = _decoder.raw_decode(rest)
            rest = rest[end:].lstrip()
            if not rest.startswith(","):
                return ""
            rest = rest[1:].lstrip()
    except (ValueError, RecursionError):
        pass
    return ""


class Draft:
    def __init__(self, plugin, snapshot):
        self.plugin = plugin
        self.snapshot = snapshot
        self.parts = Queue()
        self.cancelled = Event()
        self.prefix = ""
        self.final = None
        self.work = None

    def check(self):
        if self.cancelled.is_set() or self.plugin._stopping.is_set():
            raise RuntimeError("phrase input cancelled")

    def upload(self):
        deadline = monotonic() + 55
        while monotonic() < deadline:
            self.check()
            try:
                part = self.parts.get(timeout=.2)
            except Empty:
                continue
            yield (dumps(part, ensure_ascii=False) + "\n").encode()
            if part is None:
                return
        raise TimeoutError("phrase input expired")

    def feed(self, text, *, final=False):
        if len(text) > 1000 or not text.startswith(self.prefix):
            self.cancelled.set()
            return
        tail = text[len(self.prefix):]
        while tail:
            cut = next((m.end() for m in _boundary.finditer(tail)
                        if m.end() >= 6 and not (m.end() == len(tail) and tail[-1] in '.，,' and tail[-2:-1].isdigit())), 0)
            if not cut and len(tail) >= 36:
                # Prefer a word boundary when an unpunctuated English phrase grows.
                cut = max(0, tail.rfind(" ", 8, 36))
                if not cut and re.search(r'[\u3400-\u9fff]', tail[:32]):
                    cut = 32
            if not cut and final:
                cut = len(tail)
            if not cut:
                break
            part, tail = tail[:cut], tail[cut:]
            self.prefix += part
            self.parts.put(part)
        if final:
            self.final = text
            self.parts.put(None)


class Attempt:
    def __init__(self, plugin, scope, target, batch):
        self.plugin, self.scope, self.target, self.batch = plugin, scope, target, batch
        self.draft = self.acc = self.channel = None
        self.disabled = False

    def cancel(self):
        if self.draft:
            self.draft.cancelled.set()
            client = self.draft.snapshot.tts_client
            with _lock:
                if getattr(client, "_qq_phrase_audio", None) is self.draft:
                    del client._qq_phrase_audio
        self.disabled = True

    def observe(self, acc, event):
        if self.acc is not None and self.acc is not acc:
            self.cancel()  # A provider retry must never reuse the previous prefix.
        self.acc = acc
        if self.disabled:
            return
        kind = event.get("type")
        if kind == "response.output_text.delta" and self.plugin.tts_send_mode == "reply_with_voice":
            channel, text = "reply", acc.text
        elif kind == "response.function_call_arguments.delta":
            item = event.get("item_id")
            slot = acc._pending.get(item, {})
            if slot.get("name") != "qq_send_voice":
                return
            channel, text = item, tool_text(slot.get("arguments", ""))
        else:
            return
        if not text or len(text) > 1000:
            return
        if self.channel is not None and channel != self.channel:
            self.cancel()
            return
        if self.draft is None:
            if not _boundary.search(text) and len(text) < 36:
                return
            snapshot = self.plugin._freeze_tool("qq_send_voice",
                {"scope": self.scope, "target": self.target})
            if snapshot.batch_json != freeze_batch(lambda: self.batch):
                self.disabled = True
                return
            if snapshot.tts_client is None or snapshot.tts_client.base_url.host not in ("127.0.0.1", "::1", "localhost"):
                self.disabled = True
                return
            from qq_voice_io import render_voice
            self.draft = Draft(self.plugin, snapshot)
            self.draft.feed(text)
            if not self.draft.prefix:
                self.draft = None
                return
            self.channel = channel
            draft = self.draft
            draft.work = self.plugin._submit_background(render_voice, snapshot, text, draft.cancelled,
                                                       draft.check, self.plugin._log, draft)
        else:
            self.draft.feed(text)

    def finish(self, result):
        draft = self.draft
        if not draft or self.disabled:
            return
        if getattr(result, "status", None) not in (None, "completed") or getattr(result, "refusal", ""):
            self.cancel()
            return
        calls = getattr(result, "calls", ())
        if self.channel == "reply" and not calls:
            text = result.text
        else:
            slot = self.acc._pending.get(self.channel, {})
            call = next((c for c in calls if c.name == "qq_send_voice" and c.call_id == slot.get("call_id")), None)
            args = loads(call.arguments) if call else {}
            text = args.get("text") if (args.get("scope"), str(args.get("target"))) == (self.scope, self.target) else None
        if not isinstance(text, str) or not text.strip() or not text.startswith(draft.prefix) or len(text) > 1000:
            self.cancel()
            return
        draft.feed(text, final=True)
        with _lock:
            draft.snapshot.tts_client._qq_phrase_audio = draft


def retire(console):
    attempt = getattr(console, "_qq_voice_attempt", None)
    if attempt is not None:
        attempt.cancel()
        console._qq_voice_attempt = None


async def observe_reply(inner, console, args, kwargs):
    retire(console)
    batch = console._active_batch or ()
    if not batch:
        return await inner(*args, **kwargs)
    scope = batch_scope(batch)
    if not scope.startswith(("private:qq:", "group:qq:")):
        return await inner(*args, **kwargs)
    from .plugins import _active_items
    plugin = dict(_active_items()).get("core-infinity-qq")
    client = getattr(plugin, "tts_client", None)
    if client is None or not callable(getattr(plugin, "_submit_background", None)):
        return await inner(*args, **kwargs)
    with _lock:
        old = getattr(client, "_qq_phrase_audio", None)
        if old is not None:
            del client._qq_phrase_audio
    if old:
        old.cancelled.set()
    room, _, target = scope.split(":", 2)
    attempt = Attempt(plugin, room, target, tuple(batch))
    console._qq_voice_attempt = attempt
    token = current.set(attempt)
    try:
        result = await inner(*args, **kwargs)
        try:
            attempt.finish(result)
        except Exception:
            attempt.cancel()
        return result
    except BaseException:
        attempt.cancel()
        raise
    finally:
        current.reset(token)


def take_audio(snapshot, text, cancelled, check):
    if snapshot.tts_client is None:
        return None
    with _lock:
        draft = getattr(snapshot.tts_client, "_qq_phrase_audio", None)
        if draft is None:
            return None
        frozen = draft.snapshot
        if (frozen.generation, frozen.bot, frozen.scope, frozen.batch_json, frozen.voice,
                draft.final) != (snapshot.generation, snapshot.bot, snapshot.scope,
                                  snapshot.batch_json, snapshot.voice, text):
            return None
        del snapshot.tts_client._qq_phrase_audio
    try:
        while not cancelled.is_set():
            check()
            try:
                return draft.work.result(timeout=.2)
            except FutureTimeout:
                if draft.work.done():
                    break
    except Exception:
        check()
    finally:
        draft.cancelled.set()
    check()
    if cancelled.is_set():
        raise RuntimeError("QQ 语音已取消")
    return None
