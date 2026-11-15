"""Featherbox benchmark: measures how the installed app performs on this PC.

It talks to the running app (http://127.0.0.1:8420) exactly like the interface does, times each kind of
model with the models you have installed, samples CPU / RAM / GPU use while they run, and writes a report
folder (and .zip) on your Desktop that you can send back.

Run it with Run-Benchmark.bat (uses the Python bundled with Featherbox, nothing to install), or:
    python featherbox_bench.py [--full] [--video] [--all-image-models] [--keep] [--port 8420]

    --full              also make a complete Studio Short (script, voice, pictures, captions, music)
    --video             also make one AI video clip (can take a long time without an NVIDIA GPU)
    --all-image-models  one picture with every installed image model (default: only the one in use)
    --keep              keep the test pictures/recordings in your Featherbox galleries
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

try:
    import httpx
    import psutil
except ImportError:
    sys.exit("This script needs Featherbox's bundled Python. Start it with Run-Benchmark.bat.")

BENCH_VERSION = "1.0"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
ENGINE_PROCS = {"llama-server.exe": "text", "sd-cli.exe": "image", "sd.exe": "image", "whisper-cli.exe": "speech",
                "ffmpeg.exe": "ffmpeg"}

EN_TEXT = ("Featherbox runs every kind of AI model on your own computer. It can write a script, paint a picture "
           "for every scene, record a natural voiceover and turn speech back into text. Everything stays on this "
           "laptop, and nothing is uploaded to the internet. Today we are measuring how fast it works on this machine.")
HI_TEXT = ("फेदरबॉक्स आपके अपने कंप्यूटर पर हर तरह का एआई मॉडल चलाता है। यह स्क्रिप्ट लिख सकता है, तस्वीरें बना सकता है "
           "और आवाज़ रिकॉर्ड कर सकता है। सब कुछ इसी लैपटॉप पर रहता है और इंटरनेट पर कुछ भी नहीं भेजा जाता।")
CHAT_PROMPT = ("In about 150 words, explain to a student why the sky looks blue during the day and orange at sunset. "
               "Use simple language.")
IMAGE_PROMPT = "A cozy tea stall on a rainy Mumbai street at dusk, warm yellow lights, steam rising, reflections on the wet road"
VIDEO_PROMPT = "Waves rolling onto a sandy beach at sunset, gentle camera push in"


# ---------------------------------------------------------------- helpers
def log(msg: str = "") -> None:
    print(msg, flush=True)


def fmt_s(s: float | None) -> str:
    if s is None:
        return "—"
    return f"{s:.1f} s" if s < 90 else f"{int(s // 60)} min {int(s % 60):02d} s"


def gb(b: float | None) -> str:
    return "—" if b is None else f"{b / 1024**3:.1f} GB"


def find_install() -> Path | None:
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Featherbox") as k:
            return Path(winreg.QueryValueEx(k, "InstallLocation")[0])
    except OSError:
        p = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Featherbox"
        return p if p.exists() else None


def powershell(script: str, timeout: float = 60) -> str:
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                              capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def words(text: str) -> list[str]:
    return [w for w in (re.sub(r"[.,!?।;:\"'“”‘’()\-–—]", " ", text.lower())).split() if w]


def word_accuracy(ref: str, hyp: str) -> float:
    """1 - word error rate (word-level edit distance), clamped to 0..100 %."""
    r, h = words(ref), words(hyp)
    if not r:
        return 0.0
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hw in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw))
        prev = cur
    return max(0.0, 100.0 * (1 - prev[-1] / len(r)))


# ---------------------------------------------------------------- resource sampling
class Sampler:
    """CPU / RAM every second (psutil) and GPU busy % + GPU memory every ~2 s (Windows performance counters)."""

    GPU_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$c = @('\GPU Engine(*engtype_3D)\Utilization Percentage', '\GPU Engine(*engtype_Compute*)\Utilization Percentage',
       '\GPU Adapter Memory(*)\Shared Usage', '\GPU Adapter Memory(*)\Dedicated Usage')
Get-Counter -Counter $c -SampleInterval 2 -Continuous | ForEach-Object {
  $s = $_.CounterSamples
  $u = ($s | Where-Object { $_.Path -like '*utilization percentage' } | Measure-Object CookedValue -Sum).Sum
  $sh = ($s | Where-Object { $_.Path -like '*shared usage' } | Measure-Object CookedValue -Sum).Sum
  $de = ($s | Where-Object { $_.Path -like '*dedicated usage' } | Measure-Object CookedValue -Sum).Sum
  [Console]::WriteLine("$([DateTimeOffset]::Now.ToUnixTimeMilliseconds()) $u $sh $de")
}
"""

    def __init__(self) -> None:
        self.cpu: list[tuple[float, float, float, dict]] = []   # t, cpu %, ram used bytes, engine rss by kind
        self.gpu: list[tuple[float, float, float, float]] = []  # t, busy %, shared bytes, dedicated bytes
        self._stop = False
        self._gpu_proc = None

    def start(self) -> None:
        psutil.cpu_percent(None)
        threading.Thread(target=self._cpu_loop, daemon=True).start()
        if os.name == "nt":
            enc = base64.b64encode(self.GPU_PS.encode("utf-16-le")).decode()
            try:
                self._gpu_proc = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                                  creationflags=NO_WINDOW)
                threading.Thread(target=self._gpu_loop, daemon=True).start()
            except OSError:
                self._gpu_proc = None

    def stop(self) -> None:
        self._stop = True
        if self._gpu_proc:
            self._gpu_proc.kill()

    def _cpu_loop(self) -> None:
        while not self._stop:
            time.sleep(1)
            rss: dict[str, int] = {}
            for p in psutil.process_iter(["name", "memory_info"]):
                kind = ENGINE_PROCS.get((p.info["name"] or "").lower())
                if kind and p.info["memory_info"]:
                    rss[kind] = rss.get(kind, 0) + p.info["memory_info"].rss
            self.cpu.append((time.time(), psutil.cpu_percent(None), psutil.virtual_memory().used, rss))

    def _gpu_loop(self) -> None:
        assert self._gpu_proc and self._gpu_proc.stdout
        for line in self._gpu_proc.stdout:
            parts = line.split()
            if len(parts) == 4:
                try:
                    t, u, sh, de = (float(x.replace(",", ".")) for x in parts)
                    self.gpu.append((t / 1000, u, sh, de))
                except ValueError:
                    pass
            if self._stop:
                break

    def window(self, t0: float, t1: float) -> dict:
        c = [s for s in self.cpu if t0 <= s[0] <= t1 + 1]
        g = [s for s in self.gpu if t0 <= s[0] <= t1 + 2]
        out: dict = {}
        if c:
            out["cpu_avg"] = round(statistics.mean(s[1] for s in c), 1)
            out["ram_peak"] = max(s[2] for s in c)
            kinds = {k for s in c for k in s[3]}
            out["engine_rss_peak"] = {k: max(s[3].get(k, 0) for s in c) for k in kinds}
        if g:
            out["gpu_busy_avg"] = round(statistics.mean(min(100.0, s[1]) for s in g), 1)
            out["gpu_busy_peak"] = round(max(min(100.0, s[1]) for s in g), 1)
            out["gpu_shared_peak"] = max(s[2] for s in g)
            out["gpu_dedicated_peak"] = max(s[3] for s in g)
        return out


# ---------------------------------------------------------------- the benchmark
class Bench:
    def __init__(self, args) -> None:
        self.args = args
        self.base = f"http://127.0.0.1:{args.port}"
        self.http = httpx.Client(base_url=self.base, timeout=httpx.Timeout(900.0, connect=10.0))
        self.install = find_install()
        self.data = Path(args.data) if args.data else Path(os.environ.get("FEATHERBOX_DATA") or
                                                            (self.install / "data" if self.install else "data"))
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
        name = f"Featherbox-benchmark-{platform.node()}-{stamp}"
        desktop = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"
        self.out = Path(args.out) if args.out else (desktop if desktop.exists() else Path.cwd()) / name
        (self.out / "outputs").mkdir(parents=True, exist_ok=True)
        self.sampler = Sampler()
        self.results: list[dict] = []
        self.made: list[tuple[str, str]] = []      # creations to clean up: (kind, id)
        self.projects: list[str] = []
        self.report: dict = {"bench_version": BENCH_VERSION, "started": dt.datetime.now().isoformat(timespec="seconds"),
                             "args": vars(args), "results": self.results}

    # ---------- plumbing
    def api(self, method: str, path: str, **kw):
        r = self.http.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:300]
            raise RuntimeError(f"{r.status_code}: {detail}")
        return r.json() if "json" in r.headers.get("content-type", "") else r

    def wait_job(self, job: dict, label: str) -> dict:
        last = ""
        while True:
            j = self.api("GET", f"/api/jobs/{job['id']}")
            line = f"   {label}: {int(j['progress'] * 100):3d}%  {j['stage']}"
            if line != last:
                print("\r" + line[:110].ljust(110), end="", flush=True)
                last = line
            if j["status"] in ("done", "error", "cancelled"):
                print()
                if j["status"] != "done":
                    raise RuntimeError(j.get("error") or j["status"])
                return j
            time.sleep(1)

    def run(self, name: str, kind: str, fn, **extra) -> dict:
        log(f"\n▶ {name}")
        rec = {"name": name, "kind": kind, **extra}
        t0 = time.time()
        try:
            rec.update(fn() or {})
            rec["status"] = "ok"
        except Exception as exc:          # one failed test must not stop the others
            rec["status"], rec["error"] = "error", str(exc)[:600]
            log(f"   ✗ {rec['error']}")
        rec["seconds"] = rec.get("seconds") or round(time.time() - t0, 1)
        rec["resources"] = self.sampler.window(t0, time.time())
        if rec["status"] == "ok":
            log(f"   ✓ {fmt_s(rec['seconds'])}" + (f" · {rec['summary']}" if rec.get("summary") else ""))
        self.results.append(rec)
        return rec

    def skip(self, name: str, kind: str, why: str) -> None:
        log(f"\n▷ {name}: skipped ({why})")
        self.results.append({"name": name, "kind": kind, "status": "skipped", "error": why})

    # ---------- setup
    def connect(self) -> None:
        for attempt in range(2):
            try:
                self.system = self.api("GET", "/api/system")
                return
            except (httpx.HTTPError, RuntimeError):
                if attempt == 0 and self.install and (self.install / "Featherbox.exe").exists():
                    log("Featherbox isn't running, starting it…")
                    subprocess.Popen([str(self.install / "Featherbox.exe")])
                    for _ in range(90):
                        time.sleep(2)
                        try:
                            self.http.get("/api/system", timeout=3)
                            break
                        except httpx.HTTPError:
                            pass
        sys.exit(f"Can't reach Featherbox at {self.base}. Open Featherbox, wait for its window, then run this again.")

    def collect_system(self) -> None:
        log("Collecting system information…")
        info = powershell(r"""
$o = [ordered]@{}
$os = Get-CimInstance Win32_OperatingSystem
$o.os = "$($os.Caption) $($os.Version) build $($os.BuildNumber)"
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$o.cpu = $cpu.Name.Trim(); $o.cores = $cpu.NumberOfCores; $o.threads = $cpu.NumberOfLogicalProcessors; $o.cpu_max_mhz = $cpu.MaxClockSpeed
$o.gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object { [ordered]@{ name = $_.Name; driver = $_.DriverVersion; driver_date = "$($_.DriverDate)"; ram_mb = [int]($_.AdapterRAM / 1MB) } })
$o.ram_modules = @(Get-CimInstance Win32_PhysicalMemory | ForEach-Object { [ordered]@{ gb = [int]($_.Capacity / 1GB); speed = $_.ConfiguredClockSpeed } })
$o.power_plan = ((powercfg /getactivescheme) -replace '.*\((.*)\).*', '$1')
$o.vulkan_runtime = Test-Path "$env:WINDIR\System32\vulkan-1.dll"
$o.model = (Get-CimInstance Win32_ComputerSystem).Model
$o | ConvertTo-Json -Depth 4
""")
        try:
            win = json.loads(info) if info else {}
        except ValueError:
            win = {"raw": info[:2000]}
        bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        win["on_battery"] = bool(bat and not bat.power_plugged)
        win["battery_percent"] = bat.percent if bat else None
        win["ram_total"] = psutil.virtual_memory().total
        win["ram_free_at_start"] = psutil.virtual_memory().available
        try:
            win["disk_free"] = shutil.disk_usage(self.data if self.data.exists() else Path.home()).free
        except OSError:
            pass
        cat = {}
        try:
            cat = self.api("GET", "/api/catalog")
        except Exception as exc:
            log(f"   (catalog unavailable: {exc})")
        installed = [{"id": c["id"], "name": c["name"], "category": c["category"], "kind": c["kind"],
                      "fit": c.get("fit"), "reason": c.get("reason")} for c in cat.get("components", []) if c.get("installed")]
        self.report.update({"windows": win, "featherbox": {
            "version": self.system.get("version"), "hardware": self.system.get("hardware"),
            "ready": self.system.get("ready"), "settings": self.system.get("settings"),
            "install": str(self.install) if self.install else None, "data": str(self.data)},
            "installed": installed, "active": cat.get("active", {})})
        hw = self.system.get("hardware", {})
        log(f"   {win.get('cpu', hw.get('cpu'))} · {gb(win['ram_total'])} RAM · "
            f"{', '.join(g['name'] for g in win.get('gpus', [])) or 'GPU ?'}")
        log(f"   Featherbox {self.system.get('version')} · engine backend: {hw.get('backend')}"
            f"{' · ON BATTERY (plug in for real numbers!)' if win['on_battery'] else ''}")

    # ---------- tests
    def t_text(self, cold: bool) -> dict:
        if cold:
            self.api("POST", "/api/llm/unload")
            time.sleep(1)
        llog = self.data / "logs" / "llama-server.log"
        t0 = time.time()
        first = None
        chunks: list[str] = []
        with self.http.stream("POST", "/api/chat", json={"messages": [{"role": "user", "content": CHAT_PROMPT}],
                                                         "temperature": 0.3}) as r:
            if r.status_code >= 400:
                r.read()
                raise RuntimeError(f"{r.status_code}: {r.text[:300]}")
            for piece in r.iter_text():
                if piece and first is None:
                    first = time.time() - t0
                chunks.append(piece)
        total = time.time() - t0
        text = "".join(chunks)
        if "[Error:" in text:
            raise RuntimeError(text[text.index("[Error:"):][:400])
        out = {"seconds": round(total, 1), "first_word_s": round(first or total, 1), "chars": len(text),
               "model": self.system.get("settings", {}).get("llm_model") or self.report.get("active", {}).get("llm")}
        try:
            lt = llog.read_text("utf-8", errors="replace")
            pe = re.findall(r"prompt eval time =\s*([\d.]+) ms /\s*(\d+) tokens.*?([\d.]+) tokens per second", lt)
            ev = re.findall(r"(?<!prompt )eval time =\s*([\d.]+) ms /\s*(\d+) tokens.*?([\d.]+) tokens per second", lt)
            if ev:
                out["gen_tokens"], out["gen_tok_s"] = int(ev[-1][1]), float(ev[-1][2])
            if pe:
                out["prompt_tok_s"] = float(pe[-1][2])
            m = re.findall(r"offloaded (\d+)/(\d+) layers to GPU", lt)
            if m:
                out["gpu_layers"] = f"{m[-1][0]}/{m[-1][1]}"
            dev = re.findall(r"using device (\w+) \(([^)]+)\)", lt) or re.findall(r"ggml_vulkan: \d+ = ([^|(\n]+)", lt)
            if dev:
                out["device"] = " ".join(dev[-1]) if isinstance(dev[-1], tuple) else dev[-1].strip()
        except OSError:
            pass
        if cold:
            out["load_s"] = round(max(0.0, (first or total) - 0.5), 1)
        tps = out.get("gen_tok_s")
        out["summary"] = (f"{tps:.1f} tokens/s" if tps else f"{len(text)} chars") + f", first words after {fmt_s(out['first_word_s'])}"
        (self.out / "outputs" / f"chat-{'cold' if cold else 'warm'}.txt").write_text(text, "utf-8")
        return out

    def t_script(self, language: str) -> dict:
        t0 = time.time()
        j = self.wait_job(self.api("POST", "/api/studio/script", json={
            "topic": "Why the sky is blue" if language == "english" else "चाय भारत में कैसे आई",
            "language": language, "seconds": 45}), "script")
        sc = j["result"]
        (self.out / "outputs" / f"script-{language}.json").write_text(json.dumps(sc, ensure_ascii=False, indent=2), "utf-8")
        if language == "english":
            self._script = sc
        n = len(sc.get("scenes", []))
        return {"seconds": round(time.time() - t0, 1), "scenes": n, "summary": f"{n} scenes"}

    def t_image(self, model: dict) -> dict:
        t0 = time.time()
        j = self.wait_job(self.api("POST", "/api/tools/image", json={
            "prompt": IMAGE_PROMPT, "model": model["id"], "aspect": "portrait", "style": "none", "count": 1, "seed": 42}),
            model["name"])
        it = j["result"]["items"][0]
        self.made.append(("image", it["id"]))
        self._fetch(it["url"], f"image-{model['id']}.png")
        self._last_image = it["id"]
        return {"seconds": round(time.time() - t0, 1), "engine_seconds": it.get("seconds"), "model": model["id"],
                "size": f"{it.get('width')}×{it.get('height')}", "summary": f"{it.get('width')}×{it.get('height')}"}

    def t_voice(self, text: str, voice: str, tag: str) -> dict:
        t0 = time.time()
        c = self.api("POST", "/api/tools/voice", json={"text": text, "voice": voice, "speed": 1.0})
        took = time.time() - t0
        self.made.append(("voice", c["id"]))
        self._fetch(c["url"], f"voice-{tag}.wav")
        setattr(self, f"_voice_{tag}", self.out / "outputs" / f"voice-{tag}.wav")
        rtf = took / max(0.1, c.get("duration", 1))
        return {"seconds": round(took, 1), "audio_s": c.get("duration"), "realtime_x": round(1 / rtf, 1),
                "summary": f"{c.get('duration')} s of speech, {1 / rtf:.1f}× faster than real time"}

    def t_transcribe(self, wav: Path, ref: str, language: str, tag: str) -> dict:
        up = self.api("POST", f"/api/creations/media?name=bench-{tag}.wav", content=wav.read_bytes())
        t0 = time.time()
        j = self.wait_job(self.api("POST", "/api/tools/transcribe", json={
            "media": up["ref"], "title": f"Benchmark {tag}", "language": language}), f"transcribe {tag}")
        it = j["result"]["items"][0]
        self.made.append(("transcript", it["id"]))
        (self.out / "outputs" / f"transcript-{tag}.txt").write_text(it.get("text", ""), "utf-8")
        acc = word_accuracy(ref, it.get("text", ""))
        took = time.time() - t0
        return {"seconds": round(took, 1), "audio_s": it.get("duration"), "engine": it.get("engine", "cpu"),
                "model": it.get("model"), "word_accuracy": round(acc, 1),
                "realtime_x": round((it.get("duration") or 0) / max(0.1, took), 1),
                "summary": f"{acc:.0f}% words right, {(it.get('duration') or 0) / max(0.1, took):.1f}× real time on {it.get('engine', 'cpu').upper()}"}

    def t_short(self) -> dict:
        script = getattr(self, "_script", None)
        if not script:
            raise RuntimeError("needs the English script from the earlier test")
        t0 = time.time()
        j = self.wait_job(self.api("POST", "/api/studio/render", json={"script": script, "style": "cinematic",
                                                                       "motion": "still", "seed": 42}), "short")
        res = j["result"]
        self.projects.append(res["project"])
        self._fetch(res["thumb"], "short-thumb.jpg")
        if self.args.keep:
            self._fetch(res["video"], "short.mp4")
        return {"seconds": round(time.time() - t0, 1), "video_s": res.get("duration"),
                "scenes": len(script.get("scenes", [])), "summary": f"{res.get('duration')} s video"}

    def t_video(self, model: dict) -> dict:
        t0 = time.time()
        j = self.wait_job(self.api("POST", "/api/tools/video", json={"prompt": VIDEO_PROMPT, "model": model["id"],
                                                                     "aspect": "portrait", "seed": 42}), model["name"])
        it = j["result"]["items"][0]
        self.made.append(("video", it["id"]))
        self._fetch(it["url"], "clip.mp4")
        return {"seconds": round(time.time() - t0, 1), "model": model["id"], "quality": it.get("quality"),
                "summary": f"{it.get('duration')} s clip, {it.get('width')}×{it.get('height')}"}

    def _fetch(self, url: str, name: str) -> None:
        try:
            r = self.http.get(url)
            if r.status_code == 200:
                (self.out / "outputs" / name).write_bytes(r.content)
        except httpx.HTTPError:
            pass

    # ---------- main
    def main(self) -> None:
        log(f"Featherbox benchmark {BENCH_VERSION}\nReport folder: {self.out}\n")
        self.connect()
        self.collect_system()
        self.sampler.start()
        time.sleep(2)
        self.report["idle"] = self.sampler.window(time.time() - 2.5, time.time())

        ready = self.system.get("ready", {})
        try:
            opts = self.api("GET", "/api/tools/options")
        except Exception:
            opts = None

        # text
        if ready.get("llm"):
            self.run("Text: chat, first answer (includes loading the model)", "text", lambda: self.t_text(True))
            self.run("Text: chat, model already loaded", "text", lambda: self.t_text(False))
            self.run("Studio: write an English script", "script", lambda: self.t_script("english"))
            self.run("Studio: write a Hindi script", "script", lambda: self.t_script("hindi"))
        else:
            self.skip("Text", "text", "no text model or engine installed")

        # image
        if opts and opts.get("image_models"):
            models = opts["image_models"]
            if not self.args.all_image_models:
                models = [m for m in models if m["id"] == opts["active"].get("image")] or models[:1]
            for m in models:
                self.run(f"Image: {m['name']}, portrait", "image", lambda m=m: self.t_image(m), model=m["id"])
        elif opts is None:
            self.skip("Image", "image", "this Featherbox version has no Image tool (update to 0.2+)")
        else:
            self.skip("Image", "image", "no image model installed")

        # voice + transcribe
        if ready.get("tts") and opts is not None:
            self.run("Voice: English paragraph", "voice", lambda: self.t_voice(EN_TEXT, "af_heart", "en"))
            self.run("Voice: Hindi paragraph", "voice", lambda: self.t_voice(HI_TEXT, "hf_alpha", "hi"))
        else:
            self.skip("Voice", "voice", "Kokoro voice model not installed")
        try:
            topts = self.api("GET", "/api/tools/transcribe/options")
        except Exception:
            topts = None
        if topts and topts.get("models") and topts.get("engine"):
            for tag, ref, lang in (("en", EN_TEXT, "en"), ("hi", HI_TEXT, "hi")):
                wav = getattr(self, f"_voice_{tag}", None)
                if wav and wav.exists():
                    self.run(f"Transcribe: {'English' if tag == 'en' else 'Hindi'} recording", "transcribe",
                             lambda wav=wav, ref=ref, lang=lang, tag=tag: self.t_transcribe(wav, ref, lang, tag))
        else:
            self.skip("Transcribe", "transcribe", "speech-to-text not installed (Models → Speech to text)")

        if self.args.full:
            if ready.get("llm") and ready.get("image") and ready.get("tts"):
                self.run("Studio: make a complete Short (still pictures)", "short", self.t_short)
            else:
                self.skip("Studio Short", "short", "needs text, image and voice models")
        if self.args.video:
            vm = (opts or {}).get("video_models") or []
            if vm:
                m = next((x for x in vm if x["id"] == opts["active"].get("video")), vm[0])
                self.run(f"Video: {m['name']} clip", "video", lambda: self.t_video(m), model=m["id"])
            else:
                self.skip("Video", "video", "no video model installed")

        self.sampler.stop()
        self.cleanup()
        self.finish()

    def cleanup(self) -> None:
        if self.args.keep:
            return
        for kind, cid in self.made:
            try:
                self.api("DELETE", f"/api/creations/{kind}/{cid}")
            except Exception:
                pass
        for pid in self.projects:
            try:
                self.api("DELETE", f"/api/projects/{pid}")
            except Exception:
                pass

    def finish(self) -> None:
        self.report["finished"] = dt.datetime.now().isoformat(timespec="seconds")
        logs = self.out / "logs"
        logs.mkdir(exist_ok=True)
        for name in ("llama-server.log", "sd.log", "whisper.log", "app.log"):
            src = self.data / "logs" / name
            if src.exists():
                data = src.read_bytes()[-400_000:]          # the recent part is what matters
                (logs / name).write_bytes(data)
        (self.out / "report.json").write_text(json.dumps(self.report, ensure_ascii=False, indent=2, default=str), "utf-8")
        ref = None
        ref_file = Path(__file__).with_name("reference-rtx3050.json")
        if ref_file.exists() and ref_file.resolve() != (self.out / "report.json").resolve():
            try:
                ref = json.loads(ref_file.read_text("utf-8"))
            except ValueError:
                ref = None
        md = render_markdown(self.report, ref)
        (self.out / "report.md").write_text(md, "utf-8")
        zpath = self.out.with_suffix(".zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for p in self.out.rglob("*"):
                if p.is_file():
                    z.write(p, p.relative_to(self.out.parent))
        log("\n" + "=" * 70)
        log(md)
        log("=" * 70)
        log(f"\nDone. Send this file back:\n   {zpath}\n(The same report is in {self.out / 'report.md'})")


def render_markdown(rep: dict, ref: dict | None = None) -> str:
    w, fb = rep.get("windows", {}), rep.get("featherbox", {})
    hw = fb.get("hardware") or {}
    gpus = "; ".join(f"{g.get('name')} (driver {g.get('driver')})" for g in w.get("gpus", [])) or "?"
    ram_mods = w.get("ram_modules") or []
    ram_speed = f" @ {ram_mods[0].get('speed')} MT/s" if ram_mods and ram_mods[0].get("speed") else ""
    lines = [
        f"# Featherbox benchmark: {w.get('model') or platform.node()}",
        "",
        f"Run {rep.get('started')} · benchmark {rep.get('bench_version')} · Featherbox {fb.get('version')}",
        "",
        "## This PC",
        "",
        "| | |", "|---|---|",
        f"| Processor | {w.get('cpu', hw.get('cpu'))} ({w.get('cores')} cores / {w.get('threads')} threads) |",
        f"| Memory | {gb(w.get('ram_total'))}{ram_speed}, {gb(w.get('ram_free_at_start'))} free at start |",
        f"| Graphics | {gpus} |",
        f"| Windows | {w.get('os')} · power plan: {w.get('power_plan')} · {'**on battery**' if w.get('on_battery') else 'plugged in'} |",
        f"| Featherbox engines | backend **{hw.get('backend')}** (recommended: {hw.get('recommended_backend')}), Vulkan runtime: {w.get('vulkan_runtime')} |",
        f"| Free disk | {gb(w.get('disk_free'))} |",
        "",
        "## Results",
        "",
        f"| Test | Time | {'Reference PC ' if ref else ''}{'| ' if ref else ''}Result | CPU avg | PC RAM in use (peak) | GPU busy avg / peak | GPU memory peak |",
        f"|---|---|{'---|' if ref else ''}---|---|---|---|---|",
    ]
    ref_times = {x["name"]: x for x in (ref or {}).get("results", []) if x.get("status") == "ok"}
    for r in rep.get("results", []):
        res = r.get("resources", {})
        gpu = (f"{res['gpu_busy_avg']:.0f}% / {res['gpu_busy_peak']:.0f}%" if "gpu_busy_avg" in res else "—")
        gmem = max(res.get("gpu_shared_peak", 0), res.get("gpu_dedicated_peak", 0)) if res else 0
        if r["status"] == "ok":
            result = r.get("summary", "")
        elif r["status"] == "skipped":
            result = f"skipped: {r.get('error')}"
        else:
            result = f"**failed:** {(r.get('error') or '')[:160]}"
        rcol = ""
        if ref:
            rr = ref_times.get(r["name"])
            rcol = f"{fmt_s(rr['seconds'])} |" if rr else "— |"
        lines.append(f"| {r['name']} | {fmt_s(r.get('seconds')) if r['status'] == 'ok' else '—'} | {rcol} {result} | "
                     f"{res.get('cpu_avg', '—')}{'%' if 'cpu_avg' in res else ''} | {gb(res.get('ram_peak'))} | {gpu} | "
                     f"{gb(gmem) if gmem else '—'} |")
    text = [r for r in rep.get("results", []) if r["kind"] == "text" and r["status"] == "ok"]
    extra = []
    for r in text:
        bits = [f"{k}={r[k]}" for k in ("gen_tok_s", "prompt_tok_s", "gen_tokens", "gpu_layers", "device", "load_s") if k in r]
        if bits:
            extra.append(f"- {r['name']}: {', '.join(bits)}")
    if extra:
        lines += ["", "### Text engine details", "", *extra]
    if ref:
        rw = ref.get("windows", {})
        lines += ["", f"Reference PC: {rw.get('cpu')}, {', '.join(g.get('name', '') for g in rw.get('gpus', []))}, "
                      f"{gb(rw.get('ram_total'))} RAM (Featherbox {ref.get('featherbox', {}).get('version')})."]
    lines += ["", "## Installed models", ""]
    active = set((rep.get("active") or {}).values())
    for c in rep.get("installed", []):
        if c["kind"] == "model":
            lines.append(f"- {c['name']} ({c['category']}){' · **in use**' if c['id'] in active else ''} · advice: {c.get('fit')}: {c.get('reason')}")
    eng = [c["name"] for c in rep.get("installed", []) if c["kind"] == "engine"]
    lines += ["", f"Engines: {', '.join(eng) or 'none'}", ""]
    warn = []
    if w.get("on_battery"):
        warn.append("The laptop was on battery: Windows slows the CPU and GPU, so these numbers are lower than plugged in.")
    if hw.get("backend") == "cpu" and any("radeon" in (g.get("name") or "").lower() or "intel" in (g.get("name") or "").lower()
                                          for g in w.get("gpus", [])):
        warn.append("Featherbox is using the CPU engines although a Radeon/Intel GPU is present (Vulkan engines not installed?).")
    if warn:
        lines += ["## Notes", "", *[f"- {x}" for x in warn], ""]
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Measure Featherbox performance on this PC.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("FEATHERBOX_PORT", 8420)))
    ap.add_argument("--data", help="Featherbox data folder (found automatically)")
    ap.add_argument("--out", help="report folder (default: on the Desktop)")
    ap.add_argument("--full", action="store_true", help="also make a complete Studio Short")
    ap.add_argument("--video", action="store_true", help="also make one AI video clip (slow without NVIDIA)")
    ap.add_argument("--all-image-models", action="store_true", help="one picture with every installed image model")
    ap.add_argument("--keep", action="store_true", help="keep test creations in the Featherbox galleries")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    Bench(ap.parse_args()).main()
