"""Imported first by every test: a throwaway data folder (never the real one) and import paths."""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if "FEATHERBOX_DATA" not in os.environ:
    _tmp = tempfile.mkdtemp(prefix="featherbox-test-")
    os.environ["FEATHERBOX_DATA"] = _tmp
    atexit.register(shutil.rmtree, _tmp, ignore_errors=True)

for p in (ROOT / "backend", ROOT / "tools" / "benchmark"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
