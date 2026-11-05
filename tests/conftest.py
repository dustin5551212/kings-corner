import sys
from pathlib import Path

# Tests import the package from the repo root and `policy` from this directory.
ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT,):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
