"""Opt-in, serialized CosyVoice3 CUDA Graph backend. No voice/sampling changes.

Startup/capacity failures use eager. Runtime faults fail the current request
without resampling or publishing partial WAV; subsequent requests use eager.
Fatal CUDA faults are unhealthy, not silently recovered on a poisoned context.
"""
from dataclasses import dataclass
import hashlib
import inspect
import json
from pathlib import Path
import threading
import time

CALLER_SHA256 = '14a4f7fba5458682ecc8f22f8eae2bfa2591d6243df26466ebad348fa9f0cb0e'
# Same pinned source with POSIX line endings (GitHub release archives).
CALLER_LF_SHA256 = 'c3799f4b2a00baaf1784bda86c5c7f22d4f9199b45e1fb9edd247b996d101ffa'


def settings(path, environ):
    value = environ.get('TELYIN_TTS_LM_BACKEND')
    source = 'environment'
    if value is None:
        source = 'default'
        value = 'cuda_graph'
        if path.is_file():
            source = 'runtime_file'
            try:
                with path.open('rb') as f:
                    raw = f.read(4097)
                if len(raw) > 4096:
                    raise ValueError('Oversized backend settings')
                data = json.loads(raw.decode('utf-8-sig'))
                value = data['backend'] if isinstance(data, dict) else None
            except (OSError, ValueError, KeyError, RecursionError):
                value = None
    if value not in ('eager', 'cuda_graph'):
        return 'eager', source, 'invalid_settings'
    return value, source, None


def fatal_cuda(exc):
    return any(word in str(exc).lower() for word in (
        'illegal memory access', 'device-side assert', 'unspecified launch failure',
        'misaligned address', 'context is destroyed', 'driver shutting down'))


@dataclass
class State:
    owner: object
    generation: int
    used: int

    def __getitem__(self, layer):
        # Upstream bistream asks cache[0][0].size(2) for its current mask.
        cache = self.owner.cache
        return (cache.key_cache[layer][:, :, :self.used, :],
                cache.value_cache[layer][:, :, :self.used, :])


class Decoder:
    def __init__(self, torch, qwen, original, capacity=512):
        from transformers.cache_utils import StaticCache, DynamicCache
        self.torch, self.qwen, self.original = torch, qwen, original
        self.DynamicCache = DynamicCache
        self.capacity, self.generation = capacity, 0
        self.prefills = self.decodes = self.capacity_fallbacks = 0
        self.cache = StaticCache(config=qwen.config, max_batch_size=1,
                                 max_cache_len=capacity, device='cuda', dtype=torch.float16)
        self.x = torch.zeros((1, 1, qwen.config.hidden_size), device='cuda', dtype=torch.float16)
        self.pos = torch.zeros((1,), device='cuda', dtype=torch.long)
        self.graph = self.output = None

    def base(self, x, pos):
        return self.qwen.model(inputs_embeds=x, past_key_values=self.cache,
                               cache_position=pos, attention_mask=None,
                               output_hidden_states=False, return_dict=True,
                               use_cache=True).last_hidden_state

    def capture(self):
        t = self.torch
        with t.inference_mode(), t.cuda.amp.autocast(enabled=True, cache_enabled=False):
            self.cache.reset()
            self.pos.fill_(1)
            stream = t.cuda.Stream()
            stream.wait_stream(t.cuda.current_stream())
            with t.cuda.stream(stream):
                for _ in range(3):
                    self.base(self.x, self.pos)
            t.cuda.current_stream().wait_stream(stream)
            t.cuda.synchronize()
            self.graph = t.cuda.CUDAGraph()
            with t.cuda.graph(self.graph):
                self.output = self.base(self.x, self.pos)
            t.cuda.synchronize()
            self.cache.reset()

    def dynamic_copy(self, used):
        layers = tuple((k[:, :, :used, :].clone(), v[:, :, :used, :].clone())
                       for k, v in zip(self.cache.key_cache, self.cache.value_cache))
        return self.DynamicCache.from_legacy_cache(layers)

    def forward(self, x, masks, cache=None):
        t = self.torch
        supported = (x.ndim == 3 and x.shape[0] == 1 and x.dtype == t.float16
                     and x.is_cuda and not self.qwen.training
                     and self.qwen.config._attn_implementation == 'sdpa')
        if isinstance(cache, State):
            if cache.owner is not self or cache.generation != self.generation:
                raise RuntimeError('Stale or foreign graph cache state')
            if not supported or cache.used + x.shape[1] > self.capacity:
                self.capacity_fallbacks += 1
                return self.original(x, masks, self.dynamic_copy(cache.used))
            if x.shape[1] > 1:
                # A new text phrase extends the cache by several tokens at once.
                end = cache.used + x.shape[1]
                hidden = self.base(x, t.arange(cache.used, end, device=x.device))
                cache.used = end
                return hidden, cache
            self.pos.fill_(cache.used)
            self.x.copy_(x)
            self.graph.replay()
            cache.used += 1
            self.decodes += 1
            return self.output, cache
        if cache is not None or not supported or not 0 < x.shape[1] <= self.capacity:
            self.capacity_fallbacks += 1
            return self.original(x, masks, cache)
        self.generation += 1
        self.cache.reset()
        self.prefills += 1
        y = self.base(x, t.arange(x.shape[1], device=x.device))
        return y, State(self, self.generation, x.shape[1])


class Backend:
    def __init__(self, requested='eager', source='default', reason=None):
        self.requested, self.source, self.reason = requested, source, reason
        self.active = 'eager'
        self.capacity = 512
        self.startup_ms = 0.
        self.runtime_failures = 0
        self.unhealthy = False
        self.request_error = None
        self.decoder = self.encoder = self.original = self.torch = None
        self.model = None
        self.jobs = {}
        self.before_keys = {}
        self._lock = threading.RLock()

    def install(self, model, torch, factory=Decoder):
        if self.requested != 'cuda_graph':
            return
        self.torch = torch
        start = time.perf_counter()
        try:
            import importlib.metadata
            if (str(torch.__version__) not in ('2.3.0+cu121', '2.7.1+cu128')
                    or importlib.metadata.version('transformers') != '4.51.3'):
                self.reason = 'unvalidated_runtime'
                return
            if not torch.cuda.is_available() or torch.cuda.mem_get_info()[0] < 256 * 2**20:
                self.reason = 'insufficient_cuda_headroom'
                return
            enc = model.llm.llm
            qwen = enc.model
            path = inspect.getfile(type(enc))
            if (type(model).__name__ != 'CosyVoice3Model'
                    or type(model.llm).__name__ != 'CosyVoice3LM'
                    or type(enc).__name__ != 'Qwen2Encoder'
                    or hashlib.sha256(Path(path).read_bytes()).hexdigest() not in (CALLER_SHA256, CALLER_LF_SHA256)
                    or qwen.training or qwen.config._attn_implementation != 'sdpa'
                    or len(qwen.model.layers) != 24
                    or qwen.config.hidden_size != 896
                    or next(qwen.parameters()).dtype != torch.float16):
                self.reason = 'unvalidated_model_or_caller'
                return
            self.encoder, self.original = enc, enc.forward_one_step
            candidate = factory(torch, qwen, self.original, self.capacity)
            candidate.capture()
            self.decoder = candidate
            self.model = model
            enc.forward_one_step = self.forward
            original_job = model.llm_job

            def guarded_job(*args, **kwargs):
                uid = kwargs.get('uuid', args[-1] if args else None)
                with self._lock:
                    self.jobs[uid] = threading.current_thread()
                try:
                    return original_job(*args, **kwargs)
                except Exception as exc:
                    self.fail(exc)
                    model.llm_end_dict[uid] = True
                finally:
                    with self._lock:
                        self.jobs.pop(uid, None)
            model.llm_job = guarded_job
            self.active, self.reason = 'cuda_graph', None
        except Exception as exc:
            self.decoder = None
            if self.encoder is not None:
                self.encoder.forward_one_step = self.original
            self.reason = 'startup_failed:' + type(exc).__name__
            if fatal_cuda(exc):
                self.unhealthy = True
                raise
            # A failed capture may leave CUDA unusable. Do not advertise a safe
            # fallback unless this context can synchronize; startup also warms
            # the original inference path before becoming ready.
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
        finally:
            self.startup_ms = (time.perf_counter() - start) * 1000

    def forward(self, x, masks, cache=None):
        try:
            return self.decoder.forward(x, masks, cache)
        except Exception as exc:
            self.fail(exc)
            raise  # Never re-sample/retry a partly executed utterance.

    def fail(self, exc):
        with self._lock:
            first = self.request_error is None
            if first:
                self.request_error = exc
            self.unhealthy = self.unhealthy or fatal_cuda(exc)
            if first:
                self.runtime_failures += 1
                self.reason = 'runtime_failed:' + type(exc).__name__
            self.active = 'eager'
            if self.encoder is not None:
                self.encoder.forward_one_step = self.original
            # Keep graph buffers alive until this failed request has drained.

    def begin_request(self):
        with self._lock:
            if self.unhealthy:
                raise RuntimeError('TTS CUDA context unhealthy; restart required')
            self.request_error = None
            if self.active == 'eager':
                self.decoder = None
            if self.model is not None:
                self.before_keys = {name: set(getattr(self.model, name, {})) for name in
                                    ('tts_speech_token_dict', 'llm_end_dict', 'hift_cache_dict')}

    def drain_failed(self):
        # Do not release the serialized model slot while its LM thread is still
        # running, even when Flow/main-thread execution has already failed.
        with self._lock:
            jobs = list(self.jobs.values())
        for job in jobs:
            if job is not threading.current_thread():
                job.join()
        if self.model is not None:
            for name, before in self.before_keys.items():
                data = getattr(self.model, name, {})
                for uid in set(data) - before:
                    data.pop(uid, None)

    def check(self):
        with self._lock:
            if self.request_error is not None:
                raise RuntimeError('TTS inference failed; no completed WAV published') from self.request_error

    def status(self):
        with self._lock:
            return {'requested': self.requested, 'source': self.source, 'active': self.active,
                    'capacity': self.capacity, 'reason': self.reason,
                    'startup_ms': round(self.startup_ms, 1),
                    'runtime_failures': self.runtime_failures, 'unhealthy': self.unhealthy,
                    'prefills': self.decoder.prefills if self.decoder else 0,
                    'decodes': self.decoder.decodes if self.decoder else 0,
                    'capacity_fallbacks': self.decoder.capacity_fallbacks if self.decoder else 0}
