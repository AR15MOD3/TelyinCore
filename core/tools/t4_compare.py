""" T4 隔离对照工具（批②v4）—— **只跑替身，不发真实请求**。

 v4 补丁（针对第二轮独立复核）：
  · 排空用**真正超时那个句柄**；排空不完 ⇒ 记 `finished_after_timeout=false` 并**停止后续运行**；
  · 冻结：**完整规范化快照**（题目／声明含 schema／配置／评分实现哈希／顺序）参与校验；
  · 计分：**不用字符集合剥离**，改用**受限回答语法**；否定／不确定／无法解析 ⇒ 至少 manual；
  · 书目答案**只从回执里**取书名，不补齐；
  · `max_rounds` 在**派发前**检查并真实计数（`rounds`）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
import re
import sys
import threading
import time

CORE = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(CORE), str(CORE / "telyincore")]

from telyincore import memory                                     # noqa: E402
from telyincore.dispatch import Dispatcher                         # noqa: E402
from telyincore.stream import ToolCall                             # noqa: E402
from telyincore.tools import DECLARED_TOOLS, ToolTag, build_table  # noqa: E402

T4_TEXT = ("③ **你自己收着些什么**（那棵树里都有哪些 —— "
           "被问到「你有没有…」「那一本里收了哪些」就来查，别凭印象答）。")

DICTS = ("成语", "词典", "术语", "俗语")
ENTRIES_PER_DICT = 125
REPEATS = 2
SEED = 20261018

QUESTIONS = (
    {"qid": "q1", "kind": "positive", "target": "成语", "answer_kind": "entries",
     "context": "（无前文）", "rule": "只允许 成语-条目NNN，用顿号／分号／“和”连接；别的字一律不过",
     "text": "你那本成语词典收了哪些词？"},
    {"qid": "q2", "kind": "positive", "target": "成语", "answer_kind": "entries",
     "context": "前文：上一句你说「我记着几条成语」—— 「几个」指成语那本。",
     "rule": "同 q1（前文把“几个”绑定到成语那本）",
     "text": "挑几个念给我听听。"},
    {"qid": "q3", "kind": "positive", "target": None, "answer_kind": "books",
     "context": "（无前文）", "rule": "只允许书名（成语／词典／术语／俗语）；出现条目 ⇒ 混淆",
     "text": "你都有哪些词典？"},
    {"qid": "q4", "kind": "positive", "target": "成语", "answer_kind": "entries",
     "context": "（无前文）", "rule": "同 q1", "text": "你收着的那些本子里，有哪些成语？"},
    {"qid": "n1", "kind": "negative", "target": None, "answer_kind": "denial",
     "context": "（无前文）", "rule": "只允许完整否认句（没有这一本 ／ 没有这本 ／ 查无此书）；别的一律不过",
     "text": "你那本《诗经》收了哪些？"},
)

ENTRY = re.compile(r"([\u4e00-\u9fa5]{2,6})-条目(\d{3})")
ANY_ENTRY = re.compile(r"[\u4e00-\u9fa5]{2,6}-条目\d{3}")
DENIAL_OK = {"没有这一本", "没有这本", "查无此书", "并未收录", "没有"}
#: **否定／不确定**的标记：出现就不许自动通过（最多 manual）。
NEG_MARK = re.compile(r"不在|不是|并不|不包含|没有|不知道|不确定|记不清|说不准|大概|也许")
#: 受限语法：只由**条目**或**书名**组成（允许顿号／分号／逗号／和／与／空格／句号结尾）。
CONN = r"[、，,；;和与\s]"
ENTRY_TAIL = r"[、，,；;和与\s]*[。！!\s]*"
ENTRIES_SEQ = re.compile(r"^" + CONN + r"*(?:[\u4e00-\u9fa5]{2,6}-条目\d{3}(?:"
                         + CONN + r"+[\u4e00-\u9fa5]{2,6}-条目\d{3})*)"
                         + ENTRY_TAIL + r"$")
BOOKS_SEQ = re.compile(r"^" + CONN + r"*(?:成语|词典|术语|俗语)(?:" + CONN
                       + r"+(?:成语|词典|术语|俗语))*" + ENTRY_TAIL + r"$")


def make_corpus(home: pathlib.Path) -> dict:
    (home / "知识").mkdir(parents=True, exist_ok=True)
    white: dict[str, list[str]] = {}
    hashes = {}
    for one in DICTS:
        lines, names = [], []
        for n in range(1, ENTRIES_PER_DICT + 1):
            entry = f"{one}-条目{n:03d}"
            names.append(entry)
            lines.append(f"M{DICTS.index(one)+1:02d}｜{entry}：合成语料里的释义。")
        body = "\n".join(lines) + "\n"
        (home / "知识" / (one + ".md")).write_bytes(body.encode("utf-8"))
        white[one] = names
        hashes[one] = hashlib.sha256(body.encode()).hexdigest()[:16]
    flat = [entry for names in white.values() for entry in names]
    return {"by_dict": white, "entries": flat, "count": len(flat), "hashes": hashes}


def the_declarations():
    import dataclasses
    keep = [one for one in DECLARED_TOOLS if ToolTag.READ in one.tags]
    candidate = list(keep)
    baseline = []
    for one in keep:
        if one.name == "kb_search":
            if T4_TEXT not in one.description:
                raise SystemExit("现状声明里没有第③支那一段")
            baseline.append(dataclasses.replace(
                one, description=one.description.replace(T4_TEXT, "", 1)))
        else:
            baseline.append(one)
    return candidate, baseline


def _decl_snapshot(rows) -> list[dict]:
    return [{"name": o.name, "description": o.description,
             "parameters": o.parameters, "tags": [str(t) for t in o.tags],
             "effect": str(o.effect)} for o in rows]


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _digest(obj) -> str:
    return hashlib.sha256(_canon(obj).encode()).hexdigest()[:16]


def diff_is_only_t4(candidate, baseline) -> None:
    assert len(candidate) == len(baseline)
    for a, b in zip(candidate, baseline):
        assert a.name == b.name
        assert a.parameters == b.parameters, f"{a.name} 参数不一样"
        assert a.tags == b.tags and a.effect == b.effect, f"{a.name} 标签不一样"
        if a.name != "kb_search":
            assert a.description == b.description
    a = next(one for one in candidate if one.name == "kb_search")
    b = next(one for one in baseline if one.name == "kb_search")
    assert b.description == a.description.replace(T4_TEXT, "", 1)


def _items():
    return [{"item_id": f"{one['qid']}-r{rep}", **one, "question": one["text"],
             "repeat": rep} for one in QUESTIONS for rep in range(1, REPEATS + 1)]


def the_plan():
    items = _items()
    order = [(item["item_id"], variant)
             for variant in ("baseline", "candidate") for item in items]
    random.Random(SEED).shuffle(order)
    return [{"item_id": iid, "variant": variant} for iid, variant in order]


# ── 计分：受限语法 ───────────────────────────────────────────────────────
def score_answer(answer: str, item: dict, corpus: dict) -> dict:
    said = str(answer or "").strip()
    allowed = set(corpus["entries"])
    target = item.get("target")
    allowed_here = set(corpus["by_dict"].get(target, [])) if target else set()
    entries = [m.group(0) for m in ENTRY.finditer(said)]
    unknown = [one for one in entries if one not in allowed]
    wrong = [one for one in entries if one in allowed and one not in allowed_here]
    kind = item.get("answer_kind", "entries")

    if unknown:
        return {"verdict": "fail", "why": "念了库里没有的", "entries": entries}
    if kind == "denial":
        if entries:
            return {"verdict": "fail", "why": "负对照仍念了条目", "entries": entries}
        core = re.sub(r"[。！!\s]+$", "", said)
        if core in DENIAL_OK:
            return {"verdict": "pass", "why": "", "entries": []}
        if NEG_MARK.search(said) or "不" in said:
            return {"verdict": "manual", "why": "否认里夹了别的内容，判不准", "entries": []}
        return {"verdict": "manual", "why": "不是完整的否认句", "entries": []}

    if kind == "books":
        if entries:
            return {"verdict": "fail", "why": "书目问题却念了条目（混淆）", "entries": entries}
        if BOOKS_SEQ.match(said):
            return {"verdict": "pass", "why": "", "entries": []}
        if NEG_MARK.search(said):
            return {"verdict": "manual", "why": "带否定／不确定，判不准", "entries": []}
        return {"verdict": "manual", "why": "不是完整的书名清单", "entries": []}

    if wrong:
        return {"verdict": "fail", "why": "答错了词典", "entries": entries}
    if NEG_MARK.search(said) or "不" in said:
        return {"verdict": "manual", "why": "带否定／不确定，判不准", "entries": entries}
    if entries and ENTRIES_SEQ.match(said) and all(one in allowed_here for one in entries):
        return {"verdict": "pass", "why": "", "entries": entries}
    if entries:
        return {"verdict": "manual", "why": "条目之外还有内容，判不准", "entries": entries}
    return {"verdict": "manual", "why": "没念出可核对的条目", "entries": []}


# ── 隔离配置 → 正式适配 ─────────────────────────────────────────────────
#: **冻结配置的白名单** —— 只这几格进产物；`secret` ／ `auth` 头 ／ `base_url` **一律不入档**。
#: ⚠️ 连"凭证值的哈希"也不存（那仍然是凭证的派生数据）。
PROFILE_FIELDS = ("kind", "model", "max_tokens", "thinking",
                  "temperature", "reasoning_effort")


def the_profile(root) -> dict:
    raw = json.loads((pathlib.Path(root) / "engine.json").read_text(encoding="utf-8"))
    return dict(raw["profiles"][raw["current"]])


def safe_profile(profile) -> dict:
    """ 只留白名单那几格；**未知字段不自动入档**（要加就改这个元组，并在判据里写清）。 """
    return {key: profile.get(key) for key in PROFILE_FIELDS if key in profile}


def shaped_request(*, root, variant, kind=None):
    from telyincore import generate as _generate        # noqa: PLC0415
    from telyincore import protocol as _protocol        # noqa: PLC0415
    from telyincore.config import Config                # noqa: PLC0415

    profile = the_profile(root)
    decl = the_declarations()[0 if variant == "candidate" else 1]
    cfg = Config(base_url=str(profile.get("base_url", "")),
                 model=str(profile.get("model", "")),
                 max_tokens=int(profile.get("max_tokens", 0)),
                 thinking=bool(profile.get("thinking", False)),
                 tools=tuple({"type": "function", "name": one.name,
                              "description": one.description,
                              "parameters": dict(one.parameters)} for one in decl))
    body = _generate.build_body(cfg, [{"role": "user", "content": "（离线核对）"}])
    got_kind = kind or str(profile.get("kind") or "chat")
    adapted = _protocol.shape(
        got_kind, body, base_url=str(profile.get("base_url", "")),
        thinking=bool(profile.get("thinking", False)),
        temperature=profile.get("temperature"),
        reasoning_effort=profile.get("reasoning_effort"))
    reason = adapted.get("reasoning") or adapted.get("reasoning_effort")
    if isinstance(reason, dict):
        reason = reason.get("effort")
    names = []
    for one in adapted.get("tools") or ():
        if isinstance(one, dict):
            names.append(one.get("name") or (one.get("function") or {}).get("name"))
    return adapted, {"kind": got_kind, "model": cfg.model,
                     "max_output_tokens": body.get("max_output_tokens"),
                     "temperature": adapted.get("temperature"), "reasoning": reason,
                     "thinking": adapted.get("chat_template_kwargs", {}).get("enable_thinking"),
                     "tools": [one for one in names if one]}


# ── 替身：只从回执取事实 ────────────────────────────────────────────────
def stand_in(item, *, receipt, variant):
    text = str(receipt or "")
    entries = [m.group(0) for m in ENTRY.finditer(text)]
    kind = item.get("answer_kind")
    if kind == "denial":
        return {"answer": "没有这一本。" if not entries else "；".join(entries), "usage": 0}
    if kind == "books":
        # ⚠️ 书名**只能**来自回执里真出现的那些，不补齐。
        seen = [one for one in DICTS if one in text]
        return {"answer": "、".join(seen), "usage": 0}
    if not entries:
        return {"answer": "", "usage": 0}
    return {"answer": "；".join(entries[:3]), "usage": 0}


# ── 有界：迟到标记 ＋ 真正排空 ──────────────────────────────────────────
class Late:
    def __init__(self):
        self.alive = False
        self.thread = None


def run_bounded(fn, seconds: float):
    box, late = {}, Late()

    def work():
        try:
            box["got"] = fn()
        except BaseException as exc:                  # noqa: BLE001
            box["err"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        late.alive, late.thread = True, thread
        return None, late
    if "err" in box:
        raise box["err"]
    return box.get("got"), late


def drain(late: Late, seconds: float = 5.0) -> bool:
    """ 等它结束（**不假装取消**）。返回"现在是否已结束"。 """
    if late is None or late.thread is None:
        return True
    if late.alive:
        late.thread.join(seconds)
    return not late.thread.is_alive()


def classify(exc: BaseException) -> str:
    name = type(exc).__name__
    if isinstance(exc, TimeoutError):
        return "timeout"
    if name in ("TransportError", "FirstResponseTimeout", "ContentIdleTimeout"):
        return "transport"
    status = getattr(exc, "status", None)
    if name == "LLMError" and isinstance(status, int):
        return "http-4xx" if 400 <= status < 500 else "http-5xx"
    return "other:" + name


class RoundLimit(RuntimeError):
    pass


class Budget:
    def __init__(self, max_runs):
        self.max_runs, self.runs = max_runs, 0

    def before_run(self):
        if self.runs >= self.max_runs:
            raise TimeoutError("预算用完：运行数到顶")
        self.runs += 1


# ── 冻结：完整快照 ＋ 校验 ──────────────────────────────────────────────
def frozen_of(*, plan, corpus, candidate, baseline, profile, scoring_hash, limits) -> dict:
    questions = [dict(one) for one in QUESTIONS]
    decl = {"candidate": _decl_snapshot(candidate), "baseline": _decl_snapshot(baseline)}
    safe = safe_profile(profile)
    return {
        "seed": SEED, "plan": plan, "questions": questions,
        "declarations": decl, "profile": safe, "limits": dict(limits),
        "corpus_hashes": dict(corpus["hashes"]),
        "scoring_src_hash": scoring_hash,
        "hashes": {
            "plan": _digest(plan), "questions": _digest(questions),
            "declarations": _digest(decl), "profile": _digest(safe),
            "limits": _digest(limits),
            "corpus": _digest(corpus["hashes"]), "scoring": scoring_hash,
        },
    }


def verify_frozen(frozen, *, candidate, baseline, corpus, plan, profile, scoring_hash,
                  limits):
    """ 全核：题目／声明（含 schema）／配置／评分实现／顺序／语料／**执行限制**。 """
    pairs = [
        ("plan", frozen["hashes"]["plan"], _digest(plan)),
        ("questions", frozen["hashes"]["questions"], _digest(frozen["questions"])),
        ("declarations", frozen["hashes"]["declarations"], _digest(frozen["declarations"])),
        ("profile", frozen["hashes"]["profile"], _digest(safe_profile(profile))),
        ("corpus", frozen["hashes"]["corpus"], _digest(frozen["corpus_hashes"])),
        ("limits", frozen["hashes"]["limits"], _digest(frozen["limits"])),
        ("scoring", frozen["hashes"]["scoring"], scoring_hash),
        # ★ 顶层的评分字段**也要**核（它曾是"只存不验"的那一格）
        ("scoring_src_hash", frozen.get("scoring_src_hash"), scoring_hash),
    ]
    for name, saved, now in pairs:
        if saved != now:
            raise AssertionError(f"冻结项被改过：{name}")
    # 保存内容 vs **实际执行对象**
    if _canon(frozen["questions"]) != _canon([dict(one) for one in QUESTIONS]):
        raise AssertionError("保存的题面 与 实际题目 不一致")
    if _canon(frozen["declarations"]["candidate"]) != _canon(_decl_snapshot(candidate)):
        raise AssertionError("保存的候选声明 与 实际声明 不一致")
    if _canon(frozen["declarations"]["baseline"]) != _canon(_decl_snapshot(baseline)):
        raise AssertionError("保存的基线声明 与 实际声明 不一致")
    if _canon(frozen["plan"]) != _canon(plan):
        raise AssertionError("保存的顺序 与实际顺序 不一致")
    if _canon(frozen["profile"]) != _canon(safe_profile(profile)):
        raise AssertionError("保存的配置 与 实际配置 不一致")
    if _canon(frozen["limits"]) != _canon(limits):
        raise AssertionError("保存的执行限制 与 本次实际限制 不一致")
    if _canon(frozen["corpus_hashes"]) != _canon(corpus["hashes"]):
        raise AssertionError("保存的语料哈希 与实际 不一致")


def run_drill(*, home, out, agent=None, timeout_s=2.0, max_rounds=1, max_runs=20,
              profile_root=None):
    out = pathlib.Path(out)
    if out.exists():
        raise FileExistsError("产物目录已存在，拒绝覆盖：" + str(out))
    out.mkdir(parents=True)
    corpus = make_corpus(home)
    candidate, baseline = the_declarations()
    diff_is_only_t4(candidate, baseline)
    plan = the_plan()
    items = {one["item_id"]: one for one in _items()}
    # ⚠️ **默认不读任何配置**（正式 `CORE/data` 也不读）；要读就显式给隔离的 root。
    profile = the_profile(profile_root) if profile_root else {}
    scoring_hash = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()[:16]

    limits = {"timeout_s": timeout_s, "max_rounds": max_rounds, "max_runs": max_runs}
    frozen = frozen_of(plan=plan, corpus=corpus, candidate=candidate, baseline=baseline,
                       profile=profile, scoring_hash=scoring_hash, limits=limits)
    (out / "frozen.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    verify_frozen(frozen, candidate=candidate, baseline=baseline, corpus=corpus,
                  plan=plan, profile=profile, scoring_hash=scoring_hash, limits=limits)

    all_handlers = memory.handlers(home, clock=lambda: ("20261018-233000", "2026-10-18"))
    handlers = {name: fn for name, fn in all_handlers.items()
                if name in {one.name for one in candidate}}
    budget = Budget(max_runs)
    rows, aborted = [], False
    with (out / "runs.jsonl").open("w", encoding="utf-8", newline="") as log:
        for step in plan:
            item = items[step["item_id"]]
            row = {"item_id": item["item_id"], "variant": step["variant"],
                   "kind": item["kind"], "answer_kind": item["answer_kind"],
                   "target": item["target"], "question": item["question"],
                   "call": None, "receipt": "", "answer": "", "verdict": "", "why": "",
                   "usage": 0, "failure": "", "late": False, "rounds": 0,
                   "finished_after_timeout": None}
            late_all: list[Late] = []
            try:
                budget.before_run()
                table = build_table(candidate if step["variant"] == "candidate" else baseline,
                                    handlers)
                need = 0 if item["answer_kind"] == "denial" else 1
                if need and max_rounds < need:
                    raise RoundLimit("rounds-exceeded")
                while row["rounds"] < need:
                    if row["rounds"] >= max_rounds:
                        raise RoundLimit("rounds-exceeded")
                    row["rounds"] += 1
                    call = ToolCall(call_id="c-" + item["item_id"], name="kb_search",
                                    arguments=json.dumps(
                                        {"query": ("M%02d" % (DICTS.index(item["target"]) + 1))
                                         if item["target"] else "M02",
                                         "k": 1, "lines": 6, "from_line": 3},
                                        ensure_ascii=False))
                    row["call"] = json.loads(call.arguments)
                    got, late = run_bounded(
                        lambda: Dispatcher(table).run([call]), timeout_s)
                    late_all.append(late)
                    if late.alive:
                        raise TimeoutError("检索超时（后台仍在跑）")
                    row["receipt"] = "\n".join(str(getattr(one, "text", ""))
                                               for one in (got.receipts or ()))
                chosen = agent if agent is not None else stand_in
                acted, late2 = run_bounded(
                    lambda: chosen(item, receipt=row["receipt"], variant=step["variant"]),
                    timeout_s)
                late_all.append(late2)
                if late2.alive:
                    raise TimeoutError("替身超时（后台仍在跑）")
                row["answer"] = acted["answer"]
                row["usage"] = acted.get("usage", 0)
                graded = score_answer(row["answer"], item, corpus)
                row["verdict"], row["why"] = graded["verdict"], graded["why"]
            except BaseException as exc:                    # noqa: BLE001
                row["failure"] = classify(exc)
                if isinstance(exc, TimeoutError) and budget.runs >= budget.max_runs:
                    aborted = True          # 预算用完 ⇒ 停止后续运行（不静默跑完）
            finally:
                if any(one.alive for one in late_all):
                    row["late"] = True
                    ended = all(drain(one, 5.0) for one in late_all)
                    row["finished_after_timeout"] = ended
                    if not ended:
                        row["failure"] = "late-still-running"
                        aborted = True
            rows.append(row)
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
            if aborted:
                break

    stats = {"runs": len(rows), "aborted": aborted,
             "by_variant": {n: {"runs": sum(1 for r in rows if r["variant"] == n),
                                "pass": sum(1 for r in rows
                                            if r["variant"] == n and r["verdict"] == "pass")}
                            for n in ("baseline", "candidate")},
             "verdicts": {v: sum(1 for r in rows if r["verdict"] == v)
                          for v in ("pass", "fail", "manual")},
             "failures": sorted({r["failure"] for r in rows if r["failure"]}),
             "late": sum(1 for r in rows if r["late"])}
    (out / "result.json").write_text(json.dumps({"stats": stats, "rows": rows},
                                                ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    return {"dir": str(out), "stats": stats}


def result_exit_code(stats: dict) -> int:
    """Zero means a completed, entirely passing measurement, not just a written report."""
    if not isinstance(stats, dict) or not stats.get('runs') or stats.get('aborted'):
        return 1
    if stats.get('failures') or stats.get('late'):
        return 1
    buckets = stats.get('buckets', stats.get('verdicts', {}))
    if not isinstance(buckets, dict) or buckets.get('pass') != stats['runs']:
        return 1
    if any(buckets.get(key, 0) for key in ('fail', 'manual', 'run_failed')):
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drill", action="store_true")
    ap.add_argument("--live", action="store_true")
    #: 隔离配置目录（含 `engine.json`）。**不给就不读配置** —— 正式 `CORE/data` 不碰。
    ap.add_argument("--profile-root", default=None)
    args = ap.parse_args(argv)
    if args.live:
        raise SystemExit("本批禁止真实请求：只允许 --drill")
    if not args.drill:
        raise SystemExit("要么 --drill，要么什么都不做")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = CORE / "data" / "instances" / ("t4-" + stamp)
    got = run_drill(home=out / "home", out=out, profile_root=args.profile_root)
    print(json.dumps(got, ensure_ascii=False))
    return result_exit_code(got['stats'])


if __name__ == "__main__":
    raise SystemExit(main())
