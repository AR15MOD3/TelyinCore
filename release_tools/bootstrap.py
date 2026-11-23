"""One-time migration for installations without the in-app installer."""
import argparse
import os
from pathlib import Path
import sys

from bootstrap_install import apply_pending


def main():
    parser = argparse.ArgumentParser(description="正常退出 Core 后，将更新应用到已有部署目录。")
    parser.add_argument("deployment", type=Path)
    root = parser.parse_args().deployment.resolve(strict=True)
    sys.path.insert(0, str(root / "core"))
    apply_pending(root, "win" if os.name == "nt" else "linux", manual_package=Path(sys.argv[0]))
    print("已完成迁移，请从原启动器打开 Core。")


if __name__ == "__main__":
    main()
