# telyincore/tokens.py
"""token 估算 —— **只判断"够不够装"，不参与计费**。

⚠️ **2026-09-27：三档常数里有一档是错的，而且方向正是危险的那个。**
原 docstring 写着「宁可高估」，实现却在**低估** —— 下面全部是**实测**，不是推断。

**取法**：本机 `:24550` 的 NInfer 引擎，`POST /v1/responses`
（`chat_template_kwargs.enable_thinking=false`、`max_output_tokens=16`），
读**终态事件**里的 `usage.input_tokens`：

| 输入 | 实测 `input_tokens` |
|---|---|
| `[{"role":"user","content":""}]` | **40**（三次一致） |
| 1 / 2 / 3 条内容 `"a"*40` 的 user 消息 | **45 / 55 / 65** |
| `"a"*40` / `"a"*400` | 45 / 90 |
| `"中"*1` / `"中"*70` / `"中"*700` | 41 / 110 / **740** |
| `"用一句话说你好"`（7 字） | **44** |
| `"1234567890"*8`（80 位数字） | 120 |

**⇒ 三条结论，分别对应下面三处改动：**

1. **有一笔固定开销，旧实现完全没有这一项。**
   一条**空的** user 消息就是 **40** token（＝ 基座 35 ＋ 每条消息 5）。
   短输入上这一项占绝对多数：`"用一句话说你好"` 旧估 **5**、实测 **44** —— **8.8 倍**。
   ⇒ 修在 `estimate_messages_tokens`（**不是** `estimate_tokens`：那是"这段文字多少 token"，
   不含请求包装）。
2. **密文字不是 1.5 字符/token，是 1.0。**
   `"中"*70` ⇒ 110、`"中"*700` ⇒ 740 —— 1.5 会**低估 1.5 倍**，与「宁可高估」相反。
   ⇒ `CJK_CHARS_PER_TOKEN` 1.5 → **1.0**。
3. **ASCII 那一档是启发式，不是上界 —— 这一条**没有**改，是因为改了更糟。**
   实测跨度极大：`"a"*80` ⇒ 5 token（**16** 字符/token），
   而 80 位数字 ⇒ 80 token（**1** 字符/token）、代码串也接近 1 字符/token。
   取 4.0 是**自然语言**的取法；若为了代码／数字取 1.0，英文散文会被**高估 4 倍**，
   而那条路的代价是假的"装不下"。⇒ **保留 4.0，并在这里写明它不是上界**
   （代码／数字上仍可低估最多 4 倍）。这是**已知的、写下来的**余量缺口，不是"已经闭合"。

⚠️ 非 BMP 那一档（emoji 等）**没有实测**：按 1 字符 1 token 算。
真实 tokenizer 里 emoji 常占 2~4 token ⇒ **这一档也可能偏低**，同样写明。

⚠️ 这里仍然**不做文字块枚举**（不列"该按密算的是这几个区段"）：枚举一定会漏
（假名、谚文、西里尔、泰文、阿拉伯文都漏过一轮）。按 `ord()` 分档没有洞。
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

#: 比例是量出来的（见模块 docstring 的表），不是猜的。
#: ⚠️ ASCII 那档**故意**留在 4.0 —— 它是自然语言的取法，不是上界；理由见 docstring 第 3 条。
ASCII_CHARS_PER_TOKEN = 4.0
#: 密文字（非 ASCII 的 BMP）—— **实测 1.0 字符/token**（`"中"*700` ⇒ 740）。
#: ⚠️ 这里**曾经是 1.5**：那会低估 1.5 倍，方向与「宁可高估」相反（回评 I1）。
CJK_CHARS_PER_TOKEN = 1.0
#: 非 BMP（emoji 等）：**一个字符按一个 token 算** —— ⚠️ 未实测，且真实 tokenizer 往往更贵。
WIDE_CHARS_PER_TOKEN = 1.0

#: **一张图按多少 token 估**（2026-10-05）—— **不是**它那串 base64 的字符数 。
#: 实测（本机那台带视觉的引擎，`POST /v1/responses` 读 `usage.input_tokens`，与不带图那次比）：
#:   · 大图**约 1000 像素 / token**（1968×1730 ⇒ 3350 ／ 1448×1448 ⇒ 2027 ／ 1200×1368 ⇒ 1636
#: ／ 1375×900 ⇒ 1206 —— 四张折算一致 ）；
#:   · 小图有**保底 ≈ 70 token**（195×120 ／ 256×256 ／ 229×110 都是 66～79）。
#: 而那一页**先把图缩到最长边 2048**（`live.ts` 的 `MAX_IMAGE_EDGE` ）⇒ 最坏 2048×2048
#: ＝ 4.19M 像素 ⇒ **≈ 4200 token** ⇒ 取它当"每张的上界估计" 。
#: ⚠️ 那串 base64 **一个字符都不算**：它只是传输的壳，引擎看到的是**解码之后的像素** 。
IMAGE_TOKENS = 4200
#: 哪些片算"图" —— 两个协议两种名字（`responses` 认 `input_image` ／ `chat` 认 `image_url`）。
IMAGE_PART_TYPES = ("input_image", "image_url")

#: 一条请求的**基座**开销（BOS ＋ 模板骨架）—— 实测：一条**空的** user 消息就是 40。
REQUEST_BASE_TOKENS = 35
#: 每条消息的**包装**开销（role 标记、分隔符）—— 实测：每多一条消息 ＋10，
#: 其中 5 是内容、**5 是包装**（`"a"*40` 一条消息 ⇒ 45，两条 ⇒ 55，三条 ⇒ 65）。
MESSAGE_WRAPPER_TOKENS = 5


def estimate_tokens(text: str) -> int:
    """**一段文字**的 token 估算 —— **不含**请求的固定包装（那在 `estimate_messages_tokens`）。

    ⚠️ 不枚举文字，是这里唯一要紧的设计决定：枚举（"该按密算的是这几个区段"）
    **一定会漏** —— 假名、谚文、西里尔、泰文、阿拉伯文都漏过一轮 ——
    而**低估是危险方向**：调用方会以为装得下、实际装不下。
    按 `ord()` 边界分档就不会有洞：

    · `ord(ch) < 0x80`      ASCII —— 4 字符 1 token（**启发式，非上界**）
    · `ord(ch) > 0xFFFF`    非 BMP（emoji 等）—— 1 字符 1 token（**未实测**）
    · 其余                  **非 ASCII 的 BMP 一律按密算** —— **1.0 字符 1 token**（实测）

    结尾用 `math.ceil` 而不是截断 —— 同一个方向：宁可高估。
    """
    if not text:
        return 0
    if text.isascii():
        return max(1, math.ceil(len(text) / ASCII_CHARS_PER_TOKEN))
    # An ASCII encoding with "ignore" emits exactly one byte per ASCII
    # character, including NUL. Count it in C, without retaining prompt data.
    ascii_n = len(text.encode("ascii", "ignore"))
    # At the calibrated rates all non-ASCII code points cost the same.
    wide = (0 if WIDE_CHARS_PER_TOKEN == CJK_CHARS_PER_TOKEN
            else sum(1 for ch in text if ord(ch) > 0xFFFF))
    dense = len(text) - ascii_n - wide
    n = (ascii_n / ASCII_CHARS_PER_TOKEN
         + wide / WIDE_CHARS_PER_TOKEN
         + dense / CJK_CHARS_PER_TOKEN)
    return max(1, math.ceil(n))


def _the_text_of(one: Mapping) -> Any:
    """ **一条 `input` 项里"要算 token 的那段字"**（2026-10-17 修）。

    ⚠️⚠️ **它治的是什么**：这一层原来一律读 `one.get("content", "")` —— 于是
       `function_call`（带的是 `arguments`）与 `function_call_output`（带的是 `output`）
       **贡献 0 token** ⇒ 整份估算**少算 16%**（模型真那一份：真数 **137703**，报 **115795**）。
    ⚠️ **那不只是显示**：`estimate_messages_tokens` **就是递给 `Handoff` 的估算器**
       （"该不该交接"那个触发条件）⇒ **模型历史里最大的一块，那个阈值一直看不见**。
    ⚠️ 与 `protocol.clean_items` 那个坑**同一个形状**（"工具那两条没有 `content`"）——
       同一天两处都踩了：一处把它们的**内容扔了**，一处把它们的**体积算成 0**。
    """
    kind = str(one.get("type") or "")
    if kind == "function_call":
        return f"{one.get('name') or ''}{one.get('arguments') or ''}"
    if kind == "function_call_output":
        return one.get("output") or ""
    return one.get("content", "")


def estimate_messages_tokens(messages: Sequence[Mapping]) -> int:
    """**整条请求的输入**估算 —— ⚠️ **含实测出来的固定包装开销**。

    这是与 `estimate_tokens` 的分工：后者管"这段文字多少 token"，
    前者管"引擎这条请求会看到多少 token"。而**固定开销是实测存在的**：
    一条**空的** user 消息就是 **40** input_tokens ——
    旧实现漏了它，于是在短输入上低估近 9 倍（`"用一句话说你好"`：估 5、实测 44）。

    ⚠️ 空列表返回 **0**：引擎会拒 `input: []`
    （实测 `{"message":"input must not be empty","param":"input"}`）⇒
    "零条消息的请求"不存在，不该凭它凭空收一笔基座开销。

    ⚠️ 包装开销是**按消息条数**算的（实测 45 / 55 / 65 那条斜率），
    **不分 role** —— 实测里 assistant／system 的偏差在 ±4 token 以内
    （1 user ＋ 1 assistant ⇒ 59、1 system ＋ 1 user ⇒ 51，模型分别给 65 / 65），
    两个偏差方向相反、量级小于包装项本身 ⇒ 不再按 role 细分（那会变成没有数据的拟合）。
    """
    if not messages:
        return 0
    return (REQUEST_BASE_TOKENS
            + MESSAGE_WRAPPER_TOKENS * len(messages)
            + sum(_one_message_tokens(_the_text_of(m)) for m in messages))


def summarize_messages(messages: Sequence[Mapping], *,
                       tools: Sequence[Any] = ()) -> dict[str, int | None]:
    """ **一份请求的 token 摊在四块上**（2026-10-05"上下文占用"那个面板要的）。

    顺序**照作者给的那一份**（面板上就按这个次序画 ）：
    · `framing` —— **结构 · 基座**：请求基座 ＋ 每条的包装（本仓实测的那两个常数 ）；
    · `prefix` —— **系统前缀**：`role == "system"` 那一条（那句合成开头：人设那几段 ）；
    · `tools` —— **工具 · 插件**： **这一格接上了**（2026-10-05 作者："那就先做。" ⇒ "做。"）
       —— 递了 `tools` ⇒ 算**声明**那几样占多少（`_tools_tokens` ）；
       ⚠️ **没递 ⇒ 照旧 `None`** —— `None` 不是 0：0 是"算了，一点不占"，
       而 `None` 是"**还没有可以算的东西**" （与 `state_of()` 那条"不许把不知道画成零"
       **同一条口径** —— 那一页画 `—` ）。 那条接口就是这一格 。
    · `dialogue` —— **对话**：其余那些往来（ 图按 `IMAGE_TOKENS` 那个常数算 ）。
    ⚠️ 那三块**有数**的之和 ＝ `estimate_messages_tokens` 那个数（同一批常数 ⇒ 说得通 ）。
      ⚠️ `framing` 那一块**是例外**：它含请求基座，而 `estimate_messages_tokens` 也含
      （所以两者之和**对得上**；`tools` 那一块**不在** `estimate_messages_tokens` 里 ——
      那一条只算 `input` 那半边 ，工具声明是**另一格** ）。
    ⚠️ 空列表 ⇒ 有数的那三块都是 0（与"零条消息的请求不存在"同一个判法 ）。
    """
    tools_size = _tools_tokens(tools)
    if not messages:
        return {"framing": 0, "prefix": 0, "tools": tools_size, "dialogue": 0}
    prefix = dialogue = 0
    for one in messages:
        size = _one_message_tokens(_the_text_of(one))
        if str(one.get("role") or "") == "system":
            prefix += size
        else:
            dialogue += size
    return {
        "framing": REQUEST_BASE_TOKENS + MESSAGE_WRAPPER_TOKENS * len(messages),
        "prefix": prefix,
        "tools": tools_size,
        "dialogue": dialogue,
    }


def _tools_tokens(tools: Sequence[Any]) -> int | None:
    """ **工具声明占多少**（2026-10-05）—— 名字 ＋ 说明 ＋ 参数，各按字符算 。

    ⚠️ **没递 ⇒ `None`**（不是 0 —— "没有工具"与"工具不占地方"是两件事，见上面那一格 ）。
    ⚠️ 收的是**声明**那几样（`name` ／ `description` ／ `parameters` ）：
      `DECLARED_TOOLS` 那 13 行是 `ToolDef` ，而 handler 那一格**不在**这条路上
      （那是装配层绑的 见 `tools.py` 的抬头）—— 这里只读那三样 。
    ⚠️ 参数那一份用 `str()` 排一下（估算器本来就是启发式的 ；**不为它引 json** ）。
    """
    if not tools:
        return None
    total = 0
    for one in tools:
        name = str(getattr(one, "name", "") or "")
        desc = str(getattr(one, "description", "") or "")
        params = getattr(one, "parameters", None)
        total += estimate_tokens(f"{name}{desc}{params if params is not None else ''}")
    return total


def _one_message_tokens(content: Any) -> int:
    """一条消息的 `content` 有多少 token —— **认得出"片"与"图"**（2026-10-05）。

    ⚠️⚠️ 从前这里是一句 `estimate_tokens(str(content))` —— 而带图的那一条，`content` 是一个
       **列表**，里面躺着一段 **base64 data URL** ⇒ 它被当成**自然语言**、按 4 字符/token 算
       ⇒ 一张 66 KB 的图估成 **35 万 token**（作者实测：那个圆写着 **136%** ），
       而引擎的真实开销是 **≈ 3000**（1000 像素/token）—— **差两个数量级** 。
    ⚠️ 而这个数**不只是显示**：`estimate_messages_tokens` 就是注入给 `Handoff` 的那一个估算器
       ⇒ 有图时的假数字会把"该不该交接"顶爆（软阈那一条）。
    ⇒ 所以：**文本片照旧按字符算；图片片按 `IMAGE_TOKENS` 这个常数算**（不从 base64 算 ）。
    """
    if not isinstance(content, list):
        return estimate_tokens(str(content))
    total = 0
    for part in content:
        if not isinstance(part, Mapping):
            total += estimate_tokens(str(part))
            continue
        if str(part.get("type") or "") in IMAGE_PART_TYPES:
            total += IMAGE_TOKENS
            continue
        text = part.get("text") or part.get("content") or ""
        total += estimate_tokens(str(text))
    return total
