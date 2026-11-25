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
FAILED_CHECK_INTERVAL = 5 * 60
_PAGE_SIZE = 50


def release_tag(platform, version, channel="stable"):
    return f"{platform}-acceptance-{version}" if channel == "acceptance" else f"{platform}-{version}"


def installed_channel(root):
    try:
        setting = json.loads((root/"runtime/release-channel.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        try:
            candidate = json.loads((root / "candidate.json").read_text(encoding="utf-8-sig"))
            return "acceptance" if isinstance(candidate, dict) and candidate.get("release_channel") == "acceptance" else "stable"
        except (OSError, ValueError):
            return "stable"
    except (OSError, ValueError):
        return "stable"
    return "acceptance" if setting == {"channel": "acceptance"} else "stable"


def github_latest(repository: str, platform: str, *, channel="stable", proxy_root=None) -> dict:
    from urllib.parse import quote
    from http.client import HTTPException
    from .download_sources import github_json, selected_proxy

    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("发行仓库名称无效")
    latest = None
    deadline = time.monotonic() + 12
    attempts = []
    def fetch(url, *args, **kwargs):
        try:
            return github_json(url, *args, report=attempts.append, **kwargs)
        except (OSError, ValueError, HTTPException) as error:
            detail = " → ".join(dict.fromkeys(attempts[-6:]))
            raise ValueError("检查版本失败。" + detail) from error

    bucket = int(time.time() // 60)
    for page in range(1, 11):
        url = f"https://api.github.com/repos/{repository}/releases?per_page={_PAGE_SIZE}&page={page}&check={bucket}"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("GitHub 检查超时")
        releases = fetch(url, {"Accept": "application/vnd.github+json", "User-Agent": "TelyinCore-release-check"},
                               selected_proxy("official", proxy_root), timeout=min(4, remaining / 2),
                               expected=lambda rows: isinstance(rows, list) and all(isinstance(row, dict) for row in rows))
        for release in releases:
            if not isinstance(release, dict) or release.get("draft") is not False:
                continue
            tag = release.get("tag_name")
            if not isinstance(tag, str):
                continue
            prefix = release_tag(platform, "", channel)
            if not tag.startswith(prefix):
                continue
            match = _TAG.fullmatch(platform + "-" + tag[len(prefix):])
            if not match or match[1] != platform or not _VERSION.fullmatch(match[2]):
                continue
            version = match[2]
            if latest is None or version_key(version) > version_key(latest["latest"]):
                latest = {"latest": version, "releaseUrl": f"https://github.com/{repository}/releases/tag/{quote(tag)}"}
                # A mirror may announce a version, but only an official release can
                # declare a breaking compatibility floor for an ordinary deployment.
                if "TelyinCore-Minimum:" in (release.get("body") or ""):
                    official = fetch(
                        f"https://api.github.com/repos/{repository}/releases/tags/{quote(tag)}",
                        {"User-Agent": "TelyinCore-release-check"}, selected_proxy("official", proxy_root),
                        official_only=True, timeout=4)
                    if official.get("tag_name") == tag and official.get("draft") is False:
                        floor = re.search(r"(?m)^TelyinCore-Minimum: (beta\.[0-9.]+)\s*$", official.get("body") or "")
                        if floor and version_key(floor[1]) <= version_key(version):
                            latest["minimum"] = floor[1]
                latest["assets"] = [a["name"] for a in release.get("assets", [])
                                    if isinstance(a, dict) and isinstance(a.get("name"), str)
                                    and a.get("state") == "uploaded" and a.get("size", 0) > 0]
                latest["native_assets"] = [a["name"] for a in release.get("assets", [])
                                           if isinstance(a, dict) and a.get("label") == "core-online-update-v1"
                                           and a.get("name") in latest["assets"]]
        if len(releases) < _PAGE_SIZE:
            if latest is None:
                raise ValueError("仓库中还没有此平台的公开发布记录。")
            latest["checkRoute"] = " → ".join(dict.fromkeys(attempts[-6:]))
            return latest
    raise ValueError("发布历史过长，本次未能核对完整。")


def overlay_name(platform, current, latest):
    return f"Telyincore_{platform}_{current}_to_{latest}_update.zip"


def overlay_url(repository, platform, current, latest, channel="stable"):
    name = overlay_name(platform, current, latest)
    return f"https://github.com/{repository}/releases/download/{release_tag(platform, latest, channel)}/{name}"


class PackageUpdates:
    def __init__(self, root, platform, repository, fetch=None):
        from threading import Lock
        self.root = root
        self.platform = platform
        self.repository = repository
        self.channel = installed_channel(root)
        from .download_sources import settings_root
        self.proxy_root = settings_root(root / "core")
        from .release_preview import LocalRelease
        preview = LocalRelease(root)
        self.preview = preview if self.channel == "acceptance" and preview.config.is_file() else None
        self.fetch = fetch or (self.preview.latest if self.preview else None) or (lambda repo, plat: github_latest(repo, plat, channel=self.channel, proxy_root=self.proxy_root))
        self.install_lock = Lock()
        self.install_progress = ""
        self._restart_requested = False
        self._lock = Lock()
        self._cache = None
        self._checked_at = 0.0
        self._checked_wall = 0.0

    def installation(self):
        from .online_install import folder, pending
        row = pending(self.root)
        status = {"pendingVersion": row["version"]} if row else {}
        try:
            record = json.loads((folder(self.root) / "last-install.json").read_bytes())
            if (isinstance(record, dict) and record.get("platform") == self.platform
                    and record.get("version") == installed_package(self.root, self.platform)
                    and record.get("source") in ("mirror", "official", "cache", "offline", "preview")
                    and record.get("route") in ("direct", "proxy", "local")):
                status["lastInstall"] = {k: record[k] for k in ("version", "source", "route")}
                base = record.get("base")
                if isinstance(base, str) and _VERSION.fullmatch(base):
                    status["lastInstall"]["base"] = base
        except (OSError, ValueError):
            pass
        if self.install_lock.locked() and self.install_progress:
            status["installProgress"] = self.install_progress
        return status

    def prepare_install(self):
        from .online_install import stage
        self.install_progress = "正在检查版本"
        return stage(self)

    def restart_for_update(self, port):
        from .online_install import start_restart
        if not self._restart_requested:
            start_restart(self.root, port)
            self._restart_requested = True

    def check(self, force=False):
        from urllib.error import HTTPError
        from http.client import HTTPException

        if not force and not self.preview:
            self._from_launcher()
        with self._lock:
            if self._cache is not None:
                if time.monotonic() - self._checked_at < (5 if force else self.check_interval()):
                    return self._cache
            result = {"platform": self.platform, "current": None, "channel": self.channel}
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
                        floor = release.get("minimum")
                        result["required"] = bool(floor and local < version_key(floor) <= remote)
                        if status == "available" and overlay_name(self.platform, current, release["latest"]) in release.get("assets", ()):
                            result["installable"] = overlay_name(self.platform, current, release["latest"]) in release.get("native_assets", ())
                            if not self.preview:
                                result["downloadUrl"] = overlay_url(self.repository, self.platform, current, release["latest"], self.channel)
                    except HTTPError as exc:
                        limited = exc.code == 429 or (exc.code == 403 and exc.headers
                                  and exc.headers.get("X-RateLimit-Remaining") == "0")
                        message = ("GitHub 暂时限制了检查频率，请稍后重试。" if limited
                                   else f"暂时无法读取发行仓库（HTTP {exc.code}）。")
                        result.update(status="unavailable", message=message)
                    except (OSError, HTTPException, ValueError) as exc:
                        result.update(status="unavailable", message=str(exc) if type(exc) is ValueError
                                      else "暂时无法连接 GitHub，请稍后重试。")
            self._cache = result
            self._checked_at = time.monotonic()
            self._checked_wall = time.time()
            result.update(checkedAt=self._checked_wall,
                          nextCheckAt=self._checked_wall + self.check_interval())
            return result

    def check_interval(self):
        return FAILED_CHECK_INTERVAL if self._cache and self._cache.get("status") == "unavailable" else CHECK_INTERVAL

    def wake_block(self):
        if not (self.root / "candidate.json").is_file():
            return ""
        state = self.check()
        if state.get("required"):
            return f"本机 {state['current']}，此版本要求至少 {state['minimum']}。请从控制台更新后再唤醒 Bot。"
        return ""

    def launcher_check(self):
        """Hand the launcher's fresh platform/version result to the Bot process."""
        import tempfile
        result = self.check(force=True)
        if result["current"] and self.repository:
            path = self.root / "runtime" / ("launcher-release-check.json" if self.channel == "stable" else "launcher-release-check-acceptance.json")
            temporary = None
            try:
                path.parent.mkdir(exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                                 delete=False) as f:
                    temporary = Path(f.name)
                    json.dump([time.time(), self.repository, self.platform, result["current"], result.get("latest"), bool(result.get("downloadUrl")), bool(result.get("installable")), result.get("minimum"),
                               result.get("checkRoute", ""), result.get("message", "")], f)
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
        path = self.root / "runtime" / ("launcher-release-check.json" if self.channel == "stable" else "launcher-release-check-acceptance.json")
        try:
            with path.open("rb") as f:
                stamp, repository, platform, current, latest, *asset = json.loads(f.read(4097))
            age = time.time() - stamp
            if not (0 <= age < (CHECK_INTERVAL if self._cache is not None else 120) and repository == self.repository and platform == self.platform
                    and current == installed_package(self.root, self.platform)):
                return
            result = {"current": current, "platform": platform, "channel": self.channel}
            if latest is None:
                result.update(status="unavailable", message=(asset[4] if len(asset) == 5 and isinstance(asset[4], str) and asset[4] else "启动自检暂未取得公开版本，可手动重试。"))
            else:
                local, remote = version_key(current), version_key(latest)
                result.update(latest=latest,
                              status="available" if remote > local else "ahead" if local > remote else "current",
                              releaseUrl=f"https://github.com/{repository}/releases/tag/{release_tag(platform, latest, self.channel)}")
                if len(asset) == 5 and isinstance(asset[3], str):
                    result["checkRoute"] = asset[3][:512]
                if len(asset) >= 3 and asset[2] and local < version_key(asset[2]) <= remote:
                    result.update(required=True, minimum=asset[2])
                if result["status"] == "available":
                    if not asset:
                        return  # Earlier launchers did not check the release assets.
                    if asset[0] is True:
                        result["installable"] = len(asset) >= 2 and asset[1] is True
                        result["downloadUrl"] = overlay_url(repository, platform, current, latest, self.channel)
            result.update(checkedAt=stamp, nextCheckAt=stamp + (
                FAILED_CHECK_INTERVAL if result["status"] == "unavailable" else CHECK_INTERVAL))
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
        route = (" · " + result["checkRoute"]) if result.get("checkRoute") else ""
        if status == "available":
            return f"版本自检 · 发现新版 {result['latest']}（本机 {result['current']}）{route}"
        if status == "current":
            return f"版本自检 · {result['current']} 已是最新公开版本{route}"
        if status == "ahead":
            return f"版本自检 · 本机 {result['current']}，公开版本 {result['latest']}{route}"
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
                    remaining = self._checked_at + self.check_interval() - time.monotonic()
                    # A manual failure can shorten a previously successful
                    # check's deadline while this monitor is sleeping.
                    if remaining <= 0 or stopped.wait(min(remaining, 5)):
                        break
        Thread(target=monitor, name="core-package-update", daemon=True).start()


package_updates = PackageUpdates(Path(__file__).resolve().parents[2],
                                 "win" if sys.platform == "win32" else sys.platform,
                                 RELEASE_REPOSITORY)
