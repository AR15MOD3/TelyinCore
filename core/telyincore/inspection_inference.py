"""Keep lack of inspection distinct from proof that a container remained unopened."""
from functools import cache
import re


_NEG = r'(?:没有|并未|未曾|没|未)'
_BOX = r'(?:盒子?|箱子?)'
_INSIDE = _BOX + r'(?:里面?|内)(?:的)?(?:样卡|样品|实物|样片|附件|零件)'
_UNCHECKED = re.compile(_NEG + r'(?:亲自|实际)?(?:查看|检查|核对|核验|检视|查验|看|检验|确认)(?:过)?' + _INSIDE)
_UNOPENED = re.compile(_NEG + r'(?:打开|开过|开|拆开)(?:过)?(?:这[个只])?' + _BOX
                       + r'|' + _BOX + r'(?:当时|一直)?(?:没(?:有)?|未)(?:被)?(?:打开|开)(?:过)?')
_INFER = re.compile(r'(?:因此|所以|因而|可见|意味着|这(?:就|足以|也|能)?(?:证明|说明)|足以(?:说明|证明))'
                    r'[，,：:\s]*(?:可以(?:确认|断定)|当时|肯定|必然|也|就|一直|确实|是|[“「"『*])*')
_SHARED = re.compile(r'(?:这|那)?(?:两边|两条(?:记录|留言|原文)?|两份(?:记录|材料|单据)?|两者|二者)'
                     r'(?:都|也都|均)(?:对得上|能证实|能证明|证明了?|证实了?)'
                     r'[\s：:“「"『*]*')
_OPEN_CHECK = re.compile(r'\s*(?:来|去|对)?(?:核对|确认|检查|查看|检视|查验)')
_DISPUTED = re.compile(r'\s*(?:(?:这|那)(?:个结论|项结论|一点|句话|一说法)|的(?:说法|结论|推断)|一事)?'
                       r'\s*(?:仍|还|也|是|并)?(?:不能|不成立|无法|不一定|未必|没有依据|待确认|尚待|尚未|不正确|不准确)')
_QUALIFIED = re.compile(r'(?:如果|假如|假设|是不是|是否|未必|不一定|不能|无法|并非|不等于|不代表|'
                        r'不是说|别说成|不要说成|不要据此说|否认|误称|误以为|据说|例如|比如|假设)')
_DOC_NAME = r'(?:变更通知单|变更单|交接单|调拨单|更改单|送货单|移交单)'
_INSPECTION = r'(?:(?:收货|到货|验收)?(?:检视|检查|检验)(?:单|记录|表)|验收记录)'
_ID = r'[A-Z]{1,10}-[0-9]{2,10}(?![A-Za-z0-9-])'
_DOC = re.compile(_DOC_NAME + r'(?:\s*' + _ID + r')?')
_ATTRIBUTED = re.compile(
    r'(?P<intro>根据|依据|按照|据|按)?(?:这份|该|这张)?(?P<name>' + _DOC_NAME + r')'
    r'(?:\s*(?P<id>' + _ID + r'))?'
    r'(?P<pair>\s*(?:和|与|、|及)(?:那张|另附的|那份)?' + _INSPECTION + r')?'
    r'[\s，,:：]*(?:里|上)?(?:都|均|也|还|当时|现场|明确|清楚)*'
    r'(?P<verb>写明|说明|写着|注明|记载|记录|写道|证明|显示|标明|标为|说)?'
    r'[\s，,:：]*(?:当时|现场|是|都|均|也|明确|确实|写的(?:是)?|[“「"『*])*')
_ANY_OPEN = re.compile(r'(?:开|拆|启)(?:过|了)?(?:这[个只])?(?:包装|外)?[盒箱]|'
                       r'未拆封|原封|封条完好|未启封|未开封|已拆封|已开封|已启封|(?:[盒箱]子?)(?:曾经|已经|当时|还|一直|是|被|没(?:有)?|未)*(?:打开|拆开|开过)')
_AFFIRM_OPEN = re.compile(r'(?:^|[，,。；;：:\n])\s*我(?:确实|已经|当时)?(?:打开|拆开|开过)(?:了)?(?:这[个只])?(?:包装|外)?[盒箱]子?(?=[，,。；;：:\s]|$)')
_QUOTED_CONTEXT = re.compile(r'示例|例句|演练|排练|台词|示范|模拟|举例|比方|转引|转述他人|有人说|他(?:说|称)|她(?:说|称)|假设')
_OTHER_TIME = re.compile(r'昨天|前天|次日|次月|前一天|后来|之前|之后|(?:19|20)\d{2}[-年]|\d{1,2}月\d{1,2}[日号]')

_DOCUMENT_OPEN = re.compile(
    r'(?:原文[：:]|末栏(?:明确)?(?:注明|写着|写的是|写[：:]|写明)|'
    r'(?:变更单(?:\s*[A-Z]{1,10}-\d{2,10})?(?:上|里)?(?:也)?|原单)\s*(?:明确)?(?:注明|写明|写着|标为))'
    r'\s*[：:“「"『*]*' + r'(?:未开[盒箱]|没有开[盒箱]|没有打开[盒箱]子?)'
    r'(?=[”」』"*、，。；,;\s]|$)')
_CLOSE = re.compile(r'[”」』"*]+')

BOUNDARY = '仅凭这项未做的检查，不能判断当时是否打开过包装'
FEEDBACK = '请分开说明是否查看盒内，以及是否开过盒。可以保留另一份记录对开盒情况的明确记载，并说明它指的是哪个时点。未查看内装不能作为未开盒的旁证。'


def _in_quote(text, at):
    close = {'“': '”', '「': '」', '『': '』', '"': '"', '‘': '’'}
    stack = []
    for char in text[:at]:
        if stack and char == stack[-1]:
            stack.pop()
        elif char in close:
            stack.append(close[char])
    return bool(stack) or text[:at].count('`') % 2 == 1


def _closed(part, connector):
    opening = _UNOPENED.match(part, connector.end())
    if not opening:
        return None
    stop = opening.end()
    marks = _CLOSE.match(part, stop)
    if marks:
        stop = marks.end()
    if _OPEN_CHECK.match(part, stop) or _DISPUTED.match(part, stop):
        return None  # "没有开盒核对" can describe the unperformed inspection as a unit.
    return stop


def _antecedent(prefix, prior):
    # Check only the immediately preceding assertion. A separate quotation
    # or a paragraph break cannot supply a new asserted causal relationship.
    if found := _UNCHECKED.search(prefix):
        if not prefix[found.end():].strip(' \t，,。；;：:、”」』"*_'):
            return prefix
        return ''
    if not prefix.strip(' ，,。；;：:\t*-'):
        return prior
    return ''


def _corroborated(premise, sources):
    doc = _DOC.search(premise)
    if not doc:
        return False
    label = doc.group()
    # Only abstain on a possibly corroborating original. Never infer a date or
    # source identifier from a match, or insert its words into a reply.
    candidates = {original for _, original, _, _ in sources()
                  if label in original and _UNCHECKED.search(original)
                  and not _QUOTED_CONTEXT.search(original)
                  and not re.search(r'另(?:附|有|一)|另外|另一', original)}
    return bool(candidates) and all(_DOCUMENT_OPEN.search(original) for original in candidates)


def _own_record(original, name, identifier):
    doc = _DOC.search(original[:80])
    if (not doc or not doc.group().startswith(name)
            or not re.fullmatch(r'[「“\s]*(?:(?:我转述一下|我手里这张|这份|归档留言)[：: ]*)?', original[:doc.start()])
            or _DOC.search(original, doc.end())):
        return False
    if identifier and re.search(_ID, doc.group()) is None:
        return False
    if identifier and doc.group().split()[-1] != identifier and doc.group() != name + identifier:
        return False
    # The opening words of an annex may name the change order to which it is
    # attached. They do not identify the annex itself as the change order.
    return re.match(r'\s*(?:另附|另有|附件|所附|附[有带]?|的附)', original[doc.end():]) is None


def _weak_statement(original):
    for clause in re.split(r'[，,。；;：:\n]', original):
        found = _UNCHECKED.search(clause)
        if found and re.fullmatch(r'[\s「“"『]*(?:(?:我|我们|本人|当时|这次|亲自|经办人|当班人员)\s*)*', clause[:found.start()]):
            return True
    return False


def _document_correction(part, sources):
    for match in _ATTRIBUTED.finditer(part):
        opening = _UNOPENED.match(part, match.end())
        if not opening or not (match['intro'] or match['verb'] or match['pair']):
            continue
        stop = _closed(part, match)
        if stop is None or _in_quote(part, match.start()) or _QUALIFIED.search(part[:opening.start()]):
            continue
        if _OTHER_TIME.search(part[:opening.start()]):
            continue
        originals = sources()
        named = {(ref, original, room) for _, original, ref, room in originals
                 if _own_record(original, match['name'], match['id'])}
        if len(named) != 1:
            continue
        _, body, room = next(iter(named))
        if not _weak_statement(body) or _OTHER_TIME.search(body):
            continue
        opened = _AFFIRM_OPEN.search(body)
        if _ANY_OPEN.search(body) and (not opened or _UNOPENED.search(body) or _QUALIFIED.search(body)):
            continue
        label = match['name'] + (' ' + match['id'] + ' ' if match['id'] else '')
        # A rehearsal/example or another speaker can use first-person words
        # about opening. Never add a positive business assertion from those words.
        fix = '关于' + label + '，单凭其中提到的未检查内装，无法判断当时是否打开过包装'
        if match['pair']:
            document = _DOC.search(body)
            code = re.search(_ID, document.group()) if document else None
            # Preserve a separately attributed, explicit last-field entry only
            # when the annex itself names this exact parent document. Being the
            # sole other inspection receipt, or sharing a day, is insufficient.
            attachments = {original for _, original, _, origin in originals
                           if code and origin == room and re.match(re.escape(match['name']) + r'\s*' + re.escape(code.group())
                                        + r'\s*(?:另附|所附|的附件为)' + _INSPECTION, original)}
            note = '检视单是否有开盒记载，须按它自己的原话单独说明'
            if len(attachments) == 1:
                annex = next(iter(attachments))
                field = re.search(r'(?:末栏|末行)写[道着]?[：:]?[^。！？；\n]{0,45}[，,：:]\s*(未开[盒箱])[。；;\s]*$', annex)
                if field and not _QUOTED_CONTEXT.search(annex):
                    note = '该单明确附列的检视记录另记有“' + field[1] + '”'
            fix += '；' + note
        return part[:match.start()] + fix + part[stop:]
    return None


def corrections(parts, visible_sources):
    from .inspection_time import corrections as time_corrections
    originals = cache(visible_sources)
    changes = time_corrections(parts, originals, _in_quote)
    if not any(_UNCHECKED.search(part) or (_DOC.search(part) and _UNOPENED.search(part)) for part in parts):
        return changes
    prior = ''
    fenced = False
    last_direct = -2
    for i, part in enumerate(parts):
        if '```' in part:
            if part.count('```') % 2:
                fenced = not fenced
            prior = ''
            continue
        if fenced or not part.strip() or part.lstrip().startswith('>') or part.rstrip().endswith(('?', '？')):
            prior = ''
            continue
        if _DOC.search(part) and _UNOPENED.search(part):
            direct = _document_correction(part, originals)
            if direct:
                changes[i] = direct
                last_direct = i
                prior = part
                continue
        for pattern in (_INFER, _SHARED):
            for match in pattern.finditer(part):
                stop = _closed(part, match)
                if stop is None or _in_quote(part, match.start()):
                    continue
                prefix = part[:match.start()]
                premise = _antecedent(prefix, prior)
                linked_correction = pattern is _SHARED and last_direct == i - 1 and not prefix.strip()
                if not linked_correction and (not _UNCHECKED.search(premise) or _UNOPENED.search(premise)):
                    continue
                if _QUALIFIED.search(premise) or _QUALIFIED.search(prefix):
                    continue
                if pattern is _SHARED and not linked_correction and (not _DOC.search(premise) or _corroborated(premise, originals)):
                    continue
                # Preserve the other source, numbered fields, provenance and
                # follow-up uncertainty in this same sentence.
                text = (prefix + BOUNDARY + part[stop:])
                changes[i] = text
                break
            if i in changes:
                break
        prior = part
    return changes
