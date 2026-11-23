"""Check/install core dependencies with the exact Python used by the launcher.

Standard library only: this must also work in a newly created virtualenv.
Optional QQ ASR dependencies are deliberately not part of core startup.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from telyincore.download_sources import pip_install, settings_root

PROBES = {
    "pillow": "from PIL import Image; Image.init(); assert all(fmt in Image.OPEN for fmt in ('GIF', 'PNG', 'JPEG', 'WEBP'))",
    "certifi": "import certifi, ssl; ssl.create_default_context().load_verify_locations(certifi.where())",
    "httpx": "import httpx",
    "anyio": "import anyio",
    "websockets": "import websockets; from websockets.sync.client import connect",
    "pyjwt": "import jwt",
    "pyjwt[crypto]": "import jwt; from jwt.algorithms import RSAAlgorithm; assert jwt.algorithms.has_crypto",
}


def dependency_probe(requirements: list[str]) -> str:
    checks = []
    for requirement in requirements:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?)(?:[<>=!~].*)?", requirement)
        key = match.group(1).lower() if match else ""
        if key not in PROBES:
            raise ValueError("unmapped core dependency; update capability probe: " + requirement)
        checks.append(PROBES[key])
    if not checks:
        raise ValueError("empty core dependency declaration")
    return "; ".join(checks)


def run(argv: list[str], root: Path, env=None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=root, env=env if env is not None else dict(os.environ, PYTHONUTF8="1"),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", shell=False)


def ensure(root: Path, *, install: bool = False) -> int:
    manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = manifest["project"]["dependencies"]
    probe = dependency_probe(requirements)
    check = run([sys.executable, "-c", probe], root)
    if check.returncode == 0:
        print("Core dependencies OK.", flush=True)
        return 0
    print(f"Core dependencies missing or broken. Python: {sys.executable}", flush=True)
    print(check.stderr.strip(), flush=True)
    if not install:
        return 1
    pip = run([sys.executable, "-m", "pip", "--version"], root)
    if pip.returncode:
        bootstrap = run([sys.executable, "-m", "ensurepip", "--upgrade"], root)
        if bootstrap.returncode:
            print(bootstrap.stdout + bootstrap.stderr, flush=True)
            return 1
    print("Installing core dependencies; internet access is required...", flush=True)
    command = [sys.executable, "-m", "pip", "install", *requirements]
    logs = root / "data"
    logs.mkdir(exist_ok=True)
    def install_from(argv, env):
        result = run(argv, root, env)
        output = result.stdout + result.stderr
        (logs / "setup-core.log").write_text(output, encoding="utf-8")
        print(output, flush=True)
        return result.returncode

    wheels = root.parent / "runtime/wheels"
    if wheels.is_dir() and install_from([*command, "--no-index", "--find-links", str(wheels)], None) == 0:
        return ensure(root)
    if pip_install(command, install_from, settings_root(root)):
        print("Install failed. See data/setup-core.log; check network/Python compatibility.",
              flush=True)
        return 1
    return ensure(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        print("Python 3.11+ is required.")
        return 1
    return ensure(ROOT, install=args.install)


if __name__ == "__main__":
    raise SystemExit(main())
