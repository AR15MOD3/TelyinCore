"""Progressive, current-tool documentation; no authority or execution."""
import hashlib
import json

from .tools import ToolDef, ToolTag
from . import capability_index

DECLARATION = ToolDef(name='tool_guide',
    description='按需查工具或做法。已熟悉的工具可直接用；根据当前目标和上一步回执自行组合。section=tools（默认）按用途 intent 找当前可用工具；section=skills 找宿主核对流程及已学做法入口。不给 name 返回分页目录，指定 name 读取详情；详情按 offset 与 version 续读。目录只指路，不是事实、执行记录或权限。',
    parameters={'type':'object','properties':{
        'name':{'type':'string'}, 'after':{'type':'string'},
        'limit':{'type':'integer','minimum':1,'maximum':30},
        'offset':{'type':'integer','minimum':0}, 'version':{'type':'string'},
        'section':{'type':'string','enum':['tools','skills']},
        'intent':{'type':'string','enum':[key for key, _, _ in capability_index.INTENTS]}},
        'additionalProperties':False}, tags=(ToolTag.READ,))


def handler(catalog):
    def guide(name='', after='', limit=12, offset=0, version='', section='', intent=''):
        if (not all(isinstance(x,str) and len(x) <= 160 for x in (name,after,version))
                or type(limit) is not int or not 1 <= limit <= 30
                or type(offset) is not int or offset < 0):
            raise ValueError('tool guide arguments invalid')
        if section not in ('','tools','skills') or intent not in ('', *(key for key, _, _ in capability_index.INTENTS)):
            raise ValueError('tool guide section or intent invalid')
        current = sorted(catalog(), key=lambda row: row['name'])  # Current permissions and hot reload.
        names = {r['name'] for r in current}
        if not section:
            # Exact, currently available procedure IDs are unambiguous unless
            # a real tool owns the same name. Never fuzzy-match a private ID.
            section = ('skills' if name and name not in names and
                any(r['name'] == name for r in capability_index.procedures(current)) else 'tools')
        if section == 'skills':
            rows = capability_index.procedures(current)
            if intent: rows = [r for r in rows if r['intent'] == intent]
        else:
            rows = [dict(row, judgment=capability_index.tool_summary(row)) for row in current]
            if intent: rows = [r for r in rows if intent in r['judgment']['intents']]
        if not name:
            if offset or version:
                raise ValueError('tool directory has no content offset/version')
            page = [r for r in rows if r['name'] > after][:limit+1]
            more = len(page) > limit
            page = page[:limit]
            items = ([{'name': r['name'], 'title': r['title'], 'intent': r['intent'],
                       'read': {'tool':'tool_guide','arguments':{'section':'skills','name':r['name']}}}
                      for r in page] if section == 'skills' else
                     [{'name':r['name'], 'description':r['description'][:160],
                       **r['judgment'], 'read':{'tool':'tool_guide','arguments':{'name':r['name']}}}
                      for r in page])
            result = {'kind':'skill_directory' if section == 'skills' else 'tool_directory',
                'items':items, 'next_after':page[-1]['name'] if more else None,
                'complete':not more, 'execution_authorized':False, 'evidence':False,
                'intent':intent, 'groups':capability_index.groups(current),
                'coverage':'仅本次当前可用能力；空结果不证明历史上没有此能力。索引不会执行任何步骤。'}
            if section == 'skills' and {'memory_navigate','skill_read'} <= names:
                result['learned_index'] = {'tool':'memory_navigate','arguments':{'section':'skills'},
                    'meaning':'另行按当前资料权限读取已学做法；此目录的 complete 仅指宿主流程。'}
        else:
            if after:
                raise ValueError('tool details use offset instead of after')
            row = next((r for r in rows if r['name'] == name), None)
            if row is None:
                raise PermissionError('tool unavailable')
            raw = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',',':'))
            digest = hashlib.sha256(raw.encode()).hexdigest()
            if version and version != digest:
                raise ValueError('tool documentation changed; restart at offset 0')
            if offset and not version:
                raise ValueError('continuation requires version')
            if offset > len(raw):
                raise ValueError('tool documentation offset out of range')
            end = min(offset+4000, len(raw))
            result = {'kind':'tool_document', 'name':name, 'version':digest,
                'content':raw[offset:end], 'offset':offset,
                'next_offset':end if end < len(raw) else None,
                'complete':end == len(raw), 'execution_authorized':False}
        return json.dumps(result, ensure_ascii=False)
    return guide
