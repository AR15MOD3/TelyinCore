"""Per-console communication preferences, separate from personality and account links."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Callable

from telyincore.tools import ToolDef, ToolTag
from telyincore.session_view import batch_scope
from runtime_identity import safe_path

SCOPE = 'private:console:local-owner'
SINGLE_DAYS = 7
REPEATED_DAYS = 30
MAX_BYTES = 512_000

DECLARATION = ToolDef(
    name='communication_preference',
    description=(
        '读写当前控制台用户明确反馈的交流偏好，不写人格、自述或习惯。'
        'read 查看当前参考；record 记录新主题；revise 更新同一主题；withdraw 撤回。'
        '写入必须引用本轮该用户原话 evidence，不能用沉默、继续聊天或你自己的评价作依据。'
        'text 只概括原话支持的偏好，context 写适用情境；一次纠正保持情境性，不能概括成永远或所有场合。'
        '更新或撤回用原 topic；旧版本仍保留。不要把交流偏好再抄进人物事实或自己的性格。'
        '单次反馈参考7天，同一偏好获不同来话再次明确支持后参考30天；到期退出提示但保留记录。'
        '这些是可修正的参考，保持自己的语气和判断；没有明确反馈不必调用。'),
    parameters={'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['read', 'record', 'revise', 'withdraw']},
        'topic': {'type': 'string', 'description': '简短稳定的主题名；更新时沿用原主题'},
        'text': {'type': 'string', 'description': '仅由原话支持的偏好概括，不写固定台词'},
        'context': {'type': 'string', 'description': '具体适用情境，保留原反馈的条件'},
        'evidence': {'type': 'string', 'description': '本轮控制台用户明确反馈的原话片段，逐字引用'}},
        'required': ['action'], 'additionalProperties': False},
    tags=(ToolTag.READ, ToolTag.WRITE))


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _field(value, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(name + ' 缺失或过长')
    return value.strip()


class PreferenceMemory:
    def __init__(self, home: Path, batch: Callable, now: Callable = None):
        self.home = Path(home)
        self.path = self.home / '交流偏好.json'
        self.batch = batch
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _current(self):
        batch = tuple(self.batch())
        if (not batch or batch_scope(batch) != SCOPE
                or not all(getattr(e, 'origin', '') == 'external'
                           and getattr(e, 'source', '') == 'console'
                           and getattr(e, 'sender_key', '') == 'console:local-owner'
                           and getattr(e, 'type', '') == 'message' for e in batch)):
            raise ValueError('交流偏好只用于当前控制台用户的直接来话')
        return batch

    def _read(self):
        safe_path(self.home, self.path)
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return None, {'version': 1, 'scope': SCOPE, 'items': {}, 'history': []}
        if len(raw) > MAX_BYTES:
            raise ValueError('交流偏好记录过大，停止写入')
        data = json.loads(raw)
        if (not isinstance(data, dict) or data.get('version') != 1
                or data.get('scope') != SCOPE or not isinstance(data.get('items'), dict)
                or not isinstance(data.get('history'), list)):
            raise ValueError('交流偏好记录不可读，保留原文件')
        for key, row in data['items'].items():
            if (not isinstance(row, dict) or key != row.get('topic')
                    or not isinstance(row.get('evidence'), list)
                    or not row['evidence'] or not isinstance(row.get('active'), bool)):
                raise ValueError('交流偏好条目格式异常')
            datetime.fromisoformat(row['expires'])
        return raw, data

    def view(self):
        self._current()
        _, data = self._read()
        now = self.now()
        return [row for row in data['items'].values()
                if row['active'] and datetime.fromisoformat(row['expires']) > now]

    def hint(self) -> str:
        try:
            rows = self.view()
        except (OSError, ValueError, TypeError, KeyError):
            return ''
        if not rows:
            return ''
        return ('〔当前用户的交流偏好参考〕只在相应情境参考，保持自己的性格和表达；'
                '单次反馈不推广到其他场合，不当作永久规则。后来的明确反馈可以修正或撤回。\n'
                + '\n'.join(json.dumps({k: row[k] for k in
                    ('topic', 'text', 'context', 'basis')}, ensure_ascii=False) for row in rows))

    def handle(self, action: str, topic: str = '', text: str = '',
               context: str = '', evidence: str = '') -> str:
        try:
            return self._handle(action, topic, text, context, evidence)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return '交流偏好未变更：' + str(exc)[:160]

    def _handle(self, action, topic, text, context, evidence):
        batch = self._current()
        if action == 'read':
            return self.hint() or '当前没有适用的交流偏好参考。'
        if action not in ('record', 'revise', 'withdraw'):
            raise ValueError('未知操作')
        topic = _field(topic, 'topic', 60)
        # Preserve quotation bytes for the evidence check, not a paraphrase.
        if not isinstance(evidence, str) or not 2 <= len(evidence.strip()) <= 500:
            raise ValueError('需要本轮明确反馈的原话')
        matches = [e for e in batch if evidence in str(getattr(e, 'text', ''))]
        if not matches:
            raise ValueError('原话不在本轮控制台来话中')
        event = matches[-1]
        event_id = _hash(json.dumps([str(getattr(event, k, '')) for k in
            ('source', 'sender_key', 'cursor', 'ts', 'text')], ensure_ascii=False).encode('utf8'))
        if action != 'withdraw':
            text = _field(text, 'text', 300)
            context = _field(context, 'context', 200)
        self.home.mkdir(parents=True, exist_ok=True)
        safe_path(self.home, self.home)
        lock = safe_path(self.home, self.home / '.communication-preferences.lock')
        with lock.open('x') as lock_handle:
            try:
                raw, data = self._read()
                old = data['items'].get(topic)
                if old and old['active'] and action == 'record':
                    if (old['text'], old['context']) != (text, context):
                        raise ValueError('同主题已有记录；请用 revise 并保留适用条件')
                if not old and action in ('revise', 'withdraw'):
                    raise ValueError('该主题不存在')
                if old and any(e['id'] == event_id for e in old['evidence']):
                    # Tool retries must not turn one human message into repeated support.
                    if action == 'withdraw' and not old['active']:
                        return '该交流偏好已撤回，没有重复写入。'
                    if old['active'] and action != 'withdraw' and (old['text'], old['context']) == (text, context):
                        return '这次反馈已经记过，没有重复写入。'
                    raise ValueError('同一条来话不能重复改写或算作再次确认')
                now = self.now()
                live_count = sum(row['active'] and datetime.fromisoformat(row['expires']) > now
                                 for row in data['items'].values())
                if live_count >= 20 and not old:
                    raise ValueError('交流偏好主题已达上限，请先整理现有主题')
                support = list(old['evidence']) if old and old['active'] else []
                # A changed preference starts with the new feedback; old, conflicting
                # evidence remains in history, never counted as support for the revision.
                if old and (old['text'], old['context']) != (text, context):
                    support = []
                support.append({'id': event_id, 'quote': evidence, 'at': now.isoformat()})
                repeated = len(support) >= 2
                if old:
                    data['history'].append(old)
                data['items'][topic] = {
                    'topic': topic, 'text': old['text'] if action == 'withdraw' else text,
                    'context': old['context'] if action == 'withdraw' else context,
                    'basis': '重复明确反馈' if repeated else '单次情境反馈',
                    'active': action != 'withdraw', 'evidence': support,
                    'expires': (now + timedelta(days=REPEATED_DAYS if repeated else SINGLE_DAYS)).isoformat()}
                wire = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf8')
                if len(wire) > MAX_BYTES:
                    raise ValueError('交流偏好记录已达容量上限；原记录未裁剪')
                temp = safe_path(self.home, self.path.with_suffix('.json.tmp'))
                created = False
                try:
                    with temp.open('xb') as stream:
                        created = True
                        stream.write(wire)
                        stream.flush()
                        os.fsync(stream.fileno())
                    current = self.path.read_bytes() if self.path.exists() else None
                    if current != raw:
                        raise ValueError('记录被并发修改；未覆盖')
                    if raw is not None:
                        backup = safe_path(self.home, self.path.with_suffix('.json.bak'))
                        shutil.copy2(self.path, backup)
                    if (self.path.read_bytes() if self.path.exists() else None) != raw:
                        raise ValueError('记录被并发修改；未覆盖')
                    os.replace(temp, self.path)
                finally:
                    if created:
                        temp.unlink(missing_ok=True)
                return ('已撤回这项交流偏好，旧版本保留。' if action == 'withdraw' else
                        '已记录这次明确反馈及适用情境，旧版本保留；它是可更新的参考，不改写人格。')
            finally:
                lock_handle.close()
                lock.unlink(missing_ok=True)


def self_memory_status(home: Path, *, include_guidance=True) -> str:
    """Describe real writable resources without requiring a model to fill them."""
    from telyincore.web import HER_FILES, PersonaPort
    book = PersonaPort(Path(home).parent / 'persona')
    states = []
    for name in HER_FILES:
        try:
            content = book.path_of(name).read_text(encoding='utf8')
            status = '目前为空' if not content.strip() else '已有内容'
        except (OSError, UnicodeError):
            status = '本次不可读'
        states.append({'世界认知':'观察与更正','习惯':'作息与习惯'}.get(Path(name).stem,Path(name).stem) + '：' + status)
    state = '〔自己的记录状态〕' + '；'.join(states) + '。'
    return state + (SELF_MEMORY_GUIDANCE if include_guidance else '')


SELF_MEMORY_GUIDANCE = (
    '自述可用 self_account_add/self_account_set 更新；观察与更正、作息与习惯在整理态更新。self_observe_add 写待思考的自我观察，不是直接编辑观察与更正。'
    '有自己的新认识才写，空白允许保留；别抄设定凑数。'
    '对方的交流偏好记在对方的偏好记录里，不能自动变成你的性格或习惯。')
