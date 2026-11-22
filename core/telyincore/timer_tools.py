"""Scoped reminder inspection/cancellation; scheduling stays in App.arm."""
import json
import re
import sqlite3
from datetime import datetime
from .tools import ToolDef,ToolTag
from .reminder_delivery import is_reminder, UnconfirmedResult
from .unresolved import UnresolvedError
from .retrieval_scope import OWNER
from .reminder_confirmation import current_receipt
from . import reminder_provenance as provenance
from .evidence_relations import time_evidence
from .reminder_read_proof import CheckedRead, ReadProof

# Host-derived semantics, never taken from reminder text or model arguments.
# Creation context is not a verified link to an original user request.
READ_EVIDENCE = {
    'kind': 'reminder_state', 'original_request_ref': None,
    'origin_linkage': 'see_each_creation_evidence',
    'historical_absence': 'not_established_by_this_view',
    'negative_result_meaning': '仅本次指定记录视图未返回条目；不是从未发生的证明。',
    'independent_dimensions': {
        'creation_receipt_existence': 'not_proven_by_missing_locator',
        'past_delivery': 'not_proven_absent_by_future_due_time',
        'reading_completion': 'not_established_by_schedule',
        'boundary': '定位、排期、检测、唤醒、送达、业务结果分别核对；未知不是未发生。也没有留存、谈不上提醒过、一件都还没到等结论仍须各自证据，不能从其他维度代推。',
    },
    'limits': [
        '本表没有经过核实的原始约定引用；每条 creation_evidence 仅提供创建上下文。相同书名、话题或相近时间不能把旧留言绑定为这条提醒的来源。',
        'late_seconds 仅是检测到期相对 due_at 的延迟；不是实际唤醒、发出、收到或读到提醒的延迟。',
        '检测、唤醒或模型输出记录不能单独证明送达，更不证明事项完成。',
        '当前未列出事项不证明过去没有设置、已经取消或已经完成；核对历史需要对应原文和回执。',
    ],
}

_OTHER_NAMES_UNKNOWN = '同一事项是否用了别的名称排程，凭这个关键词还不能确定。'


DECLARATIONS=(
    ToolDef(name='timer_read',description='状态核对可选 view=status，query 填主题短词。可查匹配的排程、待办与可见的旧闹钟原文。从第一页查完且只匹配一个编号时，结果附上该编号的可见处理回执。状态、来源和收件情况看各自的证据。需要详细来源说明可用 full。继续查到期待办时用 next_after 翻页。',
            parameters={'type':'object','properties':{'view':{'type':'string','enum':['status','full']},'query':{'type':'string','maxLength':160},'legacy_after':{'type':'string','maxLength':128},'after':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':100}}},tags=(ToolTag.FLOW,),effect='immediate'),
    ToolDef(name='timer_cancel',description='按 timer_read 的编号撤销尚未触发的旧提醒。只撤销该提醒，不删除约定或历史；已触发的事件不能由此撤回。',
            parameters={'type':'object','properties':{'id':{'type':'string'}},'required':['id']},tags=(ToolTag.FLOW,),effect='immediate'),
    ToolDef(name='timer_history',description='按完整编号或至少八位的唯一十六进制前缀查看到期检测、唤醒、模型和发言准入记录。输出成功不代表对方已读，准入不代表发送成功。按 next_after 继续分页。',
            parameters={'type':'object','properties':{'id':{'type':'string'},'after':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':100}},'required':['id']},tags=(ToolTag.FLOW,),effect='immediate'),
    ToolDef(name='timer_resolve',description='本轮本人明确确认对应事项完成或取消后，关闭已到期的提醒待办；宿主核对原话，证据不足继续待确认。仅说看到了、收到或已读，待办继续挂起。如本人要办理，可用「我已完成【事项原文】」或「请取消提醒【事项原文】」。是否询问由你决定。唤醒、说过话或时间过去不能证明完成；处理记录保留。',
            parameters={'type':'object','properties':{'id':{'type':'string'},'resolution':{'type':'string','enum':['completed','cancelled']}},'required':['id','resolution']},tags=(ToolTag.FLOW,),effect='immediate'),
)

def _status_view(result, query):
    """Keep row data, source locators and page boundaries. Explain shared semantics once."""
    for row in (*result['scheduled'], *result['pending']):
        context = row['creation_evidence']
        for key in ('boundary', 'intent_boundary', 'attribution_boundary'):
            context.pop(key, None)
        if 'time_evidence' in row:
            row['time_evidence'].pop('boundary', None)
    selection = '按本次关键词' if query else '在当前可见范围内'
    facts = [f"{selection}，本页匹配未触发排程 {len(result['scheduled'])} 条、仍挂起的到期待办 {len(result['pending'])} 条。"]
    result['evidence'] = {
        'kind': 'reminder_state', 'query': query, 'page_facts': facts,
        'limits': '创建上下文不证明原始约定、创建者或意图。预定、检测、送达、已读和事项完成各有各的证据。'
                  '查空不能证明过去没有发生；仅收到提醒不满足关闭条件。'}
    preview = result.get('receipt_preview')
    if preview is not None:
        if preview['all_visible_stages_listed']:
            count = len(preview['stages'])
            preview['meaning'] = f"已从头查完此编号的处理回执，共 {count} 条。timer_history 查询同一张表。"
            if not count:
                detected = any(r['id'] == preview['id'] and r.get('detected_at') for r in result['pending'])
                detection = '待办另有到期检测时间。' if detected else ''
                facts.append(f"编号 {preview['id']} 的可见回执表已查完，共 0 条。{detection}凭这份表无法确认发出或收到。主动提问不能证明已读，本人明确说看到了可记为本轮自述。")
        else:
            preview['meaning'] = '本页只列部分阶段，可用 timer_history 和 next_after 查后续记录。'
    elif result.get('next_after') is not None:
        facts.append('匹配的待办还有下一页，凭这份表无法确认收件情况。')
    if result.get('legacy_alarm_notes'):
        result['legacy_meaning'] = ('以下匹配原文保存在旧待办，记录的是预定时刻。'
                                    '可比对原文明确写出的提醒编号。若原文未写编号，同名不足以认定编号相同或不同。留存文字不能证明实际调度响过、送达或已读。')
        facts.append('旧待办中有匹配文字。该文字本身证明留存，单靠它不能确认实际入队、送达或已读。')
    elif 'legacy_alarm_notes' in result:
        result['legacy_meaning'] = '本页未查到匹配的旧闹钟原文。'


def handlers(store,app_of,scope_of,*,journal=None,source_events=None,event_store=None,legacy_pending=None):
    def evidence(value, scope):
        return provenance.project(value, scope, event_store() if event_store is not None else None)
    def set_timer(when='',what='',**_rest):
        scope=scope_of();scope.require()
        try:
            moment=datetime.fromisoformat(str(when or '').strip().replace('Z','+00:00'))
            said_when=moment.astimezone().isoformat(timespec='seconds')
        except (ValueError,OverflowError):
            return '未设置：时刻必须是有效的 ISO 8601 时间。'
        payload={'what':what,'scope':scope.current}
        if source_events is not None:
            payload[provenance.KEY] = provenance.capture(scope, source_events(), event_store() if event_store is not None else None)
        app=app_of()
        # App.arm owns rearming the timer driver; the pre-assembly fallback is
        # still durable and must pass the same current-scope check first.
        ident=(app.arm(said_when,payload) if app is not None and getattr(app,'timers',None) is not None
               else store.set(said_when,payload))
        return ('记下了：到「'+said_when+'」提醒你「'+str(what)+'」（编号 '+ident+'）。'
                ' 需要程序运行才能处理；错过时间会在下次启动补检并保留待办，唤醒或发言不等于事项完成。')
    def visible(row,scope):
        p=row.payload
        return is_reminder(row) and (
            scope.administrator or scope.permits(p.get('scope')))
    def read(after=0,limit=50,query='',legacy_after='',view='full'):
        scope=scope_of();scope.require()
        if not isinstance(query, str) or len(query) > 160:
            raise ValueError('提醒关键词最长 160 字。')
        if view not in ('full', 'status'):
            raise ValueError('提醒视图须为 full 或 status。')
        query = query.strip()
        extra = {}
        if query and legacy_pending and scope.current.startswith('private:') and scope.permits(OWNER):
            if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError('invalid reminder page')
            if not isinstance(legacy_after, str) or len(legacy_after) > 128:
                raise ValueError('旧待办翻页位置无效。')
            try:
                items = legacy_pending()
            except (OSError, UnresolvedError, UnicodeError):
                extra = {'legacy_lookup': 'unavailable', 'legacy_meaning': '旧待办暂不可读，本次只核对当前提醒。'}
            else:
                hits = sorted((row for row in items if row.id > legacy_after
                               and row.text.startswith('闹钟响了：到「') and query in row.text),
                              key=lambda row: row.id)
                extra = {'legacy_alarm_notes': [row.text for row in hits[:limit]],
                          'legacy_next_after': hits[limit-1].id if len(hits) > limit else None,
                          'legacy_meaning': '列出的每一条都已在旧待办文件中找到，可确认那里记过这条闹钟；原文只有预定时刻。何时入队、是否发给对方、是否收到仍未知。只有原文明确写出的提醒编号可供比对。按 legacy_next_after 填 legacy_after 续读。'}
        with store.current_view() as current:
            rows=[{'id':r.id,'when':r.at,'what':r.payload['what'],
                   'creation_evidence':evidence(r.payload.get(provenance.KEY),scope),
                   'time_evidence':time_evidence(r.at)} for r in current if visible(r,scope) and query in r.payload['what']]
            if journal is not None:
                excluded_ids = {r.id for r in current if not visible(r, scope)}
                preview = None
                if query and after == 0:
                    pending, next_after, preview = journal.status_page(
                        scope, after=after, limit=limit, query=query, scheduled_ids=(r['id'] for r in rows),
                        excluded_ids=excluded_ids)
                else:
                    pending, next_after = journal.pending_page(
                        scope, after=after, limit=limit, include_creation=True, query=query, excluded_ids=excluded_ids)
                proofs = []
                for row in pending:
                    row['creation_evidence']=evidence(json.loads(row.pop('_creation_context') or 'null'),scope)
                # One scoped history read for an unambiguous modern reminder.
                # Never infer a link between its ID and an old free-text alarm.
                if preview is not None:
                    ident, receipts, receipt_after = preview
                    extra['receipt_preview'] = {
                        'id': ident, 'stages': [{'stage': r['stage'], 'at': r['at']} for r in receipts],
                        'all_visible_stages_listed': receipt_after is None,
                        'next_after': receipt_after,
                        'meaning': '与 timer_history 共用同一张可见回执表。空表不能证明未送达。'}
                    if not rows and len(pending) == 1:
                        proofs.append(ReadProof('pending_only', ident, query, scope, scope_of))
                    if not receipts and receipt_after is None:
                        proofs.append(ReadProof('empty_receipts', ident, query, scope, scope_of))
                result = {'scheduled':rows, 'pending':pending,
                          'next_after':next_after,
                          'evidence':READ_EVIDENCE, **extra}
                if view == 'status':
                    _status_view(result, query)
                    result = {'evidence': result.pop('evidence'), **result}
                if query and not rows and not pending and not extra.get('legacy_alarm_notes'):
                    if view == 'status':
                        result['evidence']['page_facts'].append(_OTHER_NAMES_UNKNOWN)
                    else:
                        result['evidence'] = {
                            **READ_EVIDENCE, 'negative_result_meaning':
                            '本页未匹配到可见排程或到期待办。' + _OTHER_NAMES_UNKNOWN
                            + '这页也无法证明过去没有设置过。'}
                return CheckedRead(json.dumps(result, ensure_ascii=False), tuple(proofs))
            if query and not rows:
                return '未匹配到包含本次关键词的可见未触发排程。' + _OTHER_NAMES_UNKNOWN
            return json.dumps(rows,ensure_ascii=False) if rows else '当前可见范围内没有未触发的提醒。'
    def cancel(id):
        scope=scope_of();scope.require()
        if not isinstance(id,str) or len(id)<8:raise ValueError('请使用 timer_read 返回的完整编号或至少8位唯一前缀。')
        with store.current_view() as current:
            rows=[r for r in current if r.id.startswith(id) and visible(r,scope)]
            if len(rows)!=1:return '未撤销：编号不存在、不可见、已触发或前缀不唯一；请重新查询。'
            app=app_of()
            if app is None or app.timers is not store:raise RuntimeError('提醒调度尚未就绪，未撤销。')
            if journal is not None: journal.preserve_creation(rows[0])
            if not app.disarm(rows[0].id):
                return '未撤销：提醒已触发或已被撤销；已触发的事件保持原状。'
            if journal is not None:
                try: journal.cancelled(rows[0].id, datetime.now().astimezone().isoformat())
                except (OSError, sqlite3.Error):
                    return '已撤销提醒：'+rows[0].id+'；撤销回执未能保存，不能据此回执缺失推断撤销失败。'
            return '已撤销提醒：'+rows[0].id+'；其他提醒与历史不变。'
    def history(id,after=0,limit=50):
        scope=scope_of();scope.require()
        if journal is None:raise RuntimeError('提醒处理记录尚未接线。')
        # A future reminder's source is already durable in its schedule;
        # do not pretend it is missing merely because due preparation has not run.
        with store.current_view() as current:
            lookup = {}
            if isinstance(id, str) and re.fullmatch(r'[0-9a-f]{8,31}', id):
                matches = {r.id for r in current if r.id.startswith(id) and visible(r,scope)}
                excluded_ids = {r.id for r in current if not visible(r,scope)}
                matches.update(journal.matching_ids(id, scope, excluded_ids=excluded_ids))
                if len(matches) == 1:
                    id = matches.pop()
                    # Only a currently authorized result may disclose the full ID.
                    lookup = {}
                else:
                    return json.dumps({'receipts': [], 'next_after': None, 'evidence': READ_EVIDENCE,
                        'lookup_status': 'ambiguous' if matches else 'unresolved_prefix',
                        'hint': '无法唯一定位。可用 timer_read(query=事项关键词) 查排程和旧待办原文；空回执不能证明没有提醒。'}, ensure_ascii=False)
            live=next((r for r in current if r.id==id),None)
            if live is not None and not visible(live,scope):
                rows=[];next_after=None;context=None;found=False
            else:
                rows,next_after,context,found=journal.history_evidence(
                    id,scope,after=after,limit=limit,visible_schedule=live is not None)
                if live is not None and found:
                    context=live.payload.get(provenance.KEY)
            proofs = ()
            if found:
                lookup.update(matched_id=id, all_visible_stages_listed=(after == 0 and next_after is None))
                if after == 0 and not rows and next_after is None:
                    proofs = (ReadProof('empty_receipts', id, '', scope, scope_of),)
            result = {'receipts':rows,'next_after':next_after,'evidence':READ_EVIDENCE,
                      'creation_evidence':evidence(context,scope), **lookup}
            return CheckedRead(json.dumps(result,ensure_ascii=False), proofs)
    def resolve(id,resolution):
        scope=scope_of();scope.require()
        if journal is None:raise RuntimeError('提醒处理记录尚未接线。')
        # A prepared-but-not-consumed scheduling record must be cancelled first.
        refused='未关闭：编号不存在、不可见或已经关闭。'
        with store.current_view() as current:
            live=next((r for r in current if r.id==id),None)
            if live is not None:
                return ('未关闭：提醒仍在调度表，请先用 timer_cancel 撤销。'
                        if visible(live,scope) else refused)
            def evidence_for(row, others):
                return current_receipt(scope, scope_of, source_events, event_store, row, others, resolution)
            try:
                changed = journal.resolve(id, scope, resolution=resolution,
                    at=datetime.now().astimezone().isoformat(), evidence_for=evidence_for)
            except UnconfirmedResult:
                return '未关闭：结果待确认。本轮没有能与此事项唯一对应的本人明确确认。是否询问由你决定。'
            return '已关闭到期提醒待办，处理记录保留。' if changed else refused
    return {'set_timer':set_timer,'timer_read':read,'timer_cancel':cancel,'timer_history':history,'timer_resolve':resolve}
