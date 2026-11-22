"""Scoped, durable concerns and work checkpoints; never execution authority.

The current scope is supplied by the host on every call. A saved intention is
not a user instruction, a wake grant, a sent reminder, or proof of completion.
Revisions and their history commit together. No user transcript is rewritten.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid

from .retrieval_scope import current
from .snapshot_json import loads
from .tools import ToolDef, ToolTag

STATES = ('open', 'waiting', 'paused', 'reported_done', 'cancelled')
FIELDS = frozenset({'title', 'next_step', 'waiting_for', 'evidence', 'state', 'review_at_ms'})
SCHEMA = 'core-attention-v1'
# Legacy closed interruption checkpoints remain visible without rewriting them.
OPEN_CLAUSE = "(state NOT IN ('reported_done','cancelled') OR (json_extract(body,'$.checkpoint.kind')='waiting' AND COALESCE(json_extract(body,'$.result_obligation.status'),'unverified')='unverified'))"

def _result_policy(body, requested_state=None):
    body = dict(body)
    if body.get('checkpoint', {}).get('kind') != 'waiting': return body
    obligation = dict(body.get('result_obligation') or {
        'status':'unverified', 'review_state':body['state'], 'outcome':'unknown', 'reference':''})
    if requested_state is not None: obligation['review_state'] = requested_state
    body['result_obligation'] = obligation
    if obligation['status'] == 'unverified':
        body['state'] = 'waiting'
        if not body['waiting_for'].strip():
            body['waiting_for'] = '业务结果仍待确认；复核完成不解除此项。等待明确结果或使用者明确取消。'
    return body

SELF_SCOPE = 'private:core:self'


class AttentionConflict(ValueError):
    pass


def _validate(body):
    if not isinstance(body, dict) or not FIELDS <= set(body) or set(body)-FIELDS-{'checkpoint','result_obligation'}:
        raise ValueError('attention fields invalid')
    for key, maximum in (('title', 160), ('next_step', 2000), ('waiting_for', 1000)):
        if not isinstance(body[key], str) or len(body[key]) > maximum:
            raise ValueError('attention text invalid: ' + key)
    if not body['title'].strip() or body['state'] not in STATES:
        raise ValueError('attention title/state invalid')
    if 'checkpoint' in body:
        from .activity_memory import validate_checkpoint
        validate_checkpoint(body['checkpoint'])
    obligation = body.get('result_obligation')
    if obligation is not None:
        if (not isinstance(obligation, dict) or set(obligation) != {'status','review_state','outcome','reference'}
                or body.get('checkpoint',{}).get('kind') != 'waiting'
                or obligation['status'] not in ('unverified','confirmed','cancelled')
                or obligation['review_state'] not in STATES
                or obligation['outcome'] not in ('unknown','succeeded','failed','cancelled')
                or not isinstance(obligation['reference'], str) or len(obligation['reference']) > 256
                or (obligation['status']=='unverified' and (obligation['outcome']!='unknown' or obligation['reference']))
                or (obligation['status']=='confirmed' and (obligation['outcome'] not in ('succeeded','failed') or not obligation['reference']))
                or (obligation['status']=='cancelled' and (obligation['outcome']!='cancelled' or not obligation['reference']))):
            raise ValueError('attention result obligation invalid')
        if obligation['review_state']=='reported_done' and not body['evidence']:
            raise ValueError('reported review completion requires evidence references')
    refs = body['evidence']
    if (not isinstance(refs, list) or len(refs) > 16
            or any(not isinstance(ref, str) or not ref or len(ref) > 256 for ref in refs)):
        raise ValueError('attention evidence invalid')
    at = body['review_at_ms']
    if at is not None and (type(at) is not int or not 0 <= at <= 253402300799999):
        raise ValueError('attention review time invalid')
    if body['state'] == 'reported_done' and not refs:
        raise ValueError('reported completion requires evidence references')
    # A completion report remains a report even with references; consumers
    # must read the source and assess what it actually proves.
    return json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _write_body(body):
    raw = _validate(body)
    if body['state'] == 'reported_done' and body['waiting_for'].strip():
        raise ValueError('reported_done conflicts with waiting_for; keep state=waiting while awaiting confirmation, or explicitly clear waiting_for if no follow-up remains')
    return raw


class AttentionStore:
    def __init__(self, root, *, clock, is_open=lambda: True):
        self.path = Path(root).absolute() / 'attention.sqlite3'
        self.clock = clock
        self.is_open = is_open

    def _paths_safe(self):
        for path in (self.path.parent, self.path, Path(str(self.path) + '-journal'),
                     Path(str(self.path) + '-wal'), Path(str(self.path) + '-shm')):
            try:
                value = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(value.st_mode):
                raise ValueError('attention path is a link')
            if path != self.path.parent and (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1):
                raise ValueError('attention path is not a private regular file')

    @contextmanager
    def _connection(self, *, write=False, before_commit=None):
        self._paths_safe()
        if write and not self.is_open():
            raise RuntimeError('attention store is stopped')
        if not self.path.exists() and not write:
            yield None
            return
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
        self._paths_safe()
        connection = sqlite3.connect(self.path.as_uri() + ('?mode=rw' if write else '?mode=ro'),
                                     uri=True, timeout=3)
        connection.row_factory = sqlite3.Row
        try:
            if write:
                connection.execute('PRAGMA synchronous=FULL')
                connection.execute('BEGIN IMMEDIATE')
            names = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if not names and not write and self.path.stat().st_size == 0:
                # A first write may roll back its schema together with the row.
                # Keep the zero-byte file for concurrent SQLite users; reads
                # see an empty store, not a permanently unreadable database.
                yield None
                return
            if not names and write:
                connection.execute('CREATE TABLE metadata (schema TEXT NOT NULL)')
                connection.execute('INSERT INTO metadata VALUES (?)', (SCHEMA,))
                connection.execute('CREATE TABLE concerns (sequence INTEGER PRIMARY KEY AUTOINCREMENT, '
                    'id TEXT UNIQUE NOT NULL, scope TEXT NOT NULL, origin TEXT NOT NULL, '
                    'source_ref TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, '
                    'state TEXT NOT NULL, review_at_ms INTEGER, updated_ms INTEGER NOT NULL)')
                connection.execute('CREATE INDEX concerns_scope ON concerns(scope, sequence)')
                connection.execute('CREATE TABLE revisions (id TEXT NOT NULL, revision INTEGER NOT NULL, '
                    'body TEXT NOT NULL, updated_ms INTEGER NOT NULL, PRIMARY KEY(id, revision))')
            elif names != {'metadata', 'concerns', 'revisions'}:
                raise ValueError('attention database schema invalid')
            if [r[0] for r in connection.execute('SELECT schema FROM metadata')] != [SCHEMA]:
                raise ValueError('attention database version invalid')
            yield connection
            if write:
                if before_commit is not None:
                    before_commit()
                if not self.is_open():
                    raise RuntimeError('attention store stopped before commit')
                connection.commit()
        finally:
            connection.close()  # Any uncommitted transaction rolls back.

    @staticmethod
    def _row(row):
        body = loads(row['body'])
        _validate(body)
        recorded_state = body['state']
        body = _result_policy(body)
        result = dict(id=row['id'], sequence=row['sequence'], scope=row['scope'],
                    origin=row['origin'], source_ref=row['source_ref'], revision=row['revision'],
                    updated_ms=row['updated_ms'], **body)
        if 'result_obligation' in body:
            result['review_state'] = body['result_obligation']['review_state']
            result['result_status'] = body['result_obligation']['status']
            if recorded_state != body['state']: result['recorded_state'] = recorded_state
        if recorded_state == 'reported_done' and body['waiting_for'].strip() and 'result_obligation' not in body:
            # Legacy data stays readable and byte-for-byte intact. An explicit
            # version-checked update must resolve this conflict; reads do not
            # rewrite history, reopen work or grant a wake.
            result['state_conflict'] = 'reported_done_with_waiting_for'
            result['needs_review'] = True
        return result

    def read(self, visibility, *, identifier='', before=None, limit=12, include_closed=False):
        visibility.require()
        if type(limit) is not int or not 1 <= limit <= 40:
            raise ValueError('attention limit must be 1..40')
        if before is not None and (type(before) is not int or before < 1):
            raise ValueError('attention cursor invalid')
        if not isinstance(identifier, str) or len(identifier) > 64 or type(include_closed) is not bool:
            raise ValueError('attention read arguments invalid')
        scopes = sorted(visibility.allowed)
        clauses = ['scope IN (' + ','.join('?' for _ in scopes) + ')']
        args = list(scopes)
        # Even the local administrator receives its current linked scopes,
        # not a global inventory of private/group concerns in a model prompt.
        if identifier:
            clauses.append('id=?'); args.append(identifier)
        elif not include_closed:
            clauses.append(OPEN_CLAUSE)
        if before is not None:
            clauses.append('sequence<?'); args.append(before)
        with self._connection() as db:
            if db is None:
                return {'items': [], 'next_before': None, 'complete': True}
            rows = db.execute('SELECT * FROM concerns WHERE ' + ' AND '.join(clauses)
                              + ' ORDER BY sequence DESC LIMIT ?', (*args, limit + 1)).fetchall()
            more = len(rows) > limit
            items = [self._row(row) for row in rows[:limit]]
        return {'items': items, 'next_before': items[-1]['sequence'] if more else None,
                'complete': not more}

    def focus(self, visibility, *, limit=3):
        """Small context hint: overdue intentions, paused work, then oldest concerns.

        This reads a clock and SQLite only. A review time grants neither a wake
        nor permission to execute; next eligible activity still decides what to do.
        """
        visibility.require()
        if type(limit) is not int or not 1 <= limit <= 12:
            raise ValueError('attention focus limit must be 1..12')
        at = self.clock()
        if type(at) is not int or at < 0:
            raise ValueError('attention clock invalid')
        scopes = sorted(visibility.allowed)
        with self._connection() as db:
            if db is None:
                return {'items': [], 'complete': True}
            rows = db.execute('SELECT * FROM concerns WHERE scope IN ('
                + ','.join('?' for _ in scopes) + ") AND " + OPEN_CLAUSE + " "
                "ORDER BY CASE WHEN review_at_ms IS NOT NULL AND review_at_ms<=? THEN 0 "
                "WHEN state='paused' THEN 1 ELSE 2 END, "
                'CASE WHEN review_at_ms<=? THEN review_at_ms ELSE NULL END, updated_ms, sequence LIMIT ?',
                (*scopes, at, at, limit+1)).fetchall()
            items = [self._row(row) for row in rows[:limit]]
        for item in items:
            item['review_due'] = item['review_at_ms'] is not None and item['review_at_ms'] <= at
        return {'items': items, 'complete': len(rows) <= limit}

    def update(self, visibility, *, changes, identifier='', expected_revision=None, source_ref='', checkpoint=None, _before_commit=None):
        visibility.require()
        if (not isinstance(changes, dict) or not changes or not set(changes) <= FIELDS
                or not isinstance(identifier, str) or len(identifier) > 64
                or not isinstance(source_ref, str) or len(source_ref) > 256):
            raise ValueError('attention update arguments invalid')
        if checkpoint is not None:
            from .activity_memory import validate_checkpoint
            validate_checkpoint(checkpoint)
        at = self.clock()
        if type(at) is not int or at < 0:
            raise ValueError('attention clock invalid')
        if not identifier:
            if expected_revision is not None:
                raise ValueError('new concern has no expected revision')
            body = dict(title='', next_step='', waiting_for='', evidence=[], state='open', review_at_ms=None)
            body.update(changes)
            if checkpoint is not None:
                body['checkpoint'] = checkpoint
            body = _result_policy(body, changes.get('state'))
            raw = _write_body(body)
        with self._connection(write=True, before_commit=_before_commit) as db:
            if identifier:
                if type(expected_revision) is not int or expected_revision < 1 or source_ref:
                    raise ValueError('update requires revision and cannot replace provenance')
                old = db.execute('SELECT * FROM concerns WHERE id=? AND scope=?',
                                 (identifier, visibility.current)).fetchone()
                if old is None:
                    raise PermissionError('attention item unavailable in current scope')
                if old['revision'] != expected_revision:
                    raise AttentionConflict('attention changed; read it again before updating')
                body = {**loads(old['body']), **changes}
                if checkpoint is not None:
                    body['checkpoint'] = checkpoint
                body = _result_policy(body, changes.get('state'))
                revision = expected_revision + 1
                raw = _write_body(body)
                db.execute('UPDATE concerns SET revision=?,body=?,state=?,review_at_ms=?,updated_ms=? '
                           'WHERE id=?', (revision, raw, body['state'], body['review_at_ms'], at, identifier))
            else:
                identifier, revision = 'att-' + uuid.uuid4().hex, 1
                db.execute('INSERT INTO concerns(id,scope,origin,source_ref,revision,body,state,review_at_ms,updated_ms) '
                           'VALUES (?,?,?,?,?,?,?,?,?)', (identifier, visibility.current,
                           'host_checkpoint' if checkpoint is not None else ('observed_event' if source_ref else 'agent_initiated'), source_ref,
                           revision, raw, body['state'], body['review_at_ms'], at))
            db.execute('INSERT INTO revisions VALUES (?,?,?,?)', (identifier, revision, raw, at))
        result = {'id': identifier, 'revision': revision, 'state': body['state'],
                'saved': True, 'execution_authorized': False, 'business_success_proven': False}
        if 'result_obligation' in body:
            result.update(review_state=body['result_obligation']['review_state'],result_status=body['result_obligation']['status'],
                boundary='复核状态与业务结果分开；未知结果持续挂起，只有宿主明确结果确认或使用者明确取消可解除。')
        return result


    def confirm_result(self, visibility, *, identifier, expected_revision, outcome, reference, verify):
        """Trusted host/operator entry point. Deliberately NOT a model tool.

        The caller owns outcome semantics. verify must validate the actual
        result/operator instruction again inside the commit boundary. A model
        assertion or a mere readable reference is not such confirmation.
        """
        visibility.require()
        if (not isinstance(identifier,str) or not identifier or len(identifier)>64
                or type(expected_revision) is not int or expected_revision<1
                or outcome not in ('succeeded','failed','cancelled')
                or not isinstance(reference,str) or not reference.strip() or len(reference)>256
                or not callable(verify)):
            raise ValueError('invalid host result confirmation')
        def recheck():
            if verify(visibility, reference, outcome) is not True:
                raise PermissionError('result confirmation unavailable or changed')
        recheck()
        with self._connection(write=True,before_commit=recheck) as db:
            old=db.execute('SELECT * FROM concerns WHERE id=? AND scope=?',(identifier,visibility.current)).fetchone()
            if old is None:raise PermissionError('attention item unavailable in current scope')
            if old['revision']!=expected_revision:raise AttentionConflict('attention changed; read again')
            body=_result_policy(loads(old['body']))
            obligation=body.get('result_obligation')
            if not obligation or obligation['status']!='unverified':raise ValueError('no unresolved host result')
            body['result_obligation']={**obligation,'status':'cancelled' if outcome=='cancelled' else 'confirmed',
                'outcome':outcome,'reference':reference}
            body['state']='cancelled' if outcome=='cancelled' else ('reported_done' if obligation['review_state']=='reported_done' else 'open')
            if body['state']=='reported_done' and reference.startswith(('record:','event:')):
                body['evidence']=list(dict.fromkeys([reference,*body['evidence']]))[:16]
            body['waiting_for']=''
            revision=expected_revision+1;at=self.clock()
            if type(at) is not int or at<0:raise ValueError('attention clock invalid')
            raw=_write_body(body)
            db.execute('UPDATE concerns SET revision=?,body=?,state=?,review_at_ms=?,updated_ms=? WHERE id=?',
                (revision,raw,body['state'],body['review_at_ms'],at,identifier))
            db.execute('INSERT INTO revisions VALUES (?,?,?,?)',(identifier,revision,raw,at))
        return {'id':identifier,'revision':revision,'state':body['state'],'result_obligation':body['result_obligation'],
                'saved':True,'execution_authorized':False,'replay_authorized':False}


DECLARATIONS = (
    ToolDef(name='attention_checkpoint', description='用平铺参数保存一项已有关注的原文证据检查点，适合本批收尾。不能新建事项；identifier 使用 attention_read 的 update_ref 或完整 id（完整 id 必须配 expected_revision）。evidence_ref 填一个已经核对的原文/结果引用；此保存不执行或重放业务。不填 status/waiting_for 时保留原状态和等待条件；确有完成证据才选 reported_done，并显式清空 waiting_for。所有权限、版本与引用校验沿用 attention_update。',
        parameters={'type':'object','properties':{
            'identifier':{'type':'string'},'evidence_ref':{'type':'string'},
            'expected_revision':{'type':'integer','minimum':1},
            'status':{'type':'string','enum':list(STATES)},
            'next_step':{'type':'string','maxLength':2000},
            'waiting_for':{'type':'string','maxLength':1000}},
            'required':['identifier','evidence_ref'],'additionalProperties':False},tags=(ToolTag.WRITE,)),
    ToolDef(name='attention_read', description='查看当前可见范围的关注事项与工作检查点；更新前优先取得本批 update_ref，避免手抄长编号。按返回引用继续查原文；这不是提醒送达或执行成功证明。',
        parameters={'type':'object','properties': {
            'identifier':{'type':'string'}, 'before':{'type':'integer','minimum':1},
            'limit':{'type':'integer','minimum':1,'maximum':40}, 'include_closed':{'type':'boolean'}},
            'additionalProperties':False}, tags=(ToolTag.READ,)),
    ToolDef(name='attention_update', description='记录或更新一件值得后续关注的事。若只为等一次结果，在 changes.waiting_for 写明还要等什么即可；仅在另有独立、值得记的人物事实或明确要求分别保留时，再另记画像。自身打算不等于用户授权。创建时不传 identifier 和 expected_revision；更新已有事项时先读取，优先把返回的 update_ref 放入 identifier；短引用绑定当时的 revision，expected_revision 可省略。仍可传完整 id，但必须同时传 revision 作为 expected_revision。短引用过期或冲突时重新读取，不猜改编号。所有待改字段（包括 evidence）都放在 changes 内。完成只登记为 reported_done，必须有原文证据引用，仍须核验。宿主中断的未知结果单独挂起：reported_done 只记复核完成，不能解除业务待确认；cancelled 也不能冒充使用者取消。waiting_for 非空表示仍待确认，须保持 waiting 等未关闭状态；确无待办时显式清空 waiting_for 后才可报完成。review_at_ms 只记录下次检查意向，不代替 set_timer。',
        parameters={'type':'object','properties':{
            'identifier':{'type':'string','description':'更新时优先填本批 attention_read 返回的 update_ref；完整 id 仍需 expected_revision。创建时省略。'}, 'expected_revision':{'type':'integer','minimum':1},
            'source_ref':{'type':'string','description':'创建时可填当前可读的 event:数字 或 record:完整原文引用；不填表示自身发起。'},
            'changes':{'type':'object','properties':{
                'title':{'type':'string','maxLength':160}, 'next_step':{'type':'string','maxLength':2000},
                'waiting_for':{'type':'string','maxLength':1000,'description':'尚在等待的回执或条件。非空时不可 reported_done；取消可保留历史等待说明。'},
                'evidence':{'type':'array','items':{'type':'string'},'maxItems':16,
                    'description':'只填本轮已读取且可验证的 event:游标、record:完整记录引用，或 read_events 返回的 evidence_ref 短别名。memory_ref、目录标题和自己的陈述不是这种引用；没有原文引用时保持空数组，不要猜造。已确认的阅读进度必须选覆盖该进度的用户原文结果引用，用户提问或无关查询回执不能代替；无支持时写成待核实意向。'},
                'state':{'type':'string','enum':list(STATES)},
                'review_at_ms':{'type':['integer','null'],'minimum':0}},'additionalProperties':False}},
            'required':['changes'],'additionalProperties':False}, tags=(ToolTag.WRITE,)),
)


class AttentionHandles:
    """Ephemeral exact locators, never storage IDs or grants of write authority."""
    def __init__(self, limit=256):
        if type(limit) is not int or not 1 <= limit <= 2048:
            raise ValueError('attention handle limit invalid')
        self.limit = limit
        self.owner = None
        self.visibility = None
        self.entries = {}
        self.nonce = ''
        self.serial = 0

    def bind(self, owner, visibility):
        if owner is None or owner is not self.owner or visibility != self.visibility:
            from secrets import token_hex
            self.owner, self.visibility = owner, visibility
            self.entries.clear()
            self.nonce = token_hex(4)
            self.serial = 0

    def issue(self, identifier, revision, scope):
        if self.owner is None:
            return None
        target = (identifier, revision, scope)
        for ref, entry in self.entries.items():
            if entry == target:
                self.entries[ref] = self.entries.pop(ref)
                return ref
        if len(self.entries) >= self.limit:
            self.entries.pop(next(iter(self.entries)))  # Keep this response's new locators alive.
        self.serial += 1
        ref = f'attention:{self.nonce}:{self.serial}'
        self.entries[ref] = target
        return ref

    def resolve(self, identifier, revision=None, *, writing=False):
        if not isinstance(identifier, str) or len(identifier) > 64:
            raise ValueError('attention identifier invalid')
        if not identifier.startswith('attention:'):
            return identifier, revision
        target = self.entries.get(identifier)
        if target is None:
            raise PermissionError('attention handle expired or unavailable; call attention_read again')
        key, observed_revision, scope = target
        if writing:
            if self.visibility.current != scope:
                raise PermissionError('attention item is read-only in current scope')
            if revision is not None:
                if type(revision) is not int:
                    raise ValueError('expected_revision must be an integer')
                if revision != observed_revision:
                    raise AttentionConflict('attention handle revision mismatch; call attention_read again')
            revision = observed_revision
        return key, revision


def handlers(store, scope_provider, *, check_reference, resolve_reference=None, batch_identity=None, complete_evidence=None, project_checkpoint=None, check_support=None):
    from threading import RLock
    if batch_identity is not None and not callable(batch_identity):
        raise TypeError('batch_identity must be callable')
    handles, lock = AttentionHandles(), RLock()

    def snapshot():
        visibility = current(scope_provider)
        owner = batch_identity() if batch_identity is not None else None
        handles.bind(owner, visibility)
        return visibility, owner

    def verify(visibility, owner):
        if current(scope_provider) != visibility:
            raise PermissionError('attention scope changed')
        if batch_identity is not None and batch_identity() is not owner:
            raise PermissionError('attention batch changed; call attention_read again')

    def read(**arguments):
        with lock:
            if set(arguments) - {'identifier','before','limit','include_closed'}:
                raise ValueError('attention_read accepts identifier, before, limit and include_closed only')
            visibility, owner = snapshot()
            if 'identifier' in arguments:
                arguments['identifier'], _ = handles.resolve(arguments['identifier'])
            page = store.read(visibility, **arguments)
            if project_checkpoint is not None:
                for row in page['items']:
                    projected = project_checkpoint(row, visibility)
                    if projected is not None: row['checkpoint_evidence'] = projected
            verify(visibility, owner)
            if owner is not None:
                for row in page['items']:
                    ref = handles.issue(row['id'], row['revision'], row['scope'])
                    row['read_ref'] = ref
                    row['writable_in_current_scope'] = row['scope'] == visibility.current
                    row['update_ref'] = ref if row['writable_in_current_scope'] else None
                page['handle_scope'] = '本批及当前权限内的精确定位；更新绑定读取时版本。过期或冲突请重新 attention_read，不模糊修正，不跨批沿用。'
            return json.dumps(page, ensure_ascii=False)

    def update(*, changes, identifier='', expected_revision=None, source_ref='', **unexpected):
        with lock:
            if unexpected:
                raise ValueError('attention_update accepts changes, identifier, expected_revision and source_ref only; put evidence and other changed fields inside changes (changes.evidence)')
            visibility, owner = snapshot()
            identifier, expected_revision = handles.resolve(identifier, expected_revision, writing=True)
            if not isinstance(changes, dict):
                raise ValueError('attention changes must be an object')
            refs = changes.get('evidence', [])
            support_changes = changes
            support_fields = {'title','next_step','waiting_for','evidence'}
            support_update = bool(support_fields & set(changes)) or changes.get('state') == 'reported_done'
            if identifier and ('evidence' not in changes or (check_support is not None and support_update)):
                existing = store.read(visibility, identifier=identifier)['items']
                if not existing:
                    raise PermissionError('attention item unavailable; call attention_read again and use update_ref')
                if 'evidence' not in changes: refs = existing[0]['evidence']
                if check_support is not None and support_update:
                    support_changes = {key:existing[0][key] for key in ('title','next_step','waiting_for')}
                    support_changes.update(changes)
            if not isinstance(refs, list) or len(refs) > 16:
                raise ValueError('attention evidence invalid')
            if resolve_reference is not None:
                refs = [resolve_reference(visibility, ref) for ref in refs]
                changes = dict(changes)
                if 'evidence' in changes: changes['evidence'] = refs
                if source_ref: source_ref = resolve_reference(visibility, source_ref)
            requested_refs = list(refs)
            if complete_evidence is not None:
                refs = complete_evidence(visibility, requested_refs)
                if (not isinstance(refs, list) or len(refs) > 16
                        or any(ref not in refs for ref in requested_refs)):
                    raise ValueError('invalid completed evidence set')
                changes = dict(changes, evidence=refs)
            added_refs = [ref for ref in refs if ref not in requested_refs]
            for ref in ([source_ref] if source_ref else []) + refs:
                if not check_reference(visibility, ref):
                    raise PermissionError('attention reference unavailable; read original evidence first')
            support = check_support(visibility, support_changes, refs) if check_support is not None else {}
            verify(visibility, owner)
            def before_commit():
                verify(visibility, owner)
                if complete_evidence is not None and complete_evidence(visibility, requested_refs) != refs:
                    raise PermissionError('paired tool evidence changed before commit; read original again')
                # Database lock waits and source reads can outlive the batch or
                # grants under which this action began. Any failure here rolls
                # back both the concern and its revision history.
                verify(visibility, owner)
                for ref in ([source_ref] if source_ref else []) + refs:
                    if not check_reference(visibility, ref):
                        raise PermissionError('attention reference changed before commit; read original again')
                if check_support is not None and check_support(visibility, support_changes, refs) != support:
                    raise PermissionError('attention factual support changed before commit')
                verify(visibility, owner)
            result = store.update(visibility, changes=changes, identifier=identifier,
                                  expected_revision=expected_revision, source_ref=source_ref,
                                  _before_commit=before_commit)
            if support: result.update(support)
            if added_refs:
                result['host_added_result_evidence'] = added_refs
                result['evidence_meaning'] = '宿主补入同一调用的已落盘结果原文；可能是失败或结果未知，不证明业务成功。'
            if owner is not None:
                result['update_ref'] = handles.issue(result['id'], result['revision'], visibility.current)
            return json.dumps(result, ensure_ascii=False)

    def checkpoint(*, identifier, evidence_ref, expected_revision=None, status='', next_step='', waiting_for=None):
        if not isinstance(identifier, str) or not identifier.strip() or not isinstance(evidence_ref, str) or not evidence_ref.strip():
            raise ValueError('checkpoint requires an existing identifier and original evidence reference')
        changes = {'evidence':[evidence_ref]}
        if status: changes['state']=status
        if waiting_for is not None: changes['waiting_for']=waiting_for
        if next_step: changes['next_step']=next_step
        return update(identifier=identifier, expected_revision=expected_revision, changes=changes)

    return {'attention_read': read, 'attention_update': update, 'attention_checkpoint': checkpoint}
