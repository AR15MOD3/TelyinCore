"""**她自己那几支笔，她会不会用** —— 独处那一轮的真模型探针（2026-10-17）。

⚠️ **为什么单独一个探针**（不并进 `group_portrait_probe.py`）：
   那一个量的是**群聊那一支笔**（`profile_add`），入口是"投一批群来话"；
   而这一个量的是**独处那一轮**（`self_note` ／ `unresolved_add` ／ `self_observe_add` ／
   自述那三份），入口是"心跳推一条 tick 注记进来"。两条路的起点不同，混在一起
   会让两份报告各读一半。
   ⚠️ 而且那个文件已被另一路工作改过（overlay ／ requests ／ linger 那几档）——
   不去动它。

⚠️⚠️ **它量的是什么**：那些笔在**全部事件库里都是 0 次**，而它们**都在她手上**
   （启动日志逐条写着"接上了"）。病因与画像那支笔一模一样：**笔有，场合与判据没有**。
   所以这里要回答的是：**给了那段话之后，她会不会动** —— 以及**会不会动过头**
   （每一轮都写一句"今天我学到…"就是另一种坏）。

⚠️ **默认只动副本**：她的家抄一份到 `data/instances/probe-home/`，写也只写副本。
   报告最后一行照实报**她真那一份家**（这一趟不该动它）。

用法（在 `core/` 那一层）：

```
.\\.venv\\Scripts\\python.exe tools\\probe_her_own_pens.py --repeat 3
.\\.venv\\Scripts\\python.exe tools\\probe_her_own_pens.py --no-hint      # 对照：不给那段话
```
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent
for _one in (str(_CORE), str(_CORE / "telyincore")):
    if _one not in sys.path:
        sys.path.insert(0, _one)

#: 那几支**记她自己**的笔（这一份探针只关心它们）。
THE_PENS = ("self_note", "unresolved_add", "self_observe_add", "abouther_propose",
            "self_account_add", "self_account_set", "abouther_settle")

#: 那一轮**什么都没被问到**（自己醒的）—— 与 `launch._the_quiet_words` 同一个形状。
ALONE_NOTE = "[system] 已安静 5 分钟。"

#: 造出来的"真发生了事"那一批 —— **她对某人许诺／被纠正**，是"以后要接上"的那类事。
#: ⚠️ 为什么用这个而不继续用"空的安静轮"：那几支笔记的是**"以后还用得上"的事**，
#:   而一场没有任何新事的安静本来就没有这种东西 —— 在那种输入上量出 0，
#:   证明不了判据没用（2026-10-17 真踩过这一脚）。
SCENARIO = (
    ("特琳，以后我发图不用每次都描述，等我问你再说。"),
    ("还有，别老回“我在”，听着像复读机。"),
    ("不用回我，你自己记着就行。"),
)

ALICE = "qq:1228600196"


def _a_group_message(text: str, *, message_id: int) -> dict[str, Any]:
    """一件真群来话的草稿（形状照 QQ 插件那一处）。"""
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    meta = {"conversation": "group:765749025", "message_id": message_id,
            "user_id": ALICE.split(":", 1)[1], "segment_types": ["text"],
            "has_user_text": True, "group_id": "765749025"}
    return {"type": "message", "ts": stamp, "source": "qq", "origin": "external",
            "text": f"[QQ群765749025·消息#{message_id}] {ALICE}：{text}",
            "sender_key": ALICE, "meta": meta,
            "dedup_key": f"qq:probe:group:765749025:{message_id}"}


def _the_scenario() -> list[dict[str, Any]]:
    return [_a_group_message(text, message_id=-100 - i)
            for i, text in enumerate(SCENARIO)]


def _probe_runtime(core: Path, bot: str) -> Path:
    """Read the explicit runtime owner; never create or guess a Bot partition."""
    from bot_registry import bot_id
    bot = bot_id(bot)
    base = core / "data"
    owner = json.loads((base / "bot-runtime-owner.json").read_text(encoding="utf-8"))
    baseline = bot_id(owner["baseline"])
    return base if bot == baseline else base / "bot-runtimes" / bot


def _the_home_copy(bot: str, destination: Path) -> Path:
    """把她那份家抄一份给探针（写也只写副本）。"""
    from bot_registry import bot_id
    source = _CORE.parent / "bots" / bot_id(bot) / "memory"
    box = destination / "memory"
    if box.exists():
        raise FileExistsError("fresh isolated probe home required")
    for one in sorted(source.rglob("*")):
        if not one.is_file() or "__pycache__" in one.parts or ".bak-" in one.name:
            continue
        where = box / one.relative_to(source)
        where.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(one, where)
    # `self_account_add` 写的是 `persona/她写的/自述.md`（家上一级）—— 那一层也备好
    shutil.copytree(source.parent / "persona", box.parent / "persona", dirs_exist_ok=False)
    (box.parent / "persona" / "她写的").mkdir(parents=True, exist_ok=True)
    (box.parent / "persona" / "一起写的").mkdir(parents=True, exist_ok=True)  # abouther 那本
    return box


def _self_note(home: Path, *, now: str, at_most: int = 10):
    """照 `launch.main` 里那一份 `_self_note` 的形状（这一份探针自己带的）。

    ⚠️ 为什么复制一份、不 import 那一份：它是 `main()` 里的闭包（够不到）。
       行为照它写：**一行**、**最多 10 条**、满了压最旧的、落 `悬着的事/selfnote.jsonl`。
    """
    from telyincore import memory

    def pen(text: str = "", **_rest: Any) -> str:
        said = str(text or "").strip()
        if not said:
            return "没写内容，这一条没记。"
        one_line = " ".join(said.split())
        box = home / "悬着的事" / "selfnote.jsonl"
        rows = list(memory.pending_of(home, proposed="selfnote"))
        kept = rows[-(at_most - 1):] if len(rows) >= at_most else rows
        if len(kept) != len(rows):
            box.parent.mkdir(parents=True, exist_ok=True)
            box.write_text("".join(one.as_line() + "\n" for one in kept),
                           encoding="utf-8", newline="")
        memory.remember_pending(home, one_line, proposed="selfnote",
                                ts=now, source="自己")
        return ("记下了：" + one_line + "。 这本现在 " + str(len(kept) + 1)
                + " 条（上限 " + str(at_most) + "，满了压最旧的）。")
    return pen


def _counts(home: Path) -> dict[str, int]:
    """那三本各多少条（与页面同一个来源口径：`selfmade` / `proposal` / `unfixed`）。"""
    from telyincore import memory
    from telyincore.unresolved import UnresolvedStore

    notes = proposals = 0
    for one in memory.pending_of(home):
        if str(getattr(one, "role", "")) == "selfnote":
            notes += 1
        else:
            proposals += 1
    try:
        gaps = len(UnresolvedStore(_CORE / "data" / "unresolved").list())
    except Exception:                        # noqa: BLE001
        gaps = 0
    return {"selfnotes": notes, "proposals": proposals, "gaps": gaps}


def _a_snapshot(home: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    for name in ("悬着的事", "画像", "做法"):
        box = home / name
        if box.is_dir():
            for one in sorted(box.rglob("*")):
                if one.is_file():
                    out[str(one.relative_to(home))] = one.stat().st_size
    persona = home.parent / "persona" / "她写的"
    if persona.is_dir():
        for one in sorted(persona.glob("*.md")):
            out["persona/她写的/" + one.name] = one.stat().st_size
    return out


class _TheProbe:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.core = _CORE
        import tempfile
        from bot_registry import bot_id
        bot_id(args.bot)
        self.workspace = Path(tempfile.mkdtemp(prefix='telyin-probe-'))
        self.home = _the_home_copy(args.bot, self.workspace)
        self.root = self.workspace / "runtime"
        # ⚠️⚠️ **每趟清空**（2026-10-17 真踩到）：事件库跨趟留着时，同一批草稿的
        #   `dedup_key` 与上一趟那条撞上 ⇒ `PluginIngress.append` **不推**任何东西
        #   （`store.append` 交回的是旧那一条，游标没往前走）⇒ 泵一直空转，
        #   报告写"一次回答都没有"。第一趟能跑、后面全哑，就是这个原因。
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
        self.said: list[str] = []
        self.console: Any = None
        self.mark = 0
        self.whole = 0
        self.pushed = False
        self.tools: list[Any] = []
        self.answered = False

    async def _assemble(self) -> Any:
        import launch
        from telyincore import memory
        from telyincore.console import Console
        from telyincore.engine import Engine
        from telyincore.events import EventStore
        from telyincore.plugin_ingress import PluginIngress
        from telyincore.identity import IdentityBook
        from telyincore.tools import build_table
        from telyincore.unresolved import UnresolvedStore

        engine = Engine(self.core / "data")
        memory.lay_out(self.home)
        book = IdentityBook(self.home / "身份绑定.json")
        store = EventStore(self.root / "events")
        # 插件入口（与 `launch._bind_plugin_ingress` 同一个位置）—— `--then-alone`
        # 那一档要靠它把"真发生了事"的那一批投进来。
        ingress = PluginIngress(store)
        self.ingress = ingress
        # ⚠️⚠️ **必须把会话带进来**：独处那一轮只有一条 `[system] 已安静…`，
        #   若会话里**一个 user 都没有**，引擎会当场 400
        #   （`input must provide a user query when no previous response contains one`）。
        #   生产里她能醒，靠的正是"会话里本来就有历史" ⇒ 这里照它来（每趟重抄一份）。
        src = _probe_runtime(self.core, self.args.bot) / "session-main.jsonl"
        if src.is_file():
            shutil.copy2(src, self.root / "session-main.jsonl")
        console = Console(write=self.said.append)
        self.console = console

        def clock() -> tuple[str, str]:
            now = datetime.now()
            return now.strftime("%Y%m%d-%H%M%S"), now.strftime("%Y-%m-%d")

        handlers: dict[str, Any] = dict(memory.handlers(
            self.home, clock=clock,
            profile_context=lambda: launch._profile_context_for_batch(
                book, console._active_batch),
            profile_targets=lambda: launch._group_profile_targets(
                book, console._active_batch),
            self_source="她自己",
        ))
        handlers.update(__import__(
            "telyincore.unresolved", fromlist=["handlers"]).handlers(
                UnresolvedStore(self.root / "unresolved")))
        handlers.update(launch.the_self_account_handlers(self.home, clock=clock))
        handlers.update(launch.the_abouther_handlers(self.home, clock=clock))
        handlers["self_note"] = _self_note(self.home, now=clock()[0])
        # ⚠️ **先摆一条已有的做法**（`--seed-skill`）：探针每趟清空副本 ⇒
        #    不播种的话她一趟里只写一条，那条"你记过这几条"永远不会出现。
        if self.args.seed_skill:
            box = self.home / "做法"
            box.mkdir(parents=True, exist_ok=True)
            (box / (self.args.seed_skill + ".md")).write_text(
                "（探针摆的一条旧做法：有人发图/发表情时，别急着描述。）",
                encoding="utf-8", newline="")
        # ⚠️ `finish_turn` 这一格探针原来没接 ⇒ 她一想收工就撞 `NotImplementedError`
        #    （回执写着"还没接上"），那一轮也就没真的收住。照 `launch` 那一份补上。
        handlers["finish_turn"] = lambda reason="", **_rest: (
            "收工：这一轮到此为止（" + str(reason) + "）")

        declarations = launch._profile_tool_declarations(list(launch.DECLARED_TOOLS))
        config = launch._DeclaresTools(
            engine.live(), launch._the_sendable_declarations(declarations, handlers))
        self.tools = list(config.tools)
        table = launch._one_speech_per_turn(build_table(declarations, handlers))

        from telyincore.dispatch import Dispatcher, make_run_tools

        async def run_tools(calls: Any) -> Any:
            return await make_run_tools(Dispatcher(table))(calls)

        def identity_hint() -> str:
            if self.args.no_hint:
                return ""
            return launch._her_own_pens_hint(**_counts(self.home))

        async def read() -> Any:
            from telyincore.console import CHECK_THE_BUS
            # ⚠️ **第一批在"读"这一下投**（照 `group_portrait_probe.py` 那个走了很多遍的
            #    形状）：在 `run()` 之前投、或刚起泵就投，都撞过时序 —— 那一趟会一句
            #    都不发，而报告写"一次回答都没有"（真踩到过两次）。
            if self.args.then_alone and not self.pushed:
                self.pushed = True
                for one in _the_scenario():
                    self.ingress.append(one)
            await asyncio.sleep(0.05)
            return CHECK_THE_BUS

        console.assemble(
            root=self.root, config=config,
            persona_root=self.workspace / "persona",
            event_store=store, memory_handlers=handlers, read=read,
            identity_hint=identity_hint, run_tools=run_tools,
            # ⚠️ **钥匙要递**（2026-10-17 补）：不递的话这一层自己造的那条 transport
            #    一颗鉴权头都不带 ⇒ 换成在线那条档案（DeepSeek 那种 `chat`）当场 401，
            #    而报告只会写"一次回答都没回来"。
            key_headers=launch.the_key_headers(engine))
        from telyincore.ingress_shutdown import IngressShutdown
        console.bind_ingress_shutdown(IngressShutdown(ingress))
        ingress.bind(console.bus, asyncio.get_running_loop())
        return console

    async def run(self) -> int:
        self._runner_started = False
        try:
            return await self._run()
        except BaseException as primary:
            if not self._runner_started:
                errors = []
                ingress = getattr(self, 'ingress', None)
                if ingress is not None:
                    try: ingress.begin_shutdown()
                    except BaseException as error: errors.append(error)
                console = getattr(self, 'console', None)
                if console is not None:
                    try:
                        await console.stop_and_drain(deadline=asyncio.get_running_loop().time()+30.0)
                    except BaseException as error: errors.append(error)
                if ingress is not None:
                    try:
                        state = ingress.shutdown_state()
                        if (state['accepting'] or state['pending'] or state['buffered']
                                or state['failed'] or state['drained'] is not True):
                            raise RuntimeError('probe ingress responsibility unresolved')
                    except BaseException as error: errors.append(error)
                BaseException.__setattr__(primary, 'probe_owner', self)
                if errors:
                    BaseException.__setattr__(primary, 'probe_cleanup_errors', tuple(errors))
            raise

    async def _run(self) -> int:
        console = await self._assemble()
        before = _a_snapshot(self.home)
        counts = _counts(self.home)
        print(f"[探针] {'先出事再独处' if self.args.then_alone else '独处那一轮'}；"
              f"三本：留下的 {counts['selfnotes']} ／ "
              f"悬着的 {counts['proposals']} ／ 没放下的 {counts['gaps']}；"
              f"提示 {'不给（对照）' if self.args.no_hint else '给'}")
        self.mark = len(console.session.records())
        self.whole = self.mark      # 这一趟的起点 —— 报告从这儿算（两轮都算）
        # ⚠️ **先投、再起泵**（2026-10-17 修）：先起泵再投批会撞时序 ——
        #    泵的第一跳可能落在"批还没进总线"那一刻，那一趟就一句都不发、
        #    报告还写"一次回答都没有"（真踩到过一次）。
        if not self.args.then_alone:
            console._inject_a_note(ALONE_NOTE, "tick", "internal",  # noqa: SLF001
                                   source="heartbeat")
        runner = asyncio.create_task(console.run())
        self._runner_started = True
        timed_out = False
        try:
            if self.args.then_alone:
                if not await self._until_she_moved(self.args.timeout):
                    raise asyncio.TimeoutError
                print("  （第一轮落定）")
                self.mark = len(console.session.records())
            # 第二轮：那一响心跳（独处）—— 这一条必须在泵跑起来之后投
            console._inject_a_note(ALONE_NOTE, "tick", "internal",  # noqa: SLF001
                                   source="heartbeat")
            if not await self._until_she_moved(self.args.timeout):
                raise asyncio.TimeoutError
        except asyncio.TimeoutError:
            timed_out = True
            print(f"（{self.args.timeout} 秒里一次回答都没有 —— 不算数）", file=sys.stderr)
        finally:
            primary = sys.exception()
            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError as stopped:
                if getattr(stopped, '_console_shutdown_errors', ()):
                    if primary is not None:
                        primary.probe_cleanup_errors = tuple(stopped._console_shutdown_errors)
                        primary.probe_runner = runner
                    else:
                        error = RuntimeError('probe cleanup incomplete')
                        error.probe_cleanup_errors = tuple(stopped._console_shutdown_errors)
                        error.probe_runner = runner
                        raise error from stopped
            except BaseException as cleanup:
                if primary is None:
                    cleanup.probe_runner = runner
                    raise
                primary.probe_cleanup_errors = (cleanup,)
                primary.probe_runner = runner
        self._report(before)
        return 0 if self.answered and not timed_out else 1

    async def _until_she_moved(self, seconds: float | None = None) -> bool:
        limit = int((seconds or self.args.timeout) * 20)
        for _ in range(limit):
            await asyncio.sleep(0.05)
            fresh = self.console.session.records()[self.mark:]
            if any(getattr(one, "role", "") == "assistant"
                   and str(getattr(one, "kind", "")) in ("message", "function_call")
                   for one in fresh):
                await asyncio.sleep(0.8)
                self.answered = True
                return True
        return False

    def _report(self, before: dict[str, int]) -> None:
        print("\n" + "=" * 72)
        if not self.answered:
            print("⚠️ **这一趟不算数**：一次回答都没回来（引擎不可用／钥匙不对）。")
        # ⚠️ 从**这一趟的开头**算（`self.whole`）—— 用 `self.mark` 会把第一轮漏掉
        fresh = self.console.session.records()[self.whole:]
        pens_called = [str(getattr(one, "name", "")) for one in fresh
                       if getattr(one, "kind", "") == "function_call"
                       and str(getattr(one, "name", "")) in THE_PENS]
        print("她这一趟落进会话的动作：")
        for one in fresh:
            role = str(getattr(one, "role", ""))
            kind = str(getattr(one, "kind", ""))
            name = str(getattr(one, "name", "") or "")
            text = str(getattr(one, "text", "") or "")
            mark = "  ★ 她动了那几支笔" if name in THE_PENS else ""
            print(f"  · [{role}/{kind}{('/' + name) if name else ''}] {text[:400]}{mark}")
        print(f"\n★ 那几支笔动了 {len(pens_called)} 次"
              + (f"：{'、'.join(pens_called)}" if pens_called else "（一次都没动）"))
        after = _a_snapshot(self.home)
        print("\n副本家里的变化：")
        moved = False
        for key in sorted(set(before) | set(after)):
            was, now = before.get(key, 0), after.get(key, 0)
            if was != now:
                moved = True
                print(f"  · {key}: {was} → {now} 字节")
        if not moved:
            print("  （一个字都没动）")
        real = _a_snapshot(_CORE.parent / "bots" / self.args.bot / "memory")
        print("她**真**那一份家（这一趟不该动它）："
              + "；".join(f"{k} {v} 字节" for k, v in sorted(real.items())
                          if "悬着的事" in k or "她写的" in k) or "（空）")
        print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="独处那一轮她那几支笔记她自己")
    parser.add_argument("--no-hint", action="store_true", help="对照：不给那段话")
    parser.add_argument("--seed-skill", default="", metavar="名字",
                        help="先在副本里放一条已有的做法；用来量她看见已有名字之后，"
                             "是改那一条还是另立一条")
    parser.add_argument("--then-alone", action="store_true",
                        help="先投一批**真发生了事**的群来话（有人对她提要求），"
                             "再让那一响心跳叫醒她 —— 这才符合那几支笔的用途")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--bot", default="Telyin")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)
    failed = False
    for index in range(max(1, args.repeat)):
        if args.repeat > 1:
            print(f"\n########## 第 {index + 1} ／ {args.repeat} 趟 ##########")
        failed = asyncio.run(_TheProbe(args).run()) != 0 or failed
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
