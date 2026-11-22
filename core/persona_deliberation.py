"""Conversation-led persona decisions; files, versions and audit are system-owned."""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from telyincore import memory, web
from telyincore.session_view import event_scope
from telyincore.source_guard import lease

WORKFLOW = '〔设定修订与自主性〕\n你或对方都可以提出修订。是否讨论、何时讨论、查什么工具、是否修改由你结合上下文判断，\n没有必须依次调用的工具，也没有好感、情绪或次数达到阈值就必须改的规则。\n需要交流时自然提出并听取回应；既可接受建议，也可保留、暂缓或拒绝，不把建议当作命令。\n局部场景的做法不必升级为全局宪法。propose_change 记录具体章节的讨论稿，回执给出ID；\npending_read 可重读。形成判断后用 decide_change 决定 apply、keep 或 defer，写明理由；\nevidence 可引用本轮对话原话，没有本轮来话时不编造回应。异步独处或后续事件中也可以继续思考。\n系统保存章节版本、修订前后内容、决定和依据。工具失败不算生效；待议条目不会自动变成生效设定。\n这取代旧档案中必须网页审批的流程，但不能改写程序权限、身份绑定、来源隔离和工具真实性约束。\n'


def pending_for_scope(home: Path, scope: str):
    rows = memory.pending_of(home)
    if not scope:
        return rows  # autonomous local reflection has no external recipient
    if scope == 'mixed':
        return []
    return [row for row in rows if (row.proposal or {}).get('scope') == scope
            or (not (row.proposal or {}).get('scope') and not scope.startswith('group:'))]


def decide(home: Path, *, proposal_id: str, decision: str, reason: str, evidence: str = "",
           events=(), scope: str = '') -> str:
    if decision not in {'apply', 'keep', 'defer'} or not reason.strip():
        raise ValueError('须填写有效决定及理由。')
    matching = [e for e in events if getattr(e, 'type', '') == 'message'
                and getattr(e, 'origin', '') == 'external'
                and event_scope(e) == scope and evidence.strip()
                and evidence.strip() in str(getattr(e, 'text', ''))]
    if evidence.strip() and not matching:
        raise ValueError('所引原话不在本轮对话中；不要编造对方的回应。')
    if scope == 'mixed':
        raise ValueError('混合通道需先明确修订讨论的来源范围。')
    with lease(home):
        row = next((r for r in memory.pending_of(home, proposed='persona')
                    if (r.proposal or {}).get('id') == proposal_id), None)
        if row is None:
            raise ValueError('提议不存在或已经落定；请先 pending_read。')
        proposal = row.proposal
        if scope.startswith('group:') and not proposal.get('scope'):
            raise ValueError('旧提议缺少交流范围，不能在群聊中落定。')
        if scope and proposal.get('scope') and proposal['scope'] != scope:
            raise ValueError('本轮交流范围与提议不同，不能跨通道落定。')
        now = datetime.datetime.now()
        ts = now.isoformat()
        stamp = now.strftime('%Y%m%d-%H%M%S')
        target = home / '修订记录.jsonl'
        target.parent.mkdir(parents=True, exist_ok=True)
        # Preflight the journal before touching the persona. A write failure is visible.
        record = dict(ts=ts, source=row.source, role='assistant', scope=proposal.get('scope') or scope,
                      text=f'设定修订：{decision}。理由：{reason.strip()}\n对话依据：{evidence.strip()}',
                      proposal_id=proposal_id, decision=decision, section=proposal['section'],
                      before=proposal['before'], proposed=row.text,
                      evidence_cursors=[e.cursor for e in matching])
        # Retry after an interrupted write must not overwrite the same section twice.
        prior = [json.loads(line) for line in target.read_text(encoding='utf-8').splitlines()
                 if line.strip()] if target.exists() else []
        interrupted = any(r.get('proposal_id') == proposal_id and r.get('decision') == decision
                          and r.get('stage') == 'intent' for r in prior)
        with target.open('a', encoding='utf-8', newline='') as journal:
            journal.write(json.dumps(dict(record, stage='intent'), ensure_ascii=False) + '\n')
            journal.flush()
            os.fsync(journal.fileno())
            if decision == 'apply':
                port = web.PersonaPort(home.parent / 'persona')
                info = port.file_info(row.source)
                current = next((s['body'] for s in info.get('sections', ())
                                if s['id'] == proposal['section']), None)
                if interrupted and current is not None and current.strip() == row.text.strip():
                    record['recovered_existing_write'] = True
                else:
                    port.save_section(row.source, proposal['section'], row.text, stamp=stamp,
                                      slot='conversation', expect=proposal['revision'])
            try:
                journal.write(json.dumps(dict(record, stage='completed'), ensure_ascii=False) + '\n')
                journal.flush()
                os.fsync(journal.fileno())
            except OSError:
                if decision == 'apply':
                    return '章节正文已保存，但修订日志未能完成；待议条目仍保留，请核对后恢复记录，不要重复改稿。'
                raise
        # The journal is authoritative; this projection is readable by memory tools.
        warning = ''
        try:
            path = home / '经历/整理/设定修订.jsonl'
            previous = memory.read_a_transcript(path) if path.is_file() else []
            memory.write_the_transcript(home, '设定修订.jsonl',
                [*previous, memory.Remembered(ts=ts, valid_from=ts, source=row.source,
                   role='assistant', text=record['text'], scope=record['scope'])], tree=memory.TREES[2])
        except (OSError, ValueError):
            warning = '（决定已记入修订日志，但经历页投影失败，需核对修订记录.jsonl。）'
        if decision != 'defer':
            try:
                removed = memory.resolve_pending(home, proposal_id, proposed='persona')
            except (OSError, ValueError):
                removed = False
            if not removed:
                warning += '（决定已经完成，待议条目未能收起；不要再次修订同一章节。）'
        return {'apply': '已按你的判断修订该章节，历史与理由已保存。',
                'keep': '已决定保留原设定，理由已保存，提议已结束。',
                'defer': '已记录暂缓理由，原设定未改，提议继续保留。'}[decision] + warning
