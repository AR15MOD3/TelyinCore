"""Explicit V3 transport profile: Responses selector, forced native Chat proposal.
Only native delta.tool_calls are assembled. Content JSON is never rescued.
"""
import codecs,json
from urllib.parse import urlsplit
from types import SimpleNamespace as NS
from .contracts import Refused,canonical,bounded_json,byte_count,MAX_REQUEST_BYTES,MAX_PROPOSAL_ARGUMENT_BYTES
from .provider_adapter_v3 import wire_bytes as responses_wire
PROFILE='strata-chat-forced-profile-add-v1'
MAX_STREAM=262144
MAX_EVENT=65536
DONE=object()

def wire_bytes(request,model,*,admitted_kinds=None):
    original=responses_wire(request,model,admitted_kinds=admitted_kinds)  # unchanged envelope/source/schema checks
    if request.stage=='selector':return original
    if request.stage!='proposal':raise Refused('forced_chat_stage')
    validated=json.loads(original)
    if len(validated.get('tools',[]))!=1 or validated['tools'][0].get('name')!='profile_add':raise Refused('forced_chat_single_declared_tool')
    messages=[]
    for m in validated['input']:
        if len(m['content'])!=1 or m['content'][0]['type']!='input_text':raise Refused('forced_chat_text_messages_only')
        messages.append(dict(role=m['role'],content=m['content'][0]['text']))
    tools=[dict(type='function',function={k:v for k,v in t.items() if k!='type'}) for t in validated['tools']]
    out=dict(model=model,stream=True,max_tokens=2048,temperature=0,reasoning_effort='none',chat_template_kwargs=dict(enable_thinking=False),strata_mcp=False,messages=messages,tools=tools,tool_choice=dict(type='function',function=dict(name='profile_add')),stream_options=dict(include_usage=True))
    raw=canonical(out).encode('utf-8')
    if len(raw)>MAX_REQUEST_BYTES:raise Refused('forced_chat_wire_byte_budget')
    return raw

def endpoint_for(base,stage):
    # base is first validated by Permit; no second user-supplied URL is accepted.
    try:u=urlsplit(base);port=u.port
    except (ValueError,TypeError):raise Refused('forced_chat_endpoint_not_loopback') from None
    if u.scheme!='http' or u.hostname!='127.0.0.1' or u.username or u.password or u.query or u.fragment or not port or base!=f'http://127.0.0.1:{port}/v1/responses':raise Refused('forced_chat_endpoint_not_loopback')
    if stage=='selector':return base
    if stage!='proposal' or not base.endswith('/v1/responses'):raise Refused('forced_chat_endpoint_stage')
    return base[:-len('/responses')]+'/chat/completions'

async def chat_events(chunks):
    decoder=codecs.getincrementaldecoder('utf-8')('strict');buf='';pending_cr=False;first=True;total=0
    def normalize(s,final=False):
        nonlocal pending_cr,first
        out=[]
        for c in s:
            if first:
                first=False
                if c=='\ufeff':continue
            if pending_cr:
                out.append('\n');pending_cr=False
                if c=='\n':continue
            if c=='\r':pending_cr=True
            else:out.append(c)
        if final and pending_cr:out.append('\n');pending_cr=False
        return ''.join(out)
    def frame(value):
        if byte_count(value)>MAX_EVENT:raise Refused('chat_event_byte_budget')
        data=[];events=[]
        for line in value.split('\n'):
            if line.startswith(':'):continue
            k,sep,v=line.partition(':')
            if sep and v.startswith(' '):v=v[1:]
            if k=='data':data.append(v)
            elif k=='event':events.append(v)
        if len(events)>1 or events and events[0] not in ('','message'):raise Refused('chat_sse_event_namespace')
        if not data:return None
        text='\n'.join(data)
        return DONE if text=='[DONE]' else bounded_json(text,MAX_EVENT,'chat_chunk')
    async for chunk in chunks:
        if type(chunk) is not bytes:raise Refused('chat_sse_bytes_required')
        total+=len(chunk)
        if total>MAX_STREAM:raise Refused('chat_stream_byte_budget')
        buf+=normalize(decoder.decode(chunk))
        while '\n\n' in buf:
            block,buf=buf.split('\n\n',1);event=frame(block)
            if event is not None:yield event
        if byte_count(buf)>MAX_EVENT:raise Refused('chat_event_byte_budget')
    buf+=normalize(decoder.decode(b'',final=True),final=True)
    while '\n\n' in buf:
        block,buf=buf.split('\n\n',1);event=frame(block)
        if event is not None:yield event
    if buf.strip():raise Refused('chat_unterminated_sse_frame')

class ChatToolAccumulator:
    def __init__(self,model):
        self.model=model;self.response_id=None;self.created=None;self.finish_reason=None;self.done=False;self.slots=[];self.text='';self.reasoning='';self.usage=None
    def _usage(self,value):
        if value is None:return
        if self.usage is not None:raise Refused('chat_duplicate_usage')
        if type(value) is not dict:raise Refused('chat_usage_shape')
        for k in ('prompt_tokens','completion_tokens','total_tokens'):
            if type(value.get(k)) is not int or value[k]<0:raise Refused('chat_usage_shape')
        if value['total_tokens']!=value['prompt_tokens']+value['completion_tokens']:raise Refused('chat_usage_total')
        self.usage=value
    def feed(self,event):
        if self.done:raise Refused('chat_data_after_done')
        if event is DONE:
            if self.finish_reason!='tool_calls':raise Refused('chat_done_before_tool_finish')
            self.done=True;return
        if type(event) is not dict or event.get('error'):raise Refused('chat_provider_error')
        if event.get('object')!='chat.completion.chunk' or event.get('model')!=self.model:raise Refused('chat_chunk_identity')
        cid=event.get('id');created=event.get('created')
        if type(cid) is not str or not cid or byte_count(cid)>256 or type(created) is not int or created<0:raise Refused('chat_chunk_identity')
        if self.response_id is None:self.response_id=cid;self.created=created
        if cid!=self.response_id or created!=self.created:raise Refused('chat_chunk_identity_drift')
        if event.get('strata_mcp') or event.get('strata_rejected_tool_calls'):raise Refused('chat_external_or_rejected_tools')
        self._usage(event.get('usage'))
        choices=event.get('choices')
        if choices==[]:
            if self.finish_reason!='tool_calls' or event.get('usage') is None:raise Refused('chat_empty_choice')
            return
        if type(choices) is not list or len(choices)!=1:raise Refused('chat_choice_coverage')
        choice=choices[0]
        if type(choice) is not dict or type(choice.get('index')) is not int or choice['index']!=0:raise Refused('chat_choice_index')
        delta=choice.get('delta');finish=choice.get('finish_reason')
        if type(delta) is not dict or set(delta)-{'role','content','reasoning_content','refusal','tool_calls'}:raise Refused('chat_delta_shape')
        if self.finish_reason is not None:raise Refused('chat_chunk_after_finish')
        if 'role' in delta and delta['role']!='assistant':raise Refused('chat_role')
        for key in ('content','reasoning_content','refusal'):
            value=delta.get(key)
            if value is not None and type(value) is not str:raise Refused('chat_delta_text')
            if key=='refusal' and value:raise Refused('chat_refusal')
            if key=='content' and value:self.text+=value
            if key=='reasoning_content' and value:self.reasoning+=value
        tool_delta=delta.get('tool_calls',[])
        if type(tool_delta) is not list:raise Refused('chat_tool_delta_shape')
        for t in tool_delta:
            if type(t) is not dict or set(t)-{'index','id','type','function'}:raise Refused('chat_tool_delta_shape')
            index=t.get('index');fn=t.get('function')
            if type(index) is not int or not 0<=index<=1 or type(fn) is not dict or set(fn)-{'name','arguments'}:raise Refused('chat_tool_index_or_function')
            if index==len(self.slots):
                cid=t.get('id');name=fn.get('name')
                if t.get('type')!='function' or type(cid) is not str or not cid or byte_count(cid)>256 or any(s['call_id']==cid for s in self.slots) or name!='profile_add':raise Refused('chat_tool_initial_identity')
                self.slots.append(dict(call_id=cid,name=name,arguments=''))
            if index>=len(self.slots):raise Refused('chat_tool_index_gap')
            slot=self.slots[index]
            if 'id' in t and t['id']!=slot['call_id'] or 'type' in t and t['type']!='function' or 'name' in fn and fn['name']!=slot['name']:raise Refused('chat_tool_identity_drift')
            fragment=fn.get('arguments','')
            if type(fragment) is not str:raise Refused('chat_arguments_string')
            slot['arguments']+=fragment
            if byte_count(slot['arguments'])>MAX_PROPOSAL_ARGUMENT_BYTES:raise Refused('chat_argument_byte_budget')
        if finish is not None:
            if finish!='tool_calls' or not 1<=len(self.slots)<=2:raise Refused('chat_finish_not_native_tool_calls')
            for s in self.slots:bounded_json(s['arguments'],MAX_PROPOSAL_ARGUMENT_BYTES,'chat_tool_argument')
            self.finish_reason=finish
    def finish(self):
        if not self.done or self.finish_reason!='tool_calls':raise Refused('chat_missing_tool_terminal_or_done')
        return NS(status='completed',text=self.text,reasoning=self.reasoning,refusal='',calls=[NS(**s) for s in self.slots],usage=self.usage,attempts=1,transport_profile=PROFILE,provider_finish_reason=self.finish_reason,provider_response_id=self.response_id)
