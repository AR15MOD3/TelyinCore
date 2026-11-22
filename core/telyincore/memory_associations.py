"""Rebuildable indexes for observed senders and explicit topic annotations."""

from collections import defaultdict
from datetime import datetime, timezone
import heapq
import re
import unicodedata

from .events import RangeQuery
from .learned_knowledge import ID as KNOWLEDGE_ID, read as read_knowledge
from .tools import ToolDef, ToolTag

_KIND = "memory-association"
_EVENT = re.compile(r"event:([1-9][0-9]{0,18})")
_REFRESH_PAGE = 128

DECLARATIONS = (
    ToolDef(name="memory_link",
            description="给可读的原始事件加话题标签，可附一个相关事件或知识 ID。标签仅供回查。",
            parameters={"type": "object", "properties": {
                "event": {"type": "integer", "minimum": 1},
                "topic": {"type": "string"},
                "target": {"type": "string", "description": "相关的 event:数字 或知识 ID"},
            }, "required": ["event", "topic"]}, tags=(ToolTag.WRITE,)),
    ToolDef(name="memory_related",
            description="按话题、发言账号或已绑定的人物 ID 回查关联事件，可组合筛选。结果包含可继续读取的 ID。",
            parameters={"type": "object", "properties": {
                "topic": {"type": "string"},
                "person": {"type": "string", "description": "实际发言账号或已绑定的人物 ID"},
                "before": {"type": "integer", "minimum": 1, "description": "只看此游标之前的结果"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 40},
            }}, tags=(ToolTag.READ,)),
)


def _topic(value):
    if not isinstance(value, str) or len(value) > 120 or not value.strip():
        raise ValueError("话题须为 1 至 120 字。")
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _annotation(row):
    return row is not None and row.type == _KIND and row.origin == "internal" and row.source == "memory" and isinstance(row.meta, dict)


class _Index:
    def __init__(self):
        self.cursor = 0
        self.by_sender = defaultdict(set)
        self.by_topic = defaultdict(set)
        self.by_event = defaultdict(set)

    def refresh(self, store):
        latest = store.latest_cursor()
        if latest < self.cursor:
            self.__init__()
        if latest > self.cursor:
            upper = latest
            while upper > self.cursor:
                # EventStore.range returns the newest page in ascending order.
                # Hold one page of payloads, even when the index starts cold.
                page = store.range(RangeQuery(from_cursor=self.cursor + 1,
                                               to_cursor=upper, limit=_REFRESH_PAGE))
                if not page:
                    break
                for row in page:
                    if row.origin == "external" and row.sender_key:
                        self.by_sender[row.sender_key].add(row.cursor)
                    elif _annotation(row):
                        topic = row.meta.get("topic")
                        event = row.meta.get("event")
                        if isinstance(topic, str) and type(event) is int:
                            self.by_topic[topic].add(row.cursor)
                            self.by_event[event].add(row.cursor)
                upper = page[0].cursor - 1
                del page, row
            self.cursor = latest


def handlers(home, store, context):
    index = _Index()

    def can_read_target(scope, target):
        match = _EVENT.fullmatch(target)
        if match:
            row = store.get(int(match[1]))
            return row is not None and scope.event(row)
        if KNOWLEDGE_ID.fullmatch(target):
            try:
                read_knowledge(home, scope, target)
            except (PermissionError, OSError, ValueError):
                return False
            return True
        return False

    def link(event, topic, target=""):
        scope, _book = context()
        scope.require()
        if type(event) is not int or event < 1:
            raise ValueError("事件游标须为正整数。")
        label = _topic(topic)
        row = store.get(event)
        if row is None or not scope.event(row):
            raise PermissionError("当前范围内没有可关联的来源事件。")
        if not isinstance(target, str) or (target and not can_read_target(scope, target)):
            raise PermissionError("当前范围内没有可关联的目标。")
        written = store.append({
            "type": _KIND, "source": "memory", "origin": "internal",
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "text": f"话题标注：{label}",
            "meta": {"scope": scope.current, "event": event, "topic": label, "target": target},
        })
        if not written:
            raise RuntimeError("话题关联未保存。")
        return f"已保存话题关联 event:{written.cursor}。"

    def related(topic="", person="", before=None, limit=12):
        scope, book = context()
        scope.require()
        if not isinstance(person, str) or len(person) > 160:
            raise ValueError("人物须为实际发言账号或已绑定 ID。")
        if not topic and not person:
            raise ValueError("请指定话题或人物。")
        if type(limit) is not int or not 1 <= limit <= 40:
            raise ValueError("limit 须在 1 至 40 之间。")
        if before is not None and (type(before) is not int or before < 1):
            raise ValueError("before 须为正整数。")
        if store.skipped:
            return "事件来源有不可读分片，暂时无法完整回查关联。"
        index.refresh(store)
        accounts = {person} if person else set()
        if person and (scope.administrator or scope.current.startswith("private:")):
            bound = book.resolve(person) or next((p for p in book.people() if p.id == person), None)
            if bound and (scope.administrator or any(scope.permits("private:" + key) for key in bound.accounts)):
                accounts.update(bound.accounts)
        def annotation_matches(cursor):
            annotation = store.get(cursor)
            if _annotation(annotation) and scope.permits(annotation.meta.get("scope")):
                return annotation.meta
            return None

        if topic:
            selected = {
                meta["event"] for cursor in index.by_topic.get(_topic(topic), ())
                if (meta := annotation_matches(cursor)) is not None
            }
        else:
            selected = {cursor for account in accounts for cursor in index.by_sender.get(account, ())}

        def matches():
            for cursor in selected:
                if before is not None and cursor >= before:
                    continue
                row = store.get(cursor)
                if row is not None and scope.event(row) and (not person or row.sender_key in accounts):
                    yield row

        # Keep only the visible page plus one lookahead for the continuation.
        # A small response must not retain every matching message body.
        ordered = heapq.nlargest(limit + 1, matches(), key=lambda row: row.cursor)
        lines = []
        for row in ordered[:limit]:
            key = row.cursor
            targets = set()
            topics = set()
            for cursor in index.by_event.get(key, ()):
                meta = annotation_matches(cursor)
                if meta is not None:
                    topics.add(meta["topic"])
                    target = meta.get("target")
                    if isinstance(target, str) and target and can_read_target(scope, target):
                        targets.add(target)

            lines.append(f"event:{key}｜{row.ts}｜{row.sender_key or row.source}｜{row.text[:160]}"
                         + (f"｜话题 {'、'.join(sorted(topics))}" if topics else "")
                         + (f"｜可读关联 {'、'.join(sorted(targets))}" if targets else ""))
        if len(ordered) > limit:
            lines.append(f"（继续回查可传 before={ordered[limit-1].cursor}）")
        return "\n".join(lines) or "当前范围没有匹配的关联。"

    return {"memory_link": link, "memory_related": related}
