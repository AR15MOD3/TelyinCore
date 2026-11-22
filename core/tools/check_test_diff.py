"""判「一段 tests/ 的 diff 里，测试的**断言面**有没有被改动」。

**为什么域是断言面，而不是「所有改动行」**（Task 3 裁决 ⑥）：

`tests/` 的 diff 里，除跑法之外还**合法地**多出许多非断言改动 —— 一次机械的
同步→异步迁移会改掉替身（`def close` → `async def aclose`）、改掉 import、
`httpx.Client` → `httpx.AsyncClient`、`SyncByteStream` → `AsyncByteStream`、
`lambda` 替身 → `async def` 替身……⇒ **"合法的跑法形状"是无界的，枚举不完。**

实测（范围 `git diff bfdd9ff..15b4808 -- tests/`，**578** 条 `+`／`-` 行；2026-09-28 本机）：
枚举式白名单的残差 = **276** 行（brief 原样）／**217**（＋包装配对）／**90**
（＋纯新增豁免）／**67**（＋注释豁免）—— 而那 67 行**全是**上面那类管线。
把白名单放宽到能吞下它们，就必然也吞下本支**唯一的真例外**
（`match="must return a generator"` → `…"must return an async generator"`）⇒ 闸失去牙。
**⇒ 域收窄到「测试判定的东西」** —— 那才是 spec §七① 那条规矩本身。

⚠️ **上面那个 `578` 是"白名单实验那一刻"的快照，不是交付点 —— 两个数都要带着"哪一刻"读**：
   · **578** ＝ `bfdd9ff..15b4808`（`bee974d` 做完整条 HTTP 链的异步迁移起就是这个数，
     到 `15b4808` 记下这条 **67** 行曲线时仍是它；这正是本文那一段的测量基底）；
   · **1069** ＝ 本支原先那个交付点 `bfdd9ff..9b4782a`（**893** `+` ／ **176** `-`，
     diff 全文 **1836** 行 —— 与 `git diff --stat` 的 `893 insertions(+), 176 deletions(-)` 逐数相同）。
   ⇒ 照本仓的规矩：**范围一律写到提交点**。裸数（不管 `578` 还是 `1069`）都会随 `tests/`
     的**任何**改动过期 —— 与下面残差那个数同一条规矩：**只许量出来报，不许写死。**

域：`assert` ／ `with pytest.raises|warns` ／ `pytest.raises|warns|fail|skip|importorskip|xfail`
    的调用 ／ `@pytest.mark.skip|skipif|xfail` 装饰器。
    （**静音类** —— `skip`／`skipif`／`xfail`／`importorskip` —— 会让一条测试**不再判任何东西**，
      所以也算断言面：本仓全套唯一那条 skip 就是 `skipif(os.name == "nt", …)` 造的。）
放行：① **纯新增** —— 改动块里没有对应的删除行（那是**新**测试，不是改既有测试）；
      ② **跑法包装** —— 把 `-`／`+` 两侧施加**同一组**归一化（见 `_PACKAGING`：
         `await ` 与 `async def|for|with` 这类**语法**外壳）之后**逐字相同**。
报出：其余一切（**宁可多报，不可漏报**）。

⚠️ **原来还有一条 ③（"该行本身就是跑法形状"）—— 裁决 ⑫ 把它连名字带测试一起删了。**
   账（brief 那 13 条跑法形状的去向，一条不剩）：
   · **11 条**管的全是**非断言行**（`@pytest.fixture`、`def anyio_backend`、`import asyncio`、
     `async def stream_chat`、diff 文件头……）⇒ 域收窄之后**永远不会被查到** ⇒ 裁决 ⑥ 删；
   · 第 12 条 `\bawait\b` 实测"**有匹配、零消费者**"（加回去残差不变）⇒ 裁决 ⑥ 删；
   · 最后 1 条（`async for|with`）在裁决 ⑨ 收窄之后**在真 diff 上不再豁免任何行**
     （带门禁地加回去，逐行重跑：残差仍是 **8**）⇒ **一条在真实产物上效果为零的豁免**：
     **收益为零**，
     而风险是将来某天**悄悄放过**一条本该报出来的行（那时没人会知道是它干的）⇒ 裁决 ⑫ 删。
   ⇒ **留下的是"严格更强或相等"的闸** —— "相等"那半有实测：残差 **8 → 8**。
     ⚠️ **这个数改过两次，三次的值都量过 —— 账留在这里**（它比"一个数"值钱）：
     · **4**：收尾波**动手之前**那一版的快照（实测 `bfdd9ff..5a862be` 上正是 4）——
       收尾波把它当成交付值写了下来，**漏掉了它自己那 9 行** （这就是被复评盖章的那个 Important）；
     · **13**：收尾波**交付时**（`a397101`）的真值 ＝ 4 ＋ 它自己那 9 行
       （git 把新旧两条测试**共用的一行 `\"\"\"`** 当上下文 ⇒ 一块里带上了删除行 ⇒ ① 不成立）；
     · **8**：**尾波**（本文这一版）在 `tests/` 里补了 `_LENGTH_EXEMPT` 的钉住测试 ⇒
       git **重新切块** ⇒ 那 7 条 `+ assert` 落进一块"纯新增"里（不再被报），
       而新测试的 2 条 `assert` 落进带删除行的那一块（被报）。
     ⇒ **所以这个数不许"写死"**：它随 `tests/` 的**任何**改动而变 —— 只能量出来再报。

    **而"报出来的那些行都是什么"也要一起量**（⑥-v：原先只活在一份 gitignored 的报告里）：
   同一个脚本跑 `git diff 227d995..bfe0fe8 -- tests/` ⇒ 报出 **16 行 ／ 8 对**
   （本机 2026-09-30 量的；那份 diff 是复评看的那一份），而**逐对都是加固、没有一处放宽**：
   · **7 对**：`wire()` 那七处 `==` 的右边**各加一格** `write_clobbered`（其中一处正是
     "普通函数替代品"那一格：判据从"顶掉了"变成"**那件事报得出来**"）；
   · **1 对**：`@pytest.mark.skipif(_NODE is None or not _ESBUILD.exists(), …)` →
     `@pytest.mark.skipif(_BUILD_SKIP is not None, …)` —— 同一条判据**挪进一个纯函数**
     （它自己的真值表另有钉子），于是那条**判据本身第一次判得动**。
   ⇒ **它是"那一段 diff 上"的一个读数**（ **范围一律写到提交点**，与本文上面那条
    规矩同）：换一段 diff 就得重量，**别拿它当"这条闸今天是干净的"那句保证**。

⚠️ 判据是**白名单**，不是"不许改 assert"那种黑名单 —— 黑名单会漏掉 `pytest.raises` /
   `pytest.fail`：它们**不在 `assert` 行上**。

⚠️ 已知边界**两条**（都在这里写清，供将来收）：

① **多行断言的续行不在域里** —— 域按「行首是不是 `assert`／`with pytest.raises`」判，
   续行不是。本支的 diff 里**没有**这种行（两版都解析成 AST、取出多行断言的续行，
   再与全部改动行逐条比对：**零命中**）—— 但它是**真实的洞**。

② **`with pytest.raises|warns(...)` 的**非断言块体**不在域里**（准备／改写的那些行）——
   那几行不是断言行，而它们恰恰是「该抛的那个调用」⇒ **改了它们不会被本脚本报出来。**
   实测本支的 diff：块体里有 **87** 条改动行落在域外。把它们并进域**不划算**：
   实测（只并块体、其余判据一字不动）残差 **3 → 64** 行（⑨ 之前的测量；
   ⑨ 之后本支基线是 **8** —— 尾波在 `tests/` 里补了一条测试、git 重新切过块 ⇒ 4 → 13 → 8，
   三态都量过，账见上面 `_WRAPPER` 上方那段）—— 块体里的改动多半是
   `sleep=lambda ms: None` → `sleep=no_sleep`，以及多行语句的括号／缩进重排；
   要吃掉它们就得再加一条**空白归一化**与一条替身豁免，那又回到"枚举不完"的老路上了。
   **⇒ 冲抵（**只在一种情形下**）**：块体丢掉了「该抛的那个调用」（或让它不再抛）⇒
   `with pytest.raises(...)` 当场失败 ⇒ **测试本身会红**。
   ⚠️ **换成另一个仍然抛同类异常的调用、或换掉块体里的替身 ⇒ 测试仍绿、这道闸也不响** ——
   块体的**语义等价性本脚本判不了**，那是这条边界的真实代价，**不许读成"测试会兜住"**。
   （块体里若有 `assert`，那些行**在**域内、仍然判 —— 这一句是准的。）
   ⇒ 代价与收益都量在这里：**要不要收，是一个决定，不是一次优化。**
"""
from __future__ import annotations

import argparse
import io
import tokenize
import pathlib
import re
import sys

#: 断言面 —— 只有这些形状的行才进判据。
#: ⚠️ 锚在行首（缩进后）：判的是**语句**，而不是"哪一行里提到了 assert" ——
#:    后者会把注释与 docstring 也拖进来，那些是**说明**，不是判定。
#: ⚠️ 末两组是**静音类**（裁决 ⑩ 补进来的）：`skip`／`skipif`／`xfail` 能让一条测试
#:    **不再判任何东西**，而那正是"改了测什么"里最重的一种。
_ASSERTION = re.compile(
    r"^\s*(?:assert\b"
    r"|with\s+pytest\.(?:raises|warns)\b"
    r"|pytest\.(?:raises|warns|fail|skip|importorskip|xfail)\b"
    r"|@pytest\.mark\.(?:skip|skipif|xfail)\b)"
)

#: "跑法包装"的归一化 —— **同一组**规则施加到 `-`／`+` 两侧，之后要求**逐字相同**。
#: ⚠️ 只认**语法**上的 async 外壳。**没有**"抹掉 `async` 这个词"那条规则 ——
#:    `match="must return an async generator"` 里的 `async` 是**字符串字面量的一部分**，
#:    抹掉它这道闸就没牙了（那正是本支唯一的真例外）。
#:
#: ⚠️ **评估过而没采纳的一条：`aiter_of(`**（审查要求把"不采纳"写进代码里，而不是只留在
#:    gitignored 的 `.superpowers/` 账本里 —— 只写在那儿的话，出了这台机器就不可复核）。
#:    它在计划里本来是 `_WRAPPER` 的第三条。**结论：无牙 ⇒ 不加。** 实测（2026-09-28 本机，
#:    真 diff `bfdd9ff..HEAD -- tests/`）：把它加回 `_PACKAGING`（`re.compile(r"aiter_of\(")` 的
#:    替换是 `""`）之后逐行重跑，残差 **8 行、逐行相同** ⇒ 它**一行都没吞掉**。
#:    （那条 `-  assert list(...)` / `+  assert [e async for e in …]` 的差值在
#:      `list(` 与 `[e async for e in `，与 `aiter_of(` 无关 ⇒ 它当然吞不掉。）
#:    ⇒ 按裁决 ⑫ 的同一条理由（"一条真实产物上效果为零的豁免"只带来将来某天悄悄放过一行的
#:      风险），**不采纳**。
#:    ⚠️ 但留着这条**披露**是有用的，两个方向都要能自洽：
#:    · 加进去是**安全**的（它不可能放过本支那处真例外：`match="…async generator"` 里
#:      没有 `aiter_of(` 可以抹）；
#:    · 而它**今天不承重** —— 所以"加了它、闸就出事"这种说法**对不上实测**。
#:    将来若有人真在断言面上改了 `aiter_of(...)` 的用法，这条注释记的是"当时的量法"，
#:    不是"它永远无所谓"。
_PACKAGING = (
    (re.compile(r"\bawait\s+"), ""),
    (re.compile(r"\basync\s+(def|for|with)\b"), r"\1"),
)

def _normalize(body: str) -> str:
    """把一行代码里的**语法** async 外壳抹掉（`-`／`+` 两侧用**同一组**规则）。"""
    # Tokenize first, fail closed on incomplete literals/lines. Never rewrite
    # STRING/COMMENT tokens (including f-strings on newer Python versions).
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(body).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return body
    if any(t.type == tokenize.ERRORTOKEN or
           tokenize.tok_name[t.type].startswith("FSTRING") for t in tokens):
        return body
    cuts = []
    for index, token in enumerate(tokens):
        if token.type != tokenize.NAME or token.string not in ("await", "async"):
            continue
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        if following is None or token.start[0] != 1 or following.start[0] != 1:
            continue
        if token.string == "async" and following.string not in ("def", "for", "with"):
            continue
        if not body[token.end[1]:following.start[1]].isspace():
            continue
        cuts.append((token.start[1], following.start[1]))
    for start, end in reversed(cuts):
        body = body[:start] + body[end:]
    return body


def _change_blocks(diff_text: str) -> list[tuple[list[str], list[str]]]:
    """把 diff 切成「改动块」：一段连续的 `-` 行 ＋ 紧随其后的连续的 `+` 行。

    上下文行、`@@` 头、`diff --git` / `index` / `---` / `+++` 文件头**都切开块**
    ⇒ **配对只发生在同一个「改动块」内**（口径是"按改动块"：隔着上下文行就断开）
    —— 跨块／跨 hunk 的两个"看着像一对"的行**不会**被配起来。

    ⚠️ 已知边角：内容以 `--` 开头的**删除行**渲染出来也是 `--- …` ⇒ 会被当成文件头
       （brief 的写法就是这样）。断言行不可能长成那样（它的行首是 `assert`／
       `with pytest.`／`pytest.`），所以它**藏不住域里的行**；但它会**切开块** ⇒ 影响配对。
    """
    blocks: list[tuple[list[str], list[str]]] = []
    removed: list[str] = []
    added: list[str] = []

    def flush() -> None:
        if removed or added:
            blocks.append((list(removed), list(added)))
        removed.clear()
        added.clear()

    for line in diff_text.splitlines():
        if line.startswith(("---", "+++")):      # 文件头；不是改动行
            flush()
        elif line.startswith("-"):
            if added:                            # `+` 段结束 ⇒ 这一块收口
                flush()
            removed.append(line)
        elif line.startswith("+"):
            added.append(line)
        else:                                    # 上下文行／`@@`／`diff --git`
            flush()
    flush()
    return blocks


def _partner(removed: list[str], added: list[str], index: int) -> str | None:
    """改动块里第 `index` 行在**另一侧**的对应行；对不上就返回 `None`。

    ⚠️ 按**位置**对齐：一次机械迁移里，删掉的那几行与换上的那几行是一一对应的
       （git 也是按这个顺序排的）。多删／多加导致**对不齐**时**不猜** ⇒ `None`
       ⇒ 那几行照报（宁可多报，不可漏报）。
    """
    if index < len(removed):
        return added[index] if index < len(added) else None
    offset = index - len(removed)
    return removed[offset] if offset < len(removed) else None


def run_check(diff_text: str) -> list[str]:
    """返回越界的行（空列表 = 干净）。**只认 `+`／`-` 行，不认上下文行。**"""
    bad: list[str] = []
    for removed, added in _change_blocks(diff_text):
        if not removed:
            continue                             # ① 纯新增：新测试，不是改既有测试
        for index, line in enumerate(removed + added):
            body = line[1:]
            if not _ASSERTION.match(body):
                continue                         # 非断言行：不是这条判据的域
            partner = _partner(removed, added, index)
            if partner is not None and _normalize(partner[1:]) == _normalize(body):
                continue                         # ② 同一条断言被"跑法包装"过
            bad.append(line)
    return bad


def main(argv: list[str] | None = None) -> int:
    """把越界行原样打到 stdout；0 ＝ 干净，1 ＝ 有越界行。"""
    parser = argparse.ArgumentParser(description="判 tests/ 的 diff 有没有改到断言面")
    parser.add_argument("--diff-file", help="从文件读 diff；不给就读 stdin")
    args = parser.parse_args(argv)
    if args.diff_file:
        diff_text = pathlib.Path(args.diff_file).read_text(encoding="utf-8")
    else:
        diff_text = sys.stdin.read()
    bad = run_check(diff_text)
    for line in bad:
        print(line)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
