"""Opt-in final-wire diagnostic candidate; NOT installed in production.

Comparison IDs stay in memory, scoped to account/bot/conversation/model/origin.
No retries, credential discovery, model selection, disk I/O or lifecycle calls.
An enabled caller must stop its experiment on answer/protocol/quality failures.
"""
from collections import OrderedDict, deque
import hashlib
import hmac
import json
import re
import secrets
import time

from telyincore.cache_observation import _StreamProbe, normalize_usage
from telyincore.chatgpt_subscription import API_BASE

TYPES = {'cache_hit', 'cache_miss', 'comparison_response_not_found', 'unavailable'}
REASONS = {'model_changed', 'prompt_cache_key_changed', 'service_tier_changed',
           'tools_changed', 'text_format_changed', 'reasoning_effort_changed',
           'verbosity_changed', 'context_compacted', 'input_changed'}


def diagnostic(value):
    """Strict output allowlist; absence/unsupported never become a cache hit."""
    if value is None:
        return {'status': 'missing'}
    if (not isinstance(value, dict) or not isinstance(value.get('type'), str)
            or value['type'] not in TYPES):
        return {'status': 'invalid'}
    out = {'status': 'reported', 'type': value['type']}
    if 'reason' in value:
        if not isinstance(value['reason'], str) or value['reason'] not in REASONS:
            return {'status': 'unrecognized_reason', 'type': value['type']}
        out['reason'] = value['reason']
    if value['type'] == 'cache_miss' and 'reason' not in out:
        return {'status': 'invalid'}
    for name in ('comparison_reusable_tokens', 'cache_missed_tokens'):
        if name in value:
            count = value[name]
            if type(count) is not int or not 0 <= count <= 2**63-1:
                return {'status': 'invalid'}
            out[name] = count
    return out


class TerminalProbe(_StreamProbe):
    def __init__(self, clock):
        super().__init__(clock)
        self.created = None
        self.response_id = None
        self.terminals = 0
        self.ambiguous = False
        self.native = {'status': 'missing'}

    def flush(self):
        raw = b'\n'.join(self.data) if not self.overflow else b''
        try:
            event = json.loads(raw)
            if isinstance(event, dict):
                response = event.get('response')
                kind = event.get('type')
                if kind == 'response.created' and isinstance(response, dict):
                    if self.created is not None:
                        self.ambiguous = True
                    self.created = response.get('id')
                if kind in ('response.completed', 'response.incomplete', 'response.failed'):
                    self.terminals += 1
                    if isinstance(response, dict):
                        rid = response.get('id')
                        if (kind == 'response.completed' and response.get('status') == 'completed'
                                and isinstance(rid, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,256}', rid)
                                and rid == self.created):
                            self.response_id = rid
                        else:
                            self.ambiguous = True
                        self.native = diagnostic(response.get('prompt_cache_diagnostics'))
        except (ValueError, TypeError):
            pass
        super().flush()


class UpstreamDiagnosticTransport:
    """Place INSIDE protocol adapter, after subscription_body has run.

Default off is exact pass-through. Enabled is deliberately serial per partition;
conflicting caller-supplied comparison IDs fail closed before any HTTP request.
Metadata snapshots do not contain response IDs, labels, headers or prompts.
"""
    def __init__(self, inner, *, enabled=False, labels_of=None, clock=time.monotonic,
                 max_age_s=120):
        if type(enabled) is not bool or not 0 < max_age_s <= 300:
            raise ValueError('explicit boolean and bounded baseline age required')
        self.inner, self.enabled, self.labels_of = inner, enabled, labels_of
        self.clock, self.max_age_s = clock, max_age_s
        self._key = secrets.token_bytes(32)
        self._baselines = OrderedDict()
        self._busy = set()
        self._rows = deque(maxlen=64)
        self._failed = {}
        self._closed = False
        self._close_attempted = False
        self._close_failure = None

    def _digest(self, value):
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        return hmac.new(self._key, raw, hashlib.sha256).hexdigest()

    def snapshot(self):
        return json.loads(json.dumps({'candidate': True, 'enabled': self.enabled,
            'subscription_support_verified': False, 'rows': list(self._rows),
            'closed': self._closed, 'active': len(self._busy),
            'unresolved': len(self._failed), 'close_failed': self._close_failure is not None}))

    def stream_chat(self, base_url, body, **kwargs):
        if self._closed:
            raise RuntimeError('diagnostic transport closed')
        if not self.enabled:
            return self.inner.stream_chat(base_url, body, **kwargs)

        async def one():
            if self._closed:
                raise RuntimeError('diagnostic transport closed')
            if base_url.rstrip('/') != API_BASE or kwargs.get('path') != '/responses':
                raise ValueError('candidate requires final official Responses wire seam')
            labels = self.labels_of() if self.labels_of is not None else None
            names = ('bot', 'scope', 'epoch', 'auth', 'account')
            if (not isinstance(labels, dict) or any(not isinstance(labels.get(k), str)
                    or not 0 < len(labels[k]) <= 512 for k in names)
                    or labels['auth'] not in ('chatgpt', 'api_key')
                    or not isinstance(body.get('model'), str) or not body['model']):
                raise ValueError('explicit trusted partition labels required')
            partition = self._digest([base_url.rstrip('/'), body['model'],
                                      [labels[k] for k in names]])
            if len(self._busy) + len(self._failed) >= 16 or partition in self._busy or partition in self._failed:
                raise RuntimeError('concurrent comparison in same partition denied')
            options = body.get('prompt_cache_options', {})
            if not isinstance(options, dict) or 'comparison_response_id' in options:
                raise ValueError('caller comparison conflict')
            baseline = self._baselines.pop(partition, None)
            if baseline is not None and not 0 <= self.clock()-baseline[1] < self.max_age_s:
                baseline = None
            shaped = ({**body, 'prompt_cache_options': {**options,
                'comparison_response_id': baseline[0]}} if baseline else body)
            self._busy.add(partition)
            probe = TerminalProbe(self.clock)
            upstream = None
            complete = False
            observation_failed = False
            primary = None
            try:
                upstream = self.inner.stream_chat(base_url, shaped, **kwargs)
                async for chunk in upstream:
                    try:
                        probe.feed(chunk)
                    except Exception:
                        observation_failed = True
                        probe.ambiguous = True
                    yield chunk  # Never re-encode, suppress or reconstruct output.
                complete = True
            except BaseException as error:
                primary = error
                raise
            finally:
                try:
                    if upstream is not None:
                        try:
                            await upstream.aclose()
                        except BaseException as cleanup:
                            complete = False
                            self._failed[partition] = (upstream, cleanup)
                            if primary is None:
                                raise
                            primary.diagnostic_cleanup_errors = (cleanup,)
                            primary.diagnostic_owner = self
                            primary.add_note('diagnostic stream close failed; partition retained')
                finally:
                    self._busy.discard(partition)
                    valid = (complete and not probe.ambiguous and probe.terminals == 1
                             and probe.response_id is not None and probe.outcome == 'completed')
                    if valid:
                        self._baselines[partition] = (probe.response_id, self.clock())
                        while len(self._baselines) > 16:
                            self._baselines.popitem(last=False)
                    self._rows.append({'partition': partition,
                        'comparison_requested': baseline is not None,
                        'observation_failed': observation_failed,
                        'comparison_id_hmac': self._digest(baseline[0]) if baseline else None,
                        'baseline_retained': valid,
                        'response_id_hmac': self._digest(probe.response_id) if valid else None,
                        'outcome': probe.outcome if complete else 'not_completed',
                        'usage': normalize_usage(probe.usage), 'diagnostic': probe.native})
        return one()

    async def aclose(self):
        self._closed = True
        self._baselines.clear()
        if not self._close_attempted:
            self._close_attempted = True
            try:
                await self.inner.aclose()
            except BaseException as error:
                self._close_failure = error
                raise
        if self._close_failure is not None:
            raise self._close_failure
        if self._busy or self._failed:
            error = RuntimeError('diagnostic transport cleanup unresolved')
            error.diagnostic_owner = self
            raise error
