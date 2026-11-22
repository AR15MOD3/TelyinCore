"""Versioned text-partition protocol. Never accepts/repairs numeric v2 output.

V3 models copy source fragments, while this module deterministically derives
their ordered Unicode-codepoint offsets before the unchanged v2 guard runs.
Raw v3 and derived numeric evidence are separate, content-bound artifacts.
"""
import hashlib,json
from dataclasses import dataclass
from types import SimpleNamespace as NS
from .contracts import Refused,Request,canonical,bounded_json,MAX_REQUEST_BYTES,MAX_AUDIT_RESPONSE_BYTES,MAX_SEGMENTS_PER_SOURCE
from .audited_admission import AuditedFlow,SYSTEM,ROLE_RELATIONS
V2='portrait-evidence-audit-v2'
V3='portrait-evidence-audit-v3-textspans'

def selector_system():
    pairs=[
      ('顶层只有input_sha256和decisions。',f'顶层只有protocol、input_sha256和decisions。protocol必须为{V3}。'),
      ('每段只有start、end、role、relation。','每段只有text、role、relation。'),
      ('start/end按Unicode码点计数，含标点，从0开始、右端不包含；第一段start=0，相邻end/start相等，最后end=char_length。源原文只保留在证据封套，勿在segments重写原文。',
       '每段text必须是原文当前位置起连续、非空且逐字相同的片段。按顺序拼接全部text必须与完整源text相等，保留空格、换行、标点和Unicode字符。不要输出或估算start/end；程序根据片段长度导出坐标。不改字、不跳过、不重复、不拼接非连续内容。正常JSON转义不改变解码后的文字。')]
    out=SYSTEM
    for old,new in pairs:
        if out.count(old)!=1:raise Refused('v3_selector_template_drift')
        out=out.replace(old,new)
    return out
V3_SYSTEM=selector_system()

def version_request(request,*,system=None):
    obj=request.read()
    if obj.get('protocol')!=V2:raise Refused('v3_internal_request_version')
    obj['protocol']=V3
    if system is not None:obj['messages'][0]['content']=system
    raw=canonical(obj)
    if len(raw.encode('utf-8'))>MAX_REQUEST_BYTES:raise Refused('v3_request_byte_budget')
    return Request(request.stage,raw,hashlib.sha256(raw.encode('utf-8')).hexdigest())

@dataclass(frozen=True)
class MaterializedAudit:
    raw_sha256:str
    source_sha256:str
    numeric_json:str
    numeric_sha256:str
    def read(self):
        if hashlib.sha256(self.numeric_json.encode('utf-8')).hexdigest()!=self.numeric_sha256:raise Refused('v3_materialization_digest')
        return bounded_json(self.numeric_json,MAX_AUDIT_RESPONSE_BYTES,'v3_derived_audit')
    def evidence(self):
        return dict(protocol=V3,origin='deterministic text-fragment materialization, not model-provided offsets',raw_sha256=self.raw_sha256,source_sha256=self.source_sha256,derived_sha256=self.numeric_sha256,derived=self.read())

def materialize(raw,envelope):
    obj=bounded_json(raw,MAX_AUDIT_RESPONSE_BYTES,'v3_audit')
    if set(obj)!={'protocol','input_sha256','decisions'} or obj['protocol']!=V3:raise Refused('v3_response_protocol_or_shape')
    if obj['input_sha256']!=envelope.sha256:raise Refused('v3_source_digest')
    sources=envelope.read();decisions=obj['decisions']
    if type(decisions) is not list or len(decisions)!=len(sources):raise Refused('v3_decision_coverage')
    derived=[];seen=set()
    for d in decisions:
        if type(d) is not dict or set(d)!={'source_index','author','decision','kind','quote','self_report','segments'}:raise Refused('v3_decision_shape')
        i=d['source_index']
        if type(i) is not int or not 0<=i<len(sources) or i in seen:raise Refused('v3_source_index')
        seen.add(i);source=sources[i]
        if type(d['author']) is not str or d['author']!=source['author']:raise Refused('v3_author_binding')
        parts=d['segments'];pos=0;spans=[]
        if type(parts) is not list or not 1<=len(parts)<=MAX_SEGMENTS_PER_SOURCE:raise Refused('v3_segment_count')
        for part in parts:
            if type(part) is not dict or set(part)!={'text','role','relation'}:raise Refused('v3_text_segment_shape')
            text=part['text'];role=part['role'];relation=part['relation']
            if type(text) is not str or not text:raise Refused('v3_empty_or_nontext_segment')
            if type(role) is not str or role not in ROLE_RELATIONS or type(relation) is not str or relation not in ROLE_RELATIONS[role]:raise Refused('v3_role_relation')
            end=pos+len(text)
            if source['text'][pos:end]!=text:raise Refused('v3_exact_ordered_partition')
            spans.append(dict(start=pos,end=end,role=role,relation=relation));pos=end
        if pos!=len(source['text']):raise Refused('v3_incomplete_partition')
        derived.append({**d,'segments':spans})
    numeric=canonical(dict(input_sha256=envelope.sha256,decisions=derived))
    bounded_json(numeric,MAX_AUDIT_RESPONSE_BYTES,'v3_derived_audit')
    return MaterializedAudit(hashlib.sha256(raw.encode('utf-8')).hexdigest(),envelope.sha256,numeric,hashlib.sha256(numeric.encode('utf-8')).hexdigest())

class TextSpanFlowV3:
    def __init__(self,sources,*,rule='',scaffold='',admitted_kinds=None):
        self._base=AuditedFlow(sources,rule=rule,scaffold=scaffold,admitted_kinds=admitted_kinds)
        self.raw_selection_v3=None;self.materialization=None
    def __getattr__(self,name):return getattr(self._base,name)
    @property
    def state(self):return self._base.state
    @state.setter
    def state(self,value):self._base.state=value
    @property
    def reason(self):return self._base.reason
    @reason.setter
    def reason(self,value):self._base.reason=value
    @property
    def raw_selection(self):return self.raw_selection_v3
    def selection_request(self):
        if self.state!='new':raise Refused('v3_selector_once')
        # Every fragment partition must reproduce every source character. This
        # necessary lower bound is not a tokenizer estimate or a fit guarantee.
        minimum=sum(len(canonical(s['text']).encode('utf-8')) for s in self.envelope.read())
        if minimum>MAX_AUDIT_RESPONSE_BYTES:
            self.state='stopped';self.reason='v3_capacity_impossible'
            raise Refused('v3_source_copy_exceeds_response_capacity')
        return version_request(self._base.selection_request(),system=V3_SYSTEM)
    def receive_selection(self,result):
        if self.state!='selecting':raise Refused('v3_selection_not_pending')
        self.raw_selection_v3=result.text
        try:
            if result.status!='completed' or result.refusal or type(result.attempts) is not int or result.attempts!=1 or result.calls:raise Refused('v3_selector_completion')
            self.materialization=materialize(result.text,self.envelope)
        except BaseException:
            self.state='stopped';self.reason='v3_materialization_invalid';raise
        # Explicit derived representation, not repaired/rewritten model output.
        derived=NS(status=result.status,text=self.materialization.numeric_json,refusal=result.refusal,calls=[],attempts=result.attempts)
        request=self._base.receive_selection(derived)
        return None if request is None else version_request(request)
    def receive_proposal(self,result):return self._base.receive_proposal(result)
    def evidence(self):
        return dict(protocol=V3,raw_selector_v3=self.raw_selection_v3,materialization=None if self.materialization is None else self.materialization.evidence(),guard='unchanged AuditedFlow v2',semantic_review_completed=False,tool_execution_allowed=False)
