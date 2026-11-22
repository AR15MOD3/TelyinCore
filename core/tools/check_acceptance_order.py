r"""**把验收工单里的脚本抽出来真跑一遍**（2026-10-17 立；v2 按审阅意见改）。

 为什么要有它：`docs/superpowers/notes/2026-10-17-验收工单-批②.md` 是**交给别人跑**的，
 而里面每段都是 `@' ... '@` 的 PowerShell here-string —— **没人保证它跑得起来**。
 这里把那些块**原样抽出来**（就是交给接手人的那一份），在**临时运行目录**上逐项跑。

 v2 改了什么（对应第三轮审阅）：
   · **每项一份副本**：`<runroot>/<项名>/bots/Telyin` —— 不再靠 `reset()` 清历史，
     也不会互相污染证据；
   · **每项一个子进程，带超时**：超时即 `kill`，并把"超时"本身当证据存下来；
   · **stdout ／ stderr ／ 退出码全存**到 `_evidence\<项名>.txt`；
   · 环境只继承 OS 启动所需白名单，另设：`RUNROOT` ／ `ACCEPT` ／ `CORE_ROOT`（后两个每项现设）。

 ⚠️ **它不碰真实家**：临时目录里现造 `bots/Telyin/persona`（空目录，不读取真实 Bot）。
 ⚠️ **C 默认跳过**：它要真上游（引擎可能不在）。要跑就设 `ACCEPT_CHECK_C=1`。
 ⚠️ 用法：`python tools/check_acceptance_order.py --doc <已审阅文档> --doc-sha256 <摘要>`（在 `core/` 下跑）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import tempfile

_CORE = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_DOC = _CORE / "docs/superpowers/notes/2026-10-17-验收工单-批②.md"

#: 每块脚本：`$X = Join-Path $RunRoot 'scripts\\name.py'` … `@' … '@ | Set-Content … $X`
_PATTERN = re.compile(
    r"\$(?P<var>\w+)\s*=\s*Join-Path \$RunRoot 'scripts\\(?P<name>[^']+)'\n"
    r"(?P<body>.*?)"
    r"@'\n(?P<code>.*?)\n'@ \| Set-Content -Encoding utf8 \$(?P=var)",
    re.S)

#: 批② 的项（A–I）；批③ 用 `--doc … --items a,b,c,d,e` 指定
_ITEMS = (
    ("a", "accept_a.py"),
    ("b", "accept_b.py"),
    ("c", "accept_c.py"),
    ("d", "accept_d.py"),
    ("e", "accept_e.py"),
    ("f", "accept_f.py"),
    ("g", "accept_g.py"),
    ("h", "accept_h.py"),
)

#: 一个子进程最多跑多久（秒）—— 超时就杀，并把超时当证据。
_TIMEOUT_S = 300


def _an_item(root: pathlib.Path, item: str) -> pathlib.Path:
    """建这一项的副本（**不许复用**：目录已存在就抛）。"""
    dst = root / item
    if dst.exists():
        raise SystemExit(f"这一项已经跑过：{dst} —— 换一个新的运行目录")
    home = dst / "bots" / "Telyin"
    (home / "persona").mkdir(parents=True)
    # Deliberately empty; no real-Bot copy mode is authorized here.
    return home          # ⚠️ 交回的是**bot 的家**（与工单的 New-AcceptCopy 一致）


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute explicitly reviewed document snippets")
    parser.add_argument("--doc", type=pathlib.Path, required=True)
    parser.add_argument("--doc-sha256", required=True)
    args = parser.parse_args()
    # A digest pins reviewed bytes; it is not a sandbox or execution authorization.
    raw = args.doc.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.doc_sha256.lower():
        raise ValueError("document digest mismatch")
    wanted = [one.strip() for one in os.environ.get("ACCEPT_ITEMS", "").split(",") if one.strip()]
    if set(wanted) - {item for item, _ in _ITEMS}:
        raise ValueError("unknown acceptance item")
    text = raw.decode("utf-8").replace("\r\n", "\n")
    blocks = {m.group("name"): m.group("code") for m in _PATTERN.finditer(text)}
    if not blocks:
        print("工单里一段脚本都没抽到 —— 判据多半没在看（先查那几个 here-string）")
        return 2

    root = pathlib.Path(tempfile.mkdtemp()) / "accept-b2-dryrun"
    scripts, evidence = root / "scripts", root / "_evidence"
    for one in (scripts, evidence):
        one.mkdir(parents=True)
    if set(blocks) - {name for _, name in _ITEMS}:
        raise ValueError("document contains unapproved script names")
    for name, code in blocks.items():
        (scripts / name).write_text(code + "\n", encoding="utf-8")

    run_c = os.environ.get("ACCEPT_CHECK_C") == "1"
    bad: list[str] = []
    chosen = [one for one in _ITEMS if (not wanted or one[0] in wanted)]
    for item, script in chosen:
        if item == "c" and not run_c:
            print(f"\n=== {script} 跳过（要真上游；设 ACCEPT_CHECK_C=1 才跑）")
            continue
        if script not in blocks:
            print("缺脚本：" + script)
            bad.append(script)
            continue
        try:
            home = _an_item(root, item)
        except SystemExit as why:
            print(str(why))
            bad.append(script)
            continue

        # OS bootstrap variables only; no account, proxy or model environment.
        env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
               if key in os.environ}
        env.update({"RUNROOT": str(root), "ACCEPT": str(home),
                    "CORE_ROOT": str(_CORE)})
        log_path = evidence / (item + ".txt")
        try:
            got = subprocess.run([sys.executable, str(scripts / script)],
                                 cwd=str(_CORE), env=env, capture_output=True,
                                 text=True, timeout=_TIMEOUT_S)
            out, err, code = got.stdout or "", got.stderr or "", got.returncode
            timed_out = False
        except subprocess.TimeoutExpired as expired:
            out = (expired.stdout or b"").decode("utf-8", "replace") if isinstance(
                expired.stdout, (bytes, bytearray)) else (expired.stdout or "")
            err = (expired.stderr or b"").decode("utf-8", "replace") if isinstance(
                expired.stderr, (bytes, bytearray)) else (expired.stderr or "")
            code, timed_out = -1, True

        log_path.write_text(
            f"# {script}\n# exit={code}  timeout={timed_out}\n"
            "----- stdout -----\n" + out + "\n----- stderr -----\n" + err + "\n",
            encoding="utf-8")

        print(f"\n=== {script} exit={code}" + ("  ** 超时 **" if timed_out else ""))
        for line in out.strip().splitlines()[-3:]:
            print("    " + line)
        if timed_out:
            print(f"  ! 超过 {_TIMEOUT_S} 秒没结束 —— 已终止；证据在 {log_path}")
            bad.append(script)
        elif code != 0:
            for line in err.strip().splitlines()[-6:]:
                print("  ! " + line)
            bad.append(script)

    print("\n结论：" + ("全部 PASS" if not bad else "失败：" + str(bad)))
    print("dry-run 目录（留着看）：" + str(root))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
