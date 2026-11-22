"""Finite fresh-save assertions require an actual current-batch save receipt."""
import re


def save_corrections(parts, receipts):
    changes={};fenced=False
    ids={value for r in receipts for key in ('id','update_ref')
         if isinstance((value:=r.get(key)),str)}
    for i,part in enumerate(parts):
        if '```' in part:
            if part.count('```')%2:fenced=not fenced
            continue
        if fenced or part.lstrip().startswith('>'):continue
        for clause in re.split(r'[。！？\n]',part):
            if not re.search(r'关注|检查点|attention_update|attention_checkpoint',clause):continue
            if re.search(r'如果|假如|例如|示例|原话|他说|她说|你说|之前|上次|历史|只证明|不证明|可以|能否|是否|尚未|还没|未能|不能确认|无法确认',clause):continue
            if not re.search(r'(?:已(?:经)?|刚(?:刚)?)(?:经)?(?:保存|记录|更新|记下|创建|新增|留下)|顺手.{0,24}(?:留了|记了|存了|保存|记录|更新)',clause):continue
            refs=set(re.findall(r'att-[0-9a-f]{32}|attention:[0-9a-f:-]{8,80}',clause))
            if receipts and (not refs or refs<=ids):continue
            changes[i]='〔Core 证据校正〕本批没有与这句关注保存声明相符的成功回执，暂不能确认这次已经保存；这不证明更早没有记录。\n'
            break
    return changes


def attempt_corrections(parts, counts):
    """Explicit current-batch attempt counts, never inferred success/failure."""
    numbers={'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,'十一':11,'十二':12}
    changes={};fenced=False
    for i,part in enumerate(parts):
        if '```' in part or '~~~' in part:
            if (part.count('```')+part.count('~~~'))%2:fenced=not fenced
            continue
        if fenced or part.lstrip().startswith('>'):continue
        plain=re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"',lambda match:' '*len(match.group()),part)
        if re.search(r'如果|假如|例如|示例|原话|上次|上一批|之前|历史|是否|有没有|不确定|[？?]',plain):continue
        # An explicit exhaustive inventory is contradicted by a current-batch
        # attention call receipt even when the omitted call's success is unknown.
        # Do not infer which natural-language aliases name other tools.
        edits=[]
        for match in re.finditer(r'(?:本轮|本次|这一轮|这轮|当前批次)\s*(?:我\s*)?(?:只|仅)(?:调用|使用|执行)了[^，,；;。！？!?\n]{1,256}(?=[，,；;。！？!?\n]|$)',plain):
            prefix=plain[:match.start()].rstrip()
            if prefix and prefix[-1] not in '，,；;。\n':continue
            claim=match.group()
            if not re.search(r'(?<![a-zA-Z0-9_])[a-z][a-z0-9]*_[a-z0-9_]{1,63}(?![a-zA-Z0-9_])',claim):continue
            if re.search(r'关注|检查点|attention_update|attention_checkpoint|不是|并非|不能|没有|没调用',claim):continue
            omitted=[(name,total) for name,total in counts.items()
                     if name in ('attention_update','attention_checkpoint') and total>0]
            if omitted:
                summary='、'.join(f'{name} {total} 条' for name,total in omitted)
                edits.append((match.start(),match.end(),'〔Core 调用核对〕本批还取得 '+summary+' 调用结果；这不是成功次数，是否保存以各条回执及实际状态为准'))
        # Same-length quotation masking preserves exact splice offsets.
        if edits:
            value=part
            for start,end,replacement in reversed(edits):
                value=value[:start]+replacement+value[end:]
            if value!=part:changes[i]=value
        for name,total in counts.items():
            number=r'([0-9]{1,3}|十二|十一|十|[零一二两三四五六七八九])'
            patterns=(number+r'次\s*`?'+name+r'`?',r'`?'+name+r'`?\s*(?:调用|尝试|更新)?\s*(?:了|共|总共)?\s*'+number+r'次')
            for pattern in patterns:
                for match in re.finditer(pattern,plain):
                    value=match[1];claimed=int(value) if value.isascii() else numbers[value]
                    if claimed!=total:
                        changes[i]=f'〔Core 调用核对〕本批实际取得 {total} 条 {name} 调用结果；这不是成功次数，是否保存以各条回执及实际状态为准。\n'
    return changes


def denial_of_speech(parts, originals):
    """A denial of having made a statement alone cannot settle its outcome."""
    if len(originals) != 1 or not originals[0]['current_speech']:
        return {}
    fact = re.fullmatch(r'\s*我(?:没(?:有)?|从未)说(?:过)?[：:\s]*[“「『"]?我?(?:已(?:经)?)?'
                        r'(读完|看完|做完|完成|收到|取消|撤销)(?:了)?[。]?[”」』"]?[。.!！\s]*',
                        originals[0]['text'])
    if not fact:
        return {}
    action = fact[1]
    claim = re.compile(r'(?:否认|否定)(?:了)?\s*[“「『"]?(?:已(?:经)?)?' + action +
                       r'(?:了)?[”」』"]?\s*(?:(?:这|该|这一)(?:状态|事实|结果)|的(?:事实|结果|状态))')
    changes = {}
    fenced = False
    for index, part in enumerate(parts):
        if '~~~' in part or '```' in part:
            if (part.count('~~~') + part.count('```')) % 2:
                fenced = not fenced
            continue
        if fenced or part.lstrip().startswith('>'):
            continue
        for match in claim.finditer(part):
            preceding = part[:match.start()]
            if not re.search(r'发言者|说话人|否定句|这句(?:话|原话)|对方', preceding):
                continue
            if preceding.count('"') % 2 or any(preceding.count(left) > preceding.count(right)
                    for left, right in [('“','”'),('「','」'),('『','』')]):
                continue
            prefix = re.split('[，,。；;！!?？\n]', part[:match.start()])[-1]
            if re.search(r'不能|无法|不代表|不等于|不是|并非|没有|并未|未必|如果|假如|是否|例如|举例',prefix):
                continue
            if re.search(r'[？?]',part[match.end():]):
                continue
            changes[index] = f'这位发言者否认说过自己已经{action}。是否{action}，仍需另行确认。\n'
            break
    return changes


def progress_source_corrections(parts, speakers):
    """Keep a first-person report of undisclosed progress attached to its account."""
    source = re.compile(r'我的(?:阅读|工作|任务|学习)?进度(?:还没(?:有)?|尚未)(?:说|说明|告诉你|透露)[。.!！]?')
    accounts = {key for key, texts in speakers.items() if len(texts) == 1 and source.fullmatch(texts[0])}
    if not accounts:
        return {}
    claim = re.compile(r'(?<![\w:.@-])([a-z][a-z0-9_-]{1,31}:[a-zA-Z0-9_.-]{1,128})\s*'
                       r'(?:仅|只是|只)转述(?:了)?(?:其|自己(?:的)?)(?:阅读)?进度(?:尚未|还未|没有)(?:说明|透露|告知)')
    changes = {}
    fenced = False
    for i, part in enumerate(parts):
        if '```' in part or '~~~' in part:
            if (part.count('```') + part.count('~~~')) % 2:
                fenced = not fenced
            continue
        if fenced or part.lstrip().startswith('>') or re.search(r'如果|假如|例如|举例|是否|[?？]', part):
            continue
        def correct(match):
            before = part[:match.start()]
            if (match[1] not in accounts or before.count('"') % 2 or
                    any(before.count(a) > before.count(b) for a,b in [('“','”'),('「','」'),('『','』')]) or
                    re.search(r'不(?:能|代表|是)|并非|无法', re.split('[，,；;]',before)[-1])):
                return match[0]
            return match[1] + ' 的消息只说明本人尚未给出进度'
        updated = claim.sub(correct, part)
        if updated != part:
            changes[i] = updated
    return changes
