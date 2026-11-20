"""Paths, ports and persisted user settings."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

APP_NAME = "Featherbox"
APP_VERSION = "0.3.3"


def _env(name: str, default: str) -> str:
    """FEATHERBOX_<name>, falling back to the pre-rename SRIJAN_<name>."""
    return os.environ.get(f"FEATHERBOX_{name}") or os.environ.get(f"SRIJAN_{name}") or default

ROOT = Path(__file__).resolve().parents[2]          # project root
DATA = Path(_env("DATA", str(ROOT / "data")))
BIN = DATA / "bin"
MODELS = DATA / "models"
OUTPUTS = DATA / "outputs"
LOGS = DATA / "logs"
FRONTEND_DIST = ROOT / "frontend" / "dist"

for _p in (BIN, MODELS, OUTPUTS, LOGS):
    _p.mkdir(parents=True, exist_ok=True)

API_HOST = "127.0.0.1"
API_PORT = int(_env("PORT", "8420"))
LLM_PORT = int(_env("LLM_PORT", "8421"))

# Windows: keep engine processes from flashing console windows.
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Which engine builds to download. FEATHERBOX_PLATFORM overrides it (tests check both catalogs on one PC).
PLATFORM = _env("PLATFORM", "windows" if os.name == "nt" else "mac" if sys.platform == "darwin" else "linux")
EXE = ".exe" if PLATFORM == "windows" else ""
# Oldest CUDA version the NVIDIA driver must support for the CUDA engines (their builds differ per system).
CUDA_MIN = 12.4 if PLATFORM == "windows" else 12.8

_SETTINGS_FILE = DATA / "settings.json"
_settings_lock = threading.Lock()
_DEFAULTS: dict = {
    "backend": "auto",        # auto | cuda | vulkan | cpu
    "llm_model": None,        # catalog id; None = first installed
    "image_model": None,
    "video_model": None,
    "video_quality": "fast",  # fast: tiny TAEHV decoder (~2.5x quicker, slightly softer) | best: full Wan VAE
    "voice": "af_heart",
    "stt_model": None,        # speech-to-text model; None = best installed
    "update_check": True,     # look for a new Featherbox version about once a day (asks GitHub; sends nothing personal)
}


def load_settings() -> dict:
    with _settings_lock:
        data = dict(_DEFAULTS)
        if _SETTINGS_FILE.exists():
            try:
                data.update(json.loads(_SETTINGS_FILE.read_text("utf-8")))
            except (OSError, ValueError):
                pass
        return data


def save_settings(patch: dict) -> dict:
    current = load_settings()
    current.update({k: v for k, v in patch.items() if k in _DEFAULTS})
    with _settings_lock:
        _SETTINGS_FILE.write_text(json.dumps(current, indent=2), "utf-8")
    return current
