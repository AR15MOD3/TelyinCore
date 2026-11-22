# telyincore/markers.py
"""模型上下文的结构标记。

“未执行”使用冻结常量；“执行但失败”通过 tool_failed 生成并带专用前缀，
两族必须可区分。每个标记的消费者在 _CONSUMERS 中登记；未实现的消费者明确标注。
移植部分的来源及许可见仓库 THIRD_PARTY_NOTICES 文件。"""

# ── 折叠（`truncate.ts` 那一族）─────────────────────────
FOLD_PLACEHOLDER = "[result folded]"
FOLD_ARGS_PLACEHOLDER = "[arguments folded]"
OVERSIZE_PLACEHOLDER = "[turn too long, omitted]"

# ── 配对修复 ────────────────────────────────────────────
MISSING_RESULT = "[result missing]"
MISSING_RESULT_RESTART = "[result missing (process restart)]"
PENDING_IN_MAIN_THREAD = "[result pending in the main thread]"

# ── 压根没办 ────────────────────────────────────────────
UNKNOWN_TOOL = "[unknown tool]"
NOT_EXECUTED_INCOMPLETE = "[not executed: function call incomplete]"
NOT_EXECUTED_BARRIER = "[not executed: review the preceding tool result first]"
NOT_EXECUTED_STREAM_ABORTED = "[not executed: stream aborted mid-response]"
NOT_EXECUTED_LOOP_STOPPED = "[not executed: main loop stopped]"
SHUTDOWN_INTERRUPTED = "[tool result unavailable: shutdown interrupted the round]"


# ── 办了但办砸了 ────────────────────────────────────────
def tool_failed(detail: str) -> str:
    """造一条"办了、但办砸了"的回执 —— **这一族的手工制造口就是它**。

    ⚠️ 前缀 `[tool failed]` 是**承重的**：它让这一族与"压根没办"那一族
       （`NOT_EXECUTED_*` ／ `SHUTDOWN_INTERRUPTED` ／ `MISSING_RESULT*`）
       **永远不重合** —— 不论 `detail` 里塞什么，甚至把"没办"的记号本身塞进来。
    """
    return f"[tool failed] {detail}"


TOOL_FAILED_BAD_ARGS = tool_failed("arguments are not valid JSON")

#: **每个记号点名它的消费者** —— 而**今天没有消费者的要明说**（`loop.py:60-63` 那条家规）。
#: ⚠️ 这张表**由 `tests/test_markers.py::test_every_marker_names_who_owes_it` 钉着** ⇒
#:    加一个记号而不填这里 = 当场红。**函数也算记号**：那条测试收的是
#:    "公开常量（大写）∪ 本模块定义的公开函数" ⇒ `tool_failed` 也在被钉之列
#:    （只按 `isupper()` 收的话**看不见它** —— 派前扫描 #1 正是这条）。
_CONSUMERS: dict[str, str] = {
    "FOLD_PLACEHOLDER": "等裁剪那一层（**今天没有**）",
    "FOLD_ARGS_PLACEHOLDER": "等裁剪那一层（**今天没有**）",
    "OVERSIZE_PLACEHOLDER": "等裁剪那一层（**今天没有**）",
    "MISSING_RESULT": "配对修复（`dispatch.py` 的配对账）",
    "MISSING_RESULT_RESTART": "配对修复 —— **重启那一路（今天没有）**",
    "PENDING_IN_MAIN_THREAD": 'fork —— **本仓不做**',
    "UNKNOWN_TOOL": "`dispatch.py` 第 2 步",
    "NOT_EXECUTED_INCOMPLETE": "`dispatch.py`（半截的调用）",
    "NOT_EXECUTED_BARRIER": "`dispatch.py` 第 3 步",
    "NOT_EXECUTED_STREAM_ABORTED": "`dispatch.py`（流断了）",
    "NOT_EXECUTED_LOOP_STOPPED": "`dispatch.py`（主循环停了）",
    "SHUTDOWN_INTERRUPTED": "`dispatch.py` 的关机收尾（**那就是 ⑤**）",
    "TOOL_FAILED_BAD_ARGS": "`dispatch.py` 第 1 步",
    "tool_failed": "`dispatch.py`（handler 抛异常那一路；`TOOL_FAILED_BAD_ARGS` 也由它造）",
}
