"""Narrow, batch-local evidence for statements about a specific visible read."""
from dataclasses import dataclass
import re
from typing import Callable


@dataclass(frozen=True, eq=False)
class ReadProof:
    kind: str
    ident: str
    query: str
    scope: object
    scope_of: Callable

    def available(self):
        from .retrieval_scope import RetrievalScope
        if not isinstance(self.scope, RetrievalScope):
            return False
        try:
            current = self.scope_of()
            current.require()
            return current == self.scope
        except (OSError, ValueError, RuntimeError, AttributeError):
            return False


class CheckedRead(str):
    """The dispatcher preserves native string results. Serialized copies carry no grant."""

    def __new__(cls, text, proofs=()):
        result = super().__new__(cls, text)
        result.proofs = proofs
        return result

    def __reduce__(self):
        return str, (str(self),)


def update_read_proofs(facts, name, result):
    if name not in ('timer_read', 'timer_history', 'settime', 'set_timer', 'timer_cancel', 'timer_resolve'):
        return
    for old in tuple(facts):
        if isinstance(old, ReadProof) and (name != 'timer_history' or old.kind == 'empty_receipts'):
            facts.discard(old)
    if isinstance(result, CheckedRead) and name in ('timer_read', 'timer_history'):
        facts.update(result.proofs)
        result.proofs = ()


_PENDING = r'(?:挂起|挂着|待确认|待处理)'
_OUTCOME = r'(?:已(?:经)?(?:被)?|已被)?(?:完成|取消)(?:状态)?'
_CURRENT_CLASSIFICATION = re.compile(
    r'(?:它|这项|这条)?(?:目前|现在|当前)?'
    r'(?:(?:不是|并非)' + _OUTCOME + r'(?:[，,、](?:也)?(?:不是|并非)' + _OUTCOME + r')?'
    r'[，,](?:现在|目前|当前)?(?:就|还|仍)?(?:是|在)?' + _PENDING + r'|'
    r'(?:还|仍)?(?:是|在)?' + _PENDING + r'(?:状态)?[，,](?:不是|并非)' + _OUTCOME + r')[。]?')


def _current_pending_subject(text, ident):
    """Local antecedent of a separate state predicate, scoped to one selected ID."""
    text = text.strip()
    first = re.split(r'[，,；;]', text, maxsplit=1)[0]
    if re.search(r'旧|另一|其他|上次|以前|从前|当时|假如|如果|假设|可能|也许|会不会|是否|[?？]', text):
        return False
    # No unrecognized identifier can take over an otherwise implicit subject.
    exact_id = re.compile(r'(?<![A-Za-z0-9_-])' + re.escape(ident) + r'(?![A-Za-z0-9_-])')
    if re.search(r'[A-Za-z0-9]+[-_][A-Za-z0-9_-]+|(?<![A-Za-z0-9])[0-9a-f]{8,}', exact_id.sub('', text)):
        return False
    if not re.search(_PENDING, first):
        return False
    if exact_id.search(first):
        return bool(re.search(r'当前|目前|现在|仍|还', first))
    return bool(re.search(r'(?:当前|目前|现在|这条|这项|该)(?:的)?(?:待办|提醒|事项)|'
                          r'待办(?:这一格|的状态)', first))


_EMPTY_SCHEDULE = re.compile(r'(?:同一份|这份|该)状态回执的未触发(?:的)?排程列表(?:里|中)没有条目')
_EMPTY_CLOSE_TIME = re.compile(r'没有(?:完成或取消|取消或完成)的落账时间')
_UNMARKED_OUTCOME = re.compile(r'(?:既)?没(?:有)?标(?:为|记为|成)?完成(?:，?也|或)没(?:有)?标(?:为|记为|成)?取消')
_SCHEDULE_COUNT = re.compile(
    r'\s*(?P<scope>(?:同一|本|这)(?:次|份)(?:状态)?查询(?:结果)?(?:里|中)?)?'
    r'未触发(?:的)?排程(?:这(?:一)?栏)?(?:为|是|共(?:有)?)?\s*0\s*条'
    r'(?:[，,]这只限于本次关键词与本页范围[，,]不证明过去没有排过)?[。]?\s*')
_STAGE = r'(?:唤醒|发言准入|送达|已读)'
_EMPTY_STAGE_ENTRIES = re.compile(
    r'按这个提醒编号查的回执(?:已经|已)?(?:列完|查完)[，,]可见阶段里没有'
    r'(?:到期检测之后的)?' + _STAGE + r'(?:(?:、|或|和)' + _STAGE + r')*(?:的)?条目')
_NAMED_ID = re.compile(r'(?:编号|\bID\b)\s*[:：]?\s*`?([a-zA-Z0-9][a-zA-Z0-9_-]*)', re.I)


def field_observations(part, facts, preceding=''):
    """Mark verified field descriptions for the absence checker. Keep the rest of the claim."""
    if not any(word in part for word in ('落账时间','排程','可见阶段里','标完成','标为完成','标记为完成','标成完成')):
        return part
    reads = [proof for proof in facts if isinstance(proof, ReadProof) and proof.available()]
    pending = {proof.ident for proof in reads if proof.kind == 'pending_only'}
    named = set(_NAMED_ID.findall(part))
    for proof in reads:
        if named and named != {proof.ident}:
            continue
        if proof.kind == 'pending_only':
            if proof.query in part and '当前状态' in part:
                part = _EMPTY_CLOSE_TIME.sub('关闭时间字段为空', part)
                part = _EMPTY_SCHEDULE.sub('本次排程列表为空', part)
            if named == {proof.ident} and re.search(r'(?:当前|现在)状态|状态(?:是|为)?\s*(?:待确认|deferred)', part):
                part = _UNMARKED_OUTCOME.sub('关闭字段未标记', part)
            count = _SCHEDULE_COUNT.fullmatch(part)
            if count:
                previous_ids = set(_NAMED_ID.findall(preceding))
                if not previous_ids - {proof.ident} and (count['scope'] or previous_ids == {proof.ident}):
                    return '本次所查排程栏为零条。'
        elif proof.kind == 'empty_receipts' and pending == {proof.ident}:
            part = _EMPTY_STAGE_ENTRIES.sub('按此编号读完的阶段表为空', part)
    return part


def checked_statement(part, facts, *, preceding=''):
    """Match a complete, explicitly delimited observation; leave any extra claim to review."""
    for proof in facts:
        if not isinstance(proof, ReadProof):
            continue
        if proof.kind == 'pending_only':
            present = _CURRENT_CLASSIFICATION.fullmatch(part.strip())
            subject = present and _current_pending_subject(preceding, proof.ident)
            if present and subject and proof.available():
                return True
            topic = re.escape(proof.query)
            matched = re.fullmatch(
                r'\s*(?:我)?(?:这次|本次)?按[「“『"]?' + topic + r'[」”』"]?(?:这个)?关键词'
                r'(?:查(?:询|找)?|匹配)[，,]\s*未触发的排程(?:没有匹配到(?:条目|记录)|(?:为|是)?\s*0\s*条)[。]?\s*',
                part)
            if matched and proof.available():
                return True
        if proof.kind == 'empty_receipts':
            matched = re.fullmatch(
                r'\s*(?:编号\s*)?`?' + re.escape(proof.ident) + r'`?\s*的(?:可见|处理)?回执(?:表|阶段|列表)'
                r'(?:我)?(?:已(?:经)?)?(?:从头|从第一页)?(?:查完|翻完|列完)(?:了)?[，,]\s*'
                r'(?:是空的|为空|(?:共(?:有)?)?\s*0\s*条(?:记录|回执)?)[，,]\s*'
                r'没有(?:任何)?(?:送达或已读|送达和已读|送达|已读)(?:的)?记录[。]?\s*', part)
            if matched and proof.available():
                return True
    return False
