# telyincore/tools.py
"""工具协议 · **声明面** —— 声明与实现分开（6.1）＋ 六个标签（6.2）＋ `effect`（6.3）。

```
ToolSpec = ToolSchema + { usage?, tags }                       # 插件声明（规格 6.1）
ToolDef  = ToolSchema + { tags, barrier_after?, ends_turn?,     # 装配层绑定（规格 6.1）
                          effect?, handler, usage? }
ToolSchema = { name, description, parameters }                 # `02-框架与插件.md:37`
```

 **那两个类型的分工是承重的**：**插件只声明 schema ＋ 用途 ＋ 标签，handler 由装配层绑**
   —— 规格 6.1 给的理由是"**这样它的工具能被注入假实现**"，而那是它 114 个测试文件
   搬过来的前提。⇒ `ToolSpec` 上**没有** `handler`：连递都递不进去（构造即 `TypeError`）。
   两半都由 `tests/test_tools.py` 钉着（缺一半，另一半坏掉就看不见）。

 **`act` 空着是有意的**（6.2：没有『改外部但不说话』的动作），
   而普通状态提示**由那个空缺触发** —— `build_table` 每次装配都看一眼，表里一个 `act` 都没有
   才输出普通状态提示，不再发出 Python UserWarning。
   ⚠️ 判据是**触发**不是"有告警"：表里有一个 `act` 工具时**不许**再出声。
   一个把它写成常量的实现在那条测试上是红的（自检里量过，报告 4.3）。

⚠️ **13 条的参数 schema：规格里没有** ⇒ **一条都没编**。
   `06` 的 6.7 那张表只给了**名字 ／ 标签 ／ effect**（6.2 ／ 6.3 也没有参数），
   而 `02` 只说 `ToolSchema` 上有个 `parameters` 字段、**一个字没说它装什么**；
   全仓唯一一处真签名是 `08` 8.3 的 `schedule_wake(…)`，而它**不是这 13 个名字里的一个**
   （它与 `set_timer` 的关系是**另一件要裁的事**，见交付报告）。
   ⇒ **这一笔 2026-10-07 当天就还上了** —— 今天这 13 行**都有 `parameters`**
     （ 每一张照**它自己的 handler 签名**写 ，见下面 `A_QUERY` 那一族 ）。
   ⚠️ **这两句从前写的是“一律 `None`”** —— 那是**当时的账** ， 它今天是一句
     **假话** （ 本仓的规矩：一条已经兑现的债留在表里就是一句假话 ）。
   ⚠️ **还有一处最容易读歪的** ： `A_PROPERTIES_TYPE`
     （`{'type': 'object', 'properties': {}}` ）**不是“没声明”** ——
      它是**照 handler 写的** ： `pending_read(**rest)` **本来就不收参数**
     （ 它交的是“整份清单一遍” ）。 别看到“空 properties”就去补一个 `query`
     —— 那会把一个**合法的调用**判成错的 。
   ⚠️ 那张表由测试**双向闭合**钉着（每个声明的工具都要有欠主 ／ 表里不许有查无此人的名字）
   —— 不是靠人记得回来补。

⚠️ **今天谁欠什么**（`loop.py:60-63` 那条家规：不许让一个东西静默无效）：

```
· **13 个 handler 全没绑**（一律 `handler is None`）—— 6.1 要的就是"先声明"，
  而**执行段归 Task 3（`dispatch.py`）**：它把那 13 个点名接缝绑上来。
· `usage` 在装配时保留，由按需 `tool_guide` 向模型提供；不改变工具执行权限。
· **`reserved` 的默认那一份今天是空的** —— 欠主写在 `reserved._OWED_BY` 上。
```
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Callable, Iterable, Mapping, Sequence

from .reserved import is_reserved


class ToolTag(StrEnum):
    """六个标签（规格 6.2）—— **核心只认这六个，语义归插件**（S2 的判据那条）。

    ⚠️ `ACT` **在这张表里**，而它的成员今天**一个都没有** —— 那是正常的
       （没有『改外部但不说话』的动作），见模块开头那一段。
    """

    READ = "read"
    WRITE = "write"
    SPEAK = "speak"
    ACT = "act"
    FLOW = "flow"
    SNAPSHOT = "snapshot"


#: `effect` 的两个等级（规格 6.3）—— **框架只需要知道"有两种写入等级"**，
#: 它**不需要知道"关于模型自己"是什么**（队列由插件提供）。派发第 5 步按它分岔。
EFFECTS: tuple[str, ...] = ("immediate", "pending")

#: 默认那一级：**模型说了算**（规格 6.3）。⚠️ 它也是 `ToolDef.effect` 的默认值 ——
#: 这个默认**只写这一处**（装配那一侧不许再写第二份）。
DEFAULT_EFFECT = "immediate"


class ToolDeclarationError(ValueError):
    """声明 ／ 装配当场不合法：重名 ／ 撞保留名 ／ `effect` 不在那两级 ／ handler 无人认领。

    ⚠️ 它管的全是**装配那一刻**就该炸的事，不是运行期的事：一条声明坏了，
       要坏在"表造出来的那一秒"，而不是"模型真来调它的那一秒"。
    """


@dataclass(frozen=True)
class ToolSpec:
    """**插件声明**（规格 6.1）：schema ＋ 用途 ＋ 标签。**没有 handler，也没有 effect。**

    ⚠️ `parameters` **故意不给默认值**（要显式写出来）：因为"还没声明"与"不收参数"
       是两件事 —— 写 `{}` 等于替模型断言"这个工具不收参数"，而那句今天没人验证过；
       给个 `None` 默认值又会让人**看不见**自己漏了它。⇒ 逼每一行表态。
    `usage` 保留到 ToolDef，按需读取说明时使用。
    """

    name: str
    description: str
    parameters: Mapping[str, Any] | None
    tags: tuple[ToolTag, ...]
    usage: str | None = None


@dataclass(frozen=True)
class ToolDef:
    """**装配层绑定**（规格 6.1）：在 schema ＋ 标签之上，绑实现与三个开关。

    ⚠️ `handler` 默认 `None`，而"没绑"**不是**一种静默失效：13 个执行段今天全在这个状态，
       它们由 Task 3（`dispatch.py`）**点名接缝**绑上来，而这里**不替它们编一个假的**。
    """

    name: str
    description: str
    parameters: Mapping[str, Any] | None
    tags: tuple[ToolTag, ...]
    barrier_after: bool = False
    ends_turn: bool = False
    effect: str = DEFAULT_EFFECT
    handler: Callable[..., Any] | None = None
    usage: str | None = None


# 规格 6.7 那张**动词表逐行抄下来**（行序照它：B 做功课 → C 整理自己 → D 惦记 →
#    E 找你 → 收工），而 **handler 一律不绑** —— 那正是 6.1 说的"声明与实现分开"。
#
# ⚠️ 它们是 `ToolDef` 而不是 `ToolSpec`，这**不是**随手选的：6.7 那一列给了 **effect**，
#    而 6.1 把 `effect` 划在**装配层**（`ToolSpec` 上没有它）⇒ 只有 `ToolDef`
#    装得下"名字 ／ 标签 ／ effect ＋ handler 空着"这一整行。
#    ⚠️ 于是 `build_table` **两种入参都收**（裸声明 ／ 已经带绑定的一行），理由写在它那里。
#: 2026-10-07：**工具的参数模具**（没有它，模型看到的工具没有旋钮）。
#: ⚠️ **这一句记的是“还没有它”那天的状态** —— 它当天就还上了 ：
#: 今天这一族**每一行都有 `parameters`** （ 下面 `A_QUERY` 那一族就是 ）。
#: ⚠️ **而 `A_PROPERTIES_TYPE`（空 `properties`）不是漏写** ——
#: 它是**照 handler 签名写的** ： `memory.pending_read(**rest)` **不收参数**
#: ⇒ 模型发 `{}` 调它**是合法的** （ 别去给它补 `query` ）。
#: 而每一张照**它自己的 handler 签名**写 （`kb_search(query)` ／ `profile_add(text, who)` ）。
A_QUERY = {'type': 'object', 'properties': {'query': {'type': 'string',
           'description': '要查的那个词或那句话'}}, 'required': ['query']}
A_WHO = {'type': 'object', 'properties': {'who': {'type': 'string',
         'description': '那一个人的名字'}}, 'required': ['who']}
A_TEXT = {'type': 'object', 'properties': {'text': {'type': 'string',
          'description': '要说下的那一句'}}, 'required': ['text']}
A_NONE = {'type': 'object', 'properties': {}}


# 2026-10-07: 工具的参数模具（没它就没旋钮）——
# **当天就填上了** ； 每一张照**它自己的 handler 签名**写 。
# ⚠️ 这一块与上面那一块说的是同一件事 （ 两块都改准了 ）。


A_TEXT_REASON = {
    'type': 'object',
    'properties': {
        'text': {'type': 'string',
                 'description': '要带给那个人的那一句'},
        'reason': {'type': 'string',
                 'description': '为什么现在想说'},
    },
    'required': ['text'],
}

A_WHO_TEXT = {
    'type': 'object',
    'properties': {
        'text': {'type': 'string',
                 'description': '记下的那一句'},
        'who': {'type': 'string',
                 'description': '关于谁'},
        # 2026-10-10（作者拍的「乙1」）：这一支笔也能写"我什么态度 ＋ 给他多少分"。
        # 挂在这一扇已有的门上，不另立一件工具（作者："别非要写一本本子去强调"）。
        # ⚠ 两格都可以不给；给哪一格就写哪一格。
        'standing': {'type': 'string',
                     'description': '你对这个人的态度（一句话；不给就不写这一格）'},
        'favor': {'type': 'integer', 'minimum': -100, 'maximum': 100,
                  'description': '好感度 -100～100，初始0；不填保留原值，不控制行为'},
        # **2026-10-12（作者：「\u5979自己标」）**：**轻重那一格由它自己给** ——
        # 它进"取记忆"那个打分（`memory._a_weight_in_the_ranking`）：
        # 3 ＝ 一般（＝乘 1.0，与"没标"同一个下场）、1 很轻、5 很重。
        # ⚠️ 这一层**不猜**：不给 ⇒ 就是"没标"，按一般算。
        'weight': {'type': 'integer',
                   'description': '这条有多重：1 很轻 ／ 3 一般 ／ 5 很重'
                                  '（不填就按一般算；你说重，它以后再被取到时往前排）'},
    },
    'required': ['text'],
}

#: **2026-10-10：`skill_note` 那一支笔的两个旋钮**
#: ⚠ **为什么另起一份、不复用 `A_WHO_TEXT`** ： 那两格在
#: 这个语境里**意思完全不同** —— 这一支的 `name` 是
#: **什么时候用它** （ 比如「进游戏之后先开背包」），
#: 而 `A_WHO_TEXT` 的 `who` 是 **关于哪一个人** ⇒ 说明书
#: 是**给它看的** ⇒ 用错一个字，它就会把名字当标题写下去 。
#: ⚠ **两格都必填** ； `overwrite` 可选 （ 缺省**拒写**同名的 ）。
A_A_SKILL = {
    'type': 'object',
    'properties': {
        'name': {'type': 'string',
                 'description': '什么时候用它（比如「进游戏之后先开背包」）'},
        'text': {'type': 'string',
                 'description': '怎么做：步骤 ／ 顺序 ／ 什么情况下别用'},
        'kind': {'type': 'string', 'enum': ['communication', 'operation'],
                 'description': 'communication 为可选交流做法；operation 为实现目标的操作技能'},
        'overwrite': {'type': 'boolean',
                      'description': '要覆盖同名的才填 true；不填就拒写'},
    },
    'required': ['name', 'text'],
}

#: 2026-10-08（作者拍的「接成查」）：`read_events` 的那两个旋钮 。
#: ⚠️ 说明里原来那句「攒着还没看的」**没有底** （全树没有"读到哪"那个概念 ）
#: ⇒ 砍掉它，换成**它真做得到的两件事**： 给时刻 ⇒ 只看那之后 ；
#: 给一个词 ⇒ 按词翻 。
A_SINCE_GREP = {
    'type': 'object',
    'properties': {
        'store': {'type': 'string', 'enum': ['events', 'records'], 'description': 'events（默认）按 cursor 查事件；records 可跨交接查保留的原始对话、工具请求和回执，按列表返回的 record_ref 读单条。旧版本未保留的原文不会凭摘要补造。'},
        'category': {'type': 'string', 'enum': ['dialogue', 'user', 'assistant', 'tools', 'system', 'all'], 'description': 'store=records 的列表筛选，默认 dialogue 只查用户和 Bot 的对话原文；user 只查用户发言，assistant 只查 Bot 发言。核对用户原话可选 user，Bot 转述不能替代用户原话；tools 查工具调用/回执；system 查系统记录；all 查全部。完整原文按 record_ref 读取，不受类别筛选影响。翻页保持类别不变。'},
        'scan_budget': {'type':'integer','minimum':1,'maximum':256,'description':'事件关键词慢查询可按游标从新到旧分段，每次最多核对这么多条可见候选正文。保持筛选条件，按回执继续。'},
        'scan_before': {'type':'integer','minimum':1,'description':'配合 scan_budget 续查上一页游标之前的事件。'},
        'record_ref': {'type': 'string', 'description': '完整复制查询返回的 record_ref（哈希:序号），保留末尾冒号及序号；也接受 record: 前缀或 [record:…] 显示引用。配合 offset 续读'},
        'before': {'type': 'string', 'description': 'store=records 时用上一页返回的 before 翻页，保持筛选条件；limit 为 1–50，默认20'},
        'cursor': {'type': 'integer', 'minimum': 1,
                   'description': '仅用于 events。看到 event:123，可调用 read_events(cursor=123)。'},
        'offset': {'type': 'integer', 'minimum': 0,
                   'description': '精确读正文时从此字符偏移继续，默认 0'},
        'since': {'type': 'string',
                  'description': '只看这之后的（带时区的 ISO 8601，例如 2026-10-08T07:00:00+08:00）；'
                                 '不给就按 limit 那一格看（ 不是整本库）'},
        'until': {'type': 'string', 'description': '事件结束时间（带时区的 ISO 时间）'},
        'sender_key': {'type': 'string', 'description': '按事件中的实际发言账号精确筛选'},
        'grep': {'type': 'string',
                 'description': '按一个词翻；给了它就按词翻，不给就按时间看'},
        # **2026-10-10 加的这一格** （ 作者当天说「5 也做一下」）。
        # ⚠️ **为什么非加不可** ： 那张表原来**没有 `required`**
        # ⇒ 模型**以为可以不填** —— 而那一天它就是这么空手调的
        # （ `read_events '{}'` ⇒ 那时缺省是"**把整本库倒出来**"：
        # 实测 **209 条 ／ 约 14.5 万字** ⇒ 那一轮**装不下也写不下**
        # ⇒ 结果没进会话 ⇒ 输入不变 ⇒ 引擎缓存命中 ⇒ **无限** ）。
        # 2026-10-10 已经给那一头**一个底**了 （`plugins.READ_EVENTS_LIMIT = 50`
        # —— 缺省最多 50 条 ， 而**显式给 `limit` 就照它给** ）。
        # 而这一格是**另一头** ： 让**模型自己知道**那个缺省是什么 ——
        # 于是它不必靠猜 ， 也不必空手调一次才知道 。
        'limit': {'type': 'integer',
                  'description': '最多回几条；不给就 50（ 缺省**不是**整本库）'},
    },
}

A_WHEN_WHAT = {
    'type': 'object',
    'properties': {
        'when': {'type': 'string',
                 'description': '什么时候 —— 必须是 ISO 8601（例如 2026-10-08T07:00:00）；'
                               '请把「明天早上七点」先算成那个样子'},
        'what': {'type': 'string',
                 'description': '到那时提醒什么'},
    },
    'required': ['when', 'what'],
}

A_ID = {
    'type': 'object',
    'properties': {
        'id': {'type': 'string',
                 'description': '那一件的编号'},
    },
    'required': ['id'],
}

A_QUERY_K = {
    'type': 'object',
    'properties': {
        # **2026-10-10**：这一格补上说明（照 `A_QUERY` 那一句 ）——
        # 从前它空着，而 `kb_search` 现在**也走这一份** ⇒ 两扇门都得说清"要查什么" 。
        'query': {'type': 'string',
                 'description': '要查的那个词或那句话'},
        'k': {'type': 'integer',
                 # ★ **2026-10-17：这一格原来只写"要几条"** —— 而"条"这个字在回执里
                 #    **同时**表示"几份文件"与"几个词条" ⇒ 实测模型把它读成**词条数**，
                 #    于是反复调它（`k=5` → `k=10` → `k=5`，一趟 13 次）想把词条调多；
                 #    而命中只有 1 份时调它**毫无变化** ⇒ 模型就一直调、一句不答。
                 #    ⇒ 写明它数的是**份**，并指出"要更多词条"该用哪一格。
                 'description': '要看几**份**（一份 ＝ 一个文件）—— 要看某一份里更多的'
                                '**词条**，用 `lines`，不是这一格'},
        # ★ **2026-10-17：同一扇门里就能"看全"** —— 见 `kb_search` 那个 `lines` 参数。
        # ⚠️ **为什么加在这一扇门上、而不是只让 `kb_read` 管**（当天量到的）：
        #    探针跑了 5 种问法 ／ 7 次观察（"念前后的几条"／"挑几个念"／"点名那一本＋
        #    要范围"／"核一个词"），`kb_read` **一次都没被调过** —— 而同一批里
        #    模型把 `kb_search` 重问了 4 到 6 遍。⇒ "要更多"这件事在模型那儿**不映射成
        #    换一件工具**（哪怕回执里把工具名和参数都写给模型了）。
        #    ⇒ 于是把这条路**开在同一扇门里**：不用换工具，多填一格就行。
        'lines': {'type': 'integer',
                  'description': '那一条要看几行 —— 那一本整份多少行，回执里写着；'
                                 '想看全就给个大数（如 200）；不给就只给命中的那一节'},
        # ★ **2026-10-17：起点也得能填**（量出来才补的）——只给 `lines` 时模型从第 0 行起，
        #   而模型要的是"某一条**前后**"那几条，于是同一批连问了 12 次。
        'from_line': {'type': 'integer',
                      'description': '从第几行起（从 0 算）—— 回执里"接着看"那一句会把它填好；'
                                     '单独给它就按一块（40 行）给'},
    },
    'required': ['query'],
}

A_QUERY = {
    'type': 'object',
    'properties': {
        'query': {'type': 'string',
                 'description': '要查的那个词或那句话'},
    },
    'required': ['query'],
}

#: **`kb_read` 那一格**（2026-10-10，作者拍的「乙」）—— 与 `A_QUERY_K` **配套的一对**：
#: `kb_search` 只给「命中那一条」（目录 ），要看全 ／ 看后面 ⇒ 走这一格 。
#: 学外面那两套的成法（Claude Code 的 `read` 带行范围 ／ Dust 的 `cat` 带
#: `offset` ／ `limit` ）：**大文件不许整份进 context** 。
A_KB_READ = {
    'type': 'object',
    'properties': {
        'name': {'type': 'string',
                 'description': '哪一条 —— `kb_search` 交回来的那串名字'},
        'version': {'type': 'integer', 'minimum': 1,
                    'description': '习得知识版本；不填读取当前版'},
        'offset': {'type': 'integer',
                   'description': '从第几行起（ 从 0 算；要看全就给 0）'},
        'limit': {'type': 'integer',
                  'description': '最多看几行'},
    },
    'required': ['name'],
}

#: **2026-10-12（作者拍的：那支笔要有读口）**：读「做法」那一棵的那一格。
#: ⚠️ **`name` 可以不填** —— 不填就是"**我记过哪几条**"：那一棵今天很小，而
#:   "我到底记过什么"是这一扇门最先要回答的那一问（填了就按名字开一条、看一截）。
A_SKILL_READ = {
    'type': 'object',
    'properties': {
        'name': {'type': 'string',
                 'description': '哪一条做法 —— `skill_note` 交回来的那个名字；不填就列名单'},
        'offset': {'type': 'integer',
                   'description': '从第几行起（从 0 算；要看全就给 0）'},
        'limit': {'type': 'integer',
                  'description': '最多看几行'},
    },
}

#: **2026-10-12（作者：「\u5979自己决断」）**：把提过的那一条**落定**那两格。
#: ⚠️ `what` 要的是**它提过的那一句原样**（`pending_read` 交回来的样子）——
#:   两边都先折过空白再逐字比（`memory.resolve_pending` 那一句就是这么配的）。
A_SETTLE = {
    'type': 'object',
    'properties': {
        'what': {'type': 'string',
                 'description': '哪一条 —— 你提过的那一句原样（`pending_read` 里看得见）'},
        'action': {'type': 'string',
                   'description': '确认 ／ 降级 ／ 删除 三样之一；不填按「确认」'},
        'when': {'type': 'string',
                 'description': '要不要在那一行后面留个日子（例如 2026-10-12）；不填就不留'},
    },
    'required': ['what'],
}

A_PROPOSE_CHANGE = {
    'type': 'object',
    'properties': {
        #  ★ **2026-10-13（作者：「做」）**：第 22 件 —— 提议改「你定的」那一份。
        #    ⚠️ **它补的是哪一处空**：`abouther_propose` 提的是**它自己那一本**，
        #       而"改它那个人定的那几份"从前**没有落点** —— 它只能干看着
        #       （`consolidate._OWED["propose_to_the_ones_you_set"]` 那一笔）。
        'name': {'type': 'string',
                 'description': '以当前档案标识为准。新版只填宪法.md或角色设定.md；'
                                '旧版填实际存在的相对路径，不猜测或沿用其他布局的文件名。'},
        'text': {'type': 'string',
                 'description': '提议的新正文。新版给完整目标章节，必须原样保留开头的##章节标题及章节内原有标题，不含section注释标记；旧版给整份文件。'},
        'section': {'type': 'string',
                    'description': '新版必填正文标出的章节标识；角色设定可用identity、expression、world、relations，'
                                   '宪法可用evidence、agency、revisions、memory。旧版按文件提议时不填。'},
        'why': {'type': 'string',
                'description': '为什么想改。新版章节提议必须填写，可带具体例子。'},
    },
    'required': ['name', 'text'],
}

A_REASON = {
    'type': 'object',
    'properties': {
        'reason': {'type': 'string',
                 'description': '这一轮为什么可以收了'},
    },
    'required': ['reason'],
}

A_TEXT = {
    'type': 'object',
    'properties': {
        'text': {'type': 'string',
                 'description': '要说下的那一句'},
    },
    'required': ['text'],
}

A_WHO = {
    'type': 'object',
    'properties': {
        'who': {'type': 'string',
                 'description': '那一个人的名字'},
    },
    'required': ['who'],
}

A_PROPERTIES_TYPE = {
    'type': 'object',
    'properties': {
    },
}

DECLARED_TOOLS: tuple[ToolDef, ...] = (
    # ── B 做功课（read ×8）─────────────────────────────────
    # **2026-10-10：`A_QUERY` ⇒ `A_QUERY_K`** —— 知识那一棵是"**一份文件 = 一条**"，
    #    而"一份一条"之后**一次常见的查会命中上百条**（本机实测：500 条成语里查「形容」
    # ⇒ **165 条 ／ 23866 字** ）⇒ 它和 `memory_search` 一样得有一个上限
    # （那一边一直有 `k=5` —— 这只是一把**同一族的另一扇门**补齐 ）。
    # **2026-10-10（作者拍的「乙」）：这一格一次只给「命中那一条」，不给整份** ——
    # 与下面新加的 `kb_read` 是**配套的一对** （学 Claude Code 的 `grep` 定位 ＋
    # `read` 带行范围 ／ Dust 的 `cat` 带 `offset` ／ `limit` ）。
    # **这一句描述就是"永远在它眼前"的那一层**（L1 元数据 ）——
    # ⚠️ 它原来只写着"设定、术语、已经定下来的事"⇒ **不说话这里到底住着什么**
    # （ 本机实测：问"书房里有什么"，它去查了 `memory_search` 而不是这一条 ）
    # ⇒ 照外面那套（`Anthropic Skills` 的三级披露：L1 就是"**决定要不要加载**"那一层 ）
    # 把**这里有什么**写进这一句 —— 而这句是**我们的字**，不是它的 。
    ToolDef(name="kb_search",
            description="查知识库：① 那个地方与它自己那几件的**事实**"
                        "（住在哪儿 ／ 书房里有什么 ／ 馆口那座桥 ／ 它长什么样 ／ 常穿什么）；"
                        "② 字词、成语、歇后语、专门说法上的**意思**；"
                        # ★ **2026-10-17 补的第③支** —— 见下面那一段注释：量到 4 趟里 0 次查。
                        "③ **你自己收着些什么**（那棵树里都有哪些 —— "
                        "被问到「你有没有…」「那一本里收了哪些」就来查，别凭印象答）。"
                        " k 控制返回结果的文件份数（缺省5份，不是词条数）。"
                        "默认给命中处摘录；命中文件名且有小节时，给目录和前几节正文。"
                        "用 lines／from_line 看指定行窗，或用 kb_read 按文件名续读。",
            parameters=A_QUERY_K, tags=(ToolTag.READ,)),
    # ★★ **2026-10-17 为什么补第③支**（量出来的，不是设想）：
    #   ⚠️ **基线**：拿「你那本成语词典收了哪些词？挑几个念给我听听」连问 **4 趟**，
    #      **0 趟查过库**（`kb_search` ／ `kb_read` 一次都没有）——
    #      模型答"我没有一本专门的词典"，再凭印象念三条成语
    #      （`画蛇添足` ／ `守株待兔` ／ `画龙点睛` —— 而模型库里那本 500 条**一条都没有**）。
    #   ⚠️ **病根在这一句的字里**：原来两支说的是"那个地方的**事实**"与"某个词的**意思**"
    #      ⇒ 而"**我自己有什么**"**既不在这两支里、也不在别处**：
    #      · 系统前缀（`段序.md`）里**一个字都没提**知识那一棵树；
    #      · 模型的 persona 里只有一句**写给作者看**的话（"举例 ／ 陈设 ／ 谁会来 ⇒ 进知识库"）。
    #      ⇒ 问"你有什么"时，模型手上没有任何东西提示"这件事该去查" ⇒ 模型照自述答。
    #   ⚠️ **能力其实早就有**：`kb_search("词典")` 今天就能交出四本的名字与"里面 500 条"，
    #      目录那一刀（同一个下午）还让每一本列出头 12 条真词条名。
    #      ⇒ **缺的不是能力，是"要不要去查"那一步** —— 而那一层正是这一句描述
    #      （抬头那句"L1 元数据 ＝ 决定要不要加载"说的就是它）。
    #   ⚠️ 它**不改机制、不加参数、不动前缀**：只是把第③支**写进模型已经看得到的那一句**。
    # **2026-10-10（作者拍的「乙」）：第 17 件 `kb_read`** ——
    # `kb_search` 的交回从此**只是目录**（名字 ＋ 命中那几行 ＋ 整份多少行 ）
    # ⇒ 要看全 ／ 看后面就得有**这一条** ，否则第一条只能靠"一次多给点" 。
    # 它**不许越出**知识那一棵树（`../` ／ 绝对路径挡在 handler 里 ）。
    ToolDef(name="kb_read",
            description="看知识库里某一条的一截 —— 名字就是 kb_search 交回来的那串；"
                        "一次看不完就带着 offset 接着看。",
            parameters=A_KB_READ, tags=(ToolTag.READ,)),
    # **第 20 件 ／ 2026-10-12（作者拍的：那支笔要有读口）** —— `skill_read`。
    # ⚠️ **为什么非有不可**（本机量到的）：`skill_note` 写进去的那一棵**原来只有索引
    #    一条回读路**（`see_skills()` 喂给匹配器 ⇒ "碰到了才跳到眼前"），而**没有一条
    #    "我要看那一条"的路** ⇒ 它自己记下的"以后遇到这个该怎么做"**平时问不回来**
    #    —— 而"写下去只算学会了一半，另一半在它想起来去查的时候"正是那一棵立的理由。
    # ⚠️ 与 `kb_read` 是**配套的一对**（grep 定位 ＋ read 带行范围），只是这一棵是
    #    **它自己学的**，而 `知识/` 那一棵是**人定的**（见 `memory.py` 那一处）。
    ToolDef(name="skill_read",
            description="看你记下的一条做法 —— 名字就是 skill_note 交回来的那串；"
                        "不给名字就列你记过哪几条。一次看不完就带着 offset 接着看。",
            parameters=A_SKILL_READ, tags=(ToolTag.READ,)),
    ToolDef(name="memory_search", description="查当前授权范围的记忆。搜索返回命中片段，不是完整原文；用本工具的 memory_ref 和 offset 分段读取全文（此时 query 填空串）。笔记不是执行证据，核对实际执行请查原始记录与工具回执。私聊不会自动包含群聊，查不到不等于没发生。",
            parameters={'type': 'object', 'properties': {
                'query': {'type': 'string'}, 'k': {'type': 'integer', 'description': '最多返回10条，默认5条'},
                'memory_ref': {'type': 'string', 'description': '搜索返回的记忆引用；读取时重新检查权限及内容版本'},
                'offset': {'type': 'integer', 'minimum': 0, 'description': '全文字符偏移，初次0；按回执续读'}},
                'required': ['query'], 'additionalProperties': False}, tags=(ToolTag.READ,)),
    ToolDef(name="profile_read", description="读画像历史，按新到旧返回。读当前私聊对象的画像无需参数。核对该画像的原话时只填 evidence_ref。旧条目不自动代表当前状态。续页沿用原查询并按回执传偏移和快照。",
            parameters={'type': 'object', 'properties': {
                'who': {'type': 'string', 'description': '当前私聊省略此项。群聊指定本批可信发言者时用其账号键；本地控制台可指定姓名。'},
                'evidence_ref': {'type': 'string', 'description': '画像条目给出的 portrait- 来源引用只填在这里。返回可读原话、记录时间和复核上下文。'},
                'offset': {'type': 'integer', 'minimum': 0, 'description': '首次为0，续读按回执偏移'},
                'snapshot': {'type': 'string', 'description': '续读必须携带回执中的快照；内容或可见范围变化后重新读取'}},
                'additionalProperties': False}, tags=(ToolTag.READ,)),
    # 2026-10-10（作者拍的「甲2」）：这一句里那句"你自己给的分也在里面"是要紧的 ——
    # 那一格从前读不到（它写得出、却看不见）。这里点一句，它才知道该来这儿看。
    ToolDef(name="relations_read",
            description="读关系：和谁是什么关系，以及你自己给过的那个分。",
            parameters=A_WHO, tags=(ToolTag.READ,)),
    # **第 14 件**（2026-10-07「①b」）：让它读得到自己那些悬着的事 —— 只读 。
    ToolDef(name="pending_read", description="读悬着的事：心里还没落地的那几件。",
            parameters=A_PROPERTIES_TYPE, tags=(ToolTag.READ,)),
    # 2026-10-08：**说明改准**（作者拍的「接成查」）。
    # ⚠️ 原来那句是「读事件：**攒着还没看的外部事件**」 —— 而 全树**没有**
    # "读到哪"那个概念 ⇒ 那半句**没有底** （ 留着它就是一句假话 ）。
    # 而它**真能做的**是查： `EventStore.range(RangeQuery)` ／ `grep(GrepQuery)`
    # （ 而库里今天**真有东西**：87 条，含它自己的思考 ）。
    ToolDef(name="read_events",
            description="读当前授权范围的事件；私聊中不含其他人的私聊或群聊。可用的会话历史工具能补查当前会话。查不到不等于没发生。"
                        "事件引用 event:N 用 read_events(cursor=N)；会话记录用 record_ref 精确读取。offset 可续读；since/until 必须带时区；给 since 就只看那之后的；grep 给一个词就按词翻。"
                        " 两个都不给时 events 默认最近 50 条、records 默认最近 20 条。回查旧对话或工具原始回执用 store=records，支持时间、翻页及原文续读，不按新近程度降低旧记录的相关性。"
                        " 核对约定先查 user；只有需要核对旧回复或执行结果时才扩展 assistant/tools，all 还会命中本轮检索回执中的重复引用。"
                        " 已标注正文完整的条目可直接作为原文证据，无需再读；若有下一页则使用 before，不要仅更换 limit 重查第一页。"
                        " record_ref 必须逐字复制，不要拼写、补造或用省略号缩短；参数失败只修正该参数。"
                        " 证据足以回答即可结束检索；无新线索时不重复同条件查询，不为证明不存在穷举同义词。"
                        " 回答保留各工具的覆盖范围、未完成或降级限制，查不到不代表不存在。",
            parameters=A_SINCE_GREP, tags=(ToolTag.READ,)),
    # ── C 整理自己（write ×5；effect 见 6.3）────────────────
    ToolDef(name="self_note", description="给自己记一条：当下的决定或结论。",
            parameters=A_TEXT, tags=(ToolTag.WRITE,), effect="immediate"),
    ToolDef(name="self_observe_add", description="记录关于自身的待思考观察，进入「悬着的事」，由你后续判断；不直接写观察与更正文档，不代存他人的人物事实。",
            parameters=A_TEXT, tags=(ToolTag.WRITE,), effect="pending"),
    ToolDef(name="abouther_propose", description="提一条《关于自己》的补充 —— 只是提议，"
            "进「悬着的事」。", parameters=A_TEXT, tags=(ToolTag.WRITE,), effect="pending"),
    # **第 21 件 ／ 2026-10-12（作者：「\u5979自己决断」）** —— `abouther_settle`。
    # ⚠️ **它补的是哪一半**：`abouther_propose` 只把话**记下来**（落「悬着的事」），
    #    而"**那一条算不算数**"从前等的是**插件那一侧的待确认队列**（规格 6.3）——
    #    今天没有谁提供它 ⇒ 它提的事**永远没人批**。作者 2026-10-12 拍了：
    #    「**\u5979自己决断**」⇒ 于是"批"这一半归**它自己**：这一件就是它的手。
    # ⚠️ **三个动作直接借 `hers.act`**（那一份纯逻辑早就在：确认 ／ 降级 ／ 删除，
    #    上界也都在 —— 核心 ≤40 条、单条 ≤120 字）⇒ 这一件不另立一套规矩。
    # ⚠️ 落定之后，那条提议**从「悬着的事」里收起来**（`memory.resolve_pending`）——
    #    收起来的动作归记忆层，拍板那一下归它（两半分得开）。
    ToolDef(name="abouther_settle",
            description="把你提过的那一条《关于自己》落定 —— 确认（写进那一本）／"
                        "降级（挪进长尾）／删除（不要了）。落定之后，那一条就从"
                        "「悬着的事」里收起来。",
            parameters=A_SETTLE, tags=(ToolTag.WRITE,), effect="immediate"),
    # **第 22 件 ／ 2026-10-13（作者：「做」）** —— `propose_change`。
    # ⚠️ **它补的是哪一处空**：前两件（`self_observe_add` ／ `abouther_propose`）提的都是
    #    **它自己那一边**的事（自我观察 ／ 《关于自己》）；而"**改它那个人定的那几份**"
    #    （`你定的/…` 那一类：改不改由人定）从前**提都没有地方提**
    #    （`consolidate._OWED["propose_to_the_ones_you_set"]` 那一笔）。
    # ⚠️ **它只提、不写**（`effect="pending"`，与那两件同一族）：落进「悬着的事」里
    #    那一格（`proposed="persona"`），而**放行那一下归人** —— `POST /persona/adopt`。
    # ⚠️ **它自己的那几份不许走这条路**（`你写的/…` 那几份它**写得了**，直接写；
    #    走提议只会让"它自己能做的事"多绕一圈）。
    ToolDef(name="propose_change",
            description="提出待讨论的设定修订；需要时在对话中交流，由你用decide_change决定。新版指定宪法.md或角色设定.md、section和why，"
                        "text只给目标章节的新正文；旧版按实际文件整份提议。"
                        "只有回执明确记录成功才表示已提交，失败不产生待审核提议。自述等自我记录使用对应写入工具。",
            parameters=A_PROPOSE_CHANGE, tags=(ToolTag.WRITE,), effect="pending"),
    ToolDef(name="decide_change",
            description="结合交流或自主反思，自行决定某条设定提议是否生效。apply修改该章节，keep保留原设定并结束提议，"
                        "defer暂缓。理由和所需工具由你判断，不规定检索步骤；evidence选填，填写时引用本轮对方原话。系统自动保留历史和决定依据。",
            parameters={'type': 'object', 'properties': {
                'proposal_id': {'type': 'string', 'description': 'propose_change或pending_read给出的ID'},
                'decision': {'type': 'string', 'enum': ['apply', 'keep', 'defer']},
                'reason': {'type': 'string', 'description': '考虑了哪些依据，为什么修改或不改'},
                'evidence': {'type': 'string', 'description': '本轮对方讨论回应的原话片段，不引用工具回执'}},
                'required': ['proposal_id', 'decision', 'reason']},
            tags=(ToolTag.WRITE,), effect="immediate"),
    # **第 18 件 ／ 第 19 件（2026-10-12 作者拍的「乙」的下半）** ——
    #    它自己那份**自述**的那一支笔（`\u5979\u5199\u7684/\u81ea\u8ff0.md`）。
    # ⚠️ **为什么非有不可**（作者那一句的由来）：问它"这是哪里"，它该答得出
    #    "这里是一座图书馆"，而不是**上来就把自己的隐私说出口**；
    #    而量到的根因是：它那一头里"图书馆"出现 **0 次**，而那一份是 **0 字节**
    #    ⇒ 一问"你是谁"，它手上只有名字 ＋ 住处。 而那一份**只有它自己说了算**
    #    （`10:63-71`：它自己那些事归它；`abouther_propose` 那一件只是**提**）。
    # ⚠️ **为什么是两件、不是一件**（这是这一刀的形状，有出处，不是拍的）：
    #    · **Stanford《Generative Agents》§4.2**：自我认知是从证据里**合成、会累积**的
    #      高层反思，不是一份被反复重写的稿子；而它**周期性**触发；
    #    · **Letta（core memory blocks）**：`append` 与 `replace` **两个操作都提供**，
    #      由 agent 自己挑；
    #    · **《Retain or Consolidate?》arXiv 2607.17545**：retention 与 consolidation
    #      **没有哪个普遍更好**；真要压缩时，「**跨条抽象／合并**」胜过「就地重写」。
    #    ⇒ 落到这一页：**平时添一句**（`self_account_add`）／
    #      **要整理时整份换掉**（`self_account_set`，且是"把散的捏成一条"那种合并）。
    # ⚠️ **两件都写盘、都要等下一次请求才生效**（与那一页那句诚实同一条）；
    #    而换下来的旧版**留着**（走 `web.PersonaPort.save_hers_text` 那扇门 ⇒ 它自带留版）。
    ToolDef(name="self_account_add", description="往你自己那份自述里添一句 —— 直接写进去，"
            "不用等人确认；旧的不会动。",
            parameters=A_TEXT, tags=(ToolTag.WRITE,), effect="immediate"),
    ToolDef(name="self_account_set", description="把你那份自述整份重写一遍 —— 回头看你"
            "写的那一份，把散的捏成一条；换下来的旧版留着。",
            parameters=A_TEXT, tags=(ToolTag.WRITE,), effect="immediate"),
    # **第 15 件**（2026-10-07「第 2 刀」）： **确信**的才记这儿（进**画像**）；
    # 人物推断可按原话与解释分开记入 experience_note；self_observe_add 仅记录自身观察。
    # 2026-10-10（作者拍的「乙1」）：这一支笔也能写"我什么态度 ＋ 给他多少分" ——
    # 挂在这一扇已有的门上，不另立一件工具（作者："别非要写一本本子去强调"）。
    ToolDef(name="profile_add", description="记录有依据的人物信息，包括长期信息、近期事实及计划；保留时间、尚未完成和非长期等限定，近期情况不能写成长期习惯、计划不能写成已经完成。系统审核后分类保存，只有成功回执才表示已记住；"
            "每次只写一个有直接原话依据的事实或紧密相关事项；不同经历应在首次调用前分条，不把工作、赴约、情绪等独立事项混写，也不把近期工作推断成本职或长期职业。系统自动记录写入时间，不必自行在正文追加说明日期；原话明确的事实时间和相对时间限定须保留，不把消息时间当成事实发生时间。"
            "作者明确说某事尚未决定，可以忠实记录这一近期状态，不得补出决定；归属或内容拿不准时先不写，不用自我记录替代画像审核。"
            "也可以顺手写下你对他的态度（standing）和你给他的分（favor）——"
            "那一句 text 同时就是「为什么」；给了就写进去，以后用 relations_read 看得回来。"
            "顺手也可以给这条标个轻重（weight：1 很轻 ／ 3 一般 ／ 5 很重）。",
            parameters=A_WHO_TEXT, tags=(ToolTag.WRITE,), effect="immediate"),
    # **第 16 件：记一条做法**（ 2026-10-10 —— 作者要补的那一支 ）。
    # ⚠ **为什么它与上面那几件不是一回事** ： 那几件记的是
    # **关于谁** ／ **经历过什么** ； 而这一件记的是
    # **以后遇到这个该怎么做** —— 文献里那个词：
    # **程序性记忆** （ 让 agent 把 how-to 内化，
    # 从而**减少重复试错** ）。
    # ⚠ **落在另一棵树** （`做法/` —— **不是** `知识/` ）。
    ToolDef(name="skill_note", description="记下可选的沟通做法或操作技能。填写 kind，"
            "写明适用条件、怎么做、何时需要调整。可用 [[知识ID]] 关联知识，[[sk-0001]] 关联已有做法，[[event:123]] 引用读到的事件。某人明确反馈的偏好另用 communication_preference；"
            "角色设定和宪法另走 propose_change。已有待分类做法可用 overwrite 和 kind 归类并保留旧版。",
            parameters=A_A_SKILL, tags=(ToolTag.WRITE,), effect="immediate"),
    # ── D 惦记（flow ×3）───────────────────────────────────
    ToolDef(name="unresolved_add", description="记一条没放下的事。",
            parameters=A_TEXT, tags=(ToolTag.FLOW,), effect="immediate"),
    ToolDef(name="unresolved_resolve", description="把一条没放下的事划掉。",
            parameters=A_ID, tags=(ToolTag.FLOW,), effect="immediate"),
    ToolDef(name="set_timer", description="定个闹钟：到点把自己叫醒。",
            parameters=A_WHEN_WHAT, tags=(ToolTag.FLOW,), effect="immediate"),
    # ── E 找你（speak ×1）─────────────────────────────────
    ToolDef(name="send_private", description=(
                "想找主人时可以主动开口，也可以安静收工。说什么由你决定。"
                "可以接着上次的话，或聊自己写过、记得、最近在意的事。"
                "涉及别的私聊或群里可辨认的人、原话和具体事件时，使用带来源审核的分享工具。"),
            parameters=A_TEXT_REASON, tags=(ToolTag.SPEAK,), effect="immediate"),
    # ── 收工（flow ×1）—— 6.7 的 `ends_turn` ＋ `08` 8.6 坑 2 的 `barrier_after`：
    #    有屏障 ⇒ 排在它后面的调用（例如 `set_timer`）记为未执行，
    #    所以"定个闹钟然后收工"**必须分两步**。
    ToolDef(name="finish_turn", description=(
                "结束这一轮，不发送任何回答。reason 只是内部收工原因，不是回复正文；"
                "写‘应当回答’或‘直接回复即可’不会执行回答。"
                "控制台需要回答时先输出实际 assistant 正文，只有正文输出也可以自然结束，不必调用本工具；"
                "外部平台需要回复时先完成该平台的真实发送，再收工。"
                "确实无需回应的内部唤醒、无新内容通知等仍可直接用本工具沉默结束。"),
            parameters=A_REASON, tags=(ToolTag.FLOW,), effect="immediate",
            ends_turn=True, barrier_after=True),
)

#: **13 条的参数 schema 谁欠它** —— 规格里没有那份 schema，而"没有"要在**这一行**上说话
#: （`loop.py:60-63`）。⇒ `parameters=None` ＋ 这里点名，而不是编一份 JSON schema 顶上去。
#: ⚠️ 它由 `tests/test_tools.py::test_every_declared_tool_names_who_owes_its_parameter_schema`
#: 钉着：**双向闭合** —— 加一个工具而不填这里 = 当场红；这里留一个查无此人的名字 = 也红。
_SCHEMA_OWNERS: dict[str, str] = {
    "kb_search": "知识库那一侧（**今天没有**）",
    # **2026-10-10**：第 17 件 `kb_read` 那一格 —— 与 `kb_search` **配套的一对**
    # （它交回的是"目录"，这一格才看得到正文 ）。 声明已经写上去了（`A_KB_READ` ）。
    "kb_read": "知识库那一侧（**今天没有**）—— 声明已经落成 `A_KB_READ` ",
    "memory_search": "记忆系统（**今天没有**）",
    "profile_read": "记忆系统 · 画像那一块（**今天没有**）",
    "relations_read": "记忆系统 · 关系那一块（**今天没有**）",
    "read_events": "事件库那一侧（ **已经接上了**：`launch.py` 的 `remember_handlers` "
                   "交了它 ⇒ 读口有了；参数 schema 照旧没有）",
    "self_note": "自己那本记录那一侧（ **已经接上了**：`launch.py` 的 `remember_handlers` "
                 "交了它；`note.py` 是交接笔记，不是它）",
    "self_observe_add": "自我观察那一侧 ＋ 待确认队列（ 那是**插件侧**的机制名，与「悬着的事」那条记录分开）",
    "abouther_propose": "《关于自己》那一侧 ＋ 待确认队列（ 同上：那是插件侧的机制名）",
    "profile_add": "记忆系统 · 画像那一块（ **已经接上了**："
                   "`memory.handlers` 交了它 ⇒ 落 `memory.remember_person` ；参数 schema 照旧没有）",
    "pending_read": "记忆系统 · 悬着的事那一块（ **已经接上了**："
                   "`memory.handlers` 交了它；参数 schema 照旧没有：`parameters=None`）",
    "send_private": "出站匣那一层（ **已经接上了**：`outbox.handlers` 交了它 ；"
                    "而**递到手机**那一头仍然没有 —— 那句话由 `outbox.NO_ROAD` 带着 ）",
    "finish_turn": "主循环那一侧（ **已经接上了**：`launch.py` 的 `remember_handlers` "
                   "交了它 ⇒ 收工由 `ends_turn` 那一格驱动；参数 schema 照旧没有）",
}


def build_table(
    specs: Sequence[ToolSpec | ToolDef],
    handlers: Mapping[str, Callable[..., Any]],
    *,
    reserved: Callable[[str], bool] = is_reserved,
) -> dict[str, ToolDef]:
    """装配：把声明变成一张**可用的表**（`dict`：名字 → `ToolDef`）。

    ⚠️ **两种入参都收，而这是有意的**（6.1 把两侧划开了，这个入口正是它俩交汇的地方）：

    · 插件递的是**裸声明** `ToolSpec` —— 上面没有 `effect`（6.1 把它划给装配层）
      ⇒ 在这里补默认那一级 `immediate`；
    · 动词表那 13 行（`DECLARED_TOOLS`）**已经是 `ToolDef`** —— 6.7 给了每一行的
      effect ／ `ends_turn`（`finish_turn` 还有 `08` 8.6 那条 `barrier_after`）
      ⇒ **原样收下**，只往上绑 handler。
      ⚠️ 若这里只收 `ToolSpec`，那两条 `pending` 与 `finish_turn` 那两个开关
      **永远走不进来** —— 那才是"一个静默无效的东西"。

    ⚠️ **三件事在这里当场炸**（都是装配那一刻的错，见 `ToolDeclarationError`）：
       重名 ／ 撞上保留名 ／ `effect` 不是那两级。
    ⚠️ **第四件**：`handlers` 里**没人认领**的名字 ⇒ 当场炸 —— 递进来一个"表上根本没有"的
       handler 而默默丢掉，就是"一次装配什么都没发生、也没人说话"（`loop.py:60-63`）。
       ⚠️ 同一个道理，**递一个 `None`（或别的不能调的东西）也算炸**：那与"压根没递"
       装配完一模一样，而调用方会以为自己接上了。
    它输出普通状态信息：表里一个 `act` 都没有时（规格 6.2）——
       那是预期分类状态，不影响派发，也不使用 Python 警告。
    """
    table: dict[str, ToolDef] = {}
    for spec in specs:
        if not isinstance(spec, ToolDef):
            spec = ToolDef(name=spec.name, description=spec.description,
                           parameters=spec.parameters, tags=spec.tags, usage=spec.usage)
        if spec.name in table:
            raise ToolDeclarationError(
                f"{spec.name} 声明了两次 —— 后一条会静默盖掉前一条，"
                "而『谁赢了』取决于行序")
        if spec.effect not in EFFECTS:
            raise ToolDeclarationError(
                f"{spec.name} 的 effect 是 {spec.effect!r} —— 只认 {EFFECTS}")
        if reserved(spec.name):
            raise ToolDeclarationError(
                f"{spec.name} 是框架自己占着的名字（保留名）—— 换一个")
        if spec.name in handlers:
            handler = handlers[spec.name]
            if not callable(handler):
                raise ToolDeclarationError(
                    f"{spec.name} 的 handler 递进来是 {handler!r} —— 不是能调的"
                    "（递个 None 与『压根没递』装配完一模一样，而那正是静默无效）")
            table[spec.name] = replace(spec, handler=handler)
        else:
            table[spec.name] = spec

    unclaimed = sorted(set(handlers) - set(table))
    if unclaimed:
        raise ToolDeclarationError(
            f"这些 handler 没人认领：{unclaimed} —— 表里没有同名的声明"
            "（丢掉它们 = 一次装配什么都没发生、也没人说话）")

    _notice_if_no_act(table.values())
    return table


def _notice_if_no_act(tools: Iterable[ToolDef]) -> None:
    """Expected empty category: status only, no changes to dispatch or tags."""
    if any(ToolTag.ACT in tool.tags for tool in tools):
        return
    logging.getLogger(__name__).debug(
        "工具分类：当前无 act（仅改变外部状态的动作）类工具；属正常情况。")
