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


_DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


def _adapter_memory_mb() -> dict[str, int]:
    """Dedicated memory per display adapter name, from the driver's registry entry (WMI caps it at 4 GB).
    For a built-in GPU this is the small part of RAM set aside for it at boot."""
    sizes: dict[str, int] = {}
    try:
        import winreg
        cls = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS)
    except (ImportError, OSError):
        return sizes
    for i in range(64):
        try:
            sub = winreg.EnumKey(cls, i)
        except OSError:
            break
        try:
            key = winreg.OpenKey(cls, sub)
            name = str(winreg.QueryValueEx(key, "DriverDesc")[0]).strip()
            raw = None
            for value in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                try:
                    raw = winreg.QueryValueEx(key, value)[0]
                    break
                except OSError:
                    continue
            if isinstance(raw, bytes):
                raw = int.from_bytes(raw[:8], "little")
            if name and isinstance(raw, int) and raw > 0:
                sizes[name] = max(sizes.get(name, 0), raw // (1024 * 1024))
        except OSError:
            continue
    return sizes


def _is_integrated(vendor: str, name: str) -> bool:
    """Built-in (shares system RAM) or a separate graphics card? Judged from the product name."""
    if vendor == "amd":
        return not re.search(r"\bRX\b|Radeon Pro|FirePro|Instinct|Radeon VII|Vega (56|64)\b", name, re.I)
    if vendor == "intel":
        return not re.search(r"Arc(\(TM\))? (Pro )?[AB]\d{2,3}|Iris Xe MAX|\bDG\d", name, re.I)
    return False


@functools.lru_cache(maxsize=1)
def _other_gpus() -> list[dict]:
    """Non-NVIDIA adapters (AMD / Intel) — candidates for the Vulkan backend."""
    if os.name != "nt":
        return []
    out = _run(["powershell", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_VideoController).Name"])
    memory = _adapter_memory_mb()
    gpus = []
    for name in (n.strip() for n in out.splitlines()):
        low = name.lower()
        if not name or "nvidia" in low or "basic" in low or "virtual" in low:
            continue
        vendor = "amd" if ("amd" in low or "radeon" in low) else "intel" if "intel" in low else "other"
        gpu = {"vendor": vendor, "name": name, "integrated": _is_integrated(vendor, name)}
        mb = memory.get(name, 0)
        if gpu["integrated"]:
            gpu["reserved_mb"] = mb            # set aside at boot; the rest comes from shared RAM
        elif mb >= 1024:
            gpu["vram_mb"] = mb
        gpus.append(gpu)
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
    others = _other_gpus()
    cards = [g for g in others if g.get("vram_mb")]
    if not nv and cards:                       # AMD / Intel graphics card: its own memory (free amount unknown)
        info["vram_mb"] = max(g["vram_mb"] for g in cards)
    # Built-in graphics (Radeon 780M/860M, Intel Arc 140V...) use system RAM: Windows lets them take up to
    # half of it on top of the part reserved at boot. Only counted when there is no graphics card.
    igpu = next((g for g in others if g.get("integrated")), None)
    info["igpu"] = None
    if igpu and not info["vram_mb"]:
        shared = int(info["ram_gb"] * 1024 / 2)
        info["igpu"] = {"name": igpu["name"], "memory_mb": igpu.get("reserved_mb", 0) + shared,
                        "shared_mb": shared}
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


def other_gpu_programs() -> list[str]:
    """Other programs holding NVIDIA GPU memory, described for people (e.g. 'python.exe in D:/Tools/TTS')."""
    if not shutil.which("nvidia-smi"):
        return []
    import os
    import psutil
    mine = {os.getpid(), *(c.pid for c in psutil.Process().children(recursive=True))}
    out = []
    for line in _run(["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"]).splitlines():
        pid_s, _, name = line.partition(",")
        try:
            pid = int(pid_s.strip())
        except ValueError:
            continue
        if pid in mine:
            continue
        label = os.path.basename(name.strip()) or f"process {pid}"
        try:
            label += f" in {psutil.Process(pid).cwd()}"
        except (psutil.Error, OSError):
            pass
        out.append(label)
    return out
