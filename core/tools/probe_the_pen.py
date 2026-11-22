"""**那一支笔她拿不拿得动** —— 直接问真模型要一次 `profile_add`（不信探针那一层）。

⚠️ **它与 `group_portrait_probe.py` 是两件事，别混**：
   · 探针量的是"她自己会不会想起来动手"（走生产那条路，判据是行为）；
   · 这一份量的是"**这支笔本身通不通**"（直接发一次请求 + 那个工具声明）。
   ⇒ 探针跑了十几趟都是"一次都没调"，于是必须先分清：
     是**她不想写**，还是**这支笔发出去就坏**（那两条的修法完全不同）。

⚠️ **它真发一次请求**（花钱、要密钥）：密钥从盘上读、只用来发出去，
   不回显、不落盘。本机那条路（`Local`）不需要密钥。

用法：
```
.\\.venv\\Scripts\\python.exe tools\\probe_the_pen.py
.\\.venv\\Scripts\\python.exe tools\\probe_the_pen.py --say "她说她不吃辣的"
```
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parent.parent
for _one in (str(_CORE), str(_CORE / "telyincore")):
    if _one not in sys.path:
        sys.path.insert(0, _one)

GROUP = "group:qq:765749025"
ALICE = "qq:1228600196"


async def _run(args: argparse.Namespace) -> int:
    import launch
    from telyincore.engine import Engine
    from telyincore.generate import generate
    from telyincore.transport import HttpxTransport

    engine = Engine(_CORE / "data")
    profile = engine.current()
    if profile is None:
        print("盘上没有当前档案（data/engine.json）", file=sys.stderr)
        return 2
    declarations = launch._profile_tool_declarations(list(launch.DECLARED_TOOLS))
    tools = [one for one in launch._the_declarations(declarations)
             if one["name"] in ("profile_add", "finish_turn")]
    config = launch._DeclaresTools(engine.live(), tuple(tools))

    head = [
        {"role": "system", "content":
         "你在一个 QQ 群里。此刻群里正在说话的是 " + ALICE + "。"
         "把下面这一轮里**关于这个人的稳定事实**记进画像，用 profile_add，省略 who；"
         "不要回话，记完就 finish_turn。"},
        {"role": "user", "content":
         "[QQ群765749025·消息#1] " + ALICE + "：" + args.say},
    ]
    print(f"[笔] 档案 {profile.name}／{profile.kind}／{profile.model}；"
          f"发出去的工具：{', '.join(one['name'] for one in tools)}")
    got = await generate(config, head, HttpxTransport(
        headers_of=launch.the_key_headers(engine)))
    print(f"[笔] 回来 status={got.status} 文字={got.text[:200]!r}")
    print(f"[笔] 它要调的：{[(one.name, one.arguments) for one in got.calls]}")
    if not got.calls:
        print("\n★ **一次工具调用都没有** —— 这支笔发出去没能让她动手（不是画像那一层的事）")
        return 1
    print("\n★ 她动手了 —— 那一支笔本身是通的")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="量那一支笔她拿不拿得动")
    parser.add_argument("--say", default="我不吃辣的，一点都碰不了。上回那个火锅我就涮清水。")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
