"""A proposal can presuppose a false fact about an already verified date.

Correct only replacement proposals implying the existing finite due date will
not arrive. Ordinary earlier/later proposals and delivery uncertainty survive.
"""
import re

_REPLACEMENT = re.compile(r'(?:换|改|另设|重设|重新设)(?:成|为|到)?(?:个|一个)?\s*(?:(?:真(?:正)?|确实|实际|肯定)?(?:会|能)(?:够)?到期的|真实的?)(?:日子|日期|时间)')
_QUALIFIED = re.compile(r'如果|假如|假设|即使|除非|例如|举例|原话|另一|其他|别的|不能|不应|不代表|不意味着|无须|不必|不用|没必要')
_QUOTES = re.compile(r'[“「『][^”」』]*[”」』]|"[^"\n]*"')

def corrections(parts, boundary):
    changes={};fenced=False
    for i,part in enumerate(parts):
        if '```' in part:
            if part.count('```')%2:fenced=not fenced
            continue
        if fenced or part.lstrip().startswith('>') or _QUALIFIED.search(part):continue
        quoted=[m.span() for m in _QUOTES.finditer(part)]
        hits=[m for m in _REPLACEMENT.finditer(part) if not any(a<=m.start()<b for a,b in quoted)]
        hits=[m for m in hits if not re.search(r'你说|他说|她说',part[:m.start()])]
        if not hits:continue
        # Keep cancellation/change choices and their question marks. Only the
        # unsupported premise is removed; no new action is authorized.
        rewritten=part
        for m in reversed(hits):
            replacement=re.sub(r'(?:(?:真(?:正)?|确实|实际|肯定)?(?:会|能)(?:够)?到期的|真实的?)(?:日子|日期|时间)', '新的日期',m.group())
            rewritten=rewritten[:m.start()]+replacement+rewritten[m.end():]
        changes[i]=boundary+'\n'+rewritten
    return changes
