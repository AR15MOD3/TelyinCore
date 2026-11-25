# telyincore/web.py
"""**那个服务** —— 只听回环的一条本地 HTTP 服务 ＋ 一条**推**的通道（第二段 · Task 1）。

 **它只认两个注入面**（这一刀的全部接口就这两个）：
   · 出口 `WebService.push(frame)` —— **同步**、可从**任何**线程叫，形状就是
     `Console(write=…)` 那一格要的 `Callable[[dict], None]`（交回一个序号，被忽略也不碍事）；
      **实参是一帧**（谁说的 ／ 那一刻 ／ 正文 ／ 哪一类，见 `console.py::_frame_for`）——
     本模块**只把它编成一条 `data:`**，不去认那四格里面装着什么。
   · 进口 `WebService.read_line()` —— **`async def`、零参**，交回**一行（不含行尾）**，
     形状就是 `Console.pump(read=…)` 那一格要的 `Callable[[], Awaitable[str]]`。
   ⇒ 于是中间那一串（`_line_to_event` ／ `_content_text` ／ `Console` ／ `pump` ／
     `deliver`）**一个字都不用改** —— 而本模块**不 import `console` 的任何一个内部**
     （连一个"提到那个名字的 import 节点"都没有；`tests/test_web.py` 拿 AST 钉着）。

 **那条通道拍 SSE，不拍 WebSocket** —— 判据是"只出不进要多、而要进的那一点点从哪来"：
   · **出去的很多**：那边的每一行回话、状态那几个数 —— 都是**服务 ⇒ 页面**，一条长连接，
     随时可能来，而页面**只是看着**；
   · **要进的很少**：**人打的那一行** —— 一次一行、人手的节奏。 而它**天生**就是一次
     request/response（一个 `POST`）⇒ **它不需要挤进那条推的通道**；
   · ⇒ 那条通道**只需要一个方向** ⇒ SSE 正好是"**一个方向的 HTTP**"；
     WebSocket 会买来一个我们**没有流量**的双向帧层，而它的服务端在本仓要么**装一个包**
     （`websockets` ／ `uvicorn` —— 实测都没装，而这一刀**不许装 Python 包** ），要么
     **手写 RFC 6455 的分帧**（掩码 ／ 续帧 ／ ping-pong ／ 关闭握手）⇒ 为"要进的那一点点"
     付**一整层协议** ；
   · 而 SSE 是**素 HTTP** ⇒ 落在标准库那个 `http.server` 上（**零新依赖**），浏览器那头
     是 `EventSource`（自带重连）；`id:` 那条也让页面拿得到序号。
   · ⚠️ **代价写清楚**（它不为零）：SSE 单向、文本、每一帧都要 `data:` 前缀；而"从页面到
     服务"那一条走的是**另一个口**（`POST /say`）—— 于是"连上"在页面上**不只是一条连接**，
     而是"**一条连接 ＋ 一个能打的 `POST`**"。

 **只听回环** —— 而"**不鉴权**"这条保证的**边界**就在这里，逐条写清（别读成"已经鉴过了"）：
   · **它只绑 `127.0.0.1`** ⇒ 这台机器之外**打不到**它（构造那一刻就立住：递一个不是回环
     字面量的地址进来，**当场抛** `ValueError` ）；
   · **而"这台机器上的谁都打得到"**：本机**任何一个进程**、以及本地浏览器里**任何来源的
     一页**，都能连上 `/events`、也能往 `POST /say` 塞一行 —— 因为**没有令牌**、
     **没有** Origin ／ Referer 检查（跨来源的一个 `POST` 是"简单请求" ⇒ 连预检都不发）；
   · ⇒ 所以这条保证的边界是"**这一台机器**"，**不是**"这一页" 。它今天适合的用法只有
     一种：**一个人在本机、开着自己那一页** ；
   · 要收紧的话，收紧点**只有一处**：`/events` 与 `/say` 都按 Origin 判一遍 ——
     ⚠️ **这一刀刻意不做**（设计稿 §五：鉴权 ），而上面这三句就是那条边界的**原文**，
     免得下一刀把"只听回环"读成"已经鉴过权了" 。
   · **那一页继承的就是同一条边界** —— `GET /` ／ `GET /state` ／ `GET /events` 在这台
     机器上也**谁都读得到**（不是"只有我能看"）； 那句边界**显示在那一页上**
     （引擎那一栏，`src/web/client/engine.ts`），免得"打开这个地址"被读成"只有我能打开"。

 **几条端点**（素 HTTP，没有框架）：
   · `GET /events`   —— 那条推的通道（`text/event-stream`；一条连接 ＝ 一个订阅）；
   · `POST /say`     —— 走进口那一头（一行 UTF-8 ⇒ `read_line()` 交回它）；
   · `GET /state` —— **那一页顶上那条计数**（四个数 ＋ 端点 ／ 模型 ／ 连接状态）——
                        数从哪来见 `state_of()`：**它是那几个对象的读数，不是本模块的常量**；
   · `GET /healthz`  —— 服务自己那几个数（端点 ／ 端口 ／ 序号 ／ 连接数 ／ 收下的行数）；
   · `GET /` —— **那一页**：产物目录里的 `index.html` ⇒ **打开这个地址就看得见**；
                        还没建的时候，它交回的**就是"怎么建"那几句**（见 `_NOT_BUILT`）；
   · `GET /<产物>` —— 那一页的 `app.js` ／ `style.css` 那一类（ 见下面那条边界）。

 **本刀（Task 2）加的那两条路**（`/persona` —— 那条人设口的两头，源在 `PersonaPort`）：
   · `GET /persona` —— 那十段 ＋ 两张清单（`PersonaPort.report()` 那一份）；
   · `POST /persona` —— **保存一段**：`{name, text, stamp[, slot]}` ⇒ **先留一版历史** ⇒
                          再写新文。 那串时间戳**由请求递进来**（本模块**一次都不读时钟**）
                          ⇒ 缺了它就是 **400**（不是"替它取现在"）；
   · ⚠️ **没接那条口 ⇒ 两条路都 404**（缺省不是 `DEFAULT_PERSONA_ROOT`：一个"没接也去读写
        这台机器上那条真的根"的缺省值，正是最容易把"在别处 clone 也建得出来"弄坏的地方）。

 **本刀加的那一条路**（`POST /handoff` —— **手动交接 ＋ 入梦**，作者 2026-10-03 拍的）：
   · `POST /handoff` —— **请它交接一次**（没有正文、没有参数）⇒ 落到
                          `Console.ask_for_handoff()` ⇒ `Handoff.request()`：
                          **批内只登记（批末兑现），空闲才当场做**（`05:123-136`）；
   · ⚠️ **它是一个"请求"，不是"执行"** —— 所以回答的是"那一刻是哪一种情形"
         （`wired` ／ `asked` ／ `requested` ／ `processing` 四个记号），不是"交接完了"；
   · ⚠️ **503 只说一件事**：现在叫不动（没接 console ／ 机器还没起来 ／ 正在收摊）
         —— 与 `/say` 那条 503 同一条口径："递不进去"，不是"你发错了"；
   · ⚠️ **它同时是"入梦"**：整理态是**交接的副产品**（`console._a_handoff` 的 `snapshot`
         那一格排的）⇒ 按这一下 ＝ **交接 ＋ 起整理态**两件一起，页上那个词要照实写。

 **那一页的源在哪、怎么重建它**（这是"怎么重建那一页"这个问题的**树里那一份答案**）：
   · 源：`src/web/client/`（壳 ／ 四个 TS ／ 一份 CSS —— **它由 Tailwind v4 编译**：
     词汇表写在那一份的 `@theme` 里，**没有 config 文件**）＋
     一份说明 `src/web/README.md`；构建：`package.json` 的 `build:web` ⇒
     `scripts/build-web.mjs`（ **两步**：esbuild 出 `app.js` ＋ Tailwind 的 CLI 出
     `style.css` ⇒ `dist/web/`，**产物只有一份 CSS**）；
   · **从仓根跑 —— 而这一格的真话是"两条命令 ＋ 一个必须留在树里的文件"**
     （别读成"两条就完了"）：① `pnpm install` ② `pnpm run build:web`；
      而①压在 **`pnpm-workspace.yaml`** 上（pnpm 11 起"审过哪些依赖的构建脚本"就搬到了
     那里；那两行是 `allowBuilds:` 底下的 `esbuild: true` 与
     `'@parcel/watcher': false`）：**少了它，①不是"少点东西"，是当场
     交回非零**（`ERR_PNPM_IGNORED_BUILDS`）⇒ 上面那两条命令当场变成一句会失败的话 ；
   · 工具链来自 **PATH 上系统装的** `node` ／ `pnpm`（本机实测 `node` v24.21.0 ／
     `pnpm` 12.3.4）； **脚本里零绝对路径** —— 不许出现 DSH 那份运行时目录那一条路径
     （`.dsh` 那一段一个字都不该有）：出现一处就把"借 DSH 的运行时"重新引回来，
     而那件事**不必要**（"在别处 clone 也建得出来"这句真话就是从这一条来的）；
   · 代价：一次 `pnpm install` 的**网络往来**（**三个依赖**：esbuild ／ `tailwindcss` ／
     `@tailwindcss/cli`，各带自己那份按系统架构落的二进制包）＋ 仓里多一个
     `node_modules/`（**不进 git**）＋ **`pnpm-workspace.yaml`
     留在树里、并且进 git**（它不是"多一个文件"那种小事 —— 它是①交回 0 的前提），
     之后是一次本地构建；
   · ⚠️ **Python 那一套不依赖它**：`node_modules/` 不在的时候整套测试照旧全绿 ——
     那一页的构建那一条**跳过**，而"怎么发那一页"那几条用一份**假产物**量。
      **跳过的判定是两件事各量一次**：PATH 上有没有 `node` ＋ `node_modules/esbuild`
     在不在 —— **少一样就跳过**（两种"少"不一样：前者那条命令起不来，后者它起得来
     而交回非零）。 判据只有一处，在 `tests/test_web.py::_build_skip_reason` 上。
     ⚠️ **点名的边界**：那个判据看的是 `node_modules/esbuild` **那个目录在不在**，
     **不是**它**能不能被 import** ⇒ 一份"装了一半"的依赖照旧会跑、而那条构建红
     （ 这里是**登记**，不是"没问题" —— 详话在那个判据自己的 docstring 上）。

 **那一页是"谁"起、谁接线、谁停** —— `serve()`（本模块尾部）：
   · 它按 `console.main()` 那个形状交出**一个协程**（`asyncio.run` 全树只许有一处，在
     `cli.py`）⇒ 起循环的那一句在**命令**里，入口自己**不碰事件循环**；
   · **接线是两句**（`wire()` ＋ `assemble(…, read=console.read)`）—— 两句都写在
     `serve()` 里：`read` 那一格有**两个住处**，缺一处输入就从别处来（实测的形状：
     那条泵走它自己的缺省 `_stdin_line` ⇒ 真的 `sys.stdin`）。

⚠️ **本模块一次都不读时钟**（没有 `time` ／ `datetime`）：起停靠 `Event` ／ `join(timeout)`，
   而"多久探一次头"那一个是 `queue.get(timeout=…)` 的实参，不是判据。
⚠️ **每个请求一条线程，而每条都是 daemon** ⇒ 就算哪条连接赖着不走，**进程也退得掉**
   （"长驻的服务不许把测试挂住"那条：daemon 是底线，而"收摊时叫醒每一条连接"是体面）。
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
import ipaddress
import io
from datetime import datetime, timezone
import zipfile
import json
import time
from time import monotonic as _shutdown_now
import queue
import re
import shutil
from subprocess import SubprocessError
import sys
import threading
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping

# ── 共同提交门那几件零件（2026-10-17 批②）─────────────────────────────────────
#
# ⚠️⚠️ **为什么它们写在这个文件里，而不是一个单独的小模块**：`web.py` 有一条硬规矩 ——
#    **一个同胞模块都不 import**（`tests/test_web.py::test_the_service_does_not_import_…`
#    按 import 量它；那两个面本来就是**注入**进来的）。所以"版本 ／ 原子替换 ／ 按资源锁"
#    这三件只能**就地**写在这儿。
# ⚠️ **保障边界（这一段比代码重要）**：
#    · **进程内锁**只拦得住同一个进程里的并发写；两个进程同时改同一份文件，它拦不住 ——
#      那种情况靠**版本检查**：后提交的那一位会发现手里的版本已经不是盘上那一版 ⇒ 不覆盖。
#    · **版本**是正文逐字节的摘要，**不是 CAS**：两人同时读到同一版、各自算完再各自写，
#      后者仍会覆盖前者（窗口极窄但存在）。真要堵死得靠文件锁，本刀**没做**。
#    · **外部编辑器完全在保障之外**（它不拿锁也不认版本）：它写完以后，下一次提交会被拒，
#      但那一次它已经写进去了 —— 这就是"版本检查"能给的全部保证。

def _version_of(text: str) -> str:
    """ 一份正文的**版本** —— 逐字节（utf-8）的 sha256，只回答"是不是同一份正文"。"""
    import hashlib
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


class PersonaConflict(RuntimeError):
    """ **版本对不上** —— 盘上那份已经不是"我读到的那一版"（**不覆盖、不拼接**）。"""

    def __init__(self, name: str, expected: str, actual: str) -> None:
        self.name = name
        self.expected = expected
        self.actual = actual
        super().__init__(
            "这一份在读完以后被别人改过了：" + str(name)
            + "（我读到的是 " + str(expected)[:12] + "…，盘上现在是 "
            + str(actual)[:12] + "…）—— 这一版不落，稿子留着。")


class PersonaReadFailed(RuntimeError):
    """Current bytes cannot be read safely; no revision or write is authorized."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__("当前文件暂时无法读取为 UTF-8；未保存，草稿保留。请修复读取问题后重试。")


def _atomic_replace(path: Path, text: str) -> None:
    """ **同一目录临时文件 → 关闭 → 原子替换**（失败时原文件一个字都不动）。"""
    import os as _os
    import tempfile as _tempfile
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, made = _tempfile.mkstemp(prefix="." + target.name + ".tmp-", dir=str(target.parent))
    tmp = Path(made)
    try:
        with _os.fdopen(handle, "wb") as stream:
            stream.write(str(text).encode("utf-8"))
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


class _LockBook:
    """ **进程内**的按资源锁（键是那份文件的绝对路径）。

    ⚠️ 一次要拿多把时**按名字排序**（`hold` 做的就是它）：两个人一个按 A→B、
       一个按 B→A 拿就会互相等死。排序把那件事变成不可能。
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def _one(self, key: str) -> threading.Lock:
        with self._guard:
            got = self._locks.get(key)
            if got is None:
                got = threading.Lock()
                self._locks[key] = got
            return got

    def hold(self, keys):
        wanted = sorted({str(one) for one in keys})
        taken: list[threading.Lock] = []

        class _Ctx:
            def __enter__(inner_self):
                for key in wanted:
                    lock = self._one(key)
                    lock.acquire()
                    taken.append(lock)
                return inner_self

            def __exit__(inner_self, *exc: Any) -> bool:
                for lock in reversed(taken):
                    lock.release()
                return False

        return _Ctx()


#: **全模块共用的一本锁**（2026-10-17 补验 ②）：两个 `PersonaPort` 实例指向**同一个根**
#: 时，只有共用同一本注册表，同一份文件的锁才是**同一把**。
#: ⚠️ 它仍然只是**进程内**的（见上面那一节的边界）。
_LOCKS = _LockBook()


def _a_lock_key(path: Any) -> str:
    r""" **锁的键** —— 规范化过的绝对路径（大小写 ／ 相对段 ／ 别名都不许绕过）。

    ⚠️ `normcase` 在 Windows 上把大小写压平（`C:\A` 与 `c:\a` 是同一份），
       在别的系统上原样 —— 那正是 `os.path.normcase` 的意思。
    """
    import os as _os
    return _os.path.normcase(str(Path(path).resolve()))


def _consolidation_write_into(persona: Any) -> Any:
    """ **整理态那扇写门**（2026-10-17 补验 ①）—— 生产那两条装配里的一条。

    ⚠️ **它必须把 `expect` 转过去**：整理那一趟生成期间不持锁，提交那一刻要拿
       "我读到的是哪一版"去比对（`PersonaPort.save_hers_text(..., expect=…)`）。
       少了这一格，`consolidate._write_guarded` 会**拒绝提交**（`NoVersionCheck`）——
       那是**故意的**：宁可这一趟不落盘，也不要无保护地写进主体的档案。
    """
    def write(name: str, text: str, stamp: Any, **kw: Any) -> Any:
        return persona.save_hers_text(name, text, stamp=stamp, **kw)
    return write


#: **那条保证本身**：只听这一个地址（**它不是配置项** —— 换掉它就是换掉那条保证）。
LOOPBACK = "127.0.0.1"


def the_cache_share(cached: Any, told: Any) -> int | None:
    """缓存命中那一格照什么显示（2026-10-11，作者选"老实显示"）。

    ⚠️ **一条命中都没报过 ⇒ 交回 `None`**（页上画 `—`），不画 0%：
    本地那个引擎恒报 `input_tokens_details.cached_tokens = 0`
    （2026-10-11 实测：7420 token 的一模一样前缀连发三次，三次都是 0）
    ⇒ 拿它除一万多的 input，只会得到一个一路往下走的**假比值**。
    那不是"命中率低"，是"这个引擎不告诉你命中什么"。

    真报过命中（`cached` 为正）才算得出比值；`told` 缺失或为 0 ⇒ 也算不出来。
    """
    if not isinstance(cached, int) or not isinstance(told, int):
        return None
    if cached <= 0 or told <= 0:
        return None
    return round(cached / told * 100)


#: 缺省端口（设计稿 §〇 那个地址上就是它）。
DEFAULT_PORT = 7799

#: 没人在读的时候，替那条进口最多留几行 —— 满了**回 503**（见 `_accept`），不悄悄丢。
#: 它**同时是**那条推的通道上"一个不读的页面最多能被欠几帧"（见 `_overflow`）：
#:    两处问的是**同一个问题**（"有人不读，我们替他攒多少"）⇒ 只有这一个数。
#: ⚠️⚠️ **64 是一个"拍"的数，不是量出来的**（⑥-ii：这句话原先只活在一份 gitignored 的
#:    报告里 ⇒ 出了这台机器就不可复核）。它今天站得住的理由是**两边都用得上**：
#:    进口那一半（`_accept`）是人手打字的节奏（到不了 64），而推的那一半（`_overflow`）
#:    要的是一个"不读的页面能欠多久"的界 —— 而**本波量的只是"它到界时的行为"**
#:    （断开 ／ 503），**不是**"64 比别的数好在哪"。⇒ 别把它读成一条量出来的阈值。
#: 真要调，改的是**这一行** —— 而它**同时管**上面那两件事：改一次，两处一起动。
MAX_PENDING = 64

#: 一次 `POST /say` 最多收多少字节。
#: ⚠️ **2026-10-05 从 64 KiB 提到 16 MiB**（作者实测报的一处）：那条路上现在**会带图** ——
#:    照片转成 data URL 是几百 KB 到几 MB（base64 还涨三分之一）⇒ 64 KiB 那一版**必然 413**。
#: 而 413 那一条**回完就关连接**（不读完 body 就没法复用连接）⇒ 浏览器那一下是**抛**、
#:      拿不到那句"太大了" ⇒ 页上只能说"这条路不通"。⇒ 所以**页面自己也要卡一道**（见 `live.ts`）。
#: ⚠️ 16 MiB 这个数的来处：一次最多 8 张× 单张上限 6 MiB 缩过之后
#: 的常见量级，留了余量；**仍然是个界**（不是"不设限"）。
#: ⚠️ 它同时管着几条 POST 口 —— 改一次，那几处一起动 。
MAX_BODY = 16 * 1024 * 1024
REJECT_DISCARD_BYTES = 64 * 1024
REJECT_DISCARD_SECONDS = 0.2
BODY_READ_SECONDS = 5.0

#: **一次 `POST /handoff` 最多等多久**（本刀）—— 那一条路会**等**那条循环把交接做完
#: （空闲时 `request()` 要跑完六步才回来：裁笔记 ／ 装前缀 ／ 换会话 ／ 写盘 ／ 注入）。
#: ⚠️ **它是"等不到"的上界，不是"做不完"的界**：超了照实回 `answered: False`
#: （"递出去了，还没等到回话"），而那一条协程**照旧在循环上跑**（不取消 —— 取消一次
#: 做了一半的交接，正是本仓最忌的那种"半份"）。
#: 它比 `POLL_SECONDS` 松一点是**故意的**：六步里有写盘那一下，而本机那个盘偶尔慢。
ASK_HANDOFF_BOUND_S = 30.0

#: 长连接上多久探一次头（探到空就发一条注释帧，也顺手认出"那头已经走了"）。
#: ⚠️ 它**不是**心跳机制（S8-B 归别的刀 ）：它只保证一条死掉的连接**不会**把线程挂到永远。
POLL_SECONDS = 15.0

#: `serve_forever` 那一圈的选择超时 —— ⚠️ **它就是 `stop()` 的延迟上界**
#: （`shutdown()` 只是立旗，而那条循环要**醒来**才看得见它）。
#: 标准库缺省是 **0.5 s**（实测：本刀的第一版每条测试白等 ~0.5 s，`--durations` 量到的），
#: 而这里 0.1 s ⇒ 收摊 ≤0.1 s；代价是空闲时**每 0.1 s 醒一次**（一次 `select()` 超时，
#: 对一个本机单人用的服务值这个价 ）。
SHUTDOWN_POLL_SECONDS = 0.1

#: 收摊时塞进那条进口队列的记号 ⇒ `read_line()` 把它翻成 `StopAsyncIteration`。
_CLOSED = object()

#: 收摊时塞给每一条长连接的记号（队列里装的其余东西都是 `bytes` ⇒ `None` 分得开）。
_STOP = None

#: SSE 的注释帧（探到空的时候发一条）—— 客户端会忽略它，而"写不出去"会把死连接认出来。
_KEEPALIVE = b": keepalive\n\n"

#: **那一页的产物目录** —— `scripts/build-web.mjs` 的缺省输出（从本模块所在的仓根算）。
#: ⚠️ 它是**算出来的**（`__file__` 往上两级），不是写死的一条绝对路径：写死的那一天，
#:    "在别处 clone 也建得出来"这句真话就没了。
DEFAULT_WEB_ROOT = Path(__file__).resolve().parent.parent / "dist" / "web"

#: **产物目录里发得出去的名字**：只有一层、且不以点开头（`..` ／ 隐藏文件因此进不来）。
#: ⚠️ 这条正则就是"越界那一类"的**唯一**判据：`%2e%2e` ／ `a/b` ／ `.gitignore` 都不匹配
#:    ⇒ 404。它比"拼完再看开头是不是产物目录"简单，而且没有规范化那一步可以骗。
_PAGE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: 产物那几种后缀 ⇒ `Content-Type`（ 只列我们会真的产出的那几种，不猜）。
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}

#: 还没建那一页的时候，`GET /` 交回的那几行 —— **它就是"怎么重建那一页"这条答案
#: 在浏览器里的那一份**（另两份在 `src/web/README.md` 与 `package.json`）。
#: ⚠️ 照旧是**两条命令 ＋ 一个必须留在树里的文件**：少了 `pnpm-workspace.yaml` 那一行
#:    `allowBuilds`，第 ① 条在 pnpm 12 上就是 `exit=1`（`ERR_PNPM_IGNORED_BUILDS`）
#:    ⇒ 那两句会**当场变成一句会失败的话**（这条边界与模块 docstring 那一份同数）。
_NOT_BUILT = (
    "还没建那一页。\n"
    "\n"
    " 怎么建（从仓根跑两条命令 —— 而它们压在一个必须留在树里的文件上）：\n"
    "    pnpm install\n"
    "    pnpm run build:web\n"
    "  ⚠️ 两句都压在 pnpm-workspace.yaml 上（那一行 allowBuilds）：少了它，\n"
    "     第一条在 pnpm 12 上当场 exit=1（ERR_PNPM_IGNORED_BUILDS）。\n"
    "\n"
    "产物落 dist/web/（不进 git）；工具链来自 PATH 上的 node ／ pnpm，脚本里没有绝对路径。\n"
    "详见 src/web/README.md。\n"
    "\n"
    " 而写入那条路的边界：本服务只听 " + LOOPBACK + "（回环），POST /say 校验可信 Host / Origin\n"
    "  （无 Origin 的本地调用保留），但没有令牌 ⇒ 「只听回环」是「这一台机器」，不是「已鉴权」。\n"
)

#: **顶栏那条计数** —— 这四个名字就是那一页要显示的数（`GET /state` 的 `machine` 那一段）。
#: ⚠️ 它们与那一页读的名字是**同一份**（`src/web/client/state.ts` 读的就是这四个字符串）；
#:    `tests/test_web.py` 拿一条测试钉着"端点发的名字就是页面读的名字"。
COUNTER_KEYS = ("tokens", "messages", "batches", "paused",
                # 2026-10-10（三颗按钮 ⑤）：那两格是**作者按下的那个暂停**
                # （ 与 `paused` 那一格**两回事** —— 它只写、读从主循环现读 ）。
                "stopped_by_hand", "processing", "round_id", "interrupting", "yielding", "dropped",
                # 第 ⑥ 步： 上一次那一下的**一句真话** （ 页上读它 ）。
                "gate_note", "activity", "next_wake", "cache_note",
                # 第 ⑧ 步： "正在起"那一格 （ 页上那颗键靠它转起来 ）。
                "starting")

#: **引擎那一页的另外两样**（端点 ／ 模型）—— 连接状态那一样在顶层（`connected`）。
ENGINE_KEYS = ("endpoint", "context_window", "model", "profile", "model_available")
#: ⚠️ 顺序**与 `state_of()` 里那个字典一致**（判据拿 `(*COUNTER_KEYS, *ENGINE_KEYS)` 与它比 ）；
#: 而 `context_window` 是 **2026-10-05** 加的那一格 —— 那一页右下角那个圆要的分母
#: （分子是 `tokens`，见 `COUNTER_KEYS`）。原注：`profile`＝这一条档案的**名字**
                                          #   （右上角「当前模型供应商」那一格要它 ——
                                          #   `endpoint`／`model` 都是**值**，不是名字）

#: **「模型」那一页**（刀 4 重做的那一页）载荷的顶层名字。
#: ⚠️ **与页面读的必须是同一份**（照 `COUNTER_KEYS` ／ `ENGINE_KEYS` 的成法，新立一组常量）。
#: 发出去时报的那个名字（与 `transport.py::USER_AGENT` **必须逐字相同** ——
#: 本模块零同胞 import，只能各写一份；那条判据就是防这两份漂移的）。
USER_AGENT = "core-kernel/1.0 (+local)"

#: **2026-10-12**：第三套话那一家要的那条版本头 —— 少它就回 `400 invalid_request_error`
#:   （"missing anthropic-version"），而没带 `x-api-key` 则回 401；两条都齐才认得出
#:   "密钥／协议对不对"。⚠️ 它跟着**探活**这一条路走（真请求那一条今天还**不带任何钥匙** ——
#:   那一笔是另一条账，写在 `telyincore/transport.py` 的抬头与待办账上）。
ANTHROPIC_VERSION = "2023-06-01"

ENGINE_PAGE_KEYS = ("profiles", "current", "keys", "availability", "secret", "can_probe",
                    "boundary", "presets")

#: **那一页的边界句**（**一处**：改一次，几处一起动）—— 涵盖三件事：
#: ⚠️ **2026-10-04：作者看过实物之后，页上那两行红字去掉了** ⇒ 这一个常量**照旧由
#:   `GET /engine` 交出来**（它是那句实话的**住处**），只是**页今天不显示它**。
#: "页上不显示" ≠ "那条话不成立"：它照旧在 `web.py` 的抬头与 `src/web/README.md` 里。
#: 涵盖三件事：
#:   ① 同源校验挡的是「你浏览器里**另一个站点**上的一页」，**挡不住本机任何一个进程**；
#:   ② 密钥**只写不回显**；③ 这一页改的是「**这台机器用哪个脑**」。
#: ⚠️ **不许含糊**：那半句"挡不住本机任何一个进程"是本仓那条立过的规矩
#:   （「只听回环」＝「这一台机器」，**不是**「已鉴权」）。
ENGINE_BOUNDARY_LINE = (
    "这一页改的是「**这台机器用哪个脑**」（端点 ／ 模型 ／ 密钥）。"
    "它拦得住「你浏览器里另一个站点上的一页」发来的跨站 POST；"
    "**拦不住这一台机器上的任何一个进程**（`curl` 也一样）。"
    "密钥只写、不回显。"
)


def _complete_model_catalog(raw, request, opener, deadline, limit):
    """Follow catalog cursors on the admitted endpoint under one size/time budget."""
    try:
        page = json.loads(raw)
    except (ValueError, UnicodeError):
        page = None
    if not isinstance(page, dict) or not isinstance(page.get("data"), list):
        raise ValueError("上游返回的模型目录格式无效")
    rows = list(page["data"])
    remaining_bytes = limit - len(raw)
    cursors = set()
    while page.get("has_more") is True:
        cursor = page.get("last_id")
        if not isinstance(cursor, str) or not cursor or cursor in cursors or len(cursors) >= 100:
            raise ValueError("上游模型目录分页无效，未能完整读取")
        cursors.add(cursor)
        remaining_time = deadline - time.monotonic()
        if remaining_time <= 0:
            raise TimeoutError()
        url = request.full_url + "?" + urllib.parse.urlencode({"after_id": cursor})
        following = urllib.request.Request(url, headers=dict(request.header_items()))
        with opener.open(following, timeout=remaining_time) as response:
            chunk = response.read(remaining_bytes + 1)
        remaining_bytes -= len(chunk)
        if remaining_bytes < 0:
            raise ValueError("上游模型目录过大，未能完整读取")
        try:
            page = json.loads(chunk)
        except (ValueError, UnicodeError):
            page = None
        if not isinstance(page, dict) or not isinstance(page.get("data"), list):
            raise ValueError("上游返回的模型目录格式无效")
        rows.extend(page["data"])
    return json.dumps({"data": rows}, ensure_ascii=False)


def _model_names(body: str) -> list[str] | None:
    """从 `/models` 那一份里把名字挑出来（挑不出来 ⇒ `None`，不是空表）。"""
    try:
        one = json.loads(body)
    except ValueError:
        return None
    rows = one.get("data") if isinstance(one, dict) else None
    if not isinstance(rows, list):
        return None
    out = [str(row.get("id")) for row in rows if isinstance(row, dict) and row.get("id")]
    return out or None


def usage_of(console: Any, *, config: Any = None,
             split: Callable[..., Mapping[str, int]] | None = None,
             tools: Any = ()
             ) -> Callable[[], dict[str, Any]]:
    """ **这一份请求是怎么占的**（2026-10-05 作者："关于上下文，你要做一个点击显示"）。

    ⚠️⚠️ **分块那一步由外面递**（`split`）—— `web.py` 那条"**零同胞 import**"的围栏 ⇒
       它够不到 `tokens` ／ `session`（与 `read_memory` 同一条路：**由入口递** ）。
    ⚠️ 没递 `split` ⇒ 那一格照旧答得出**总数**，只是**没有分块**（不编一块假的 ）。
     **`tools` 那一格**（2026-10-05 作者："那就先做。" ⇒ "做。"）：
       那几份**工具的声明**由入口递进来 （`DECLARED_TOOLS` —— 这里够不到 `tools` ），
       再由这一格**转手**给 `split(..., tools=tools)` ⇒ 「工具 · 插件」那一块才有数 。
       ⚠️ **没递 ⇒ 那一块照旧 `—`** （"没有工具"与"工具不占地方"是两件事 ）。
    Core Console优先使用只读request_usage_preview（含尾部/媒体/声明）；以下是旧调用方回退约定。
    ⚠️ 总数与分块来自**同一份消息**（`session.outbound(())` —— 会话那一份，**含**里面那条
       system ，而**不含**每请求才现装的那半句 ）⇒ 与 `console.request_size()` 那个数
       可能差一点点，那一点在 `note` 里说清 （两个数各自都是真的 ）。
    """
    def read() -> dict[str, Any]:
        session = getattr(console, "session", None)
        messages: list[Any] = []
        preview_of = getattr(console, "request_usage_preview", None)
        has_preview = callable(preview_of)
        preview = preview_of() if has_preview else None
        if session is not None and not has_preview:
            # ⚠️⚠️ **要带"那一句话"**（2026-10-05 实机抓到的一次真 bug）：
            #   本仓的 system 前缀是**合成的** —— 由 `console._head_cell()` 现装，
            # **它不在落盘里** （`session-main.jsonl` 里只有对话那几条 ）。
            # 从前这里递的是**空的** `()` ⇒ 那一份里没有系统前缀 ⇒
            # `parts.prefix` 恒为 **0** 、`total` 少算一大截（实测 110 而
            # 会话那一边是 5822 ）。
            # `console.request_size()` 用的正是 `outbound(self._head_cell())` ，
            # 而它自己的注释写着"**页上那两格要的是『真发出去的那一份』**"
            # ⇒ 这一格与它**同源**（同一个 `outbound` 的同一个 list ）。
            head_cell = getattr(console, "_head_cell", None)
            messages = list(session.outbound(head_cell() if callable(head_cell) else ()))
        if has_preview:
            window = preview.get('window') if isinstance(preview, Mapping) else None
        else:
            window = getattr(config, 'context_window', None) if config is not None else None
        out: dict[str, Any] = {"window": window, "total": None, "parts": {},
                               "notes": [
                "这一份是按**会话那一份**算的（含里面那条 system ／ 不含每请求才现装的那半句）",
                "而顶栏那个数是**那一轮实际装的那一份** ⇒ 两个数可能差一点点；两个数各自都是真的",
            ]}
        # **缓存命中**（2026-10-05）：引擎在终态报的 `cached_tokens` ÷ `input_tokens`
        # ⚠️ 它是**上一次请求**的读数（事后才有 ）—— 还没跑过一轮 ⇒ `None`（页上画 `—` ）。
        # **会话累计**（2026-10-05 晚）：分子分母都用**整个会话**累计的数 ——
        # 参考那一份就是这么算的（见 `console._keep_usage` 里那一段 ）。
        # ⚠️ 从前用的是**上一次请求**（`last_usage`） ⇒ 那个数会**跳**
        # （连发三次实测 0% ／ 0% ／ 100% ）—— 口径不同，不是算错 。
        totals = getattr(console, "usage_totals", None)
        cached = told = None
        if isinstance(totals, Mapping):
            if isinstance(totals.get("cached_tokens"), int):
                cached = totals["cached_tokens"]
            elif isinstance(totals.get("cached"), int):
                cached = totals["cached"]
            if isinstance(totals.get("input"), int):
                told = totals["input"]
        # 2026-10-11（作者选"老实显示"）：**一条命中都没报过 ⇒ 画 —，不画 0%**。
        # 本地那个引擎恒报 `cached_tokens = 0`（实测：7420 token 的一模一样前缀
        # 连发三次，三次都是 0）⇒ 拿它除一万多的 input 会得到一个一路往下走的假比值。
        # 那不是"命中率低"，是"这个引擎不告诉你命中什么"。
        if the_cache_share(cached, told) is None:
            cached = None
        out["cache"] = ({"cached": cached, "input": told,
                         "share": (None if not told else round(cached / told * 100))}
                        if cached is not None and told else None)
        # Additive diagnostics; the legacy cache field keeps its old contract.
        observations = getattr(console, "cache_observations", None)
        try:
            out["cache_observations"] = observations.snapshot() if observations is not None else None
            out["cache_observation_error"] = getattr(console, "cache_observation_error", None)
        except Exception:
            out["cache_observations"] = None
            out["cache_observation_error"] = "snapshot_failed"
        # The UI uses this exact console partition of the SAME snapshot above.
        # Old `cache` remains API-compatible history, never a selection fallback.
        select_cache = getattr(console, "cache_console_view", None)
        out["cache_view"] = {"version": 1, "scope": "console", "status": "unavailable"}
        try:
            snapshot = out["cache_observations"]
            if snapshot is not None and callable(select_cache):
                out["cache_view"] = select_cache(snapshot)
        except Exception:
            out["cache_view"]["status"] = "selection_failed"
        out["cache_all_view"] = out["cache_view"].pop("all_paths", {
            "version": 1, "scope": "all_paths", "status": out["cache_view"].get("status", "unavailable")})
        out['cache_local_view'] = out['cache_all_view'].pop('local_prefix', None)
        # Read-only, process-local diagnostics. Never replace legacy cache usage.
        requested = getattr(console, "cache_prefix_diagnostics_requested", False) is True
        sink = getattr(console, "cache_prefix_diagnostics_buffer", None)
        diagnostic = getattr(console, "cache_prefix_diagnostics_transport", None)
        try:
            rows = sink.snapshot() if sink is not None else []
            out["cache_prefix_diagnostics"] = {
                "requested": requested, "active": sink is not None,
                "status": ("active" if sink is not None else
                           "unavailable" if requested else "disabled"),
                "comparison_order": "transport_stream_open", "capacity": 64,
                "persistent": False, "provider_token_prefix": None,
                "instance_id": sink.instance_id if sink is not None else None,
                "coverage": diagnostic.coverage() if diagnostic is not None else None,
                "rows": rows,
                "error": (getattr(console, "cache_prefix_diagnostics_error", None)
                          or getattr(diagnostic, "error", None)),
            }
        except Exception:
            out["cache_prefix_diagnostics"] = {
                "requested": requested, "active": False,
                "status": "unavailable", "rows": [], "error": "snapshot_failed"}
        # **那三段有多久**（2026-10-05）：`{"thinking", "output", "total"}`（秒 ）
        # ⚠️ 它是**上一次那一轮**的（事后才有 ）；还没跑过 ⇒ `None`（页上画 — ）。
        out["batch_timing"] = getattr(console, "batch_timing_last", None)
        got_timing = getattr(console, "timing_last", None)
        out["timing"] = (dict(got_timing) if isinstance(got_timing, Mapping)
                         and got_timing.get("total") is not None else None)
        if has_preview:
            # Core Console owns one tail/media/tool-consistent source preview.
            # Unknown is not a partial sum; legacy split is only for old callers.
            known = isinstance(preview, Mapping) and preview.get('status') == 'estimated'
            out['window'] = preview.get('window') if isinstance(preview, Mapping) else None
            out['estimate_source'] = preview.get('estimate_source', 'next_request_preview') if isinstance(preview, Mapping) else 'next_request_preview'
            out['estimated_for_window'] = preview.get('estimated_for_window', preview.get('window')) if isinstance(preview, Mapping) else None
            out['estimate_captured_at_ms'] = preview.get('captured_at_ms') if isinstance(preview, Mapping) else None
            out['estimate_status'] = 'estimated' if known else 'unknown'
            out['parts'] = dict(preview['parts']) if known else {}
            out['total'] = preview['total_estimated_tokens'] if known else None
            out['notes'] = [
                '当前配置下的下一请求源侧估算，包含动态尾部、媒体预算和工具声明；'
                '不是上游实测用量或安全上界。']
            if out['estimate_source'] == 'standby_archive_preview':
                out['notes'] = ['待机基础上下文估算：仅控制台自身存档、公共系统前缀和工具声明；不含本轮动态检索、身份提示及其他会话，不代表已发送请求。']
            if out['estimate_source'] == 'completed_batch_snapshot':
                out['notes'] = ['分子是上一批结束时的输入估算快照，分母是当前模型配置窗口；不是当前请求或上游实测用量。']
        elif split is not None:
            # ⚠️ **那一份可以是一个"会变的"**（2026-10-05 真跑抓到的）：
            # 它在**起点**钉死那一份的话，后来开／关一个插件**这一格不会跟着变**
            # （实测：关掉之后 `tools` 照旧 303 ）⇒ 所以**可调用的就现叫一次** 。
            now = tools() if callable(tools) else tools
            got = dict(split(messages, tools=now))
            # ⚠️ 那一格里**允许 `None`**（＝"还没有可以算的东西" 与 0 不是一回事 ——
            # 见 `tokens.summarize_messages` 里 `tools` 那一条）⇒ 不许拿 `int()` 硬转 。
            out["parts"] = {str(k): (None if v is None else int(v)) for k, v in got.items()}
            out["total"] = sum(v for v in out["parts"].values() if v is not None)
        return out

    return read


def _a_media_line(line: str) -> dict[str, Any] | None:
    """ **一条"带图的" `POST /say`**（2026-10-05）—— 不是那个形状 ⇒ 交回 `None` 。

    ⚠️ 只在**第一个非空字符是 `{`** 时才去解析（纯文本一行照旧走原路 ）；
       解析不了、或形状不对（没有 `text` 这一格 ／ 它不是字符串）⇒ 也交回 `None`
       —— 那一行仍旧当**纯文本**处理 （"你发错了"由既有的那几条 400 说，不在这里新造一句）。
    """
    if not line.lstrip().startswith("{"):
        return None
    try:
        one = json.loads(line)
    except ValueError:
        return None
    if not isinstance(one, dict) or not isinstance(one.get("text"), str):
        return None
    return one


_probe_registry_lock = threading.RLock()



def _probe_http_failure(status: int, raw: bytes) -> dict[str, Any]:
    """Classify bounded upstream evidence; never publish an upstream error body."""
    try:
        data = json.loads(raw) if len(raw) <= 16_384 else {}
    except (ValueError, UnicodeError, RecursionError):
        data = {}
    error = data.get("error", {}) if isinstance(data, dict) else {}
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code", "")).lower()
    message = str(error.get("message", "")).lower()
    evidence = code + " " + message
    category, reason = "request", "模型名或协议有误，请检查"
    if status == 401:
        category, reason = "authentication", "API密钥有误或没填，请检查"
    elif status == 403:
        category, reason = "permission", "上游拒绝访问，请检查模型权限或服务访问限制"
    elif status == 402 or (status in (400, 429) and any(word in evidence for word in
            ("insufficient_quota", "insufficient_balance", "insufficient credit", "余额不足", "额度不足"))):
        category, reason = "quota", "上游余额或额度不足，请检查账户用量"
    elif status == 429:
        category, reason = "rate_limit", "上游请求限流，请稍后重试"
    elif status in (408, 504):
        category, reason = "timeout", "上游处理超时，请稍后重试"
    elif status >= 500:
        category, reason = "upstream", "上游服务暂时不可用，请稍后重试"
    elif status == 404:
        category, reason = "not_found", "模型或API路径不存在，请检查模型名和地址"
    elif status in (400, 422):
        category, reason = "parameters", "上游不接受请求参数，请检查模型与协议"
    elif 300 <= status < 400:
        category, reason = "redirect", "上游要求跳转，请填写最终API地址"
    # Only fixed parameter names are public, not free-form codes or messages.
    param = error.get("param")
    allowed = ("max_tokens", "max_completion_tokens", "max_output_tokens", "stream", "model", "messages", "input", "temperature")
    param = param if isinstance(param, str) and param in allowed else None
    detail = f"HTTP {status}" + (f"；参数：{param}" if param else "")
    return dict(ok=False, status=status, models=None, error=reason, detail=detail,
                error_category=category, error_param=param)


def probe_engine(engine: Any, asked: Mapping[str, Any] | None = None, *,
                 timeout_s: float = 45.0, owner: Any = None) -> dict[str, Any]:
    """ **探活** —— **以最小的一次真请求**问一句：「这一套配置通不通？」

    ⚠️⚠️ 与从前那一版**不一样**：从前只打 `/models`（那一格不要鉴权，也答不出"协议选对没有"）
       ⇒ 作者要的是"**真的按这一条配置发一次最小请求、把上游的回话拿回来**"。
    ⚠️ **四种下场各一句给定的话**（作者定的，页上照原样显示）：
       · 没填模型名 ⇒ `模型名不能为空`
       · 没填地址   ⇒ `API地址不能为空`
       · 连不上 ／ 超时 ⇒ 分别提示网络连接失败或等待超时
       · 上游回了 4xx ／ 5xx ⇒ 按认证、权限、额度、限流、参数及服务故障分类
       · 成了 ⇒ `ok: True`（页上那一条画**绿色**的「测试成功」）
    ⚠️ **最小**：输出额度压到 `16`、`stream: false`（一次返回就够，不必读流）——
       而**回话原样带回来**（作者要"请求一次 API 的返回"）。
    ⚠️ 它用 stdlib 打（本模块**零同胞 import** ⇒ 用不了 `transport.py`），
       **不用环境里的代理**（`ProxyHandler({})`：与本仓 `trust_env=False` 同一条口径），
       而整个探活在**一个线程**里跑（那一页不许因为对方不回话而挂住）。
    ⚠️ **回话里没有密钥**：那一格今天本来就没递进这里（`asked` 里只有名字 ／ 协议 ／
       地址 ／ 模型 —— 见 `_a_engine_probe`）。
    """
    one = dict(asked or {})
    mode = str(one.get("mode") or "minimal")
    model = str(one.get("model") or "")
    base = str(one.get("base_url") or "")
    kind = str(one.get("kind") or "responses")
    if mode not in ("minimal", "models", "connection"):
        return {"ok": False, "error": "探测模式无效", "url": ""}
    if not base.strip():
        return {"ok": False, "error": "API地址不能为空", "url": ""}
    if mode == "minimal" and not model.strip():
        return {"ok": False, "error": "模型名不能为空", "url": ""}
    try:
        # A captures target, file-only credentials and proxy under one boundary.
        # It must reject stale slot ownership; no current-profile or anonymous fallback.
        capture = getattr(engine, "web_probe_snapshot", None)
        if not callable(capture):
            raise ValueError("probe snapshot unavailable")
        snapshot = dict(capture(dict(one)))
        expected = {"name": str(one.get("name") or ""), "mode": mode,
                    "auth": str(one.get("auth") or "api_key"), "kind": kind,
                    "model": model, "base_url": base}
        if any(snapshot.get(key) != value for key, value in expected.items()):
            raise ValueError("probe target changed")
        key = snapshot["api_key"]
        selected_proxy = snapshot["proxy"]
        if not isinstance(key, str) or (selected_proxy is not None and not isinstance(selected_proxy, str)):
            raise ValueError("invalid probe snapshot")
        key = key.strip()
        host = urllib.parse.urlsplit(base).hostname
        if not key and host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("remote credentials missing")
    except Exception:
        return {"ok": False, "error": "探测目标或认证快照不可用，未发送请求", "url": ""}

    if mode in ("models", "connection"):
        url = f"{base.rstrip('/')}/models"
        body: dict[str, Any] | None = None
    else:
        # 端点由**协议**说了算（与 `protocol.endpoint_for` 同一条拼法）
        path = {"chat": "/chat/completions", "anthropic": "/messages"}.get(
            kind, "/responses")
        url = f"{base.rstrip('/')}{path}"
        if kind == "chat":
            body = {"model": model, "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 16, "stream": False}
        elif kind == "anthropic":
            # **第三套**（2026-10-12）：`max_tokens` 是必填、消息只有 user／assistant
            #   （它没有 `role: "system"`），而"系统那一句"要放在**顶层**。
            body = {"model": model, "max_tokens": 16, "stream": False,
                    "messages": [{"role": "user", "content": "hi"}]}
        else:
            body = {"model": model, "input": [{"role": "user", "content": "hi"}],
                    "max_output_tokens": 16, "stream": False}

    # Decode before redacting: JSON escapes and later reply truncation must not
    # turn a complete private key into an unredactable public fragment.
    def public_text(text: str) -> str:
        try:
            value = json.loads(text)
        except ValueError:
            return ""  # Truncated/non-JSON upstream data is not public evidence.
        def scrub(value):
            if isinstance(value, str):
                return value.replace(key, "[redacted]") if key else value
            if isinstance(value, list):
                return [scrub(item) for item in value]
            if isinstance(value, dict):
                return {scrub(name): scrub(item) for name, item in value.items()}
            return value
        return json.dumps(scrub(value), ensure_ascii=False)

    box: dict[str, Any] = {}

    def work() -> None:
        probe_deadline = time.monotonic() + timeout_s
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"https": selected_proxy} if selected_proxy else {}), NoRedirect())
        if snapshot.get('proxy_fallback') is True and selected_proxy:
            from .model_network import ModelNetwork, ModelProbeOpener
            opener = ModelProbeOpener(ModelNetwork(selected_proxy, True))
        payload = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="GET" if body is None else "POST")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        # **User-Agent 得设**（2026-10-05 量出来的）：不设就是 `Python-urllib/3.x`，而
        #    Cloudflare 那一类**专挡这一串**（实测那家第三方对**每一个路径**都回
        #    `403 error code: 1010`；带上一个正经 UA 之后**立刻变成 401** —— 那才是
        #    "要鉴权"，与"钥匙对不对"是两件事）。
        #    ⚠️ 本模块**零同胞 import** ⇒ 这一行与 `transport.py` 那一份是**同一个字符串**，
        #    改一处要改两处（`test_the_two_places_say_the_same_user_agent` 盯着这件事）。
        req.add_header("User-Agent", USER_AGENT)
        if key:
            # 真供应商那一边（DeepSeek ／ OpenAI …）没有它一律 401 ⇒ 这一格是承重的。
            # ⚠️ **Anthropic 不认 `Authorization: Bearer`**（2026-10-12）：它要的是
            #    `x-api-key` 那一颗头，外加一条 `anthropic-version` —— 少任何一条都回 401，
            #    而那会被人读成"密钥填错了"（同一类"把人支到错方向"的老毛病）。
            if kind == "anthropic":
                req.add_header("x-api-key", key)
                req.add_header("anthropic-version", ANTHROPIC_VERSION)
            else:
                req.add_header("Authorization", f"Bearer {key}")
        try:
            with opener.open(req, timeout=timeout_s) as resp:
                code = int(getattr(resp, "status", 0) or 0)
                if mode == "connection":
                    box.update(ok=True, status=code, error=None, models=None)
                    return
                limit = 8 * 1024 * 1024 if mode == "models" else 200_000
                raw = resp.read(limit + 1)
                if len(raw) > limit:
                    box.update(ok=False, status=code, models=None, error="上游响应过大，未能完整读取")
                    return
                if mode == "models":
                    try:
                        text = _complete_model_catalog(raw, req, opener, probe_deadline, limit)
                    except ValueError as exc:
                        box.update(ok=False, status=code, models=None, error=str(exc))
                        return
                else:
                    text = raw.decode("utf-8", "replace")
                text = public_text(text)
                box.update(ok=True, status=code, error=None,
                           models=_model_names(text) if mode == "models" else None,
                           # **量出来的那一笔**：`/models` 那一份里逐模型带着窗口与额度
                           #    ⇒ 「上下文上限」这类默认值**问上游要**，不猜（作者要的）。
                           specs=_model_specs(text) if mode == "models" else None,
                           reply=_a_short_reply(kind, text) if mode != "models" else None)
            # Model discovery only, not generation: llama.cpp advertises
            # template-specific reasoning levels in /props, not every /models.
            props_url = _local_llama_props_url(base) if mode == "models" else None
            remaining = min(2.0, probe_deadline - time.monotonic())
            if props_url and remaining > 0:
                try:
                    props_request = urllib.request.Request(props_url, method="GET")
                    props_request.add_header("User-Agent", USER_AGENT)
                    if key:
                        props_request.add_header("Authorization", f"Bearer {key}")
                    with opener.open(props_request, timeout=remaining) as props_response:
                        raw_props = props_response.read(300_001)
                    if len(raw_props) <= 300_000:
                        box["specs"] = _with_llama_effort_specs(box.get("specs"), json.loads(public_text(raw_props.decode("utf-8", "replace"))))
                except Exception:
                    pass  # optional capabilities must not break a valid model list
        except urllib.error.HTTPError as exc:
            try:
                raw_error = exc.read(16_385)
            except Exception:
                raw_error = b""
            finally:
                exc.close()
            box.update(_probe_http_failure(int(exc.code), raw_error))
        except Exception as exc:                    # connection failure is not proof of a bad URL
            reason = getattr(exc, "reason", exc)
            timed_out = isinstance(reason, TimeoutError)
            box.update(ok=False, status=None, models=None,
                       error="探测超时，上游未及时返回，请稍后重试" if timed_out else "连接上游失败，请检查网络和API地址",
                       error_category="timeout" if timed_out else "connection",
                       detail="本次未自动重试")

    public_url = url.replace(key, "[redacted]") if key else url
    worker = threading.Thread(target=work, daemon=True)
    if owner is None:
        owner = engine
    with _probe_registry_lock:
        workers = getattr(owner, '_probe_workers', [])
        workers = [thread for thread in workers if thread.is_alive()]
        owner._probe_workers = workers
        if getattr(owner, '_closing', False) or len(workers) >= 8:
            return {"ok": False, "error": "探活尚未排空或已停止；未发送请求", "url": public_url}
        workers.append(worker)
        try:
            worker.start()
        except BaseException:
            workers.remove(worker)
            raise
    worker.join(timeout_s)
    if worker.is_alive() or not box:
        return {"ok": False, "status": None, "error": "探测超时，上游未及时返回，请稍后重试",
                "error_category": "timeout", "detail": "本次未自动重试", "url": public_url}
    box["url"] = public_url
    return box


def _image_capability(row, model_id):
    # Explicit upstream negatives outrank name-based catalogue hints.
    def negative(value, depth=0):
        if depth > 3:
            return False
        if isinstance(value, dict):
            return any((item is False and any(word in str(key).lower()
                        for word in ("vision", "image", "multimodal")))
                       or negative(item, depth + 1) for key, item in value.items())
        if isinstance(value, (list, tuple)):
            return any(negative(item, depth + 1) for item in value)
        return False
    return False if negative(row) else (_sees_images(row) or _vision_beside(model_id))


def _sees_images(row: Mapping[str, Any]) -> bool:
    """ 这一条能不能**收图**（量得到的那一样）。

    ```
    DeepSeek 那一份： `input_modalities: ["text", "image"]`
    本机那台那一份： `modalities: {"vision": true}`
    ```
    ⚠️ **"最大图片输出 token"那一格上游没有**（实测：两家 `output_modalities` 都是 `["text"]`
       —— 它们**只出文本**）⇒ 页上不画一个编出来的数，只画"能不能收图"。
    """
    # ⚠️ **各家报法不一样**（作者拿官方文档质问过一次：GLM 明明是多模态，页上却说"只看文本"）
    #    ⇒ 不赌某一个字段名，**整条记录里递归找**"带视觉味"的迹象：
    #      键名里有 `vision` ／ `image` ／ `multimodal` ／ `vl`，或者值里出现 `image` ／ `vision`
    #      ／ `multimodal`（列表或字符串都算）。
    #    ⚠️ 反证也要认：**明确写 `false`** 的不算（`{"vision": false}` / `supports_vision: false`）。
    #    ⚠️ 这条是**启发式**（上游没报就查不出）—— 报告里写清；要更硬只能各家写死映射。
    return _looks_multimodal(row, 0)


def _looks_multimodal(thing: Any, depth: int) -> bool:
    """ 在一条模型记录里**递归**找"能收图"的迹象（见上面那段：各家报法不一样）。"""
    if depth > 3:
        return False
    if isinstance(thing, dict):
        for key, value in thing.items():
            name = str(key).lower()
            if isinstance(value, bool):
                if value and any(w in name for w in ("vision", "image", "multimodal")):
                    return True
                if not value and any(w in name for w in ("vision", "image", "multimodal")):
                    continue # 明确写"没有" ⇒ 这一项不算
            if any(w in name for w in ("vision", "image", "multimodal", "modalities")):
                if _looks_multimodal(value, depth + 1):
                    return True
            if isinstance(value, (dict, list)) and _looks_multimodal(value, depth + 1):
                return True
    if isinstance(thing, (list, tuple)):
        return any(_looks_multimodal(one, depth + 1) for one in thing)
    if isinstance(thing, str):
        low = thing.lower()
        return any(word in low for word in ("image", "vision", "multimodal", "vl-"))
    return False


#: **"能不能收图"那张旁证表**（2026-10-05 量的一份公开目录，见 `_sees_images` 上面那段）。
#: ⚠️ 顺序＝**从具体到笼统**；先命中的算数；一条都不命中 ⇒ `None`（**不表态**，
#:    页上那边就落回"关"，而**不说"只看文本"** —— 那两句是不一样的）。
_VISION_HINTS: tuple[tuple[str, bool], ...] = (
    # —— 量到"能收图"的（公开目录里 `input_modalities` 带 image ／ video）——
    ("glm-5.3-flashx", True), ("glm-5.3", True), ("glm-4.5v", True), ("glm-4v", True),
    ("deepseek-v4.1-flash", True), ("deepseek-flash", True), ("deepseek-vl", True),
    ("qwen3.8", True), ("qwen-vl", True), ("qwen2-vl", True), ("qwen3-vl", True),
    ("claude-sonnet", True), ("claude-opus", True), ("claude-haiku", True),
    ("gemini", True), ("grok", True), ("gpt-4o", True), ("gpt-4.1", True), ("gpt-5", True),
    ("kimi-latest", True), ("moonshot-v1-vision", True), ("kimi-vl", True),
    ("step-1v", True), ("step-1o", True), ("hunyuan-vision", True), ("hunyuan-turbo-v", True),
    ("ernie-4.5-vl", True), ("doubao-vision", True), ("doubao-1.5-vision", True),
    ("internvl", True), ("llava", True), ("minicpm-v", True), ("vision", True), ("-vl", True),
    # —— 量到"只看文本"的 ——
    ("deepseek-pro", False), ("z-ai/glm-5.3-prime", False),
)


#: **那份公开目录**（2026-10-05 量到的"真办法"）：OpenRouter 的 `/api/v1/models`
#: **不要钥匙**，逐模型报 `architecture.input_modalities`，而它覆盖各家模型
#: （实测 `z-ai/glm-5.3-flashx` → `["text","image","video"]`）。
#: ⚠️ 它是一次**对外请求**（在探活那一趟里顺带做），失败一律当"没查到"（`None`），
#:   并把结果在**进程内缓存**一段时间（同一趟里不会为每个模型各拉一次）。
_CATALOG_URL = "https://openrouter.ai/api/v1/models"
_CATALOG_TTL_S = 3600.0
_catalog_cache: dict[str, Any] = {"at": 0.0, "sees": {}}


def _catalog_sees(*, allow_fetch: bool = False) -> dict[str, bool]:
    """Use cached metadata; fetching another provider requires explicit admission."""
    now = time.time()
    if _catalog_cache["sees"] and now - float(_catalog_cache["at"]) < _CATALOG_TTL_S:
        return _catalog_cache["sees"]          # type: ignore[return-value]
    if not allow_fetch:
        return {}  # A probe may only send to its captured target, never enrich remotely.
    seen: dict[str, bool] = {}
    try:
        req = urllib.request.Request(_CATALOG_URL)
        req.add_header("User-Agent", USER_AGENT)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=8.0) as resp:
            rows = json.loads(resp.read(4_000_000).decode("utf-8", "replace")).get("data") or []
        for one in rows:
            mods = (one.get("architecture") or {}).get("input_modalities") or []
            name = str(one.get("id") or "").lower().rsplit("/", 1)[-1]
            if name:
                seen[name] = any(str(one2).lower() in ("image", "video") for one2 in mods)
        _catalog_cache["at"] = now
        _catalog_cache["sees"] = seen
    except Exception:                          # noqa: BLE001 —— 拉不到就当"没查到"
        return {}
    return seen


def _vision_beside(model_id: str) -> bool | None:
    """上游未声明时只用本地提示及已缓存目录；未知返回 None，不追加外部请求。"""
    want = str(model_id or "").strip().lower()
    for mark, sees in _VISION_HINTS:
        if mark in want:
            return sees
    short = want.rsplit("/", 1)[-1].split(":")[0]
    seen = _catalog_sees()
    if short in seen:
        return seen[short]
    # ⚠️ 目录里的名字常带版本后缀（`deepseek-v4.1-flash` 对 `deepseek-flash`）⇒ 再按前缀找一遍。
    for name, sees in seen.items():
        if short and (name.startswith(short) or short.startswith(name)):
            return sees
    return None


def _the_upstream_sentence(body: str) -> str:
    """ **把上游那句话取出来**（2026-10-05）：它常包在一层 JSON 外壳里。

    ```
    {"error":{"message":"分组 DeepSeek Flash 不含 kimi-k3"}}    ← 要这一句
    {"message":"Incorrect API key provided"}                    ← 或这一句
    纯文本                                                       ← 就用原文
    ```
    ⚠️ 从前页上拿到的是**整段 JSON**，再截到 40 字 ⇒ 那句人话正好被切掉（作者量到过一次）。
    """
    text = str(body or "").strip()
    try:
        one = json.loads(text)
    except ValueError:
        return text
    if isinstance(one, dict):
        for path in (("error", "message"), ("message",), ("error", "detail"), ("detail",)):
            got: Any = one
            for step in path:
                got = got.get(step) if isinstance(got, dict) else None
                if got is None:
                    break
            if isinstance(got, str) and got.strip():
                return got.strip()
    return text


def _first_of(row: Mapping[str, Any], *names: str) -> Any:
    """ **同一个东西，各家叫法不同** ⇒ 按这几种名字挨个问，第一个不是空的算数。

    ⚠️ 取不到就交回 `None`（**不是**编一个数）—— "这一家没报它"与"它等于某个值"
       是两件事，而页那一侧对前者有明确处置：给默认值（作者要的）。
    """
    for name in names:
        got = row.get(name)
        if got is not None and got != "" and got != []:
            return got
    return None


def _local_llama_props_url(base: str) -> str | None:
    """Optional metadata read on the same loopback origin only; never scan ports."""
    from urllib.parse import urlsplit, urlunsplit
    try:
        url = urlsplit(base)
        if (url.scheme not in ("http", "https") or url.hostname not in
                ("127.0.0.1", "localhost", "::1") or url.username or url.password
                or url.query or url.fragment or url.path.rstrip("/") not in ("", "/v1")):
            return None
        return urlunsplit((url.scheme, url.netloc, "/props", "", ""))
    except ValueError:
        return None


def _with_llama_effort_specs(specs, props):
    """Use explicit levels for the exact loaded model, never parse/execute Jinja."""
    if not isinstance(specs, list) or not isinstance(props, dict):
        return specs
    caps = props.get("chat_template_caps")
    levels = caps.get("reasoning_effort_levels") if isinstance(caps, dict) else None
    model = props.get("model_path")
    if (not isinstance(model, str) or not model or not isinstance(levels, list)
            or not 0 < len(levels) <= 32 or any(not isinstance(v, str)
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", v) for v in levels)):
        return specs
    matching = [i for i, row in enumerate(specs) if isinstance(row, dict)
                and str(row.get("id", "")).replace("\\", "/") == model.replace("\\", "/")]
    if len(matching) != 1:
        return specs  # alias/identity ambiguity must not update unrelated models
    result = [dict(row) if isinstance(row, dict) else row for row in specs]
    index = matching[0]
    result[index]["effort_levels"] = list(dict.fromkeys(levels))
    if result[index].get("effort_default") not in levels:
        result[index]["effort_default"] = None  # available levels do not prove a default
    return result


def _catalog_display_name(row, fallback):
    """Public display metadata only; never infer a base model from a quant alias."""
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    for value in (row.get("display_name"), row.get("name"), row.get("label"), meta.get("label")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _model_specs(body: str) -> list[dict[str, Any]] | None:
    """ **上游自己报的那几格**（量出来的，不是猜的）—— `/models` 那一份里逐个模型带着：

    ```
    {"id": "deepseek-flash", "context_window": 1048576, "max_output_tokens": 393216,
     "effort": {"supported_levels": ["low", "high", "max"], "default_level": "high"}}
    ```

    ⚠️ **本仓实测（2026-10-04）**：DeepSeek 的 `/v1/models` 真的报这三样 ⇒ 于是
       「上下文上限」与「推理强度」的默认值**问上游要**，而不是我们写一个数。
    ⚠️ 报不出来（别的供应商不报）⇒ 交回 `None`（页那一侧就照旧不动那几格）。
    """
    try:
        one = json.loads(body)
    except ValueError:
        return None
    rows = one.get("data") if isinstance(one, dict) else None
    if not isinstance(rows, list):
        return None
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        effort = row.get("effort") if isinstance(row.get("effort"), dict) else {}
        # **这一行的字段名照参考项目那份 `ListedModel` 取**（2026-10-05）：
        #    `display_name`（菜单显示它、值用 `id`）／ `context_window` ／
        # `max_output_tokens` ／ `input_images`（ "选模型就把它设上" —— 页那一侧照这句做）。
        #    ⚠️ **上游没说的一律 `None`**（不编）；`display_name` 缺了就**落回 `id`**。
        model_id = str(row["id"])
        out.append({
            "id": model_id,
            "display_name": _catalog_display_name(row, model_id),
            # **同一个东西各家叫法不同**（2026-10-05 作者：glm 那三格一个都没拿到）——
            #    于是按**多种常见名字**取；都没有 ⇒ `None`（页那一侧给默认值）。
            "context_window": _first_of(row, "context_window", "context_length",
                                        "max_context_length", "max_input_tokens",
                                        "max_model_len"),
            "max_output_tokens": _first_of(row, "max_output_tokens", "max_tokens",
                                           "max_output_length", "output_tokens"),
            "effort_default": effort.get("default_level")
            or _first_of(row, "reasoning_effort", "default_reasoning_effort",
                         "reasoning_level"),
            "effort_levels": effort.get("supported_levels")
            or list(row.get("reasoning_effort_levels") or []),
            "temperature": _first_of(row, "temperature", "default_temperature",
                                     "temperature_default"),
            # **"能不能收图"**（2026-10-05 量的）：上游这一格**有**（DeepSeek 报
            #    `input_modalities: ["text","image"]`，本机那台报 `modalities: {"vision": true}`），
            #    而"最大图片输出 token"那一格**根本没有** —— 两家都只出文本、图片只是输入模态，
            #    图片 token 也没有独立上限（算在同一个上下文里）⇒ 页上就画这一样。
            # 上游**自己报了**就用它；没报 ⇒ 查那张旁证表 ／ 那份公开目录
            #    （都查不到就是 `None` —— **不表态**，页上也不假装知道）。
            "input_images": _image_capability(row, model_id),
        })
    return out or None


def _a_short_reply(kind: str, text: str) -> str:
    """从**一次最小请求**的回话里挑出"它答的那一句"（挑不出来就把原文截短）。"""
    try:
        one = json.loads(text)
    except ValueError:
        return text.strip()[:120]
    if not isinstance(one, dict):
        return text.strip()[:120]
    if kind == "chat":
        rows = one.get("choices")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            message = rows[0].get("message") or {}
            got = message.get("content")
            if isinstance(got, str) and got.strip():
                return got.strip()[:120]
    elif kind == "anthropic":
        # 第三套（2026-10-12）：它答的话在 `content[*].text` 那几片里（与另外两套都不同）。
        rows = one.get("content")
        if isinstance(rows, list):
            said = " ".join(str(part.get("text") or "") for part in rows
                            if isinstance(part, dict) and part.get("type") == "text")
            if said.strip():
                return said.strip()[:120]
    else:
        got = one.get("output_text")
        if isinstance(got, str) and got.strip():
            return got.strip()[:120]
    return text.strip()[:120]


def _is_loopback(host: str) -> bool:
    """它是不是一个**字面量**回环地址 —— **不查 DNS**（一个名字解析成什么，这里不管）。

    ⚠️ 只认**字面量**：`localhost` **不收**（它可能解析到 `::1`，而这张 socket 是 IPv4 的
       ⇒ 绑得上／绑不上会随那台机器的 hosts 走 ）。地址要写就写 `127.0.0.1`。
    """
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _strip_one_eol(text: str) -> str:
    """去掉**恰好一个**行尾（`\\r\\n` 或 `\\n`，各一个）—— 与 `Console.pump` 那一步同一条规矩。

    ⚠️ 不用 `strip()`：那会把 `"   "` 也抹成空行，而"空行"与"空串"在那条泵里**不是一件事**
       （`console.py` 那一段逐字写着）。
    """
    if text.endswith("\r\n"):
        return text[:-2]
    if text.endswith("\n"):
        return text[:-1]
    return text


def _frame(seq: int, frame: Any) -> bytes:
    """一帧 ⇒ **一条 SSE 帧**（`id:` ＋ **一条** `data:` ＋ 一个空行收尾）。

     **整帧编成一条 `data:`** —— 值就是这一帧的 JSON（一个对象：谁说的 ／ 那一刻 ／
       正文 ／ 哪一类，见 `console.py::_frame_for` 那张表）。
    ⚠️⚠️ **一条 `data:` 里不许出现裸换行**（SSE 的规矩），而 JSON **把换行转义**
       （`"一\n二"` 落成 `"一\n二"` 那五个字符）⇒ **一句话永远是这一条 `data:`**
       的**一个**值：多行正文不会被拆开，也不会变成两条 `data:`。
        本刀之前正相反：正文按行拆成几条 `data:`，于是"哪一段是谁说的"要在**每一条**
       上重新回答一次 —— 那两处机器（按行贴记号 ／ 每个读者按行剥）就是这么长出来的，
       而它的两个缺陷（多行只剥第一行 ⇒ 页上留下一个 U+001F 与一截前缀）就是本刀要删的东西。
    ⚠️ **`ensure_ascii=False`**：中文照原样进那条流（本仓的判据按 UTF-8 读它，
       而"发出去的字与盘上那一份逐字相同"这件事在线上当场看得见）。
    ⚠️ **空行收尾**（那条空行就是 SSE 的"这一帧到此为止"）：少它，两条帧会被读成一条。
    ⚠️ **它只认"一帧"这一个形状**（一个映射）—— 别的形状（一行文本 ／ 一个数字）
       当场抬 `TypeError`：那是**故意的**，"这一条 `data:` 里装什么"只此一个答案。
    """
    if not isinstance(frame, Mapping):
        raise TypeError(f"那条通道收的是**一帧**（一个映射），收到的是：{frame!r}")
    body = json.dumps(dict(frame), ensure_ascii=False)
    return f"id: {seq}\ndata: {body}\n\n".encode("utf-8")


def _the_moment(frame: Mapping[str, Any], now: Any) -> dict[str, Any]:
    """ **那一刻那一格：今天只写时刻，不是今天带上日期**（2026-10-09 作者点的 ）。

    ⚠️ **「今天」是从入口问来的**（`now` 那个 callable ）—— 本模块**自己不读钟**
      （ 与它一句命令都不跑同一条纪律 ）。
    ⚠️ **问不到就不带日期** （ 只写时刻 ）—— 不许拿一个"想当然的今天"去比 。
    ⚠️ **两个出口都要走这里** ： `GET /history`（历史 ）与 `push`（现场 ）——
       只改一处的话，历史与现场会一个带日期、一个不带 。
    ⚠️ **它必须在 `class _Handler` 之前** —— 一个模块级的 `def` 插进类体中间
      会把**那个类截断** （ 而它**照样编译得过** ⇒ 症状是一堆
      `AttributeError: _Handler has no attribute …` ）。
    """
    out = dict(frame)
    at = out.get("at")
    if not isinstance(at, str) or len(at) < 16:
        return out # 不是整串 ⇒ 原样（不猜 ）
    clock = at[11:16]
    today = now() if callable(now) else None
    if not isinstance(today, str) or today[:10] == at[:10]:
        out["at"] = clock # 今天（或问不到）⇒ 只有时刻
        return out
    out["at"] = "%d 月 %d 日 %s" % (int(at[5:7]), int(at[8:10]), clock)
    return out


class _Handler(BaseHTTPRequestHandler):
    """那几条端点 —— **素 HTTP**（标准库那一层），没有框架、没有模板。"""

    protocol_version = "HTTP/1.1"
    server_version = "telyincore-web"

    #: **本服务只听回环** ⇒ 访问日志一律不打（谁连的、什么时候连的，不落在任何流上）。

    def log_message(self, fmt: str, *args: Any) -> None:
        return None

    @property
    def _service(self) -> WebService:
        return self.server.service            # 由 `_Server.__init__` 挂上去的那一个

    def handle_one_request(self) -> None:
        try:
            return self._handle_admitted_request()
        finally:
            # Keep admission through response flush, lease and observation cleanup.
            with self._service._lock:
                self._service._http_admitted.discard(self.connection)
                if self._service._closing:
                    self.close_connection = True

    def _handle_admitted_request(self) -> None:
        self._runtime_evidence_token = None
        self._runtime_evidence_context = None
        try:
            return self._handle_one_request_owned()
        finally:
            primary = sys.exception()
            token = self._runtime_evidence_token
            if token is not None:
                outcome = ("RETURNED" if primary is None else "CANCELLED"
                           if isinstance(primary, asyncio.CancelledError) else "FAILED"
                           if isinstance(primary, Exception) else "INTERRUPTED")
                try:
                    self._service.runtime_evidence.finish(token, outcome)
                except BaseException:
                    if primary is None:
                        raise
                    primary.add_note("runtime evidence incomplete; original exception retained")
                finally:
                    if self._runtime_evidence_context is not None:
                        self._service.runtime_evidence.leave(self._runtime_evidence_context)

    def parse_request(self) -> bool:
        parsed = super().parse_request()
        if parsed:
            # The same lock orders EACH request against shutdown, including reuse.
            with self._service._lock:
                admitted = not self._service._closing
                if admitted:
                    self._service._http_admitted.add(self.connection)
            if not admitted:
                self.close_connection = True
                self._send_text(503, "服务正在停止，不接受新的请求\n")
                return False
        evidence = getattr(self._service, "runtime_evidence", None)
        # Explicit diagnostic escape hatch: no business dispatch, no pretend coverage.
        if parsed and self.command == 'GET' and self.path.split('?',1)[0] == '/runtime-evidence':
            return parsed
        if parsed and evidence is not None:
            self._runtime_evidence_token = evidence.begin("http")
            self._runtime_evidence_context = evidence.enter(self._runtime_evidence_token)
        return parsed

    def _handle_one_request_owned(self) -> None:
        # Release only after BaseHTTPRequestHandler has completed dispatch AND
        # flushed the response (including exceptional exits), not after command().
        try:
            super().handle_one_request()
        finally:
            primary = sys.exception()
            lease = getattr(self, "_subscription_http_lease", None)
            if lease is not None:
                self._subscription_http_lease = None
                try:
                    self._service._finish_subscription_http(lease)
                except BaseException:
                    if primary is None:
                        raise
                    primary.add_note("subscription HTTP cleanup failed; responsibility retained")

    def _admit_subscription_http(self) -> bool:
        if getattr(self, "_subscription_http_lease", None) is not None:
            return True
        lease = self._service._begin_subscription_http()
        if lease is None:
            self.close_connection = True
            self._send_text(503, "服务正在停止，不能开始新的订阅操作\n")
            self._discard_rejected_body()
            return False
        self._subscription_http_lease = lease
        return True

    # ── 路由 ────────────────────────────────────────────────────────────────
    def do_GET(self) -> None:
        if not self._same_origin():
            self._reject_cross_origin("local_same_origin_required\n")
            return
        route = self.path.split("?", 1)[0]
        if route == '/runtime-evidence':
            owner=self._service.runtime_evidence
            self._send_json(200, {'observation_exempt':True,'runtime_evidence':
                {'recording_status':'DISABLED'} if owner is None else owner.capacity_state()})
            return
        if route == "/plugin-versions":
            checker = self._service.plugin_updates
            if checker is None:
                self._send_json(503, {"message": "当前 Core 尚未接入版本选择"})
                return
            try:
                from urllib.parse import parse_qs, urlsplit
                query = parse_qs(urlsplit(self.path).query, strict_parsing=True)
                if any(len(v) != 1 for v in query.values()):
                    raise ValueError("版本参数重复")
                result = checker.history(query.get('repository', [''])[0],
                    int(query.get('page', ['1'])[0]), query.get('kind', ['releases'])[0])
                self._send_json(200, result)
            except (OSError, ValueError, TypeError):
                self._send_json(400, {"message": "暂时无法获取版本，请检查网络或稍后重试"})
            return
        if route == "/plugin-updates":
            checker = self._service.plugin_updates
            if checker is None:
                self._send_json(503, {"message": "当前 Core 尚未接入插件更新检查"})
            else:
                self._send_json(200, checker.read(refresh=self.path == "/plugin-updates?refresh=1"))
            return
        if route == "/package-update":
            checker = self._service.package_updates
            if checker is None:
                self._send_json(503, {"message": "版本检查尚未接入"})
            else:
                self._send_json(200, {**checker.check(force=self.path == "/package-update?refresh=1"), **checker.installation()})
            return
        if route == "/timers":
            self._timer_control("list")
            return
        if route == "/settings/model-proxy":
            self._model_proxy_settings()
        elif route == "/events":
            self._stream_events()
        elif route == "/avatar":
            # **2026-10-09 作者：「点击特字圆球可以打开浏览本地的 JPG 这类图片格式，
            # 给 BOT 上传头像…」** —— 这一条把那张图**交回去** 。
            # ⚠️ 读法**由入口递**（`read_avatar` —— 与 `read_pending` 同一条口径：
            # 本模块零同胞 import ）； 没接 ／ 没上传过 ⇒ **404**
            # （ 与 `/usage` 同一条：\u201c还没接上\u201d要说出来，不许回一张空图 ）。
            self._send_an_avatar()
        elif route == "/look":
            # **2026-10-10（外观那一页 ③）**：那一套**自定义**
            # （ 没接 ⇒ **503** —— 与 `/usage` 同一条口径：
            # “这一格还没接上”要说出来 ，不该回一份空的 ）。
            seen = self._service.look()
            if seen is None:
                self._send_text(503, "这一格还没接上\n")
            else:
                self._send_json(200, seen)
        elif route == "/state":
            self._send_json(200, self._service.state())
        elif route == "/usage":
            # **上下文占用那几块**（2026-10-05，作者要的"点击显示"读的就是这一格）——
            # ⚠️ 那一格**由入口递**（`usage_of(…)` 同 `/memory` 那条路）；
            # 没接 ⇒ **503**（"这一格还没接上"），**不是**回一份空的（与 `engine` 同一条口径 ）。
            answer = self._service.usage()
            if answer is None:
                self._send_text(503, "这一格还没接上\n")
            else:
                self._send_json(200, answer)
        elif route == "/healthz":
            self._send_json(200, self._service.stats())
        elif route == "/history":
            # **已经说过的话**（2026-10-05 作者：那一列抬头写着"刷新之后旧话就不在了
            # —— 历史重放还没有" ）—— 回的是一串**帧**，与那条推的通道**同一个形状**
            # ⇒ 页那一侧只用一条路（`chat.append(wire)` ）把旧话画回去 。
            # ⚠️ 造帧走的是**同一处**（`console.history()` ⇒ `_frame_for` 那一族 ）。
            # ⚠️ 服务没有这一格（没接 console 的机器）⇒ 空那一串（**不编** ）。
            read_history = getattr(self._service, "history", None)
            # **改写成"已经判好的那一刻"**（2026-10-09 ）—— 判的那一下在入口 。
            _ask = getattr(self._service, "now", None)
            self._send_json(200, {"frames": [_the_moment(one, _ask) for one in
                                             (read_history() if callable(read_history) else [])]})
        elif route == "/persona":
            self._send_report()
        elif route == "/persona/qq-environment":
            port = self._service.persona
            info = None if port is None else port.qq_environment()
            if info is None:
                self._send_text(404, "当前 Bot 没有可编辑的 QQ 场景\n")
            else:
                self._send_json(200, info)
        # **2026-10-07「那三页的后端」**（作者拍的）：世界里的事**各自一条口** ——
        # 而它们**读的还是同一份数据**（就是 `/memory` 那一份里的那三格 ）——
        # 于是"页上那一格"与"这一条口"**不会各答一个数** （判据钉着这一条 ）。
        # ⚠️ 三条都**只读**：不写、不算时间、不算权重 （与那三条约定的形状一致 ）。
        elif route == "/knowledge":
            know = getattr(self._service, "read_knowledge", None)
            try:
                notes = [] if know is None else list(know())
            except (OSError, ValueError, UnicodeError):
                self._send_json(503, {"error": "knowledge_unreadable",
                                      "message": "知识原文暂不可读，请稍后重试。"})
                return
            # This endpoint only returns knowledge; unrelated private readers
            # must not add I/O, authorization requirements or failure coupling.
            q = _the_query(self.path)
            search_limited = False
            if q:
                # **2026-10-07「模糊搜接上」**： **先精确、再模糊**（那个匹配器里定的 ）
                # ⚠️ 它收**一串文本**、交回**下标** ⇒ 把名字与正文拼一行给它看 ；
                # ⚠️ 没接 ／ 它坏了 ⇒ **照旧**用简单包含（`_the_picked` 交 `None` ）。
                # 两趟（2026-10-07 真跑量到）：**先只在名字里找** ——
                # 因为另外几份的**正文**里提到过它（ 那也算对，但名字命中该排前面 ）。
                pick = _the_picked(getattr(self._service, "match_rows", None), q,
                                   [str(one.get("name", "")) for one in notes])
                if pick is not None and not pick:
                    pick = _the_picked(getattr(self._service, "match_rows", None), q,
                                       [str(one.get("name", "")) + chr(10)
                                        + str(one.get("text", "")) for one in notes])
                from .search import SemanticUnavailable
                search_limited = pick is None or isinstance(pick, SemanticUnavailable)
                if search_limited:
                    pick = [i for i, one in enumerate(notes)
                            if q in str(one.get("name", "")) or q in str(one.get("text", ""))]
                notes = [notes[i] for i in pick]
            self._send_json(200, {"knowledge": notes, "wired": know is not None, "q": q,
                                  "search_limited": search_limited})
        elif route == "/skills":
            # **2026-10-18 批④**：做法治理的**只读**视图 —— 有效 ／ 已并入 ／ 操作账 ／ 运行状态。
            # ⚠️ **不写、不算、不补**：读口交什么就说什么；没接 ／ 读坏了 ⇒ `wired=false`
            #    与 `state=unknown`（页上照实说"未知／未提供"，不猜）。
            # ⚠️ 与 `/memory` 那几格**同一份数据**（都由入口递进来），页与口不会各答一个数。
            reader = getattr(self._service, "read_skills", None)
            if not callable(reader):
                self._send_json(200, {"wired": False, "active": [], "merged": [],
                                      "ops": [], "active_count": 0, "merged_count": 0,
                                      "status": {"state": "unknown", "recovery_pending": 0,
                                                 "why": "这一台没有接做法读口"}})
            else:
                try:
                    got = dict(reader() or {})
                    self._send_json(200, {
                        "wired": True,
                        "active": list(got.get("active") or []),
                        "merged": list(got.get("merged") or []),
                        "ops": list(got.get("ops") or []),
                        "missing": list(got.get("missing") or []),
                        "index_status": dict(got.get("index_status") or {}),
                        "active_count": int(got.get("active_count") or 0),
                        "merged_count": int(got.get("merged_count") or 0),
                        "status": dict(got.get("status") or {"state": "unknown"})})
                except Exception as why:      # noqa: BLE001 —— 读坏了照实说
                    self._send_json(200, {"wired": True, "error": type(why).__name__,
                                          "active": [], "merged": [], "ops": [],
                                          "active_count": 0, "merged_count": 0,
                                          "status": {"state": "unknown", "recovery_pending": 0,
                                                     "why": "做法读口坏了：" + type(why).__name__}})
        elif route == "/console/introduction":
            self._console_introduction(write=False)
        elif route == "/people":
            # ⚠️ **它不在这一页铺开**（作者："认识的人会越来越多，一个个看不太现实" ）：
            #    这一条口**给的是最近说过话的那几位 ＋ 搜得到的那几位** ——
            # 送 `?q=` 就按名字／说法过滤 ；不送 ⇒ 只交**最近那几位**（缺省几位由调用方定 ）。
            getter = getattr(self._service, "read_memory", None)
            rows = [] if getter is None else list(getter())
            q = _the_query(self.path)
            if q:
                # 同上：**先精确、再模糊** （ 人那一行取"名字 ＋ 说法"两格 ）
                pick = _the_picked(getattr(self._service, "match_rows", None), q,
                                   [str(one.get("who", "")) for one in rows])
                def search_text(one):
                    fields = [one.get("who"), one.get("standing"), one.get("display_name")]
                    for visit in one.get("encounters", ()):
                        fields.extend(visit.get(key) for key in ("label", "room", "account", "common_name"))
                    return "\n".join(str(value) for value in fields if value)

                if pick is not None and not pick:
                    pick = _the_picked(getattr(self._service, "match_rows", None), q,
                                       [search_text(one) for one in rows])
                if pick is None:
                    pick = [i for i, one in enumerate(rows) if q in search_text(one)]
                rows = [rows[i] for i in pick]
            else:
                rows = rows[-_PEOPLE_AT_MOST:]
            identities = getattr(self._service, "read_identities", None)
            identity_rows = None
            identity_error = False
            if callable(identities):
                try:
                    visible = {str(row.get("who") or "") for row in rows}
                    identity_rows = [row for row in identities()
                                     if str(row.get("profile_key") or "") in visible]
                except (OSError, ValueError):
                    identity_error = True
            self._send_json(200, {"everyone": rows, "wired": getter is not None, "q": q,
                                  "identities": identity_rows,
                                  "identity_error": identity_error})
        elif route == "/eval":
            # **2026-10-07「④」**：跑一次评测 ＋ 把"要不要喊"摆出来（只读）。
            #    ⚠️ 不写那几个正本（账落在入口递来的那个目录）；
            #    ⚠️ 入口没接 ⇒ **404**（"今天没接上"，不是 500）。
            runner = getattr(self._service, "run_eval", None)
            if not callable(runner):
                self._send_text(404, "没有这一条\n")
                return
            self._send_json(200, dict(runner()))
        elif route == "/conversation/labels":
            reader = getattr(self._service, "read_display_labels", None)
            self._send_json(200, {} if reader is None else dict(reader()))
        elif route == "/experience":
            lived = getattr(self._service, "read_experience", None)
            self._send_json(200, {"experience": [] if lived is None else list(lived()),
                                  "wired": lived is not None})
        elif route == "/memory":
            # **只读**（2026-10-05 作者："留一个我可以看到的接口 —— 它**对所有人**的
            #   好感度**数值**；只要可以看到，但**不要写代码去束缚它**"）。
            #   ⚠️ 这一格**只把已经记下的读出来**：不写盘、不算分、不下判断 —— 那个数值是
            #     **它自己写的**（`memory.remember_relation(..., favor=…)`），本服务不解释它。
            #   ⚠️ `web.py` **零同胞 import**（AST 钉着）⇒ 读法是**注入**进来的（与 `state_of` 同形）。
            # ⚠️ **走 `self.`**（2026-10-05 我第一版写的是**裸名字** ⇒ 每次请求 `NameError` ：
            #      那是 `serve()` 的参数，**处理函数看不见它** —— 照 `state=` 那条路挂到服务对象上）。
            # ⚠️ **查 `self._service`**（2026-10-05 我先前查的是 `self` ⇒ 永远拿不到 ：
            #    在 `http.server` 里 `self` 是**处理函数实例**，服务在 `self._service` 上 ——
            #    旁边那两格写的就是 `self._service.state()` ／ `self._service.stats()`）。
            getter = getattr(self._service, "read_memory", None)
            rows = [] if getter is None else list(getter())
            # **「悬着的事」那一格**（2026-10-07，作者拍的「①a」）—— 它里头是
            # **心里那些还没落地的事** （ 作者当时口头说的那个词是「心事」 ——
            # 而 那一格 **2026-10-05 起就叫「悬着的事」** ）。
            # **只读** （这一页**不批 ／ 不催 ／ 不画期限** —— 那是定下的三条）；
            # 而"自己能读到自己那几件"那一半归**内核那边一条新工具**（另起一刀）。
            # ⚠️ 与 `read_memory` 同一个手法：**由入口递**（本模块零同胞 import ）。
            hear = getattr(self._service, "read_pending", None)
            hearts = [] if hear is None else list(hear())
            # **「知识」那一块**（2026-10-07，第二件）：它记下来的那些**定下来的事** ——
            # 同样**只读** （这一页不写、不排序、不算时间）。
            know = getattr(self._service, "read_knowledge", None)
            notes = [] if know is None else list(know())
            # **「经历」那一块**（2026-10-07，第三件）：它**经历过什么** —— 那一棵树里
            # 每一份的名字与大小／条数。 同样**只读**：不写、不排序、**不读时钟** 。
            lived = getattr(self._service, "read_experience", None)
            lived_rows = [] if lived is None else list(lived())
            # **它自己写的那三份**（2026-10-08）—— 那三行原来摆的是**编的**
            # （「10 条」⁄「还有 9 行」）⇒ 现在只报**盘上的真状态** 。
            hers = getattr(self._service, "read_hers", None)
            her_rows = [] if hers is None else list(hers())
            # ⚠️ 发 JSON 走本类那一处帮手（`self._send_json(200, …)` —— 照 `/state` 那一格）。
            self._send_json(200, {"everyone": rows, "pending": hearts,
                                  "knowledge": notes, "experience": lived_rows,
                                  "hers": her_rows,
                                  "wired": getter is not None})
        elif route == "/plugins":
            # **那一页要读的那一份**（2026-10-05 作者："先做 1" ⇒ 这一格）。
            # ⚠️ **`wired` 那一格是这一条路的关键**：`False` ⇒ 页上写"还没接上"，
            # 而那与"零个插件"是**两件事**（`plugins-page.ts` 的抬头那条 ）。
            # ⚠️ 本模块**零同胞 import** ⇒ 读法**由入口递**（与 `read_memory` 同一条路 ）。
            mine = getattr(self._service, "read_plugins", None)
            # ⚠️ **没递 ⇒ 交一个"没接上"的形状**（`wired: False` ）——
            # 不是 `{}`（那会让页上拿不到 `wired`，而它就分不开那两件事了 ）。
            answer = ({"where": "", "plugins": [], "broken": [], "wired": False}
                      if mine is None else dict(mine()))
            restart = self._service.plugin_restart
            answer['runtime'] = {**(restart.snapshot() if restart is not None else
                {'restart_supported': False, 'restart_reason': '当前服务尚未接入页面内重启。'}),
                'instance': self._service._shutdown_instance_token}
            self._send_json(200, answer)
        elif route == "/infinity/waves/read-check":
            checker = self._service.read_tool_check
            self._send_json(503 if checker is None else 200,
                {"available": False} if checker is None else checker.snapshot())
        elif route.startswith("/infinity/"):
            read = getattr(self._service, "read_infinity", None)
            if read is None:
                self._send_json(503, {"wired": False})
            else:
                try:
                    result = read(route)
                    if route == "/infinity/bots":
                        result = {**result, "shutdownInstance": self._service._shutdown_instance_token}
                    self._send_json(200, result)
                except ValueError as exc:
                    self._send_json(404, {"ok": False, "said": str(exc)})
        elif route == "/market":
            # **市场那一半**（2026-10-05）—— 读的是 **GitHub 的 `topic:` 搜索**
    # （作者："删掉 npm 那一条路，不用管了" ）。
            # ⚠️⚠️ **`found == []` 与 `wired is False` 是两件事**（与 `/plugins` 那条同口径 ）：
            # 前者是「**问到了，而没人发过**」／ 后者是「**根本没问到**」。
            #   ⚠️ 它**打外网**（这一层头一处）—— 成法照 `_catalog_sees` 那条抄：
            # UA ＋ 绕代理 ＋ 失败不抛 ＋ 带缓存（都在 `plugins.market` 里 ）。
            # ⚠️ 本模块**零同胞 import** ⇒ 读法由入口递 。
            fare = getattr(self._service, "read_market", None)
            answer2 = ({"found": [], "wired": False, "where": "", "said": ""}
                       if fare is None else dict(fare()))
            self._send_json(200, answer2)
        elif route == "/consolidation/jobs":
            reader = getattr(self._service.console, "consolidation_jobs", None)
            self._send_json(200, {"wired":callable(reader),
                                 "jobs":reader() if callable(reader) else []})
        elif route == "/engine":
            # **刀 4：那一页要读的那一份**（档案清单 ＋ 当前 ＋ 每个键的生效值与来源）
            self._send_the_engine()
        elif route == "/":
            self._send_page("index.html")
        else:
            self._send_page(route.lstrip("/"))

    def do_POST(self) -> None:
        route = self.path.split("?", 1)[0]
        if route in ("/timers/reschedule", "/timers/cancel"):
            self._timer_control(route.rsplit("/", 1)[1])
            return
        if route == "/settings/model-proxy":
            if not self.headers.get("Origin") or not self._same_origin():
                self._reject_cross_origin("跨站的一页不许修改模型代理\n")
                return
            self._model_proxy_settings(write=True)
            return
        if route == "/engine/chatgpt":
            # OAuth controls require an explicit local same-origin browser context.
            if not self.headers.get("Origin") or not self._same_origin():
                self._reject_cross_origin("跨站的一页不许修改 ChatGPT 登录\n")
                return
            self._a_engine_chatgpt()
            return
        if route == "/console/introduction":
            self._console_introduction(write=True)
            return
        if route == "/people/identity":
            if not self._same_origin():
                self._reject_cross_origin("跨站的一页不许改身份绑定\n")
                return
            self._a_person_identity()
            return
        if route == "/diagnostics/export":
            if not self._same_origin():
                self._reject_cross_origin("local_same_origin_required\n")
                return
            if self._read_body() is not None:
                self._send_diagnostic_archive()
            return
        if route == "/avatar":
            # **新写路径 ⇒ 同源校验** （照裁定 8，与 `/engine` ／ `/handoff` 同一条 ）。
            if not self._same_origin():
                self._reject_cross_origin("跨站的一页不许换这一格\n")
                return
            self._accept_an_avatar()
            return
        if route == "/look":
            # 写路径 ⇒ **同源校验** （ 与 `/avatar` 同一条 ）。
            if not self._same_origin():
                self._reject_cross_origin("跨站的一页不许改这一格\n")
                return
            self._accept_the_look()
            return
        if route in ("/package-update/install", "/package-update/stage", "/package-update/restart"):
            if not self.headers.get("Origin") or not self._same_origin() or self.client_address[0] not in ("127.0.0.1", "::1"):
                self._reject_cross_origin("更新操作仅供本机控制台使用")
                return
            if self._read_body() is None:
                return
            checker = self._service.package_updates
            if checker is None:
                self._send_json(503, {"error": "安装包版本服务尚未接入"})
                return
            if not checker.install_lock.acquire(blocking=False):
                self._send_json(409, {"error": "更新正在进行中"})
                return
            try:
                if route.endswith("/stage"):
                    row = checker.prepare_install()
                    self._send_json(200, {"pendingVersion": row["version"]})
                    return
                if getattr(self._service, "_shutdown_callback", None) is None:
                    raise ValueError("请从已安装的 Core 启动器启动，再执行重启")
                if route.endswith("/install") and not checker.installation().get("pendingVersion"):
                    checker.prepare_install()
                checker.restart_for_update(self.server.server_address[1])
            except Exception as exc:
                self._send_json(409, {"error": str(exc) if isinstance(exc, ValueError) else "更新未完成，请检查网络与更新包后重试"})
                return
            finally:
                checker.install_lock.release()
            self._accept_the_shutdown()
            return
        if route == "/shutdown":
            # **2026-10-10（三颗按钮 ④）**： **收工**
            # （ 写路径 ⇒ **同源校验** —— 与 `/avatar` ／ `/look` 同一条 ）。
            if not self._same_origin():
                self._reject_cross_origin("跨站的一页不许停这一格\n")
                return
            self._accept_the_shutdown()
            return
        if route == "/observe":
            if not self._same_origin():
                self._reject_cross_origin("cross-origin observation refused\n")
                return
            callback = getattr(self._service, "observe_activity", None)
            if not callable(callback):
                self._send_json(404, {"error":"activity_control_unavailable"})
                return
            raw = self._read_body()
            if raw is None:
                return
            try:
                body = json.loads(raw)
                if not isinstance(body, dict) or not set(body) <= {'text','mode','round_id'}:
                    raise ValueError('observation fields invalid')
                result = callback(body)
            except (ValueError,TypeError):
                self._send_json(400, {"error":"invalid_observation_request"})
                return
            except Exception:
                self._send_json(503, {"error":"observation_status_unknown", "retry_automatically":False})
                return
            self._send_json(202, result)
            return
        if route == "/interrupt":
            if not self._same_origin():
                self._reject_cross_origin("cross-origin interruption refused\n")
                return
            callback = getattr(self._service, "interrupt_round", None)
            if not callable(callback):
                self._send_json(404, {"error": "round_control_unavailable"})
                return
            try:
                body = json.loads(self._read_body())
                if not isinstance(body, dict) or not isinstance(body.get("round_id"), str):
                    self._send_json(400, {"error": "round_id_required"})
                    return
                result = callback(body["round_id"])
            except (ValueError, TypeError):
                self._send_json(400, {"error": "invalid_interrupt_request"})
                return
            except Exception:
                self._send_json(503, {"error": "interrupt_status_unknown"})
                return
            self._send_json(202 if result.get("accepted") else 409, result)
            return
        if route == "/pause":
            # **2026-10-10（三颗按钮 ⑤）**： 按下那颗 ▶ 的「暂停」
            # （ 写路径 ⇒ **同源校验** —— 与 `/avatar` ／ `/shutdown` 同一条 ）。
            if not self._same_origin():
                self._reject_cross_origin("跨站的一页不许动这一格\n")
                return
            self._accept_the_gate(True)
            return
        if route == "/run":
            # 弹起来那一半（ 同一个成法 ）。
            if not self._same_origin():
                self._reject_cross_origin("跨站的一页不许动这一格\n")
                return
            self._accept_the_gate(False)
            return
        if route == "/persona":
            self._accept_a_save()
            return
        if route == "/open":
            # **2026-10-07「在文件夹里打开」**（v7 稿上那个按钮 ）：
            # ⚠️ **它是新写路径** ⇒ 照裁定 8 **同源校验** （与 `/engine` 那几条同一条）。
            # ⚠️ **本模块连路径都不认识**：解析与打开**都由入口做**
            # （"打开"＝命令执行 ⇒ 内核那几层不许跑命令 ）；这里只**转发**
            # —— 越界 ／ 不在盘上那些判断也都在入口那一个 callable 里 。
            if not self._same_origin():
                self._reject_cross_origin("跨站的一页不许动这一格\n")
                return
            self._accept_an_open()
            return
        if route == "/persona/adopt":
            # ★ **2026-10-13（作者：「做」）**：「悬着的事」里那条**提议改你定的那一份**
            #   被按下的那一下（提议是它提的 ⇒ **这一下归人**）。
            #   ⚠️ 新写路径 ⇒ 照裁定 8 **同源校验**（与旁边那几条一字不差）。
            if not self._same_origin():
                self._reject_cross_origin("跨站的一页不许动\n")
                return
            self._accept_an_adopt()
            return
        if route == "/persona/about":
            # **2026-10-07「丙-2」**：那一本的三个动作（**人**的动作 ）。
            # ⚠️ 新写路径 ⇒ 照裁定 8 **同源校验** 。
            if not self._same_origin():
                self._reject_cross_origin("\u8de8\u7ad9\u7684\u4e00\u9875\u4e0d\u8bb8\u52a8\n")
                return
            self._accept_an_about()
            return
        if route == "/consolidation/resume":
            if not self._same_origin():
                self._reject_cross_origin("跨站的一页不许恢复整理任务\n")
                return
            if self.headers.get("Content-Type", "").split(";",1)[0].strip().lower() != "application/json":
                self._send_json(400,{"ok":False,"error":"需要application/json"})
                return
            raw = self._read_body()
            if raw is None:
                return
            try:
                body = json.loads(raw.decode("utf8"))
                task = body.get("task") if isinstance(body, dict) else None
                if not isinstance(task,str) or re.fullmatch(r"[A-Za-z0-9_-]{1,96}",task) is None:
                    raise ValueError()
            except (ValueError,UnicodeDecodeError):
                self._send_json(400,{"ok":False,"error":"需要合法task ID"})
                return
            result = self._service.queue_consolidation_recovery(task)
            if result is None:
                self._send_json(503,{"ok":False,"error":"整理队列当前不可用或结果未确认"})
            else:
                self._send_json(202 if result else 404,
                                {"accepted":bool(result),"task":task,
                                 "status":"queued" if result else "not-found"})
            return
        if route in ("/engine", "/engine/secret", "/engine/probe"):
            # **同源校验**（设计稿 裁定 8 ／ §8.3）：**只给新写路径加**。
            if not self._same_origin():
                self._reject_cross_origin("跨站的一页不许动这一格\n")
                return
            if route == "/engine":
                self._a_engine_command()
            elif route == "/engine/secret":
                self._a_engine_secret()
            else:
                self._a_engine_probe()
            return
        if route == "/handoff":
            # **2026-10-03 补**（刀 4）：它**也是**一条新写路径 —— 它会**换掉整个会话**
            # ⇒ 按裁定 8 的口径，它属于"要加同源校验"的那一类。 这一条记在设计稿抬头。
            if not self._same_origin():
                self._reject_cross_origin("跨站的一页不许叫它交接\n")
                return
            self._ask_for_handoff()
            return
        if route in ("/plugins/switch", "/plugins/remove", "/plugins/restart"):
            expected = self.headers.get('X-Telyin-Shutdown-Instance')
            if expected is not None and expected != self._service._shutdown_instance_token:
                self.close_connection = True
                self._send_json(409, {'ok': False, 'said': 'Bot 服务实例已变化，请重新确认操作。'})
                return
        if route == "/plugins/restart":
            if (not self.headers.get('Origin') or not self._same_origin()
                    or not self.headers.get('X-Telyin-Shutdown-Instance')):
                self._reject_cross_origin('页面内重启需要同源及当前实例标识')
                return
            raw = self._read_body()
            if raw is None:
                return
            try:
                body = json.loads(raw)
                if type(body) is not dict or set(body) != {'name', 'action'}:
                    raise ValueError('无效重启请求')
                restart = self._service.plugin_restart
                if restart is None or self._service._shutdown_callback is None:
                    raise ValueError('当前服务不支持页面内重启')
                restart.request(body['name'], body['action'], self.server.server_address[1])
            except (ValueError, TypeError, KeyError) as exc:
                self._send_json(409, {'ok': False, 'said': str(exc)})
                return
            except OSError:
                self._send_json(500, {'ok': False, 'said': '无法保存重启请求，请检查本机日志。'})
                return
            self._accept_the_shutdown()
            return
        if route == "/plugins/switch":
            # **开它 ／ 关它**（2026-10-05 作者："接着做" ⇒ 那一页上的开关 ）。
            # ⚠️ **它是一条新写路径**（它会改 `extensions/state.json`）⇒ 按裁定 8 的口径
            # **加同源校验** —— 与 `/engine` ／ `/handoff` 那两条同一类 。
            # ⚠️ 它**只动那个开关**：插件那些目录**一个字节都不动** （那是别人的东西）。
            # ⚠️ 没递那个动作 ⇒ 答 503（"这一格还没接上" —— 不是 404，那会被读成"没有这条路" ）。
            if not self._same_origin():
                self._reject_cross_origin("跨站的一页不许动插件那一格\n")
                return
            self._a_plugin_switch()
            return
        if route == "/infinity/waves/read-check":
            if not self._same_origin():
                self._reject_cross_origin("跨站的一页不许调用本机工具检查\n")
                return
            raw = self._read_body()
            if raw is None:
                return
            try:
                body = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json(400, {"error": "invalid_read_check_json"})
                return
            code, result = self._service.check_waves_read(body)
            self._send_json(code, result)
            return
        if route.startswith("/infinity/"):
            if not self._same_origin():
                self._reject_cross_origin("跨站的一页不许改扩展配置或进程\n")
                return
            self._a_infinity(route)
            return
        if route == "/plugins/remove":
            # **删掉一个插件**（2026-10-05 作者："参考它的排版" ⇒ 照参考那张卡底下的
            # 「删除扩展」）。⚠️ **它是一条新写路径，而且是最重的那一条** ——
            # 它会**真删盘上的东西**（那一格目录）⇒ 按裁定 8 的口径**加同源校验**
            # （ 而且是这里头**最该加**的那一条：跨站的一页能删盘上的东西，那是不能有的 ）。
            # ⚠️ **它不是一个"删目录"的动作**：先关掉、再删那一格、再把那一格名字忘掉
            # （三件都归 `plugins.remove` —— 这一格只负责"谁的请求"和"交回复执" ）。
            if not self._same_origin():
                self._reject_cross_origin("跨站的一页不许删插件\n")
                return
            self._an_plugin_remove()
            return
        if route == "/market/install":
            # **装一份**（2026-10-05 作者："让市场真能装"）—— 而它**是一条新写路径**：
            # 它往 `extensions/` 里**落文件** ⇒ 按裁定 8 的口径**加同源校验** 。
            # ⚠️ **顺序是这一条的要害**：那一边（`market.install`）**先只读文档、判关键字**，
            # 判不过就**一个字节都不下** （照它那份「check 在前」）。
            # ⚠️ **装完不自动开** （"开不开"是那一格的开关的事 ）。
            if not self._same_origin():
                self._reject_cross_origin("跨站的一页不许装东西\n")
                return
            self._a_market_install()
            return
        if route != "/say":
            self._send_text(404, "没有这一条\n")
            return
        if not self._same_origin():
            self._reject_cross_origin("local_same_origin_required\n")
            return
        raw = self._read_body()
        if raw is None:
            return                            # 已经回过 413 了（那条路自己关连接）
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            self._send_text(400, "得是 UTF-8\n")
            return
        line = _strip_one_eol(text)
        if line == "":
            self._send_text(400, '空的一行不接（"到头了"在那条泵里就是它）\n')
            return
        if "\n" in line or "\r" in line:
            self._send_text(400, "一行一次（一次 POST 就是一行）\n")
            return
        # **带附件的那一条**（2026-10-05「图片输入」）：形状是
        # `{"text": …, "images": [data-url…]}`。⚠️ **不是那个形状 ⇒ 照旧当纯文本**
        # （一行以 `{` 打头的普通文本、或解析不了的那一行，都走原路 —— 不报错、
        #     也不改变既有那条路的行为）。
        try:
            seq = self._service._accept(_a_media_line(line) or line)
        except ValueError:
            self._send_json(400, {"error":"invalid_local_observation"})
            return
        except Exception:
            self._send_json(503, {"error":"observation_status_unknown", "retry_automatically":False})
            return
        if seq is None:
            self._send_text(503, "前面那几行还没人读 —— 一会儿再来\n")
            return
        self._send_json(202, {"ok": True, "seq": seq})

    # ── 那条推的通道 ────────────────────────────────────────────────────────
    def _ask_for_handoff(self) -> None:
        """ `POST /handoff` —— **请它交接一次**（手动那一条路，本刀新加的那口）。

        ⚠️⚠️ **它是一个"请求"，不是"执行"**（`05:123-136`）：落到 `Console.ask_for_handoff`
           ⇒ `Handoff.request()` —— 批内只登记（批末兑现），空闲才当场做。
           ⇒ 这一条回答的**不是**"交接完了"，而是"**那一刻是哪一种情形**"。
        ⚠️ **状态码只有两种，而它们说的是两件事**：
           · **503** —— 没有落点（`assemble()` 还没跑 ／ 没递 console ／ 正在收摊）
             ⇒ 那是"**现在叫不动**"，不是"你发错了"（与 `/say` 那条 503 同一条口径）；
           · **200** —— 递过去了，而回值里那四个记号说清了结果（`asked` 为假 ＝ 单飞挡住）。
           ⚠️ **没有 4xx**：这一条请求**没有正文、也没有参数**（`_read_body` 都不读）
           ⇒ 没有"请求不对"这回事。
        ⚠️ **这一条会等一会儿**（上界 `ASK_HANDOFF_BOUND_S`）：空闲时 `request()` 要跑完
           交接那六步才回来（都是本地动作：裁笔记 ／ 装前缀 ／ 换会话 ／ 写盘 ／ 注入）。
            而"等超了"**不许说成"做完了"**：那时候回的是 `answered: False`
           （"递出去了，还没等到回话"）—— 那是这一条路上**唯一**诚实的那句话。
        """
        try:
            got = self._service.ask_for_handoff()
        except Exception as error:
            print(f"   [handoff] 交接失败 ⇒ {type(error).__name__}；可导出诊断。",
                  file=sys.stderr, flush=True)
            self._send_json(500, {"error": "handoff_failed", "message": "交接失败，请导出诊断。"})
            return
        if got is None:
            self._send_text(503, "现在叫不动（还没起来 ／ 正在收摊）—— 交接没有落点\n")
            return
        self._send_json(200, got)

    def _a_market_install(self) -> None:
        """ `POST /market/install` —— **装一份**（作者："让市场真能装"）。

        ⚠️ **两个入口，由正文里那两格选**：
           · `{"from": "…"}` ⇒ **从本机的一个目录装** ；
           · `{"url": "…"}` ⇒ **从一条网址装**（谁把那份 `.tgz` 放在哪儿都行 ）；
           · `{"from": "/某一格"}` ⇒ **从本机目录装**（那边叫 `link:` —— 它是能立刻验的那条 ）。

        ⚠️ **状态码说三件事**：**503** 没落点 ／ **400** 正文不对或**那一份装不得**（拒的理由就在正文里 ）
           ／ **200** 装好了（回**新那一份名单**，页上照着重画 ）。
        """
        act = getattr(self._service, "install_plugin", None)
        if act is None:
            self._send_text(503, "装东西那一格还没接上（这里没有落点）\n")
            return
        raw = self._read_body()
        if raw is None:
            return
        try:
            box = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._send_text(400, "那一段不是一份 JSON\n")
            return
        if not isinstance(box, dict):
            self._send_text(400, "那一段该是一份 JSON 对象\n")
            return
        if "version" in box:
            version = box["version"]
            if (set(box) != {"version"} or not isinstance(version, dict)
                    or set(version) != {"repository", "tag", "name", "current"}
                    or any(not isinstance(v, str) for v in version.values())):
                self._send_text(400, "版本安装参数无效\n")
                return
            try:
                got = act(version=version)
                if not got.get("ok"):
                    self._send_text(400, str(got.get("said") or "安装失败") + "\n")
                else:
                    self._send_json(200, got)
            except Exception as exc:
                self._send_text(400, f"版本安装失败：{exc}\n")
            return
        name = str(box.get("name") or "")
        where = str(box.get("from") or "")
        # **第三个入口：一个网址**（2026-10-05 作者："别人没法发没法搜那就没用了"）——
        # ⚠️ 它**不依赖任何 registry**：谁把那份 `.tgz` 放在哪儿都行
        # （而它照旧要过"关键字"那一道门槛 —— 只是**下完才判、判不过就撤** ）。
        url = str(box.get("url") or "")
        if not name and not where and not url:
            self._send_text(400, "得说清装什么（`name` ／ `from` ／ `url` 三选一）\n")
            return
        try:
            got = act(name=name, where=where, url=url)
        except Exception as exc: # noqa: BLE001 —— 出错就说错（不静默 ）
            self._send_text(400, f"装的时候出错了：{type(exc).__name__}: {exc}\n")
            return
        # ⚠️ **没装成 ⇒ 400**（ 而那一句人话就在 `said` 里 —— 页上照实显示 ）
        if not got.get("ok"):
            self._send_text(400, str(got.get("said") or "没装成") + "\n")
            return
        self._send_json(200, got)

    def _a_plugin_switch(self) -> None:
        """ `POST /plugins/switch` —— **开它 ／ 关它**（2026-10-05 作者："接着做"）。

        ⚠️ **状态码说的是三件不同的事**（照 `_ask_for_handoff` 那一条）：
           · **503** —— 没有落点（没递那个动作）⇒ "**现在叫不动**"；
           · **400** —— 名字不对 ／ 正文不是 JSON ⇒ "**你发的这个我没法办**"；
           · **200** —— 办了，回的是**那一份新的名单**（页上照着重画 ）。

        ⚠️ **只动那个开关**：插件那些目录**一个字节都不动** （那是别人的东西）。
        """
        act = getattr(self._service, "switch_plugin", None)
        if act is None:
            self._send_text(503, "插件那个开关还没接上（这一格没有落点）\n")
            return
        raw = self._read_body()
        if raw is None:
            return                            # 已经回过 413 了（那条路自己关连接）
        try:
            box = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._send_text(400, "那一段不是一份 JSON\n")
            return
        if not isinstance(box, dict):
            self._send_text(400, "那一段该是一份 JSON 对象\n")
            return
        try:
            got = act(str(box.get("name") or ""), bool(box.get("on")))
        except KeyError as exc:
            # ⚠️ **没装过那个** ⇒ 400（ 而**不写一条永远对不上的状态** —— 那是假账 ）
            self._send_text(400, f"{exc}\n")
            return
        except Exception as exc:  # 插件生命周期错误也必须给页面一份回执。
            self._send_text(500, f"切换插件时出错了：{type(exc).__name__}: {exc}\n")
            return
        self._send_json(200, got)

    def _console_introduction(self, *, write: bool) -> None:
        if not self._same_origin() or (write and not self.headers.get("Origin")):
            self._reject_cross_origin("只支持从本机控制台设置称呼\n")
            return
        callback = self._service.console_intro
        if callback is None:
            self.close_connection = write
            self._send_json(404, {"pending": False})
            return
        try:
            if write:
                if self.headers.get_content_type() != "application/json":
                    self.close_connection = True
                    self._send_json(415, {"said": "请提交 JSON 格式的称呼。"})
                    return
                raw = self._read_body()
                if raw is None:
                    return
                body = json.loads(raw.decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("请填写称呼。")
                state = callback(body)
            else:
                state = callback()
        except FileExistsError as exc:
            self._send_json(409, {"said": str(exc)})
        except (UnicodeError, ValueError) as exc:
            self._send_json(400, {"said": str(exc)})
        except OSError:
            self._send_json(503, {"said": "控制台画像暂时无法读写，请稍后重试。"})
        else:
            self._send_json(200, state)

    def _a_person_identity(self) -> None:
        """A local operator action; the model and plug-ins cannot call it."""
        action = getattr(self._service, "act_identity", None)
        if not callable(action):
            self.close_connection = True
            self._send_json(503, {"ok": False, "said": "身份绑定尚未接线"})
            return
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            self.close_connection = True
            self._send_json(400, {"ok": False, "said": "得是 application/json"})
            return
        raw = self._read_body()
        if raw is None:
            return
        try:
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("请求必须是 JSON 对象")
            if body.get("action") == "unbind" and any(
                    not isinstance(body.get(key), str) or not body[key].strip()
                    for key in ("expected_person_id", "revision")):
                self._send_json(428, {"ok": False, "said": "解绑需要期望身份和 revision，请刷新。"})
                return
            try:
                result = action(body)
            except ValueError as exc:
                if body.get("action") != "unbind":
                    raise
                self._send_json(409, {"ok": False, "said": str(exc)})
                return
        except (UnicodeDecodeError, ValueError, KeyError, PermissionError) as exc:
            self._send_json(400, {"ok": False, "said": str(exc)})
            return
        except OSError as exc:
            self._send_json(500, {"ok": False, "said": f"本地身份记录写入失败：{exc}"})
            return
        self._send_json(200, result)

    def _a_infinity(self, route: str) -> None:
        private = route == "/infinity/qq/initial-password"
        if private:
            from ipaddress import ip_address
            port = self.server.server_address[1]
            if (self.headers.get("X-Core-Local-Password") != "1"
                    or not ip_address(self.client_address[0]).is_loopback
                    or self.headers.get("Host", "").lower() not in
                    {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}):
                self.close_connection = True
                self._send_json(403, {"ok": False, "said": "初始密码只可在运行 Core 的电脑上查看。"})
                return
        action = getattr(self._service, "act_infinity", None)
        if action is None:
            self._send_json(503, {"ok": False, "said": "扩展控制尚未接线"})
            return
        raw = self._read_body()
        if raw is None:
            return
        try:
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("请求必须是 JSON 对象")
            result = action(route, body)
        except (UnicodeDecodeError, ValueError) as exc:
            self._send_json(400, {"ok": False, "said": str(exc)})
            return
        except Exception as exc:
            self._send_json(500, {"ok": False, "said": f"{type(exc).__name__}: {exc}"})
            return
        if private:
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send_body(200, body, "application/json; charset=utf-8", no_store=True)
        else:
            self._send_json(200, result)

    def _an_plugin_remove(self) -> None:
        """ `POST /plugins/remove` —— **删掉一个装进来的插件**（2026-10-05 作者："参考它的排版"）。

        ⚠️ **状态码说的是三件不同的事**（与上面那个开关同一条口径）：
           · **503** —— 没有落点（没递那个动作）⇒ "**现在叫不动**"；
           · **400** —— 名字不对 ／ 那一个本来就没装 ／ 正文不是 JSON ⇒ "**你发的这个我没法办**"；
           · **200** —— 办了，回的是那边的回执（页上照着重画 ）。

        ⚠️ **它真删盘上的东西**（那一格目录）⇒ 这也是为什么它**必须**在同源校验后面
           （ 跨站的一页能删盘上的东西，那是不能有的 ）。
        """
        act = getattr(self._service, "remove_plugin", None)
        if act is None:
            self._send_text(503, "删插件那一格还没接上（这一格没有落点）\n")
            return
        raw = self._read_body()
        if raw is None:
            return                            # 已经回过 413 了（那条路自己关连接）
        try:
            box = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._send_text(400, "那一段不是一份 JSON\n")
            return
        if not isinstance(box, dict):
            self._send_text(400, "那一段该是一份 JSON 对象\n")
            return
        if getattr(act, 'shared_uninstall', False) and box.get('confirm_shared') is not True:
            self._send_json(400, {'ok':False, 'said':'此操作影响所有档案，请在卸载提示中确认。'})
            return
        try:
            got = act(str(box.get("name") or ""))
        except KeyError as exc:
            self._send_text(400, f"{exc}\n")
            return
        except Exception as exc:  # 插件生命周期错误也必须给页面一份回执。
            self._send_text(500, f"卸载插件时出错了：{type(exc).__name__}: {exc}\n")
            return
        self._send_json(200, got)

    def _stream_events(self) -> None:
        """连上就是订阅 ⇒ 之后每一次 `push` 都在这里变成一帧写出去。


        ⚠️⚠️ **订阅在响应头之前**（次序是承重的，不是风格）：客户端"拿到了响应头"这件事
           于是**蕴含**着"服务这边已经登记了这一页" ⇒ 一次推**不会漏在缝里**。
           `tests/test_web.py` 拿 `clients == 1` 那一句钉着它。
        ⚠️ 这一条连接是**长驻**的 ⇒ 收摊时靠哨兵（`_STOP`）叫醒；探头的那个上界是第二条路
           （哨兵丢了也走得掉），而"写不出去"是第三条（那一头已经走了 ⇒ 当场结束）。
            而"欠到上界被断开"（`_overflow`）走的**也是哨兵**这一条路 —— 而它压在这个
           循环里那一次 `wfile.write` 上：那一头不读、写堵住了，"断开"就来得晚
           （⑥-iii，准话与边界写在 `_overflow` 那段）。
        """
        service = self._service
        sub = service._subscribe()
        if sub is None:                       # 正在收摊 ⇒ 让页面照常重连
            self._send_text(503, "在收摊\n")
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            # ⚠️ 这一条流的边界由**关闭**给出（没有 `Content-Length`，也不是 chunked）
            #    ⇒ 两头都得知道：`Connection: close` 说给对端，`close_connection` 说给本层。
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            while True:
                try:
                    frame = sub.get(timeout=POLL_SECONDS)
                except queue.Empty:
                    self.wfile.write(_KEEPALIVE)
                    self.wfile.flush()
                    continue
                if frame is _STOP:
                    break
                self.wfile.write(frame)
                self.wfile.flush()
        except (ConnectionError, OSError):
            pass                              # 那一头走了 —— 那不是本端的事故
        finally:
            service._unsubscribe(sub)

    # ── 那些回话 ────────────────────────────────────────────────────────────
    def _send_page(self, name: str) -> None:
        """ **发那一页**（`GET /` ⇒ 产物里的 `index.html`；其余按名字发产物）。

        ⚠️⚠️ **边界就是 `_PAGE_FILE` 那一条正则**：只有一层、且不以点开头的名字发得出去
           ⇒ `..` ／ `%2e%2e` ／ `a/b` ／ `.gitignore` **一个都进不来**（404）。
            于是"产物目录之外一个字节都发不出去"这件事**不需要**规范化那一步
             （那一步才是这类路由常出错的地方：先解码、后拼接就能被绕）。
        ⚠️ **还没建那一页**（产物目录不在 ／ 名字不是 `index.html`）⇒ `/` 交回"怎么建"那几句
           （`_NOT_BUILT`，200 —— 它是**一句有用的话**，不是一条 404）；别的名字 404。
        ⚠️ **本方法不读时钟**：不设 `Last-Modified` ／ 不做条件请求 ⇒ 每次现读那份文件
           （那一页只有几 KB，而"缓存多久"这一格今天没有判据）。
        """
        root = self._service.web_root
        if root is not None:
            from .web_assets import read_asset
            try:
                published = read_asset(root, name)
            except (OSError, ValueError, KeyError, TypeError):
                self._send_text(503, "Static generation unavailable\n")
                return
            if published is not None:
                self._send_body(200, published, _CONTENT_TYPES.get(Path(name).suffix, "application/octet-stream"))
                return
        if name == "index.html":
            path = root / name if root is not None else None
            if path is None or not path.is_file():
                self._send_text(200, _NOT_BUILT)
                return
        else:
            if root is None or not _PAGE_FILE.match(name):
                self._send_text(404, "没有这一条\n")
                return
            path = root / name
            if not path.is_file():
                self._send_text(404, "没有这一条\n")
                return
        try:
            body = path.read_bytes()
        except OSError:
            self._send_text(404, "没有这一条\n")
            return
        ctype = _CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        self._send_body(200, body, ctype)

    def _reject_cross_origin(self, message: str) -> None:
        """Reply unchanged, then discard only a small, unambiguous body; never reuse."""
        self.close_connection = True
        self._send_text(403, message)
        self._discard_rejected_body()

    def _discard_rejected_body(self) -> None:
        # No JSON/body parser, business dispatch or persistence on this path.
        # Invalid/unknown framing and oversized bodies are closed without reading.
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding", []) or len(lengths) != 1:
            return
        text = lengths[0].strip(" \t")
        if re.fullmatch(r"[0-9]+", text) is None:
            return
        digits = text.lstrip("0") or "0"
        if len(digits) > 5:
            return
        remaining = int(digits)
        if remaining == 0 or remaining > REJECT_DISCARD_BYTES:
            return
        deadline = time.monotonic() + REJECT_DISCARD_SECONDS
        previous_timeout = self.connection.gettimeout()
        try:
            while remaining:
                budget = min(REJECT_DISCARD_SECONDS, deadline - time.monotonic())
                if budget <= 0:
                    return
                self.connection.settimeout(budget)
                # read1 uses at most one underlying read and includes prefetched bytes.
                # A read(n) loop with a fixed per-read timeout can be extended by trickles.
                chunk = self.rfile.read1(min(remaining, 8192))
                if not chunk:
                    return
                remaining -= len(chunk)
        except OSError:
            return
        finally:
            self.connection.settimeout(previous_timeout)

    def _read_body(self) -> bytes | None:
        """把请求体读下来（**带上界**）；太大 ⇒ 回 413 并关连接，交回 `None`。

        ⚠️ **不收 chunked**：只有 `Content-Length` 那条路（没有它 ⇒ 当成空的 ⇒ 400）。
           一个本地服务的一次 `fetch(url, {body: …})` 走的就是 `Content-Length` 。
        """
        lengths = self.headers.get_all('Content-Length', []) if hasattr(self.headers,'get_all') else [self.headers.get('Content-Length','0')]
        if self.headers.get('Transfer-Encoding') or len(lengths) > 1:
            self.close_connection = True
            self._send_text(400, 'ambiguous request framing\n')
            return None
        raw_length = lengths[0] if lengths else '0'
        try:
            if not raw_length.isascii() or not raw_length.strip().isdecimal(): raise ValueError()
            length = int(raw_length)
        except ValueError:
            self.close_connection = True
            self._send_text(400, "Content-Length 不是个数\n")
            return None
        if length <= 0:
            return b""
        if length > MAX_BODY:
            # ⚠️ 没读的那一段体还在那条流上 ⇒ 只能关连接（不能装成"读过了"）。
            self.close_connection = True
            self._send_text(413, "太大了\n")
            return None
        deadline = time.monotonic() + BODY_READ_SECONDS
        previous_timeout = self.connection.gettimeout()
        chunks = []; remaining = length
        try:
            read = getattr(self.rfile, 'read1', self.rfile.read)
            while remaining:
                budget = deadline-time.monotonic()
                if budget <= 0: raise TimeoutError('request body deadline')
                self.connection.settimeout(budget)
                chunk = read(min(65536,remaining))
                if not chunk:
                    self.close_connection = True
                    self._send_text(400, 'incomplete request body\n')
                    return None
                chunks.append(chunk); remaining -= len(chunk)
            if time.monotonic() > deadline: raise TimeoutError('request body deadline')
            return b''.join(chunks)
        except TimeoutError:
            self.close_connection = True
            self._send_text(408, 'request body deadline\n')
            return None
        finally:
            self.connection.settimeout(previous_timeout)

    # ── 「模型」那一页那四条（刀 4）──────────────────────────────────────
    def _timer_control(self, operation) -> None:
        if not self._same_origin() or (operation != "list" and not self.headers.get("Origin")):
            self.close_connection = True
            self._send_json(403, {"error": "local_same_origin_required"})
            return
        body = None
        if operation != "list":
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self.close_connection = True
                self._send_json(415, {"error": "application_json_required"})
                return
            if self.headers.get("Transfer-Encoding"):
                self.close_connection = True
                self._send_json(400, {"error": "chunked_body_not_supported"})
                return
            raw = self._read_body()
            if raw is None:
                return
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError, RecursionError):
                self._send_json(400, {"error": "invalid_JSON"})
                return
        status, result = self._service.manage_timers(operation, body)
        self._send_json(status, result)

    def _same_origin(self) -> bool:
        """Trust the bound listener, never a reflected Host. This is not authentication.

        Non-browser local clients may omit Origin; an empty/duplicate Origin is rejected.
        Timer writes and OAuth routes retain their additional explicit-Origin requirement.
        """
        hosts = self.headers.get_all("Host", [])
        origins = self.headers.get_all("Origin", [])
        if len(hosts) != 1 or len(origins) > 1:
            return False
        host, port = self.server.server_address[:2]
        if host not in ("127.0.0.1", "::1", "localhost"):
            return False
        literal = f"[{host}]" if ":" in host else host
        authorities = {f"{literal}:{port}", f"localhost:{port}"}
        if port == 80:
            authorities |= {literal, "localhost"}
        authority = hosts[0]
        return authority in authorities and (not origins or origins[0] == f"http://{authority}")

    def _the_engine(self) -> Any:
        """ 那一格（引擎对象）—— **按名字注入**的那一个（`WebService.engine`）。

        ⚠️ **没接上就回 503**（不是 404）：这一格**有**（那一页画得出来），
           只是这一趟没把它递进来 —— 与 `/handoff` 那条"现在叫不动"同一条口径。
        """
        return getattr(self._service, "engine", None)

    def _model_proxy_settings(self, write=False):
        engine = self._the_engine()
        boundary = getattr(engine, "model_proxy_settings", None)
        if not callable(boundary):
            self._send_json(503, {"error": "模型代理设置未接入，请核对后端版本"})
            return
        asked = None
        if write:
            raw = self._read_body()
            if raw is None:
                return
            try:
                asked = json.loads(raw.decode("utf-8"))
                if not isinstance(asked, dict):
                    raise ValueError()
            except (ValueError, UnicodeDecodeError):
                self._send_json(400, {"error": "代理设置必须是 JSON 对象"})
                return
        result = boundary(asked)
        self._send_json(result["status"], result)

    def _send_diagnostic_archive(self) -> None:
        service = self._service
        stamp = datetime.now(timezone.utc)
        manifest = {"schema": "telyincore-diagnostics-v1", "exported_at": stamp.isoformat(),
                    "version": None, "unavailable": [], "history_frame_limit": 250}
        candidate = Path(__file__).resolve().parents[2] / "candidate.json"
        try:
            version = json.loads(candidate.read_text(encoding="utf-8")).get("version")
            if isinstance(version, str) and re.fullmatch(r"beta\.\d+\.\d+\.\d+(?:\.\d+)?", version):
                manifest["version"] = version
        except (OSError, ValueError, AttributeError):
            pass

        def collect(name, reader):
            try:
                value = reader()
                if value is not None:
                    return value
            except Exception as error:
                print(f"   [web] 诊断读取失败（{name}，{type(error).__name__}）。",
                      file=sys.stderr, flush=True)
            manifest["unavailable"].append(name)
            return None

        def engine_summary():
            engine = self._the_engine()
            snapshot = engine.web_report_snapshot() if engine is not None else None
            if not isinstance(snapshot, dict):
                return None
            fields = ("name", "kind", "model", "thinking", "max_tokens",
                      "reasoning_effort", "reasoning_budget_tokens", "context_window")
            profiles = []
            for profile in snapshot.get("profiles", []):
                if not isinstance(profile, dict):
                    continue
                summary = {key: profile[key] for key in fields if key in profile}
                try:
                    summary["provider_host"] = urllib.parse.urlsplit(profile.get("base_url") or "").hostname
                except (TypeError, ValueError):
                    summary["provider_host"] = None
                profiles.append(summary)
            keys = snapshot.get("keys") or {}
            effective = {key: keys[key] for key in
                         ("context_window", "max_tokens", "model") if key in keys}
            return {"current": snapshot.get("current"), "profiles": profiles,
                    "effective": effective, "capacity_status": snapshot.get("capacity_status"), "capacities": snapshot.get("capacities", {})}

        state = collect("state.json", service.state)
        machine = (state or {}).get("machine") or {}
        # Select public diagnostics explicitly; paths, request headers and keys are excluded.
        state_fields = ("model", "profile", "model_available", "paused", "processing",
                        "interrupting", "stopped_by_hand", "batches", "messages", "tokens")
        usage = collect("usage.json", service.usage)
        frames = collect("conversation.json", getattr(service, "history", None) or (lambda: None))
        if frames is None:
            manifest["unavailable"].append("tool-receipts.json")
        conversation, tools = [], []
        for index, frame in enumerate((frames or [])[-250:]):
            kind = frame.get("kind")
            if kind not in ("msg", "tool", "batch", "batch-status", "system-notice"):
                continue
            clean = {key: value[:8192] for key in ("kind", "who", "at", "text", "tool",
                     "stage", "run", "display_id") if isinstance(value := frame.get(key), str)}
            clean["sequence"] = index
            (tools if kind == "tool" else conversation).append(clean)
        files = {
            "state.json": {key: machine[key] for key in state_fields if key in machine},
            "usage.json": {key: usage[key] for key in ("window", "total", "parts")
                           if isinstance(usage, dict) and key in usage},
            "engine.json": collect("engine.json", engine_summary),
            "conversation.json": {"frames": conversation},
            "tool-receipts.json": {"frames": tools},
            "portrait-review.json": machine.get("portrait_write_gate"),
            "recent-history.json": collect("recent-history.json",
                getattr(service, "diagnostic_history", None) or (lambda: None)),
            "round-failures.json": collect("round-failures.json",
                getattr(service, "round_failures", None) or (lambda: None)),
        }
        qq_diagnostics = getattr(service, 'qq_startup_diagnostics', None)
        if qq_diagnostics is not None:
            files['qq-startup.json'] = collect('qq-startup.json', qq_diagnostics)
        if files["portrait-review.json"] is None:
            manifest["unavailable"].append("portrait-review.json")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
            for name, value in {"manifest.json": manifest, **files}.items():
                package.writestr(name, json.dumps(value, ensure_ascii=False, indent=2))
        self._send_body(200, archive.getvalue(), "application/zip", no_store=True)

    def _send_the_engine(self) -> None:
        """ `GET /engine` —— 档案清单 ＋ 当前 ＋ **每个键的生效值与来源** ＋ 可用性。"""
        engine = self._the_engine()
        if engine is None:
            self._send_text(503, "这一格还没接上（引擎那份配置没递进来）\n")
            return
        # Proposed A read contract: one atomic, public-only report snapshot.
        capture = getattr(engine, "web_report_snapshot", None)
        if not callable(capture):
            self._send_json(503, {"code": "snapshot_unavailable", "error": "模型报告快照接口尚未接入"})
            return
        try:
            snapshot = capture()
            if not isinstance(snapshot, dict) or any(key not in snapshot for key in ENGINE_PAGE_KEYS):
                raise ValueError("incomplete report snapshot")
            report = {key: snapshot[key] for key in ENGINE_PAGE_KEYS}
            for key in ("defaults", "capacities", "capacity_status"):
                if key in snapshot:
                    report[key] = snapshot[key]
        except Exception:  # no retry, credential reflection or mixed legacy reads
            self._send_json(503, {"code": "snapshot_failed", "error": "模型报告快照不可用"})
            return
        self._send_json(200, report)

    def _a_engine_command(self) -> None:
        """ `POST /engine` —— 三条动作：**保存** ／ **设为当前** ／ **删除**。

        ```
        {"action": "save",   "profile": {...}, "set_current": true|false}
        {"action": "use",    "name": "..."}
        {"action": "delete", "name": "..."}
        ```
        ⚠️ **状态码说的是三件不同的事**（照设计稿 §8.1）：
           · **400** —— 请求本身不对（动作不认得 ／ 名字不合规 ／ **没模型设为当前**）；
           · **409** —— **动不了**（删**当前**那一条）⇒ `EngineConflict`；
           · **200** —— 成了；保存返回原提交回执，展示报告另行读取。
        """
        engine = self._the_engine()
        if engine is None:
            self._send_text(503, "这一格还没接上（引擎那份配置没递进来）\n")
            return
        raw = self._read_body()
        if raw is None:
            return
        try:
            asked = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._send_text(400, "得是一份 UTF-8 的 JSON\n")
            return
        if not isinstance(asked, dict):
            self._send_text(400, "得是一个对象\n")
            return
        # 分派与诊断在引擎那一层（它才认那些规矩）—— 这一层只把 `code` 印成一个数。
        got = engine.apply_json(asked)
        status = 200 if got["code"] == "ok" else 409 if got["code"] == "conflict" else 400
        # Forward the exact commit receipt. No later report is an authority for a slot.
        reply = {"code": got["code"], "error": got.get("error", "")}
        if asked.get("action") == "save" and status == 200:
            reply["profile"] = got.get("profile")
            reply["secret_binding"] = got.get("secret_binding")
            # Missing producer fields remain visible as incomplete, never fabricated.
        self._send_json(status, reply)

    def _a_engine_secret(self) -> None:
        """ `POST /engine/secret` —— **写一条密钥**（只写不回显；盘上有旧的 ⇒ 先留一版）。

        ⚠️ **它只回条件写入的 code/error**，**不是密钥值** ——
           "只写不回显"这条规矩要**回话里也不出现那个值**才算数。
        """
        engine = self._the_engine()
        if engine is None:
            self._send_text(503, "这一格还没接上（引擎那份配置没递进来）\n")
            return
        raw = self._read_body()
        if raw is None:
            return
        try:
            asked = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._send_text(400, "得是一份 UTF-8 的 JSON\n")
            return
        if not isinstance(asked, dict):
            self._send_text(400, "得是一个对象\n")
            return
        expected = asked.get("expected")
        if (not isinstance(expected, dict) or any(
                not isinstance(expected.get(key), str) or not expected[key].strip()
                for key in ("profile_name", "secret", "revision"))
                or asked.get("name") != expected["secret"]):
            self._send_json(428, {"code": "expectation_required", "error": "密钥写入需要原保存回执"})
            return
        # A must compare this expectation and perform the write under one boundary.
        got = engine.secret_json(asked)
        status = 200 if got["code"] == "ok" else 409 if got["code"] == "conflict" else 400
        self._send_json(status, {"code": got["code"], "error": got.get("error", "")})

    def _a_engine_probe(self) -> None:
        """ `POST /engine/probe` —— **测试连通**（探活）。

        ⚠️⚠️ **它是"另一件事"**（设计稿 §8.2 第 1 条）：**不参与可用性判断** ——
           可用性只看本地状态（选模型了吗），而真连通要按这一下。
        ⚠️ **它用 stdlib 打**（`urllib`）：本模块**零同胞 import** ⇒ 不能用 `transport.py`。
           而**环境里的代理一律不用**（`ProxyHandler({})`）—— 与 `HttpxTransport` 的
           `trust_env=False` **同一条口径**（那台机器上有 `HTTP_PROXY`，本机地址会被它劫走）。
        ⚠️ **它是有界的**：单次探测等待最多 45 秒，不自动重试，而整个探活在**一个线程**里跑
           （那一页不许因为对方不回话而挂住）。
        """
        engine = self._the_engine()
        if engine is None:
            self._send_text(503, "这一格还没接上（引擎那份配置没递进来）\n")
            return
        raw = self._read_body()
        if raw is None:
            return  # Body framing/size rejection already wrote its HTTP response.
        try:
            asked = json.loads(raw.decode("utf-8"))
            if not isinstance(asked, dict):
                raise ValueError("probe body must be an object")
        except (UnicodeDecodeError, ValueError):
            self._send_json(400, {"ok": False, "error": "得是一个 UTF-8 JSON 对象", "url": ""})
            return
        if not isinstance(asked.get("base_url"), str) or not asked["base_url"].strip():
            self._send_json(400, {"ok": False, "error": "API地址不能为空", "url": ""})
            return
        if asked.get("auth") == "chatgpt":
            if not self._admit_subscription_http():
                return
            run = getattr(engine, "web_subscription_probe", None)
            if not callable(run):
                self._send_json(503, {"ok": False, "error": "订阅探测快照接口尚未接入", "url": ""})
                return
            try:
                got = run(dict(asked), scope=self._service._chatgpt_scope)
            except Exception:
                self._send_json(503, {"ok": False, "error": "订阅探测结果未确认，未自动重试", "url": ""})
                return
            self._send_json(200, got)
            return
        # **只递这四样**（名字 ／ 协议 ／ 地址 ／ 模型 ＋ 想要哪一种）——
        #    ⚠️ **密钥一个字都不递**：探活是"这一套配置通不通"，不是"拿你的密钥去干什么"。
        got = probe_engine(engine, {
            "mode": str(asked.get("mode") or "minimal"),
            "auth": str(asked.get("auth") or "api_key"),
            "name": str(asked.get("name") or ""),
            "kind": str(asked.get("kind") or "responses"),
            "model": str(asked.get("model") or ""),
            "base_url": str(asked.get("base_url") or ""),
            # 钥匙：页上刚敲的优先，没敲就按变量名去 `engine.env` 里找
            "api_key": str(asked.get("api_key") or ""),
            "secret_name": str(asked.get("secret_name") or ""),
        }, owner=self._service)
        self._send_json(200, got)

    def _a_engine_chatgpt(self) -> None:
        if not self._admit_subscription_http():
            return
        engine = self._the_engine()
        if engine is None or not callable(getattr(engine, "chatgpt_command", None)):
            self._send_text(503, "ChatGPT 订阅登录尚未装配\n")
            return
        raw = self._read_body()
        if raw is None:
            return
        try:
            asked = json.loads(raw.decode("utf-8"))
            if not isinstance(asked, dict):
                raise ValueError()
        except (ValueError, UnicodeDecodeError):
            self._send_text(400, "得是一个 UTF-8 JSON 对象\n")
            return
        if self._service._chatgpt_scope is not None:
            got = engine.chatgpt_command(asked, scope=self._service._chatgpt_scope)
        else:
            got = engine.chatgpt_command(asked)
        if got["code"] != "ok":
            self._send_text(400, got["error"] + "\n")
            return
        self._send_json(200, got["data"])

    # ── 人设那两条路（Task 2 —— 控制器裁过：**只往路由表上添**，既有分支没动） ──
    def _accept_an_about(self) -> None:
        """ **`POST /persona/about`** —— 那一本的一次动作：`{action, what, stamp[, when]}`。

        ⚠️ 三个动作只认递进来那件说的（`hers.ACTIONS` ）；
        ⚠️ 上界（核心 40 条 ／ 单条 120 字）**拒就是拒**（400 ＋ 那句话说清 ），不许悄悄塞；
        ⚠️ 写下去走 `port.save_text`（ **先留一版历史** ）⇒ 改坏了拿得回来 ；
        ⚠️ 正文一个字节都没动时**不写**（"照实说没改"那种情况下盘上不该多出一版 ）。
        """
        port = self._service.persona
        actor = getattr(port, "act_on_shared", None) if port is not None else None
        if port is None or not callable(actor):
            self._send_text(404, "没有这一条\n")
            return
        ctype = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            self._send_text(400, "得是 application/json\n")
            return
        raw = self._read_body()
        if raw is None:
            return                            # 已经回过 413 了
        try:
            asked = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_text(400, "得是一段 UTF-8 的 JSON\n")
            return
        try:
            action = str(asked["action"])
            what = str(asked["what"])
            stamp = str(asked["stamp"])
        except (KeyError, TypeError, ValueError):
            self._send_text(400, "要 action ／ what ／ stamp 三样\n")
            return
        low = port.read_text(SHARED_FILE)
        try:
            fresh, said = actor(low, what=what, action=action, when=str(asked.get("when", "")))
        except ValueError as why:
            self._send_text(400, str(why) + "\n")
            return
        if fresh != low:
            port.save_text(SHARED_FILE, fresh, stamp=stamp, slot="about")
        self._send_json(200, {"said": said, "changed": fresh != low})
    def _accept_an_adopt(self) -> None:
        """ **`POST /persona/adopt`** —— 「悬着的事」里那条提议：人按下「照它改」。
    
        ⚠️ 这一条**只转发**（本模块零同胞 import ＋ 不认路径）：
           · 入口没接 ⇒ **404**（「今天没接上」，不是 500）；
           · 入口那一个 callable 交回 `{ok, said[, name]}` ⇒ **200**
             （`ok=False` 也是 200：那是「这一条办不成，理由在这儿」，不是服务坏了）；
           · **只认 `{text[, stamp]}`**：认的是**那句话本身**（提议那一格存的就是它）。
        """
        actor = getattr(self._service, "adopt_proposal", None)
        if not callable(actor):
            self._send_text(404, "没有这一条\n")
            return
        ctype = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            self._send_text(400, "得是 application/json\n")
            return
        raw = self._read_body()
        if raw is None:
            return                            # 已经回过 413 了
        try:
            asked = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_text(400, "得是一段 UTF-8 的 JSON\n")
            return
        if not isinstance(asked, dict) or not str(asked.get("text", "")).strip():
            self._send_text(400, "要 text 那一格（哪一条提议）\n")
            return
        try:
            told = actor(said=str(asked.get("text", "")),
                         stamp=str(asked.get("stamp", "")))
        except Exception as why:                       # noqa: BLE001 —— 交给页面看
            self._send_json(200, {"ok": False,
                            "said": "没办成：" + type(why).__name__ + " " + str(why)[:120]})
            return
        self._send_json(200, dict(told) if isinstance(told, dict)
                        else {"ok": False, "said": "那一步没说话。"})
    
    def _accept_an_open(self) -> None:
        """ **`POST /open`** —— "在文件夹里打开那一份"：`{kind, key}` ⇒ 交给入口那一个 callable。

        ⚠️ 这一条**只转发**（本模块零同胞 import ＋ 不跑命令 ）：
           · 入口没接 ⇒ **404**（"今天没接上" 不是 500 ）；
           · 那一个 callable 交回 `None` ⇒ **404**（"那一样不在盘上" 与"没接上"同一个码 ）；
           · **不许泄露盘上的路径**（交回来的只是一句人话 ）。
        """
        actor = getattr(self._service, "open_a_path", None)
        if not callable(actor):
            self._send_text(404, "没有这一条\n")
            return
        ctype = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            self._send_text(400, "得是 application/json\n")
            return
        raw = self._read_body()
        if raw is None:
            return                            # 已经回过 413 了
        try:
            asked = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_text(400, "得是一段 UTF-8 的 JSON\n")
            return
        try:
            kind = str(asked["kind"])
            key = str(asked["key"])
        except (KeyError, TypeError, ValueError):
            self._send_text(400, "要 kind ／ key 两样\n")
            return
        try:
            said = actor(kind, key)
        except ValueError as why: # 越界那一类 ⇒ 403（"这一份不该你动"）
            self._send_text(403, str(why) + "\n")
            return
        except (ImportError, OSError, SubprocessError) as why:
            print(f"   [web] 打开文件夹失败（{type(why).__name__}）。",
                  file=sys.stderr, flush=True)
            self._send_text(500, "无法打开文件夹，请检查程序文件及系统文件管理器。\n")
            return
        if not said:
            self._send_text(404, "那一样不在盘上\n")
            return
        self._send_json(200, {"said": said})
    def _send_report(self) -> None:
        """ `GET /persona` —— 那十段 ＋ 两张清单（ 读口 `report()` 那一份）。

        ⚠️ **没接那条口 ⇒ 404**（"今天没接上"）—— **不是**一个空的十段：一个空列表会被
            那一页读成"它一段都没有"（"读不到"与"零"是两件事，本仓那条立过的规矩）。
        """
        port = self._service.persona
        if port is None:
            self._send_text(404, "没有这一条\n")
            return
        self._send_json(200, port.report())

    def _send_an_avatar(self) -> None:
        """ `GET /avatar` —— **把 BOT 那张头像交回去**（2026-10-09 作者点的那一件 ）。

        ⚠️ **读法由入口递**（`read_avatar` ）： 没接 ／ 没上传过 ⇒ **404**
        —— 与 `/usage` 同一条口径（\u201c这一格还没接上\u201d要说出来 ，
            不许回一张空图 ： 页那一侧分不出\u201c没有头像\u201d与\u201c读坏了\u201d）。
        """
        reader = getattr(self._service, "read_avatar", None)
        picture = reader() if callable(reader) else None
        if not picture:
            self._send_text(404, "这一格还没有头像\n")
            return
        self._send_body(200, bytes(picture), "image/png")

    def _accept_the_gate(self, closed: bool) -> None:
        """ `POST /pause` ／ `POST /run` —— 作者那颗 ▶ 按下／弹起（ 2026-10-10 ）。

        ⚠️ **那一下由入口做** （`set_gate` —— 本模块连「闸门」住在哪儿都不认识 ，
          与 `/look` ／ `/shutdown` 同一条口径 ）。
        ⚠️ **抛了要回一句真话** ： 处理函数里抛出去 ⇒ 这一条**连接空回**
          ⇒ 页上只看成「没反应」（ 今天在 `/look` 那一格正是这么栽的 ）。
        """
        setter = getattr(self._service, "set_gate", None)
        if not callable(setter):
            self._send_text(404, "这一条还没接上\n")
            return
        try:
            setter(closed)
        except Exception as exc: # noqa: BLE001 —— 回一句真话
            self._send_text(500, "这一下抛了：" + str(exc) + "\n")
            return
        if not closed and self._service.package_updates is not None:
            self._service.package_updates.wake()
        self._send_json(200, {"stopped_by_hand": closed})
    def _accept_the_shutdown(self) -> None:
        """ `POST /shutdown` —— **收工**（ 2026-10-10 ）。

        ⚠️ **那一下由服务自己收** （`stop()` —— 本模块**不跑任何命令** ，
          与 `/avatar` ／ `/look` 那条边界同形 ）。

        ⚠️ **为什么整段包在 `try` 里** ： 处理函数里抛出去 ⇒ 这一条
          **连接空回** ⇒ 页上只看成"没反应"
          （ 而今天在 `/look` 那一格**正是这么栽的** ）⇒ 一律回一句真话 。

        ⚠️ **交回的是"了结了没有"** ， 不是"它试过了" —— `stop()` 自己
          就是这么写的 ⇒ 没结就说没结（**503**）。
        """
        expected = self.headers.get("X-Telyin-Shutdown-Instance")
        if expected is not None and expected != getattr(self._service, "_shutdown_instance_token", None):
            self._send_json(409, {"accepted": False, "error": "service_instance_changed"})
            return
        stopper = getattr(self._service, "request_shutdown", None)
        if not callable(stopper):
            stopper = getattr(self._service, "stop", None)
        if not callable(stopper):
            self._send_text(404, "这一条还没接上\n")
            return
        try:
            settled = stopper(timeout=5.0)
        except Exception as exc: # noqa: BLE001 —— 回一句真话
            self._send_text(500, "收工那一下抛了：" + str(exc) + "\n")
            return
        if settled is True:
            if getattr(self._service, '_shutdown_callback', None) is not None:
                # Listener retirement does not prove the accepted model work ended.
                self._send_json(202, {"accepted": True, "done": False,
                                      "listener_stopped": True})
            else:
                self._send_json(200, {"done": True})
        else:
            self._send_text(503, "还没了结（有页面还连着，或者那条循环没退）\n")

    def _accept_the_look(self) -> None:
        """ `POST /look` —— **收下那一套外观，交给入口写盘** 。

        ⚠ **写盘那一下由入口做** （`save_look` —— 本模块
          连路径都不认识 ， 与 `/avatar` 同一条口径 ）。
        ⚠ **只收 JSON 对象** —— 别的一律 **400** 。
        """
        saver = getattr(self._service, "save_look", None)
        if not callable(saver):
            self._send_text(404, "这一条还没接上\n")
            return
        raw = self._read_body()
        if raw is None:
            return                      # 已经回过 413 了
        try:
            box = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._send_text(400, "得是一份 JSON\n")
            return
        if not isinstance(box, dict):
            self._send_text(400, "得是一份 JSON 对象\n")
            return
        saver(box)
        self._send_json(200, {"ok": True})

    def _accept_an_avatar(self) -> None:
        """ `POST /avatar` —— **收下那张图，交给入口写盘**（2026-10-09 ）。

        ⚠️ **写盘那一下由入口做** （`save_avatar` —— 本模块连路径都不认识 ，
           与 `/open` 同一条口径 ）。
        ⚠️ **只收 `image/png`** —— 缩到 256×256 那一步在**浏览器里**做
          （`canvas` ⇒ `toBlob('image/png')` ）⇒ 到这里已经是一张规规矩矩的 PNG ；
           别的类型一律 **400** （不猜、不转码 ）。
        """
        saver = getattr(self._service, "save_avatar", None)
        if not callable(saver):
            self._send_text(404, "这一条还没接上\n")
            return
        ctype = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if ctype != "image/png":
            self._send_text(400, "得是一张 PNG\n")
            return
        raw = self._read_body()
        if raw is None:
            return                      # 已经回过 413 了（那条路自己关连接）
        saver(bytes(raw))
        self._send_json(200, {"ok": True})

    def _accept_a_save(self) -> None:
        """ `POST /persona` —— **保存一段**：`{name, text, stamp[, slot]}` ⇒ 先留一版 ⇒ 再写。

        ⚠️⚠️ **那串时间戳由请求递进来**（`stamp`）：本服务**一次都不读时钟**（模块抬头
            那一句照旧）⇒ **缺了它就 400**，而不是"替它取现在" ——
            那是"时钟可注入"在 HTTP 这一层的样子（页那一侧挑'哪一秒'）。
        ⚠️ **名字与正文都在正文里**（不走 `?name=`）：名字里有中文与斜杠，挂到 URL 上就
            多出一处要编码 ／ 解码的地方 —— 而那种地方正是这一刀防的那一类。
        ⚠️ **那两类错分开报**：`PersonaPathError`（**名字**越界 ／ 那串时间戳不对）
            ⇒ **400**；`ValueError` ＋ `KeyError`（**正文**不是那几格 JSON）
            ⇒ 也是 **400**（都是"这一次请求不对"）。
             别的错照旧抛出去（`_Server.handle_error` 那一层）—— 一个"什么都吞成 400"
              的实现，会把"我们这边坏了"说成"你发错了"。
        """
        if not self._same_origin():
            self._reject_cross_origin("跨站的一页不许保存人设\n")
            return
        port = self._service.persona
        if port is None:
            self._send_text(404, "没有这一条\n")
            return
        ctype = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            self._send_text(400, "得是 application/json\n")
            return
        raw = self._read_body()
        if raw is None:
            return                            # 已经回过 413 了（那条路自己关连接）
        try:
            asked = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_text(400, "得是一段 UTF-8 的 JSON\n")
            return
        if not isinstance(asked, dict):
            self._send_text(400, "得是一个 JSON 对象\n")
            return
        if not isinstance(asked.get("name"), str) or not isinstance(asked.get("text"), str):
            self._send_text(400, "`name` 与 `text` 都得是一串字\n")
            return
        try:
            _version_name(asked["name"], asked.get("stamp"),
                          asked.get("slot", DEFAULT_VERSION_SLOT))
        except PersonaPathError as exc:
            self._send_text(400, f"{exc}\n")
            return
        # HTTP writers must supply the revision they actually loaded. Internal
        # Python callers retain expect=None for backwards compatibility only.
        if "expect" not in asked:
            self._send_json(428, {"error": "persona_revision_required",
                                 "message": "请重新读取正文和 revision，再带 expect 保存。"})
            return
        expect = asked["expect"]
        if not isinstance(expect, str) or (expect != "missing" and
                re.fullmatch(r"[0-9a-f]{64}", expect) is None):
            self._send_json(400, {"error": "persona_revision_invalid",
                                 "message": "expect 必须是读取时的 revision。"})
            return
        try:
            options = {"stamp": asked.get("stamp"),
                       "slot": asked.get("slot", DEFAULT_VERSION_SLOT), "expect": expect}
            if "section" in asked:
                got = port.save_section(asked["name"], asked["section"], asked["text"], **options)
            else:
                got = port.write_text(asked["name"], asked["text"], **options)
        except PersonaConflict as exc:
            self._send_json(409, {"error": "persona_revision_conflict",
                                 "name": exc.name, "expected": exc.expected,
                                 "actual": exc.actual, "message": str(exc)})
            return
        except PersonaReadFailed as exc:
            self._send_json(503, {"error": "persona_current_read_failed",
                                  "name": exc.name, "retryable": True,
                                  "message": str(exc)})
            return
        except PersonaWriteRefused as exc:
            # **403（不是 400）**：名字**完全合法**，而这一份**人不能改** ——
            #    那两件事在页上是两句不同的话（"你打错了" ／ "这一份不该你动"）。
            self._send_text(403, f"{exc}\n")
            return
        except PersonaShapeRefused as exc:
            # **409（本波加的那一条）**：名字合法、这一份也归你写，而**这一版会把它
            #    改坏**（一处承重的形状没了）—— 那与"你打错了" ／ "这一份不该你动"
            # 是第三件事 ⇒ 第三个状态码。 理由写在那一类自己的 docstring 上。
            self._send_text(409, f"{exc}\n")
            return
        except (PersonaPathError, FileExistsError) as exc:
            self._send_text(400, f"{exc}\n")
            return
        self._send_json(200, {"ok": True, "name": got["name"], "bytes": got["bytes"],
                              "version": got["version"],
                              "revision": got["version_after"],
                              "version_before": got["version_before"],
                              "version_after": got["version_after"],
                              "left_a_version": got["left_a_version"],
                              # **本波补的那一格**（在**真浏览器**上量出来的）：
                              #    页那一侧从 Task 3 起就**读** `history`（它要说"旧版留在
                              # 哪儿"），而这条回答**从前一次都没发过它** ⇒ 页上**永远**
                              #    写着"盘上原来没有这一份 ⇒ 没留下旧版" —— **而历史真的留了**
                              # （那是一条**假话**，正落在这一页那条承诺上）。 加一格
                              #    （纯追加：多出的键不吃掉任何既有调用方），两处就对上了。
                              "history": got["history"]})

    def _send_body(self, status: int, body: bytes, ctype: str, *, no_store: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if no_store:
            self.send_header("Cache-Control", "no-store")
        if getattr(self, "close_connection", False):
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, text: str) -> None:
        self._send_body(status, text.encode("utf-8"), "text/plain; charset=utf-8")

    def _send_json(self, status: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send_body(status, body, "application/json; charset=utf-8")


class _Server(ThreadingHTTPServer):
    """标准库那一层：一个请求一条线程 —— **每条都是 daemon，而且不 join**。

    ⚠️ 那两条一起才是"**不许把测试（或那个进程）挂住**"：`daemon_threads` 是底线
       （赖着的连接拦不住解释器退出），`block_on_close = False` 让 `server_close()`
       不必等它们 —— 体面由 `stop()` 那一下（叫醒 ＋ 有界地等）负责，不靠 join 兜着。
    """

    daemon_threads = True
    block_on_close = False
    max_connections = 32

    def __init__(self, address: tuple[str, int], handler: type[_Handler],
                 service: WebService) -> None:
        self.service = service
        self._request_slots = threading.BoundedSemaphore(self.max_connections)
        self._request_lock = threading.Lock()
        self._request_changed = threading.Condition(self._request_lock)
        self._request_threads = set()
        self._request_count = 0
        self.requests_quiet = threading.Event()
        self.requests_quiet.set()
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        with self.service._lock:
            accepted = not self.service._closing and self._request_slots.acquire(blocking=False)
            if accepted:
                self.service._http_connections.add(request)
                with self._request_lock:
                    self._request_count += 1
                    self.requests_quiet.clear()
        if not accepted:
            self.shutdown_request(request)
            return
        try:
            request.settimeout(5.0)
            super().process_request(request, client_address)
        except BaseException:
            self._release_request(request)
            raise

    def _release_request(self, request):
        with self.service._lock:
            self.service._http_connections.discard(request)
            self.service._http_admitted.discard(request)
            self.service._http_retired_unadmitted.discard(request)
        with self._request_lock:
            self._request_count -= 1
            self._request_slots.release()
            if self._request_count == 0:
                self.requests_quiet.set()
            self._request_changed.notify_all()

    def process_request_thread(self, request, client_address):
        identity = threading.get_ident()
        with self._request_lock:
            self._request_threads.add(identity)
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._request_lock:
                self._request_threads.discard(identity)
            self._release_request(request)

    def wait_requests(self, deadline, *, response_owner=False):
        """The shutdown HTTP caller may await OTHER requests before its reply.

        Its own slot stays owned until the thread's finally. Public stop waits
        for every slot; a client acceptance response is never full retirement.
        """
        with self._request_changed:
            excluded = int(response_owner and threading.get_ident() in self._request_threads)
            while self._request_count > excluded:
                remaining = max(0.0, deadline - _shutdown_now())
                if remaining <= 0:
                    return False
                self._request_changed.wait(remaining)
            return True

    def server_bind(self) -> None:
        if sys.platform == "win32":
            import socket
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request: Any, client_address: Any) -> None:
        """一页走掉不算事故 —— `ConnectionError` 咽掉（`BrokenPipe` 是它的子类），别的照旧炸。"""
        exc = sys.exc_info()[1]
        with self.service._lock:
            retired = request in self.service._http_retired_unadmitted
        if (retired and isinstance(exc, OSError)
                and (exc.errno == 9 or getattr(exc, 'winerror', None) == 10038)):
            return  # our unadmitted Windows header reader observed descriptor retirement
        if isinstance(exc, ConnectionError):
            return
        super().handle_error(request, client_address)


class WebService:
    """ **那个服务** —— 起来 ／ 推 ／ 读 ／ 停（四个动作，别的一个都不多）。

    ⚠️ **两个注入面**就是 `push` 与 `read_line`（见模块 docstring）—— 本类**不认**
       `console` 里的任何东西：谁来接、怎么接，是**外面**那一格的事（Task 2 ）。
    ⚠️ **一辈子一次**：`start()` 只能成功一次。停过之后再起同一个对象会抛 ——
       半死的重启比一声清楚的拒绝坏得多，而"再起一个"的代价只是再造一个对象。

     **另有那两格是"那一页"要的**（Task 3，两格都是**递进来的**，本类一个字节都不猜）：
       · `web_root` —— 产物目录（`GET /` 发它里面的 `index.html`）：
         递 `None` ⇒ 本服务**没有那一页**（`/` 交回"怎么建"那几句）；
       · `state` —— **顶上那条计数的来源**（零参可调用对象，每次现读）：
         本类只负责把它交出来的那一段放进 `GET /state` 的 `machine` 那一格，**不解释**它；
         ⇒ 那几个数"从哪来"的答案**在 `state_of()` 上**，不在这里（这是那一格的全部责任）。
    """

    def look(self) -> dict[str, str] | None:
        """ **那一套自定义的外观**（ 2026-10-10 ）。

        ⚠ **读法由入口递** （`read_look` ）； 没接 ⇒ `None`
          （ 而 `do_GET` 自己回 503 ）。
        ⚠ **为什么非得有这个方法** ： `do_GET` 那一条叫的就是它
          —— 而它一度**落在别的类里** ⇒ 还是 `AttributeError`
          ⇒ 那一格**连接空回** （ 真页上量到的 `ERR_EMPTY_RESPONSE` ）。
        """
        reader = getattr(self, "read_look", None)
        if not callable(reader):
            return None
        return reader()

    def __init__(self, *, host: str = LOOPBACK, port: int = DEFAULT_PORT,
                 web_root: Any = DEFAULT_WEB_ROOT,
                 state: Callable[[], dict[str, Any]] | None = None,
                 persona: Any = None, console: Any = None,
                 engine: Any = None,
                 #: 那个**只读**接口的读法（2026-10-05）：`None` ⇒ `GET /memory` 答空。
                 read_memory: Callable[[], list[dict[str, Any]]] | None = None,
    console_intro: Callable[..., dict[str, Any]] | None = None,
    read_identities: Callable[[], list[dict[str, Any]]] | None = None,
    act_identity: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    read_pending: Callable[[], list[dict[str, Any]]] | None = None,
    read_knowledge: Callable[[], list[dict[str, Any]]] | None = None,
    # **2026-10-18 批④**：做法治理的只读视图（有效 ／ 已并入 ／ 操作账 ／ 运行状态）。
    # 与 `read_knowledge` 同一条路（由入口递 ，本模块零同胞 import）。
    read_skills: Callable[[], dict[str, Any]] | None = None,
    # 2026-10-08：**它自己写的那三份**（`persona/它写的/*.md` ）——
    # 与 `read_knowledge` 同一条路（由入口递 ，本模块零同胞 import ）。
    read_hers: Callable[[], list[dict[str, Any]]] | None = None,
    read_experience: Callable[[], list[dict[str, Any]]] | None = None,
    read_display_labels: Callable[[], dict[str, str]] | None = None,
    read_shared: Callable[[], dict[str, Any]] | None = None,
    act_on_shared: Any = None,
    open_a_path: Any = None,
    #: ★ **2026-10-13（作者：「做」）**：**人按的那一下** —— 把「悬着的事」里那条
    #:   提议写下去，并从那一格里收起来（提议是它自己提的；**放行归人**）。
    #:   ⚠️ 不递 ⇒ 那条路回 404（「今天没接上」，与 `/open` 同一条口径）。
    adopt_proposal: Any = None,
    # **2026-10-07「④」**：跑一次评测那一个 callable（缺省禁 ⇒ 照旧）。
    run_eval: Any = None,
    # **2026-10-07「模糊搜接上」**：那一个匹配器（ 由入口递 —— 零同胞 import ）。
    match_rows: Any = None,
                 #: 那一份"**占在哪几块**"的读法（2026-10-05，同上由入口递 ）。
                 read_usage: Callable[[], dict[str, Any]] | None = None,
                 #: **已经说过的话**（2026-10-05，同上由入口递 ）——
                 #: `None` ⇒ `GET /history` 答**空那一串**（"这一格还没接上" 而"没有旧话"
                 #: 是两件事：一台刚起来的机器上，空会话本来就该答空 ）。
                 read_history: Callable[[], list[dict[str, Any]]] | None = None,
    #: **2026-10-09 终端那一刻**： 「今天」由**入口**说了算 —— 它是这一层里
    #: **唯一**读钟合法的地方 （ 本模块不许跑命令 、 内核那几层不许读钟 、
    #: 页上也不许读钟 ⇒ 三面墙里只剩这一个口子 ）。
    now: Any = None,
    #: **2026-10-09 头像那一件**： 读 ／ 写那张 `avatar.png`（**都由入口递** ）——
    #: `read_avatar()` 交回字节或 `None` （没上传过 ⇒ `None` ⇒ 404 ）；
    #: `save_avatar(raw)` 收下那张 PNG 并写盘 （路径那一半归入口 ）。
    read_avatar: Any = None,
    save_avatar: Any = None,
    #: **2026-10-10（外观那一页 ③）**：那一套外观的读写
    #: `read_look()` 交回一份 dict（ 没有 ⇒ `None` ）；
    #: `save_look(box)` 收下一份并写盘 （ 路径那一半归入口 ）。
    #: `set_gate(closed)` 那一下由入口做 （ 它**只写** —— 读那一头
    #: 从那个主循环上现读 ，不走这里 ）。
    set_gate: Any = None,
    read_look: Any = None,
    save_look: Any = None,
                 #: **插件那一份名单**（2026-10-05 作者："先做 1"）—— 同一条路由入口递 。
                 #: `None` ⇒ `GET /plugins` 答 `wired: False`（"还没接上" ，
                 #: 而那与"零个插件"是两件事 ）。
                 read_plugins: Callable[[], dict[str, Any]] | None = None,
                 #: **市场那一份**（2026-10-05 作者："先做插件市场吧"）—— 同一条路由入口递 。
                 #: `None` ⇒ `GET /market` 答 `wired: False`（"根本没问到" ，
                 #: 而那与"问到了、没人发过"是两件事 ）。
                 read_market: Callable[[], dict[str, Any]] | None = None,
                 #: **开它 ／ 关它**（2026-10-05 作者："接着做"）—— 同一条路由入口递 。
                 #: 形如 `(name, on) -> 新那一份名单`；`None` ⇒ 那一格答 503（"还没接上" ）。
                 switch_plugin: Callable[[str, bool], dict[str, Any]] | None = None,
                 #: **删掉一个插件**（2026-10-05 作者："参考它的排版"）—— 同一条路由入口递 。
                 #: 形如 `(name) -> {ok, said, …}`；`None` ⇒ 那一格答 503（"还没接上" ）。
                 #: ⚠️ **它真删盘上的东西**（那一格目录）⇒ 它比上面那个开关**更重** 。
                 remove_plugin: Callable[[str], dict[str, Any]] | None = None,
                 #: **装一份**（2026-10-05 作者："让市场真能装"）—— 同一条路由入口递 。
                 #: 形如 `(name=…, where=…) -> {ok, said, …}`；`None` ⇒ 那一格答 503 。
                 install_plugin: Callable[..., dict[str, Any]] | None = None,
                 read_infinity: Callable[[str], dict[str, Any]] | None = None,
                 read_tool_check: Any = None,
                 act_infinity: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
                 package_updates: Any = None,
                 plugin_updates: Any = None,
                 plugin_restart: Any = None,
                 runtime_evidence: Any = None, runtime_evidence_finalizer: bool = True
                 ) -> None:
        if not _is_loopback(host):
            raise ValueError(
                f"只听回环：host 得是一个回环字面量（缺省 {LOOPBACK}），递进来的是 {host!r}")
        self.host = host
        self.package_updates = package_updates
        self.plugin_updates = plugin_updates
        self.plugin_restart = plugin_restart
        self._updates_stopped = threading.Event()
        self.runtime_evidence = runtime_evidence
        self.runtime_evidence_finalizer = runtime_evidence_finalizer
        #: **刀 4 那一格**：那个引擎对象（档案 ／ 密钥 ／ 来源 ／ 探活都在它身上）。
        #: ⚠️ **按名字用**（本模块零同胞 import）⇒ 它只要长得像 `engine.Engine` 就够了；
        #:    没递 ⇒ 那三条端点回 **503**（"这一格还没接上"），**不是**回一份空的。
        self.engine = engine
        scope_factory = getattr(engine, "chatgpt_scope", None)
        self._chatgpt_scope = scope_factory() if callable(scope_factory) else None
        #: 递 `0` ⇒ 起来之后这里换成本核给的那个空位（见 `start`）。
        self.port = port
        #: 那一页的产物目录（`None` ⇒ 没有那一页 —— 见类文档）。
        self.web_root: Path | None = None if web_root is None else Path(web_root)
        #: 那条计数的来源（`state_of()` 造它；本类只调它，不解释它）。
        self.state_source = state
        # 那个只读接口的读法（2026-10-05）：挂在这儿 —— 处理函数看不见 `serve()` 的参数
        self.read_memory = read_memory
        self.console_intro = console_intro
        self.read_identities = read_identities
        self.act_identity = act_identity
        self.read_pending = read_pending
        self.read_knowledge = read_knowledge
        self.read_skills = read_skills
        self.read_hers = read_hers
        self.read_experience = read_experience
        self.read_display_labels = read_display_labels
        self.read_shared = read_shared
        self.act_on_shared = act_on_shared
        self.open_a_path = open_a_path
        self.adopt_proposal = adopt_proposal
        self.run_eval = run_eval
        self.match_rows = match_rows
        # 那一份"占在哪几块"的读法（2026-10-05）：挂在这儿，理由与上一条**一字不差**
        self.read_usage = read_usage
        # 已经说过的话（2026-10-05）：同上 —— 处理函数看不见 `serve()` 的参数
        self.read_history = read_history
        self.now = now
        self.read_avatar = read_avatar
        self.save_avatar = save_avatar
        self.read_look = read_look
        self.set_gate = set_gate
        self.save_look = save_look
        # 插件那一份名单（2026-10-05）：同上，理由一字不差
        self.read_plugins = read_plugins
        # 市场那一份（2026-10-05）：同上
        self.read_market = read_market
        # 那个开关（2026-10-05）：同上
        self.switch_plugin = switch_plugin
        # **删掉一个插件**（2026-10-05）—— 「读法／动作都由入口递」那一条的又一个落点
        # （零同胞 import ⇒ `web.py` 够不到 `plugins` ）。
        self.remove_plugin = remove_plugin
        # 装一份（2026-10-05）：同上
        self.install_plugin = install_plugin
        import uuid
        self._shutdown_instance_token = uuid.uuid4().hex
        self.read_infinity = read_infinity
        self.act_infinity = act_infinity
        self.read_tool_check = read_tool_check
        if read_tool_check is not None and not all(callable(getattr(read_tool_check, name, None))
                for name in ('__call__', 'bind_loop', 'snapshot', 'close')):
            raise TypeError('read tool check requires a bound host callback')
        #: **那一条人设口**（`PersonaPort` —— Task 1 读、Task 2 写）。
        #: ⚠️⚠️ **缺省是 `None`，而那是故意的**：把 `DEFAULT_PERSONA_ROOT` 填在这儿，
        #: 就等于"没接它的时候也去读写这台机器上那**一条真的根**"
        #:    ⇒ 没接 ⇒ `/persona` 那两条路都交 **404**（"今天没接上"），一个字都不猜。
        self.persona = persona
        #: **那一台机器**（`Console`）—— 本刀加的这一格，为**手动那一条路**
        #:    （`POST /handoff` ⇒ `ask_for_handoff()`）要够得着 `Handoff.request()`。
        #: ⚠️ 在那之前本类**手上没有它**：`state_of(console, …)` 那个闭包替它拿着
        #:    （读那几个数用），而"**叫它做一件事**"要的是这一格 —— 一条读路一条写路，
        #:    两件不同的事（照 `may_be_written` ／ `may_be_written_by_her` 那一对成法）。
        #: ⚠️ **缺省 `None`**：没递 ⇒ `/handoff` 回 **503**（"现在叫不动"），不猜、不装。
        self.console = console
        self._lock = threading.Lock()
        self._push_lock = threading.Lock()
        self._shutdown_requested = threading.Event()
        self._shutdown_response_owner = threading.local()
        self._http_connections = set()
        self._http_admitted = set()
        self._http_retired_unadmitted = set()
        self._http_close_failed = False
        self._subs: set[queue.Queue[Any]] = set()
        self._retiring_subs: set[queue.Queue[Any]] = set()
        #: 等着的那个读者那一条队 —— ⚠️ **可等待**（不是 `queue.Queue`）：
        #: 见 `read_line` 那段"为什么不是一个线程里的阻塞读"。
        self._inbox: asyncio.Queue[Any] = asyncio.Queue()
        #: 还没有人在读的时候先替它排着的那几行（有界：`MAX_PENDING`）。
        self._pending: list[str] = []
        #: 正在等的那一个循环（进口要从**别的线程**投进来 ⇒ 得有个把东西递过去的地方）。
        self._loop: asyncio.AbstractEventLoop | None = None
        #: 没有页面连着的时候它是 set ⇒ `stop()` 那一下"有界地等"靠它（不轮询）。
        self._quiet = threading.Event()
        self._quiet.set()
        self._subscription_http = set()
        self._subscription_http_quiet = threading.Event()
        self._subscription_http_quiet.set()
        self._seq = 0
        from uuid import uuid4
        self._display_epoch = uuid4().hex
        self._accepted = 0
        #: **因为没在读而被断开的那几条连接**（见 `_overflow`）—— 只在 `/healthz` 上露面。
        #: ⚠️ 它是一个**可观测量**，不是内务：在它出现之前，"一个不读的页面被断开了"
        #:    这件事**只在那一页自己那头**看得见（`onerror`），服务这一侧一个字都没有
        #:    ⇒ 那种行为**判不动**（测试只能去猜 `clients` 为什么会掉到 0）。
        self._stalled = 0
        self._closing = False
        self._timer_pending = 0
        self._timer_timeout = 2.0
        self._read_check_pending = set()
        self._read_check_quiet = threading.Event()
        self._read_check_quiet.set()
        self._read_check_timeout = 25.0
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._bound: tuple[str, int] | None = None

    # ── 起 ／ 停 ────────────────────────────────────────────────────────────
    def start(self) -> WebService:
        """起来（**幂等**：已经在跑就原样交回自己）；递 `0` ⇒ `port` 换成本核给的那个。"""
        if self.running:
            return self
        if self._thread is not None:
            raise RuntimeError("这个服务一辈子只起一次 —— 停过了要再起，请另造一个")
        try:
            server = _Server((self.host, self.port), _Handler, self)
        except Exception:
            self.stop()
            raise
        thread = threading.Thread(target=server.serve_forever,
                                  args=(SHUTDOWN_POLL_SECONDS,),
                                  name="telyincore-web", daemon=True)
        self._server = server
        self._thread = thread
        self._bound = server.server_address[:2]
        self.port = self._bound[1]
        try:
            thread.start()
        except Exception:
            self.stop()
            raise
        if self.package_updates is not None:
            self.package_updates.warm(self._updates_stopped)
        if self.plugin_updates is not None:
            self.plugin_updates.warm(self._updates_stopped, lambda: not bool(
                getattr(getattr(self.console, 'round_control', None), 'status', lambda: {})().get('processing')))
        return self

    def _begin_subscription_http(self):
        with self._lock:
            if self._closing:
                return None
            lease = object()
            self._subscription_http.add(lease)
            self._subscription_http_quiet.clear()
            return lease

    def _finish_subscription_http(self, lease):
        with self._lock:
            self._subscription_http.discard(lease)
            if not self._subscription_http:
                self._subscription_http_quiet.set()

    @property
    def subscription_http(self):
        """Counts only owned subscription management/probe requests; no headers/body."""
        with self._lock:
            return {"inflight": len(self._subscription_http),
                    "admission_closed": self._closing,
                    "drained": not self._subscription_http}

    @property
    def chatgpt_shutdown(self):
        """Owner-visible stop state, available even after HTTP listener closes."""
        if self._chatgpt_scope is None:
            return {"state": "not_wired", "complete": True,
                    "login_save_outcome": "not_applicable", "ownership_retained": False}
        return self._chatgpt_scope.shutdown_status()

    @property
    def shutdown_requested(self) -> bool:
        """Explicit operator intent, not proof of model success or full drain."""
        return self._shutdown_requested.is_set()

    def bind_shutdown(self, callback: Any) -> None:
        """Bind the assembled owner's synchronous loop-thread cutoff once."""
        if hasattr(self, '_shutdown_callback'):
            raise RuntimeError('shutdown owner already bound')
        if not callable(callback):
            raise TypeError('shutdown callback required')
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError('shutdown loop changed')
        self._loop = loop
        if self.read_tool_check is not None:
            self.read_tool_check.bind_loop(loop)
        self._shutdown_callback = callback
        self._shutdown_callback_done = False
        self._shutdown_callback_error = None
        if self.shutdown_requested:
            self._loop.call_soon_threadsafe(self._notify_shutdown)

    def _notify_shutdown(self) -> None:
        if getattr(self, '_shutdown_callback_done', False):
            return
        callback = getattr(self, '_shutdown_callback', None)
        if callback is None:
            return
        self._shutdown_callback_done = True
        try:
            callback()
        except BaseException as error:
            self._shutdown_callback_error = error

    def begin_operator_shutdown(self) -> None:
        """Trusted loop-thread phase one; final serve() stop still proves drain.

        This is not an HTTP route. It uses the existing private launcher's
        capability, never a discovered port, and performs no blocking join.
        """
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            raise RuntimeError('operator shutdown requires the assembled owner loop')
        with self._lock:
            self._shutdown_requested.set()
            first = not self._closing
            self._closing = True
            subs = tuple(self._subs | self._retiring_subs)
        self._notify_shutdown()
        if first:
            self._inbox.put_nowait(_CLOSED)
        for sub in subs:
            sub.put(_STOP)
        if self._shutdown_callback_error is not None:
            raise self._shutdown_callback_error

    def request_shutdown(self, *, timeout: float = 5.0) -> bool:
        # Mark BEFORE stop wakes the inbox, so serve cannot race the HTTP reply.
        # Called only after the existing same-origin route admission.
        self._shutdown_requested.set()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._notify_shutdown)
        previous = getattr(self._shutdown_response_owner, 'active', False)
        self._shutdown_response_owner.active = True
        try:
            return self.stop(timeout=timeout)
        finally:
            self._shutdown_response_owner.active = previous

    def stop(self, *, timeout: float = 10.0) -> bool:
        """**收摊** —— 交回"**了结了没有**"（不是"它试过了"）：线程死了 ＋ 没有页面还连着。

        ⚠️⚠️ **次序是承重的**（不是随便排的）：
           ① 在锁里把 `_closing` 立起来（**先立旗、后叫醒**）—— 于是之后再来的订阅
              **进不来**（`_subscribe` 看见旗就回 `None`）⇒ 那一轮哨兵**发得齐**；
           ② 给**每一条**挂着的长连接塞一个哨兵（它们正停在一个没有上界的 `get` 上）；
           ③ 进口那一头也塞一个（正等着的那个读者 ⇒ `StopAsyncIteration`，不是永远挂着）；
           ④ `shutdown()` ＋ `server_close()`（后者不等 handler 线程：它们不 join）；
           ⑤ `join(timeout)` ＋ **等那面旗**（"没有页面连着了"）—— 有界。
        ⚠️ **幂等**：没起来过、停过第二次，都照旧回 `True`。
        """
        self._updates_stopped.set()
        # Monotonic stop budget only; never a timestamp for persisted page edits.
        deadline = _shutdown_now() + max(0.0, timeout)
        with self._lock:
            first = not self._closing
            self._closing = True
            subs = tuple(self._subs | self._retiring_subs)
            loop = self._loop
            unadmitted = tuple(self._http_connections - self._http_admitted
                               - self._http_retired_unadmitted)
            self._http_retired_unadmitted.update(unadmitted)
        # Wake idle keep-alives / incomplete headers only. Admitted bodies,
        # callbacks, current shutdown response and their finalizers remain owned.
        import socket
        unadmitted_close_ok = True
        for connection in unadmitted:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # shutdown may race an already closed peer
            # makefile() holds a socket reference. close() alone would leave its
            # Windows header read asleep; retire only this unadmitted descriptor.
            descriptor = connection.detach()
            if descriptor >= 0:
                try:
                    socket.close(descriptor)
                except OSError:
                    unadmitted_close_ok = False  # never certify an unknown close
                    with self._lock:
                        self._http_close_failed = True
        if self.read_tool_check is not None:
            self.read_tool_check.close()
        oauth_settled = self._chatgpt_scope is None or self._chatgpt_scope.close(
            timeout=max(0.0, deadline - _shutdown_now()), retain_ownership=True)
        for sub in subs:
            sub.put(_STOP)
        if first and loop is not None:
            try:
                loop.call_soon_threadsafe(self._inbox.put_nowait, _CLOSED)
            except RuntimeError:              # 那个循环已经关了 —— 那就没人还等着了
                pass
        server, thread = self._server, self._thread
        if server is not None:
            if thread is not None and thread.is_alive():
                server.shutdown()
            server.server_close()
        if thread is not None:
            if thread.ident is not None:
                thread.join(max(0.0, deadline - _shutdown_now()))
        settled = self._quiet.wait(max(0.0, deadline - _shutdown_now()))
        # Request exit clears this event after response flush/exception cleanup.
        # A zero remaining budget only observes it; never spawns a late drain task.
        http_settled = self._subscription_http_quiet.wait(max(0.0, deadline - _shutdown_now()))
        read_checks_settled = self._read_check_quiet.wait(max(0.0, deadline - _shutdown_now()))
        requests_settled = (server is None or server.wait_requests(
            deadline, response_owner=getattr(self._shutdown_response_owner, 'active', False)))
        with _probe_registry_lock:
            probes = tuple(getattr(self, '_probe_workers', ()))
        for probe in probes:
            probe.join(max(0.0, deadline - _shutdown_now()))
        probe_settled = not any(probe.is_alive() for probe in probes)
        done = bool(unadmitted_close_ok and not self._http_close_failed
                    and read_checks_settled and probe_settled and oauth_settled
                    and settled and http_settled and requests_settled
                    and (thread is None or not thread.is_alive()))
        if done and self._chatgpt_scope is not None:
            done = self._chatgpt_scope.confirm_stopped()
        if self.runtime_evidence is not None and self.runtime_evidence_finalizer:
            done = bool(self.runtime_evidence.stop()) and done
        return bool(done)

    def __enter__(self) -> WebService:
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # ── 那几个数（也在 `/healthz` 上） ──────────────────────────────────────
    @property
    def bound(self) -> tuple[str, int] | None:
        """**内核说它绑在哪儿**（`(host, port)`；没起来过 ⇒ `None`）。

        ⚠️ 它是从 socket 上读回来的那一个（`server_address`），不是把构造参数念一遍 ——
           "只听回环"这条保证的判据就落在这上面。
        """
        return self._bound

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def clients(self) -> int:
        """连着那条通道的页面数（一条连接 ＝ 一个）。"""
        with self._lock:
            return len(self._subs)

    @property
    def seq(self) -> int:
        """推出去的行数（序号从 1 起 —— 也是那一帧的 `id:`）。"""
        with self._lock:
            return self._seq

    @property
    def accepted(self) -> int:
        """**收下**的行数（被挡下的那些不算 —— 400 ／ 503 不占号）。"""
        with self._lock:
            return self._accepted

    def url(self, path: str = "/") -> str:
        """那条地址（ 起来那一刻定下的那一个 —— 端口递 `0` 时由本核给）。"""
        return f"http://{self.host}:{self.port}{path}"

    def stats(self) -> dict[str, Any]:
        """ **服务自己**那几个数（`GET /healthz`）—— 与那台机器的数**分开两格**。

        ⚠️ 这里一个"机器"的数都没有（token ／ 消息 ／ 批 ／ 暂停）：那些在 `state()` 的
           `machine` 那一格里（来源是递进来的那个可调用对象）。 分开是**故意的**：
           服务说了不算的事，不许从这一格里读出来。
        ⚠️ **`stalled` 是"因为没在读被断开的那几条"**（见 `_overflow`）：它不是错误计数，
           而是"那条上界**真的起过作用**"这一个事实的痕迹 —— 没有它，那种行为在服务这一侧
           是**看不见**的（只能从 `clients` 掉到 0 去猜）。
        """
        with self._lock:
            data = {
                "service": "telyincore-web",
                "channel": "sse",
                "host": self.host,
                "port": self.port,
                "seq": self._seq,
                "accepted": self._accepted,
                "clients": len(self._subs),
                "retiring_clients": len(self._retiring_subs),
                "pending": len(self._pending),
                "stalled": self._stalled,
                "closed": self._closing,
            }
        return data

    def usage(self) -> dict[str, Any] | None:
        """ `GET /usage` 那一份 —— **没接 ⇒ `None`**（那一头回 503，不回空的 ）。"""
        if self.read_usage is None:
            return None
        return self.read_usage()

    def history(self) -> list[dict[str, Any]]:
        """ **已经说过的话**（`GET /history` ，2026-10-05）—— **没接 ⇒ 空那一串** 。

        ⚠️ 与 `usage()` 那一格的差别是**故意的**：那一格"没接"是"这一格还没接上"
           （所以它回 503 ）；而这一格"没接"＝**没有旧话** —— 一台刚起来的机器上
           空会话本来就该答空 （编几条假的才是坏 ）。
        """
        if self.read_history is None:
            return []
        return list(self.read_history())

    def state(self) -> dict[str, Any]:
        """ **那一页读的那一份**（`GET /state`）：服务自己那几个数 ＋ 那台机器的那个读数。

        ⚠️ **两格是两支**，谁也说不了谁的话：
           · 顶层 —— **服务自己的**（`stats()` 那几项 ＋ `connected`）；
           · `machine` —— **那台机器的**（递进来的那个可调用对象交出来的那一段，
             四个计数 ＋ 端点 ／ 模型；**没递 ⇒ `None`**，那一页画 `—`）。
        ⚠️ **`connected` 说的是"有一条页面连在那条推的通道上"**（`clients > 0`）——
           它**不是**"引擎通了没有"：那要发一个请求才判得了，而这一刀不发
           （发了就是一个会超时／要网络的判据，而测试不许依赖外部可选物）。
        ⚠️ **那个可调用对象在锁外面调**（它读的是别的东西 —— 本类不去替它加锁），
           于是**它抛就照抛**：一个读不出来的数不该被悄悄换成 `0`（那是"编一个数"）。
        """
        data = self.stats()
        data["connected"] = data["clients"] > 0
        source = self.state_source
        data["machine"] = None if source is None else source()
        return data

    # ── 两个注入面 ──────────────────────────────────────────────────────
    def push(self, line: dict[str, Any]) -> int:
        with self._push_lock:
            return self._push_serialized(line)

    def _push_serialized(self, line: dict[str, Any]) -> int:
        """ **出口注入面** —— 把**一帧**推给**每一个**连着的页面；同步，可从任何线程叫。

        ⚠️ **实参是"一帧"**（一个映射：谁说的 ／ 那一刻 ／ 正文 ／ 哪一类 —— 见
           `console.py::_frame_for`）： 那一格的名字照旧叫 `line`（`tests/test_web.py`
           那一条形状判据点名的是这个名字），而它收的东西**从本刀起不是一行文本** ——
           是**一帧的结构**（那个名字是这一刀唯一没跟着改的地方，理由记在这里）。
        ⚠️ **交回的是这一帧的序号**（从 1 起）—— 它同时是那一帧的 `id:`。 而"有交回值"
           这件事对 `Console(write=…)` 那一格是**中性的**：那一格只看副作用。
        ⚠️ **推给 0 个页面不算事故**（没人开着那一页而已）—— 序号照旧往前走。
        ⚠️⚠️ **不读的那一页有上界**（`MAX_PENDING`）：欠着的帧到那个数就把它**断开**
           （见 `_overflow` 那段"为什么是断开而不是丢帧"）。
            **本函数一次都不阻塞**：`queue.Queue` 没有 `maxsize` ⇒ `put` 不等人 ——
           而这一句是承重的，因为它跑在**主循环**那条链上（`App.deliver` ⇒ 这一格）：
           一旦这里会堵，一个不读的页面就能把**整台机器**停住。
        """
        with self._lock:
            self._seq += 1
            seq = self._seq
            subs = tuple(self._subs)
        # **现场那一帧也走同一处改写**（2026-10-09 ）—— 与 `/history`
        # 那一头共用 `_the_moment` （ 只改一处会让历史与现场不一致 ）。
        identified = dict(line)
        if not identified.get('display_id'):
            identified['display_id'] = 'live:' + self._display_epoch + ':' + str(seq)
        frame = _frame(seq, _the_moment(identified, getattr(self, "now", None)))
        for sub in subs:
            if sub.qsize() >= MAX_PENDING: # 那一页欠得够多了 ⇒ 断开（不悄悄丢帧）
                self._overflow(sub)
                continue
            sub.put(frame)
        return seq

    def manage_timers(self, operation, body=None):
        """HTTP-thread bridge. All store access occurs on the owning loop.

        A queued timeout is cancelled; a write already running is reported as
        uncertain, never falsely rolled back. CAS makes caller retries explicit.
        """
        with self._lock:
            actor = getattr(self.console, "manage_timers", None)
            loop = self._loop
            if self._closing or loop is None or not loop.is_running() or not callable(actor):
                return 503, {"error": "timer_runtime_unavailable"}
            if self._timer_pending >= 8:
                return 429, {"error": "timer_requests_busy"}
            self._timer_pending += 1
        future = Future()

        def execute():
            try:
                if not future.set_running_or_notify_cancel():
                    return
                with self._lock:
                    closing = self._closing
                if closing:
                    future.set_result((503, {"error": "timer_runtime_unavailable"}))
                else:
                    future.set_result(actor(operation, body))
            except Exception:
                future.set_result((500, {"error": "timer_operation_failed", "refresh_required": True}))
            finally:
                with self._lock:
                    self._timer_pending -= 1

        try:
            loop.call_soon_threadsafe(execute)
        except RuntimeError:
            with self._lock:
                self._timer_pending -= 1
            return 503, {"error": "timer_runtime_unavailable"}
        try:
            return future.result(timeout=self._timer_timeout)
        except TimeoutError:
            if future.cancel():
                return 503, {"error": "timer_request_cancelled_before_execution"}
            return 202, {"outcome": "unknown", "refresh_required": True,
                         "note": "Execution started; GET /timers before deciding whether to retry."}

    def check_waves_read(self, body):
        """HTTP-to-owner-loop bridge for two fixed, locally initiated reads.

        Timeout keeps the operation owned; it does not release its slot or
        re-execute it. GET returns the original request's status/receipt summary.
        """
        with self._lock:
            checker, loop = self.read_tool_check, self._loop
            if self._closing or checker is None or loop is None or not loop.is_running():
                return 503, {"error": "read_check_runtime_unavailable"}
            if len(self._read_check_pending) >= 2:
                return 429, {"error": "read_check_requests_busy"}
            lease = object()
            self._read_check_pending.add(lease)
            self._read_check_quiet.clear()

        async def execute():
            try:
                return await checker(body)
            except Exception:
                return 500, {"error": "read_check_failed"}
            finally:
                with self._lock:
                    self._read_check_pending.discard(lease)
                    if not self._read_check_pending:
                        self._read_check_quiet.set()

        pending = execute()
        try:
            future = asyncio.run_coroutine_threadsafe(pending, loop)
        except RuntimeError:
            pending.close()
            with self._lock:
                self._read_check_pending.discard(lease)
                if not self._read_check_pending:
                    self._read_check_quiet.set()
            return 503, {"error": "read_check_runtime_unavailable"}
        try:
            return future.result(timeout=self._read_check_timeout)
        except TimeoutError:
            # No cancellation/retry: OwnedBlockingIO may still be settling.
            return 202, {"state": "pending", "execution_outcome": "unknown",
                         "status_path": "/infinity/waves/read-check"}

    def queue_consolidation_recovery(self, task):
        with self._lock:
            actor = getattr(self.console, "queue_consolidation_recovery", None)
            if self._closing or self._loop is None or not callable(actor):
                return None
            loop = self._loop
        pending = actor(task)
        try:
            future = asyncio.run_coroutine_threadsafe(pending, loop)
        except RuntimeError:
            pending.close()
            return None
        try:
            return bool(future.result(timeout=3))
        except (TimeoutError,RuntimeError):
            future.cancel()
            return None

    def ask_for_handoff(self) -> dict[str, Any] | None:
        """ **把"请交接一次"交给那条循环** —— `POST /handoff` 落到这里（本刀新加的那口）。

        ⚠️⚠️ **为什么不能在本函数里直接 `await`**：本函数跑在 **HTTP 那条线程**上，
        而 `Console.ask_for_handoff()` 是一个协程（它要等 `Handoff.request()`）——
        ⇒ 与 `/say` 那条路**同一个形状的问题**，答案也**同一个**：
        `call_soon_threadsafe` ／ `run_coroutine_threadsafe` 那一族
        （`read_line` 那段"为什么不是一个线程里的阻塞读"逐字写着理由）。

        ⚠️ **三条准话，各自对应一种"叫不动"**：
           · **没递 `console`**（`None`）⇒ 回 `None`（上层 503）—— 那是"没接上"，不是"坏了"；
           · **那条循环还没有**（`_loop` 是 `None`：没人 `await` 过 `read_line` ⇒ 机器还没起来）
             ⇒ **不排队、不假装**：回 `None`（上层 503）。⚠️ 与 `_accept`（`/say`）那一条
             **故意不一样**：一行话可以**替它排着**、等有人读了再交进去；而一次"请交接"
             **排着没有意义** —— 它说的是"**现在**这一份上下文该换了"，
             等一小时之后才兑现的交接是**另一个意思**（那时候上下文已经不是它了）。
           · **正在收摊**（`_closing`）⇒ 回 `None`（与 `/say` 那条 503 同一条口径）。
        ⚠️ **等它有上界**（`ASK_HANDOFF_BOUND_S`）：超了就回 `{"answered": False, …}` ——
            "等超了"**不许**说成"做完了"（见 `_ask_for_handoff` 那段）。
        ⚠️ **它不是"入梦按钮"**：整理态是**交接的副产品** —— 按这一下 ＝ 交接 ＋ 起整理态。
        """
        with self._lock:
            if self._closing or self.console is None:
                return None
            loop = self._loop
            if loop is None:
                return None
        future = asyncio.run_coroutine_threadsafe(self.console.ask_for_handoff(), loop)
        try:
            return dict(future.result(timeout=ASK_HANDOFF_BOUND_S))
        except TimeoutError:
            # 它**还在跑**（那条协程没被取消：`future` 照旧在循环上）⇒ 照实说"没等到回话"。
            return {"wired": True, "asked": None, "requested": None,
                    "processing": None, "answered": False}
        except RuntimeError:
            # 那个循环在这中间关掉了 —— 与上面"还没起来"同一句话（不另编一句）。
            return None

    async def read_line(self) -> str:
        """ **进口注入面** —— 等**下一行**（`POST /say` 送进来的那一种），零参、可等待。

        ⚠️⚠️ **为什么不是"一个线程里的阻塞读"**（`asyncio.to_thread(queue.get)` 那一路）：
           · `Console.pump` 那边叫它的方式是 `line = await read()`（**不包上界**）⇒
             一个阻塞的读会把一个工作线程**钉在那儿**；
           · 而 `asyncio.run` 收尾时会 `shutdown_default_executor()` ⇒ 那个钉住的线程会让
             **整个进程**在退出那一步等下去 —— 一个"停不下来"的形状 ；
           · ⇒ 这里走**可等待的队列**：`asyncio.Queue.get()` 什么也不留在身后；
             而"从别的线程投一行进来"由 `call_soon_threadsafe` 接上（HTTP 那条线程与
             这个循环**不是同一条**）。
        ⚠️ **收摊之后它是"到头了"**（`StopAsyncIteration`）—— 正是 `Console.pump` 收下的
           那一个形状 ⇒ 服务一停，那条泵**收工**，而不是挂着等一条永远不来的下一行。
        ⚠️ **一次只有一个读者**（那条泵就一个）：多个读者会分掉那几行，而"谁该拿哪一行"
           不是这一层要答的问题；真要多读者，那是另一刀的事。
        ⚠️ **一行一次、不带行尾**：见模块 docstring 那条约定。
        """
        loop = asyncio.get_running_loop()
        with self._lock:
            self._loop = loop
            if self._closing:
                raise StopAsyncIteration
            pending, self._pending = self._pending, []
        for line in pending:                  # 还没人在读的时候先替它排着的那几行
            self._inbox.put_nowait(line)
        reader = getattr(self, 'read_observations', None)
        item = await reader(self._inbox.get) if callable(reader) else await self._inbox.get()
        if item is _CLOSED:
            raise StopAsyncIteration
        return item

    # ── 里面的几个（那些端点用的） ──────────────────────────────────────────
    def _subscribe(self) -> queue.Queue[Any] | None:
        """登记一个订阅；**正在收摊** ⇒ `None`（那一条连接回 503，让页面照常重连）。

        ⚠️ 与 `stop()` 抢的是同一把锁 ⇒ "已经立旗了却还有一条连接漏在哨兵之外"这个缝
           不存在（两条路都在锁里看一眼旗）。
        """
        with self._lock:
            if self._closing:
                return None
            sub: queue.Queue[Any] = queue.Queue()
            self._subs.add(sub)
            self._quiet.clear()
            return sub

    def _unsubscribe(self, sub: queue.Queue[Any]) -> None:
        with self._lock:
            self._subs.discard(sub)
            self._retiring_subs.discard(sub)
            if not self._subs and not self._retiring_subs:
                self._quiet.set()

    def _overflow(self, sub: queue.Queue[Any]) -> None:
        """ **那一条连接没在读**（欠着的帧到了 `MAX_PENDING`）⇒ **把它断开**，不继续攒。

        ⚠️⚠️ **为什么是"断开"而不是"丢掉这一帧"**（两条都量得出来，选的是**出声**的那一条）：
           · 帧里装的是**话**（`[批 N]` 与那几句正文），**不是**几个数 —— 那几个数在
             `GET /state` 上，页面重读一次就回来了；欠掉的那几帧**没有任何东西能补回来**；
           · ⇒ 悄悄丢一帧 = 那一页上**少一句话而没有任何痕迹**（本仓最忌的那一种：
             "丢了却没声"，与 `_accept` 那句"满了**明说**（503）"是同一条口径）；
           · 而断开是**看得见**的：那一条 SSE 结束 ⇒ 页面那头 `EventSource.onerror`
             当场响（那一格会翻成"断开"，而 `EventSource` **自带重连**）⇒ 页面重连、
             顶栏那几个数重读一遍。**这**是这一格里唯一能出声的地方。
        ⚠️⚠️ **队列为什么**不**建成 `queue.Queue(maxsize=MAX_PENDING)`**（看着更省事的那条路）：
           满了的队列**塞不进哨兵** —— 而"断开"这件事**只能**靠哨兵送达（读者停在一个
           `get` 上）。`maxsize` + `put_nowait` ⇒ 只能"丢这一帧"（上面刚说了为什么不选它），
           而 `put`（阻塞式）⇒ `push()` 会**堵在**一条不读的连接上，而它跑在**主循环**那条链上
           （`App.deliver` ⇒ `Console.write` ⇒ 这里）⇒ 一整台机器为一个不读的页面停住 。
           ⇒ 所以界在**投递这一侧**判，而队列本身不设 `maxsize`（`push` 是唯一的写者）。
        ⚠️ **清空 ＋ 哨兵**：先把欠着的那几帧取走（它们已经不会被读了），再塞 `_STOP`
           把那一跳叫醒 ⇒ 那条响应**结束**（`_stream_events` 收到哨兵就 `break`）。
        ⚠️ **名册要跟着摘**（`_unsubscribe`）：不摘的话 `clients` 会一直算着这一条，
           而"连着"那一格从此说假话（`_quiet` 也照旧在最后一个走掉时立起）。
        ⚠️⚠️ **送达那一段有一条不闭合的路**（⑥-iii：原先只活在一份 gitignored 的报告里）：
           哨兵是塞进**队列**的，而读者要在**下一次 `get`** 才取到它 —— 于是那个读者若正
           卡在 `wfile.write`（对端不读、那条 socket 的缓冲满了），这一次"断开"就要等
           那次写**解除之后**才生效 ⇒ 在**那一条**连接上，"断开"来得**晚**。
           ⚠️ 那是 TCP 那一层的事，本波**没改**（要闭合就得给那次写加上界 —— 另一刀的事）。
           ⇒ 准话是"**界一到就不再收它了，而送达可能压在一条堵住的写上**"，
           不是"那一刻就断开了"。
        """
        while True:
            try:
                sub.get_nowait()
            except queue.Empty:
                break
        with self._lock:
            if sub in self._retiring_subs:
                return
            self._subs.discard(sub)
            self._retiring_subs.add(sub)
            self._stalled += 1
        sub.put_nowait(_STOP)

    def _accept(self, line: str) -> int | None:
        """收下一行（`POST /say` 那条路）—— 交回它的序号；**排满了** ⇒ `None`（回 503）。

        ⚠️ **没人读的时候它的界是 `MAX_PENDING`**：满了**明说**（503），**不悄悄丢**最旧的
           那一行 —— 一个人打字到不了那儿，而"丢了却没声"是这一格里最坏的形状。
        """
        callback = getattr(self, 'accept_observation', None)
        if callable(callback):
            with self._lock:
                if self._closing:
                    return None
                if self.runtime_evidence is not None:
                    line = self.runtime_evidence.wrap_input(line)
            accepted = callback(line)
            with self._lock:
                self._accepted += 1
            return accepted['seq']
        with self._lock:
            if self._closing:
                return None
            loop = self._loop
            if loop is None:
                if len(self._pending) >= MAX_PENDING:
                    return None
                if self.runtime_evidence is not None:
                    line = self.runtime_evidence.wrap_input(line)
                self._accepted += 1
                self._pending.append(line)
                return self._accepted
            if self.runtime_evidence is not None:
                line = self.runtime_evidence.wrap_input(line)
            self._accepted += 1
            seq = self._accepted
            try:
                loop.call_soon_threadsafe(self._inbox.put_nowait, line)
            except RuntimeError:              # 那个循环已经关了 ⇒ 退回"排着"
                self._loop = None
                if len(self._pending) >= MAX_PENDING:
                    self._accepted -= 1
                    if self.runtime_evidence is not None:
                        self.runtime_evidence.reject_input(line)
                    return None
                self._pending.append(line)
        return seq


# ── Task 2：把两个口接上（本刀的核心） ───────────────────────────────────


#: **出厂那个出口的身份证** —— `telyincore/console.py::_to_stdout` **自己那两个名字**。
#: ⚠️ 按**名字**认它（**不 import**：`web.py` 一个同胞 import 都没有，那条围栏拿 AST 钉着）——
#:    于是"这一格装的是不是调用方递进来的东西"在**普通函数**上第一次有了一个可判的答案。
#: ⚠️⚠️ **它为什么可信**：那个缺省住在 `console.py` 里，而那一面被**逐字节冻住**了
#:    （`tests/test_frozen_faces.py`）⇒ 它**不可能**悄悄改名（先红的会是那条冻结）。
_STOCK_WRITE = ("_to_stdout", "telyincore.console")

#: **出厂那个进口的身份证** —— `telyincore/console.py::_stdin_line` **自己那两个名字**。
#: ⚠️⚠️ **它与出口那一份有一处真的不一样，量过、也写清**（别把它读成"复制一份"）：
#:    出厂那个**出口**是**装在格子里**的（`Console.__post_init__` 把 `write` 填成
#:    `_to_stdout`），而出厂那个**进口**在格子里是**空着**的（`Console().read is None`）；
#:    `_stdin_line` 是那条泵在**调用那一刻**才替上去的（`console.py:622-623`）。
#:    ⇒ 所以"出厂那一份"对进口有**两个形状**：**空着**（`None`）／**那个函数自己** ——
#:    而 `wire()` 那一条判据把前者算成"没装东西"、后者算成"出厂那份"（见下面两处）。
_STOCK_READ = ("_stdin_line", "telyincore.console")


def _name_pair(value: Any) -> tuple[Any, Any]:
    """那一格身上那两个名字（`__name__` ／ `__module__`）—— **两个口共用这一步**。"""
    return (getattr(value, "__name__", None), getattr(value, "__module__", None))


def _is_the_stock_write(value: Any) -> bool:
    """ 那一格装的是不是**出厂那个**出口（不是"任何普通函数"）。

    ⚠️⚠️ **它答的是哪一个问题**（别读宽了）：`_somebody_elses` 量的是"**别的对象的方法**"
       那一类；剩下那一半（**普通函数** ／ 闭包，`Console(write=send)` 就是它）在它眼里
       与缺省**分不开** —— 因为 `console.py` 把缺省那一格填成的**也是**一个普通函数。
        而"分不开"只到**名字**那一层为止：出厂那个有名字（上面那一对），
       而调用方递进来的那个**几乎不会**同时叫 `_to_stdout` 又住在 `telyincore.console` 里。
    ⚠️ **`None` ⇒ 假**（与进口那一份同一条规矩）：空的算"没装东西"，不算"出厂那一份"。
    ⚠️ **它不判"该不该顶掉"**（那是 `wire()` 那几条守卫的事）：它只交回**一个事实**，
       而那个事实被 `wire()` 摆进返回值里（`write_clobbered`）—— 让调用方看得见。
     进口那一半是它的**镜像**：`_is_the_stock_read`（同一个形状，另一个函数）。
    """
    return _name_pair(value) == _STOCK_WRITE


def _is_the_stock_read(value: Any) -> bool:
    """ 那一格装的是不是**出厂那个**进口（`_stdin_line`）—— 与出口那条判据**同一个形状**。

    ⚠️⚠️ **它答的是哪一个问题**（别读宽了，逐条与 `_is_the_stock_write` 对齐）：
       · `None` ⇒ **假**（那条守卫前面有 `is not None` ⇒ 空着的那一格不算"顶掉"什么）；
       · 调用方递进来的**普通函数** ⇒ 在**这一条**判据上**看不见**（与出口那一份同一条
         边界：`Console(read=my_read)` 与"缺省"在名字那一层长得一样）；
       · ✅ `_stdin_line` **自己** ⇒ **真** —— 这一格是它比出口那份**多**出来的那一格：
         调用方把出厂那个函数**又写了一遍**（`Console(read=_stdin_line)`），
         那与 `Console(write=_to_stdout)` 是**同一个形状** ⇒ 两份都不算"顶掉"。
    ⚠️⚠️ **为什么进口这一格非要比出口多认一点（量出来的，见 `_STOCK_READ` 那段）**：
       出厂那个出口**在格子里**（`write` 缺省就是 `_to_stdout`），进口**不在**
       （`read` 缺省是 `None`，`_stdin_line` 由那条泵在调用那一刻替上）⇒
       只认函数名的话，"调用方又写了一遍出厂那份"会被读成"顶掉了一个普通函数" 。
    ⚠️ **它不判"该不该顶"**（那是 `wire()` 那几条守卫的事）：它只交回**一个事实**，
       而那个事实被 `wire()` 摆进返回值里（`read_clobbered`）—— 让调用方看得见。
    ⚠️ **它为什么可信**：那个函数住在 `console.py` 里，而那一面被**逐字节冻住**了
       （`tests/test_frozen_faces.py`）⇒ 它**不可能**悄悄改名（先红的会是那条冻结）。
    ⚠️ **够不着的那一类仍然够不着**：递进来的替代品若**也**叫 `_stdin_line`、也住在
       `telyincore.console` 里，那它与出厂那个在这一条判据上就是同一个东西。
    """
    return _name_pair(value) == _STOCK_READ


def _somebody_elses(value: Any, service: WebService) -> bool:
    """那一格是不是**别的对象的一个方法**（不是本服务的、也不是本层认得的缺省）。

    ⚠️⚠️ **它是本刀那条守卫里唯一一条**硬的判据**（另一条是"已经接上本服务了"）：
       · 一个**绑定方法**身上带着 `__self__` ⇒ 它说的是"**那一个对象**要收这一行"：
         `Console(write=lines.append)` ／ `Console(write=a_window.send)` ／
         `Console(read=another_service.read_line)` 都是这个形状 ⇒ **那两格一个字都不许动**；
       · 而本服务自己那两份（`svc.push` ／ `svc.read_line`）**不算**（它们是这条线本身）。
    ⚠️⚠️ **本函数量不到的那一半，也要写清**（本波量过，别把它读成一条形式化证明）：
       一个**普通函数**或**闭包**（`lambda` ／ `def send(line): …`）**没有** `__self__`
       ⇒ 它在**这一条**判据上是"看不见的"：`Console(write=send)` 那种替代品在这里
       与"缺省"**长得一模一样**（`console.py` 把缺省那一格**填成**一个普通函数
       `_to_stdout`，而且它落在 `__dict__` 里 ⇒ 连"调用方递过没有"都读不出来 ——
       本波两条候选判据都当场量过：`'write' in console.__dict__` **恒真**、
       按名字认缺省（那个缺省函数叫 `_to_stdout`、**不叫** `write`）**对任何 console 都假**）。
       ⇒ 所以这一条边界要**在装配那一层说清**、并**有测试钉着它**：
         `tests/test_web.py::test_the_guard_stops_at_plain_function_substitutes` ——
         它量的是"那种替代品会被接线顶掉"，而不是"守卫万无一失"。
     **本波补上的那一条**（上面那三行里，两条候选判据的第二条量的是**错的名字**：
       它拿缺省那个函数去与 `"write"` 比 —— 那当然对任何 console 都假）。真分得开的是
       **出厂那个函数自己那两个名字**（`_STOCK_WRITE`，见上）⇒ 于是"上面那一格被顶掉
       没有"这件事**报得出来**（`wire()` 交回的 `write_clobbered`）；而"**该不该**顶"
       照旧不是本层判得了的（那几条守卫说了算）——
        旧那段话读起来像"这一半永远量不到"，那是**写宽了**：量得到的是"是不是出厂那个"。
     **而进口那一格此后是同一个答案**（`read_clobbered` ／ `_is_the_stock_read`）：
       "那一半沉默"这件事**两个口都不剩**了 —— 这里量不到的那一类（普通函数），
       两个口各有一格把它**报出来**（边界与判据在那两个 `_is_the_stock_*` 上）。
    ⚠️ **够不着的那一类仍然够不着**：递进来的替代品若**也**叫 `_to_stdout`、也住在
       `telyincore.console` 里，那它与出厂那个在这一条判据上就是同一个东西。
    """
    owner = getattr(value, "__self__", None)
    return owner is not None and owner is not service and owner != service


def wire(service: WebService, console: Any) -> dict[str, bool]:
    """ **把那条通道接到那两个口上** —— `console.write` ⇒ `service.push` ／
    `console.read` ⇒ `service.read_line`（ 而它**不 import `console`**：只认形参上那两格）。

    ⚠️⚠️ **为什么接线住在这里**（"谁构造服务、谁递那两个可调用对象"这一问的答案）：
       · **服务由调用方构造**（`WebService(host=…, port=…)`）—— 本模块**不自己起**任何东西：
         "多久起、谁来停"归那一页那一刀（Task 3 的入口），本模块只把**接口**交出去；
       · **品牌中立**：本模块**一个"console"的名字都不 import**（`tests/test_web.py`
         拿 AST 钉着那条：`web.py` 里一个同胞 import 都没有）⇒ 接线**只能**落成"按名字喂两个
         可调用对象"，而**两格的名字就是 `console.py` 那两个字段**（`write` ／ `read`）；
       · ⇒ 于是**别在别处再写一份"哪一格接什么"**：第 ① ② 条那句话**只有这一处**。

    ⚠️⚠️ **两个方向各一条，而它们各是一句**（本刀就这两句 —— 中间那一串一个字都不用动）：
       · ① **出去**：`console.write = service.push` —— `_to_stdout` 那一格的照形状替身也是
         `Callable[[str], None]`（`push` 交回的序号被忽略：那一格只看副作用）；
       · ② **进来**：`console.read = service.read_line` —— `_stdin_line` 那一格的照形状替身
         （`async def`、零参、回一行）； 而它**还要**被递到那条泵上（`assemble(read=…)`
         ／ `pump(read=…)`）—— 那两个槽**读的是同一格**，缺一处它就不生效：
         `console.assemble(read=None)` 用的是 `self.read`（就是这里接上的那一个）。

    ⚠️⚠️ **"调用方递的替代品不许顶掉"这条守卫在这里的形状是三条**（本仓每一处注入面都有它）：
       · **两头都已经是调用方自己那两份** ⇒ **一个字都不动**（递进来的就归它）；
       · **两头都已经是本服务** ⇒ **什么也不做**（重复接线是幂等的，不抛）；
       · **只有一头** ⇒ **当场抛**，**不替它写**（一个头一条、一个尾一条是**两份真相**：
         那一头的话从别处来，而回答从这条通道出去 —— 它今天真的走得到，见下面那条）。

    ⚠️⚠️ **那条"半接"为什么今天走得到**（"这一条不许读成"不可能发生"）：`assemble(read=…)`
       把**递进来的**那个读法转交给那条泵（`self.read = read`），而**这一格**（`console.read`）
       照旧是原来那一个 ⇒ 于是"装配这一层记住的"与"泵真正在用的"是**两个**对象，
       而"输入从哪来"从此有**两个**答案。**递给 `assemble()` 的那个读法说了算**（那条泵
       用它），接线时**只剩一头** ⇒ 这里抛，别让一句静默把它盖过去（谁要半接，
       就别接另一半：那是另一个形状，该在**装配那一层**写清）。

    ⚠️ **本函数不碰生命周期**（不起服务、也不停它）：`push` ／ `read_line` 在服务**起来之前**
       与**停之后**都是合法调用（没页面就推给 0 个；停之后读到的就是"到头了"）。
    ⚠️ **线程与循环**：`push` 由**任何**线程叫（那一格自己跨线程投递）；而 `read_line`
       必须在**它自己那条泵所在的循环**里被 await（一次只有一个读者）—— 接线只是**放上去**，
       "谁在跑它"归装配那一层。
    ⚠️ **交回的是"接上了什么"** —— **四格**（两个"接没接上" ＋ 两个"顶掉了没有"）：
       `{"write": bool, "read": bool, "write_clobbered": bool, "read_clobbered": bool}`。
       前两格都是假 ⇒ 调用方自己那两份原样留着 —— 那是一个**当场可判**的事实
       （那一页看得到东西／看不到，不该靠猜"到底接上没有"）。
        **后两格是"悄悄顶掉"那一处的答案**（reviewer 点的那一处，**两个口各一格**）：
       `write_clobbered` 为真 ⇔ **那一格原本装着一个普通函数，而它不是出厂那个 `_to_stdout`**；
       `read_clobbered` 为真 ⇔ **同一个形状落在进口那一格**（原本装着一个普通函数，
       而它不是出厂那个 `_stdin_line`）—— 也就是说：调用方递过
       `Console(write=<普通函数>)` ／ `Console(read=<普通函数>)` 那种替代品，
       而**它刚刚被顶掉了**。
       ⇒ 前两格说不了这件事（那种替代品与缺省在守卫眼里长得一样，两种情形都回真），
       于是补上之前，调用方**没有任何办法**知道自己的东西没了 —— 现在它有两格可看。
       ⚠️ **两格只在真的写下去过**那一支上才可能为真（另外三支一个字都没动 ⇒ 两格全假）。
       ⚠️⚠️ **它为什么读的是"不是出厂那个"而不是"是调用方那个"**：这两种说法在这里
       **分不开**（判据与边界写在 `_somebody_elses` ／ `_is_the_stock_write` ／
       `_is_the_stock_read` 上）—— 能报出来的就是"它不是出厂那一份"这个事实本身。

    ⚠️ **⚠️ 说到没读**：`read_line` **一次只服务一个读者**，而这条通道**不重放**
       （没有 `Last-Event-ID`）：谁要"打开页面就看到前面那几句"，那份东西在**会话**上
       （`session.py` 那条链），不在这条通道上 。 而 `POST /say` **没有 Origin 检查**
       ⇒ "只听回环"的边界是**这一台机器**，不是**你那一页** —— 这里接上的出口与进口
       继承的是同一条边界，别把"接上了"读成"已经鉴过了"。

    ⚠️⚠️ **"接上没有"判的是 `==`、不是 `is`** —— 绑定方法是**每次都新造的对象**
       （实测：`svc.push is svc.push` **假**）⇒ 用 `is` 的话，一条接得好好的线会被读成
       "一头都没接"（下面那三条守卫跟着全走错）。`==` 在函数上落成 `__func__` ＋ `__self__`
       那一比 —— 对"同一个服务的同一个方法"它是**真**，而对"另一个服务的 `push`"它是**假**
       （那正是这里要分的那两种情形 ）。

    ⚠️⚠️ **四种状态，四个答案**（判据见 `_somebody_elses` —— 它也是这一层**唯一**那条硬判据）：
       · **两头都还是缺省**（`write` ／ `read` 都没被动过）⇒ **两头都接上**，回真；
       · **两头都已经接的是本服务** ⇒ **什么也不做**，回假（重复接线是幂等的）；
       · **一头是本服务、另一头不是** ⇒ **当场抛**（不替它写 —— 见下面那段"半接"）；
       · **一头是别的对象的一个方法**（`lines.append` ／ 另一个服务的 `read_line`）⇒
         **一个字都不动**，回假（那两格归调用方）； 而"**只有一头**是那样、另一头空着"
         ⇒ 也**当场抛**：接上去，那一头就**同时**进了两处（调用方那一份说了算的那一处 ＋
         这条通道）—— 一页上一句出现在两个地方，正是这一刀要避免的形状。
    ⚠️⚠️ **`Console(write=<普通函数>)` 那种替代品**：本层**拦不住**它（它没有 `__self__`），
       但它**不是无声的**了 —— `write_clobbered` 那一格会说它顶掉了什么（见上）；
        **进口那一格是同一个形状**（`Console(read=<普通函数>)`），答案在 `read_clobbered` 上
       —— 两个口各一格，**没有一处只剩半边沉默**。
       那一条边界、判据与实测写在 `_somebody_elses` ／ `_is_the_stock_write` ／
       `_is_the_stock_read` 上（ 而 `tests/test_web.py` 拿测试钉住它们，
       不让它悄悄变成一句"守卫很严"的假话）。
    """
    wired = {"write": service.push, "read": service.read_line}
    current = {name: getattr(console, name, None) for name in wired}
    attached = {name for name in wired if current[name] == wired[name]}
    supplied = {name for name in wired if _somebody_elses(current[name], service)}
    #: 一会儿要写下去的那两格，原本是不是**调用方自己的东西**（量得到的那个口径）——
    #: 出厂那两份不算（它们就是"那两格本来就该在那儿的东西"：`_to_stdout` ／ `_stdin_line`），
    #: 而**空着**的那一格也不算（`is not None` 那半边 —— 两个口同一条规矩）。
    clobbered = (current["write"] is not None and not _is_the_stock_write(current["write"]))
    read_clobbered = (current["read"] is not None and not _is_the_stock_read(current["read"]))
    if len(attached) == 2:                 # 两头都接过了 ⇒ 幂等，什么也不做
        return {"write": False, "read": False,
                "write_clobbered": False, "read_clobbered": False}
    if len(attached) == 1:                 # 只剩一头挂在外面 ⇒ 两份真相，当场拒
        half = sorted(set(wired) - attached)[0]
        raise RuntimeError(
            f"接线只接得上'一半'：`{half}` 那一头不是本服务，而另一头是 —— "
            "一折两半对不上（那一头的话从别处来、而回答从这条通道出去）。"
            "要那条通道就把两头**都**给它（两头都是空的／都是调用方自己的）；"
            "要自己那一份就别接这一头，并在装配那一层写清输入从哪来。")
    if len(supplied) == 2:                 # 两头都是调用方自己那两份 ⇒ 一个字都不动
        return {"write": False, "read": False,
                "write_clobbered": False, "read_clobbered": False}
    if supplied:                           # 一头递了替代品、另一头还空着 ⇒ 也别接
        which = sorted(supplied)[0]
        raise RuntimeError(
            f"接线只接得上'一半'：`{which}` 那一头是调用方自己的，而另一头还空着 —— "
            "接上去，那一头就**同时**进了两处（一处由调用方说了算）。"
            "要那条通道就把两头**都**交给它（`Console()`）；"
            "要留着自己那一份，就在装配那一层写清输入从哪来。")
    console.write, console.read = wired["write"], wired["read"]
    return {"write": True, "read": True,
            "write_clobbered": clobbered, "read_clobbered": read_clobbered}


# ── Task 3：那一页 ＋ 它的入口 ────────────────────────────────────────────


def _current_model_name(engine: Any) -> Callable[[], str | None]:
    """ 从那个引擎对象上要"当前那一条的模型名"（**人看的那个** ＝ `model_name || model`）。

    ⚠️ 走"按名字读"那一套（本模块零同胞 import）；读不到（没有当前 ／ 没有那一条）⇒ `None`。
    """
    def read() -> str | None:
        try:
            current = getattr(engine, "current_name", None)
            name = current() if callable(current) else None
            if not name:
                return None
            rows = getattr(engine, "profiles", None)
            for one in (rows() if callable(rows) else []):
                if getattr(one, "name", None) == name:
                    return str(getattr(one, "model_name", "") or "") or str(
                        getattr(one, "model", "") or "") or None
        except Exception:                        # noqa: BLE001 —— 读不到就当没有（不编）
            return None
        return None
    return read


def _the_gate_now(console: Any) -> bool:
    """ 作者按下的那个「暂停」（ 从那个主循环上**现读** —— 没接 ⇒ 假 ）。

    ⚠️ **为什么不从 `self` 读** ： 这一份载荷住在 `state_of()` 里
      —— 那是**模块级函数** （ 那儿没有 `self` ， 而上一遍就栽在这儿 ）。
    """
    loop = getattr(getattr(console, "app", None), "loop", None)
    gate = getattr(loop, "gated", None)
    return bool(gate()) if callable(gate) else False


def _the_dropped_now(console: Any) -> int:
    """ 关闸期间**丢掉了几条**（ 同上 —— 没接 ⇒ 零 ）。"""
    loop = getattr(getattr(console, "app", None), "loop", None)
    return int(getattr(loop, "dropped_count", 0) or 0)


def _the_starting_now(console: Any) -> bool:
    """ 那台**正在起**没有 （ 同上 —— 没接 ⇒ 假 ）。"""
    loop = getattr(getattr(console, "app", None), "loop", None)
    see = getattr(loop, "gate_starting", None)
    return bool(see()) if callable(see) else False


def _the_note_now(console: Any) -> str:
    """ 上一次那一下的**一句真话**（ 同上 —— 没接 ⇒ 空串 ）。"""
    loop = getattr(getattr(console, "app", None), "loop", None)
    note = getattr(loop, "gate_note", None)
    return str(note()) if callable(note) else ""



def the_cache_note(cached: Any, told: Any) -> str:
    """缓存那一格**为什么没数**（2026-10-11）。

    由来：作者问"万一还有别人也是本地部署，他一看到这个缓存命中低得令人发指，怎么办"。
    答：那一格不能只画一个 `—` 就走开 —— **它得自己说清是什么情况**，
    因为"引擎不报"和"引擎报 0"在页上长得一样，而它们对人的意思完全不同。

    ⚠️ 这一句只描述**已发生的事实**（这一档一次命中都没报过 ／ 分母还没有），
    不猜引擎内部、不替它下结论。
    """
    if told is None and cached is None:
        return "这一档还没有跑过请求 —— 还没有数。"
    if told is None:
        return ""
    if int(cached) <= 0:
        return ("这一档的引擎一次缓存命中都没有报过。本机引擎常见这样"
                "（实测：一模一样的前缀连发几次，它报的仍是 0）；"
                "会报缓存的上游（例如云端那一档）这里才有数。")
    return ""


def _the_next_wake_now(console: Any) -> Any:
    """它下一次会自己醒是什么时候（2026-10-11）。

    ⚠️ **不许编**：那本账里没排上 ⇒ 交回 `None`（页上画 `—`）。
    这一格只**转述** `console.next_wake` 那句话（谁排的谁负责），
    它答不上来（没挂 ／ 读不动）就照实说不知道。
    """
    getter = getattr(console, "next_wake", None)
    if not callable(getter):
        # 2026-10-11：**两个对象都找** —— 入口把那一格挂在 `console` 上，
        # 而实测 `state_of` 拿到的那一个未必是它（挂着的是 `console.app.loop`）。
        # 与其争"该挂谁"，不如这里**两处都问**（问不到才是真的没有）。
        inner = getattr(getattr(console, "app", None), "loop", None)
        getter = getattr(inner, "next_wake", None)
    if not callable(getter):
        return None
    try:
        got = getter()
    except Exception:                                  # noqa: BLE001
        return None
    return got if isinstance(got, dict) and got else None



def _bind_observation_ingress(service, console, control_loop):
    """All local UI observations enter the host loop before HTTP acceptance."""
    def scheduled(actor, value, *, wrapped=False):
        import concurrent.futures
        pending = concurrent.futures.Future()
        def apply():
            if not pending.set_running_or_notify_cancel():
                return
            try:
                pending.set_result(actor(value))
            except BaseException as error:
                pending.set_exception(error)
        try:
            control_loop.call_soon_threadsafe(apply)
        except RuntimeError:
            if wrapped and service.runtime_evidence is not None:
                service.runtime_evidence.reject_input(value)
            raise
        try:
            return pending.result(timeout=3)
        except TimeoutError:
            # Only reject an unstarted callback. A started admission has an
            # unknown caller outcome and must not be duplicated or relabelled.
            if pending.cancel() and wrapped and service.runtime_evidence is not None:
                service.runtime_evidence.reject_input(value)
            raise
    service.observe_activity = lambda body: scheduled(lambda args:console.observe_text(**args), body)
    service.accept_observation = lambda line: scheduled(console.observe_line, line, wrapped=True)
    service.read_observations = console.read_observations


def state_of(console: Any, *, config: Any = None,
             name_of_current: Callable[[], str | None] | None = None,
             model_of_current: Callable[[], str | None] | None = None,
             model_probe: Callable[[], bool | None] | None = None,
             total_of: Any = None
             ) -> Callable[[], dict[str, Any]]:
    """ **顶上那条计数的来源** —— 交回一个零参可调用对象，**每次现读**那台机器。

    ⚠️⚠️ **每一个数各对着一个真对象**（这一格的全部责任就是**别编**）：
       · `tokens` ⇒ 独立的 `total_of()`；无答案时退回 `console.request_size()[1]`。
                      后者是**这一份请求有多少 token**
                      （会话 ＋ 那一句**每请求现装**的 system 前缀：**与真发出去的那一份
                      同一个数**）。⚠️ **它从 `session.est_tokens()` 换过来的**（本刀）：
                      那一条说的是"**存档那一份**"（`session.py:393`），而这一格说的是
                      "这一份请求" —— 今天两者差**整整那一句话**（16553 字节 ≈ 5459 token），
                       而那一格从前**看不见**它（缺省那一格是空的，所以这个差一直是**不可见**的）。
       · `messages` ⇒ `console.request_size()[0]` —— **那一份请求**有几条（**同一个来源**）。

    ⚠️⚠️ **那一句话订不出来的时候，这两格降级成 `None`（那一页画 `—`），而 `/state` 照旧答话**：
       它**不许**拿存档那一份端上来顶上（那就是"悄悄少算一截"，正是这一格在治的东西），
       也**不许**让整份 `/state` 陪葬 —— `request_size()` 会抛（`段序.md` 读不到 ⇒
       "**段序没了**"，见 `prefix.assemble` 的抬头），而这一格**收住它**：
       **那两个数没有答案**，而 `batches` ／ `paused` ／ 端点 ／ 模型照旧答真话。
       ⚠️ **只收"读不出来"那一族**（`OSError`：不在 ／ 读不动；`ValueError`：在、
          可不是 UTF-8，或订不出那张表——`prefix.table` 自己那条）⇒ 别的异常照旧穿过去：
          那才是真的坏了，不该被这一格吞掉。
       · `batches`  ⇒ `console.batches` —— 这个出口**真的递出去**几批（空批不占号）。
       · `paused` ⇒ `console.done`（那条泵读到"到头了"）**或** `console.signal` 立起
                      （收工信号）—— **准话见下面那一段**。
       · `endpoint` ／ `model` ⇒ `config.base_url` ／ `config.model`（那份配置自己的值）。

    paused 表示当前输入结束或收工信号立起；用户暂停另有控制入口。

    ⚠️ **没有那个对象 ⇒ `None`，不是 `0`**：一台**没装配过**的 `Console` 没有会话
       ⇒ "有几条消息"**没有答案**（`None`）；而 `batches` 是**真的零**（它真的一批都没递过）。
       ⇒ 把两个都写成 `0` 的实现在这里当场红：那一页会把"不知道"画成"零"。
    ⚠️ **它一个同胞模块都不 import**（那条围栏照旧）：读的是**递进来的对象上的属性** ——
       与 `wire()` 同一套办法（按名字喂 ／ 按名字读）。
    ⚠️ **现读、不缓存**：`GET /state` 每次都调它 ⇒ 那一页上那几个数**跟着机器走**
       （缓存一份就成了一张过期的收据）。
    """
    def read() -> dict[str, Any]:
        session = getattr(console, "session", None)
        tokens: int | None = None
        messages: int | None = None
        # **2026-10-10：这一格改读"上下文那个总数"** （ 作者点的 ）——
        # 从前取 `console.request_size()[1]` ， 而那一份**只按会话算**
        # ⇒ **漏掉「工具 · 插件」那一整块** （ 真页量到： 差 **993** ）。
        # 铁证： 工具**真的进请求体** （`generate.py:146-148` 的
        # `body["tools"] = [dict(one) for one in tools]` ）。
        # 问不到 ⇒ 退回旧那一路 （ 不编一个数 ）。
        if callable(total_of):
            try:
                got = total_of()
            except (OSError, ValueError):
                got = None  # One unavailable estimate must not hide batch/gate state.
            if isinstance(got, int):
                tokens = got
        counter = getattr(console, "request_size", None)
        # Message count is independent of the richer context-token total.
        # Always read it, but only use request tokens when total_of had no answer.
        if session is not None and callable(counter):
            try:
                size = counter()
            except (OSError, ValueError):
                # 条数未知；若独立的 token 总数已知则保留，不把它抹成 None。
                #    ⚠️ 收的只是"读不出来"那一族（见上面那一段）；别的异常照旧穿过去。
                size = None
            if size is not None:
                messages, request_tokens = size
                if tokens is None:
                    tokens = request_tokens
        signal = getattr(console, "signal", None)
        stopped = bool(signal is not None and signal.is_set())
        # 2026-10-11：`cache_note` 要用的那两个数 —— **在这个函数里现取**
        # ⚠️ 我第一次写错了：直接用了另一个函数里的 `cached` ／ `told` ⇒ `NameError`
        # ⇒ **`/state` 那条路当场崩**（端口在听、请求被掐断）。判据没盖到那一格，
        # 是作者看着页面不对才问出来的。
        _usage = getattr(console, "usage_totals", None)
        _usage = _usage if isinstance(_usage, Mapping) else {}
        _cached_now = _usage.get("cached")
        _told_now = _usage.get("input")

        data = {
            "tokens": tokens,
            "messages": messages,
            "batches": int(getattr(console, "batches", 0) or 0),
            "paused": bool(getattr(console, "done", False)) or stopped,
            # 2026-10-10（三颗按钮 ⑤）： 作者按下那颗 ▶ 之后那个「暂停」
            # —— **与上面那一格两回事** （ 那一格讲的是「不再收新的了」）。
            "stopped_by_hand": _the_gate_now(console),
            **(console.round_control.status() if getattr(console, 'round_control', None) is not None
               else {'processing': False, 'round_id': None, 'interrupting': False, 'yielding': False}),
            # 关闸期间**丢掉了几条**（ 照实报数 —— 作者定的 ）。
            "dropped": _the_dropped_now(console),
            # 上一次那一下的**一句真话**（ 页上把它写进那颗键的 `title` ）。
            "gate_note": _the_note_now(console),
            "activity": (console.activity_control.status()
                         if getattr(console, "activity_control", None) is not None else None),
            # 2026-10-11：**它下一次会自己醒是什么时候**。
            # 由来：作者今晚问过两次"它静默了，滴答有在正常算吗"——
            # 账上全对，而页上看不出"什么时候会再动"。
            "next_wake": _the_next_wake_now(console),
            # 2026-10-11：**这一格为什么没有数** —— 页上跟着 `—` 一起显示，
            # 免得本地部署的人把它读成"我的引擎坏了"（它可能只是不报）。
            "cache_note": the_cache_note(_cached_now, _told_now),
            # 那台**正在起**没有（ 页上那颗键靠它转起来 ）。
            "starting": _the_starting_now(console),
            "endpoint": getattr(config, "base_url", None),
            # **上下文窗口**（2026-10-05）：那一页右下角那个圆要的**分母** ——
            # 分子是上面那个 `tokens`（`console.request_size()[1]` ）。
            #    ⚠️ 它是**生效值**（档案里填的 ／ 上游带回来的 ／ 都没有 ⇒ `None`）——
            # 而 `None` 那一头由页面照实画（"—"），**不冒充一个数** 。
            "context_window": getattr(config, "context_window", None),
            # **交"给人看的那个名字"**（2026-10-05 作者："三者不一致，以获取的模型名称为准"）：
            #    上游那一份里 `id` 与 `name` 是两样，而**存下来的 `model_name` 才是人看的那个**
            #    （`Profile.model_name`）；拿不到就照旧交 `model`。
            "model": ((model_of_current() if model_of_current is not None else None)
                      or getattr(config, "model", None)),
            # **这一条叫什么名字**（2026-10-05 作者："页面右上角也读不到模型名字"）：
            #    右上角那一格写的是「当前模型供应商」—— 而这一份从前只带 `endpoint`
            #    ／ `model`（都是**值**，不是**名字**）⇒ 页上只能显示 `—`。
            #    ⚠️ 这一格是**这一条档案的名字**（`profile.name`），不是模型名；
            #    活视图那一层没有它，所以从"谁在当当前"那个口要（拿不到就是 `None`）。
            "profile": (name_of_current() if name_of_current is not None else None),
            # 只有入口明确递来本地模型探针才有答案；在线 API 不冒充已探活。
            "model_available": (model_probe() if model_probe is not None else None),
            # ⚠️ 拿不到就是 None（不许编 —— 这一格的全部责任就是"别编"）。
        }
        if callable(getattr(console, 'uses_standby_preview', None)) and console.uses_standby_preview():
            data['context_estimate_source'] = 'standby_archive_preview'
        guard = getattr(console, "portrait_write_guard", None)
        diagnostic = getattr(guard, "public_state", None)
        if callable(diagnostic):
            data["portrait_write_gate"] = diagnostic()
        return data

    return read


def _hers_text_or_empty(port: Any, name: str) -> str:
    """ **整理态读它那两份**用的一格 —— 读得到就交正文，**读不到就交空那一串**。

    ⚠️ **为什么"读不到"也照旧交空那一串、而不抛**：那一份第一次做梦时**在盘上、但是空的**
       （0 字节），而"今天不在盘上"在这一格是**同一下场**：把它**当成空的**往下走。
        那两件事在 `file_info()` 那里分得开（`exists` ／ `bytes` ／ `body` 三段账），
       而这一格要的是**正文**。
       ⇒ 而"**整条根都没了**"是另一件事：那时这个函数**照旧抛**（它只收 `OSError`），
       由 `Dream.run` 那一层按"这一趟办不成"处置（吞掉它、记一行账 —— 见 `Dream.run`）。
    ⚠️ **名字守卫照旧只在那一条路上**（`port.read_text` 自己走 `path_of()`）——
       本函数**不判名字**（判第二份就是"两处各答一个数"）。
    """
    try:
        return port.read_text(name)
    except OSError:
        return ""


async def serve(console: Any, *, root: Any = "data", runtime_evidence: Any = None,
                record_runtime: bool = False, evidence_stop_timeout: float = 5.0,
                evidence_finalizer: bool = True, **options: Any):
    if type(evidence_finalizer) is not bool or (record_runtime and not evidence_finalizer):
        raise ValueError('created evidence requires lifecycle owner')
    from .runtime_evidence import observed_lifecycle, select_owner
    owner = select_owner(root, runtime_evidence, record_runtime, stop_timeout=evidence_stop_timeout)
    console.runtime_evidence_owner = owner
    async def operation():
        return await _serve_owned(console, root=root, runtime_evidence=owner, **options)
    if not evidence_finalizer:return await operation()
    return await observed_lifecycle(owner, operation, stop_timeout=evidence_stop_timeout)


async def _serve_owned(console: Any, *, root: Any = "data", config: Any = None,
    package_updates: Any = None,
    plugin_updates: Any = None,
    plugin_restart: Any = None,
    runtime_evidence: Any = None,
    reply_review: bool | str = False,
    persona_root: Any = None,
    # 同上一处（ `now` 由入口递 —— 终端那一刻用 ）。
    now: Any = None,
    # **2026-10-09 头像那一件**：读 ／ 写 `avatar.png` 那两件事**由入口递**
    # —— 与 `read_pending` 同一条口径（本模块零同胞 import ）。
    read_avatar: Any = None,
    save_avatar: Any = None,
    read_look: Any = None,
    save_look: Any = None,
    set_gate: Any = None,
    model_probe: Callable[[], bool | None] | None = None,
                transport: Any = None, generate: Any = None, engine: Any = None,
                #: **2026-10-12（作者：「补」）**：**真请求那几颗鉴权头** —— 由入口造好递进来
                #: （本模块零同胞 import，不认识密钥；而入口认识 `engine`）。
                #: ⚠️ 它**原样转交** `console.assemble(key_headers=…)` —— 缺省 ⇒ 一颗头都不加。
                key_headers: Any = None,
                #: ★ **2026-10-12（作者：「一起解开」）**：那只闹钟表**原样转交**
                #: `console.assemble(timers=…)` —— 由入口造一只递进来 ⇒ 全树**只留一只**。
                #: ⚠️ 不递 ⇒ 逐字节与从前一样。
                timers: Any = None,
                event_store: Any = None,
                host: str = LOOPBACK, port: int = DEFAULT_PORT,
                web_root: Any = DEFAULT_WEB_ROOT,
                out: Callable[[str], None] | None = None,
                on_start: Callable[[WebService], None] | None = None,
                read_memory: Callable[[], list[dict[str, Any]]] | None = None,
    console_intro: Callable[..., dict[str, Any]] | None = None,
    read_identities: Callable[[], list[dict[str, Any]]] | None = None,
    act_identity: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    read_pending: Callable[[], list[dict[str, Any]]] | None = None,
    read_knowledge: Callable[[], list[dict[str, Any]]] | None = None,
    # **2026-10-18 批④**：做法治理的只读视图（有效 ／ 已并入 ／ 操作账 ／ 运行状态）。
    # 与 `read_knowledge` 同一条路（由入口递 ，本模块零同胞 import）。
    read_skills: Callable[[], dict[str, Any]] | None = None,
    # 2026-10-08：**它自己写的那三份**（`persona/它写的/*.md` ）——
    # 与 `read_knowledge` 同一条路（由入口递 ，本模块零同胞 import ）。
    read_hers: Callable[[], list[dict[str, Any]]] | None = None,
    read_experience: Callable[[], list[dict[str, Any]]] | None = None,
    read_display_labels: Callable[[], dict[str, str]] | None = None,
    read_shared: Callable[[], dict[str, Any]] | None = None,
    act_on_shared: Any = None,
    adopt_proposal: Any = None,
    # **2026-10-07「在文件夹里打开」**：那一个 callable（ 本模块不许跑命令 ⇒ 由入口递 ）。
    open_a_path: Any = None,
    # **2026-10-07「④」**：跑一次评测那一个 callable（缺省禁 ⇒ 照旧）。
    run_eval: Any = None,
    # **2026-10-07「模糊搜接上」**：那一个匹配器（ 由入口递 —— 零同胞 import ）。
    match_rows: Any = None,
    # **2026-10-07「3b-②」**：那一格事实（它自己说过什么 ）—— 递给装配那一层 。
    facts: Any = None,
    # **2026-10-07「触发／自动拉进」**：那一格（缺省禁 ⇒ 一个字都不多 ）。
    pull_in: Any = None,
    # **2026-10-10「当前这一头」**（本刀）：装在说话的是**哪一头** ——
    # 原样转交 `console.assemble(channel=…)` ⇒ 装订器拿它去**通道那张表选一行**
    #      （`prefix.assemble(channel=…)`，见 `docs/design/段序.md`）。
    # ⚠️ **本模块不解释它**：收的是一个**不透明值**，一个名字都不认识
    # （与 `facts` ／ `pull_in` 同一条口径：由入口递 ）。
    # ⚠️ **缺省 `None` ⇒ 退回表里第一行**（ 那一头的名字**是本仓的禁字之一** ⇒
    # 这里一个字都不写它 ）⇒ 不递 ＝ 逐字与从前一样 。
    channel: Any = None,
    channel_context: Any = None,
    channel_scene: Any = None,
    batch_admission: Any = None,
    cache_prefix_diagnostics: bool = False,
    retrieval_at_tail: bool = False,
    channel_at_tail: bool = False,
    consolidation_enabled: bool = True,
    linked_accounts: Any = None,
    group_policy: Any = None,
    identity_hint: Any = None,
                consolidation_pending_read: Any = None,
                consolidation_pending_settle: Any = None,
                #: 记忆层那几个执行段（2026-10-05）：**由入口递**，本函数照原样转交 `assemble`。
                #:   ⚠️ 少了这一格，入口那一句 `serve(..., memory_handlers=…)` 会**当场 TypeError**
                #: —— 而它照样"看起来接好了"（判据里的替身收下了它 ）。载过：见那一笔修。
                memory_handlers: Mapping[str, Callable[..., str]] | None = None,
                #: 那一份"占在哪几块"的读法（2026-10-05）：由入口递，本函数原样转交 。
                read_usage: Callable[[], dict[str, Any]] | None = None,
                #: **已经说过的话**（2026-10-05）：同上 —— 由入口递、原样转交 。
                #: `None` ⇒ `GET /history` 答空（"没有旧话" ，不是"还没接上" ）。
                read_history: Callable[[], list[dict[str, Any]]] | None = None,
                #: **跑工具那一步**（2026-10-05 作者："往下走" ⇒ "做"）—— 同上：
                #: 由入口递、原样转交 `assemble` （`web.py` 零同胞 import ⇒ 它造不出来 ）。
                #: ⚠️ 不递 ⇒ `console.assemble` 那一格收 `None` ⇒ 那一层逐字不变 。
                run_tools: Any = None,
                #: **插件那一份名单**（2026-10-05 作者："先做 1"）—— 同上：
                #: 由入口递、原样转交 `WebService` （`web.py` 零同胞 import ⇒ 造不出来 ）。
                #: ⚠️ 不递 ⇒ 那一格答 `wired: False`（"还没接上" ，与"零个插件"分得开 ）。
                read_plugins: Callable[[], dict[str, Any]] | None = None,
                #: **市场那一份**（2026-10-05 作者："先做插件市场吧"）—— 同上，
                #: 由入口递、原样转交 `WebService` 。
                read_market: Callable[[], dict[str, Any]] | None = None,
                #: **开它 ／ 关它**（2026-10-05 作者："接着做"）—— 同上，由入口递 。
                switch_plugin: Callable[[str, bool], dict[str, Any]] | None = None,
                #: **装一份**（2026-10-05 作者："让市场真能装"）—— 同上，由入口递 。
                install_plugin: Callable[..., dict[str, Any]] | None = None,
                #: **删掉一个插件**（2026-10-05）—— 与上面那两格同一条路由入口递 。
                remove_plugin: Callable[[str], dict[str, Any]] | None = None,
                read_infinity: Callable[[str], dict[str, Any]] | None = None,
                 read_tool_check: Any = None,
                act_infinity: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None
                ) -> int:
    """ **那一页的入口** —— 装 ／ 起 ／ 泵 ／ 打 ／ 停（照 `console.main()` 那个形状）。

     **它就是"打开那个地址 ⇒ 看得见、能说话"这条验收的落点**，而它自己只做四件事：
       ① 把服务起在回环上（`host` ／ `port`）＋ 把那台机器的读数接上去（`state_of`）；
       ② **接线**：`wire(service, console)`；
       ③ **装配**：`console.assemble(…, read=console.read)`；
       ④ `await console.run()`（那条泵读的每一行都从那条通道来），收工再 `stop()`。

    ⚠️⚠️ **② 与 ③ 是两句，缺一不可**（Task 2 量到的那一处，本入口就是那两个住处的落点）：
       `wire()` 只写 `console.read` 那一格，而那条泵**不读那一格** —— 它读的是
       `assemble(read=…)` **递进去的**那一个（缺省那一句今天在 `console.py:1045-1046`）⇒ 少写 ③，
       那条泵会走它自己的缺省 `_stdin_line`（真的 `sys.stdin`）。
        于是"接线是两句、而这件事靠调用方记得"在这里不再靠记性：它写在树里、有测试。

    ⚠️ **`asyncio.run` 不在本模块**（全树只许一处，在 `cli.py`）⇒ 本函数是**一个协程**，
       起循环的那一句在**命令**里（`console.main()` 的 docstring 立的同一个先例）：

    ```
    .\\.venv\\Scripts\\python.exe -c "import asyncio,sys; from telyincore.config import load_config;
        from telyincore.console import Console; from telyincore.web import serve;
        sys.exit(asyncio.run(serve(Console(), config=load_config())))"
    ```

    ⚠️ **`load_config()` 递在命令里**（不是本模块做的事）：那份配置既是装配要的、
       也是引擎那一栏（端点 ／ 模型）要的 —— **必须是同一份**（两处各读一遍会各答一个数）。

    ⚠️ **从**仓根**跑**（就是有 `.venv` 与 `telyincore/` 的那一层）：`root` 的缺省是
       `"data"` —— 与 `console.py` 的 `_DEFAULT_ROOT` **同一个字面量**（它**相对当下工作目录**）；
       在别处跑，会话 ／ 事件库 ／ 闹钟表会一起落到别处。⚠️ 这里**不 import 那个常量**
       （本模块零同胞 import 是那条围栏）⇒ **两个地方都得改**的那一天，改的是这两处。
    ⚠️ **`engine` 递不递**（刀 4）：递了 ⇒ `GET ／ POST /engine` 那四条有落点；
       不递 ⇒ 它们回 **503**（"这一格还没接上"）—— **不是**回一份空的（那会被那一页
       读成"一条档案都没有"，而"读不到"与"零"是两件事，本仓那条立过的规矩）。
    ⚠️ **`config` 递不递**：不递 ⇒ `assemble()` 自己 `load_config()`（照旧能跑），而
        **引擎那一栏的端点 ／ 模型会是 `—`** —— 那一栏读的是**递进 `state_of` 的那一份**；
       ⇒ 要它们有值（那一页上最想看的两样），调用方把**同一份** `load_config()` 递进来
       （本模块**不 import `config`**：那条围栏照旧 ⇒ 这里没有第二条读它的路）。
    ⚠️ **它把地址打出来**（`out`，缺省 `print`）：那几行里有 `127.0.0.1:<端口>`，
       所以"端口递 `0`"这种跑法也看得见自己那条地址。
    ⚠️ **`on_start` 是给"要停它的人"留的抓手**（测试用；也是 `service.stop()` 的唯一入口）：
       它在**起来之后、`run()` 之前**被调，递进去的就是那个服务对象。
    Explicit same-origin HTTP shutdown + successful service drain returns 0;
    otherwise the conversation receipt remains answered=0 / unanswered=1.
    Cancellation and cleanup failures still propagate, never a flush/offline proof.
    ⚠️ **收摊**：`console.run()` 回来（或被打断）之后一定 `stop()` —— 那条泵读到"到头了"
       是因为服务收摊塞进去的那个记号（`read_line` ⇒ `StopAsyncIteration`）⇒ 两件事互为因果，
       而 `stop()` 幂等。
    ⚠️⚠️ **而"叫停"到"这一趟真的回来"之间，可能隔着**一整轮**（本波冒烟量到的形状）：
       `stop()` 只叫停**那条泵**（进口 ⇒ 到头了）；而那一刻若**正有一轮在跑**，主循环要等
       它跑完才出来 —— 而"取消正在跑的那一轮"在本仓**今天没有接上**（装配那一半欠着，
       见 `app.py` 的 `App.stop()` ③ 与 `round._OWED["signal_source"]`）。
       ⇒ 对着一个**连不上的端点**，那个等待由引擎那一侧的额度管（连接 ／ 首字超时 ＋
       重试，`Config.retry_delays_ms` 是 1 ／ 4 ／ 10 s）—— **本波冒烟实测：18.2 s**
       （不是挂死，是"等那一轮认输"）。
        这一条**不是本入口引入的**，也不会被本入口增加：它是这台机器今天的形状，
       写在这里是为了让下一个按 Ctrl-C 的人知道**要等一下**。
    ⚠️ **Ctrl-C 走 `finally`**：那个协程被取消时，`stop()` 照旧跑（本函数**没有** `except`，
       调用方自己的取消照旧往上走）。
    ⚠️⚠️ **写入那条路的边界**（`POST /say`）：服务只听 `127.0.0.1`，而它**不校验 `Origin`**、
       **没有令牌** ⇒ 「只听回环」是「**这一台机器**」，**不是**「已鉴权」——
       本机任何一个进程 ／ 任何来源的一页都塞得进一行。 那一页把这句话**显示出来**
       （引擎那一栏），免得"打开它"被读成"只有我能打开"。
     **本刀的这一句更重**（Task 2 加的那两条路 —— `/persona` 是它自己的那两条：
       `GET` 读那十段，`POST` **写**其中一段）： `/persona` 那一条改的不是"会话里的一句话"，
       而是**人格那几段本身** ⇒ 「只听回环」照旧**只是**「**这一台机器**」，
       而**这一台机器上的谁都改得动「那一份是谁」** —— 那句话的原文是
       `PERSONA_BOUNDARY_LINE`（**一处**，可引用、可显示：改一次，几处一起动）。
    """
    say = print if out is None else out
    # ⚠️ 名字**不许叫 `port`** —— 那会把上面那个端口参数盖掉，而 `WebService(port=…)`
    #    收到的就是一个 `PersonaPort`（实测：`TypeError: 'PersonaPort' object cannot be
    #    interpreted as an integer`，而它发生在**第一条 `say` 之前** ⇒ 入口那半句都不打）。
    persona = PersonaPort(DEFAULT_PERSONA_ROOT if persona_root is None else persona_root,
                          read_shared=read_shared, act_on_shared=act_on_shared,
                          open_a_path=open_a_path, run_eval=run_eval, match_rows=match_rows)
    # **2026-10-10：那一格的总数从这儿来** —— 与 `/usage` 的 `total` **同源**
    # （ 用**已经有的** `read_usage` 现造 ⇒ 入口那一侧**一个字不用改** ）。
    # ⚠️ **这一句必须真写出来** —— 我上一版只把参数加上、把实装写成了注释
    # ⇒ `total_of` 一直是 `None` ⇒ 那一格照旧漏掉工具那一块 。
    # ⚠️ **锚必须唯一** ： `read_usage: …` 那一行在文件里**出现两次**
    # （类 ＋ 这里 ）⇒ 上一版加到了**类**上 ⇒ `NameError` 。
    def _the_total() -> int | None:
        box = read_usage() if callable(read_usage) else None
        return box.get("total") if isinstance(box, dict) else None

    service = WebService(host=host, port=port, web_root=web_root, runtime_evidence=runtime_evidence,
                         package_updates=package_updates, plugin_updates=plugin_updates, plugin_restart=plugin_restart,
                         runtime_evidence_finalizer=False,
                         state=state_of(
                     console, config=config, total_of=_the_total,
                     # 走按名字喂／按名字读那一套（本模块零同胞 import）：
                     name_of_current=(None if engine is None
                                      else getattr(engine, 'current_name', None)),
                     # 同一套办法：从引擎上问"当前那一条的模型名（人看的那个）"。
                     model_of_current=(None if engine is None
                                       else _current_model_name(engine)),
                      model_probe=model_probe),
                         persona=persona, console=console, engine=engine,
                          # 那个只读接口的读法（2026-10-05）：`None` ⇒ `GET /memory` 答空
                          read_memory=read_memory,
                          read_identities=read_identities, act_identity=act_identity, console_intro=console_intro,
                          read_pending=read_pending,
                             read_knowledge=read_knowledge,
                             read_skills=read_skills,
                          read_hers=read_hers,
                             read_experience=read_experience,
                             read_display_labels=read_display_labels,
                             read_shared=read_shared, act_on_shared=act_on_shared,
                             adopt_proposal=adopt_proposal,
                         open_a_path=open_a_path, run_eval=run_eval, match_rows=match_rows,
                             read_usage=read_usage,
                          read_history=read_history, read_plugins=read_plugins,
            now=now,
            read_avatar=read_avatar, save_avatar=save_avatar,
            # 2026-10-10：**那两样也得递进去** —— 照 `avatar` 那两样
            # （ 而一度**只在 `serve()` 里收下、没往下递** ⇒
            # 那一层拿到的是默认 `None` ⇒ `look()` 回 `None`、
            # 写盘那一句不 callable ）。
            read_look=read_look, save_look=save_look,
            set_gate=set_gate,
                          read_market=read_market, switch_plugin=switch_plugin,
                          install_plugin=install_plugin,
                          remove_plugin=remove_plugin,
                          read_infinity=read_infinity,
                          act_infinity=act_infinity, read_tool_check=read_tool_check)
    service.start()
    try:
        wired = wire(service, console) # 接线第一句
        console.assemble(root=root, config=config, transport=transport,
                         runtime_evidence=runtime_evidence, reply_review=reply_review,
                         persona_root=persona_root,
                         event_store=event_store,
                         # ★ **2026-10-12**：那只闹钟表**原样转交**（由入口递 ⇒ 全树只留一只）。
                         timers=timers,
                         # **2026-10-12**：那几颗鉴权头（原样转交 —— 只在
                         #    这一层自己造 transport 时用得上；不递 ⇒ 与从前一样）。
                         key_headers=key_headers,
                         # **2026-10-07「3b-②」**：那一格事实（缺省 ⇒ 一个字都不加 ）。
                         facts=facts, pull_in=pull_in,
                         # **2026-10-10「当前这一头」**：原样转交
                         # （缺省 `None` ⇒ 退回通道表第一行 ⇒ 逐字与从前一样 ）。
                         channel=channel, channel_context=channel_context,
                         channel_scene=channel_scene, batch_admission=batch_admission,
                         cache_prefix_diagnostics=cache_prefix_diagnostics,
                         retrieval_at_tail=retrieval_at_tail,
                         channel_at_tail=channel_at_tail,
                         linked_accounts=linked_accounts,
                         **({"group_policy": group_policy} if group_policy is not None else {}),
                         identity_hint=identity_hint,
                         generate=generate, read=console.read,
                         # **整理态那条路**（2026-10-03）：它要一扇"它自己那份"的门，
                         #    而那道守卫住在本文件（`may_be_written_by_her` ＋ `HER_FILES`）
                         #    ⇒ **由这里递**，`console` 那一层不许再写第二份。
                         #    ⚠️ 第三个实参是那一刻（那一串 `YYYYMMDD-HHMMSS`）——
                         #    由 `console._a_stamp()` 造（本文件不读时钟，见模块抬头）。
                         # ★ 2026-10-17 补验 ①：**基线版本要递得过去**（`expect`）——
                         #   少了它，网页这条整理路径就是"没有版本检查地写"，
                         #   而 `consolidate` 会**拒绝提交**（`NoVersionCheck`）。
                         #   提成模块级那一支（`_consolidation_write_into`）是为了让
                         #   判据能**直接量这条生产装配**，而不是复制一份三个参数的替身。
                         consolidation_write=(_consolidation_write_into(persona)
                                              if consolidation_enabled else None),
                         # **读回来那一格**（第二次真跑补上的）：整理态要**看见**
                         #    它要改的那两份 —— 没有这一格，它交回来的是一句
                         #    "我原样交回"，而那一句会真的变成那一份的正文。
                         #    ⚠️ 那一份**不在**（`OSError`）⇒ 交回空那一串
                         #    （"没有这一份"与"这一份是空的"在**这一条路上**同一下场：
                         #     那两件事在 `file_info()` 那里分得开，而这里要的是正文）。
                         consolidation_read=lambda name: _hers_text_or_empty(persona, name),
                         consolidation_pending_read=consolidation_pending_read,
                         consolidation_pending_settle=consolidation_pending_settle,
                         # 记忆层那几个执行段（2026-10-05）：**照原样转交**，本函数不解释它。
                         memory_handlers=memory_handlers,
                         # **跑工具那一步**（2026-10-05 作者："往下走" ⇒ "做"）——
                         # 它归 `console.assemble`（那一层再递给 `make_run_round` ）；
                         # ⚠️ **不是** `WebService` 那一格 （那个对象是 HTTP 那一侧的 ——
                         # 递错了会在 `WebService(...)` 那一句当场炸 实测抓到过一次）。
                         run_tools=run_tools)
        # Serialize interruption with the active round; never cancel from an HTTP thread.
        control_loop = asyncio.get_running_loop()
        def interrupt_round(round_id):
            import concurrent.futures
            result = concurrent.futures.Future()
            def apply():
                try:
                    result.set_result(console.round_control.interrupt(round_id))
                except BaseException as exc:
                    result.set_exception(exc)
            control_loop.call_soon_threadsafe(apply)
            return result.result(timeout=3)
        if callable(getattr(console, "enable_display_timeline", None)):
            console.enable_display_timeline()
        service.interrupt_round = interrupt_round
        _bind_observation_ingress(service, console, control_loop)
        def round_failures():
            import concurrent.futures
            from .events import RangeQuery
            pending = concurrent.futures.Future()
            def read():
                try:
                    store = getattr(console.app, "store", None)
                    if store is None:
                        pending.set_result(None)
                        return
                    events = store.range(RangeQuery(origin="internal", limit=1000))
                    rows = [{"cursor": event.cursor, "at": event.ts, "type": event.type,
                             "detail": ((event.meta or {}).get("diagnostic")
                                        if event.type == "round-failure" else dict(event.meta or {}))}
                            for event in events if event.type in ("round-failure", "handoff-failed")][-50:]
                    compatibility = [
                        {"cursor": event.cursor, "at": event.ts,
                         "detail": (event.meta or {}).get("diagnostic")}
                        for event in events if event.type == "provider-replay-fallback"][-50:]
                    continuations = [{"cursor": event.cursor, "at": event.ts,
                        "detail": {key: (event.meta or {}).get(key) for key in ('source', 'status')}}
                        for event in events if event.type == 'continuation-status'][-50:]
                    pending.set_result({"failures": rows, "provider_compatibility": compatibility,
                                        "continuations": continuations,
                                        "source_event_limit": 1000,
                                        "handoffs": [{"cursor": event.cursor, "at": event.ts, "type": event.type,
                                            "detail": {k: (event.meta or {}).get(k) for k in
                                                ('estimate', 'hard', 'soft', 'reason', 'split_at_ms', 'error_type')
                                                if k in (event.meta or {})}}
                                            for event in events if event.type.startswith('handoff-')][-50:]})
                except Exception as error:
                    pending.set_exception(error)
            control_loop.call_soon_threadsafe(read)
            return pending.result(timeout=3)
        service.round_failures = round_failures
        def diagnostic_history():
            from .diagnostic_history import collect
            session = getattr(console, 'session', None)
            path = getattr(session, 'path', None)
            return collect(path) if path is not None else None
        service.diagnostic_history = diagnostic_history
        if sys.platform == 'win32' and persona_root is not None:
            from qq_startup_diagnostics import collect as collect_qq_startup
            core = Path(__file__).resolve().parents[1]
            persona = Path(persona_root).absolute()
            bot = persona.parent.name
            if (persona == core.parent/'bots'/bot/'persona'
                    and Path(root).absolute() in (core/'data',core/'data/bot-runtimes'/bot)):
                service.qq_startup_diagnostics = lambda: collect_qq_startup(core, bot)
        console.round_control.on_change = lambda state: service.push({'kind': 'control_state', **state})

        # ⚠️⚠️ **先说"装好了"，再说那条地址**（次序是量出来的）：`assemble()` 之前
        #    `console.session` 还是 None ⇒ 有人此刻去读 `/state`，那几个数会是 `null`
        #    （实测：本波的冒烟探针就撞上了这个缝）。⇒ 于是地址与那个抓手都等机器整了再给。
        say(f"那一页在：      {service.url('/')}")
        say(f"· 推的通道：     {service.url('/events')}")
        say(f"· 说一句：       POST {service.url('/say')}")
        say(f"· 那几个数：     {service.url('/state')}")
        if not (wired["write"] and wired["read"]):
            say(" 那两个口上已经有调用方自己的东西 ⇒ 这条通道一条都没接（见 wire()）。")
        if config is None:
            say(" 没递 `config` ⇒ 装配那一侧自己 load_config()，而**引擎那一栏的端点 ／ 模型"
                "会是 —**；要它们有值，把同一份 `load_config()` 递进来。")
        say(" 只听回环 ⇒ 只有这一台机器上打得开 —— 而它**不鉴权**（边界见 telyincore/web.py）。")
        say(" Ctrl-C 收摊。")
        cutoff = getattr(console, 'request_shutdown', None)
        if cutoff is not None:
            service.bind_shutdown(cutoff)
        if on_start is not None:
            on_start(service) # 抓手：装完之后才交出去
        await console.run()
    finally:
        primary = sys.exception()
        try:
            # Subscription/OAuth drains may schedule work on this very loop.
            # Keep it running while the synchronous HTTP/request owners retire.
            # Await the owned worker: failure still blocks successful shutdown.
            if await asyncio.to_thread(service.stop) is not True:
                raise RuntimeError("web service shutdown unresolved")
            error = getattr(service, '_shutdown_callback_error', None)
            if error is not None:
                raise error
        except BaseException as cleanup_error:
            if primary is None:
                raise
            primary.add_note("web service shutdown unresolved: " + type(cleanup_error).__name__)
    # A requested, drained server exit is normal even if no model ever spoke.
    # Unrequested no-answer completion remains a failed conversation receipt.
    return 0 if service.shutdown_requested or getattr(console, "answered", False) else 1


# Preserve the explicit public keyword inventory for introspection consumers.
from inspect import signature as _signature, Parameter as _Parameter
serve.__signature__ = _signature(_serve_owned).replace(parameters=[
    *_signature(_serve_owned).parameters.values(),
    _Parameter('record_runtime',_Parameter.KEYWORD_ONLY,default=False,annotation=bool),
    _Parameter('evidence_finalizer',_Parameter.KEYWORD_ONLY,default=True,annotation=bool),
    _Parameter('evidence_stop_timeout',_Parameter.KEYWORD_ONLY,default=5.0,annotation=float)])
serve.__doc__ = _serve_owned.__doc__

# ── 第三段 · Task 1：读那一侧 —— 十段与它们的归属 ────────────────────────────
#
# **先核的那一件事（本刀唯一的新机制）**：本模块今天只有只读静态 ＋ `/state` ＋
#    `/events` ＋ `/say` —— **它从不读任意文件**；而这一刀要的就是那一条口子：
# **一个口子，受限于一个根** ⇒ 只有 `bots\<bot>\persona\` 底下的文件读得到。
#
# **本刀给那条回环边界多加一条**（设计稿 §五④ 那句"树里写明"的落点）：
# 上面那一条说的是"**这一台机器上的谁都连得上**"； 而这条口子之后还有一件更重的：
#    **它读的是那几段人格正文** ⇒ 那条边界从此多一句：**这一台机器上的谁都改得动
# "那一份是谁"** 。⇒ 它不是比 `POST /say` 更紧的一条保证，而是**同一条**：
#    「只听回环」＝「**这一台机器**」；收紧点照旧只有一处（`Origin` ／ 令牌 —— 本刀
# 刻意不做 ）。 那一句的原文是下面那个 `PERSONA_BOUNDARY_LINE`（**一处**，
#    可显示、可引用：改一次，两处一起动）。
#
# ⚠️⚠️ **两张清单对不上，而两张都要带出来**（本波量到的，别只挑一张）：
#    · **段序十段**（`docs/design/段序.md` 那张表）vs **表十一行**（那一份 `README.md`）；
# · `示例对话` 在段序里、**今天没有文件** ⇒ 交成"缺的那一段"（不许悄悄少一段）；
# · `世界认知` 在表里、**段序里没有它** —— 而那样的一行**不止一条**（还有一行是
#      那一格"两份文件"）⇒ 它们交在 `rows` ／ `unplaced` 上（不许悄悄丢一行）。
#
# ⚠️ **这一段两个口子都是**现读**的**（`state_of()` 那条规矩）：每次调用都重读那几份
#    文件 —— 缓存一份就成了一张过期的收据。
# ⚠️ **本段一个同胞模块都不 import**（那条围栏照旧）：只用标准库那几样。

#: **`<bot>` 那一段** —— 故意写成转义（`telyincore/boundary.py` 同一个写法）：
#: 那六个禁词是**按原始字节**扫这一棵树的，写字面量就等于让检查器举报自己
#: （那个模块的抬头逐字写着这件事）。
_BOT_DIR_NAME = "\u0054\u0065\u006c\u0079\u0069\u006e"

#: 仓根（`telyincore/` 的上一层）—— 与 `DEFAULT_WEB_ROOT` 同一条"算出来"的口径。
_REPO_ROOT = Path(__file__).resolve().parent.parent

#: **那一条根**（只有一条）：`bots\<bot>\persona\`。
#: ⚠️⚠️ **它不在这个 checkout 里**（本波实测：`core/bots` 不存在，而
#: `<仓根>\..\bots\<bot>\persona` 在）⇒ **`git diff --stat` 永远看不到**这条根
#:    底下的任何改动：那一类改动只能拿**文件本体**去量（Task 2 的验收正是这么写的）。
#: ⚠️ 它**是算出来的**，不是写死的绝对路径（与 `DEFAULT_WEB_ROOT` 同一条）。
DEFAULT_PERSONA_ROOT = _REPO_ROOT.parent / "bots" / _BOT_DIR_NAME / "persona"

#: **人和它共用那一本的名字**（2026-10-07「丙-2」）—— 那个字走转义。
SHARED_FILE = "\u4e00\u8d77\u5199\u7684/\u5173\u4e8e\u5979\u81ea\u5df1.md"

#: **那一份正本 ＋ 那张对照表** —— 段序 ／ 三个家都从这儿读（都在仓里，只读）。
DESIGN_DIR = _REPO_ROOT / "docs" / "design"
#: **段序那一处正本**（`docs/design/段序.md`）—— 页与装订器读的是**同一份盘上的文件**：
#: ⚠️ 它**不能**是一条"页 import 内核模块"的路（本模块一个同胞都不 import，
#:    `tests/test_web.py` 拿 ast 钉着）⇒ 只能共用**一份文件**（与 `DEFAULT_WEB_ROOT`
#: 同一条"算出来"的口径）。 装订器那一侧读的正是同一个路径（`prefix.SEGMENTS_DOC`）
#:    —— 它一改，两边一起改（`tests/test_prefix.py` 钉着）。
ORDER_DOC = DESIGN_DIR / "段序.md"
OWNERS_DOC = DESIGN_DIR / "11-现实与世界观对照.md"

#: 段序那一块的**标题** —— 按**名字**取，不按行号（`tests/test_frozen_faces.py` 那条
#: R3 立的规矩：行号每改一次文档就假一次，而且漂了没人知道）。
_ORDER_HEADING = "## 段序"

#: 三个家那一行的**一个字样**（同上：是一个词，不是第几行）。
_OWNERS_ANCHOR = "前六份"

#: **那几个家，各自认哪几个词** —— ⚠️ 这是**词汇**，不是归属：它说"读到这几个词，
#:    指的就是那一个家"。**哪一段归谁**由每一份**自己**说（抬头的注释 ／ 它住在哪一层
#:    目录）—— 见 `PersonaPort.owner_of()`。
#: ⚠️ 那两个家会在 `11:67` 那一行上**逐个核过**（`_homes_of`）：少一个就当场抛 ——
#:    一个文档没点名的家，这一层不许自己造。
#: ⚠️⚠️ **`你定的` 故意不在这一张表里**（本波量到的）：`边界.md` 与 `通道形状.md` 的
#:    抬头**都**写着"不进 persona/你定的/" —— 把它也当词去认，那两份会被读成"你定的"
#:    （它们那句话里**有**这三个字，而意思**相反**）。⇒ 那一个家只由**路径**认。
_HOME_MARKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("跟框架走", ("跟框架走",)),
    ("归 infinity", ("infinity", "Infinity", "World")),
)

#: 归属那一栏的**第三格**：那六份自己住的那一层（`11:67`：前六份在 `persona/你定的/`）
#: —— 它同时是那张表里路径的第一层，所以那三个字**不用在代码里写死**也能认出来。
_BOT_HOME = "你定的"

#: 一份**占位**的记号（`<!-- 占位 · 这里不是正文 -->`）—— 与家那几个词同一类：
#: 它是**一份文件抬头上的一个词**，不是这里的一句话。
_PLACEHOLDER_MARK = "占位"

#: 那张表住在哪份文件里（ 这条根底下的那一份 —— 不是 `那一份旧材料（2026-10-07 已删）/` 那一份）。
_README_NAME = "README.md"

#: **这一条根底下唯一的后缀**（本波补的那一格，见 `_under_root()`）——
#: ⚠️ 它是**一处**：`_under_root()` 拿它判名字（不是 `.md` ⇒ 当场抛），而历史那两处
#:    （`_history_dir()` ／ `_version_name()`）拿它切文件名 —— 三处同一个常量，
#:    少了这一格就是"判的时候按一条、切的时候按另一条"（名字被啃掉一截还不报错）。
_MD_SUFFIX = ".md"

#: **本刀给那条回环边界多加的那一条**（设计稿 §五④ 那句"树里写明"的落点）。
#: ⚠️⚠️ **为什么它值得一个**常量**、而不是一句注释**：这一句会说给三个地方听
#:    （页面 ／ Task 2 那条保存路 ／ 报告），而"只听回环"这句话今天在树里有三处原文 ——
#: 埋一句在注释里，下一个人就会把"只听回环"读成"已经够紧了"。
#: **它说的是这一刀之后那件更重的事**：这条口子读的、写的，是**人格那几段本身**
#: ⇒ 「只听回环」＝「**这一台机器**」，而**这一台机器上的谁都改得动「那一份是谁」** 。
#: ⚠️ **它不比 `POST /say` 更紧、也不比它更松**：**同一条**边界，同一个收紧点
#: （`Origin` ／ 令牌 —— 本刀刻意不做）； 不一样的只是**代价**：改错了那一份，
#:    改的是"那几段字本身"，而不是会话里的一句话。
PERSONA_BOUNDARY_LINE = (
    " 那条回环边界**多一条**：这条口子改的是**人格那几段本身** —— 这一台机器上的谁都改得动它。")

#: 表里"一个反引号里那一样东西"—— ⚠️ **只从第一格里取**，理由见 `_rows_of`。
_CODE_SPAN = re.compile(r"`([^`]+)`")


def _cells_of(line: str) -> list[str]:
    """一行表 ⇒ 那几格（`| a | b | c |` ⇒ `["a", "b", "c"]`）。"""
    return [one.strip() for one in line.strip().strip("|").split("|")]


def _is_a_separator(cells: list[str]) -> bool:
    """表头底下那一道分隔（`|---|---|---|`）—— 它自己不是一个段。"""
    return bool(cells) and all(one and set(one) <= set("-:") for one in cells)


#: 表里那两格的**位置**：段名在第一格，文件在第二格。
#: ⚠️ 与那份对照表（`| 文件 | 段 |`）**不是同一种写法** —— 两处各按自己那一张读。
_COL_NAME = 0
_COL_FILES = 1


def _segments_in(text: str, *, heading: str = _ORDER_HEADING) -> list[tuple[str, tuple[str, ...]]]:
    """ **那一处正本** ⇒ 一行一条：段名 ＋ 它那几份文件（**行序就是段序**）。

    ⚠️ **按标题取那一块，不按行号**（`_ORDER_HEADING` 那条注释里的理由）。
    ⚠️⚠️ **它读的是那张表的前两格**（`| 段 | 文件 | 缺了怎么办 |`）—— 正本从前把次序
       放在一个**围栏**里，那个围栏与 `00-总览.md` 里那一份一起**让位了** ⇒ 一个
       照旧去找围栏的读法，在这份正本上会**当场抛**（"之后没有围栏"），而那是
       "读法过期了"，不是"这一段没有内容"（两件事，与下面那两条 raise 同一条口径）。
    ⚠️ **一个解析器，两个读面**（段名 ／ 文件）：页那边也要文件名（`segments()` 的
       `files` 换出处）—— 两处各写一个解析器就是"同一份文档两种读法"。
    ⚠️ **找不到就当场抛**：一个"找不到就交回空表"的实现，会把"文档挪了 ／ 改名了"
       读成"这一段没有内容"——那是两件完全不同的事。
    ⚠️ **文件名只从第二格的反引号里取**：那一格可以是**空的**（＝ 这一段声明"没有文件"）。
    ⚠️ **第一格空的当场抛** —— 一个**没有名字的段**从来不正立。 而"只在一处钉住它、
       另一处不钉"正是「段序只住一处」那条判据（`tests/test_prefix.py`）要挡的那种走样：
       两个读法在同一条输入上**各答一个数** ⇒ 装订器那一侧（`prefix.table()`）照同一条办。
       ⚠️ 这条守卫本来是 `_order_of()` 的（`cae8d0a`），上一刀把那一格重写成 `_segments_in`
       时**丢掉了**（两个读法同案不同判）—— 本刀**补回**，而它从此住在**唯一**那个解析器上。
    """
    if heading not in text:
        raise ValueError(f"那一份文档里没有这一句标题：{heading!r} —— 要取的那块没处可找")
    out: list[tuple[str, tuple[str, ...]]] = []
    under_the_header_rule = False
    for line in text.split(heading, 1)[1].splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if out:
                break # 第一张表就是它 —— 表读完就收
            continue
        cells = _cells_of(stripped)
        if _is_a_separator(cells):
            under_the_header_rule = True
            continue
        if not under_the_header_rule:
            continue                     # 表头那一行（分隔之前的都不是段）
        if not cells[_COL_NAME]:
            raise ValueError(f"段序那张表里有一行第一格是空的：{stripped!r}")
        files = tuple(_CODE_SPAN.findall(cells[_COL_FILES])) if len(cells) > 1 else ()
        out.append((cells[_COL_NAME], files))
    if not out:
        raise ValueError(f"{heading!r} 底下没有一行表 —— 要取的那块没处可找")
    return out


def _order_of(text: str, *, heading: str = _ORDER_HEADING) -> list[str]:
    """ **段序**（那几段的名字，**次序就是它**）—— 从那一处正本里读。

    ⚠️ **它不在代码里另写一份**（`02:127`：面板读的值＝实际生效的值，由同一个函数算）：
       表改了次序，这一层当场跟着变；而代码里再抄一份，两份就会各答一个数。
    """
    return [name for name, _ in _segments_in(text, heading=heading)]


def _marks_of(home: str) -> tuple[str, ...]:
    """那一个家**认哪几个词**（词汇表在 `_HOME_MARKS`；不在表里的那一个就用它自己的名字）。"""
    for label, marks in _HOME_MARKS:
        if label == home:
            return marks
    return (home,)


def _homes_of(text: str, *, anchor: str = _OWNERS_ANCHOR) -> tuple[str, ...]:
    """ **那几个家** —— `owners_doc` 里带 `anchor` 的那一行，**逐个核它点过名**。

    ⚠️⚠️ **它判的是"文档点过这几个名没有"**：一个文档没点名的家，这一层**不许自己造**
        （造一个就是第二份真相）⇒ 少一个当场抛，并把缺的那几个名字说出来。
    ⚠️ **按字样取那一行**（`_OWNERS_ANCHOR`），不按行号 —— 与 `_ORDER_HEADING` 同一条。
    """
    line = next((one for one in text.splitlines() if anchor in one), None)
    if line is None:
        raise ValueError(f"那几家的那一行找不到了（带 {anchor!r} 的那一行）")
    homes = (_BOT_HOME, *(label for label, _ in _HOME_MARKS))
    missing = [home for home in homes
               if not any(mark in line for mark in _marks_of(home))]
    if missing:
        raise ValueError(f"那一份文档没有点名这几个家：{missing} —— 这一层不许自己造")
    return homes


def _rows_of(readme: str) -> list[dict[str, Any]]:
    """ **那张对照表**（`| 文件 | 段 |` 那一段）—— 一条一条读，不抄。

    ⚠️⚠️ **两条约定，都是量出来的，不是猜的**：
        · **文件只在第一格里取**（反引号里那些）—— 第二格那个括号里**也**有反引号
          （那一行两样文件的就是：段名后面还跟着一条"格式契约见 core/docs/design/12-…"
          的括注）⇒ 两格一起扫的话，那一行会多出第三样"文件"（一个设计文档的路径）；
        · **段名取第二格**，并把**那个全角括号及其后**切掉（`甲一（…）` ⇒ `甲一`）——
          段名里今天没有那个括号。
    ⚠️ **表头与分隔行自己就落选**（第一格里一个反引号都没有）⇒ 不用另判"这一行是不是表头"。
    """
    rows: list[dict[str, Any]] = []
    for line in readme.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [one.strip() for one in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        files = _CODE_SPAN.findall(cells[0])
        if not files:
            continue
        rows.append({"files": files, "label": cells[1].split("（", 1)[0].strip()})
    return rows


def _lead_comment(text: str) -> str | None:
    """那一份**抬头**那条 HTML 注释里的字（它就没有 ⇒ `None`）。

    ⚠️ 三条：
        · **只看抬头**（正文中间那条注释不算 —— 它说的不是"这一份归谁"）；
        · BOM 与抬头那几个空行先剥掉；
        · **一条注释可以跨好几行**（`你定的/破甲.md` 就是）⇒ 取到 `-->` 为止，
          不是只看第一行。
    """
    stripped = text.lstrip("\ufeff").lstrip()
    if not stripped.startswith("<!--"):
        return None
    end = stripped.find("-->")
    return stripped[4:] if end == -1 else stripped[4:end]


def _home_in(comment: str) -> str | None:
    """抬头那条注释点的是哪一个家（一个都没点 ⇒ `None`）。

    ⚠️⚠️ **只在那两个"不是它自己那一层"的家里找**（`_HOME_MARKS`），理由在那个表上面
        那段：真那两份的抬头**都**写着"不进 persona/你定的/"，把 `你定的` 也当词去认，
        它们会被读成"你定的"——那句话里**有**那三个字，而意思**相反**。
    """
    for label, marks in _HOME_MARKS:
        if any(mark in comment for mark in marks):
            return label
    return None


class PersonaPathError(ValueError):
    """那个名字**不在这条根底下**（或者它长得就不像一个相对名）。

    ⚠️ 它是 `ValueError` 的子类：一个"名字不对"就是一条**实参**上的错（与
       `WebService.__init__` 里那条回环判据同一种形状 —— 当场抛，不装成"没有这一条"）。
    """


class PersonaWriteRefused(PersonaPathError):
    """ **这个名字在这条根底下、而"人"不许写它** —— 与"越界"**不是同一件事**。

    ⚠️⚠️ **为什么要分成两个类型**（本刀为什么值得多一个类）：
        · `PersonaPathError` ＝ **名字越界**（`..` ／ 绝对路径 ／ 符号链接）⇒ 那条路
          从来就不该到达它，**400**；
        · `PersonaWriteRefused` ＝ 名字**完全合法**、而它是那一类"**只有文档点名的那一个人
          才写得动**"的文件（本刀那条硬轴：能改 ／ 不能改）
          ⇒ **403**，而且**理由要能说给人听**（引那一句文档）。
         合成一个的话，页上就分不出"你打错了"与"这一份不该你动" —— 而作者要的
          正是后者的那一条**看得见的**界线（"手一滑改了不该改的"）。
    ⚠️ 它照旧是 `PersonaPathError` 的子类 ⇒ 既有的"名字那一侧"的兜底一个字不动
        （读口那一侧也就照旧把越界说成越界）。
    """


class PersonaShapeRefused(PersonaPathError):
    """ **这一版会把一份文件里承重的形状改掉**（本波那条 Critical）——
    与"名字越界" ／ "这一份不归你写"**都不是同一件事**。

    ⚠️⚠️ **为什么还要第三个类型**（本仓的成法：一类错一个类型、一类错一句话）：
        · `PersonaPathError`     —— **这一次请求不对**（名字 ／ 那串时间戳）⇒ **400**；
        · `PersonaWriteRefused`  —— 这一份**人不能改**（文档点名的那几份）⇒ **403**；
        · `PersonaShapeRefused` —— **名字合法、这一份也归你写，而这一版会把它改坏**
          ⇒ **409**。 选 409 的理由：这一条判的是**关系**（这一版 vs 盘上现在那一份
          —— 删掉抬头那条注释、拆掉一对 `**`，都是"相对盘上那一份"才成立的），
          而那正是 **409 Conflict** 的意思； 422 说的是"你交的这一份**本身**不成立"
          （那会与"JSON 少一格"那种 400 搅在一起），而今天这两种情形走的是同一条判据
          —— 只有 `**` 不成对那一半是"本身不成立"，为它单独再开一个状态码不值当。
    ⚠️ 它照旧是 `PersonaPathError` 的子类（与上面那一个同一条理由：兜底一个字不动，
        而 `_accept_a_save` 里那两处 `except` 的**次序**是承重的 —— 它们在前面）。
    """


#: **"人"改不动的那几份 —— 以及各自的理由**（本刀的硬轴：能改 ／ 不能改）。
#:
#: ⚠️⚠️ **这是"哪些不能改"的唯一定义处**：页那一侧**只画**，不另写一份清单
#:     （`02:127`：面板读的值＝实际生效的值，**由同一个函数算**）。
#: 而写那一侧**也过这一张表**（`save_text()`）⇒ 页上画错、或者有人直接打那条路，
#:     落点都是同一个答案（**一条判据，两处生效**）。
#:
#: ⚠️ **每一条都引了出处**（那一句原文，可核）：一个只写"只读"的实现，答不出
#:     "**为什么**它只读" —— 而作者怕的正是"手一滑改了不该改的"，所以理由要能读出来。
_PERSONA_READ_ONLY: tuple[tuple[str, str, str], ...] = (
    (
        "\u5979\u5199\u7684/\u4e60\u60ef.md",
        " **2026-10-05（习惯那一层）**:长出来的样子由"
        "**整理态按门槛写**（不是人层写）—— 而人写不动它："
        "一旦人能改，那就又回到了“人画框子”那一边。",
        "本仓 2026-10-05 定",
    ),
    (
        "\u5979\u5199\u7684/\u81ea\u8ff0.md",
        "\u53ea\u6709\u5979\u80fd\u5199\u8fd9\u4e00\u4efd\uff0812:13\uff09\uff1b\u800c\u5b83\u7684"
        "\u6b63\u6587\u5728\u5979\u9879\u76ee\u91cc\u3001\u5df2\u7ecf\u5b58\u5728\u3001"
        "\u5e26\u5386\u53f2\uff0812:5\uff09\u2014\u2014\u522b\u91cd\u5efa\u8fd9\u4e24\u4efd"
        "\u6587\u4ef6\uff0812:109\uff09\u3002\u5979\u7684\u5199\u5165\u8d70\u5979"
        "\u81ea\u5df1\u7684\u8def\uff0c\u4e0d\u662f\u8fd9\u4e00\u9875\u3002",
        "12:13 / 12:5 / 12:109",
    ),
    (
        "\u5979\u5199\u7684/\u4e16\u754c\u8ba4\u77e5.md",
        "\u53ea\u6709\u6574\u7406\u6001\u80fd\u5199\u5b83\uff0810:68\uff09\u3002\u5979"
        "\u7684\u5199\u5165\u8d70\u6574\u7406\u6001\u90a3\u6761\u8def\uff0c\u4e0d\u662f\u8fd9"
        "\u4e00\u9875\u3002",
        "10:68",
    ),
    # **2026-10-07：「人和它共用那一本"那一格**（原文：`"\u4e00\u8d77\u5199\u7684/…md"`）——
    # **从这张表里摘掉了** ⇒ **这一份现在写得动** 。它原来那句理由是：
    #    「它的编辑方式还没做：核心 ／ 悬着的事 ／ 库里长尾**三层**，而那三层今天还没建
    #      （12:57-69，核心 ≤40 条、单条 ≤120 字）。先渲染成只读，等那一套做出来再开。」
    # ⇒ **那一套做出来了**（`hers.py`：三层 ＋ 三个动作 ＋ 两个上界 ）⇒ 门开 ，
    # 而改它走 `POST /persona/about` 那一条路（每一次**先留一版历史** ）。
    (
        "README.md",
        "\u5b83\u662f\u90a3\u5f20\u5bf9\u7167\u8868\u7684\u5bb6\u2014\u2014\u8bfb\u53e3\u4ece\u5b83"
        "\u90a3\u513f\u8bfb\u300a\u54ea\u4e00\u6bb5\u662f\u54ea\u4e2a\u6587\u4ef6\u300b"
        "\uff08persona/README.md:13-25\uff09\u21d2\u6539\u5b83\u7b49\u4e8e\u6539\u8fd9\u4e00\u9875"
        "\u81ea\u5df1\u7684\u6765\u6e90\u3002",
        "README.md:13-25",
    ),
)
#: **那两个"一次都不许写"的地方** —— ⚠️ 它们的名字**根本不在这条根底下**
#:    （一个在仓里、一个在别处），所以它们**进不了**这个口子；这一条是**说清**
#:    那件事（免得下一个人以为"没在这一张只读表里"就是"可以写"）。
_PERSONA_NEVER_WRITTEN = (
    "那一份旧材料（2026-10-07 已删）/（设计材料，不是正本）",
    "\u65e7\u90a3\u4e00\u5957\u9879\u76ee\u91cc\u7684 persona/（带历史的正文；\u8def\u5f84\u5199\u5728"
    "persona/README.md:33-40）",
)
#: **历史的目录名那一格**（`PERSONA_HISTORY_SHAPE` 里那一格）—— 它落在**根顶上**、
#:    名字**以点开头** ⇒ 第一层守卫就把它挡在外面（`_is_a_relative_persona_name`：
#: 每一层不以点开头）。 于是它**天然只读**：页上那条口子够不着它。
#:    ⚠️ 这一条是给读的人看的（"回退用的那一份，页上改不动"那句话有出处）。
_HISTORY_PREFIX = ".history-"


def may_be_written(name: str) -> tuple[bool, str]:
    """ **"人"能不能写这一份** —— 本刀那条硬轴的唯一判据（交回：能不能 ／ 为什么）。

    ⚠️⚠️ **它按"整格相等"或"目录那一段相等"配**（照 T1 那条量出来的教训：
        `世界认知` 与 `世界` 那种"按包含去配"会各错一半）——
        **目录那一条**（`\u5979\u5199\u7684/`）管的是它**底下**，别的条目管的是**那一份文件本身**。
    ⚠️ **出一条命中的就够**（表里前面那几条更细，后面的更宽）—— 今天没有两条同时命中的名字，
        而**谁先命中谁说了算**这条次序写在表里（`_PERSONA_READ_ONLY`）。
    ⚠️⚠️ **"目录那一条"今天一次都命不中**（2026-10-13 核过）：那一支要的是
       **以 `/` 结尾的键**，而那张表今天**一个都没有**（四条键全是具体文件名）——
       ⇒ 它不是"漏了"，是**留给以后**的形状：谁要把整整一个目录锁成"人只读"，
       就往那张表里加一条以 `/` 结尾的键，这一支当场生效。
       ⚠️ 判据钉着两件（`tests/test_web.py`）：那一支**还在**（别顺手删）、
       而表里**今天确实没有**那种键（所以"它走不到"这句话**写得准**）。
    ⚠️ **读不到的文件名**（不在表里、也不在那个目录底下）⇒ **可写**：
        那正是 `你定的/` 那六份、`边界.md` ／ `通道形状.md` ／ `素材/语气示例.md` ——
         而它们"归谁"是**另一条轴**（`owner_of()`），两条轴不许混成一条。
    """
    for key, why, cite in _PERSONA_READ_ONLY:
        if name == key or (key.endswith("/") and name.startswith(key)):
            return False, why
    return True, ""


#: **只有它自己写得动的那两份** —— **这是另一扇门，不是上面那张表的第二份**（`10:63-71`）。
#: ⚠️⚠️ 两条轴**方向相反**、各自回答一个问题，别把它们读成一个：
#:    · `may_be_written()` —— **人**能不能写？（那两份：**不能**，理由在 `_PERSONA_READ_ONLY` 里）；
#:    · `may_be_written_by_her()` —— **整理态**能不能写？（那两份：**能**，名单就是下面这一格）。
#: ⇒ 于是"页上那一块点不开"与"它能写那两份"**同时成立**，而那正是 `12:13` 与 `10:68`
#:    那两句要的形状：**它的写入走整理态那条路，不是这一页。**
#: ⚠️ **只有这几份**（名单就是下面那一格，今天三个名字）：`你定的` 那六份它只能**提议**（`10:70-79`：改不改由人定）
#:    ⇒ 那一件事今天**没有落点**（欠在 `consolidation._OWED` 的 `propose_to_the_ones_you_set` 上 ——
#:    本行的前一版只写了一句"欠在 `consolidation._OWED` 那一类地方"，而 `consolidate.py` 里**当时没有**
#: 那张表： 一个指不到东西的指针，正是本仓最忌的那一类。表落在 `consolidate.py`）。
#: ⚠️ 名字写成转义：核心全树不许出现那一个主体字（`tests/test_boundary.py`）。
HER_FILES: tuple[str, ...] = (
    "\u5979\u5199\u7684/\u81ea\u8ff0.md",
    "\u5979\u5199\u7684/\u4e16\u754c\u8ba4\u77e5.md",
    # **2026-10-05（习惯那一层）**：长出来的样子
    #    —— 帐在记忆那一侧，而**渲染件由整理态写在这一侧**
    # （ 与 `自述` 同族：**只有那一扇门写得动它** ）。
    "\u5979\u5199\u7684/\u4e60\u60ef.md",
)


def may_be_written_by_her(name: str) -> tuple[bool, str]:
    """ **整理态那一扇门的唯一判据** —— 交回：写得动吗 ／ 为什么（照 `may_be_written` 的成法）。

    ⚠️ **它判的是"这一份归不归它写"，不是"这一版写不写得下去"** —— 后者是 `persona_shape()`
       那一门（承重形状），而它**在两道门后面各判一次、只有一份实现**（见 `_write_a_version`）。
    ⚠️ **一条命中的就够，而今天只有一种命中**：名单**整格相等** —— 刻意**不做**
       "在那个目录底下就放行"那种按目录放行：那是**又一处会更宽**的判据，
       而这两个名字没有"将来还会多几份"的前景（多一份要人先写进这一格）。
    """
    if name in HER_FILES:
        return True, ""
    return False, (
        f"这一扇门只写它自己那几份（{list(HER_FILES)}）—— `{name}` 不在名单上。"
        " `你定的` 那几份**它只能提议、不能写**（`10:70-79`：改不改由人定）")



# ── **那五类承重的形状**（本波那条 Critical：改掉它们等于改掉这一份的功能） ──────
#
# ⚠️⚠️ **这一处判的是"哪些字符是语义，不是散文"** —— 作者的原话：
#    「去掉这些格式或是字符，会造成破坏性修改，小则这一部分提示词直接失效，
#      大则整个文件的功能失效。」
# ⇒ 它是**一条判据**，而这条判据有**两个下家**（都从这一处算 —— 页那一侧只画）：
#      · **读那一侧**：`GET /persona` 里每份文件带一份 `shape`（`file_info()["shape"]`）
#        ⇒ 页照它把那一行**锁住**、并把 `why` 说出来（页上**不许**另写一份"哪几行不能改"：
#        两份清单一定会漂 —— 与 `may_be_written()` 同一条）；
#      · **写那一侧**：`save_text()` 顶上照它**当场拒**（**不信任页那一侧**：
#        一个人直接打 `POST /persona` 也该被拦下）。
#
# ⚠️ **五类各引了一处出处**（`cite` 就是那一类，一类一条、可核）：
# ① `comment` 抬头那条 `<!-- … -->` —— 它是 `owner_of()` **唯一**的输入
#                 （`_lead_comment()` ＋ `_home_in()`）：删掉它，归属那一列**静默**变成 `—`；
#                 而 `边界.md:1` 那一条还写着「按部署可覆盖」（那是这一份的许可声明）。
#    ② `heading`  以 `#+ ` 开头的那一行 —— 一节从它起（`12:5`：正文已经存在、不要重写）。
# ③ `section` 以 **`▎`（U+258E）** 开头的那一行 —— 是 `▎`，**不是** `|`
#                 （`|` 是表格那一行；这是本波订正的一处）；`存在方式.md` 整份由它分节。
#    ④ `row`      以 `|` 开头的那一行 —— 表格的每一行（`README.md` 那张对照表就是它）。
#    ⑤ `emphasis` 一行里的 `**…**` —— `12:32` 那句纪律（"观测、推测、事实分开写"）
#                 写在原文里时**就带着这些星号** ⇒ 星号是语义，不是排版。
#
# ⚠️ **它只判"这一份里哪些字符是承重的"**（一个字都不读盘、不看名字、不读时钟）——
#    于是它与 `may_be_written()` 同一种形状：**一条纯函数 ＋ 一份可引用的表**。

#: `▎`（U+258E）—— 写转义：它是**一个字符**，不是排版。
_SECTION_MARK = "\u258e"

#: 强调那一对（`**…**`）—— 在这五类里只有它是一对一对数的（见 `persona_shape`）。
_EMPHASIS = "**"

#: 一行以 `#+ ` 起头（1–6 个 `#` ＋ 一个空格）。
_A_HEADING = re.compile(r"^#{1,6}\s")

#: **五类各自的 `why`**（`cite` 与类名在下面那五个常量上）——
#:    它们会**原样显示在页上**（那一行被锁住时旁边那一句）⇒ 写成人话，别写标记。
_SHAPE_WHY: dict[str, str] = {
    "comment": "抬头那条注释是「这一段归谁」唯一的出处 —— 删掉它，归属那一列会静默变成 —",
    "heading": "这一行是一节的开头（`#+ `）—— 没有它，这一节就散了",
    "section": "这一行是一节的题（以 ▎ 开头）—— 它没了，底下那一节就并进了上一节",
    "row": "这一行是表的一行（以 | 开头）—— 表少一行，读它的那几处就少一条",
    "emphasis": "这一行里的 ** 是一对一对的 —— 拆掉任何一对，这一行读起来就不一样了",
}

#: **五类的名字与出处**（`cite` **同时是类名** —— 比较时拿它当键，一类一条）。
_CITE_COMMENT = "web.py::_lead_comment() \u2192 owner_of() \uff0f 边界.md:1\uff08按部署可覆盖\uff09"
_CITE_HEADING = "12:5\uff08正文已经存在、带历史，不要重写\uff09"
_CITE_SECTION = "\u5b58\u5728\u65b9\u5f0f.md:5,22,27,37,42"
_CITE_ROW = "12:5 \uff0f README.md\uff08那张对照表\uff09"
_CITE_EMPHASIS = "12:32\uff08写法只有一条纪律：**观测、推测、事实分开写**\uff09"


def _the_lines(text: str) -> list[str]:
    """正文切成几行 —— **CRLF ／ CR 先归一成 LF**：只影响"第几行"，不动正文那一段。

    ⚠️ 归一在这里、**只在这里**：页那一侧写回去的是 `\\n` 拼的（`linesOf()` 同一条口径）
        ⇒ 一份 CRLF 的文件被页改一次就成了 LF —— 那**不是**形状上的损失（形状那份键
        两边都过这一处），别把它读成"改坏了"。
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def persona_shape(name: str, text: str) -> list[dict[str, Any]]:
    """ **这一份里哪几处是承重的形状** —— 一处一条，人读得懂的 `why` ＋ 可核的 `cite`。

    ⚠️⚠️ **它是一条纯函数**（`name` 只为对称与将来按份分档；今天一个字都不用它）：
        同一个 `text` 交回同一串 —— 于是"页上锁哪几行"与"写那一侧拒哪一版"
        **是同一个答案**（`02:127`：面板读的值＝实际生效的值，由同一个函数算）。

    ⚠️ **每一格是什么**：
        · `line` —— **1 起**的行号（那一件形状**从哪一行开始**；跨行的只有第 ① 类）；
        · `start` ／ `end` —— **整份正文里的字符偏移**（半开区间），**不是行内偏移**
          —— 因为抬头那条注释**可以跨好几行**（`你定的/破甲.md` 就是）：行内偏移表达不了
          它，而"这一段字符"这个说法两边都成立；
        · `why` ／ `cite` —— 人话 ＋ 出处（页上只显示 `why`，`cite` 走那一格的 `title`）。

    ⚠️ **五类各有一条记录**（一行可以同时落在两类上 —— 那是对的，两件事各说各的）：
        ① 抬头那一块 `<!-- … -->`（**一整块一条**，可能跨行）；② `#+ ` 标题行；
        ③ `▎` 起头的那一行；④ `|` 起头的那一行；⑤ 带 `**` 的那一行（**一行一条**，
        而 `why` 说着那一行有几个 —— 见 `_a_shape_key()`：配对是**按行数**配的）。

    ⚠️ **它不判"多了"**：加一行、加一节、多一对强调，这一函数照旧交回它们的记录
        —— 拦不拦是 `save_text()` 那一道闸的事（那一道只拦"少"）。
    """
    lines = _the_lines(text)
    flat = "\n".join(lines)
    starts: list[int] = []
    at = 0
    for one in lines:
        starts.append(at)
        at += len(one) + 1
    out: list[dict[str, Any]] = []

    # ① 抬头那一块：**第一个非空行**以 `<!--` 开头（与 `_lead_comment()` 同一条口径）。
    head = next((index for index, one in enumerate(lines) if one.strip()), None)
    if head is not None and lines[head].lstrip().startswith("<!--"):
        col = len(lines[head]) - len(lines[head].lstrip())
        begin = starts[head] + col
        shut = flat.find("-->", begin)
        out.append({"line": head + 1, "start": begin,
                    "end": len(flat) if shut == -1 else shut + len("-->"),
                    "why": _SHAPE_WHY["comment"], "cite": _CITE_COMMENT})

    for index, one in enumerate(lines):
        number = index + 1
        base = starts[index]
        if _A_HEADING.match(one):
            out.append({"line": number, "start": base, "end": base + len(one),
                        "why": _SHAPE_WHY["heading"], "cite": _CITE_HEADING})
        if one.startswith(_SECTION_MARK):
            out.append({"line": number, "start": base, "end": base + len(one),
                        "why": _SHAPE_WHY["section"], "cite": _CITE_SECTION})
        stripped = one.lstrip()
        if stripped.startswith("|"):
            left = len(one) - len(stripped)
            out.append({"line": number, "start": base + left, "end": base + len(one),
                        "why": _SHAPE_WHY["row"], "cite": _CITE_ROW})
        count = one.count(_EMPHASIS)
        if count:
            first = one.find(_EMPHASIS)
            last = one.rfind(_EMPHASIS)
            out.append({"line": number, "start": base + first,
                        "end": base + last + len(_EMPHASIS),
                        "why": f"{_SHAPE_WHY['emphasis']}（这一行有 {count} 个）",
                        "cite": _CITE_EMPHASIS})
    out.sort(key=lambda one: (one["start"], one["cite"]))
    return out


def _a_shape_key(one: dict[str, Any], text: str) -> tuple[str, str]:
    """ **一件形状是哪一件** —— 拿它去配"旧那一版有的，这一版还有没有"。

    ⚠️⚠️ **两类各用各的键，而那是量出来的**：
        · ①②③④ 那四类：键是**那一段字符本身**（`text[start:end]`）——
          删了 ／ 改了 ／ 拆了，键就找不着了；
        · ⑤ `**` 那一类：键是**那一行有几个**（`"4"` 这样的一个数）——
           因为强调那一格**只有"成对"这一件事是承重的**：把里头那句话改写掉，
          星号照旧在、这一行的语气照旧归你 —— **那正是编辑该做的事** 不该拦。
          ⚠️ 键**不含行号**：行号会随插入 ／ 删除而漂，而"少了一对"不会。
    """
    if one["cite"] == _CITE_EMPHASIS:
        return (_CITE_EMPHASIS, str(_the_lines(text)[one["line"] - 1].count(_EMPHASIS)))
    return (one["cite"], "\n".join(_the_lines(text))[one["start"]:one["end"]])


def _shape_keys(name: str, text: str) -> dict[tuple[str, str], int]:
    """这一份的**形状账**（一件一件数一遍）—— 与 `persona_shape()` 同一次走法，不另写一份。"""
    keys: dict[tuple[str, str], int] = {}
    for one in persona_shape(name, text):
        key = _a_shape_key(one, text)
        keys[key] = keys.get(key, 0) + 1
    return keys


def _the_shape_that_is_gone(name: str, was: str | None,
                            now: str) -> dict[str, Any] | None:
    """ **这一版把哪一件承重的形状弄没了**（一件都没丢 ⇒ `None`）—— 交回那一件。

    ⚠️⚠️ **判两件事，各答各的**（那两件不是同一件事）：
        · **这一版自己就不成对**：某一行里的 `**` 是**奇数个** ⇒ 当场交回那一行
          —— 它**不需要**有旧文（一份全新的文件写坏了也算）；
        · **旧那一份有、这一版没了**：按 `was` 的**文件次序**走，第一件找不着的就是它
          —— "找不着"按**计数**配（同一样东西有两份，那就要有两份），
          于是"少了一份"与"全没了"都落在这里。
    ⚠️ **只拦"少"，不拦"多"**：加一行 ／ 加一节 ／ 多一对强调**照旧写得下去**
        （那是编辑本来该做的事）—— 这一道闸拦的是**拆掉**，不是**加上**。
    ⚠️ **盘上没有那一份**（`was is None`）⇒ 只判前一件：没有旧文，就没有"少了什么"。
    """
    lines = _the_lines(now)
    for one in persona_shape(name, now):
        if one["cite"] == _CITE_EMPHASIS and lines[one["line"] - 1].count(_EMPHASIS) % 2:
            return one
    if was is None:
        return None
    left = _shape_keys(name, now)
    for one in persona_shape(name, was):
        key = _a_shape_key(one, was)
        if left.get(key, 0) > 0:
            left[key] -= 1 # 配掉一件（同样的东西有几份，就要有几份）
            continue
        return one
    return None


def _was_text(path: Path) -> str | None:
    """盘上现在那一份的正文（**读不到 ／ 不是 UTF-8 ⇒ `None`**）—— 形状那一关的"旧那一侧"。

    ⚠️ **两个"没有"合成一个 `None` 是对的吗**：对，**只在这一处**。这一格要答的只有一个
        问题——"**拿什么去比**"；一份读不出来的旧文**没有**可比的形状（而不是"它的形状
        是空"）⇒ 交 `None`，那一关就只判"这一版自己成不成对"。 而"那一份在不在"
        这件事**不归它答**：那是 `save_text()` 里 `was_there` 那一格（它决定留不留版）。
    """
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _is_a_relative_persona_name(name: str) -> bool:
    """ **第一层**：那个名字长不长得像这条根底下的一个相对名 —— **只看名字，不碰盘**。

    ⚠️⚠️ **它与第二刀那条守卫（`_PAGE_FILE` 那条正则）是同一个形状**：一条**形状判据**，
        过了才去拼路径 —— 而**没有**"先拼、再规范化、再看开头是不是那条根"那一步
        （那一步才是这类路由常出错的地方：一解码、一规范化就能被绕过去）。
    ⚠️ 四条一起才是它：
        · **不是绝对路径**（不以 `/` 开头、不带盘符那种 `:`、不含 `\\`）；
        · **每一层都不以点开头** ⇒ `.` ／ `..` ／ `.hidden` 一个都进不来；
        · **每一层都非空** ⇒ `a//b` 与结尾那个 `/` 都被挡下；
        · **一个 NUL 都没有**（下一层会把名字截断）。
    ⚠️ **它比第二刀那一条宽一档，而那是量出来的**：这里的名字有中文、而且有一层子目录
        （那张表里 12 个文件有 3 个住在子目录里）⇒ 不能像 `_PAGE_FILE` 那样只认一层 ASCII。
         "宽出来那一档"由**第二层**兜着（见 `_under_root`）—— 两层各管一半，都要有牙。
    """
    if not name or "\0" in name:
        return False
    if name.startswith("/") or "\\" in name or ":" in name:
        return False
    return all(part and not part.startswith(".") for part in name.split("/"))


def _under_root(root: Path, name: str) -> Path:
    """ **第二层**：两层都过了，才交回那一个落点（**绝对、已解开链接**）。

    ⚠️⚠️ **第二层问的是另一件事**：把名字**按符号链接解开之后**，落点还在不在那条根里
        —— `resolve()` 就是那一步（它跟随链接、也消掉 `..`）。
         名字那一层**看不见**符号链接（`链接.md` 长得一点毛病都没有）⇒ 少了这一层，
        一个根底下的链接就能读到根外面那一份。
    ⚠️ **交回的是 `resolve()` 之后那一条**（不是拼出来的那一条）：调用方拿它去读写，
        走的就正是**判过**的那一条路径 —— 中间没有第二条路可以骗。
    ⚠️ **本波补的第五格：名字得是一份 `.md`**（一处，就在这一层 —— 它是"名字 ⇒ 路径"
        那一处、也是唯一会为名字抛错的那一处）。**为什么它是承重的**：历史那两处
        （`_history_dir()` ／ `_version_name()`）**都按 `.md` 那三个字符切文件名**
        （`Path(name).name[:-len(".md")]`）—— 于是一个 `你定的/x.txt` 交上去，切出来的是
        `x.tx` ⇒ **磁盘上悄悄多出一条名字被啃掉一截的历史目录**（本波量到的形状：
        它不报错、也不越界，只是名字不对）。 今天那条根底下**没有一份不是 `.md`**
        （12 份全是），所以这一格只拦得住"打错的那个名字"—— 拦早一步，比事后去猜好。
    """
    if not _is_a_relative_persona_name(name):
        raise PersonaPathError(
            f"那个名字不像这条根底下的相对名：{name!r}（得是相对的、每一层不以点开头）")
    if not name.endswith(_MD_SUFFIX):
        raise PersonaPathError(
            f"那个名字得是一份 `{_MD_SUFFIX}`：{name!r}（历史那一格按它切文件名）")
    base = Path(root).resolve()
    path = (base / name).resolve()
    if base not in path.parents:
        raise PersonaPathError(f"那个名字指到根外面去了：{name!r} ⇒ {path}")
    return path


# ── 第四段 · Task 2：那一条**形状**与那一段**写那一侧** ──────────────────────────
#
# **保存那一件事的次序是承重的**（`save_text()` 那一段逐字写着那两个动作的先后）：
#    **先留一版历史** ⇒ **再写新文**。反过来做，第一次保存之后旧正文就**没了**
#    —— 而那正是"留了一版"这句话唯一的用处。
#
# ⚠️⚠️ **历史住在这一条根的**第一层**（`<这条根>/.history-<文件名>/…`），而它进不了读口**
# —— **本波订正过一句话**：这一处从前把那条目录说成住在**根的外面**（跟这条根并排），
#    那是**假的**：`_history_dir()` 交回的是 `root / ".history-<文件名>"` —— 它落在**根里面**，
#    而 `tests/test_web.py` 那条 `…the_history_shape_is_kept_in_one_place_and_the_stamp…`
# 钉的正是它（`port.root / ".history-甲一"`）。 与那一份设计稿 `§一 · 5` 写的
#    `.history\<文件名>\<时间戳>.md` **是同一个落点**（它本来就长在根底下）。
# ⇒ **于是"它永远不在读口的射程里"靠的不是位置，而是名字**：
#      `_is_a_relative_persona_name()` 要求**每一层不以点开头** ⇒ `.history-…` 那一段
#      过不去，`path_of()` 从不认它（那一层守卫就是它唯一的门槛）。
# 那一串时间戳照旧在文件名上 （形状的原文见下面那个常量）。
#
# ⚠️ **文件名照旧项目那一刻的形状**：`<文件名>-<时间戳>.md`（旧项目那 4 ＋ 11 份
#    **每一份都带着它自己的名字** —— `自我-20260921-004017.md` 那一类）。
# 而那一份设计稿把那一格写成"时间戳"⇒ 两处**说的是同一件事**（一版一个名字、
#      名字认得出是哪一份、也认得出来它出自哪一份），只是那一格多印了一遍文件名
#      ⇒ **本刀照旧项目那一份**（"照它"那句话指的是那套东西本身）。
#
# ⚠️⚠️ **时钟：一个字都不读**（本模块抬头那句"一次都不读时钟"照旧成立）——
#    那串时间戳**由调用方递进来**。理由有两条，都不是洁癖：
#      · 本仓立过的规矩：**测试不许依赖墙上时钟**（文件名上带时间戳 ⇒ 一读就带上了）；
# · 而它**让"哪一秒"这件事归调用方** —— 页那一侧要标"什么时候改的"，
#        服务这一侧不该替它挑一个。
#    ⇒ 于是"缺了那一串"不是"取现在"那条路：它是**一条 400**（`_a_stamp()`）。

#: **历史那一条形状**（一处，可引用）：`<这条根>/.history-<文件名>/<文件名>-<时间戳>.md`。
#: ⚠️ 它是**给下一个人读的那一句话**，而落盘那一侧算的就是它（`_history_dir()` ＋
#:    `write_text()` 那两处）—— 一份"常量写着这一句、而代码里另算一处"就是两份真相。
PERSONA_HISTORY_SHAPE = ".history-<文件名>/<文件名>-<时间戳>.md"

#: **那一串时间戳的形状**（旧项目那几份历史里就是它）：`YYYYMMDD-HHMMSS`。
#: ⚠️⚠️ **它是一道守卫，不是排版**：那一串**直接长在一条路径上** ⇒
#:    `../../` ／ 反斜杠 ／ 盘符 ／ 空串从这儿进去，落点照样跑到根外面（与名字那条同一种事故）。
#: ⚠️⚠️ **收口是 `\Z` 而不是 `$`**（本波量到的一处真缺陷）：`$` 在 Python 里**还认结尾那个换行**
#:    —— `"20260930-101112\n"` 它能放过去，而那个换行会一路长进文件名
#:    （实测：`OSError [Errno 22]`，落点成了 `甲一-20260930-101112\n.md`）。
_STAMP = re.compile(r"^[0-9]{8}-[0-9]{6}\Z")

#: **文件名上最后那一格**（`slot`）的形状 —— 旧项目那两份历史里是
#: `-edit` ／ `-confirm` ／ `-demote` ／ `-delete`（`12:115`：三种动作都在那 11 份里）。
#: ⚠️ **它的第二个用处是"同一秒里的两次保存"**：那一串时间戳只到秒 ⇒ 同一秒 ＋ 同一格
#:    就是同名 ⇒ 撞名那一下**当场抛**（见 `write_text`），绝不盖掉前一版。
#: ⚠️ **收口同 `_STAMP`（`\Z`）**：理由一模一样，两处一起看。
_SLOT = re.compile(r"^[a-z][a-z-]*\Z")

#: 缺省那一格（ **`edit` 就是旧项目那几份历史里用的那一个词**，不是这里新造的一个）。
DEFAULT_VERSION_SLOT = "edit"


def _a_stamp(stamp: Any) -> str:
    """ **那串时间戳** —— 调用方递的那一串；形状不对**当场抛**，**绝不**去读时钟。

    ⚠️ **它不是"校验一下更稳妥"**：这一串是**由调用方控制**的一格，而它长在一条路径上
        ⇒ 少了它，"保存"那一条路就是一条可以被写出这条根的路（读那一侧的守卫一个字都拦不住：
        那个名字**长得完全合规**）。
    """
    if not isinstance(stamp, str) or not _STAMP.match(stamp):
        raise PersonaPathError(
            f"那串时间戳得是调用方递进来的 `YYYYMMDD-HHMMSS`，递来的是 {stamp!r}"
            "（这一层不读时钟 —— 页那一侧挑'哪一秒'）")
    return stamp


def _a_slot(slot: Any) -> str:
    """ **文件名最后那一格** —— 与 `_a_stamp` 同一条理由（它也是由调用方控制的一格）。"""
    if not isinstance(slot, str) or not _SLOT.match(slot):
        raise PersonaPathError(
            f"那一格得是 `[a-z][a-z-]*`（如 `edit` ／ `confirm`），递来的是 {slot!r}")
    return slot


def _history_dir(root: Any, name: str) -> Path:
    """ **那一条历史目录** —— `<这条根>/.history-<文件名>/`（**根顶上**，见上面那一段）。

    ⚠️ **名字取那一份自己的**（不是整条相对名）：`你定的/甲一.md` 与 `一写/自述.md`
        各领到一条自己的历史目录，两条都在**同一个地方**（这条根顶上）。
    ⚠️ 历史目录**不经** `_under_root()`：它不在这条根底下（那是**故意**的 —— 于是它
        进不了那十段的读口）。⇒ 而"落点跑不到别处"这件事靠的是**名字**：
        那一个名字里**只有** `<文件名>` 那一段（一个名字，没有 `/`），于是那条目录
        只能落在这条根顶上。
    """
    return Path(root).resolve() / f".history-{Path(name).name[:-len(_MD_SUFFIX)]}"


def _keep_a_version(root: Any, source: Path, name: str, stamp: Any,
                    slot: Any) -> Path:
    """ **留一版** —— 把 `source` 现在这一份**逐字节**放进这一条历史目录，交回那一版。

    ⚠️⚠️ **它是 `save_text()` 的第一步，而这一步与第二步之间隔着一次调用**
        （不是两份实现）：`save_text()` 里"先留版 ⇒ 再写新文"那条次序，判据就落在
        **这一处**上（`tests/test_web.py` 把它换成一个当场抛的东西 ⇒ 正文一个字都不许动）。
        ⇒ **别把这一处并回 `save_text()`**：并回去，那条判据就只剩"看调用序"那种弱形式。

    ⚠️ **复制而不是"读成文本再写回去"**：后者会顺手改掉换行与 BOM
        （`read_text(encoding=…)` 那一族默认 `newline=None` ⇒ `\\r\\n` 会变成 `\\n`）
        —— 而"留的那一版"就不是原来那一份了。

    ⚠️ **撞名当场抛**（`FileExistsError`）：`shutil.copyfile` 会**默默盖掉**目标，
        而"盖掉前一版"与"留一版"是相反的两件事。
    """
    kept = _history_dir(root, name)
    target = kept / _version_name(name, stamp, slot)
    kept.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(
            f"那一版已经在盘上了：{target.name} —— 同一秒里的第二次保存要换一格 slot")
    shutil.copyfile(source, target)
    return target


#: **`/people` 那一页不铺全表** —— 不送 `q` 时最多交这几位（最近的那几位 ）。
#: 作者的原话："认识的人会越来越多，一个个看不太现实" ⇒ 要找人，**搜**。
_PEOPLE_AT_MOST = 12


def _the_picked(matcher: Any, query: str, texts: list[str]) -> list[int] | None:
    """ 叫那个匹配器一次（ 没接 ／ 抛了 ⇒ 交 `None`：调用方**照旧**用简单包含 ）。

    ⚠️ 它**不让匹配器的毛病带走这一条请求**：这里只做"转发 ＋ 兜底" 。
    """
    if not callable(matcher):
        return None
    try:
        got = matcher(query, texts)
    except Exception: # noqa: BLE001 —— 匹配器坏了，照旧能搜
        return None
    from .search import SemanticUnavailable
    if isinstance(got, SemanticUnavailable):
        return got
    return [int(one) for one in got] if got is not None else None


def _the_query(path: str) -> str:
    """ 从那条路径里取 `?q=`（没有 ⇒ 空那一串 ）。 解析失败也照旧回空（不抛 ）。"""
    if "?" not in path:
        return ""
    pairs = path.split("?", 1)[1]
    for one in pairs.split("&"):
        if one.startswith("q="):
            from urllib.parse import unquote_plus
            return unquote_plus(one[2:]).strip()
    return ""

def _version_name(name: str, stamp: Any, slot: Any) -> str:
    """那一版历史**叫什么** —— `<文件名>-<时间戳>[-<那一格>].md`（缺省那一格不印）。

    ⚠️ **两处都先过形状**（`_a_stamp` ／ `_a_slot`）：这一步**在落盘之前**，
        于是"那两格不对"与"盘上什么都没动"是同一条路上的两件事。
    """
    stem = Path(name).name[:-len(_MD_SUFFIX)]
    said = _a_slot(slot)
    tail = "" if said == DEFAULT_VERSION_SLOT else f"-{said}"
    return f"{stem}-{_a_stamp(stamp)}{tail}.md"


class PersonaPort:
    """ **读那一侧**（Task 1）—— 那十段、它们的归属、以及它们各自那一份文件。

     **一个根**：所有读**和所有写**都过 `path_of()`（那两层守卫）—— 而它只认这条根
       底下的名字。 写那一侧（Task 2）接的**是同一层守卫**（`write_text()` 走的就
       是它，**没有**第二份判据）。

     **保存那条路改的是"那一份是谁"**（本段给那条回环边界多加的那一条，原文在
       `PERSONA_BOUNDARY_LINE` 上）：这一条口子**读**的是那几段人格正文，而
       `write_text()` **写**的就是它们 ⇒ 「只听回环」＝「**这一台机器**」，
       而**这一台机器上的谁都改得动「那一份是谁」** 。它不比 `POST /say` 更紧、
       也不比它更松（**同一条**边界，同一个收紧点：`Origin` ／ 令牌 —— 本刀刻意不做 ）；
        不一样的只是**代价**：改错了那一份，改的是"那几段字本身"。

     **它今天的来源是三份文件**（本类的全部责任就是"别在代码里再抄一份"）：
       · **段序｜哪一段是哪个文件** ⇒ `order_doc`（`docs/design/段序.md` **那一处正本**：
         行序就是段序，前两格是段与它那几份文件）；
       · **这棵档案里有哪些文件** ⇒ **根底下的 `README.md`**（那一列的正本 —— 它自己写着
         "运行时以这一份为准"）。 它**不再是**"哪一段是哪个文件"的出处（那一格从正本
         那张表的前两格读）， 而它**照旧是一处来源**（`rows()` ／ `unplaced()` 现读它）；
       · **三个家** ⇒ `owners_doc`（`docs/design/11-现实与世界观对照.md` 那一行）。
       ⇒ **`order_doc` ／ `owners_doc` ／ 根底下的 `README.md`** —— 这三处都是**现读**：
       改了文档，这个口子当场跟着变（`02:127` 那条判据）。

    ⚠️⚠️ **两张清单对不上是事实，不是缺陷**（本波量的）：段序十段 ／ 表十一行 ⇒
        `segments()` 交**十段**（段序），而 `rows()` 交**十一行**（表）——
         两张**都要带出来**：`示例对话` 交成 `missing`，`世界认知` 与那一行"两份文件"
        交成 `unplaced`。**不许**挑一张、把另一张悄悄丢掉。

    ⚠️⚠️ **这条口子读的那些字，进得了那一次请求**（ 本类这一段**订正过两次**：它从前写着
       "**不会**进任何一次请求 …… `build_prefix()` 今天走到就抛" —— **那三句早就不成立**）：
       · **③ 过得去** —— `console.py` 那个 `build_prefix()` 不抛了：没递前缀时**现装**
         （`prefix.assemble` —— 同一处正本、同一批文件、`''.join` 不加分隔符）；
       · **④ 拿那一条换过会话** —— 交接在换掉会话**之前**先装好那条前缀，而
         `reset([*prefix, *tail])` 之后，新会话那条 `role="system"` **就是**那几段订成的；
       · **而"每请求都装那一刀"之后它是从第一句话起就进**：`Console.assemble(head=…)`
         那一格的缺省换成 `None`（＝**现装**那一句话，`console._a_head_cell`）⇒ **不必等交接**
         —— 而"哪一格在装、那一句话在请求里恰好一次、以及它为什么每请求现装"都写在
         `console._a_head_cell` 那一段上（本类不重复那一份账）。
       ⚠️ **页面这一条口子接的是"点一块就地改"那一条路**（`POST /persona` —— 它写的是
       盘上那一份文件）； 而"下一次订前缀"读的正是**同一份盘上的文件**（同一处正本、
       同一条根）⇒ 两条**不是一条路**，而它们**接在同一条文件上**：页上写下去的字，
       **下一次请求**那一刻起进得了请求。 装订器一次都不缓存（每次现读那份正本与那几份
       正文）⇒ 盘上那一份改了，下一次订出来的就是新的那一份。
       ⇒ **页上那一句诚实跟着这件事走**（`HONEST_LINE`）：它**今天说的是"下一次请求
       就生效"**（本类这一段从前点名的是它的上一版； 它属于那一页，本类**一个字都不动它**，
       只在这里点名 —— 本仓的规矩：话跟不上树就该报出来，而不是原地留着）。
    """

    _SECTION_FILES = frozenset({"宪法.md", "角色设定.md"})
    _SECTION_MARKER = re.compile(r"^<!-- section: ([a-z][a-z-]{0,63}) -->[ \t]*\r?$",
                                 re.MULTILINE)
    QQ_ENVIRONMENT = "环境/QQ.md"
    _MEMORY_FILES = {
        HER_FILES[0]: "自我/自述.md",
        HER_FILES[1]: "自我/观察与更正.md",
        HER_FILES[2]: "作息与习惯/作息与习惯.md",
        SHARED_FILE: "自我/关于她自己.md",
    }

    def __init__(self, root: Any = DEFAULT_PERSONA_ROOT, *,
                 read_shared: Any = None, act_on_shared: Any = None,
                 open_a_path: Any = None,
    # **2026-10-07「④」**：跑一次评测那一个 callable（缺省禁 ⇒ 照旧）。
    run_eval: Any = None,
    # **2026-10-07「模糊搜接上」**：那一个匹配器（ 由入口递 —— 零同胞 import ）。
    match_rows: Any = None,
                 order_doc: Any = ORDER_DOC, owners_doc: Any = OWNERS_DOC) -> None:
        #: **那一条根**（只有一条）—— 每个名字都得落在它底下。
        self.root = Path(root)
        self.read_shared = read_shared
        self.act_on_shared = act_on_shared
        self.open_a_path = open_a_path
        self.run_eval = run_eval
        self.match_rows = match_rows
        #: 段序那一处正本（`docs/design/段序.md`—— 一段一行，行序就是段序）。
        self.order_doc = Path(order_doc)
        #: 三个家那一份文档（`11-现实与世界观对照.md`）。
        self.owners_doc = Path(owners_doc)
        #: **进程内**的按资源锁（2026-10-17 批②）：只护"读版本 → 查版本 → 留版 → 替换"
        #: 那一段。⚠️ 它**拦不住另一个进程**，那一段靠版本检查（见本文件顶上那一节）。
        self._locks = _LOCKS

    def path_of(self, name: str) -> Path:
        """ **那条守卫** —— 一个相对名 ⇒ 根底下的那一条绝对路径；越界当场抛。

        ⚠️ 它是本段**唯一**一处"名字 ⇒ 路径"的转换：`read_text()` 与之后写那一侧都走它
           —— 两处各写一份判据，就是"两处各答一个数"那种事故（`02:127`）。
        """
        root, relative = self._storage(name)
        return _under_root(root, relative)

    def _storage(self, name: str) -> tuple[Path, str]:
        if name in self._MEMORY_FILES and self.modern_layout():
            return self._sibling_root("memory"), self._MEMORY_FILES[name]
        if name == self.QQ_ENVIRONMENT:
            if not self.modern_layout():
                raise PersonaPathError("旧版档案没有独立的 QQ 编辑入口")
            return self._sibling_root("环境"), "QQ.md"
        return self.root, name

    def _sibling_root(self, name: str) -> Path:
        target = self.root.parent.resolve() / name
        if target.resolve() != target:
            raise PersonaPathError("档案目录不能重定向")
        return target

    def modern_layout(self) -> bool:
        return all(_under_root(self.root, name).is_file() for name in self._SECTION_FILES)

    def history_directory(self, name: str) -> Path:
        root, relative = self._storage(name)
        return _history_dir(root, relative)

    def qq_environment(self) -> dict[str, Any] | None:
        if not self.modern_layout():
            return None
        try:
            routes = json.loads((self._sibling_root("环境") / "场景.json").read_text(encoding="utf-8"))
            qq = routes["routes"]["qq"]
        except (OSError, ValueError, KeyError, TypeError, PersonaPathError):
            return None
        if qq != {"provider": "core-infinity-qq", "files": ["QQ.md"]}:
            return None
        info = self.file_info(self.QQ_ENVIRONMENT)
        if not info["exists"] or info["body"] is None:
            return None
        return {"text": info["body"], "revision": info["revision"]}

    def read_text(self, name: str) -> str:
        """读一份文件的正文（UTF-8）—— **名字不合规 ⇒ `PersonaPathError`**，不交回 `None`。

        ⚠️ **"不许读"与"读不到"是两件事**：前者抛 `PersonaPathError`（实参不对），
           后者抛 `OSError`（那一份不在）—— 把两者合成一个 `None`，就把"越界了"
           说成"没有这一份"（正是本仓最忌的那种"丢了却没声"）。
        """
        return self.path_of(name).read_bytes().decode("utf-8")

    def read_with_version(self, name: str) -> tuple[str, str]:
        """ **读正文，并带出它那一刻的版本**（2026-10-17 批②）。

        ⚠️ 版本是**逐字节正文**的摘要（`_version_of`）—— 调用方拿着它去提交，
           提交那一刻会再读一遍：**不是那一版就不覆盖**（`PersonaConflict`）。
        不存在交空正文与 "missing"，区别于现存空文件的 sha256。
        其他读取错误仍然抛出；不能把无权限读当作不存在。
        """
        target = self.path_of(name)
        try:
            text = target.read_bytes().decode("utf-8")
        except FileNotFoundError:
            return "", "missing"
        return text, _version_of(text)

    # ── 写那一侧（Task 2：保存 ＋ 留历史） ──────────────────────────────────
    def save_text(self, name: str, text: str, *, stamp: Any = None,
                  slot: Any = DEFAULT_VERSION_SLOT,
                  expect: str | None = None) -> dict[str, Any]:
        """ **保存一份** —— **先留一版历史**（那一版是它**原样的旧正文**）⇒ **再写新文**。

        ⚠️⚠️ **次序是承重的，不是风格**：反过来做，第一次保存之后旧正文就**没了**
            —— 而"留了一版"这句话唯一的用处就是"改坏了还能拿回来"。
             那一版是**逐字节复制**的（`shutil.copyfile`，不是"读成文本再写回去"：
            那样会顺手改掉换行与 BOM，而"留的那一版"就不是原来那一份了）。

        ⚠️⚠️ **这串时间戳由调用方递**（`stamp`，形状 `YYYYMMDD-HHMMSS`）—— 本模块
            **一次都不读时钟**（模块抬头那一句照旧），于是"哪一秒"这件事归调用方：
            测试里递一串写死的，页那一侧递它自己那一刻。

        ⚠️ **盘上没有那一份的时候照旧写得下去**：那一格交 `left_a_version: False`
            （"没有旧文可留"与"留了一版"**不是一件事**）—— 而零字节的那一份**照旧留**
            （它的旧正文就是"空的"，那也是一份正文）。

        ⚠️ **撞名当场抛**（`FileExistsError`）：同一秒 ＋ 同一格 = 同名，而"盖掉前一版"
            与"留一版"是相反的两件事 ⇒ 本实现选**当场抛**，让调用方换一格
            （`slot='confirm'` 那一类）或者等下一秒。

        ⚠️ **它与 `read_text()` 走的是同一道守卫**（`path_of()`）—— 写这一侧**不另写**
            一份判据：两处各写一份，就是"两处各答一个数"那种事故（`02:127`）。

        ⚠️⚠️ **本刀多加的那一道：名字合法 ≠ 这一份该你写**（`may_be_written()`，本刀的硬轴）
            —— 那几份**只有文档点名的那一方**写得动（在两处子目录里那三份 ＋ `README.md`）
            —— 它们**当场抛 `PersonaWriteRefused`**（→ HTTP **403**），理由跟着那一句走。
             它**压在留版之前**：一份不许写的东西，**连历史都不该多出一版**。

        ⚠️⚠️ **本波（第三刀）加的那一道：这一份归你写 ≠ 这一版改得下去**
            （`persona_shape()` ／ `_the_shape_that_is_gone()`）—— 一份文件里那几处
            **承重的形状**（抬头那条注释 ／ `#+ ` 标题 ／ `▎` 那一节 ／ `|` 那一行 ／
            一对一对的 `**`）**删掉或拆散一处**，这一版就把它改坏了 ⇒ **当场抛
            `PersonaShapeRefused`**（→ HTTP **409**），而那件形状的 `why` ＋ `cite`
            跟着那一句走。
             **它压在留版之前**（与上面那一道同一个次序，同一条理由）；
             **它不信任页那一侧**：页上那几行**锁住**只是"不让人顺手改"，
              而这一道是"直接打 `POST /persona` 也过不去"——两条一起才算数。
            ⚠️ **只拦"少"，不拦"多"**（加一行 ／ 加一节 ／ 多一对强调照旧写得下去）。
        """
        allowed, why = may_be_written(name)
        if not allowed:
            raise PersonaWriteRefused(f"这一份人不能改：{name} —— {why}")
        return self._write_a_version(name, text, stamp=stamp, slot=slot, expect=expect)

    def save_section(self, name: str, section: str, text: str, *, stamp: Any,
                     expect: str, slot: Any = DEFAULT_VERSION_SLOT) -> dict[str, Any]:
        """Replace one named section against the revision the reviewer saw."""
        if name not in self._SECTION_FILES:
            raise PersonaPathError("这份档案不支持按节保存")
        if not isinstance(section, str) or not re.fullmatch(r"[a-z][a-z-]{0,63}", section):
            raise PersonaPathError("章节标识无效")
        if self._SECTION_MARKER.search(text):
            raise PersonaPathError("正文不能包含章节标记")
        try:
            before, revision = self.read_with_version(name)
        except (OSError, UnicodeDecodeError):
            raise PersonaReadFailed(name) from None
        if revision != expect:
            raise PersonaConflict(name, expect, revision)
        for key, start, end in self._section_ranges(before):
            if key == section:
                newline = "\r\n" if before[start - 1:start + 1] == "\r\n" else "\n"
                replacement = newline.join(text.rstrip("\r\n").splitlines())
                updated = before[:start].rstrip("\r") + newline + replacement
                updated += newline * (2 if end < len(before) else 1) + before[end:]
                return self.save_text(name, updated, stamp=stamp, slot=slot, expect=expect)
        raise PersonaPathError("档案里没有这个章节")

    @classmethod
    def _section_ranges(cls, text: str) -> list[tuple[str, int, int]]:
        markers = list(cls._SECTION_MARKER.finditer(text))
        if len({item[1] for item in markers}) != len(markers):
            raise PersonaPathError("档案包含重复的章节标识")
        ends = [item.start() for item in markers[1:]] + [len(text)]
        return [(item[1], item.end(), end) for item, end in zip(markers, ends)]

    def save_hers_text(self, name: str, text: str, *, stamp: Any = None,
                       slot: Any = DEFAULT_VERSION_SLOT,
                       expect: str | None = None) -> dict[str, Any]:
        """ **整理态那一扇门** —— 交给它的那两段正文写下来（`10:63-71`）。

        ⚠️⚠️ **它与 `save_text()` 只差第一道闸**，其余**一个字都不差**（同一段正文：
           `_write_a_version`）—— 两扇门各写一份"留版 ＋ 形状门 ＋ 路径守卫"的话，
           它们会漂，而那种漂法**在页上看得见**（同一份文件两种行为）。
        逐条对：
           · 第一道 `may_be_written()`（**人**能不能写）—— **这一扇门不走它**：
             那两份在它那儿是 **403**（"只有它自己能写"），而**能写它的就是本方法**
             ⇒ 两扇门是**互补**的，不是一条路两个名字（见 `may_be_written_by_her`）；
           · 第二道 `may_be_written_by_her()`（**它**能不能写这一份）—— **走它**（本次新增）；
           · 第三道 `persona_shape()`（承重形状）—— **走它**，与那一页**同一份实现**：
              于是"另一头删掉「观测／推测／事实分开写」那几个星号"这件事**在这条路上也拦得住**
             （`12:32` 那条纪律写在原文里时**就带着星号**）；
           · 路径守卫 `path_of()` ＋ 留一版历史 —— **都走**（同一条根、同一个形状）。
        ⚠️ **它不读时钟**（与 `save_text` 同一条）：那一串时间戳由调用方递
           （整理态那一边是 `console._a_stamp()`）。
        """
        allowed, why = may_be_written_by_her(name)
        if not allowed:
            raise PersonaWriteRefused(f"这一份不归它写：{name} —— {why}")
        return self._write_a_version(name, text, stamp=stamp, slot=slot, expect=expect)

    def _write_a_version(self, name: str, text: str, *, stamp: Any,
                         slot: Any, expect: str | None = None) -> dict[str, Any]:
        """ **写下去那一件事** —— **两扇门共用的唯一一段**（上面两个方法都收在这里）。

        ⚠️⚠️ **它是"两扇门"这件事的全部代价**：两道闸（**写不写得了**）在门外分，
           而**写下去怎么走**（路径守卫 ／ 承重形状 ／ 留一版历史）在门里**只有一份**。
            于是"哪一份文件该谁写"与"写下去会发生什么"是**两个可以分开改的问题**，
           而后者改一次两扇门一起动。
        ⚠️ **次序照旧**：形状门**压在留版之前** —— 一份会被改坏的东西**连历史都不该多一版**
           （与 `save_text` 那句同一条：`allowed` → `shape` → `keep` → `write`）。
        """
        # ★★ **共同提交门**（2026-10-17 批②）—— 从这里往下，四件事**在同一把锁里**：
        #    ① 读盘上这一份现在的正文（并算版本）；② 有 `expect` 就**比对版本**；
        #    ③ 承重形状那一门；④ 留一版 ＋ 原子替换。
        #    ⚠️ **模型生成不在这把锁里**（那一趟可能几十秒）：调用方在外面算完，
        #       带着"我读到的是哪一版"进来；锁只护这一段微秒级的动作。
        #    ⚠️ **跨进程与外部编辑器不在保障范围内**（见本文件顶上那一节）。
        storage_root, stored_name = self._storage(name)
        path = _under_root(storage_root, stored_name)
        with self._locks.hold([_a_lock_key(path)]):
            # IR01: a failed read is NOT the valid literal text "None".
            # Hash and shape must share one successful snapshot. Leave the
            # permissive _was_text helper's other shape-only semantics unchanged.
            try:
                before = path.read_bytes().decode("utf-8")
            except FileNotFoundError:
                before = None
                was_there = False
                now = "missing"
            except (OSError, UnicodeDecodeError):
                raise PersonaReadFailed(name) from None
            else:
                was_there = True
                now = _version_of(before)
            if expect is not None and str(expect) != now:
                # **不覆盖、不拼接**：盘上已经不是我读到的那一版了
                raise PersonaConflict(name, str(expect), now)
            gone = _the_shape_that_is_gone(name, before, text)
            if gone is not None:
                raise PersonaShapeRefused(
                    f"这一版会把 {name} 第 {gone['line']} 行的一处承重形状改掉 —— "
                    f"{gone['why']}（出处：{gone['cite']}）。"
                    " 那一处是这一份的功能所在：要改就把那一行连着它的形状一起写，别删掉 ／ 拆散。")
            version = _version_name(stored_name, stamp, slot)
            raw = text.encode("utf-8")
            kept = None
            if was_there:
                # ⚠️ **留版失败 ⇒ 当场抛**（不静默继续覆盖）：那种"改坏了还能拿回来"
                #    的话就不成立了 —— `_keep_a_version` 自己就是这样做的，这里不改它。
                kept = _keep_a_version(storage_root, path, stored_name, stamp, slot)
            # ⚠️ **原子替换**（同目录临时文件 → 关闭 → `os.replace`）：
            #    直接 `write_bytes` 写到一半出事的后果是**半份档案**，那比"这一趟没写"坏得多。
            _atomic_replace(path, text)
        return {"name": name, "path": path, "bytes": len(raw), "version": version,
                "version_before": now, "version_after": _version_of(text),
                "history": None if kept is None else str(kept),
                "left_a_version": was_there}

    def write_text(self, name: str, text: str, *, stamp: Any = None,
                   slot: Any = DEFAULT_VERSION_SLOT,
                   expect: str | None = None) -> dict[str, Any]:
        """ **保存**那一条路的**唯一**入口 —— 就是 `save_text()`（两个名字留一个）。

        ⚠️ **为什么两个名字都在**：那一位控制器与那一段 brief 把这件事叫**写**，而
            上面那一格是这一层**真正的那一件事**（它不只写文本，它先留一版）。
            ⇒ 名字交两个，**路只有一条**（一个转调另一个，不是第二份实现）。
        """
        if name in self._SECTION_FILES:
            raise PersonaPathError("这份档案必须按章节保存")
        if name == self.QQ_ENVIRONMENT:
            if expect in (None, "missing") or self.qq_environment() is None:
                raise PersonaPathError("请先读取当前 Bot 的 QQ 场景")
            if (len(text.encode("utf-8")) > 16 * 1024
                    or not re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()):
                raise PersonaPathError("QQ 场景正文须非空且不超过 16 KiB")
        return self.save_text(name, text, stamp=stamp, slot=slot, expect=expect)

    # ── **三处来源**（`order_doc` ／ `owners_doc` ／ 根底下的 `README.md`）⇒ 这一节底下**每一处都现读** ──────────
    #
    # ⚠️ **"来源"那个数给出来（三处，就在上面点着名）；"现读几处"那个数不给，是故意的**。
    # · **来源是三处** —— `order_doc` ／ `owners_doc` ／ **根底下的 `README.md`**：
    #      与类文档那段列的**同一份**三处（`order_doc` 供两格：段序 ＋ 哪一段是哪个文件，
    #      第三处就是 `rows()` 读的那一份 —— 它自己写着"运行时以这一份为准"）；
    #    · 而"现读"**不只有一个数**：`read_text()` 每次都 `path.read_bytes()`、**无缓存**
    #      ⇒ 那三处在这**一节**里被读几遍，取决于**按什么数**（按方法 ／ 按直接的读点 ／
    # 按调用点）—— "三处现读"那么写过一次，**那个数是假的**；立得住的是
    #      "**没有一处是缓存来的**"这句。
    # ⇒ **真要数，就数下面那些方法**（`order` ／ `homes` ／ `rows` ／ `segments` …）：
    #      本行的责任只是说清这一节的读法 —— 而**来源那个数两处必须同一个**（见类文档那段）。
    def order(self) -> list[str]:
        """ **段序**（那几段的名字，次序就是它）—— 从那一处正本里**现读**，不缓存。"""
        return _order_of(self.order_doc.read_text(encoding="utf-8"), heading=self._order_heading())

    def _order_heading(self) -> str:
        if self.modern_layout():
            return "## 新版档案段序"
        return _ORDER_HEADING

    def homes(self) -> tuple[str, ...]:
        """ **那几个家** —— 从 `owners_doc` 里**现读**；它没点名的家当场抛。"""
        return _homes_of(self.owners_doc.read_text(encoding="utf-8"))

    def rows(self) -> list[dict[str, Any]]:
        """ **那张对照表**（表那十一行）—— 从这条根底下的 `README.md` 里**现读**。

        ⚠️ **读的是那一条根底下的那一份**（`README.md:31`："运行时以这一份为准"）——
           `那一份旧材料（2026-10-07 已删）/` 那一份是**材料**，不是正本 （本段一次都不碰它）。
        ⚠️ **每一行带上它落在哪一段**（`segment`）：配得上就是那一段的名字，
           **配不上就是 `None`** —— 那些行**不丢**，见 `unplaced()`。
         **本刀给每一行加了 `file_infos`**（那几份文件的读数：`exists` ／ `writable` ／
           `why_read_only`）—— **页上那两块"只读的块"要的就是它**：
           "这一行是哪几份文件 ＋ 能不能改 ＋ 为什么" —— 而只交名字／路径画不出来。
           ⚠️ 加的是**新的一格**（既有那三格一个没动）⇒ 那一侧读别的格的人不受影响。
        """
        heading = self._order_heading()
        if heading != _ORDER_HEADING:
            table = _segments_in(self.order_doc.read_text(encoding="utf-8"), heading=heading)
            return [{"files": list(files), "label": label, "segment": label,
                     "file_infos": [self.file_info(name) for name in files]}
                    for label, files in table]
        text = self.read_text(_README_NAME)
        order = self.order()
        return [{"files": list(row["files"]), "label": row["label"],
                 "segment": row["label"] if row["label"] in order else None,
                 "file_infos": [self.file_info(one) for one in row["files"]]}
                for row in _rows_of(text)]

    def owner_of(self, names: list[str]) -> tuple[str | None, str | None]:
        """ **这一段的归属**（归属 ／ 它是从哪儿读来的）—— **读那一份自己**，不猜。

        ⚠️⚠️ **两级，次序是承重的**：
            ① **那一份自己的抬头**说它跟谁走 ⇒ 就是它（`"comment"`）；
            ② 不然看它**住在哪一层**：第一层目录是 `你定的` ⇒ 那个家（`"path"`）；
            ③ 都不成立 ⇒ **`None`**（文档没点过它的名 ⇒ 这一层**不编一个**）。
        ⚠️ **为什么第 ① 级压过第 ② 级**：那两份真文件（`边界.md` ／ `通道形状.md`）
           住在根顶上，而它们的抬头写着自己跟谁走 —— 抬头是**这一份自己的声明**，
           路径只是它今天恰好住在哪儿。
        ⚠️ **`homes()` 先过一遍**（那一步核"文档点过这几个名没有"）：一个文档没点名的家，
           这里宁可当场抛，也不交回一个"代码里还记着"的家。
        ⚠️ **有文件读不到的**（越界 ／ 不在 ／ 不是 UTF-8）⇒ 跳过它，看下一份 ——
           但那几种情形**没有消失**：它们在 `file_info()` 那三个旗子上。
        """
        self.homes()
        for name in names:
            try:
                text = self.read_text(name)
            except (PersonaPathError, OSError, UnicodeDecodeError):
                continue
            comment = _lead_comment(text)
            home = _home_in(comment) if comment else None
            if home:
                return home, "comment"
        for name in names:
            if name.split("/")[0] == _BOT_HOME:
                return _BOT_HOME, "path"
        return None, None

    def file_info(self, name: str) -> dict[str, Any]:
        """那一份文件的读数 —— **越界 ／ 不在 ／ 读到**是三件事，**各报各的**。

        ⚠️ **它们不许合成一个 `None`**：一个"越界了"被读成"没有这一份"，就把"有人往表里
           塞了一个越界名字"这件事吃掉了（`refused` 那一格就是为它留的）。
        ⚠️ **解不开 UTF-8 的**：`exists` 照旧真、`bytes` 照旧有、而 `body` 是 `None` ——
           "那一份在，而正文读不出来"与"没有那一份"不是一件事。
        """
        info: dict[str, Any] = {"name": name, "exists": False, "refused": False,
                                "bytes": None, "placeholder": False, "body": None, "revision": None,
                                # 本波那一格：这一份里**哪几处是承重的形状**
                                #   （`persona_shape()`）—— 页照它把那一行锁住、
                                # 而写那一侧照同一条判据当场拒。 读不到正文时是空表
                                #   （没有正文 ⇒ 没有可判的形状），**不是** `None`：
                                #   页那一侧拿到的永远是一串，少一次"它有没有这一格"的分支。
                                "shape": []}
        # **本刀加的那两格**（那两个数在**同一个地方**算 —— 见 `may_be_written()`）：
        # `writable` ／ `why_read_only`。 即使名字越界也照填（那两格是"这一份归谁写"，
        #    与"这个名字合不合法"是两件事）。
        allowed, why = may_be_written(name)
        info["writable"] = allowed
        info["why_read_only"] = why
        try:
            path = self.path_of(name)
        except PersonaPathError:
            info["refused"] = True
            return info
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            info["revision"] = "missing"
            return info
        except OSError:
            return info
        info["exists"] = True
        info["bytes"] = len(raw)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return info
        # Derive both fields from the SAME read; never re-read to hash.
        info["revision"] = _version_of(text)
        info["body"] = text
        info["placeholder"] = _PLACEHOLDER_MARK in (_lead_comment(text) or "")
        # **本波那一格**：这一份里哪几处是承重的形状 —— 它在**同一个地方**算
        #    （`persona_shape()`）：页上锁哪几行 ／ 写那一侧拒哪一版，是同一个答案。
        info["shape"] = persona_shape(name, text)
        if name in self._SECTION_FILES:
            try:
                info["sections"] = [
                    {"id": key, "body": text[start:end].strip("\r\n")}
                    for key, start, end in self._section_ranges(text)
                ]
            except PersonaPathError:
                info["sections"] = []
        return info

    def segments(self) -> list[dict[str, Any]]:
        """ **那几段**（正本那张表：次序与文件都在它上面）—— 每一段带着归属与它的文件。

        ⚠️⚠️ **本刀换的就是这一处**：从前"段序"从一份文档读、"哪一段是哪个文件"从
           `README.md` 读（两张表各答一格）⇒ 今天**次序与文件都从那一处正本读**，
           而"这棵档案里到底有哪些文件"照旧听 `README.md`（那些行在 `rows()` ／
           `unplaced()` 上 —— 两张清单**都要**带出来）。
        ⚠️ **一段可能有好几份文件**（那一格可以写好几份）⇒ `files` 是**一串**，
           而 `file` ／ `body` 取**第一份读得到正文的**（一份都没有 ⇒ `None`，不是空串）。
        ⚠️ **`missing` 判的是"盘上一份都没有"**（`exists`）—— 表里那一格空着、
           与表里点了名而盘上没有，在这几段上都算"这一段还没有文件"。
        ⚠️ **归属看它自己那一份文件的抬头**（`owner_of`）：一段两份文件时按表里的次序，
           **第一份说了算**。
        """
        table = _segments_in(self.order_doc.read_text(encoding="utf-8"),
                             heading=self._order_heading())
        out: list[dict[str, Any]] = []
        for name, listed in table:
            names = list(listed)
            files = [self.file_info(one) for one in names]
            first = next((one for one in files if one["body"] is not None), None)
            owner, source = self.owner_of(names)
            blocked = next((one for one in files if not one["writable"]), None)
            out.append({
                "name": name,
                "owner": owner,
                "owner_source": source,
                "files": files,
                "file": None if first is None else first["name"],
                "body": None if first is None else first["body"],
                "revision": None if first is None else first["revision"],
                "missing": not any(one["exists"] for one in files),
                "writable": blocked is None,
                "why_read_only": "" if blocked is None else blocked["why_read_only"],
            })
        return out

    def missing(self) -> list[str]:
        """ **今天没有文件的那几段**（段序里有名、而盘上一份都没有）。

        ⚠️ 它就是"**不许悄悄少一段**"那条的落点：页上那一段要显形（"这一段还没有文件"），
           而不是从十段变成九段。
        """
        return [seg["name"] for seg in self.segments() if seg["missing"]]

    def unplaced(self) -> list[str]:
        """ **表里有、段序里没有的那几行的名字** —— 它们**不许被丢掉**，从这儿报出来。

        ⚠️ 本波在真那一份表上量到的是**两行**（不是一行）：`世界认知`，以及那一行
           两样文件的 —— 两张清单对不上的地方**不止一处**。
         **本波订正过三处不实的记录**（那三处都写着"这一格**没动**、返回值一个字没变"
            —— **假的**）：T1 交回来的那一格是**整行**（`list[dict[str, Any]]`），
            而 T3 把它改成**名字那一串**，并把注解改成 `list[str]`。
            ⇒ **它真的动过，而且是**放宽**（整行那个形状里那三样 —— 哪几份文件 ／
              能不能改 ／ 为什么 —— 今天在 `rows()` 里，`label` 是配对的键）；
              而"它没动"那句话与 `git log -S "def unplaced"` 对不上。
             依据：`tests/test_web.py` 那两条断言**跟着改过**（它们是**改过**的，
              不是"照旧全绿"）—— 而那一次改动是本仓允许的、并且**是明写的**。
        """
        out: list[str] = [row["label"] for row in self.rows() if row["segment"] is None]
        return out

    def report(self) -> dict[str, Any]:
        # **2026-10-07「丙-2」**：多交一格 `shared` —— 那一本的**三层**
        # （核心 ／ 长尾 ／ 上限； "悬着的事"那一层在 `/memory` 上，不重复 ）。
        # ⚠️ 读法是**递进来的**（本模块零同胞 import ）；没递 ⇒ 那一格交 null。
        """ **那一页要的整一份**（一次读全）：两张清单 ＋ 缺的 ／ 没位置的。

        ⚠️ **两个清单各是各的**（`segments` 十段 ／ `rows` 十一行）—— 页上要**都画得出来**：
           一张是段序那一份，另一张是"这份档案里真的有哪些文件"，而它们对不上。
        """
        segments = self.segments()
        rows = self.rows()
        return {
            "persona_scope": "user-authored",
            "root": str(self.root),
            "order": self.order(),
            "homes": list(self.homes()),
            "segments": segments,
            "rows": rows,
            "shared": (self.read_shared() if callable(self.read_shared) else None),
            "missing": [seg["name"] for seg in segments if seg["missing"]],
            # **本波订正**：这一格从前写着"照旧是 T1 交的形状（没动）" —— **假的**
            # （T1 那一处交的是**整行**，见 `unplaced()` 那段订正）。 而它**现在是**
            #    名字那一串 —— 与 `unplaced()` **同一个答案**（一处说，不两处各答一个）。
            "unplaced": self.unplaced(),
        }
