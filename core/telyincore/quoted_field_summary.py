"""Bind an exclusive summary of an end field to the original already quoted in the reply."""
import re

from .archive_wording import _headline, _materials, _REPORTED
from .bounded_archive_read import common_box_text
from .inspection_inference import _in_quote
from .memory import _literal_number
from .record_follow_title import _EXCLUDED, _NONCURRENT_FIELD
from .requested_quote import heading_title


_LIMITED = re.compile(
    r'(?:^|(?<=[。！？!?，,；;\n]))[ \t]*(?:但|可)?(?P<claim>末栏只(?:'
    r'记了(?P<delivery>(?:交给|交至)[\w· -]{1,18})这一句[，,]并没有写明附签上的名称和架位'
    r'|提了「(?P<title>[^「」。\n]{2,32})」和没记签收人))(?=[，,。；;\n]|$)')
_FINAL = re.compile(r'末栏[：:][ \t]*(?P<value>按「(?P<title>[^「」。\n]{2,32})」'
                    r'(?P<delivery>(?:交给|交至)[\w· -]{1,18})，本单没记签收人)。')


def qualify(text, read):
    if _REPORTED.search(text) or re.search(r'旧答|原话如下|转述|复述|误以为|不能说|并非|不要说|反例|另一份修订', text):
        return None
    matches = list(_LIMITED.finditer(text))
    if len(matches) != 1:
        return None
    match, = matches
    start, end = match.span('claim')
    paragraph = text[text.rfind('\n', 0, start) + 1:start]
    if (_in_quote(text, start) or paragraph.lstrip().startswith('>')
            or re.match(r'[^。！？!?\n]{0,100}(?:[？?]|说法(?:不对|有误|不成立))', text[end:])):
        return None
    label = _literal_number(text[:256])
    if not label or any(_literal_number(word) not in ('', label) for word in re.findall(r'[A-Za-z][A-Za-z0-9-]*', text)):
        return None
    mains = [(key, source) for key, source in _materials(read).items()
             if any(heading_title(_headline(s), label) for s in source.excerpts)]
    if len(mains) != 1:
        return None
    key, source = mains[0]
    if source.recipients != {key[0]} or len(source.bodies) != 1:
        return None
    body, = source.bodies
    lines = body.splitlines()
    if (len(lines) < 4 or not common_box_text(lines[1:-1])
            or _NONCURRENT_FIELD.search(body) or _EXCLUDED.search(body)):
        return None
    field = _FINAL.fullmatch(lines[-1])
    if field is None or field['value']+'。' not in text[:start]:
        return None
    if ((match['title'] and field['title'] != match['title'])
            or (match['delivery'] and field['delivery'] != match['delivery'])):
        return None
    revised = text[:start]+'末栏写明'+field['value']+text[end:]
    return revised, '附带说明和前面照录的整句有出入，可保留末栏原话。', key[0], key[0]
