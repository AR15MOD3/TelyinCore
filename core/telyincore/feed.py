"""把总线上的一批「等」出来 —— 循环与总线之间的那座桥。

⚠️ **它认识 `asyncio`，而 `bus` 不认识** —— 那是刻意的：
   总线保持纯（它的「等」是注入的 `on_ready`），而这一层负责把那个信号
   变成可以 `await` 的东西。

⚠️ **`ready.clear()` 是承重的，不是"清理一下"** —— ready 残留为 set 在真总线上**可达**
   （取走一批之后它仍是 set）；不清就直接 `await`，而**已 set 的 `wait()` 不 yield 就立刻返回**
   ⇒ 循环退化成**不让出事件循环的忙等**，整个进程卡死、连超时都轮不上。

⚠️ **而 happy path 只探一次**：`take_if_ready()` 与 `clear()` 都是**同步**调用，
   两次之间**没有 await 点** ⇒ 别人插不进来 ⇒ "清之后再试一次"那个第二次 try 是**死的**
   （下面那条测试同时钉着这两件事）。
"""
from __future__ import annotations

import asyncio
from typing import Any


async def next_batch(bus: Any, ready: asyncio.Event) -> list:
    """等到总线可以取的时候，取一批给它。

    ⚠️ `bus` 只按**形状**用（`take_if_ready()`）—— 不 import 它，
       与这个仓其余部分一致。

    ⚠️ 循环是「**试 → 清 → 等**」，不是「先清、再试」——
       `clear()` 清的是**残留的那一次就绪**，不是"清与试之间"来的什么东西。
    """
    while True:
        batch = bus.take_if_ready()
        if batch is not None:
            return batch
        ready.clear()          # ⚠️ 清掉残留的那次就绪 —— 否则已 set 的 wait() 不 yield 就返回 ⇒ 忙等
        await ready.wait()
