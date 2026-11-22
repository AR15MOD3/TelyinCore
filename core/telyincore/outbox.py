""" **出站匣** —— 它主动说的那些话落在哪儿（2026-10-07「第 3 刀」的纯那半边）。

⚠️ 为什么单立一个模块：它那件 `send_private`（"主动开口：不等有人先问"）此前**没有执行段**
   —— 而它的落点不是"记忆"（那是 `memory` 的事 ）⇒ 单开一层：**匣子** 。

⚠️ **这一层只做三件、且只做这三件**：
   ① **收**：把一封（正文 ＋ 时刻 ＋ 理由）**追加**进匣子；
   ② **数**：那一天收了几封、最近一封是什么；
   ③ **说**：把②拼成**一句事实**（给它的 system 用 见 `the_facts`）。
    **不判断、不排序以外的裁量、更不设上界** —— "要不要说、说几封"归**它自己**看那格事实决定
     （ 那正是"不先给它限制"那一条的落点：这一层里**没有任何配额** ）。

⚠️ **本模块一次都不读时钟**（本仓那条老规矩 ⇒ 时刻与"哪一天"都由调用方**递进来** ）：
   于是"现在几点"归入口，测试递一串写死的 。

⚠️⚠️ **匣子这一头就是全部**：把话**递到外面**那一头（真的到它那个人眼前）**今天没有** ——
   那要外面那一侧允许"没人问也发话"（`06` §6.7 注 2 ）⇒ 这一层**不许假装做到了**
   （`the_facts` 那句里带着这件事 ）。
"""

from __future__ import annotations

from collections import deque

from .append_journal import append, rows
import pathlib
from typing import Any

#: 匣子那一份的名字（放在**调用方递来的那个目录**底下 —— 它是服务那一侧的东西）。
THE_BOX = "outbox.jsonl"

#: **没有收件人**时那句实话（它把话说进匣子，而匣子外面还没有路 ）。
NO_ROAD = "这边还没有路通到对方，先落在这只匣子里。"


def a_line(*, text: str, stamp: str, reason: str = "", day: str = "") -> dict[str, str]:
    """ 造一条（ 正文空的不许收 —— 与"空的不写"同一条规矩 ）。"""
    said = " ".join(str(text).split())
    if not said:
        raise ValueError("那一封是空的（空的不收）")
    when = str(stamp).strip()
    if not when:
        raise ValueError("没给时刻 —— 本层不读钟，时刻得由调用方递")
    return {"text": said, "stamp": when, "day": str(day or when[:10]),
            "reason": " ".join(str(reason).split())}


def put_a_line(base: Any, *, text: str, stamp: str, reason: str = "",
               day: str = "") -> dict[str, str]:
    """ **收一封** —— 追加进匣子（一个字节都不改前面的那些 ）。"""
    one = a_line(text=text, stamp=stamp, reason=reason, day=day)
    box = pathlib.Path(base) / THE_BOX
    append(box, one)
    return one


def the_lines(base: Any, *, day: str = "") -> list[dict[str, str]]:
    """ 匣子里那几条（给了 `day` ⇒ 只要那一天收的 ；读不成的那几行**跳过**、不抛 ）。"""
    return [row for row in rows(pathlib.Path(base) / THE_BOX)
            if not day or row.get("day", "") == day]


def the_facts(base: Any, *, day: str, road: bool = False) -> str:
    """ **给它的那一格事实**（不是闸 ）——"今天主动说了几封 ／ 最近一封是什么"。

    ⚠️ **一条都不许编**：一封都没有 ⇒ 照实说"今天还没主动说过话"
       （ 而那件事**不影响**它要不要说 —— 这一格是**事实**，判断归它 ）。
    """
    count = 0
    recent = deque(maxlen=3)
    for row in rows(pathlib.Path(base) / THE_BOX):
        if not day or row.get("day", "") == day:
            count += 1
            recent.append(row)
    if not count:
        return ("今天出站匣里没有你主动发起的私聊；这不统计 群聊或对来话的回复，"
                "不能据此判断某条群消息是否已经答过。")
    # 2026-10-11：列最近几次（不只最后一次）—— 同一件事追着问是这条线上最容易出的丑，
    # 而它只要看得见自己刚说过什么就够了。三行封顶、每条截四十字（这一格每轮都重发）。
    out = ["今天你主动发起私聊 " + str(count) +
           " 次（不含 群聊或对来话的回复）。最近几次是："]
    for one in reversed(recent):
        when = str(one.get("stamp") or "")
        if " " in when:
            when = when.split(" ", 1)[1]
        out.append("  · " + when + " 说的是：" + str(one.get("text") or "")[:40])
    return "\n".join(out) + ("" if road else "\n" + NO_ROAD)


def handlers(base: Any, *, clock: Any, tell: Any = None,
             deliver: Any = None, prepare_delivery: Any = None,
             bot_id: str = "") -> dict[str, Any]:
    """ **那一件 SPEAK 的执行段** —— `send_private`（"主动开口：不等有人先问" ）。

     **两样都由调用方递**（本层自己不读钟、也不认识外面那一条路 ）：
      · `clock` —— 叫它一次交 `(时刻, 哪一天)`（形状照本仓那一条：`YYYYMMDD-HHMMSS` ＋ `YYYY-MM-DD` ）；
      · `tell` —— 可选：**递出去那一头**（真的到它那个人眼前）。 **没有它也照旧说得成话**
        —— 话落进匣子，只是没人被叫醒； 而那一头**崩了也不许带走这一轮** 。

    ⚠️ 这一件**不判断该不该说**："要不要说"发生在它自己那一轮里（不想说就 `finish_turn` ）；
       这里只做"**说了就收下**" —— 配额一条都没有 。
    """
    def send_private(text: str = "", reason: str = "", **rest: Any) -> str:
        when, day = clock()
        one = put_a_line(base, text=text, stamp=when, reason=reason, day=day)
        receipt = None
        failure = None
        if deliver is not None:
            try:
                receipt = deliver(one["text"])
            except Exception as exc:  # optional route failure cannot erase the outbox entry
                failure = type(exc).__name__
        if tell is not None:
            try:
                tell(one["text"], one["stamp"])
            except Exception: # noqa: BLE001 —— 递不出去不许带走这一轮
                pass
        status = (str(receipt) if receipt else
                  f"主动消息投递未确认（{failure}）。" if failure else NO_ROAD)
        return "这句话先落在匣子里了：" + one["text"] + "\n" + status

    if prepare_delivery is not None:
        from .async_tools import OwnedBlockingIO, WorkOwner, current_invocation
        if not isinstance(bot_id, str) or not bot_id or not callable(prepare_delivery):
            raise ValueError('native outbox requires explicit Bot and delivery preparer')
        runner = OwnedBlockingIO()
        frozen_base = pathlib.Path(base).resolve()
        frozen_bot = bot_id
        frozen_scope = 'outbox:' + str(frozen_base)

        async def owned(text: str = '', reason: str = '', **rest: Any) -> str:
            invocation = current_invocation.get()
            if invocation is None or invocation.name != 'send_private':
                raise RuntimeError('native outbox requires its real dispatcher invocation')
            when, day = clock()
            one = a_line(text=text, stamp=when, reason=reason, day=day)
            owner = WorkOwner(frozen_bot, frozen_scope, invocation.call_id, 'send_private')
            receipt, failure, prepared = None, None, None
            try:
                prepared = prepare_delivery(one['text'], bot=frozen_bot)
                import inspect
                if not inspect.iscoroutinefunction(prepared):
                    raise TypeError('delivery prepare must return a native cold callable')
            except Exception as exc:
                failure = type(exc).__name__

            def append(cancelled):
                if cancelled.is_set():
                    raise RuntimeError('outbox append cancelled before admission')
                return put_a_line(frozen_base, **one)

            await runner.run(owner, append)
            if prepared is not None and failure is None:
                try:
                    receipt = await prepared()
                except Exception as exc:
                    failure = type(exc).__name__
            if tell is not None:
                def notify(cancelled):
                    if not cancelled.is_set():
                        tell(one['text'], one['stamp'])
                try:
                    await runner.run(owner, notify)
                except Exception:
                    pass  # Optional notification never retries delivery or append.
            status = (str(receipt) if receipt else
                      f'主动消息投递未确认（{failure}）。' if failure else NO_ROAD)
            return '这句话先落在匣子里了：' + one['text'] + '\n' + status

        send_private.__owned_async_handler__ = owned

    return {"send_private": send_private}

