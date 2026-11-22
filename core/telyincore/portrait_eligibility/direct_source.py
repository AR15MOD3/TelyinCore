"""Recognize narrow, verbatim first-person statements in current QQ messages."""
import re

from .context import actor_text
from ..events import RangeQuery
from ..session_view import event_scope


# Strip only a recognized conversational lead-in for classification. The
# evidence comparison and writer still use the complete, unchanged message.
_LEAD_IN = re.compile(r'^(?:说到这个|说起来|话说|对了)[，,] *')
_PLAN_TIME = r'[年月日周星期一二三四五六七上下本今明后天早中午晚0-9（）() -]'
_STATEMENTS = (
    ('plan', re.compile(rf'{_PLAN_TIME}{{0,24}}我(?:计划|打算){_PLAN_TIME}{{0,24}}'
                        r'(?:动手做|做|去|买|学|读|写|练|看|吃|喝|跑|整理|复习|准备).+')),
    ('recent_context', re.compile(r'我(?:最近|这几天|这两天|今天|昨天)(?:在|开始|只|还没|已经).+')),
    ('stable_preference', re.compile(r'我(?:一直|一向|长期|向来)(?:都)?(?:更|比较|最|不太|不)?'
                                     r'(?:喜欢|偏好).+')),
)
# The short path has no semantic reviewer. Keep dialogue, embedded quotations,
# multiple clauses, requests, corrections and uncertain references in review.
_UNCLEAR = re.compile(
    r'[，,、；;！？!?：:。.…“”‘’「」『』《》"\'<>\[\]{}\\/\r\n\t]'
    r'|但是|不过|或者|并且|而且|另外|还是|不是说|而是|更正|改成|撤回|算了'
    r'|可能|也许|似乎|好像|应该|大概|未必|假如|如果|除非|取决于'
    r'|听说|据说|说过|提过|他说|她说|转述|开玩笑|假装|举例|编造|忽略|执行|吗$|么$|吧$|呢$|请你|帮我|记住|写入|删掉'
)


def direct_source(console, turn, author, text):
    """Return the verified source cursor and kind, or defer to normal review."""
    if len(turn.messages) != 1 or turn.messages[0].author != author:
        return None
    store = console.app.store
    event = store.get(int(turn.messages[0].message_id))
    if (event is None or event.source != 'qq' or event.sender_key != author
            or event_scope(event) != turn.scope or event.blobs):
        return None
    meta = event.meta
    if (not isinstance(meta, dict) or meta.get('segment_types') != ['text'] or meta.get('has_user_text') is not True
            or meta.get('plain_text') != text or actor_text(event) != text or not 5 <= len(text) <= 64 or text.strip() != text):
        return None
    statement = _LEAD_IN.sub('', text.removesuffix('。'), count=1)
    if not statement.isprintable() or _UNCLEAR.search(statement):
        return None
    for kind, pattern in _STATEMENTS:
        if pattern.fullmatch(statement):
            return (event.cursor, kind) if _source_still_latest(store, event) else None
    return None


def _source_still_latest(store, event):
    """Defer if a later same-room statement could qualify or correct the source."""
    if store.latest_cursor() == event.cursor:
        return True
    newer = store.range(RangeQuery(from_cursor=event.cursor + 1, sender_key=event.sender_key,
                                   source=event.source, origin='external', limit=32))
    if len(newer) == 32:
        return False
    return not any(row.type == 'message' and event_scope(row) == event_scope(event) for row in newer)
