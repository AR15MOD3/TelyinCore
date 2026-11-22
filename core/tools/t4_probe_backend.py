""" 后端可用性探针（一次极小请求；不改配置、不重启、不加载模型）。 """
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import time

CORE = pathlib.Path(r"D:\TelyinCore\core")
sys.path[:0] = [str(CORE), str(CORE / "telyincore")]

from telyincore import engine as E          # noqa: E402
from telyincore import generate as G        # noqa: E402
from telyincore import protocol as P        # noqa: E402
from telyincore.transport import HttpxTransport  # noqa: E402
import launch  # noqa: E402


async def main() -> int:
    eng = E.Engine(CORE / "data")
    profile = eng.current()
    print("档案:", eng.current_name(), "｜kind:", getattr(profile, "kind", "?"),
          "｜model:", getattr(profile, "model", "?"))
    from telyincore.config import Config
    live = eng.live()
    cfg = Config(base_url=str(getattr(live, "base_url", "")),
                 model=str(getattr(live, "model", "")),
                 max_tokens=int(getattr(live, "max_tokens", 8) or 8),
                 thinking=bool(getattr(live, "thinking", False)), tools=())
    transport = P.adapt(HttpxTransport(headers_of=launch.the_key_headers(eng)),
                        kind_of=lambda: str(getattr(profile, "kind", "")),
                        values_of=lambda: {"thinking": bool(getattr(cfg, "thinking", False))})
    started = time.time()
    try:
        got = await asyncio.wait_for(
            G.generate(cfg, [{"role": "user", "content": "只回复一个字：好"}],
                       transport), timeout=120)
        print("HTTP 正常｜耗时 %.1fs｜有文字: %s｜字数: %d"
              % (time.time() - started, bool(getattr(got, "text", "")),
                 len(str(getattr(got, "text", "")))))
        return 0
    except BaseException as why:      # noqa: BLE001
        print("探针失败:", type(why).__name__, str(why)[:120])
        return 2


raise SystemExit(asyncio.run(main()))
