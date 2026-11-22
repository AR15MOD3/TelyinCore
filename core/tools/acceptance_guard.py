"""Process-local acceptance write guard; NOT an OS sandbox/security boundary.

Run only trusted Python fixtures. Native code or inherited handles can bypass
Python audit hooks. A violation fails acceptance even if the fixture catches it.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
import runpy
import sys


class WriteGuard:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.violations = []

    def deny(self, event, path=None):
        self.violations.append({"event": event, "path": str(path) if path is not None else None})
        raise PermissionError("acceptance_guard_denied: " + event)

    def check(self, event, path, dir_fd=None):
        # Descriptors opened through Python were checked at the path open;
        # inherited descriptors are one reason this is not a security sandbox.
        if isinstance(path, int):
            return
        base = None
        if dir_fd not in (None, -1):
            # Resolve an original directory descriptor, never guess it from cwd.
            # Platforms without a descriptor path fail closed. This remains a
            # trusted-fixture audit hook, not protection from hostile races.
            try:
                if type(dir_fd) is not int or not sys.platform.startswith('linux'):
                    raise ValueError('unsupported descriptor')
                original = os.fstat(dir_fd)
                base = Path(os.readlink(f'/proc/self/fd/{dir_fd}')).resolve(strict=True)
                current = base.stat()
                if (not stat.S_ISDIR(original.st_mode)
                        or (original.st_dev, original.st_ino) != (current.st_dev, current.st_ino)
                        or not base.is_relative_to(self.root)):
                    raise ValueError('descriptor outside fixture')
            except (OSError, TypeError, ValueError):
                self.deny(event + ':dir_fd', path)
        try:
            resolved = Path(os.fsdecode(path))
            if base is not None and not resolved.is_absolute():
                resolved = base / resolved
            resolved = resolved.resolve()
            allowed = resolved == self.root or self.root in resolved.parents
        except (OSError, TypeError, ValueError):
            allowed = False
        if not allowed:
            self.deny(event, path)

    def audit(self, event, args):
        if event == "open":
            path, mode, flags = args
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+"))
            writing |= bool(isinstance(flags, int) and flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if writing:
                self.check(event, path)
        elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime",
                       "os.truncate", "os.chown"):
            fd_index = {"os.remove": 1, "os.rmdir": 1, "os.mkdir": 2,
                        "os.chmod": 2, "os.utime": 3, "os.chown": 3}.get(event)
            fd = args[fd_index] if fd_index is not None and len(args) > fd_index else None
            self.check(event, args[0], fd)
        elif event in ("os.rename", "os.replace"):
            self.check(event, args[0], args[2] if len(args) > 2 else None)
            self.check(event, args[1], args[3] if len(args) > 3 else None)
        elif event in ("os.symlink", "os.link", "subprocess.Popen", "os.system",
                       "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty", "os.startfile",
                       "os.startfile/2"):
            self.deny(event)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--script", required=True)
    args = parser.parse_args(argv)
    root, script = Path(args.root).resolve(), Path(args.script).resolve()
    if not root.is_dir() or root not in script.parents or not script.is_file():
        parser.error("script must already exist inside the dedicated output root")
    guard = WriteGuard(root)
    sys.dont_write_bytecode = True
    sys.addaudithook(guard.audit)
    failure = None
    try:
        sys.argv = [str(script)]
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            failure = "SystemExit"
    except BaseException as exc:
        failure = type(exc).__name__  # Never dump fixture text/provider secrets.
    report = {"scope": "python_process_audit_only", "os_sandbox": False,
              "allowed_write_root": str(root), "script": str(script),
              "violations": guard.violations, "failure_class": failure,
              "passed": not guard.violations and failure is None}
    (root / "write-guard-result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"write_guard_passed": report["passed"],
                      "denied_operations": len(guard.violations)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
