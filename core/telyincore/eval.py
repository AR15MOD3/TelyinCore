""" **评测：检索到底答不答得上**（2026-10-07「④」）—— 外加"它自己会喊" 。

⚠️ **为什么要它**： 前面每一步升级（要不要上语义 ／ 要不要换模型 ／ 要不要重排）
   **都只能靠它回答** —— 没有它，往上走全是猜 。
    而更要紧的一条： **"日后不一定会想起来"** ⇒ 那就**别指望人记** ——
   **让它自己报**（ 越过线 ⇒ 页上写一句 ）。

⚠️ **它守那三条约定**（不许过期 ／ 不许催 ／ 只读）：
   · 只在**真变差**时喊 （ 不是每天报一次进度 ）
   · 喊的是**事实 ＋ 一件可以做的事** （"这一批里有 8 条字面命不中 ⇒ 该开语义那一层" ）
   · 别说"你该去关心…"那种话 。

⚠️ 本模块**不读时钟**（"哪一次跑的"由调用方递 ）；**不写那几个正本**（账是派生件 ）。
"""

from __future__ import annotations

from collections import deque
import pathlib
from typing import Any, Callable, Sequence

from .append_journal import append, objects
from .source_guard import lease

#: 那一份问题（**人写的** —— 每行 `{"问": …, "该命中": ["名字", …]}` ）。
THE_QUESTIONS = "问答.jsonl"

#: 那本账（每次跑一行 ⇒ **能看趋势** ）。
THE_RUNS = "跑过的.jsonl"

#: 一条问句算"答上了"的门槛： 该命中的那一份，得落在**前几名**里 。
IN_THE_TOP = 3


def the_questions(base: Any) -> list[dict[str, Any]]:
    """ 读那一份问题（ 读不成 ⇒ **空那一串** 不是崩 ；坏行跳过 ）。"""
    where = pathlib.Path(base) / THE_QUESTIONS
    if not where.is_file():
        return []
    out: list[dict[str, Any]] = []
    for row in objects(where):
        question, targets = row.get("问"), row.get("该命中")
        if (isinstance(question, str) and question.strip() and isinstance(targets, list)
                and targets and all(isinstance(target, str) and target.strip() for target in targets)):
            out.append({"问": question, "该命中": targets})
    return out


def run(base: Any, pairs: Sequence[dict[str, Any]], *,
        finder: Callable[[str], list[str]]) -> dict[str, Any]:
    """ **跑一遍**（ `finder` 由调用方递 ⇒ 本层不认识检索 也不认识模型 ）。

    ⚠️ 交回四样： **条数** ／ **答上的** ／ **漏的**（一条都没进前列 ）／ **平均名次** 。
    """
    said: list[dict[str, Any]] = []
    for one in pairs:
        want = [str(x) for x in one.get("该命中", [])]
        got = [str(x) for x in finder(str(one.get("问", "")))]
        top = got[:IN_THE_TOP]
        rank = None
        for i, name in enumerate(got):
            if name in want:
                rank = i + 1
                break
        said.append({"问": str(one.get("问", "")), "拿到": got[:IN_THE_TOP],
                     "该命中": want, "名次": rank,
                     "答上了": rank is not None and rank <= IN_THE_TOP})
    hit = [one for one in said if one["答上了"]]
    ranks = [one["名次"] for one in hit]
    return {"几条": len(said), "答上的": len(hit), "漏的": len(said) - len(hit),
            "平均名次": round(sum(ranks) / len(ranks), 2) if ranks else None,
            "逐条": said}


def put_a_run(base: Any, one: dict[str, Any], *, stamp: str) -> None:
    """ 记一笔（ 只记数，不记逐条 ⇒ 账小 ； 时刻由调用方递 ）。"""
    where = pathlib.Path(base) / THE_RUNS
    where.parent.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in one.items() if k != "逐条"}
    slim["stamp"] = str(stamp)
    append(where, slim)


def the_runs(base: Any) -> list[dict[str, Any]]:
    """ 那本账里的几次（ 坏行跳过 ）。"""
    where = pathlib.Path(base) / THE_RUNS
    if not where.is_file():
        return []
    return list(objects(where))


def _valid_run(row):
    counts = [row.get(key) for key in ("几条", "答上的", "漏的")]
    return (all(type(value) is int and value >= 0 for value in counts)
            and counts[0] == counts[1] + counts[2])


def the_alarm(one: dict[str, Any], before: Sequence[dict[str, Any]] = (), *,
              too_many: float = 0.3) -> str:
    """ **它自己会喊**（ 这是"日后不一定想起来"的正解 ）—— 交一句人话（没事 ⇒ 空 ）。

    ⚠️ **只在真变差时喊**（守"不许催"那条 ）：
       · 一条问句都没有 ⇒ 空 （ 不是"你该写问题" —— 那是催 ）
       · **漏的**越过线 ⇒ 喊 （"这一批里有 N 条一条都没进前 K" ）
       · 或者**比上一回更差**（漏的多了 ／ 平均名次退了 ）⇒ 喊
    ⚠️ 喊的话里要有**一件可以做的事**（ 例如"该开语义那一层了" ）—— 不是光报个数 。
    ⚠️ 它**不算天数、不比"多久没跑"** （ 那就是过期 —— 本仓不要那一种 ）。
    """
    many = int(one.get("几条", 0) or 0)
    if many <= 0:
        return ""
    lost, hit = int(one.get("漏的", 0) or 0), int(one.get("答上的", 0) or 0)
    rate = lost / many
    # ⚠️ **先看"变差"、再看"绝对线"**（2026-10-07 判据量出来的顺序）：
    # "比上一回差"是**新信息** 而"这批答得不好"可能上一回也这么说 ⇒
    # 先喊新信息，才不啰嗦 （ 而两条都不成立 ⇒ 一个字不说 ）。
    said = ""
    for old in list(before)[-1:]:
        was_lost = int(old.get("漏的", 0) or 0)
        # `was_lost` **不许当门槛**（2026-10-07 判据抓到）： 上一回漏 0 条、
        # 这一回漏 1 条 —— **更值得喊** （0 是假值，会被 `and` 吞掉 ）。
        if lost > was_lost:
            said = (f"检索比上一回差了：漏的从 {was_lost} 条变成 {lost} 条。")
            break
    if not said and rate > too_many:
        said = (f"检索这一批答得不好：{many} 条里有 {lost} 条一条都没进前 {IN_THE_TOP}。")
    if not said:
        return ""
    return said + " 该看看**语义那一层**了（`embeddings` 那一格今天是不是真的在算）。"


def keep_and_watch(base: Any, pairs: Sequence[dict[str, Any]], *,
                   finder: Callable[[str], list[str]], stamp: str) -> dict[str, Any]:
    """ 一次做完：**跑 → 记账 → 看要不要喊** （入口那边一处调用就够 ）。"""
    one = run(base, pairs, finder=finder) if pairs else {"几条": 0}
    told = ""
    if pairs:
        where = pathlib.Path(base) / THE_RUNS
        # Freeze the immediately preceding valid result and append as one
        # cooperating operation. Keep one summary, not the complete history.
        with lease(where):
            previous = deque((row for row in objects(where) if _valid_run(row)), maxlen=1)
            put_a_run(base, one, stamp=str(stamp))
            told = the_alarm(one, previous)
    return {"跑过": {k: v for k, v in one.items() if k != "逐条"}, "喊": told}
