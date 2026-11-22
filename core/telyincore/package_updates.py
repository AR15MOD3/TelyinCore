"""Release numbers for the Windows and Linux deployment packages."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import time
from threading import Thread

_VERSION = re.compile(r"beta\.((?:0|[1-9]\d{0,8})(?:\.(?:0|[1-9]\d{0,8})){2,3})", re.ASCII)


def version_key(value: object) -> tuple[int, ...]:
    match = _VERSION.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError("无法识别分发包版本号")
    parts = tuple(map(int, match[1].split(".")))
    return parts + (0,) * (4 - len(parts))


def installed_package(root: Path, platform: str) -> str:
    package = json.loads((root / "candidate.json").read_text(encoding="utf-8-sig"))
    schemas = {"win": "windows-core-acceptance-v1", "linux": "linux-core-acceptance-v1"}
    if not isinstance(package, dict) or package.get("schema") != schemas.get(platform):
        raise ValueError("安装包平台信息不匹配")
    current = package.get("version")
    version_key(current)
    return current


# Set this once for official builds. Every installation then uses the same source.
RELEASE_REPOSITORY = "AR15MOD3/TelyinCore"
_TAG = re.compile(r"(win|linux)-(beta\.[0-9.]+)", re.ASCII)
CHECK_INTERVAL = 30 * 60
_PAGE_SIZE = 50


def github_latest(repository: str, platform: str) -> dict:
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("发行仓库名称无效")
    latest = None
    deadline = time.monotonic() + 5
    for page in range(1, 11):
        url = f"https://api.github.com/repos/{repository}/releases?per_page={_PAGE_SIZE}&page={page}"
        req = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "TelyinCore-release-check"})
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("GitHub 检查超时")
        with urlopen(req, timeout=remaining) as reply:
            body = reply.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise ValueError("GitHub 发布列表过大")
        releases = json.loads(body)
        if not isinstance(releases, list):
            raise ValueError("GitHub 发布列表格式无效")
        for release in releases:
            if not isinstance(release, dict) or release.get("draft") is not False:
                continue
            tag = release.get("tag_name")
            match = _TAG.fullmatch(tag) if isinstance(tag, str) else None
            if not match or match[1] != platform or not _VERSION.fullmatch(match[2]):
                continue
            version = match[2]
            if latest is None or version_key(version) > version_key(latest["latest"]):
                latest = {"latest": version, "releaseUrl": f"https://github.com/{repository}/releases/tag/{quote(tag)}"}
                latest["assets"] = [a["name"] for a in release.get("assets", [])
                                    if isinstance(a, dict) and isinstance(a.get("name"), str)
                                    and a.get("state") == "uploaded" and a.get("size", 0) > 0]
                latest["native_assets"] = [a["name"] for a in release.get("assets", [])
                                           if isinstance(a, dict) and a.get("label") == "core-online-update-v1"
                                           and a.get("name") in latest["assets"]]
        if len(releases) < _PAGE_SIZE:
            if latest is None:
                raise ValueError("仓库中还没有此平台的公开发布记录。")
            return latest
    raise ValueError("发布历史过长，本次未能核对完整。")


def overlay_name(platform, current, latest):
    return f"Telyincore_{platform}_{current}_to_{latest}_update.zip"


def overlay_url(repository, platform, current, latest):
    name = overlay_name(platform, current, latest)
    return f"https://github.com/{repository}/releases/download/{platform}-{latest}/{name}"


class PackageUpdates:
    def __init__(self, root, platform, repository, fetch=github_latest):
        from threading import Lock
        self.root = root
        self.platform = platform
        self.repository = repository
        self.fetch = fetch
        self._lock = Lock()
        self._cache = None
        self._checked_at = 0.0
        self._checked_wall = 0.0

    def installation(self):
        from .online_install import pending
        row = pending(self.root)
        return {"pendingVersion": row["version"]} if row else {}

    def prepare_install(self):
        from .online_install import stage
        return stage(self)

    def restart_for_update(self, port):
        from .online_install import start_restart
        start_restart(self.root, port)

    def check(self, force=False):
        from urllib.error import HTTPError
        from http.client import HTTPException

        if not force:
            self._from_launcher()
        with self._lock:
            if self._cache is not None:
                if time.monotonic() - self._checked_at < (5 if force else CHECK_INTERVAL):
                    return self._cache
            result = {"platform": self.platform, "current": None}
            try:
                current = installed_package(self.root, self.platform)
            except (OSError, ValueError):
                result.update(status="unknown", message="此目录缺少可核对的平台发行版本。")
            else:
                result["current"] = current
                if not self.repository:
                    result.update(status="unconfigured", message="这份发行包尚未指定官方 GitHub 仓库。")
                else:
                    try:
                        release = self.fetch(self.repository, self.platform)
                        local = version_key(current)
                        remote = version_key(release["latest"])
                        status = "available" if remote > local else "ahead" if local > remote else "current"
                        result.update({k: v for k, v in release.items() if k not in ("assets", "native_assets")}, status=status)
                        if status == "available" and overlay_name(self.platform, current, release["latest"]) in release.get("assets", ()):
                            result["installable"] = overlay_name(self.platform, current, release["latest"]) in release.get("native_assets", ())
                            result["downloadUrl"] = overlay_url(self.repository, self.platform, current, release["latest"])
                    except HTTPError as exc:
                        message = ("GitHub 暂时限制了检查频率，请稍后重试。" if exc.code in (403, 429)
                                   else f"暂时无法读取发行仓库（HTTP {exc.code}）。")
                        result.update(status="unavailable", message=message)
                    except (OSError, HTTPException, ValueError) as exc:
                        result.update(status="unavailable", message=str(exc) if type(exc) is ValueError
                                      else "暂时无法连接 GitHub，请稍后重试。")
            self._cache = result
            self._checked_at = time.monotonic()
            self._checked_wall = time.time()
            return result

    def launcher_check(self):
        """Hand the launcher's fresh platform/version result to the Bot process."""
        import tempfile
        result = self.check(force=True)
        if result["current"] and self.repository:
            path = self.root / "runtime" / "launcher-release-check.json"
            temporary = None
            try:
                path.parent.mkdir(exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                                 delete=False) as f:
                    temporary = Path(f.name)
                    json.dump([time.time(), self.repository, self.platform, result["current"], result.get("latest"), bool(result.get("downloadUrl")), bool(result.get("installable"))], f)
                temporary.replace(path)
            except OSError:
                pass  # Read-only installations still report the check.
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
        return self.describe(result)

    def _from_launcher(self):
        # Only version numbers cross the process boundary. Derive links locally.
        path = self.root / "runtime" / "launcher-release-check.json"
        try:
            with path.open("rb") as f:
                stamp, repository, platform, current, latest, *asset = json.loads(f.read(513))
            age = time.time() - stamp
            if not (0 <= age < (CHECK_INTERVAL if self._cache is not None else 120) and repository == self.repository and platform == self.platform
                    and current == installed_package(self.root, self.platform)):
                return
            result = {"current": current, "platform": platform}
            if latest is None:
                result.update(status="unavailable", message="启动自检暂未取得公开版本，可手动重试。")
            else:
                local, remote = version_key(current), version_key(latest)
                result.update(latest=latest,
                              status="available" if remote > local else "ahead" if local > remote else "current",
                              releaseUrl=f"https://github.com/{repository}/releases/tag/{platform}-{latest}")
                if result["status"] == "available":
                    if not asset:
                        return  # Earlier launchers did not check the release assets.
                    if asset[0] is True:
                        result["installable"] = len(asset) == 2 and asset[1] is True
                        result["downloadUrl"] = overlay_url(repository, platform, current, latest)
            checked_at = time.monotonic() - age
            with self._lock:
                if self._cache is None or stamp > self._checked_wall:
                    self._cache = result
                    self._checked_at = checked_at
                    self._checked_wall = stamp
        except (OSError, TypeError, ValueError):
            return

    @staticmethod
    def describe(result):
        status = result["status"]
        if status == "available":
            return f"版本自检 · 发现新版 {result['latest']}（本机 {result['current']}）"
        if status == "current":
            return f"版本自检 · {result['current']} 已是最新公开版本"
        if status == "ahead":
            return f"版本自检 · 本机 {result['current']}，公开版本 {result['latest']}"
        return "版本自检 · " + result["message"]

    def wake(self):
        def report():
            print("[check] " + self.describe(self.check(force=True)), file=sys.stderr, flush=True)
        Thread(target=report, name="core-wake-update", daemon=True).start()

    def warm(self, stopped):
        if not self.repository or not (self.root / "candidate.json").is_file():
            return

        def monitor():
            while not stopped.is_set():
                result = self.check()
                print("[check] " + self.describe(result), file=sys.stderr, flush=True)
                # A wake or manual check may have moved the next deadline.
                while not stopped.is_set():
                    remaining = self._checked_at + CHECK_INTERVAL - time.monotonic()
                    if remaining <= 0 or stopped.wait(remaining):
                        break
        Thread(target=monitor, name="core-package-update", daemon=True).start()


package_updates = PackageUpdates(Path(__file__).resolve().parents[2],
                                 "win" if sys.platform == "win32" else sys.platform,
                                 RELEASE_REPOSITORY)
