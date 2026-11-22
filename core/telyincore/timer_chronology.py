"""Compare explicit creation/message chronology using typed authorized reads.

No wall-clock guesses, parsed model prose, implicit timezone, or attribution of
business causality. Ambiguous reminders, context events or reread timestamps
remain unbound. Record instants describe records, not when a real event happened.
"""
from datetime import datetime
import re


def instant(value):
    if not isinstance(value, str) or len(value) > 64: return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp if stamp.tzinfo is not None and stamp.utcoffset() is not None else None
    except (ValueError, OverflowError): return None


def corrections(parts, timers, event_times):
    if len(timers) != 1: return {}
    context, _ = next(iter(timers.values()))
    if (not isinstance(context, dict) or context.get('context_check') != 'verified'
            or context.get('context_incomplete') is not False): return {}
    events = context.get('context_events')
    if not isinstance(events, list) or len(events) != 1 or not isinstance(events[0], dict): return {}
    ref = events[0].get('event_ref')
    if not isinstance(ref, str) or not re.fullmatch(r'event:[0-9]{1,80}', ref): return {}
    matches = {fact[1] for fact in event_times if isinstance(fact, (tuple, list))
               and len(fact) == 2 and fact[0] == ref and isinstance(fact[1], str)}
    if len(matches) != 1: return {}
    message_raw = next(iter(matches)); created_raw = context.get('created_at')
    message, created = instant(message_raw), instant(created_raw)
    if message is None or created is None: return {}
    actual = (created > message) - (created < message)
    relation = {1:'晚于', -1:'早于', 0:'等于'}[actual]
    correction = (f'〔Core 时间校正〕提醒创建记录时间为 {created_raw}，本轮已读取的消息 {ref} '
                  f'记录时间为 {message_raw}；按这两个记录时刻，创建记录{relation}该消息记录。'
                  '这只比较记录时间，不推定现实发生顺序或用户委托归属。\n')
    changes = {}; fenced = False
    pattern = re.compile(r'创建(?:记录|时间戳|时间|时刻)?[^，,；;。！？!?\n]{0,16}?'
                         r'(?P<direction>早|晚)(?:它|于(?:这条|该条)?(?:消息|留言))')
    for i, part in enumerate(parts):
        if '```' in part:
            if part.count('```') % 2: fenced = not fenced
            continue
        if fenced or part.lstrip().startswith('>') or re.search('[?？]', part): continue
        plain = re.sub(r'[“「『][^”」』]*[”」』]|"[^"\n]*"', '', part)
        # "it" is only bound when this segment names the message being read.
        if not (ref in plain or re.search('消息|留言', plain)): continue
        if re.search('另一|其他|别的|如果|假如|假设|例如|举例|不能|无法|不确定|未必|可能|并非|不是|原话', plain): continue
        refs = set(re.findall(r'event:\d+', plain))
        if refs and refs != {ref}: continue
        for match in pattern.finditer(plain):
            claimed = -1 if match['direction'] == '早' else 1
            if claimed != actual:
                changes[i] = correction
                break
    return changes
