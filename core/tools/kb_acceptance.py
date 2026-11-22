"""知识库真实模型隔离验收（2026-10-18，一次性批次）。

 固定计划：4 题 × 2 遍 × 2 后端 = 16 次运行；API 先、本地后、串行。
 预算：每后端 ≤24 次传输尝试（每次运行 ≤3）；工具 ≤4/次运行（全批 64）；单次 180s；全批 40min。
 隔离：全合成知识库、独立会话；只给 kb_search／kb_read；不读正式画像；凭据只进进程内请求头。
 证据：frozen.json（首请求前）、runs.jsonl（逐运行）、attempts 账、最终请求体（去头）、回执、正文、usage。
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import threading
import re
import dataclasses
import hashlib
import json
import pathlib
import sys
import time

CORE = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(CORE), str(CORE / "telyincore")]

import launch                                                    # noqa: E402
import t4_compare as base                                        # noqa: E402
from telyincore import engine as _engine                          # noqa: E402
from telyincore import generate as _generate                      # noqa: E402
from telyincore import memory, protocol                           # noqa: E402
from telyincore.config import Config                              # noqa: E402
from telyincore.dispatch import Dispatcher                        # noqa: E402
from telyincore.stream import ToolCall                            # noqa: E402
from telyincore.tools import DECLARED_TOOLS, build_table          # noqa: E402
from telyincore.transport import (
    HttpxTransport, LLMError, TransportError,
)                   # noqa: E402

TOOLS = ("kb_search", "kb_read")
QUESTIONS = (
    {"qid": "q1", "kind": "lookup", "text": "查一下「成语」那本里的第 3 条是什么。", "target": "成语"},
    {"qid": "q2", "kind": "list", "text": "你收着的《词典》里，念三条词条给我听。", "target": "词典"},
    {"qid": "q3", "kind": "books", "text": "你都有哪些本子？", "target": None},
    {"qid": "n1", "kind": "negative", "text": "你那本《诗经》收了哪些？", "target": None},
)
REPEATS = 2
MAX_RUNS = 16
ATTEMPTS_PER_BACKEND = 24
TOOLS_TOTAL = 64
ATTEMPTS_PER_RUN = 3
TOOLS_PER_RUN = 4
RUN_TIMEOUT = 180.0
BATCH_TIMEOUT = 2400.0


class BudgetStop(RuntimeError):
    """Budget exhaustion is a control result, never a timeout."""

    def __init__(self, kind):
        super().__init__("budget:" + kind)


class Budget:
    def __init__(self):
        self.runs = self.tools = self.outer_calls = self.timeouts = 0
        self.attempts = {"deepseek": 0, "local": 0}
        self.started = time.monotonic()

    def remaining(self):
        return BATCH_TIMEOUT - (time.monotonic() - self.started)

    def run(self):
        if self.remaining() <= 0:
            raise BudgetStop("time")
        if self.runs >= MAX_RUNS:
            raise BudgetStop("runs")
        self.runs += 1

    def attempt(self, backend, row):
        if self.remaining() <= 0:
            raise BudgetStop("time")
        if row["attempts"] >= ATTEMPTS_PER_RUN:
            raise BudgetStop("run-attempts")
        if self.attempts[backend] >= ATTEMPTS_PER_BACKEND:
            raise BudgetStop("backend-attempts")
        self.attempts[backend] += 1
        row["attempts"] += 1

    def tool(self, row):
        if self.remaining() <= 0:
            raise BudgetStop("time")
        if row["tools"] >= TOOLS_PER_RUN:
            raise BudgetStop("run-tools")
        if self.tools >= TOOLS_TOTAL:
            raise BudgetStop("tools")
        self.tools += 1
        row["tools"] += 1


def make_corpus(home: pathlib.Path) -> dict:
    (home / "知识").mkdir(parents=True, exist_ok=True)
    hashes, by = {}, {}
    for name in ("成语", "词典", "术语", "俗语"):
        rows = [f"{name}-条目{i:03d}：{name}合成释义。" for i in range(1, 21)]
        body = "\n".join(rows) + "\n"
        (home / "知识" / f"{name}.md").write_bytes(body.encode("utf-8"))
        hashes[name] = hashlib.sha256(body.encode()).hexdigest()[:16]
        by[name] = [f"{name}-条目{i:03d}" for i in range(1, 21)]
    return {"hashes": hashes, "by": by}


def the_decls():
    keep = [one for one in DECLARED_TOOLS if one.name in TOOLS]
    assert {one.name for one in keep} == set(TOOLS)
    return keep


def the_plan(backends=("deepseek", "local")):
    order = [(q["qid"], rep, backend)
             for backend in backends
             for q in QUESTIONS for rep in (1, 2)]
    return [{"qid": q, "repeat": r, "backend": b} for q, r, b in order]


SCORING_VERSION = "2026-10-18/bare-number-allowed"


NUMBER = re.compile(r"第?\s*(\d{1,2})\s*条")


def _bare_numbers(said: str) -> list[int]:
    return [int(one) for one in NUMBER.findall(said)]


def score(item: dict, answer: str, corpus: dict) -> dict:
    said = (answer or "").strip()
    if not said:
        return {"verdict": "manual", "why": "空答案（不算正确拒答）"}
    entries = [m.group(0) for m in base.ENTRY.finditer(said)]
    target = item.get("target")
    allowed = set(corpus["by"].get(target, [])) if target else set()
    if item["kind"] == "negative":
        if entries:
            return {"verdict": "fail", "why": "负对照仍在念条目"}
        if "诗经" in said and any(one in said for one in ("没有", "没有这一本", "查无此书")):
            return {"verdict": "pass", "why": ""}
        return {"verdict": "manual", "why": "没有明确否认《诗经》"}
    if target:
        if not entries:
            if item["kind"] == "lookup":
                nums = _bare_numbers(said)
                if nums == [3] and "诗经" not in said:
                    return {"verdict": "pass", "why": "裸编号唯一"}
                if any(one in said for one in ("诗经", "俗语", "术语")) and "成语" not in said:
                    return {"verdict": "fail", "why": "库外内容"}
            return {"verdict": "manual", "why": "没有念出可核对条目"}
        bad = [one for one in entries if one not in corpus["by"][target]]
        if bad:
            return {"verdict": "fail", "why": "念了别的本子或库外条目"}
        if item["kind"] == "lookup":
            if "成语-条目003" in entries:
                return {"verdict": "pass", "why": ""}
            # 裸编号可接受：唯一 + 题目允许按编号回答 + 无库外内容
            nums = _bare_numbers(said)
            if nums == [3] and not bad and target == "成语":
                return {"verdict": "pass", "why": "裸编号唯一"}
            return {"verdict": "fail", "why": "没有给到第 3 条"}
        return {"verdict": "pass" if len(entries) >= 1 else "fail", "why": ""}
    books = [one for one in ("成语", "词典", "术语", "俗语") if one in said]
    if entries:
        return {"verdict": "fail", "why": "书目题念了条目"}
    if len(books) >= 4:
        return {"verdict": "pass", "why": ""}
    return {"verdict": "manual", "why": "书目不全（只提到 %d 本）" % len(books)}


MAX_TIMEOUTS = 3
DRAIN_TIMEOUT = 5.0
CLEANUP_TIMEOUT = 5.0
CLEANUP_RESERVE_FRACTION = 0.1
WIRE_FIELDS = frozenset({
    "model", "messages", "input", "tools", "stream", "max_tokens", "max_output_tokens",
    "temperature", "top_p", "stop", "reasoning", "thinking", "chat_template_kwargs",
    "tool_choice", "parallel_tool_calls", "stream_options", "system",
})


def new_row(item, backend):
    return {"qid": item["qid"], "repeat": item["repeat"], "backend": backend,
            "text": item["text"], "answer": "", "receipts": [], "requests": [],
            "usage": "unknown", "usages": [], "usage_complete": False,
            "runs": 0, "outer_calls": 0, "attempts": 0, "tools": 0,
            "attempt_events": [], "tool_events": [], "rejected_calls": [],
            "rejected_attempts": [], "failure": "", "verdict": "", "why": "",
            "stop_batch": False, "stop_reason": "", "late": False,
            "registered": 0, "cleanup_errors": [], "finalized": False}


def fail(row, why):
    if not row["failure"]:
        row["failure"] = why


class Counter:
    def __init__(self, inner, budget: Budget, backend: str):
        self.inner, self.budget, self.backend = inner, budget, backend
        self.attempts, self.bodies, self.workers = [], [], []
        self.row = new_row({"qid": "transport-only", "repeat": 0, "text": ""}, backend)
        self.closed = False
        self.pending_requests = []
        self.late_cleanup_errors = []

    async def stream_chat(self, *a, **k):
        if self.closed:
            raise RuntimeError("transport already closed")
        row = self.row
        if row is None:
            raise RuntimeError("attempt has no run owner")
        try:
            self.budget.attempt(self.backend, row)
        except BudgetStop as exc:
            row["rejected_attempts"].append({"round": row["outer_calls"], "why": str(exc)})
            raise
        body = k.get("body") or (a[1] if len(a) > 1 else {})
        wire = copy.deepcopy({key: value for key, value in body.items() if key in WIRE_FIELDS})
        event = {"attempt_id": row["attempts"], "round": row["outer_calls"],
                 "state": "open", "usage": "unknown",
                 "omitted_fields": sorted(set(body) - WIRE_FIELDS)}
        self.attempts.append(event)
        self.bodies.append(wire)
        row["attempt_events"].append(event)
        row["requests"].append(wire)
        upstream = self.inner.stream_chat(*a, **k)
        try:
            async for chunk in upstream:
                if self.closed:
                    raise RuntimeError("late response after run closed")
                yield chunk
        except GeneratorExit:
            if event["state"] in ("open", "closed"):
                event["state"] = "closed"
            raise
        except BaseException as exc:
            if event["state"] in ("open", "closed"):
                event.update(state="failed", error_type=type(exc).__name__)
            raise
        else:
            if event["state"] in ("open", "closed"):
                event["state"] = "closed"
        finally:
            try:
                await upstream.aclose()
            except Exception as exc:
                detail = {"where": "stream-close", "type": type(exc).__name__}
                if row["finalized"]:
                    self.late_cleanup_errors.append(detail)
                else:
                    row["cleanup_errors"].append(detail)
                    row["stop_batch"] = True

    def settle(self, ok, usage=None):
        for event in reversed(self.attempts):
            if event["state"] in ("open", "closed"):
                event["state"] = "ok" if ok else "failed"
                event["usage"] = copy.deepcopy(usage) if usage is not None else "unknown"
                return

    async def aclose(self):
        if not self.closed:
            self.closed = True
            await self.inner.aclose()


class Worker:
    def __init__(self, event):
        self.event = event
        self.box = {}
        self.done = asyncio.Event()
        self.thread = None
        self.collected = False


def collect_worker(worker, row):
    if worker.collected or not ({"error", "result"} & worker.box.keys()):
        return
    worker.collected = True
    event = worker.event
    if "error" in worker.box:
        event.update(state="failed", error_type=type(worker.box["error"]).__name__)
        return
    result = worker.box["result"]
    receipt = "\n".join(str(getattr(r, "text", "")) for r in (result.receipts or ()))
    event["state"] = "ok"
    event["receipt"] = receipt
    row["receipts"].append({"call_id": event["call_id"], "name": event["name"],
                             "arguments": event["arguments"], "receipt": receipt})


async def wait_worker_exit(worker, deadline):
    """A result notification is not proof of exit; poll within the shared drain budget.

    Do not join on the event loop or spawn an unbounded helper thread. A false
    snapshot at the deadline remains conservative even if exit follows immediately.
    """
    while worker.thread.is_alive():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.005, remaining))
    return True


async def dispatch_tool(table, call, counter):
    row = counter.row
    event = {"call_id": str(call.call_id), "name": str(call.name),
             "arguments": str(call.arguments), "state": "started"}
    worker = Worker(event)
    loop = asyncio.get_running_loop()

    def work():
        try:
            worker.box["result"] = Dispatcher(table).run([call])
        except BaseException as exc:
            worker.box["error"] = exc
        finally:
            try:
                loop.call_soon_threadsafe(worker.done.set)
            except RuntimeError:
                pass  # A late daemon is recorded, never reported as killed.

    worker.thread = threading.Thread(target=work, daemon=True)
    counter.workers.append(worker)  # Registered before the first await.
    row["tool_events"].append(event)
    row["registered"] += 1
    try:
        worker.thread.start()
    except BaseException as exc:
        worker.box["error"] = exc
        collect_worker(worker, row)
        raise
    await worker.done.wait()
    collect_worker(worker, row)
    if "error" in worker.box:
        error = worker.box["error"]
        if isinstance(error, Exception):
            raise error
        raise RuntimeError("tool aborted: " + type(error).__name__)
    return event["receipt"]


async def _run_one(item, table, cfg, transport, counter, budget, backend):
    row = counter.row
    messages = [{"role": "user", "content": item["text"]}]
    for _round in range(3):
        row["outer_calls"] += 1
        budget.outer_calls += 1
        try:
            got = await _generate.generate(cfg, messages, transport)
        except BaseException:
            if not row["finalized"]:
                row["usages"].append("unknown")
                counter.settle(False)
            raise
        if row["failure"] or counter.closed:
            raise RuntimeError("late result cannot dispatch or change the completed row")
        usage = getattr(got, "usage", None)
        row["usages"].append(copy.deepcopy(usage) if usage is not None else "unknown")
        if usage is not None:
            row["usage"] = copy.deepcopy(usage)
        counter.settle(True, usage)
        calls = list(getattr(got, "calls", ()) or ())
        if not calls:
            row["answer"] = str(getattr(got, "text", "") or "")
            return row
        for index, call in enumerate(calls):
            try:
                budget.tool(row)
            except BudgetStop as exc:
                row["rejected_calls"].extend({
                    "call_id": str(c.call_id), "name": str(c.name),
                    "arguments": str(c.arguments), "why": str(exc)
                } for c in calls[index:])
                raise
            receipt = await dispatch_tool(table, call, counter)
            messages += [{"type": "function_call", "call_id": str(call.call_id),
                          "name": str(call.name), "arguments": str(call.arguments)},
                         {"type": "function_call_output", "call_id": str(call.call_id),
                          "output": receipt}]
    fail(row, "round-limit")
    return row


async def run_one(item, table, cfg, transport, counter, budget, backend, *, row=None):
    row = new_row(item, backend) if row is None else row
    counter.row = row
    task = None
    wall_left = max(0.0, min(RUN_TIMEOUT, budget.remaining()))
    wall_deadline = time.monotonic() + wall_left
    batch_limited = budget.remaining() < RUN_TIMEOUT
    reserve = min(DRAIN_TIMEOUT + CLEANUP_TIMEOUT, wall_left * CLEANUP_RESERVE_FRACTION)
    execution_left = max(0.0, wall_left - reserve)
    with protocol.replay_scope(counter):
        try:
            budget.run()
            row["runs"] = 1
            task = asyncio.create_task(_run_one(item, table, cfg, transport,
                                                counter, budget, backend))
            counter.pending_requests.append(task)
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            done, _ = await asyncio.wait({task}, timeout=execution_left)
            if not done:
                task.cancel()
                if batch_limited or budget.remaining() <= 0:
                    raise BudgetStop("time")
                raise TimeoutError("run deadline")
            await task
        except asyncio.CancelledError:
            fail(row, "cancelled")
            row.update(stop_batch=True, stop_reason="cancelled")
        except BudgetStop as exc:
            fail(row, str(exc))
            row.update(stop_batch=True, stop_reason=str(exc))
        except TimeoutError:
            if budget.remaining() <= 0:
                fail(row, "budget:time")
                row.update(stop_batch=True, stop_reason="budget:time")
            else:
                fail(row, "timeout")
                budget.timeouts += 1
                if budget.timeouts >= MAX_TIMEOUTS:
                    row.update(stop_batch=True, stop_reason="timeout-limit")
        except LLMError as exc:
            fail(row, "http-" + str(exc.status))
            row["error_status"] = exc.status
            if exc.status == 400 and "reasoning_content" in str(exc):
                row.update(stop_batch=True, stop_reason="reasoning_content-400")
        except Exception as exc:
            fail(row, "transport" if isinstance(exc, TransportError) else "run-error")
            row["error_type"] = type(exc).__name__
        finally:
            if task is not None and not task.done():
                task.cancel()
                done, _ = await asyncio.wait(
                    {task}, timeout=max(0.0, min(
                        CLEANUP_TIMEOUT, wall_deadline - time.monotonic())))
                if not done:
                    row.update(stop_batch=True, stop_reason="request-still-running")
            if task is not None and task.done() and not task.cancelled():
                task.exception()  # Retrieve, never override the already recorded primary error.
            deadline = min(wall_deadline, time.monotonic() + DRAIN_TIMEOUT)
            for worker in counter.workers:
                if worker.thread.is_alive():
                    row["late"] = True
                finished = await wait_worker_exit(worker, deadline)
                collect_worker(worker, row)
                worker.event["finished_after_timeout"] = finished
                if not finished:
                    row.update(stop_batch=True, stop_reason="late-still-running")
            close_task = asyncio.create_task(counter.aclose())
            closed, _ = await asyncio.wait(
                {close_task}, timeout=max(0.0, min(
                    CLEANUP_TIMEOUT, wall_deadline - time.monotonic())))
            if not closed:
                close_task.cancel()
                row["cleanup_errors"].append({"where": "transport", "type": "close-timeout"})
            else:
                try:
                    await close_task
                except Exception as exc:
                    row["cleanup_errors"].append({"where": "transport",
                                                   "type": type(exc).__name__})
            if row["cleanup_errors"]:
                fail(row, "cleanup-error")
                row["stop_batch"] = True
                row["stop_reason"] = row["stop_reason"] or "cleanup-error"
            if row["stop_batch"] and not row["failure"]:
                fail(row, row["stop_reason"])
            counter.settle(False)
    row["usage_complete"] = (bool(row["usages"]) and
                             all(isinstance(u, dict) for u in row["usages"]) and
                             all(a["usage"] != "unknown" for a in counter.attempts))
    row["finalized"] = True
    return row


async def execute_plan(plan, factory, table, corpus, out, budget):
    rows = []
    with (out / "runs.jsonl").open("w", encoding="utf-8", newline="") as log:
        for step in plan:
            item = dict(next(q for q in QUESTIONS if q["qid"] == step["qid"]),
                        repeat=step["repeat"])
            backend = step["backend"]
            row = new_row(item, backend)
            phase = "factory"
            try:
                cfg, transport, counter = factory(step)
                phase = "run-wrapper"
                row = await run_one(item, table, cfg, transport, counter,
                                    budget, backend, row=row)
            except Exception as exc:
                fail(row, phase + "-error")
                row.update(stop_batch=True, stop_reason=phase + "-error",
                           error_type=type(exc).__name__)
            if row["failure"]:
                row["verdict"], row["why"] = "run_failed", row["failure"]
            else:
                row.update(score(item, row["answer"], corpus))
            rows.append(row)
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
            if row["stop_batch"]:
                (out / "STOP.txt").write_bytes((row["stop_reason"] + "\n").encode("utf-8"))
                break
    buckets = {name: 0 for name in ("pass", "fail", "manual", "run_failed")}
    for row in rows:
        buckets[row["verdict"]] += 1
    stats = {"runs": budget.runs, "rows": len(rows), "outer_calls": budget.outer_calls,
             "attempts": budget.attempts, "tools": budget.tools, "buckets": buckets,
             "timeouts": budget.timeouts, "aborted": bool(rows and rows[-1]["stop_batch"]),
             "by_backend": {b: sum(r["backend"] == b for r in rows)
                            for b in ("deepseek", "local")}}
    assert sum(r["runs"] for r in rows) == budget.runs
    assert sum(r["outer_calls"] for r in rows) == budget.outer_calls
    assert sum(r["tools"] for r in rows) == budget.tools
    for backend in budget.attempts:
        counted = sum(r["attempts"] for r in rows if r["backend"] == backend)
        assert counted == budget.attempts[backend]
    (out / "result.json").write_bytes(json.dumps({"stats": stats, "rows": rows},
                                               ensure_ascii=False, indent=1).encode("utf-8"))
    return stats


async def main(out: pathlib.Path, backends=("deepseek", "local")):
    out.mkdir(parents=True)
    home = out / "home"
    corpus = make_corpus(home)
    decls = the_decls()
    handlers = {k: v for k, v in memory.handlers(
        home, clock=lambda: ("20261018-230000", "2026-10-18")).items() if k in TOOLS}
    table = build_table(decls, handlers)
    plan = the_plan(backends)
    raw = json.loads((CORE / "data" / "engine.json").read_text(encoding="utf-8"))
    profs = raw["profiles"]

    frozen = {"plan": plan, "questions": [dict(q) for q in QUESTIONS],
              "corpus": corpus["hashes"], "tools": [o.name for o in decls],
              "decls": [{"name": o.name, "parameters": o.parameters} for o in decls],
              "limits": {"runs": MAX_RUNS, "attempts_per_backend": ATTEMPTS_PER_BACKEND,
                         "tools_total": TOOLS_TOTAL, "max_output_tokens": 2048,
                         "run_timeout": RUN_TIMEOUT, "batch_timeout": BATCH_TIMEOUT,
                         "attempts_per_run": ATTEMPTS_PER_RUN,
                         "tools_per_run": TOOLS_PER_RUN,
                         "max_timeouts": MAX_TIMEOUTS,
                         "drain_timeout": DRAIN_TIMEOUT,
                         "cleanup_timeout": CLEANUP_TIMEOUT,
                         "cleanup_reserve_fraction": CLEANUP_RESERVE_FRACTION},
              "backends": {b: {k: profs["DeepSeek" if b == "deepseek" else "Local"].get(k)
                               for k in ("kind", "model", "thinking", "max_tokens")}
                           for b in ("deepseek", "local")}}
    (out / "frozen.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=1),
                                     encoding="utf-8")

    def factory(step):
        backend = step["backend"]
        p = profs["DeepSeek" if backend == "deepseek" else "Local"]
        cfg = Config(base_url=str(p.get("base_url")), model=str(p.get("model")),
                     max_tokens=2048, thinking=bool(p.get("thinking")),
                     tools=tuple({"type": "function", "name": o.name,
                                  "description": o.description,
                                  "parameters": dict(o.parameters)} for o in decls))
        eng = _engine.Engine(CORE / "data")
        want = "DeepSeek" if backend == "deepseek" else "Local"
        headers_of = (launch.the_key_headers(eng) if raw["current"] == want
                      else (lambda: {}))
        counter = Counter(HttpxTransport(headers_of=headers_of), budget, backend)
        transport = protocol.adapt(counter,
            kind_of=lambda kind=p.get("kind"): str(kind),
            values_of=lambda th=bool(p.get("thinking")): {"thinking": th})
        return cfg, transport, counter

    budget = Budget()
    stats = await execute_plan(plan, factory, table, corpus, out, budget)
    print(json.dumps(stats, ensure_ascii=False))
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--backends", default="deepseek,local")
    args = ap.parse_args()
    chosen = tuple(one.strip() for one in args.backends.split(",") if one.strip())
    result = asyncio.run(main(pathlib.Path(args.out), chosen))
    if (result["aborted"] or any(result["buckets"].get(key, 0)
            for key in ('run_failed', 'fail', 'manual'))
            or not result["buckets"].get('pass')):
        raise SystemExit(1)
