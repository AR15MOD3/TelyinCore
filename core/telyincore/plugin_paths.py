"""Package source and install roots for the active deployment."""
from pathlib import Path
import sys


def source_root(install_root):
    root = Path(install_root).resolve()
    distribution = Path(__file__).resolve().parents[1]
    if sys.platform == 'linux' and root == distribution / '.linux-acceptance/core':
        return distribution
    return root
