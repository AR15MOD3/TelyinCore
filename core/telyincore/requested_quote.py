"""Check an explicitly requested whole archive field against its currently displayed original."""
from datetime import date, datetime
from dataclasses import dataclass
import re

from .quote_typography import FOLD, PAIRS, RECORD, SOURCE_LABEL, restore

FULL_CLOCK_NOTE = '发送、送达及业务时间须另有依据'
AUTOMATIC_DAY_NOTE = '本次按日显示，完整写入时刻可用 record_ref 查看'

def _archive_clock(receipt, ref):
    """Read only the precision stated in this original's host header, before any user text."""
    lines=receipt.split('\n',5)
    header=f'[{ref}] user · message · '
    # A listing starts with a filter description. Its rows, search terms and
    # user text cannot supply a direct-open header, wherever they contain a label.
    alias = r'本批引用 evidence_ref="evidence:[0-9a-f]{8}:[1-9][0-9]*"（仅为原文定位别名，仍需当前权限和来源校验）'
    index = int(bool(re.fullmatch(alias, lines[0])))
    if index < len(lines) and lines[index] == '本批次已返回此条完整原文；本次为重复读取。':
        index += 1
    if index+1>=len(lines) or lines[index]!=header:
        return None
    day = re.fullmatch(r'证据类型：[^\n]*? · 归档写入日期：(\d{4}-\d\d-\d\d)（(?:'
                       + re.escape(AUTOMATIC_DAY_NOTE) + '|' + re.escape(FULL_CLOCK_NOTE) + ')）',
                       lines[index + 1])
    if day:
        try:
            return date.fromisoformat(day[1])
        except ValueError:
            return None
    # Previously archived exact reads used the older label for the same clock.
    stamp = re.fullmatch(r'证据类型：[^\n]*? · 归档写入时间：([^\s（]{10,40})（发送、送达及业务时间须另有依据）',
                         lines[index + 1])
    if stamp is None:
        stamp = re.fullmatch(r'证据类型：[^\n]*? · 消息记录时间：([^\s，]{10,40})，不代表事实发生时间',
                             lines[index + 1])
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp[1])
    except ValueError:
        return None


def archive_record_time(receipt, ref):
    """A calendar date never attests an instant at midnight."""
    stamp = _archive_clock(receipt, ref)
    return stamp if isinstance(stamp, datetime) else None


def mark_matches(receipt, ref, day, clock, zone):
    at = _archive_clock(receipt, ref)
    if at is None:
        return False
    if not isinstance(at, datetime):
        return at.isoformat() == day and clock is None and zone is None
    return (at.date().isoformat()==day and (clock is None or at.strftime('%H:%M')==clock)
            and (zone is None or at.strftime('%z')==zone.replace(':','')))


# The source supplies the document, field name, first character and entire quoted
# value. The response may vary the order of its metadata. Any other assertion in
# those two paragraphs makes this optional typography edit inapplicable.
CONTEXT = re.compile(r'示例|举例|假设|假如|如果|转述|台词|有人说|不是要|不要|不用|不必|别照录|别抄')
DATE_WANTED = re.compile(r'(?:记录|归档)(?:的)?日期')
FIELD = re.compile(r'(?m)^([\u3400-\u9fff]{1,12})[：:]([^\r\n]{6,220})$')
ANCHOR = r'从(?P<open>[“「‘『])(?P<first>[\u3400-\u9fff])(?P<close>[”」’』])(?:字)?起'
MARK = re.compile(r'(?P<day>\d{4}-\d{2}-\d{2})|(?P<zone>[+-]\d{2}:\d{2})|(?P<clock>\d{2}:\d{2})')
TITLES = re.compile(r'([「“『‘《])([^「」“”『』‘’《》\r\n]{1,40})([」”』’》])')
META_WORDS = re.compile(r'记录|归档|登记|档案|单号|编号|日期|时间|时区|'
                        r'查到|找到|翻到|标注|标记|显示|注明|我|这儿|这里|这|份|条|的|在|是|落|为|于|了|[ \t。.,，：:（）()、；;]+')
COPY_WORDS = re.compile(r'的|整句|照录|逐字|抄录|引用|原文|原话|如下|是|为|[ \t：:]+')


def heading_title(original, label):
    heading = re.split(r'[。；;！？\n：:]', original, maxsplit=1)[0].strip()
    if heading == label:
        return label.strip('《》')
    match = re.fullmatch(r'([\u3400-\u9fff]{1,20})[ \t]+' + re.escape(label), heading)
    if match and not re.search(r'参照|参见|引用|另见|对应|转述|摘录|提及|示例', match[1]):
        return match[1]
    return None


def requested_value(ask, label, title, original, *, require_inner_quotes=True):
    fields = FIELD.findall(original)
    candidates = []
    for field, value in fields:
        prefix = r'(?:把|请)(?P<owner>[^，,。！？；;\n]{0,48}?)' + re.escape(field)
        request = re.search(prefix + ANCHOR + r'(?:的)?整句(?:照录|逐字抄录|逐字引用)', ask)
        if not request or PAIRS[request['open']] != request['close']:
            continue
        owner = request['owner'].replace(label, '').strip().removeprefix('把').strip().removesuffix('的').strip()
        # A shortened title can be a suffix of the actual document heading.
        if (owner and not title.endswith(owner)) or not value.startswith(request['first']):
            continue
        if (re.fullmatch(r'[^。！？!?]+[。！？!?]', value)
                and (not require_inner_quotes or any(mark in value for mark in PAIRS))
                and sum(other == field for other, _ in fields) == 1):
            candidates.append((field, value))
    return candidates[0] if len(candidates) == 1 else None


def archive_stamp(header, label, title):
    if len(header) > 180 or header.count(label) != 1:
        return None
    remaining = header.replace(label, ' ', 1)
    names = list(TITLES.finditer(remaining))
    if len(names) > 1:
        return None
    if names:
        name, = names
        if {**PAIRS, '《': '》'}[name[1]] != name[3] or name[2] != title:
            return None
        remaining = remaining[:name.start()] + ' ' + remaining[name.end():]
    if not re.search(r'记录|登记|归档', remaining):
        return None
    stamps = {}
    for mark in MARK.finditer(remaining):
        kind = mark.lastgroup
        role = re.search(r'(日期|时间|时区)[ \t:：为是]*$', remaining[:mark.start()])
        if (kind in stamps or (role and role[1] == '日期' and kind != 'day')
                or (role and role[1] == '时区' and kind != 'zone')
                or (role and role[1] == '时间' and kind == 'zone')):
            return None
        stamps[kind] = mark[0]
    if 'day' not in stamps or ('zone' in stamps and 'clock' not in stamps):
        return None
    # Unknown words (including places, other titles, modality and negation)
    # disqualify this edit. No free-text claim enters the source match.
    if META_WORDS.sub('', MARK.sub(' ', remaining)):
        return None
    return stamps['day'], stamps.get('clock'), stamps.get('zone')


def quoted_field(paragraph, field, value):
    if not paragraph.startswith(field):
        return None
    folded = value.translate(FOLD)
    offset = paragraph.translate(FOLD).find(folded)
    end = offset + len(value)
    if offset <= len(field) or end >= len(paragraph):
        return None
    actual = paragraph[offset:end]
    if (actual == value or PAIRS.get(paragraph[offset-1]) != paragraph[end]
            or paragraph[end+1:] not in ('', '。', '.')):
        return None
    declaration = paragraph[len(field):offset-1]
    anchor = re.match(ANCHOR, declaration)
    if anchor is None or PAIRS[anchor['open']] != anchor['close'] or anchor['first'] != value[0]:
        return None
    tail = declaration[anchor.end():]
    if '整句' not in tail or not tail.rstrip().endswith(('：', ':')) or COPY_WORDS.sub('', tail):
        return None
    return actual


def quote_before_stamp(first, second, label, title, field, value):
    """Match an explicitly attributed field followed by the date of that same record."""
    intro, separator, rest = first.partition(field + '从')
    if not separator:
        return None, None
    document = re.fullmatch(
        r'(?:(?:查到|翻到|找到)了[。，,][ \t]*)?' + re.escape(label)
        + r'[ \t]*([\u3400-\u9fff]{0,20})', intro)
    if not document:
        return None, None
    name = document[1].removesuffix('的')
    if name and not title.endswith(name):
        return None, None
    # A demonstrative may refer to the preceding single document. Accept just
    # the archive timestamp and an optional clarification about what it dates.
    date_line = re.fullmatch(
        r'(?:这|那)(?:一)?条((?:登记|归档|记录).{1,100}?)'
        r'(?:[（(]记录时间[，,][ \t]*(?:不是|并非)(?:单据上的|事情的|事实的)?'
        r'(?:落款|发生)时间[）)])?[。.]?', second)
    if not date_line or SOURCE_LABEL.search(second):
        return None, None
    return quoted_field(separator + rest, field, value), archive_stamp(label + ' ' + date_line[1], label, title)


def requested_field(text, current, visible, complete):
    if '整句' not in text or '从' not in text or len(text) > 16000 or '```' in text:
        return None
    speakers = [s['text'] for s in current if s.get('current_speech')]
    if len(speakers) != 1:
        return None
    ask, = speakers
    labels = SOURCE_LABEL.findall(ask)
    if (len(labels) != 1 or CONTEXT.search(ask) or not DATE_WANTED.search(ask)
            or len(re.findall(ANCHOR, ask)) != 1):
        return None
    blocks = re.split(r'\r?\n[ \t]*\r?\n', text.strip(), maxsplit=2)
    if len(blocks) < 2 or '\n' in blocks[0] or '\n' in blocks[1]:
        return None
    label, = labels
    if label not in blocks[0]:
        return None
    visible_rows = list(visible())
    sources = {(ref, origin) for _, src, ref, _, origin in visible_rows
               if isinstance(ref, str) and RECORD.fullmatch(ref) and heading_title(src, label) is not None}
    if len(sources) != 1:
        return None
    ref, scope = sources.pop()
    # One signed record can expose both its original and the reader's local
    # calendar day. Identity comes from the reference, scope and bytes. The
    # actual archive header still has to confirm the specific day quoted below.
    full = {original for _, original, r, _, origin in complete(None) if (r, origin) == (ref, scope)}
    if len(full) != 1:
        return None
    original = full.pop()
    title = heading_title(original, label)
    selected = requested_value(ask, label, title, original) if title else None
    if not selected:
        return None
    quoted = quoted_field(blocks[1].strip(), *selected)
    stamp = archive_stamp(blocks[0].strip(), label, title)
    if quoted is None and stamp is None:
        quoted, stamp = quote_before_stamp(blocks[0].strip(), blocks[1].strip(), label, title, *selected)
    if quoted is None or stamp is None:
        return None
    return restore(text, (stamp[0], quoted, ref), lambda: visible_rows, lambda: complete(stamp))


@dataclass(frozen=True)
class LiteralFieldMismatch:
    canonical: str
    direct: str
    feedback: str
    scope: str
    auto: bool
    conflict: bool


DISCUSSION = re.compile(r'假设|假如|如果|示例|举例|排版|标点|引号|讨论写法|转述|概括|大意|大概|是否|能否|可否|不是要|不要|不用')
ABSOLUTE = re.compile(r'(?P<day>\d{4}-\d{2}-\d{2})|(?P<zone>[+-]\d{2}:\d{2})|(?P<clock>\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)')
ASKED_FIELD = re.compile(r'(?:把|请)[^，,。！？?；;\n]{1,60}' + ANCHOR + r'(?:的)?整句(?:照录|逐字抄录|逐字引用)')
REPORTED = re.compile(r'转引|声称|有人问|[他她]说|[他她]写|[他她]在.{0,20}(?:说|写)|按[他她]的|笔记里有人|另一份|另有一份|另一个人的')


def field_presentation(text, at, end, label, title, field):
    """A named field or an isolated value answering the sole current copying request."""
    from .inspection_inference import _in_quote
    wrapped = (at > 0 and end < len(text)
               and {**PAIRS, '"':'"'}.get(text[at-1]) == text[end])
    start = at-1 if wrapped else at
    after = end+1 if wrapped else end
    head = text[:start].rstrip()
    local = re.split(r'[。；;！？!?\n\r]', head)[-1]
    preceding = text[:start].rsplit('\n',1)[-1].strip()
    following = text[after:].split('\n',1)[0].strip(' \t\r。.')
    if (_in_quote(text, start) or DISCUSSION.search(head) or REPORTED.search(head)
            or any(ch in head for ch in '?？') or re.match(r'[^。!?！？；;\n]{0,100}[?？]', text[after:])):
        return None
    if field in local:
        subject, _, designation = local.partition(field)
        subject = subject.strip(' \t：:，,').removesuffix('的')
        if label not in local and re.search(r'单|表|报告|通知|档案', subject) and not title.endswith(subject):
            return None
        if re.search(r'没|不|并非|别|另|反例|[他她]|别人', designation):
            return None
        if not wrapped and (following or (preceding and not preceding.endswith(('：',':','｜','|')))):
            return None
    else:
        # The field may be omitted entirely. The quotation can stand alone,
        # follow the identified document heading, or be set off by a dash.
        heading = (not head or (label in local and
                   (title in local or local.strip().startswith(label))) or
                   (not local.strip() and label in head))
        standalone = not preceding and not following and heading
        labelled_quote = wrapped and local.endswith(('—','–')) and label in head
        if not (standalone or labelled_quote):
            return None
    return head, local, text[after:]


def needs_metadata_review(before, label, title, field):
    """A complete literal cannot grant authority to its separate metadata header."""
    lines = before.lstrip().split('\n')
    if len(lines) == 1:
        return False
    if any(ABSOLUTE.search(line) for line in lines[1:-1]):
        return True
    header = lines[0].strip()
    declaration = '\n'.join(lines[1:]).strip()
    if re.search(re.escape(field) + r'\s*从', declaration) and (label in header or ABSOLUTE.search(header)):
        # The existing paired header/anchored-field protocol owns this layout.
        return True
    if not ABSOLUTE.search(header):
        return False
    declaration = declaration.rstrip('「『“‘"').strip()
    if declaration.rstrip('：:｜|').strip() not in ('', field):
        return True
    # A bare value can follow a verified heading, including the exact unquoted
    # document title before its identifier or a reference to the named original.
    named = re.match(r'^(?:[这那](?:一)?[份条张])?' + re.escape(title) + r'(?=$|[\s（(的，,。])', header)
    if named:
        header = header[named.end():].strip()
        if label not in header:
            header = label + ' ' + header
    return archive_stamp(header, label, title) is None


def date_evidence_conflict(outside, stamp):
    """Verify numeric date, clock and zone independently of how the answer is laid out."""
    for clause in re.split(r'[。；;，,！？!?\n]', outside):
        equality = re.search(r'(?:发生|现场|签收)时间(?:就|也)?(?:是|为)(?:这|该|记录)|'
                             r'(?:就是|即是|即为|等同于).{0,12}(?:发生|落款)时间', clause)
        if equality and not re.search(r'不|未|无法|假如|如果', clause[:equality.start()]):
            return '请区分消息记录时间和事情发生的时间；目前能确定的是记录时间。'
    tokens = list(ABSOLUTE.finditer(outside))
    if not tokens:
        return ''
    if stamp is None:
        return '本轮没有能核实该时点的原件时间头。'
    if isinstance(stamp, datetime):
        zone = stamp.strftime('%z')
        expected = dict(day=stamp.date().isoformat(), zone=zone[:3]+':'+zone[3:], clock=stamp.time().isoformat(timespec='microseconds'))
    else:
        if any(m.lastgroup != 'day' for m in tokens):
            return '已读原件只标了入档日期。要引用具体时刻或时区，可用 record_ref 查看。'
        expected = dict(day=stamp.isoformat())
    if any(expected[m.lastgroup][:len(m[0])] != m[0] for m in tokens):
        return '草稿给出的日期、时刻或时区与对应消息的记录时间不一致。'
    # A real archive timestamp cannot establish when a physical event occurred.
    for m in tokens:
        if m.lastgroup == 'day':
            clause = re.split(r'[。!！?？；;，,\n]', outside[:m.start()])[-1]
            if (re.search(r'不(?:是|在|为)|并非|发生|现场|落款|或许|可能|未核实|另一[份条张]', clause)
                    or not re.search(r'记录|归档|存档|登记', clause)):
                return '请把这个日期归给对应消息的记录时间；事情发生在哪一天需要另有依据。'
    return ''


def _declares_anchored_copy(before, label, title, field, value):
    declaration = before.split('\n')[-1].strip()
    declaration = re.sub(r'^(?:查|翻|找)到了[。！!，,][ \t]*', '', declaration, count=1)
    subject, separator, direction = declaration.partition(field)
    if not separator or subject.count(label) != 1:
        return False
    name = subject.replace(label, '', 1).strip().removesuffix('的').strip()
    if name and not title.endswith(name):
        return False
    selection = re.fullmatch(r'(?:那一行|这一行)?[ \t，,]*' + ANCHOR
                             + r'(?:的)?(?:整句)?(?:照录|逐字抄录|逐字引用)(?:如下)?[ \t]*[：:]', direction)
    if not selection or PAIRS[selection['open']] != selection['close'] or selection['first'] != value[0]:
        return False
    return not (DISCUSSION.search(before) or REPORTED.search(before)
                or re.search(r'举个例|样例|引述|写道|说过|这么说|这样说|原稿|草稿|编者按|示范', before))


def _extra_field_label(text, at, end, label, title, field, value):
    """Return the start of a field label copied inside a separately declared literal."""
    from .inspection_inference import _in_quote
    line_start = text.rfind('\n', 0, at) + 1
    prefix = text[line_start:at]
    match = re.fullmatch(r'[ \t]*([「『“‘"]?)(' + re.escape(field) + r'[：:][ \t]*)', prefix)
    if not match:
        return None
    wrapper = match[1]
    closing = {**PAIRS, '"': '"'}.get(wrapper, '')
    if text[end:].partition('\n')[0].strip() != closing:
        return None
    if _in_quote(text, line_start) or not _declares_anchored_copy(text[:line_start].rstrip(), label, title, field, value):
        return None
    return line_start + match.start(2)


def _missing_inner_pair(text, label, title, field, value, body, materials, key):
    from .inspection_inference import _in_quote
    from .quote_typography import CLOSE
    from .record_follow_title import _NONCURRENT_FIELD
    if _NONCURRENT_FIELD.search(body):
        return None
    marks = [i for i, ch in enumerate(value) if ch in PAIRS or ch in CLOSE]
    if len(marks) != 2:
        return None
    left, right = marks
    if PAIRS.get(value[left]) != value[right] or not 0 < left < right - 1 < len(value) - 1:
        return None
    plain = value[:left] + value[left+1:right] + value[right+1:]
    if text.count(plain) != 1:
        return None
    without = str.maketrans('', '', ''.join(PAIRS) + ''.join(CLOSE))
    if (body.translate(without).count(plain) != 1 or
            any(plain in src.translate(without) for source, item in materials.items()
                if source != key for src in item.excerpts)):
        return None
    at = text.index(plain)
    end = at + len(plain)
    line_start = text.rfind('\n', 0, at) + 1
    before = text[:line_start].rstrip()
    preamble = before.rpartition('\n')[0].strip()
    if (preamble and not re.fullmatch(r'(?:查|翻|找)到了[。！!]?', preamble)
            or not _declares_anchored_copy(before, label, title, field, value)
            or _in_quote(text, line_start)):
        return None
    line = text[line_start:at].strip()
    rest = text[end:].partition('\n')[0].strip()
    if line not in PAIRS or not rest.startswith(PAIRS[line]):
        return None
    tail = rest[1:].strip()
    inner = re.escape(value[left+1:right])
    if tail and not re.fullmatch(r'[（(]原(?:单|文|件)(?:里|中)“' + inner + r'”是带引号的[）)]', tail):
        return None
    if _reassigned_quote(text[end:].partition('\n')[2], label):
        return None
    fixed = text[:at] + value + text[end:]
    feedback = f'照录的整句漏了原件中的一对内引号 {value[left]} {value[right]}。可直接沿用已读原句，保留这两个字符。'
    return LiteralFieldMismatch(fixed, fixed, feedback, key[0], False, True)


def _reassigned_quote(after, label):
    return (any(ref != label for ref in SOURCE_LABEL.findall(after)) or
            re.search(r'转述|转引|意译|改写|简称|摘录|引号|标点|排版|假设|比如|示范|讨论|另.{0,5}出处|'
                      r'出自|引自|来自另一|[上前这]一?句.{0,12}(?:错|误|原话|[说写]的)', after))


def _dated_copy_block(text, label, title, field, value, at, rows, ref, scope):
    """Only the single promised line of a uniquely named, dated field is eligible."""
    from .quote_typography import balanced
    start = text.rfind('\n', 0, at) + 1
    end = at + len(value)
    if (text[start:at] != '> ' or text[end:].partition('\n')[0].strip() or
            len(re.findall(r'(?m)^[ \t]*>', text)) != 1 or
            _reassigned_quote(text[end:], label)):
        return None
    blocks = re.split(r'\r?\n[ \t]*\r?\n', text[:start].strip())
    if len(blocks) != 2 or '\n' in blocks[0] or '\n' in blocks[1]:
        return None
    header, declaration = blocks
    if not _declares_anchored_copy(label + ' ' + declaration, label, title, field, value):
        return None
    stamp = re.fullmatch(
        r'(?:(?:查|翻|找)到了[。，,][ \t]*)?' + re.escape(label) +
        r'[ \t]*(?:那|这)(?:一)?条归档(?:记录)?的日期(?:是|为)[ \t]*(?P<em>\*\*|)'
        r'(?P<day>\d{4}-\d{2}-\d{2})(?P=em)'
        r'(?:[（(]写入时间(?:为|是)?[ \t]*(?P<clock>\d{2}:\d{2}(?::\d{2})?)'
        r'[，,][ \t]*(?P<zone>[+-]\d{2}:\d{2})[）)])?[。.]?', header)
    if not stamp:
        return None
    clocks = set()
    for (_, _, target, _, origin), row in rows:
        if (target, origin) == (ref, scope):
            clocks.update(display for record, display in row.text.record_times if record == ref)
    if not clocks or any(not isinstance(c, str) or (c != stamp['day'] and not c.startswith(stamp['day']+'T')) for c in clocks):
        return None
    if stamp['clock']:
        exact = {c for c in clocks if len(c)>10}
        if len(exact)!=1:
            return None
        try:
            when = datetime.fromisoformat(exact.pop())
        except ValueError:
            return None
        if when.strftime('%z') != stamp['zone'].replace(':','') or not when.strftime('%H:%M:%S').startswith(stamp['clock']):
            return None
    actual = text[at:end]
    if actual == value or not balanced(actual) or not balanced(value):
        return None
    fixed = text[:at] + value + text[end:]
    return LiteralFieldMismatch(fixed, fixed, '照录的内引号与已读原件不同，其他字句可沿用。', scope, True, False)


def literal_field_mismatch(text, current, read):
    """Find one verified whole-field spelling discrepancy independent of date prose and line layout."""
    from .archive_wording import _materials
    from .quote_typography import balanced
    if len(text) > 16000 or '`' in text:
        return None
    speakers = [s['text'] for s in current if s.get('current_speech')]
    if len(speakers) != 1:
        return None
    ask, = speakers
    labels = SOURCE_LABEL.findall(ask)
    if (len(labels) != 1 or not ASKED_FIELD.search(ask)
            or CONTEXT.search(ask) or DISCUSSION.search(ask)
            or len(re.findall(ANCHOR, ask)) != 1):
        return None
    label, = labels
    rows = tuple(read())
    materials = _materials(lambda: rows)
    targets = [(key, item) for key, item in materials.items()
               if any(heading_title(excerpt, label) for excerpt in item.excerpts)]
    if len(targets) != 1:
        return None
    (scope, ref), material = targets[0]
    if len(material.bodies) != 1 or material.recipients != {scope}:
        return None
    body, = material.bodies
    title = heading_title(body, label)
    selected = requested_value(ask, label, title, body, require_inner_quotes=False) if title else None
    if not selected:
        return None
    field, value = selected
    needle = value.translate(FOLD)
    folded = text.translate(FOLD)
    at = folded.find(needle)
    end = at + len(value)
    if at < 0:
        if re.search(r'(?m)^\s*>', text):
            return None
        return _missing_inner_pair(text, label, title, field, value, body, materials, (scope, ref))
    if (folded.count(needle) != 1 or body.translate(FOLD).count(needle) != 1
            or any(needle in s.translate(FOLD) for key, item in materials.items()
                   if key != (scope, ref) for s in item.excerpts)):
        return None
    if re.search(r'(?m)^\s*>', text):
        from .record_follow_title import _NONCURRENT_FIELD
        if _NONCURRENT_FIELD.search(body):
            return None
        return _dated_copy_block(text, label, title, field, value, at, rows, ref, scope)
    quoted = text[at:end]
    if not balanced(quoted) or not balanced(value):
        return None
    start = _extra_field_label(text, at, end, label, title, field, value)
    boundary = f'所问引文从“{value[0]}”字起，字段名和冒号在引文之外。'
    if start is not None:
        canonical = text[:start] + value + text[end:]
        # The QQ call remains the model's pending draft. Its original arguments
        # cannot gain a send capability after a length-changing correction.
        return LiteralFieldMismatch(canonical, canonical, boundary + '可沿用已读原句。', scope, False, True)
    if quoted == value:
        return None
    presentation = field_presentation(text, at, end, label, title, field)
    if presentation is None:
        return None
    head, local, remainder = presentation
    if any(s in text for s in ('纯属示例', '只是示例', '只是举例')):
        return None
    differences = dict.fromkeys((old, new) for old, new in zip(quoted, value) if old != new)
    changes = '、'.join(old + ' → ' + new for old, new in differences)
    feedback = '照录的整句文字一致，内引号与已读原件有差异（' + changes + '）。' + boundary
    canonical = text[:at] + value + text[end:]
    # A positive literal match licenses only those punctuation characters.
    # For automatic submission also require a local document attribution and
    # validate every separate date statement by the existing archive header check.
    between = head.rpartition(label)[2].partition(field)[0].strip(' \t\r\n：:，,。') if label in local and field in local else ''
    named = re.sub(r'^[这那该](?:一)?[份张条]', '', between).strip().removesuffix('的')
    anchors = re.findall(ANCHOR, local)
    auto = title.endswith(named) and all(PAIRS.get(left) == right and first == value[0] for left, first, right in anchors)
    outside = head + '\n' + remainder
    if ABSOLUTE.search(outside) and not re.search('时间|日期|时点|时刻', ask):
        auto = False
    if label not in head and title not in head and ABSOLUTE.search(head):
        auto = False
    stamps = [_archive_clock(row.text, ref) for (_, src, material_ref, _, origin), row in rows
              if (material_ref, origin, src) == (ref, scope, body)]
    marks = {s.isoformat(): s for s in stamps if s is not None and (not isinstance(s, datetime) or s.utcoffset() is not None)}
    for s in stamps:
        if isinstance(s, datetime) and s.utcoffset() is not None:
            marks.pop(s.date().isoformat(), None)
    stamp = next(iter(marks.values())) if len(marks) == 1 else None
    wrong_time = date_evidence_conflict(outside, stamp)
    named_labels = set(SOURCE_LABEL.findall(outside))
    wrong_source = bool(named_labels - {label}) or (label in local and bool(named) and not title.endswith(named))
    review_metadata = needs_metadata_review(text[:at], label, title, field)
    direct = canonical
    if wrong_source or wrong_time:
        direct += '\n\n〔Core 引文核对〕上述整句出自' + label + '的' + field + '。'
        if wrong_time and stamp:
            direct += '这条消息的记录时间为' + stamp.isoformat() + '，其他时点不能据此归给这条记录。'
        feedback += (('请核对整句所归属的档号。' if wrong_source else '') + wrong_time + '其余已核实的字段可保留。')
    elif review_metadata:
        # The caller may still approve the existing exact header/field pair.
        # Otherwise preserve independent facts and ask for the attribution to be checked.
        direct += '\n\n〔Core 引文核对〕已核对这句引文。前段的归档说明与这句引文的对应关系仍待核对。'
        feedback += '请核对前段的文书名、记录时间和字段归属。'
    conflict = bool(wrong_source or wrong_time)
    return LiteralFieldMismatch(canonical, direct, feedback, scope, auto and not (conflict or review_metadata), conflict)
