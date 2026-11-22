"""Bounded search previews and scope-rechecked full-text paging; never writes memory."""
import hashlib
import json
import heapq
from .memory_json import flat_bytes
from .original_evidence import OriginalSpan, OriginalText, source

PREVIEW_CHARS = 480
PAGE_CHARS = 2400


class KnowledgeSearchMiss(str):
    """A live query miss in one knowledge view. The count can be truly zero."""

    @classmethod
    def observed(cls, text, query, visibility, visible_count):
        result = cls(text)
        result.query = str(query)
        result.visibility = visibility
        result.visible_count = visible_count
        return result


class MemorySearchMiss(str):
    """The same scoped search, displayed alone or beside a completed records search."""

    @classmethod
    def for_query(cls, standalone, *, query, number, semantic_note, visibility=None):
        result = cls(standalone)
        result.query = query
        result.visibility = visibility
        result.beside_records = semantic_note + f'当前可见记忆资料未匹配「{query}」。其他用词下有无相关资料仍未知。'
        if number and number.casefold() != query.strip().casefold():
            result.beside_records += f'若要扩大上页的查询范围，可只按编号查 read_events(store="records", grep="{number}")。'
        return result


# These labels describe storage metadata, never infer when a life event occurred.
EVIDENCE_BOUNDARY = ('画像与笔记是整理记录，不等于用户逐字原话。记录时间、消息时间不等于事实发生或偏好变化时间；'
                     '实际时间只以明确原话为准，未说明则未知，不根据写入间隔推测原因或矛盾。'
                     '保留“最近、更喜欢、有时”等程度和条件，不改成固定要求或嫌恶。近期情况仅代表记录时，计划不代表已完成；当前是否有效需要核对，不能自动升格为长期特征。'
                     'Bot 历史发言中的转述和推测不能替代用户原话或工具执行证据。'
'分别核对每个对象、属性及时间范围：同句提及或检索命中相邻不代表配套、因果或共同用途；'
'某项未定不能概括为所有项未定，已有明确用途的对象须保留其用途。'
'单个物品的用途已知，也不等于它与另一物品的配套关系已确认。关系有明确依据才作为事实陈述；联想可以表达为猜想，不替用户补成已确认的安排，也不必为未知项强行追问。')

def provenance(where, role, ts):
    if where.startswith('画像/'):
        return f'画像整理记录 · 记录时间：{ts or "未记录"} · 源消息时间及原话来源请查画像条目 · 实际变化时间：不可由记录时间推定'
    if where.startswith('经历/对话/'):
        who = {'user':'用户原话','assistant':'Bot发言','system':'系统记录','developer':'系统记录'}.get(role, '对话记录（说话者未标记）')
        return f'{who} · 消息记录时间：{ts or "未记录"} · 不代表所述事实发生时间'
    return f'整理资料 · 资料记录时间：{ts or "未记录"} · 不代表所述事实发生时间'


def reference(where, ts, source, text):
    digest = hashlib.sha256()
    for block in flat_bytes([where, ts, source, text]):
        digest.update(block)
    return 'mem-' + digest.hexdigest()


def _positions(text, term):
    start = 0
    while (at := text.find(term, start)) >= 0:
        yield max(0, at - 60)
        start = at + len(term)


def preview(text, query):
    if len(text) <= PREVIEW_CHARS:
        return text, '正文完整'
    terms = list(dict.fromkeys(str(query).split()))
    # Locate literal matching passages, not a model-authored interpretation.
    # Merge ordered candidates lazily instead of retaining every occurrence.
    # Strictly better scores preserve the old earliest-position tie break.
    start, best_score, previous = 0, -1, None
    possible = sum(len(term) <= PREVIEW_CHARS for term in terms)
    for position in heapq.merge((0,), *(_positions(text, term) for term in terms)):
        if position == previous:
            continue
        previous = position
        end = min(position + PREVIEW_CHARS, len(text))
        score = sum(text.find(term, position, end) >= 0 for term in terms)
        if score > best_score:
            start, best_score = position, score
            if score == possible:
                break
    end = min(start + PREVIEW_CHARS, len(text))
    return text[start:end], f'命中片段，第 {start + 1}–{end} 字／共 {len(text)} 字；非完整原文'


def _user_body(prefix, excerpt, ref, *, complete, scope):
    from .retrieval_scope import valid_scope
    spans = ()
    if complete and excerpt and valid_scope(scope):
        spans = (OriginalSpan(('memory', scope, ref), len(prefix), len(prefix) + len(excerpt)),)
    return OriginalText(prefix + excerpt, wire_spans=spans)


def render(query, rows, *, single_scope=None):
    from .memory import portrait_kind_label
    lines = [f'查到 {len(rows)} 条（和「{query}」有关）：']; originals = []
    for row in rows:
        body = row['text']
        ref = reference(row['where'], row['ts'], row['source'], body)
        excerpt, label = preview(body, query)
        if row["where"].startswith("经历/对话/") and row.get("role") == "user":
            originals.extend(source(row["ts"], excerpt, ref))
        if row.get('literal_followup'):
            lines.append('〔同名原文补充候选；按明确书名匹配，不证明属于同一约定。时间仅为消息时间。〕')
        prefix = (f"· {provenance(row['where'], row.get('role', ''), row['ts'])} · 来源：{row['source'] or row['where']}\n"
                     f"memory_ref={ref}\n〔{label}〕" + (f" · {portrait_kind_label(row.get('memory_kind', ''))}" if row['where'].startswith('画像/') else "") + "\n")
        complete = row["where"].startswith("经历/对话/") and row.get("role") == "user" and excerpt == body
        lines.append(_user_body(prefix, excerpt, ref, complete=complete, scope=single_scope))
    if any(row.get('literal_followup') for row in rows):
        lines.append('同名补充只选每个名称的早期/近期少量原文，受本次条数上限限制，不是完整历史。需要更多时按名称继续 memory_search 或 memory_navigate 翻文件。')
    lines.append('检索候选不等于已证实相关事实；需核对正文是否真正支持问题，不因返回了候选就声称查到目标约定，也不因候选无关就断言其他记录不存在。')
    lines.append(EVIDENCE_BOUNDARY)
    lines.append('Bot 历史发言只证明 Bot 当时说过这些，不能证明用户说过同样的话或工具执行成功；需要核对原话时，可用 read_events(store="records", category="user") 检索。')
    lines.append('片段省略不代表原记录中断或事件未发生。读取全文：memory_search(query="", memory_ref="上面的引用", offset=0)，按回执的下一段 offset 续读。笔记是历史整理，不能替代原始对话与工具回执作为执行证据。')
    joined = OriginalText.join(lines)
    return OriginalText(joined, originals, wire_spans=joined.wire_spans)


def page_end(text, offset):
    """Prefer a boundary in the latter half; preserve every delimiter verbatim."""
    limit = min(offset + PAGE_CHARS, len(text))
    if limit == len(text):
        return limit, False
    lower = offset + PAGE_CHARS // 2
    for delimiter in ('\n\n', '\n'):
        at = text.rfind(delimiter, lower, limit)
        if at >= lower:
            return at + len(delimiter), False
    for chars in ('。！？!?；;', ' \t'):
        ends = [i + 1 for i in range(lower, limit) if text[i] in chars]
        if ends:
            return ends[-1], False
    # Avoid splitting an ordinary number/token if its start is near the limit.
    end = limit
    while end > lower and text[end - 1].isascii() and (text[end - 1].isalnum() or text[end - 1] in '._-/'):
        end -= 1
    if end > lower and end < limit:
        return end, True
    return limit, True


def read(documents, ref, offset):
    from .memory import portrait_kind_label
    if not isinstance(ref, str) or not ref.startswith('mem-') or len(ref) != 68:
        raise ValueError('记忆引用格式无效')
    if type(offset) is not int or offset < 0:
        raise ValueError('offset 必须是非负整数')
    for where, row in documents:
        if reference(where, row.ts, row.source, row.text) != ref:
            continue
        if offset > len(row.text):
            raise ValueError('offset 超出正文范围')
        end, hard_boundary = page_end(row.text, offset)
        tail = (f'下一段 offset={end}；请使用此偏移继续，不要按固定步长推算；分页边界不代表原文缺失'
                + ('；本段在连续文本中分页，下一段紧接本段末尾' if hard_boundary else '')
                if end < len(row.text) else '原文结束')
        kind_label = portrait_kind_label(row.memory_kind) + '\n' if where.startswith('画像/') else ''
        originals = source(row.ts, row.text[offset:end], ref) if where.startswith('经历/对话/') and row.role == 'user' else ()
        prefix = kind_label + f'[{ref}] 来源：{row.source or where} · {provenance(where, row.role, row.ts)} · {EVIDENCE_BOUNDARY}\n正文共 {len(row.text)} 字；本段 offset={offset}，结束位置 {end}\n'
        complete = where.startswith('经历/对话/') and row.role == 'user' and offset == 0 and end == len(row.text)
        page = _user_body(prefix, row.text[offset:end], ref, complete=complete, scope=getattr(row, "scope", None))
        return OriginalText(page + '\n（' + tail + '）', originals, wire_spans=page.wire_spans)
    raise PermissionError('记忆引用已变化、不存在或不在当前授权范围；请重新检索')


PROFILE_PAGE_CHARS = 6000


def profile_page(text, *, offset=0, snapshot='', evidence_ref=''):
    """Page the authorized view; reject changed views rather than skip history."""
    if type(offset) is not int or offset < 0:
        raise ValueError('画像 offset 必须是非负整数')
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    if snapshot and snapshot != digest:
        raise ValueError('画像或可见范围已变化，请从 offset=0 重新读取，不能沿用旧分页位置')
    if offset and not snapshot:
        raise ValueError('画像续读需要上一页的 snapshot')
    if offset > len(text):
        raise ValueError('画像 offset 超出范围')
    end = min(offset + PROFILE_PAGE_CHARS, len(text))
    argument = f'evidence_ref="{evidence_ref}", ' if evidence_ref else ''
    tail = (f'还有画像内容；保持 who 不变，以 profile_read({argument}offset={end}, snapshot="{digest}") 继续读取。分页不代表原文中断。'
            if end < len(text) else '画像读取结束。')
    heading = '复核当时的原话及上下文。' if evidence_ref else '画像历史按新到旧展示；保留全部记录，不代表每条都是当前状态。'
    return (f'{heading}正文字符 {offset}–{end}/{len(text)}。\n'
            + text[offset:end] + '\n' + tail)
