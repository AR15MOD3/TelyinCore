"""插件市场那两件事 —— **装**（**从一条网址** ／ **从本机目录**）。作者 2026-10-05："让市场真能装"。

 插件安装流程：

```
· 装：向 `extensions/` 里加一份 —— 那边是 `pnpm add`，这边是"拉 tarball ＋ 解开 ＋ 落进去" ；
· **安装前先 check**：那边逐字写着「控制台安装前先调 `POST /api/extensions/check`：
  **只读 manifest**（本机目录读 `package.json`），类别与所选类别不符、
  声明不合格时**拒绝**，**不导入包代码**」 ⇒ 这一层**照它那条办** ；
· 本机目录那种（那边叫 `link:`）—— **它是能立刻验的那一条**
  （ 而"从哪儿拿"由**那个网址**定 —— 2026-10-05 起市场那一头是 GitHub 的话题搜索 ）。
```

⚠️⚠️ **两件事分开说**：
   · **"装上了"** ＝ 那份东西**在 `extensions/` 里** （这一层管的就是这个）；
   · **"跑起来了"** ＝ `loaded` （那由**那个开关**管 —— 装完**不自动开**
     —— 照那边：装完要"重启进程后加载" ／ 而"开不开"是你的意思 ）。
"""

from __future__ import annotations

import io
import os
import json
import shutil
import tempfile
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

#: 那一句"你是谁"—— **逐字照 `web.py::USER_AGENT`**（`core-kernel/1.0 (+local)` ）。
#: ⚠️ 那个名字在本仓**只有那一个**（`web.py:245-247` 写着"与 `transport.py` 必须逐字相同"）。
USER_AGENT = "core-kernel/1.0 (+local)"

UNPACK_CEILING = 64 * 1024 * 1024


def validate_plugin_name(name: str) -> str:
    """A portable single Windows path segment; reject rather than sanitize."""
    if (not isinstance(name, str) or not name or name in (".", "..")
            or name.endswith((".", " "))
            or any(ord(c) < 32 or c in '\\/:*?"<>|' for c in name)):
        raise ValueError("插件名称必须是安全的单路径段")
    stem = name.split(".", 1)[0].rstrip(" ").upper()
    if (stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
            or stem in {p + n for p in ("COM", "LPT") for n in "123456789¹²³"}):
        raise ValueError("插件名称不能使用 Windows 设备名")
    return name


def plugin_destination(base: Path, name: str) -> Path:
    base = Path(base).resolve()
    target = base / validate_plugin_name(name)
    if target.resolve().parent != base or target.is_symlink():
        raise ValueError("插件最终目录越界或为链接")
    return target


def _archive_parts(name: str) -> tuple[str, ...]:
    # TAR uses '/', irrespective of the host OS. Backslashes are not separators.
    parts = tuple(name.split("/"))
    for part in parts:
        validate_plugin_name(part)
    return parts


def install(root: Any, name: str) -> dict[str, Any]:
    """No trusted name-to-package resolver exists: fail without fetching."""
    return {"ok": False, "where": "", "name": str(name or ""),
            "said": "暂不支持仅凭名称安装；请提供经校验的本机目录或 http(s) 包网址。"}


def _fetch(url: str, proxy=None) -> bytes:
    """ 打一次（ 成法照 `web.py::_catalog_sees`：UA ＋ 系统代理 ＋ TLS 校验 ＋ 超时 ）。

    ⚠️ **不吞错**（ 与市场那两格不同）：那两格是"刷页时顺手问一下"，拉不到就写"没拉到" ；
       而**装**是一个**动作** ⇒ 它失败了要**说出来**（抛上去，由调用方交一句人话 ）。
    """
    import re
    from .download_sources import github_json, github_routes, github_route_text, open_public, TRANSFER_ERRORS
    from .plugin_network import github_destination
    headers = {"User-Agent": USER_AGENT}

    def read(target, via):
        request = urllib.request.Request(target, headers=headers)
        if via:
            from .plugin_network import opener
            connection = opener(via).open(request, timeout=30)
        else:
            connection = open_public(request, 30)
        with connection as resp:
            raw = resp.read(UNPACK_CEILING + 1)
        if len(raw) > UNPACK_CEILING:
            raise ValueError("下载包超过大小上限")
        return raw

    # Arbitrary user-provided URLs keep their original origin and proxy policy.
    if not github_destination(url):
        if proxy:
            raise urllib.error.URLError("插件市场代理仅用于 GitHub")
        from .plugin_network import opener
        with opener().open(urllib.request.Request(url, headers=headers), timeout=30) as response:
            raw = response.read(UNPACK_CEILING + 1)
        if len(raw) > UNPACK_CEILING:
            raise ValueError("下载包超过大小上限")
        return raw

    match = re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+)/archive/refs/(heads|tags)/([\w./-]+)\.tar\.gz", url, re.ASCII)
    pinned = re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+)/archive/([a-f0-9]{40})\.tar\.gz", url, re.ASCII)
    routes = github_routes(url, proxy, official_only=not bool(match or pinned))
    failures = []
    for index, (target, via) in enumerate(routes):
        try:
            raw = read(target, via)
            if target == url:
                return raw
            # Mirror bytes still require an independent official file inventory.
            if pinned:
                repo, ref = pinned.groups()
                tree_ref = ref
            else:
                repo, kind, ref = match.groups()
                tree_ref = f"refs/{kind}/{ref}"
            api = f"https://api.github.com/repos/{repo}/git/trees/{tree_ref}?recursive=1"
            tree = github_json(api, headers, proxy, official_only=True)
            if not isinstance(tree, dict) or tree.get("truncated") is not False:
                raise ValueError("未取得完整插件文件清单")
            expected = {row["path"]: row["sha"] for row in tree["tree"] if row["type"] == "blob"}
            import tempfile
            import hashlib
            with tempfile.TemporaryDirectory() as tmp:
                stage = Path(tmp)
                _unpack(raw, stage)
                actual = {}
                for file in stage.rglob("*"):
                    if file.is_file():
                        blob = file.read_bytes()
                        actual[file.relative_to(stage).as_posix()] = hashlib.sha1(
                            b"blob " + str(len(blob)).encode() + b"\0" + blob).hexdigest()
            if actual and actual == expected:
                return raw
            raise ValueError("镜像插件文件与官方清单不符")
        except (*TRANSFER_ERRORS, tarfile.TarError, KeyError, TypeError) as exc:
            failures.append(github_route_text(target, url, via, exc))
            if index == len(routes) - 1:
                raise ValueError("；".join(failures)) from exc


def _unpack(raw: bytes, into: Path) -> None:
    """ **把那一份 tarball 解开**（ 两边都是 `.tgz` ＝ tar ＋ gzip —— npm 那种包是这样，GitHub 的归档也是这样 ）。

    ⚠️⚠️ **两处守卫**（外网来的东西）：
       · **一个字节都不许写到 `into` 外面** —— `..` 与绝对路径**当场拒**
         （照本仓那条"不许越界"；那边的包里写 `../../etc/x` 是**做得到**的 ）；
       · **解出来的总量有上界**（`UNPACK_CEILING` ）—— 一个"说 9 KB、解成 9 GB"的包
         会把这一台机器拖死 。
    """
    into.mkdir(parents=True, exist_ok=True)
    base = into.resolve()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as box:
        # **先看清那一层**（**通用**的剥法 —— 2026-10-05 真跑抓到的 ）：
        # · npm 那种包里，顶层是 **`package/`** ；
        # · GitHub 的归档里，顶层是 **`<仓库>-<版本>/`** （ 名字不一样 ）。
        # ⇒ **不是"按名字剥"**，而是**看结构**： **所有文件都在同一个顶层目录下**
        # ⇒ 剥掉那一层 ； 否则**一个都不剥**（ 免得误剥一个本来就有目录结构的包 ）。
        files = [m for m in box.getmembers() if m.isfile()]
        entries = [(m, _archive_parts(m.name)) for m in files]
        tops = {parts[0] for _, parts in entries}
        strip_top = len(tops) == 1 and all(len(parts) > 1 for _, parts in entries)
        total = 0
        planned = []
        for one, parts in entries:
            short = parts[1:] if strip_top else parts
            target = base.joinpath(*short).resolve()
            if target == base or base not in target.parents:
                raise ValueError("归档剥层后最终路径越界")
            total += int(one.size)
            if total > UNPACK_CEILING:
                raise ValueError("那一包解出来太大了（超过上界）")
            planned.append((one, short))
        for one, short in planned:
            target = base.joinpath(*short).resolve()
            if target == base or base not in target.parents:
                raise ValueError("归档最终写入路径越界")
            body = box.extractfile(one)
            if body is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body.read())


def install_url(root: Any, url: str, *, proxy_root=None, expected_name="", publisher=None) -> dict[str, Any]:
    """ **从一个网址装**（2026-10-05 作者："别人没法发没法搜那就没用了" ⇒ 补这一条）。

     **它不依赖任何 registry**（也不依赖"搜" ）—— 谁把那份 `.tgz` 放在哪儿都行：
      自己的网站 ／ GitHub Release 的下载链接 ／ 一个对象存储 ／ 甚至内网那一台 。

    ⚠️ **它照旧要过那一道门槛**（ 不因为它是从一条网址来的就放行 ）：
       解出来之后**看它 `package.json` 里有没有 `KEYWORD`** ⇒ 没有 ⇒ **撤掉 ＋ 说清楚**
       （ **撤掉那一半是这一条独有的**：市场那一条是**先拿到链接**才装 ；
        而网址这条只能"先下再说" ⇒ 所以**下了之后要能反悔** ）。
    ⚠️ **只能 http(s)**（`file:` ／ `ftp:` 那一类**当场拒** —— 那会让这一格变成
       "读本机任意文件"的一条口子 ； 本机那一份走 `install_from` ）。
    """
    import shutil
    # Internal pinned installs provide a publisher after validating the Release.
    if publisher is None:
        import re
        from urllib.parse import unquote
        from .plugin_versions import install_release
        from .plugin_updates import repository
        repo=repository(url)
        if repo:
            match=re.fullmatch(r'https://github\.com/[\w.-]+/[\w.-]+/archive/refs/tags/(.+)\.tar\.gz',url)
            if not match: return {'ok':False,'said':'插件市场仅支持正式 Release，不支持开发分支或裸提交安装'}
            try:
                return install_release(root,repo,unquote(match[1]),expected_name=expected_name,proxy_root=proxy_root)
            except Exception as exc:
                return {'ok':False,'said':'正式版本安装失败：'+str(exc)}

    from .plugins import KEYWORD, MANIFEST, the_place

    said = str(url or "").strip()
    if not said.startswith(("http://", "https://")):
        return {"ok": False, "where": "", "name": "",
                "said": "那个网址得是 http(s) 的（本机那一份请用「从目录装」）。"}
    # URL path is untrusted; staging uses an exclusive random directory.
    import tempfile
    home = the_place(root)
    home.mkdir(parents=True, exist_ok=True)
    folder = "装来的那一份"
    stage = Path(tempfile.mkdtemp(prefix=".装到一半-", dir=home))
    place = home / folder
    try:
        from .plugin_network import github_destination
        from .download_sources import public_network
        proxy = (public_network(proxy_root, 'market')
                 if proxy_root is not None and github_destination(said) else None)
        raw = _fetch(said, proxy) if proxy else _fetch(said)
        # **先解到一个"临时那一格"** ⇒ 读它 `package.json` 的 `name` ⇒ 再挪到真名下
        _unpack(raw, stage)
        manifest = json.loads((stage / MANIFEST).read_text(encoding="utf-8"))
        from .plugins import incompatible_plugin
        reason = incompatible_plugin(manifest)
        if reason:
            raise ValueError(reason)
        named = validate_plugin_name(str(manifest.get("name") or folder))
        place = plugin_destination(home, named)
        if expected_name and named != expected_name:
            raise ValueError("所选版本的插件名称不匹配")
        words = [str(w) for w in (manifest.get("keywords") or [])]
        if KEYWORD not in words:
            raise ValueError(f"那一份里没有「{KEYWORD}」这个关键字")
        entry = stage.joinpath(*_archive_parts(str(manifest.get("main") or "index.py")))
        if not entry.is_file():
            raise ValueError("插件入口文件缺失，安装未发布")
        if place.exists() and publisher is None:
            shutil.rmtree(stage, ignore_errors=True)
            return {"ok": False, "where": str(place), "name": named,
                    "said": f"「{named}」那一格已经在了（这一层不覆盖别人的东西）。"}
        from .plugin_uninstall import publish_package
        (publisher or publish_package)(root, named, stage, place)
    except Exception as exc: # noqa: BLE001 —— 出错就说错
        shutil.rmtree(stage, ignore_errors=True)
        from .plugin_network import error_text
        return {"ok": False, "where": str(place), "name": folder,
                "said": "插件安装失败：" + error_text(exc, said)}
    return {"ok": True, "name": named, "where": str(place), "url": said,
            "said": f"「{named}」装好了（从那个网址来的 还没开 ）。"}


def install_from(root: Any, source: Any) -> dict[str, Any]:
    """ **从本机的一个目录装**（照那边那条 `link:` 式 ）。

     **它是能立刻验的那一条**（ 不必等任何人发布什么 ）：
       把一份写好的插件目录**收进 `extensions/`** —— 而**关键字那一条照判**
       （ 不只是本机的东西就放行 —— 那条秩序对两边一样 ）。

    ⚠️ **只收那些文件**（`package.json` ／ 它 `main` 指的那一份 ／ 别的 `.py` ）——
       不跟着符号链接跑出去 （照本仓那条"不许越界"）。
    """
    from .plugins import KEYWORD, MANIFEST, the_place

    src = Path(source)
    if src.is_symlink():
        raise ValueError("插件源目录不能是链接")
    src = src.resolve()
    if not (src / MANIFEST).is_file():
        return {"ok": False, "said": f"那一份里没有 {MANIFEST}（那不像一个插件）。",
                "where": "", "name": ""}
    manifest = json.loads((src / MANIFEST).read_text(encoding="utf-8"))
    from .plugins import incompatible_plugin
    reason = incompatible_plugin(manifest)
    if reason:
        return {"ok": False, "said": reason, "where": "", "name": manifest.get("name", "")}
    words = [str(w) for w in (manifest.get("keywords") or [])]
    if KEYWORD not in words:
        return {"ok": False, "where": "", "name": str(manifest.get("name") or ""),
                "said": f"「{manifest.get('name') or src.name}」不是给我们写的"
                        f"（它的关键字里没有「{KEYWORD}」）。"}
    named = str(manifest.get("name") or src.name)
    try:
        folder = validate_plugin_name(named)
        place = plugin_destination(the_place(root), folder)
    except ValueError as exc:
        return {"ok": False, "where": "", "name": named, "said": str(exc)}
    if place.exists():
        return {"ok": False, "name": named, "where": str(place),
                "said": (f"「{folder}」目录已存在，请检查 {place / MANIFEST} 是否完整；"
                         "插件页的损坏目录列表可查看读取结果。")}
    place.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".install-", dir=place.parent))
    try:
        copied = 0
        for one in sorted(src.rglob("*")):
            if one.is_symlink() or not one.is_file():
                continue
            rel = one.relative_to(src)
            if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
                continue
            if any(parent.is_symlink() for parent in one.parents if parent != src.parent):
                continue
            parts = _archive_parts(rel.as_posix())
            target = stage.joinpath(*parts).resolve()
            if stage.resolve() not in target.parents:
                raise ValueError("插件最终写入路径越界")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(one.read_bytes())
            copied += 1
        entry = stage.joinpath(*_archive_parts(str(manifest.get("main") or "index.py")))
        if not (stage / MANIFEST).is_file() or not entry.is_file():
            raise ValueError("插件清单或入口文件缺失，安装未发布")
        from .plugin_uninstall import publish_package
        publish_package(root, named, stage, place)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {"ok": True, "name": named, "where": str(place),
            "said": f"「{named}」装好了（收到 {copied} 个文件 还没开 ）。"}
