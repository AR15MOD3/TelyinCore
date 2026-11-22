"""Keep visible user accounts of documents separate from primary business evidence."""
import re
from collections.abc import Mapping


REPORT_LABEL = '〔用户转述外部文书。可核实的是这条留言的原话。原表是否存在、表上是否这样写、业务是否发生，都需要独立来源。〕'
REPORT_SCOPE = '可确认留言写了什么。原表是否存在、业务是否实际发生，需分别核实。'
REPORT_CORRECTION = '〔Core 来源核对〕目前能确认的是用户留言中的说法。所述业务记录及实际情况仍待独立核实。'
ID_CORRECTION = '〔Core 编号核对〕旧待办文字没有可比对的提醒编号。两个编号是否相同仍未知。'

_DOCUMENT = re.compile(r'(?:我(?:来|只是|再)?转述(?:一下)?[：:]|据(?:他|她|对方|当事人)(?:所述|转述)|(?:登记(?:簿|表)?|复核表|借阅表|对[账帐]单|流水|台[账帐]|订单|收据|报告|签收单|交接单|清单|复核单|记录表)'
                       r'(?:里|中|上(?:面)?)?(?:写着|写的(?:却|也)?是|写道|写明|记着|记载|注明|标为|记为|显示))')
_OPERATIONS = (
    re.compile(r'借出|借阅|借的|借走|出借|借还|在借'),
    re.compile(r'转账|打款|汇款|付款|支付|扣款|到账'),
    re.compile(r'交付|交货|交接|运输|出货|发货|签收|收货|退货'),
    re.compile(r'退款|退费'),
)
_STRONG = re.compile(r'确实(?:有|存在|发生)|确有|已经(?:证实|确认)|可以确认|(?:已经|已|独立|亲自)(?:核实|证实|查证|核验|查到)|'
                     r'(?:本身|确实|已经|的确)(?:成立|发生|存在|属实)|成立|属实|没有争议|毫无疑问')
_SCOPE = re.compile(r'(?:你|您|用户)(?:曾(?:经)?|先前|当时)?(?:说|提到|称|转述|描述|写|的留言|的消息)|留言(?:里|中|写|称|提到|的说法|都(?:说|提到))|'
                    r'(?:据|按照?|依照)(?:你的|您的|这段|两条)(?:转述|说法|留言)|不能|无法|未能|不足以|'
                    r'尚未|无法确认|不确定|看不出|没法|不代表|不意味着|未必|不清楚|有待|未核实|待核|是不是|是否|如果|假设|举例|例如')
_ID_COMPARISON = re.compile(r'(?:编号|\bID\b)(?:\s*[也并就]?\s*)(?:互不相同|不一样|不相同|相同|一样|不同|一致)'
                            r'|(?:不是|并非|并不是|是|属于)(?:同一(?:个|条|组)?|两个不同的)(?:提醒)?(?:编号|ID)', re.I)
_LEGACY = re.compile(r'旧(?:待办|提醒|闹钟|那条|的)|以前那条|上次(?:的|那条)?闹钟')
_BOTH = re.compile(r'它和|它跟|两(?:者|条|个)|二者|和(?:现在|当前|新|这条)|跟(?:现在|当前|新|这条)')


class DocumentReport(str):
    """Created for an ordinary user text record, after session visibility filtering."""


def mentions_document(text):
    return isinstance(text, str) and bool(_DOCUMENT.search(text, 0, 2048))


def project(said, record):
    if (record.role == 'user' and record.kind == 'message' and not record.is_head
            and not record.is_flow and not record.images and mentions_document(record.text)):
        return DocumentReport(REPORT_LABEL + '\n' + said)
    return said


def observed_legacy_pair(value):
    if not isinstance(value, dict):
        return None
    notes = value.get('legacy_alarm_notes')
    scheduled, pending = value.get('scheduled'), value.get('pending')
    if not (isinstance(notes, list) and 0 < len(notes) <= 100 and all(isinstance(note, str) for note in notes)
            and isinstance(scheduled, list) and isinstance(pending, list)):
        return None
    if len(scheduled) + len(pending) != 1:
        return None
    row = (scheduled or pending)[0]
    if not isinstance(row, dict):
        return None
    topic, ident = row.get('what'), row.get('id')
    # Some older or externally supplied notes do carry a printed reminder ID.
    if any(_PRINTED_ID.search(note) for note in notes):
        return None
    if isinstance(topic, str) and topic and isinstance(ident, str) and ident and all(topic in note for note in notes):
        return topic, ident
    return None


def _visible_texts(messages):
    names = {}
    for row in messages:
        if not isinstance(row, Mapping):
            continue
        if row.get('type') == 'function_call':
            names[row.get('call_id')] = row.get('name')
            continue
        role = row.get('role')
        if row.get('type') == 'function_call_output':
            role = names.get(row.get('call_id')) or 'tool'
            content = row.get('output')
        else:
            content = row.get('content')
        if isinstance(content, (tuple, list)):
            for item in content:
                if isinstance(item, Mapping) and item.get('type') in ('input_image', 'image_url'):
                    yield 'image', ''
                elif isinstance(item, Mapping) and isinstance(item.get('text'), str):
                    yield role, item['text']
        elif isinstance(content, str):
            yield role, content


_REPORT_NOUN = r'(?:留言|转述|说法|消息|矛盾|分歧|冲突|指代)'
_REPORT_SUBJECT = re.compile(_REPORT_NOUN + r'(?:本身|都|的确|确实|可以)?$')
_REPORT_OBJECT = re.compile(
    r'(?:(?:的)?(?:只是|仅是|仅有|只有|是|有))?(?:这|那)?(?:一|两|二|三|几|双方|两者)?(?:份|条|个)?'
    r'(?:用户的?)?' + _REPORT_NOUN +
    r'|(?:的)?(?:只是|是|仅是)?(?:两者|两份(?:转述|说法)|两段(?:转述|说法)|双方(?:说法)?)(?:都)?指(?:向|的(?:是)?)同一')

# Quotation normally attributes words to a source. An explicit conclusion
# endorsing the quoted proposition instead asserts it in the speaker's voice.
_ENDORSED = re.compile(
    r'(?P<lead>所以|因此|可见)[\s]*[「“『"](?P<claim>[^」”』"\n]{1,240})[」”』"]'
    r'(?P<verdict>(?:这件事|这一点|这个结论)?(?:没有分歧[，,]\s*可以落定|'
    r'可以(?:确认|落定)|已(?:被)?证实|确实成立|属实))')
_DOCUMENTS_PRESENT = re.compile(
    r'(?:这|那)?[两二三几](?:份|张|笔)(?:(?:原始|外部|纸面|业务)?(?:文书|单据|表格|文件|登记表|原件|凭据)|(?:原始|外部|纸面|业务)记录)'
    r'(?:本身|的原件)?\s*(?:都|均|的确|确实|真实|已经|已)*'
    r'(?:存在|在我手(?:中|里)|已(?:经)?核验过)'
    r'|(?:借出|借阅|付款|交付|发货|退款)(?:原始)?记录本身\s*[:：]\s*(?:可以|能|已经)(?:确认|核实)')


def _unqualified(part, pattern):
    # Test each clause separately. A later admission of uncertainty cannot
    # qualify a separate earlier claim. Leave quoted examples intact.
    if pattern is _STRONG:
        part = _ENDORSED.sub(lambda m: m['lead'] + m['claim'] + '成立', part)
    plain = re.sub(r'[“「『\"][^”」』\"\n]*[”」』\"]', ' ', part)
    for clause in re.split(r'[；;。！？!?\n]', plain):
        for match in pattern.finditer(clause):
            before = clause[:match.start()].rstrip(' *_')
            if _SCOPE.search(clause[:match.end()]):
                continue
            after = clause[match.end():].lstrip(' *_：:—-')
            if pattern is _STRONG and (_REPORT_SUBJECT.search(before) or _REPORT_OBJECT.match(after)):
                continue
            return True
    return False


_PRINTED_ID = re.compile(r'(?:提醒编号|编号|reminder_id|\bID\b)\s*(?:为|是|[:：])?\s*[`“「]?[a-zA-Z0-9][a-zA-Z0-9_-]{5,}', re.I)
_OLD_ID = re.compile(r'(?:旧|上次|以前|当时)[^，。；\n]{0,40}(?:编号|\bID\b)\s*(?:为|是|[:：])?\s*[`“「]?[a-zA-Z0-9][a-zA-Z0-9_-]{5,}', re.I)


def _additional_source(role, text, operation, reported, questions):
    if not operation.search(text):
        return False
    if role == 'user':
        clauses = re.split(r'(?<=[。！？!?\n])', text)
        return any(operation.search(line) and not line.rstrip().endswith(('?', '？')) for line in clauses)
    if role in ('read_events', 'memory_search'):
        for seen in (*reported, *questions):
            text = text.replace(seen, '')
        # The query label may itself contain the business verb; only content
        # in addition to the retrieved, already-attributed user texts matters.
        text = '\n'.join(line for line in text.splitlines() if not line.startswith('本次筛选：'))
    return bool(operation.search(text))


def corrections(parts, messages, legacy_pair, retrieved=()):
    # The request itself supplies the provenance tag. Text resembling a tag in
    # tool output, assistant history or a user message supplies no host proof.
    if not legacy_pair and not any(_STRONG.search(part) or _ENDORSED.search(part) or _DOCUMENTS_PRESENT.search(part)
                                   for part in parts):
        return {}
    reports, other = [], []
    images = False
    for role, content in _visible_texts(messages):
        if role == 'user' and isinstance(content, DocumentReport):
            reports.append(str(content).split('\n', 1)[-1])
        elif role == 'image':
            images = True
        elif role not in (None, 'system', 'assistant', 'timer_read', 'timer_history') and content:
            if role == 'read_events':
                reports.extend(raw for ref, raw in retrieved
                               if ref in content and raw in content and raw not in reports)
            other.append((role, content))

    domains = []
    if reports and not images:
        questions = [txt for role, txt in other if role == 'user' and len(txt) <= 2048 and ('？' in txt or '?' in txt)]
        for operation in _OPERATIONS:
            relevant = [r for r in reports if operation.search(r)]
            if not relevant:
                continue
            # A primary source or a first-person confirmation can change the
            # conclusion. Where extra evidence may apply, leave the judgment to
            # the model. Questions and retrieval of the very same original
            # messages are not additional independent sources.
            if any(_additional_source(role, text, operation, relevant, questions) for role, text in other):
                continue
            anchors = set()
            for report in relevant:
                for phrase in re.split(r'[，,。；;：:、"“”「」]|' + _DOCUMENT.pattern, report[:2048]):
                    if phrase:
                        anchors.update(re.findall(r'(?=([\u3400-\u9fffa-zA-Z0-9]{4}))', phrase))
            domains.append((operation, anchors))

    if legacy_pair:
        topic, current_id = legacy_pair
        # A later direct source can supply the old ID absent from timer_read.
        # Abstain if that is possible; the free-text note never certifies an ID.
        if images or any((topic in txt or current_id in txt) and _OLD_ID.search(txt) for _, txt in other):
            legacy_pair = None
    changes = {}
    # A document can be visibly attached or retrieved even if the receipt
    # doesn't repeat the business verb. Abstain when a primary source may exist.
    documents_only_reported = not images and not any(
        role not in ('user', 'read_events', 'memory_search') for role, _ in other)
    fenced = False
    last_legacy = -3
    for i, part in enumerate(parts):
        if '```' in part:
            if part.count('```') % 2:
                fenced = not fenced
            continue
        if fenced or part.lstrip().startswith('>') or part.rstrip().endswith(('?', '？')):
            continue
        if legacy_pair:
            if _LEGACY.search(part):
                last_legacy = i
            mentions_pair = (_LEGACY.search(part) or legacy_pair[0] in part or legacy_pair[1] in part
                             or re.search(r'它(?:和|跟)(?:现在|当前)(?:挂着|挂起)?的?那条|(?:两者|二者|这两条)(?:记录)?的?(?:编号|ID)', part))
            if (i - last_legacy <= 2 and mentions_pair and _BOTH.search(part)
                    and _unqualified(part, _ID_COMPARISON)):
                changes[i] = ID_CORRECTION + '\n'
        if not domains:
            continue
        document_claim = documents_only_reported and _unqualified(part, _DOCUMENTS_PRESENT)
        if not document_claim and not _unqualified(part, _STRONG):
            continue
        subject = part
        if document_claim and len(part.strip()) < 35:
            # A short heading can name the claimed document separately from
            # its subject; the immediately following sentence must identify it.
            subject += ''.join(parts[i+1:i+2])
        compact = re.sub(r'[^\w]', '', subject)
        for operation, anchors in domains:
            if not operation.search(subject):
                continue
            # A shared entity phrase (not just the verb or the kind of form)
            # helps keep business claims about another subject out of scope.
            if any(gram in compact for gram in anchors):
                changes[i] = REPORT_CORRECTION + '\n'
                break
    return changes
