"""**画像自主性探针** —— 把一批（造的或回放的）群来话喂给真模型，看她动不动那支笔。

⚠️ **它为什么存在**（设计文档 25「画像现状复查」那一段的下一步）：
   那一天群里 54 条真来话、当天 `profile_add` **0 次**。而那之后的两处提示改动
   （`launch.py` 03:58 那一版：账号级"尚无可用群画像观察" ＋ "只记不开口"那一路）
   **从没有一次真请求带过**（进程比改动早）。
   ⇒ "她会不会写"这件事**只能拿真模型量**：接线那几条判据
   （`tests/test_group_portrait_autonomy.py`）证得了"提示到了、工具在手、写进去归属对"，
   **证不了她动不动手**。

⚠️ **它走的是生产那一条路**（不是另拼一份请求）：
   `Console.assemble()` 装那一套 → 插件入口 `ingress.append(draft)` 投一件
   **形状与 QQ 插件造的一模一样**的群来话 → **真模型** → **真那张表**。
   ⇒ 量到的"她调了什么"就是生产里会发生的那件事。

⚠️ **默认不动她的东西**：会话／事件库抄一份到 `data/instances/probe-Telyin/`，
   **她那一份家**也抄一份到 `data/instances/probe-home-*/`（每趟独立）
   ⇒ 那一支笔写的是**副本**，她真的画像目录只读不动（报告最后一行会照实报它）。
   回放只从事件库读，不回写。

用法（在 `core/` 那一层跑）：

```
.\\.venv\\Scripts\\python.exe tools\\group_portrait_probe.py --list
.\\.venv\\Scripts\\python.exe tools\\group_portrait_probe.py \\
    --batch preference-worth-keeping --repeat 5
.\\.venv\\Scripts\\python.exe tools\\group_portrait_probe.py --batch solo-smalltalk --short
.\\.venv\\Scripts\\python.exe tools\\group_portrait_probe.py \\
    --batch preference-worth-keeping --forget-account qq:100000001
.\\.venv\\Scripts\\python.exe tools\\group_portrait_probe.py --batch solo-smalltalk --no-presence
.\\.venv\\Scripts\\python.exe tools\\group_portrait_probe.py --replay 2026-10-17T03:47 --short
.\\.venv\\Scripts\\python.exe tools\\group_portrait_probe.py \\
    --batch preference-worth-keeping --hint-file tools\\hints\\group-portrait-rubric.md
```
"""
from __future__ import annotations
from channel_view_policy import qq_group_policy as _boundary_group_policy

import argparse
import asyncio
import collections
import json
import shutil
import stat
import tempfile
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent
for _one in (str(_CORE), str(_CORE / "telyincore")):
    if _one not in sys.path:
        sys.path.insert(0, _one)

# Public fixtures use synthetic identifiers, never real accounts or rooms.
ROOM = "group:qq:100000004"
ALICE = "qq:100000001"
BOB = "qq:100000002"
BOT = "100000003"

#: 那几批**造出来的场景** —— 每条都照真实群聊里出现过的形状写
#: （别写"教科书式的可记录事实"，那会把探针变成"喂她一句该记的话"）。
BATCHES: dict[str, dict[str, Any]] = {
    "solo-rougelike": {
        "why": "单人发言、内容是稳定偏好（这两天在打肉鸽、卡在第三层）",
        "messages": [
            (ALICE, "我这俩天开了个肉鸽的", None),
            (ALICE, "打到第三层，打不过了。", None),
        ],
    },
    "solo-smalltalk": {
        "why": "单人发言、只有短感叹（**不**该记）",
        "messages": [
            (ALICE, "我要昏头了。", None),
            (ALICE, "唉。", None),
        ],
    },
    "two-speakers-game": {
        "why": "两人同批各说各的（乙的话不许记到甲名下）",
        "messages": [
            (ALICE, "我这两天在玩脸白周报那个整合包", None),
            (BOB, "那个我玩过，第三层有个坑", None),
        ],
    },
    "side-chat-worth-keeping": {
        "why": "**旁听**（没人问她）而话里真有一条关于对方的稳定事实",
        "messages": [
            (ALICE, "我白天上班，晚上才开游戏", None),
            (BOB, "我也是，下班回来就打两把，打到两三点", None),
        ],
    },
    "preference-worth-keeping": {
        "why": "旁听，话里是**偏好**（不吃辣）—— 这类该不该记，看的是她",
        "messages": [
            (ALICE, "我不吃辣的，一点都碰不了", None),
            (ALICE, "上回那个火锅我就涮清水", None),
        ],
    },
    "preference-with-example": {
        "why": "长期口味与一次购买事例混合；事例不能升级成固定饮品偏好",
        "messages": [
            (ALICE, "我喝饮料只喝无糖的", None),
            (ALICE, "昨天路过买了瓶无糖乌龙茶", None),
        ],
    },
    "one-off-only": {
        "why": "只有一次经历，没有明确的长期偏好或持续结果",
        "messages": [(ALICE, "上回那个火锅我就涮清水", None)],
    },
    "explicit-habit": {
        "why": "明确的长期习惯是正例，不能被一次发言的过滤误伤",
        "messages": [(ALICE, "我每周日都去游泳，坚持好多年了", None)],
    },
    "durable-change": {
        "why": "搬家发生一次但有明确持续结果，不能把长期事实等同于重复行为",
        "messages": [(ALICE, "我已经搬到杭州了，以后就住这边了", None)],
    },
    "two-speakers-example": {
        "why": "甲的长期偏好与乙的一次代购分开，乙没有在自述饮用习惯",
        "messages": [
            (ALICE, "我一直不喝咖啡", None),
            (BOB, "今天帮同事买了杯咖啡，我自己没喝", None),
        ],
    },
    "conditional-preference": {
        "why": "稳定偏好中的条件与否定必须保留，不能压成笼统标签",
        "messages": [(ALICE, "我只有周末下午才喝咖啡，工作日从来不喝", None)],
    },
    "at-other": {
        "why": "明确 @ 另一个成员（此刻不是在问她）",
        "messages": [
            (ALICE, "明天几点起", [BOB]),
        ],
    },
    "probe-her": {
        "why": "明确 @ 本 Bot 的一句（她该回话）",
        "messages": [
            (ALICE, "我睡不着", [BOT]),
        ],
    },
    # ── 知识库那一组（2026-10-17 加的：量"她到底会不会调 kb_search"）──────────
    #: ⚠️ 那两句的答案**就在她的库里**（`memory/知识/词典/成语-常用.md` 有这两个词条；
    #:    `memory/知识/图书馆.md` 明写着"这一份走 kb_search 按需查"，
    #:    而常驻的 `persona/你定的/世界.md` **没有**书架／烛火／软榻那几个字）。
    "kb-word": {
        "why": "**词义**问题，库里真有这个词条（成语-常用.md 有「九曲回肠」）",
        "messages": [
            (BOB, "「九曲回肠」是什么意思？", [BOT]),
        ],
    },
    "kb-own-world": {
        "why": "**她自己的细节**，只在 知识/图书馆.md 里（常驻那份没写书架／烛火／软榻）",
        "messages": [
            (BOB, "你书房里都有什么？", [BOT]),
        ],
    },
    "kb-song-real": {
        "why": "**回放真事**：10-16 阿炜就是这么问的，那次她连调了 9 次 kb_search",
        "messages": [
            (BOB, "トマトの局限性（番茄的局限性）这首歌你可以解析一下吗", None),
        ],
    },
    "kb-new-topic": {
        "why": "**她历史里没有过**的陌生话题（真回放那条已被污染：她记得'这首不合适谈'）",
        "messages": [
            (BOB, "《夜行人》这首歌你可以解析一下吗", None),
        ],
    },
    "kb-not-needed": {
        "why": "一句情绪话（**不该**去查库）",
        "messages": [
            (BOB, "我要昏头了。", [BOT]),
        ],
    },
    # ── `kb_read` 那一组（2026-10-17 加的：量"她会不会翻开看全"）────────────
    #: ⚠️ 那两问**只能靠 `kb_read` 答**：`kb_search` 只给"命中那一条"（一节），
    #:    要前后几条 ／ 要看名单，非翻不可。
    "kb-read-more": {
        "why": "**明确要更多**：要那一条前后的几条 —— 只有 kb_read 做得到",
        "messages": [
            (BOB, "「实事求是」是什么意思？顺便把那本成语词典里它前后的几条也念给我听听", [BOT]),
        ],
    },
    "kb-read-list": {
        "why": "**要看名单**：那本词典里都有什么 —— 一节装不下",
        "messages": [
            (BOB, "你那本成语词典收了哪些词？挑几个念给我听听", [BOT]),
        ],
    },
    # ★ **2026-10-17 加的对照批**：与 `kb-read-list` **只差措辞** —— 那一批问"**你**那本词典"
    # （她那头读到的是"我的书"），这一批问"你**知识库里**那一份"（用管路自己的词）。
    # ⚠️ **它要判的是一条假设**：她收到正确回执却答"没有那本词典"，
    #    究竟是"**库里的文件 ≠ 我的书**"那个措辞鸿沟，还是别的。
    #    两批**同一件事、同一个真文件**，只有"叫它什么"不同 —— 差别只能出在措辞上。
    "kb-name-framing": {
        "why": "**对照批**：同一件事换成知识库的说法问 —— 判「措辞鸿沟」那条假设",
        "messages": [
            (BOB, "你知识库里那一份成语词典收了哪些词？挑几个念给我听听", [BOT]),
        ],
    },
    "kb-read-named": {
        "why": "**点名那一本 ＋ 要一个范围**（名字她在回执里见过）—— 非 kb_read 不可",
        "messages": [
            (BOB, "词典-成语那一本我记得挺厚 —— 从头念二十条给我听听", [BOT]),
        ],
    },
    "kb-read-verify": {
        "why": "**要她核一个词在不在库里**（上一趟她就是在这类问题上编的）",
        "messages": [
            (BOB, "「灯下看花」算成语吗？你那本成语词典里查得到它吗？", [BOT]),
        ],
    },
}


def _draft(account: str, text: str, mentioned: list[str] | None = None,
           *, message_id: int | None = None) -> dict[str, Any]:
    """一件 QQ 群来话的**草稿** —— 形状照 `core-infinity-qq/index.py` 那一处。"""
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    group = ROOM.rsplit(":", 1)[1]
    meta: dict[str, Any] = {
        "conversation": "group:" + group,
        "message_id": (message_id if message_id is not None
                       else -(abs(hash(text)) % 10**9)),
        "user_id": account.split(":", 1)[1],
        "segment_types": ["text"],
        "has_user_text": True,
        "group_id": group,
        "self_id": BOT,
    }
    if mentioned is not None:
        meta["mention_targets"] = list(mentioned)
        meta["mentions_self"] = BOT in mentioned
    return {
        "type": "message", "ts": stamp, "source": "qq", "origin": "external",
        "text": f"[QQ群{group}·消息#{meta['message_id']}] {account}：{text}",
        "sender_key": account, "meta": meta,
        "dedup_key": f"qq:{BOT}:group:{group}:{meta['message_id']}",
    }


def _the_batch_of(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.draft:
        out = []
        for one in args.draft:
            account, _, text = one.partition("=")
            out.append(_draft(account.strip(), text))
        return out
    if args.replay:
        return _from_the_corpus(args.replay)
    return [_draft(row[0], row[1], row[2]) for row in BATCHES[args.batch]["messages"]]


def _inside_copy(box: Path, name: str) -> Path:
    """Reject absolute/traversal/ADS paths and every existing reparse component."""
    from pathlib import PureWindowsPath
    relative = Path(name)
    windows = PureWindowsPath(name)
    if (not name or relative.is_absolute() or windows.drive or windows.root
            or ".." in relative.parts or ".." in windows.parts or ":" in name):
        raise ValueError("unsafe overlay path")
    base = box.absolute()
    candidate = base / relative
    for part in (base, *base.parents, *candidate.parents, candidate):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or
                getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("overlay reparse point refused")
    if candidate.resolve() == base.resolve() or not candidate.resolve().is_relative_to(base.resolve()):
        raise ValueError("overlay escaped copy")
    return candidate


def _lay_over(box: Path, source: Path) -> None:
    """ **把一份改写过的样子盖在副本上**（`--overlay <目录>`）。

    ⚠️ **它治的是什么**：量"换一种命名 ／ 换一种开头会不会不一样"时，
       要动的是知识那一棵树 —— 而那是**她的数据**。
       ⇒ 先照她那一份抄一个副本，再把改写过的几份**盖上去** ⇒ 量的是**副本**，
         她真那一份家**一个字节不动**。
    ⚠️ **两种动法**：
       · 同名文件 ⇒ 直接覆盖（改开头那种）；
       · `_DELETE.txt`（每行一个**相对路径**）⇒ 从副本里删掉（**改名**就是
         "加新的 ＋ 把旧的列在这儿"）。
    ⚠️ **盖不动就照实说**（不留一句"盖过了"）：列出没盖上的那些。
    """
    if not source.is_dir():
        print(f"（--overlay 那个目录不在：{source}）", file=sys.stderr)
        return
    # Validate the complete deletion manifest before the first mutation.
    drops = _inside_copy(source, "_DELETE.txt")
    names = [line.strip() for line in drops.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("#")] if drops.is_file() else []
    for name in names:
        _inside_copy(box, name)
    laid, missed = 0, []
    for one in sorted(source.rglob("*")):
        if not one.is_file() or "_DELETE.txt" in one.name:
            continue
        _inside_copy(source, str(one.relative_to(source)))
        where = _inside_copy(box, str(one.relative_to(source)))
        try:
            where.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(one, where)
            laid += 1
        except Exception:                        # noqa: BLE001
            missed.append(str(one.relative_to(source)))
    dropped = 0
    for name in names:
        gone = _inside_copy(box, name)
        if gone.is_file():
            gone.unlink()
            dropped += 1
        else:
            missed.append("（要删的没找到）" + name)
    print(f"[overlay] 盖上 {laid} 份、删掉 {dropped} 份"
          + (f"；没盖上的：{'、'.join(missed)}" if missed else ""))


def _a_home_copy(home: Path, *, forget: Sequence[str] = ()) -> Path:
    """把**她那一份家**里读得动的那几样抄一份给探针（抄到它自己那个 root 底下）。

    ⚠️ **为什么整份家都要抄**：只抄会话那一份的话，`memory.handlers()` 读的是**她真那一份**
       ⇒ `profile_add` 会往她真画像里写（2026-10-17 真踩到：第一次跑 `--home` 之前
       那几趟的落点就是她的目录）。抄一份之后**写也只写进副本**。
    ⚠️ **它读，不写她的家**；抄不动某一项就跳过（不拦这一趟）。
    ⚠️ **`forget` 那一串**：把 `身份绑定.json` 里**这些账号**从绑定里摘掉
       —— 用来量「**未绑定** ⇒ 独立 `QQ-<账号>` 键」那一条路
       （她真那一份绑定本一个字不动）。
    """
    target = Path(home)
    if not target.is_dir():
        raise ValueError("source memory directory is missing; refusing original path")
    # Every invocation owns a fresh directory, including an empty source.
    parent = _CORE / "data" / "instances"
    parent.mkdir(parents=True, exist_ok=True)
    box = Path(tempfile.mkdtemp(prefix="probe-home-", dir=parent))
    for one in sorted(target.rglob("*")):
        if not one.is_file() or "__pycache__" in one.parts or ".bak-" in one.name:
            continue
        where = box / one.relative_to(target)
        where.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(one, where)
    if forget:
        _forget_accounts(box / "身份绑定.json", forget)
    return box


def _forget_accounts(where: Path, accounts: Sequence[str]) -> None:
    """把 `accounts` 从那一份绑定本里摘掉（摘不动 ⇒ 明说，不静悄悄当没这回事）。

    ⚠️ 那一份的形状是 `{"people": {<person_id>: {"accounts": [...], ...}}}` ——
       **`people` 是字典、不是表**（2026-10-17 第一版按表遍历，摘掉 0 条还照跑，
       量出来的仍是"已绑定"那条路）。
    """
    if not where.is_file():
        print(f"（{where.name} 不在，这一趟仍是'已绑定'那条路）", file=sys.stderr)
        return
    try:
        book = json.loads(where.read_text(encoding="utf-8"))
    except ValueError as why:
        print(f"（{where.name} 读不动：{why}）", file=sys.stderr)
        return
    wanted = {str(one) for one in accounts}
    folk = book.get("people")
    hit = 0
    if isinstance(folk, dict):
        for person in folk.values():
            if not isinstance(person, dict):
                continue
            before = [str(one) for one in (person.get("accounts") or [])]
            keep = [one for one in before if one not in wanted]
            hit += len(before) - len(keep)
            person["accounts"] = keep
    where.write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding="utf-8")
    if not hit:
        print(f"⚠️ 绑定本副本里**没有**这些账号可摘：{'、'.join(sorted(wanted))}"
              "（那一趟量的仍是'已绑定'那条路）", file=sys.stderr)
    else:
        print(f"[探针] 绑定本副本里摘掉了 {hit} 条账号绑定："
              f"{'、'.join(sorted(wanted))}")


def read_a_hint_file(where: str) -> str:
    """读那一份**实验提示**（`--hint-file`）—— 里面的 `{…}` 占位按下面那张表填。"""
    body = Path(where).read_text(encoding="utf-8")
    return (body.replace("{ROOM}", ROOM).replace("{ALICE}", ALICE)
            .replace("{BOB}", BOB).replace("{BOT}", BOT))


def _from_the_corpus(when: str) -> list[dict[str, Any]]:
    """**回放真来话**：事件库里那个时刻前缀下的 QQ 群消息，**原样**投进去。

    ⚠️ 只挑 `type=message` ＋ `source=qq` ＋ `meta.conversation` 是群的那些；
       `text`／`sender_key`／`meta` 一个字都不改（改了就不是回放）。"""
    root = _CORE / "data" / "events"
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("events-*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                got = json.loads(line)
            except ValueError:
                continue
            if got.get("type") != "message" or got.get("source") != "qq":
                continue
            meta = got.get("meta") if isinstance(got.get("meta"), dict) else {}
            if not str(meta.get("conversation") or "").startswith("group:"):
                continue
            if not str(got.get("ts", "")).startswith(when):
                continue
            out.append({key: got[key] for key in
                        ("type", "ts", "source", "origin", "text", "sender_key", "meta")
                        if key in got})
    return out


class _TheProbe:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.core = _CORE
        self.bot = args.bot
        #: **探针自己那一套状态** —— 默认是她那一份的**一份拷贝**：
        #: 会话要带着（不然她没有前文），而新落的那些**不许写回她的正本**。
        root = self._a_scratch_copy(Path(args.root).resolve())
        self.root = root
        #: 她那一份家（`bots/<谁>/memory`）—— **提示里"有没有记过谁"就是从这儿读的**。
        #: ⚠️ 抄一份到探针那个 root 底下（`probe-home/`）：那一支笔写的是**副本**
        #:    （2026-10-17 踩过：不抄的话 `profile_add` 会落进她真画像）。
        #: ⚠️ 提示读的也是副本 —— 语义没变（它本来就是同一份），而她的家只读不写。
        self.home = _a_home_copy(
            _CORE.parent / "bots" / args.bot / "memory",
            forget=list(args.forget or [])) if not args.live_root else (
                _CORE.parent / "bots" / args.bot / "memory")
        # ★ **把一份"改写过的样子"盖上去**（2026-10-17）—— 见 `--overlay`：
        #   量"换一种写法会不会不一样"时，**改的是副本**，她真那一份家一个字不动。
        if getattr(args, "overlay", None):
            _lay_over(self.home, Path(args.overlay).resolve())
        #: 那一支笔这一趟**该往哪儿写**：不给 `--home` ⇒ 就是上面那一份副本家。
        self.write_home = (Path(args.home).resolve() if args.home else self.home)
        self.said: list[str] = []
        #: **这一趟是不是"时间到了还没收尾"** —— 与"一次都没回来"分两句说（见 `_report`）。
        self.timed_out = False
        self.batch_finished = False
        self.failures_in_batch = 0
        self.valid_measurement = False
        #: **这一趟已经发出过几轮请求**（`--requests` 那一档用；没开就是空表）。
        self._the_requests: list[int] = []
        self.console: Any = None
        self.mark = 0

    def _a_scratch_copy(self, source: Path) -> Path:
        """把她那一套状态抄一份给探针用（抄不成 ⇒ 就用空的，不拦路）。"""
        if self.args.live_root:
            return source
        target = source / "instances" / ("probe-" + self.bot)
        target.mkdir(parents=True, exist_ok=True)
        for name in ("session-main.jsonl", "consolidation.jsonl", "心跳.json",
                     "唤醒.json", "整理.json"):
            one = source / name
            if one.is_file():
                shutil.copy2(one, target / name)
        events = source / "events"
        if events.is_dir():
            (target / "events").mkdir(exist_ok=True)
            for one in sorted(events.glob("events-*.jsonl"))[-3:]:
                shutil.copy2(one, target / "events" / one.name)
        return target

    def _an_engine_root(self) -> Path:
        """探针用哪一份 `engine.json` —— 默认就是她那一个 `data/`。

        ⚠️ **`--profile` 不写她的盘**：它把那一份抄到 `data/instances/probe-engine/`
           再把 `current` 改成那一条 ⇒ 这一趟换脑，而她的配置一个字都没动
           （本机那条引擎挂掉时，这一格就是"换个脑接着量"的路）。
        """
        if not self.args.profile:
            return self.core / "data"
        source = json.loads((self.core / "data" / "engine.json")
                            .read_text(encoding="utf-8"))
        if self.args.profile not in (source.get("profiles") or {}):
            raise SystemExit(f"没有叫 {self.args.profile!r} 的那一条档案")
        source["current"] = self.args.profile
        box = self.core / "data" / "instances" / "probe-engine"
        box.mkdir(parents=True, exist_ok=True)
        (box / "engine.json").write_text(
            json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")
        for name in ("engine.env",):
            one = self.core / "data" / name
            if one.is_file():
                shutil.copy2(one, box / name)
        return box

    async def _assemble(self, drafts: list[dict[str, Any]]) -> Any:
        """照 `launch.main` 的次序装这一趟用得到的那几样（不铺记忆、不投经历过）"""
        import launch
        from telyincore import memory
        from telyincore.console import Console
        from telyincore.engine import Engine
        from telyincore.events import EventStore
        from telyincore.identity import IdentityBook
        from telyincore.plugin_ingress import PluginIngress
        from telyincore.tools import build_table

        engine = Engine(self._an_engine_root())
        memory.lay_out(self.home)
        book = IdentityBook(self.home / "身份绑定.json")
        store = EventStore(self.root / "events")
        #: **事件库的高水位**（装配那一刻）—— 报告里的"这一趟的回执"以它为界，
        #: 免得把**昨天**那些回执当成这一趟的（那会把"没回来"读成"她做了"）。
        self.receipt_cursor = store.latest_cursor()
        ingress = PluginIngress(store)
        console = Console(write=self.said.append)
        self.console = console
        self.drafts = list(drafts)
        self.pushed = False

        handlers = memory.handlers(
            self.home,
            clock=lambda: (datetime.now().strftime("%Y%m%d-%H%M%S"),
                           datetime.now().strftime("%Y-%m-%d")),
            profile_context=lambda: launch._profile_context_for_batch(
                book, console._active_batch),
            profile_targets=lambda: launch._group_profile_targets(
                book, console._active_batch),
            self_source="她自己",
        )
        if self.write_home != self.home:
            # 那一支笔改指到别处（提示照旧读她**真**那一份家 —— 那是"她知道什么"）。
            self.write_home.mkdir(parents=True, exist_ok=True)
            pen = memory.handlers(
                self.write_home,
                clock=lambda: (datetime.now().strftime("%Y%m%d-%H%M%S"),
                               datetime.now().strftime("%Y-%m-%d")),
                profile_context=lambda: launch._profile_context_for_batch(
                    book, console._active_batch),
                profile_targets=lambda: launch._group_profile_targets(
                    book, console._active_batch),
                self_source="她自己",
            )
            handlers = dict(handlers)
            for name in ("profile_add", "profile_read", "relations_read", "self_note"):
                if name in pen:
                    handlers[name] = pen[name]
        # ⚠️⚠️ **`finish_turn` 必须在这里接上** —— 2026-10-17 量到过它的后果：
        #    探针原先只递 `memory_handlers`，于是她一调 `finish_turn` 就落到那条
        #    "还没接上"的会抛接缝上（回执是 `[tool failed] NotImplementedError`）。
        #    而那**不是生产的样子**（`launch.py` 有一格专门接它）——
        #    收工那面旗立不起来，这一轮的走向就与真跑不一样了。
        #    ⚠️ 收工那面旗本身由 `ends_turn` 那一格驱动，这一格只负责"让那一步办到"
        #       （与 `launch.py` 那一处逐字同一条道理）。
        def _finish_turn(reason: str = "", **_rest: Any) -> str:
            return launch._the_finish_receipt(reason)

        handlers["finish_turn"] = _finish_turn
        self.handlers = handlers
        declarations = launch._profile_tool_declarations(list(launch.DECLARED_TOOLS))
        self.offline_sends = []
        if getattr(self.args, "offline_send", False):
            from tools.portrait_offline_send import declarations as offline_declarations
            substitutes = offline_declarations(lambda: console._active_batch, self.offline_sends)
            declarations.extend(substitutes)
            handlers.update({tool.name: tool.handler for tool in substitutes})
            print("[探针] QQ 文字发送使用离线替身；仅记录拟发送内容，绝不外发。")
        config = launch._DeclaresTools(
            engine.live(), launch._the_sendable_declarations(declarations, handlers))
        self.tools = list(config.tools)
        self.table = launch._one_speech_per_turn(build_table(declarations, handlers))
        from telyincore.dispatch import Dispatcher, make_run_tools

        dispatcher = Dispatcher(self.table)

        async def run_tools(calls: Any) -> Any:
            """**真表、真派发** —— 这一趟她动的手会真的落下去（落进它该落的那一处）。

            ⚠️ 不接这一格的话，模型那几次调用只会**记进会话**、不落地 ——
               那量出来的是"她想干什么"，**不是**"她干成了什么"。"""
            result = await make_run_tools(dispatcher)(calls)
            launch.the_receipts_into_the_ledger(
                store, result.receipts, now_iso=datetime.now().astimezone().isoformat())
            return result

        def identity_hint() -> str:
            batch = console._active_batch
            if not batch:
                return ""
            got = launch._identity_hint_for_batch(book, batch)
            if got:
                return got
            if self.args.hint_file:
                # **实验那一版提示**：整段换掉（含"先判断谁对谁说话"那些）
                # —— 只用来量"这一句换了，她动不动手"，不改生产那一句。
                return read_a_hint_file(self.args.hint_file)
            presence = (None if self.args.no_presence else
                        launch._group_profile_presence(self.home, book, batch))
            return launch._group_turn_hint(batch, console.session.records(), presence)

        async def read() -> Any:
            from telyincore.console import CHECK_THE_BUS
            if not self.pushed:
                self.pushed = True
                for one in self.drafts:
                    ingress.append(one)
            else:
                await asyncio.sleep(0.05)
            return CHECK_THE_BUS

        # **把这一趟真发出去的那一份抬头留下来**（`--dump`）——
        # ⚠️ 只有它能回答"那一句提示到底到没到她眼前"（别的都只是"函数交回了什么"）。
        # 取法：`console._head_cell()` 逐字就是出请求时插在会话前面那一串
        # （与 `session.outbound(head())` 同一个来源 —— 见 `_a_head_cell` 那一段）。
        def dump_the_head() -> None:
            if not self.args.dump:
                return
            try:
                rows = console._head_cell()             # noqa: SLF001 —— 见上面那一句
            except Exception as why:                    # noqa: BLE001
                print(f"（抬头读不到：{type(why).__name__} {why}）", file=sys.stderr)
                return
            where = Path(self.args.dump)
            where.parent.mkdir(parents=True, exist_ok=True)
            where.write_text(json.dumps(
                {"tools": self.tools, "head": rows}, ensure_ascii=False, indent=1),
                encoding="utf-8")
            hit = any("尚无可用的群画像观察" in str(one.get("content", "")) for one in rows)
            print(f"[探针] 抬头留在 {where}（{len(rows)} 条）；"
                  f"账号级画像状态那一段：{'在' if hit else '不在'}")

        # ★★ **把每一轮真发出去的那一份请求留下来**（`--requests`，2026-10-17）——
        #    ⚠️⚠️ **为什么非有它不可**：这一趟里有**两样东西只进请求、不进会话**：
        #      · 合成的抬头（`head`）；
        #      · **每轮现取的尾巴**（`the_tail` —— 软阈那句提醒现在就走它）。
        #    ⇒ 只看会话记录**永远看不到它们**，而"它到底有没有到她眼前"正是要靠它们回答的。
        #      （当天为此绕过两次弯：先误判"它没出现"，后来才发现是量法看不到。）
        #    ⚠️ 取法是**包一层 `generate`**：`round.py` 每一轮都是调它，而它收到的
        #      `messages` 就是那一轮逐字要发的那一份（含抬头与尾巴）。
        #    ⚠️ 递进去的仍是**原来那个**（包一层不改行为，只多记一笔）。
        the_generate = None
        if self.args.requests:
            # ⚠️ 不记请求时**递 `None`**（那是装配那一层自己的缺省 ⇒ 行为逐字不变）；
            #    要记时才包一层，而里面调的仍是**它自己那一个**
            #    （`telyincore.generate.generate`）—— 包一层只多记一笔。
            from telyincore.generate import generate as the_real_generate

            async def the_generate(cfg, messages, transport, **rest):   # noqa: ANN001
                try:
                    with Path(self.args.requests).open("a", encoding="utf-8") as fp:
                        fp.write(json.dumps(
                            {"轮": len(self._the_requests) + 1,
                             "messages": [dict(one) for one in messages]},
                            ensure_ascii=False) + "\n")
                    self._the_requests.append(1)
                except Exception as why:                # noqa: BLE001 —— 记不下不该带走这一轮
                    print(f"（请求留档失败：{type(why).__name__}）", file=sys.stderr)
                return await the_real_generate(cfg, messages, transport, **rest)

        console.assemble(
            root=self.root, config=config,
            persona_root=self.core.parent / "bots" / self.bot / "persona",
            event_store=store,
            linked_accounts=lambda account: launch._linked_accounts(book, account),
            channel=lambda: launch._channel_for_batch(console._active_batch),
            memory_handlers=handlers, read=read, identity_hint=identity_hint,
            # ⚠️⚠️ **这几颗鉴权头必须递**（2026-10-17 修）：装配那一层的缺省是
            #   `key_headers=None` ⇒ `HttpxTransport` **一颗头都不带**。
            #   本机引擎不看这个，所以这个问题**藏了一整天**；换成在线 API 就当场
            #   `401 Authentication Fails (governor)`（而"最小请求"那条手写的路是带头的
            #   ⇒ 会得出"钥匙没问题、探针有问题"这种最费时的形状）。
            #   ⇒ 与 `launch.main` 递的是**同一个来源**（`launch.the_key_headers`）。
            key_headers=launch.the_key_headers(engine),
            generate=the_generate,
            # ⚠️ `--short` ⇒ **不接这一格**：模型那几次调用只记进会话，
            #    一个字都不落盘、也一条都不发出去（对照那一趟专门用来"只想看她想干什么"）。
            run_tools=(None if self.args.short else run_tools),
            group_policy=_boundary_group_policy,
        )
        self.dump_the_head = dump_the_head
        # 插件入口要绑在**装配之后**（`launch._bind_plugin_ingress` 那一下的位置）——
        # ⚠️ 只有 `console_main` 有 `on_assembled` 那个钩子，而这里走的是
        #    "自己装、自己跑"：`assemble()` 已经把总线造好了，所以直接绑。
        ingress.bind(console.bus, asyncio.get_running_loop())
        return console

    async def run(self) -> int:
        drafts = _the_batch_of(self.args)
        if not drafts:
            print("（没有可投的来话）", file=sys.stderr)
            return 2
        console = await self._assemble(drafts)
        before = self._the_portraits()
        #: **这一趟开始之前会话里已经有几行**（轮询那一格用的近似切片）——
        #: 报告那一半走 `_fresh_rows` 的**多重集相减**（旧临时记录会被删，
        #: 行号从那以后就对不上了，见那一段）。
        self.mark = len(console.session.records())
        #: 整份会话的**起点快照**（报告按它做多重集相减）。
        self.before_rows = list(console.session.records())
        print(f"[探针] 投 {len(drafts)} 件；工具 {len(self.tools)} 件；"
              f"账号级画像状态 {'关' if self.args.no_presence else '开'}；"
              f"会话起点第 {self.mark} 行")
        runner = asyncio.create_task(console.run())
        got_anything = False
        try:
            got_anything = await asyncio.wait_for(
                self._until_batch_finished(console, runner), self.args.timeout)
        except asyncio.TimeoutError:
            self.timed_out = True
            print(f"（{self.args.timeout} 秒到点了 —— 这一趟**没等到它收尾**。"
                  f"⚠️ 这不是'她没动'：往上看那几行，可能已经跑了好几轮；"
                  f"要它自己收尾就把 `--timeout` 放大。"
                  f"往上看 `[http]`，503 是引擎没起来、401 是钥匙不对）", file=sys.stderr)
        finally:
            # ★ **批跑完之后再多等一会儿**（`--linger <秒>`，2026-10-17）——
            #   它量的是"**这一批之后，会不会自己再起一批**"。
            #   ⚠️ **为什么需要它**：有些东西是**批末才推出去的**（例如跑到第 N 轮那句提醒
            #      走 `bus.push` 进总线，而总线**只在批与批之间**被取走）⇒
            #      不等这一下，量到的是"一次都没出现"，而那个结论**是假的**。
            #   ⚠️ 它照旧会把这一段落进档（`_fresh_rows` 算的是"这一趟从头到现在新增的"）。
            if getattr(self.args, "linger", 0) and got_anything:
                print(f"（再多等 {self.args.linger} 秒 —— 看这一批之后它自己会不会再起一批）")
                await asyncio.sleep(self.args.linger)
            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError as stopped:
                if getattr(stopped, '_console_shutdown_errors', ()):
                    raise RuntimeError('probe cleanup incomplete') from stopped
        self.got_anything = got_anything
        self.dump_the_head()
        self._report(before)
        return 0 if self.valid_measurement and not self.timed_out else 1

    async def _until_batch_finished(self, console: Any,
                                    runner: asyncio.Task[Any]) -> bool:
        """Wait for the whole batch, not merely its first assistant action.

        The loop's processing flag spans every model/tool round in one batch.
        A first tool call may be followed by another model request; cancelling
        0.8 seconds after that call undercounts later profile_add/finish_turn.
        """
        saw_processing = False
        for _ in range(int(self.args.timeout * 20)):
            await asyncio.sleep(0.05)
            fresh = (self._fresh_rows(self.before_rows, console.session.records())
                     if hasattr(self, "before_rows") else console.session.records()[self.mark:])
            answered = any(str(getattr(one, "kind", "")) in ("message", "function_call")
                           and getattr(one, "role", "") == "assistant" for one in fresh)
            processing = bool(console.app.loop.processing)
            saw_processing |= processing
            if runner.done() and not runner.cancelled() and runner.exception():
                raise runner.exception()
            if not processing and (saw_processing or answered or runner.done()):
                self.batch_finished = True
                self.failures_in_batch = int(getattr(console.app.loop, "_failures_in_batch", 0))
                self.valid_measurement = answered and self.failures_in_batch == 0
                return answered
        return False

    def _the_portraits(self) -> dict[str, int]:
        """**探针那份副本家里**的画像 —— 她这一趟读的写的就是它（变化要在这儿量）。"""
        return self._a_boxes_portraits(self.home)

    def _the_written_portraits(self) -> dict[str, int]:
        return self._a_boxes_portraits(self.write_home)

    def _the_real_portraits(self) -> dict[str, int]:
        """**她真那一份家** —— 只用来证明这一趟**没有**动它。"""
        return self._a_boxes_portraits(_CORE.parent / "bots" / self.bot / "memory")

    @staticmethod
    def _a_boxes_portraits(home: Path) -> dict[str, int]:
        box = home / "画像"
        return {str(one.relative_to(box)): one.stat().st_size
                for one in sorted(box.glob("**/*.jsonl"))} if box.is_dir() else {}

    def _the_receipts(self) -> list[str]:
        """**这一趟**那些工具交回来的回执（事件库里**这一趟之后**新落的那几条）。

        ⚠️⚠️ **原来这里读的是整本事件库、再打印末六条** —— 于是一趟**什么都没回来**
           （引擎 503）的报告里，照样列着昨天的 `finish_turn` ／ 发群回执，
           看起来像"这一趟她动了好几下手"。那正是"把量不到读成她做了"。
        ⇒ 判据是**游标**：`assemble()` 那一刻记下事件库的高水位，
           只认在那之后落的（`EventStore.range()` 的每一条都带 `cursor`）。
        """
        from telyincore.events import EventStore
        try:
            rows = EventStore(self.root / "events").range()
        except Exception:                       # noqa: BLE001 —— 读不成就不报
            return []
        floor = getattr(self, "receipt_cursor", 0)
        return [str(getattr(one, "text", "")) for one in rows
                if str(getattr(one, "type", "")) == "tool-receipt"
                and int(getattr(one, "cursor", 0) or 0) > floor]

    @staticmethod
    def _a_row_key(one: Any) -> tuple:
        """一条会话记录的**身份**（多重集相减用的那一格）。"""
        return (str(getattr(one, "kind", "")), str(getattr(one, "role", "")),
                int(getattr(one, "ts_ms", 0) or 0),
                str(getattr(one, "name", "") or ""), str(getattr(one, "text", "") or ""))

    @classmethod
    def _fresh_rows(cls, before: Any, after: Any) -> list:
        """**`after` 里比 `before` 多出来的那些**（按记录的**多重集**相减）。

        ⚠️⚠️ **为什么不能拿"起点行号 + 切片"**（2026-10-17 量到的第二处陷阱）：
           会话里那些**旧临时记录**（`kind == "tick"` 那一类唤醒注记）会在
           **跑的中途被删掉** ⇒ 起点行号从那以后就**对不上了** ——
           切片可能少一条、也可能多一条入站 ⇒ "这一趟新增了什么"这个数**不可信**。
           ⇒ 于是改成"整份减去整份"：**删掉的那些不影响增量**。
        """
        left = collections.Counter(cls._a_row_key(one) for one in before)
        out = []
        for one in after:
            key = cls._a_row_key(one)
            if left.get(key, 0) > 0:
                left[key] -= 1
                continue
            out.append(one)
        return out

    def _report(self, before: dict[str, int]) -> None:
        print("\n" + "=" * 72)
        if not getattr(self, "got_anything", False):
            if getattr(self, "timed_out", False):
                # ⚠️⚠️ **超时 ≠ 没动**（2026-10-17 踩到）：给 40 秒那趟，她**动了 11 次手**，
                #    而这一格原来照旧打"一次回答都没回来" —— 又是一次"把量不到读成她不做"。
                #    ⇒ 分两句说：**是超时**，还是**真的一次都没回来**。
                print("⚠️ **这一趟没等到收尾**（时间到了）—— 但它**可能已经动过手**："
                      "下面那份'这一趟落进会话的行'**照旧算数**，只是没有最后那句话。")
            else:
                print("⚠️ **这一趟不算数**：一次回答都没回来（引擎不可用／钥匙不对）——"
                      "下面那些'一次都没调'**不能**读成'她不写'。")
        print("这趟请求里她手上的工具（名字）：")
        print("  " + "、".join(str(one.get("name")) for one in self.tools))
        print("\n**这一趟**落进会话的那些行（多重集相减 —— 见 `_fresh_rows`）：")
        fresh = self._fresh_rows(getattr(self, "before_rows", []),
                                 self.console.session.records())
        if not fresh:
            print("  （一件都没有 —— 那一次请求没回来？）")
        for one in fresh:
            role = str(getattr(one, "role", ""))
            kind = str(getattr(one, "kind", ""))
            name = str(getattr(one, "name", "") or "")
            text = str(getattr(one, "text", "") or "")
            mark = "  ★ 这一支笔动了" if name == "profile_add" else ""
            # ⚠️⚠️ **续行要带缩进记号**（2026-10-17 踩到）：她的话**常常是好几段**，
            #    这里原来是原样打出来 ⇒ 后面那几段**行首没有记号** ⇒ 谁用
            #    `grep '· \[assistant/message\]'` 读报告，就**只拿到第一段**。
            #    当天据此得出过两条**错结论**（"她否认那本词典"／"书房那条她没照库里的答"）——
            #    而那两趟的**完整**答话里其实都有库里的东西（`书架`／`窗`）。
            #    ⇒ 续行加一个记号：**行首带 `·` 或 `│` 的才算同一条记录**。
            body = text[:500].replace("\n", "\n      │ ")
            print(f"  · [{role}/{kind}{('/' + name) if name else ''}] {body}{mark}")
        # ⚠️ **只数"调用"那一行**（`function_call`）—— `function_result` 是同一次的两半，
        #    两个都数就会把"调了一次"报成两次。
        calls = [one for one in fresh
                 if str(getattr(one, "kind", "")) == "function_call"]
        names = [str(getattr(one, "name", "") or "") for one in calls]
        wrote = [one for one in calls if str(getattr(one, "name", "")) == "profile_add"]
        kb = [one for one in calls if str(getattr(one, "name", "")) in ("kb_search", "kb_read")]
        print(f"\n★ 这一趟共 {len(calls)} 次工具调用"
              + (f"：{'、'.join(names)}" if names else ""))
        print(f"★ profile_add：{'调了 ' + str(len(wrote)) + ' 次' if wrote else '一次都没调'}")
        print(f"★ 知识库：{'kb_search/kb_read 共 ' + str(len(kb)) + ' 次' if kb else '一次都没调'}")
        print("\n她动的手交回来的回执：")
        receipts = self._the_receipts()
        for one in receipts[-6:]:
            print("  · " + one[:300])
        if not receipts:
            print("  （事件回执为空；须另核 function_result 与画像落盘，不能据此判没执行）")
        after = self._the_portraits()
        print("\n她读／写那一份（副本家）的画像目录变化：")
        moved = False
        for key in sorted(set(before) | set(after)):
            was, now = before.get(key, 0), after.get(key, 0)
            if was != now:
                moved = True
                print(f"  · {key}: {was} → {now} 字节")
        if not moved:
            print("  （一个字都没动）")
        written = self._the_written_portraits()
        if self.write_home != self.home:
            print("那一支笔实际写进的地方（--home）：")
            for key, size in sorted(written.items()):
                print(f"  · {key}: {size} 字节")
            if not written:
                print("  （空的）")
        real = self._the_real_portraits()
        print("她**真**那一份家（这一趟不该动它）："
              + ("；".join(f"{key} {size} 字节" for key, size in sorted(real.items()))
                 or "（空的）"))
        print("=" * 72)
        self._keep_the_transcript(fresh, calls, receipts)

    def _keep_the_transcript(self, fresh: list, calls: list,
                             receipts: list[str]) -> None:
        """ **把这一趟留下来**（2026-10-17）—— 每趟一份，不许被下一趟冲掉。

        ⚠️⚠️ **为什么非有它不可**（当天踩到两次）：探针每趟把**她的会话原样拷进**
           `instances/probe-<bot>/session-main.jsonl`（那是"她这一趟读的那一份"）
           ⇒ **下一趟一开跑，上一趟的记录就没了**。
           而引擎的窗口只有几趟 —— 一趟失败（503）就把**前面几趟成功的证据**一起冲掉
           （量 `kb-own-world` 时正是这样：三趟跑完，读回来只剩最后那趟的入站那一行）。
        ⇒ 于是这一趟落盘一份：**这一趟新增的那些行**（`_fresh_rows` 交回来的）＋
           工具调用串 ＋ 回执。它按**批名 ＋ 时刻**命名，永不覆盖。
        ⚠️ 它落在**探针那一份家**里（`instances/probe-<bot>/runs/`），
           不碰她真那一份家（那一条纪律没动）。
        """
        try:
            # ⚠️ `self.root` **本身就是探针那一份状态目录**（`…/instances/probe-<bot>`）
            #    ⇒ 这里只加一层 `runs`（2026-10-17 第一版多拼了一层，写到了两层底下）。
            box = Path(self.root) / "runs"
            box.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%m%d-%H%M%S")
            one = box / f"{stamp}-{self.args.batch}.md"
            rows = [f"# {self.args.batch} ／ {stamp}",
                    "（工具 " + str(len(calls)) + " 次："
                    + ("、".join(str(getattr(c, "name", "")) for c in calls) or "一次都没调")
                    + "）",
                    ""]
            for row in fresh:
                role = str(getattr(row, "role", ""))
                kind = str(getattr(row, "kind", ""))
                name = str(getattr(row, "name", "") or "")
                rows.append(f"## [{role}/{kind}{('/' + name) if name else ''}]")
                rows.append(str(getattr(row, "text", "") or ""))
                rows.append("")
            if receipts:
                rows.append("## 回执")
                rows += [f"· {one_[:500]}" for one_ in receipts]
            one.write_text("\n".join(rows), encoding="utf-8", newline="\n")
            print(f"（这一趟留在 {one}）")
        except Exception as why:                 # noqa: BLE001 —— 留不下不该带走这一趟
            print(f"（留档失败，不影响这一趟：{type(why).__name__}）", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="画像自主性探针")
    parser.add_argument("--batch", choices=sorted(BATCHES), default="solo-rougelike",
                        help="用哪一批造出来的场景")
    parser.add_argument("--replay", default="", metavar="YYYY-MM-DDTHH:MM",
                        help="改用事件库里那个时刻前缀的真来话（原样回放）")
    parser.add_argument("--draft", action="append", default=[], metavar="账号=正文",
                        help="手工投一件（可重复；给了它就不用 --batch／--replay）")
    parser.add_argument("--no-presence", action="store_true",
                        help="**去掉**账号级画像状态那一段（对照用）")
    parser.add_argument("--hint-file", default="", metavar="文件.md",
                        help="整段换成本文件里那一版提示（实验用；"
                             "里面可以写 {ROOM}／{ALICE}／{BOB}／{BOT} 占位）")
    parser.add_argument("--short", action="store_true",
                        help="**不接那一趟的执行段**：她动的手只记进会话，"
                             "一个字都不落盘、一条都不发出去（只看她想干什么时用）")
    parser.add_argument("--home", default="", metavar="目录",
                        help="那一支笔改往这儿写（提示照旧读她真那一份家；"
                             "给的目录里应有 画像/ 那一层，没有就现建）")
    parser.add_argument("--list", action="store_true", help="列出那几批场景")
    parser.add_argument("--dump", default="", metavar="文件.json",
                        help="把她这一趟真发出去的抬头（system 那几条 ＋ 工具表）"
                             "留成一份 JSON —— 用来核对那一句提示到底在不在")
    parser.add_argument("--root", default=str(_CORE / "data"),
                        help="她从哪一份状态起步（探针默认抄一份到 instances/probe-<谁>/，"
                             "新落的那些不写回她的正本）")
    parser.add_argument("--offline-send", action="store_true",
                        help="使用无网络 QQ 文字替身，仅记录拟发送内容，不向真人群发送")
    parser.add_argument("--live-root", action="store_true",
                        help="直接在她那一份状态上跑（会往她的会话里落这一轮；慎用）")
    parser.add_argument("--overlay", metavar="目录",
                        help="把这一份改写过的样子盖在**副本**上（同名覆盖 ＋ "
                             "`_DELETE.txt` 里列的删掉）—— 量「换一种写法」用它，"
                             "她真那一份家一个字节不动")
    parser.add_argument("--requests", metavar="文件",
                        help="把**每一轮真发出去的那一份请求**逐轮追加到这个文件里"
                             "（JSONL）—— 抬头与尾巴**只进请求、不进会话**，"
                             "要判「那句提醒到底有没有到她眼前」就得看它")
    parser.add_argument("--linger", type=int, default=0, metavar="秒",
                        help="**那一批跑完之后再多等这么多秒** —— 量「批末推出去的东西"
                             "（例如跑到第 N 轮那句提醒）会不会自己再起一批」")
    parser.add_argument("--forget-account", action="append", default=[],
                        dest="forget", metavar="账号",
                        help="在**绑定本副本**里把这些账号摘掉 —— 用来量"
                             "「未绑定 ⇒ 独立 QQ-<账号> 键」那条路（她真那一份不动）")
    parser.add_argument("--bot", default="Telyin")
    parser.add_argument("--profile", default="", metavar="档案名",
                        help="换一条引擎档案跑（默认用她那一条；"
                             "抄到 instances/probe-engine/ 再改 current，不写她的盘）")
    parser.add_argument("--repeat", type=int, default=1,
                        help="同一批跑几趟（默认 1；看她那一趟是偶然还是习惯）")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    if args.list:
        for name, one in BATCHES.items():
            print(f"{name}\n    {one['why']}")
            for account, text, mentioned in one["messages"]:
                print(f"    {account}：{text}"
                      + (f"  @{mentioned[0]}" if mentioned else ""))
        return 0

    if args.short:
        print("[探针] --short：她动的手**不落地**（只想看她想干什么）")
    return _a_few_rounds(args)


def _a_few_rounds(args: argparse.Namespace) -> int:
    """同一批跑 `--repeat` 趟 —— 每趟一个**新的探针**（状态不互相带）。"""
    failed = 0
    for index in range(max(1, int(args.repeat))):
        if args.repeat > 1:
            print(f"\n########## 第 {index + 1} ／ {args.repeat} 趟 ##########")
        failed += asyncio.run(_TheProbe(args).run()) != 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
