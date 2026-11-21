"""Text-to-image through the stable-diffusion.cpp command-line binary."""
from __future__ import annotations

import random
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

from .. import catalog, config, hardware, timings
from ..jobs import Cancelled
from .llm import llm

_STEP_RE = re.compile(rb"\|\s*(\d+)/(\d+)([^|\r\n]*)")


_FLAGS = {"model": "-m", "diffusion": "--diffusion-model", "vae": "--vae", "clip_l": "--clip_l",
          "t5xxl": "--t5xxl", "llm": "--llm", "tae": "--tae"}


def model_args(comp: catalog.Component, skip: tuple[str, ...] = ()) -> list[str]:
    """Command-line flags that load every file of a model (checkpoint, VAE, text encoders...)."""
    args: list[str] = []
    for f in comp.files:
        if f.role in skip:
            continue
        args += [_FLAGS[f.role], str(config.DATA / f.dest)]
    return args


class ImageEngine:
    def default_model(self) -> str:
        wanted = config.load_settings().get("image_model")
        have = catalog.installed("image")
        if wanted and any(c.id == wanted for c in have):
            return wanted
        if not have:
            raise RuntimeError("No image model installed. Open Models and run One-click setup.")
        return have[0].id

    def generate(self, prompt: str, out: Path, negative: str = "", width: int | None = None,
                 height: int | None = None, steps: int | None = None, cfg: float | None = None,
                 seed: int = -1, model_id: str | None = None,
                 on_progress: Callable[[float], None] | None = None,
                 is_cancelled: Callable[[], bool] | None = None) -> Path:
        hw = hardware.detect()
        exe, backend = catalog.engine_exe("image", hw["backend"])
        comp = catalog.get(model_id or self.default_model())
        m = comp.meta
        if backend != "cpu":
            llm.stop()            # small GPUs can't hold the text model and the image model at once
        seed = seed if seed >= 0 else random.randint(0, 2**31 - 1)
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [str(exe), *model_args(comp), "-p", prompt,
               "-W", str(width or m["width"]), "-H", str(height or m["height"]),
               "--steps", str(steps or m["steps"]), "--cfg-scale", str(cfg or m["cfg"]),
               "--sampling-method", m.get("sampler", "euler_a"), "-s", str(seed), "-o", str(out)]
        if m.get("cfg", 7) > 1:            # distilled models (cfg 1) ignore the negative prompt
            cmd += ["-n", negative]
        if m.get("scheduler"):
            cmd += ["--scheduler", m["scheduler"]]
        cmd += m.get("extra", [])          # model-specific options, e.g. Qwen-Image Turbo's fixed schedule
        vram = hw.get("vram_mb", 0)
        if backend == "cpu":
            cmd += ["-t", str(hw["cores"])]
        else:
            if vram < 6144:
                cmd += ["--vae-tiling"]
            if vram and vram < m.get("vram_mb", 3000) + 2048 and m.get("arch") != "sd15":
                cmd += ["--offload-to-cpu"]   # keep weights in RAM, stream them to the GPU as needed
        t0 = time.monotonic()
        path = _run_sd(cmd, exe, out, "Image engine", on_progress, is_cancelled)
        timings.record(f"image:{comp.id}", time.monotonic() - t0)   # whole wait: engine start, load, paint
        return path


def _run_sd(cmd: list[str], exe: Path, out: Path, what: str,
            on_progress: Callable[[float], None] | None, is_cancelled: Callable[[], bool] | None,
            phases: list[tuple[float, float]] | None = None, steps: int | None = None) -> Path:
    """Run stable-diffusion.cpp, stream its step counter as progress, log everything to sd.log."""
    log = config.LOGS / "sd.log"
    phase = [0]
    proc = subprocess.Popen(cmd, cwd=exe.parent, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=config.NO_WINDOW)
    tail = b""
    assert proc.stdout
    with open(log, "ab") as fh:
        fh.write(b"\n$ " + " ".join(cmd).encode("utf-8", "replace") + b"\n")
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
            if not chunk:
                break
            fh.write(chunk)
            tail = (tail + chunk)[-4000:]
            # Progress bars look like "|====>   | 6/20 - 6.78s/it". Bars ending in MB/s or GB/s are file
            # loading, not work, so they are skipped. With `phases` = [(lo, hi) sampling, (lo, hi) decode],
            # the bars after "sampling completed" (video VAE decode tiles) fill the second slice.
            if phases and b"sampling completed" in chunk:
                phase[0] = 1
                if on_progress:
                    on_progress(phases[1][0])
            hits = [(int(i), int(n)) for i, n, unit in _STEP_RE.findall(chunk) if b"B/s" not in unit]
            if phases and phase[0] == 0 and steps:
                hits = [h for h in hits if h[1] == steps]     # other short bars before sampling aren't steps
            if hits and on_progress:
                i, n = hits[-1]
                if n:
                    lo, hi = phases[phase[0]] if phases else (0.0, 1.0)
                    on_progress(lo + (hi - lo) * i / n)
            if is_cancelled and is_cancelled():
                proc.kill()
                raise Cancelled()
    rc = proc.wait()
    if rc != 0 or not out.exists():
        text = tail.decode("utf-8", "replace")
        if re.search(r"cannot make enough memory|out of memory|failed to allocate", text, re.I):
            free, total = hardware.free_vram_mb(), hardware.detect().get("vram_mb", 0)
            others = hardware.other_gpu_programs()
            hint = (f" Another program is using the graphics card: {'; '.join(others)}. Close it and try again."
                    if others else " Close other programs that use the graphics card (games, other AI apps) and try again.")
            raise RuntimeError(f"Not enough free graphics memory: only {free} MB of {total} MB is free right now.{hint}")
        msg = text.strip().splitlines()[-8:]
        raise RuntimeError(f"{what} failed:\n" + "\n".join(msg))
    return out


# Wan's standard negative prompt (it was trained with this Chinese list): oversaturated, static, blurry,
# subtitles, still frame, grey, worst quality, JPEG artefacts, deformed hands/faces, extra limbs, crowds...
WAN_NEGATIVE = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，"
                "JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，"
                "形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走")


class VideoEngine:
    """Short AI clips with Wan through stable-diffusion.cpp (`-M vid_gen`)."""

    def installed(self) -> list[catalog.Component]:
        return catalog.installed("video")

    def default_model(self) -> str:
        wanted = config.load_settings().get("video_model")
        have = self.installed()
        if wanted and any(c.id == wanted for c in have):
            return wanted
        if not have:
            raise RuntimeError("No video model installed. Open Models → Video to download one.")
        return have[0].id

    def generate(self, prompt: str, out: Path, init_image: Path | None = None, seed: int = -1,
                 model_id: str | None = None, on_progress: Callable[[float], None] | None = None,
                 is_cancelled: Callable[[], bool] | None = None,
                 width: int | None = None, height: int | None = None) -> Path:
        hw = hardware.detect()
        exe, backend = catalog.engine_exe("image", hw["backend"])
        comp = catalog.get(model_id or self.default_model())
        m = comp.meta
        if backend != "cpu":
            llm.stop()
        seed = seed if seed >= 0 else random.randint(0, 2**31 - 1)
        out.parent.mkdir(parents=True, exist_ok=True)
        # Fast: the tiny TAEHV decoder (22 MB). Measured on a 4 GB RTX 3050: decode 1.4 s instead of ~220 s,
        # a clip in ~2.3 min instead of ~6, slightly softer detail, first frame still true to the picture.
        tae = next((f for f in comp.files if f.role == "tae"), None)
        fast = (config.load_settings().get("video_quality", "fast") == "fast" and tae is not None
                and (config.DATA / tae.dest).exists())
        cmd = [str(exe), "-M", "vid_gen", *model_args(comp, skip=() if fast else ("tae",)),
               "-p", prompt, "-n", WAN_NEGATIVE,
               "-W", str(width or m["width"]), "-H", str(height or m["height"]), "--video-frames", str(m["frames"]),
               "--fps", str(m["fps"]), "--steps", str(m["steps"]), "--cfg-scale", str(m["cfg"]),
               "--sampling-method", "euler", "--flow-shift", str(m["flow_shift"]), "-s", str(seed),
               "-o", str(out)]
        if init_image is not None and m.get("i2v"):
            cmd += ["-i", str(init_image)]
        if backend == "cpu":
            cmd += ["-t", str(hw["cores"])]
        else:
            vram = hw.get("vram_mb", 0)
            cmd += ["--vae-tiling"]           # (the Fast test ran with it too)
            if vram < 10240 and not fast:
                # Measured on a 4 GB RTX 3050: the default 256 px tiles run out of memory and the engine
                # retries smaller (426 s decode); asking for 128 px tiles with less overlap up front, plus
                # temporal tiling, decodes the same clip in ~220 s with no visible seams.
                cmd += ["--temporal-tiling", "--vae-tile-size", "128x128", "--vae-tile-overlap", "0.25"]
            if vram < m.get("vram_mb", 8000) + 2048:
                cmd += ["--offload-to-cpu"]
        t0 = time.monotonic()
        path = _run_sd(cmd, exe, out, "Video engine", on_progress, is_cancelled,
                       # Best: decoding is ~2/3 of the time on 4 GB; Fast: it takes seconds
                       phases=[(0.0, 0.97), (0.97, 1.0)] if fast else [(0.0, 0.35), (0.35, 1.0)], steps=m["steps"])
        timings.record(f"video:{comp.id}:{'fast' if fast else 'best'}", time.monotonic() - t0)
        return path


image_engine = ImageEngine()
video_engine = VideoEngine()
