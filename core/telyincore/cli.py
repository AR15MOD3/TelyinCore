# telyincore/cli.py
from __future__ import annotations

import asyncio
import sys

from .config import Config, load_config
from .generate import generate
from .transport import HttpxTransport, Transport


async def _run(cfg: Config, text: str, transport: Transport | None = None) -> str:
    """要说给你看的那一行 —— **正文优先，没有正文时用拒绝文本**。

    ⚠️ 只回拒绝的流里 `Result.text` 是空的，而**那是一次成功的对话**（内容是拒绝）。
    直接打 `text` 就会打印一个空行 —— 对用户说"成功"却什么都不显示，
    是三种做法里最坏的（裁定见 T7 报告 C.2）。
    ⚠️ **不为它加退出码**：拒绝不是错误，调用方不需要分辨一个它不需要分辨的东西。

    ⚠️ 空判据用 **`.strip()`**，与 `generate.py` 里那条**同一口径**：
    只含空白的 `text` 是**可达的**（一段只含空白的 delta 之后跟一个拒绝 delta，
    判据 6 会放行）—— `text or refusal` 那种写法会打一个**空格**并 rc 0，
    又是一个"说成功、什么都不显示"。

    ⚠️ **transport 是参数（回评 I4）**：默认 `None` ＝ 造一个真的 `HttpxTransport`；
    测试／调用方可以注入自己的那个（于是**不必碰引擎**，也不必 monkeypatch 本模块的全局名）。

    ⚠️ **谁的谁关**：`_run` 只关**它自己造的那个**（`finally`，所以失败路径上也关）；
    注入进来的归调用方 —— `_run` 替调用方关掉它，等于替对方做了一个它没要求的决定
    （对方可能还要复用那个 client）。
    """
    owned = transport is None
    if transport is None:
        # ⚠️⚠️ **这一条路不带鉴权头 —— 那不是漏了，是它没有那个概念**（2026-10-17 查清）：
        #   `Config` 里**没有"密钥"那一格**（只有 `base_url` ／ `model` ／ `max_tokens` …），
        #   带钥匙的那一份在 `launch.the_key_headers(engine)` 上 —— 而它要一个 `Engine`
        #   （那份档案里有 `secret` 那一格），这一条 CLI 路**从头到尾没有 Engine**。
        #   ⇒ 后果照实说：**对着在线那几家，这条命令会 401**；本机那种不看钥匙的照旧通。
        #   ⇒ 要用在线那家，走 `launch.main`（面板那条路），别用这一条。
        #   ⚠️ 别在这里硬塞一把钥匙：那要么把密钥塞进 `Config`（它不是为这个长的），
        #     要么让 CLI 也去读盘上那份档案 —— 两样都比"把话说清"更重。
        transport = HttpxTransport()
    try:
        result = await generate(cfg, [{"role": "user", "content": text}], transport)
    finally:
        # ⚠️ 放在 `finally`：`generate()` 抛是**常事**（连不上、超时、协议错），
        # 只在成功路径上关就等于"只有成功的请求不泄漏" —— 而失败恰恰更常见。
        if owned:
            await transport.aclose()
    return result.text if result.text.strip() else result.refusal


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print('用法: python -m telyincore.cli "你要说的话"', file=sys.stderr)
        return 2
    try:
        # ⚠️ **全仓唯一一处事件循环入口**（spec §六⑨）：`loop.py` **已经来了**（`telyincore/loop.py`），
        # 而循环**归它** —— 它自己的抬头写着「循环归**入口**所有，它只在里面跑」⇒
        # 入口只有这一处可以换掉，别把循环散到别的地方去。
        reply = asyncio.run(_run(load_config(), " ".join(args)))
    except Exception as exc:               # noqa: BLE001 —— CLI 兜底
        print(f"出错了：{exc}", file=sys.stderr)
        return 1
    # ⚠️ `print` 在 `try` **外面**：写输出失败（非 UTF-8 控制台的 `UnicodeEncodeError`、
    # 管道给 `head` 时的 `BrokenPipeError`）是**我们这端**的事，
    # 被上面那个 handler 抓住就会变成「出错了：…」＋ rc 1 ——
    # 那是把"输出写不出去"报成"引擎坏了"，两条完全不同的故障。
    print(reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
