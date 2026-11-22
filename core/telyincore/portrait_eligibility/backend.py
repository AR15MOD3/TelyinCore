"""Explicit native transport adapter; host creates/owns the stream context.

stream_factory(path, body) must return an async context manager yielding raw
SSE byte chunks. No URLs, credentials, provider selection or retry here.
"""
import asyncio
from types import SimpleNamespace as NS
from ._frozen.contracts import Refused
from .eligibility_contract import MEMORY_KINDS
from ._frozen.forced_chat_codec import PROFILE,wire_bytes,chat_events,ChatToolAccumulator
from ._frozen.provider_adapter import strict_events,ResponseAccumulator

class NativeStreamBackend:
    profile=PROFILE
    def __init__(self, *, model, stream_factory):
        if type(model) is not str or not model or not callable(stream_factory):
            raise TypeError('explicit_host_provider_required')
        self.model=model;self.stream_factory=stream_factory
    async def __call__(self,request):
        body=wire_bytes(request,self.model,admitted_kinds=MEMORY_KINDS)
        path='/v1/responses' if request.stage=='selector' else '/v1/chat/completions'
        async with self.stream_factory(path,body) as chunks:
            if request.stage=='proposal':
                acc=ChatToolAccumulator(self.model)
                async for event in chat_events(chunks):acc.feed(event)
                return acc.finish()
            acc=ResponseAccumulator()
            async for event in strict_events(chunks):
                status={'response.completed':'completed','response.incomplete':'incomplete','response.failed':'failed'}.get(event.get('type'))
                if status is not None and (type(event.get('response')) is not dict or event['response'].get('status')!=status):raise Refused('terminal_type_status_mismatch')
                acc.feed(event)
                if acc.terminated:break
            acc.finish()
            if acc.status!='completed' or acc.refusal:raise Refused('completion_not_accepted')
            announced=(acc._terminal.get('response') or {}).get('output') or []
            if [(x.get('call_id'),x.get('name'),x.get('arguments')) for x in announced if x.get('type')=='function_call']!=[(c.call_id,c.name,c.arguments) for c in acc.calls]:raise Refused('terminal_tool_calls_disagree')
            return NS(status=acc.status,text=acc.text,refusal=acc.refusal,calls=acc.calls,usage=acc.usage,attempts=1)
