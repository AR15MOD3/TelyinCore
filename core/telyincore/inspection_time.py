"""A point-in-time inspection and an unchecked item do not attest continuous custody."""
import re

_ID = r'[A-Z]{1,10}-[0-9]{2,10}(?![A-Za-z0-9-])'
_ID_RE = re.compile(_ID)
_WEAK_DOC = r'(?:变更通知单|变更单|调拨单|更改单|交接单|移交单|送货单)'
_POINT_DOC = r'(?:(?:签收|收货|到货|现场|验收)?(?:检视|检查|检验)(?:单|记录|表)|验收记录)'
_DOC = re.compile(rf'(?:(?:{_ID})\s*(?P<a>{_WEAK_DOC}|{_POINT_DOC})|(?P<b>{_WEAK_DOC}|{_POINT_DOC})\s*(?:{_ID}))\s*(?:原文)?\s*[:：。]\s*(?P<body>.+)',re.S)
_WEAK = re.compile(r'(?:我|我们|本人)?(?:当时|现场)?(?:没(?:有)?|并未|未)(?:亲自|实际)?(?:查看|看|检查|核对|查验)(?:过)?'
                   r'(?:箱|盒)(?:里面?|内)(?:的|所附|附着|放置|放着)*(?:的)?(?:样卡|样品|样片|实物|附件|零件)[。.]?')
_POINT = re.compile(r'(?:(?:签收|收货|检视|检查|验收)(?:时|当时|现场)?|当时|现场)?(?:未|没有|没)(?:开[盒箱]|打开(?:外)?[箱盒]子?)')
_BOX_NO = re.compile(r'(?:(?:签收|收货|检视|检查|验收)(?:时|当时|现场)?|当时|现场)?外[盒箱](?:交接号|编号|号)\s*[：:]?\s*[A-Za-z0-9\u3400-\u9fff-]{1,16}')
_CHAIN = re.compile(r'全程|全过程|连续(?:录像|监控|封存|记录)|从未(?:打开|拆封|开封)|始终(?:封存|未开|未拆)|自封存|一直封存|从封装')
_LIFE = re.compile(r'(?:这(?:个|只)?)?[箱盒]子?(?:从(?:来)?|始终|一直|曾经)?(?:都)?(?:没(?:有)?|未曾|从未|未)(?:被)?(?:打开|拆开|开启|拆封|开封)过'
                   r'|[盒箱]子?(?:从(?:来)?|始终|一直)?(?:都)?(?:没(?:有)?|未曾|从未)(?:开过|拆过)'
                   r'|(?:从(?:来)?没(?:有)?|从未|始终未|一直未|一直没(?:有)?)(?:被)?(?:打开|拆开|开启|拆封|启封|开封|开[箱盒])(?:过)?')
_LABEL = r'(?:[①②]|第[一二12]张|第[一二12]份|[12][)）.、])'
_QUOTE = r'(?:"[^"\n]{1,65}"|“[^”\n]{1,65}”|「[^」\n]{1,65}」|『[^』\n]{1,65}』)'
_SUM = re.compile(rf'(?P<quote>{_QUOTE})(?P<approve>[，,：:\s]*(?:这句(?:话)?(?:只|仅)?对)?(?P<label>{_LABEL})(?:才)?成立)')
_SIDE = re.compile(rf'(?P<which>{_LABEL}|{_WEAK_DOC})(?:本身|也|还)?(?:算(?:是)?|是|可作(?:为)?|能作(?:为)?)(?:一(?:份|点|个))?(?:旁证|佐证)(?:[，,]?不算(?:直接)?证据)?(?=[。；;\s]|$)')
_BOTH = r'(?:这|那)?两(?:张(?:单)?|条记录|份记录|份材料|份单据)(?:都|均)?'
_ASSERT = re.compile(rf'(?P<who>{_POINT_DOC}|{_BOTH}|这(?:一)?(?:条|张|份)(?:记录|单据)?|因此|所以|由此|可见)(?:就|足以|能|可以|直接|明确|也|的确|确实|相互|互相)*(?:支撑|支持|证明|证实|说明|佐证|印证|断定|得出)(?:了|的是)?\s*[：:]?\s*[“「『"]?')
_REPORTED = re.compile(r'假如|如果|假设|若|能否|是否|不能|无法|并非|未必|不一定|难以|别把|不要|不是(?:说|指)|不认为|也许|可能|误以为|有人认为|[他她]说|(?:朋友|用户|有人)(?:问|说|称|认为)')
_REJECT = re.compile(r'^[”」』"\s]*(?:(?:这个|这句|的)(?:说法|结论|推断))?[，,\s]*(?:也|仍|其实|并)?(?:不能|不成立|无法|待确认|站不住|不对|没有依据|不准确)')
_POINT_WORD = re.compile(_POINT_DOC)
_WEAK_WORD = re.compile(_WEAK_DOC)
_POINT_TEXT = '检视记录只记了检视当时未开盒，单凭它无法判断此前或此后是否开过'
_SUM_TEXT = '，这两份材料都不能证明整个期间从未开盒，检视记录只记了检视当时的情况'
_SIDE_TEXT = '只记了未查看内装，无法据此判断开盒情况'


def _number(label):
    return 1 if any(c in label for c in '①一1') else 2


def _pair(code, sources):
    matches = {'point':set(), 'weak':set()}
    for _, text, reference, scope in sources:
        if not isinstance(reference,str) or not reference.startswith('record:'):
            continue
        ids = set(_ID_RE.findall(text))
        if code not in ids:
            continue
        doc = _DOC.fullmatch(text.strip())
        if not doc:
            if re.search(r'监控|录像|封存|封条|开启历史|全程',text):
                return False
            if _CHAIN.search(text) or _LIFE.search(text):
                question = re.match(r'\s*(?:朋友说|有人说|朋友问|有人问|请问|请核对|想问)', text)
                if not question or not re.search(r'[？?]|请(?:核|查|逐|说)', text):
                    return False
            continue
        if ids != {code}:
            return False
        title,body=doc['a'] or doc['b'],doc['body'].strip()
        if _WEAK_WORD.fullmatch(title) and _WEAK.fullmatch(body):
            matches['weak'].add((reference,scope))
        elif _POINT_WORD.fullmatch(title):
            fields=[s.strip() for s in re.split('[，,。；;\n]',body) if s.strip()]
            if (any(_POINT.fullmatch(s) for s in fields)
                    and all(_POINT.fullmatch(s) or _BOX_NO.fullmatch(s) for s in fields)):
                matches['point'].add((reference,scope))
            else:
                return False
        else:
            return False
    return (len(matches['weak']) == len(matches['point']) == 1
            and next(iter(matches['weak']))[1] == next(iter(matches['point']))[1]
            and next(iter(matches['weak']))[0] != next(iter(matches['point']))[0])


def corrections(parts, originals, in_quote):
    """Only review an affirmative attribution to two independently visible archive originals."""
    if not any(_ASSERT.search(p) or _SUM.search(p) or _SIDE.search(p) for p in parts):
        return {}
    full=''.join(parts)
    if not _LIFE.search(full) or re.search(r'连续(?:录像|监控|封存|记录)|(?:全程|一直)(?:录像|监控|封存)|从封装(?:至|到)',full):
        return {}
    codes=set(_ID_RE.findall(full))
    if len(codes)!=1 or not _pair(next(iter(codes)),originals()):
        return {}
    labels={}
    for part in parts:
        label=re.match(rf'\s*[-*]*\s*(?P<n>{_LABEL})\s*[*]*(?P<doc>{_POINT_DOC}|{_WEAK_DOC})',part)
        if label:
            n=_number(label['n']);kind='point' if _POINT_WORD.fullmatch(label['doc']) else 'weak'
            if n in labels and labels[n]!=kind:
                return {}
            labels[n]=kind
    result={}; prior=''; fenced=False; earlier_endorsement=False
    for i,part in enumerate(parts):
        if '```' in part:
            if part.count('```')%2: fenced=not fenced
            prior=''
            continue
        if fenced or not part.strip() or part.lstrip().startswith('>') or part.rstrip().endswith(('?','？')):
            prior=''
            continue
        if re.search(r'以下(?:是|为)?(?:转述|举例|原话|照录)|(?:逐字引用|演练用语|模拟说法|举例说明)', prior + part):
            prior=part
            continue
        changes=[]
        for m in _ASSERT.finditer(part):
            claim=_LIFE.match(part,m.end())
            if (not claim or in_quote(part,m.start()) or _REJECT.match(part[claim.end():])
                    or not re.match(r'[”」』"，,；;。\s]|$', part[claim.end():])):
                continue
            before=part[:m.start()]
            if _REPORTED.search(before) or re.search(r'另一|另外|别的|其他|其它',before) or re.search(r'签收时|检视时|当时|截至',part[m.start():claim.start()]):
                continue
            joint=re.fullmatch(_BOTH,m['who'])
            if not (joint or _POINT_WORD.fullmatch(m['who'])) and not _POINT_WORD.search(prior+' '+before):
                continue
            end=claim.end()
            if part[end:end+1] in ('”','」','』','"'):end+=1
            changes.append((m.start(),end,_POINT_TEXT + ('；未查内装也无法确定开盒情况' if joint else '')))
            earlier_endorsement=True
        for m in _SUM.finditer(part):
            quote=m['quote'][1:-1]
            if labels.get(_number(m['label']))!='point' or in_quote(part,m.start()) or _REJECT.match(part[m.end():]):
                continue
            if (_REPORTED.search(quote) or re.search(r'并不|不都|不代表|不等于|待确认|当时|签收时|检视时|查验时|期间|截至|吗|[?？]',quote)
                    or re.search(r'(?:转述|复述|是他(?:的)?原话|我不(?:认同|同意))',part[m.end():])):
                continue
            if not (_LIFE.search(quote) or (earlier_endorsement and re.fullmatch(r'(?:这|那)?两(?:张|份|条)(?:都|均)(?:能|可以)?(?:证明|证实)',quote))):
                continue
            if re.search(r'如果|假如|假设|若|未必|不认为|不能说|是否',part[:m.start()]):
                continue
            changes.append((m.start('approve'),m.end(),_SUM_TEXT))
        for m in _SIDE.finditer(part):
            if in_quote(part,m.start()) or (not _WEAK_WORD.fullmatch(m['which']) and labels.get(_number(m['which']))!='weak'):
                continue
            if _REPORTED.search(part[max(part.rfind('，',0,m.start()),part.rfind('。',0,m.start()))+1:m.start()]) or _REJECT.match(part[m.end():]):
                continue
            if not (changes or _LIFE.search(part) or _LIFE.search(prior) or _SUM.search(prior)):
                continue
            changes.append((m.end('which'),m.end(),_SIDE_TEXT))
        if changes:
            text=part
            for start,end,value in sorted(changes,reverse=True):
                text=text[:start]+value+text[end:]
            result[i]=text
        prior=part
    return result
