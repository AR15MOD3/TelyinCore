"""Limit two affirmative claims to what the identified archive messages establish."""
from datetime import datetime
import re

from .archive_wording import _headline, _materials
from .inspection_inference import _in_quote
from .memory import _literal_number
from .reported_records import _visible_texts
from .record_follow_title import _title_heading, referenced_title
from .requested_quote import heading_title


_PAIR = r'(?:这|那)?(?:两条(?:消息|记录)?|两份(?:材料|单据|记录)|两张(?:单据|单子))'
_ANNEX = r'(?:另[一]?张|这张|那张)?(?:附[件签表页单]|附录)(?:这条|那条)?'
_NAMED_RECORD = r'(?:还|另)有一条(?:(?:标题|名称)(?:同)?为|名为)?'
_DATE = r'\d{4}[-/]\d{1,2}[-/]\d{1,2}'
_BATCH = re.compile(
    r'(?P<near>' + _ANNEX + r'[（(])同(?:一)?批(?:[、，,](?:紧邻|相邻|相继)写入(?P<one>的一条)?'
    r'|[ \t]+(?P<clock>' + _DATE + r'[ \t]+\d{1,2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)(?=[，,、）)]))'
    r'|(?P<written>' + _ANNEX + r'(?:是|也是))同(?:一)?批(?:写进来|写入|录入|归档)的'
    r'(?P<gap>[（(](?P<order>[早晚])[ \t]*(?P<ms>\d{1,6})[ \t]*毫秒[）)])?'
    r'|(?P<named>同(?:一)?批(?:中|里|内)?)(?=' + _NAMED_RECORD + r'[「《“])'
    r'|(?P<modifier>同(?:一)?批(?:次)?(?:[、，,][ \t]*)?)(?P<nearby>相邻|紧邻|相继)?'
    r'(?:写入|归档)?的(?:另|那|这)?一(?:条|份)(?P<independent>独立)?(?:消息|留言|记录)'
    r'|(?P<dated>' + _PAIR + r'(?:都|均)?在\s*(?P<date>' + _DATE + r')\s*的)同(?:一)?批记录[里中]'
    r'|(?P<period>都(?:是|属[于]?)\s*(?P<day>' + _DATE + r')\s*(?P<hour>凌晨|上午|下午|傍晚|晚上))那批'
    r'|(?P<same>(?:这|那)?两条(?:消息|记录)?同编号[、，,])同一(?:个)?时间戳'
    r'|(?P<arrivals>' + _PAIR + r')(?:都|均)?(?:是|属[于]?)同(?:一)?批(?:进来|收进来|录入|归档|写入)的(?=[、，,。；;：:\n]|$)'
    r'|(?P<pair>' + _PAIR + r')?\s*(?:是|属于|来自|归在)同(?:一)?批(?:次)?(?=[，,。；;：:\n]|$)')
_WRITE = r'(?:录入|写入|录下|入档|归档|导入|投递|贴进来|写进来|贴入)'
_ONE_WRITE = r'同一(?:次|笔)(?:系统|批量)?' + _WRITE + r'(?:操作|事务)?'
_INGEST = re.compile(
    r'(?:(?P<difference>(?:相差|只差|差)[ \t]*(?P<ms>\d{1,6})[ \t]*毫秒[，,][ \t]*)' + _ONE_WRITE
    + r'|(?P<all>都(?:是|在|属于)?[ \t]*)(?P<at>' + _DATE + r'[ \t]+\d{1,2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)'
      r'[ \t]*(?:那一次|同一次|同一笔)' + _WRITE + r'(?:的)?'
    + r'|(?P<both>' + _PAIR + r')(?:都|均|也)?(?:是|属于|来自|经由|由)' + _ONE_WRITE
    + r'|(?P<inline>[（(][ \t]*同(?:一)?批(?:次)?(?:一并|一起)?' + _WRITE + r'(?:的)?[ \t]*[）)])'
    + r'|(?P<together>(?:是|都是|是在|都在)?(?:同一天|当天)(?:一起|一同|一并)(?:写(?:入|下|进来)?|录入|记录|归档)(?:的)?)'
    + r'|(?P<pointed>它(?:与|和)(?P<form>[\u3400-\u9fff]{1,11}(?:单|表|清册|记录))(?:是|属于)' + _ONE_WRITE
    + r'的)(?=[（(](?:前后)?(?:只差|相差|差|相隔)\s*\d+\s*毫秒[）)])'
    + r'|(?:是|属于|来自|由)' + _ONE_WRITE + r')(?=[，,。；;：:！？!?（(）)\n]|$)')
_INGEST_DETAILS = re.compile(
    r'(?:同(?:一)?(?:次|笔|个)|一笔|一次性|一起|一并|一同|共同|一次|批量|统一|一块儿|同时).{0,12}' + _WRITE
    + r'|' + _WRITE + r'.{0,12}(?:同一次|同一笔|同一操作|同一事务|操作号|操作标识|事务|流水号|批号|批次)'
      r'|(?:导入|归档|录入|入档|写入|投递|提交)(?:作业|任务|操作|事务|流水|事件|批次)(?:号|标识|编号|ID)'
      r'|(?:事务|操作|流水|投递)(?:号|标识|编号|ID)|transaction[_ -]?id|ingest[_ -]?id'
      r'|(?:一起|一同|一并|共同)(?:写|记录)(?:下|在|的|了)',re.I)
_BLANK = re.compile(
    r'(?:^|(?<=[，,。！？!?；;：:\n]))[ \t]*'
    r'(?P<lead>(?:也就是说|换句话说|所以|因此|可见)?[ \t]*)'
    r'(?:(?:纸单|纸本|表单|原件|纸面|纸质表单)(?:上|中)?的?)?(?P<field>[\u3400-\u9fff]{2,14}?)(?:这[一]?|那[一]?)?(?:栏|栏位|字段)'
    r'(?:是|为|仍是|还是|仍然是)?(?:空的|空着的?|空白的?|留空的?|留白的?|空)(?=[，,。；;！？!?\n]|$)')
_NONASSERTION = re.compile(r'假设|假如|如果|举例|例如|比如|示例|演示|台词|引用示范|有人说|据说|误称|声称|转述'
                           r'|可能|或许|也许|猜测|推断|我猜|推测|我倾向|不能|无法|不应|不等于|不代表|并非|是否|是不是|未必')
_ATTRIBUTION = re.compile(
    r'要是|倘若|倘使|假使|即使|即便|比方|据(?:你|您|他|她|对方)|(?:按|照)(?:照)?(?:你|您|他|她|对方)(?:的)?(?:回忆|说法|印象)'
    r'|(?:你|您|他|她|对方)(?:上次|昨天|当时|刚才|之前|先前|曾经|曾|刚)?(?:说|提到|回忆|认为|觉得|记得)'
    r'|我的(?:猜想|猜测|推断|推测|印象)|我(?:觉得|估计|怀疑)|似乎|看起来|像是')
_AFTER_THOUGHT = re.compile(
    r'^[\s。；;，,、]*(?:(?:这(?:一)?点|这(?:只|还)?|以上|这个判断)[ \t]*)?'
    r'(?:只|仅|还|仍|也)?(?:是|属[于]?)?[ \t]*(?:我(?:个人)?的|个人的?|一种|初步的?|单纯的?)?'
    r'(?:猜想|猜测|推断|推测|估计|印象|假设|回忆|看法)|^[\s。；;，,、]*(?:还|仍|尚|也)?(?:需|待)(?:要)?核实')
_BATCH_DETAILS = re.compile(r'批|同次|同一(?:次|组|趟)|一起(?:入库|登记|交回|归档|提交)|一并(?:入库|登记|归档)|同组|同时(?:入库|归档)|一同(?:入库|归档)')
_PAPER_DETAILS = re.compile(r'空白|空栏|空格|栏位|空的|空着|留空|留白|为空|无字|没(?:有)?填|未填|未写字|横线|划线|表格|纸本|纸面|照片|扫描|影像|图像|[\t｜|].*[\t｜|]')
_EXTERNAL = re.compile(r'照片|扫描|图像|亲眼|现场看[到见]|批号|批次号|批次字段|根据另|另据|另外的证据|你刚(?:说|确认)|据你所说')
_END = re.compile(r'[。！？!?；;\n]')
_EXAMPLE = re.compile(r'(?:排练|演练|假设|假如|举例|示例|例句|例如|举个例子|演示|示范|台词|example|sample|demo|hypothetical)\s*[：:]'
                      r'|(?:他说|她说|有人写道|原话如下|转述如下)\s*[：:]', re.I)
_BUSINESS_CLOCK = re.compile(r'(?:纸面|纸单|纸本|单据(?:自身|本身)?|文件(?:内|上)?|原件|业务|现场|设备|传感器|表内|表上)'
                             r'(?:上|中|里|自带|标注|注明|印刷|印着|的|填的){0,3}(?:计时|时间|时刻|钟|日期)'
                             r'|(?:发送|发出|送达|接收|收到|签发|落款|签章|签收|出单|打印|盖章|生产|采集|测量|提取|作业|交接|复检|复核|开始|完成|提交)(?:时间|时刻|日期)'
                             r'|(?:document|paper|device|sensor)\s+(?:time(?:stamp)?|clock)',re.I)
_HOST_CLOCK = re.compile(r'(?:消息|留言|会话)(?:记录|归档)?(?:写入|入库)?(?:时间|时刻|时间戳)'
                         r'|(?:归档|写入)(?:时间|时刻|时间戳|日期)'
                         r'|(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}[ \t]+)?\d{1,2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?[ \t]*(?:所)?记录'
                         r'|(?:message|archive)\s+(?:time(?:stamp)?|clock)',re.I)
_OTHER_PAIR = re.compile(r'另一(?:对|组|批)|另外(?:两|一)|另两|其[他余](?:的)?(?:两|一|消息|记录|材料|附)|别的|调度|队列|事件批次|批处理')
_CLOCK_DELTA = (r'(?:前后)?(?:仅|只|相)?(?:差|相差|相隔|隔)[ \t]*'
                r'(?:\d+(?:\.\d+)?|[一二三四五六七八九十两百]+)[ \t]*(?:毫秒|秒|分钟)[ \t]*[，,]?[ \t]*')
_ARCHIVE_CLOCK = r'(?:消息|记录|归档|入库|录入|写入)(?:写入)?(?:的)?(?:时间戳|时间|时刻)'
_ARCHIVE_PAIR_TIME = re.compile(r'(?:^|[。！？!?；;：:，,\n])[ \t]*(?:这|那)?两条(?:归档|档案|历史)?(?:记录|消息|留言)'
                                r'(?:的)?(?:' + _ARCHIVE_CLOCK + r')?' + _CLOCK_DELTA + r'$')

BATCH_FEEDBACK = '消息写入时间和标题不能单独证明业务批次；可照报原文或写入时差，批次留待确认。'
INGEST_FEEDBACK = '两条消息各有归档，写入时间接近不能确认两条由同一次操作录入。可报告各自的时间，录入次数须有另外依据。'
PAPER_FEEDBACK = '文字记录能说明这项结果没有记载。要说纸面栏位空白，还需原文对栏位的明确说明或原件证据。'


def _asserted(text, match):
    if _in_quote(text, match.start()) or _EXAMPLE.search(text[:match.start()]):
        return False
    before = _END.split(text[:match.start()])[-1]
    after = re.split(r'[。；;\n]', text[match.end():], maxsplit=1)[0]
    if match.re is _BATCH and match['same'] and re.fullmatch(
            r'\s*(?:是不是|是否)同(?:一)?批[：:]\s*(?:只(?:能)?确认|能确认|可确认)', before):
        before = ''
    if (_ATTRIBUTION.search(before) or _AFTER_THOUGHT.match(text[match.end():])):
        return False
    # A qualification after punctuation can apply to this same proposition.
    if _NONASSERTION.search(before + match.group()) or re.search(r'[？?]|说法(?:并)?不|(?:是|属[于]?)猜测|只是推测', after):
        return False
    return not re.search(r'(?:假设|假如|举例|示例|例句|演示|台词|草稿示范)[：:]\s*$', text[:match.start()])


def _complete(source):
    return len(source.bodies) == len(source.recipients) == 1


def _attested_clocks(rows):
    by_record = {}
    for (_, _, ref, _, scope), row in rows:
        for named, display in row.text.record_times:
            if ref == named:
                by_record.setdefault((scope, ref), set()).add(display)
    clocks = {}
    for key, displays in by_record.items():
        # Date-only list views carry no attested instant. A subsequent exact
        # open can supply one, provided its printed local day is consistent.
        precise = [s for s in displays if isinstance(s, str) and len(s) > 10]
        if len(precise) != 1 or displays - {precise[0], precise[0][:10]}:
            continue
        try:
            clock = datetime.fromisoformat(precise[0])
            if clock.tzinfo is not None:
                clocks[key] = clock
        except (ValueError, TypeError, OverflowError):
            pass
    return clocks


def _same_printed_day(value, clock):
    try:
        return datetime.strptime(value.replace('/', '-'), '%Y-%m-%d').date() == clock.date()
    except ValueError:
        return False


def _matches_printed_clock(value, clock):
    day, time = value.split()
    if not _same_printed_day(day, clock):
        return False
    values = time.split(':')
    hour, minute = map(int, values[:2])
    if (clock.hour, clock.minute) != (hour, minute):
        return False
    if len(values) == 2:
        return True
    second, _, digits = values[2].partition('.')
    return clock.second == int(second) and f'{clock.microsecond:06d}'.startswith(digits)


def _names_pair(before, label, title):
    # A plural pronoun needs a current antecedent. A subject in an
    # earlier paragraph cannot identify two other records just introduced.
    recent = [s.strip() for s in _END.split(before) if s.strip()]
    if not recent:
        return False
    clause = recent[-1]
    return (label in clause and title in clause and not _OTHER_PAIR.search(clause)
            and re.search(re.escape(title) + r'[》」”]?(?:都|均)?(?:已|已经)?(?:查到|找到|看过|读到|在这里|在)?了?$', clause))


_MS_CUE = re.compile(r'^[（(、，,，][ \t]*(?:写入)?(?:时间|前后)?(?:只差|相差|差|相隔)\s*'
                     r'(\d{1,6}|[一二三四五六七八九])\s*毫秒')
_FIELD_LINK = re.compile(r'(?:末栏|末项|最后一栏)(?:里|上)?(?:点名|指的|提到|列明)')


def _refers_to_named_annex(text, match, main, annex, title, gap):
    # On a remote pronoun, require a verified exact annex quotation, the
    # established numerical clock delta and an explicit link to the main
    # form's final field. None of those clues alone binds an implicit pair.
    if gap is None or gap % 1000:
        return False
    after = text[match.end():]
    cue = _MS_CUE.match(after)
    if not cue or not _FIELD_LINK.search(_END.split(after, maxsplit=1)[0]):
        return False
    digits = cue[1]
    delta = int(digits) if digits.isascii() else '一二三四五六七八九'.index(digits)+1
    if delta != abs(gap)//1000:
        return False
    field = re.search(r'(?:末栏|末项|最后一栏)[：:]([^\n]*)$', main.rstrip())
    if not field or referenced_title(field[1]) != title:
        return False
    before = text[:match.start()]
    end = max((before.rfind(left+annex+right)+len(annex)+2
               for left,right in (('「','」'),('“','”'),('『','』'),('"','"'))
               if left+annex+right in before), default=0)
    if not end:
        return False
    bridge = before[end:].strip()
    # Only an explicit recap of that quote and an optional relation heading
    # may intervene. A new subject, a third pair or someone else's opinion
    # makes the archive referent ambiguous.
    return (not re.search(r'另|另外|其余|他人|对方|别的|新[材料档单]|两[条份张]|物流|转述|认为|提到', bridge)
            and re.fullmatch(r'(?:(?:所以|也就是说|可见)[^。；;！？!?\n]{1,60}。)?\s*'
                             r'(?:(?:说明一下|关于)?(?:关联|关系|对应关系)(?:的确定程度)?[：:])?', bridge))


def _batch_edit(text, match, source, origin, materials, label, clocks):
    (body,) = source.bodies
    title = referenced_title(body)
    if not title or title not in text:
        return None
    heading = _title_heading(title)
    annexes = [(key, item) for key, item in materials.items()
               if any(heading.match(excerpt) for excerpt in item.excerpts)]
    if len(annexes) != 1:
        return None
    (key, annex), = annexes
    if (key == origin or key[0] != origin[0] or not _complete(annex)
            or annex.recipients != source.recipients):
        return None
    first, second = clocks.get(origin), clocks.get(key)
    gap = None
    if first is not None and second is not None:
        # Each instant carries the offset displayed for its own record.
        diff = second - first
        gap = (diff.days * 86400 + diff.seconds) * 1_000_000 + diff.microseconds
    if match.re is _BATCH and match['arrivals']:
        if (_names_pair(text[:match.start()],label,title) or
                _refers_to_named_annex(text,match,body,next(iter(annex.bodies)),title,gap)):
            return match['arrivals']+'的批次仍待确认'
        return None
    if match.re is _INGEST and match['pointed']:
        name = heading_title(_headline(body),label)
        if (name and len(match['form']) >= 3 and name.endswith(match['form'])
                and _refers_to_named_annex(text,match,body,next(iter(annex.bodies)),title,gap)):
            return '它和'+match['form']+'是否由同一次操作写入仍待确认'
        return None
    modifier = match.re is _BATCH and match['modifier']
    inline = match.re is _INGEST and match['inline']
    together = match.re is _INGEST and match['together']
    if modifier or inline or together:
        before = text[:match.start()]
        named = [before.rfind(token) for token in (label, title)]
        if min(named) < 0 or _OTHER_PAIR.search(before[max(named):]):
            return None
        if modifier:
            # Only a predicate about the identified annex, never a parcel,
            # an event scheduling batch, or the contents of a quotation.
            attached = re.search(r'(?:^|[。！？!?；;：:，,\n])[ \t]*(?:这条|那条|另张|这张|那张)?(?:'
                                 + re.escape(title) + r'|附[签表件页单])(?:那条|这条)?(?:则是|也是|就是|是)$', before)
            (annex_body,) = annex.bodies
            quotes_annex = (re.search(r'(?:^|[。！？!?；;：:\n])[ \t]*$', before)
                            and re.match(r'(?:写的(?:是)?|记的(?:是)?|原文(?:是)?)[ \t：:]*[「“『]'
                                         + re.escape(annex_body) + r'[」”』]', text[match.end():]))
            if not attached and not quotes_annex:
                # A numbered item must establish *its own* subject. Earlier
                # names could describe a different attachment in the list.
                numbered = re.search(r'(?:^|\n)[ \t]*\d{1,2}[.)、）][ \t]*'
                                     r'(?:另|另[一]?张|这张|那张)?附[签表件页单](?:则是|也是|就是|是)$', before)
                item = re.split(r'\n[ \t]*(?:\d{1,2}[.)、）]|[-*•])', text[match.end():], maxsplit=1)[0]
                # The item heading names the same title and the next line
                # quotes the whole independently inspected body, verbatim.
                lines = item.splitlines()
                if (not numbered or len(lines)<2 or title not in lines[0] or
                        not any(lines[1].strip()==left+annex_body+right for left,right in
                                (('「','」'),('“','”'),('『','』'),('"','"')))):
                    return None
            if match['nearby']:
                if gap is None or abs(gap) >= 1_000_000:
                    return '另一条' + (match['independent'] or '') + '记录（写入是否相近待确认）'
                # Proximity is justified by the displayed clocks. Contiguous
                # record positions and actual event order are not entailed.
                return '写入时间相近的另一条' + (match['independent'] or '') + '记录'
            return match[0][len(match['modifier']):].removeprefix('的')
        clause = _END.split(before)[-1]
        if _BUSINESS_CLOCK.search(clause) or re.search(r'手写|手书|起草|誊写|笔迹|落笔|纸本|纸面|纸单', clause):
            return None
        main_title = heading_title(_headline(body), label)
        named_subject = (re.escape(main_title) + r'(?:[ \t]+' + re.escape(label) + r')?[ \t]*(?:和|与|及)[ \t]*[《「“]?'
                         + re.escape(title) + r'[》」”]?') if main_title else None
        named_pair = named_subject and re.search(
            named_subject + (r'(?:的)?' + _ARCHIVE_CLOCK + _CLOCK_DELTA if inline else '') + r'$', before)
        if not (named_pair or _ARCHIVE_PAIR_TIME.search(before)):
            return None
        if inline:
            return match[0][0] + '是否同一次录入仍待确认' + match[0][-1]
        if first is not None and second is not None and first.date() == second.date():
            return '写入日期相同，是否同一次录入仍待确认'
        return '是否同日或同一次录入仍待确认'
    if match.re is _INGEST:
        if match['difference']:
            if gap is None or abs(gap) != int(match['ms']) * 1000:
                return '所述时差及是否同一次录入仍待确认'
            return match['difference'] + '是否同一次录入仍待确认'
        if match['all']:
            before = _END.split(text[:match.start()])[-1]
            if not re.search(r'两(?:条|份|张)', before):
                return None
            if gap is not None and all(_matches_printed_clock(match['at'], t) for t in (first,second)):
                return '写入时间都在 ' + match['at'] + '，录入次数仍待确认'
            return '所述写入时间和录入次数仍待确认'
        if match['both']:
            return match['both'] + '是否同一次录入仍待确认'
        clause = re.split(r'[，,。！？!?；;：:\n]', text[:match.start()])[-1]
        if label in clause and re.search(r'(?:和|与|及)\s*[《「“]?' + re.escape(title) + r'[》」”]?$', clause):
            return '是否同一次录入仍待确认'
        return None
    if match['named']:
        # This is the modifier on the named second archive record, not a
        # production lot mentioned elsewhere or text merely being quoted.
        rest = text[match.end():]
        if not any(re.match(_NAMED_RECORD + re.escape(left + title + right)
                            + r'(?:的)?(?:消息|记录|留言)', rest)
                   for left, right in (('「','」'),('《','》'),('“','”'))):
            return None
        if not re.search(r'(?:^|[，,。；;：:（(—\n]|是|则是)\s*$', text[:match.start()]):
            return None
        return ''
    if match['same']:
        other_body, = annex.bodies
        # The host clock may only qualify a claim about message timestamps.
        # A printed or business timestamp denotes a different clock, even on
        # forms for which both system messages have known write times.
        before = text[:match.start()]
        context = _END.split(before)[-1]
        sentence = context + match[0] + _END.split(text[match.end():])[0]
        if _BUSINESS_CLOCK.search(sentence) or '时间栏' in sentence or '时间字段' in sentence:
            return None
        specific = ('消息' in match['same'] or
                    any(not _in_quote(text, len(before) - len(context) + m.start()) for m in _HOST_CLOCK.finditer(context)))
        if not specific:
            competing = any(_BUSINESS_CLOCK.search(line) and not re.search('待补|待约|未记|没有|未知', line)
                            for context_text in (before, body, other_body) for line in _END.split(context_text))
            if (competing
                    or not any(not _in_quote(text, m.start()) for m in _HOST_CLOCK.finditer(before))):
                return None
        if _literal_number(other_body[:256]) != label or not gap or abs(gap) >= 1_000_000 or gap % 1000:
            return None
        return match['same'] + f'写入时间相差 {abs(gap) // 1000} 毫秒'
    if match['near']:
        if match['clock']:
            if second is not None and _matches_printed_clock(match['clock'], second):
                return match['near'] + match['clock']
        elif gap is not None and abs(gap) < 1_000_000:
            return match['near'] + '写入时间相近' + (match['one'] or '')
        return match['near'] + '所述写入时间和批次仍待确认'
    if match['written']:
        tail = ''
        if match['gap']:
            expected = int(match['ms']) * (1000 if match['order']=='晚' else -1000)
            tail = match['gap'] if gap == expected else '（所述写入时差待核）'
        return match['written'] + '另一条记录' + tail
    if match['dated']:
        if gap is not None and all(_same_printed_day(match['date'], t) for t in (first,second)):
            return match['dated'] + '归档记录里'
        return '两条的归档日期和批次仍待确认'
    if match['period']:
        bounds = {'凌晨':(0,5), '上午':(7,11), '下午':(13,17), '傍晚':(17,18), '晚上':(19,23)}
        start, end = bounds[match['hour']]
        if gap is not None and all(_same_printed_day(match['day'], t) and start <= t.hour <= end for t in (first,second)):
            return '都记在 ' + match['day'] + ' ' + match['hour']
        return '批次和所述写入时段仍待确认'
    if match['pair']:
        # A bare plural pronoun may introduce two different records. Bind it
        # to the two named documents in the current or last complete sentence.
        if not _names_pair(text[:match.start()],label,title):
            return None
        return match['pair'] + '的批次仍待确认'
    # A bare predicate needs both named documents in its own subject. A
    # reference elsewhere could instead describe a shipment or a later job.
    clause = re.split(r'[，,。！？!?；;：:\n]', text[:match.start()])[-1]
    if label in clause and re.search(r'(?:和|与|及)\s*[《「“]?' + re.escape(title) + r'[》」”]?$', clause):
        return '的批次仍待确认'
    return None


def _missing_in_record(body, field):
    subject = re.escape(field)
    return re.search(r'(?:没有|没记|未(?:记载|记录|给出|注明)|没(?:有)?(?:写明|给出|记录))' + subject
                     + r'(?=[，,。；;\s]|$)|' + subject + r'(?:尚未|还没(?:有)?|未|没有)(?:记载|记录|给出|注明)', body)


def _possible_support(text, detail):
    # Asking about an empty form or a shared batch does not attest one.
    if not detail.search(text):
        return False
    for clause in re.split(r'[，,。！？!?；;\n]', text):
        if detail.search(clause) and not re.search(r'是否|是不是|有没有|[吗么]$', clause.strip()):
            return True
    return False


def qualify(text, read, current, messages=()):
    """Use only inspected message bodies. The caller rechecks scope before applying the edit."""
    if len(text) > 4000 or '`' in text or re.search(r'(?m)^\s*>', text):
        return None
    claims = [(m, _BATCH_DETAILS, BATCH_FEEDBACK) for m in _BATCH.finditer(text) if _asserted(text, m)]
    claims += [(m, _INGEST_DETAILS, INGEST_FEEDBACK) for m in _INGEST.finditer(text) if _asserted(text, m)]
    claims += [(m, _PAPER_DETAILS, PAPER_FEEDBACK) for m in _BLANK.finditer(text) if _asserted(text, m)]
    ids = {_literal_number(t) for t in re.findall(r'[A-Za-z0-9_./-]+', text)} - {''}
    if not claims or len(ids) != 1:
        return None
    (label,) = ids
    rows = list(read())
    materials = _materials(lambda: rows)
    # An explicitly marked batch, form, image or other potential source of
    # corroboration must not be overridden on the basis of two archive records.
    current_text = {row['text'] for row in current if row.get('current_speech')}
    clocks = _attested_clocks(rows) if any(m.re is not _BLANK for m, _, _ in claims) else {}
    available = current_text | {entry[1] for entry, _ in rows}
    for role, content in _visible_texts(messages):
        if role == 'image':
            return None
        # An independent source may corroborate this document. Host navigation,
        # previous drafts and serialized read_events text confer no authority.
        if role not in (None, 'system', 'assistant', 'read_events') and label in content:
            available.add(content)
    # The search may also return the current user's question bearing the
    # exact same identifier. All other matching headings remain candidates,
    # even when a version is only partially visible or lacks the field.
    candidates = [(key, item) for key, item in materials.items()
                  if any(part not in current_text and heading_title(_headline(part), label) is not None
                         for part in item.excerpts)]
    if len(candidates) != 1:
        return None
    (key, source), = candidates
    if not _complete(source):
        return None
    (body,) = source.bodies
    edits = []
    for match, explicit, feedback in claims:
        if match.re is _INGEST:
            before = _END.split(text[:match.start()])[-1]
            if _EXTERNAL.search(before) or any(_possible_support(item, explicit) for item in available):
                continue
        elif not (match.re is _BATCH and match['same']) and (
                _EXTERNAL.search(text) or any(_possible_support(item, explicit) for item in available)):
            continue
        if match.re is _BLANK:
            if not _missing_in_record(body, match['field']):
                continue
            replacement = match['lead'] + '这份记录没有给出' + match['field']
        else:
            replacement = _batch_edit(text, match, source, key, materials, label, clocks)
        if replacement is not None:
            if match.re is _BATCH and match['same']:
                feedback = '两份已读消息的写入时间在毫秒位不同。可以照报两条写入时间或这项差值。'
            edits.append((match.start(), match.end(), replacement, feedback))
    if not edits:
        return None
    revised = text
    for start, end, replacement, _ in sorted(edits, reverse=True):
        revised = revised[:start] + replacement + revised[end:]
    return revised, ' '.join(dict.fromkeys(e[3] for e in edits)), next(iter(source.recipients)), key[0]
