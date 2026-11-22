"""Host-issued, task-local single-use write capability; never model supplied."""
import asyncio,contextvars,hashlib,re
from contextlib import contextmanager
from pathlib import Path
from ._frozen.contracts import canonical,Refused
_REVIEW_FAILURES = {
    'portrait_batch_aborted': '本条审核已通过，但同组其他写入未通过；整组未提交，本条不是被内容审核否定，请查看同组其他回执',
    'portrait_batch_not_reviewed': '同组前一项审核未通过，本条尚未进入审核；整组未提交，不能据此判断本条内容不合格',
    'portrait_per_write_request_budget': '内部审核请求额度已耗尽；这是审核流程限制，不是用户写入限流或内容否定。未写入，请导出诊断',
    'portrait_independent_review_timeout': '独立审核超时；未写入，不代表内容不真实。请导出诊断，不要反复改写提交',
    'portrait_quote_background_conflict': '审核摘录夹带了无关背景，无法从原话唯一取出完整依据。保留原始记录，无须换措辞重投或转存其他笔记',
    'portrait_source_uncertain': '原话的归属或含义未明。可先放着，或按需用 experience_note 分开记原话与猜测；只有确实需要后续跟进才建待办。请勿把猜测转存为事实',
    'portrait_needs_review': '候选证据尚未通过完整性校验；不等于内容被判为不真实。画像未保存，请勿声称已长期记住，也不要仅换措辞重复提交',
    'wire_nomination_binding': '审核请求的候选类别或作者绑定不符合协议；这是请求校验异常，不代表限流或内容被判不真实，请导出诊断',
    'v3_role_relation': '审核服务返回了不符合协议的标签，已进行一次格式重试仍未通过；这不是内容被否定，也不是限流，请导出诊断，不要换措辞刷写',
    'v3_exact_ordered_partition': '审核服务未能逐字复现原文分段，本次未写入。请勿仅换措辞重试',
    'v3_incomplete_partition': '审核服务遗漏了部分原文，本次未写入。请勿仅换措辞重试',
    'v3_audit_json': '审计服务返回的 JSON 格式无效',
    'portrait_independent_review_protocol_error': '独立审核输出或执行异常；不是内容审核否定，请查看诊断',
    'portrait_candidate_scope_mismatch': '候选审核的摘录与所标注限定范围不一致，内部校正后仍未通过；这是审核结构问题，不等于事实不真实。未写入，请导出诊断，不要让用户重复发送',
    'portrait_no_candidate': '提供的原话没有可支持本条拟写的事实；不是接口故障，也不表示画像已保存。不能用 Bot 自己的转述或改写到其他笔记代替有效来源',
    'portrait_no_supported_source': '未找到同一作者、同一会话中仍有效的原话依据，或后续已更正、撤回；本次未写入。不是接口故障，不能把旧回复当作用户证据',
    'portrait_chat_identity': '审核接口返回的响应结构或标识无效；这是接口协议异常，不是名字、昵称或画像内容被否定。未写入，请导出诊断，不要猜测内容不合格',
    'portrait_chat_identity_drift': '审核响应流中标识发生变化；这是接口协议异常，不是用户身份改变或内容被否定。未写入，请导出诊断',
    'portrait_kind_unknown': '实际拟写内容的记忆类别尚不明确；未写入，不属于限流',
    'portrait_kind_binding': '分类响应与本次拟写内容不匹配；未写入，请导出诊断',
    'portrait_kind_protocol': '分类服务未返回完整有效结果；未写入，请导出诊断',
    'portrait_independent_review_fail': '内容审核发现证据与拟写内容不一致；请核对原话。若混有不同计划、偏好或已发生的事，请各自核对证据后分开提交；不要改写后整条重投',
    'portrait_independent_review_unknown': '内容审核未能得出可靠结论；不代表事实被否定，本次不保存',
    'portrait_context_changed': '审核期间会话或目标发生变化；请在有效的当前会话重新核对来源',
    'chat_tool_identity_drift': '审计服务返回的工具流标识不一致',
}
_active=contextvars.ContextVar('portrait_write_capabilities',default=None)
_copy_guard = contextvars.ContextVar('portrait_rejected_draft_copy', default=None)
_PERSONAL_NOTES = frozenset({'self_note','self_observe_add','self_account_add','self_account_set',
                            'abouther_propose','unresolved_add','skill_note','experience_note'})


def draft_digest(text):
    if not isinstance(text, str):
        return None
    text = text.strip().rstrip('。').rstrip()
    return hashlib.sha256(text.encode('utf-8', 'surrogatepass')).digest() if text else None


@contextmanager
def rejected_copies(digests, current):
    token = _copy_guard.set((digests, current))
    try:
        yield
    finally:
        _copy_guard.reset(token)


def note_copy_denied(name, args):
    state = _copy_guard.get()
    if not state or name not in _PERSONAL_NOTES or not isinstance(args, dict):
        return False
    # A properly attributed pending observation has separate exact-source and
    # interpretation fields. Its own handler verifies both scope and authorship.
    if name == 'experience_note' and args.get('subject'):
        return False
    fields = ('observation', 'interpretation') if name == 'experience_note' else ('text',)
    return any((key_digest := draft_digest(args.get(key))) is not None and key_digest in state[0]
               for key in fields) and state[1]()

def arguments(value):
    if type(value) is not dict or set(value)-{'text','who','standing','favor','weight'}:raise Refused('writer_argument_fields')
    d=dict(text='',who='',standing='',favor=None,weight=None);d.update(value)
    if type(d['text']) is not str or not d['text'].strip() or type(d['who']) is not str:raise Refused('writer_text_or_author')
    if type(d['standing']) is not str or d['standing']!='' or d['favor'] is not None:raise Refused('relationship_write_requires_own_policy')
    if d['weight'] is not None and (type(d['weight']) is not int or not 1<=d['weight']<=5):raise Refused('writer_weight')
    return d
def signature(binding,args):
    home=Path(binding['home']).resolve(strict=True);stat=home.stat()
    target=binding['resolve'](args['who'])
    if len(target)!=3 or target[2] or not target[0] or not target[1]:raise Refused('writer_target_unavailable')
    return canonical(dict(home=str(home),root_identity=[stat.st_dev,stat.st_ino],target=target,args=args))
def bind_writer(fn,home,resolve):
    fn._portrait_write_binding=dict(handler=fn,home=Path(home),resolve=resolve,required=False)
    return fn
def consume(fn,value):
    binding=getattr(fn,'_portrait_write_binding',None)
    if not binding or not binding['required']:return
    value=arguments(value);state=_active.get()
    if state is None or state['task'] is not asyncio.current_task():raise PermissionError('画像写前复核未授权，本次未写入。')
    key=(id(fn),signature(binding,value));ticket=state['tickets'].pop(key,None)
    if ticket is None:
        item_refusal=state.get('refusals',{}).get(key,{})
        reason = item_refusal.get('reason') or state.get('refusal')
        if reason == 'portrait_rate_limited':
            rate = item_refusal.get('rate_limit') or state.get('rate_limit') or {}
            bucket = '当前用户在本会话' if rate.get('bucket') == 'author' else '当前 Bot'
            raise PermissionError(f"画像请求暂缓（portrait_rate_limited）：{bucket}在 {rate.get('window_seconds', '?')} 秒内已提交 {rate.get('used', '?')}/{rate.get('limit', '?')} 次审核请求；约 {rate.get('retry_after_seconds', '?')} 秒后可重新尝试。本次未进入内容审核、未写入；不会自动补写，请勿立即反复提交。")
        if reason in ('portrait_call_already_consumed', 'portrait_duplicate_draft'):
            raise PermissionError(f'画像重复请求已拦截（{reason}），本次未写入；请核对原调用回执，不能据此认定原调用成功。')
        if reason and (reason.startswith('portrait_claim_') or reason == 'portrait_rate_policy_invalid'):
            raise PermissionError(f'画像请求登记不可用（{reason}），本次未进入内容审核、未写入；请检查诊断与配置，不要删除保护记录。')
        if reason in ('TimeoutError', 'LLMError', 'OSError', 'RuntimeError', 'AttributeError'):
            raise PermissionError(f'画像审核或存储服务异常（{reason}），本次未写入；这不是内容不合格，请查看诊断。')
        if reason:
            detail = _REVIEW_FAILURES.get(reason)
            explanation = f'（{reason}）' if detail is None else f'（{reason}，{detail}）'
            raise PermissionError(f'画像写前复核未通过{explanation}，本次未写入。')
        raise PermissionError('画像写许可缺失或已消费，本次未写入。')
    if state['current']() is not True:
        raise PermissionError('画像写入前上下文已变化或许可已过期，本次未写入。')
    return ticket
@contextmanager
def authorized(tickets,current,*,refusal=None,rate_limit=None,refusals=None):
    # Called by trusted host policy only. Review output cannot enter this API.
    reason = None
    if isinstance(refusal,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,127}',refusal):
        reason = refusal
    per_item={}
    for key,value in (refusals or {}).items():
        code=value.get('reason')
        if isinstance(code,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,127}',code):
            per_item[key]=dict(reason=code,rate_limit=value.get('rate_limit'))
    token=_active.set(dict(task=asyncio.current_task(),tickets=dict(tickets),current=current,refusal=reason,rate_limit=rate_limit,refusals=per_item))
    try:yield
    finally:_active.reset(token)
