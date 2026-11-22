"""Host checkpoints record what was observed; they never authorize replay."""
import hashlib
import json

from .retrieval_scope import current


def validate_checkpoint(value):
    if (not isinstance(value, dict) or set(value) != {'kind','records','tail_only','replay_permitted'}
            or value['kind'] not in ('paused','waiting') or value['tail_only'] is not True
            or value['replay_permitted'] is not False or not isinstance(value['records'], list)
            or len(value['records']) > 32):
        raise ValueError('activity checkpoint invalid')
    for row in value['records']:
        if (not isinstance(row, dict) or set(row) != {'name','role','kind','tool_ref','record_ref','sha256'}
                or any(not isinstance(v,str) or len(v) > 256 for v in row.values())
                or len(row['sha256']) != 64 or any(c not in '0123456789abcdef' for c in row['sha256'])):
            raise ValueError('activity checkpoint record invalid')
    if len(json.dumps(value, ensure_ascii=False).encode()) > 24576:
        raise ValueError('activity checkpoint too large')


def bind(console, store, *, scope_provider, check_reference, resume_scope, event_matches=lambda event: False):
    def checkpoint(state):
        visibility = current(scope_provider)
        refs = []
        for event in console.original_batch():
            ref = 'event:' + str(getattr(event, 'cursor', ''))
            if event_matches(event) and check_reference(visibility, ref):
                refs.append(ref)
            if len(refs) == 16:
                break
        records = []
        for row in console.session.recent_records(32):
            # Do not copy another audience's tool facts into this checkpoint.
            if row.scope != visibility.current or row.role not in ('tool','assistant'):
                continue
            records.append(dict(name=row.name, role=row.role, kind=row.kind, tool_ref=row.tool_ref,
                record_ref=row.record_ref, sha256=hashlib.sha256(row.text.encode()).hexdigest()))
        saved = store.update(visibility, changes={
            'title':'暂歇活动：待复核后接续' if state == 'paused' else '中断活动：执行结果待复核',
            'state':state, 'next_step':'先核对原始消息与工具回执，再决定继续、调整、等待或取消。'
                'checkpoint 只列最近记录的定位信息和内容摘要值，不证明工具成功；'
                '先用 attention_read 核对 checkpoint_evidence：text_complete=true 的原文已完整返回，不必再搜索同一记录；缺少原文时按 record_ref 补读，不重放已完成或结果未知的操作。'
                '中断后的业务结果待确认与复核状态分开，复核完成不会解除未知结果挂起。',
            'evidence':refs}, checkpoint={'kind':state, 'records':records,
                'tail_only':True, 'replay_permitted':False})
        return {**saved, 'scope':visibility.current}

    def resume_valid(saved, originals, *, pending=True):
        try:
            visibility = resume_scope(originals)
            visibility.require()
            if visibility.current != saved['scope']:
                return False
            check = getattr(console, 'event_filter', None)
            if callable(check) and not all(check(event) for event in originals):
                return False
            if not pending:
                return True
            rows = store.read(visibility, identifier=saved['id'])['items']
            return bool(rows and rows[0]['revision'] == saved['revision'] and rows[0]['state'] == 'paused')
        except Exception:
            # Unavailable evidence/identity can never become an automatic retry.
            return False

    console.activity_checkpoint = checkpoint
    console.activity_resume_valid = resume_valid
