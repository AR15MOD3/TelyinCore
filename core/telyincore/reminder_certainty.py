"""Keep independent reminder evidence dimensions independent in final prose.

Only a singular reminder observed through genuine current-batch receipts is
eligible. Qualifiers attach to their own clause, not all later conclusions.
This is bounded host validation, not semantic review or a closed-world claim.
"""
import re
from .evidence_boundaries import CREATION_UNKNOWN, HISTORY_UNKNOWN

BUSINESS_UNKNOWN = '〔Core 证据校正〕当前排期只给出到期时刻；检测、唤醒、送达与阅读完成是独立事实，不能合并断言“全都还没发生”。阅读结果须另按原话或业务回执核对。'
_RECEIPT = re.compile(r'创建(?:工具)?(?:的)?(?:回执|收据)(?:本身)?(?:也|还|仍|并)?(?:没有|没|未)(?:被)?(?:留存|留档|保存|保留|落盘)|(?:没有|没|未)(?:被)?(?:留存|留档|保存|保留|落盘)(?:过)?(?:任何)?创建(?:工具)?(?:的)?(?:回执|收据)')
_PAST = re.compile(r'(?:谈不上|算不上|不能算|不算)(?:已经|曾经|曾|已)?(?:向你|给你)?(?:提醒过|提醒到|发声过|通知过|送达过)|(?:因此|所以|也就是说)[^。！？\n]{0,18}(?:还没|没有|不曾)(?:向你|给你)?(?:提醒|通知|发声|送达)过')
_ALL = re.compile(r'(?:(?:全(?:部|都)?|所有|四(?:件|项)|这几(?:件|项)|一件|一项)(?:事)?(?:都|也)?|一(?:样|个)都)(?:还|尚|仍)?(?:没(?:有)?|未)(?:到|发生|完成|进行)')
_UNCERTAIN = re.compile(r'无法|不知道|不清楚|不确定|未核实|尚未确认|不代表|不证明|不意味着|不等于|不能(?:据此|说|认定|证明|推出|断言)|没有(?:任何)?(?:证据|依据)(?:表明|证明|支持|确认)|是否|有没有')
_ATTRIBUTED = re.compile(r'原话|原文|举例|例如|他说|她说|你说|另一|其他|别的')
_QUOTED = re.compile(r'[“「『][^”」』]*[”」』]|"[^"\n]*"')

def corrections(parts, context, *, finite, created_here, originals=()):
    receipt_unknown = (not created_here and isinstance(context,dict)
                       and context.get('creation_receipt_existence')=='not_established_by_creation_context'
                       and context.get('creation_tool_ref') is None)
    # Already-read user progress can support a compound negative. We do not
    # infer its subject/date here; ambiguity is reason to leave it alone.
    progress_read = any(re.search(r'(?:已(?:经)?|还没|没有|没|未)(?:读|看)完', row[1]) for row in originals)
    result={};fenced=False;recent=''
    for i,part in enumerate(parts):
        if not part.strip():recent='';continue
        if '\n\n' in part:recent=''
        if '```' in part:
            if part.count('```')%2:fenced=not fenced
            recent='';continue
        if fenced or part.lstrip().startswith('>'):recent='';continue
        plain=_QUOTED.sub('',part)
        if re.search('[?？]',plain) or _ATTRIBUTED.search(plain):recent='';continue
        qualified=False;found=[]
        context_text=(recent+plain)[-1200:]
        domains=sum(bool(re.search(word,context_text)) for word in ('到期|到点|检测','唤醒','送达'))
        reading=bool(re.search(r'(?:读|看)完|阅读完成',context_text))
        for clause in re.split('[，,；;]',plain):
            qualified=qualified or bool(re.search('如果|假如|假设|即使|除非',clause))
            if qualified or _UNCERTAIN.search(clause):continue
            if receipt_unknown and _RECEIPT.search(clause):
                # A literal missing locator is a different, supported fact.
                if not re.search('creation_tool_ref|引用|指针|定位|字段|查到|读到|找到|可见范围|本页',clause):found.append(CREATION_UNKNOWN)
            if finite and _PAST.search(clause):found.append(HISTORY_UNKNOWN)
            if finite and not progress_read and domains>=2 and reading and _ALL.search(clause):
                if not re.search(r'证据|回执|记录|确认|核实|前三|前两|不全|并非|不是|不必|不用|没必要',clause):found.append(BUSINESS_UNKNOWN)
        if found:result[i]='\n'.join(dict.fromkeys(found))+'\n'
        recent=(recent+plain)[-800:]
        if '\n\n' in part:recent=''
    return result
