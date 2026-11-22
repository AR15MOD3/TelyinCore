"""Lossless session generations, independent of the active context window.

Single session writer (the existing launcher lease). Publish and fsync the old
bytes before reset may replace them. Readers never create or repair archives.
Existing history before installation is not reconstructed from summaries.
"""
from __future__ import annotations
from contextlib import closing, contextmanager, ExitStack
import hashlib
import heapq
import json
import math
import os
import re
import uuid
from pathlib import Path
from itertools import islice
from threading import RLock
from datetime import datetime, timedelta, timezone
from .file_signature import signature, descriptor_signature
from .source_bytes import checked_open

DIRECTORY = 'record-history'
_REF = re.compile(r'([0-9a-f]{64}):([1-9][0-9]*)\Z')


def _safe(path: Path) -> None:
    if path.resolve() != path.absolute():
        raise ValueError('原始记录路径不能包含链接')


def _lines(raw: bytes):
    position = 0
    while position < len(raw):
        newline = raw.find(b'\n', position)
        end = len(raw) if newline < 0 else newline + 1
        # Keep physical line endings for the archive/prefix identity. A lone
        # line reuses raw; a batch copies only its currently parsed line, never
        # splits and retains all later records before yielding the first one.
        line = raw[position:end]
        position = end
        try:
            row = json.loads(line.decode('utf-8'))
        except (ValueError, UnicodeError) as error:
            raise ValueError('原始记录解析失败，读取不完整；未裁剪上下文') from error
        if not isinstance(row, dict) or not isinstance(row.get('text'), str):
            raise ValueError('原始记录格式损坏；读取不完整，未裁剪上下文')
        if row.get('record_ref') and (not isinstance(row['record_ref'], str)
                                     or not _REF.fullmatch(row['record_ref'])):
            raise ValueError('原始记录引用格式损坏')
        yield line, row


def _unchanged(path, stream, initial):
    _safe(path)
    try:
        current = signature(path)
    except FileNotFoundError as error:
        raise ValueError('原始记录在读取过程中变化；未裁剪上下文') from error
    if current != initial or descriptor_signature(stream.fileno()) != initial:
        raise ValueError('原始记录在读取过程中变化；未裁剪上下文')


def _stream_lines(path):
    """Validate a finite source prefix with at most one record body in memory."""
    with checked_open(path) as stream:
        initial = descriptor_signature(stream.fileno())
        _unchanged(path, stream, initial)
        remaining = initial[2]
        while remaining:
            line = stream.readline(remaining)
            if not line:
                raise ValueError('原始记录在读取过程中变化；未裁剪上下文')
            remaining -= len(line)
            yield from _lines(line)
        _unchanged(path, stream, initial)


def _file_digest(path):
    """Hash an exact finite file, refusing observable edits during validation."""
    digest = hashlib.sha256()
    with checked_open(path) as stream:
        initial = descriptor_signature(stream.fileno())
        _unchanged(path, stream, initial)
        remaining = initial[2]
        while remaining:
            block = stream.read(min(65536, remaining))
            if not block:
                raise ValueError('原始记录在读取过程中变化；未裁剪上下文')
            remaining -= len(block)
            digest.update(block)
        _unchanged(path, stream, initial)
    return initial[2], digest.hexdigest()


def reuse_preserved(session: Path, size, digest) -> bool:
    """Use a verified existing generation when reset already knows its identity."""
    if not size:
        return False
    target = session.parent / DIRECTORY / session.name / (digest + '.jsonl')
    _safe(target)
    try:
        target.lstat()
    except FileNotFoundError:
        return False
    # Even a matching filename and digest do not establish valid record syntax.
    current = hashlib.sha256()
    with closing(_stream_lines(session)) as lines:
        for raw, _ in lines:
            current.update(raw)
    if current.hexdigest() != digest:
        raise ValueError('原始记录在读取过程中变化；未裁剪上下文')
    if _file_digest(target) != (size, digest):
        raise ValueError('原始记录归档校验失败；未裁剪上下文')
    return True


def preserve(session: Path) -> None:
    session = Path(session); _safe(session)
    if not session.exists():
        return
    if not session.stat().st_size:
        return
    folder = session.parent / DIRECTORY / session.name
    _safe(folder); folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / ('.pending-' + uuid.uuid4().hex)
    try:
        digest, size = hashlib.sha256(), 0
        with temporary.open('xb') as out:
            with closing(_stream_lines(session)) as lines:
                for line, _ in lines:
                    out.write(line)
                    digest.update(line)
                    size += len(line)
            out.flush(); os.fsync(out.fileno())
        expected = size, digest.hexdigest()
        if _file_digest(temporary) != expected:
            raise OSError('原始记录归档回读不一致')
        target = folder / (expected[1] + '.jsonl'); _safe(target)
        if target.exists():
            if _file_digest(target) != expected:
                raise ValueError('原始记录归档校验失败；未裁剪上下文')
            return
        # An archive is immutable. A file arriving after the exists() check
        # must be verified, never replaced (including conflicting evidence).
        try:
            os.link(temporary, target)
        except FileExistsError:
            _safe(target)
            if _file_digest(target) != expected:
                raise ValueError('原始记录归档校验失败；未裁剪上下文')
            return
        if os.name != 'nt':
            for directory in (folder, folder.parent, session.parent):
                fd = os.open(directory, os.O_RDONLY)
                try: os.fsync(fd)
                finally: os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def current_references(session: Path, records):
    """Bind current in-memory rows to exact persisted prefix references.

    Used before handoff reset; the same references survive preserve(). Refuse
    mismatched snapshots rather than attach an original to the wrong excerpt.
    """
    from .note import Record
    session = Path(session)
    _safe(session)
    if not records and not session.exists():
        return {}
    digest = hashlib.sha256()
    result = {}
    number = 0
    with closing(_stream_lines(session)) as lines:
        for number, (line, row) in enumerate(lines, 1):
            if number > len(records):
                raise ValueError('交接原文快照已变化，不能绑定引用')
            record = records[number - 1]
            if Record(**row) != record or (row.get('record_ref') or '') != record.record_ref:
                raise ValueError('交接原文快照不一致，不能绑定引用')
            digest.update(line)
            reference = row.get('record_ref') or f'{digest.hexdigest()}:{number}'
            if id(record) in result and result[id(record)] != reference:
                raise ValueError('交接原文对象对应多个记录位置；请重新打开会话后再绑定引用')
            result[id(record)] = reference
    if number != len(records):
        raise ValueError('交接原文快照已变化，不能绑定引用')
    return result


def _source_paths(session, folder):
    # The index orders sources on disk. Do not sort/materialize a directory
    # full of Path objects before feeding its transactional inventory.
    try:
        scan = os.scandir(folder)
    except FileNotFoundError:
        pass
    else:
        with scan:
            for entry in scan:
                if entry.name.endswith('.jsonl'):
                    yield Path(entry.path)
    try:
        session.stat()
    except FileNotFoundError:
        pass
    else:
        yield session


def rows(session: Path, *, reference=None, keyword='', _preview=False, _window=None, _visible=None, _select=None):
    """Stable original references from a private, incrementally validated index.

    Sources stay read-only. Changed archives are fully rehashed; current access
    permissions are applied by the caller, never inherited from the cache.
    """
    from .history_snapshot import snapshot, _RetryWithoutSearch
    session = Path(session); _safe(session)
    folder = session.parent / DIRECTORY / session.name; _safe(folder)
    # Close even when validation aborts while directory iteration is suspended;
    # exception tracebacks may retain generators long after this read fails.
    for attempt in range(2):
        try:
            with closing(_source_paths(session, folder)) as paths:
                with snapshot(session, paths, _safe, _lines, without_search=bool(attempt),
                              restart_paths=lambda: _source_paths(session, folder)) as index:
                    options = {'window': _window} if _window is not None else {}
                    if _visible is not None:options['visible'] = _visible
                    if _select is not None:options['select'] = _select
                    yield from index.rows(reference, keyword=keyword, preview=_preview, **options)
            return
        except _RetryWithoutSearch:
            # Raised only before the snapshot context yields. Never restart a
            # partially delivered result stream. Re-enumerate and revalidate
            # every source, with optional indexing disabled, at most once.
            if attempt:
                raise


def _instant(value: str):
    if not value: return None
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('历史查询时间须包含时区；请沿用记录中的偏移，例如 2026-11-10T07:00:00+08:00，不要猜测其他时区')
    return stamp.timestamp() * 1000


def _whole_day_window(since, until):
    """Both bounds must be midnight in the supplied and displayed local timezones."""
    if not since or not until:
        return False
    try:
        for value in (since, until):
            bound = datetime.fromisoformat(value.replace('Z', '+00:00'))
            local = bound.astimezone()
            if any((bound.hour, bound.minute, bound.second, bound.microsecond,
                    local.hour, local.minute, local.second, local.microsecond)):
                return False
        return True
    except (ValueError, OverflowError, OSError):
        return False


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _record_stamp(row):
    stamp = row.get('ts_ms', 0)
    if isinstance(stamp, str) and re.fullmatch(r'[0-9]+', stamp):
        stamp = int(stamp)  # Older records used decimal strings.
    if type(stamp) not in (int, float):
        raise ValueError('记录时间格式无效')
    try:
        if not math.isfinite(stamp):
            raise ValueError('记录时间格式无效')
        if stamp:
            _EPOCH + timedelta(milliseconds=stamp)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError('记录时间超出可表示范围或格式无效') from error
    return stamp


def _formatted_record_time(stamp):
    if not stamp:
        return '时间未记录', False
    when = _EPOCH + timedelta(milliseconds=stamp)
    precision = 'microseconds' if when.microsecond % 1000 else 'milliseconds'
    try:
        local = when.astimezone()
    except (OSError, OverflowError, ValueError):
        # Some Windows runtimes cannot obtain a local offset before the Unix epoch.
        return when.isoformat(timespec=precision), True
    return local.isoformat(timespec=precision), False


def _time_label(stamp):
    return _formatted_record_time(stamp)[0]


def _source_label(row):
    return (f'说话者标识：{row.get("who") or "未记录"} · 来源范围：{row.get("scope") or "未标记"}'
            f' · 关联调用：{row.get("tool_ref") or "无"}')


def _document_reports(row, reference, shown):
    from .reported_records import mentions_document
    if ((row.get('kind') or 'message') == 'message' and row.get('role') == 'user'
            and not row.get('is_head') and not row.get('is_flow') and not row.get('images')
            and mentions_document(shown)):
        return (('record:' + reference, shown[:2048]),)
    return ()


def evidence_label(row):
    kind = row.get('kind') or 'message'
    if kind == 'function_result':
        return '工具回执：执行结果以回执内容为准'
    if kind == 'function_call':
        return '工具调用：仅表示请求，不代表执行成功'
    if row.get('role') == 'user' and not row.get('is_head'):
        from .reported_records import REPORT_SCOPE, mentions_document
        if kind == 'message' and mentions_document(row.get('text')):
            return '用户发言原文，含外部文书转述。' + REPORT_SCOPE
        return '用户发言原文：引用、假设与自述须按原话语境区分'
    if row.get('role') == 'assistant' and not row.get('is_head'):
        return 'Bot 历史发言：可能包含转述或推测，不能据此认定用户原话或执行成功'
    return '系统或其他记录：不作为用户原话'


def _current_page_sources(context, room, page):
    """Label the current input only when the host can identify its exact archived row."""
    if not context:
        return ''
    console, owner = context
    incoming = getattr(console, '_incoming_prompt_context', None)
    record = getattr(incoming, 'record', None)
    if not owner or not getattr(record, 'display_id', '') or record.scope != room:
        return ''
    matches = [i for i, (_, _, row) in enumerate(page)
               if row.get('display_id') == record.display_id and row.get('scope') == room
               and row.get('role') == 'user' and row.get('kind') == 'message'
               and row.get('who') == record.who and row.get('ts_ms') == record.ts_ms
               and row['_text_length'] == len(record.text) and record.text.startswith(row['text'])]
    if len(matches) != 1 or console.current_batch_identity() is not owner:
        return ''
    from .session_view import event_scope
    batch = console.original_batch()
    if (len(batch) != 1 or batch[0].type != 'message' or batch[0].origin != 'external'
            or event_scope(batch[0]) != room or batch[0].text != record.text):
        return ''
    if len(page) == 1:
        return '本页仅命中本轮来信，它与当前输入是同一条消息。'
    counts = {}
    for i, (_, _, row) in enumerate(page):
        if i == matches[0]:
            continue
        if row.get('kind') in ('function_call', 'function_result'):
            kind = '工具记录'
        elif (row.get('role') == 'user' and (row.get('kind') or 'message') == 'message'
              and not any(row.get(flag) for flag in ('is_head', 'is_flow', 'is_system', 'is_ephemeral'))):
            kind = '用户历史发言'
        else:
            kind = '其他记录'
        counts[kind] = counts.get(kind, 0) + 1
    others = '、'.join(f'{kind} {count} 条' for kind, count in counts.items())
    return f'本页第 {matches[0] + 1} 条是本轮来信，与当前输入同源。其余为{others}。'


class _PageEvidence(set):
    def __init__(self, previous, title_source, identity, serial):
        super().__init__(previous)
        self.previous_title_source = title_source
        self.identity = identity
        self.serial = serial
        self.opened_title_source = None
        self.matched_title = False
        self.listed_title_ref = None
        self.hint_lookup = None
        self.hint_header = ''
        self.published = False


class BatchEvidence:
    """Advisory overlap only; never caches records or grants read authority."""
    def __init__(self):
        self.lock = RLock()
        self.owner = None
        self.visibility = None
        self.complete = set()
        self.title_source = None
        self.query_serial = 0
        self.open_serial = 0
        self.aliases = {}
        self.nonce = ''
        self.serial = 0

    def alias(self, ref):
        if self.owner is None: return ''
        canonical = ref if ref.startswith('record:') else 'record:' + ref
        for key, value in self.aliases.items():
            if value == canonical:
                self.aliases[key] = self.aliases.pop(key)
                return key
        if len(self.aliases) >= 2048:
            self.aliases.pop(next(iter(self.aliases)))
        self.serial += 1
        key = f'evidence:{self.nonce}:{self.serial}'
        self.aliases[key] = canonical
        return key

    def resolve(self, reference):
        if not isinstance(reference, str): raise ValueError('invalid evidence reference')
        if reference.startswith('evidence:'):
            if reference not in self.aliases:
                raise PermissionError('evidence alias expired; read original again')
            return self.aliases[reference]
        # read_events exposes the exact bare record_ref as well as evidence_ref.
        # Canonicalize only that complete syntax; existence and current access
        # still belong to each consumer's fresh source verification.
        if re.fullmatch(r'[0-9a-f]{64}:[1-9][0-9]{0,18}', reference):
            return 'record:' + reference
        return reference

    def bind(self, owner, visibility):
        with self.lock:
            if owner is None or owner is not self.owner or visibility != self.visibility:
                from secrets import token_hex
                self.complete = set()
                self.title_source = None
                self.query_serial = self.open_serial = 0
                self.aliases.clear()
                self.nonce = token_hex(4)
                self.serial = 0
                self.owner, self.visibility = owner, visibility
            return self.complete if owner is not None else None


    @contextmanager
    def returning_page(self, owner, visibility):
        """Register only the complete originals in a successfully assembled tool return."""
        with self.lock:
            current = self.bind(owner, visibility)
            identity = self.complete
            prior = frozenset(current) if current is not None else None
            self.query_serial += 1
            pending = _PageEvidence(prior, self.title_source, identity, self.query_serial) if prior is not None else None

        def alias(ref):
            with self.lock:
                if self.complete is not identity:
                    raise PermissionError('原始记录请求的会话或权限已变化，请重新读取')
                return self.alias(ref)

        yield pending, alias
        with self.lock:
            if self.complete is not identity:
                raise PermissionError('原始记录请求的会话或权限已变化，请重新读取')
            if pending is not None:
                added = pending.difference(prior, current)
                # Keep other calls' recent registrations when trimming the old hints.
                excess = max(0, len(current) + len(added) - 2048)
                if excess:
                    current.difference_update(islice((ref for ref in prior if ref in current), excess))
                current.update(islice(added, 2048 - len(current)))


    def publish_page(self, pending, result):
        """Commit a title or its optional hint only after retrieval and enrichment succeed."""
        if not isinstance(pending, _PageEvidence):
            return result
        # The source may have changed while supplementary retrieval was in flight.
        # Keep fresh source/room validation outside the shared batch lock.
        hint = pending.hint_lookup() if pending.hint_header and pending.hint_lookup else ''
        with self.lock:
            if pending.identity is not self.complete or pending.published:
                raise PermissionError('原始记录请求的会话或权限已变化，请重新读取')
            pending.published = True
            if pending.listed_title_ref is not None:
                if (hint and pending.listed_title_ref not in self.complete
                        and pending.previous_title_source is self.title_source):
                    from .record_follow_title import insert_hint
                    return insert_hint(result, result, hint)
                return result
            if pending.opened_title_source is not None:
                # An older slow original open must not replace a newer completed one.
                if pending.serial > self.open_serial:
                    if hint and pending.opened_title_source != self.title_source:
                        from .record_follow_title import insert_hint
                        result = insert_hint(result, pending.hint_header, hint)
                    self.title_source = pending.opened_title_source or None
                    self.open_serial = pending.serial
            elif self.title_source is not None and self.title_source is pending.previous_title_source:
                if pending.matched_title:
                    self.title_source = None
                elif hint:
                    from .record_follow_title import insert_hint
                    annotated = insert_hint(result, pending.hint_header, hint)
                    if annotated is not result:
                        self.title_source = None
                        result = annotated
        return result


def _remember(complete, refs):
    if complete is not None:
        if len(complete) + len(refs) > 2048:
            complete.clear()  # Losing an advisory hint must not affect retrieval.
        complete.update(refs)


def _category_matches(row, category):
    kind = row.get('kind') or 'message'
    is_dialogue = kind == 'message' and row.get('role') in ('user', 'assistant') and not row.get('is_head')
    is_tool = kind in ('function_call', 'function_result')
    if category == 'dialogue':return is_dialogue
    if category in ('user', 'assistant'):return is_dialogue and row.get('role') == category
    if category == 'tools':return is_tool
    if category == 'system':return not (is_dialogue or is_tool)
    return True


def _listed_title_source(page, scope, keyword, already_read):
    """Suggest a title written in a fully displayed sheet, using only this page."""
    from .memory import _literal_number
    from .record_follow_title import found_by_name, human_original, referenced_title
    from .requested_quote import heading_title

    if not keyword or _literal_number(keyword) != keyword:
        return ''
    matches = []
    for _, ref, row in page:
        body = row['text']
        if (human_original(row, scope) and row['_text_length'] == len(body) <= 240
                and heading_title(body, keyword) is not None):
            matches.append((ref, body))
    if len(matches) != 1:
        return ''
    ref, body = matches[0]
    if ref in already_read:
        return ''
    title = referenced_title(body)
    if not title or found_by_name(page, (ref, title), scope, title):
        return ''
    return ref, title


@contextmanager
def _listing_view(session):
    from .history_snapshot import snapshot, _RetryWithoutSearch
    session=Path(session);_safe(session)
    folder=session.parent/DIRECTORY/session.name;_safe(folder)
    for attempt in range(2):
        try:
            with closing(_source_paths(session,folder)) as paths:
                with snapshot(session,paths,_safe,_lines,without_search=bool(attempt),
                              restart_paths=lambda:_source_paths(session,folder)) as index:
                    yield index
            return
        except _RetryWithoutSearch:
            if attempt:raise


def search(session: Path, visibility, *, grep='', since='', until='', sender_key='',
           limit=20, before='', reference='', offset=0, category='dialogue', _complete=None, _alias=None, _receipt_context=None) -> str:
    # Keep the validated view locked through page hydration. Filtering and
    # display must come from the same source snapshot, including during paging.
    with ExitStack() as lifetime:
        return _search(session,visibility,grep=grep,since=since,until=until,sender_key=sender_key,
                       limit=limit,before=before,reference=reference,offset=offset,category=category,
                       _complete=_complete,_alias=_alias,_lifetime=lifetime,_receipt_context=_receipt_context)


def _search(session: Path, visibility, *, grep='', since='', until='', sender_key='',
           limit=20, before='', reference='', offset=0, category='dialogue', _complete=None, _alias=None, _lifetime=None, _receipt_context=None, _opened_row=None, _opened_day='') -> str:
    visibility.require()
    if category not in ('dialogue', 'user', 'assistant', 'tools', 'system', 'all'):
        raise ValueError('原始记录 category 须为 dialogue、user、assistant、tools、system 或 all')
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('原始记录 limit 须在 1 到 50 之间')
    if type(offset) is not int or offset < 0:
        raise ValueError('原文 offset 须为非负整数')
    if reference:
        reference = reference.strip()
        if reference.startswith('[record:') and reference.endswith(']'):
            reference = reference[8:-1]
        elif reference.startswith('record:'):
            reference = reference[7:]
        if not _REF.fullmatch(reference):
            raise ValueError('原文引用格式无效；请完整复制返回的 record_ref，保留末尾的冒号及序号，不要截断')
    start, end = _instant(since), _instant(until)
    if start is not None and end is not None and start > end: raise ValueError('查询时间范围倒置')
    if offset and not reference:
        raise ValueError('原文 offset 需要同时提供 record_ref')
    visible = []
    total = 0
    original = None
    use_preview = not reference
    # This call's host-supplied policy gates expensive body work too. Never
    # retain it in the snapshot or skip full source validation on its account.
    work_visibility = (visibility.permits if not visibility.administrator and (grep or reference)
                       else None)
    def selected_for_work(row):
        if not _category_matches(row, category):return False
        if start is not None or end is not None or sender_key:
            try:
                stamp = _record_stamp(row)
            except ValueError:
                # Invalid timestamps used to fail only after a keyword hit,
                # before sender filtering. Defer that decision until the body
                # is matched; never hide corruption behind a sender mismatch.
                return True
            if start is not None and stamp < start or end is not None and stamp > end:return False
            if sender_key and row.get('who') != sender_key:return False
        return True
    # Only reject metadata that proves this record cannot enter the listing.
    # Keep the authoritative filter/error sequence below and never cache this
    # query's predicates. Original-reference reads ignore listing filters.
    work_select = (selected_for_work if grep and not reference and
                   (category != 'all' or start is not None or end is not None or sender_key) else None)
    from .bounded_archive_read import field_location, question, select
    ask = (question(_receipt_context, visibility.current, grep) if not reference
           and isinstance(_complete, _PageEvidence) and not _complete.previous_title_source
           and not any((before, since, until, sender_key)) and category in ('dialogue', 'user', 'all') else '')
    target_index = _lifetime.enter_context(_listing_view(session)) if ask else None
    listing = None
    def matching(lookup=None):
        nonlocal listing
        if _opened_row is not None and reference == lookup == _opened_row[0]:
            # The bounded lookup already verified these full bytes on this live index.
            candidates = (_opened_row,)
        elif target_index is not None:
            candidates = target_index.rows(lookup, keyword=grep, preview=True, visible=visibility.permits,
                                           select=work_select)
        elif not grep and not reference:
            if listing is None:listing=_lifetime.enter_context(_listing_view(session))
            candidates=listing.rows(lookup,preview=True,listing=True)
        else:candidates=rows(session, reference=lookup, keyword=grep if not reference else '',
                             _preview=use_preview, _visible=work_visibility,
                             **({'_window': (offset, 2000)} if reference else {}),
                             **({'_select': work_select} if work_select is not None else {}))
        for ref,row in candidates:
            if not visibility.administrator and not visibility.permits(row.get('scope')): continue
            if reference:
                yield ref, row
                continue
            if not _category_matches(row, category):continue
            stamp = _record_stamp(row)
            if start is not None and stamp < start or end is not None and stamp > end: continue
            if sender_key and row.get('who') != sender_key: continue
            # Keyword previews already verified the complete cached body;
            # matching against the first240 characters would lose tail hits.
            if grep and not use_preview and grep.casefold() not in row['text'].casefold(): continue
            yield stamp, ref, row
    try:
        if reference:
            original = next(matching(reference), None)
        else:
            anchor = next(matching(before), None) if before else None
            for stamp, ref, row in matching():
                if anchor is not None and (stamp, ref) >= anchor[:2]: continue
                total += 1
                if len(visible)<limit or (stamp,ref)>visible[0][:2]:
                    # Count every matching row, retain only potential winners.
                    item=(stamp,ref,dict(row))
                    if len(visible)<limit:heapq.heappush(visible,item)
                    else:heapq.heapreplace(visible,item)
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError('原始记录读取不完整，请检查诊断；不能视为没有记录') from error
    if before and not reference and anchor is None:
        raise ValueError('翻页位置已不可用，请保留原筛选条件重新查询')
    if reference:
        if original is None:
            raise PermissionError('当前可见原始记录中没有这一条')
        ref, row = original
        text = row['text']
        text_length = row['_text_length']
        when = _time_label(_record_stamp(row))
        if offset > text_length:
            raise ValueError(f'原文 offset 超出正文长度（{text_length} 字）；请使用上次回执的续读位置')
        stop = min(offset + 2000, text_length)
        repeated = _complete is not None and ref in _complete
        if offset == 0 and stop == text_length:
            _remember(_complete, [ref])
        receipt_alias = _alias(ref) if _alias is not None else None
        short = f'本批引用 evidence_ref="{receipt_alias}"（仅为原文定位别名，仍需当前权限和来源校验）\n' if receipt_alias is not None else ''
        search_receipt = row.get('role') == 'assistant' and row.get('kind') == 'function_result' and row.get('name') == 'read_events'
        repeated_note = '本批次已返回此条完整原文；本次为重复读取。\n'
        end_note = '（原文结束）'
        if search_receipt:
            from .archive_receipt import navigation
            short = '检索回执 · read_events（核对上次查询过程）' + (
                f' · 本批引用 evidence_ref="{receipt_alias}"\n' if receipt_alias is not None else '\n')
            if offset == 0:
                short += navigation(session, visibility, ref, row, _receipt_context, _alias)
            repeated_note = '本批次已返回这条完整检索回执。\n'
            end_note = '（检索回执结束）'
        if isinstance(_complete, _PageEvidence) and row.get('role') == 'user':
            from .record_follow_title import human_original, referenced_title
            title = (referenced_title(text) if offset == 0 and stop == text_length
                     and human_original(row, visibility.current) else '')
            # Each successful open creates a new object, including a reopen of the same row.
            _complete.opened_title_source = (ref, title) if title else ()
            if (offset > 0 and stop == text_length <= 2000 and not repeated
                    and human_original(row, visibility.current)):
                from .record_follow_title import lookup_hint, visible_tail_title
                tail_title = visible_tail_title(text)
                if tail_title:
                    _complete.hint_lookup = lambda: lookup_hint(
                        session, visibility, (ref, tail_title), '', _receipt_context, on_open=True, tail=(offset, text))
            if title and _complete.opened_title_source != _complete.previous_title_source:
                from .record_follow_title import lookup_hint
                _complete.hint_lookup = lambda: lookup_hint(
                    session, visibility, (ref, title), '', _receipt_context, on_open=True)
        from .original_evidence import OriginalText, source
        from .requested_quote import AUTOMATIC_DAY_NOTE, FULL_CLOCK_NOTE
        originals = source(when, text, 'record:'+ref) if row.get('role') == 'user' else ()
        # Only the bounded, automatic expansion can request a calendar-day header.
        # An explicit open, including a reopen of these same bytes, keeps the instant.
        day_shown = bool(_opened_row is not None and _opened_day and when.startswith(_opened_day + 'T'))
        displayed_stamp = _opened_day if day_shown else when
        clock_note = AUTOMATIC_DAY_NOTE if day_shown else FULL_CLOCK_NOTE
        source_clock = ('归档写入日期：' if day_shown else '归档写入时间：') + displayed_stamp
        header = (short + (repeated_note if repeated else '') + f'[record:{ref}] {row.get("role", "")} · {row.get("kind", "message")} · {row.get("name", "")}\n'
                  f'证据类型：{evidence_label(row)} · {source_clock}（{clock_note}）\n{_source_label(row)}')
        if isinstance(_complete, _PageEvidence) and _complete.hint_lookup:
            _complete.hint_header = header
        header += f'\n正文共 {text_length} 字\n'
        spans = ()
        if _opened_row is not None and 240 < len(text) == text_length and originals:
            from .original_evidence import OriginalSpan
            # A bounded full original is always sent verbatim. Its prefix may
            # anchor a later, independently authorized preview of the same row.
            key = ('record_full_prefix', row['scope'], ref)
            spans = (OriginalSpan(key, len(header), len(header) + 240, source_length=text_length),)
        return OriginalText(header + text +
                (f'\n继续读取：record_ref={ref}，offset={stop}' if stop < text_length else '\n' + end_note), originals,
                document_reports=_document_reports(row, ref, text), wire_spans=spans,
                record_times=(('record:'+ref, displayed_stamp),) if originals else (),
                record_scopes=(('record:'+ref, row.get('scope')),),
                complete_records=(('record:'+ref, text_length),) if offset == 0 and stop == text_length else ())
    visible.sort(key=lambda item: (item[0], item[1]), reverse=True)
    page = visible[:limit]
    if listing is not None:
        hydrated=[]
        for stamp,ref,_ in page:
            with closing(listing.rows(ref,preview=True)) as found:
                full=next(found,None)
            if full is None:raise ValueError('原始记录派生页不完整')
            hydrated.append((stamp,ref,full[1]))
        page=hydrated
    plain_query = grep and not any((before, since, until, sender_key)) and category in ('user', 'dialogue', 'all')
    follow = _complete if plain_query and isinstance(_complete, _PageEvidence) and _complete.previous_title_source else None
    if follow is not None and (not total or (
            total == 1 and page[0][1] in follow and page[0][2].get('role') == 'user'
            and page[0][2].get('scope') == visibility.current)):
        from .record_follow_title import lookup_hint
        follow.hint_lookup = lambda: lookup_hint(session, visibility, follow.previous_title_source, grep, _receipt_context)
    if not page:
        empty = '当前可见范围内没有匹配的原始记录。本次可见记录已按指定筛选及翻页范围查完；不代表所有记录均无相关事实。'
        if follow is not None and follow.hint_lookup:
            follow.hint_header = empty
        return empty
    output = ['每行首列是归档写入时间。发送、送达及业务时间须另有依据。按主机本地时区显示，含UTC偏移；判断今天或昨天请比较完整的本地日期。', '按写入时间倒序列出。核对用户说过什么可用 category="user"，查看 Bot 过去说法可用 category="assistant"。两者不能互相替代；翻页保持筛选条件。']
    from .original_evidence import OriginalSpan, OriginalText, source
    extra = select(target_index, page, total, visibility, grep, ask, _receipt_context, _complete)
    expanded_slot = None
    exact = None
    shown = []
    complete_refs = []
    repeated_count = 0
    used = 0
    utc_fallback = False
    record_times = []
    from .pair_receipt_scope import DAY_HEADER, document_date_view
    day_view = (isinstance(_complete, _PageEvidence) and not sender_key
                and category in ('user', 'dialogue', 'all') and document_date_view(_receipt_context, visibility.current)
                and ((grep and not since and not until) or _whole_day_window(since, until)))
    compact_window = (category == 'user' and not grep and (since or until)
                      and _alias is not None and len(page) >= 3)
    alias_help = '（可用于 read_events.record_ref 或 attention_update.changes.evidence）'
    if compact_window:
        output.append('逐条标有正文完整或预览截断。截断只限本页显示，可用 record_ref 读取全文。'
                      '各条的 evidence_ref 可用于 read_events.record_ref 或 attention_update.changes.evidence。')
    for stamp, ref, row in page:
        when, utc_only = _formatted_record_time(stamp)
        short_clock = (day_view and stamp and not utc_only and row.get('role') == 'user'
                       and row.get('kind') == 'message' and row.get('scope') == visibility.current)
        display_time = when[:10] if short_clock else when
        prefix = (f'[record:{ref}] record_ref="{ref}"\n{display_time}｜{row.get("role", "")}｜{row.get("kind", "message")}｜{row.get("name", "")}\n'
                  f'证据类型：{evidence_label(row)}\n{_source_label(row)}\n')
        excerpt = row['text'][:240]
        entry = (prefix + excerpt + (f'\n〔检索预览已截断：仅前 240 字，全文 {row["_text_length"]} 字；这不是原记录中断。用 record_ref 读取全文后再判断。〕' if row['_text_length'] > 240 else '\n〔此条正文完整，可直接核对引用，无需重复读取〕'))
        repeated = _complete is not None and ref in _complete
        if repeated:
            entry += '\n〔本批次已返回完整原文；重复命中，不是新证据〕'
        # A shorter clock label must not admit an extra row or move the cursor.
        clock_charge = len(when) - len(display_time)
        if shown and used + len(entry) + clock_charge > 6500:
            break
        if _alias is not None:
            alias_line = f'\n本批引用 evidence_ref="{_alias(ref)}"'
            entry += alias_line + alias_help
        # Keep the old page-size charge: shortening display instructions must not
        # admit extra records, move the continuation cursor, or change scope.
        page_cost = len(entry) + clock_charge
        if compact_window:
            length = row['_text_length']
            status = (f'预览已截断：前 240 字，全文 {length} 字' if length > 240 else '此条正文完整')
            repeat = '；本批已返回完整原文' if repeated else ''
            entry = f'{prefix}{excerpt}\n〔{status}{repeat}〕{alias_line}'
        if (grep and row.get('role') == 'user' and (row.get('kind') or 'message') == 'message'
                and row.get('scope') and row['scope'] == visibility.current
                and not row.get('record_ref') and row['_text_length'] > 240):
            # Without an explicit ref, the index derives this ref from all original
            # bytes through this record. A tail edit must not reuse a stale preview.
            key = ('record_preview', row['scope'], ref)
            span = OriginalSpan(key, len(prefix), len(prefix) + len(excerpt), source_length=row['_text_length'])
            entry = OriginalText(entry, wire_spans=(span,))
        if extra and ref == extra[0]:
            alias_note = alias_line + alias_help if _alias is not None else ''
            location = field_location(extra[2]['text'])
            expanded_slot = len(output), prefix + '〔' + location + '〕' + alias_note
        output.append(entry)
        utc_fallback |= utc_only
        repeated_count += int(repeated)
        if row['_text_length'] <= 240:
            complete_refs.append(ref)
            if stamp and row.get('role') == 'user':
                record_times.append(('record:'+ref, display_time))
        shown.append((stamp, ref, row))
        used += page_cost + 1
    page = shown
    if expanded_slot is not None:
        ref, title, verified = extra
        from .requested_quote import AUTOMATIC_DAY_NOTE, DATE_WANTED, FULL_CLOCK_NOTE
        clock, utc = _formatted_record_time(_record_stamp(verified))
        day = clock[:10] if day_view and DATE_WANTED.search(ask) and not utc and clock != '时间未记录' else ''
        candidate = _search(session, visibility, reference=ref, _opened_row=(ref, verified), _opened_day=day)
        # Reserve the existing page charge before adding the optional direct read.
        charge = len(candidate) + (len(clock) - len(day) + len(FULL_CLOCK_NOTE) - len(AUTOMATIC_DAY_NOTE) if day else 0)
        if used + charge <= 6500 and question(_receipt_context, visibility.current, grep) == ask:
            exact = candidate
            index, replacement = expanded_slot
            output[index] = replacement
            complete_refs.append(ref)
    if utc_fallback:
        output[0] = '时间戳包含UTC偏移。系统无法提供当地时区的旧记录按UTC显示。每行首列为归档写入时间，发送、送达及业务时间须另有依据。'
    if day_view:
        output[0] = (DAY_HEADER + ('无法转换当地时区的旧记录保留完整 UTC 时刻。' if utc_fallback else ''))
    source_breakdown = ''
    if _complete is not None:
        if plain_query and isinstance(_complete, _PageEvidence) and follow is None:
            listed = (extra[:2] if exact is not None and extra[1] else
                      _listed_title_source(page, visibility.current, grep, _complete))
            if listed:
                from .record_follow_title import lookup_hint
                _complete.listed_title_ref = listed[0]
                _complete.hint_lookup = lambda: lookup_hint(
                    session, visibility, listed, '', _receipt_context, on_open=True, from_list=True,
                    alias=_alias if exact is not None else None)
        new_complete_count = sum(ref not in _complete for ref in complete_refs)
        preview_only_count = len(page) - repeated_count - new_complete_count
        source_breakdown = _current_page_sources(_receipt_context, visibility.current, page)
        label = '记录' if source_breakdown else '原文'
        if source_breakdown:
            output[1] = source_breakdown
        output.insert(0, f'本轮查阅进度：本页先前已完整返回 {repeated_count} 条，'
                      f'本次新增完整{label} {new_complete_count} 条，尚无完整返回记录、本次仅给预览 {preview_only_count} 条。' +
                      (' 本页无新增原文条目；可依据已有原文作答，只有尚缺证据时才需继续查询。' if repeated_count == len(page) else ''))
        from .pair_receipt_scope import comparison_note
        scope_note = comparison_note(_receipt_context, visibility.current, page, _complete)
        if scope_note:
            output.insert(1, scope_note)
        _remember(_complete, complete_refs)
    output.insert(0, f'本次筛选：category={category} · 关键词={grep or "未指定"} · 本页 {len(page)} 条 · 本页之后 {total - len(page)} 条。')
    if category == 'all' and not source_breakdown:
        output.insert(1, 'all 包含工具回执与系统记录，可能复现本轮查询或旧引用；这些命中不是新增用户证言。核对用户原话可改用 user。')
    if isinstance(_complete, _PageEvidence) and _complete.listed_title_ref is not None:
        _complete.hint_header = output[0]
    if follow is not None:
        from .record_follow_title import found_by_name
        follow.matched_title = found_by_name(page, follow.previous_title_source, visibility.current, grep)
        if follow.hint_lookup and not follow.matched_title:
            follow.hint_header = '\n'.join(output[:2 if category == 'all' and not source_breakdown else 1])
    output.append('列表只展示本页匹配项，不能据此断言更早没有记录或某次执行未发生；可用 record_ref 读取完整原文。')
    if total > len(page): output.append(f'还有记录；保留筛选条件，before={page[-1][1]} 继续翻页。')
    else: output.append('（匹配结果结束）')
    originals = [s for stamp, ref, row in page if row.get('role') == 'user' and not (exact is not None and ref == extra[0])
                 for s in source(_time_label(stamp), row['text'][:240], 'record:'+ref)]
    reports = (fact for _, ref, row in page for fact in _document_reports(row, ref, row['text'][:240]))
    joined = OriginalText.join(output)
    result = OriginalText(joined, originals, document_reports=reports, wire_spans=joined.wire_spans,
                        record_times=record_times,
                        complete_records=(('record:'+ref, row['_text_length']) for _, ref, row in page
                                          if row.get('role') == 'user' and row['_text_length'] <= 240),
                        record_scopes=(('record:'+ref, row.get('scope')) for _, ref, row in page
                                       if row.get('role') == 'user'))
    if exact is not None:
        return OriginalText.join((exact, result))
    return result
