"""Local native Responses variant: supply transport ordinals when ALL are absent.

Never repair mixed/invalid/descending supplied sequence numbers, output text,
terminal status or response/item identity. The shared accumulator stays strict.
"""
import contextlib
import json

from .stream import ResponseProtocolError, iter_sse_events


async def local_response_events(chunks):
    mode = None
    ordinal = 0
    parser = iter_sse_events(chunks)
    async with contextlib.aclosing(parser):
        async for event in parser:
            if type(event) is not dict:
                raise ResponseProtocolError("local response event must be an object")
            supplied = "sequence_number" in event
            if mode is None:
                mode = supplied
            if supplied != mode:
                raise ResponseProtocolError("mixed local response sequence presence")
            if not supplied:
                event = {**event, "sequence_number": ordinal}
            ordinal += 1
            yield ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8")
