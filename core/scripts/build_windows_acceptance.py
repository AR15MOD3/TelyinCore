"""Create a content-pinned, clean Windows CORE acceptance candidate.

This is not a complete QQ/TTS/ASR distribution and not a Windows runtime verdict.
Only an explicit public-source allowlist is read; no Bot/runtime/environment
directories, installed plugins, model weights or Git internals are packaged.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import zipfile

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
from telyincore.distribution_version import validate_release_version

TREES = ("telyincore", "telyincore_correlation_v2", "builtin", "templates",
         "presets", "schemas", "dist/web", "scripts", "tools")
ROOT_FILES = ("start.bat", "start.ps1", "pyproject.toml", "LICENSE", "THIRD_PARTY_NOTICES", "README.md",
              "requirements-windows-acceptance.txt")
RUNTIME_DOCS = ("docs/design/段序.md", "docs/design/11-现实与世界观对照.md")
REQUIRED = ("startup.py", "launch.py", "desktop_open.py", "bot_launcher_child.py", "bot_birth_process.py",
            "bot_launcher_host.py", "bot_host_observer.py", "bot_global_admission.py",
            "scripts/ensure_runtime.py", "scripts/windows_acceptance.py",
            "scripts/check_document_storage.py",
            "dist/web/index.html",
            "templates/bot-default/persona/宪法.md",
            "templates/bot-default/persona/角色设定.md",
            "templates/bot-default/环境/场景.json",
            "templates/bot-default/环境/共用机制.md",
            "templates/bot-default/环境/QQ.md")
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", "node_modules"}
SKIP_FILES = {"config.json", "engine.env", ".env", "linux_dev.py"}


def source_files(core: Path) -> dict[str, bytes]:
    core = core.resolve(strict=True)
    selected = list(core.glob("*.py")) + [core / name for name in (*ROOT_FILES, *RUNTIME_DOCS)]
    for name in TREES:
        directory = core / name
        if directory.is_symlink():
            raise ValueError("source tree is a symlink: " + name)
        if not directory.is_dir():
            raise ValueError("missing public source tree: " + name)
        # Do not follow links, even when a recursive source tree is malformed.
        import os
        for folder, dirs, files in os.walk(directory, followlinks=False):
            for child in dirs:
                if (Path(folder) / child).is_symlink():
                    raise ValueError("source directory symlink refused")
            dirs[:] = [child for child in dirs if child not in SKIP_DIRS]
            selected.extend(Path(folder) / filename for filename in files
                            if filename not in SKIP_FILES and not filename.endswith((".pyc", ".pyo")))
    result = {}
    folded = set()
    for path in sorted(set(selected)):
        if path.is_symlink() or not path.is_file():
            raise ValueError("missing or linked source file: " + str(path))
        relative = path.relative_to(core).as_posix()
        if relative.casefold() in folded:
            raise ValueError("case-colliding source path: " + relative)
        for part in PurePosixPath(relative).parts:
            if (part.endswith((".", " ")) or re.search(r'[<>:"\\|?*\x00-\x1f]', part)
                    or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL",
                        *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
                raise ValueError("non-Windows-compatible filename: " + relative)
        blob = path.read_bytes()
        if path.suffix == ".py":
            # The current native Windows backend explicitly requires 3.11.
            ast.parse(blob, filename=relative, feature_version=(3, 11))
        folded.add(relative.casefold())
        result[relative] = blob
    for relative in (*ROOT_FILES, *REQUIRED):
        if relative not in result:
            raise ValueError("required startup source absent: " + relative)
    return result


PYTHON_INSTALLER = "python-3.11.9-amd64.exe"
# Download from python.org and cross-check its published release checksum before pinning.
PYTHON_INSTALLER_SHA256 = "5ee42c4eee1e6b4464bb23722f90b45303f79442df63083f05322f1785f5fdde"


def batch_file() -> bytes:
    # ASCII command shell, including messages before Python exists.
    lines = [
        '@echo off', 'setlocal', 'chcp 65001 >nul',
        'set PYTHONUTF8=1', 'set PYTHONIOENCODING=utf-8',
        'cd /d "%~dp0" || exit /b 2',
        'set "probe=import sys,sysconfig; assert sys.implementation.name == \'cpython\' and sys.version_info[:2] == (3,11) and sysconfig.get_platform() == \'win-amd64\'"',
        r'set "script=%~dp0core\scripts\windows_acceptance.py"',
        r'set "runtime=%~dp0core\.venv\Scripts\python.exe"',
        'if exist "%runtime%" goto use_runtime',
        r'set "bundled=%LOCALAPPDATA%\TelyinCore\Python311"',
        'py -3.11-64 -c "%probe%" >nul 2>nul',
        'if not errorlevel 1 goto use_py',
        r'set "runtime=%bundled%\python.exe"',
        'if exist "%runtime%" goto check_bundled',
        r'set "runtime=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"',
        'if exist "%runtime%" (',
        '    "%runtime%" -c "%probe%" >nul 2>nul',
        '    if not errorlevel 1 goto use_runtime',
        ')',
        'for /f "delims=" %%P in (\'where python.exe 2^>nul ^| findstr /v /i /l /c:"WindowsApps"\') do (',
        '    "%%P" -c "%probe%" >nul 2>nul',
        '    if not errorlevel 1 (',
        '        set "runtime=%%P"',
        '        goto use_runtime',
        '    )',
        ')',
        rf'set "installer=%~dp0runtime\{PYTHON_INSTALLER}"',
        'if not exist "%installer%" goto missing_python',
        'echo.', 'echo   T E L Y I N   C O R E', 'echo   ----------------------------',
        'echo   [setup] Installing CPython 3.11 x64...',
        'start "" /wait "%installer%" /passive InstallAllUsers=0 TargetDir="%bundled%" Include_launcher=0 PrependPath=0 Include_pip=1 Include_test=0',
        'if errorlevel 1 goto install_failed',
        r'set "runtime=%bundled%\python.exe"',
        ':check_bundled',
        '"%runtime%" -c "%probe%" >nul 2>nul',
        'if errorlevel 1 goto install_failed',
        'goto use_runtime',
        ':use_py',
        'py -3.11-64 -u "%script%" %*',
        'goto finished',
        ':use_runtime',
        '"%runtime%" -u "%script%" %*',
        ':finished',
        'set "result=%errorlevel%"',
        'if not %result%==0 (',
        '    echo.',
        '    set /p "closed=  Press Enter to close this window..."',
        ')',
        'exit /b %result%',
        ':missing_python',
        'echo   [error] CPython 3.11 x64 was not found. Use the complete package or install Python.',
        'set /p "closed=  Press Enter to close this window..."', 'exit /b 2',
        ':install_failed',
        'echo   [error] Python setup did not finish. Check the installer result and retry.',
        'set /p "closed=  Press Enter to close this window..."', 'exit /b 2',
    ]
    return ("\r\n".join(lines) + "\r\n").encode("ascii")


GUIDE = """# Windows 核心启动验收候选包

主入口「启动 TelyinCore.bat」先选择唤醒、档案或休止。唤醒后再选择 Bot。操作档案前先退出该部署全部 Bot。档案菜单提供修改档案名称、归档、误删档案处理、恢复归档及未完成操作的恢复。改名须先休止全部 Bot，会同步目录、内部名称和显示名，保留记忆、历史正文及运行身份；已归档档案须先恢复。遇到未核清的外部账号或扩展归属会拒绝改名。档案保存在 bots/.archives，不能作为多份独立备份。承载共享运行根的基础 Bot 暂不支持移除。

此包固定了当前工作区的公开核心源码和前端产物。它尚未通过真实 Windows 验收，
不是 QQ、TTS、ASR、鸣潮或现有 Bot 数据迁移的完整分发包。

## 使用另一台电脑验收

1. 使用 Windows 10/11 x64。启动引导会复用兼容的 CPython 3.11 x64。
2. 完整包名为 `Telyincore_win_beta.x.y.z.zip`，包内顶层目录统一为 `Telyincore_win`。首次部署时完整解压到新目录；已有验收部署请使用对应版本更新包，停止服务后在原目录合并替换，保留 bots、data、extensions 和 .venv。
3. 双击 `启动 TelyinCore.bat`。首次启动或环境变化时完整检查，按需创建环境并安装固定依赖；找不到兼容 Python 时使用随包附带的官方安装程序（如包内提供）。验证成功后记录环境状态，后续直接使用本地环境，快速自检通过即启动。程序更新但依赖未变时沿用该状态；自检失败则回到完整检查。可用 `启动 TelyinCore.bat --check` 主动完整检查。
首次启动会离线检查并准备随包 Node.js 与 SnowLuma Windows 程序组件。程序位于 services，账号、凭据、日志与缓存随各自 Bot 档案保存；安装过程不启动 QQ、不执行注入。QQ 号、允许的会话和登录接入请在 QQ 插件页配置。已有未知修改的运行文件会保留并提示处理，不自动覆盖。
首次启动（包括旧部署覆盖更新后首次启动）会检查本地语义检索。缺失时自动联网安装 CPU 推理依赖和固定版本 BGE-M3（约 2.3 GB，优先国内 hf-mirror 镜像，失败回退官方源），逐文件核验并实际执行一次推理；失败会明确提示，重启可重试。模型保存在 core/models/bge-m3，更新保留。旧部署的模型会校验后自动迁移。已有官方模型可运行 core/scripts/setup_semantic.py --source 模型目录导入。

4. 首次选择中立 Mybot。模型请求在启动时暂停。
5. 检查页面能打开并刷新，使用页面退出或终端 Q 结束，再次启动确认端口正常。
6. 在实例运行时再次打开验收入口，记录复用或拒绝行为。
7. 人设页应显示宪法与角色设定的章节。修改一个章节并保存、重载，检查其他章节保持原文。
8. 侧栏应显示 QQ 连接。插件预先安装，默认关闭；直接进入页面检查环境提示词的读取、保存与重载。QQ 登录与实际收发另行验收。
9. 空知识库搜索应正常返回空结果，新增或修改知识后自动同步文字索引并后台增量补建语义向量；补建完成后用不同说法检索验证。
10. 配好模型后在控制台明确自述名字，请 Bot 记住。核对 profile_add 的实际回执及画像页；若复核拒绝，记录括号内原因码及模型名称。
11. 可在命令提示符运行 `"启动 TelyinCore.bat" --check-documents`，用临时 Bot 离线检查人设分节保存、冲突、提议与自主决定、自述工具写入和文本索引增改删。报告写入 `core/data/acceptance/documents-latest.json`。不接模型或 QQ，不修改现有 Bot；真实模型选工具与语义向量仍需单独验收。


## 回传结果

- 候选编号：见 `candidate.json` 的 `candidate_id`。
- Windows 版本、Python 版本及是否首次部署。
- 安装依赖／环境检查／首次启动／页面／正常退出／再次启动：分别写通过或失败。
- 失败时回传完整报错；不要发账号密钥、Cookie、engine.env 或整个运行数据目录。

## 校验与范围

压缩包旁的同名 `.sha256` 文件用来核对整个 ZIP。
`MANIFEST.sha256` 记录压缩包内各文件的内容摘要。
`candidate.json` 中的 source fingerprint 来自当前工作区内容，包含已提交和未提交的公开源码，
不把 Git HEAD 当作完整当前版本。仅靠哈希一致不能证明 Windows 可运行或分发包稳定。

本包不包含现有 Bot 数据、运行控制记录、私人配置、已安装插件、模型权重、
Git 目录、Python 环境或 Linux 隔离开发数据。默认附带 DeepSeek 配置，API 密钥留空。
验收产生的数据只属于此新部署。
"""


def check_index_startup_contract(sources: dict[str, bytes]) -> None:
    """Check the launcher's keyword arguments against the class actually being shipped."""
    launcher = ast.parse(sources["launch.py"])
    calls = [node for node in ast.walk(launcher) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "IndexMaintenance"]
    if not calls:
        return
    module = ast.parse(sources.get("telyincore/index_maintenance.py", b""))
    init = next((method for cls in module.body if isinstance(cls, ast.ClassDef)
                 and cls.name == "IndexMaintenance" for method in cls.body
                 if isinstance(method, ast.FunctionDef) and method.name == "__init__"), None)
    if init is None:
        raise ValueError("missing packaged IndexMaintenance constructor")
    accepted = {arg.arg for arg in (*init.args.args, *init.args.kwonlyargs)}
    used = {kw.arg for call in calls for kw in call.keywords if kw.arg is not None}
    if not init.args.kwarg and (unknown := used - accepted):
        raise ValueError("packaged IndexMaintenance does not accept " + ", ".join(sorted(unknown)))


def build(core: Path, destination: Path, *, version: str = "beta.0.3.0",
          python_installer: Path | None = None, wheelhouse: Path | None = None) -> Path:
    validate_release_version(version)
    core = core.resolve(strict=True)
    destination = destination.absolute()
    if destination.resolve().is_relative_to(core):
        raise ValueError("release output must be outside the source core")
    sources = source_files(core)
    check_index_startup_contract(sources)
    # Flat-file overlays must never silently leave an older generation selected.
    if (core / "dist/web/current.json").exists():
        from telyincore.web_assets import read_asset
        for name in ("index.html", "app.js", "style.css"):
            served = read_asset(core / "dist/web", name)
            if served is None:
                raise ValueError("selected web generation missing: " + name)
            if name != "index.html" and served != sources.get("dist/web/" + name):
                raise ValueError("selected web generation differs from flat asset: " + name)
    hashes = {name: hashlib.sha256(blob).hexdigest() for name, blob in sources.items()}
    fingerprint = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    candidate_id = "windows-core-" + fingerprint[:12]
    metadata = {
        "schema": "windows-core-acceptance-v1", "candidate_id": candidate_id,
        "version": version,
        "offline_dependencies_bundled": wheelhouse is not None,
        "source_fingerprint_sha256": fingerprint, "source_files": len(sources),
        "python": "CPython 3.11 x64", "python_installer_bundled": python_installer is not None, "windows_runtime_verified": False,
        "production_ready": False, "includes_private_runtime_data": False,
        "scope": "fresh-install core and web UI; QQ plugin installed disabled; Node and SnowLuma runtime bundled; QQ account setup per Bot; TTS/ASR require separate setup",
    }
    members = {"core/" + name: blob for name, blob in sources.items()}
    members.update({"启动 TelyinCore.bat": batch_file(),
                    "README-验收.md": GUIDE.replace("候选包\n", f"候选包 {version}\n", 1).encode("utf-8"),
                    "candidate.json": (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode()})
    members["core/data/engine.json"] = (json.dumps({"current": "DeepSeek", "profiles": {
        "DeepSeek": {"name": "DeepSeek", "kind": "chat",
            "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
            "secret": "DEEPSEEK_API_KEY", "max_tokens": 16384},
    }}, ensure_ascii=False, indent=2) + "\n").encode()
    # The generated profile is runtime data. Ship it with a matching synthetic
    # baseline so first launch passes the normal ownership check.
    members["core/data/bot-runtime-owner.json"] = b'{"baseline":"Mybot"}\n'
    template_prefix = "templates/bot-default/"
    for name, blob in sources.items():
        if name.startswith(template_prefix):
            members["bots/Mybot/" + name.removeprefix(template_prefix)] = blob
        plugin_prefix = "builtin/infinity/core-infinity-qq/"
        if name.startswith(plugin_prefix):
            members["core/extensions/core-infinity-qq/" + name.removeprefix(plugin_prefix)] = blob
    if python_installer is not None:
        installer = python_installer.read_bytes()
        if hashlib.sha256(installer).hexdigest() != PYTHON_INSTALLER_SHA256:
            raise ValueError("Python installer differs from the official release checksum")
        members["runtime/" + PYTHON_INSTALLER] = installer
    if wheelhouse is not None:
        wheels = sorted(wheelhouse.glob("*.whl"))
        if not wheels:
            raise ValueError("no wheels found in the dependency bundle")
        for wheel in wheels:
            if wheel.is_symlink() or not wheel.is_file() or not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", wheel.name):
                raise ValueError("invalid wheel path in dependency bundle")
            members["runtime/wheels/" + wheel.name] = wheel.read_bytes()
        members["README-验收.md"] = members["README-验收.md"].replace(
            "按需联网安装".encode(), "从包内离线安装".encode())
    members["MANIFEST.sha256"] = "".join(
        hashlib.sha256(blob).hexdigest() + "  " + name + "\n"
        for name, blob in sorted(members.items())).encode("utf-8")
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f"Telyincore_win_{version}.zip"
    if output.exists() or output.with_suffix(".sha256").exists():
        raise FileExistsError("candidate already exists; not overwritten: " + str(output))
    with tempfile.TemporaryDirectory(prefix=".candidate-", dir=destination) as temporary:
        archive = Path(temporary) / output.name
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as package:
            for name, blob in sorted(members.items()):
                info = zipfile.ZipInfo("Telyincore_win/" + name, date_time=(2026, 11, 6, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                package.writestr(info, blob)
        with zipfile.ZipFile(archive) as package:
            if package.testzip() is not None:
                raise ValueError("ZIP integrity check failed")
            if set(package.namelist()) != {"Telyincore_win/" + name for name in members}:
                raise ValueError("archive membership mismatch")
            for name, blob in members.items():
                if package.read("Telyincore_win/" + name) != blob:
                    raise ValueError("archive content mismatch")
        if source_files(core) != sources:
            raise ValueError("source files changed during build; candidate not published")
        archive.rename(output)
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    with output.with_suffix(".sha256").open("x", encoding="utf-8") as stream:
        stream.write(checksum + "  " + output.name + "\n")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", default="beta.0.3.0", help="Beta release version, for example beta.0.3.0")
    parser.add_argument("--python-installer", type=Path, help="Official offline CPython 3.11.9 x64 installer")
    parser.add_argument("--wheelhouse", type=Path, help="Resolved Windows x64 CPython 3.11 dependency wheels")
    options = parser.parse_args()
    print(build(CORE, options.output, version=options.version,
                python_installer=options.python_installer, wheelhouse=options.wheelhouse))
