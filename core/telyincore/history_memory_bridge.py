"""Bounded archive supplement for unfiltered user-history keyword queries.

Current session search does not cover archived memory. Keep counts/provenance
separate and never treat a search result's own receipt as new user evidence.
"""
from threading import Lock
from .memory_receipt import MemorySearchMiss
from .original_evidence import OriginalText


_SCOPE_HINT = ('\n〔资料范围〕以上计数仅为活动会话记录，不含归档记忆文件。'
               '本轮检索回执只证明查过什么，不是新增用户原话；已展示的完整原文不必再递归读取其检索回执。'
               '查旧事实可用 memory_search 或 memory_navigate 翻原文。')
_ARCHIVE_HEADING = '\n〔独立归档记忆候选；不计入上面的会话页数，不证明同一事项〕\n'
_STATUS = {
    'running': '\n同一关键词的归档补查正在进行，本次仅返回活动会话页。',
    'failed': '\n上次用这个关键词补查归档未成功，本次仅返回活动会话页。',
    'denied': '\n上次用这个关键词补查归档时访问被拒，本次仅返回当前可见的活动会话页。',
    'completed': '',
}


class HistoryMemoryBridge:
    def __init__(self):
        self.key = None
        self.queries = {}
        self._lock = Lock()

    def enrich(self, result, *, identity, visibility, keyword, lookup,
               category='dialogue', reference='', before='', since='', until='', offset=0, sender_key='', limit=20):
        with self._lock:
            owner = identity()
            key = (owner, visibility)
            if self.key is None or self.key[0] is not owner or self.key[1] != visibility:
                self.key = key
                self.queries = {}
            if reference or before or since or until or offset or sender_key or category not in ('user','dialogue'):
                return result
            query = str(keyword or '').strip()
            if not query or type(limit) is not int or limit < 1:
                return result + _SCOPE_HINT
            attempts = self.queries
            if query in attempts:
                return result + _SCOPE_HINT + _STATUS[attempts[query]]
            if len(attempts) >= 2:
                return result + _SCOPE_HINT + '\n本批两次自动归档补查已用完，本次未查这个关键词的归档。'
            attempts[query] = 'running'

        # Never hold the per-batch lock across I/O. A replaced map belongs
        # to the retired audience and cannot add status to its successor.
        try:
            supplement = lookup(query, min(5, limit))
            missed = isinstance(supplement, MemorySearchMiss)
            shown = supplement.beside_records if missed else supplement
            combined = result + _SCOPE_HINT + _ARCHIVE_HEADING + shown
            if missed and isinstance(combined, OriginalText):
                combined.archive_search_miss = (query, supplement.visibility)
        except BaseException as error:
            denied = isinstance(error, PermissionError)
            with self._lock:
                attempts[query] = 'denied' if denied else 'failed'
            if not isinstance(error, Exception):
                raise
            if denied:
                raise PermissionError('归档补查访问被拒。') from None
            raise RuntimeError('归档补查未成功，本次工具未返回检索结果。') from None

        with self._lock:
            if identity() is not owner or self.queries is not attempts:
                attempts[query] = 'failed'
                raise ValueError('history query batch changed during archive lookup')
            attempts[query] = 'completed'
        return combined
