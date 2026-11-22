"""Responses wire/SSE adapter closed over finite in-memory fixtures only.

No HTTP client, URL, socket, engine API, credentials or tool dispatcher exists.
This module is not authorization for a real transport.
"""
import asyncio,codecs,json,hashlib
from dataclasses import dataclass,field
from pathlib import Path
from types import SimpleNamespace as NS
from .contracts import (Refused,canonical,byte_count,bounded_json,EvidenceEnvelope,MAX_REQUEST_BYTES)
from .audited_admission import ROLE_RELATIONS,KINDS
from .stream_snapshot import ResponseAccumulator
MAX_SSE_BYTES=262144
MAX_SSE_EVENT_BYTES=65536
def _source_binding(data):
    sources=data.get('sources')
    if type(sources) is not list:raise Refused('wire_sources')
    if any(type(s) is not dict or set(s)!={'source_index','author','text','char_length'} or type(s['source_index']) is not int or s['source_index']!=i or type(s['char_length']) is not int or type(s['text']) is not str or s['char_length']!=len(s['text']) for i,s in enumerate(sources)):raise Refused('wire_source_fields')
    envelope=EvidenceEnvelope.build(sources)
    if sources!=envelope.read() or data.get('input_sha256')!=envelope.sha256:raise Refused('wire_envelope_binding')
    return sources
def _proposal_binding(data,sources,tools,*,admitted_kinds=None):
    nominations=data.get('nominations');audit=data.get('audit_spans')
    if type(nominations) is not list or not 1<=len(nominations)<=2 or type(audit) is not list or len(audit)!=len(sources):raise Refused('wire_nomination_audit_coverage')
    indexed={};authors=set()
    for n in nominations:
        if type(n) is not dict or set(n)!={'source_index','author','kind','start','end','quote'} or type(n['author']) is not str:raise Refused('wire_nomination_shape')
        i=n['source_index']
        if type(i) is not int or not 0<=i<len(sources) or i in indexed or n['author'] in authors:raise Refused('wire_nomination_index')
        s=sources[i]
        if n['author']!=s['author'] or type(n['kind']) is not str or n['kind'] not in (KINDS if admitted_kinds is None else admitted_kinds) or type(n['quote']) is not str:raise Refused('wire_nomination_binding')
        a=n['start'];b=n['end'];q=n['quote']
        if type(a) is not int or type(b) is not int or not 0<=a<b<=len(s['text']) or s['text'][a:b]!=q or s['text'].find(q)!=a or s['text'].find(q,a+1)>=0:raise Refused('wire_nomination_span')
        indexed[i]=n;authors.add(n['author'])
    seen=set()
    for a in audit:
        if type(a) is not dict or set(a)!={'source_index','author','self_report','spans'}:raise Refused('wire_audit_shape')
        i=a['source_index']
        if type(i) is not int or not 0<=i<len(sources) or i in seen:raise Refused('wire_audit_index')
        seen.add(i);s=sources[i]
        if a['author']!=s['author'] or a['self_report'] not in ('self','other','quoted'):raise Refused('wire_attribution')
        spans=a['spans'];pos=0
        if type(spans) is not list or not 1<=len(spans)<=16:raise Refused('wire_audit_segments')
        for span in spans:
            if type(span) is not dict or set(span)!={'start','end','role','relation'}:raise Refused('wire_audit_segment_shape')
            x=span['start'];y=span['end'];role=span['role'];relation=span['relation']
            if type(x) is not int or type(y) is not int or x!=pos or not x<y<=len(s['text']):raise Refused('wire_audit_partition')
            if type(role) is not str or role not in ROLE_RELATIONS or type(relation) is not str or relation not in ROLE_RELATIONS[role] or role=='unresolved':raise Refused('wire_audit_relation')
            pos=y
        if pos!=len(s['text']):raise Refused('wire_audit_partition')
        if i not in indexed:
            if any(x['role'] in ('claim','qualifier') for x in spans):raise Refused('wire_unselected_claim')
            continue
        n=indexed[i];x=n['start'];y=n['end']
        if a['self_report']!='self' or not any(t['role']=='claim' for t in spans):raise Refused('wire_not_self_claim')
        if any(t['role'] in ('claim','qualifier') and not x<=t['start']<t['end']<=y for t in spans):raise Refused('wire_scope_omitted')
        if any(t['role']=='quoted' and max(x,t['start'])<min(y,t['end']) for t in spans):raise Refused('wire_quote_attribution')
        if any(t['role']=='background' and max(x,t['start'])<min(y,t['end']) and any(ch.isalnum() for ch in s['text'][max(x,t['start']):min(y,t['end'])]) for t in spans):raise Refused('wire_unrelated_in_quote')
    expected=json.loads((Path(__file__).parent/'inputs/schema.json').read_text(encoding='utf-8'))
    expected['function']['parameters']['properties']['who']['enum']=sorted({s['author'] for s in sources})
    if tools!=[expected]:raise Refused('wire_tool_schema_binding')
def wire_bytes(request,model,*,admitted_kinds=None):
    if type(model) is not str or not 1<=byte_count(model)<=256:raise Refused('wire_model')
    b=request.read();stage=request.stage
    expected={'protocol','stage','max_output_tokens','messages'}|({'tools'} if stage=='proposal' else set())
    if stage not in ('selector','proposal') or set(b)!=expected or b['stage']!=stage or b['protocol']!='portrait-evidence-audit-v2' or type(b['max_output_tokens']) is not int or b['max_output_tokens']!=2048:raise Refused('wire_request_shape')
    messages=b['messages']
    if type(messages) is not list or len(messages)!=2 or any(type(m) is not dict or set(m)!={'role','content'} or type(m['content']) is not str for m in messages) or [m['role'] for m in messages]!=['system','user']:raise Refused('wire_messages')
    data=bounded_json(messages[1]['content'],MAX_REQUEST_BYTES,'wire_input');sources=_source_binding(data)
    if stage=='selector':
        if set(data)!={'input_sha256','sources'}:raise Refused('wire_selector_fields')
    else:
        if set(data)!={'input_sha256','sources','nominations','audit_spans'}:raise Refused('wire_proposal_fields')
        _proposal_binding(data,sources,b['tools'],admitted_kinds=admitted_kinds)
    out=dict(model=model,stream=True,max_output_tokens=2048,temperature=0,reasoning={'effort':'none'},input=[dict(type='message',role=m['role'],content=[dict(type='input_text',text=m['content'])]) for m in messages])
    if stage=='proposal':out['tools']=[dict(type='function',**t['function']) for t in b['tools']]
    raw=canonical(out).encode('utf-8')
    if len(raw)>MAX_REQUEST_BYTES:raise Refused('final_wire_byte_budget')
    return raw

async def strict_events(chunks):
    decoder=codecs.getincrementaldecoder('utf-8')('strict');buf='';pending_cr=False;first=True;total=0
    def normalize(s,final=False):
        nonlocal pending_cr,first
        out=[]
        for ch in s:
            if first:
                first=False
                if ch=='\ufeff':continue
            if pending_cr:
                out.append('\n');pending_cr=False
                if ch=='\n':continue
            if ch=='\r':pending_cr=True
            else:out.append(ch)
        if final and pending_cr:out.append('\n');pending_cr=False
        return ''.join(out)
    def frame(s):
        values=[];event_name=None
        for line in s.split('\n'):
            if line.startswith(':'):continue
            key,sep,value=line.partition(':')
            if sep and value.startswith(' '):value=value[1:]
            if key=='data':values.append(value)
            elif key=='event':event_name=value
        if not values:return None
        raw='\n'.join(values)
        if raw=='[DONE]':raise Refused('done_before_response_terminal')
        obj=bounded_json(raw,MAX_SSE_EVENT_BYTES,'sse_event')
        if event_name is not None and event_name!=obj.get('type'):raise Refused('sse_event_name_mismatch')
        if type(obj.get('sequence_number')) is not int:raise Refused('sse_sequence_type')
        return obj
    try:
        async for raw in chunks:
            if type(raw) is not bytes:raise Refused('sse_chunk_type')
            total+=len(raw)
            if total>MAX_SSE_BYTES:raise Refused('sse_total_byte_budget')
            buf+=normalize(decoder.decode(raw))
            while '\n\n' in buf:
                part,buf=buf.split('\n\n',1);event=frame(part)
                if event is not None:yield event
            if byte_count(buf)>MAX_SSE_EVENT_BYTES:raise Refused('sse_frame_byte_budget')
        buf+=normalize(decoder.decode(b'',final=True),final=True)
        if buf.strip():
            event=frame(buf)
            if event is not None:yield event
    except UnicodeError:raise Refused('sse_invalid_utf8') from None

@dataclass
class FixtureResponse:
    status:int
    parts:tuple
    closed:bool=False
    chunks_read:int=0
    async def chunks(self):
        for part in self.parts:
            self.chunks_read+=1;yield part
    async def aclose(self):self.closed=True
class FixtureTransport:
    def __init__(self,replies):self.replies=list(replies);self.requests=[];self.responses=[]
    async def exchange(self,body):
        self.requests.append(body)
        if not self.replies:raise Refused('fixture_exhausted_no_retry')
        status,parts=self.replies.pop(0)
        response=FixtureResponse(status,tuple(parts));self.responses.append(response);return response
class OfflineAdapter:
    def __init__(self,transport,model='offline-fixture'):
        if type(transport) is not FixtureTransport:raise Refused('fixture_transport_only')
        self.transport=transport;self.model=model;self.used=set();self.output_reserved=0;self.results=[];self.logs=[]
    async def invoke(self,request):
        body=wire_bytes(request,self.model)
        if request.stage in self.used or len(self.used)>=2:raise Refused('single_shot_stage_budget')
        self.used.add(request.stage);self.output_reserved+=2048
        log=dict(stage=request.stage,wire_bytes=len(body),wire_sha256=hashlib.sha256(body).hexdigest(),fixture_attempt=1,retries=0);self.logs.append(log)
        response=await self.transport.exchange(body);iterator=None;events=None
        try:
            if response.status!=200:raise Refused('fixture_http_status')
            acc=ResponseAccumulator();iterator=response.chunks();events=strict_events(iterator)
            async for event in events:
                terminal_status={'response.completed':'completed','response.incomplete':'incomplete','response.failed':'failed'}.get(event.get('type'))
                if terminal_status is not None:
                    response_data=event.get('response')
                    if type(response_data) is not dict or response_data.get('status')!=terminal_status:raise Refused('terminal_type_status_mismatch')
                acc.feed(event)
                if acc.terminated:break
            acc.finish()
            if acc.status=='completed':
                announced=(acc._terminal.get('response') or {}).get('output') or []
                final_calls=[(c.get('call_id'),c.get('name'),c.get('arguments')) for c in announced if c.get('type')=='function_call']
                accumulated=[(c.call_id,c.name,c.arguments) for c in acc.calls]
                if final_calls!=accumulated:raise Refused('terminal_tool_calls_disagree')
            result=NS(status=acc.status,text=acc.text,refusal=acc.refusal,calls=acc.calls,usage=acc.usage,attempts=1)
            self.results.append(result);return result
        finally:
            if events is not None:await events.aclose()
            if iterator is not None:await iterator.aclose()
            await response.aclose();log['response_closed']=response.closed;log['chunks_read']=response.chunks_read
async def run_fixture_pipeline(flow,adapter):
    if type(adapter) is not OfflineAdapter:raise Refused('offline_adapter_only')
    try:
        selected=await adapter.invoke(flow.selection_request())
        req=flow.receive_selection(selected)
        if req is None:return None
        return flow.receive_proposal(await adapter.invoke(req))
    except BaseException:
        flow.state='stopped';flow.reason='offline_adapter_or_contract_error';flow.events.append('fixture_pipeline_stopped_without_retry');raise
