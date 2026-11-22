"""Bind a misspelled private text trigger to the same inbound message and sender."""
import json
import re
from collections.abc import Mapping
from .channel_admission import BatchIdentityBridge
from .snapshot_json import loads
from .stream import ToolCall


STOCK_PLAIN_TEXT = object()
_ID = re.compile(r'-?[1-9][0-9]{0,18}\Z')
_TARGET = re.compile(r'[1-9][0-9]{0,19}\Z')
_QUOTE = re.compile(r'引用|回引|引述|引回|[这那原]条?消息|消息气泡|回复.{0,4}[这那](?:一)?条|'
                   r'\bquot(?:e|ed|ing)|\breply\s+to\b', re.I)


class BoundPrivateText(ToolCall):
    # Only the standard three ToolCall fields are serialized. The handler is
    # a process-local identity checked against the dispatcher's frozen table.
    __slots__ = ('handler', 'lease')

    def __init__(self, call, arguments, handler, lease):
        super().__init__(call.call_id, call.name, arguments)
        object.__setattr__(self, 'handler', handler)
        object.__setattr__(self, 'lease', lease)


def bind_private_text(call, guard, config):
    console = guard.console
    lookup = getattr(console, '_qq_plain_text_handler', None)
    if not callable(lookup) or type(guard.owner) is not BatchIdentityBridge:
        return None
    handler = lookup()
    if getattr(handler, '__qq_plain_text__', None) is not STOCK_PLAIN_TEXT:
        return None
    schema = next((row.get('parameters') for row in config.tools
                   if isinstance(row, Mapping) and row.get('name') == call.name), None)
    if (not isinstance(schema, Mapping) or schema.get('additionalProperties') is not False
            or schema.get('required') != ['scope','target','text']
            or not isinstance(schema.get('properties'), Mapping)
            or set(schema['properties']) != {'scope','target','text','trigger_message_id'}):
        return None
    try:
        args = loads(call.arguments)
    except (TypeError, ValueError):
        return None
    if not isinstance(args, dict) or set(args) != {'scope','target','text','message_id'}:
        return None
    target, raw = args['target'], args['message_id']
    if (args['scope'] != 'private' or not isinstance(target, str) or not _TARGET.fullmatch(target)
            or type(raw) not in (str,int) or not _ID.fullmatch(str(raw))
            or not isinstance(args['text'], str) or not args['text'].strip()):
        return None
    batch = console.original_batch()
    if len(batch) != 1:
        return None
    event, = batch
    meta = event.meta
    if (event.type != 'message' or event.source != 'qq' or event.origin != 'external'
            or event.sender_key != 'qq:' + target or not isinstance(meta, dict)
            or meta.get('conversation') != 'private:' + target
            or type(meta.get('message_id')) not in (str,int) or str(meta['message_id']) != str(raw)
            or not isinstance(event.text, str) or _QUOTE.search(event.text)):
        return None
    if guard._earlier_qq_text('private', target):
        return None
    del args['message_id']
    args['trigger_message_id'] = str(raw)
    return BoundPrivateText(call, json.dumps(args, ensure_ascii=False, separators=(',', ':')), handler, guard.owner)
