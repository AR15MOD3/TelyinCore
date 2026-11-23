"""市场那一半的**第二个来源：GitHub**（2026-10-05 作者："直接放弃 npm 好了，转用 github"）。

 为什么有它（作者的原话是这套东西的来处）：
   「**这个项目我是打算开源的**，也就是说有其他人会使用，其他人也想做插件的话，
     可以用 npm 上传，这样的话我也能用他的插件，**其他任何人都能搜到他做的插件**。」
   ⇒ npm 那一条**卡在注册**（Cloudflare 拦了那个页面，而 npm 又把命令行开户关了 ）
   ⇒ 而 **GitHub 那条三条全满足**（建仓库就行 ⇒ **不需要审核** ＋ 归档链接能直接下 ＋
      **`topic:` 搜索任何人都能用** ）。

⚠️⚠️ **两个"关键字"是两个东西**（别混 —— 作者问过一次，这是那次说清的落点）：
   · **`core-infinity`** 写进**插件包里** `package.json` 的 `keywords` ——
      **它是"门槛"**：装完由 `market` 那一层查它（那一条**照旧**守着 ）；
   · **`core-infinity-plugin`** 打在**GitHub 仓库上**（topic ）——
      **它是"报名"**：市场搜的就是它 。
   ⇒ 一个在**包里**、一个在**仓库上**；一个管**装得上**、一个管**搜得到** 。
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

#: **那一格话题**（作者 2026-10-05 定）：仓库打了它 ⇒ 市场搜得到 。
TOPIC = "core-infinity-plugin"

#: GitHub 那几个口（ 都只要**公开读**，不需要任何 token ）。
SEARCH_URL = "https://api.github.com/search/repositories"
REPOS_URL = "https://api.github.com/repos"
TAGS_PER_REPO = 1

#: 那份"你是谁"（ 逐字照 `web.py::USER_AGENT` —— 本仓那一条规矩 ）。
USER_AGENT = "core-kernel/1.0 (+local)"

_gh_cache: dict[str, Any] = {"at": 0.0, "found": None}
_last_error = ""
_retry_after = 0.0


def _ask(url: str, proxy=None) -> Any:
    """ 打一次 GitHub（ 成法照 `web.py::_catalog_sees`：UA ＋ 系统代理 ＋ TLS 校验 ＋ 超时 ）。

    ⚠️ **不吞错**（与页上那几格不同）：那几格是"刷页顺手问一下"、拉不到就写"没拉到" ；
       而这是**市场那一半** ⇒ 拉不到要**交回一句能读的话**（由调用方决定怎么说 ）。
    """
    from .download_sources import github_json
    def expected(value):
        if url.startswith(SEARCH_URL):
            return isinstance(value, dict) and isinstance(value.get("items"), list)
        return isinstance(value, list)

    return github_json(url, {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
                       proxy, expected=expected)


def _an_archive(owner: str, repo: str, branch: str, proxy=None) -> tuple[str, str]:
    """ **一条能下载的归档链接**（ 有 tag 就用 tag，没有就退回那个分支 ）。

    交回 `(链接, 用的是哪个)`： 第二个**要说出来** —— "从分支拿的"与"从发布拿的"
    不是一回事（ 前者会**随仓库变** ，而用户有权知道手里那一份是哪种 ）。

    ⚠️ **tag 那一次可能失败**（没有 tag 的仓库、或者那一跳被限流 ）⇒ **退回分支**
       —— 而**不许静默**：第二种退路**照实说出来**（走 `said` 那一格 ）。
    """
    try:
        url = f"{REPOS_URL}/{owner}/{repo}/tags?per_page={TAGS_PER_REPO}"
        tags = _ask(url, proxy) if proxy else _ask(url)
        if isinstance(tags, list) and tags:
            name = str(tags[0].get("name") or "")
            if name:
                # **版本就是那个 tag 名**（2026-10-05 作者给的排法：「版本：v0.1.0」）——
                # 原先是"发布 v0.1.0"那种带前缀的话 ⇒ 页上没法直接当版本用 。
                return (f"https://github.com/{owner}/{repo}/archive/refs/tags/{name}.tar.gz", name)
    except Exception: # noqa: BLE001 —— 退回分支（而下面说出来 ）
        pass
    # 退回分支那一种：**照样把话说全**（"这一份会随仓库变" ）—— 而它**不是**一个版本号
    return (f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.tar.gz",
            f"{branch}（分支，没打 tag）")


from threading import Lock
_fetch_lock = Lock()
_warm_lock = Lock()


def _fresh(cache: Any = None, proxy=None) -> dict[str, Any]:
    observed = _gh_cache['at']
    with _fetch_lock:
        if _gh_cache['at'] != observed and _gh_cache['found'] is not None:
            return _gh_cache['found']
        return _fetch_fresh(cache, proxy) if proxy else _fetch_fresh(cache)


def _fetch_fresh(cache: Any = None, proxy=None) -> dict[str, Any]:
    """ **真去打一次 GitHub**，交回新那一份，并**把它落盘**（`cache` 递了就落 ）。"""
    import json
    import time

    query = urllib.parse.quote(f"topic:{TOPIC}")
    url = f"{SEARCH_URL}?q={query}&per_page=30&sort=updated"
    rows: list[dict[str, Any]] = []
    got = _ask(url, proxy) if proxy else _ask(url)
    for one in (got.get("items") or []):
        full = str(one.get("full_name") or "")
        if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", full) or full.endswith(("/.", "/..")):
            continue
        owner, repo = full.split("/", 1)
        branch = str(one.get("default_branch") or "main")
        where, how = _an_archive(owner, repo, branch, proxy) if proxy else _an_archive(owner, repo, branch)
        rows.append({
            "name": full.split("/")[-1],
            "full": full,
            "version": how,
            "description": str(one.get("description") or ""),
            "author": owner,
            "date": str(one.get("updated_at") or "")[:10],
            "keywords": list(one.get("topics") or []),
            "source": "github",
            "url": where,
        })
    answer = {"found": rows, "wired": True, "where": url,
              "said": (f"GitHub 上打了「{TOPIC}」这一格的仓库有 {len(rows)} 个。"
                       if rows else f"GitHub 上还没有仓库打上「{TOPIC}」这一格。"),
              "at": time.time(), "stale": False}
    global _last_error, _retry_after
    _last_error = ""
    _retry_after = 0.0
    _gh_cache["at"] = float(answer["at"])
    _gh_cache["found"] = answer
    if cache is not None: # 落盘（下一次开机立刻有东西可显示 ）
        try:
            Path(cache).write_text(json.dumps(answer, ensure_ascii=False),
                                   encoding="utf-8", newline="")
        except OSError:
            pass
    return answer


def _on_disk(cache: Any) -> dict[str, Any] | None:
    """ **上一次问到的**（`cache` 那一份盘上的 ）—— 读不到 ⇒ `None`（不抛 ）。"""
    if cache is None:
        return None
    import json

    try:
        got = json.loads(Path(cache).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) and "found" in got else None


#: ⚠️ **同一个时刻只许一个预热线程**（2026-10-05）：不加这个闸，每次刷页都会开一个 ——
#: 而"拿到旧那一份就回、同时后台重问"那条路**每次请求都会走到** ⇒ 那就是一场线程风暴 。
_warming = False


def warm(cache: Any = None, proxy=None) -> None:
    """ **在后台先问一次**（2026-10-05 作者："重启之后插件市场那会先空着，有没有办法提前刷新好"）。

     起来那一刻就叫它一次 ⇒ 页打开时**缓存已经热了** （ 而且它**不挡着启动**：
      这是一个 `daemon` 线程，主线程照旧往下走 ）。
    """
    import threading

    global _warming
    with _warm_lock:
        if _warming:
            return
        _warming = True

    def one() -> None:
        global _warming, _last_error, _retry_after
        try:
            _fresh(cache, proxy) if proxy else _fresh(cache)
        except Exception as exc:
            import time
            from .plugin_network import error_text
            _last_error = error_text(exc, SEARCH_URL)
            _retry_after = time.time() + 30.0
        finally:
            with _warm_lock:
                _warming = False

    try:
        threading.Thread(target=one, name="market-warm", daemon=True).start()
    except BaseException:
        with _warm_lock:
            _warming = False
        raise


def found(*, now: float | None = None, ttl: float = 300.0, cache: Any = None,
          none_yet: bool = False, proxy=None) -> dict[str, Any]:
    """ **搜 GitHub 上打了那一格话题的仓库**（那是"任何人都搜得到"那一半 ）。

    交回 `{found, wired, where, said}`（ 四格形状与市场那一格**一模一样** —— 页那一边
    只用同一个画法 ； 而每一条多一格 `source: "github"` 与一个 `wherefrom` ）。

    ⚠️ **每一条带的是"归档链接"**（不是包名 ）—— 因为"装"那条路对两个来源**不一样**：
        今天只有 GitHub ⇒ `{"url": 归档链接}`
       ⇒ 所以每条**必须标明它从哪儿来**（不然页上会拿包名去装一个 GitHub 的东西 ）。
    ⚠️ **缓存**（`ttl` 秒 ）：GitHub 那个口**匿名限流很紧**（一分钟几十次 ）⇒
       刷页不该每次都真打 。
    """
    import time

    at = time.time() if now is None else float(now)
    if _gh_cache["found"] is not None and 0 <= at - float(_gh_cache["at"]) < ttl:
        return _gh_cache["found"]
    # **先看看有没有"上一次问到的"**（内存 ／ 盘上 ）——
    # 有 ⇒ **立刻把它交出去**（页上马上有东西 ）， 而**同时**在后台重问一次
    # （"提前刷新好"那一件事就落在这一句上 ）。
    old = _gh_cache["found"] or _on_disk(cache)
    if old is not None:
        if at >= _retry_after:
            warm(cache, proxy) if proxy else warm(cache)
        return {**old, "stale": True, "error": _last_error}
    # 一次都没有过（头一回开机）⇒ **只能等着**（而那时预热线程多半已经在路上了 ）
    url = f"{SEARCH_URL}?q={urllib.parse.quote(f'topic:{TOPIC}')}&per_page=30&sort=updated"
    try:
        return _fresh(cache, proxy) if proxy else _fresh(cache)
    except Exception as exc:
        from .plugin_network import error_text
        return {"found": [], "wired": False, "where": url, "stale": False,
                "said": error_text(exc, url)}
