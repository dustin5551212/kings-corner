import sys
from pathlib import Path

# Tests import the package from the repo root and `policy` from this directory.
ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT,):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# The CPUs think between actions so a person can watch them. A test has better things
# to do, so it runs at the speed of thought.
import os

os.environ.setdefault("KINGS_CORNERS_BOT_PACE", "0.01")
