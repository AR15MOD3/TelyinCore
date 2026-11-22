""" T4 真实运行入口 v2（批③补修）—— 只跑一批；本轮**不再发请求**。

 v2 修：逐版本装配声明（并在请求边界核对只差 T4）；冻结**实际适配参数**与系统提示；
 预算按**传输尝试**逐次前扣、超预算立即停整批并独立分类；工具调用**有界**（线程 ＋ 超时）；
 四桶互斥（运行失败优先）；逐事件账（调用 ／ 回执 ／ 用量）失败也不丢。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import threading
import time
from contextlib import asynccontextmanager


@asynccontextmanager
async def _owned_probe_resources(raw, events, *, owns_transport):
    """Close only this probe's transport; preserve primary and every cleanup failure."""
    try:
        yield
    finally:
        primary = sys.exception()
        failures = []
        if owns_transport:
            try:
                await raw.aclose()
            except BaseException as error:
                error.probe_transport = raw
                failures.append(error)
        try:
            events.close()
        except BaseException as error:
            error.probe_events = events
            failures.append(error)
        if failures:
            chosen = primary if primary is not None else failures[0]
            chosen.probe_cleanup_errors = tuple(failures)
            if primary is None:
                raise chosen

CORE = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(CORE), str(CORE / "tools"), str(CORE / "telyincore")]

import launch                                               # noqa: E402
import t4_compare as base                                   # noqa: E402
from telyincore import engine as _engine                    # noqa: E402
from telyincore import generate as _generate                # noqa: E402
from telyincore import memory                               # noqa: E402
from telyincore import protocol as _protocol                # noqa: E402
from telyincore.config import Config                        # noqa: E402
from telyincore.dispatch import Dispatcher                  # noqa: E402
from telyincore.transport import HttpxTransport             # noqa: E402
from telyincore.tools import build_table                   # noqa: E402

READ_ALLOW = ("kb_search", "kb_read")
MAX_RUNS, MAX_ATTEMPTS, MAX_TOOL_CALLS = 20, 60, 40
ROUNDS_PER_ITEM = 3
ITEM_TIMEOUT_S, ROUND_TIMEOUT_S = 180.0, 60.0
MAX_TIMEOUTS = 3

SYSTEM = ("你是一个在隔离环境里接受评测的助手。你可以用给你的只读工具查资料；"
          "是否调用、查什么由你自己决定。回答时只说你确实知道的事。")


class BudgetExceeded(RuntimeError):
    def __init__(self, what: str) -> None:
        super().__init__("budget:" + what)
        self.what = what


MAX_ITEM_ATTEMPTS = 3


class Budget:
    def __init__(self) -> None:
        self.runs = self.attempts = self.tools = self.outer = 0
        self.per_item: dict[tuple[str, str], int] = {}

    def run(self) -> None:
        if self.runs >= MAX_RUNS:
            raise BudgetExceeded("runs")
        self.runs += 1

    def attempt(self, item: str = "", variant: str = "") -> None:
        if self.attempts >= MAX_ATTEMPTS:
            raise BudgetExceeded("attempts")
        key = (item, variant)
        used = self.per_item.get(key, 0)
        if used >= MAX_ITEM_ATTEMPTS:                 # ★ 重试也算这个 item 的账
            raise BudgetExceeded("item-attempts")
        self.attempts += 1
        self.per_item[key] = used + 1

    def tool(self) -> None:
        if self.tools >= MAX_TOOL_CALLS:
            raise BudgetExceeded("tools")
        self.tools += 1


class CountedTransport:
    """ 每次 `stream_chat` 尝试**先扣账**（重试也因此逐次计数）。 """

    def __init__(self, inner, budget: Budget, events=None) -> None:
        self._inner, self._budget = inner, budget
        self.bodies: list = []
        self.ok = self.failed = 0
        self.events = events
        self.ctx = {"item": "", "variant": "", "round": 0}
        self.n = 0
        self.attempts: list[dict] = []

    def bind(self, item: str, variant: str, round_no: int) -> None:
        self.ctx = {"item": item, "variant": variant, "round": round_no}

    def _say(self, body: dict) -> None:
        if self.events is None:
            return
        self.events.write(json.dumps(body, ensure_ascii=False) + "\n")
        self.events.flush()

    async def stream_chat(self, *a, **k):
        self._budget.attempt(self.ctx["item"], self.ctx["variant"])   # 全局 ＋ 本版本
        self.n += 1
        one = {"attempt_id": "%s-%s-r%s-a%s" % (self.ctx["item"] or "?", self.ctx["variant"] or "?",
                                                self.ctx["round"], self.n),
               "item": self.ctx["item"], "variant": self.ctx["variant"],
               "round": self.ctx["round"]}
        one["state"] = "open"
        self.attempts.append(one)
        self._say({"type": "attempt-start", **one})
        self.bodies.append(dict(k.get("body") or (a[1] if len(a) > 1 else {})))
        try:
            async for chunk in self._inner.stream_chat(*a, **k):
                yield chunk
        except GeneratorExit:
            # ⚠️ 正式生成链**收到终态后会主动关闭**这个异步生成器 ⇒ 这不是失败，
            #    也**不是终态**：结果由 `settle()` 归结（否则一次尝试两个终态）。
            one["state"] = "closed"
            raise
        except BaseException as exc:
            one["state"] = "failed"
            self.failed += 1
            self._say({"type": "attempt-failed", **one, "why": type(exc).__name__})
            raise
        else:
            # ⚠️ 正常读完**不出终态事件**：终态由 `settle()` 按正式调用结果给出
            #    （否则一次尝试会有 closed ＋ ok 两个终态）。
            one["state"] = "closed"

    def settle(self, *, ok: bool, why: str = "") -> None:
        """ **由正式调用结果归结**最后一次尝试；一次尝试只有一个终态。 """
        for one in reversed(self.attempts):
            if one.get("state") in ("open", "closed"):
                one["state"] = "ok" if ok else "failed"
                if ok:
                    self.ok += 1
                else:
                    self.failed += 1
                self._say({"type": "attempt-ok" if ok else "attempt-failed",
                           **one, "why": why})
                return

    async def aclose(self):
        return await self._inner.aclose()


def _profile(root) -> dict:
    raw = json.loads((pathlib.Path(root) / "engine.json").read_text(encoding="utf-8"))
    prof = dict(raw["profiles"][raw["current"]])
    return {"name": raw["current"], **{k: prof.get(k) for k in
            ("kind", "model", "max_tokens", "thinking", "temperature",
             "reasoning_effort", "base_url")}}


def _decls_for(variant: str):
    candidate, baseline = base.the_declarations()
    keep = candidate if variant == "candidate" else baseline
    return [one for one in keep if one.name in READ_ALLOW]


def the_two_read_variants():
    """ 两版（只读）—— 在建表之前核一次"恰好只差第③支"。 """
    candidate, baseline = base.the_declarations()
    cand = [one for one in candidate if one.name in READ_ALLOW]
    basev = [one for one in baseline if one.name in READ_ALLOW]
    base.diff_is_only_t4(cand, basev)
    return cand, basev


def _cfg(profile: dict, decls) -> Config:
    return Config(base_url=str(profile.get("base_url") or ""),
                  model=str(profile.get("model") or ""),
                  max_tokens=int(profile.get("max_tokens") or 0),
                  thinking=bool(profile.get("thinking")),
                  tools=tuple({"type": "function", "name": o.name,
                               "description": o.description,
                               "parameters": dict(o.parameters)} for o in decls))


def _freeze(out: pathlib.Path, *, profile, plan, corpus, decls_by_variant) -> dict:
    frozen = {
        "backend": {k: profile.get(k) for k in
                    ("name", "kind", "model", "max_tokens", "thinking",
                     "temperature", "reasoning_effort")},
        "adapted_values": {"thinking": bool(profile.get("thinking")),
                           "temperature": profile.get("temperature"),
                           "reasoning_effort": profile.get("reasoning_effort")},
        "limits": {"max_runs": MAX_RUNS, "max_attempts": MAX_ATTEMPTS,
                   "max_tool_calls": MAX_TOOL_CALLS, "rounds_per_item": ROUNDS_PER_ITEM,
                   "item_timeout_s": ITEM_TIMEOUT_S, "round_timeout_s": ROUND_TIMEOUT_S,
                   "max_timeouts": MAX_TIMEOUTS},
        "system": SYSTEM,
        "questions": [dict(one) for one in base.QUESTIONS],
        "declarations": {k: [{"name": o.name, "description": o.description,
                              "parameters": o.parameters} for o in v]
                         for k, v in decls_by_variant.items()},
        "corpus_hashes": corpus["hashes"], "plan": plan,
        "impl_hashes": {
            "scoring": base.hashlib.sha256(
                pathlib.Path(base.__file__).read_bytes()).hexdigest()[:16],
            "entry": base.hashlib.sha256(
                pathlib.Path(__file__).read_bytes()).hexdigest()[:16],
        },
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (out / "frozen.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    return frozen


class Late:
    """ 一次执行的登记 —— **登记不等于迟到**。 """

    def __init__(self, thread: threading.Thread) -> None:
        self.thread = thread
        self.timed_out = False       # 只有在超时那一刻还活着才置真
        self.drained = False


async def _dispatch_bounded(table, call, seconds: float, registry: list):
    """ 同步 Dispatcher 放进线程：**不阻塞事件循环**；句柄**启动即登记**（取消也不丢）。 """
    box = {}

    def work():
        try:
            box["got"] = Dispatcher(table).run([call])
        except BaseException as exc:          # noqa: BLE001
            box["err"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    late = Late(thread)
    registry.append(late)          # ★ 在第一次 await **之前**登记
    await asyncio.to_thread(thread.join, seconds)
    if thread.is_alive():
        late.timed_out = True
        return None, late
    if "err" in box:
        raise box["err"]
    return box["got"], None


async def _drain(late: Late, seconds: float = 5.0) -> bool:
    """ 排空（在**线程池**里等，不阻塞事件循环）。返回是否已结束。 """
    await asyncio.to_thread(late.thread.join, seconds)
    late.drained = not late.thread.is_alive()
    return late.drained


def _messages(item) -> list[dict]:
    said = [{"role": "system", "content": SYSTEM}]
    if item.get("context") and "无前文" not in item["context"]:
        said.append({"role": "user", "content": item["context"]})
    said.append({"role": "user", "content": item["question"]})
    return said


async def _one(item, table, cfg, transport, counter, budget, events, row: dict,
               late_box: list) -> dict:
    """ 跑一个 item —— **行在外层创建**，这里只往下填；异常也不丢已消耗的计数。 """
    messages = _messages(item)
    for _round in range(ROUNDS_PER_ITEM):
        budget.outer += 1
        row["models"] += 1          # **先记**：调用失败了也已经发生
        counter.bind(item["item_id"], item["variant"], _round + 1)
        try:
            got = await _generate.generate(cfg, messages, transport)
        except BaseException as exc:
            counter.settle(ok=False, why=type(exc).__name__)
            raise
        counter.settle(ok=True)
        row["usage"] = getattr(got, "usage", None) or "unknown"
        calls = list(getattr(got, "calls", ()) or ())
        if not calls:
            row["answer"] = str(getattr(got, "text", "") or "")
            break
        for call in calls:
            budget.tool()
            row["tools"] += 1
            events.write(json.dumps({"type": "tool-start", "item": item["item_id"],
                                     "variant": item["variant"],
                                     "name": str(getattr(call, "name", "")),
                                     "arguments": str(getattr(call, "arguments", ""))},
                                    ensure_ascii=False) + "\n")
            events.flush()
            got_one, late = await _dispatch_bounded(table, call, ROUND_TIMEOUT_S,
                                                    late_box)
            if late is not None:
                events.write(json.dumps({"type": "tool-timeout", "item": item["item_id"],
                                         "variant": item["variant"]},
                                        ensure_ascii=False) + "\n")
                events.flush()
                raise TimeoutError("tool-timeout")
            text = "\n".join(str(getattr(one, "text", "")) for one in (got_one.receipts or ()))
            events.write(json.dumps({"type": "tool-ok", "item": item["item_id"],
                                     "variant": item["variant"], "receipt": text},
                                    ensure_ascii=False) + "\n")
            events.flush()
            row["events"] += 1
            messages.append({"type": "function_call", "call_id": str(getattr(call, "call_id", "")),
                             "name": str(getattr(call, "name", "")),
                             "arguments": str(getattr(call, "arguments", ""))})
            messages.append({"type": "function_call_output",
                             "call_id": str(getattr(call, "call_id", "")), "output": text})
    else:
        row["failure"] = "round-limit"
    return row


async def run_live(*, home, out, profile_root, transport=None):
    out = pathlib.Path(out)
    if out.exists():
        raise FileExistsError("产物目录已存在，拒绝覆盖：" + str(out))
    out.mkdir(parents=True)
    corpus = base.make_corpus(home)
    profile = _profile(profile_root)
    plan = base.the_plan()
    the_two_read_variants()
    decls = {name: _decls_for(name) for name in ("baseline", "candidate")}
    _freeze(out, profile=profile, plan=plan, corpus=corpus, decls_by_variant=decls)
    handlers = {k: v for k, v in memory.handlers(
        home, clock=lambda: ("20261019-010000", "2026-10-19")).items()
        if k in READ_ALLOW}
    tables = {name: build_table(decls[name], handlers) for name in decls}
    cfgs = {name: _cfg(profile, decls[name]) for name in decls}
    eng = _engine.Engine(pathlib.Path(profile_root))
    # ★ **统一装配**：底层来源（真／假）→ CountedTransport → **正式 adapt**。
    events = (out / "events.jsonl").open("w", encoding="utf-8", newline="")
    budget = Budget()
    owns_transport = transport is None
    try:
        raw = transport if transport is not None else HttpxTransport(
            headers_of=launch.the_key_headers(eng))
    except BaseException as primary:
        try:
            events.close()
        except BaseException as cleanup:
            primary.probe_cleanup_errors = (cleanup,)
            primary.probe_events = events
        raise
    async with _owned_probe_resources(raw, events, owns_transport=owns_transport):
        counted = CountedTransport(raw, budget, events)
        transport = _protocol.adapt(
            counted,
            kind_of=lambda: str(profile.get("kind")),
            values_of=lambda: {"thinking": bool(profile.get("thinking")),
                               "temperature": profile.get("temperature"),
                               "reasoning_effort": profile.get("reasoning_effort")})
        items = {one["item_id"]: one for one in base._items()}
        rows, timeouts, aborted = [], 0, False
        with (out / "runs.jsonl").open("w", encoding="utf-8", newline="") as log:
            for step in plan:
                item = dict(items[step["item_id"]], variant=step["variant"])
                row = {"item_id": item["item_id"], "variant": step["variant"],
                       "kind": item["kind"], "answer_kind": item["answer_kind"],
                       "target": item["target"], "question": item["question"],
                       "answer": "", "models": 0, "tools": 0, "usage": "unknown",
                       "failure": "", "events": 0}
                late_box: list = []
                primary = None
                try:
                    budget.run()
                    await asyncio.wait_for(
                        _one(item, tables[step["variant"]], cfgs[step["variant"]],
                             transport, counted, budget, events, row, late_box),
                        timeout=ITEM_TIMEOUT_S)
                except asyncio.TimeoutError:
                    timeouts += 1
                    row["failure"] = "timeout"          # 句柄在 late_box 里，下面统一排空
                except BudgetExceeded as why:
                    row["failure"] = "budget:" + why.what
                    # 单 item 的超额**只废这一题**；全批预算用完才停整批。
                    aborted = why.what in ("attempts", "tools", "runs")
                except BaseException as exc:                  # noqa: BLE001
                    row["failure"] = base.classify(exc)
                    if not isinstance(exc, Exception):
                        primary = exc
                        primary.probe_workers = late_box
                        aborted = True
                for one in late_box:                    # 取消窗口：此刻还活着就是迟到
                    one.timed_out = one.timed_out or one.thread.is_alive()
                row["registered"] = len(late_box)
                row["late"] = any(one.timed_out for one in late_box)
                if row["late"]:
                    ended = True
                    deadline = time.monotonic() + 5.0
                    for late in late_box:
                        if late.timed_out:
                            try:
                                ended = await _drain(late, max(0.0, deadline - time.monotonic())) and ended
                            except BaseException as cleanup:
                                if primary is None:
                                    primary = cleanup
                                else:
                                    primary.probe_drain_errors = (*getattr(primary, 'probe_drain_errors', ()), cleanup)
                                primary.probe_workers = late_box
                                ended = False
                                break
                    row["late_drained"] = ended
                    if not ended:
                        row["failure"] = "late-still-running"
                        aborted = True
                if not row["failure"]:
                    graded = base.score_answer(row["answer"], items[item["item_id"]], corpus)
                    row["verdict"], row["why"] = graded["verdict"], graded["why"]
                else:
                    row["verdict"], row["why"] = "", ""
                rows.append(row)
                try:
                    log.write(json.dumps(row, ensure_ascii=False) + "\n")
                    log.flush()
                except BaseException as report_error:
                    if primary is None:
                        raise
                    primary.probe_report_error = report_error
                if primary is not None:
                    primary.probe_row = row
                    raise primary
                if row["failure"] == "timeout" and timeouts >= MAX_TIMEOUTS:
                    aborted = True
                if aborted:
                    break
    
    # 四桶**互斥**：运行失败优先
    buckets = {"pass": 0, "fail": 0, "manual": 0, "run_failed": 0}
    for one in rows:
        if one.get("failure"):
            buckets["run_failed"] += 1
        else:
            buckets[one.get("verdict") or "manual"] += 1
    stats = {"runs": len(rows), "outer_calls": budget.outer,
             "transport_attempts": budget.attempts,
             "attempts_ok": counted.ok, "attempts_failed": counted.failed,
             "attempts": counted.attempts,
             "tool_calls": budget.tools,
             "aborted": aborted, "buckets": buckets,
             "by_variant": {n: {"runs": sum(1 for r in rows if r["variant"] == n),
                                "pass": sum(1 for r in rows
                                            if r["variant"] == n and r.get("verdict") == "pass")}
                            for n in ("baseline", "candidate")}}
    (out / "result.json").write_text(json.dumps({"stats": stats, "rows": rows},
                                                ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    return {"dir": str(out), "stats": stats}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--profile-root", required=True)
    args = ap.parse_args(argv)
    if not args.live:
        raise SystemExit("真实入口只认 --live；替身请用 tools/t4_compare.py --drill")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = CORE / "data" / "instances" / ("t4-live-" + stamp)
    got = asyncio.run(run_live(home=out / "home", out=out,
                               profile_root=pathlib.Path(args.profile_root)))
    print(json.dumps(got, ensure_ascii=False))
    return base.result_exit_code(got['stats'])


if __name__ == "__main__":
    raise SystemExit(main())
