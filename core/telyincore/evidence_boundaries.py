"""Known absence/unknown distinctions grounded in observed reminder receipts.

These bounded checks do not attempt general entailment or infer user intent.
Quoted examples, questions, explicit uncertainty and scoped negative searches
are preserved. No model call or tool execution occurs here.
"""
import re

CREATION_UNKNOWN = '〔Core 证据校正〕创建工具引用缺失只说明未保存定位；创建回执是否存在尚未核实。'
HISTORY_UNKNOWN = '〔Core 证据校正〕当前处理记录只覆盖可核对范围，不能据此断言过去从未触发、发声或撤销；具体发生情况需按对应回执确认。'


def history_retry_feedback(parts, corrections):
    """Quote only the current draft; correction rules remain the source of verdicts."""
    from .evidence_relations import LINK_UNVERIFIED, SOURCE_UNKNOWN
    hints = {
        HISTORY_UNKNOWN: '请按本批同一编号的回执写当前状态或已列出的阶段。局部查询的数量限于查询范围，过去是否发生仍需相应依据。',
        CREATION_UNKNOWN: '若本批同一编号的回执列出创建字段，可直接引用字段和值。定位缺失时，创建回执是否存在仍待确认。',
        SOURCE_UNKNOWN: '可沿用本批同一编号的已知来源字段。没有明确委托原文时，委托关系仍待确认。',
        LINK_UNVERIFIED: '可以分别说明两条记录已核实的内容，把它们是否相关留作待确认。已有记录足以说明这些边界。',
    }
    excerpts = []
    reasons = []
    for index, replacement in corrections.items():
        rules = [hint for marker, hint in hints.items() if marker in replacement]
        if not rules:
            continue
        part = parts[index].strip()
        if len(part) > 120:
            part = part[:50] + '…' + part[-69:]
        excerpts.append(part)
        for rule in rules:
            if rule not in reasons:
                reasons.append(rule)
        if len(excerpts) == 2:
            break
    if not excerpts:
        return ''
    import json
    return '待调整的草稿片段：' + json.dumps(excerpts, ensure_ascii=False) + '。' + ''.join(reasons)

# late_seconds describes detection lag in the reminder tool contract. Exclude
# only the negative definition of its unit, never the surrounding claims.
_LATENCY_ACTION = r'(?:唤醒|发出|收到|读到|送达)'
_LATENCY_DEFINITION = re.compile(
    r'(?:不是|并非)(?:实际)?' + _LATENCY_ACTION +
    r'(?:(?:、|或|和)(?:实际)?' + _LATENCY_ACTION + r')*的(?:延迟|耗时)'
    r'(?=$|[，,；;。！？!?\s]|所以|因此)')
_INFERRED_NONRECEIPT = re.compile(
    r'(?:所以|因此|可见|也就是说)(?:你|您|他|她|对方|用户)?(?:并未|没有|没|未)(?:曾)?收到')


# Do not equate an unlinked creation record or empty history page with a
# worldwide absence proof. Collect only genuine successful tool JSON output.
def observe(name, value, facts):
    if not isinstance(value, dict):
        return
    if name == 'timer_read' and isinstance(value.get('scheduled'), list) and isinstance(value.get('pending'), list):
        contexts = [r.get('creation_evidence') for r in value['scheduled'][:256] + value['pending'][:100] if isinstance(r, dict)]
    elif name == 'timer_history' and isinstance(value.get('receipts'), list):
        contexts = [value.get('creation_evidence')]
        facts.add('bounded_history')
    else:
        return
    if any(isinstance(c, dict) and (('creation_tool_ref' in c and c['creation_tool_ref'] is None)
                                   or c.get('origin_linkage') == 'not_recorded') for c in contexts):
        facts.add('creation_locator_missing')


def corrections(parts, facts):
    if not facts:
        return {}
    from .reminder_read_proof import checked_statement, field_observations
    creation = re.compile(r'(?:没有|也没|不存在)(?P<bridge>[^，,；;。！？?\n]{0,16})创建[^，,；;。！？?\n]{0,16}(?:回执|收据)')
    creation_after = re.compile(r'创建[^，,；;。！？?\n]{0,16}(?:回执|收据)\s*(?:也|还|仍|并|都|均)?(?:不存在|(?:没有|没|未)(?:被)?(?:保留|保存|落盘|留存|留档))')
    non_absence = re.compile(r'必要|理由|证据|证明|依据|确认|核实|核对|说过|否认|声称|要求|寻找|查找|成功|失败')
    historical = re.compile(r'(?:从未|从没(?:有)?|从来没(?:有)?|未曾|不曾|一次都(?:还)?没(?:有)?|连一次都(?:还)?没(?:有)?)[^，,；;。！？?\n]{0,24}(?:触发|唤醒|叫醒|响过|响|提醒过|发出|发声|送达|通知过|撤销|取消|timer_cancel)|(?:撤销(?:这件事)?(?:本身)?|触发(?:这件事)?)(?:就)?(?:没有|没)发生')
    negative_action = re.compile(r'(?:没有|没|并未|尚未|未|不是|并非)(?P<bridge>[^，,；;。！？?\n]{0,20}?)(?:触发|唤醒|叫醒|发声|发出(?:过)?(?:提醒|通知)|提醒过|送达|通知过|撤销|取消|timer_cancel|响)(?:过)?`?')
    named_tool_absence = re.compile(r'`?(?:timer_cancel|timer_resolve|set_timer|settime)`?\s*(?:从未|从没(?:有)?|未曾|不曾|没有|没|未)(?:被)?(?:调用|执行)(?:过)?')
    not_event_absence = re.compile(r'证据|证明|依据|确认|核实|核对|记录|回执|必要|理由|计划|打算|意图|权限|能力|要求|否认|说过|声称')
    record_suffix = re.compile(r'^(?:你|您|我|自己|她|他|它)?(?:的)?(?:记录|回执|证据|信息|迹象|条目|日志|打算|意图|必要|理由|权限|能力)')
    uncertain = re.compile(r'没有(?:任何)?(?:证据|依据)(?:表明|证明|支持|说明|确认)|不能|无法|不知道|不清楚|不确定|不表示|不代表|不证明|不等于|不意味着|未必|不一定|是否|有没有|可能|如果|假如|即使|原话(?:是|为|说)|举例|例如|你说|用户说|这句话|这种说法')
    scoped = re.compile(r'本批|本轮|本次|本页|这次|当前|现有可读记录|可见范围|(?:这份|该页|这些)(?:记录|回执)|查到|读到|看到|找到|定位到|可(?:供)?(?:核对|核实|指认|定位)|未能')
    bundled_creation = re.compile(r'创建[^，,；;。！？?\n]{0,16}(?:回执|收据)[^。！？?\n]{0,40}(?:这两(?:样|项)|两者|二者)(?:现在|目前)?(?:都|均)(?:没有|不存在|缺失)')
    changes = {}
    fenced = False
    last_latency_definition = -2
    for i, part in enumerate(parts):
        if '```' in part:
            if part.count('```') % 2:
                fenced = not fenced
            continue
        if fenced or part.lstrip().startswith('>') or re.search(r'[?？]', part):
            continue
        if checked_statement(part, facts, preceding=parts[i-1] if i else ''):
            continue
        # A quoted complete proposition is an example/quotation, not a host
        # assertion. Backticks around field names do not hide adjacent prose.
        normalized = field_observations(part, facts, parts[i-1] if i else '')
        def unquote(match):
            # Negated predicates in quotation marks still assert an event
            # claim (不是「已经提醒过」); attributed quotations remain data.
            prefix = normalized[:match.start()]
            return match.group()[1:-1] if re.search(r'(?:不是|并非|没有)\s*$', prefix) else ''
        plain = re.sub(r'[“「『][^”」』]*[”」』]', unquote, normalized)
        if ('bounded_history' in facts and 'late_seconds' in plain and '检测' in plain
                and re.search(r'预定|due_at', plain)):
            plain, definitions = _LATENCY_DEFINITION.subn('指标口径', plain)
            if definitions:
                last_latency_definition = i
        bundled = bundled_creation.search(plain)
        if ('creation_locator_missing' in facts and bundled
                and not uncertain.search(plain[:bundled.end()])
                and not scoped.search(plain[:bundled.start()])):
            changes[i] = CREATION_UNKNOWN + '\n'
            continue
        # In a rhetorical heading, 有没有 is a question but its explicit
        # answer 没有 is still an assertion. Do not exempt that answer.
        answered_negative = re.search(r'有没有(?:撤销|取消|触发|唤醒|送达)[^，,；;。！？?\n]{0,4}[—:：]+\s*没有', plain)
        answered_negative = answered_negative or re.search(
            r'(?:撤销|取消|触发|唤醒|送达)(?:这件事)?\s*[—:：]+\s*'
            r'(?:没有这回事|没有(?:发生(?:过)?)?|未发生(?:过)?)(?=$|[。！？\s*])', plain)
        if ('bounded_history' in facts and answered_negative
                and not scoped.search(plain[:answered_negative.start()])
                and not re.search(r'如果|假如|例如|举例|不能|你说|原话', plain[:answered_negative.start()])):
            changes[i] = HISTORY_UNKNOWN + '\n'
            continue
        conditional = False
        for clause in _clauses(plain, scoped, uncertain):
            # A later mention of “你说” or an unrelated uncertainty must not
            # exempt an earlier independent historical assertion.
            conditional = conditional or bool(re.search(r'如果|假如|即使', clause))
            if conditional or re.search(r'原文(?:是|为)|原话(?:是|为|说)|举例|例如|他说|她说', clause):
                continue
            if uncertain.search(clause):
                continue
            absent_creation = (any(not non_absence.search(m.group('bridge')) for m in creation.finditer(clause))
                               or creation_after.search(clause))
            if 'creation_locator_missing' in facts and absent_creation and not scoped.search(clause):
                changes[i] = CREATION_UNKNOWN + '\n'
                break
            # A bounded negative search is not a negative event claim. Keep
            # current-state descriptions and lack-of-record statements intact.
            implicit_history = any(not not_event_absence.search(m.group('bridge'))
                                   and not record_suffix.match(clause[m.end():].lstrip())
                                   for m in negative_action.finditer(clause))
            inverse_tool_history = any(not record_suffix.match(clause[m.end():].lstrip())
                                       for m in named_tool_absence.finditer(clause))
            inferred_nonreceipt = i - last_latency_definition <= 1 and _INFERRED_NONRECEIPT.search(clause)
            if 'bounded_history' in facts and (historical.search(clause) or inferred_nonreceipt
                    or ((implicit_history or inverse_tool_history) and not scoped.search(clause))):
                changes[i] = HISTORY_UNKNOWN + '\n'
                break
    from .absence_inference import corrections as discourse_corrections
    changes.update(discourse_corrections(parts, facts, history_unknown=HISTORY_UNKNOWN, creation_unknown=CREATION_UNKNOWN))
    # A sentence boundary can lie inside a Markdown emphasis span. Preserve
    # unmatched boundary delimiters when replacing a segment; paired emphasis
    # wholly inside the removed assertion needs no replacement.
    for i in changes:
        markers = ''.join(token for token in ('**', '__') if parts[i].count(token) % 2)
        changes[i] = markers + changes[i]
    return changes


def _clauses(text, scoped, uncertain):
    """A list's later parenthetical cannot qualify an earlier assertion.

    Share a leading scope/uncertainty only when it precedes the first action,
    as in 本轮没触发、没发声. A later historical/contrast cue starts its own
    claim. This is local clause structure, not a general semantic parser.
    """
    for group in re.split(r'[，,；;]', text):
        items = group.split('、')
        first = items[0]
        yield first
        action = re.search(r'创建|触发|唤醒|叫醒|发声|发出|提醒|送达|通知|撤销|取消|timer_cancel|响|到期|到点', first)
        prefix = first[:action.start()] if action else ''
        shared = prefix if scoped.search(prefix) or uncertain.search(prefix) else ''
        for item in items[1:]:
            independent = re.search(r'历史|过去|以前|曾经|从前|至今|一直|从未|从没|但|却|不过|然而', item)
            yield ('' if independent else shared) + item
