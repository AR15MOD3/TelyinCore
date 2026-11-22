"""批④ 隔离实例：起一个只读页（不碰 7799、不碰真实家），给浏览器 ／ curl 看。"""
from __future__ import annotations

import json
import pathlib
import sys
import time

CORE = pathlib.Path(r"D:\TelyinCore\core")
sys.path[:0] = [str(CORE), str(CORE / "telyincore")]

from telyincore import skills, web  # noqa: E402

RUN = CORE / "data" / "instances" / ("accept-b4-" + time.strftime("%Y%m%d-%H%M%S"))
HOME = RUN / "bots" / "Telyin" / "memory"
TREE = HOME / "做法"
TREE.mkdir(parents=True)


def write(name: str, body: str) -> None:
    (TREE / name).write_bytes(body.encode("utf-8"))


write("别人发图时.md", "别人发图，别急着描述；等对方问。")
write("有人发表情包时.md", "表情包不用猜是什么。")
write("群里冷场时.md", "别硬找话题。")

rows = {one["title"]: one for one in skills.rows(HOME)}
skills.merge(HOME, op_id="op-b4-1", keep=rows["别人发图时"]["id"],
             sources=[rows["有人发表情包时"]["id"]],
             expect={rows["别人发图时"]["id"]: rows["别人发图时"]["version"],
                     rows["有人发表情包时"]["id"]: rows["有人发表情包时"]["version"]},
             text="别人发图或发表情包时：别急着描述，也不用猜。", stamp="20261018-120000")

# **同一任务的真实顺序**：先 files-only（后续欠着），后来 done（办完了）。
LEDGER = RUN / "consolidation.jsonl"
_task = "20261018-120000-abc123"
_ledger_rows = [
    {"ts": "20261018-120000", "type": "consolidate-files-only", "source": "core",
     "text": json.dumps({"task": _task, "wrote": ["她写的/世界认知.md"],
                         "settled": [], "planned_settle": 1}, ensure_ascii=False)},
    {"ts": "20261018-120500", "type": "consolidate-done", "source": "core",
     "text": json.dumps({"task": _task, "wrote": ["她写的/世界认知.md"]},
                        ensure_ascii=False)},
]
with open(LEDGER, "w", encoding="utf-8", newline="") as handle:
    for one in _ledger_rows:
        handle.write(json.dumps(one, ensure_ascii=False) + "\n")


def view() -> dict:
    got = dict(skills.history_view(HOME))
    got["status"] = skills.status_view(HOME, ledger=LEDGER)
    return got


PORT = 7813
with web.WebService(port=PORT, web_root=CORE / "dist" / "web", read_skills=view) as svc:
    print("PORT=" + str(PORT), flush=True)
    print(json.dumps(view()["status"], ensure_ascii=False), flush=True)
    deadline = time.time() + 900
    while time.time() < deadline:
        time.sleep(1)
