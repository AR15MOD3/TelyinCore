"""Bounded correction of unsupported *historical reminder purpose* claims.

Unknown provenance is not evidence for a guessed purpose, even with a hedge.
This is not a general intent classifier: only an explicit singular reminder
subject, backed by the caller's current-batch host facts, is considered.
"""
import re

UNKNOWN_INTENT = '〔Core 证据校正〕这条提醒的创建意图尚未核实；不能从远期日期、来源缺失或记录仍保留，推断它只是占位、备忘或不打算提醒。可以提出后续处理建议，但创建原因仍需原始依据。'
_OBJECT = re.compile(r'(?:这|那)(?:条|个|笔)[^，,。！？\n]{0,8}?(?:提醒|闹钟|定时器)|该(?:提醒|闹钟|定时器)')
_HEDGE = re.compile(r'更像|像是|看起来|看着|看来|多半|大概|估计|似乎|应该是|可能|恐怕|想必|显然|说明|可见|其实|实质上|本质上|只是|仅仅|原本|当初|当时|本来')
_PURPOSE = re.compile(r'打算|有意|故意|意图|目的|用意|本意|为了|用来|想要|想让|想提醒|占位|备忘|保留[^，,。！？\n]{0,8}(?:记号|标记|记录)|留[^，,。！？\n]{0,6}(?:记号|标记|念想)|随手(?:记|设)|测试(?:用|的)')
_ASSERTION = re.compile(r'为了|用来|目的是|用意是|本意是|本来就|原本就|只是为了|仅仅为了')
_NEGATION = re.compile(r'不能|无法|不可|不应|不代表|不意味着|不等于|没有证据|尚未核实|未知|不确定|不知道|不要|别把|不能排除')
_QUOTES = re.compile(r'[“「『][^”」』]*[”」』]|"[^"\n]*"')


def corrections(parts, due=None):
    result = {}; fenced = False
    # A date is a reference only when it names this sole observed schedule.
    # Do not borrow today's date or infer a reminder from unrelated prose.
    date_object = None
    if isinstance(due,str) and re.match(r'\d{4}-\d{2}-\d{2}T',due):
        day,year=due[:10],due[:4]
        date_object=re.compile(r'(?<![0-9])(?:'+re.escape(day)+'|'+year+r'年?)(?![0-9-])\s*(?:这|那)(?:个)?(?:日期|时间|排期)')
    for i, original in enumerate(parts):
        if '```' in original or '~~~' in original:
            if (original.count('```')+original.count('~~~')) % 2: fenced = not fenced
            continue
        if fenced or original.lstrip().startswith('>'): continue
        # A trailing request for a decision cannot turn its preceding assertion
        # into a mere question. Preserve that request when replacing the claim.
        split = re.split(r'([，,]\s*(?=要不要|需不需要|你要|你想))', original, maxsplit=1)
        text = split[0]; followup = ''.join(split[1:]).lstrip('，, ') if len(split)>1 else ''
        if text.rstrip().endswith(('？','?')): continue
        quoted = [m.span() for m in _QUOTES.finditer(text)]
        targets=list(_OBJECT.finditer(text))
        dates=list(date_object.finditer(text)) if date_object is not None else []
        target = next((m for m in sorted(targets+dates,key=lambda m:m.start()) if not any(a <= m.start() < b for a, b in quoted)), None)
        if target is None: continue
        prefix, claim = text[:target.start()], text[target.end():]
        if re.search(r'如果|假如|假设|即使|除非|另一|其他|别的|例如|举例|原话|他说|她说|你说', prefix + claim): continue
        # Current proposals belong to the agent; do not recast them as a
        # claim about why an earlier reminder was originally created.
        if re.search(r'(?:我|我们)(?:现在|接下来)?(?:打算|准备|决定|想|希望|建议|可以|会)', prefix): continue
        if re.match(r'\s*(?:是否|是不是|会不会|要不要|今后|接下来|可以|建议|不如|以后)', claim): continue
        purpose = _PURPOSE.search(claim)
        if purpose is None: continue
        before_purpose = re.split(r'[，,；;]|但是|不过|所以|因此', claim[:purpose.start()])[-1]
        if _NEGATION.search(prefix[-12:]) or _NEGATION.search(before_purpose): continue
        if not (_HEDGE.search(claim) or _ASSERTION.search(claim)): continue
        retained_prefix = prefix if target in dates else ''
        result[i] = retained_prefix + UNKNOWN_INTENT + '\n' + followup
    return result
