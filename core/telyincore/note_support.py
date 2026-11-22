"""Literal reading-progress coverage, not general semantic entailment.

Only typed originals from current visible durable tool results can support
these finite assertions. The normal attention handler revalidates selected
references and invokes this check again inside its commit transaction.
"""
import json
import re
from .original_evidence import PortraitSourceText, is_original_receipt

_NUMBER = r'(第?[一二三四五六七八九十百零两0-9]{1,8}章)'
_FORWARD = re.compile(r'(?:只(?:核到|确认到|读完|读了|读到)|已确认|已核实|确认到|核实到|读完|读了|读到)\s*'+_NUMBER)
_BACKWARD = re.compile(_NUMBER+r'(?:已经|已)?(?:读完|读过)')
_HYPOTHESIS = re.compile(r'是否|有没有|不能(?:说|确认|断言)|不确定|不代表|不等于|没有证据|尚未(?:确认|核实)|待(?:确认|核实)|尚待|如果|假如|打算|希望|计划|询问|问问|要不要|(?:吗|么)[？?。！!]?\s*$|[？?]')

def claims(text):
    found=set()
    if not isinstance(text,str):return found
    for segment in re.finditer(r'[^，,；;。！？!?\n]+[！？!?]?',text):
        clause=segment.group()
        # A review-state label is not uncertainty about an explicit factual
        # assertion that follows it. Preserve actual questions/hypotheses.
        label = re.match(r'^\s*(?:待核实|待确认|待复核)[：:]\s*', clause)
        if label and re.search(r'已读原话|已确认|已核实|确认到|核实到', clause[label.end():]):
            clause = clause[label.end():]
        if _HYPOTHESIS.search(clause):continue
        for pattern in (_FORWARD,_BACKWARD):
            for match in pattern.finditer(clause):
                prefix=clause[:match.start()]
                if re.search(r'(?:要|想|会|准备)\s*$',prefix):continue
                negative=bool(re.search(r'(?:没(?:有)?|未|不曾|不)\s*$',prefix))
                anchor=match[1].replace('第1章','第一章')
                # The original assertion "只读完第一章" also supports the
                # narrower progress label, not arbitrary chapter/count aliases.
                level='completed' if re.search(r'读完|读过',match.group()) else 'progress'
                found.add((anchor,negative,level))
    return found


def originals(console, visibility=None, *, for_reading_claim=False, limit=64):
    guard=getattr(console,'reply_evidence_guard',None)
    reader=getattr(getattr(console,'_request_session',None),'tool_evidence_snapshot',None)
    if guard is None or not callable(reader):return []
    guard.bind();marker=guard.receipt_marker
    if marker is None:return []
    epoch,_,entries=reader()
    if epoch is not marker[0]:return []
    out=[]
    for position,row,reference in entries:
        if position <= marker[1] or row.kind != 'function_result' or not is_original_receipt(row.name, row.text):
            continue
        if visibility is not None and not (visibility.administrator or visibility.permits(row.scope)):continue
        portrait = isinstance(row.text, PortraitSourceText)
        if portrait:
            if visibility is None and row.text.source_scope != row.scope:
                continue
            if visibility is not None and not visibility.permits(row.text.source_scope):
                continue
            if for_reading_claim and not row.text.single_source:
                continue
        for date,text,source in row.text.originals:
            if not text or text not in row.text:continue
            if portrait and for_reading_claim:
                statements = claims(text)
                if any((chapter, not negative, level) in statements for chapter, negative, level in statements):
                    continue
            out.append({'result_ref':reference,'source_ref':source,'date':date,'text':text})
    return out[-limit:]


def receipt_facts(console, visibility):
    from .evidence_boundaries import observe
    guard = getattr(console, 'reply_evidence_guard', None)
    reader = getattr(getattr(console, '_request_session', None), 'tool_evidence_snapshot', None)
    facts = set()
    if guard is None or not callable(reader): return facts
    guard.bind(); marker = guard.receipt_marker
    if marker is None: return facts
    epoch, _, entries = reader()
    if epoch is not marker[0]: return facts
    for position, row, reference in entries:
        if position <= marker[1] or row.kind != 'function_result': continue
        if not (visibility.administrator or visibility.permits(row.scope)): continue
        if row.name not in ('timer_read', 'timer_history'): continue
        try: value = json.loads(row.text)
        except (ValueError, TypeError): continue
        observe(row.name, value, facts)
    return facts


def check(console,visibility,changes,references):
    from .evidence_boundaries import corrections
    from .reply_evidence import sentences
    facts = receipt_facts(console, visibility)
    for key in ('title', 'next_step', 'waiting_for'):
        value = changes.get(key, '')
        if isinstance(value, str) and corrections(sentences(value), facts):
            raise ValueError('关注 '+key+' 把尚未核实写成了未发生或没有回执。'
                             '请保留原文范围：本次未查到回执；过去是否触发、创建回执是否存在仍未核实。'
                             '该更新未保存，不要重复查查询回执来证明历史不存在。')
    required=set()
    for key in ('title','next_step','waiting_for'):
        required.update(claims(changes.get(key,'')))
    if not required:return {}
    sources=originals(console,visibility,for_reading_claim=True)
    def supports(item,claim):
        anchor,negative,level=claim
        return any(a==anchor and n==negative and (level=='progress' or l=='completed')
                   for a,n,l in claims(item['text']))
    missing=[];candidates=[]
    for claim in sorted(required):
        choices=[s for s in sources if supports(s,claim)]
        if not any(s['result_ref'] in references or (s['source_ref'].startswith('event:') and s['source_ref'] in references) for s in choices):
            missing.append(claim[0])
            candidates.extend(s['result_ref'] for s in choices)
    if missing:
        choices=list(dict.fromkeys(candidates))[:8]
        raise ValueError('关注中的阅读进度未由所选用户原文覆盖：'+','.join(missing)+
                         '。请引用实际更正/进度原文的工具结果，不用提问或查询回执代替；也可改为尚待核实的意向。'+
                         (' 本批候选结果定位：'+', '.join(choices) if choices else ' 请先按当前权限读取对应原文。'))
    return {'evidence_coverage':'literal_reading_claims_matched','checked_reading_claims':len(required),
            'coverage_boundary':'仅核对已实现阅读进度形式与所选原文中的字面内容，不证明任意语义、对象关联或现实真伪。'}
