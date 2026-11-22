"""Public review failures: fixed codes only, no upstream text or credentials."""
SAFE_CODES = frozenset({
    'portrait_per_write_request_budget', 'portrait_kind_protocol',
    'portrait_kind_binding', 'portrait_kind_unknown', 'portrait_review_tool_call',
    'invalid_json', 'portrait_cancelled', 'portrait_chat_stop', 'portrait_chat_missing_done',
    'portrait_chat_missing_stop', 'portrait_chat_identity', 'portrait_chat_identity_drift',
    'portrait_chat_choice', 'portrait_tool_free_completion',
    'portrait_incomplete_or_refused', 'response_fields', 'response_field_type',
    'binding_request_id', 'binding_request_sha256', 'binding_proposal_sha256',
    'binding_author', 'dimension_fields', 'dimension_values', 'inconsistent_verdict',
    'source_binding', 'source_handle_unknown', 'response_type_or_size', 'duplicate_key', 'nonfinite_number', 'object_required',
})
SAFE_TYPES = frozenset({'Refused', 'ReviewProtocolError', 'TimeoutError',
    'JSONDecodeError', 'TransportError', 'LLMError', 'KeyError', 'TypeError', 'ValueError'})

def review_diagnostics(reason, backend):
    result = {}
    if reason.startswith('review_error:'):
        parts = reason.split(':', 2)
        kind = parts[1] if len(parts) > 1 else ''
        code = parts[2] if len(parts) > 2 else ''
        if kind == 'ReviewProtocolError' and code.startswith(('Expecting ', 'Extra data:', 'Unterminated string', 'Invalid ')):
            code = 'invalid_json'
        result['error_type'] = kind if kind in SAFE_TYPES else 'OtherError'
        result['error_code'] = code if code in SAFE_CODES else ('review_timeout' if kind == 'TimeoutError' else 'unclassified_review_error')
    stage = getattr(backend, 'review_stage', None)
    if stage in ('classification', 'verdict'): result['stage'] = stage
    for name in ('requests', 'max_requests'):
        value = getattr(backend, name, None)
        if type(value) is int and 0 <= value <= 16: result[name] = value
    evidence = getattr(backend, 'protocol_evidence', None)
    if isinstance(evidence, list): result['protocol_evidence'] = evidence[-12:]
    return result


def protocol_evidence(stage, check, values):
    """Bounded structural evidence; no headers, keys, prompts or source text."""
    import re
    def safe(value):
        if value is None or isinstance(value, (bool, int)): return value
        text = str(value)[:160]
        return re.sub(r'(?i)(?:sk-|bearer\s+)[a-z0-9_.-]+', '[redacted]', text)
    return dict(stage=stage, check=check, **{key:safe(value) for key,value in values.items()})


def verdict_evidence(payload, text):
    import hashlib, json
    result = dict(stage='verdict', check='response_binding', response_chars=len(text),
                  response_sha256=hashlib.sha256(text.encode()).hexdigest())
    try:
        from ..portrait_shadow.review import strict_json
        data = strict_json(text)
        request = json.loads(payload)
        result['binding_matches'] = {key: data.get(key)==request.get(key)
            for key in ('request_id','request_sha256','proposal_sha256','author')}
        result['source_id_matches'] = sum(row['message_id']==data.get('source_id') for row in request['sources'])
        result['source_pair_matches'] = sum(row['message_id']==data.get('source_id') and
            row['source_sha256']==data.get('source_sha256') and row['author']==request['author'] for row in request['sources'])
        result['source_count'] = len(request['sources'])
        result['source_id'] = protocol_evidence('', '', {'value':data.get('source_id')})['value']
        result['source_handle'] = protocol_evidence('', '', {'value':data.get('source_sha256')})['value']
        result['expected_sources'] = [dict(source_id=row['message_id'],source_handle=row['source_sha256']) for row in request['sources']]
        result['verdict'] = data.get('verdict') if data.get('verdict') in ('pass','fail','unknown') else 'invalid'
        result['dimensions'] = {key:value if value in ('pass','fail','unknown') else 'invalid'
            for key,value in data.get('dimensions',{}).items() if key in __import__('telyincore.portrait_shadow.review',fromlist=['DIMENSIONS']).DIMENSIONS} if isinstance(data.get('dimensions'),dict) else {}
    except (ValueError, TypeError):
        result['parse_status'] = 'invalid_json'
    return result


def response_evidence(stage, text):
    import hashlib
    from ..diagnostic_history import redact
    safe = redact(text)
    return dict(stage=stage, check='audit_response', response_chars=len(text),
        response_sha256=hashlib.sha256(text.encode()).hexdigest(),
        response_excerpt=safe[:4096], excerpt_truncated=len(safe)>4096)
