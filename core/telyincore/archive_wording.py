"""Qualify explicit archive claims against the currently displayed and attested originals."""
from dataclasses import dataclass, field
from datetime import date, timezone
import re

from .inspection_inference import _in_quote
from .memory import _literal_number
from .quote_typography import RECORD
from .record_follow_title import PAIR_ANNOTATION, qualify_annotation
from .requested_quote import archive_record_time, heading_title


_REPORTED = re.compile(r'例如|举例|示例|假设|假如|如果|演示|排练|台词|据说|有人说|声称|复述[：:]|转述[：:]|以下是引文')
_SUMMARY = re.compile(
    r'(?<=[。！？\n])(?P<lead>(?:以上|上述|上面这些|这些|这)(?:都|均)?(?:是|为)[ \t]*)'
    r'(?P<date>(?:(?P<year>\d{4})[ \t]*年[ \t]*)?(?P<month>\d{1,2})[ \t]*月[ \t]*(?P<day>\d{1,2})[ \t]*日)'
    r'(?P<owner>[ \t]*(?:这|那)(?:一)?条(?:归档)?记录)(?:的|[里中上](?:的|(?:你|您|我)(?:当时)?(?:留下|写下|写|说过)的))'
    r'(?:原文|原话|原句)(?=[，,。；;\n]|$)')
_PAIR = re.compile(
    r'(?m)^(?P<subjects>(?:附[签表件页单]|附录|附件|副单|补充记录)(?:这|那)?(?:一)?条(?:记录)?(?:和|与)(?:它|主单|原单|前一条))'
    r'(?:是|用的(?:是)?)?(?:同一(?:个)?时间戳|时间戳(?:完全)?相同)[，,][ \t]*'
    r'(?:正文|原文|内容)(?:是|为)?[：:][ \t]*(?P<left>[「“『])(?P<body>[^\n「」“”『』]{4,300})(?P<right>[」”』])')
_QUOTES = {'「':'」', '“':'”', '『':'』'}
_ONLY_BOX = re.compile(
    r'(?:^|(?<=[。！？!?，,；;：:\n]))[ \t]*(?P<sheet>[\u3400-\u9fff]{1,12}单)'
    r'(?:本身|本单[0-9一二三四五六七八九十百]{1,5}格都)(?P<claim>只写(?P<quote>“见末栏”|「见末栏」|"见末栏"))'
    r'(?=[，,。；;\n]|$)')


@dataclass
class _Source:
    excerpts: set = field(default_factory=set)
    bodies: set = field(default_factory=set)
    dates: set = field(default_factory=set)
    times: set = field(default_factory=set)
    recipients: set = field(default_factory=set)


def _materials(read):
    sources = {}
    for (day, body, ref, _, scope), row in read():
        if not isinstance(ref, str) or not RECORD.fullmatch(ref):
            continue
        item = sources.setdefault((scope, ref), _Source())
        item.recipients.add(row.scope)
        receipt = row.text
        item.excerpts.add(body)
        item.dates.add(day)
        if (ref, len(body)) in receipt.complete_records:
            item.bodies.add(body)
            stamp = archive_record_time(receipt, ref)
            if stamp is not None and stamp.tzinfo is not None:
                item.times.add(stamp.astimezone(timezone.utc))
    return sources


def _headline(body):
    return re.split(r'[，,。；;！？\n：:]', body, maxsplit=1)[0]


def _near_time(text, match, key, main, sources):
    if _QUOTES[match['left']] != match['right'] or len(main.times) != 1:
        return None
    neighbors = [(k, s) for k, s in sources.items() if match['body'] in s.excerpts]
    if len(neighbors) != 1:
        return None
    other_key, other = neighbors[0]
    if (other_key == key or other_key[0] != key[0] or other.bodies != {match['body']}
            or len(other.times) != 1):
        return None
    first, = main.times
    second, = other.times
    gap = abs(first - second)
    if (first.replace(microsecond=0) != second.replace(microsecond=0) or not gap
            or gap.microseconds % 1000):
        return None
    replacement = f'记在同一秒，时间戳相差 {gap.microseconds // 1000} 毫秒'
    comma = re.search('[，,]', match[0]).start() + match.start()
    return (text[:match.end('subjects')] + replacement + text[comma:],
            '两份已读原件的记录时间在同一秒内，毫秒值不同。可以说同一秒，也可省去这项比较。')


_LIST_ITEM = re.compile(r'^\s*(?:[-+*•]|\d{1,2}[、.）)])\s*')
_COMPACT = re.compile(r'[\W_]+')
_QUOTES_IN_PROSE = re.compile('[「」“”『』"‘’]')


def _claimed_passages(narrative, plural):
    # A singular retrospective can also refer just to the last paragraph or
    # list item. Do not infer that an earlier unmatched line is its referent.
    paragraphs = re.split(r'\n[ \t]*\n', narrative.strip())
    last = paragraphs[-1]
    lines = [line.strip() for line in last.splitlines() if line.strip()]
    if not lines:
        return ()
    if any(_LIST_ITEM.match(line) for line in lines):
        if len(lines) > 1 and not _LIST_ITEM.match(lines[0]) and lines[0].endswith(('：', ':')):
            lines = lines[1:]
        if not all(_LIST_ITEM.match(line) for line in lines):
            return ()
        return [_LIST_ITEM.sub('', line, count=1) for line in (lines if plural else lines[-1:])]
    if len(lines) != 1:
        return ()
    claims = re.split(r'[。！？!?]', last.rstrip('。！？!?'))
    if plural and len(claims) > 1:
        # The opening document identification is frequently an editorial lead.
        return claims[1:] if _literal_number(claims[0]) else claims
    return claims[-1:]


def _changed_passage(passage, original):
    # Typography and a short introductory field label can wrap an exact
    # selection. The publication check does not adjudicate such formatting.
    if _QUOTES_IN_PROSE.search(passage) or re.search(r'逐字|照录|摘录|节选|引述', passage):
        return False
    body = passage.rsplit('：', 1)[-1].rsplit(':', 1)[-1]
    words = _COMPACT.sub('', body)
    if not 6 <= len(words) <= 350 or words in original or '…' in body or '...' in body:
        return False
    # A date and number bind the claim to the record, but unrelated prose is
    # not evidence of a retelling. Require some wording shared with the body.
    first = None
    for i in range(len(words) - 1):
        pair = words[i:i+2]
        if pair != first and pair in original:
            if first is not None:
                return True
            first = pair
    return False


def _matches_day(day, match):
    try:
        recorded = date.fromisoformat(day)
    except (TypeError, ValueError):
        return None
    return (int(match['month']) == recorded.month and int(match['day']) == recorded.day
            and (not match['year'] or int(match['year']) == recorded.year))


def _summary_label(text, match, material):
    if len(material.dates) != 1 or not _matches_day(next(iter(material.dates)), match):
        return None
    narrative = text[:match.start()].strip()
    if len(narrative) > 1200:
        return None
    original, = material.bodies
    actual = _COMPACT.sub('', original)
    if not actual or actual in _COMPACT.sub('', narrative):
        return None
    passages = _claimed_passages(narrative, match['lead'].strip() not in ('这是', '这为'))
    if not any(_changed_passage(passage, actual) for passage in passages):
        return None
    lead = match['lead'].rstrip()
    label = lead + '我对' + match['lead'][len(lead):] + match['date'] + match['owner'] + '的概括'
    revised = text[:match.start()] + label + text[match.end():]
    return revised, '前面的答复概括了已读记录，并非逐字照录；可将来源说明改为“据该记录概括”，也可直接省略这句。'


def qualify(text, read):
    """Use no index or additional model call. Ambiguous references leave this check inactive."""
    if len(text) > 4000 or '\x60' in text:
        return None
    if '末栏只' in text:
        from .quoted_field_summary import qualify as qualify_summary
        corrected = qualify_summary(text, read)
        if corrected:
            return corrected
    if re.search(r'(?m)^\s*>', text):
        return None
    matches = [m for pattern in (_SUMMARY, _PAIR, PAIR_ANNOTATION) for m in pattern.finditer(text)]
    if not matches and '只写' in text and '见末栏' in text:
        matches = list(_ONLY_BOX.finditer(text))
    if len(matches) != 1:
        return None
    match, = matches
    # A conditional after a complete retrospective is a separate statement.
    # A hypothetical, example, or reported assertion introducing it is not.
    if _REPORTED.search(text[:match.end()] if match.re is _SUMMARY else text):
        return None
    # Only a positive statement by the speaker, not a question about the assertion.
    if (_in_quote(text, match.start())
            or re.match(r'[^。！？!?\n]{0,120}[？?]', text[match.end():])
            or re.search(r'(?:这一说法|这句话|这种说法)(?:是)?(?:错误|不对|不成立)', text[match.end():])):
        return None
    before = text[:match.start()]
    first = re.split(r'[。！？!?\n]', before, maxsplit=1)[0]
    if match.re is _ONLY_BOX:
        # The answer may open with a greeting before naming the archived sheet.
        if re.search(r'你说|他说|她说|旧答|旧回复|有人说|原话是[：:]\s*$|说法|并非|不能说|是否|是不是|并不是|假定|不要说|引述|回忆|反例', before[-64:]):
            return None
        label = _literal_number(before[:256])
    else:
        label = _literal_number(first)
    if not label or _literal_number(before[:256]) != label:
        return None
    # Another explicit identifier anywhere can change the demonstrative's subject.
    if any(_literal_number(word) not in ('', label) for word in re.findall(r'[A-Za-z][A-Za-z0-9-]*', text)):
        return None
    sources = _materials(read)
    candidates = [(key, s) for key, s in sources.items()
                  if any(heading_title(_headline(excerpt), label) is not None for excerpt in s.excerpts)]
    if match.re is _SUMMARY:
        # A fresh user question can itself be returned by record search with
        # the same identifier. Its known later date rules out that referent.
        candidates = [(key, src) for key, src in candidates
                      if not src.dates or any(_matches_day(day, match) is not False for day in src.dates)]
    if len(candidates) != 1:
        return None
    key, main = candidates[0]
    if len(main.bodies) != 1 or len(main.recipients) != 1:
        return None
    if match.re is _ONLY_BOX:
        from .bounded_archive_read import common_box_text
        from .record_follow_title import _FINAL_FIELD, _NONCURRENT_FIELD, _EXCLUDED
        body, = main.bodies
        title = heading_title(body, label)
        lines = body.splitlines()
        if (main.recipients != {key[0]} or not title or not title.endswith(match['sheet']) or len(lines) < 4
                or not _FINAL_FIELD.fullmatch(lines[-1]) or _NONCURRENT_FIELD.search(body) or _EXCLUDED.search(body)):
            return None
        common = common_box_text(lines[1:-1])
        if not common:
            return None
        # Replace the full assertion, including any model-supplied box count.
        # A verbatim source clause makes the same correction useful on both speech channels.
        replacement = f'{match["sheet"]}的 {len(lines)-2} 格都写有“{common.removesuffix("。")}”'
        revised = text[:match.start('sheet')] + replacement + text[match.end():]
        edit = revised, f'请按主件已核对的格内原文说明：{common}'
    elif match.re is PAIR_ANNOTATION:
        edit = qualify_annotation(text, match, key, main, sources)
    elif match.re is _PAIR:
        edit = _near_time(text, match, key, main, sources)
    else:
        edit = _summary_label(text, match, main)
    if edit:
        recipient, = main.recipients
        return *edit, recipient, key[0]
    return None
