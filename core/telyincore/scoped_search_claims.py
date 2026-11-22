"""Limit a document-check summary to the current search or its quoted, unlabeled field."""
import re
from collections.abc import Mapping

from .inspection_inference import _in_quote
from .memory import _literal_number
from .memory_receipt import KnowledgeSearchMiss, MemorySearchMiss
from .original_evidence import OriginalText
from .receipt_projection import CurrentBatchOutput
from .reported_records import _visible_texts

_MISSING = re.compile(
    r'(?:^|(?<=[。！？\n]))(?P<claim>知识库(?P<archive>(?:和|与)归档记忆)?[里中](?:都)?没有'
    r'(?P<object>这[个条]编号的(?:条目|照片|资料)|另外的资料|其他(?:相关)?资料|相关资料))'
    r'(?P<photos>[，,]也没有纸单的(?:照片|扫描件)(?:或扫描件)?)?(?=[。！\n]|$)')
_SIGNATURE = re.compile(
    r'正文(?:是|为)[「“](?P<body>[^」”\n]{5,240})[」”](?:——|[，,])所以附签上'
    r'(?P<role>署名|签名)(?:是|为)\s*(?P<bold>\*\*|)(?P<name>[\u3400-\u9fff]{2,5})(?P=bold)(?=[，,。；;\n]|$)')
_REPORTED = re.compile(r'如果|假设|假如|假定|示范|举例|例如|演示|据说|声称|误称|转述|旧答|旧稿|摘录|抄录|复述|'
                       r'原话如下|如下原话|这么写[：:]|曾[经]?(?:说|写)|下列说法|^(?:\s*>|\s*[-*]?\s*```)', re.M)
_CATALOGUE_FACT = re.compile(r'知识库|归档记忆|清空.{0,8}库|库.{0,8}清空')

_KNOWN_INVENTORY = re.compile(r'(?:我|管理员|维护者)(?:本人|之前|当时|刚刚?|亲自|已(?:经)?|确实|可以)*'
                              r'(?:盘点|确认|清空|核实|证明|检查过|查遍|已知|说过|说)|(?:已|刚)清空')
_QUERY = re.compile(r'是否|是不是|能否|能不能|有没有|[吗么]|[？?]')
_REQUEST = re.compile(r'请(?:帮我|你)?(?:查|核|搜|看看)|帮我(?:查|核|搜|看)|麻烦(?:查|核|搜|看)|'
                      r'(?:查查|搜搜|查一下|看一下|核一下|找一下)')


def _catalogue_information(body):
    # A request to search is not testimony about the contents of the catalogue.
    for clause in re.split(r'(?<=[。！？?!；;\n])', body):
        if not _CATALOGUE_FACT.search(clause):
            continue
        if _KNOWN_INVENTORY.search(clause) or not (_QUERY.search(clause) or _REQUEST.search(clause)):
            return True
    return False


def _topic(query, question):
    term = query.strip()
    number = _literal_number(question)
    if number:
        return _literal_number(term).casefold() == number.casefold()
    return 2 <= len(term) <= 120 and term in question


def _search_scope(guard, messages, question, need_archive):
    marker = guard.receipt_marker
    view = getattr(guard.console, '_request_session', None)
    if marker is None or not callable(getattr(view, 'tool_evidence_snapshot', None)):
        return None
    try:
        epoch, count, entries = view.tool_evidence_snapshot()
        from .retrieval_scope import current
        provider = getattr(guard.console, 'quote_retrieval_scope', None)
        if not callable(provider):
            return None
        grant = current(provider)
    except (OSError, RuntimeError, ValueError):
        return None
    if epoch is not marker[0] or count - marker[1] > 128:
        return None
    # The live session keeps its typed result. A matching string in old history,
    # an archived response or a user message is not a search observation.
    bodies = [item['output'] for item in messages if isinstance(item, Mapping)
              and item.get('type') == 'function_call_output' and isinstance(item.get('output'), str)]
    direct = {id(body) for body in bodies}
    projected = {id(body.record):body for body in bodies if isinstance(body, CurrentBatchOutput)
                 and body.source_batch and hasattr(body, 'record')}
    knowledge = []
    archive = set()
    for position, row, _ in entries:
        if position <= marker[1] or row.kind != 'function_result':
            continue
        if row.name not in ('kb_search', 'kb_read', 'memory_search', 'read_events'):
            return None  # Other results may independently establish this fact.
        value = row.text
        wrapper = projected.get(id(row))
        shown = id(value) in direct or (wrapper is not None and wrapper.source_reader == row.name
                                      and wrapper.source_scope == row.scope and wrapper == value)
        if row.name == 'kb_search':
            if (not shown or not isinstance(value, KnowledgeSearchMiss) or not value.visibility
                    or value.visibility != grant or grant.current != row.scope or not _topic(value.query, question)
                    or (value.visibility.administrator and value.visible_count == 0)):
                return None
            knowledge.append(row.scope)
        elif row.name == 'kb_read':
            return None
        elif row.name == 'memory_search' and need_archive:
            if (not shown or not isinstance(value, MemorySearchMiss) or value.visibility != grant
                    or not _topic(value.query, question)):
                return None
            archive.add(row.scope)
        elif row.name == 'read_events' and isinstance(value, OriginalText):
            supplement = getattr(value, 'archive_search_miss', None)
            if shown and supplement and supplement[1] == grant and _topic(supplement[0], question):
                archive.add(row.scope)
            elif need_archive and '〔独立归档记忆候选' in value:
                # Positive or differently worded supplemental results can be
                # relevant even when an earlier supplementary lookup missed.
                return None
    if len(set(knowledge)) == 1 and (not need_archive or archive == set(knowledge)):
        return knowledge[0]
    return None


def qualify(text, read_sources, originals, messages, guard):
    """Keep every other sentence. Require an unambiguous current receipt and source."""
    if _REPORTED.search(text) or '```' in text:
        return None
    questions = [row['text'] for row in originals if row.get('current_speech')]
    if len(questions) != 1:
        return None
    question, = questions
    if '只有' in text or ('附签' in text and ('署名' in text or '签名' in text)):
        from .archive_listing_claims import qualify as qualify_listing
        limited = qualify_listing(text, read_sources, question, messages)
        if limited:
            return limited
    miss = _MISSING.search(text)
    if miss and not _in_quote(text, miss.start('claim')):
        # A user-provided inventory (including earlier messages) or a trusted
        # document that states the catalogue's contents can be independent proof.
        if any(role == 'image' or (role == 'user' and _catalogue_information(body))
               for role, body in _visible_texts(messages)):
            return None
        if _catalogue_information(question) or any(_catalogue_information(original)
                    for (_,original,_,_,_), _ in read_sources()):
            return None
        scope = _search_scope(guard, messages, question, bool(miss['archive']))
        if scope:
            where = '知识和归档记忆' if miss['archive'] else '知识'
            obj = miss['object'].replace('另外的','其他')
            safe = '这次检索在可见的' + where + '中未找到' + obj
            if miss['photos']:
                safe += '，本次也未取得' + miss['photos'].split('也没有',1)[1]
            return (text[:miss.start('claim')]+safe+text[miss.end():],
                    '请把未命中限定为这次可见范围与查词，保留已经查到的原文。',scope,scope)

    signed = _SIGNATURE.search(text)
    if not signed or _in_quote(text, signed.start()) or '附签' not in question:
        return None
    name = signed['name']
    body = signed['body']
    # This is only an unlabeled, delimited cell in the quoted original.
    if (not re.fullmatch(r'[^｜|。\n]{1,24}[｜|]'+re.escape(name)+r'[｜|][^｜|。\n]{1,24}(?:。[^｜|\n]{0,120})?',body)
            or re.search(r'签名|署名|签字|落款|经手|签收人|执笔',body)):
        return None
    role = re.compile(r'署名|签名(?!称)|签字|签署|落款|亲笔')
    if any(role.search(value) for speaker,value in _visible_texts(messages) if speaker=='user') or role.search(question):
        return None
    sources = set()
    for (_,original,ref,_,origin), row in read_sources():
        if original != body:
            if role.search(original):
                return None
        elif row.name=='read_events' and row.scope==origin and (ref,len(body)) in row.text.complete_records:
            sources.add((row.scope,origin,ref))
    if len(sources) != 1:
        return None
    recipient,origin,_ = sources.pop()
    return (text[:signed.start('role')]+'列出的名字'+text[signed.end('role'):],
            '附签这一栏印有姓名，原件没有标成署名。请按实际栏位作答。',recipient,origin)
