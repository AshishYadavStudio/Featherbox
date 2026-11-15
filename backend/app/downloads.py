"""Resumable downloader that installs catalog components."""
from __future__ import annotations

import shutil
import threading
import time
import zipfile
from pathlib import Path

import httpx

from . import config
from .catalog import Component

_UA = {"User-Agent": f"Featherbox/{config.APP_VERSION}"}


class _Cancelled(Exception):
    pass


class DownloadManager:
    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self._lock = threading.Lock()

    def status(self, cid: str) -> dict | None:
        return self.tasks.get(cid)

    def start(self, comp: Component) -> dict:
        with self._lock:
            t = self.tasks.get(comp.id)
            if t and t["status"] in ("queued", "downloading", "extracting"):
                return t
            t = {"id": comp.id, "status": "queued", "done": 0, "total": comp.size_mb * 1024 * 1024,
                 "speed": 0.0, "file": "", "error": None, "cancel": False}
            self.tasks[comp.id] = t
        threading.Thread(target=self._run, args=(comp, t), daemon=True).start()
        return t

    def cancel(self, cid: str) -> None:
        if cid in self.tasks:
            self.tasks[cid]["cancel"] = True

    def uninstall(self, comp: Component) -> None:
        from .catalog import shared_paths_in_use
        keep = shared_paths_in_use(comp)
        comp.marker.unlink(missing_ok=True)
        for f in comp.files:
            if f.dest in keep:
                continue
            if f.extract_to:
                shutil.rmtree(config.DATA / f.extract_to, ignore_errors=True)
            else:
                (config.DATA / f.dest).unlink(missing_ok=True)
        self.tasks.pop(comp.id, None)

    # ------------------------------------------------------------------
    def _run(self, comp: Component, t: dict) -> None:
        sizes = [f.size_mb * 1024 * 1024 for f in comp.files]
        try:
            t["status"] = "downloading"
            for i, f in enumerate(comp.files):
                dest = config.DATA / f.dest
                dest.parent.mkdir(parents=True, exist_ok=True)
                t["file"] = Path(f.dest).name
                base = sum(sizes[:i])

                def on_total(n: int, i=i) -> None:
                    sizes[i] = n
                    t["total"] = sum(sizes)

                if not dest.exists():
                    self._fetch(f.url, dest, t, base, on_total)
                else:
                    on_total(dest.stat().st_size)
                t["done"] = sum(sizes[: i + 1])
                if f.extract_to:
                    t["status"] = "extracting"
                    out = config.DATA / f.extract_to
                    out.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(dest) as zf:
                        zf.extractall(out)
                    dest.unlink(missing_ok=True)
                    t["status"] = "downloading"
            comp.marker.parent.mkdir(parents=True, exist_ok=True)
            comp.marker.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), "utf-8")
            t["status"], t["done"] = "done", t["total"]
        except _Cancelled:
            t["status"] = "cancelled"
        except Exception as exc:
            t["status"], t["error"] = "error", f"{type(exc).__name__}: {exc}"

    def _fetch(self, url: str, dest: Path, t: dict, base: int, on_total) -> None:
        part = dest.with_name(dest.name + ".part")
        pos = part.stat().st_size if part.exists() else 0
        headers = dict(_UA)
        if pos:
            headers["Range"] = f"bytes={pos}-"
        timeout = httpx.Timeout(30.0, read=120.0)
        with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=timeout) as r:
            if r.status_code == 416:          # .part already holds the whole file
                part.replace(dest)
                return
            r.raise_for_status()
            if pos and r.status_code != 206:  # server ignored Range: restart
                pos = 0
            length = int(r.headers.get("content-length") or 0)
            if length:
                on_total(length + pos)
            got, last_t, last_b = pos, time.time(), pos
            with open(part, "ab" if pos else "wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    if t["cancel"]:
                        raise _Cancelled()
                    fh.write(chunk)
                    got += len(chunk)
                    t["done"] = base + got
                    now = time.time()
                    if now - last_t >= 1.0:
                        inst = (got - last_b) / (now - last_t)
                        t["speed"] = inst if not t["speed"] else 0.7 * t["speed"] + 0.3 * inst
                        last_t, last_b = now, got
        part.replace(dest)


downloads = DownloadManager()
