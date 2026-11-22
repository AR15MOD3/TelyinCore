"""Exact machine-field assertions use actual observed values, not synonyms."""
import json,re
_FIELDS='creator|request_attribution|creation_reason|creation_intent_status|context_check|origin_linkage'
_PATTERN=re.compile(r'(?P<keys>(?:`?(?:'+_FIELDS+r')`?)(?:\s*(?:与|和|、|及)\s*`?(?:'+_FIELDS+r')`?)*)\s*(?:都|均)?\s*(?:是|为|标记为|记录为|=|：|:)\s*[`"“]?(?P<value>[a-z][a-z_]{1,60})[`"”]?')

def corrections(parts,context):
    if not isinstance(context,dict):return {}
    changes={};fenced=False
    for i,part in enumerate(parts):
        if '```' in part:
            if part.count('```')%2:fenced=not fenced
            continue
        if fenced or part.lstrip().startswith('>') or re.search(r'如果|假如|例如|举例|原话|你说|是否|不能|并非|不是|[？?]',part):continue
        for m in _PATTERN.finditer(part):
            prefix=part[:m.start()]
            if prefix.count('"')%2:continue
            if any(prefix.count(a)>prefix.count(b) for a,b in [('“','”'),('「','」'),('『','』')]):continue
            keys=re.findall(_FIELDS,m['keys'])
            actual={k:context[k] for k in keys if isinstance(context.get(k),str)}
            if actual and any(v!=m['value'] for v in actual.values()):
                changes[i]='〔Core 字段校正〕本次读取的字段原值为 '+json.dumps(actual,ensure_ascii=False)+'；字段值逐项保留，不把 unknown 与 unverified 等不同状态写成同一值。\n';break
    return changes
