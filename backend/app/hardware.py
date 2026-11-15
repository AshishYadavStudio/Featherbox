"""Detect CPU / RAM / GPU so the app can pick a backend and model sizes."""
from __future__ import annotations

import functools
import os
import platform
import re
import shutil
import subprocess

import psutil

from . import config


def _run(cmd: list[str], timeout: float = 8) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              creationflags=config.NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _cpu_name() -> str:
    if os.name == "nt":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    return platform.processor() or "Unknown CPU"


def nvidia_gpus() -> list[dict]:
    if not shutil.which("nvidia-smi"):
        return []
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3:
            try:
                gpus.append({"vendor": "nvidia", "name": parts[0],
                             "vram_mb": int(float(parts[1])), "free_mb": int(float(parts[2]))})
            except ValueError:
                pass
    return gpus


@functools.lru_cache(maxsize=1)
def _cuda_driver_version() -> float | None:
    m = re.search(r"CUDA (?:UMD )?Version:\s*([\d.]+)", _run(["nvidia-smi"]))
    if not m:
        return None
    try:
        major, minor = (m.group(1).split(".") + ["0"])[:2]
        return float(f"{major}.{minor}")
    except ValueError:
        return None


@functools.lru_cache(maxsize=1)
def _other_gpus() -> list[dict]:
    """Non-NVIDIA adapters (AMD / Intel) — candidates for the Vulkan backend."""
    if os.name != "nt":
        return []
    out = _run(["powershell", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_VideoController).Name"])
    gpus = []
    for name in (n.strip() for n in out.splitlines()):
        low = name.lower()
        if not name or "nvidia" in low or "basic" in low or "virtual" in low:
            continue
        vendor = "amd" if ("amd" in low or "radeon" in low) else "intel" if "intel" in low else "other"
        gpus.append({"vendor": vendor, "name": name})
    return gpus


@functools.lru_cache(maxsize=1)
def _static() -> dict:
    return {
        "os": f"{platform.system()} {platform.release()}",
        "cpu": _cpu_name(),
        "cores": psutil.cpu_count(logical=False) or 1,
        "threads": psutil.cpu_count(logical=True) or 1,
        "ram_gb": round(psutil.virtual_memory().total / 1024**3, 1),
    }


def detect() -> dict:
    info = dict(_static())
    nv = nvidia_gpus()
    cuda = _cuda_driver_version() if nv else None
    info.update({
        "gpus": nv + _other_gpus(),
        "cuda_driver": cuda,
        "ram_free_gb": round(psutil.virtual_memory().available / 1024**3, 1),
    })
    primary = max(nv, key=lambda g: g["vram_mb"]) if nv else None
    info["vram_mb"] = primary["vram_mb"] if primary else 0
    info["vram_free_mb"] = primary["free_mb"] if primary else 0
    if nv and cuda and cuda >= 12.4:
        auto = "cuda"
    elif nv or info["gpus"]:
        auto = "vulkan"
    else:
        auto = "cpu"
    info["recommended_backend"] = auto
    chosen = config.load_settings().get("backend", "auto")
    info["backend"] = auto if chosen == "auto" else chosen
    return info


def free_vram_mb() -> int:
    nv = nvidia_gpus()
    return max((g["free_mb"] for g in nv), default=0)
