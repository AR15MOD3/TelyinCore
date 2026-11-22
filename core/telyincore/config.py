from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

#: 与 charsvc 同一套判法（`main.py:4786` / `services.py:1239`：`not in ("0","false","off","no")`）。
FALSEY = frozenset({"0", "false", "off", "no"})


@dataclass(frozen=True)
class Config:
    #: **上游地址** —— 可以为空，而**缺省就是"没有"**（2026-10-05 作者裁定"甲"）。
    #: ⚠️ 空着的意思是"**还没人说过**" ⇒ 由人在页上填，或由「获取模型列表」从上游带回来 。
    #: 从前这里的缺省是 `http://127.0.0.1:24550/v1` —— 那是**照某一台机器写的** ，
    #: 于是"新建实例"会把那个地址**带出来**（而那台早就停了 ）。
    base_url: str
    #: **模型名** —— 同一条规矩：可以为空，缺省"没有"（不预设任何一台机器的名字）。
    model: str
    max_tokens: int
    thinking: bool
    #: **模型的物理上限** —— 硬阈的**分母**（`hardTokens = max(0, context_window − max_tokens)`）。
    #: ⚠️ 它与 `max_tokens`（**出站**额度）是**两个不同的东西**：`max_tokens` 管"这一答最多多长"，
    #:    这一个管"输入 ＋ 输出加起来最多多长"。混成一个 ⇒ 硬阈恒错。
    #: 配置留空表示自动发现。Engine 优先使用上游报告或已知官方容量，
    #: 无法取得时使用用户约定的 131072 token 规划值，并标明 fallback 来源。
    #: 这里保留 None 作为未填写标记，避免把回退容量固化为手动上限。
    context_window: int | None = None
    first_response_ms: int = 300_000
    frame_idle_ms: int = 120_000
    content_idle_ms: int = 300_000
    retry_delays_ms: tuple[int, ...] = (1000, 4000, 10000)
    #: **这一趟带去的那张工具表**（2026-10-05）—— 就是请求体里 `tools` 那一格 。
    #: ⚠️ **缺省是"空那一串"＝「没有工具」** ⇒ 请求体里**那一格根本不放**
    #: （与上面那几格同一条规矩：**没有就说没有，不编一个空壳** ）。
    #: 它是**声明**（`ToolSchema` 那三样：`name` ／ `description` ／ `parameters` ），
    #: 不是 `ToolDef`（handler 由装配层绑 —— 那个**不许**上这条路 见 `tools.py` 的抬头）。
    tools: tuple[Mapping[str, object], ...] = ()
    # Independent thinking allowance; None leaves the upstream default intact.
    reasoning_budget_tokens: int | None = None
    # One whole generation, including authentication, retries and backoff.
    # Cooperative deadline: cleanup must still retain resources until retired.
    total_response_ms: int = 600_000


def _env(e: Mapping[str, str], key: str, default: str) -> str:
    """空串当没设 —— 与 `TELYIN_THINKING` 的处理保持一致。"""
    v = e.get(key)
    return default if v is None or v.strip() == "" else v


def _opt_int(e: Mapping[str, str], key: str) -> int | None:
    """ **空 ⇒ `None` ＝「没人说过」**（与 `Config.context_window` 那一格同一条规矩 ）。

    ⚠️ 它只给**可选**的那几格用；必填那几格（如 `max_tokens`）照旧用 `int(_env(...))` ——
       空着就是错，那是另一回事 。
    """
    text = _env(e, key, "").strip()
    return int(text) if text else None


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """三层配置里最高的那层 —— 环境变量。

    默认值写在这儿；**同一个函数**给运行时和面板用，不许各写一份
    （那是作者那边踩过的坑：面板说 off、实际 keep-image）。
    """
    e = os.environ if env is None else env
    return Config(
        # **这两个缺省是空的**（2026-10-05 作者裁定"甲"）—— **不预设任何一台机器** 。
        # ⚠️ 空着 ＝ "还没人说过" ⇒ 由人在页上填，或由「获取模型列表」从上游带回来 。
        #    ⚠️ 它们同时喂三处：本文件的运行时 ／ `engine.DEFAULTS` ／ `PRESETS` 里那条 `Local`
        # （那条写的是 `base_url=DEFAULTS.base_url` ⇒ **引用**，所以改这里它跟着空 ）。
        base_url=_env(e, "TELYIN_UPSTREAM", "").rstrip("/"),
        model=_env(e, "TELYIN_MODEL", ""),
        max_tokens=int(_env(e, "TELYIN_MAX_TOKENS", "16384")),
        # ⚠️ 与 `max_tokens` **各读各的 env** —— 刻意不共用一个字段（见 `Config.context_window`
        #    那段：它们是"出站额度"与"物理上限"两件事）。
        # **空 ⇒ `None`**（`_env` 把空串当「没设」⇒ 交回 default；这里再判一次空 ）
        context_window=_opt_int(e, "TELYIN_CONTEXT_WINDOW"),
        reasoning_budget_tokens=_opt_int(e, "TELYIN_REASONING_BUDGET_TOKENS"),
        thinking=_env(e, "TELYIN_THINKING", "1").strip().lower() not in FALSEY,
        total_response_ms=int(_env(e, "TELYIN_TOTAL_RESPONSE_MS", "600000")),
    )
