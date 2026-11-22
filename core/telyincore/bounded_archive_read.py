"""Experiment: expose one explicitly requested, short original through the exact-read path."""
from contextlib import closing
import re

from .record_follow_title import _EXCLUDED, _FINAL_FIELD, _NONCURRENT_FIELD, human_original, referenced_title
from .requested_quote import ANCHOR, CONTEXT, SOURCE_LABEL, heading_title, requested_value
from .session_view import event_scope


# A separate final sentence may decline only the optional write-order hint.
# Keep every exclusion on the actual request for the field quotation.
_OMIT_WRITE_COMPARISON = re.compile(
    r'(?<=[。！？!?])[ \t]*(?:不用|不必|无需|不要)(?:再)?(?:比较|核对|说明|计算)'
    r'(?:这两条(?:记录)?的)?(?:归档|写入|入库)(?:的)?(?:先后(?:顺序)?|时差|时间差)[。.!！]?[ \t]*$'
)


def question(context, room, number):
    if not context or not number or not SOURCE_LABEL.fullmatch(number):
        return ''
    console, owner = context
    if not owner or console.current_batch_identity() is not owner:
        return ''
    events = [event for event in console.original_batch() if event.type == 'message'
              and event.origin == 'external' and event_scope(event) == room]
    if len(events) != 1:
        return ''
    ask = events[0].text
    if (len(ask) > 2048 or '末栏' not in ask or '整句' not in ask or len(re.findall(ANCHOR, ask)) != 1
            or CONTEXT.search(_OMIT_WRITE_COMPARISON.sub('', ask)) or SOURCE_LABEL.findall(ask) != [number]):
        return ''
    guard = getattr(console, 'reply_evidence_guard', None)
    marker = getattr(guard, 'receipt_marker', None)
    view = getattr(console, '_request_session', None)
    if (marker is None or getattr(guard, 'owner', None) is not owner or view is None
            or not callable(getattr(view, 'quote_source_visible', None))):
        return ''
    try:
        if not view.quote_source_visible(room, room) or view.tool_evidence_snapshot()[0] is not marker[0]:
            return ''
    except (OSError, ValueError, RuntimeError):
        return ''
    return ask


def select(index, page, total, grant, number, ask, context, seen):
    if index is None or not ask or not page or len(page) != total:
        return None
    matches = [(ref, row) for _, ref, row in page
               if human_original(row, grant.current) and heading_title(row['text'], number)]
    if len(matches) != 1:
        return None
    ref, row = matches[0]
    if ref in seen or not 240 < row['_text_length'] <= 512:
        return None
    with closing(index.rows(ref, visible=grant.permits, window=(0, 512))) as found:
        verified = next(found, None)
    if (not verified or verified[0] != ref or not human_original(verified[1], grant.current)
            or not grant.permits(verified[1].get('scope')) or verified[1]['_text_length'] != row['_text_length']):
        return None
    body = verified[1]['text']
    if (len(body) != row['_text_length'] or not body.startswith(row['text']) or _NONCURRENT_FIELD.search(body)
            or question(context, grant.current, number) != ask):
        return None
    title = heading_title(body, number)
    if not title:
        return None
    field = requested_value(ask, number, title, body)
    if not field or field[0] != '末栏':
        return None
    return ref, referenced_title(body), verified[1]


def common_box_text(rows):
    """Return the exact shared text only for consecutively numbered, identical boxes."""
    if len(rows) < 2:
        return ''
    piece = re.fullmatch(r'第1格((?:登记|记录|记载)[\u3400-\u9fff]{1,16}，'
                         r'(?:附签|附表|附件)[\u3400-\u9fff]{1,16}见末栏。)', rows[0])
    if piece and all(row == f'第{i}格{piece[1]}' for i, row in enumerate(rows, 1)):
        return piece[1]
    return ''


def field_location(body):
    """The full bounded read already verified a unique ending field for this request."""
    lines = body.splitlines()
    hits = [i for i, line in enumerate(lines, 1) if re.match(r'^末栏[：:]', line)]
    if len(hits) != 1:
        return '主件全文在本回执开头。'
    field, = hits
    earlier = sum('见末栏' in line for line in lines[:field - 1])
    location = f'主件全文在本回执开头。正文第 {field} 行是末栏字段，照录该行字段名后的整句。'
    last = lines[field - 1]
    if (field == len(lines) and _FINAL_FIELD.fullmatch(last) and not _NONCURRENT_FIELD.search(body) and not _EXCLUDED.search(body)
            and (written_name := referenced_title(last))):
        first = re.split(r'[：:]', last, maxsplit=1)[1].lstrip()[0]
        location += f'引文首字为“{first}”，字段名和冒号在引文之外。末栏本句所写的名称是「{written_name}」。'
    if earlier:
        location += f'此前 {earlier} 行含“见末栏”字样，出处在各自所在行。'
        # Display one common literal only when every intervening numbered box
        # actually contains it. A change to one box invalidates the summary.
        rows = lines[1:field - 1]
        if len(rows) == earlier and (piece := common_box_text(rows)):
            location += f'主件这 {earlier} 格的共同原句（格号后）是“{piece}”'
    return location
