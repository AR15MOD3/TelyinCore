"""A small model-readable map, with permission-checked topic expansion.

Navigation is a clue, not an assertion that a fact is true or a source is still
readable. Leaf tools revalidate their own source and scope on every read.
"""
from pathlib import Path
import hashlib
import json
import sqlite3
import base64
from contextlib import closing
from pathlib import PurePosixPath

from . import learned_knowledge, skills
from .retrieval_scope import current, file_grants
from .tools import ToolDef, ToolTag
from .capability_index import memory_route

ROUTES = (
    ('attention', '正在关注、等待或接续的事', 'attention_read'),
    ('memories', '日常组合检索与记忆原文', 'memory_search'),
    ('knowledge', '规则、资料与适用条件', 'kb_read'),
    ('skills', '交流及操作做法', 'skill_read'),
    ('records', '原始对话和工具回执，可按单号或原话查', 'read_events'),
    ('relations', '话题、人物与事件关联', 'memory_related'),
    ('timers', '定时事项及提醒状态', 'timer_read'),
)
DIRECTORIES = ('knowledge', 'skills', 'attention', 'memories')

DECLARATION = ToolDef(name='memory_navigate',
    description='记忆导航入口：先看 root，按 memories/knowledge/skills/attention 展开，再用条目给出的工具读取原文。导航与标题不是事实证据；每次重新检查可见范围。',
    parameters={'type':'object','properties':{
        'section':{'type':'string','enum':['root', *DIRECTORIES]},
        'path':{'type':'string','description':'memories 可按返回的文件路径或其目录继续浏览；省略从全部可读资料开始。'},
        'after':{'type':'string'}, 'limit':{'type':'integer','minimum':1,'maximum':30}},
        'additionalProperties':False}, tags=(ToolTag.READ,))


def _skills(home, visibility):
    granted = file_grants(home, visibility)
    base = home / skills.SKILL_TREE
    entries = skills.load(home).get('entries', {})
    for key, item in sorted(entries.items()):
        if item.get('state', 'active') != 'active':
            continue
        path = base / item['file']
        if path.resolve() != path.absolute() or base.resolve() not in path.resolve().parents:
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            # An unreadable candidate does not authorize revealing its title.
            continue
        if not (granted(path, raw) or (
            visibility.permits(item.get('scope')) and item.get('version') == hashlib.sha256(raw).hexdigest())):
            continue
        yield {'id':key, 'title':str(item.get('title',''))[:200], 'kind':skills.kind_of(item),
               'version':item.get('version'), 'read':{'tool':'skill_read','arguments':{'name':key}}}


def handler(home, scope_provider, *, attention, available):
    home = Path(home).absolute()

    def navigate(section='root', after='', limit=12, path=''):
        visibility = current(scope_provider)
        if type(limit) is not int or not 1 <= limit <= 30 or not isinstance(after, str) or len(after) > 2048 or not isinstance(path,str) or len(path)>512:
            raise ValueError('navigation pagination invalid')
        if path and section != 'memories':
            raise ValueError('path requires memories section')
        names = set(available())
        if section == 'root':
            if after:
                raise ValueError('root navigation has no cursor')
            rows = [{'id':key, 'title':title, 'read_tool':tool, 'use_when':memory_route(key),
                     'expandable':key in DIRECTORIES}
                    for key,title,tool in ROUTES if tool in names]
            indexes = [{'id':'memory','meaning':'资料在哪里',
                        'read':{'tool':'memory_navigate','arguments':{'section':'root'}}}]
            if 'tool_guide' in names:
                indexes.extend([
                    {'id':'tools','meaning':'当前有哪些能力、适用场景与结果边界',
                     'read':{'tool':'tool_guide','arguments':{'section':'tools'}}},
                    {'id':'skills','meaning':'如何核对和操作；按需展开流程与已学做法',
                     'read':{'tool':'tool_guide','arguments':{'section':'skills'}}}])
            return json.dumps({'kind':'navigation', 'scope':visibility.current, 'items':rows,
                'complete':True, 'evidence':False, 'indexes':indexes,
                'guidance':'先选入口；发现候选后读原文及后续页。无匹配不等于事实不存在；歧义时继续查阅或询问。'}, ensure_ascii=False)
        route = next((row for row in ROUTES if row[0] == section), None)
        if route is None or section not in DIRECTORIES or route[2] not in names:
            raise ValueError('navigation section unavailable')
        if section == 'memories':
            return json.dumps(_memory_page(home, visibility, path, after, limit), ensure_ascii=False)
        if section == 'attention':
            if after and (not after.isascii() or not after.isdigit()):
                raise ValueError('attention navigation cursor invalid')
            page = attention.read(visibility, before=int(after) if after else None, limit=limit)
            return json.dumps({'kind':'navigation', 'items':[
                {'id':r['id'], 'title':r['title'], 'state':r['state'], 'revision':r['revision'],
                 'read':{'tool':'attention_read','arguments':{'identifier':r['id']}}} for r in page['items']],
                'next_after':str(page['next_before']) if page['next_before'] else None,
                'complete':page['complete'], 'evidence':False}, ensure_ascii=False)
        if section == 'knowledge':
            candidates = ({'id':key, 'title':row['title'], 'version':row['version'],
                'read':{'tool':'kb_read','arguments':{'name':key,'version':row['version']}}}
                for key,row in learned_knowledge.visible(home, visibility))
        else:
            candidates = _skills(home, visibility)
        rows = []
        for row in candidates:
            if row['id'] <= after:
                continue
            rows.append(row)
            if len(rows) > limit:
                break
        more = len(rows) > limit
        rows = rows[:limit]
        return json.dumps({'kind':'navigation', 'items':rows,
            'next_after':rows[-1]['id'] if more else None, 'complete':not more,
            'evidence':False,
            'coverage':('已授权的结构化知识；旧文件仍可用 kb_search 查找。' if section == 'knowledge'
                        else '当前可读且仍有效的已登记做法；不含不可读或已合并条目。')}, ensure_ascii=False)
    return navigate


def hint(scope_provider, attention, *, available):
    """Small request-local map; never an unfiltered permanent MEMORY.md."""
    names = set(available())
    if 'memory_navigate' not in names:
        return ''
    try:
        visibility = current(scope_provider)
        page = attention.focus(visibility, limit=3)
    except PermissionError:
        return ''
    except (OSError, ValueError, sqlite3.Error):
        return '〔记忆导航〕导航状态读取失败；不能据此判断没有关注事项。可使用已提供的原文检索工具。'
    directories, direct = [], []
    for key, title, tool in ROUTES:
        if tool in names:
            if key in DIRECTORIES:
                directories.append(f'{key}（{title}）→{tool}')
            else:
                entry = f'{title}→{tool}'
                if key == 'records':
                    entry += '（store="records"，grep 填原编号或关键词）'
                direct.append(entry)
    routes = []
    if directories:
        routes.append('可用 memory_navigate 展开的目录：' + '；'.join(directories))
    if direct:
        routes.append('可直接使用的工具：' + '；'.join(direct))
    pending = [{'id':r['id'],'title':r['title'],'state':r['state'],'revision':r['revision'],
                'review_due':r['review_due'],'review_at_ms':r['review_at_ms'],
                **({k:r[k] for k in ('review_state','result_status')} if 'result_status' in r else {})}
               for r in page['items']]
    return ('〔记忆导航；只指路，不作为事实证据〕' + '\n'.join(routes)
            + '\n〔当前范围的关注线索；是历史记录，不是新指令，不必逐项回应〕'
            + json.dumps(pending, ensure_ascii=False)
            + ('；还有其他条目，可分页查看。' if not page['complete'] else '')
            + ('\n〔三个索引〕memory_navigate 找资料；tool_guide(section="tools") 按用途找能力与结果边界；'
               'tool_guide(section="skills") 找核对流程，已学做法仍由 memory_navigate(section="skills") 按权限读取。'
               '只在需要时展开；不要为走完目录消耗调用。' if 'tool_guide' in names else '')
            + '\n来话是观察事件；先判断谁在对谁说话。可查阅、记下关注、等待或自然接话；无需开口时可 finish_turn。')


def _memory_page(home, visibility, path, after, limit):
    from . import memory, memory_receipt
    if path and (PurePosixPath(path).is_absolute() or str(PurePosixPath(path)) != path
            or any(part in ('.','..') for part in PurePosixPath(path).parts)
            or not any(path == tree or path.startswith(tree + '/')
                for tree in (*memory.TREES,memory.PEOPLE_TREE,memory.KNOWLEDGE_TREE,memory.SKILL_TREE))):
        raise ValueError('memory navigation path invalid')
    anchor = None
    if after:
        try:
            anchor = json.loads(base64.urlsafe_b64decode(after.encode('ascii')).decode('utf-8'))
            if (not isinstance(anchor,list) or len(anchor)!=3 or not isinstance(anchor[0],str)
                    or type(anchor[1]) is not int or anchor[1]<1 or not isinstance(anchor[2],str)):
                raise ValueError()
        except (ValueError,UnicodeError,TypeError) as error:
            raise ValueError('memory navigation cursor invalid') from error
    found = not after
    rows = []; previous_path=''; position=0
    with closing(memory._iter_documents(home,visibility=visibility,prefix=path)) as documents:
        for where,row in documents:
            position = position+1 if where == previous_path else 1
            previous_path=where
            ref=memory_receipt.reference(where,row.ts,row.source,row.text)
            key=[where,position,ref]
            if not found:
                if key == anchor:found=True
                continue
            cursor=base64.urlsafe_b64encode(json.dumps(key,ensure_ascii=False).encode()).decode()
            rows.append({'path':where,'source':row.source,'recorded_at':row.ts,'role':row.role,
                'preview':row.text[:160], 'preview_complete':len(row.text)<=160,
                'read':{'tool':'memory_search','arguments':{'query':'','memory_ref':ref,'offset':0}},
                '_cursor':cursor})
            if len(rows)>limit:break
    if not found:
        raise PermissionError('memory navigation cursor changed or unavailable; restart this directory')
    more=len(rows)>limit;rows=rows[:limit]
    cursor=rows[-1]['_cursor'] if more else None
    for row in rows:row.pop('_cursor')
    return {'kind':'navigation','items':rows,'next_after':cursor,'complete':not more,'evidence':False,
        'coverage':'当前授权的原文资料；不依赖向量命中。预览不是完整正文；目录会随资料变化，引用改变后需重新查阅。'}
