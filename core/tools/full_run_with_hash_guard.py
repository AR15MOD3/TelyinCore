"""在**当前工作区**跑全量，并在前后各算一次哈希：证明"采集范围内"跑的时候没被改。

 ⚠️ **证据边界**（2026-10-18 09:5x）：
   它只是**跑前 ／ 跑后两次快照**，**不是持续监控**；采集范围见下面 `ROOTS` ／ `FILES`。
   能说的只有"**采集范围内**前后哈希一致"，**不能**说"全工作区全程不可变"。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

CORE = Path(r"D:\TelyinCore\core")
#: 采集范围（2026-10-18 09:5x 补齐）：
#:   · 内核 ／ 判据 ／ 页面源 ／ 工具 ／ 文档 ／ 构建脚本；
#:   · **内置插件源码**（`builtin/` —— 判据真的会读它）与 `extensions/` 的清单；
#:   · 构建配置与锁文件（`package.json` ／ `pnpm-lock.yaml`）—— 页面产物由它们决定。
ROOTS = ["telyincore", "tests", "src", "tools", "docs", "scripts", "builtin", "extensions"]
FILES = ["launch.py", "pyproject.toml", "package.json", "pnpm-lock.yaml"]
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".venv", "node_modules"}


def manifest() -> dict:
    out = {}
    for rel in ROOTS:
        base = CORE / rel
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file() and not (set(p.parts) & SKIP_PARTS):
                out[str(p.relative_to(CORE)).replace("\\", "/")] = hashlib.sha256(
                    p.read_bytes()).hexdigest()
    for rel in FILES:
        p = CORE / rel
        if p.is_file():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def main() -> int:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    before = manifest()
    print("采集范围:", " ／ ".join(ROOTS), "＋", " ／ ".join(FILES), flush=True)
    print("采集项数:", len(before), flush=True)

    got = subprocess.run([str(CORE / ".venv" / "Scripts" / "python.exe"),
                          "-m", "pytest", "tests/", "-q"],
                         cwd=str(CORE), capture_output=True, text=True, timeout=1800)
    after = manifest()
    moved = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))

    out = CORE / "data" / "instances" / ("full-run-" + stamp)
    out.mkdir(parents=True, exist_ok=True)
    (out / "pytest-full.txt").write_text(got.stdout + "\n[stderr]\n" + got.stderr, encoding="utf-8")
    scope_text = "\n".join([
        "ROOTS: " + ", ".join(ROOTS),
        "FILES: " + ", ".join(FILES),
        "items: " + str(len(before)),
        "boundary: before/after snapshots only; not continuous monitoring",
    ])
    (out / "scope.txt").write_text(scope_text + "\n", encoding="utf-8")
    (out / "manifest-before.json").write_text(json.dumps(before, indent=1), encoding="utf-8")
    (out / "manifest-after.json").write_text(json.dumps(after, indent=1), encoding="utf-8")

    lines = [one for one in (got.stdout or "").splitlines() if one.strip()]
    print("\n".join(lines[-16:]))
    print("exit:", got.returncode)
    print("采集范围内跑前跑后变了：", len(moved), moved[:5])
    print("证据目录:", out)

    return got.returncode if got.returncode else (1 if moved else 0)


if __name__ == "__main__":
    raise SystemExit(main())
