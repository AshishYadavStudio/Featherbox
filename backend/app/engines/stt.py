"""Speech-to-text with whisper.cpp (`whisper-cli`).

The CPU engine works everywhere and never competes with image/video jobs for the graphics card.
On NVIDIA PCs the optional CUDA engine is far faster (Turbo needs only about 1 GB of graphics memory).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

from .. import catalog, config, hardware
from ..jobs import Cancelled

_PROGRESS = re.compile(r"progress\s*=\s*(\d+)%")

# Languages offered in the UI (Whisper knows 99; these are the ones people here ask for most).
LANGUAGES = {
    "auto": "Detect automatically", "en": "English", "hi": "Hindi", "bn": "Bengali", "mr": "Marathi",
    "ta": "Tamil", "te": "Telugu", "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "pa": "Punjabi",
    "ur": "Urdu", "ne": "Nepali", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese",
    "it": "Italian", "ru": "Russian", "ar": "Arabic", "ja": "Japanese", "ko": "Korean", "zh": "Chinese",
}


class STTEngine:
    def uses_gpu(self) -> bool:
        try:
            return catalog.engine_exe("stt", hardware.detect()["backend"])[1] == "cuda"
        except RuntimeError:
            return False

    def default_model(self) -> str:
        have = catalog.installed("stt")
        if not have:
            raise RuntimeError("No speech-to-text model installed. Open Models → Speech to text.")
        wanted = config.load_settings().get("stt_model")
        if wanted and any(c.id == wanted for c in have):
            return wanted
        # best installed model that is still quick: Turbo, then Small, then whatever is there
        for pick in ("stt-whisper-turbo", "stt-whisper-small"):
            if any(c.id == pick for c in have):
                return pick
        return have[0].id

    def transcribe(self, wav16k: Path, out_base: Path, model_id: str | None = None, language: str = "auto",
                   translate: bool = False, prompt: str = "",
                   on_progress: Callable[[float], None] | None = None,
                   is_cancelled: Callable[[], bool] | None = None) -> dict:
        """Transcribe a 16 kHz mono WAV. Returns {language, spoken, engine, segments: [{start, end, text}]}."""
        hw = hardware.detect()
        exe, backend = catalog.engine_exe("stt", hw["backend"])
        comp = catalog.get(model_id or self.default_model())
        if comp.category != "stt" or not comp.is_installed():
            raise RuntimeError("That speech-to-text model isn't installed. Open Models → Speech to text.")
        model = next(config.DATA / f.dest for f in comp.files if f.role == "model")
        vad = next((config.DATA / f.dest for f in comp.files if f.role == "vad"), None)
        args = ["-m", str(model), "-f", str(wav16k), "-of", str(out_base), "-oj", "-pp", "-np",
                "-t", str(max(2, hw["cores"])), "-l", language if language in LANGUAGES else "auto"]
        if translate:
            args.append("-tr")
        if prompt.strip():
            args += ["--prompt", prompt.strip()[:500]]
        if vad and vad.exists():
            args += ["--vad", "-vm", str(vad)]
        run = lambda exe, cuda: self._run(exe, args, language, translate, out_base, on_progress, is_cancelled, cuda)
        if backend == "cuda":
            from .llm import llm
            if hardware.free_vram_mb() < comp.size_mb * 2 + 600:
                llm.stop()          # small cards can't hold the text model and Whisper together
            try:
                return run(exe, True)
            except RuntimeError:
                cpu = catalog.find_exe("whisper-cpu", ("whisper-cli.exe",))
                if not (cpu and catalog.get("whisper-cpu").is_installed()):
                    raise
                exe = cpu           # out of graphics memory or a driver problem: the CPU engine still works
        return run(exe, False)

    def _run(self, exe: Path, args: list[str], language: str, translate: bool, out_base: Path,
             on_progress, is_cancelled, cuda: bool) -> dict:
        cmd = [str(exe), *args]
        env = dict(os.environ)
        if cuda:   # cuBLAS / cudart come from the llama.cpp CUDA runtime folder
            dlls = sorted((config.BIN / "llama-cuda").rglob("cublas64_*.dll"), key=lambda q: len(q.parts))
            if dlls:
                env["PATH"] = f"{dlls[0].parent}{os.pathsep}{env.get('PATH', '')}"
        if on_progress:
            on_progress(0.0)
        log = config.LOGS / "whisper.log"
        proc = subprocess.Popen(cmd, cwd=exe.parent, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=config.NO_WINDOW, env=env)
        tail: list[str] = []
        with open(log, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"\n=== {time.strftime('%H:%M:%S')} {' '.join(cmd)}\n")
            assert proc.stdout
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                fh.write(line + "\n")
                tail = (tail + [line])[-15:]
                m = _PROGRESS.search(line)
                if m and on_progress:
                    on_progress(int(m.group(1)) / 100)
                if is_cancelled and is_cancelled():
                    proc.kill()
                    proc.wait()
                    raise Cancelled()
        if proc.wait() != 0:
            hint = next((t for t in reversed(tail) if "error" in t.lower()), tail[-1] if tail else "")
            raise RuntimeError(f"Speech-to-text engine failed: {hint[:300]}")
        out_json = Path(f"{out_base}.json")
        data = json.loads(out_json.read_text("utf-8", errors="replace"))
        out_json.unlink(missing_ok=True)
        segs = []
        for s in data.get("transcription", []):
            text = re.sub(r"^[-–]\s*", "", s.get("text", "").strip())   # Whisper marks speaker turns with "- "
            if text:
                off = s.get("offsets", {})
                segs.append({"start": round(off.get("from", 0) / 1000, 2), "end": round(off.get("to", 0) / 1000, 2),
                             "text": text})
        lang = data.get("result", {}).get("language") or (language if language != "auto" else "")
        return {"language": "en" if translate else lang, "spoken": lang, "segments": segs,
                "engine": "cuda" if cuda else "cpu"}


stt = STTEngine()
