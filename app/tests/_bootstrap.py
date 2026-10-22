import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO_ROOT / "app"), str(REPO_ROOT / "src")]
