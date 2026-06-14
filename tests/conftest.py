import sys
from pathlib import Path

# Allow `from config import ...` style imports that the existing src/*.py use
# (they assume `python ...` is run from inside src/).
SRC = Path(__file__).parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
