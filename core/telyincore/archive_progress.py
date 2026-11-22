"""Keep a conclusion from a pending-status document tied to the date of that document."""
import re

from .archive_wording import _materials
from .inspection_inference import _in_quote
from .memory import _literal_number
from .reported_records import _visible_texts
from .requested_quote import heading_title


# The two fields must be complete, name the same activity, and belong to
# the same titled original. An optional unqualified final attachment is metadata.
_FIELDS = re.compile(
    r'(?P<schedule>(?P<work>[\u3400-\u9fff]{2,7})时间(?:待约|待定|另约))。'
    r'(?P<result>本(?:单|表)(?:没有|未记|尚无)(?P=work)结果)。(?:'
    r'(?:另见|参见|详见|另附)《(?P<annex>[^《》。\n]{1,30})》。)?')
_PENDING = (r'(?:连时间都还没约|尚未约到时间|还没约到时间|还没定时间|'
            r'(?:尚未|还没(?:有)?|未)(?:安排|排期|定下时间))')
POSSIBLE = re.compile(_PENDING)
_RESULT = (r'(?:[，,、]\s*(?:(?:所以|因此|也就)\s*)?|\s*(?:→|->)\s*)'
           r'(?:(?:本单|这单|该单)(?:也)?)?(?:没有结果可谈|没有结果|无(?:{work})?结果)')
_CONCLUSION = re.compile(r'(?:说明|即|所以|因此|可见|也就是|表明|'
                         r'(?:目前|现在)?(?:只)?能确认到(?:的终点是)?[：:]?)\s*$')
_NOW = r'(?:目前|现在|至今|截至现在)'
_QUALIFIED = re.compile(r'当时|那时|彼时|截至当日|截至归档|历史上|在当日|当年的|当年的结论|原话|'
                        r'原句如下|转述|抄录|引用如下|按原样|例句|示例|假[如设]|如果|若是|假使|就算|即使|可能|'
                        r'也许|推测|我猜|不认为|不表示|不代表|并非|不能说|并不是|不能确认|无法确认|不足以确认|无法断定|不能断定|'
                        r'还需核实|有待确认|未必|据说|据.+说|不能排除|兴许|(?:旧单|报告|表上|旧记录)(?:说|写|记载)')
_META = re.compile(r'转述|例句|示例|虚构|演练|猜测|揣测|照抄|模拟|逐字|照录|原样复述|原话如下|原句如下|假设|假如|举例|引用(?:一段|以下|如下)|'
                   r'(?:提供|抄录|引用|念出|背诵|重述|还原|复述)(?:这段|那段|上次|旧答)|[你我他她]说|(?:对方|别人|用户)(?:说|写道)|旧答|旧结论')
_STATUS = re.compile(r'排期|约到|约好|预约|约了|还没约|安排|改期|取消|已定|定了|定下|'
                     r'昨天|昨日|如今|至今|今天|刚刚|最新|结果|进度|已完|已经|现在|仍然|依旧|照旧|确认过|情况未变|没变|我刚')
FEEDBACK = '请把排期和结果归给这份单据。若要说明现在的进度，请依据另外取得的现况；也可直接保留待确认。'


def _fields(body, number):
    title = heading_title(body, number)
    if not title or title == number or len(body) > 320:
        return None
    head, sep, rest = body.partition('。')
    if not sep or head != f'{title} {number}':
        return None
    return _FIELDS.fullmatch(rest)


def _allows_status(text, work):
    # Questions provide a search topic; statements may independently supply the
    # answer, with or without the document number.
    for clause in re.findall(r'[^，,。！？!?；;\n]+[，,。！？!?；;\n]?', text):
        clause = clause.strip()
        if clause.endswith(('?', '？')) or re.search(r'是否|是不是|有没有|能否|如何|怎么|怎样|哪一步|什么', clause):
            continue
        if work in clause or _STATUS.search(clause):
            return True
    return False


def _edit(text, state):
    work = state['work']
    # An attributed past conclusion and an inference endorsed by the assistant
    # may use the same words. Only the following closed grammatical forms qualify.
    claim = re.compile(r'(?P<left>["“]?)' + re.escape(work) + _PENDING
                       + r'(?P<result>' + _RESULT.format(work=re.escape(work)) + r')?(?P<right>["”]?)'
                       + r'(?P<step>这一步)?(?=[。;；！？!?\n]|$)')
    edits = []
    for match in claim.finditer(text):
        if {'"':'"', '“':'”', '':''}.get(match['left']) != match['right'] or _in_quote(text, match.start()):
            continue
        after = text[match.end():]
        if after.startswith(('？', '?')) or re.match(
                r'^[。；;\s]*(?:这(?:句|一判断|个说法)(?:话)?|此推断)?(?:也|并|仍|并非)?(?:不能成立|没有依据|未获证实|不准确|还需核实)', after):
            continue
        before = text[:match.start()]
        sentence = re.split(r'[。！？!?；;\n]', before)[-1]
        connector = _CONCLUSION.search(sentence)
        present = re.search(_NOW + r'\s*$', sentence)
        # In “原话是「…」，配合「…」，说明…”, quotation ends before
        # the conclusion. Strip only the two exact attested fields for this check.
        quoted_fields = f'原话是「{state["result"]}」，配合「{state["schedule"]}」'
        if not (connector or present) or _QUALIFIED.search(sentence.replace(quoted_fields, '')) or re.search(
                r'根据|另据|据(?:最新|刚才|现场)|(?:今天|刚才|今早|昨天)(?:的|又)|亲眼|另有|现场确认', sentence):
            continue
        lead = connector if connector else present
        prefix = sentence[:lead.start()].strip(' \t-*')
        # Require a standalone assertion, or a conclusion tied to the
        # actual earlier field. A free prefix may attribute this status
        # to a new witness or qualify it as conjecture.
        if prefix not in ('', '所以', '因此') and not (connector and
                prefix.endswith((f'配合「{state["schedule"]}」，',
                                 f'停在「{state["schedule"]}」——'))):
            continue
        previous = before[:len(before)-len(sentence)]
        if re.search(r'[：:][ \t]*\n\s*$', previous):
            continue
        # An old title may itself contain the apparent conclusion. A quotation
        # in the conclusion slot is handled only as a summary with an explicit
        # endorsement and the complete closing "这一步".
        if match['left'] and (not match['step'] or not connector):
            continue
        if connector:
            start = match.start()
            wording = f'单据当时所记的“{state["schedule"]}”'
            if match['result']:
                wording += f'和“{state["result"]}”'
        else:
            start = match.start() - len(present[0])
            wording = f'这份单据当时记为“{state["schedule"]}”'
            if match['result']:
                wording += f'，并写明“{state["result"]}”'
        # Use a comma to keep other host checks bound to their original sentence.
        edits.append((start, match.end(), wording + '，现在的实际进度须另有依据'))
    if len(edits) > 1:
        # One qualification of the current state is sufficient for the reply.
        suffix = '，现在的实际进度须另有依据'
        edits[:-1] = [(start, end, replacement.removesuffix(suffix)) for start, end, replacement in edits[:-1]]
    for start, end, replacement in reversed(edits):
        text = text[:start] + replacement + text[end:]
    return text


def qualify(text, read, current, messages):
    if len(text) > 4000 or '`' in text or re.search(r'(?m)^\s*>', text) or _META.search(text):
        return None
    ids = {_literal_number(value) for value in re.findall(r'[A-Za-z0-9_./-]+', text)} - {''}
    questions = {row['text'] for row in current if row.get('current_speech')}
    if len(ids) != 1 or len(questions) != 1:
        return None
    number, = ids
    ask, = questions
    if _literal_number(ask) != number or _META.search(ask):
        return None
    records = _materials(read)
    candidates = []
    for key, source in records.items():
        if len(source.bodies) == len(source.recipients) == 1:
            state = _fields(next(iter(source.bodies)), number)
            if state:
                candidates.append((key, source, state))
    if len(candidates) != 1:
        return None
    key, source, state = candidates[0]
    work = state['work']
    if _allows_status(ask, work):
        return None
    revised = _edit(text, state)
    if revised == text:
        return None
    known = {ask, next(iter(source.bodies))}
    for other_key, other in records.items():
        if other_key == key:
            continue
        for excerpt in other.excerpts:
            if excerpt in known:
                continue
            # An attachment's own title may include the activity. Check its
            # substantive fields without mistaking that title for a new report.
            heading, separator, details = excerpt.partition('。')
            if (not other.bodies or not separator or not state['annex']
                    or heading not in {state['annex'] + sep + number for sep in ('｜', '|')}):
                return None
            from .pair_receipt_scope import _SCALAR
            if not all(_SCALAR.fullmatch(line) for line in details.rstrip('。').split('。')):
                return None
            known.add(excerpt)
    for role, content in _visible_texts(messages):
        if role == 'image':
            return None
        if role == 'user' and content not in known and _allows_status(content, work):
            return None
        if role not in (None, 'system', 'assistant', 'user', 'read_events') and (work in content or _STATUS.search(content)):
            return None
    recipient, = source.recipients
    return revised, FEEDBACK, recipient, key[0]
