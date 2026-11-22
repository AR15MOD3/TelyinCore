"""Restore explicit elisions from one dated typed original before other checks.

Never approve an elided assertion as-is: omitted negation/qualifiers are restored
verbatim. Ambiguous, reordered or unmatched snippets stay subject to rejection.
"""
import re
from datetime import datetime
_PATTERN=re.compile(r'(\d{4}-\d{2}-\d{2}|\d{4}年\d{1,2}月\d{1,2}日)([^。！？\n]{0,240}?)([“「"])([^”」"\n]{6,200})([”」"])')

def expand(text,originals):
    if '…' not in text and '...' not in text:
        return text, 0
    material = None
    out=[];fenced=None;count=0
    for line in text.splitlines(keepends=True):
        marker=re.match(r'^\s*(`{3,}|~{3,})',line)
        if marker:
            token=marker[1]
            if fenced is None:fenced=token
            elif token[0]==fenced[0] and len(token)>=len(fenced):fenced=None
            out.append(line);continue
        if fenced or line.lstrip().startswith('>'):
            out.append(line);continue
        def replace(match):
            nonlocal count, material
            date,bridge,left,quote,right=match.groups()
            if {'“':'”','「':'」','"':'"'}[left]!=right:return match.group()
            prefix=line[:match.start()]
            if any(prefix.count(a)>prefix.count(b) for a,b in [('“','”'),('「','」'),('『','』')]):return match.group()
            if not re.search(r'原话|原文|正文|说|记录|留言|：|:',bridge):return match.group()
            if re.search(r'如果|假如|例如|举例|不是|误写|是否',line[:match.start()]):return match.group()
            if not re.search(r'…|\.{3}',quote):return match.group()
            try:day=datetime(*map(int,re.findall(r'\d+',date))).date().isoformat()
            except ValueError:return match.group()
            if material is None:
                material = tuple(originals() if callable(originals) else originals)
            candidates=[body for d,body,_ in material if d==day]
            if any(quote in body for body in candidates):return match.group()
            pieces=re.split(r'…+|\.{3,}',quote)
            if not 2<=len(pieces)<=4 or any(len(p)<4 for p in pieces):return match.group()
            restored=set()
            for body in candidates:
                if any(body.count(piece)!=1 for piece in pieces):continue
                start=body.find(pieces[0])
                while start>=0:
                    end=start+len(pieces[0]);valid=True
                    for piece in pieces[1:]:
                        position=body.find(piece,end)
                        if position<0:valid=False;break
                        end=position+len(piece)
                    if valid and end-start<=1000:restored.add(body[start:end])
                    start=body.find(pieces[0],start+1)
            if len(restored)!=1:return match.group()
            count+=1
            return date+bridge+left+next(iter(restored))+right
        out.append(_PATTERN.sub(replace,line))
    return ''.join(out),count
