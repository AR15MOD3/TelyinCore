# telyincore/consolidate.py
"""交接后的整理态：使用交接前上下文生成结构化结果，由注入的写入接口保存。

模型指令在 prompts.py；本模块负责排队、串行、重试、解析和写入。
时钟、路径与文件名校验由调用方及写入接口负责，避免重复实现。"""
from __future__ import annotations

import asyncio
from .retained_io import file_call
from contextlib import ExitStack
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

# **那一段话住在一个单独的文件里**（`prompts.py` —— 名字是设计给的，见它的抬头）：
#    `INSTRUCTION` ／ `KEYS` ／ `NOTHING` ／ `the_ask` 都是**给模型看的字**，
#    而本模块管的是**编排**（排队 ／ 串行 ／ 空跑保护 ／ 退一次 ／ 读回来 ／ 写下去）。
#    ⚠️ 分成两个文件是**一处**设计（`10-整理态.md` §交付物那张表），不是本仓自己劈的；
#    而在此之前它叫 `consolidation.py`（照抄了参考实现里的标识符）——
# 那条术语更正逐字写在 `prompts.py` 抬头（"梦"在参考里**是一个人的会话 id**）。
from .prompts import INSTRUCTION, KEYS, NOTHING, PENDING_KEY, STATE_UNREADABLE, the_ask

#: **那一份「举止」**（2026-10-07「乙」）—— 它是**原料**（读给这一趟看），
#: **不是**要改的那一份 ⇒ **不许**把它加进 `HER_FILES`（那样写手会去写它 ）。
MANNERS_FILE = "素材/举止.md"

#: 浮现那一件的 `type`（注入用的 kind）—— 它**必须**与 `note.HANDOFF_NOTE_KIND`
#:   和 `handoff.INJECT_INTERNAL_KIND` **都不是同一个字符串**：三件事（交接笔记 ／
#:   醒来那句 ／ 整理完想让人知道的一件事）。塌成一个就再也分不开（`handoff.py:88-96`）。
CONSOLIDATE_KIND = "consolidate"

#: **它自己写的那几份**（相对 persona 那条根；2026-10-07 起**三份**）——
#: 这是**本模块唯一的名单**，
#:    而它照抄 `docs/design/10-整理态.md` §「它这一层做的四件事」那一张表：
#:    二 ⇒ `世界认知`（"**只有它能写**那一份"）／三 ⇒ `自述`（"只有它"写的那一份，`12:13`）。
#:    ⚠️ **别的名字一个都不许出现**：`你定的` 那六份只能**提议**，不许写（`10:70-79`）。
#: ⚠️ **这个名字是被一道判据改过的**：本常量起初叫另一个名字 —— 而**那一个名字
#:    正是那一刀删掉的一个常量**（`chat.ts` 里"另一头"那个头字符）⇒
#:    `tests/test_retired_names.py` **在落地当天就红了一次**（一个与已删之物**同名**的新常量）。
#: 于是叫 `HER_FILES`：名字换掉，而不是到那张分发表里去给一个**新东西**签一句。
HER_FILES: tuple[str, ...] = ("\u5979\u5199\u7684/\u81ea\u8ff0.md",
                               "\u5979\u5199\u7684/\u4e16\u754c\u8ba4\u77e5.md",
                               # **2026-10-07 补的第三份**（ 修一个真缺口）：
                               #    那一趟答复里**本来就是四样**，而这一份名单里
                               # **没有「习惯」** ⇒ 它按门槛长出来的那一条**被丢掉**
                               #    （提示里要它、写完不落盘）。⚠️ 它与 `web.py` 那扇
                               # 只写门（自己那扇）的名单**应当同长** —— 那边就是三份 。
                               "\u5979\u5199\u7684/\u4e60\u60ef.md")



class NoVersionCheck(RuntimeError):
    """ **这扇写口没有版本检查的能力** —— 这一趟**拒绝提交**（2026-10-17 补验 ①）。

    ⚠️ 从前那一版是"记一笔警告，然后照写"：那等于把保护换成一句日志，
       而日志没人读的时候它就是没有。**装配漏了一个参数**不该退化成"无保护地写"。
    """


class ConsolidationReplyError(ValueError):
    """那一趟的答复读不成 `{"自述"／"世界认知"／"习惯"／"浮现"}` 那个对象。"""


#: **单份正文的上界**（本刀的裁量，量得出来的理由）：这一趟是**模型写的字直接落进
#:    它的档案**，而"消化多于追加"（`10:113`）是那条纪律的正身 ⇒ 一份涨到几万字
#:    已经不是整理、是把上下文抄了一遍。⚠️ 它不是**截断**（截断会静默丢东西）——
#:    超了**当场抛**，这一趟不写，下次再来（见 `run` 那一支）。
MAX_BYTES_PER_FILE = 24000


@dataclass(frozen=True)
class ConsolidationResult:
    """这一趟交回来的三样。

    · `wrote` —— 真写下去的那几个名字（**空那一串是正常的**：这一段没什么可写的）；
    · `emergence` —— 要送到醒着的另一头那儿的几行（`None` ＝ 不打扰）；
    · `text` —— 模型原样的那段正文（判据与日志读它）。
    """

    wrote: tuple[str, ...]
    emergence: str | None
    text: str
    settled: tuple[str, ...] = ()




#: **这一份还欠着的那几笔**（名字 → 欠主）—— 成法照 `unresolved._OWED` ／ `round._OWED`
#: ／ `timers._OWED`：键是**稳定的机器名**（改措辞不会红），值里**必须点名**，而
#: **还清就删条目** —— 一条已经兑现的债留在表里就是一句假话。
#: ⚠️ 它与 `if write is None: return None`（`console._a_consolidation`）那种**接缝**不是一回事：
#:    接缝说"这一格没人递"，这一张说"**这一件事今天没有落点**"。两个都要有名字。
#: ⚠️ 本表的依据是 `docs/design/10-整理态.md` §它这一层做的四件事 —— 那四条里**两条落地、
#:    两条没有**，而没有的那两条**根是同一个**（见最后一条）。
_OWED: dict[str, str] = {
    "abouther_pending_zone":
        "《关于自己》那本共用的**核心 ／ 悬着的事 ／ 长尾**三层（`12:57-69`，核心 ≤40 条、"
        "单条 ≤120 字）—— 今天那份只渲染成**只读**，而它自己写着'等那一套做出来再开' ⇒ "
        "**四件事的第一件**（整理悬着的事）没有落点。欠主：**记忆那一侧**（今天不存在）。"
        " 不是本模块能还的：它要一个**放提议的地方**，而那是队列 ＋ 人的确认，不是一次写作。",
    "propose_to_the_ones_you_set":
        "**提议改『你定的』那几份**（`10:70-79`：改不改由人定）—— 今天**没有落点**："
        "本模块**写不动**那六份（`web.HER_FILES` 只有两个名字，而那是故意的），"
        "于是它连'提出来'这一格都没有（没有一张待裁决的清单、也没有人读它）。"
        "欠主：**装配那一层 ＋ 那一页**（今天两边都没有那一格）。"
        "⚠️ 它与上一条**不是一个落点**：那一条是记忆的悬着的事，这一条是人格那六份的提议。",
    "it_has_no_hands": (
        " ★ **已经接上了**（2026-10-13 订正）。原先写的是：**上两条共用的那个前置** ——"
        "它得有手（能翻自己的档案 ／ 能把提议放下来），而手就是工具那一面"
        "（`dispatch._REQUEST_OWED_BY` 那一笔：`generate.build_body` 今天连 `tools` 都不发）。"
        " 今天的事实：**那一笔已经接上了**（`_REQUEST_OWED_BY` 自己就那么写着）——"
        "请求体那一格**发得出工具**，而整理态那两扇门由页面那一侧递"
        "（`consolidation_write` ／ `consolidation_read`）。"),
    "terminal_entry":
        "**已经接上了**（2026-10-12，作者：「要」）。原先写的是：**终端那个入口装不出本模块**"
        "（`console.main()` 手上没有那条名字守卫 —— 见 `console._a_consolidation` 与 "
        "`web.serve()` 那一处 `consolidation_write=`），欠主：**终端入口自己**。"
        " 今天那条路走的是 `launch --console`：入口把**与页面那条路同一套**递过去"
        "（`console.main(root, **handed)` ⇒ `assemble(**handed)`），而那扇门由入口那一层"
        "现造一份 `web.PersonaPort`（与 `the_self_account_handlers` 同一个成法）；"
        "`console.main` 把外面递进来的**原样转交**。"
        " ⚠️ **不递 ⇒ 逐字节与从前一样**（那条半成品仍在：这条路照旧跑得起来，只是没有那几样）。",
    # **2026-10-05 还清了一笔**：经历三样**都有家了**（P0① 收口完成）——
    #   ① 对话原话 ⇒ `经历/对话/`（`memory.project_transcript`）
    #   ② 笔记 ⇒ `经历/笔记/`（`memory.project_notes`，逐字节）
    #   ③ 整理那一趟的**账** ⇒ `经历/整理/`（`memory.project_events`，按 `consolidate-` 那几类）
    #   全部在 `launch.py` 那一层起服务之前跑一遍。
    #   ⚠️ 而"整理那一趟交回的**原文**"不在这三条腿上：按作者拍的「甲」，那一格
    #      （`consolidation_write`）归 persona 那两份 —— 所以这条腿记的是**账**、不是那份正文。
    #   ⇒ 按本仓那条"欠账还了就删条目"，这一笔删掉了。

}


def _worth_doing(context: Sequence[Mapping]) -> bool:
    """ **空跑保护**（`10:27`：快照里**只有 system** 就不跑）。

    ⚠️ 它判的是**那一段里有没有"发生过的事"** —— 一条 `role != "system"` 的记录。
        只有 system 的一趟，看到的是"我是谁"，而**没有任何刚过去的事可收**：
       跑它等于凭空重写一遍自己（那是改人设，不是整理）。
    """
    return any(one.get("role") != "system" for one in context)


# ── **纯文本投影**（2026-10-17 批①）───────────────────────────────────────────
#
# 那一趟拿到的是**交接前的整份线格式快照**（`session.outbound` 交回的那个形状）。
# 里面有图 —— 而"整理"要收的是"这段时间想明白了什么"，**不要像素**。
#
# ⚠️ **为什么非加这一层**（真跑量到的两次 400，各自是各自的病）：
#    · `input_image.image_url must use HTTP(S) or a data URI` —— 那一片的
#      `image_url` 是个本地路径，上游不认；
#    · `input Item must contain type` —— 快照里夹着认不出来的项（空项／怪形状）。
#    ⇒ 两种都证明不了"删掉图就好了"：一个是"图的值不合法"，一个是"这一项本身不合法"。
#      所以这里**整片重建**，不是把 `image_url` 摘掉留一个空壳（那正是畸形内容块）。
#
# ⚠️ **深度不许变**：条数、次序、角色照快照 —— 这一层只换"内容怎么表示"，
#   不压缩层级、不并条、不丢轮次（并了就不是"同一段历史"了）。
# ⚠️ **它只被这一趟叫到**：正常聊天那条视觉路径一个字都不动。

#: 有图、但**没有说明**时补的那一句（作者 2026-10-17 指定的原文）。
NO_IMAGE_NOTE = "此处有图片，本次整理未提供图片内容"

#: 图片那几片在两个协议里的两个名字（与 `session._render_message` 产的形状一致）。
_IMAGE_PART_TYPES: tuple[str, ...] = ("input_image", "image_url")
#: 文字那几片（`responses` 叫 `input_text`、`chat` 叫 `text`、输出叫 `output_text`）。
_TEXT_PART_TYPES: tuple[str, ...] = ("input_text", "text", "output_text")
#: 工具那两项 —— 与 `protocol.clean_items` 认得的那两个名字同一条口径。
_TOOL_ITEM_TYPES: tuple[str, ...] = ("function_call", "function_call_output")
#: 一条工具调用／回执在这段历史里**最多留多少字**（`10:33` 那条"回执 800"的边际）。
_TOOL_TEXT_AT_MOST = 800


def _a_marker(n: int, why: str) -> dict[str, Any]:
    """ **一条认不出来的项** —— 换成一句**带序号**的文字，而不是把它悄悄扔掉。

    ⚠️ 保留位置（深度不变）＋ 留一个**查得到的记号**：这是"明确留下可追踪标记"那一档。
       `why` 只写**形状**（类型名／缺哪个键），**不写正文** —— 这一格会进日志。
    """
    return {"role": "user",
            "content": "［整理：第 " + str(n) + " 条认不出来（" + why + "），这一条已略过］"}


def _cut(value: Any) -> str:
    """ 工具那一侧的文字**留一个上界**（超出就截断并标明）。"""
    said = str(value if value is not None else "")
    if len(said) <= _TOOL_TEXT_AT_MOST:
        return said
    return said[:_TOOL_TEXT_AT_MOST] + "……（原样还有 " + str(len(said) - _TOOL_TEXT_AT_MOST) + " 字，此处截断）"


def _a_caption_of(part: Mapping) -> str:
    """ **图那一片自己带的说明**（有就留，没有就交空串）。

    ⚠️ 只认**明写的**说明字段（`caption` ／ `alt` ／ `description`）——
       不拿 `image_url` 那个值当说明（那是个路径，不是话）。
    """
    for key in ("caption", "alt", "description"):
        said = part.get(key)
        if isinstance(said, str) and said.strip():
            return said.strip()
    return ""


def _one_as_text(one: Any, *, n: int) -> dict[str, Any]:
    """ **一项怎么投影**（闸在 `as_text` 那一层，这里只管一条）。"""
    if not isinstance(one, Mapping):
        return _a_marker(n, "不是一条映射（" + type(one).__name__ + "）")
    kind_of = str(one.get("type") or "")
    if kind_of in _TOOL_ITEM_TYPES:
        call_id = str(one.get("call_id") or "")
        if not call_id:
            return _a_marker(n, "工具那一条缺 call_id")
        if kind_of == "function_call":
            return {"type": "function_call", "call_id": call_id,
                    "name": str(one.get("name") or ""),
                    "arguments": _cut(one.get("arguments"))}
        return {"type": "function_call_output", "call_id": call_id,
                "output": _cut(one.get("output"))}
    role = str(one.get("role") or "")
    if not role:
        return _a_marker(n, "既不是工具也没有 role")
    body = one.get("content")
    if isinstance(body, str):
        if not body.strip():
            return _a_marker(n, "正文是空的")
        return {"role": role, "content": body}
    if not isinstance(body, (list, tuple)):
        return _a_marker(n, "正文既不是字也不是片（" + type(body).__name__ + "）")
    texts: list[str] = []
    had_image = False
    for part in body:
        if isinstance(part, str):
            if part.strip():
                texts.append(part)
            continue
        if not isinstance(part, Mapping):
            texts.append("［整理：这一片不是文字也不是图，已略过］")
            continue
        part_type = str(part.get("type") or "")
        if part_type in _IMAGE_PART_TYPES:
            had_image = True
            said = _a_caption_of(part)
            if said:
                texts.append(said)      # **图换了，说明留着**
            continue
        if part_type in _TEXT_PART_TYPES or isinstance(part.get("text"), str):
            said = str(part.get("text") or "")
            if said.strip():
                texts.append(said)
            continue
        texts.append("［整理：这一片认不出来（type=" + (part_type or "没有 type") + "），已略过］")
    if not texts:
        # **有图没说明** ⇒ 补那一句；连图都没有 ⇒ 这一条本来就没内容（留记号）
        texts = [NO_IMAGE_NOTE] if had_image else ["［整理：这一条没有可读的正文，已略过］"]
    if len(texts) == 1:
        return {"role": role, "content": texts[0]}
    return {"role": role,
            "content": [{"type": "input_text", "text": said} for said in texts]}


def as_text(context: Sequence[Any]) -> list[dict[str, Any]]:
    """ **把交接快照投影成纯文本** —— 整理那一趟唯一的入口（见上面那一段的理由）。

    · **深度不变**：交回几项就是几项，次序与角色照旧；
    · **图整片换掉**：带说明的留说明，没说明的补 `NO_IMAGE_NOTE`；
    · **工具照旧成对**：只截长度，不动 `call_id`（配对照旧合法）；
    · **认不出来的**：换成带序号的记号行（位置不丢、查得到）。
    """
    return [_one_as_text(one, n=n) for n, one in enumerate(context or (), start=1)]


def _projection_facts(context: Sequence[Any], projected: Sequence[Mapping]) -> dict[str, int]:
    """ 投影前后的**计数**（日志用：只写数，不写正文，也不写任何密钥）。"""
    images = 0
    tools = 0
    for one in context or ():
        if not isinstance(one, Mapping):
            continue
        body = one.get("content")
        if isinstance(body, (list, tuple)):
            images += sum(1 for part in body
                          if isinstance(part, Mapping)
                          and str(part.get("type") or "") in _IMAGE_PART_TYPES)
        if str(one.get("type") or "") in _TOOL_ITEM_TYPES:
            tools += 1
    marked = sum(1 for one in projected
                 if str(one.get("content") or "").startswith("［整理："))
    return {"items": len(list(projected)), "images": images,
            "tools": tools, "unreadable": marked}


def _worth_another_try(exc: BaseException) -> bool:
    """ **这一趟值不值得再试一次**（2026-10-17 补验 ④）。

    判据照 `generate` 那条重试环自己的分类（`LLMError.retryable`）来，**不另立一套**：

      · `LLMError` —— `retryable` 说了算（400 那一类 `invalid_value` ⇒ **不重试**；
        5xx ／ 429 ／ 408 ／ 连接失败 ⇒ 可以再试一次）；
      · `TransportError` ／ 两条超时 —— **可重试**（读断、连不上、等不到首帧）；
      · `RoundCancelled` —— **不重试**（那一轮已经被取消了）；
      · `IrreversibleError` ／ 别的程序侧错误 —— **不重试**：那不是"再试一次会好"，
        把它当请求错误重试只会**多花一次钱、还掩盖真正的毛病**。
    """
    from .generate import (IrreversibleError, RoundCancelled,  # noqa: PLC0415
                           FirstResponseTimeout, ContentIdleTimeout)
    from .transport import LLMError, TransportError            # noqa: PLC0415
    if isinstance(exc, (RoundCancelled, IrreversibleError)):
        return False
    if isinstance(exc, LLMError):
        return bool(getattr(exc, "retryable", False))
    if isinstance(exc, TransportError):
        return exc.retryable
    if isinstance(exc, (FirstResponseTimeout, ContentIdleTimeout)):
        return True
    return False


def _version_of(text: str) -> str:
    """ 一份正文的**版本** —— 与 `web._version_of` **同一条算法**（逐字节 sha256）。

    ⚠️ **为什么这里也写一份**：`web.py` 有一条硬规矩 —— 它**不 import 任何同胞模块**
       （那一面全是注入进来的）。而"提交时比对版本"这件事两边都要算，所以算法在这儿
       重写了一遍。⇒ **两处必须一致**：`tests/test_commit_gate.py` 里有一条判据钉着它
       （同一条正文在两边算出同一个版本）。
    """
    import hashlib
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, body: Any) -> None:
    """ 草稿那份 JSON **原子地**写下去（临时文件 → 关闭 → 替换）。"""
    import os as _os
    import tempfile as _tempfile
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, made = _tempfile.mkstemp(prefix="." + target.name + ".tmp-", dir=str(target.parent))
    tmp = Path(made)
    try:
        with _os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            json.dump(body, stream, ensure_ascii=False)
            stream.flush()
            _os.fsync(stream.fileno())
        _os.replace(str(tmp), str(target))
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def _a_category(exc: BaseException) -> str:
    """ **这一趟是怎么砸的** —— 四个笼统的类别（日志用，判据也读它）。

    ⚠️ 为什么非要分：从前账上只有一句 `str(exc)` ⇒ **"上游没回话"与"回话读不出来"
      在账上长得一样**，而这两件的处理完全不同（前者等一等再来，后者要改指令或改形状）。
    ⚠️ 类别**只有四个**：多一个就多一处要维护的口径，而真正要分开的就是这四件事。
    """
    if isinstance(exc, ConsolidationReplyError):
        return "reply"                      # 校验失败：答复读不成那三段／越了上界
    name = type(exc).__name__
    if name == "PersonaConflict":
        return "write"                  # 版本对不上：这一版没落（不是"请求失败"）
    if name in ("PersonaWriteRefused", "PersonaShapeRefused", "PersonaPathRefused"):
        return "write"                      # 写不进去（名字守卫／承重形状／路径）
    if isinstance(exc, OSError):
        return "write"
    return "request"                        # 请求那一侧（含传输、超时、上游 4xx/5xx）


def _parse(text: str) -> dict[str, Any]:
    """把那一段答复读成那三段 —— **读不成当场抛**（不猜、不吞）。

    ⚠️ **为什么要"从第一个 `{` 到最后一个 `}`"那一步**：指令要的是"只回那个对象"，
       而模型很爱在前后各留一句人话 —— 那不影响它交回来的东西是不是那三段。
       ⚠️ 而**不许**用一个宽松的正则去"捞出键值"：那样读出来的是**猜的**，
       而这一段的落点是另一头的档案（错的正文比缺的正文坏）。
    ⚠️ **一个键都不许少**：`KEYS` 四个都必须在（值可以是空那一串）。
       少一个说明它没照这一段要的形状交 —— 那是**下一次能修的事**，不是这一次要猜的事。
    """
    got: Any = None
    inner = text[text.find("{"):text.rfind("}") + 1] if "{" in text else ""
    for candidate in (text, inner):
        if not candidate:
            continue
        try:
            got = json.loads(candidate)
            break
        except (ValueError, TypeError):
            continue
    if not isinstance(got, dict):
        raise ConsolidationReplyError(f"那一趟没有交回一个能读的对象：{text[:200]!r}")
    missing = [one for one in KEYS if one not in got]
    if missing:
        raise ConsolidationReplyError(f"那个对象少了这几格：{missing}（要有 {list(KEYS)}）")
    for one in KEYS:
        if got[one] is not None and not isinstance(got[one], str):
            raise ConsolidationReplyError(f"`{one}` 那一格不是一段文字：{got[one]!r}")
    if PENDING_KEY in got and not isinstance(got[PENDING_KEY], list):
        raise ConsolidationReplyError(f"`{PENDING_KEY}` 必须是数组")
    return got


class Consolidation:
    """ **整理态那一趟** —— 串行、空跑保护、退一次、把结果经 `write` 落下来。

    ⚠️⚠️ **它是"同一个我的另一段时间"，不是另一个 agent**（`10:18`）：
       同一份档案、同一段历史，只是那一段没有人在跟它说话。
    ⚠️ **它不解析、不判断"写得对不对"**：它只判**能不能读成那三段**（`_parse`），
       以及**单份有没有大到不像整理**（`MAX_BYTES_PER_FILE`）。正文那一半归另一头。
    """

    def __init__(
        self,
        *,
        config: Any,
        transport: Any,
        #: 与 `round.py` 那一格**同一个形状**（`generate(cfg, messages, transport)`）——
        #: 不是"再发明一个调模型的口"：那是本仓唯一那一个。
        generate: Callable[..., Awaitable[Any]],
        #: **另一头那扇门**：`(name, text, stamp) -> 任何东西`（由装配那一层递）。
        #: ⚠️ 它**自己不许**在别处再拼一条路径 —— 名字守卫只有一处（见模块抬头）。
        #: ⚠️ **第三个实参是那一刻** —— 本模块不读钟（见抬头），而"写一份档案要一个时刻"
        #:    这件事由 `stamp` 递进来、由本模块**转交给那扇门**（它自己去决定那意味着什么）。
        write: Callable[[str, str, str], Any],
        #: **读回那两份的现状**：`(name) -> str`（由装配那一层递，与 `write` 同一个来源）。
        #: ⚠️⚠️ **它是本刀的修正，而理由是量出来的**：没有它，这一趟**只有**上面那段对话
        #:    —— 而"回头看你写自己的那一份"这件事，**先得把那一份放在它眼前**。
        #: 实测：没有它，`自述` 交回来的是一句"没有新东西要收……交回原样"，
        #:    而那一句会**真的变成那一份的正文**（见 `prompts._instruction` 那段）。
        #: ⚠️ **`None` ⇒ 这一趟不跑**（`schedule` 当场回假并记账）—— "读不到它要改的东西
        #:    却照样改"是这一整类事里最坏的那一种：那是**瞎写**。
        read: Callable[[str], str] | None = None,
        #: 那一刻的形状（`YYYYMMDD-HHMMSS`）—— 与 `stamp` 一起构成"这一版是哪一版"。
        stamp: Callable[[], str],
        #: 浮现那一条从这儿出去（**注入**，不是发言 —— `10:37`）。 `None` ⇒ 不打扰。
        inject: Callable[[str], Awaitable[None]] | None = None,
        instruct: str = INSTRUCTION,
        stable_prefix: bool = False,
        prefix_of: Any = None,
        max_bytes: int = MAX_BYTES_PER_FILE,
        backoff_ms: int = 30_000,
        sleep: Callable[[int], Awaitable[None]] = lambda ms: asyncio.sleep(ms / 1000),
        log: Callable[[str, dict], None] = lambda event, detail: None,
        pending_read: Callable[[], Sequence[str]] | None = None,
        pending_settle: Callable[[str, str], bool] | None = None,
        #: **草稿与提交账落在哪**（2026-10-17 批②）：一个目录。`None` ⇒ 不存草稿
        #: （判据里那一档；生产由入口递 `data/consolidation`）。
        drafts: Any = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._generate = generate
        self._write = write
        self._stamp = stamp
        self._read = read
        self._inject = inject
        self._instruct = instruct
        self._stable_prefix = stable_prefix
        self._prefix_of = prefix_of
        from .evidence_reuse import EvidenceReuse
        self._evidence_reuse = EvidenceReuse()
        self._max_bytes = max_bytes
        self._backoff_ms = backoff_ms
        self._sleep = sleep
        self._log = log
        self._pending_read = pending_read
        self._pending_settle = pending_settle
        self._drafts = None if drafts is None else Path(drafts)
        #: **这一趟的流水号**（与 `stamp` 一起构成稳定的任务 ID —— 2026-10-17 批①）。
        #: ⚠️ 它是**进程内**的计数：`stamp` 在同一秒里可能一样，而这个序号不会。
        self._seq = 0
        #: **串行链** —— `10:29`：整理态自己一次一个（两个同时跑会改同一份档案）。
        #: ⚠️ 它是**上一趟的句柄**，不是锁：新的一趟**排在它后面**（不丢、也不并行）。
        self._chain: asyncio.Future | None = None
        self._queued_recovery: set[str] = set()
        self._background_tasks = {}
        self._stopping = False
        self._activity_control = None

    def bind_activity_control(self, control):
        if self._activity_control is not None or self._background_tasks:
            raise RuntimeError('consolidation activity binding is closed')
        self._activity_control = control

    async def _generate_at_boundary(self, *args, **kwargs):
        if self._activity_control is not None:
            async with self._activity_control.background_generation():
                return await self._generate(*args, **kwargs)
        return await self._generate(*args, **kwargs)

    def own_background_task(self, task, *, role='chain'):
        """Retain exact chain/close tasks, including shielded predecessors.

        Called synchronously immediately after task creation, before any await.
        Successful completed tasks may leave the observation set; cancellation,
        exceptions and a close returning False keep their original responsibility.
        """
        if not isinstance(task, asyncio.Future) or role not in ('chain', 'close'):
            raise ValueError('owned background future and role required')
        if task in self._background_tasks:
            if self._background_tasks[task] != role:
                raise ValueError('background task role changed')
            return task
        self._background_tasks[task] = role
        def retired(done):
            if (not done.cancelled() and done.exception() is None
                    and not (role == 'close' and done.result() is False)):
                self._background_tasks.pop(done, None)
        task.add_done_callback(retired)
        return task

    def _chain_predecessors(self):
        # The latest tail may have been cancelled while shielding an older job.
        # Capture every still-running chain now, before publishing the new tail.
        return tuple(task for task, role in self._background_tasks.items()
                     if role == 'chain' and not task.done())

    async def _wait_predecessors(self, previous):
        # Keep the private single-Future seam; scheduling uses a frozen tuple.
        predecessors = (previous,) if isinstance(previous, asyncio.Future) else previous
        for predecessor in predecessors:
            try:
                await asyncio.shield(predecessor)
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            except Exception:
                pass  # Retained failure is still visible in shutdown_state().

    def begin_shutdown(self):
        # Stop admitting new chains; do not cancel predecessor/model/close work.
        self._stopping = True

    def shutdown_tasks(self):
        return tuple(self._background_tasks)

    def shutdown_state(self):
        states = []
        for task, role in tuple(self._background_tasks.items()):
            if not task.done():
                state = 'pending'
            elif task.cancelled():
                state = 'cancelled'
            elif task.exception() is not None:
                state = 'failed'
            elif role == 'close' and task.result() is False:
                state = 'failed'
            else:
                state = 'done'
            states.append(dict(role=role, state=state))
        return dict(quiescent=all(s['state'] != 'pending' for s in states),
                    cleanup_ok=all(s['state'] == 'done' for s in states
                                   if s['role'] == 'close'),
                    work_ok=all(s['state'] == 'done' for s in states
                                if s['role'] == 'chain'),
                    tasks=tuple(states))


    # ── 排进队列（交接那一刻调它一次）─────────────────────────────────────────

    def schedule(self, context: Sequence[Mapping]) -> bool:
        """ **排队跑一次** —— 回"排上了没有"（空跑保护挡下来的那一种是 `False`）。

        ⚠️⚠️ **它不 `await`**（`10:52`：`chain = chain.then(run, run)` 且 **schedule 不被 await**）
           —— 梦**与主对话并行**，各占引擎一个位。 而"并行"不等于"无序"：
           同一个梦的下一次**排在这一次后面**（上面那条链）。
        ⚠️ **`context` 由调用方在那一刻抓下来**（那一刻的线格式表）——
            **不许在这里回头去读会话**：这一趟跑起来的时候，交接已经
           `session.reset(...)` 过了 ⇒ 那时候读到的**不是**交接前那一份。
        ⚠️ **重复排**（交接失败重试时会再抓一次快照）⇒ 两次**同内容**的整理。
            这是**已知的**（参考实现同形）：不在这里去重 —— 去重要有"这两份是不是同一段"
           那种判据，而那是一件独立的事。两次的效果是"多写一版"，不是"写坏"。
        ⚠️⚠️ **`read` 没接 ⇒ 这一趟不排**（回假 ＋ 一条 `consolidation-skip` 的账）：
            读不到它要改的那两份、却照样把它交回来的东西写下去 —— 那是**瞎写**，
           而它比"这一趟没跑"坏得多（前者**改坏**，后者只是**没动**）。
        """
        if self._stopping:
            return False
        kept = list(context)
        if self._activity_control is not None and self._activity_control.paused():
            self._log('consolidate-skip', {'why':'operator-paused'})
            return False
        if not _worth_doing(kept):
            self._log("consolidate-skip", {"why": "only-system", "n": len(kept)})
            return False
        if self._read is None:
            self._log("consolidate-skip", {"why": "no-read", "n": len(kept)})
            return False

        loop = asyncio.get_running_loop()
        # **一个稳定的任务 ID**（批①）：带随机尾缀。它只用于把"这一趟"的几行账串起来，
        #  ⚠️ 不含任何正文、不含密钥、不含路径。
        self._seq += 1
        task = self._a_task_id()
        self._log("consolidate-queued", {"task": task, "n": len(kept)})
        previous = self._chain_predecessors()
        self._chain = self.own_background_task(
            loop.create_task(self._after(previous, kept, task,
                prefix=self._prefix_of(kept) if self._prefix_of is not None else None)))
        return True

    def schedule_recover(self, task: str) -> bool:
        if self._stopping:
            return False
        if self._drafts is None:
            return False
        path = self._draft_path(task)
        if not path.exists() and not self._chunk_path(task).exists():
            return False
        if task in self._queued_recovery:
            return True
        if len(self._queued_recovery) >= 16:
            return False
        loop = asyncio.get_running_loop()
        previous = self._chain_predecessors()
        self._queued_recovery.add(task)
        async def queued():
            try:
                await self._wait_predecessors(previous)
                answer = await self.recover(task)
                self._log("consolidate-resume-result", {"task":task,"status":answer.get("status")})
                return answer
            finally:
                self._queued_recovery.discard(task)
        self._chain = self.own_background_task(loop.create_task(queued()))
        self._chain.add_done_callback(lambda _: self._queued_recovery.discard(task))
        self._log("consolidate-resume-queued", {"task":task})
        return True

    def chunk_status(self):
        from .consolidation_chunks import read_checkpoint
        if self._drafts is None or not self._drafts.exists():
            return []
        rows = []
        # Diagnostic listing is bounded and never returns snapshot/summary text.
        paths = sorted(self._drafts.glob("*.chunks.json"), key=lambda p:p.stat().st_mtime,
                       reverse=True)[:4]
        for path in paths:
            task = path.name[:-len(".chunks.json")]
            try:
                body = read_checkpoint(path)
                final = self._read_draft(task) if self._draft_path(task).exists() else None
                rows.append({"task":task,"completed_chunks":body["steps"],
                    "next_row":body["cursor"][0],"rows":len(body["rows"]),
                    "status":("done" if final and final["stages"].get("done") else
                              "commit-pending" if final else "chunk-pending")})
            except (ValueError,OSError,KeyError):
                rows.append({"task":task,"status":"invalid-checkpoint"})
        return rows

    async def _after(self, previous: asyncio.Future | tuple[asyncio.Future, ...],
                     context: list[Mapping], task: str = "", *, prefix=None) -> ConsolidationResult | None:
        """ **等上一趟，再跑这一趟** —— 上一趟炸了**也要接着跑这一个**（`.then(run, run)`）。

        ⚠️ **它把这一趟的结果交回去**（`return`）：那条链的句柄是**判据唯一够得着**这一趟的地方
           （生产那条路 `await` 都不 `await` 它）—— 不交回去的话，"那一趟到底写了什么"
           在测试里只能**从副作用反推**，而那正是本仓最忌的那种读法。
        """
        await self._wait_predecessors(previous)
        return await self.run(context, task=task, prefix=prefix)

    # ── 那一趟本身 ──────────────────────────────────────────────────────────

    def _chunk_path(self, task):
        return self._draft_path(task).with_name(str(task) + ".chunks.json")

    async def run(self, context: Sequence[Mapping], *,
                  task: str = "", prefix=None) -> ConsolidationResult | None:
        from .consolidation_chunks import ChunkError, job_lock
        from .activity_control import BackgroundDeferred
        task = self._a_task_id(task)
        try:
            with ExitStack() as lease:
                locked = self._drafts is not None
                if locked:
                    lease.enter_context(job_lock(self._chunk_path(task).with_suffix(".lock")))
                    if self._draft_path(task).exists():
                        from .consolidation_chunks import read_checkpoint, digest
                        if self._chunk_path(task).exists() and read_checkpoint(self._chunk_path(task))["source"] != digest(as_text(context)):
                            raise ChunkError("chunk_task_source_changed")
                        result = await self._recover_final(task)
                        if result.get("status") not in ("recovered", "files-only"):
                            return None
                        return ConsolidationResult(wrote=tuple(result.get("done", ())),
                            emergence=None, text="", settled=tuple(result.get("settled", ())))
                return await self._run_task(context, task=task, _lease=lease, _locked=locked, prefix=prefix)
        except BackgroundDeferred:
            self._log('consolidate-deferred', {'task':task, 'why':'runtime-stopping'})
            return None
        except (ChunkError, OSError) as exc:
            self._log("consolidate-chunk-blocked", {"task":task,
                "err_class":type(exc).__name__,
                "code":str(exc) if isinstance(exc, ChunkError) else "checkpoint_io_failed"})
            return None

    async def _run_task(self, context: Sequence[Mapping], *, task: str,
                        _lease, _locked=False, _projected=False, prefix=None) -> ConsolidationResult | None:
        """**一趟整理** —— 交回那三样；**办不成交回 `None`**（而它出一行日志）。

        ★ **批②（2026-10-17）：这一趟被拆成五段，每一段各过各的重试**：

        ```
        ① 生成   可重试（上限两次）—— **写盘之前**，而且"看到的历史"两次一样
        ② 校验   读不成那三段 ／ 少键 ／ 越上界 ⇒ **一次都不重试**，一个字不落盘
        ③ 存草稿 失败 ⇒ **不开始提交**（半份草稿比没写更坏）
        ④ 提交   逐份带**基线版本**过共同提交门；版本变了 ⇒ 不覆盖、不拼接、留草稿
        ⑤ 后续   悬着的事落定 ／ 浮现投递 —— **只重试这一步**，绝不为它重跑模型
        ```

        ⚠️⚠️ **从前那一个大 `try` 把五段罩在一起**：于是"投递失败"会把"文件已经写好"
           这件事一起抹掉，而"提交失败"会触发**重新生成**（模型可能给出另一版）。
        ⚠️ **它吞异常**（与参考那一版同形）：这是**后台**那一趟，没有人 `await` 它的结果。
        ⚠️ **日志只写计数、类别、版本前缀** —— 不写正文、不写密钥、不写图。
        """
        # **任务 ID**：没给就现分配一个（唯一、可持久 —— 见 `_a_task_id`）
        task = self._a_task_id(task)
        shown = list(context) if _projected else as_text(context)
        facts = _projection_facts(context, shown)
        self._log("consolidate-running", {"task": task, **facts})
        # ★ **生成之前读的那一次，就是基线**：生成期间不持锁，提交时要拿
        #    「我当时读到的是哪一版」去比对。**在这里读、之后不再读**。
        state = dict(await file_call(self._their_state, ))

        pending_snapshot = tuple(dict.fromkeys(self._pending_read() if self._pending_read else ()))
        ask = the_ask(self._blocks(state, pending=pending_snapshot), instruct=self._instruct)
        def final_messages(history):
            if not self._stable_prefix:
                return [*history, {"role": "user", "content": ask}]
            # Read-only material and habits usually outlive one handoff. Put the
            # complete fresh state before changing history, without removing any
            # evidence or changing the legacy chunk/checkpoint ask below.
            instruction = self._instruct.replace("上面那段对话", "随后提供的对话")
            priorities = {MANNERS_FILE: 0, HER_FILES[2]: 1, HER_FILES[1]: 2, HER_FILES[0]: 3}
            blocks = sorted(self._blocks(state, pending=pending_snapshot),
                            key=lambda pair: priorities.get(pair[0], 4))
            state_text = the_ask(blocks, instruct=self._instruct)[len(self._instruct):].lstrip('\n')
            leading = 0
            while leading < len(history) and history[leading].get('role') == 'system':
                leading += 1
            return [{"role": "system", "content": instruction}, *history[:leading],
                    {"role": "user", "content": state_text}, *history[leading:],
                    {"role": "user", "content": "请按后台整理指令，结合已提供的当前记录审阅以上历史，交回 JSON 对象。"}]
        messages = final_messages(shown)
        ensure_final_config = None
        try:
            fork = getattr(self._config, "fork_round", None)
            cfg = await fork() if callable(fork) else self._config
            window = getattr(cfg, "context_window", None)
            output = getattr(cfg, "max_tokens", None)
            if type(window) is int and type(output) is int:
                from .tokens import estimate_messages_tokens
                estimated = estimate_messages_tokens(messages)
                # Leave estimation/tokenizer slack; do not silently discard history.
                from .input_budget import hard_input_tokens
                hard = hard_input_tokens(window, output, getattr(cfg, "base_url", ""))
                limit = int(hard * 0.95)
                self._log("consolidate-budget", {"task": task, "estimate": estimated,
                    "hard": hard, "limit": limit, "context_window": window})
                chunked = self._drafts is not None and self._chunk_path(task).exists()
                if (estimated > limit or chunked) and self._drafts is not None:
                    from .consolidation_chunks import ChunkJob, digest, job_lock, ChunkError
                    if not _locked:
                        _lease.enter_context(job_lock(self._chunk_path(task).with_suffix(".lock")))
                    if estimate_messages_tokens([{"role":"user","content":ask}]) >= limit:
                        raise ChunkError("chunk_final_fixed_input_too_large")
                    def binding_for(one):
                        identify = getattr(self._config, "_follow_identity", None)
                        identity = identify() if callable(identify) else None
                        values = getattr(one, "values", None)
                        auth = values().get("auth", "api_key") if callable(values) else "api_key"
                        return digest([identity, getattr(one,"base_url",None),
                                       getattr(one,"model",None),getattr(one,"kind",None),auth])
                    binding = binding_for(cfg)
                    async def ensure_job_config():
                        if callable(fork):
                            latest = await fork()
                            if binding_for(latest) != binding:
                                raise ChunkError("chunk_runtime_binding_changed")
                            if (latest.context_window != cfg.context_window or latest.max_tokens != cfg.max_tokens):
                                raise ChunkError("chunk_runtime_budget_changed")
                    job = await file_call(ChunkJob, self._chunk_path(task), shown, ask, binding,
                        save=_atomic_write_json, log=self._log, task=task)
                    async def generate_stage(stage, chunk_messages):
                        for attempt in (1,2):
                            await ensure_job_config()
                            try:
                                return await self._generate_at_boundary(stage, chunk_messages, self._transport)
                            except Exception as exc:
                                self._log("consolidate-stage-failed", {"task": task,
                                    "attempt": attempt, "err_class": type(exc).__name__})
                                acknowledge = getattr(self._generate, "acknowledge_failure", None)
                                if callable(acknowledge):
                                    acknowledge(exc)
                                if attempt == 2 or not _worth_another_try(exc):
                                    raise
                                await self._sleep(self._backoff_ms)
                    evidence = await job.compress(cfg, limit, generate_stage)
                    await ensure_job_config()
                    ensure_final_config = ensure_job_config
                    # Existing resumable chunk jobs retain their original prompt contract.
                    messages = [evidence, {"role":"user","content":ask}]
                    estimated = estimate_messages_tokens(messages)
                if estimated > limit:
                    self._log("consolidate-failed", {"task": task, "attempt": 0,
                        "category": "budget", "err_class": "ConsolidationInputBudget",
                        "err": "整理输入超过安全预算；历史未截断、档案未改写",
                        "estimate": estimated, "limit": limit})
                    return None
        except Exception as exc:
            from .consolidation_chunks import ChunkError
            if isinstance(exc, (ChunkError, OSError)):
                raise
            self._log("consolidate-failed", {"task": task, "attempt": 0,
                "category": "request", "err_class": type(exc).__name__,
                "err": "整理准备/分块未完成；保留已有检查点，未提交档案"})
            return None

        if prefix is not None and ensure_final_config is None:
            from .consolidation_prefix import PrefixConfig
            prefix.ask=ask
            cfg=PrefixConfig(cfg,prefix)
        else:
            prefix=None

        # ① 生成：**只有可重试的那一类**才再试一次（补验 ④）
        from .evidence_reuse import request_key
        reuse_key = (request_key(cfg, messages) if self._stable_prefix
                     and ensure_final_config is None and prefix is None and not pending_snapshot
                     and STATE_UNREADABLE not in state.values() else None)
        reused_text = self._evidence_reuse.get(reuse_key)
        text = reused_text or ""
        if reused_text is not None:
            self._log("consolidate-evidence-reused", {"task": task})
        for attempt in (() if reused_text is not None else (1, 2)):
            try:
                if ensure_final_config is not None:
                    await ensure_final_config()
                answer = await self._generate_at_boundary(
                    cfg,
                    messages,
                    self._transport)
                if prefix is not None:
                    self._log("consolidate-prefix", {"task":task,"used":prefix.used,"reason":prefix.reason})
                    invalid = bool(getattr(answer,"calls",()) or getattr(answer,"refusal","")
                                   or getattr(answer,"status",None)!="completed")
                    if not invalid:
                        try:_parse(getattr(answer,"text","") or "")
                        except ConsolidationReplyError:invalid=True
                    if invalid:
                        self._log("consolidate-prefix-rejected", {"task":task,"reason":"invalid_review"})
                        # No dispatcher exists in this worker. Reject the entire
                        # proposed answer and retry only through the original path.
                        cfg=cfg.base;prefix=None
                        answer=await self._generate_at_boundary(cfg,messages,self._transport)
                if getattr(answer,"calls",()):
                    raise ConsolidationReplyError("background_tool_call_rejected")
                text = getattr(answer, "text", "") or ""
                break
            except Exception as exc:            # noqa: BLE001 —— 见上
                self._log("consolidate-failed", {"task": task, "attempt": attempt,
                                           "category": _a_category(exc),
                                           "err_class": type(exc).__name__})
                acknowledge = getattr(self._generate, "acknowledge_failure", None)
                if callable(acknowledge):
                    acknowledge(exc)
                # ⚠️ **确定性错误不许原样重试**（400 那一类）；**取消与程序错误**也不是
                #    "再试一次就会好"的东西。只有 `_worth_another_try` 认的那几种才退一次。
                if attempt == 2 or not _worth_another_try(exc):
                    return None
                await self._sleep(self._backoff_ms)
        if not text:
            self._log("consolidate-failed", {"task": task, "attempt": 0,
                                       "category": "reply", "err_class": "EmptyReply",
                                       "err": "回复是空的"})
            return None

        # ② 校验：**确定性错误不重试**（重试它只会再错一遍）
        try:
            got = _parse(text)
            pending = pending_snapshot
            planned = self._pending_plan(got, pending)
            to_write = self._planned_texts(got)        # 只验不改
            if any(state.get(name) == STATE_UNREADABLE for name in to_write):
                raise ConsolidationReplyError("无法读取的自我记录不允许覆盖")
            emergence = self._emergence(got)
        except Exception as exc:                # noqa: BLE001
            self._log("consolidate-failed", {"task": task, "attempt": 1,
                                       "category": "reply",
                                       "err_class": type(exc).__name__})
            return None

        # ③ 草稿：**文件 ＋ 后续动作的计划一起存**（存不下就不提交）
        baseline = {name: _version_of(state.get(name, "")) for name in to_write}
        try:
            draft = await file_call(self._save_draft, task, to_write, baseline,
                                     pending=planned, emergence=emergence,
                                     stages={"files": [], "settled": [],
                                             "emergence_posted": False, "done": False})
        except Exception:                       # noqa: BLE001 —— 存不下就**不提交**
            draft = None
        if draft is None and self._drafts is not None:
            self._log("consolidate-failed", {"task": task, "attempt": 1,
                                       "category": "write", "err_class": "DraftNotSaved",
                                       "err": "草稿没存下来，这一趟不提交"})
            return None

        # ④ 提交：逐份带基线版本过门（`NoVersionCheck` 在**第一份之前**就抛 ⇒ 不写半趟）
        try:
            wrote = await file_call(self._commit, to_write, baseline, task=task)
        except Exception as exc:                # noqa: BLE001
            self._log("consolidate-failed", {"task": task, "attempt": 1,
                                       "category": _a_category(exc),
                                       "err_class": type(exc).__name__,
                                       "err": "整理写入中断，已保存的文件保留。"})
            return None

        # ⑤ 后续：**只重试这一步**（文件已经落盘，绝不为它重跑模型）
        settled = self._settle_pending(planned)
        if settled:
            await file_call(self._update_draft, task, settled=list(settled))
        posted = await self._post_follow_up(emergence, task=task)
        if posted:
            await file_call(self._update_draft, task, emergence_posted=True)

        # ★ **"文件写完"不等于"整趟办完"**：事项还欠着、浮现没投出去 ⇒ 不许记整趟完成。
        left = len(settled) < len(planned) or (emergence is not None and not posted)
        finished = not left
        await file_call(self._update_draft, task, done=finished)
        if finished:
            # Only an entirely effect-free result can be replayed. File changes,
            # pending decisions and emergence still require a fresh assessment.
            if reused_text is None and not to_write and not planned and emergence is None:
                self._evidence_reuse.put(reuse_key, text)
            self._log("consolidate-done",
                      {"task": task, "attempt": 1, "wrote": list(wrote),
                       "settled": list(settled), "emergence": emergence is not None,
                       "emergence_posted": posted,
                       "phase": "written" if wrote else "nothing-to-write"})
        else:
            # ⚠️ **不许把"文件写了"说成"整趟办完"**：后续动作还欠着，账上要这么写。
            self._log("consolidate-files-only",
                      {"task": task, "wrote": list(wrote),
                       "settled": list(settled), "planned_settle": len(planned),
                       "emergence": emergence is not None,
                       "emergence_posted": posted})
        return ConsolidationResult(wrote=wrote, emergence=emergence,
                                   text=text, settled=settled)

    def _blocks(self, state: Mapping[str, str] | None = None, *,
                pending=None) -> list[tuple[str, str]]:
        """ 摆在它眼前的那几份现状（＋ 悬着的事原话）。

        ⚠️ **现状由调用方递**（`state`）：那一份**既是「它眼前是什么」，也是「基线版本」**
           —— 两处各读一次的话，基线会跟着漂，而版本检查就成了摆设。
        """
        blocks = list(state.items()) if state is not None else self._their_state()
        if pending is None:
            pending = tuple(dict.fromkeys(self._pending_read() if self._pending_read else ()))
        if pending:
            blocks.append((PENDING_KEY + "（仅关于自己，原话）",
                           json.dumps(pending, ensure_ascii=False)))
        return blocks

    def _pending_plan(self, got: Mapping[str, Any],
                      pending: Sequence[str]) -> tuple[tuple[str, str], ...]:
        """先校验整批决定，再允许任何一项落盘。"""
        if not pending:
            if got.get(PENDING_KEY):
                raise ConsolidationReplyError("没有悬着的提议，不能凭空落定")
            return ()
        if PENDING_KEY not in got:
            raise ConsolidationReplyError("有悬着的提议，却没有交回处理数组")
        if self._pending_settle is None:
            raise ConsolidationReplyError("悬着的事没有落定入口")
        allowed = set(pending)
        seen: set[str] = set()
        planned: list[tuple[str, str]] = []
        for row in got[PENDING_KEY]:
            if not isinstance(row, dict) or set(row) != {"what", "action"}:
                raise ConsolidationReplyError("悬着的事每项只收 what 和 action")
            what, action = row["what"], row["action"]
            if not isinstance(what, str) or what not in allowed or what in seen:
                raise ConsolidationReplyError("悬着的事必须逐字引用未处理过的原话")
            if action not in ("确认", "降级", "删除"):
                raise ConsolidationReplyError("悬着的事只认确认、降级、删除")
            seen.add(what)
            planned.append((what, action))
        return tuple(planned)

    def _settle_pending(self, planned: Sequence[tuple[str, str]]) -> tuple[str, ...]:
        """一项失败只记账；已经写过档案时不可重放整趟模型结果。"""
        settled: list[str] = []
        for what, action in planned:
            try:
                if self._pending_settle is None or not self._pending_settle(what, action):
                    raise RuntimeError("落定入口没有办成")
                settled.append(what)
            except Exception as exc:  # noqa: BLE001 - 后台逐项记账
                self._log("consolidate-pending-failed",
                          {"action": action, "err_class": type(exc).__name__})
        return tuple(settled)

    def _their_state(self) -> list[tuple[str, str]]:
        """ **那两份的现状** —— `HER_FILES` 那一个次序，一份一段（纯读，不改任何东西）。

        ⚠️⚠️ **读不到不许抛**（那是这一趟**开始之后**才发生的）：一份读不到 ⇒ 那一格写
           "这一次读不到"（`STATE_UNREADABLE`）—— 而 `read is None` 那一档
           **在 `schedule` 就被挡住了**（见那儿：读不到却照写的形状不许它进来）。
        ⚠️ **它一次读两份**（不是只读要改的那一份）：这一趟**不知道自己会改哪一份**
           （那由模型交回来的东西决定）⇒ 两份都得摆在它眼前。
        """
        assert self._read is not None        # `schedule` 已经挡过这一档（见那儿）
        blocks: list[tuple[str, str]] = []
        for name in HER_FILES:
            try:
                blocks.append((name, self._read(name)))
            except OSError:                  # 那一份不在 ⇒ 与"读不到"同一格（不编一句）
                blocks.append((name, STATE_UNREADABLE))
        # **多摆在它眼前的那一份**（2026-10-07，作者拍的「乙」）：`素材/举止.md` ——
        # 它是**原料**，**不是**要改的那一份（所以它**不进** `HER_FILES` ）：
        #    "反复出现的举止过门槛之后升格成「习惯」"这件事，缺的就是这一口原料
        # （ 那一份**不在段序里** ⇒ 平时看不见 而这一趟要看得见 ）。
        try:
            blocks.append((MANNERS_FILE, self._read(MANNERS_FILE)))
        except OSError:                      # 那一份不在 ⇒ 与"读不到"同一格（不编一句）
            blocks.append((MANNERS_FILE, STATE_UNREADABLE))
        return blocks

    def _planned_texts(self, got: Mapping[str, Any]) -> dict[str, str]:
        """ **先全验完**：要写哪几份、各自多少字节（**一个字都还没落盘**）。

        ⚠️ **超上界当场抛**（不截断 —— 见 `MAX_BYTES_PER_FILE` 那段）。
        ⚠️ 空那一串**不进计划**：那是"这一趟没什么可写"，不是"写一份空的"。
        """
        planned: dict[str, str] = {}
        for name in HER_FILES:
            said = got.get(name.rsplit("/", 1)[-1][:-len(".md")], "")
            if not said:
                continue
            size = len(said.encode("utf-8"))
            if size > self._max_bytes:
                raise ConsolidationReplyError(
                    f"`{name}` 这一趟长到 {size} 字节（上界 {self._max_bytes}）"
                    " —— 那不像整理，像把上文抄了一遍 ⇒ 这一趟不写")
            planned[name] = said
        return planned

    # ── 草稿 ／ 任务记录（批②补验 ③⑤）──────────────────────────────────────
    #
    # ⚠️ 这一份**不是日志**：正文在这里**该留**（日志那边才不许写正文）。它要能回答
    #    "这一趟做到哪一步了" —— 文件到哪一份、事项落定了几条、浮现投出去没有。

    #: 草稿的格式版本。**读到一个不认得的版本 ⇒ 拒绝**（不许拿它驱动写盘）。
    DRAFT_FORMAT = 2

    def _a_task_id(self, task: str = "") -> str:
        """ **这一趟的任务 ID** —— 给了就用，没给就**现分配一个唯一的**。

        ⚠️ 从前是 `stamp-序号`：进程内序号**重启归零** ⇒ 同一秒里两次重启会撞出同一个 ID，
           而草稿是按 ID 命名的 ⇒ 后一趟会**覆盖**前一趟的草稿。现在加随机尾巴。
        """
        if task:
            return str(task)
        import secrets                                   # noqa: PLC0415
        return str(self._stamp()) + "-" + secrets.token_hex(16)

    def _draft_path(self, task: str) -> Path:
        """ **这一趟草稿的路径** —— 只许落在这一层目录底下（挡住 `..` 与绝对路径）。"""
        if self._drafts is None:
            raise NoVersionCheck("这一趟没有草稿目录 —— 不提交")
        base = Path(self._drafts).resolve()
        name = str(task or "").strip()
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            raise ValueError("任务 ID 不能当文件名：" + repr(task))
        path = (base / (name + ".draft.json")).resolve()
        if base != path.parent:
            raise ValueError("草稿路径越出了这一层的目录：" + str(path))
        return path

    def _save_draft(self, task: str, planned: Mapping[str, str],
                    baseline: Mapping[str, str], *,
                    pending: Sequence[tuple[str, str]] = (),
                    emergence: str | None = None,
                    stages: Mapping[str, Any] | None = None) -> Path | None:
        """ **把这一版连同后续动作的计划存成草稿** —— 存不下就不许开始提交。"""
        if self._drafts is None:
            return None
        path = self._draft_path(task)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "format": self.DRAFT_FORMAT,
            "task": str(task),
            "created": str(self._stamp()),
            "files": [
                {"name": name, "baseline": str(baseline.get(name, "")),
                 "target": _version_of(said), "text": said}
                for name, said in planned.items()],
            # ★ 后续动作也进草稿：文件恢复完只说明**文件**好了，事项与浮现还欠着。
            "pending": [{"what": str(w), "action": str(a)} for w, a in pending],
            "emergence": emergence,
            "stages": dict(stages or {"files": [], "settled": [],
                                      "emergence_posted": False, "done": False}),
        }
        _atomic_write_json(path, body)
        return path

    def _update_draft(self, task: str, **changed: Any) -> None:
        """ 把进度写回草稿（**只改进度，不改正文**）。"""
        if self._drafts is None:
            return
        path = self._draft_path(task)
        if not path.is_file():
            raise FileNotFoundError(str(path))
        body = json.loads(path.read_text(encoding="utf-8"))
        stages = dict(body.get("stages") or {})
        stages.update(changed)
        body["stages"] = stages
        _atomic_write_json(path, body)

    def _read_draft(self, task: str) -> dict[str, Any]:
        """ **读回一份草稿，并把它验一遍**（2026-10-17 补验 ⑤）。

        ⚠️ **坏草稿不许驱动写盘**：格式版本不认得 ／ 目标摘要与正文不符 ／ 目标文件名
           不在那三份名单里 ⇒ 当场抛（`ValueError`），这一趟不写任何东西。
        """
        path = self._draft_path(task)
        if not path.is_file():
            raise FileNotFoundError(str(path))
        body = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(body, dict):
            raise ValueError("draft object required")
        if body.get("format") != self.DRAFT_FORMAT:
            raise ValueError("这份草稿的格式版本不认得（" + str(body.get("format")) + "）")
        if str(body.get("task") or "") != str(task):
            raise ValueError("草稿里的任务 ID 与它自己的文件名对不上")
        # Validate the complete side-effect plan before any file or pending write.
        files = body.get("files")
        pending = body.get("pending")
        stages = body.get("stages")
        emergence = body.get("emergence")
        if not isinstance(files, list) or not isinstance(pending, list) or not isinstance(stages, dict):
            raise ValueError("draft collections invalid")
        if emergence is not None and (not isinstance(emergence, str) or not emergence.strip()):
            raise ValueError("draft emergence invalid")
        pending_names = set()
        for row in pending:
            if not isinstance(row, dict) or set(row) != {"what", "action"}:
                raise ValueError("draft pending shape invalid")
            what, action = row["what"], row["action"]
            if not isinstance(what, str) or not what.strip() or what in pending_names:
                raise ValueError("draft pending identity invalid")
            if not isinstance(action, str) or action not in ("确认", "降级", "删除"):
                raise ValueError("draft pending action invalid")
            pending_names.add(what)
        if set(stages) != {"files", "settled", "emergence_posted", "done"}:
            raise ValueError("draft stages shape invalid")
        for key in ("files", "settled"):
            values = stages[key]
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values) or len(set(values)) != len(values):
                raise ValueError("draft stage list invalid")
        if type(stages["done"]) is not bool or (stages["emergence_posted"] is not None and type(stages["emergence_posted"]) is not bool):
            raise ValueError("draft terminal invalid")
        if not set(stages["settled"]) <= pending_names:
            raise ValueError("draft settled identity invalid")
        rows = []
        for one in body.get("files") or []:
            if not isinstance(one, Mapping):
                raise ValueError("草稿里的文件计划不是一条映射")
            name = str(one.get("name") or "")
            text = str(one.get("text") or "")
            if name not in HER_FILES:
                raise ValueError("草稿里点名了一份不许写的文件：" + repr(name))
            if _version_of(text) != str(one.get("target") or ""):
                raise ValueError("草稿里那份正文与它自己的摘要对不上：" + name)
            rows.append({"name": name, "baseline": str(one.get("baseline") or ""),
                         "target": str(one.get("target")), "text": text})
        names = [row["name"] for row in rows]
        if len(set(names)) != len(names) or not set(stages["files"]) <= set(names):
            raise ValueError("draft file identities invalid")
        if stages["done"] and (set(stages["files"]) != set(names) or set(stages["settled"]) != pending_names or (emergence is not None and stages["emergence_posted"] is not True)):
            raise ValueError("draft completion inconsistent")
        body["files"] = rows
        return body

    def _commit(self, planned: Mapping[str, str], baseline: Mapping[str, str], *,
                task: str = "") -> tuple[str, ...]:
        """ **逐份提交** —— 每一份都带着"我读到的是哪一版"过共同提交门。

        ⚠️⚠️ **"全部预校验 ＋ 逐份原子替换"不是多文件事务**：第二份失败时第一份
           **已经在盘上了**。所以这里**如实报**已提交的那几份（`consolidate-partial`），
           而不是把整趟说成"没写"。
        ⚠️ **版本变了（`PersonaConflict`）⇒ 停**：不覆盖、不拼接，草稿留着。
        ⚠️ **写口收不下版本（`NoVersionCheck`）⇒ 一份都不写**：那是在**第一份之前**
           就抛出来的，所以不会出现"第一份写了、第二份没写"那种半趟。
        """
        wrote: list[str] = []
        for name, said in planned.items():
            try:
                self._write_guarded(name, said, baseline.get(name, ""), task=task)
            except Exception as exc:            # noqa: BLE001
                if wrote:
                    self._log("consolidate-partial",
                              {"task": task, "committed": list(wrote),
                               "failed_at": name, "category": _a_category(exc),
                               "err_class": type(exc).__name__})
                raise
            wrote.append(name)
            self._update_draft(task, files=list(wrote))
        return tuple(wrote)

    def _write_guarded(self, name: str, said: str, version: str, *,
                       task: str = "") -> Any:
        """ **过门写一份**：带着基线版本；**这扇门收不下版本 ⇒ 拒绝提交**。

        ⚠️⚠️ **不许降级成"记一句警告然后照写"**（2026-10-17 补验 ① 的口径）：
           装配漏传一个参数，后果是**这一趟不落盘**，不是"无保护地落盘"。
        """
        import inspect                                   # noqa: PLC0415
        try:
            params = inspect.signature(self._write).parameters
        except (TypeError, ValueError):
            params = {}
        # ⚠️ `**kw` 那种形参也算"收"：生产那两条线是 `lambda n, t, s, **kw: …`。
        takes = ("expect" in params
                 or any(one.kind is one.VAR_KEYWORD for one in params.values()))
        if not takes:
            raise NoVersionCheck(
                "这扇写口收不下版本（`" + str(name) + "`）—— 这一趟不提交，草稿留着。")
        return self._write(name, said, self._stamp(), expect=version)

    def _write_all(self, got: Mapping[str, Any]) -> tuple[str, ...]:
        """ **老的入口**（判据还在用它）：全验完 ⇒ 逐份写（没有基线版本那一档）。"""
        planned = self._planned_texts(got)
        wrote: list[str] = []
        for name, said in planned.items():
            self._write(name, said, self._stamp())
            wrote.append(name)
        return tuple(wrote)

    async def _post_follow_up(self, emergence: str | None, *, task: str = "") -> bool:
        """ **文件落盘之后的那一步**：把"此刻该知道的一件事"投出去。

        投递结果未知时不重试；文件已写好，绝不重跑模型。
        ⚠️ **投递端没有幂等键** ⇒ 崩溃窗口是"塞进去了但这里没记上"；那一档**标成不确定**
           （`emergence_posted: None`），**不自动重发**、也不说"严格一次"。
        """
        if emergence is None or self._inject is None:
            return False
        # Publish intent before handing control to a non-idempotent receiver.
        # None is durable uncertainty, including cancellation and a failed receipt write.
        if self._drafts is not None:
            body = await file_call(self._read_draft, task)
            if body.get("stages", {}).get("emergence_posted") is not False:
                return body.get("stages", {}).get("emergence_posted") is True
            await file_call(self._update_draft, task, emergence_posted=None)
        try:
            await self._inject(emergence)
        except Exception as exc:
            self._log("consolidate-followup-unknown",
                      {"task": task, "err_class": type(exc).__name__})
            return False
        await file_call(self._update_draft, task, emergence_posted=True)
        return True

    async def recover(self, task: str) -> dict[str, Any]:
        from .consolidation_chunks import ChunkError, job_lock, read_checkpoint
        if self._drafts is None:
            return await self._recover_final(task)
        # Validate before creating the checkpoint lock. Invalid task identifiers
        # have the same structured refusal as final-draft recovery.
        try:
            checkpoint = self._chunk_path(task)
        except ValueError as exc:
            self._log("consolidate-draft-bad", {"task": task, "err_class": type(exc).__name__})
            return {"task": task, "status": "bad-draft", "err_class": type(exc).__name__}
        try:
            with ExitStack() as lease:
                lease.enter_context(job_lock(checkpoint.with_suffix(".lock")))
                if self._draft_path(task).exists() or not self._chunk_path(task).exists():
                    return await self._recover_final(task)
                body = read_checkpoint(self._chunk_path(task))
                result = await self._run_task(body["rows"], task=task, _projected=True,
                                              _lease=lease, _locked=True)
                if result is None:
                    return {"task":task,"status":"chunk-pending"}
                draft = await file_call(self._read_draft, task)
                return {"task":task,"status":"recovered" if draft["stages"].get("done") else "files-only"}
        except (ChunkError, OSError) as exc:
            self._log("consolidate-chunk-blocked", {"task":task,"err_class":type(exc).__name__})
            return {"task":task,"status":"chunk-blocked",
                    "code":str(exc) if isinstance(exc,ChunkError) else "checkpoint_io_failed"}

    async def _recover_final(self, task: str) -> dict[str, Any]:
        """ **把一趟中断的账续上**（2026-10-17 补验 ③）—— 可重复执行。

        两段分开报：
          · **文件**：基线版 ⇒ 续办；目标版 ⇒ 跳过；都不是 ⇒ 停（冲突，不覆盖）；
          · **后续动作**（事项落定 ／ 浮现投递）：只办**还没记上**的那些。
        ⚠️ `status` 有三档说清"到哪一步"：`files-only`（文件好了、后续还欠着）／
           `recovered`（整趟完成）／`conflict`（停）。
        ⚠️ 它**不重新生成**：草稿里就是那一版正文。
        """
        if self._drafts is None:
            return {"task": task, "status": "no-draft"}
        try:
            body = await file_call(self._read_draft, task)
        except FileNotFoundError:
            return {"task": task, "status": "no-draft"}
        except ValueError as exc:
            self._log("consolidate-draft-bad", {"task": task, "err_class": type(exc).__name__})
            return {"task": task, "status": "bad-draft", "err_class": type(exc).__name__}

        stages = dict(body.get("stages") or {})
        self._log("consolidate-recovering", {"task": task, "n": len(body["files"])})

        done: list[str] = []
        for one in body["files"]:
            name = one["name"]
            now = _version_of(self._read(name) if self._read else "")
            if now == one["target"]:
                done.append(name)                 # 已经落过
                continue
            if now != one["baseline"]:
                self._log("consolidate-conflict",
                          {"task": task, "name": name,
                           "expected": one["baseline"][:12], "actual": now[:12]})
                return {"task": task, "status": "conflict", "at": name, "done": done}
            self._write_guarded(name, one["text"], one["baseline"], task=task)
            done.append(name)
        await file_call(self._update_draft, task, files=done)

        # ── 后续动作：只办还没记上的 ──────────────────────────────────────
        already = set(stages.get("settled") or [])
        still = tuple((str(one.get("what") or ""), str(one.get("action") or ""))
                      for one in (body.get("pending") or [])
                      if str(one.get("what") or "") not in already)
        settled_now = self._settle_pending(still) if still else ()
        settled_all = list(already) + list(settled_now)
        if settled_now:
            await file_call(self._update_draft, task, settled=settled_all)

        posted = stages.get("emergence_posted")
        if posted is not True and posted is not False:
            posted = None
        if body.get("emergence") and stages.get("emergence_posted") is False:
            posted = await self._post_follow_up(body["emergence"], task=task)
            if posted:
                await file_call(self._update_draft, task, emergence_posted=True)

        finished = (len(done) == len(body["files"])
                    and not (still and len(settled_now) < len(still))
                    and (not body.get("emergence") or posted is True))
        await file_call(self._update_draft, task, done=finished)
        self._log("consolidate-done" if finished else "consolidate-files-only",
                  {"task": task, "attempt": 0, "wrote": done,
                   "settled": list(settled_now), "emergence": bool(body.get("emergence")),
                   "emergence_posted": posted,
                   "phase": "recovered" if finished else "files-only"})
        return {"task": task, "status": "recovered" if finished else "files-only",
                "done": done, "settled": list(settled_now), "emergence_posted": posted}


    def _emergence(self, got: Mapping[str, Any]) -> str | None:
        """浮现那一格 —— **没有就不打扰**（`10:43`：否则不打扰）。

        ⚠️ **`None` 与空那一串都算"没有"**：那是模型的两条路交出同一个意思。
        """
        text = (got.get("浮现") or "").strip()
        if not text or text == NOTHING:
            return None
        return text


def _done(loop: asyncio.AbstractEventLoop) -> asyncio.Future:
    """一个**已经了结**的 future —— 队列里第一趟要等的那个东西（不必特判 `None`）。"""
    future = loop.create_future()
    future.set_result(None)
    return future
