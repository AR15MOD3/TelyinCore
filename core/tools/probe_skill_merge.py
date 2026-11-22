# tools/probe_skill_merge.py
""" **做法合并的真实调用链**（2026-10-17 批③ ⑩）—— 真模型、真派发、副本。

 ⚠️ 它验的是：她**通过现有声明与派发路径**读到版本，并**明确发出** `skill_merge`；
    然后回执 ／ 落盘 ／ 有效数 ／ 检索结果对得上。
 ⚠️ 用的是**验收专用提示**（明说"把这两条并成一条"）—— 那**只证明链路**，
    不能据此说"生产提示下她会自己想到合并"。
 ⚠️ 不碰真实家：`bots/<谁>/` 抄到 `data/instances/probe-skill-merge-<时间戳>/` 再跑。
 ⚠️ 引擎不可用 ⇒ 照实说"待验"，不写"通过"。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import shutil
import sys
import time

_CORE = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_CORE), str(_CORE / "telyincore")]

import launch                                              # noqa: E402
from telyincore import memory, search                      # noqa: E402
from telyincore.dispatch import Dispatcher, make_run_tools  # noqa: E402
from telyincore.engine import Engine                       # noqa: E402
from telyincore.generate import generate                   # noqa: E402
from telyincore.protocol import adapt                      # noqa: E402
from telyincore.tools import DECLARED_TOOLS, build_table   # noqa: E402
from telyincore.transport import HttpxTransport            # noqa: E402

TASK = (
    "你手上有两条做法：一条叫「别人发图时」，一条叫「有人发表情包时」。"
    "它们其实是同一件事 —— 请把它们并成一条：留下「别人发图时」，"
    "把「有人发表情包时」并进来，正文你自己写成一条更完整的。"
    "操作 ID 用 op-probe-1。先读它们的版本，再调 skill_merge。"
)


def _the_copy() -> pathlib.Path:
    root = _CORE / "data" / "instances" / ("probe-skill-merge-" + time.strftime("%Y%m%d-%H%M%S"))
    root.mkdir(parents=True)
    shutil.copytree(_CORE.parent / "bots" / "Telyin", root / "bots" / "Telyin")
    return root


async def main() -> int:
    engine = Engine(_CORE / "data")
    cfg = engine.live()
    try:
        name = engine.current_name() or "?"
    except Exception:                       # noqa: BLE001
        name = "?"
    print("引擎档案：" + str(name) + " ／ kind=" + str(getattr(cfg, "kind", "?")), flush=True)

    home = _the_copy() / "bots" / "Telyin" / "memory"
    tree = home / "做法"
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "别人发图时.md").write_bytes("别人发图，别急着描述；等对方问。".encode("utf-8"))
    (tree / "有人发表情包时.md").write_bytes("表情包不用猜是什么。".encode("utf-8"))

    handlers = dict(memory.handlers(home, clock=lambda: ("20261018-050000", "2026-10-18")))
    handlers.update(memory.skill_merge_handler(home, stamp="20261018-050000"))
    table = build_table([*DECLARED_TOOLS, launch._SKILL_MERGE_TOOL], handlers)
    print("表里有 skill_merge：", "skill_merge" in table, flush=True)

    transport = adapt(HttpxTransport(headers_of=launch.the_key_headers(engine)),
                      kind_of=lambda: cfg.kind, values_of=lambda: cfg.values())
    config = launch._DeclaresTools(cfg, launch._the_sendable_declarations(
        [*DECLARED_TOOLS, launch._SKILL_MERGE_TOOL], handlers))
    run_tools = make_run_tools(Dispatcher(table))

    rows = memory.skills_history(home)
    print("先前的条目：", [(one["id"], one["title"]) for one in rows], flush=True)
    messages = [
        {"role": "system", "content": "你是这份人格。工具照实调；没有工具能办的事就说办不了。"},
        {"role": "user", "content": TASK},
    ]

    # 最多三轮：她可能先 `skill_read` 读版本，再 `skill_merge`（这正是我们要看的链）。
    calls_seen: list[str] = []
    for turn in (1, 2, 3):
        got = await generate(config, messages, transport)
        said = getattr(got, "text", "") or ""
        calls = getattr(got, "calls", ()) or ()
        print("\n第 " + str(turn) + " 轮文字：" + said[:200], flush=True)
        if calls:
            print("它要调：", [getattr(one, "name", "?") for one in calls], flush=True)
        if not calls:
            break
        calls_seen.extend(str(getattr(one, "name", "?")) for one in calls)
        result = await run_tools(calls)
        receipts = list(getattr(result, "receipts", ()) or ())
        for one in receipts:
            print("  [回执] " + str(getattr(one, "text", one))[:220], flush=True)
        if "skill_merge" in calls_seen:
            break
        # 把这一轮接上，让它接着来（回执进历史）
        for one in calls:
            messages.append({"type": "function_call",
                             "call_id": str(getattr(one, "call_id", "")),
                             "name": str(getattr(one, "name", "")),
                             "arguments": str(getattr(one, "arguments", ""))})
        for one in receipts:
            messages.append({"type": "function_call_output",
                             "call_id": str(getattr(one, "call_id", "")),
                             "output": str(getattr(one, "text", ""))})
    print("\n它这一趟调过的工具：", calls_seen, flush=True)

    after = memory.skills_history(home)
    effective = memory.skills_of(home)
    print("\n合并后全貌：", [(one["id"], one["state"], one["merged_into"]) for one in after], flush=True)
    print("有效条目数：", len(effective), flush=True)
    base = home.parent.parent / "search"
    search.put_all(base, [(n, b) for n, b in effective])
    print("索引里的键：", sorted(search.the_keys(base)), flush=True)

    merged = [one for one in after if one["state"] == "merged"]
    ok = bool(merged) and len(effective) == 1 and all(
        one["title"] + ".md" in search.the_keys(base) for one in after if one["state"] == "active")
    print("\nPASS" if ok else "\n待验／未覆盖：模型这一趟没有发出合并动作")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
