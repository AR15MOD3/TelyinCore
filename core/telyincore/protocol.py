# telyincore/protocol.py
""" 刀 3：「这一条档案说的是哪一套话」那一层 —— **请求体塑形 ／ 响应流翻译 ／ 能力问句**。

设计稿 §六：适配器是一个**纯函数层** —— 收 chat 的 SSE 字节、吐 Responses 事件的字节。
 它插在**传输层的外面**（`ChatTransport` 是个**装饰器**）⇒ 于是：

```
ResponseAccumulator.feed()   一个字不用改（它只认 Responses 那些事件名）
generate.py                  一个字不用改（它拿到的仍是同形的事件流）
transport.py ⚠️ **改了一处，加性的**（见下面那段更正）
```

⚠️ **2026-10-03 更正（落地时量的）**：设计稿 §六 说 `transport.py` **一行不改** ——
   而量下来**做不到**：端点那一段从前是**写死**在 `HttpxTransport` 里的
   （`url = f"{base_url.rstrip('/')}/responses"`）⇒ 包在它外面的适配器**没有路**
   走到另一个端点（把路径塞进 `base_url` 会变成 `…/chat/completions/responses`
   ⇒ **404** —— 第一版烟测真撞上了）。
   ⇒ 改成**加性**的：`stream_chat(…, path: str = "/responses")`（协议 ＋ 实现各一处），
     而缺省值就是原样 ⇒ **既有的每一个调用方逐字节不变**（那条 `>= 300` ／ `aread()`
     那一整套一个字未动）。 那一处由 `_FROZEN` 里的 `transport.py` 记着（同一次重记）。

⚠️ **为什么非有它不可**（`transport.py:125-130` 那段实测写着）：`/chat/completions` 流出来的是
OpenAI 的 `{"choices":[{"delta":{"content":…}}],"object":"chat.completion.chunk"}` ——
**没有 `type`、没有 `sequence_number`** ⇒ 喂进 `ResponseAccumulator.feed()` 在**第一个事件**
上就抛 `ResponseProtocolError`。而我们要的 `reasoning` 与 `status` **只在 `/responses` 里**
（chat chunk 的 delta 只有 `content`）⇒ 这一层就是把那两样**补出来**。

⚠️ **序号（`sequence_number`）是我们造的**：累加器要求它**严格递增**（重复 ⇒ 协议错），
   而 chat 那一边根本没有这个字段 ⇒ 适配器自己数（一个流一个计数器，从 0 起）。
⚠️ **`[DONE]` 与 `finish_reason` 两条都要认**：有的供应商先给 `finish_reason` 再给 `[DONE]`，
   有的只给一条 ⇒ 终态**只许发一次**，两条路都要落到"发过就算"。
"""
from __future__ import annotations
import inspect

import asyncio
import contextlib
import contextvars
import uuid
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any, AsyncGenerator, AsyncIterable, AsyncIterator, Mapping

from .stream import iter_sse_events
from .transport import LLMError

#: 三条协议（ 与 `engine.Profile.kind` 同一套字）。
KIND_RESPONSES = "responses"
KIND_CHAT = "chat"
#: **2026-10-12（作者：「现在就补」）**：第三套 —— Anthropic 的 `/v1/messages`。
#: ⚠️ **它不是 chat 的方言**，是另一套话：`system` 那一句在**顶层**、消息只有
#:   `user` ／ `assistant` 两种、工具是 `tool_use` ／ `tool_result` 那两块、
#:   工具声明用 `input_schema`。⇒ 于是它同时要**另一支请求体塑形**与**另一支流翻译**。
KIND_ANTHROPIC = "anthropic"
KINDS: tuple[str, ...] = (KIND_RESPONSES, KIND_CHAT, KIND_ANTHROPIC)

#: **那一家的版本头**（2026-10-12）：它要求每一条请求都带 `anthropic-version`，
#: 而缺它回的是 `400 invalid_request_error: missing anthropic-version`（不是 401）。
#: ⚠️ **`web.py` 自己也有一份**（那个模块零同胞 import，只能各写一份；
#: `tests/test_protocol_anthropic.py::test_the_two_places_say_the_same_version` 盯着漂移 ——
#: 与 `USER_AGENT` 那一对同一个成法）。
ANTHROPIC_VERSION = "2023-06-01"

#: 两边**都认**的几格（设计稿 §6.2 那张表）。
PORTABLE_FIELDS: tuple[str, ...] = ("temperature", "max_tokens", "context_window")

#: ⚠️ **只有 `responses` 系与本地那一类有意义**的两格：
#:   · 思维链开关：chat 系的在线供应商**多半忽略或报错**；
#:   · 推理强度：同上（`chat:…` 那一边今天没有这一格的落点）。
#:   ★ **Anthropic 也不认这两格**（2026-10-12）：它那一套里"想不想"是
#:     `thinking: {"type": "enabled", "budget_tokens": N}` —— **另一个形状**，
#:     而这一层今天只发"认得的字段名"⇒ 先如实回 `False`（页上那一格会灰掉），
#:     要支持得另开一格子（那是下一刀的事，记在 `_OWED` 上）。
LOCAL_ONLY_FIELDS: tuple[str, ...] = ("thinking", "reasoning_effort")

#: 页上那几格的名字（`accepts()` 认的那一套）—— 与 `engine.KEYS` **有意重合但不相同**：
#: `engine.KEYS` 是"能存哪几个键"，这一张是"**这一条档案认不认它**"。
FIELDS: tuple[str, ...] = PORTABLE_FIELDS + LOCAL_ONLY_FIELDS

#: 本机那几个字面量（照 `web._is_loopback` 同一条口径：**只认字面量**，不查 DNS）。
_LOOPBACK = ("127.0.0.1", "::1", "[::1]", "localhost")


def accepts(kind: str, field: str) -> bool:
    """ **能力问句**：这一条档案认不认这一格（设计稿 §6.2）。

    ⚠️ 它是**页上那一格该显示还是该灰掉**的唯一依据 —— 一个"两边都画、发出去再看报错"的
       实现，会在一条 DeepSeek 档案上把两格**画成能用的样子**（而它们会被忽略或报错）。
    ⚠️ 判"认不认"的这一层**不认得的字段一律 `False`**（不然写错一个名字就成了"认"）。
    """
    if kind not in KINDS:
        return False
    if field in PORTABLE_FIELDS:
        return True
    if field in LOCAL_ONLY_FIELDS:
        return kind == KIND_RESPONSES
    return False


def is_local(base_url: str | None) -> bool:
    """这一条档案是不是**本机那一个** —— `chat_template_kwargs` 只发给它（设计稿 §6.1 末句）。"""
    text = (base_url or "").strip().lower()
    if "://" not in text:
        return False
    host = text.split("://", 1)[1].split("/", 1)[0]
    host = host.rsplit("@", 1)[-1]              # 去掉 userinfo（有的话）
    if host.startswith("["):
        # IPv6 那个方括号：**先剥括号再取主机** ——
        #   ⚠️ 先 `split(":")` 的话，`[::1]:8000` 会被切成空串（判成"不是本机"）。
        host = host[1:].split("]", 1)[0]
    else:
        host = host.split(":", 1)[0]
    return host in ("127.0.0.1", "::1", "localhost")


def endpoint_for(kind: str) -> str:
    """这一条协议的那个端点（ 只回那一段路径，拼的规矩与 `transport.py:131` 同一条）。"""
    if kind == KIND_CHAT:
        return "/chat/completions"
    if kind == KIND_RESPONSES:
        return "/responses"
    if kind == KIND_ANTHROPIC:
        # ⚠️ **那一截 `/v1` 归 `base_url`**（与 OpenAI ／ DeepSeek 那几条同一个成法：
        #    `https://api.anthropic.com/v1` ＋ `/messages`）——
        #    把 `/v1/messages` 整段写在这儿，会让"地址里已经带 `/v1`"的档案拼出 `/v1/v1/…`。
        return "/messages"
    raise ValueError(f"不认这条协议：{kind!r}（只认 {list(KINDS)}）")


#: **每一边认的名字不一样**：`responses` 认 `input_image`、`chat` 认 `image_url`
#:    —— ⚠️ 我第一版两边都是 `image_url`，判据当场红了（它对得很准）。
_IMAGE_NAME = {"responses": "input_image", "chat": "image_url"}
#: 两边都算“图片那一片”的类型名（收不收先看这个）。
_IMAGE_TYPES = {"input_image", "image_url"}


def accepts_media(multimodal: Any, mime: Any) -> bool:
    """ **收不收这种输入**（2026-10-05，照参考 `ProviderModule.accepts?`）。

    ⚠️ 规矩就两条：**这一条档案把图像输入开着**（`multimodal`）**且**那个媒体是图
    （`mime` 以 `image/` 开头）⇒ 收；其余一律不收。
     参考那一句注释是："图像输入的手动开关；provider 可通过 `accepts` 提供具体的 MIME 支持判断"
      —— 本仓先把"开关 ＋ image/ 开头"这一层做出来（各家更细的 MIME 表等真要用再说）。
    """
    return bool(multimodal) and str(mime or "").lower().startswith("image/")


def _image_url(value: Any, resolver: Any) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("url")
    if not isinstance(value, str):
        return None
    if value.startswith("plugin-media:"):
        if not callable(resolver):
            return None
        try:
            value = resolver(value)
        except Exception:  # an unavailable optional media plugin must not lose the text turn
            return None
    return value if isinstance(value, str) and value else None


def _shape_media(kind: str, parts: list[Any], *, multimodal: Any,
                 image_resolver: Any = None) -> list[Any]:
    """ 图片那几片：**收就映射成这一边认的那个名字与形状，不收就拦住**（只留文本那几片）。

    ⚠️⚠️ **两边不只是名字不同，内部形状也不同**（2026-10-05「图片输入」最小路那一下量到的）：
      · `responses` ⇒ `{"type": "input_image", "image_url": "<字符串>"}`；
      · `chat` ⇒ `{"type": "image_url", "image_url": {"url": "<字符串>"}}` —— **包一层** 。
    ⇒ 所以这一处同时做两件事：**改名**（`_IMAGE_NAME`）＋ **把值的形状摆正** 。
    文本那一片同理：`responses` 叫 `input_text`、`chat` 叫 `text` 。
    """
    out: list[Any] = []
    for one in parts:
        if not isinstance(one, Mapping):
            out.append(one)
            continue
        kind_of_part = str(one.get("type") or "")
        if kind_of_part in _IMAGE_TYPES:
            if not accepts_media(multimodal, "image/*"):
                continue # 不收 ⇒ 拦住（不发出去）
            # 值可能已经是对象（chat 那边来的）⇒ 一律**先取到那根字符串**
            raw_url = _image_url(one.get("image_url"), image_resolver)
            if raw_url is None:
                out.append({"type": "input_text" if kind == KIND_RESPONSES else "text",
                            "text": "[图片内容暂不可用]"})
                continue
            if kind == KIND_ANTHROPIC:
                if raw_url.startswith("data:image/") and ";base64," in raw_url:
                    media_type, encoded = raw_url[5:].split(";base64,", 1)
                    source = {"type": "base64", "media_type": media_type, "data": encoded}
                else:
                    source = {"type": "url", "url": raw_url}
                out.append({"type": "image", "source": source})
                continue
            fixed = dict(one)
            if kind == KIND_CHAT:
                fixed["type"] = _IMAGE_NAME[KIND_CHAT]
                fixed["image_url"] = {"url": raw_url}
            else:
                fixed["type"] = _IMAGE_NAME[KIND_RESPONSES]
                fixed["image_url"] = raw_url
            out.append(fixed)
            continue
        if kind_of_part == "input_text" and kind in (KIND_CHAT, KIND_ANTHROPIC):
            # 文本那一片也换名（`input_text` ⇒ `text`）—— 否则那两边认不出它
            out.append({"type": "text", "text": one.get("text") or ""})
            continue
        out.append(one)
    return out


def flatten_tools(tools: Any) -> list[dict[str, Any]]:
    """ **把工具拍平**（2026-10-07 修的根因）：`responses` 那一档不吃 `function` 那一层。

    ⚠️ 我们发的是 OpenAI 常见的嵌套写法：`{"type": "function", "function": {…}}`；
        而它那一台（`ninfer-serve-89` 的 `responses`）只认**扁平**：
       `{"type": "function", "name": …, "description": …, "parameters": …}`
       ⇒ 嵌套的那种它当场回 **400 `unsupported tools member: function`**
       ⇒ 而 `generate` 会重试 ⇒ **看起来就是"一句话都不回"** （真跑量到 ）。

    ⚠️ 已经扁平的**原样放过** （不重复处理 ）；认不出的**原样留着**
       （ 不许在这里悄悄丢工具 —— 丢了比报错更难查 ）。
    """
    out: list[dict[str, Any]] = []
    for one in tools or ():
        if not isinstance(one, Mapping):
            out.append(one)
            continue
        inner = one.get("function")
        if one.get("type") == "function" and isinstance(inner, Mapping):
            flat = {"type": "function"}
            flat.update({str(k): v for k, v in inner.items()})
            out.append(flat)
        else:
            out.append(dict(one))
    return out


#: **工具那两条的 `type`** —— 它们**没有 `content`**（带的是 `arguments` ／ `output`）。
#: ⚠️⚠️ 这一层踩过一个大坑（2026-10-17 才查出来）：`clean_items` 原来按"`content` 空不空"
#:   判"这条要不要",而工具那两条**永远"空"** ⇒ 本机那一档
#:   （`KIND_RESPONSES`，**只有它调 `clean_items`**）把**每一次工具调用与它的回执**
#:   全扔了 ⇒ 那个模型手上只有「用户说的话 ＋ 它自己说过的话」，
#:   **从来不知道自己查过什么、查回来什么**。
#:   ⇒ 表现就是：同一句 `kb_search` 问 5—28 遍、`kb_read` 一辈子不调
#:   （因为没有"我刚查过"的记忆）—— 而**这不是模型的能力问题**。
_TOOL_ITEM_TYPES = ("function_call", "function_call_output")


def clean_items(items: Any) -> list[Any]:
    """ **把 `input` 那些项滤干净**（2026-10-07 修的根因）。

    ⚠️ 不滤会怎样（真跑量到）：`input` 里混进 `None` 项 ⇒ 那台引擎当场
       **400「input Item must contain type」** ⇒ `generate` 重试 ⇒ **它一句都不回**
       —— 而日志里**什么都看不见**（`loop` 只记"一轮失败"） 所以这一条特别难查 。

    ⚠️ 滤掉的是： `None` ／ 不是映射的项 ／ **空 content 的项** 。
        留的是：任何像"一条消息"的东西 （ 不认识的形状**原样留** ⇒ 让对面的错说清楚 ）。

    ⚠️⚠️ **工具那两条例外**（2026-10-17 修）：`function_call` ／ `function_call_output`
       **天生没有 `content`** ⇒ 照"空 content"判就会把**每一次工具调用与它的回执**
       一起扔掉（本机那档正是这样，模型因此没有"我刚查过"的记忆）。
       ⇒ 它们的 `type` 认得出来，**先放行**，再谈 `content`。
    """
    out: list[Any] = []
    for one in items or ():
        if one is None or not isinstance(one, Mapping):
            continue
        if str(one.get("type") or "") in _TOOL_ITEM_TYPES:
            out.append(dict(one))
            continue
        body = one.get("content")
        if body is None or (isinstance(body, (list, tuple, str)) and len(body) == 0):
            continue
        out.append(dict(one))
    return out


def _responses_history_notices(items: list[Any]) -> list[Any]:
    """Represent unclosed calls as history, never as invented tool results.

    Operates on the already recipient-filtered request only. Does not read or
    repair the durable session, dispatch a tool, or infer execution status.
    Complete pairs retain their IDs, relative order and contents.
    """
    answered = {one.get("call_id") for one in items
                if isinstance(one, Mapping)
                and one.get("type") == "function_call_output"
                and isinstance(one.get("call_id"), str) and one["call_id"]}
    out: list[Any] = []
    for one in items:
        if (isinstance(one, Mapping) and one.get("type") == "function_call"
                and isinstance(one.get("call_id"), str) and one["call_id"]
                and one["call_id"] not in answered):
            # A proposal is not evidence that a tool ran, failed, or succeeded.
            # Keep its content at this position, but not as an active API call.
            details = json.dumps({"call_id": one["call_id"],
                                  "name": one.get("name", ""),
                                  "arguments": one.get("arguments", "")},
                                 ensure_ascii=False, separators=(",", ":"))
            out.append({"role": "assistant", "content": (
                "〔历史工具请求说明〕以下仅是历史中记录的工具请求；当前可见记录没有对应结果，"
                "无法确认是否执行、成功或失败。这不是工具返回，也不是要求重新执行。\n" + details)})
        else:
            out.append(one)
    return out


def _anthropic_blocks(body: Any, *, multimodal: Any,
                      image_resolver: Any = None) -> list[dict[str, Any]]:
    """ 一条消息的 `content` ⇒ Anthropic 那几片（`text` ／ `image`）。"""
    if isinstance(body, str):
        return [{"type": "text", "text": body}] if body else []
    out: list[dict[str, Any]] = []
    for one in body or ():
        if not isinstance(one, Mapping):
            continue
        if str(one.get("type") or "") in _IMAGE_TYPES:
            if not accepts_media(multimodal, "image/*"):
                continue                        # 不收 ⇒ 拦住（与另外两条同一条口径）
            url = _image_url(one.get("image_url"), image_resolver)
            if url is None:
                out.append({"type": "text", "text": "[图片内容暂不可用]"})
            elif url.startswith("data:image/") and ";base64," in url:
                media_type, encoded = url[5:].split(";base64,", 1)
                out.append({"type": "image", "source": {
                    "type": "base64", "media_type": media_type, "data": encoded}})
            else:
                out.append({"type": "image", "source": {"type": "url", "url": url}})
            continue
        said = one.get("text")
        if isinstance(said, str) and said:
            out.append({"type": "text", "text": said})
    return out


def _anthropic_tools(tools: Any) -> list[dict[str, Any]]:
    """ 工具声明 ⇒ Anthropic 那一形：`{"name", "description", "input_schema"}`。

    ⚠️ 我们手上那两份（拍平的 `{"type":"function","name",…,"parameters"}` 与嵌套的
       `{"type":"function","function":{…}}`）都要认 —— 而**认不出的原样留着**
       （不许在这一层悄悄丢工具：丢了比报错更难查，与 `flatten_tools` 同一条）。
    """
    out: list[dict[str, Any]] = []
    for one in tools or ():
        if not isinstance(one, Mapping):
            out.append(one)
            continue
        inner = one.get("function")
        source = inner if isinstance(inner, Mapping) else one
        name = source.get("name")
        if not isinstance(name, str) or not name:
            out.append(dict(one))
            continue
        made: dict[str, Any] = {"name": name}
        if source.get("description") is not None:
            made["description"] = str(source.get("description") or "")
        schema = source.get("parameters")
        if schema is None:
            schema = source.get("input_schema")
        made["input_schema"] = dict(schema) if isinstance(schema, Mapping) else {"type": "object"}
        out.append(made)
    return out


def _to_anthropic_body(out: Mapping[str, Any], *, multimodal: Any,
                       image_resolver: Any = None) -> dict[str, Any]:
    """ 把那份规范的 Responses 请求体摆成 Anthropic 认的样子（`/v1/messages`）。

    四样不同（逐条照它文档对的）：
      ① **`system` 那一句提到顶层**（它不许 `role: "system"` 出现在 `messages` 里）；
      ② 只有 `user` ／ `assistant` 两种角色 —— `tool_result` 归 **user** 那一侧；
      ③ 工具那两项**换身份**：`{"type":"function_call"}` ⇒ assistant 的一条 `tool_use`；
         `{"type":"function_call_output"}` ⇒ user 的一条 `tool_result`；
      ④ 工具声明用 `input_schema`（不是 `parameters`），`max_output_tokens` → `max_tokens`。

    ⚠️ **相邻同角色的几条要并成一条**（它那边 `messages` 是"一条一条消息"，
       而 `tool_use` ／ `tool_result` 都是**消息内部的块**）—— 不并的话它回
       `messages: roles must alternate`。
    ⚠️ **它不认的键一个都不留**（`input` ／ `max_output_tokens` ／
       `chat_template_kwargs` 那几样发了就是 400）。
    """
    systems: list[str] = []
    messages: list[dict[str, Any]] = []

    def push(role: str, block: dict[str, Any]) -> None:
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].append(block)
        else:
            messages.append({"role": role, "content": [block]})

    for one in out.get("input") or ():
        if not isinstance(one, Mapping):
            continue
        which = str(one.get("type") or "message")
        if which == "function_call":
            raw = one.get("arguments")
            try:
                parsed = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
            except ValueError:
                # 参数不是合形 JSON ⇒ **照原样包一层**（不猜、也不丢）
                parsed = {"arguments": raw}
            if not isinstance(parsed, Mapping):
                parsed = {"value": parsed}
            push("assistant", {"type": "tool_use", "id": str(one.get("call_id") or ""),
                               "name": str(one.get("name") or ""), "input": parsed})
            continue
        if which == "function_call_output":
            push("user", {"type": "tool_result",
                          "tool_use_id": str(one.get("call_id") or ""),
                          "content": str(one.get("output") or "")})
            continue
        role = str(one.get("role") or "user")
        body = one.get("content")
        if role == "system":
            for block in _anthropic_blocks(body, multimodal=False):
                if block.get("type") == "text":
                    systems.append(str(block.get("text") or ""))
            continue
        if role not in ("user", "assistant"):
            role = "user"                     # 它只认这两种（别的**归到 user**，不丢内容）
        for block in _anthropic_blocks(body, multimodal=multimodal,
                                       image_resolver=image_resolver):
            push(role, block)

    made: dict[str, Any] = {}
    for key in ("model", "stream"):
        if key in out:
            made[key] = out[key]
    budget = out.get("max_output_tokens")
    if budget is not None:
        made["max_tokens"] = budget
    kept_system = "\n\n".join(one for one in systems if one)
    if kept_system:
        made["system"] = kept_system
    # ⚠️⚠️ **每个 `tool_use` 都要配一条 `tool_result`**（2026-10-17，与 chat 那一支同一个病）：
    #   它会话里**真有**"只有调用、没有结果"的记录（没定案的调用只留调用那一笔）——
    #   chat 那家当场 400，**这一家也一样**（"tool_use ids were found without tool_result
    #   blocks"）⇒ 这里补一条**明说"没有回执"**的 `tool_result`（`NO_RECEIPT`，不编结果）。
    #   ⚠️ **位置必须在它那条 `tool_use` 之后紧邻的那一条 user 上**（这一家就这么要求的），
    #     所以是"插在它后面"，不是"统一补到末尾"。
    made["messages"] = _fill_missing_tool_results(messages)
    if out.get("tools"):
        made["tools"] = _anthropic_tools(out["tools"])
    return made


def _fill_missing_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ 给**没有回应的 `tool_use`** 补一条 `tool_result`（`NO_RECEIPT`）—— 交回新的一份表。

    ⚠️ **为什么非补不可**：`tool_use` 与 `tool_result` 在这一家**成对**，
       缺一条整份请求就被退回来（`tool_use ids were found without tool_result blocks`）
       ⇒ 后果是"它在这家**一辈子**说不了话"，而不是"这一轮没说话"。
    ⚠️ **补的是"没有回执"这句话**，不是编一个结果 —— 与 chat 那一支**同一句话**（`NO_RECEIPT`）。
    ⚠️ **位置**：紧接在那条 `tool_use` 的 assistant 之后（这一家就这么要求的），
       而不是统一补到末尾。
    ⚠️ **已经被真回执答过的那些不许再补**（实况里绝大多数是有回执的）。
    """
    from copy import deepcopy
    messages = deepcopy(messages)
    out: list[dict[str, Any]] = []
    allowed: set[str] = set()
    for index, message in enumerate(messages):
        content = message.get('content')
        results = [b for b in content if isinstance(b, dict) and b.get('type') == 'tool_result'] if isinstance(content, list) else []
        result_ids = [b.get('tool_use_id') for b in results]
        if results and (message.get('role') != 'user' or any(type(i) is not str or i not in allowed for i in result_ids)
                        or len(result_ids) != len(set(result_ids))):
            raise ValueError('tool results require unique adjacent call identities')
        allowed = set()
        out.append(message)
        blocks = message.get("content")
        if not isinstance(blocks, list) or message.get("role") != "assistant":
            continue
        ids = [one.get('id') for one in blocks if isinstance(one, dict) and one.get('type') == 'tool_use']
        if any(type(i) is not str or not i for i in ids) or len(ids) != len(set(ids)):
            raise ValueError('tool calls require unique nonempty identities')
        allowed = set(ids)
        following = messages[index + 1] if index + 1 < len(messages) else None
        adjacent = following.get('content') if following and following.get('role') == 'user' else None
        adjacent_results = [b for b in adjacent if isinstance(b, dict) and b.get('type') == 'tool_result'] if isinstance(adjacent, list) else []
        if any(type(b.get('tool_use_id')) is not str for b in adjacent_results):
            raise ValueError('tool result identity must be a string')
        answered = {b['tool_use_id'] for b in adjacent_results}
        missing = [identity for identity in ids if identity not in answered]
        if missing:
            fillers = [
                {"type": "tool_result", "tool_use_id": one, "content": NO_RECEIPT}
                for one in missing]
            if isinstance(adjacent, list):
                following['content'] = fillers + adjacent_results + [b for b in adjacent if not (isinstance(b, dict) and b.get('type') == 'tool_result')]
            else:
                out.append({'role': 'user', 'content': fillers})
                allowed = set()
    return out


#: 那一条"**没有回执**"的补白（见 `shape()` 里 `KIND_CHAT` 那一段）。
#: ⚠️ **它不是编出来的结果** —— 它说的是"那次调用没有定案"这件事本身；
#:    少了它，chat 那一家会因为"`tool_calls` 后面没有 `tool`"把**整份请求**退回来。
#: ⚠️ **Anthropic 那一支要的是同一个意思**（`tool_use` 必须配 `tool_result`），
#:    所以这句话两边共用 —— 别写成两份（两份就会漂）。
NO_RECEIPT = "（这一条没有回执 —— 那次调用没有定案）"

#: ★ **组请求时保留眼前这几条工具回执的原样，更早的折叠成一句话**（2026-10-17）。
#:
#: ⚠️⚠️ **它治的是什么**（量出来的）：它那份会话 **495 行 ／ 49501 字**，而其中
#:   **`function_result` 占 26061 字 ＝ 53%** —— 全是同一类、长得很像的工具回执堆着。
#:   那一份请求 **91938 字**，而本机那个 27B 的窗口是 262144 token ⇒ 压缩阈值
#:   `context_window − max_tokens` ＝ **245760 token** ⇒ **永远不触发**
#:   ⇒ 它会一直面对着越堆越多的回执。
#:   外面那篇（`effective-context-engineering-for-ai-agents`）把这一手叫
#:   **tool result clearing**："一条回执深埋在历史里之后，为什么还要再看一遍原文"。
#:
#: ⚠️ **只折叠正文、不动调用**：`function_call` 留着（22%），`function_call_output` 换一句话。
#:   原因是**配对**：那两家都要求"每个 `tool_calls` ／ `tool_use` 必须有回应"，
#:   把调用一起删掉就会变成今天刚修的那个 400（孤立的调用）
#:   ⇒ 折叠之后**配对关系天然不破**（调用在、回应也在，只是回应变短）。
#:
#: ⚠️ **那一句话必须说清"它原来有内容"**：否则它会当成"那次调用没回东西"而**再问一遍**。
#: ⚠️ **但它也得是中性的话**（2026-10-17 收的）：原来那句写的是"**回执已折叠：原来 N 字**" ——
#:    那是**机器腔**，而它躺在它会读到的地方 ⇒ 它有可能**照念出来**（那就不是它的口吻了）。
#:    量过：它 66 条自己说的话里**一次都没漏过**这类词（文件名 ／ 参数 ／ 回执腔 全是 0），
#:    所以这是**收风险**、不是补漏洞 —— 顺手把机器词（回执 ／ 折叠 ／ 字数）都拿掉。
KEEP_TOOL_OUTPUTS = 8
FOLDED_TOOL_OUTPUT = "（这段先前的查询结果已不在手边 —— 要它的时候再查一次就行。）"


def _fold_old_outputs(items: list[Any]) -> list[Any]:
    """ 把**早先那些工具回执**换成一句话（最近 `KEEP_TOOL_OUTPUTS` 条照原样）—— 交回新的一份表。

    ⚠️ **它不碰会话**：只动这一轮要发出去的那一份（会话该多长还是多长）。
    ⚠️ **它是纯操作**：递进来的那份表与里面的项**都不被就地改**
       （`{**one, ...}` 造新的；否则会改到调用方手上那一份）。
    """
    from .receipt_projection import CurrentBatchOutput

    def historical_output(one):
        return (isinstance(one, Mapping) and one.get("type") == "function_call_output"
                and not isinstance(one.get("output"), CurrentBatchOutput))

    total = sum(1 for one in items if historical_output(one))
    keep_from = max(0, total - max(1, int(KEEP_TOOL_OUTPUTS)))
    out: list[Any] = []
    seen = 0
    for one in items:
        if not historical_output(one):
            out.append(one)
            continue
        index, seen = seen, seen + 1
        if index >= keep_from:
            out.append(one)
            continue
        # ⚠️ 那句是**中性的话**（不含字数 ／ 不含"回执""折叠"这类机器词）——
        #    理由见 `FOLDED_TOOL_OUTPUT` 上面那一段：它躺在它读得到的地方，别给它可照念的机器腔。
        out.append({**one, "output": FOLDED_TOOL_OUTPUT})
    return out


def _stable_receipt_history(kind, base_url, model):
    if deepseek_chat(base_url, kind):
        return True
    # The verified Qwen route exposes a quantization id. Limit this policy to
    # the local Strata gateway and its LAN listener, including the Windows client.
    from .batch_prompt import verified_local_qwen
    return verified_local_qwen(kind, base_url, model)


def shape(kind: str, body: Mapping[str, Any], *, base_url: str | None,
          thinking: Any = None, temperature: Any = None,
          reasoning_effort: Any = None, multimodal: Any = None,
          reasoning_budget_tokens: Any = None,
          image_resolver: Any = None) -> dict[str, Any]:
    """把 `generate.build_body()` 交出来的那份 Responses 请求体**塑成这一条档案认的**。

     三条规矩：
      ① `chat` 那一边键名不同：`input` → **`messages`**、`max_output_tokens` → **`max_tokens`**；
      ② `chat_template_kwargs` **只发给本机那一个**（它是 llama.cpp 风味，在线供应商不认）；
      ③ `temperature` 两边都给；`reasoning_effort` 只给认它的那一边（`accepts()` 说了算）。
    ⚠️ **`None` 当"这一格没给"** —— 不许把 `null` 发出去（那与"没给"在上游不是一回事）。
    """
    out: dict[str, Any] = dict(body)
    if reasoning_budget_tokens is not None:
        # Extension verified for Strata, not generic OpenAI Responses syntax.
        url = urlsplit(base_url or "")
        if not (url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")
                and url.port == 47831 and url.path.rstrip("/") == "/v1"
                and not url.username and not url.password and not url.query and not url.fragment
                and kind in (KIND_RESPONSES, KIND_CHAT)):
            raise ValueError("独立思考预算当前仅支持已验证的本机 Strata 网关（47831/v1）")
        total = out.get("max_output_tokens", out.get("max_tokens"))
        enabled = thinking
        if enabled is None:
            enabled = (out.get("chat_template_kwargs") or {}).get("enable_thinking")
        if (type(reasoning_budget_tokens) is not int or reasoning_budget_tokens <= 0
                or enabled is False or (type(total) is int and reasoning_budget_tokens >= total)):
            raise ValueError("思考预算须为正整数、小于总输出额度，且思维链须开启")
        out["reasoning_budget_tokens"] = reasoning_budget_tokens
    # For prefix-cached routes keep the authorized request projection stable
    # until the existing context budget triggers a handoff. Aging one receipt
    # would force reprocessing of all later history, including ordinary messages.
    if out.get("input") and not _stable_receipt_history(kind, base_url, out.get("model")):
        out["input"] = _fold_old_outputs(list(out["input"]))
    # **第三套（Anthropic）自己那一支**（2026-10-12）——
    #    它连"键名"都不是同族的：`system` ／ `messages` ／ `tool_use` ／ `input_schema`
    #    ⇒ 走一条**独立**的摆法，不在这几段里拼（拼出来会四不像）。
    if kind == KIND_ANTHROPIC:
        made = _to_anthropic_body(out, multimodal=multimodal,
                                  image_resolver=image_resolver)
        if temperature is not None and accepts(kind, "temperature"):
            made["temperature"] = temperature
        return made
    # 2026-10-07：**工具要拍平**（理由见 `flatten_tools` 那一整段）——
    # 只对 `responses` 那一档做（`chat` 那一边吃的就是嵌套那一种 ）。
    if kind == KIND_RESPONSES and out.get("input"):
        # 2026-10-07：**input 里不许混进 None ／ 空项**（理由见 `clean_items`）。
        out["input"] = _responses_history_notices(clean_items(out["input"]))
        if is_local(base_url):
            # Some llama.cpp Responses parsers classify assistant messages only
            # with an explicit message type. Add the discriminator, not content;
            # leave tool/reasoning items and remote provider wire unchanged.
            out["input"] = [
                {**item, "type": "message"}
                if item.get("role") == "assistant" and item.get("type") is None
                else item for item in out["input"]
            ]
    if kind == KIND_RESPONSES and out.get("tools"):
        out["tools"] = flatten_tools(out["tools"])
    if temperature is not None and accepts(kind, "temperature"):
        out["temperature"] = temperature
    if reasoning_effort is not None and accepts(kind, "reasoning_effort"):
        # 2026-10-04 更正：引擎要的是**嵌套**的 `reasoning.effort` ——
        #    顶层 `reasoning_effort` 一律 400，而且那 400 的正文自己就写着
        #    `param: "reasoning.effort"`（当时没读那一位，只读了"400"）。
        # 实测（`POST /v1/responses`）：顶层 400 ／ 嵌套 200 。
        #
        # ⚠️ 而它与 `chat_template_kwargs.enable_thinking` **是同一件事的两种说法** ——
        #    两个一起发、且说法相反时引擎报
        #    `conflicting_template_option: reasoning effort conflicts with enable_thinking`。
        #    实测矩阵（`thinking` × `effort`）：
        # `False` ＋ `low|medium|high` ⇒ 400 ／ `True` ＋ `none` ⇒ 400 ；
        # 其余全部 200 。
        #    ⇒ **显式开关优先**：说法相反时让档位让位，不把一个必然 400 的组合发出去。
        # ⚠️ 而"开关"有**两份来源**：调用方给的 `thinking` ，以及 body 里**已经带着**的
        # `chat_template_kwargs.enable_thinking` （下面那一句只在 `is_local` 且
        # `thinking is not None` 时改写它 ⇒ 没给的时候它原样发出去 ）。
        #       两份里**先有的那一份**说了算，否则这道闸只堵住一半。
        effective = thinking
        if effective is None:
            existing = out.get("chat_template_kwargs")
            if isinstance(existing, Mapping) and "enable_thinking" in existing:
                effective = existing["enable_thinking"]
        if effective is not None:
            level_says_thinking = str(reasoning_effort).strip().lower() != "none"
            if bool(effective) != level_says_thinking:
                reasoning_effort = None
        if reasoning_effort is not None:
            out.setdefault("reasoning", {})["effort"] = reasoning_effort
    if kind == KIND_CHAT:
        # DeepSeek's explicit Chat toggle is not generic OpenAI Chat syntax.
        # Relays must not be inferred from a model name or a URL substring.
        if (urlsplit(base_url or "").hostname == "api.deepseek.com"
                and isinstance(thinking, bool)):
            out["thinking"] = {"type": "enabled" if thinking else "disabled"}
        if (urlsplit(base_url or "").hostname == "api.deepseek.com"
                and reasoning_effort is not None):
            # DeepSeek Chat takes a top-level effort, unlike Responses' reasoning.effort.
            out["reasoning_effort"] = reasoning_effort
        # ⚠️ `pop` 之后**不留空键**：一条 chat 请求里带着 `input` 会被上游当未知参数。
        messages = []
        for item in out.pop("input", []):
            if isinstance(item, Mapping) and item.get("type") == "function_call":
                call = {"id": item.get("call_id"), "type": "function", "function": {
                    "name": item.get("name"), "arguments": item.get("arguments", "")}}
                # Preserve distinct replay bindings, while same-reply text and
                # parallel calls can still share one assistant envelope.
                same_binding = (messages and messages[-1].get("_provider_replay_group")
                                == item.get("_provider_replay_group"))
                if messages and messages[-1].get("role") == "assistant" and same_binding:
                    messages[-1] = {**messages[-1],
                                   "tool_calls": [*messages[-1].get("tool_calls", []), call]}
                else:
                    messages.append({"role": "assistant", "content": None,
                                     "tool_calls": [call]})
                if "_provider_replay_group" in item:
                    messages[-1]["_provider_replay_group"] = item["_provider_replay_group"]
                if "_provider_replay_source" in item:
                    messages[-1].setdefault("_provider_replay_sources", []).append(
                        item["_provider_replay_source"])
            elif isinstance(item, Mapping) and item.get("type") == "function_call_output":
                messages.append({"role": "tool", "tool_call_id": item.get("call_id"),
                                 "content": item.get("output", "")})
            else:
                messages.append(item)
        # ⚠️⚠️ **每条 `tool_calls` 后面都必须跟一条对应的 `tool`**（2026-10-17 实测）：
        #   它那份会话里**真有"只有调用、没有结果"的记录**（当天数出来：
        #   `function_call` 124 条 ／ `function_result` 122 条 ⇒ `那一侧_send_voice` 多 2 条
        #   —— 而那是**设计如此**：`round.py` 写着"没定案的调用只留那一条 `function_call`"）。
        #   `/v1/responses` 容得下，**chat 那一家直接 400**（原样）：
        #   `An assistant message with 'tool_calls' must be followed by tool messages
        #    responding to each 'tool_call_id'. (insufficient tool messages following
        #    tool_calls message)`。
        #   ⇒ 这里补一条**明说"没有回执"**的 tool 消息：**不编结果**，
        #     只是把"那次调用确实没定案"这件事照实说出来。
        #     ⚠️ 少了它，**整份请求都发不出去** —— 而那不是"它这一轮没说话"，
        #       是"它这一辈子在 chat 那一家都说不了话"（换协议就坏）。
        #   ⚠️ 位置：**紧跟在它那一条 assistant 之后**（那几家都是这么要求的）。
        answered = {str(one.get("tool_call_id")) for one in messages
                    if isinstance(one, Mapping) and one.get("role") == "tool"}
        filled: list[Any] = []
        for one in messages:
            filled.append(one)
            if not isinstance(one, Mapping) or not one.get("tool_calls"):
                continue
            for call in one.get("tool_calls") or ():
                key = str(call.get("id"))
                if key not in answered:
                    filled.append({"role": "tool", "tool_call_id": call.get("id"),
                                   "content": NO_RECEIPT})
                    answered.add(key)
        # Some Chat gateways reject assistant/tool history before the first
        # user turn (e.g. a system-triggered continuation after handoff). Keep
        # every historical item and its role; add only a transport boundary.
        # It is not appended to Session or represented as an actual user event.
        first = next((i for i, m in enumerate(filled)
                      if isinstance(m, Mapping) and m.get("role") not in ("system", "developer")), None)
        if filled and (first is None or filled[first].get("role") == "assistant"):
            filled.insert(len(filled) if first is None else first, {"role": "user", "content":
                "[系统事件边界：当前唤醒或后续助手与工具历史由系统事件触发，并非用户发言。"
                "此条仅用于协议兼容，不包含新的用户请求。]"})
        from .batch_prompt import local_chat_url, official_deepseek_url, verified_local_qwen
        deepseek = deepseek_chat(base_url, kind)
        if deepseek or local_chat_url(kind, base_url):
            from .receipt_projection import compact_read_receipts
            filled = compact_read_receipts(filled, full_repeats=deepseek,
                                           group_locations=verified_local_qwen(kind, base_url, out.get("model")),
                                           rolling_tail=deepseek and official_deepseek_url(kind, base_url))
        out["messages"] = filled
        if out.get("tools"):
            nested = []
            for tool in out["tools"]:
                if (isinstance(tool, Mapping) and tool.get("type") == "function"
                        and not isinstance(tool.get("function"), Mapping)):
                    nested.append({"type": "function", "function": {
                        key: value for key, value in tool.items() if key != "type"}})
                else:
                    nested.append(tool)
            out["tools"] = nested
        budget = out.pop("max_output_tokens", None)
        if budget is not None:
            out["max_tokens"] = budget
    # **图片那几片按 `multimodal` 过一遍**（收就映射、不收就拦住）——
    #     两边形状不同（`responses` ／ `chat` 各自一份 `messages` 或 `input`）⇒ 分别走。
    for key in ("input", "messages"):
        parts = out.get(key)
        if isinstance(parts, list):
            out[key] = [
                ({**one, "content": _shape_media(kind, list(one.get("content") or []),
                                                 multimodal=multimodal,
                                                 image_resolver=image_resolver)}
                 if isinstance(one, Mapping) and isinstance(one.get("content"), list) else one)
                for one in parts]
    if is_local(base_url):
        if thinking is not None:
            out["chat_template_kwargs"] = {"enable_thinking": bool(thinking)}
    else:
        # 在线那一边**一个字都不许带**（它不认这个键：要么忽略、要么报错）
        out.pop("chat_template_kwargs", None)
    return out


def _frame(event: Mapping[str, Any]) -> bytes:
    """一条事件 → 一个 SSE 帧（`data: <json>\\n\\n`）—— 与上游那一形逐字对齐。"""
    return ("data: " + json.dumps(dict(event), ensure_ascii=False) + "\n\n").encode("utf-8")


def _terminal_for(reason: str) -> tuple[str, str]:
    """ chat 的 `finish_reason` → Responses 的终态（设计稿 §6.1 那一行）。

    ⚠️ **截断不是错**：`length` 落 `response.incomplete` ＋ `incomplete_details.reason`
       —— 只认 `completed` 会把"被 `max_output_tokens` 截断"误判成协议违规
       （`stream.py` 的 `TERMINAL_TYPES` 三条都认，正是为这个）。
    ⚠️ **`tool_calls` 也是"好好地说完了"**（2026-10-12 补）：它的意思是"这一轮我说完了，
       接下来该动手了" —— 原先落 `incomplete` ⇒ `stream.finish()` 会**跳过一切对账**、
       而调用方会把一次正常的"它要动手"读成**截断**。
    """
    if reason in ("stop", "tool_calls"):
        return "response.completed", "completed"
    return "response.incomplete", "incomplete"


def _the_item_id_of_a_chat_call(slot: Mapping[str, str], index: int) -> str:
    """ chat 的一通工具调用 ↔ Responses 那一边那个 `item.id`（**一个稳定的名字**）。

    ⚠️ **为什么不能直接用 `index`**：那个 `item.id` 会被 `stream.py` 的累加器当作
       "这一格累加位"的键，而**同一个 `item.id` 会横跨好几条帧**（added ／ 若干 delta ／ done）
       ⇒ 它必须在**这一通调用的整段**里唯一且不变；缺 `id` 时才退回 `index`
       （那时同一轮里多个 index 仍然是分开的）。
    ⚠️ **它不许顶替 `call_id`**（`stream.py:387-389` 那条明写：「绝不拿 `item_id` 去填
       `call_id`」）—— 所以 `call_id` 那一格照旧原样带过去，缺就留空。
    """
    return "fc_" + (slot.get("id") or str(index))


#: ★ **2026-10-13（待办账 §二那一条 `usage_not_carried`）**：chat 那一份用量
#:   到 Responses 那一份形状的**映射**（这一层只做映射、不做判断 —— 与整份同一条）。
#: ⚠️ **两边字段名不一样**（这就是"映射"而不是"照抄"的全部理由）：
#:   · chat：`prompt_tokens` ／ `completion_tokens` ／ `total_tokens`
#:     （缓存命中在 `prompt_tokens_details.cached_tokens`）；
#:   · Responses（**下游读的那一份**）：`input_tokens` ／ `output_tokens` ／
#:     `total_tokens` ／ `input_tokens_details.cached_tokens` ——
#:     `console._keep_usage` 读的正是后两个（`input_tokens` 与
#:     `input_tokens_details.cached_tokens`）⇒ **照抄过去等于没映射**。
#: ⚠️ **不认识的东西一个都不编**：哪一格没有就不放哪一格（下游读不到就退回"这一轮没有
#:   用量"，而不是读到一个假 0 —— 与"不许把不知道画成零"同一条口径）。
def _the_usage_of_a_chat_chunk(one: Mapping[str, Any]) -> dict[str, Any] | None:
    """ **chat 那一份 `usage` → Responses 那一份**（交回 `None` ⇒ 这一条没有用量）。"""
    told = one.get("usage")
    if not isinstance(told, Mapping) or not told:
        return None
    out: dict[str, Any] = {}
    for mine, theirs in (("input_tokens", "prompt_tokens"),
                         ("output_tokens", "completion_tokens"),
                         ("total_tokens", "total_tokens")):
        got = told.get(theirs)
        if isinstance(got, int):
            out[mine] = got
    details = told.get("prompt_tokens_details")
    if isinstance(details, Mapping) and isinstance(details.get("cached_tokens"), int):
        out["input_tokens_details"] = {"cached_tokens": details["cached_tokens"]}
    return out or None


CHAT_USAGE_TAIL_SECONDS = 0.25
CHAT_USAGE_TAIL_BYTES = 65536
CHAT_USAGE_TAIL_EVENTS = 16

async def _chat_events_with_usage_tail(chunks):
    import asyncio
    from .stream import ResponseProtocolError
    tail = False
    remaining = CHAT_USAGE_TAIL_BYTES - 4096
    class TailLimit(Exception):
        pass
    async def bounded():
        nonlocal remaining
        async for chunk in chunks:
            # Bound parser look-ahead even when finish and tail share one packet.
            for start in range(0, len(chunk), 4096):
                part = chunk[start:start + 4096]
                if tail:
                    remaining -= len(part)
                    if remaining < 0:
                        raise TailLimit()
                yield part
    events = iter_sse_events(bounded())
    try:
        async for one in events:
            if not isinstance(one, dict):
                raise ResponseProtocolError("chat event must be an object")
            choices = one.get("choices")
            if choices is not None and not isinstance(choices, list):
                raise ResponseProtocolError("chat choices must be a list")
            finished = any(isinstance(c, dict) and c.get("finish_reason") for c in choices or [])
            if not finished:
                yield one
                if one.get("__done__"):
                    return
                continue
            tail = True
            deadline = asyncio.get_running_loop().time() + CHAT_USAGE_TAIL_SECONDS
            pending = dict(one)
            for _ in range(CHAT_USAGE_TAIL_EVENTS):
                timer = asyncio.timeout_at(deadline)
                try:
                    async with timer:
                        last = await anext(events)
                except (StopAsyncIteration, TailLimit):
                    break
                except TimeoutError:
                    if not timer.expired():
                        raise
                    break
                if not isinstance(last, dict):
                    raise ResponseProtocolError("chat tail must be an object")
                if last.get("__done__"):
                    break
                if last.get("choices") not in (None, []):
                    raise ResponseProtocolError("content after chat finish")
                if last.get("error") is not None:
                    raise ResponseProtocolError("error after chat finish")
                if last.get("id") is not None and last.get("id") != pending.get("id"):
                    raise ResponseProtocolError("chat tail identity changed")
                if last.get("usage") is not None:
                    last = dict(last, id=pending.get("id"))
                    pending.pop("usage", None)
                    yield last
            yield pending
            return
    finally:
        await events.aclose()

async def to_responses(chunks: AsyncIterable[bytes], *, completed: Any = None
                       ) -> AsyncIterator[bytes]:
    """ **那个翻译器**：吃 chat 的 SSE 字节，吐 Responses 事件的字节。

    ⚠️ 它**只做映射，不做判断**：`iter_sse_events` 已经把字节切成事件（跨块帧、跨块字符、
       CRLF、末尾没有空行那四件它都管了）⇒ 这一层按 §6.1 那张表逐条换名字。
    ⚠️ **`response.created` 只发一次**（累加器对重复的那一条当场抛），而它的 `response.id`
       一旦定下**不许再变** ⇒ 后面几条（含终态）带的 id 与它**同一个**。
    ⚠️ **错误体要变成 `LLMError`**（`transport.py:174-182` 那一族）—— 协议坏帧与提供商错误分别分类；
       `generate()` 不捕获 ResponseProtocolError 作重试，认证类 LLMError 也不应重试。
    """
    seq = 0
    ident = ""
    created = False
    terminal = False
    text = ""
    #: **工具调用那一半**（2026-10-12）：`index` → `{"id", "name", "arguments"}`。
    #: ⚠️ chat 那边**按 `index` 认**同一通调用（名字只在第一片里、arguments 分片来）。
    calls: dict[int, dict[str, str]] = {}
    opened: set[int] = set()
    closed: set[int] = set()
    #: ★ **2026-10-13**：这一条流上见过的用量（chat 那一份形状 ⇒ 上面那个函数映射过）。
    usage: dict[str, Any] | None = None
    reasoning_parts: list[str] = []
    reasoning_seen = False

    def take_one_piece(one: Mapping[str, Any]) -> tuple[int, str, str]:
        """ 把一片 `tool_calls[]` 并进那一格 ⇒ 交回 `(index, item_id, 这一片的新 arguments)`。"""
        try:
            index = int(one.get("index") or 0)
        except (TypeError, ValueError):
            index = 0
        slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
        got_id = one.get("id")
        if isinstance(got_id, str) and got_id and not slot["id"]:
            slot["id"] = got_id
        fn = one.get("function")
        piece = ""
        if isinstance(fn, Mapping):
            name = fn.get("name")
            if isinstance(name, str) and name and not slot["name"]:
                slot["name"] = name
            raw_piece = fn.get("arguments")
            if isinstance(raw_piece, str) and raw_piece:
                slot["arguments"] += raw_piece
                piece = raw_piece
        return index, _the_item_id_of_a_chat_call(slot, index), piece

    def close_the_calls() -> list[bytes]:
        """ 给每一通还没收尾的调用补一条 `output_item.done`（**arguments 逐字相同**）。

        ⚠️ **为什么非补不可**：`stream.py` 只认 `output_item.done`（或终态那一趟）来
           **定案**一通调用 —— 少了它，那一通永远不进 `calls`（「它明明调了工具却什么都没发生」）。
        ⚠️ **带上完整的 `arguments`**：累加器会拿它与分片拼出来的那一串**对账**
           （`_merge_arguments`）⇒ 两边必须逐字相同；这里带的就是**同一格**里存的那一串。
        """
        out: list[bytes] = []
        for index in sorted(calls):
            if index in closed:
                continue
            closed.add(index)
            slot = calls[index]
            out.append(tick("response.output_item.done", item={
                "id": _the_item_id_of_a_chat_call(slot, index),
                "type": "function_call", "call_id": slot["id"],
                "name": slot["name"], "arguments": slot["arguments"]}))
        return out

    def tick(kind: str, **fields: Any) -> bytes:
        nonlocal seq
        seq += 1
        return _frame({"type": kind, "sequence_number": seq - 1, **fields})

    async def terminal_frame(*, remember_turn: bool = True) -> bytes:
        """ 终态那一份里**带着正文** —— 那是**对账**用的。

        ⚠️⚠️ `stream.py:410-417` 会拿终态里的 `output[*].content[*].text` 与**增量拼出来的**
           `text` 逐字比 ⇒ 一份"不带 `output` 的终态"在**任何非空正文**上都会抛
           `terminal text disagrees with deltas: 0 vs N chars`。
           （ 这条判据是**纯对账、不是非空断言**：两边都空时放行；
             而 `incomplete` ／ `failed` 那两条**跳过**它 —— 见 `stream.py:386-393`。）
        """
        body: dict[str, Any] = {
            "id": ident, "status": "completed",
            "output": [{"type": "message",
                        "content": [{"type": "output_text", "text": text}]}]}
        # ★ **2026-10-13**：用量进**终态那一份的 `response` 里**（`stream.py` 的
        #   `usage` 那一格读的就是这个位置 —— 顶层没有它，实测过）。
        if usage is not None:
            body["usage"] = dict(usage)
        if completed is not None and remember_turn:
            saved = completed(text, [calls[i] for i in sorted(calls)],
                      "".join(reasoning_parts) if reasoning_seen else None)
            if inspect.isawaitable(saved):
                await saved
        return tick("response.completed", response=body)

    async for one in _chat_events_with_usage_tail(chunks):
        if one.get("__done__"):
            if not created:
                created = True
                yield tick("response.created", response={"id": ident, "status": "in_progress"})
            if not terminal:
                for frame in close_the_calls():
                    yield frame
                terminal = True
                yield await terminal_frame()
            continue
        error = one.get("error")
        if isinstance(error, dict):
            raise LLMError(0, str(error.get("code") or ""), str(error.get("message") or ""))
        # ★ **2026-10-13**：用量那一格**先收下**（它可能在任意一块上 —— 多数家放在最后一块，
        #   也有把它单独发一块的）⇒ 收进 `usage`，终态那一份再带出去。
        got_usage = _the_usage_of_a_chat_chunk(one)
        if got_usage is not None:
            usage = got_usage
        if not created:
            ident = str(one.get("id") or "")
            created = True
            yield tick("response.created", response={"id": ident, "status": "in_progress"})
        for choice in one.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if delta is None:
                delta = {}
            if not isinstance(delta, dict):
                from .stream import ResponseProtocolError
                raise ResponseProtocolError("chat delta must be an object")
            piece = delta.get("content")
            if piece:
                text += piece
                yield tick("response.output_text.delta", delta=piece)
            thinking = delta.get("reasoning_content")
            if thinking is not None and not isinstance(thinking, str):
                raise ValueError("reasoning_content must be a string or null")
            if isinstance(thinking, str):
                reasoning_seen = True
                if thinking:
                    reasoning_parts.append(thinking)
                    yield tick("response.reasoning_text.delta", delta=thinking)
            refusal = delta.get("refusal")
            if refusal:
                yield tick("response.refusal.delta", delta=refusal)
            # **工具调用那一半**（2026-10-12 补）：名字 ＋ 分片的 `arguments` ——
            # 第一片 ⇒ 开一条 `output_item.added`（`type=\"function_call\"`），
            # 之后每一片 ⇒ 一条 `function_call_arguments.delta`。收尾在下面（终态之前）。
            for one_call in delta.get("tool_calls") or []:
                if not isinstance(one_call, Mapping):
                    continue
                index, item_id, piece = take_one_piece(one_call)
                if index not in opened:
                    opened.add(index)
                    slot = calls[index]
                    yield tick("response.output_item.added", item={
                        "id": item_id, "type": "function_call",
                        "call_id": slot["id"], "name": slot["name"], "arguments": ""})
                if piece:
                    yield tick("response.function_call_arguments.delta",
                               item_id=item_id, delta=piece)
            reason = choice.get("finish_reason")
            if reason and not terminal:
                for frame in close_the_calls():
                    yield frame
                terminal = True
                name, status = _terminal_for(str(reason))
                body: dict[str, Any] = {"id": ident, "status": status}
                if status != "completed":
                    body["incomplete_details"] = {"reason": str(reason)}
                body["output"] = [{"type": "message",
                                   "content": [{"type": "output_text", "text": text}]}]
                if usage is not None:      # ★ 同上：用量跟着终态那一份走
                    body["usage"] = dict(usage)
                if completed is not None and status == "completed":
                    saved = completed(text, [calls[i] for i in sorted(calls)],
                      "".join(reasoning_parts) if reasoning_seen else None)
                    if inspect.isawaitable(saved):
                        await saved
                yield tick(name, response=body)

    if not created:                      # 一条事件都没来（空流）—— 也交一份合形的
        yield tick("response.created", response={"id": "", "status": "in_progress"})
    # EOF is not upstream terminal evidence. Let the accumulator reject it;
    # never settle partial calls or commit replay state on this path.
    if not terminal:
        from .stream import ResponseProtocolError
        raise ResponseProtocolError("chat stream ended without terminal evidence")


#: **Anthropic 的 `stop_reason` → Responses 的终态**（2026-10-12，口径与 `_terminal_for` 同）。
#:   ⚠️ `tool_use` 与 chat 那边的 `tool_calls` **是同一件事**："我说完了，该动手了"
#:      ⇒ 也算 `completed`（算成截断是错的）。
#: ★ **2026-10-13**：Anthropic 那一份用量 → Responses 那一份（与 chat 那一支对称）。
#: ⚠️ **它的名字与那一份差得不多，而差的正是要紧那一格**：`input_tokens` ／
#:   `output_tokens` 同名；而**缓存读**叫 `cache_read_input_tokens`（Responses 那份叫
#:   `input_tokens_details.cached_tokens`）⇒ 不映射的话，页上"缓存命中"那一格永远是 0。
#: ⚠️ **不认识的一个都不编**（哪一格没有就不放哪一格）。
def _merge_message_usage(previous, update):
    merged = dict(previous or {})
    for key, value in update.items():
        if isinstance(value, dict):
            merged[key] = {**merged.get(key, {}), **value}
        else:
            merged[key] = value
    if "input_tokens" in merged and "output_tokens" in merged:
        merged["total_tokens"] = merged["input_tokens"] + merged["output_tokens"]
    return merged


def _the_usage_of_a_message_usage(told: Any) -> dict[str, Any] | None:
    """ **Anthropic 那两处（`message_start` ／ `message_delta`）报的用量 → Responses**。"""
    if not isinstance(told, Mapping) or not told:
        return None
    out: dict[str, Any] = {}
    for mine in ("input_tokens", "output_tokens"):
        got = told.get(mine)
        if type(got) is int and got >= 0:
            out[mine] = got
    cached = told.get("cache_read_input_tokens")
    if type(cached) is int and cached >= 0:
        out["input_tokens_details"] = {"cached_tokens": cached}
    if isinstance(out.get("input_tokens"), int) and isinstance(out.get("output_tokens"), int):
        out["total_tokens"] = out["input_tokens"] + out["output_tokens"]
    return out or None


_END_TURN = ("end_turn", "stop_sequence", "tool_use", "refusal")


def _terminal_for_a_message_stop(reason: str) -> tuple[str, str]:
    """ 那一家的 `stop_reason` → 终态（`max_tokens` ＝ 截断，别的都算说完了）。"""
    if str(reason) in _END_TURN:
        return "response.completed", "completed"
    return "response.incomplete", "incomplete"


async def to_responses_from_messages(chunks: AsyncIterable[bytes]) -> AsyncIterator[bytes]:
    """ **第三支翻译器**：吃 Anthropic 的 SSE 字节，吐 Responses 事件的字节。

    ⚠️ **它多认两件 chat 那边没有的事**（这一支存在的全部理由）：
      · **一条工具调用是几块事件拼的**：`content_block_start`（报 id ／ 名字）＋
        一串 `content_block_delta` 里的 `input_json_delta`（参数分片）＋
        `content_block_stop`（收尾）⇒ 译成 `output_item.added` ／
        `function_call_arguments.delta` ／ `output_item.done` 那一套；
      · **终态那个词与终点不在一处**：`stop_reason` 在 `message_delta` 上，
        而"流完了"是 `message_stop` ⇒ **两处分开记**，到 `message_stop` 才落终态。
    ⚠️ 它**只做映射、不做判断**（与 `to_responses` 同一条）：
       坏 JSON 由 `iter_sse_events` 抛协议错；`error` 那一类落 `LLMError`（不是协议错 ——
       "上游说密钥不对"是**不该重试**的那一类）。
    """
    seq = 0
    ident = ""
    created = False
    terminal = False
    text = ""
    stop_reason = ""
    #: 块号 → `{"id", "name", "arguments"}`（**只装工具那块** ／ 文本块不进这里）。
    blocks: dict[int, dict[str, str]] = {}
    opened: set[int] = set()
    closed: set[int] = set()
    #: ★ **2026-10-13**：这一条流上见过的用量（Anthropic 在 `message_start` 与
    #: `message_delta` 两处报它 —— 后者那几格更全，所以后到的盖前面的）。
    usage: dict[str, Any] | None = None

    def tick(kind: str, **fields: Any) -> bytes:
        nonlocal seq
        seq += 1
        return _frame({"type": kind, "sequence_number": seq - 1, **fields})

    def the_item_id(slot: Mapping[str, str], index: int) -> str:
        # 与 chat 那一边**同一个成法**（`item.id` 要横跨好几条帧，必须稳定）。
        return "fc_" + (slot.get("id") or str(index))

    def close_the_blocks() -> list[bytes]:
        """ 给每一块还没收尾的工具**补一条 `output_item.done`**（arguments 逐字相同）。"""
        out: list[bytes] = []
        for index in sorted(blocks):
            if index in closed:
                continue
            closed.add(index)
            slot = blocks[index]
            out.append(tick("response.output_item.done", item={
                "id": the_item_id(slot, index), "type": "function_call",
                "call_id": slot["id"], "name": slot["name"],
                "arguments": slot["arguments"]}))
        return out

    def terminal_frame() -> bytes:
        # ⚠️ 终态那一份**带着正文**（`stream.py` 拿它与增量拼出来的对账）——
        #    与 chat 那一边逐字同一个理由。
        name, status = _terminal_for_a_message_stop(stop_reason)
        body: dict[str, Any] = {"id": ident, "status": status}
        if status != "completed":
            body["incomplete_details"] = {"reason": str(stop_reason or "unknown")}
        body["output"] = [{"type": "message",
                           "content": [{"type": "output_text", "text": text}]}]
        if usage is not None:          # ★ 用量跟着终态那一份走（与 chat 那一支同一个位置）
            body["usage"] = dict(usage)
        return tick(name, response=body)

    async for one in iter_sse_events(chunks):
        if one.get("__done__"):
            if not created:
                created = True
                yield tick("response.created", response={"id": ident, "status": "in_progress"})
            if not terminal:
                for frame in close_the_blocks():
                    yield frame
                terminal = True
                yield terminal_frame()
            continue
        which = str(one.get("type") or "")
        if which == "error":
            bad = one.get("error") if isinstance(one.get("error"), Mapping) else {}
            raise LLMError(0, str(bad.get("type") or "error"), str(bad.get("message") or ""))
        if which == "message_start":
            message = one.get("message") if isinstance(one.get("message"), Mapping) else {}
            ident = str(message.get("id") or "")
            got_usage = _the_usage_of_a_message_usage(message.get("usage"))
            if got_usage is not None:
                usage = _merge_message_usage(usage, got_usage)
            if not created:
                created = True
                yield tick("response.created",
                           response={"id": ident, "status": "in_progress"})
            continue
        if not created:                      # 第一件不是 `message_start`（有些网关省了它）
            created = True
            yield tick("response.created", response={"id": "", "status": "in_progress"})
        if which == "content_block_start":
            try:
                index = int(one.get("index") or 0)
            except (TypeError, ValueError):
                index = 0
            raw_block = one.get("content_block")
            block = raw_block if isinstance(raw_block, Mapping) else {}
            if str(block.get("type") or "") == "tool_use":
                blocks[index] = {"id": str(block.get("id") or ""),
                                 "name": str(block.get("name") or ""), "arguments": ""}
                opened.add(index)
                slot = blocks[index]
                yield tick("response.output_item.added", item={
                    "id": the_item_id(slot, index), "type": "function_call",
                    "call_id": slot["id"], "name": slot["name"], "arguments": ""})
            elif str(block.get("text") or ""):
                # 有的网关把开头那几个字放在这一块里（文本块）⇒ 照收，不丢
                piece = str(block.get("text"))
                text += piece
                yield tick("response.output_text.delta", delta=piece)
            continue
        if which == "content_block_delta":
            try:
                index = int(one.get("index") or 0)
            except (TypeError, ValueError):
                index = 0
            delta = one.get("delta") if isinstance(one.get("delta"), Mapping) else {}
            how = str(delta.get("type") or "")
            if how == "text_delta" and delta.get("text"):
                piece = str(delta.get("text"))
                text += piece
                yield tick("response.output_text.delta", delta=piece)
            elif how == "thinking_delta" and delta.get("thinking"):
                yield tick("response.reasoning_text.delta", delta=str(delta.get("thinking")))
            elif how == "input_json_delta":
                piece = str(delta.get("partial_json") or "")
                slot = blocks.get(index)
                if slot is None:             # 没见过 start 就来了参数（少见）⇒ 现起一块
                    blocks[index] = {"id": "", "name": "", "arguments": ""}
                    slot = blocks[index]
                slot["arguments"] += piece
                yield tick("response.function_call_arguments.delta",
                           item_id=the_item_id(slot, index), delta=piece)
            continue
        if which == "content_block_stop":
            try:
                index = int(one.get("index") or 0)
            except (TypeError, ValueError):
                index = 0
            if index in blocks and index not in closed:
                closed.add(index)
                slot = blocks[index]
                yield tick("response.output_item.done", item={
                    "id": the_item_id(slot, index), "type": "function_call",
                    "call_id": slot["id"], "name": slot["name"],
                    "arguments": slot["arguments"]})
            continue
        if which == "message_delta":
            delta = one.get("delta") if isinstance(one.get("delta"), Mapping) else {}
            if delta.get("stop_reason"):
                stop_reason = str(delta.get("stop_reason"))
            # ★ **2026-10-13**：这一处也报用量（而且比 `message_start` 那一份更全 ——
            #   输出那一格到这儿才定下来）⇒ 后到的**盖**前面的。
            got_usage = _the_usage_of_a_message_usage(one.get("usage"))
            if got_usage is not None:
                usage = _merge_message_usage(usage, got_usage)
            continue
        if which == "message_stop":
            if not terminal:
                for frame in close_the_blocks():
                    yield frame
                terminal = True
                yield terminal_frame()
            continue
        # 别的（`ping` ／ `signature_delta` 那一类）**原样不理** —— 不猜、不报错
    if not created:
        yield tick("response.created", response={"id": "", "status": "in_progress"})
    if not terminal:
        for frame in close_the_blocks():
            yield frame
        yield terminal_frame()


class ReasoningReplayMissing(ValueError):
    """Required provider reasoning is unknown; no HTTP request was sent."""


def deepseek_chat(base_url, kind):
    return kind == KIND_CHAT and urlsplit(base_url or "").hostname == "api.deepseek.com"


class ChatTransport:
    """ **装饰器**：把"chat 那套话"翻译成"responses 那套话"（`generate()` 看不出来）。

    ⚠️ 它只碰两件事：**发出去的 body** 与**收回来的字节** ⇒ 三条已经被实测钉住的规矩
       （跨块缓冲、协作式取消、`>= 300` 就抛）**一条都不必在这里重写**（那些在 `HttpxTransport` 里）。
    ⚠️ **`kind` 是每次请求现读的**（`kind_of()`）—— 与活视图同一条纪律：换了档案
       **下一次请求**就换协议，不必重开进程。
    ⚠️ **`responses` 那一条照旧直通**（只是多塑一下 body）： 于是"本地那一个"这条路
       与刀 3 之前**逐字节相同**的输入输出 —— 零回归。
    """

    def __init__(self, inner: Any, *, kind_of: Any, values_of: Any,
                 image_resolver: Any = None, replay_root: Any = None,
                 runtime_replay: bool = False, replay_context_of: Any = None,
                 local_native_compat: Any = False, request_of: Any = None,
                 runtime_evidence: Any = None) -> None:
        self._runtime_evidence = runtime_evidence
        self._request_of = request_of
        self._local_native_compat = local_native_compat
        self._runtime_replay = runtime_replay
        self._replay_context_of = replay_context_of
        self._runtime_ephemeral = uuid.uuid4().hex
        filename = "chat-runtime-reasoning.json" if runtime_replay else "chat-reasoning.json"
        self._replay_path = (Path(replay_root) / filename
                             if replay_root is not None else None)
        self._inner = inner
        self._kind_of = kind_of
        self._values_of = values_of
        self._image_resolver = image_resolver

    def stream_chat(self, base_url: str, body: Mapping[str, Any], *, frame_idle_ms: int,
                    signal: asyncio.Event | None = None) -> AsyncGenerator[bytes, None]:
        inner = self._inner
        kind_of = self._kind_of
        values_of = self._values_of

        async def one() -> AsyncIterator[bytes]:
            request = self._request_of() if self._request_of is not None else None
            if self._request_of is not None:
                from .engine import RequestSnapshot, EngineError
                if not isinstance(request, RequestSnapshot):
                    raise EngineError("invalid request snapshot")
                if (base_url != request.config.base_url
                        or body.get("model") != request.config.model
                        or body.get("max_output_tokens") != request.config.max_tokens
                        or body.get("chat_template_kwargs", {}).get("enable_thinking")
                           != request.config.thinking):
                    raise EngineError("request snapshot target mismatch")
                kind = request.kind
                request_values = request.values
            else:
                kind = kind_of()
                request_values = values_of() or {}
            # **`kind` 必须从 `values` 里剔掉**（2026-10-05 作者那句"这些真的生效了吗"
            # 量出来的一处真崩 —— 也就是**"没有回话"的根** ）：
            # `values_of()` 交出的那一份里**带着 `'kind'`** （活视图／`Pinned.values()` 都有它 ），
            # 而下面那句是 `shape(kind, body, …, **values)` ⇒ **`kind` 撞两次**
            # ⇒ `TypeError: shape() got multiple values for argument 'kind'`
            # ⇒ **生产那一条路上每一次请求都会在这儿炸**（`console.assemble` 里
            # `owns=True` ⇒ 这层适配器**总是**包着 ），而它**悄无声息**地消失在 `loop` 里 。
            # 为什么该在这儿剔：`kind` 是**另一条路**给的（`kind_of()` ，它现读 ）——
            # 而 `values` 是"**那几格**"（thinking ／ temperature ／ reasoning_effort ／ multimodal ）。
            # 两样混在一句调用里，就是"一个名字两处用" （本仓那条老毛病 ）。
            # 2026-10-07：**剔掉与 `shape()` 形参重名的那些键** —— 不剔就是
            # `TypeError: got multiple values for keyword argument …` （今天真撞上过）：
            # `values` 是"那一档的几格"，而谁往里塞了什么由上游决定 ⇒
            # 这一层该先剔掉，**而不该让上游一个疏忽把整轮打掉** 。
            # ⚠️ **只剔那三个"已经按位置传了的"** —— `thinking` ／ `temperature` 那些
            # 本来就是**关键字形参** ⇒ 留着它们**正是它们该走的路**
            # （ 第一版我把七个名字全剔了 ⇒ `temperature` 进不了 body ⇒ 判据当场红 ）。
            _shape_names = ("kind", "body", "base_url")
            values = {k: v for k, v in request_values.items()
                      if k not in _shape_names}
            auth = values.pop("auth", "api_key")
            subscription = values.pop("_subscription", None)
            # ⚠️ **2026-10-04 再次更正**：这里从前写着"本地那条线不吃 `reasoning_effort`"，
            #   依据是"8 个值全 400"。**那 400 的成因是层级** —— `shape()` 把它写在
            # **顶层** ，而引擎要的是**嵌套** `reasoning.effort` （400 的正文自己就写着
            # `param: "reasoning.effort"`）。实测：顶层 400 ／ 嵌套 200 。
            #   ⚠️ 而 `responses` 那条线**正是唯一认它**的那一条（`accepts()` 说了算）——
            #   在这儿对它剔掉，等于把唯一能用的那条线废掉 ⇒ 那一格永远是死的。
            #  ⇒ 现在只对**不认它的**那条线（`chat`）剔。`chat` 本来 `accepts()` 就是 False，
            #    这一句是第二道保险，不是主判据。
            # ⚠️ 2026-10-07 **撤回一次误判**：我一度改成"本机那一档一律拿掉" ——
            # 而**判据一直在说真话**： 那台认它， 只是要**嵌套**
            # （`reasoning: {"effort": …}` ／ **顶层**那个名字才 400 ）。
            # 我那次的实测发的是**顶层**写法 ⇒ 于是我把一个**不是原因**的东西改掉了 。
            # ⇒ 恢复原条件（ 而 `responses` 那一档照旧走下面"嵌进 reasoning.effort" ）。
            if is_local(base_url) and kind != KIND_RESPONSES:
                values.pop("reasoning_effort", None)
            from .provider_record_replay import current as record_frame
            replay_frame = (record_frame() if self._runtime_replay
                     and _chat_scope.get() is None and kind == KIND_CHAT else None)
            shaped_body = body
            quoted_missing = 0
            if replay_frame is not None:
                replay_frame.authorize(base_url, body.get("model"))
                replay_frame.begin_attempt()
                marked = replay_frame.mark(body.get("input", []))
                if (values.get("thinking") is not False
                        and (values.get("thinking") is True or values.get("reasoning_effort") != "none")):
                    if body.get("tools") and deepseek_chat(base_url, kind) and replay_frame.on_history_quote:
                        marked, quoted_missing = replay_frame.quote_history(marked)
                    shaped_body = {**body, "input": marked}
            from .request_media import shape_request
            shaped = await shape_request(shape, kind, shaped_body, base_url=base_url,
                image_resolver=self._image_resolver, values=values, signal=signal)
            subscription_headers = None
            frozen_headers = dict(request.headers) if request is not None else None
            if auth == "chatgpt":
                from .chatgpt_subscription import subscription_body, request_headers, SubscriptionError
                if kind != KIND_RESPONSES or subscription is None:
                    raise SubscriptionError("ChatGPT 订阅认证未正确装配")
                shaped = subscription_body(shaped, base_url)
                subscription_headers = await request_headers(subscription)
            # **端点由这一层说给传输层**（`path=`）—— 从前它是写死在传输层里的，
            #    于是包在外面的适配器**没有路**走到另一个端点
            #    （把路径塞进 `base_url` 会变成 `…/chat/completions/responses` ⇒ 404）。
            # Provider wire state only: never append reasoning to Session records.
            run_scope = _chat_scope.get()
            scoped = run_scope is not None
            runtime = self._runtime_replay and not scoped
            async def context_identity():
                value = self._replay_context_of() if self._replay_context_of else None
                if inspect.isawaitable(value):
                    value = await value
                if value is None:
                    return ("ephemeral:" + self._runtime_ephemeral, False)
                if not isinstance(value, str) or not value or len(value) > 512:
                    raise ValueError("invalid provider replay context")
                return ("durable:" + value, True)
            runtime_identity, runtime_persistent = await context_identity() if runtime else (None, False)
            if replay_frame is not None:
                if runtime_identity != "durable:" + replay_frame.context:
                    raise ValueError("provider record context changed")
                replay_frame.apply(shaped.get("messages", []))
            cache_name = ("_chat_scoped_reasoning_replay" if scoped else
                          "_chat_runtime_reasoning_replay" if runtime else
                          "_chat_reasoning_replay")
            record_owned = replay_frame is not None and replay_frame.on_incompatible is not None
            replay = {} if record_owned else getattr(inner, cache_name, None)
            if not isinstance(replay, dict):
                replay = {}
                if (not scoped and (not runtime or runtime_persistent)
                        and self._replay_path is not None):
                    try:
                        from .retained_io import file_call
                        saved = await file_call(lambda: json.loads(self._replay_path.read_text(encoding="utf-8")))
                        if isinstance(saved, dict):
                            replay = {k: v for k, v in saved.items()
                                      if isinstance(k, str) and isinstance(v, str)}
                    except (OSError, ValueError):
                        pass
                setattr(inner, cache_name, replay)
            scope = ((base_url, shaped.get("model"), run_scope) if scoped else
                     (base_url, shaped.get("model"), runtime_identity) if runtime else
                     (base_url, shaped.get("model")))
            replay_enabled = (kind == KIND_CHAT and
                              (not scoped or values.get("thinking") is True))
            def key_of(message: Mapping[str, Any], preceding: list) -> str:
                def signature(m: Mapping[str, Any]) -> list:
                    # scoped：工具调用的 assistant 正文不参与签名（保存与查找同一规则）。
                    said = ("" if ((scoped or runtime) and m.get("tool_calls"))
                            else (m.get("content") or ""))
                    return [said, [
                        (c.get("function", {}).get("name"),
                         c.get("function", {}).get("arguments"))
                        for c in m.get("tool_calls", [])]]
                # Call IDs are regenerated by Session; system hints and tool
                # result folding also change between requests. None identify
                # a provider turn. Prior assistant turns DO: repeated identical
                # calls must not overwrite or borrow one another's reasoning.
                clean = [[m.get("role"), signature(m)] for m in preceding
                         if isinstance(m, Mapping)
                         and m.get("role") in ("user", "assistant")]
                value = ["chat-replay-runtime-v2" if runtime else
                         "chat-replay-v3" if scoped else "chat-replay-v2",
                         scope, clean, signature(message)]
                return hashlib.sha256(json.dumps(value, ensure_ascii=False,
                                                sort_keys=True).encode()).hexdigest()
            messages = shaped.get("messages", [])
            if replay_enabled and not record_owned:
                for i, message in enumerate(messages):
                    if message.get("role") == "assistant":
                        if runtime and isinstance(message.get("reasoning_content"), str):
                            continue
                        saved = replay.get(key_of(message, messages[:i]))
                        if saved is not None:
                            messages[i] = {**message, "reasoning_content": saved}
            thinking = shaped.get("thinking", {}).get("type")
            if (runtime and deepseek_chat(base_url, kind) and shaped.get("tools")
                    and (thinking == "enabled" or
                         (thinking is None and shaped.get("reasoning_effort") != "none"
                          and (replay_frame is not None or values.get("thinking") is True)))):
                missing = [i for i, message in enumerate(messages)
                           if message.get("role") == "assistant"
                           and not isinstance(message.get("reasoning_content"), str)]
                if quoted_missing and not missing:
                    replay_frame.on_history_quote(quoted_missing)
                if missing and record_owned:
                    # Keep every visible message and the tool definitions. A
                    # different provider/model or non-thinking reply cannot
                    # supply this model's private reasoning.
                    # Use normal Chat grouping for old parallel calls. Marked
                    # shaping can split calls whose individual bindings differ.
                    compatible = {**values, "thinking": False, "reasoning_effort": None}
                    shaped = await shape_request(shape, kind, body, base_url=base_url,
                        image_resolver=self._image_resolver, values=compatible, signal=signal)
                    shaped.pop("reasoning_effort", None)
                    messages = shaped["messages"]
                    replay_frame.on_incompatible(len(missing))
                elif missing:
                    raise ReasoningReplayMissing(
                        "历史推理不可恢复：" + str(len(missing))
                        + " 条 assistant 缺少 reasoning_content；已在 HTTP 前停止。"
                        "不会伪造空串或删除会话；请恢复对应推理缓存，"
                        "或明确建立独立的新会话上下文。")
            non_thinking = (shaped.get("thinking", {}).get("type") == "disabled"
                            or (thinking is None and shaped.get("reasoning_effort") == "none"))
            if replay_frame is not None and deepseek_chat(base_url, kind) and non_thinking:
                for message in messages:
                    message.pop("reasoning_content", None)
            reasoning_supported = any("reasoning_content" in m for m in messages
                                      if isinstance(m, Mapping))
            async def persist_runtime(deleted=()):
                if self._replay_path is not None and runtime_persistent:
                    from .retained_io import file_call
                    frozen = dict(replay)
                    def publish():
                        self._replay_path.parent.mkdir(parents=True, exist_ok=True)
                        _publish_replay(self._replay_path, frozen, deleted=deleted)
                    await file_call(publish)

            async def remember(text: str, calls: list, reasoning: str | None) -> None:
                if not replay_enabled:
                    return
                if runtime:
                    if (await context_identity())[0] != runtime_identity:
                        return
                    if replay_frame is not None:
                        replay_frame.capture(None if non_thinking else reasoning)
                        if record_owned:
                            # Only exact record bindings authorize production replay.
                            return
                    if reasoning is None:
                        message = {"content": text or None, "tool_calls": [
                            {"function": {"name": c["name"], "arguments": c["arguments"]}}
                            for c in calls]}
                        keys = [key_of(message, messages)]
                        projected = list(messages)
                        for call in calls:
                            single = {"role": "assistant", "content": text or None,
                                      "tool_calls": [{"function": {
                                          "name": call["name"],
                                          "arguments": call["arguments"]}}]}
                            keys.append(key_of(single, projected))
                            projected.append(single)
                        changed = False
                        for key in keys:
                            if key in replay:
                                del replay[key]
                                changed = True
                        if changed:
                            await persist_runtime(keys)
                        return
                if scoped:
                    if reasoning is None:
                        return
                elif not runtime:
                    # Keep the legacy provider-support fallback, never use it in scoped mode.
                    if reasoning is None and not reasoning_supported:
                        return
                    reasoning = reasoning or ""
                message = {"content": text or None, "tool_calls": [
                    {"function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in calls]}
                def save(key):
                    if scoped:
                        _save_replay(inner, run_scope, key, reasoning)
                    else:
                        replay[key] = reasoning
                        while len(replay) > 256:
                            replay.pop(next(iter(replay)))
                save(key_of(message, messages))
                projected = list(messages)
                for call in calls:
                    single = {"role": "assistant", "content": text or None,
                              "tool_calls": [{"function": {
                                  "name": call["name"], "arguments": call["arguments"]}}]}
                    save(key_of(single, projected))
                    projected.append(single)
                if runtime:
                    await persist_runtime()
                elif not scoped and self._replay_path is not None:
                    from .retained_io import file_call
                    frozen = dict(replay)
                    def publish():
                        self._replay_path.parent.mkdir(parents=True, exist_ok=True)
                        _publish_replay(self._replay_path, frozen)
                    await file_call(publish)
            path = endpoint_for(kind)
            # **谁造谁关**：里层那条异步生成器是这一层造的 ⇒ `finally` 里确定性地关掉它。
            #    ⚠️ 只靠 `async for` 的自然收尾在"调用方提前跳出"时**不够**（实测那句
            #    `aclose(): asynchronous generator is already running` 就是那么来的）。
            # A Strata pin can protect a parked snapshot after this request and
            # after its Bot exits. Let the shared worker evict idle histories.
            upstream = inner.stream_chat(base_url, shaped, frame_idle_ms=frame_idle_ms,
                                         signal=signal, path=path,
                                         **({"request_headers": (subscription_headers if subscription_headers is not None
                                                                 else frozen_headers)}
                                            if subscription_headers is not None or frozen_headers is not None else {}))
            try:
                # **三条路各走各的**（2026-10-12：从"两条"变成"三条"）：
                #   · `chat` ⇒ 那一支翻译器；· `anthropic` ⇒ 第三支；
                #   · 其余（含 `responses` 与不认得的名字）⇒ **原样直通** ——
                #     与从前**逐字一样**（不认得的名字不许被顺手"翻译"成别的）。
                if kind == KIND_CHAT:
                    async for frame in to_responses(upstream, completed=remember):
                        yield frame
                    return
                elif kind == KIND_ANTHROPIC:
                    made = to_responses_from_messages
                elif subscription_headers is not None:
                    from .subscription_stream import subscription_events
                    async with contextlib.aclosing(subscription_events(upstream)) as normalized:
                        async for chunk in normalized:
                            yield chunk
                    return
                elif (kind == KIND_RESPONSES and is_local(base_url)
                      and (self._local_native_compat() if callable(self._local_native_compat)
                           else self._local_native_compat) is True):
                    from .local_response_stream import local_response_events
                    async with contextlib.aclosing(local_response_events(upstream)) as normalized:
                        async for chunk in normalized:
                            yield chunk
                    return
                else:
                    async for chunk in upstream:
                        yield chunk
                    return
                async for frame in made(upstream):
                    yield frame
            except BaseException as primary:
                try:
                    await upstream.aclose()
                except BaseException as cleanup:
                    primary.stream_cleanup_error = cleanup
                    primary.add_note("upstream close also failed: " + type(cleanup).__name__)
                raise
            finally:
                # Successful branches return above, so they also need close.
                import sys
                if sys.exc_info()[0] is None:
                    await upstream.aclose()

        if self._runtime_evidence is None:
            return one()
        async def observed():
            async with self._runtime_evidence.async_scope("model_stream"):
                async with contextlib.aclosing(one()) as stream:
                    async for chunk in stream:
                        yield chunk
        return observed()

    def for_request(self, config: Any, *, bound_request=None) -> ChatTransport:
        """Borrow the HTTP wire with a separate, tool-free appraisal snapshot.

        Never close this view: its inner transport belongs to the caller.
        Appraisals must not read/write the main chat reasoning replay ledger.
        """
        request = bound_request if bound_request is not None else (self._request_of() if self._request_of is not None else None)
        if self._request_of is not None or bound_request is not None:
            from .engine import RequestSnapshot, EngineError
            if not isinstance(request, RequestSnapshot):
                raise EngineError("invalid request snapshot")
            from .config import Config
            if not isinstance(config, Config):
                raise EngineError("appraisal requires a frozen Config")
            if (config.base_url != request.config.base_url
                    or config.model != request.config.model):
                raise EngineError("appraisal snapshot target mismatch")
        values = dict(request.values if request is not None else self._values_of() or {})
        values["thinking"] = config.thinking
        values["reasoning_budget_tokens"] = config.reasoning_budget_tokens
        if config.thinking is False:
            # A tool-free non-thinking request must not inherit the parent's
            # high/max effort. The parent snapshot and replay remain untouched.
            values.pop("reasoning_effort", None)
        kind = request.kind if request is not None else self.kind_now()
        if request is not None:
            from dataclasses import replace
            from types import MappingProxyType
            # EventAssessment derives a tool-free, reduced-budget Config via
            # dataclasses.replace. Keep the original auth handle/generation.
            request = replace(request, config=config, values=MappingProxyType(values))
        return ChatTransport(self._inner, kind_of=lambda: kind,
                             values_of=lambda: dict(values),
                             image_resolver=self._image_resolver,
                             local_native_compat=self._local_native_compat,
                             runtime_evidence=self._runtime_evidence,
                             request_of=(lambda: request) if request is not None else None)

    def kind_now(self) -> str:
        """ **现在说的是哪一套话**（判据与页那一层读它；`kind_of` 是现读的）。"""
        return self._kind_of()

    async def aclose(self) -> None:
        await self._inner.aclose()

    def __getattr__(self, name: str) -> Any:
        """ **它对别的一律透明** —— 不认得的属性都去问里面那一条。

        ⚠️ 这不是偷懒：本仓的判据**直接问** `transport._client`（"入口造的那个 client
           关没关"那两条）⇒ 一个"包一层就把那些问题问瘸了"的装饰器会把两条已有判据弄红。
            装饰器该做的事就是透明：**只改它明说要改的那两样**（body 与字节）。
        """
        return getattr(self._inner, name)


def adapt(transport: Any, *, kind_of: Any, values_of: Any,
           image_resolver: Any = None, replay_root: Any = None,
           runtime_replay: bool = False, replay_context_of: Any = None,
           local_native_compat: Any = False, request_of: Any = None,
           runtime_evidence: Any = None) -> ChatTransport:
    """装配那一层的一句话 —— **只包我们自己造的那一条**（递进来的那条由调用方说了算）。

    ⚠️ 递进来的 transport（判据里那些替身）**不包**：替身的契约是"调用方说什么就回什么"，
       包一层会让它的回话被翻译成另一套话 —— 而那些替身本来就照 Responses 那一套回话。
    """
    return ChatTransport(transport, kind_of=kind_of, values_of=values_of,
                         image_resolver=image_resolver, replay_root=replay_root,
                         runtime_replay=runtime_replay, replay_context_of=replay_context_of,
                         local_native_compat=local_native_compat, request_of=request_of,
                         runtime_evidence=runtime_evidence)


#: **这一层还欠着的那几笔**（名字 → 欠主）—— 成法照 `engine._OWED` ／ `round._OWED`。
_OWED: dict[str, str] = {
    "tool_calls_not_mapped": (
        " **已经接上了**（2026-10-12）。原先写的是：chat 流里的 `choices[].delta.tool_calls[]`"
        "（名字 ＋ 分片的 arguments）**没有映射**成 `output_item.added` ／"
        "`function_call_arguments.delta` 那一套 —— 那意味着**用 chat 那套档案时，它想动手会被"
        "静默丢掉**（正文照旧到得了，「要调工具」那一半看不见）。"
        " 今天怎么接的：第一片开一条 `output_item.added`（`type=function_call`，"
        "`item.id` ＝ `fc_<call_id>`）、之后每片一条 `function_call_arguments.delta`、"
        "**收尾再补一条 `output_item.done`**（带上那一格累加出来的 `arguments` —— 与分片拼出来的"
        "那一串逐字相同，累加器会对账）；`close_the_calls()` 在两处收尾（`finish_reason` 与"
        "`[DONE]`）各调一次，所以「只来了 delta、流就断了」那一条也进得了 `calls`。"
        " ⚠️ 同一笔下 `finish_reason: tool_calls` 也补进了 `completed` 那一支 —— 它原先落"
        " `response.incomplete`，于是**一次正常的「它要动手」会被读成截断**。"
         " ★ **2026-10-13：`usage` 也映射了**（`_the_usage_of_a_chat_chunk` ⇒ 终态那一份的"
         " `response.usage`；判据见下一条）。"),
    "usage_not_carried": (
        " ★ **已经接上了**（2026-10-13）。原先写的是：chat 流的 `usage`（token 数）**没有映射**，"
        "而 Responses 那边的用量今天也没人读（`generate.Result` 里没有 token 数那一格）"
        "⇒ 不是这一层漏了它，而是**两边都还没有它**。"
        " ⚠️ **后半句早就过期了**：Responses 那条路**有人读**（`stream.py` 的 `usage` 属性 ⇒ "
        "`generate.Result.usage` ⇒ `console._keep_usage`，读的是 `input_tokens` 与 "
        "`input_tokens_details.cached_tokens`）。"
        " 今天补的是**前一半**：chat 那一份用量**映射**成同一份形状"
        "（`_the_usage_of_a_chat_chunk`：`prompt_tokens` ⇒ `input_tokens` ／ "
        "`prompt_tokens_details.cached_tokens` ⇒ `input_tokens_details.cached_tokens`）——"
        "⚠️ **照抄过去等于没映射**：两份的字段名不一样，而下游读的是后一份。"
        " ⚠️ **不认识的一个都不编**：哪一格没有就不放哪一格（读不到就退回「这一轮没有用量」，"
        "而不是一个假 0）。"
        " 判据：`tests/test_protocol_chat.py::"
        "test_the_chat_stream_carries_its_usage_to_the_same_place`。"),
    "images_never_ride_into_the_body": (
        " ★ **已经接上了**（2026-10-13 订正）。原先写的是：**「把图放进正文」那条路今天没有**"
        "（`accepts_media()` 与 `shape(multimodal=…)` 那两层已经接上，而**没有任何一条路"
        "会真的把图片片放进请求体**）。"
        " 今天的事实：**那条路在** —— 入口收 `images` ⇒ 落进 `Record(images=…)` ⇒"
        "`session` 在渲染那一趟把它造成 `input_image` 那一片（`session.py:126-132`），"
        "而这一层按 `chat` ／ `responses` 换名（`_shape_media`）。"
        " ⚠️ 仍然为真的一半：**收不收**由那一档档案说了算（`accepts_media()`）——"
        "本机那台不带视觉的引擎照旧收不到图。"),
    "a_third_protocol_is_not_implemented": (
        " **已经接上了**（2026-10-12，作者：「现在就补」）。原先写的是：今天只认 `chat`"
        "（`/chat/completions`）与 `responses`（`/responses`）两套，而 **Anthropic 走的是"
        " `/v1/messages`**，它因此**没有进预设表**。 今天这一笔做的：`KIND_ANTHROPIC`"
        "（`KINDS` 三条）／`endpoint_for` 回 `/messages`（那一截 `/v1` 归 `base_url`）／"
        "`_to_anthropic_body`（`system` 提顶层 ／ 只有 user 与 assistant ／"
        " `function_call` ⇒ `tool_use`、`function_call_output` ⇒ `tool_result` ／"
        " 工具用 `input_schema`）／`to_responses_from_messages`（第三支翻译器："
        " `content_block_start` ＋ `input_json_delta` ＋ `content_block_stop` ⇒ "
        " `output_item.added` ／ `function_call_arguments.delta` ／ `output_item.done`；"
        " `stop_reason` 与 `message_stop` **分开记**）／`engine.PRESETS` 补了那一条。"
         " ★ **仍欠的一笔**：它那一边的 `thinking: {\"type\":\"enabled\",…}` 今天不发"
         "（`accepts()` 对 `thinking` ／ `reasoning_effort` 回 `False` ⇒ 页上那两格会灰掉）。"
         " ★ **用量那一格也接上了**（2026-10-13）：`_the_usage_of_a_message_usage` 把"
         "`message_start` ／ `message_delta` 两处报的用量映射成同一份形状"
         "（要紧的是**缓存读**：`cache_read_input_tokens` ⇒ "
         "`input_tokens_details.cached_tokens`）；判据在 `tests/test_protocol_anthropic.py`。"),
}


_chat_scope = contextvars.ContextVar("chat_reasoning_run_scope", default=None)


def _save_replay(inner, scope, key, reasoning):
    replay = inner._chat_scoped_reasoning_replay
    owners = getattr(inner, "_chat_replay_keys", None)
    if owners is None:
        owners = {}
        inner._chat_replay_keys = owners
    replay[key] = reasoning
    owners.setdefault(scope, set()).add(key)
    while len(replay) > 256:
        old = next(iter(replay))
        replay.pop(old)
        for owner in list(owners):
            owners[owner].discard(old)
            if not owners[owner]:
                owners.pop(owner)


def drop_replay_scope(inner, scope):
    owners = getattr(inner, "_chat_replay_keys", {})
    mine = owners.pop(scope, set())
    replay = getattr(inner, "_chat_scoped_reasoning_replay", {})
    for key in mine:
        replay.pop(key, None)
    return len(mine)


@contextlib.contextmanager
def replay_scope(inner):
    # Fresh identity per run; continuation and retries inherit its task context.
    scope = uuid.uuid4().hex
    token = _chat_scope.set(scope)
    try:
        yield scope
    finally:
        try:
            drop_replay_scope(inner, scope)
        finally:
            _chat_scope.reset(token)


def _publish_replay(path, replay, *, deleted=()):
    import os
    import tempfile
    from .engine import _config_lock
    # Independent writers serialize read/merge/replace on one stable lock inode.
    with _config_lock(path.with_name(path.name + ".lock")):
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(existing, dict):
            raise ValueError("invalid replay archive")
        merged = {**existing, **replay}
        for key in deleted:
            merged.pop(key, None)
        while len(merged) > 256:
            merged.pop(next(iter(merged)))
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(merged, ensure_ascii=False).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)


