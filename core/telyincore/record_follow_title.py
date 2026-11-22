"""Offer a search term from an explicitly cited document in a verified original."""
import json
import re
from contextlib import closing


_QUOTED = re.compile(r'「([^「」]{2,32})」|《([^《》]{2,32})》|“([^“”]{2,32})”')
_NAME = re.compile(r'[\w\u3400-\u9fff· -]{2,32}')
_DOCUMENT = re.compile(r'(?:单据|登记|记录|清册|清单|报告|档案|材料|文件|附件|附页|表格|台账|名册|文书|凭证|表|单|签|册|簿|票|函|笺)$')
_EXCLUDED = re.compile(r'不要|不再|不能|请勿|勿按|无需|禁止|作废|误写|错误|假设|假如|如果|例如|举例|演示|排练|台词')


def referenced_title(text):
    """One unambiguous search spelling only, never a statement of document identity."""
    titles = set()
    for clause in re.split(r'[。！？!?；;\n]', text):
        if _EXCLUDED.search(clause):
            continue
        for match in _QUOTED.finditer(clause):
            title = next(s for s in match.groups() if s is not None)
            if not _NAME.fullmatch(title) or not _DOCUMENT.search(title):
                continue
            before = clause[:match.start()].rstrip()
            after = clause[match.end():].lstrip()
            explicit = before.endswith(('另见','参见','参照','参阅','详见','另查','另附'))
            by_name = before.endswith('按') and re.match(r'(?:找|查|核对|交给|交至|检索|索取)', after)
            if explicit or by_name:
                titles.add(title)
    return next(iter(titles)) if len(titles) == 1 else ''


_ATTACHMENT_FIELD = re.compile(r'(?:附签|附表|附单|附件|随附材料|随件登记)[^。！？；;\n]{0,16}(?:见|列于)末栏[。.]$')
_FINAL_FIELD = re.compile(
    r'末栏[：:][ \t]*(?:按[「《“][^「」《》“”\n]{2,32}[」》”](?:交给|交至)[\w· -]{1,18}'
    r'|(?:另见|参见|详见)[「《“][^「」《》“”\n]{2,32}[」》”])'
    r'(?:，(?:本单|本表)(?:未记载|未记录|没记|未记|未注明)[\w· -]{1,18})?。')
_NONCURRENT_FIELD = re.compile(
    r'例句|示例|范例|样例|模板|演练|示范|模拟|虚构|草稿|旧稿|引文|引用|引述|转述|摘录|'
    r'待定|待核|待确认|尚未确定|未确定|不得|不应|不按|未按|未采纳|不采用|不执行|不适用|'
    r'停用|无效|取消|撤回|仅供|不是|并非|以下.*[说写]|原话|他说|她说|他说过|她说过')


def visible_tail_title(text):
    """Require the displayed end field and a preceding complete attachment instruction."""
    lines = text.rstrip().split('\n')
    # The first displayed line can start midway through a word or a negation.
    if (len(lines) < 3 or not _FINAL_FIELD.fullmatch(lines[-1]) or
            not any(_ATTACHMENT_FIELD.search(line) for line in lines[1:-1])):
        return ''
    return referenced_title(lines[-1])


_ANNEX_ASK = re.compile(
    r'(?:附[签表单件]|随附材料|随件登记)(?:上|里|中|的|又)?(?:写|记|说|是|有|怎么|怎样|哪|什么|谁)'
    r'|(?:查查|查一下|核对|看看|查看|查阅)(?:一下)?(?:那|这|另)?(?:一)?[条张份个]?(?:附[签表单件]|随附材料|随件登记)')


def _verified_tail(body, tail, title, console, room):
    from .inspection_inference import _in_quote
    from .memory import _literal_number
    from .requested_quote import heading_title
    from .session_view import event_scope
    offset, shown = tail
    if (not offset or body[offset:] != shown or body.count(title) != 1 or
            _EXCLUDED.search(body) or _NONCURRENT_FIELD.search(body)):
        return False
    lines = body.rstrip().split('\n')
    first = lines[0]
    if (not re.fullmatch(r'[^。！？;；：:\n]+。?', first) or
            any(not re.fullmatch(r'第[0-9一二三四五六七八九十百]{1,4}[格栏项行][^：:。！？;；\n]+。', line)
                for line in lines[1:-1])):
        return False
    label = _literal_number(first)
    heading = heading_title(first, label) if label else ''
    if not heading or not _DOCUMENT.search(heading) or _in_quote(body, body.rstrip().rfind('\n') + 1):
        return False
    # A partial read contributes no ongoing topic. Offer its optional route
    # only for one same-room question naming the sheet and asking about an annex.
    speech = [event for event in console.original_batch()
              if event.type == 'message' and event.origin == 'external' and event_scope(event) == room]
    if len(speech) != 1:
        return False
    question = speech[0].text
    return (_literal_number(question) == label and not _EXCLUDED.search(question)
            and bool(_ANNEX_ASK.search(question)))


def human_original(row, scope):
    return (row.get('scope') == scope and row.get('role') == 'user'
            and (row.get('kind') or 'message') == 'message' and not row.get('record_ref')
            and not any(row.get(flag) for flag in ('is_head', 'is_flow', 'is_system', 'is_ephemeral')))


def _title_heading(title):
    forms = (title, '《'+title+'》', '「'+title+'」', '“'+title+'”')
    return re.compile(r'^\s*(?:' + '|'.join(re.escape(form) for form in forms) + r')(?=[｜|：:\n]|$)')


PAIR_ANNOTATION = re.compile(
    r'(?:^|(?<=[；;。\n]))[ \t]*(?:这|那|该)(?:条|份|张)?(?:附[件签表单页]|附录)'
    r'(?:与|和)[ \t]*(?P<identifier>[A-Za-z]{1,12}(?:-[A-Za-z0-9]{1,12}){1,3})[ \t]*'
    r'(?:是|为)(?:同名|同日|同刻|相邻|独立|且|的|、){0,7}(?:两条记录|两份材料)[，,][ \t]*'
    r'(?P<claim>(?:原记录|原件|原文|原单)(?:里|中|上)?标注为[ \t]*'
    r'(?P<left>[「“『‘"])(?P<literal>[^「」“”『』‘’"\n，,。；;！？!?]{2,48})(?P<right>[」”』’"]))'
    r'(?=[，,。；;\n]|$)')


def qualify_annotation(text, match, key, main, sources):
    """Check a quoted annotation against the two complete records explicitly identified by the speaker."""
    from .inspection_inference import _in_quote
    from .quote_typography import PAIRS
    from .requested_quote import heading_title

    body, = main.bodies
    title = referenced_title(body)
    if (not title or not heading_title(body, match['identifier']) or _in_quote(text, match.start())
            or {**PAIRS, '"': '"'}[match['left']] != match['right']):
        return None
    # A quoted claim, a hypothetical or someone else's disputed statement is
    # not the speaker's literal attribution. Retain the navigation-only advice.
    sentence_tail = re.split(r'[。！？!?\n]', text[match.end():], maxsplit=1)[0]
    if re.search(r'说法|举例|假设|有误|误引|错误|不成立|不准确|据说', sentence_tail):
        return None
    heading = _title_heading(title)
    candidates = [(other, material) for other, material in sources.items()
                  if other != key and any(heading.match(excerpt) for excerpt in material.excerpts)]
    if len(candidates) != 1:
        return None
    other_key, candidate = candidates[0]
    if other_key[0] != key[0] or len(candidate.bodies) != 1:
        return None
    annex, = candidate.bodies
    # A same-title hit alone cannot identify the purported second source. It
    # must also be the record the draft has already quoted in its entirety.
    if not any(left + annex + right in text[:match.start()] for left, right in (*PAIRS.items(), ('"', '"'))):
        return None
    if match['literal'] in body or match['literal'] in annex:
        return None
    revised = text[:match.start('claim')] + '这两份已读原件里未见这项逐字标注' + text[match.end('claim'):]
    return revised, '两份已读原件里均未见这段逐字标注。可保留已核实的字段，并说明你依据什么判断两份记录的关系。'


def _asks_write_order(console, room):
    from .session_view import event_scope
    read_batch = getattr(console, 'original_batch', None)
    if not callable(read_batch):
        return False
    speech = [e.text for e in read_batch() if e.type == 'message'
              and e.origin == 'external' and event_scope(e) == room]
    if len(speech) != 1 or len(speech[0]) > 2048 or re.search(r'不用|不必|不要|无需|忽略', speech[0]):
        return False
    return bool(re.search(r'(?:归档|录入|写入)(?:的)?(?:先后|顺序|时差|时间差)'
                          r'|(?:先后|相隔|相差|相距|谁先|哪条先).{0,12}(?:归档|录入|写入)'
                          r'|(?:归档|录入|写入).{0,12}(?:相隔|相差|相距|谁先|哪条先)', speech[0]))


def _neighbor_time_note(main, candidate):
    """Compare only known integral timestamps within one second of the indexed records."""
    from .record_history import _record_stamp
    try:
        first, second = _record_stamp(main), _record_stamp(candidate)
    except ValueError:
        return ''
    if not first or not second or int(first) != first or int(second) != second:
        return ''
    first, second = int(first), int(second)
    if first // 1000 != second // 1000 or first == second:
        return ''
    order = '晚' if second > first else '早'
    return f'按消息写入时间，候选记录比当前记录{order} {abs(second - first)} 毫秒。'


def lookup_hint(session, visibility, previous, keyword, context, *, on_open=False, from_list=False, tail=None, alias=None):
    from .record_history import _listing_view
    ref, expected_title = previous
    if not context or expected_title.casefold() == keyword.casefold():
        return ''
    console, owner = context
    guard = getattr(console, 'reply_evidence_guard', None)
    marker = getattr(guard, 'receipt_marker', None)
    view = getattr(console, '_request_session', None)
    snapshot = getattr(view, 'tool_evidence_snapshot', None)
    allows = getattr(view, 'quote_source_visible', None)
    if (owner is None or console.current_batch_identity() is not owner or getattr(guard, 'owner', None) is not owner
            or marker is None or not callable(allows) or not callable(snapshot)):
        return ''
    def visible():
        try:
            return (console.current_batch_identity() is owner and getattr(guard, 'receipt_marker', None) is marker
                    and allows(visibility.current, visibility.current) and snapshot()[0] is marker[0])
        except (OSError, ValueError, RuntimeError):
            return False

    if not visible():
        return ''
    candidate = None
    time_note = ''
    with _listing_view(session) as index:
        with closing(index.rows(ref, visible=visibility.permits, window=(0, 2000))) as found:
            original = next(found, None)
        if original is None or not human_original(original[1], visibility.current):
            return ''
        row = original[1]
        title = referenced_title(row['text']) if row['_text_length'] == len(row['text']) else ''
        if title != expected_title or (tail is not None and not _verified_tail(row['text'], tail, title, console, visibility.current)):
            return ''
        if on_open:
            heading = _title_heading(title)
            candidates = {}
            for key, other in index.nearby_previews(ref):
                if (key == ref or not human_original(other, visibility.current)
                        or not visibility.permits(other.get('scope'))):
                    continue
                preview = other['text']
                match = heading.match(preview)
                # The end of a truncated preview is not the end of a heading.
                if match and (match.end() < len(preview) or len(preview) == other['_text_length']):
                    candidates[key] = other
            if len(candidates) == 1:
                candidate, other = next(iter(candidates.items()))
                if _asks_write_order(console, visibility.current):
                    time_note = _neighbor_time_note(row, other)
    if not visible() or (tail is not None and candidate is None):
        return ''
    prefix = '〔Core 检索提示〕'
    if from_list:
        prefix += '本页原文提到「' + title + '」。'
    if candidate:
        label = ('附近另一条记录的题头与这个名称相符。' if from_list
                 else '附近另一条记录的题头与已读正文所提的「' + title + '」相符。')
        reference = 'record:' + candidate
        if alias is not None:
            short = alias(candidate)
            if short:
                return (prefix + label + time_note + '稳定来源 ' + reference + '。可用 read_events(record_ref='
                        + json.dumps(short) + ') 打开原件。这里按名称定位；是否一并上传或同批归档，另需凭据。')
        return (prefix + label + time_note
                + '可用 read_events(record_ref=' + json.dumps(reference)
                + ') 打开原件。这里按名称定位；是否一并上传或同批归档，另需凭据。')
    if on_open:
        return (prefix + '需要核对时可按名称查找：read_events(store="records", '
                'category="user", grep=' + json.dumps(title, ensure_ascii=False) + ')。')
    return '〔Core 检索提示〕若需继续核对，可用已读原文中的名称检索。read_events(store="records", category="user", grep=' + json.dumps(title, ensure_ascii=False) + ')。'


def found_by_name(page, previous, scope, keyword):
    source_ref, title = previous
    if keyword.strip().casefold() != title.casefold():
        return False
    # A mention in the current question, a result receipt, or the referring sheet
    # itself is not a separately returned candidate bearing this heading.
    heading = _title_heading(title)
    return any(ref != source_ref and human_original(row, scope) and heading.match(row['text'])
               for _, ref, row in page)


def insert_hint(result, header, hint):
    """Insert in the same host header position and preserve attested source coordinates."""
    from .original_evidence import OriginalText
    pos = len(header)
    if not result.startswith(header) or result[pos:pos + 1] not in ('', '\n'):
        return result
    suffix = '\n' + hint
    updated = result[:pos] + suffix + result[pos:]
    if not isinstance(result, OriginalText):
        return updated
    if any(span.start < pos < span.end for span in result.wire_spans):
        return result
    return OriginalText(updated, result.originals, event_sources=result.event_sources,
                        event_times=result.event_times, document_reports=result.document_reports,
                        wire_spans=(span.shifted(len(suffix)) if span.start >= pos else span for span in result.wire_spans),
                        record_scopes=result.record_scopes, complete_records=result.complete_records,
                        record_times=result.record_times)
