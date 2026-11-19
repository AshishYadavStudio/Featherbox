"""How long things took on this PC, so time estimates come from real runs instead of fixed guesses.

Keeps the last few durations per key in data/timings.json:
  image:<model id>              one picture (engine start to PNG written), Image page or Studio
  video:<model id>:<fast|best>  one AI clip
  edit:<still|ai>               building one scene's video segment
  assemble                      joining scenes, music mix and thumbnail
"""
from __future__ import annotations

import json
import statistics
import threading

from . import config

_FILE = config.DATA / "timings.json"
_lock = threading.Lock()
KEEP = 10


def _load() -> dict[str, list[float]]:
    try:
        data = json.loads(_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: [float(x) for x in v if isinstance(x, (int, float)) and x > 0]
            for k, v in data.items() if isinstance(v, list)}


def record(key: str, seconds: float) -> None:
    """Remember one duration. Never raises: timing is a nicety, not part of the job."""
    if not seconds or seconds <= 0:
        return
    with _lock:
        data = _load()
        data[key] = (data.get(key, []) + [round(seconds, 2)])[-KEEP:]
        tmp = _FILE.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=1), "utf-8")
            tmp.replace(_FILE)
        except OSError:
            pass


def typical(key: str) -> float | None:
    """Median of the recent durations for `key`, or None if it never ran on this PC."""
    vals = _load().get(key)
    return round(statistics.median(vals), 1) if vals else None
