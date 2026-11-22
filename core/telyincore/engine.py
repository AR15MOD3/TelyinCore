# telyincore/engine.py
"""「这一台机器用哪个脑」的那一层 —— **档案表 ＋ 密钥 ＋ 活视图 ＋ 来源**。

 三层合并，**环境变量最高**（照 `docs/design/09-日志与配置.md` §9.1 那条）：

```
默认值（`config.load_config(env={})` 给的那一份 —— 不许在这里另写一份）
   ↑ 被 `data/engine.json` 里的**当前档案**覆盖
      ↑ 被**环境变量**覆盖（键名沿用 `TELYIN_UPSTREAM` 那一套，见 `ENV_NAMES`）
```

 于是每个键都答得出**它是从哪来的**：`{value, source}`，`source ∈ {"default","file","env"}`
（`09` §9.1 那条"每个键都要能回答这个值从哪来"—— 少了它就会重演那个坑：
**面板说 off、实际 keep-image**）。

 落盘两处，**都在 `data/` 下**（`data/` 已在 `.gitignore` 里）：

```
data/engine.json   档案 ＋ 当前那一条（**不带密钥**）
data/engine.env    密钥，一行一个 `NAME=value`
```

⚠️ **写盘一律原子**：先写同目录唯一的 `<名字>.tmp-*` → `os.replace` ⇒ 盘上**永远不会**出现半份；
⚠️ **解析失败拒写**：读不出 `engine.json` 时**不覆盖**它（宁可报错，也不许把改坏的配置写回去）。
⚠️ **密钥只写不回显**：本模块**没有**"读回某条密钥的值"这个口 —— 只有"配了没有 ＋ 从哪来"
（面板照这个画，"把谎话画成输入框"那条禁令就是这么落地的）。
 **活视图**（`live()`）：它交回的对象**每次属性一读就现算** ⇒ `generate()` 那一句一个字不用改，
  而"保存之后下一次模型调用就读新值"这件事**落在对象上**，不落在某个装配时刻上。
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from copy import deepcopy
from functools import wraps
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, replace, field
from types import MappingProxyType
import uuid
import hashlib
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import Config, load_config
from .input_budget import hard_input_tokens

#: 可改的那些键 —— 顺序就是页面上那几段的顺序（**与页面读的名字同一份**）。
KEYS: tuple[str, ...] = ("base_url", "model", "max_tokens", "thinking", "context_window",
                         "temperature", "reasoning_effort", "reasoning_budget_tokens")

#: 每个键的**环境变量名** —— 前五个**沿用 `config.py` 已经在读的那五个**
#:   （`TELYIN_UPSTREAM` ／ `MODEL` ／ `MAX_TOKENS` ／ `THINKING` ／ `CONTEXT_WINDOW`），
#:   后两个是新加的。⚠️ **一个键只许有一个 env 名** —— 两个就是"两处各答一个数"。
ENV_NAMES: dict[str, str] = {
    "base_url": "TELYIN_UPSTREAM",
    "model": "TELYIN_MODEL",
    "max_tokens": "TELYIN_MAX_TOKENS",
    "thinking": "TELYIN_THINKING",
    "context_window": "TELYIN_CONTEXT_WINDOW",
    "temperature": "TELYIN_TEMPERATURE",
    "reasoning_effort": "TELYIN_REASONING_EFFORT",
    "reasoning_budget_tokens": "TELYIN_REASONING_BUDGET_TOKENS",
}

#: **默认值不在这里另写一份** —— 它就是 `config.load_config` 在**空环境**下交出的那一份
#:   （`config.py` 抬头那句"同一个函数给运行时和面板用，不许各写一份"）。
DEFAULTS: Config = load_config(env={})

#: ⚠️ **只有那两个"新字段"的默认值住在这里** —— 因为 `Config` 今天**没有**它们
#: （温度与推理强度是这一刀新加的两格，见设计稿 裁定 6）。 它们是这一格的**第一份**，
#:   不是别处的副本；等它们进 `Config` 那一天，这两行就该删掉、改读 `Config`。
#:
#: **2026-10-04：两个数按"结合参考与我们这一支"算出来之后定的**（作者要的）：
#: · `reasoning_effort` 当年取的是 `"high"` —— 参考那一格写的是 **`hight`**（ 一个拼错，
#:     设计稿 §3.3 记过"页上照原样渲染、一句提示都没有"）⇒ 取它的**意图** `high`。
#:   ⚠️⚠️ **2026-10-05：这两格都改成 `None`**（作者："先做温度吧"⇒ 随后"一并"）——
#:     与地址／模型名／上下文上限**同一条原则**：`0.8` 是**照着 DeepSeek 的实物**、`high` 是
#: **照着参考那一格**定的 ⇒ 都不该当"谁都没说时"的缺省 。`None` ＝ **"还没人说过"**：
#: · **展示**上 ⇒ 页上那两格空着（推理强度那格显示"（不设）"）；
#: · **请求**上 ⇒ 本来就不受影响（`LiveConfig.values()` 读的是**档案里那一格本身** ，
#: 不是 `effective()` 的默认值 ⇒ "没写下过"就**不发送** ⇒ 用引擎自己的默认 ）。
#: 于是**这一份里没有一个是"某台机器的数"了** （那正是这一串改动要的）。
#:   ⚠️ 另外三格（最大输出 ／ 上下文上限 ／ 思维链）**不在这里** —— 它们本来就住在
#: `config.load_config` 那一份默认值里（ 而那两格的真数**问上游要**：见
#:     `web.probe_engine(mode='models')` —— DeepSeek 的 `/models` 逐个模型报了
#:     `context_window` ／ `max_output_tokens` ／ `effort.default_level`）。
EXTRA_DEFAULTS: dict[str, Any] = {"temperature": None, "reasoning_effort": None}

# 档案名以字母或数字开头，后续允许字母、数字、连字符和下划线。
NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

#: ⚠️ 保留名：与"当前"那几个记号撞了会分不开。
RESERVED = frozenset({"current", "profiles", "default", "env"})


class EngineError(ValueError):
    """这一层的**拒收**（输入不合法 ／ 解析失败）—— 页那一层照它答 400。"""


class EngineConflict(EngineError):
    """**动不了**（那条正被引用）—— 页那一层照它答 409。"""


@dataclass(frozen=True)
class Sourced:
    """一个键的答案：**值** ＋ **它是从哪来的**（`09` §9.1 那条）。"""

    value: Any
    source: str              # "default" ／ "file" ／ "env"

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "source": self.source}


@dataclass(frozen=True)
class Profile:
    """一条档案（设计稿 §五：名字 ＋ 协议 ＋ 地址 ＋ 模型 ＋ 最大输出 ＋ 思维链 ＋ 温度 ＋
    推理强度 ＋ 上下文窗口 ＋ 密钥变量名）。

    ⚠️ **`kind` 保存后不可改**（要换协议就新建）—— 它决定请求体怎么拼，中途改等于换了一条路。
    """

    name: str
    kind: str = "responses"          # "responses" ／ "chat"
    base_url: str = DEFAULTS.base_url
    #: **默认是空**（2026-10-05 根因）：从前它是 `DEFAULTS.model` ⇒ **预设表里没写
    #:   `model=` 的那些全都继承了"本机那台"的模型名**（作者量到的：选 OpenAI ⇒ 模型名称
    #: 那一格变成了本机那台的模型 ；`GET /engine` 里 OpenAI ／ Zhipu-GLM ／ Custom 三条都是它）。
    #: 没有模型就是没有 —— 校验会说"不能为空"，而不是替它拿本机那份顶上。
    #:   ⚠️ 「Local」那条预设**显式**写着 `DEFAULTS.model`（本机那台才是它的答案），
    #: 而 `DeepSeek` 那条显式写着 `deepseek-chat` 。
    model: str = ""
    max_tokens: int = DEFAULTS.max_tokens
    thinking: bool = DEFAULTS.thinking
    #: 可以为空（＝「没写下过」）—— 见 `Config.context_window` 那一段
    context_window: int | None = DEFAULTS.context_window
    #: ⚠️ **这两格缺省是 `None`（＝"没写下过"）**，而**不是** 1.0 ／ `"low"`：
    #:   它们**发不发出去**由"档案里有没有这一格"决定（`protocol.shape` 里那条 `is not None`）。
    #:   ⇒ 于是本机那一条（今天生产上唯一真在跑的路）**逐字节与刀 3 之前相同** ——
    #:     零回归；而页上要显示的那个"默认值"（**推理强度**；温度那一格自 2026-10-05 起是
#: `None` ⇒ 页上留空）由 `EXTRA_DEFAULTS` 给 —— 那只是**展示**用的 。
    temperature: float | None = None
    reasoning_effort: str | None = None
    reasoning_budget_tokens: int | None = None
    #: **给人看的那个模型名**（2026-10-05 作者点出来的）：上游那一份里
    #:   `id="deepseek-flash"` 而 `name="DeepSeek-V4.1-Flash"` —— 而**发出去的是 id**。
    #:   ⚠️ 只存 `model` 的话，"页上显示什么"就只能显示 id（名字是上游报的，用完就没了）
    #:   ⇒ 那一格一刷新就又变回 `deepseek-flash`（作者的原话："点击后又变成 flash 了"）。
    model_name: str = ""
    # Editable label; name remains the stable key for API/OAuth credentials.
    display_name: str = ""
    #: **图像输入那个开关**（照参考 `types.ts` 的 `multimodal`；2026-10-05 **又改一次**）：
    #: **三态**：`None` ＝ **没表过态**（⇒ 上游说了算）／`True` ／ `False` ＝ **它自己的偏好**
    #: （⇒ **不许**被"取回列表"那一步压回去 —— 作者量到的"保存后又自动关"就是它）。
    #:   ⚠️ 从前是 `bool = False` ⇒ 分不出"它说了关"与"它还没说" ⇒ 于是人扳过的那一格
    #: 每次取回列表都被压回 `False`（页上那两条注释互相打架 ）。
    #: 而上游那一格（`inputImages`）管的是另一件事：**选模型时把这一格设上** 。
    multimodal: bool | None = None
    #: **这一家声明的那几档推理强度**（2026-10-05，照参考项目 `ProviderModule.reasoningTiers`）。
    #:   ⚠️ **空 = 开放**（它那一句原话："开放模块接受任意非空字符串"）—— 没量过的家一律留空，
    #:   不替它编档位；而 `effort_suggestions` 只是**备选值**（画出来给人挑）。
    #: 量过的就写实：DeepSeek 的 `/models` 自己报 `["low","high","max"]`（2026-10-04 实测）。
    efforts: tuple[str, ...] = ()
    #: 开放模块的**备选值**（参考那份 `effortSuggestions`）。
    effort_suggestions: tuple[str, ...] = ("low", "medium", "high")
    secret: str = ""                 # 密钥的**变量名**（值在 `engine.env` 里，本模块不回显）
    auth: str = "api_key"            # Subscription credentials live separately, never in engine.env.

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "kind": self.kind, "base_url": self.base_url,
            **({"auth": self.auth} if self.auth != "api_key" else {}),
            "model": self.model, "max_tokens": self.max_tokens,
            "thinking": self.thinking, "context_window": self.context_window,
            "temperature": self.temperature, "reasoning_effort": self.reasoning_effort,
            **({"reasoning_budget_tokens": self.reasoning_budget_tokens}
               if self.reasoning_budget_tokens is not None else {}),
            "secret": self.secret,
            "multimodal": self.multimodal,
            # **这三格是补的**（2026-10-05）——⚠️ 少了 `model_name`，页上"把名字一起存进去"
            #    那一下就被**这一层丢掉**了（写进盘的只有 `model`＝id）⇒ 名字活不过一次刷新
            #    （作者："又变成 flash 了"）。那两格是第 2 步的声明（见 `efforts` 那一段）。
            "model_name": self.model_name,
            **({"display_name": self.display_name} if self.display_name else {}),
            "efforts": list(self.efforts),
            "effort_suggestions": list(self.effort_suggestions),
        }

    @staticmethod
    def from_dict(one: Mapping[str, Any]) -> Profile:
        """从盘上那一份读回来 —— ⚠️ **缺的键用默认值补**，多出来的键**丢掉**。"""
        known = {f for f in Profile.__dataclass_fields__}          # noqa: SLF001 —— 就是那几个字段
        got = {k: v for k, v in one.items() if k in known and k != "name"}
        return Profile(name=str(one.get("name", "")), **got)       # type: ignore[arg-type]


def a_free_secret_name(name: str, taken: set[str]) -> str:
    """ **给一条档案一个没人用的变量名**（2026-10-05：两条档案互相顶的那一处）。

    ⚠️ 规矩只有一条：**一个变量名最多归一条档案** —— 共用的话，「页上刚敲的那把 key」
       写进那同一个变量 ⇒ **谁后保存谁赢** ⇒ 另一条拿着**别人的钥匙**去问上游
       ⇒ 「无效的令牌」（作者量到的那一幕 ）。
     撞了就加序号（`_2` ／ `_3` …）—— 而**已经有自己名字的那条**一个字不动 （不迁移别人的数据 ）。
    """
    base = "TELYIN_KEY_" + "".join(ch if ch.isalnum() else "_" for ch in str(name)).upper()
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


def is_a_good_name(name: str) -> bool:
    """ **档案名那条规矩，只写这一处**（`add()` 与预设表都问它）。

    ⚠️ **为什么值得单独一条**（2026-10-05 作者撞出来的）：预设表里原先写着「智谱 GLM」
       （中文 ＋ 空格），而 `add()` 只收 ASCII ⇒ 页上**能选、却存不了**
       （`400 档案名字不合规`）—— 典型的"左右脑互博"。
       ⇒ 那条规矩提出来之后，判据可以**拿它逐个验预设名**（不规矩就当场红），
         而不是等人在页上点了才知道。
    """
    return bool(NAME_OK.fullmatch(str(name))) and str(name) not in RESERVED


#: **预设表**（设计稿 §五 ／ 裁定 9）—— **2026-10-04 按"量出来的地址"写全**（作者要的）。
#:
#: ⚠️⚠️ **每一个地址都是真发过一次 HTTP 量出来的**（不带钥匙也分得出来）：
#:     `GET <地址>/models` → **401 ／ 403 ／ 200 ＝ 地址＋路径存在**（只是要鉴权）；404 ＝ 地址错。
#:     存疑的那几家再量 `POST <地址>/chat/completions`（401 ／ 400 ＝ 那条路存在）。
#:     ⚠️ **量不通的没写进来** —— 写进去就是给一个「必然报错」的选项：
#:       · **Cohere** 的两个口都回 403 HTML（那台机器被它的边缘挡着）⇒ 量不到；
#:       · **零一万物** 回 410 `model_service_closed`（那家关了）。
#:     ★ **Anthropic（2026-10-12 补进来了）** —— 它从前被排除在这张表外，因为
#:       "任何路径不给钥匙都回 401 ⇒ 认不出存在性"，而它真正走的是**第三套话**
#:       （`/v1/messages`）。那一套话 2026-10-12 做出来了（作者：「现在就补」）⇒
#:       于是这一条进了表：**地址照它文档写**（`https://api.anthropic.com/v1`
#:       ＋ `/messages`），而"量过"那一行**仍然空着**（那一家的口分不出 401 与 404，
#:       所以这一条**不是量出来的** —— 别把下面那些"量到 401"的成法照抄到它头上）。
#: 量到的那一刻与状态码都写在每一条上面（下一刀要复核，照着再打一次就行）。
#:
#: ⚠️ **`kind` 按量到的定**：DeepSeek **两条协议都答 200**（chat 与 responses 都实测过）⇒
#:    这里给它 `chat`（它家文档最常走的那条）；OpenAI 与"本机那一台"给 `responses`。
#: ⚠️ **模型名留空是有意的**（除了 DeepSeek 与本地那一条）：一家的模型名它自己会变
#:    ⇒ **让「获取模型列表」去填**（那一路会把上游报的模型名 ／ 窗口 ／ 推理强度一起带回来）。
#: ⚠️ **名字一律 ASCII**（字母数字开头，其余 `-` 或 `_`）—— 那是 `add()` 的规矩，
#:   也是**真出事过的一处**（2026-10-05 作者）：预设里原先写着「智谱 GLM」（中文 ＋ 空格），
#:   而页上点它、存它 ⇒ `400 档案名字不合规` —— **预设给了个存不进去的名字**。
#:   ⚠️ 这一处是典型的"左右脑互博"：能选，却存不了 ⇒ 名字这一层**只有一套规矩**。
#: ⚠️ `secret` 那一格是**变量名**（值在 `data/engine.env` 里，本模块不回显）。
PRESETS: tuple[Profile, ...] = (
    Profile(name="ChatGPT-Subscription", kind="responses", auth="chatgpt",
            base_url="https://api.openai.com/v1"),
    # Official Coding Plan endpoints. These plans STILL require a platform API key;
    # they are not ChatGPT OAuth and must not receive ChatGPT credentials.
    Profile(name="GLM-CodingPlan", kind="chat",
            base_url="https://open.bigmodel.cn/api/coding/paas/v4"),
    Profile(name="ZAI-CodingPlan", kind="chat",
            base_url="https://api.z.ai/api/coding/paas/v4"),
    # ── 国外 ────────────────────────────────────────────────────────────────
    Profile(name="OpenAI", kind="responses", base_url="https://api.openai.com/v1",
            secret="OPENAI_API_KEY"),                       # 量到 403（地区限制）
    # ★ **2026-10-12**：第三套话（`/v1/messages`）做出来之后补的这一条。
    #   ⚠️ **它不是量出来的**（那一家的口分不出 401 与 404 —— 见上面那一段）；
    #   模型名**照旧留空** ⇒ 让「获取模型列表」（`GET <地址>/models`）去填。
    Profile(name="Anthropic", kind="anthropic", base_url="https://api.anthropic.com/v1",
            secret="ANTHROPIC_API_KEY"),
    Profile(name="Gemini", kind="chat",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            secret="GEMINI_API_KEY"),                       # 量到 400（"model is not specified"）
    Profile(name="xAI-Grok", kind="chat", base_url="https://api.x.ai/v1",
            secret="XAI_API_KEY"),                          # 量到 401
    Profile(name="Mistral", kind="chat", base_url="https://api.mistral.ai/v1",
            secret="MISTRAL_API_KEY"),                      # 量到 401
    Profile(name="Groq", kind="chat", base_url="https://api.groq.com/openai/v1",
            secret="GROQ_API_KEY"),                         # 量到 403
    Profile(name="Together", kind="chat", base_url="https://api.together.xyz/v1",
            secret="TOGETHER_API_KEY"),                     # 量到 403
    Profile(name="Fireworks", kind="chat", base_url="https://api.fireworks.ai/inference/v1",
            secret="FIREWORKS_API_KEY"),                    # 量到 401
    Profile(name="DeepInfra", kind="chat", base_url="https://api.deepinfra.com/v1/openai",
            secret="DEEPINFRA_API_KEY"),                    # 量到 200
    Profile(name="Cerebras", kind="chat", base_url="https://api.cerebras.ai/v1",
            secret="CEREBRAS_API_KEY"),                     # 量到 403
    Profile(name="NVIDIA-NIM", kind="chat", base_url="https://integrate.api.nvidia.com/v1",
            secret="NVIDIA_API_KEY"),                       # 量到 200
    Profile(name="OpenRouter", kind="chat", base_url="https://openrouter.ai/api/v1",
            secret="OPENROUTER_API_KEY"),                   # 量到 200
    Profile(name="Perplexity", kind="chat", base_url="https://api.perplexity.ai",
            secret="PERPLEXITY_API_KEY"),                   # 量到 401（`/chat/completions`）
    # ── 国内 ────────────────────────────────────────────────────────────────
    Profile(name="DeepSeek", kind="chat", base_url="https://api.deepseek.com/v1",
            model="deepseek-chat", secret="DEEPSEEK_API_KEY",   # 量到 401；最小请求 200
            # **这一家的档位是量到的**：它自己的 `/models` 报
            #    `effort: {supported_levels: ["low","high","max"], default_level: "high"}`
            #    （2026-10-04 实测）⇒ 照它声明（不开放）。
            efforts=("low", "high", "max")),
    Profile(name="Zhipu-GLM", kind="chat", base_url="https://open.bigmodel.cn/api/paas/v4",
            secret="ZHIPU_API_KEY"),                        # 量到 401
    Profile(name="Kimi", kind="chat", base_url="https://api.moonshot.cn/v1",
            secret="MOONSHOT_API_KEY"),                     # 量到 401
    Profile(name="Tongyi", kind="chat",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            secret="DASHSCOPE_API_KEY"),                    # 量到 401
    Profile(name="Volcengine", kind="chat",
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            secret="ARK_API_KEY"),                          # 量到 401
    Profile(name="Qianfan", kind="chat", base_url="https://qianfan.baidubce.com/v2",
            secret="QIANFAN_API_KEY"),                      # 量到 403
    Profile(name="Hunyuan", kind="chat", base_url="https://api.hunyuan.cloud.tencent.com/v1",
            secret="HUNYUAN_API_KEY"),                      # 量到 401
    Profile(name="MiniMax", kind="chat", base_url="https://api.minimax.chat/v1",
            secret="MINIMAX_API_KEY"),                      # 量到 401
    Profile(name="StepFun", kind="chat", base_url="https://api.stepfun.com/v1",
            secret="STEPFUN_API_KEY"),                      # 量到 401
    Profile(name="Spark", kind="chat", base_url="https://spark-api-open.xf-yun.com/v1",
            secret="SPARK_API_KEY"),                        # 量到 401
    Profile(name="SiliconFlow", kind="chat", base_url="https://api.siliconflow.cn/v1",
            secret="SILICONFLOW_API_KEY"),                  # 量到 401
    Profile(name="Baichuan", kind="chat", base_url="https://api.baichuan-ai.com/v1",
            secret="BAICHUAN_API_KEY"),                     # 量到 401
    Profile(name="SenseNova", kind="chat", base_url="https://api.sensenova.cn/compatible-mode/v1",
            secret="SENSENOVA_API_KEY"),                    # 量到 401（`/chat/completions`）
    # ── 本机那几台（设计稿 裁定 2 点过名的端口；量的时候没起来，地址照旧列着）──────
    Profile(name="Local", kind="responses",
            base_url=DEFAULTS.base_url, # 引用默认那一份，不写死
            model=DEFAULTS.model, secret=""),               # 量到 200（本机在跑的那台）
    Profile(name="Local-vLLM", kind="chat", base_url="http://127.0.0.1:8000/v1",
            secret=""),                                     # 量的时候没起来（地址照旧）
    Profile(name="Local-Ollama", kind="chat", base_url="http://127.0.0.1:11434/v1",
            secret=""),                                     # 同上
    Profile(name="Local-LMStudio", kind="chat", base_url="http://127.0.0.1:1234/v1",
            secret=""),                                     # 同上
    Profile(name="Custom", kind="chat", base_url="", secret="TELYIN_API_KEY"),
)


def preset_of(name: str) -> Profile | None:
    """按名字找那一条预设（页上"填了名字就带出地址"用的就是它）。

    ⚠️ **忽略末尾那个 `-数字`**（2026-10-05，与页上同一条规矩 —— 而正本在这一处）：
       「新建实例」会给一个还没被用过的名字（`DeepSeek-2` ／ `DeepSeek-3` …，为的是别同名
       覆盖手上那一条）⇒ 而 `DeepSeek-2` 与预设 `DeepSeek` 对不上时，**供应商那一层就整层失效**
       （作者撞过一次：地址 ／ 模型 ／ 上下文上限全落回"本机那台"）。
    """
    wanted = str(name or "").strip().lower()
    for one in PRESETS:
        if one.name.lower() == wanted:
            return one
    spare = wanted.replace("-", "").rstrip("0123456789")
    for one in PRESETS:
        if one.name.lower().replace("-", "").rstrip("0123456789") == spare and spare:
            return one
    return None


def check_a_profile(one: Profile) -> None:
    """ **一处校验**（2026-10-05 第 3 步，照参考 `configuration.ts::validateSpec`）。

    ⚠️⚠️ **为什么收成一处**：从前页上**逐格**各判各的（必填 ／ 范围 ／ 正整数），而服务这一层
       只判名字与协议 ⇒ 于是"页上过得去、盘上拒收"的话在两处各说一半，作者两边都看得到
       （"这不是左右脑互搏吗"）。⇒ 规矩写在**这一处**，页上那几处零散判断删掉，
       错由这里说清、原样交给页（`EngineError` ⇒ 400 ⇒ 页上那一行）。
    ⚠️ 每一条**都配一句人话**（说清是哪一格、要什么），不用英文错误码。
    """
    if not is_a_good_name(one.name):
        raise EngineError(f"档案名字不合规：{one.name!r}"
                          "（字母或数字开头，其余 `-` ／ `_`）")
    if one.auth not in ("api_key", "chatgpt"):
        raise EngineError("认证方式只认 API Key 或 ChatGPT 订阅")
    if one.auth == "chatgpt":
        if one.kind != "responses" or one.base_url.rstrip("/") != "https://api.openai.com/v1":
            raise EngineError("ChatGPT 订阅只能使用 OpenAI 官方 Responses 接口")
    if one.kind not in ("chat", "responses", "anthropic"):
        raise EngineError(f"协议只认 `chat` ／ `responses` ／ `anthropic`，拿到的是 {one.kind!r}")
    if not str(one.model).strip():
        raise EngineError("模型不能为空 —— 这一格是必填（探活与对话都拿它出去）")
    if (not isinstance(one.display_name, str) or len(one.display_name) > 80
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in one.display_name)):
        raise EngineError("实例名称须为80字以内的单行文字")
    if not isinstance(one.thinking, bool):
        raise EngineError(f"思维链只能是开 ／ 关，拿到的是 {one.thinking!r}")
    for field_name, got in (("最大输出 token", one.max_tokens),
                            ("上下文上限", one.context_window)):
        # ⚠️ **没写下过就不判**（`None` ＝ 用默认那一份 —— 参考那边这两格也是**可选**的：
        #    `contextWindow?` 的注释写着"两者均未知时，Core 不按窗口大小裁剪上下文"）。
        #    给了才要正整数（布尔不算 —— `True` 是 `int` 的坑）。
        if got is None:
            continue
        if isinstance(got, bool) or not isinstance(got, int) or got <= 0:
            raise EngineError(f"{field_name}必须是正整数，拿到的是 {got!r}")
    budget = one.reasoning_budget_tokens
    if budget is not None:
        if type(budget) is not int or budget <= 0:
            raise EngineError("思考预算必须为正整数；留空使用上游默认")
        if not one.thinking:
            raise EngineError("思维链关闭时不能设置思考预算")
        if budget >= (one.max_tokens if one.max_tokens is not None else DEFAULTS.max_tokens):
            raise EngineError("思考预算必须小于总输出上限，给最终回答保留空间")
        from urllib.parse import urlsplit
        url = urlsplit(one.base_url or "")
        if not (url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")
                and url.port == 47831 and url.path.rstrip("/") == "/v1"
                and not url.username and not url.password and not url.query and not url.fragment):
            raise EngineError("独立思考预算当前仅支持本机 Strata 网关（47831/v1）")
    if one.temperature is not None:
        if isinstance(one.temperature, bool) or not isinstance(one.temperature, (int, float)) \
                or not 0 <= float(one.temperature) <= 2:
            raise EngineError(f"温度要在 0 到 2 之间，拿到的是 {one.temperature!r}")
    if one.reasoning_effort is not None and str(one.reasoning_effort).strip() != "":
        if not one.thinking:
            raise EngineError("没开思维链就不该带推理强度 —— 要么把它开上，要么把那格清掉")
        declared = preset_of(one.name)
        tiers = list(declared.efforts) if declared is not None else []
        if tiers and str(one.reasoning_effort) not in tiers:
            # 参考那一句：**声明了 `reasoningTiers` 就必须命中表内值**（开放的家不拦）。
            raise EngineError(f"{declared.name if declared else one.name} 只认这几档推理强度："
                              + " ／ ".join(tiers) + f"，拿到的是 {one.reasoning_effort!r}")


def _default_for(key: str) -> Any:
    """这个键的默认值 —— **能读 `Config` 的就读它**，只有那两个新字段读 `EXTRA_DEFAULTS`。"""
    if hasattr(DEFAULTS, key):
        return getattr(DEFAULTS, key)
    return EXTRA_DEFAULTS[key]


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _as_int(value: Any, *, key: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise EngineError(f"{key} 得是一个整数，拿到的是 {value!r}") from exc


def _as_float(value: Any, *, key: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise EngineError(f"{key} 得是一个数，拿到的是 {value!r}") from exc


def _as_bool(value: Any) -> bool:
    """ 与 `config.py` 那一套**同一个判法**（`not in ("0","false","off","no")`）。"""
    if isinstance(value, bool):
        return value
    text = _as_text(value).strip().lower()
    return text not in ("0", "false", "off", "no", "")


def _coerce(key: str, value: Any) -> Any:
    #: ⚠️ **`None` 就是"没写下过"** —— 温度与推理强度那两格靠它区分
    #:   "没填"与"填了 0"（前者不发出去，后者要发）。
    if value is None:
        return None
    if key in ("max_tokens", "context_window", "reasoning_budget_tokens"):
        return _as_int(value, key=key)
    if key == "thinking":
        return _as_bool(value)
    if key == "temperature":
        return _as_float(value, key=key)
    return _as_text(value)


@contextmanager
def _config_lock(path: Path):
    """Stable sidecar inode: replacing engine.json must not replace its lock.

    OS locks coordinate separate processes and independent handles in this process.
    Never unlink the sidecar: waiters may already hold handles to it.
    """
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+b") as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + 10
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise EngineError("engine.json lock unavailable; retry later") from exc
                time.sleep(0.01)
        try:
            yield
        finally:
            import sys
            primary = sys.exception()
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError as cleanup:
                if primary is None:
                    raise
                primary.lock_cleanup_error = cleanup


def _serialized(method):
    """Readers and mutators share the same per-instance reentrant lock."""
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._state_lock:
            try:
                return method(self, *args, **kwargs)
            except Exception as exc:
                if method.__name__ == "save":
                    try:
                        self._discard_rejected_draft()
                    except BaseException as cleanup:
                        exc.draft_cleanup_error = cleanup
                    # Direct update()+save() callers can retain the rejected
                    # submission without leaving it in the effective state.
                    exc.rejected_draft = self._rejected_draft
                raise
    return call


def _transaction(method):
    """An entire public edit-and-save operation, not just its disk commit.

    Failed requests retain an isolated diagnostic draft, never a live draft.
    The caller already owns its submitted input; retry requires an explicit
    new request after reviewing the loaded winning state, not an auto replay.
    """
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._state_lock:
            try:
                return method(self, *args, **kwargs)
            except Exception as primary:
                try:
                    self._discard_rejected_draft()
                except BaseException as cleanup:
                    primary.draft_cleanup_error = cleanup
                raise
    return call


class Engine:
    """那一层的全部读写 —— **一个入口，两件事同源**（写文件与更新活视图是一件事）。

    ⚠️ 它**不拼别的路径**：`root` 递进来（`data/`），两个文件名**只在这里出现一次**。
    """

    def __init__(self, root: Path, *, env: Mapping[str, str] | None = None,
                 state_root: Path | None = None) -> None:
        self._state_lock = threading.RLock()
        self._chatgpt_direct_scope = None
        self._context_capacity = None
        self._context_capacities = {}
        self._rejected_draft: str | None = None
        self.root = Path(root)
        self._bot_state = state_root is not None
        self.state_root = self.root if state_root is None else Path(state_root)
        # The established baseline retains its existing current field in place.
        # Other runtimes share profiles/keys, with their own selection sidecar.
        self.selection_path = (self.state_root / 'engine-selection.json'
                               if self.state_root != self.root else None)
        self._catalog_current = None
        self._selection_revision = ''
        self._catalog_depth = 0
        self._env = os.environ if env is None else env
        self._profiles: dict[str, Profile] = {}
        self._current: str | None = None
        self._read_error: str | None = None
        self._binding_revision = ""
        #: **加载时读到几条**（`save()` 第二条守卫比的是它 —— 见那一段）。
        #: ⚠️ 不许比"手上现在有几条"：删最后一条时手上是 0、而盘上还没写 ⇒ 那会**误伤**
        #: 一次正常删除（2026-10-05 作者点出来的："没法全部删除"）。
        self._loaded_count = 0
        #: 盘上那一份的指纹（`mtime_ns` ＋ 大小）—— **变了就重读**（见 `_refresh`）。
        self._fingerprint: tuple | None = None
        self._load()
        self._load_selection()
        self._fingerprint = self._fingerprint_now()
        self._remember_loaded()

    # ── 那两个文件 ────────────────────────────────────────────────────────────
    def _fresh(self):
        return Engine(self.root, env=self._env,
                      state_root=self.state_root if self._bot_state else None)

    @contextmanager
    def _catalog_guard(self):
        # Every model writer uses the existing catalog lock. Nested captures on
        # the same state-locked Engine must not lock a second native handle.
        if self._catalog_depth:
            yield
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with _config_lock(self.config_path.with_name(self.config_path.name + '.lock')):
            self._catalog_depth += 1
            try:
                yield
            finally:
                self._catalog_depth -= 1

    def _load_selection(self):
        self._selection_revision = ''
        if self.selection_path is None:
            return
        self._current = None  # Missing state never adopts the baseline's choice.
        if not self.selection_path.exists():
            return
        try:
            box = json.loads(self.selection_path.read_text(encoding='utf-8'))
            if (type(box) is not dict or set(box) != {'version', 'current', 'revision'}
                    or box['version'] != 1 or type(box['version']) is not int
                    or (box['current'] is not None and not isinstance(box['current'], str))
                    or not isinstance(box['revision'], str)
                    or re.fullmatch(r'[0-9a-f]{32}', box['revision']) is None):
                raise ValueError()
            self._selection_revision = box['revision']
            # Deleting a shared profile leaves affected runtimes unselected;
            # it must never silently switch them to another model/account.
            if box['current'] in self._profiles:
                self._current = box['current']
        except (OSError, ValueError):
            self._read_error = 'Bot model selection is unreadable; not overwritten'

    def local_control_snapshot(self):
        with self._web_read():
            return {'current': self._current,
                    'profiles': {name: row.as_dict() for name, row in self._profiles.items()}}

    def model_proxy_settings(self, asked=None):
        from .model_proxy import read_settings, save_settings, ProxySettingsError, ProxySettingsConflict
        try:
            return {"ok": True, "status": 200, "settings":
                    read_settings(self.root) if asked is None else save_settings(self.root, asked)}
        except ProxySettingsConflict as exc:
            return {"ok": False, "status": 409, "error": str(exc)}
        except ProxySettingsError as exc:
            return {"ok": False, "status": 400 if asked is not None else 503, "error": str(exc)}
        except OSError:
            return {"ok": False, "status": 503, "error": "无法读取或保存模型代理设置"}

    def proxy_for(self, url):
        from .model_proxy import proxy_for
        return proxy_for(self.root, url)

    @property
    def config_path(self) -> Path:
        return self.root / "engine.json"

    @property
    def secret_path(self) -> Path:
        return self.root / "engine.env"

    def chatgpt(self, name: str):
        from .chatgpt_subscription import subscription
        return subscription(self.root, name, owner_root=self.state_root)

    def chatgpt_scope(self):
        from .chatgpt_subscription import LoginScope
        return LoginScope()

    def close_chatgpt(self, *, timeout=10.0):
        """Drain this Engine's non-Web commands only; WebServices own separate scopes."""
        with self._state_lock:
            if self._chatgpt_direct_scope is None:
                self._chatgpt_direct_scope = self.chatgpt_scope()
            scope = self._chatgpt_direct_scope
        return scope.close(timeout=timeout)

    def chatgpt_command(self, asked: Mapping[str, Any], *, scope=None) -> dict[str, Any]:
        from .chatgpt_subscription import SubscriptionError
        try:
            manager = self.chatgpt(str(asked.get("name") or ""))
            action = asked.get("action", "status")
            if action not in ("status", "start", "cancel", "logout", "models"):
                raise SubscriptionError("不认识这个 ChatGPT 登录操作")
            if scope is None:
                with self._state_lock:
                    if self._chatgpt_direct_scope is None:
                        self._chatgpt_direct_scope = self.chatgpt_scope()
                    scope = self._chatgpt_direct_scope
            return {"code": "ok", "data": scope.command(manager, action)}
        except (SubscriptionError, OSError):
            # Do not reflect OS paths, tokens, upstream bodies or authorization URLs.
            import sys
            exc = sys.exception()
            return {"code": "error", "error": str(exc) if isinstance(exc, SubscriptionError)
                    else "无法读写本机 ChatGPT 登录信息"}

    @contextmanager
    def _web_read(self):
        """One committed read boundary; no network/refresh while holding it."""
        with self._state_lock:
            if not self.root.is_dir():
                raise EngineError("engine snapshot unavailable")
            if (self._profiles != self._base_profiles or self._current != self._base_current
                    or self._touched_profiles or self._current_touched):
                raise EngineConflict("uncommitted engine draft; snapshot unavailable")
            with self._catalog_guard(), _config_lock(self.root / "model-proxy.lock"):
                self._refresh()
                if self._read_error:
                    raise EngineError("engine snapshot unavailable")
                def stamps():
                    result = []
                    paths = (self.config_path, self.secret_path, self.root / "model-proxy.json")
                    for path in paths + ((self.selection_path,) if self.selection_path is not None else ()):
                        try:
                            stat = path.stat()
                            result.append((stat.st_ino, stat.st_mtime_ns, stat.st_size))
                        except FileNotFoundError:
                            result.append(None)
                    return tuple(result)
                before, environment = stamps(), dict(self._env)
                previous = getattr(self, "_capturing_request", False)
                self._capturing_request = True
                try:
                    yield
                    if (before != stamps() or environment != dict(self._env)
                            or self._fingerprint_now() != self._fingerprint):
                        raise EngineConflict("engine snapshot changed during capture")
                finally:
                    self._capturing_request = previous

    def web_report_snapshot(self) -> dict[str, Any]:
        """Public report only: no headers, token values, handles or key material."""
        from .web import ENGINE_BOUNDARY_LINE
        with self._web_read():
            profiles = self.profiles()
            keys = self.effective_all()
            report = {
                "profiles": [row.as_dict() for row in profiles],
                "current": self._current,
                "keys": keys,
                "defaults": {key: _default_for(key) for key in KEYS},
                "capacities": self.capacity_reports(),
                "capacity_status": self.capacity_reports().get(self._current, {}).get("status", "not_checked"),
                "availability": {row.name: bool(row.model.strip()) for row in profiles},
                "secret": self.secret_sources(),  # Variable names and source labels only.
                "can_probe": bool(str(keys["base_url"]["value"] or "").strip()
                                  and str(keys["model"]["value"] or "").strip()),
                "boundary": ENGINE_BOUNDARY_LINE,
                "presets": [row.as_dict() for row in self.presets_now()],
            }
        return report

    def _web_probe_target(self, asked: Mapping[str, Any], *, subscription=False):
        """Called only inside _web_read; caller's exact target is not replaced."""
        from urllib.parse import urlsplit
        if not isinstance(asked, Mapping):
            raise EngineError("invalid probe request")
        defaults = {"name": "", "mode": "minimal", "auth": "api_key",
                    "kind": "responses", "model": "", "base_url": ""}
        target = {}
        for key, default in defaults.items():
            value = asked.get(key, default)
            if not isinstance(value, str):
                raise EngineError("invalid probe target")
            target[key] = value or default
        if (target["mode"] not in ("minimal", "models", "connection")
                or target["kind"] not in ("responses", "chat", "anthropic")
                or target["auth"] != ("chatgpt" if subscription else "api_key")
                or (target["mode"] == "minimal" and not target["model"].strip())):
            raise EngineError("invalid probe target")
        try:
            url = urlsplit(target["base_url"])
            if (url.scheme not in ("http", "https") or not url.hostname
                    or url.username is not None or url.password is not None
                    or url.query or url.fragment
                    or any(c.isspace() or ord(c) < 32 for c in target["base_url"])):
                raise ValueError()
            url.port
        except ValueError:
            raise EngineError("invalid probe URL") from None
        name = target["name"]
        if name and not is_a_good_name(name):
            raise EngineError("invalid probe profile")
        profile = self._profiles.get(name)
        if subscription and profile is not None:
            if (profile.auth != target["auth"] or profile.kind != target["kind"]
                    or profile.base_url != target["base_url"]):
                raise EngineConflict("probe target differs from saved profile")
        if subscription and not name:
            raise EngineError("请先填写订阅实例名称，并在该实例中登录 ChatGPT")
        return target, profile, url.hostname

    def web_probe_snapshot(self, asked: Mapping[str, Any]) -> dict[str, Any]:
        """Private wire snapshot; D must never serialize/log this return value."""
        asked = dict(asked)
        with self._web_read():
            target, profile, host = self._web_probe_target(asked)
            key, slot = asked.get("api_key", ""), asked.get("secret_name", "")
            if not isinstance(key, str) or not isinstance(slot, str):
                raise EngineError("invalid probe authentication")
            if profile is not None:
                if slot != profile.secret or any(row.name != profile.name and row.secret == slot
                                                for row in self._profiles.values() if slot):
                    raise EngineConflict("probe secret ownership changed")
                if not key.strip() and slot:
                    if (profile.auth != target["auth"] or profile.kind != target["kind"]
                            or profile.base_url != target["base_url"]):
                        raise EngineConflict("changed probe endpoint requires an explicit API key")
                    key = str(self._env.get(slot, "")).strip() or self._read_secret_lines().get(slot, "")
            elif slot and any(row.secret == slot for row in self._profiles.values()):
                raise EngineConflict("draft cannot borrow a saved profile secret")
            if "\r" in key or "\n" in key:
                raise EngineError("invalid probe authentication")
            key = key.strip()
            if not key and host not in ("127.0.0.1", "localhost", "::1"):
                raise EngineError("remote probe credentials missing")
            proxy = self.proxy_for(target["base_url"])
            snapshot = {**target, "api_key": key, "proxy": proxy,
                        "secret_name": slot, "generation": uuid.uuid4().hex}
        return snapshot

    def web_subscription_probe(self, asked: Mapping[str, Any], *, scope=None) -> dict[str, Any]:
        from .chatgpt_subscription import LoginScope
        try:
            if not isinstance(scope, LoginScope):
                raise EngineError("Web subscription scope required")
            # Covers refresh, model lookup, streaming and all cleanup. The
            # enclosing HTTP lease remains a separate WebService responsibility.
            with scope.request_activity():
                return self._web_subscription_probe_owned(asked, scope=scope)
        except Exception:
            return {"ok": False, "status": None, "models": None,
                    "error": "订阅探测未成功完成，未自动重试", "url": ""}

    def _web_subscription_probe_owned(self, asked: Mapping[str, Any], *, scope=None) -> dict[str, Any]:
        """Admitted Web lease owns this operation; never use Engine's direct scope.

        The real Subscription refresh/store/identity checks remain in use. A
        shallow request view changes ONLY its client factory, sharing the real
        manager's account paths and locks. No shared factory is mutated.
        """
        from copy import copy
        from .chatgpt_subscription import (API_BASE, ISSUER, LoginScope, Subscription,
                                          SubscriptionError, subscription_body)
        import asyncio
        import httpx
        from contextlib import AsyncExitStack, aclosing
        from .generate import build_body
        from .stream import iter_sse_events
        from .subscription_stream import subscription_events
        from .transport import LLMError, stream_failure
        from .transport import HttpxTransport
        result = {"ok": False, "status": None, "models": None, "error": None, "url": ""}
        try:
            if not isinstance(scope, LoginScope):
                raise EngineError("Web subscription scope required")
            asked = dict(asked)
            with self._web_read():
                target, _, _host = self._web_probe_target(asked, subscription=True)
                if target["kind"] != "responses" or target["base_url"] != API_BASE:
                    raise EngineError("subscription target must be the official endpoint")
                manager = self.chatgpt(target["name"])
                api_proxy, auth_proxy = self.proxy_for(API_BASE), self.proxy_for(ISSUER)
                # The current model-proxy policy assigns both to OpenAI. Refuse
                # a future policy split until the refresh client contract grows.
                if api_proxy != auth_proxy:
                    raise EngineError("subscription proxy policy requires review")
                config = replace(DEFAULTS, base_url=API_BASE, model=target["model"],
                                 max_tokens=16, thinking=False, retry_delays_ms=(), tools=())
            if type(manager) is not Subscription:
                raise EngineError("unsupported subscription manager")
            scope.register(manager)
            def admitted():
                if scope.shutdown_status()["state"] != "open":
                    raise SubscriptionError("subscription Web scope is stopping")
            def client_factory():
                admitted()
                return httpx.Client(proxy=auth_proxy, trust_env=False, follow_redirects=False,
                                    timeout=15, headers={"User-Agent": "TelyinCore-ChatGPT/0.1"},
                                    event_hooks={"request": [lambda request: admitted()]})
            with manager._lock:
                bound = copy(manager)
                bound._client_factory = client_factory
            admitted()
            if target["mode"] in ("models", "connection"):
                result["url"] = API_BASE + "/models"
                catalog = bound.models()
                if target["mode"] == "models":
                    return catalog
                return {**result, "ok": True, "status": catalog["status"]}
            result["url"] = API_BASE + "/responses"
            # Real refresh finishes before HTTP generation; failure is never an
            # anonymous fallback. Do not reacquire a current-profile token later.
            token = bound.access_token()
            admitted()
            headers = {"Authorization": "Bearer " + token}
            shaped = subscription_body(build_body(config, [{"role": "user", "content": "Reply with OK only."}]), API_BASE)
            async def run():
                def frozen_proxy(url):
                    admitted()
                    if url != API_BASE + "/responses":
                        raise EngineError("subscription probe target changed")
                    return api_proxy
                transport = HttpxTransport(proxy_of=frozen_proxy, request_check=admitted)
                async with AsyncExitStack() as stack:
                    stack.push_async_callback(transport.aclose)
                    upstream = transport.stream_chat(API_BASE, shaped, frame_idle_ms=15000,
                                                      request_headers=headers)
                    await stack.enter_async_context(aclosing(upstream))
                    normalized = subscription_events(upstream)
                    await stack.enter_async_context(aclosing(normalized))
                    async with asyncio.timeout(25):
                        async for event in iter_sse_events(normalized):
                            if event.get("type") == "response.completed":
                                return event.get("response", {}).get("status") == "completed"
                            if event.get("type") == "error":
                                raise stream_failure(event.get("error") or event)
                            if event.get("type") == "response.failed":
                                raise stream_failure((event.get("response") or {}).get("error"))
                            if event.get("type") == "response.incomplete":
                                return False
                return False  # EOF is never completed.
            completed = asyncio.run(run())
            result.update(ok=completed, status=200,
                          error=None if completed else "订阅探测未收到成功终态")
        except LLMError as error:
            from .transport import SUBSCRIPTION_LIMIT, SUBSCRIPTION_LIMIT_HINT
            result.update(ok=False, status=error.status, error=SUBSCRIPTION_LIMIT_HINT
                          if error.code == SUBSCRIPTION_LIMIT else "订阅探测失败，请查看模型状态。")
        except Exception:
            # No upstream body, token, account, proxy details or paths in reports.
            result.update(ok=False, error="订阅探测未成功完成，未自动重试")
        return result


    def chatgpt_probe(self, asked: Mapping[str, Any]) -> dict[str, Any]:
        from .chatgpt_subscription import SubscriptionError, API_BASE
        import asyncio
        from .protocol import adapt
        from .stream import iter_sse_events
        from .transport import HttpxTransport, LLMError, stream_failure
        model = str(asked.get("model") or "").strip()
        result = {"ok": False, "status": None, "models": None, "error": None,
                  "url": API_BASE + "/responses"}
        try:
            if not model:
                raise SubscriptionError("请先获取并选择模型")
            manager = self.chatgpt(str(asked.get("name") or ""))
            async def run():
                transport = adapt(HttpxTransport(proxy_of=self.proxy_for), kind_of=lambda: "responses",
                    values_of=lambda: {"auth": "chatgpt", "_subscription": manager})
                stream = transport.stream_chat(API_BASE, {"model": model,
                    "input": [{"role": "user", "content": "Reply with OK only."}]}, frame_idle_ms=15000)
                try:
                    async with asyncio.timeout(25):
                        async for event in iter_sse_events(stream):
                            if event.get("type") == "response.completed":
                                response = event.get("response", {})
                                return response.get("status") == "completed"
                            if event.get("type") == "error":
                                raise stream_failure(event.get("error") or event)
                            if event.get("type") == "response.failed":
                                raise stream_failure((event.get("response") or {}).get("error"))
                            if event.get("type") == "response.incomplete":
                                return False
                    return False
                finally:
                    await stream.aclose()
                    await transport.aclose()
            completed = asyncio.run(run())
            result.update(ok=completed, status=200, error=None if completed else
                          "未收到成功完成事件；请检查订阅额度与该模型的访问权限")
        except LLMError as error:
            from .transport import SUBSCRIPTION_LIMIT, SUBSCRIPTION_LIMIT_HINT
            result.update(ok=False, status=error.status, error=SUBSCRIPTION_LIMIT_HINT
                          if error.code == SUBSCRIPTION_LIMIT else "订阅探测失败，请查看模型状态。")
        except SubscriptionError as exc:
            result["error"] = str(exc)
        except Exception:
            result["error"] = "订阅测试未成功完成，请检查网络、登录状态和模型权限"
        return result

    # ── 读 ────────────────────────────────────────────────────────────────────
    def _fingerprint_now(self) -> tuple | None:
        def stamp(path):
            try:
                stat = path.stat()
                return (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            except OSError:
                return None
        catalog = stamp(self.config_path)
        return (catalog, stamp(self.selection_path)) if self.selection_path is not None else catalog

    def _refresh(self) -> None:
        """ **盘上那一份变了就重读** —— 这是"手工改 `engine.json` ⇒ 下一次请求就换了脑"。

        ⚠️⚠️ 少了这一下，活视图只是"每次重算**内存里**那一份" ⇒ 从外面改盘**读不到**，
           而刀 2 的验收（"手工改 `data/engine.json` → 下一次请求就换了脑"）**做不到**。
        ⚠️ 判据是**指纹**（`mtime_ns` ＋ 大小）而不是"每次都读"：一次 `stat` 比一次
           `read ＋ json.loads` 便宜得多，而这一格会被每个键问一次。
        ⚠️ **它变了才清空重读** ⇒ 一份坏文件**不会**把上一次好读到的档案悄悄擦掉
           （读坏了就 `_read_error` 立起来，而那一份照旧拒写）。
        """
        if getattr(self, "_capturing_request", False):
            return
        # A read must not discard an uncommitted draft. save() reconciles it
        # against the newest disk state while holding the interprocess lock.
        if (self._profiles != self._base_profiles or self._current != self._base_current
                or self._touched_profiles or self._current_touched):
            return
        now = self._fingerprint_now()
        if now == self._fingerprint:
            return
        self._fingerprint = now
        previous = (self._profiles, self._current, self._binding_revision,
                    self._catalog_current, self._selection_revision)
        self._profiles, self._current, self._read_error = {}, None, None
        self._catalog_current = None
        if not self.config_path.exists() and previous[0]:
            self._read_error = "engine configuration unavailable"
        else:
            self._load()
            self._load_selection()
        if self._read_error is not None:
            (self._profiles, self._current, self._binding_revision,
             self._catalog_current, self._selection_revision) = previous
            return  # Last-good is display-only while reads remain unhealthy.

        self._loaded_count = len(self._profiles)
        self._remember_loaded()

    def _discard_rejected_draft(self) -> None:
        # Keep a detached copy for diagnostics; it is never re-applied or used
        # by effective()/LiveConfig. The original request remains caller-owned.
        if (self._profiles != self._base_profiles or self._current != self._base_current
                or self._touched_profiles or self._current_touched):
            self._rejected_draft = self._dump()
        latest = self._fresh()
        if latest._read_error is not None or (not latest.config_path.exists() and self._base_profiles):
            self._profiles = deepcopy(self._base_profiles)
            self._current = self._base_current
            self._read_error = latest._read_error or "engine configuration unavailable"
            self._fingerprint = latest._fingerprint
            self._remember_loaded()
            return
        self._profiles = latest._profiles
        self._current = latest._current
        self._read_error = latest._read_error
        self._binding_revision = latest._binding_revision
        self._catalog_current = latest._catalog_current
        self._selection_revision = latest._selection_revision
        self._fingerprint = latest._fingerprint
        self._loaded_count = latest._loaded_count
        self._remember_loaded()

    def _remember_loaded(self) -> None:
        self._base_profiles = deepcopy(self._profiles)
        self._base_current = self._current
        self._base_selection_revision = self._selection_revision
        self._touched_profiles: set[str] = set()
        self._current_touched = False

    def _load(self) -> None:
        """ 读不出来**不抛**：记下那句话，`profiles()` 照旧答（而**写盘会被拒**）。"""
        path = self.config_path
        self._binding_revision = ""
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._read_error = "engine configuration read failed: " + type(exc).__name__
            return
        if not isinstance(raw, dict):
            self._read_error = f"{path.name} 那一份不是对象"
            return
        revision = raw.get("binding_revision", "")
        if not isinstance(revision, str) or (revision and not re.fullmatch(r"[0-9a-f]{32}", revision)):
            self._read_error = "invalid engine binding revision"
            return
        self._binding_revision = revision
        rows = raw.get("profiles", {})
        if not isinstance(rows, dict) or any(not isinstance(name, str) or not isinstance(one, dict) for name, one in rows.items()):
            self._read_error = "invalid engine profiles structure"
            return
        try:
            profiles = {name: Profile.from_dict({**one, "name": name}) for name, one in rows.items()}
        except (TypeError, ValueError, KeyError):
            self._read_error = "invalid engine profile fields"
            return
        current = raw.get("current")
        if current is not None and (not isinstance(current, str) or current not in profiles):
            self._read_error = "invalid engine current profile"
            return
        self._profiles, self._current = profiles, current
        self._catalog_current = current

    @_serialized
    def read_error(self) -> str | None:
        """盘上那一份读不动时的那句话（没有就是 `None`）。"""
        self._refresh()
        return self._read_error

    @_serialized
    def profiles(self) -> tuple[Profile, ...]:
        self._refresh()
        return tuple(self._profiles[name] for name in sorted(self._profiles))

    @_serialized
    def profile(self, name: str) -> Profile:
        self._refresh()
        return self._draft_profile(name)

    def _draft_profile(self, name: str) -> Profile:
        # Mutators must retain their loaded baseline, not silently refresh away
        # a stale request before the locked conflict check can inspect it.
        got = self._profiles.get(str(name))
        if got is None:
            raise EngineError(f"没有叫 {name!r} 的那一条档案")
        return got

    @_serialized
    def current_name(self) -> str | None:
        self._refresh()
        return self._current

    @_serialized
    def current(self) -> Profile | None:
        return self._profiles[self._current] if self._current is not None else None

    # ── 来源三档（**每个键都答得出它从哪来**）──────────────────────────────────
    def _capacity_identity(self, profile=None):
        """Invalidates on endpoint/model/auth/account/key/policy changes; no secrets exported."""
        import hashlib
        one = profile if profile is not None else self.current()
        if one is None:
            return None
        def stamp(path):
            try:
                stat = path.stat()
                return (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            except FileNotFoundError:
                return None
        account = self.chatgpt(one.name).capacity_binding() if one.auth == "chatgpt" else None
        keys = {one.secret} if one.secret else set()
        if not self._bot_state:
            keys.update(ENV_NAMES[field] for field in ('base_url', 'model', 'context_window'))
        environment = hashlib.sha256(repr([(key, self._env.get(key)) for key in sorted(keys)]).encode()).hexdigest()
        return (one, stamp(self.secret_path), account, environment,
                self.proxy_for(self.effective("base_url", profile=one).value))

    def refresh_context_capacity(self):
        from .context_capacity import lookup, select_capacity
        import time
        with self._state_lock:
            identity = self._capacity_identity()
            if identity is None:
                return
            one = self.current()
            # Explicit overrides need no discovery request.
            if one.context_window is not None or (not self._bot_state and
                    str(self._env.get(ENV_NAMES["context_window"], "")).strip()):
                return
            cached = self._context_capacities.get(repr(identity))
            if cached and cached["identity"] == identity and time.monotonic() - cached["at"] < 60:
                return
            base = self.effective("base_url").value
            model = self.effective("model").value
        value = None
        http_status = None
        status = "not_reported"
        try:
            result = lookup(self, one, base, model)
            value = select_capacity(result, model)
            http_status = result.get("status")
            if not result.get("ok"):
                status = "lookup_failed"
            elif value is not None:
                status = "reported"
        except Exception as error:
            # Exception strings may contain credential-bearing URLs; export no raw error.
            status = "lookup_failed:" + type(error).__name__
            http_status = getattr(getattr(error, "response", None), "status_code", None)
        with self._state_lock:
            if self._capacity_identity() != identity:
                self._context_capacity = None
                raise EngineConflict("容量查询期间模型或认证配置已变更，请重试")
            return identity, dict(identity=identity, value=value, status=status, http_status=http_status,
                                  at=time.monotonic())

    def accept_context_capacity(self, result):
        if result is None:
            return
        identity, report = result
        with self._state_lock:
            if self._capacity_identity() != identity:
                raise EngineConflict("容量查询期间配置已变更，请重试")
            self._context_capacity = report
            self._context_capacities[repr(identity)] = report
            while len(self._context_capacities) > 32:
                del self._context_capacities[next(iter(self._context_capacities))]

    def capacity_reports(self):
        rows = {}
        for profile in self.profiles():
            cached = self._context_capacities.get(repr(self._capacity_identity(profile)))
            effective = self.effective("context_window", profile=profile)
            rows[profile.name] = {"model": profile.model, "value": effective.value,
                "source": effective.source, "status": cached["status"] if cached else "not_checked",
                "http_status": cached.get("http_status") if cached else None}
        return rows

    @_serialized
    def effective(self, key: str, *, profile: Profile | None = None) -> Sourced:
        """这个键**生效**的那一份 ＋ 它是从哪来的。

        次序**从低到高**：默认值 → 当前档案 ／ 递进来的那一条 → 环境变量
        （ 所以**先问环境变量**：它最高 —— 裁定 3 那条"三层不变、**环境变量最高**"）。
        ⚠️ **空串当没设**（与 `config.py` 的 `_env` 同一个判法）：档案里那个格子空着
        ⇒ 落回默认值，而**不是**把端点设成空串（那会变成一条打不通的路）。
        """
        if key not in KEYS:
            raise EngineError(f"不认这个键：{key!r}（只认 {list(KEYS)}）")
        self._refresh()
        env_name = ENV_NAMES[key]
        # In Bot launch mode, machine model overrides must not defeat a Bot's
        # explicit choice. API credential env slots remain shared with profiles.
        raw_env = None if self._bot_state else self._env.get(env_name)
        if raw_env is not None and raw_env.strip() != "":
            return Sourced(_coerce(key, raw_env), "env")
        one = profile if profile is not None else self.current()
        if one is not None:
            raw = getattr(one, key, None)
            if _as_text(raw).strip() != "":
                return Sourced(_coerce(key, raw), "file")
        if key == "context_window":
            cached = self._context_capacities.get(repr(self._capacity_identity(one)))
            if cached and cached["value"] is not None:
                return Sourced(cached["value"], "upstream_catalog")
            from .provider_capacity import documented_context
            capacity = documented_context(
                self.effective("base_url", profile=one).value,
                self.effective("model", profile=one).value)
            if capacity is not None:
                return Sourced(capacity, "provider_documentation")
            # User-selected planning fallback; never present it as reported capacity.
            return Sourced(131_072, "fallback")
        return Sourced(_default_for(key), "default")

    @_serialized
    def effective_all(self) -> dict[str, dict[str, Any]]:
        return {key: self.effective(key).as_dict() for key in KEYS}

    @_serialized
    def hard_tokens(self) -> int | None:
        """ 硬阈的分母是 `context_window`、出站额度是 `max_tokens` —— **两件事**。

        `hardTokens = max(0, context_window − max_tokens)`（`config.py:17-21` 那段注释警告过
        "混成一个 ⇒ 硬阈恒错"）。 它**现读** ⇒ 换一条小窗口的档案之后，这个数当场变小。
        """
        # **没有上下文上限 ⇒ 交回 `None`**（＝「不据此限制输入」，见交接层那句 ）——
        # ⚠️ `_as_int(None)` 会当场抛（「得是一个整数」） ⇒ 这一格先判 。
        now = self.effective("context_window").value
        if now is None:
            return None
        window = _as_int(now, key="context_window")
        out = _as_int(self.effective("max_tokens").value, key="max_tokens")
        return hard_input_tokens(window, out, self.effective("base_url").value)

    # ── 密钥（**只写不回显**）──────────────────────────────────────────────────
    @_serialized
    def secrets_on_disk(self) -> dict[str, str]:
        """盘上那几个**变量名**（⚠️ **没有值** —— 本模块不许把密钥读回来）。"""
        out: dict[str, str] = {}
        path = self.secret_path
        if not path.exists():
            return out
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return out
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, _value = line.partition("=")
            out[name.strip()] = "file" # 只报"配了 ＋ 从哪来"，**不报值**
        return out

    @_serialized
    def secret_source(self, name: str) -> str:
        """"那个变量名的密钥从哪来"：`env` ／ `file` ／ `missing`（ 三档，不含值）。"""
        if self._env.get(name, "").strip() != "":
            return "env"
        return "file" if name in self.secrets_on_disk() else "missing"

    @_serialized
    def write_secret(self, name: str, value: str) -> str:
        """Trusted direct slot management; never the profile-save JSON fallback.

        Uses the same cross-process lock as profile commits/conditional writes.
        This internal API does not supply an expectation on behalf of a client.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        with self._catalog_guard():
            return self._write_secret_locked(name, value)

    def _write_secret_locked(self, name: str, value: str) -> str:
        """写一条密钥 —— **先留一版**（盘上那份还在时，整份复制进 `.history-engine.env/`）。

        ⚠️ 校验与 `PersonaPort.save_text` 同一套次序：**先拦 → 再留一版 → 再写**。
        """
        if not NAME_OK.fullmatch(str(name)) or name in RESERVED:
            raise EngineError(f"密钥的变量名不合规：{name!r}")
        if not isinstance(value, str) or value.strip() == "":
            raise EngineError("密钥不许是空串（要清掉它，就写一个空值那一份 —— 见下面那句）")
        if value.splitlines() != [value]:
            raise EngineError("secret value must fit one env line")
        kept: dict[str, str] = {}
        path = self.secret_path
        if path.exists():
            kept = self._read_secret_lines()
            self._keep_a_version_of_secrets()
        kept[str(name)] = value
        self._write_text(path, "".join(f"{k}={v}\n" for k, v in sorted(kept.items())))
        return str(name)

    def _read_secret_lines(self) -> dict[str, str]:
        path = self.secret_path
        out: dict[str, str] = {}
        if not path.exists():
            return out
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            out[name.strip()] = value
        return out

    def _keep_a_version_of_secrets(self) -> Path:
        """把**现在这一份**整份留进历史目录（`newline=""` ⇒ 字节进、字节出）。"""
        history = self.root / ".history-engine.env"
        history.mkdir(parents=True, exist_ok=True)
        stamp = _a_stamp()
        target = history / f"engine-{stamp}-{uuid.uuid4().hex}.env"
        target.write_bytes(self.secret_path.read_bytes())
        return target

    # ── 写（档案那一侧）──────────────────────────────────────────────────────
    def _write_text(self, path: Path, text: str) -> None:
        """ **原子写**：先写同目录的唯一 `.tmp-*`，再 `os.replace` 换上去。

        ⚠️ 同目录是必须的（跨盘 `os.replace` 不原子）；`.tmp-*` 这个名字与参考那边
        `workspaceGit` 的忽略规则同一形（`*.tmp-*`）。
        """
        # mkstemp, unlike a PID-only suffix, is exclusive across same-process
        # concurrent callers too (including the engine.env caller).
        fd, name = tempfile.mkstemp(prefix=f"{path.name}.tmp-", dir=path.parent)
        tmp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            import sys
            primary = sys.exception()
            try:
                tmp.unlink(missing_ok=True)
            except OSError as cleanup:
                if primary is None:
                    raise
                primary.temporary_cleanup_error = cleanup

    def _dump(self) -> str:
        body = {
            "binding_revision": self._binding_revision,
            "current": self._catalog_current if self.selection_path is not None else self._current,
            "profiles": {name: one.as_dict() for name, one in sorted(self._profiles.items())},
        }
        return json.dumps(body, ensure_ascii=False, indent=2, sort_keys=False) + "\n"

    def _profiles_on_disk(self) -> int:
        """ **盘上那一份里有几条档案**（读不出来 ／ 文件不在 ⇒ `0`）—— `save()` 的第二条守卫用。"""
        try:
            one = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        rows = one.get("profiles") if isinstance(one, dict) else None
        return len(rows) if isinstance(rows, dict) else 0

    @_serialized
    def save(self) -> Path:
        """Commit a draft using a locked three-way merge, never a stale dump.

        Each profile and the current selection are conflict units. Independent
        profiles merge; competing edits/deletes of the same profile conflict
        (even disjoint fields). Equal results are idempotent. A conflict or I/O
        failure does not publish a successful commit. The public wrapper detaches
        conflict drafts, reloads the winner, and attaches the rejected JSON to
        EngineConflict.rejected_draft. Composite edit-and-save requests detach
        on any failure. Review the winner and submit an explicit new request;
        neither restarting the service nor automatically replaying is required.
        """
        if self._read_error is not None:
            raise EngineError(f"盘上那一份读不出来，不覆盖它：{self._read_error}")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.config_path
        with self._catalog_guard():
            latest = self._fresh()
            if latest._read_error is not None:
                raise EngineError(f"盘上那一份读不出来，不覆盖它：{latest._read_error}")
            # Compare loaded baseline -> local draft against baseline -> disk.
            # Absence is a value too, so deletes never revive unrelated rows.
            merged = dict(latest._profiles)
            for name in self._base_profiles.keys() | self._profiles.keys() | self._touched_profiles:
                base = self._base_profiles.get(name)
                local = self._profiles.get(name)
                remote = latest._profiles.get(name)
                # JSON arrays load as lists while directly constructed Profiles
                # may contain tuples. Compare the persisted representation.
                base_value = base.as_dict() if base is not None else None
                local_value = local.as_dict() if local is not None else None
                remote_value = remote.as_dict() if remote is not None else None
                if local_value == base_value and name not in self._touched_profiles:
                    continue
                if remote_value != base_value and remote_value != local_value:
                    raise EngineConflict(f"档案 {name!r} 已被其他写入者修改；重新加载后重试")
                if local is None:
                    merged.pop(name, None)
                else:
                    merged[name] = local
            current = latest._current
            if self._current_touched or self._current != self._base_current:
                if current != self._base_current and current != self._current:
                    raise EngineConflict("当前档案已被其他写入者切换；重新加载后重试")
                if (self.selection_path is not None
                        and latest._selection_revision != self._base_selection_revision
                        and current != self._current):
                    raise EngineConflict("当前 Bot 的模型选择已有并发修改；请刷新后重试")
                current = self._current
            if current is not None and current not in merged:
                raise EngineConflict("当前档案已被删除；重新加载后重试")
            # Independent additions can still claim the same secret variable.
            # Reject newly introduced pairs, without silently renaming a key.
            def collisions(rows):
                names = sorted(rows)
                return {(a, b) for i, a in enumerate(names) for b in names[i + 1:]
                        if rows[a].secret and rows[a].secret == rows[b].secret}
            if collisions(merged) - collisions(self._profiles) - collisions(latest._profiles):
                raise EngineConflict("档案密钥变量名发生并发冲突；重新加载后重试")
            catalog_write = (not path.exists() or merged != latest._profiles or bool(self._touched_profiles)
                             or (self.selection_path is None and current != latest._current))
            selection_write = (self.selection_path is not None
                               and (self._current_touched or current != latest._current))
            latest._profiles, latest._current = merged, current
            if latest._catalog_current not in merged:
                latest._catalog_current = None
            if catalog_write:
                latest._binding_revision = uuid.uuid4().hex
                self._write_text(path, latest._dump())
            if selection_write:
                revision = uuid.uuid4().hex
                try:
                    self.state_root.mkdir(parents=True, exist_ok=True)
                    self._write_text(self.selection_path, json.dumps(
                        {'version': 1, 'current': current, 'revision': revision},
                        ensure_ascii=False, indent=2) + '\n')
                except OSError as error:
                    # A shared profile commit may already be durable. Tell the
                    # truth; detach/reload the draft and never replay this save.
                    raise EngineError('共享模型档案已保存，但当前 Bot 选择保存失败；请刷新后重新选择'
                        if catalog_write else '当前 Bot 模型选择保存失败；原选择保留') from error
                latest._selection_revision = revision
            # Only publish the merged state/baseline after replace succeeded.
            self._profiles, self._current = merged, current
            self._binding_revision = latest._binding_revision
            self._catalog_current = latest._catalog_current
            self._selection_revision = latest._selection_revision
            self._loaded_count = len(merged)
            self._fingerprint = self._fingerprint_now()
            self._remember_loaded()
        return path

    @_serialized
    def add(self, profile: Profile, *, make_current: bool = False) -> Profile:
        check_a_profile(profile) # 一处校验（第 3 步）
        # **一个变量名最多归一条档案**（2026-10-05 作者量出来的那处）：
        #    从前三条档案可以**共用**同一个 `secret`（例如都用 `DEEPSEEK_API_KEY`）⇒
        # 于是"页上刚敲的那把 key"写进那**同一个**变量 ⇒ **谁后保存谁赢** ⇒
        # 另一条拿着**别人的钥匙**去问上游 ⇒ 「无效的令牌」（作者那一幕 ）。
        # 而**谁保住那个名字**有先后：**正主优先**（预设表里那条自己声明的名字 ）——
        # `DEEPSEEK_API_KEY` 归 `DeepSeek` ，**借用它的那些**（例如一条第三方中转 ）
        # 才该挪走 （推演过一次：不这么写，反而是**正主**被挪 ）。
        taken = {one.secret for name, one in self._profiles.items()
                 if name != profile.name and one.secret}
        mine = preset_of(profile.name)
        canonical = mine.secret if mine is not None else ""
        if not profile.secret or (profile.secret in taken and profile.secret != canonical):
            profile = replace(profile, secret=a_free_secret_name(profile.name, taken))
        if make_current and not profile.model.strip():
            raise EngineError("没有模型的档案不许设为当前")
        self._profiles[profile.name] = profile
        self._touched_profiles.add(profile.name)
        if make_current:
            self._current = profile.name
            self._current_touched = True
        return profile

    @_serialized
    def tidy_secret_names(self) -> dict[str, tuple[str, str]]:
        """ **一次全表归置**：一个变量名最多归一条档案（正主留名、借用者让步）。

        ⚠️ **它是一次迁移，不是每次保存都跑的东西**（2026-10-05 作者那两条互相顶出来的）：
           `add()` 只肯挪**自己** —— 而"谁该保住那个名字"要**看全表**才知道：
           **正主优先**（预设表里自己声明的名字 ，例如 `DEEPSEEK_API_KEY` 归 `DeepSeek` ），
           借用的那些（第三方中转 ）改成 `TELYIN_KEY_<档案名>` 。
         交回 `{档案名: (旧变量名, 新变量名)}` —— 只有**真的动了**的在里面 （没动的不报 ）。
        ⚠️ **它只改"哪一格"，不搬值**：旧变量里那把 key **还在** ⇒ 被挪的那条要**重填一次**
           （页上那一格 ）； 所以它是**一次显式动作**，不是保存的副作用 。
        """
        moving: dict[str, tuple[str, str]] = {}
        used: set[str] = set()
        # ① **正主先占座**（预设表里它自己声明的那个名字 ）
        for name, one in sorted(self._profiles.items()):
            mine = preset_of(name)
            if one.secret and mine is not None and one.secret == mine.secret:
                used.add(one.secret)
        # ② 其余的照序分（自己那个名字没人用就用它 ，被占了就给一个新的 ）
        for name, one in sorted(self._profiles.items()):
            mine = preset_of(name)
            # **只有"正主的那个名字"才许留着**（①已经替它占了座 ）—— ⚠️ 从前这里写的是
            # `one.secret in used` 就 `continue` ，于是**借用的那些也被当成自己的** ⇒
            # 全表一个都没挪（在副本上试出来的 ）。
            if one.secret and mine is not None and one.secret == mine.secret:
                continue
            want = one.secret if (one.secret and one.secret not in used) else \
                a_free_secret_name(name, used)
            used.add(want)
            if want != one.secret:
                self._profiles[name] = replace(one, secret=want)
                self._touched_profiles.add(name)
                moving[name] = (one.secret or "", want)
        return moving

    # ── JSON 级的两个入口（给"零同胞 import"的那一层用）─────────────────────
    @_serialized
    def from_json(self, one: Mapping[str, Any]) -> Profile:
        """把一份 dict 读成一条档案 —— ⚠️ **校验在这儿**（页那一层只管搬 JSON）。

         存在的理由：`web.py` **零同胞 import** ⇒ 它构造不出 `Profile`
          ⇒ 让它递一份 dict 进来，形状与校验都归这一层。
        """
        if not isinstance(one, Mapping):
            raise EngineError("一条档案得是一个对象")
        return Profile.from_dict(one)

    @_transaction
    def save_json(self, one: Mapping[str, Any], *, make_current: bool = False) -> Profile:
        """**收一份 dict ＋ 落盘**（一个动作，两件事同源：改内存 ＋ 写文件）。

        ⚠️ 它**不做"改现有那一条"的合并**：那件事由 `update()` 说清了（而 `kind` 不许改）。
        """
        got = self.from_json(one)
        check_a_profile(got)
        # Immediate save-time error as well as per-round revalidation after reload.
        from .strata_follow import context_policy, endpoint, read_native, capabilities
        identity = (got.name, got.base_url, got.model)
        follow = context_policy(self.state_root, identity)
        if follow and follow.get("context_window"):
            try:
                cap = capabilities(read_native(endpoint(got.base_url)), got.model,
                                   {"context_window": True})["context_window"]
            except Exception as exc:
                raise EngineError("无法确认 Strata 当前容量，未保存；请确认模型已就绪") from exc
            if got.context_window is not None and got.context_window > cap:
                raise EngineError(f"上下文上限 {got.context_window} 超出上游当前容量 {cap}；请调小或留空")
            window = cap if got.context_window is None else got.context_window
            if got.max_tokens is not None and got.max_tokens >= window:
                raise EngineError("总输出上限必须小于有效上下文容量")
        got = self.add(got, make_current=make_current)
        self.save()
        return self._profiles[got.name]

    @_serialized
    def apply_json(self, asked: Mapping[str, Any]) -> dict[str, Any]:
        """ **页那一层递进来的那三条动作** —— 分派与诊断全在这一层。

        ```
        {"action": "save",   "profile": {...}, "set_current": true|false}
        {"action": "use",    "name": "..."}
        {"action": "delete", "name": "..."}
        ```
         交回的是 **`{"code": …, "error": …}`**（`code ∈ ok ／ bad_request ／ conflict ／
          unknown_action`）—— 不是抛异常：调用方那一层**零同胞 import**，它 import 不到
          那几个异常类，也就分不出"**动不了**"与"**请求不对**"。
        ⚠️ **HTTP 那个数由调用方印**（那是网页那一层的活）；这一层只交"是哪一种情形"。
        """
        action = str(asked.get("action") or "")
        try:
            if action == "save":
                if asked.get("create_only") and any(
                        p.name == str((asked.get("profile") or {}).get("name", ""))
                        for p in self.profiles()):
                    raise EngineConflict("新建实例的内部标识已存在；请刷新后重新新建，未覆盖原配置")
                saved = self.save_json(asked.get("profile") or {},
                                       make_current=bool(asked.get("set_current")))
                return {"code": "ok", "error": "", "profile": saved.as_dict(),
                        "secret_binding": self._secret_binding(saved)}
            elif action == "use":
                self.use_and_save(str(asked.get("name") or ""))
            elif action == "delete":
                self.remove_and_save(str(asked.get("name") or ""))
            else:
                return {"code": "unknown_action", "error": f"不认得这个动作：{action!r}"}
        except EngineConflict as exc:
            return {"code": "conflict", "error": str(exc)}
        except EngineError as exc:
            return {"code": "bad_request", "error": str(exc)}
        return {"code": "ok", "error": ""}

    def _secret_binding(self, profile: Profile) -> dict[str, str]:
        """Version only, not an auth token. Never includes credential values.

        Covers every persisted profile field/ownership and current selection.
        A fresh persisted nonce invalidates even an identical later save (ABA).
        Restart preserves validity; any later config save expires the binding.
        """
        if not self._binding_revision:
            raise EngineConflict("profile binding unavailable; save the profile first")
        canonical = json.dumps({"nonce": self._binding_revision,
            "selection_revision": self._selection_revision,
            "current": self._current,
            "profiles": {name: row.as_dict() for name, row in self._profiles.items()}},
            sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        revision = "v1:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return {"profile_name": profile.name, "secret": profile.secret, "revision": revision}

    @_serialized
    def secret_json(self, asked: Mapping[str, Any]) -> dict[str, Any]:
        """Conditional profile-key write. No legacy/unconditional JSON branch."""
        try:
            if not isinstance(asked, Mapping):
                raise EngineError("secret request must be an object")
            expected = asked.get("expected")
            if (not isinstance(expected, Mapping)
                    or set(expected) != {"profile_name", "secret", "revision"}
                    or not all(isinstance(v, str) and v for v in expected.values())):
                raise EngineError("profile secret expectation required")
            name, value = asked.get("name"), asked.get("value")
            if not isinstance(name, str) or name != expected["secret"]:
                raise EngineError("secret slot does not match expectation")
            if not re.fullmatch(r"v1:[0-9a-f]{64}", expected["revision"]):
                raise EngineError("invalid profile binding revision")
            if not self.root.is_dir():
                raise EngineConflict("profile binding no longer exists")
            # Lock order is always instance -> stable OS config lock. Other
            # Engine instances/processes use this same lock for both writes.
            with self._catalog_guard():
                latest = self._fresh()
                if latest._read_error:
                    raise EngineConflict("profile configuration unavailable")
                profile = latest._profiles.get(expected["profile_name"])
                if (profile is None or profile.auth == "chatgpt"
                        or profile.secret != name
                        or any(row.name != profile.name and row.secret == name
                               for row in latest._profiles.values())):
                    raise EngineConflict("profile secret ownership changed")
                if latest._secret_binding(profile) != dict(expected):
                    raise EngineConflict("profile binding expired; review and save again")
                # Do not unlock between validation and atomic engine.env replace.
                self._write_secret_locked(name, value)
        except EngineConflict as exc:
            return {"code": "conflict", "error": str(exc)}
        except EngineError as exc:
            return {"code": "bad_request", "error": str(exc)}
        return {"code": "ok", "error": ""}

    @_serialized
    def presets_now(self) -> tuple[Profile, ...]:
        """ **那张预设表**（`web.py` 零同胞 import ⇒ 按名字问这一句）。

        ⚠️ 它交回的是**常量那一份**，与当前档案无关 —— 名字里的 `now` 是"这一层今天
           就这么几条"的意思（不是"随时会变"）。
        """
        return PRESETS

    @_serialized
    def secret_sources(self) -> dict[str, str]:
        """ **变量名 → 来源**（`env` ／ `file` ／ `missing`）—— **一个值都不出去**。

        ⚠️ 它比 `secrets_on_disk()` 多一样：**当前这条档案点名的那个变量名**也列进来
           （哪怕盘上还没有它）⇒ 页上那一格显示得出"**还没配**"。
        """
        names = set(self.secrets_on_disk())
        one = self.current()
        if one is not None and one.secret:
            names.add(one.secret)
        return {name: self.secret_source(name) for name in sorted(names)}

    @_serialized
    def update(self, target: str, **changes: Any) -> Profile:
        """改一条 —— ⚠️ **`kind` 与 `name` 不可改**（换协议就新建；改名等于换一条）。

        ⚠️ 第一个形参叫 `target`（**不叫 `name`**）：那样 `name=` 才能作为"要改的那一格"
           走到下面那句 `EngineError` 上 —— 叫 `name` 的话它当场撞成 `TypeError`，
           而那种报错说不清"为什么不该改它"。
        """
        for locked in ("kind", "name"):
            if locked in changes:
                raise EngineError(f"{locked} 保存后不可改（要换它就新建一条）")
        old = self._draft_profile(target)
        # ⚠️ **放行"档案自己的每一格"**（2026-10-05）：从前这里只放行 `KEYS` 里那七格 ⇒
        # `model_name` ／ `multimodal` 这类**也被静默丢掉** （作者那条"开关存不住"里
        #    就有一半是它）。`kind` ／ `name` 那两条锁照旧在上面拦着。
        known = set(Profile.__dataclass_fields__)                   # noqa: SLF001 —— 就是那几格
        # ⚠️ **`_coerce` 只用在 `KEYS` 那七格上**（它们是"环境变量式"的值，本来就该是字符串）；
        # 档案自己的格（`multimodal` 这种布尔 ／ `context_window` 这种整数）**不许被串化**
        #    —— 串化之后 `multimodal` 会变成 `'True'`（真值判断照样过，而类型已经不是布尔了）。
        got = replace(old, **{k: (_coerce(k, v) if k in KEYS else v)
                              for k, v in changes.items() if k in known})
        # **改也要过同一处校验**（2026-10-05）—— ⚠️ 先前它直接写盘 ⇒ 一条坏值能绕过
        #    那一处（"一处校验"如果只挂在 `add()` 上，就不是一处）。
        check_a_profile(got)
        self._profiles[got.name] = got
        self._touched_profiles.add(got.name)
        return got

    @_transaction
    def use_and_save(self, name: str) -> Profile:
        """**设为当前 ＋ 落盘**（ 设计稿 §七 第 3 条：写文件与更新活视图是**一个动作**）。"""
        got = self.use(name)
        self.save()
        return got

    @_transaction
    def remove_and_save(self, name: str) -> None:
        """**删一条 ＋ 落盘**（同上）。"""
        self.remove(name)
        self.save()

    @_serialized
    def remove(self, name: str) -> None:
        """删一条 —— **连"当前那一条"也能删**（2026-10-05 作者定的）。

        ⚠️ 从前它照参考那边的规矩拒掉（`EngineConflict`：先切走再删）—— 而作者的要求是
        "**点删除就得能删掉自己**，否则只有多条档案时才删得动" ⇒ 改成：删掉它，并把
        `current` **清空**（那时这一层就没有"正在用"的那一条了 —— 活视图会落回默认值那一份，
        而页上那一列会一条都不高亮）。 那一句拒收留着**给别的引用**用（今天没有第二种引用）。
        """
        self._draft_profile(name)
        del self._profiles[str(name)]
        self._touched_profiles.add(str(name))
        if self._current == name:
            self._current = None
            self._current_touched = True

    @_serialized
    def use(self, name: str) -> Profile:
        """设为当前；没有模型的档案不允许设为当前。"""
        one = self._draft_profile(name)
        if not one.model.strip():
            raise EngineError(f"{name!r} 还没有模型，不许设为当前")
        self._current = one.name
        self._current_touched = True
        return one

    # ── 活视图 ────────────────────────────────────────────────────────────────
    @_serialized
    def live(self) -> LiveConfig:
        """交回**活视图** —— 它的属性**一读就现算**（`generate()` 一个字不用改）。"""
        return LiveConfig(self)

@dataclass(frozen=True, repr=False)
class RequestSnapshot:
    """Private request binding. Never serialize/log it: headers contain secrets."""

    config: Config
    kind: str
    values: Mapping[str, Any] = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    generation: str

    def __post_init__(self):
        if (not isinstance(self.config, Config)
                or self.kind not in ("responses", "chat", "anthropic")
                or self.values.get("kind") != self.kind
                or not self.generation):
            raise EngineError("invalid request snapshot")
        if not all(isinstance(k, str) and isinstance(v, str)
                   and not any(c in k + v for c in "\r\n")
                   for k, v in self.headers.items()):
            raise EngineError("invalid request authentication")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


class LiveConfig:
    """ **活视图**：与 `Config` **同名的那几个属性**，而它们是**现算**的。

    ⚠️⚠️ 它换掉的正是"配置是**启动那一刻定死的一个值**"这件事（设计稿 §七）：
       从前 `launch.py: cfg = load_config()` 之后，那一份一路递到请求那一句 ——
       而**改盘上的文件、下一次请求也读不到**。这一个对象让那一格**每次现读**。
    ⚠️ **不在 `KEYS` 里的那几样照旧是那一份冻结默认值**（`first_response_ms` ／
       `frame_idle_ms` ／ `content_idle_ms` ／ `retry_delays_ms`）—— 它们**不可配置**，
       本刀不碰它们（改它们要动 `07` 那一层的判据）。
    ⚠️ **正在跑的那一轮保持原绑定**（设计稿 §七 第 1 条）：那一轮手里拿着的是**它开始时
       读到的值**（调用方在那一轮开头取一次）—— 这一层不替它保管，也不替它改。
    """

    @property
    def multimodal(self) -> bool:
        """ **图像输入那个开关**（这一条档案那一格）—— 活视图把它交给 `protocol.shape()`。

        ⚠️ **它只从 `values()` 取**（那一格在那儿算**一处**）：我上一版又自己走了一遍
        `_current_name()` ／ `profiles()` ⇒ 引用了本类上没有的东西 ⇒ 取它当场抛
        `AttributeError`（而 `values()` 那条路一直是好的 —— 请求那条路走的正是它）。
        """
        return bool(self.values().get("multimodal", False))

    def __init__(self, engine: Engine, *, frozen: Config | None = None) -> None:
        self._engine = engine
        self._frozen = frozen if frozen is not None else DEFAULTS
        from .strata_follow import FollowState
        self._strata_follow = FollowState()

    def _follow_identity(self):
        one = self._engine.current()
        return (one.name if one else "", self._engine.effective("base_url").value,
                self._engine.effective("model").value)

    def _follow_values(self):
        return self._strata_follow.get(self._engine.state_root, self._follow_identity())

    async def prepare_round(self):
        """Refresh selected capabilities without blocking the event loop."""
        import asyncio
        from .strata_follow import FollowState, StrataFollowError, context_policy
        from .owned_blocking import owned_thread
        identity = self._follow_identity()
        fresh = FollowState()
        self._strata_follow = FollowState()  # No stale display on failed refresh.
        if not context_policy(self._engine.state_root, identity):
            discovered = await owned_thread(self._engine.refresh_context_capacity)
            self._engine.accept_context_capacity(discovered)
            if self._follow_identity() != identity:
                raise EngineConflict("容量查询期间模型已切换，请重试")
            window = self._engine.effective("context_window").value
            if window is not None and self._engine.effective("max_tokens").value >= window:
                raise EngineError("总输出上限必须小于有效上下文容量")
            return
        selection = self._engine.effective("context_window")
        requested = selection.value if selection.source in ("file", "env") else None
        # Three seconds closes the operation budget; cancellation retains this
        # caller until the real worker exits. No detached refresh or late publish.
        # Same-task timeout preserves worker failure on the cancellation cause.
        try:
            async with asyncio.timeout(3):
                await owned_thread(fresh.refresh, self._engine.state_root, identity)
        except (StrataFollowError, TimeoutError) as exc:
            if isinstance(exc, StrataFollowError) and not str(exc).startswith("strata_follow_refresh_failed:"):
                raise
            # Unavailable metadata does not invent upstream capacity or vision support.
            fresh = FollowState()
        if self._engine.effective("context_window") != selection:
            raise StrataFollowError("strata_follow_context_changed")
        if self._follow_identity() != identity:
            raise StrataFollowError("strata_follow_profile_changed")
        upstream = fresh.values.get("context_window")
        if upstream is not None and requested is not None:
            if type(requested) is not int or requested <= 0 or requested > upstream:
                raise StrataFollowError(
                    f"上下文上限 {requested} 超出上游当前容量 {upstream}；请调小或留空自动跟随")
        # Keep discovered capacity separate from a user's manual limit. Otherwise
        # clearing that limit later incorrectly "discovers" the previous limit.
        effective = requested if requested is not None else fresh.values.get("context_window", selection.value)
        output = self._engine.effective("max_tokens").value
        if effective is not None and output is not None and output >= effective:
            raise StrataFollowError("总输出上限必须小于有效上下文容量")
        if self._follow_identity() != identity:
            raise StrataFollowError("strata_follow_profile_changed")
        # No await after provenance recheck: cancellation cannot publish late IO.
        self._strata_follow = fresh

    async def fork_round(self):
        """A background task owns its capability snapshot, not the foreground pin."""
        fresh = LiveConfig(self._engine, frozen=self._frozen)
        await fresh.prepare_round()
        return fresh.pinned()

    def proxy_for(self, url):
        """Use this Bot's network policy with the shared provider catalog."""
        return self._engine.proxy_for(url)

    @property
    def kind(self) -> str:
        """ 当前那一条档案说的是**哪一套话**（`responses` ／ `chat`）—— **现读**。

        ⚠️ 它不是 `KEYS` 里的一格（那是"能改哪几个值"），所以走这一条显式的路；
           一条都没有 ⇒ 默认 `responses`（今天生产上跑的那一套）。
        """
        one = self._engine.current()
        return one.kind if one is not None else "responses"

    def values(self) -> dict[str, Any]:
        """ **塑形那几格**（`protocol.shape` 要的那一套）—— 与 `effective()` 同一个来源。

        ⚠️ 温度与推理强度读的是**档案里那一格本身**（不是 `effective()` 的默认值）：
           "没写下过"就是 `None` ⇒ **不发出去**（零回归那条靠这个）；
           而页上要显示的默认值由 `effective()` 给（那是**展示**，不是请求）。
        """
        one = self._engine.current()
        return {
            "kind": self.kind,
            **({"auth": "chatgpt", "_subscription": self._engine.chatgpt(one.name)}
               if one is not None and one.auth == "chatgpt" else {}),
            # ⚠️ 问的是 **`Engine`**（`effective()` 在它身上）—— 不是本视图自己。
            "thinking": self._engine.effective("thinking").value,
            "temperature": self._engine.effective("temperature").value,
            "reasoning_effort": self._engine.effective("reasoning_effort").value,
            "reasoning_budget_tokens": self._engine.effective("reasoning_budget_tokens").value,
            # **图像输入那一格也跟着进**（2026-10-05）：`shape()` 拿它决定"图片那几片
            #    收不收、按哪一边的类型名发"（照参考 `ProviderModule.accepts?`）。
            #    ⚠️ 拿不到当前那一条 ⇒ `False`（**不许编**：默认就是不收图）。
            "multimodal": self._follow_values().get("multimodal",
                getattr(one, "multimodal", False) if one is not None else False),
        }

    def __getattr__(self, key: str) -> Any:
        if key in KEYS:
            sourced = self._engine.effective(key)
            if key == "context_window" and sourced.source not in ("env", "file"):
                return self._follow_values().get(key, sourced.value)
            return sourced.value
        got = getattr(self._frozen, key, None)
        if got is None:
            raise AttributeError(f"{type(self).__name__} 没有 {key!r}（可改的只有 {list(KEYS)}）")
        return got

    def sources(self) -> dict[str, dict[str, Any]]:
        """ 页上要显示的那一份：**每个键 ＋ 值 ＋ 来源**（`09` §9.1 那条）。"""
        values = self._engine.effective_all()
        followed = self._follow_values()
        if "context_window" in followed and self._engine.effective("context_window").source not in ("env", "file"):
            values["context_window"] = {"value": followed["context_window"], "source": "strata"}
        return values

    def as_config(self) -> Config:
        """把**现在这一刻**那几个值钉成一份冻结的 `Config`（给"这一轮"用）。"""
        return replace(self._frozen, **{key: getattr(self, key) for key in KEYS
                                        if key in Config.__dataclass_fields__})   # noqa: SLF001

    def request_snapshot(self) -> RequestSnapshot:
        """Capture configuration and API-key headers under the same Engine lock.

        Detect external file/environment changes; do not retry or publish a
        mixed capture. Subscription auth remains bound to its captured handle.
        """
        engine = self._engine
        def stamp(path):
            try:
                stat = path.stat()
                return (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            except FileNotFoundError:
                return None
        with engine._state_lock, (engine._catalog_guard() if engine._bot_state else nullcontext()):
            engine._refresh()
            if engine._read_error:
                raise EngineError("request configuration unavailable")
            before = (stamp(engine.config_path), stamp(engine.secret_path))
            environment = dict(engine._env)
            engine._capturing_request = True
            try:
                config = self.as_config()
                values = self.values()
                one = engine.current()
                headers = {}
                if one is not None and one.auth != "chatgpt":
                    # Preserve the production file-only key-source contract.
                    name = str(one.secret or "").strip()
                    key = (str(environment.get(name, "")).strip() or str(engine._read_secret_lines().get(name, "")).strip()) if name else ""
                    if key:
                        if one.kind == "anthropic":
                            from .protocol import ANTHROPIC_VERSION
                            headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
                        else:
                            headers = {"Authorization": "Bearer " + key}
                after = (stamp(engine.config_path), stamp(engine.secret_path))
                if (before != after or environment != dict(engine._env)
                        or engine._fingerprint_now() != engine._fingerprint):
                    raise EngineError("request configuration changed during capture")
                return RequestSnapshot(config, values["kind"], values, headers, uuid.uuid4().hex)
            finally:
                engine._capturing_request = False

    def pinned(self) -> Pinned:
        """ 交回一个**每轮钉一次**的视图（规则①：正在跑的那一轮保持原绑定）。"""
        return Pinned(self)


class Pinned:
    """ **每一轮钉一次**的视图 —— 设计稿 §七 第 1 条（"正在跑的那一轮保持原绑定"）的落地。

    ⚠️⚠️ **为什么非有它不可**：活视图是"一读就现算"的 ⇒ 一轮跑在中途时有人保存了配置，
       那一轮就会**一半旧值、一半新值** —— 更坏的是同一轮里 `build_body` 与别处
       可能读到**不同**的两份（模型换了而额度没换那种）。
    ⇒ 于是把"读哪一份"做成**可换的一格**，而换的时机是**轮边界**：
       那一轮**开始那一刻**钉一份，跑完为止都用它；下一轮开头再钉一次。

    ⚠️ **轮边界由调用方说了算**（`console` 在每一轮入口调 `refresh()`）——
       本类**不认识"轮"**（那是主循环的词，不是配置层的词）。
    ⚠️ **`sources()` 与钉住的那一份是故意分开的**：一轮用的是**钉住**的值，
       而页上要显示的是"**现在**生效的值从哪来" ⇒ 它们可以不同，而那正是这一格的诚实。
    """

    def __init__(self, source: LiveConfig) -> None:
        self._source = source
        self._request = source.request_snapshot()

    @property
    def kind(self) -> str:
        """ 这一轮**钉住**的那一套话（与 `values()` 同一次钉 —— 两者不许各钉一次）。"""
        return self._request.kind

    def values(self) -> dict[str, Any]:
        """ 这一轮**钉住**的塑形那几格（`protocol.shape` 要的那一套）。"""
        return dict(self._request.values)

    def refresh(self) -> Config:
        """**钉一次**（在每一轮开头调）—— 交回钉住的那一份。"""
        self._request = self._source.request_snapshot()
        return self._request.config

    async def fork_round(self):
        return await self._source.fork_round()

    def request_snapshot(self) -> RequestSnapshot:
        return self._request

    def peek(self) -> Config:
        """这一轮**正在用**的那一份（判据读它）。"""
        return self._request.config

    def __getattr__(self, key: str) -> Any:
        return getattr(self._request.config, key)

    def sources(self) -> dict[str, dict[str, Any]]:
        return self._source.sources()


def _a_stamp() -> str:
    """那一个时间戳（`YYYYMMDD-HHMMSS`）—— 与 `console._a_stamp` 同一形状。"""
    import time
    return time.strftime("%Y%m%d-%H%M%S")


def live(root: Path, *, env: Mapping[str, str] | None = None) -> LiveConfig:
    """一句话拿到活视图（装配那一层用的就是这个）。"""
    return Engine(root, env=env).live()


#: **这一份还欠着的那几笔**（名字 → 欠主）—— 成法照 `unresolved._OWED` ／ `round._OWED`。
_OWED: dict[str, str] = {
    "local_probe": (
        "「本地：探测那几个常见端口，读到 `/models` 就把它列出来」（设计稿 裁定 2 ／ §五 那一行）"
        "—— **今天没有这个口**：本模块只会读档案，不会探活。"
        " ⚠️ **2026-10-13 订正（待办账第 19 条）：这一笔原先的「欠主」写错了** —— "
        "它写的是「刀 4 那条 `POST /engine/probe`」，而那一支干的是**另一件事**："
        "**拿一个最小的请求问上游通不通**（`web.probe_engine`，见那一处路由与它的抬头）"
        "—— 它**既不扫本机那几个端口、也不拿它去列模型**（那个 `mode='models'` 问的是"
        "**当前这一条档案**指向的那一家，不是「本机哪几个端口上有模型」）。"
        " ⚠️ 拿它当欠主的读法会让人以为「那件事已经有人接了」，而实际是**没有人接**。"
        " 欠主：**作者**（这个口要不要做、做成什么样）—— 在有人接上之前，"
        "「本机那几个常见端口上有哪些模型」这件事**本仓答不出来**。"),
    # **2026-10-05 还清了一笔**（第 1 步）：`context_window_probe` ——
    #   "换一条档案就自动问上游要窗口"**已经接上**：`POST /engine/probe(mode='models')`
    #   把上游那一份里的 `context_window` ／ `max_output_tokens` ／ `effort.default_level`
    #   一起带回来，页上按它填（实测：本机那台与 DeepSeek 各自报自己的窗口）。
    #   ⇒ 按本仓那条"欠账还了就删条目"，这一笔删掉了。
    # **2026-10-03 还清了一笔**（刀 3）：`temperature` 与 `reasoning_effort` 两格**已经接上**
    #   —— 走的是 `protocol.shape()`（传输层外面那一个适配器），而**不是** `generate.build_body`
    #   （那一条照旧一个字没改）。⇒ 按本仓那条"欠账还了就删条目"，这一笔删掉了。
}

