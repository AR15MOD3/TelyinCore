"""Transport labels are not authenticated human identities.

Projection follows the existing current permission check. These ephemeral host
facts do not authorize reads and must not be reconstructed from textual output.
"""
import json
import re

BOUNDARY = '来源通道与发言者标识分开；console 表示本地输入通道，不代表系统自述，也不能据此否认用户发言。入口标识不认证现实中的自然人。'

def project(row, scope, reference):
    sender = getattr(row, 'sender_key', None)
    current = bool(scope.current.startswith('private:') and sender == scope.current[8:])
    fact = (reference, row.source, sender, row.origin, row.type, current)
    label = json.dumps({'channel': row.source, 'sender_key': sender,
                        'origin': row.origin, 'type': row.type}, ensure_ascii=False)
    return '〔宿主来源字段〕' + label + '\n' + BOUNDARY, fact


def corrections(parts, facts):
    eligible = [f for f in facts if f[1]=='console' and f[3:]==('external','message',True)]
    # A phrase such as "this message" must not pick one out of several events.
    if len(facts)!=1 or len(eligible)!=1:return {}
    fact=eligible[0];changes={};fenced=False
    for i,part in enumerate(parts):
        if '```' in part:
            if part.count('```')%2:fenced=not fenced
            continue
        if fenced or part.lstrip().startswith('>') or re.search('[?？]',part):continue
        plain=re.sub(r'[“「『][^”」』]*[”」』]|"[^"\n]*"','',part)
        if not re.search(r'console|控制台|本地输入',plain,re.I):continue
        if re.search('如果|假设|假如|例如|举例|原话|他说|她说|另一|其他|别的|不能|无法|不应|不代表|不意味着|不确定|尚未核实',plain):continue
        if not re.search(r'(?:不是|并非|不属于|不算)(?:你(?:本人)?|用户)(?:的)?(?:留言|消息|发言|说的|写的)|(?:系统|程序)(?:的)?(?:自述|自己说的|自行生成)',plain):continue
        changes[i]=('〔Core 证据校正〕当前读到的 '+fact[0]+' 是外部消息，发言者标识为 '+str(fact[2])+
                    '；console 仅是输入通道，不能据此判定“不是你的留言”或“系统自述”。该入口标识也不认证现实中的自然人。\n')
    return changes
