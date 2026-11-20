"""Update check and one-click update.

About once a day (setting `update_check`, on by default) the app asks GitHub for the latest release. Only
the request itself is sent: no personal data. "Update now" downloads that release's installer to the Temp
folder, checks it against the SHA-256 checksum GitHub publishes for the file, and runs it silently into
this install folder. Setup closes the app, replaces the program (keeping data\\), and restarts it in the
background; the open window reloads itself once the new version answers.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx

from . import config

REPO = "AshishYadavStudio/Featherbox"
API_URL = os.environ.get("FEATHERBOX_UPDATE_URL") or f"https://api.github.com/repos/{REPO}/releases/latest"
CHECK_EVERY = 24 * 3600
_STATE_FILE = config.DATA / "update.json"
_lock = threading.Lock()

state: dict = {"state": "idle", "progress": 0.0, "error": None, "latest": None, "last_check": None}
installer_pid: int | None = None     # the running update installer: shutting the app down must not kill it


def _vtuple(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v.split("-")[0].split("+")[0])
    return tuple(int(n) for n in nums[:3]) or (0,)


def install_root() -> Path | None:
    """The install folder when running from an installed copy (not from a source checkout)."""
    root = config.ROOT.parent                      # <install>\app\backend\app\config.py -> ROOT=<install>\app
    if (root / "Featherbox.exe").exists() and (root / "uninstall.exe").exists() and (root / "python").is_dir():
        return root
    return None


def _load() -> None:
    try:
        saved = json.loads(_STATE_FILE.read_text("utf-8"))
        state["latest"], state["last_check"] = saved.get("latest"), saved.get("last_check")
    except (OSError, ValueError):
        pass


def _save() -> None:
    try:
        _STATE_FILE.write_text(json.dumps({"latest": state["latest"], "last_check": state["last_check"]}, indent=2), "utf-8")
    except OSError:
        pass


def status() -> dict:
    latest = state["latest"]
    available = bool(latest) and _vtuple(latest["version"]) > _vtuple(config.APP_VERSION)
    root = install_root()
    busy = _busy_reason()
    reason = None
    if not root:
        reason = ("This copy runs from source, so it can't update itself. Download the installer instead."
                  if config.PLATFORM == "windows" else
                  "This copy runs from source: update it with git pull, then start it again with ./start.sh.")
    elif busy:
        reason = busy
    elif latest and not latest.get("download_url"):
        reason = "This release has no Windows installer attached."
    return {
        "current": config.APP_VERSION, "available": available, "latest": latest if available else None,
        "newest_seen": latest["version"] if latest else None,
        "enabled": bool(config.load_settings().get("update_check", True)),
        "last_check": state["last_check"], "state": state["state"], "progress": round(state["progress"], 3),
        "error": state["error"], "can_install": available and not reason, "reason": reason if available else None,
    }


def _busy_reason() -> str | None:
    from .downloads import downloads
    from .jobs import jobs
    if any(j.status in ("queued", "running") for j in jobs.jobs.values()):
        return "Something is being made right now. Update when it has finished."
    if any(t["status"] in ("queued", "downloading", "extracting") for t in downloads.tasks.values()):
        return "A model is downloading. Update when it has finished."
    return None


def check(force: bool = False) -> dict:
    """Ask GitHub for the latest release (at most once a day unless forced)."""
    if not force and state["last_check"] and time.time() - state["last_check"] < CHECK_EVERY:
        return status()
    if state["state"] in ("downloading", "verifying", "installing"):
        return status()
    with _lock:
        state["state"], state["error"] = "checking", None
        try:
            r = httpx.get(API_URL, headers={"User-Agent": f"Featherbox/{config.APP_VERSION}",
                                            "Accept": "application/vnd.github+json"},
                          timeout=15, follow_redirects=True)
            r.raise_for_status()
            rel = r.json()
            exe = next((a for a in rel.get("assets", []) if a.get("name", "").lower().endswith(".exe")), None)
            state["latest"] = {
                "version": rel.get("tag_name", "").lstrip("vV"), "name": rel.get("name") or rel.get("tag_name"),
                "notes": (rel.get("body") or "")[:6000], "url": rel.get("html_url"),
                "published": rel.get("published_at"),
                "download_url": exe.get("browser_download_url") if exe else None,
                "file": exe.get("name") if exe else None, "size": exe.get("size") if exe else None,
                "sha256": ((exe.get("digest") or "").removeprefix("sha256:") or None) if exe else None,
            }
            state["last_check"] = time.time()
            state["state"] = "idle"
            _save()
        except Exception as exc:          # offline, rate-limited, GitHub down: try again later, quietly
            state["state"], state["error"] = "idle", f"Couldn't check for updates: {type(exc).__name__}"
            state["last_check"] = time.time() - CHECK_EVERY + 3600     # retry in about an hour
    return status()


def start_install() -> dict:
    st = status()
    if not st["available"]:
        raise RuntimeError("You already have the latest version.")
    if not st["can_install"]:
        raise RuntimeError(st["reason"] or "Can't update right now.")
    if state["state"] in ("downloading", "verifying", "installing"):
        return st
    state.update(state="downloading", progress=0.0, error=None)
    threading.Thread(target=_install, args=(dict(state["latest"]), install_root()), daemon=True).start()
    return status()


def _install(rel: dict, root: Path) -> None:
    folder = Path(tempfile.gettempdir()) / "Featherbox-update"   # outside the install folder: setup stops
    folder.mkdir(exist_ok=True)                                     # everything that runs from there
    exe = folder / (rel["file"] or f"Featherbox-Setup-{rel['version']}.exe")
    part = exe.with_name(exe.name + ".part")
    try:
        sha = hashlib.sha256()
        with httpx.stream("GET", rel["download_url"], headers={"User-Agent": f"Featherbox/{config.APP_VERSION}"},
                          follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0)) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or rel.get("size") or 0)
            got = 0
            with open(part, "wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
                    sha.update(chunk)
                    got += len(chunk)
                    if total:
                        state["progress"] = min(0.99, got / total)
        state["state"] = "verifying"
        if rel.get("size") and got != rel["size"]:
            raise RuntimeError("The download was incomplete. Please try again.")
        if rel.get("sha256") and sha.hexdigest().lower() != rel["sha256"].lower():
            raise RuntimeError("The downloaded installer didn't match its checksum, so it was not run.")
        part.replace(exe)
        state.update(state="installing", progress=1.0)
        time.sleep(0.5)            # let the window see "installing" before the app goes away
        # Detached, so it keeps running when setup closes this backend.
        flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        args = [str(exe), "/S", "/UPDATE", f"/D={root}"]
        global installer_pid
        try:
            proc = subprocess.Popen(args, cwd=folder, creationflags=flags | 0x01000000, close_fds=True)  # + BREAKAWAY_FROM_JOB
        except OSError:
            proc = subprocess.Popen(args, cwd=folder, creationflags=flags, close_fds=True)
        installer_pid = proc.pid
    except Exception as exc:
        part.unlink(missing_ok=True)
        state.update(state="error", error=str(exc)[:300])


def _auto_loop() -> None:
    time.sleep(20)                 # let the app start first
    while True:
        try:
            if config.load_settings().get("update_check", True):
                check()
        except Exception:
            pass
        time.sleep(3600)


def start_background() -> None:
    _load()
    for old in (Path(tempfile.gettempdir()) / "Featherbox-update").glob("*.exe"):
        try:
            old.unlink()           # installer of an update that has already been applied
        except OSError:
            pass
    threading.Thread(target=_auto_loop, daemon=True, name="update-check").start()
