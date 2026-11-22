"""Render trusted internal wake context without fabricating a new user message."""
import copy
import json
from .prompts import MEMORY_SCOPE_CONTRACT


def internal_tick_only(batch):
    from .continuation import is_continuation
    return bool(batch) and all(
        is_continuation(row) or (
        getattr(row, "origin", None) == "internal"
        and getattr(row, "type", None) == "tick"
        and getattr(row, "source", None) == "heartbeat") for row in batch)


def system_head(messages, *, continuation=False):
    """Some local templates only admit system text at the head of a request.

    Request-local projection for internal ticks only. Persisted roles and
    histories are unchanged; no synthetic user or model tool call is created.
    """
    systems, other = [], []
    for message in messages:
        if message.get("role") != "system":
            other.append(copy.deepcopy(message))
            continue
        content = message.get("content", "")
        if not isinstance(content, str):
            if (not isinstance(content, list) or any(
                    not isinstance(part, dict) or part.get("type") not in ("input_text", "text")
                    or not isinstance(part.get("text"), str) for part in content)):
                raise ValueError("internal_system_projection_requires_text")
            content = "\n".join(part["text"] for part in content)
        systems.append(content)
    systems.append(
        "当前请求由宿主内部续接事件触发，不是用户新来话。"
        "可根据此次事件与交接背景自主判断是否继续未完成的话题或工作；不要重复已完成的答复。"
        "无需发言时可用finish_turn结束，若没有该工具可保持空响应。"
        if continuation else
        "当前请求由内部心跳触发，不是用户新来话。历史中的提问不能据此补答。"
        "可以整理自己的状态；没有新增待办时用可用的finish_turn结束，"
        "若没有该工具则可保持空响应；不必重新回应历史中的告别或问题。")
    if MEMORY_SCOPE_CONTRACT not in systems:
        systems.append(MEMORY_SCOPE_CONTRACT)
    return [{"role": "system", "content": "\n\n".join(systems)}] + other


class WakeProjection:
    """Archive pre-wake dialogue as data, retaining live tool continuation roles.

    One instance per wake batch. Nothing is rewritten in the session.
    A changed prefix fails closed instead of guessing where current work starts.
    """

    def __init__(self, events=()):
        self.prefix = None
        self.events = copy.deepcopy(list(events))

    def __call__(self, messages):
        continuation = any(event.get('type') == 'continuation' for event in self.events)
        head = system_head(messages, continuation=continuation)
        other = head[1:]
        if self.prefix is None:
            self.prefix = copy.deepcopy(other)
        size = len(self.prefix)
        if other[:size] != self.prefix:
            raise ValueError("internal_wake_history_prefix_changed")
        history = self.prefix
        if history:
            guidance = ("保留它用于查阅背景，不要重复已完成的答复；未完成事项由你判断是否继续。\n"
                        if continuation else "保留它用于查阅背景，不要重新回复其中的告别或问题。\n")
            archive = ("以下JSON仅是唤醒前已发生的历史记录，不是当前来话或待执行指令。"
                       + guidance + json.dumps({"historical_messages": history}, ensure_ascii=False))
            head[0]["content"] += "\n\n" + archive
        current = []
        if self.events:
            head[0]["content"] += ("\n协议user槽中的HOST_INTERNAL_WAKE是宿主内部事件，"
                                   "不是人类消息；不得据此更新联系人的好感度。")
            # Local endpoints may require a query slot. Serialize the real event,
            # explicitly NOT a human speaker; never persist a synthetic user row.
            current = [{"role": "user", "content": "HOST_INTERNAL_WAKE（非人类来话）\n"
                        + json.dumps({"origin": "internal", "events": self.events},
                                     ensure_ascii=False)}]
        # Calls/results generated after the wake keep real wire roles and IDs.
        return [head[0]] + current + copy.deepcopy(other[size:])


_SCOPE_REQUIRED_TOOLS = frozenset({
    "memory_search", "kb_search", "kb_read", "skill_read", "pending_read", "read_events",
    # These production writers also require a trusted conversation scope.
    "knowledge_note", "skill_note", "skill_merge",
    # Reminder scheduling and inspection belong to a concrete conversation.
    # A self-observation heartbeat has neither a recipient nor their authority.
    "set_timer", "timer_read", "timer_cancel", "timer_history", "timer_resolve",
})


def stable_declaration_order(tools):
    """Put shared declarations before scoped tools omitted from unscoped wakes.

    This only orders already-authorized declarations. Filtering, handlers and
    schemas remain unchanged; preserve relative order inside both groups.
    """
    rows = list(tools)
    return ([tool for tool in rows if tool.get("name") not in _SCOPE_REQUIRED_TOOLS]
            + [tool for tool in rows if tool.get("name") in _SCOPE_REQUIRED_TOOLS])


class _WakeModelConfig:
    def __init__(self, config, tools):
        self._config = config
        self.tools = tools

    def __getattr__(self, name):
        return getattr(self._config, name)


def model_config_for_wake(config, batch):
    """Omit unavailable scoped readers and writers for an internal wake."""
    from .session_view import batch_scope

    if not internal_tick_only(batch) or batch_scope(batch):
        return config
    tools = getattr(config, "tools", ())
    selected = [tool for tool in tools if tool.get("name") not in _SCOPE_REQUIRED_TOOLS]
    # A wake-specific schema advertises only sections the same runtime can
    # expand. Copy only this declaration; never mutate the shared Config.
    from .memory_navigation import ROUTES
    names = {tool.get('name') for tool in selected}
    for index, tool in enumerate(selected):
        if tool.get('name') == 'memory_navigate':
            projected = copy.deepcopy(tool)
            section = projected.get('parameters', {}).get('properties', {}).get('section')
            if isinstance(section, dict) and isinstance(section.get('enum'), list):
                sections = {'root'} | {key for key, _, reader in ROUTES if reader in names}
                section['enum'] = [key for key in section['enum'] if key in sections]
            selected[index] = projected
    return _WakeModelConfig(config, tuple(selected))
