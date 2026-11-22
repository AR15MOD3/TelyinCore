"""Bounded discourse checks for reminder absence, never a semantic reviewer.

Only successful current-batch receipt facts activate these checks. Local noun
antecedents identify what a draft assertion concerns; they never establish that
assertion as fact. No lookup, model call, or persistent state is introduced.
"""
import re

_ACTION = r'到期检测|到期|触发|唤醒|叫醒|发声|送达|通知|撤销|取消'
_RECEIPT = r'(?:成功)?创建(?:那一步的|动作的|工具)?(?:回执|收据)'
_LOCATOR = r'来源(?:指针|定位|引用)|原话(?:指针|定位|引用)|原始请求(?:指针|定位|引用)|original_request_ref|creation_tool_ref'
_LABELS = re.compile(r'(?:' + _ACTION + '|' + _RECEIPT + '|' + _LOCATOR + r')\Z')
_QUOTES = re.compile(r'“([^”\n]*)”|「([^」\n]*)」|『([^』\n]*)』|"([^"\n]*)"')
_NONASSERT = re.compile(r'不能|无法|不(?:代表|等于|证明|意味着)|未必|不一定|不确定|不知道|尚未核实|尚待核实|是否|有没有|可能|如果|假如|要是|即使|例如|举例|原话(?!指针|定位|引用)|原文|他说|她说|你说|用户说|这种说法|这句话|没有(?:任何)?(?:证据|依据)(?:表明|证明|支持|说明|确认)')
_EVENT_ABSENT = re.compile(r'(?:' + _ACTION + r')(?:这一档|这一步|这一环节|这个环节|这件事|操作|本身)?(?:也|并|都|均|就|还|仍)*(?:没有|没|未)(?:发生|进行|执行|出现)')
_CAUSE = re.compile(r'(?:所以|因此|于是|也就是说|说明|表明)[^。！？\n]{0,48}?(?:没(?:有)?|并未|未)(?:走到|进入|经历|经过|发生)(?:那|这|上述|后面|后续)(?:几步|些步骤|些环节|些流程|流程|步骤|环节|几项|一环节|一步)')
_PAIR_ABSENT = re.compile(r'(?:这两(?:样|项|个)|这(?:些|几项)|上述两项|两者|二者)(?:现在|目前)?(?:都|均)(?:没有|不存在|缺失|缺乏|缺)(?!少依据|少证据)')
_SCOPED_SEARCH = re.compile(r'本(?:批|轮|次|页)|当前页|(?:这份|该页|这些)记录|查到|读到|看到|找到|定位到|可(?:供)?(?:核对|核实|指认|定位)|未能')
_CREATION_SUCCESS_ABSENT = re.compile(r'(?:没有|没|不存在)(?:任何|一条|相关的|可用的)?成功创建(?:工具)?回执')


def _plain(text):
    # Quoted predicates remain quotations. Quoted *labels* in an assertion
    # ("撤销"这一档没有发生) are nouns, not a quoted proposition.
    return _QUOTES.sub(lambda m: (value if _LABELS.fullmatch(value) else ' ')
                       if (value := next(v for v in m.groups() if v is not None)) else ' ', text).replace('`', '')


def corrections(parts, facts, *, history_unknown, creation_unknown):
    if not facts or len(parts) > 4096:
        return {}
    changes = {}; history = []; fenced = None; blank_lines = 0
    for i, part in enumerate(parts):
        fence = re.match(r'^\s*(`{3,}|~{3,})', part)
        if fence:
            marker = fence[1]
            if fenced is None: fenced = marker
            elif marker[0] == fenced[0] and len(marker) >= len(fenced): fenced = None
            history.clear(); continue
        if fenced or part.lstrip().startswith('>'):
            history.clear(); continue
        if not part.strip():
            blank_lines += part.count('\n')
            if blank_lines >= 2 or (history and history[-1].endswith('\n')): history.clear()
            continue
        blank_lines = 0
        if re.match(r'^\s*(?:#{1,6}\s|\*\*[^*\n]+\*\*\s*$)',part): history.clear()
        plain = _plain(part)
        # Explanations of a quoted/conditional statement are not assertions.
        # Do not inherit uncertainty from a prior independent sentence.
        previous = ''.join(history)
        local = previous + plain
        conditional = False
        for segment in re.finditer(r'[^，,；;（）()]+', plain):
            clause = segment.group()
            offset = len(previous) + segment.start()
            conditional = conditional or bool(re.search(r'如果|假如|要是|即使',clause))
            if conditional or _NONASSERT.search(clause) or re.search(r'[?？]', clause): continue
            if 'bounded_history' in facts:
                if re.search(r'未(?:曾)?(?:触发|送达|唤醒)(?:过|的)?(?:排期|提醒)', clause):
                    changes[i] = history_unknown + '\n'; break
                absent = _EVENT_ABSENT.search(clause)
                # Current state remains expressible. A scoped search is also
                # distinct from a worldwide claim that an event never occurred.
                if absent and not re.search(r'本批|本轮|本次|本页|当前|目前|记录|回执|证据|条目', clause[:absent.start()]):
                    changes[i] = history_unknown + '\n'; break
                causal = _CAUSE.search(clause)
                if causal and re.search(_ACTION, local[:offset+causal.start()]):
                    changes[i] = history_unknown + '\n'; break
            if 'creation_locator_missing' in facts:
                if (re.search(r'(?:来源|原话)(?:仍|还|也)?无[^。；;]{0,40}(?:创建|工具)回执', clause)
                        and not _SCOPED_SEARCH.search(clause)):
                    changes[i] = creation_unknown + '\n'; break
                pair = _PAIR_ABSENT.search(clause)
                if pair:
                    before = local[:offset+pair.start()]
                    # Only an explicit local pair involving the source pointer
                    # and creation receipt activates this pronoun check.
                    if re.search(_LOCATOR, before) and re.search(_RECEIPT, before):
                        position = segment.start() + pair.start()
                        if not (_SCOPED_SEARCH.search(plain[:position]) or _NONASSERT.search(plain[:position])):
                            changes[i] = creation_unknown + '\n'; break
                absent = _CREATION_SUCCESS_ABSENT.search(clause)
                if absent and not _SCOPED_SEARCH.search(clause):
                    changes[i] = creation_unknown + '\n'; break
        # A small paragraph-local window; quoting data or changing paragraph
        # clears it. Prose is never stored in the authoritative receipt facts.
        history.append(plain[-600:])
        del history[:-2]
    return changes
