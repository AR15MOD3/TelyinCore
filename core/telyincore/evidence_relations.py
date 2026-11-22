"""Host-verified relationships, not semantic agreement or business success.

All projections are transient and scope checked. No model or business operation
is invoked, and failure to verify a locator is not proof of no relationship.
"""
from datetime import datetime
import hashlib
import re

SOURCE_UNKNOWN = '〔Core 证据校正〕这条提醒的用户委托来源尚未核实；只能称为该提醒或相关事项，不能据创建上下文认定是用户托付的约定，也不能据来源未核实排除用户委托。'
LINK_UNVERIFIED = '〔Core 证据校正〕当前尚未核实所述记录之间的关联；没有找到或未读取来源，不能据此认定不相关。请按原文定位核对，结果未知时不重放。'
LINK_VERIFIED = '〔Core 证据校正〕该宿主检查点与可读工具原文具有一致的调用标识及内容校验，记录关联已核实；这不证明业务成功，也不授权重放。'
TIME_FINITE = '〔Core 证据校正〕该提醒记录的是有限到期时刻，不是永不到期；能否在到期时唤醒取决于运行状态，错过时仍需按补检及待办机制处理。'


_QUOTE_DATE = r'(?:\d{4}-\d{2}-\d{2}|\d{4}年\d{1,2}月\d{1,2}日)'
# An ordinal binds only a pair of explicit record citations immediately before
# the quote. A date inside the quotation cannot complete this pattern.
_RECORD_CITATION = (r'(?:消息\s*[#＃]\s*\d+\s*[（(]\s*(?:(event:\d+)\s*[,，]\s*)?'
                    r'|[（(]\s*(event:\d+)\s*[,，]\s*)(?:记录日期\s*)?('
                    + _QUOTE_DATE + r')\s*[)）]')
_PAIRED_QUOTE = re.compile(_RECORD_CITATION + r'\s*(?:与|和|及|、)\s*' + _RECORD_CITATION
    + r'\s*[,，：:]?\s*(前者|后者)(?:的)?(?:结尾|末尾|原话|原文|记录|留言|最后一句)'
      r'(?:写道|写着|记为|写的是|是|为|说)?\s*[：:]?\s*$')


def finite_due(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo is None or moment.utcoffset() is None:
            return None
        return moment.isoformat()
    except (ValueError, OverflowError):
        return None


def time_evidence(value):
    due = finite_due(value)
    return {'kind': 'scheduled_time', 'finite_due_at': due,
            'time_status': 'finite' if due is not None else 'unverified',
            'wake_or_delivery_proven': False,
            'boundary': '有限日期即使很远也不是永远不会到期；到期、实际唤醒、送达、事项完成分别核对。'}


def checkpoint_evidence(item, visibility, session, check_reference):
    """Match bounded durable locators by all fields plus original text digest.

    A recent session locator is only a candidate. check_reference must reread
    the authoritative source under current visibility, then the caller rechecks
    batch/scope. Missing/ambiguous/stale entries remain unverified, never absent.
    """
    from .activity_memory import validate_checkpoint
    checkpoint = item.get('checkpoint')
    if item.get('origin') != 'host_checkpoint' or not checkpoint:
        return None
    validate_checkpoint(checkpoint)
    visibility.require()
    _, _, entries = session.tool_evidence_snapshot()
    links = []; text_budget = 8192
    for saved in checkpoint['records']:
        if not saved['tool_ref'] or saved['kind'] not in ('function_call', 'function_result'):
            continue
        matches = [(row, ref) for _, row, ref in entries
                   if row.scope == item['scope']
                   and (visibility.administrator or visibility.permits(row.scope))
                   and all(getattr(row, k) == saved[k] for k in ('name','role','kind','tool_ref'))
                   and hashlib.sha256(row.text.encode()).hexdigest() == saved['sha256']
                   and (not saved['record_ref'] or ref == 'record:' + saved['record_ref'])]
        if len(matches) != 1:
            continue
        row, ref = matches[0]
        if check_reference(visibility, ref):
            link = {k:getattr(row, k) for k in ('name','kind','tool_ref')}
            link['record_ref'] = ref
            if any(old['record_ref'] == ref for old in links): continue
            length = min(len(row.text), 2048, text_budget)
            link.update(text=row.text[:length], text_complete=length == len(row.text), total_characters=len(row.text))
            text_budget -= length
            links.append(link)
    return {'kind': 'checkpoint_record_linkage',
            'status': 'exact_tool_records' if links else 'not_currently_verified',
            'links': links, 'business_success_proven': False, 'replay_authorized': False,
            'boundary': 'links 的调用标识、类型、名称和正文摘要已与当前可读原文核对，证明记录关联。text为已核对的原文；text_complete=true表示已完整返回，不必再读取同一原文或它的查询回执，false则用record_ref补读。source_ref 为空不抹去该关联；结果是否成功仍以结果正文为准。没有 links 只是本次未核实，不证明无关联。'}


class AnswerRelations:
    """Small current-batch facts from genuine host tool receipts only."""
    def __init__(self):
        self.timers = {}
        self.links = {}
        self.created_here = False
        self.originals = []
        self.portrait_quotes = []
        self.event_sources = []
        self.event_times = []
        self.document_reports = []

    def observe_originals(self, value):
        from .original_evidence import OriginalText, PortraitSourceText
        if isinstance(value, OriginalText):
            # A verified portrait excerpt can establish a quotation and its
            # date even when other reviewed messages qualify the conclusion.
            target = self.portrait_quotes if isinstance(value, PortraitSourceText) else self.originals
            for source in value.originals:
                if source not in target:target.append(source)
            del target[:-64]
            for fact in value.event_sources:
                if fact not in self.event_sources:self.event_sources.append(fact)
            del self.event_sources[:-32]
            for fact in value.event_times:
                if fact not in self.event_times: self.event_times.append(fact)
            del self.event_times[:-32]
            for fact in value.document_reports:
                if fact not in self.document_reports:
                    self.document_reports.append(fact)
            del self.document_reports[:-16]

    def quotation_sources(self):
        return self.originals + self.portrait_quotes if self.portrait_quotes else self.originals

    def dated_quote_corrections(self, parts, *, quote_issues=None, visible_sources=None):
        self.unverified_quote_parts = set()
        originals = self.quotation_sources()
        if not originals:
            return {}
        changes = {}; fenced = False
        # A short quotation must not be skipped to attach a later quotation
        # to the same date. Semicolons also end this local attribution clause.
        pattern = re.compile('(' + _QUOTE_DATE + r')([^。！？；;\n“”「」"]{0,80}?)[“「"]([^”」"]{6,200})[”」"]')
        for i, part in enumerate(parts):
            if '```' in part:
                if part.count('```')%2:fenced = not fenced
                continue
            if fenced or part.lstrip().startswith('>') or re.search('如果|假如|例如|举例|是否|不确定',part):continue
            # Resolve an explicit same-day reference only to an absolute date
            # earlier in this very segment. Never borrow a date from another
            # paragraph, a quotation, or the wall clock.
            quoted = [m.span() for m in re.finditer(r'[“「『"][^”」』"]*[”」』"]', part)]
            dated = [m for m in pattern.finditer(part) if not any(a <= m.start() < b for a,b in quoted)]
            for relative in re.finditer(r'(?:同一天|同日|当天)([^。！？；;\n“”「」"]{0,40}?)[“「"]([^”」"]{6,200})[”」"]', part):
                if any(a <= relative.start() < b for a,b in quoted): continue
                bridge, quote = relative.groups()
                if not re.search('说|原话|记录|留言|一句|那句|写|记', bridge): continue
                anchors = [m for m in re.finditer(r'\d{4}-\d{2}-\d{2}|\d{4}年\d{1,2}月\d{1,2}日', part[:relative.start()])
                           if not any(a <= m.start() < b for a,b in quoted)]
                if not anchors: continue
                anchor=anchors[-1]
                if re.search('不是|并非|不能|无法|误称|误写', part[:anchor.start()]): continue
                # Reuse the same typed-original/date validation below.
                dated.append((anchor.group(), bridge, quote))
            for match in dated:
                relative = isinstance(match, tuple)
                if not relative and re.search('不是|并非|不能|无法|误称|误写',part[:match.start()]):continue
                date, bridge, quote = match if relative else match.groups()
                target_ref = None
                paired = None
                if not relative and re.search(_QUOTE_DATE, bridge):
                    # Match an adjacent pair, retaining the first date's exact
                    # position so a preceding third record cannot be skipped.
                    prefix = max(0, match.start() - 100)
                    paired = _PAIRED_QUOTE.search(part[prefix:match.start(3)-1])
                    if paired and prefix + paired.start(3) == match.start():
                        offset = 1 if paired.group(7) == '前者' else 4
                        target_ref = paired.group(offset) or paired.group(offset+1)
                        date = paired.group(offset+2)
                    else:
                        # Several dates precede the same quotation. Its text
                        # can still be checked, but its date is ambiguous.
                        paired = None
                        if '前者' in bridge or '后者' in bridge:
                            date = None
                # A quoted outcome under an explicit denial of entailment is
                # not a verbatim historical attribution. Do not inspect the
                # quote's own words to grant this exemption.
                if re.search(r'(?:不能|无法|并不|不)(?:反过来|据此|直接|足以)?(?:证明|说明|推断|推出|认定|支持)\s*$', bridge):continue
                # A chronological bullet can attribute a quote by adjacency,
                # without an explicit 你说. Require a bare date/time label so
                # prose about when a described event happened is not relabelled.
                bare_label = (not relative and re.fullmatch(r'[\s*•—|\-]*', part[:match.start()])
                    and re.fullmatch(r'[\s*|]*(?:\d{1,2}:\d{2}(?::\d{2})?)?[\s*|]*', bridge))
                note_quote = (not relative and re.fullmatch(r'\s*(?:的)?(?:笔记(?:里|中)?记的是|更正(?:改)?为)\s*', bridge)
                              and not re.search(r'建议|如果|假如|计划|打算|请|拟于|例如|比如|能否|是否', part[:match.start()]))
                if not relative and not paired and not bare_label and not note_quote and not re.search('说|原话|记录|留言|一句|那句|写|：|:|—',bridge):continue
                day = None
                if date is not None:
                    try:day=datetime(*map(int,re.findall(r'\d+',date))).date().isoformat()
                    except ValueError:continue
                sources=[s for s in originals if quote in s[1]]
                if day in {s[0] for s in originals} and not sources:
                    # A dated verbatim attribution must occur in the original
                    # material actually read. Absence here does not prove the
                    # user never said it, or authorize us to invent a quote.
                    changes[i]='〔Core 证据校正〕这段带日期的逐字引文未在本批已核对原文中找到，暂不能据此确认原话或日期；不据此断言过去从未说过。\n'
                    self.unverified_quote_parts.add(i)
                    if quote_issues is not None and len(quote_issues) < 4:
                        quote_issues.append((day, quote, target_ref))
                        continue
                    break
                if target_ref:
                    sources = [source for source in sources if source[2] == target_ref]
                    if not sources:
                        changes[i] = (f'〔Core 来源核对〕本批已读原文不能确认这段引文来自 {target_ref}，'
                                      '请保留来源待确认。\n')
                        break
                if date is None:
                    changes[i] = '〔Core 日期核对〕这段引文前列出了多个记录日期，当前不能确定所指日期。\n'
                    break
                dates={s[0] for s in sources}
                if len(dates) > 1 and day not in dates:
                    changes[i] = ('〔Core 日期核对〕本批已读原文在多个日期出现同一引文，'
                                  f'暂不能确认这句话出自 {day} 的记录。\n')
                    break
                if len(dates)!=1 or day in dates:continue
                if visible_sources is not None:
                    # A newly supplied date and locator must come from an original
                    # still authorized for the current request.
                    issued = visible_sources()
                    sources = [s for s in sources if s in issued]
                    if not sources:
                        self.unverified_quote_parts.add(i)
                        changes[i] = '〔Core 日期核对〕当前可见原文不能核定这段引文的日期。\n'
                        break
                actual=next(iter(dates));reference=sources[0][2]
                changes[i]=(f'〔Core 证据校正〕本轮已核对的引文「{quote}」对应原文记录日期为 {actual}（{reference}）；'
                            f'当前依据不能把它标成 {day}。这是消息记录日期，不据此推定事实发生时间。\n')
                break
        return changes

    def observe(self, name, value, arguments=None):
        if name in ('set_timer','settime'):
            # Even an ambiguous current creation must not be labelled unowned
            # merely because the persisted request pointer is empty.
            self.created_here = True
            self.timers.clear()
            return
        if name in ('timer_cancel','timer_resolve'):
            self.timers.clear()
            return
        if name in ('attention_update','attention_checkpoint'):
            # A rejected malformed call has no mutation receipt. Do not erase
            # already verified links merely because an update was attempted.
            if isinstance(value, dict): self.links.clear()
            return
        if not isinstance(value, dict): return
        if name == 'timer_read' and isinstance(value.get('scheduled'), list) and isinstance(value.get('pending'), list):
            rows = value['scheduled'] + value['pending']
            self.timers = {}
            for row in rows[:33]:
                if isinstance(row, dict) and isinstance(row.get('id'), str):
                    self.timers[row['id']] = (row.get('creation_evidence', {}), finite_due(row.get('when')))
        elif name == 'timer_history' and isinstance(value.get('receipts'), list):
            key = arguments.get('id') if isinstance(arguments, dict) else None
            if isinstance(key, str) and key:
                old = self.timers.get(key, ({}, None))
                if len(self.timers) < 32 or key in self.timers:
                    self.timers[key] = (value.get('creation_evidence', {}), old[1])
        elif name == 'attention_read' and isinstance(value.get('items'), list):
            self.links = {}
            for item in value['items'][:33]:
                evidence = item.get('checkpoint_evidence', {})
                if (item.get('origin') == 'host_checkpoint' and evidence.get('status') == 'exact_tool_records'
                        and evidence.get('links') and evidence.get('business_success_proven') is False):
                    self.links[item['id']] = evidence

    def corrections(self, parts, *, quote_issues=None, visible_sources=None):
        # Never bind a pronoun to one of several inspected reminders/items.
        uncertain_source = False
        finite = False
        if len(self.timers) == 1:
            context, due = next(iter(self.timers.values()))
            uncertain_source = (not self.created_here and isinstance(context, dict)
                and context.get('creator') == 'unknown' and context.get('original_request_ref') is None)
            finite = due is not None
        verified_link = len(self.links) == 1
        changes = self.dated_quote_corrections(parts, quote_issues=quote_issues, visible_sources=visible_sources); fenced = False
        if len(self.timers) == 1:
            from .field_claims import corrections as field_corrections
            changes.update(field_corrections(parts, context))
        from .source_attribution import corrections as source_corrections
        changes.update(source_corrections(parts, self.event_sources))
        from .timer_chronology import corrections as chronology_corrections
        changes.update(chronology_corrections(parts, self.timers, self.event_times))
        if finite:
            from .finite_date_proposals import corrections as proposal_corrections
            changes.update(proposal_corrections(parts, TIME_FINITE))
        if uncertain_source and context.get('creation_reason', 'unknown') == 'unknown':
            from .reminder_intent import corrections as intent_corrections
            changes.update(intent_corrections(parts, due=due))
        for i, part in enumerate(parts):
            if '```' in part:
                if part.count('```') % 2: fenced = not fenced
                continue
            if fenced or part.lstrip().startswith('>') or re.search('[?？]', part): continue
            if (finite and re.search(r'(?:近于|相当于|等于)[^。！？\n]{0,10}[「“][^」”]{0,20}(?:不会响|不会叫醒|永不到期)[」”]', part)
                    and not re.search('不等于|不代表|不是|并非|如果|假如|是否|可能|不能|无法|引用|举例|原话', part)):
                changes[i] = TIME_FINITE + '\n'
                continue
            plain = re.sub(r'[“「『"][^”」』"]*[”」』"]', '', part)
            conditional = False
            for clause in re.split(r'[，,；;]', plain):
                conditional = conditional or bool(re.search('如果|假如|即使|除非', clause))
                if conditional or re.search('原话|原文|举例|例如|这种说法|这句话|他说|她说|你说|另一|其他|别的|担心|害怕|希望|承诺|决定|打算', clause): continue
                if re.search('不能|不应|不可|无法|不确定|不知道|尚未核实|不代表|不等于|未必|是否|可能', clause): continue
                # Missing provenance supports neither attribution nor its
                # converse. Restrict this to creation/source assertions; an
                # unrelated reading agreement is not a creation claim.
                denied_source = re.search(
                    r'(?:不是|并非)(?:由)?你(?:的)?(?:某句(?:话)?|哪句(?:话)?|之前|当时|曾经)?(?:要求|委托|让|叫|交代)(?:我)?(?:设|建|安排)'
                    r'|(?:不是|并非)(?:这条|该条|这个)(?:提醒)?的(?:出处|来源)'
                    r'|(?:不是|并非)(?:这条|该条|这个)(?:要求|请求|委托)(?:的)?(?:产物|结果)'
                    r'|(?:不是|并非)(?:由)?(?:这条|该条|这个)(?:要求|请求|委托)(?:所)?(?:产生|派生|造成)(?:的)?', clause)
                if uncertain_source and denied_source and not re.search(r'没有(?:任何)?(?:证据|依据)(?:表明|证明|支持|说明)|不能确定', clause[:denied_source.start()]):
                    changes[i] = SOURCE_UNKNOWN + '\n'; break
                if uncertain_source and not re.search(r'(?:不是|并非)\s*你', clause) and re.search(r'你(?:之前|当时|曾经)?(?:托付|委托|交代|要求|托)(?:给我|我)?的[^。！？\n]{0,40}(?:约定|提醒|事项|安排)', clause):
                    changes[i] = SOURCE_UNKNOWN + '\n'; break
                if finite and not re.search(r'(?:不是|并非)\s*永', clause) and re.search(r'(?:永远|永不|永不会|不会再有到期)[^。！？\n]{0,24}(?:到期|唤醒|叫醒|触发|提醒|响)|(?:永不到期|永不触发)', clause):
                    changes[i] = TIME_FINITE + '\n'; break
                if not re.search('因果|业务成功|完成证明|语义', clause) and (re.search(r'(?:(?<!有)没有|并无|不存在|(?<!有)无)[^。！？\n]{0,12}(?:关联|联系)|(?<!是)不是同一(?:件事|笔|调用)|(?<!属)不属于同一', clause)
                        and re.search(r'检查点|关注|这笔|书签|同一(?:件事|笔|调用)', plain)):
                    explicit_checkpoint = re.search(r'检查点|关注|书签', plain)
                    if not explicit_checkpoint and not verified_link:
                        if uncertain_source and '提醒' in plain:
                            changes[i] = SOURCE_UNKNOWN + '\n'; break
                    changes[i] = (LINK_VERIFIED if verified_link else LINK_UNVERIFIED) + '\n'; break
        if len(self.timers) == 1:
            from .reminder_certainty import corrections as certainty_corrections
            changes.update(certainty_corrections(parts, context, finite=finite,
                           created_here=self.created_here, originals=self.originals))
        for i in changes:
            changes[i] = ''.join(token for token in ('**','__') if parts[i].count(token)%2) + changes[i]
        return changes
