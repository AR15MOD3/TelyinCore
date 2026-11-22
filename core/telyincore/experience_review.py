"""Optional experience assessments linked to recorded evidence."""

from datetime import datetime, timezone

from .events import RangeQuery
from .tools import ToolDef, ToolTag

_KIND = "experience-review"
_ASSESSMENTS = ("pending", "supports", "challenges", "corrects", "dismissed")

DECLARATIONS = (
    ToolDef(
        name="experience_note",
        description="保存一次待验证的经验或补充支持、反驳、更正。分别填写观察与解释，"
                    "有关某人的预测或未核实推断，subject 填 current（本轮唯一作者）或发言账号，observation 逐字引该作者原话，interpretation 写你的猜测；没有对应原话就先放着。首次为 pending。想留自己的预测，直接在这条记录里引原话并写清具体猜测即可，无需另写画像。可引用可读事件和工具回执；人物观察不进入已确认画像。",
        parameters={
            "type": "object",
            "properties": {
                "observation": {"type": "string", "description":"观察到的内容。人物观察须逐字引用本轮对应发言者原话。"},
                "subject": {"type": "string", "description": "人物待核实观察填current或当前作者的可信账号键；不填则为一般经验。昵称不作账号。"},
                "interpretation": {"type": "string", "description":"你据此作出的解释或预测，保留未知和其他可能。"},
                "assessment": {"type": "string", "enum": list(_ASSESSMENTS)},
                "experience": {"type": "integer", "minimum": 1},
                "events": {"type": "array", "items": {"type": "integer", "minimum": 1},
                           "maxItems": 12},
            },
            "required": ["observation", "interpretation"],
        },
        tags=(ToolTag.WRITE,), effect="immediate",
    ),
    ToolDef(
        name="experience_read", description="查看授权的待验证经验、人物观察及后续判断；不是已确认画像。可按经验 ID、账号或关键词查。",
        parameters={"type": "object", "properties": {
            "experience": {"type": "integer", "minimum": 1},
            "query": {"type": "string"},
            "subject": {"type": "string"},
        }},
        tags=(ToolTag.READ,),
    ),
)


def _visible(scope, row):
    return (row is not None and row.type == _KIND and row.origin == "internal"
            and row.source == "memory" and isinstance(row.meta, dict)
            and scope.permits(row.meta.get("scope")))


def handlers(store_for, scope_for, *, turn_for=None):
    def note(observation, interpretation, assessment="pending", experience=None, events=(), subject=""):
        scope = scope_for()
        scope.require()
        if assessment not in _ASSESSMENTS:
            raise ValueError("经验判断类型无效。")
        if not all(isinstance(value, str) and value.strip() and len(value) <= 2000
                   for value in (observation, interpretation)):
            raise ValueError("请分别填写观察与解释，各最多 2000 字。")
        if not isinstance(events, (list, tuple)) or len(events) > 12:
            raise ValueError("最多关联 12 个事件。")
        if experience is None and assessment != "pending":
            raise ValueError("补充判断需要原经验 ID。")
        store = store_for()
        root = None
        if experience is not None:
            if type(experience) is not int or experience < 1:
                raise ValueError("经验 ID 须为正整数。")
            root = store.get(experience)
            if not _visible(scope, root) or root.meta.get("experience") is not None:
                raise PermissionError("当前可见经验中没有这一条。")
            if root.meta["scope"] != scope.current:
                raise PermissionError("补充判断须在原经验的会话范围内记录。")
        for cursor in events:
            if type(cursor) is not int or cursor < 1:
                raise ValueError("事件 ID 须为正整数。")
            row = store.get(cursor)
            if row is None or not scope.event(row):
                raise PermissionError("当前可见事件中没有可引用的这一条。")
        # Person observations are a separate, explicitly unconfirmed record.
        # Only a trusted current speaker can supply a new observation. Historical
        # evidence may explain it, but cannot silently retarget or grant identity.
        if not isinstance(subject, str):
            raise ValueError("subject须为账号键或current。")
        inherited = root.meta.get('subject', '') if root is not None else ''
        if inherited and not subject:
            subject = inherited
        if subject:
            if turn_for is None:
                raise PermissionError("人物观察缺少可信当前会话，未保存。")
            turn = turn_for()
            authors = {m.author for m in turn.messages if m.trusted and m.origin == 'external'}
            if subject == 'current':
                if len(authors) != 1:
                    raise PermissionError("本轮有多个作者，请选择真实发言者账号。")
                subject = next(iter(authors))
            if subject not in authors or turn.scope != scope.current:
                raise PermissionError("人物观察只能关联当前会话真实发言者，不按昵称或跨平台猜测。")
            if root is not None and inherited != subject:
                raise PermissionError("不能更换原观察的作者或将一般经验变成人物身份依据。")
            evidence = [m for m in turn.messages if m.author == subject and
                        m.trusted and m.origin == 'external' and observation in m.text]
            if not evidence:
                raise ValueError("人物观察须在observation逐字引用该作者本轮原话；推测放interpretation。")
            # Preserve exact source and timestamp; never synthesize a confirmed name.
            sources = [dict(message_id=m.message_id, author=m.author, text=m.text,
                            recorded_at=m.recorded_at) for m in evidence]
            for old in reversed(store.range(RangeQuery(source="memory", origin="internal", limit=128))):
                if (_visible(scope, old) and old.meta.get('scope') == scope.current and
                    old.meta.get('subject') == subject and old.meta.get('assessment') == assessment and
                    old.meta.get('experience') == experience and old.text == f"观察：{observation}\n解释：{interpretation}"):
                    return f"人物观察已有相同记录 event:{old.cursor}；未重复写入，不代表画像已确认。"
        else:
            sources = []
        if assessment != "pending" and not events and not sources:
            raise ValueError("支持、反驳、更正或放弃需要可读取的依据事件。")
        written = store.append({
            "type": _KIND, "source": "memory", "origin": "internal",
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "text": f"观察：{observation}\n解释：{interpretation}",
            "meta": {"scope": scope.current, "experience": experience, "assessment": assessment,
                     "events": list(dict.fromkeys(events)),
                     **({"subject": subject, "status": "unconfirmed", "sources": sources} if subject else {})},
        })
        if not written:
            raise RuntimeError("经验记录未保存。")
        return f"已保存经验 {experience or written.cursor}，记录 event:{written.cursor}（{assessment}）。" + ("人物观察仍为未确认，不改变画像或显示称呼；明确澄清后可另用profile_add接受正常审核。" if subject else "")

    def read(experience=None, query="", subject=""):
        scope = scope_for()
        scope.require()
        store = store_for()
        rows = [row for row in store.range(RangeQuery(source="memory", origin="internal")) if _visible(scope, row)]
        if experience is not None:
            root = store.get(experience)
            if not _visible(scope, root) or root.meta.get("experience") is not None:
                raise PermissionError("当前可见经验中没有这一条。")
            rows = [row for row in rows if row.cursor == experience or row.meta.get("experience") == experience]
        if subject:
            rows = [row for row in rows if row.meta.get("subject") == subject]
        if query.strip():
            query = query.strip().casefold()
            rows = [row for row in rows if query in row.text.casefold()]
        shown = rows[-20:][::-1]
        if not shown:
            return "当前范围没有匹配的经验记录。"
        lines = []
        for row in shown:
            refs = "、".join(f"event:{cursor}" for cursor in row.meta["events"]
                            if (target := store.get(cursor)) is not None and scope.event(target))
            lines.append(f"经验 {row.meta['experience'] or row.cursor}｜event:{row.cursor}｜"
                         f"{row.ts}｜{row.meta['assessment']}\n{row.text}"
                         + (f"\n人物待核实观察 · 账号 {row.meta['subject']} · 非已确认画像；后续支持判断也不自动升格。" if row.meta.get("subject") else "")
                         + (f"\n依据事件 {refs}" if refs else ""))
        return "\n\n".join(lines) + (f"\n（另有 {len(rows)-20} 条，可用关键词缩小范围。）" if len(rows)>20 else "")

    return {"experience_note": note, "experience_read": read}
