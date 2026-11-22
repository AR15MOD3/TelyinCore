# telyincore/handoff.py
"""交接的阈值判定、请求合并与批次边界。

生产接线由 Console._a_handoff 完成；时钟、笔记写入、会话操作和总线注入均由调用方提供。
手动请求在批内登记、批末兑现；空闲时直接执行。硬阈批末检查可直接交接，
软阈第一次只警告并记切点，下一批再请求交接；上游超限也走请求路径。

批末按 flush_handoff → batch_end_check → flush_handoff 执行，必须保证一批最多交接一次。
只有交接成功才清除请求，失败后保留重试机会；换代由主循环判定。
batch_end_check 必须自行短路已有请求及本批已完成的交接，避免软硬阈重复执行。
软阈预警与硬阈交接可以同批发生；预警必须排进总线队列，不能直接写入即将 reset 的会话。
request 是异步方法，调用方必须 await，才能实际执行并维护单飞状态。"""
from __future__ import annotations

import math
import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .note import HANDOFF_NOTE_KIND, NoteParts

# 交接笔记中单条记录的折叠上限。它与整份笔记的预算分别计算。
HANDOFF_FOLD_TOKENS = 1024

#: 软阈第一次那条警告的**原文** —— 照抄，别自己编（`05:172-177` 点名）。
PRESSURE_NOTE = (
    "[system] 交接快来了。交接会把上文全部清空,清空前最近的一段会写成交接笔记送到新会话。"
    "很早以前的事里,你还在跟的、还想做的,现在就写进文件,再过一会儿上下文就没了。"
)

#: **2026-10-12（作者拍的那句）**：跑到第 `SOFT_ROUNDS` 轮时附给它的那句提醒。
#: ⚠️ **它只说个数，不说该怎么办** —— 作者原话（2026-10-12）：「\u5979想接着聊就接着聊,
#:   给\u5979做束缚做什么。……但你说得对,确实要提醒\u5979聊了多久。但不能说让\u5979
#:   收一收。万一这个话题聊的正兴头上,然后你这样一句去束缚\u5979,那\u5979不就被泼了冷水。」
#:   ⇒ 于是这一句里**没有**"该收了"／"你说了算"／"停下"那类字 —— 那些都是**替它决定**。
#: ⚠️ **那个数就是主循环那个 `soft_rounds`**（`loop.py:64` 的缺省也是 10）：
#:   组装那一层（`console.assemble`）把**同一个数**递给两边（`soft_rounds=` 与这一句里的数）
#:   ⇒ 以后谁改了那个数，这句话跟着改，**不会变成假话**。
#: ⚠️ **文案归外面**（与 `PRESSURE_NOTE` 同一条规矩）：这一句是**缺省** ——
#:   谁要换字，覆盖 `Handoff.on_soft_limit_note()`（或把外面那一份递进来）。
#:
#: ★★ **2026-10-17：名实对齐了一次**（原来那句会读成"整场对话第 10 个来回"）。
#:   ⚠️ **数的是什么**（读出来的）：`loop.py` 的 `for round_no in range(1, self._hard + 1)`
#:      是在**每次 `_rounds()`** 里从 1 起的，而 `_rounds()` **每批**调一次
#:      （`process_one_batch`）⇒ 这个数 ＝ **这一批里的第几次模型往返**；
#:      而一轮 ＝ 一次模型调用 ＋ 它带的工具，**没调工具就当场收**（那一层的 ①）
#:      ⇒ 它**只在"工具连着调了 ≥9 次"时才够得着**（一趟普通对话永远到不了 10）。
#:   ⚠️ **原来那句"这一来一回，到这儿第 N 次"** 读起来像在数"整场对话的来回"
#:      ⇒ 两个读法差得很远（一个是"我们聊了 10 个回合"，一个是"这一趟你已经来回 10 次了"）。
#:      改成**把"这一趟"写在前面**，就是为了掐掉前一个读法。
#:   ⚠️ **作者那条纪律一个字没动**：只说个数、不说该怎么办（见上）。
SOFT_ROUNDS = 10
SOFT_ROUNDS_NOTE = "[system] 这一趟里，你已经来回第 " + str(SOFT_ROUNDS) + " 次了。"

#: 注入用的 kind（`note.HANDOFF_NOTE_KIND` 是同一个字符串 —— 写成两个会**一代一代
#: 把自己抄进笔记**，而那是静默的）。 **第三个是四个槽 Task 3 加的**（`inject_notice`）：
#: 「提醒」那一族在 `05:167` 那边的 kind 就是 `'notice'`（`inject_internal(…, 'notice')`）。
#: ⚠️ 它与 `INJECT_INTERNAL_KIND`（"handoff"）**不是同一个字符串，也不该是** ——
#: 三句话是三件事（交接快来了 ／ 交接完了 ／ 软上限那条提醒），塌成一个就再也分不开。
INJECT_NOTE_KIND = HANDOFF_NOTE_KIND
INJECT_INTERNAL_KIND = "handoff"
INJECT_NOTICE_KIND = "notice"

# 交接完成后注入的唤醒提示，与交接前的压力预警分开。
# 提示引用笔记位置，必须先写笔记，再生成提示。
WAKE_NOTE_LINES = (
    "[system] 交接完了。之前的上下文已经全部清空,工作区的文件都还在。",
    "清空前的一段写成了交接笔记,随这一批送到,也存在 {path} 里:",
    "按时间分段,有内容的段才送到;「最近的一段」接着现在,「更早的一段」只作参考。",
    "笔记中的对话、调用与回执都是历史摘录;省略处按所给原文引用核对,不能补猜执行结果。",
    "入参是当时拟发出的内容,对方是否收到、收到多少要看回执和后续事件;已受理不代表已经送达。",
    "自然接续当前话题或动作,不必口头表示意识到暂停、清空或交接。旧发言和折叠后的片段不作句式或长短的范本。",
    "对外交流的工具不要输出空内容,空的一个字也发不出去。",
    "想说话就把话直接写进调用里。这轮不想说,就调 end_turn。",
)


def default_wake_note(path: str) -> str:
    """生成交接后的唤醒提示，填入笔记位置；路径为空时使用“工作区”。"""
    return "".join(line.format(path=path or "工作区") for line in WAKE_NOTE_LINES)

#: 「这大概是多长」—— **注入**（与 `note.Estimate` 同形）。
#: ⚠️ 回 `None` 的语义是「**没得判**」（估算器没接）⇒ 软阈整条路跳过，
#:    而**不是** 0（那样会"碰巧"判成没超阈，两种情形就分不开了）。
EstimateNow = Callable[[], int | None]

# 保留尾部的生成器，输入顺序为 (两段笔记, 前缀)。
# 默认返回空尾。笔记通过注入进入新会话，与这里的保留尾部不同。
MakeTail = Callable[[tuple[str, str], Sequence[Any]], Sequence[Any]]

#: 「交接完了那句话」—— **注入**（与 `EstimateNow`／`MakeTail` 同一种手法）。
#: ⚠️ 它收的**笔记落盘位置**（可能是空串）⇒ 见 `default_wake_note` 的说明。
WakeNote = Callable[[str], str]

#: 「这段文字大概多少 token」—— 注入，**原样转交给 `note.build`**（形状同 `note.Estimate`，
#: `note.py:24`）。笔记的折行靠它记账。
#: ⚠️ 它**不是** `EstimateNow` —— **同名，两回事**：那个不收参数、回"整个会话大概多长"
#:    （软阈判据用）；这个收一段文字、回它的 token 数。混掉就会拿会话总长去折算行。
NoteEstimate = Callable[[str], int]

#: 「这条记录发生在哪一刻（毫秒）」—— 注入，**原样转交给 `note.build`**（`note.py:166`）。
#: ⚠️ 本模块**不解释递进来东西的内容**（见它自己的声明）⇒ 这里只把这条缝递过去，
#:    不自己去读 `Record` 的字段。
TimestampOf = Callable[[Any], int]


def _two(parts: NoteParts | tuple[str, str]) -> tuple[str, str]:
    """把 `note` 槽的产出收成两段 —— **收得下 `note.NoteParts`（真身）也收得下二元组**。

    ⚠️ `NoteParts` 是一个普通的 frozen dataclass（`note.py:43-48`），**不可解包**
       ⇒ 直接写 `earlier, recent = build(...)` 会 `TypeError` —— 而那是**接线那天**才炸的
       （本模块今天没有调用者）。
    ⚠️ 二元组那一支留给轻量替身与测试：它们不必为了适配去 import `note`。
    """
    if hasattr(parts, "earlier") and hasattr(parts, "recent"):
        return parts.earlier, parts.recent
    earlier, recent = parts
    return earlier, recent


@dataclass(frozen=True)
class HandoffPolicy:
    """Handoff policy, refreshed by the production caller for each decision.

    With a known input hard limit H = context - output, runtime soft pressure
    uses min(stage * soft_ratio, H * soft_ratio). Thus the normal policy leaves
    15% of INPUT capacity for growth, rather than assuming W >= 6.67 * output.
    Without a known hard limit, stage_tokens() preserves the legacy fallback.
    The note pool is floor(stage * keep_ratio), not a thinking-token budget.
    """

    max_tokens: int = 64000
    soft_ratio: float = 0.85
    keep_ratio: float = 1 / 3
    fold_tokens: int = HANDOFF_FOLD_TOKENS
    #: 本轮那个**上下文窗口**（引擎 `--max-context` ／ 档案那格「上下文上限」）。
    #: 给了它就当分母按比例走；`None` ＝ 回落 `max_tokens`（旧行为）。
    context_window: int | None = None
    allow_unknown_pressure: bool = True

    def stage_tokens(self) -> int:
        """软阈与笔记池的**分母** —— `context_window` 优先，缺席才用 `max_tokens`。

        ⚠️ 它是这一份里**唯一读那两个字段**的地方（取数只此一处）。
        """
        if self.context_window is not None and self.context_window > 0:
            return self.context_window
        return self.max_tokens

    def budget_tokens(self) -> int:
        """整份笔记的池 ＝ `floor(分母 × keepRatio)`（旧默认 ≈21333，**不是** 4096）。"""
        return int(math.floor(self.stage_tokens() * self.keep_ratio))

    def soft_limit(self) -> float:
        """软阈 ＝ `分母 × softRatio`（旧默认 54400）。"""
        return self.stage_tokens() * self.soft_ratio


@dataclass(frozen=True)
class HandoffResult:
    """换完那一刻交回来的东西。

    · `tail` —— 会话的新尾（默认返回空尾）；
    · `path` —— 笔记落盘的位置（由注入的 `write_note` 给；它也可以是空串）；
    · `parts` —— 笔记的两段（`earlier` 可能是空的 —— 池级预算会把更早那段挤掉）。
    """

    tail: list[Any]
    path: str
    parts: tuple[str, str]


class Handoff:
    """交接的状态机。**只读外面递进来的东西，不解释它们的内容。**

    ⚠️ 三个记号（`requested` ／ `pressure_warned` ／ `pressure_warned_at`）与 `processing`
       都是**公开只读的** —— 与 `loop.py` 的 `handoff_requested` 同形：先立位，由别的层读。
    """

    def __init__(
        self,
        *,
        snapshot: Callable[[], Awaitable[Sequence[Any]]],
        note: Callable[..., Awaitable[NoteParts | tuple[str, str]]],
        note_estimate: NoteEstimate,
        timestamp_of: TimestampOf,
        build_prefix: Callable[[], Awaitable[Sequence[Any]]],
        reset: Callable[[Sequence[Any]], Awaitable[None]],
        write_note: Callable[[str], Awaitable[str]],
        inject_internal: Callable[[str, str], Awaitable[None]],
        inject_external: Callable[[str, str], Awaitable[None]],
        commit: Callable[[Sequence[Any], str], Awaitable[str]] | None = None,
        make_tail: MakeTail = lambda parts, prefix: [],
        wake_note: WakeNote = default_wake_note,
        estimate: EstimateNow = lambda: None,
        hard_tokens: Callable[[], int | None] = lambda: None,
        policy: HandoffPolicy = HandoffPolicy(),
        policy_of: Callable[[], HandoffPolicy] | None = None,
        now_ms: Callable[[], int] = lambda: 0,
        log: Callable[[str, dict], None] = lambda event, detail: None,
        #: **「我正在干活」那一个真相来源** —— 由装配那一层装（`loop.processing`）。
        #: ⚠️⚠️ **这一格原来是一个属性**（`self.processing = False`），由 `app.build()`
        #:    在批末**镜像一位过来** —— 而那个镜像点**永远落在** `loop.processing` 还是真的
        #: 那一刻（`loop.py:135` 在 `:138` 松开**之前**）⇒ 那一格成了**单向闩**：
        #:    一个批跑过之后它**永远是 True**，而"写回假"那一句**全树没有**（松开在
        #:    `loop.py:138`，那一个文件逐字节禁改 ⇒ 没有钩子）。
        #: 实测代价（2026-10-03）：**手动交接那一条路只在"进程起来之后还没跑过批"的
        #:    那段时间里有效** —— 作者第 2 次按成功、第 3 次失败，差的就是"那一头答过一批"。
        #:    ⇒ 改成一个**读口**：谁拿得到那个真相（装配那一层同时拿着 `loop`），谁装它。
        #: 于是 `processing` **现读**，"两处各答一个数"这件事真的去掉。
        #: ⚠️ **没装 ⇒ 恒假**（与那一格从前的缺省同义：没有谁在干活）。
        processing_of: Callable[[], bool] | None = None,
        # Optional source-side check: old integrations keep their behavior.
        automatic_handoff_allowed: Callable[[], bool] = lambda: True,
        can_relieve_pressure: Callable[[float], bool] = lambda limit: True,
    ) -> None:
        """⚠️ 每一个钩子都是**注入的**（`loop.py:41-49` 的同一种手法）。

        · `note` 的签名是 `async (records, *, split_at_ms, budget_tokens, fold_tokens,
          estimate, timestamp_of) -> 两段` —— 本模块把 `note.build` 需要的**五样**原样转过去，
          **自己不裁**。回值**收得下 `note.NoteParts`（真身）也收得下二元组**（见 `_two`）。
          ⚠️ 槽本身必须是 `async`，而 `note.build` 是**同步**的
             ⇒ 接线方包一层（而那一层应当**只补 `async`**，不搬参数、不改形状 ——
             否则"这个槽接得上 `note.build`"就没被量到，见
             `test_the_note_slot_fits_the_real_note_build`）。
          （今天没人递它 ⇒ 见模块 docstring 那段"没有调用者"。）
        · `note_estimate` ／ `timestamp_of` 是**转交给 `note.build` 的两样**（`note.py:165-166`）
          —— 唯独它们没有默认值：给一个错的默认会**静默**算错笔记预算。
        · `log` 是**注入的**，因为 `log.py` 是另一层（`05:199` 的留痕第三处）。
        """
        self._snapshot = snapshot
        self._note = note
        self._note_estimate = note_estimate
        self._timestamp_of = timestamp_of
        self._build_prefix = build_prefix
        self._reset = reset
        self._commit = commit
        self._write_note = write_note
        self._inject_internal = inject_internal
        self._inject_external = inject_external
        self._make_tail = make_tail
        self._wake_note = wake_note
        self._estimate = estimate
        self._hard = hard_tokens
        self._policy = policy
        self._policy_of = policy_of
        self._now_ms = now_ms
        self._log = log
        # 那个**读口**（不是一位副本）—— 见构造形参上那一段：它换掉的是一个单向闩。
        self.processing_of = processing_of
        self._automatic_handoff_allowed = automatic_handoff_allowed
        self._can_relieve_pressure = can_relieve_pressure

        # 批边界那个记号 —— **本刀起它不再住在这里**：它**现读** `processing_of`
        #    （见下面那个属性）。⚠️ 从前这一行是 `self.processing = False`，而那一格
        #    由 `app.build()` 在批末镜像 —— 而那正是那个单向闩的来源。
        # 「记一笔」是**请求**，做完才不是请求（`loop.py:120-125`）。
        self.requested = False
        # 软阈那两步的两个记号 —— `pressure_warned_at` 是**毫秒戳**，不是 token 数，
        #   而它是交接笔记 `split_at_ms` 的**唯一来源**（`note.py:162,170,178`）。
        self.pressure_warned = False
        self.pressure_warned_at: int | None = None

        # 单飞：正在跑的那一次交接（`loop.py:1332-1342` 的 `truncatePromise` 同形）。
        self._running: Awaitable[None] | None = None

    # ── 判据 ────────────────────────────────────────────────────────────────

    @property
    def processing(self) -> bool:
        """ **「我正在干活」—— 现读**（不是缓存的那一位）。

        ⚠️⚠️ **它为什么从一个属性变成读口**（2026-10-03 实测那一条，逐字在构造形参上）：
           它原来由 `app.build()` 在**批末**镜像过来，而那一刻 `loop.processing`
           **总是真**（`loop.py:135` 在 `:138` 松开之前）⇒ 那一格**一个单向闩**：
           一个批跑过之后永远是 `True`，而写回假的那一句全树没有（松开在禁改的那个文件里）。
            代价：**`request()` 从此永远走"批内只登记"那一支** ⇒ 手动交接那一条路
           **只在"进程起来之后还没跑过批"的时候有效**（作者两次按下的差别就是它）。
        ⚠️ **"公开只读"这件事现在由语言保证**（`@property` 没有 setter）——
           与类文档那句"三个记号都是公开只读的"同一条： 从前它只是**约定**。
        ⚠️ **没装读口 ⇒ 恒假**（与那一格从前的缺省同义）。
        """
        return bool(self.processing_of()) if self.processing_of is not None else False

    def hard_tokens(self) -> int | None:
        """硬阈 ＝ `max(0, context_window − max_tokens)`。

        ⚠️ **`None` 的语义是「不据此限制输入」，不是 0**（`05:72-77`）——
           写成 0 会让**每一次**批末都判成超限 ⇒ 每批都交接一次。
        ⚠️ 这个数由注入的 `hard_tokens` 算（它读 `Config` 的两个字段）⇒ 本模块不认识配置。
        """
        return self._hard()

    def over_hard(self, est: int, *, if_reducible: bool = False) -> bool:
        """ 判据是 `est > hard`（**严格大于**）—— 等于是**没超**。

        ⚠️ `hard is None` ⇒ **永远回假**（"不据此限制输入"）。
        """
        hard = self._hard()
        if hard is None:
            return False
        return est > hard and (not if_reducible or self._can_relieve_pressure(hard))

    def on_pressure_note(self) -> str:
        """软阈第一次那条警告的文案。**判据与文案都归外面**（Q2 乙）⇒ 这里给个默认。

        ⚠️ 谁要换文案，**覆盖这一个方法**（或把 Persona 的那一份递进来）——
           而默认值就是要照抄的那句原文。
        """
        return PRESSURE_NOTE

    #: **2026-10-12（作者拍的那句）**：主循环那个软阈（跑到第几轮附那一句提醒）——
    #:   组装那一层把它**明着**递给两边（`soft_rounds=` 与那句话里的数）⇒ 真跑以这一格为准。
    soft_rounds: int = SOFT_ROUNDS

    def on_soft_limit_note(self) -> str:
        """ 跑到软阈那一轮附的那一句话（**只说个数**，不说该怎么办）。

        ⚠️ 与 `on_pressure_note` **同一个成法**：文案归外面，这里给一个缺省；
           谁要换字，**覆盖这一个方法**（或把外面那一份递进来）。
        ⚠️ 它**不是**"该收一收了"那条通知（那是别的东西，今天不存在）——
           这一句只报一个数，怎么办由它自己定。
        """
        return SOFT_ROUNDS_NOTE

    # ── 三条路 ──────────────────────────────────────────────────────────────

    async def request(self) -> bool:
        """请求交接，供手动入口及内部阈值逻辑调用。

批内只登记 requested，由批末兑现；空闲时 await 完成一次交接。
已有执行中的交接或登记的请求时返回 False，否则返回 True。
调用方必须 await；仅创建协程不会执行，也不能把协程对象当作执行中的任务。"""
        if self._running is not None or self.requested:
            return False
        if self.processing:
            self.requested = True
            return True
        await self._force()
        return True

    async def flush_handoff(self) -> None:
        """批边界那个口 —— **一批被调三次**（`loop.py:127`／`:134`／`:136`），所以它**幂等**。

         **`2026-09-30` 更正（单笔修波 I1）**：这一行此前写着"**批末**那个口 —— **批末被调
          两次**"，而那是 T1 之前的事实。T1 把**同一个口**接进了 `maintenance` 之后
          （`loop.py:127`，在 `self.processing = True` **之前**）它同时是**批首**那一面 ⇒
          **一批三次**：批首一次（`:127`）、批末两次（`:134`／`:136`）。
          ⚠️ 那不是"多调了一次"：`requested` 为假时它第一句就返回 ⇒ **另外两次是空转**，
          而"**批内登记的那一张票由批首兑现**"这条语义要的正是**它也在批首被调**。
          （ 缺了批首那一次，批内登记的那张票就要等到**批末**的 `flush_handoff`
          （`loop.py:134`）才被兑现 —— 而句子里那个 `processing` **那时还是真**：
          这一页管不了那一刻，它由**调用方**置（见 `self.processing` 那一行）。）

         **次序是这条的全部**：位在**做完之后**才清。
          · 位已假 ⇒ 直接返回（第二次那次是空转）；
          · 做失败 ⇒ **记号留着**（否则那次交接再也没人重试 —— `loop.py:122` 的理由）。
        ⚠️ 单飞：已经有一次在飞 ⇒ 把它**交回去**（`loop.py:1335` 同形），不另起一次。
        """
        if self._running is not None:
            await asyncio.shield(self._running)
        elif self.requested:
            await self._run_owned()

    async def batch_end_check(self) -> None:
        """批末钩子那条路 —— **先短路，再判软阈，最后判硬阈**。

        ① **批末短路**（`05:405-406`）：`requested` 为真 ⇒ 有请求**等在外面** ⇒ 直接返回。
           （本仓 `flush_handoff` 在它**之前**被调 ⇒ 正常情况下到这里位已假、
           那一次已经做过了；这一句防的是"排不上队"的那种次序。）
        ② **软阈** —— 判据从注入的 `estimate()` 来（Q6 乙）；
           `None` ⇒ 整条路跳过（"没得判"，不是 0）。
            **它可能在这一句里造出一张票，或干脆当场做完一次** ⇒ 必须**再判一次**
             （见下）—— 顶上那句短路**看不见**这一句里新造的票。
        ③ **硬阈** —— 超限就**直接做**（`_force()`），**不置位**（Q5 甲）。
           置位的话，紧接着的第 2 次 `flush_handoff` 会**再做一次** ⇒ 一批两次交接。
        """
        if self.requested or self._running is not None:
            return # 批末短路：软阈那一批不再多跑一次硬阈
        if self._automatic_handoff_allowed() is False:
            # A note-only session has nothing new to summarize. Resetting it
            # again can erase its sole handoff note and never relieve pressure.
            self._log("handoff_deferred", {"reason": "no_new_noteable_content"})
            return

        # **第二次短路**：`_check_pressure()` 回真 ＝ 它刚刚登记了一张票、或刚做完一次。
        #   两种都不许硬阈在同一批里再抢一次 —— 否则那一批会交接两次
        #   （一次来自软阈、一次来自硬阈；而第二次还会把 `pressure_warned_at` 抹掉，
        #   于是它的 `split_at_ms` 是 `None`，笔记的"更早那段"也跟着丢了）。
        #   ⚠️ 它**只警告**时回假 ⇒ 硬阈照常往下判 —— 否则第一次跨软阈时硬阈会被白推迟一批。
        if await self._check_pressure():
            return

        est = self._estimate()
        if est is None:
            return
        if self.over_hard(est, if_reducible=True):
            await self._force()

    # ── 注入那条路（ 四个槽 Task 3：这一格等的那个公开口）────────────────────
    async def inject_notice(self, text: str) -> None:
        """ **公开的注入口** —— 把一条提醒送进**注入那条路**（`_inject_internal`）。

        ⚠️ **它为什么存在**：`loop.py:49` 声明、`:236` 调用的那个槽（`attach_hint`）
           等的正是「**`handoff` 把注入那条路公开**」（那句欠账逐字写在 `app.py` 的
           `_not_wired("把提醒附上去", …)` 上）—— 而此前本模块只有**私有**的
           `_inject_internal`（`_check_pressure` 与 `hand_off` 在用）⇒ 外面接不上那一格。
           ⇒ 这一条就是那个公开口，而分工照 `loop` 那边自己立的规矩：
           **通道由本模块给，内容由调用方给**（"循环不写散文"）。

        ⚠️ **它只转交，不解释、不改写、不判时机**：谁调它、什么时候调、那条提醒该说
           什么，全是**调用方**的事 —— 今天那一格接的是 `app.build()` 用
           `functools.partial(handoff.inject_notice, hint_text)` 折出来的零参协程。

        ⚠️ **它继承了「注入要排进队列」那条契约**（模块 docstring 的 ④）：
           注入的东西**必须排进队列**（`bus.push` 那一类），**不许直接写进会话的消息表** ——
           后者会在 `reset` 那一刻被冲掉，而那是**静默的**（那句话永远送不到）。
            本模块**保证不了**它（`inject_internal` 是注入的，见 ④）⇒ 只能点名写清。

        ⚠️ **kind 用的是 `INJECT_NOTICE_KIND`（`'notice'`）**，不是 `INJECT_INTERNAL_KIND`
           （"handoff"）：三句话是三件事（交接快来了 ／ 交接完了 ／ 软上限那条提醒），
           塌成一个字符串会让读的人分不开它们。

        ⚠️ **没有返回值**（`None`）—— 与 `loop.py:49` 那份 `Callable[[], Awaitable[None]]`
           的**另一半**逐字对上（零参那一半由调用方 partial 出来）。
        """
        await self._inject_internal(text, INJECT_NOTICE_KIND)

    # ── 里面 ────────────────────────────────────────────────────────────────

    async def _check_pressure(self) -> bool:
        """软阈那条路 —— **分两步**：第一次只警告并记切点，**下一批**才真请求。

         **回值**（`batch_end_check` 靠它判"这一批是不是已经有票了"）：
          · 回**假** —— 没得判（估算器缺席）、没到软阈、或**只是警告**；
          · 回**真** —— 这一步**登记了一张票**（批内）或**当场做完了一次**（空闲）。
        ⚠️ **"只是警告"必须回假**：警告不占位（它不交接），硬阈照常判 —— 而值 54400/40960
           这组默认数下超软阈必然也超硬阈 ⇒ 回真就会让硬阈**永远**被推迟一批。
        ⚠️ 三个数**每次现读**（`05:79-86`）：`policy` 是同一个对象的属性，而
           Persona 换策略的方式是**换掉它** ⇒ 这里不许在构造时把数抄下来。
        ⚠️ `estimate()` 回 `None` ⇒ **整条路跳过**（`05` 的 `onBatchEnd` 那句 `return`）。
        """
        est = self._estimate()
        if est is None:
            return False
        hard = self._hard()
        # Reserve 15% of the actual INPUT allowance, not of input+output.
        policy = self._policy_of() if self._policy_of is not None else self._policy
        if hard is None and not policy.allow_unknown_pressure:
            return False
        soft = policy.soft_limit() if hard is None else min(
            policy.soft_limit(), max(0, hard) * policy.soft_ratio)
        if est <= soft or not self._can_relieve_pressure(soft):
            return False
        if not self.pressure_warned:
            self._log("pressure", {"estimate": est, "hard": hard, "soft": soft})
            self.pressure_warned = True
            self.pressure_warned_at = self._now_ms() # 切点：这一批的这一刻
            await self._inject_internal(self.on_pressure_note(), INJECT_INTERNAL_KIND)
            return False # 只是警告 ⇒ 不占位
        await self.request()                               # 第二次：真请求（批内登记／空闲当场做）
        return True # 登记了或做完了 ⇒ 硬阈让路

    async def _force(self) -> None:
        """ **直接做** —— 硬阈那条路走这个（**不经过 `request()`**，所以不置位）。

        ⚠️ 这就是"一批最多一次"的一半：置位的话，同一批里第 2 次 `flush_handoff`
           会**再做一次**。
        ⚠️ 单飞仍然成立：已经有一次在飞 ⇒ 交回去，不另起。
        """
        if self._running is not None:
            await asyncio.shield(self._running)
            return
        await self._run_owned()

    async def _run_owned(self) -> None:
        import asyncio
        if self._running is not None:
            await asyncio.shield(self._running)
            return
        completion = asyncio.get_running_loop().create_future()
        self._running = completion
        try:
            await self._run()
        except BaseException as error:
            completion.set_exception(error)
            completion.exception()  # Initiating caller receives the original below.
            raise
        else:
            completion.set_result(None)
        finally:
            if self._running is completion:
                self._running = None

    async def hand_off(self) -> HandoffResult:
        pending = getattr(self, "_handoff_pending", None)
        if pending is None:
            policy = self._policy_of() if self._policy_of is not None else self._policy
            snapshot = list(await self._snapshot())
            split = self.pressure_warned_at
            parts = await self._note(snapshot, split_at_ms=split,
                budget_tokens=policy.budget_tokens(), fold_tokens=policy.fold_tokens,
                estimate=self._note_estimate, timestamp_of=self._timestamp_of)
            earlier, recent = _two(parts)
            prefix = list(await self._build_prefix())
            tail = list(self._make_tail((earlier, recent), prefix))
            pending = dict(parts=(earlier,recent), prefix=prefix, tail=tail,
                           split=split, reset=False, path=None, injecting=False)
            self._handoff_pending = pending
        if pending['injecting']:
            raise RuntimeError("handoff injection outcome unresolved; automatic replay refused")
        earlier, recent = pending['parts']
        whole = '\n'.join(p for p in (earlier,recent) if p)
        if self._commit is not None:
            if not pending['reset']:
                pending['path'] = await self._commit(
                    [*pending['prefix'], *pending['tail']], whole)
                pending['reset'] = True
        else:
            # File-only adapters save the recovery note before discarding context.
            # Production commits prefix and durable scoped notes together.
            if pending['path'] is None:
                pending['path'] = await self._write_note(whole) if whole else ''
            if not pending['reset']:
                await self._reset([*pending['prefix'], *pending['tail']])
                pending['reset'] = True
        path = pending['path']
        pending['injecting'] = True
        await self._inject_internal(self._wake_note(path), INJECT_INTERNAL_KIND)
        for part in (earlier,recent):
            if part:
                await self._inject_external(part, INJECT_NOTE_KIND)
        self.pressure_warned = False
        self.pressure_warned_at = None
        self._log('handoff', {'path':path, 'split_at_ms':pending['split']})
        self._handoff_pending = None
        return HandoffResult(tail=pending['tail'], path=path, parts=(earlier,recent))

    async def _run(self) -> None:
        """**单飞的那一个** ＋ **清记号的那一处**。

         次序：**先做，成功了才清**。
          · 清早了 ⇒ 那次交接失败了就再也没人重试（`loop.py:122` 的理由）；
          · 清理摆在 `finally` 里也是错的 —— 那与"先清"是同一个效果。
        `_run_owned` 将单飞完成身份保留到所有回调退出；并发观察者通过 shield 等待，
        取消观察者不会取消原执行者。失败保留 requested，供显式恢复处理。
        """
        # _run_owned retains the single-flight completion through every callback.
        # **先做，成功了才清** —— 这三行次序就是 `loop.py:122` 那个理由的全部：
        #   · 清早了 ⇒ 那次交接失败了就**再也没人重试**（而这是静默的）；
        #   · 清理摆在 `finally` 里也是错的 —— 那与"先清"是同一个效果。
        #   ⚠️ 清的是**兑现过的那一次**：批内又来了一个新请求，语义是"再交接一次" ——
        #      而那不是今天任何一条路的形状（见模块 docstring 的第 ② 条）。
        try:
            self._log("started", {"estimate": self._estimate(), "hard": self._hard()})
            await self.hand_off()
        except BaseException as exc:
            self._log("failed", {"error_type": type(exc).__name__})
            raise
        self._log("completed", {})
        self.requested = False
