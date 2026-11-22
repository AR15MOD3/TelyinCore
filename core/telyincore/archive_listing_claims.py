"""Attribute an unlabelled attachment cell or a counted list to its displayed originals."""
import re
from .inspection_inference import _in_quote
from .memory import _literal_number
from .record_follow_title import referenced_title
from .reported_records import _visible_texts
from .requested_quote import heading_title

_COUNT = re.compile(
    r'^(?:查到(?:了)?|找到了)[，,]\s*(?:跟|与)(?:你这个号|这个编号|这个号|该编号)(?:相关|有关)的只有'
    r'(?P<n>[一二两三四五六七八九1-9])(?P<unit>条|份)(?=[，,。])')
_NAMED_ITEM = re.compile(r'(?m)^[ \t]*(?P<i>[1-9])[）).、][ \t]*《(?P<title>[^《》\n]{1,32})》[ \t]*')
_CELLS = re.compile(
    r'(?m)^附签(?:那条|这条|上)?[：:][ \t]*(?P<role>署名|签名)[「“](?P<name>[\u3400-\u9fff]{2,5})[」”]'
    r'[，,][ \t]*(?P<rack>[一二两三四五六七八九十0-9]{1,4}号架)(?=[，,。\n])')
_ROLE = re.compile(r'署名|签名(?!称)|签字|签署|落款|亲笔|执笔')
_STOCK = re.compile(r'盘点|清点|清册|清单总数|已查遍|总共|总计|只(?:有|收录|保存)|仅有|就这[些两几]|已全部|全部共')
_REPORT = re.compile(r'举例|转述|转引|原样转发|引用旧答|旧答|例句|范例|摘录|提示词|示例|假[设如定]|以下是|'
                     r'不属实|说法不对|这不成立|不能这么说|不能这样说|不是事实|这句话不对|^\s*>',re.M)
_DIGITS = dict(zip('一二三四五六七八九',range(1,10)),两=2)


def _signed_rows(sources):
    rows = {}
    for (_, body, ref, _, origin), record in sources:
        if record.name != 'read_events':
            continue
        if origin != record.scope:
            return {}  # An independently granted room can contain another copy.
        if (ref, len(body)) in record.text.complete_records:
            rows.setdefault((origin, ref, body), []).append(record.text)
    return rows


def _qualify_cell(text, cell, rows, question, number, room):
    name, rack = cell['name'], cell['rack']
    form = re.compile(r'(?P<title>[^｜|。\n]{1,32})[｜|]' + re.escape(name) +
                      r'[｜|]' + re.escape(rack) + r'(?:。[^｜|\n]{0,160})?')
    attachments = [match['title'] for _, _, body in rows if (match := form.fullmatch(body))]
    mains = [body for _, _, body in rows if body != question and heading_title(body, number)]
    if len(attachments) != 1 or len(mains) != 1 or referenced_title(mains[0]) != attachments[0]:
        return None
    if any(body.startswith((attachments[0] + '｜', attachments[0] + '|')) and not form.fullmatch(body)
           for _, _, body in rows):
        return None
    replacement = text[:cell.start('role')] + '所列姓名为' + text[cell.end('role'):]
    return replacement, '附签列有姓名和架位，原件没有署名字段。', room, room


def _qualify_count(text, claim, rows, question, number, room):
    n = int(claim['n']) if claim['n'].isascii() else _DIGITS[claim['n']]
    listed = list(_NAMED_ITEM.finditer(text))
    identifier = re.compile(re.escape(number) + r'(?![A-Za-z0-9_-])')
    if (len(listed) != n or [int(item['i']) for item in listed] != list(range(1, n + 1))
            or any(not identifier.match(text, item.end()) for item in listed)):
        return None
    names = {item['title'] for item in listed}
    if len(names) != n:
        return None
    heading = re.compile(r'^(?P<title>[^ ｜|。\n]{1,32})(?:[ \t]+|[｜|])' +
                         re.escape(number) + r'(?=[。；;\n]|$)')
    # Parse only the typed page's host header, never a string inside a record.
    header = re.compile(r'本次筛选：category=(?:all|dialogue|user) · 关键词=' + re.escape(number) +
                        r' · 本页 ([0-9]+) 条 · 本页之后 ([0-9]+) 条。')
    hits = {}
    for (_, ref, body), pages in rows.items():
        match = heading.match(body)
        if body == question or not match:
            continue
        for page in pages:
            count = header.fullmatch(page.partition('\n')[0])
            if count and int(count[1]) > n:
                hits[ref] = match['title']
                break
    if len(hits) != n or set(hits.values()) != names:
        return None
    safe = '这次从当前可见记录中核到了' + claim['n'] + claim['unit'] + '同号单据'
    return safe + text[claim.end():], '区分本次核到的同号单据数与所有相关材料数。', room, room


def qualify(text, read_sources, question, messages):
    claim = _COUNT.match(text)
    cell = _CELLS.search(text)
    if bool(claim) == bool(cell):
        return None  # No claim, or two different edits with separate authorities.
    number = _literal_number(question)
    if not number or _REPORT.search(text) or '```' in text:
        return None
    if _in_quote(text, (claim or cell).start()):
        return None
    # A separate inventory, labelled source or photo can establish a fact
    # that a keyword search or an unlabelled cell alone cannot establish.
    independent = _STOCK if claim else _ROLE
    if independent.search(question) or any(
            speaker == 'image' or (speaker not in ('assistant', 'system', 'developer', 'read_events')
                                   and independent.search(body))
            for speaker, body in _visible_texts(messages)):
        return None
    sources = tuple(read_sources())
    rows = _signed_rows(sources)
    rooms = {scope for scope, _, _ in rows}
    if len(rooms) != 1 or any(independent.search(body) for _, _, body in rows):
        return None
    complete = {(scope, ref) for scope, ref, _ in rows}
    if any((scope, ref) not in complete for (_, _, ref, _, scope), record in sources
           if record.name == 'read_events'):
        return None  # An unopened preview might hold another label, count or reference.
    room, = rooms
    if cell:
        return _qualify_cell(text, cell, rows, question, number, room)
    return _qualify_count(text, claim, rows, question, number, room)
