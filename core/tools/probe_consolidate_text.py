# tools/probe_consolidate_text.py
""" **整理那一趟的隔离验证**（2026-10-17 批①）—— 真上游、真写盘、副本。

 ⚠️ **它验的是这一件事**：那一趟拿着**带图的交接快照**去请求上游，**不再 400**，
    而且"决定不写"与"请求失败"在账上分得开。

 ⚠️ **它不碰真那一份家**：`bots/<谁>/` 整份抄到 `data/instances/probe-consolidate/`
    底下再跑；最后一行照实报真那一份家有没有被动过。
 ⚠️ **引擎不可用 ⇒ 它照实说"待验收"**（不写"通过"）。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import shutil
import sys
from datetime import datetime

_CORE = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_CORE), str(_CORE / "telyincore")]

import launch                                            # noqa: E402
from telyincore import web                               # noqa: E402
from telyincore.consolidate import (Consolidation,       # noqa: E402
                                    HER_FILES, as_text)
from telyincore.engine import Engine                     # noqa: E402
from telyincore.generate import generate                 # noqa: E402
from telyincore.protocol import adapt                    # noqa: E402
from telyincore.transport import HttpxTransport          # noqa: E402

#: 一份**带图**的交接快照（图给的是本地路径 —— 那正是那天 400 的形状）。
CONTEXT = [
    {"role": "system", "content": "你是谁那几段（副本里的那一版）"},
    {"role": "user", "content": "我回来了"},
    {"role": "user", "content": [
        {"type": "input_text", "text": "看，我桌上那张纸"},
        {"type": "input_image", "image_url": "C:/tmp/桌面.jpg"}]},
    {"role": "assistant", "content": "嗯，我看到了。"},
    {"role": "user", "content": [
        {"type": "input_image", "image_url": "C:/tmp/另一张.jpg"}]},
]


def _the_copy(bot: str) -> pathlib.Path:
    import tempfile
    from bot_registry import bot_id
    source = _CORE.parent / 'bots' / bot_id(bot)
    if not source.is_dir():
        raise ValueError('source bot directory missing')
    parent = _CORE / 'data' / 'instances'
    parent.mkdir(parents=True, exist_ok=True)
    root = pathlib.Path(tempfile.mkdtemp(prefix='probe-consolidate-', dir=parent))
    (root / "bots").mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, root / "bots" / bot)
    (root / "persona").parent.mkdir(parents=True, exist_ok=True)
    return root


async def main() -> int:
    engine = Engine(_CORE / "data")
    # `--profile <名字>`：**只在内存里**换这一趟用哪条档案（`use()` 不落盘 ——
    # 盘的 `current` 一个字不动）。两条协议都要验：那天那两条 400 是在
    # `responses` 那条路上量的，而线上跑的是 `chat`。
    want = ""
    for i, said in enumerate(sys.argv):
        if said == "--profile" and i + 1 < len(sys.argv):
            want = sys.argv[i + 1]
    if want:
        engine.use(want)
    cfg = engine.live()
    try:
        cur = engine.current()
    except Exception:                        # noqa: BLE001
        cur = cfg
    print("引擎档案：" + str(getattr(cur, "name", "?")) + " ／ kind="
          + str(getattr(cfg, "kind", "?")), flush=True)

    home = _the_copy("Telyin")
    book = web.PersonaPort(home / "bots" / "Telyin" / "persona")
    told: list[tuple[str, dict]] = []

    transport = adapt(HttpxTransport(headers_of=launch.the_key_headers(engine)),
                      kind_of=lambda: cfg.kind, values_of=lambda: cfg.values())

    try:
        def clock() -> str:
            return datetime.now().strftime("%Y%m%d-%H%M%S")
    
        def log(event: str, detail: dict) -> None:
            told.append((event, detail))
            print("  [账] " + event + " " + json.dumps(detail, ensure_ascii=False)[:180], flush=True)
    
        trip = Consolidation(
            config=cfg, transport=transport, generate=generate,
            # ★ 批②：**带着基线版本**过共同提交门（`expect`）
            write=lambda name, text, stamp, **kw: book.save_hers_text(
                name, text, stamp=stamp, **kw),
            read=lambda name: (book.read_text(name) if (book.root / name).is_file() else ""),
            stamp=clock, log=log, sleep=lambda ms: asyncio.sleep(ms / 1000))
    
        print("\n=== 投影前 / 投影后（只报形状，不报正文）===", flush=True)
        shown = as_text(CONTEXT)
        print("  条数 " + str(len(CONTEXT)) + " → " + str(len(shown)))
        print("  还有图吗：" + str("image_url" in json.dumps(shown, ensure_ascii=False)))
        print("  那一句在不在：" + str(any("此处有图片" in json.dumps(one, ensure_ascii=False)
                                       for one in shown)))
    
        print("\n=== 真那一趟 ===", flush=True)
        got = await trip.run(CONTEXT, task="probe-batch1")
        print("\n交回：" + ("None（这一趟没成）" if got is None else
                          "写了 " + str(list(got.wrote)) + " ／ 浮现=" + str(got.emergence)), flush=True)
    
        kinds = [event for event, _ in told]
        print("\n=== 判据 ===", flush=True)
        failed = [d for e, d in told if e == "consolidate-failed"]
        print("  排上了：" + str("consolidate-queued" in kinds or "consolidate-running" in kinds))
        print("  请求失败：" + str(bool([d for d in failed if d.get("category") == "request"])))
        print("  校验失败：" + str(bool([d for d in failed if d.get("category") == "reply"])))
        print("  成功（含「决定不写」）：" + str("consolidate-done" in kinds))
    
        print("\n=== 副本里的字 ===", flush=True)
        for name in HER_FILES:
            path = book.root / name
            size = len(path.read_bytes()) if path.is_file() else -1
            print("  " + name + "：" + (str(size) + " 字节" if size >= 0 else "不在"))
        keeps = sorted(p.name for p in book.root.glob(".history-*"))
        print("  留版目录：" + (", ".join(keeps) if keeps else "（没有）"))
    
        real = _CORE.parent / "bots" / "Telyin" / "persona"
        print("\n=== 真那一份家（这一趟不该动它）===", flush=True)
        for name in HER_FILES:
            path = real / name
            print("  " + name + "：" + (str(len(path.read_bytes())) + " 字节" if path.is_file() else "不在"))
        return 0 if got is not None and not failed and 'consolidate-done' in kinds else 1
    finally:
        primary = sys.exception()
        try:
            await transport.aclose()
        except BaseException as cleanup:
            cleanup.probe_transport = transport
            if primary is None:
                raise
            primary.probe_cleanup_errors = (cleanup,)

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
