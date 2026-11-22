"""Describe what two explicitly associated archive records can establish on a comparison page."""
import re

from .inspection_inference import _in_quote
from .memory import _literal_number
from .record_follow_title import _NONCURRENT_FIELD, _title_heading, human_original, referenced_title
from .requested_quote import heading_title
from .session_view import event_scope

NOTE = '两份原文的出处'
DAY_HEADER = ('每行首列是归档写入时间。发送、送达及业务时间须另有依据。'
              '单独列出的日期是当地入档日，精确写入时刻可用 record_ref 查看。'
              '写入先后不能证明同次上传或同批归档。')

_DOCUMENT_PAIR = re.compile(r'附[页签表件单录]|两[份张条]文书|两[份张]单据')
_CLOCK_DETAIL = re.compile(
    r'时间|时刻|时分|时点|时区|小时|分钟|秒|点钟|几点|何时|什么时候|先后|顺序|'
    r'早晚|先写|后写|同时|当晚|凌晨|上午|下午|晚上|相隔|时差|间隔|多久|'
    r'收发|发送|发来|发出|送达|投递|上传|原始时间戳|\d\s*[:：点时]\s*\d|\bUTC\b',
    re.IGNORECASE,
)


def document_date_view(context, scope):
    """For a scoped document comparison, list calendar days; exact opens retain full clocks."""
    if not context:
        return False
    console, owner = context
    if not owner or console.current_batch_identity() is not owner:
        return False
    events = console.original_batch()
    if len(events) != 1:
        return False
    event, = events
    if event.type != 'message' or event.origin != 'external' or event_scope(event) != scope:
        return False
    ask = event.text
    return bool(_literal_number(ask) and _DOCUMENT_PAIR.search(ask) and not _CLOCK_DETAIL.search(ask))


def _reported_status(body, annex_title):
    # Only summarize explicitly labelled, complete clauses. Other forms,
    # quoted examples or further status statements keep their full text alone.
    clauses = body.rstrip('。').split('。')
    if len(clauses) != 4:
        return ''
    pending = re.fullmatch(r'([\u3400-\u9fff]{2,7})时间待约', clauses[1])
    if not pending:
        return ''
    work = pending[1]
    standalone_link = {prefix + left + annex_title + right
                       for prefix in ('另见', '参见', '详见', '另附')
                       for left, right in (('《', '》'), ('「', '」'), ('“', '”'))}
    if clauses[2] != '本单没有' + work + '结果' or clauses[3] not in standalone_link:
        return ''
    # Identify the literal clauses. Do not turn them into a conclusion about
    # the work actually performed, even if the annex reports a later outcome.
    return f'主件所记｜第 2、3 句原文：{clauses[1]}。{clauses[2]}。'


def _reference_clause(body, target):
    # Pin a literal to its complete closing clause or to an actual header field.
    # An intervening narrative can qualify or withdraw the apparent instruction.
    if _NONCURRENT_FIELD.search(body) or re.search(r'旧版|删除|失效|废止|取消|更正|撤销|上版|不再|不应|不宜|不属实|并非|已改', body):
        return ''
    target = re.escape(target)
    pointer = r'(?:另见|参见|参阅|参照|详见|另附)(?:《' + target + r'》|「' + target + r'」|“' + target + r'”)'
    for match in re.finditer(r'(?:^|(?<=[。；;\n｜|]))[ \t]*(' + pointer + r')[ \t]*(?=[。；;\n｜|]|$)', body):
        trailing = body[match.end(1):].strip('。；;｜| \n')
        prefix = body[:match.start(1)].rstrip()
        header_field = not re.search(r'[。；;\n]', prefix) and prefix.endswith(('｜', '|'))
        if trailing and not header_field:
            continue
        if not _in_quote(body, match.start(1)):
            return match[1]
    return ''


# Only well-defined field values are eligible for a statement about one-way
# citation. Free prose may refer to a main document implicitly or by an alias.
_SCALAR = re.compile(
    r'(?:[\u3400-\u9fff]{1,10}(?:为|是|呈|[：:])'
    r'(?:[黑白灰红橙黄绿青蓝紫棕褐金银粉]{1,3}色|[0-9]{1,8}(?:\.[0-9]{1,4})?[件箱个份张卷米吨克台])'
    r'|(?:本表|本单)?(?:批次号|批号|架号|序号)[ \t：:]*[A-Za-z0-9][A-Za-z0-9-]{0,19})')
_FIELD_POINTER = (r'(?:(?:[\u3400-\u9fff]{1,4})?(?:栏|条款|数量|规格|页码))?'
                  r'(?:另见|参见|回见|参阅|参照|详见|另附)')
_CONTEXT = re.compile(r'别名|又名|也叫|简称|旧版|转述|上一条|声称|更正|删除|否决|撤销|作废|'
                      r'曾经|假如|假设|示范|原话|当时说|若要|不再|不应|不宜|不能|[“”「」].*[“”「」].*[“”「」]')


def _typed_citation(clauses, title):
    literal = re.escape(title)
    target = re.compile(_FIELD_POINTER + r'(?:《' + literal + r'》|「' + literal + r'」|“' + literal + r'”)')
    return [i for i, clause in enumerate(clauses) if target.fullmatch(clause)]


def _title_direction(main, annex, number, name, title, question):
    if (name == title or _CONTEXT.search(question) or _NONCURRENT_FIELD.search(main + annex)
            or _CONTEXT.search(main) or _CONTEXT.search(annex)):
        return ''
    first = main.removesuffix('。').split('。')
    second = annex.removesuffix('。').split('。')
    if (first[0].strip() != f'{name} {number}'
            or second[0] not in (title + '｜' + number, title + '|' + number)):
        return ''
    a, b = first[1:], second[1:]
    forward = _typed_citation(a, title)
    if len(forward) != 1 or not b:
        return ''
    # A neutral main form must itself have a typed citation and either only
    # scalar fields or the existing attested pair of scheduling/result clauses.
    if not (_reported_status(main, title) or
            all(_SCALAR.fullmatch(s) for i, s in enumerate(a) if i != forward[0])):
        return ''
    backward = _typed_citation(b, name)
    if len(backward) > 1 or any(not _SCALAR.fullmatch(s) for i, s in enumerate(b) if i not in backward):
        return ''
    if backward:
        return f'按题名引用为双向：《{name}》→《{title}》，《{title}》→《{name}》。两处原句分别是「{a[forward[0]]}」和「{b[backward[0]]}」。'
    return f'按题名引用为单向：《{name}》→《{title}》。两份题头同号；《{title}》正文没有反向点名《{name}》。'


def comparison_note(context, scope, shown, seen):
    if not context:
        return ''
    console, owner = context
    if not owner or console.current_batch_identity() is not owner:
        return ''
    events = [e for e in console.original_batch() if e.type=='message' and e.origin=='external' and event_scope(e)==scope]
    if len(events)!=1:
        return ''
    ask = events[0].text
    number = _literal_number(ask)
    asks_pair = (('批' in ask and '纸' in ask) or
                 (re.search(r'两(?:份|条|张|件)|附(?:页|录|签|表|件)', ask)
                  and re.search(r'关系|引用|指向|题头|同名|标题|发送|发出|归档|入库|日期', ask)))
    if not number or not asks_pair:
        return ''
    originals = [(ref,row['text']) for _,ref,row in shown if human_original(row,scope) and row['_text_length']==len(row['text'])<=240]
    mains = [(ref, body, referenced_title(body)) for ref, body in originals
             if heading_title(body, number) and referenced_title(body)]
    if any(human_original(row, scope) and row['_text_length'] > len(row['text'])
           and heading_title(row['text'], number) for _, _, row in shown):
        return ''
    if len(mains) != 1:
        return ''
    main, body, title = mains[0]
    # A heading followed by a subtitle, attribution or earlier version has
    # not identified whose words the following independent fragments represent.
    headline = re.split(r'[。\n]', body, maxsplit=1)[0].strip()
    name = heading_title(body, number)
    if not re.fullmatch(re.escape(name) + r'[ \t]+' + re.escape(number), headline):
        return ''
    heading = _title_heading(title)
    candidates = [(ref, row) for _, ref, row in shown if ref != main and human_original(row, scope)
                  and heading.match(row['text'])]
    if len(candidates) != 1:
        return ''
    other_ref, other = candidates[0]
    annex = other['text']
    if other['_text_length'] != len(annex) or len(annex) > 240 or (main in seen and other_ref in seen):
        return ''
    # The headings and spelling are textual facts. Reproduce an explicit
    # reference only with its owning document; an identifier in a heading
    # by itself carries neither a return reference nor a transport clock.
    lines = [NOTE, f'题头｜主件《{name}》；另一条《{title}》。']
    for owner, original, target in ((name, body, title), (title, annex, name)):
        pointer = _reference_clause(original, target)
        if pointer:
            lines.append(f'《{owner}》独立原句｜{pointer}')
    identifier_field = re.search(r'(?:｜|\|)' + re.escape(number) + r'(?=[｜|。；;\n]|$)', annex)
    if identifier_field and identifier_field.start() < len(re.split('[。；;\n]', annex, maxsplit=1)[0]):
        info = f'《{title}》题头列有编号 {number}'
        if name not in annex:
            info += f'；这条正文未出现《{name}》这个题名'
        lines.append(info + '。')
    direction = _title_direction(body, annex, number, name, title, ask)
    if direction:
        lines.append(direction)
    stamps = {ref: stamp for stamp, ref, _ in shown if ref in (main, other_ref)}
    if len(stamps) == 2 and all(stamps.values()):
        from .record_history import _formatted_record_time
        main_time, main_utc = _formatted_record_time(stamps[main])
        annex_time, annex_utc = _formatted_record_time(stamps[other_ref])
        if not (main_utc or annex_utc):
            first, second = main_time[:10], annex_time[:10]
            days = first if first == second else f'《{name}》{first}；《{title}》{second}'
            lines.append(f'Core 归档写入日期｜{days}。')
    status = _reported_status(body, title)
    if status:
        lines.append(status + '这两句只记当时的排期和单内结果；实际进度如有后续，以后续凭据为准。')
    return '\n'.join(lines)
