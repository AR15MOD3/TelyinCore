"""Create the local runtime; optional QQ packages cannot block core startup."""
from pathlib import Path
import os
import subprocess
import sys
import tomllib
import venv


def main() -> int:
    root = Path(__file__).resolve().parent
    if sys.version_info < (3, 11):
        print("Python 3.11+ required")
        return 1
    py = root / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        venv.EnvBuilder(with_pip=True).create(root / ".venv")
    env = dict(os.environ, PYTHONUTF8="1")
    result = subprocess.run([str(py), str(root / "scripts/ensure_runtime.py"),
                             "--install"], cwd=root, env=env, shell=False)
    if result.returncode:
        return result.returncode
    if "--core-only" in sys.argv:
        return 0
    manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    for name in ("qq-media", "qq-voice"):
        print(f"Installing optional {name} dependencies...", flush=True)
        result = subprocess.run([str(py), "-m", "pip", "install",
                                 *manifest["project"]["optional-dependencies"][name]],
                                cwd=root, env=env, shell=False, capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
        log = root / "data" / f"setup-{name}.log"
        log.parent.mkdir(exist_ok=True)
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
        print(result.stdout + result.stderr, flush=True)
        if result.returncode:
            print(f"WARNING: {name} unavailable. Core is ready; retry SETUP later. "
                  f"Details: data/{log.name}", flush=True)
    print("Core setup complete. You may run START.cmd.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
