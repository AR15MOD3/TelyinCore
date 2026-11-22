"""Qualify malformed displayed locators without inventing a replacement.

Only explicit inline-code locators are checked. Valid syntax is not proof of
existence, access, relevance or truth; those remain the readers' responsibility.
Quoted examples and fenced code are left alone. No source lookup or model call.
"""
import re


MARKER = '〔引用格式不完整，不能据此定位原文；需使用完整引用或本批有效短引用〕'
_TOKEN = re.compile(r'(?<!`)`((?:record:|evidence:|event:|mem-)[^`\n]{0,180})`(?!`)')
_VALID = re.compile(r'(?:record:[0-9a-f]{64}:[1-9][0-9]{0,18}|'
                    r'evidence:[0-9a-f]{8}:[1-9][0-9]*|event:[1-9][0-9]*|mem-[0-9a-f]{64})')


def qualify(text):
    if not isinstance(text, str) or len(text) > 128000:
        return text, 0
    output = []
    fenced = None
    count = 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            token = marker[1]
            if fenced is None: fenced = token
            elif token[0] == fenced[0] and len(token) >= len(fenced): fenced = None
            output.append(line)
            continue
        if fenced or line.lstrip().startswith('>') or re.search(r'示例|例如|格式示范|原话|引用格式错误|不能使用|不要使用', line):
            output.append(line)
            continue
        def replace(match):
            nonlocal count
            if _VALID.fullmatch(match[1]):return match[0]
            # Bare namespaces and schema placeholders are explanatory notation,
            # not concrete locators. A truncated hash retains a substantial
            # concrete prefix and must not be presented as a usable reference.
            if not re.match(r'(?:record:[0-9a-f]{8}|mem-[0-9a-f]{8}|evidence:[0-9a-f]{8}|event:[0-9])',match[1]):
                return match[0]
            # Preserve quoted examples/utterances rather than alter originals.
            prefix = line[:match.start()]
            if any(prefix.count(a) > prefix.count(b) for a,b in [('“','”'),('「','」'),('『','』')]):
                return match[0]
            count += 1
            return MARKER
        output.append(_TOKEN.sub(replace, line))
    return ''.join(output), count
