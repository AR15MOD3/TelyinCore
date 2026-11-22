""" T4 历史冻结件的**定点脱敏**工具（2026-10-18）。

 ⚠️ 默认 **dry-run**；真正落盘要 `--apply`，而且**只**接受下面那三个对象。
 ⚠️ 它只删 `profile` 下**明确列出**的凭据／认证字段；不递归模糊删、不改别的数据、
    不生成含原始内容的备份、**不记录凭据值或其哈希**。
 ⚠️ 落盘用**同目录临时文件 ＋ 原子替换**；失败不破坏原文件。

 用法（默认只看不改）：
   python tools/t4_redact.py
   python tools/t4_redact.py --apply        # 必须显式开
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
import tempfile

CORE = pathlib.Path(__file__).resolve().parent.parent

#: **只有这三份**允许动。
ALLOWED = (
    CORE / "data" / "instances" / "t4-20261018-112326" / "frozen.json",
    CORE / "data" / "instances" / "t4-20261018-112442" / "frozen.json",
    CORE / "data" / "instances" / "t4-20261018-112500" / "frozen.json",
)

#: **本次授权的唯一字段** —— 只删 `profile.secret`。
AUTHORIZED_FIELDS = ("secret",)
#: 其余"看起来像凭据"的字段：**只用来发现并停止**，不删。
OTHER_CREDENTIAL_FIELDS = ("api_key", "apiKey", "auth", "authorization",
                           "auth_header", "token", "password")

RECORD_NAME = "REDACTION-RECORD.json"


class Refused(Exception):
    pass


class RecordFailed(Exception):
    """ **已脱敏、留痕失败** —— 不许恢复原文，也不许说"文件没变"。 """

    def __init__(self, said: str, *, applied: bool, file: str) -> None:
        super().__init__(said)
        self.applied = applied
        self.file = file


FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_redirect(path: pathlib.Path) -> bool:
    """ 这一级是不是**重定向**（符号链接 ／ Windows junction ／ 其他 reparse point）。 """
    try:
        if path.is_symlink():
            return True
        st = path.stat(follow_symlinks=False)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def _chain_is_clean(path: pathlib.Path) -> bool:
    """ 从盘符（或根）一路走到文件，**每一级**都不许是重定向。 """
    current = pathlib.Path(path.anchor) if path.anchor else pathlib.Path(".")
    for part in path.parts[1:] if path.anchor else path.parts:
        current = current / part
        if current.exists() and _is_redirect(current):
            return False
    return True


def _guarded(target: pathlib.Path, allowlist) -> pathlib.Path:
    """ 只放行 allowlist 里的**真实文件**；拒绝链接 ／ 相对段 ／ 别的路径。 """
    given = pathlib.Path(target)
    if any(part == ".." for part in given.parts):
        raise Refused("路径里不许有 ..")
    absolute = given if given.is_absolute() else (pathlib.Path.cwd() / given)
    if not _chain_is_clean(absolute):
        raise Refused("路径链上有重定向（符号链接 ／ junction）：" + str(given))
    real = absolute.resolve()
    allowed = {pathlib.Path(one).resolve() for one in allowlist}
    if real not in allowed:
        raise Refused("不在允许清单里：" + str(given))
    if real.name != "frozen.json":
        raise Refused("只处理 frozen.json")
    if not real.is_file():
        raise Refused("不是文件：" + str(real))
    return real


def redact(target, *, apply: bool = False, allowlist=ALLOWED) -> dict:
    """ 返回一份**不含值**的报告：哪些键会被删 ／ 是否已落盘。 """
    path = _guarded(target, allowlist)
    body = json.loads(path.read_text(encoding="utf-8"))
    profile = body.get("profile")
    if not isinstance(profile, dict):
        return {"file": path.name, "dir": path.parent.name, "changed": False,
                "removed": [], "applied": False, "why": "没有 profile 对象"}
    others = [key for key in OTHER_CREDENTIAL_FIELDS if key in profile]
    if others:
        return {"file": path.name, "dir": path.parent.name, "changed": False,
                "removed": [], "blocked_by": others, "applied": False,
                "why": "发现授权范围外的认证字段 —— 停止，等人确认"}
    present = [key for key in AUTHORIZED_FIELDS if key in profile]
    if not present:
        return {"file": path.name, "dir": path.parent.name, "changed": False,
                "removed": [], "applied": False, "why": "没有要删的字段（幂等）"}
    if not apply:
        return {"file": path.name, "dir": path.parent.name, "changed": True,
                "removed": present, "applied": False, "why": "dry-run"}
    record_path = path.parent / RECORD_NAME
    try:
        probe = path.parent / (".redact-probe-" + str(os.getpid()))
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as why:
        raise RecordFailed(
            "处置记录路径不可写，未做任何修改：" + type(why).__name__,
            applied=False, file=path.name) from why
    for key in present:
        profile.pop(key, None)
    text = json.dumps(body, ensure_ascii=False, indent=1)
    handle, made = tempfile.mkstemp(prefix=".frozen.tmp-", dir=str(path.parent))
    tmp = pathlib.Path(made)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    record = {
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "file": path.name, "fields_removed": present,
        "note": "历史冻结件因敏感字段处置已修改；它不是原始验收证据。",
    }
    try:
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    except OSError as why:
        raise RecordFailed("已脱敏，但处置记录没写成：" + type(why).__name__,
                           applied=True, file=path.name) from why
    return {"file": path.name, "dir": path.parent.name, "changed": True,
            "removed": present, "applied": True, "why": "已脱敏"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="真正落盘（默认只看不改）")
    args = ap.parse_args(argv)
    report = []
    failed = False
    for one in ALLOWED:
        if failed:
            report.append({"file": one.name, "dir": one.parent.name, "changed": False,
                           "removed": [], "applied": False,
                           "why": "前一个对象留痕失败 —— 已停止"})
            continue
        try:
            report.append(redact(one, apply=args.apply))
        except Refused as why:
            report.append({"file": one.name, "dir": one.parent.name, "changed": False,
                           "removed": [], "applied": False, "why": str(why)})
        except RecordFailed as why:
            failed = True
            report.append({"file": why.file, "dir": one.parent.name, "changed": why.applied,
                           "removed": [], "applied": why.applied,
                           "why": ("已脱敏、留痕失败：" if why.applied else "未脱敏、预写失败：") + str(why)})
        except FileNotFoundError:
            report.append({"file": one.name, "dir": one.parent.name, "changed": False,
                           "removed": [], "applied": False, "why": "文件不存在"})
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
