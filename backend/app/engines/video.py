"""Assemble scene images + narration + captions into a vertical MP4 with FFmpeg."""
from __future__ import annotations

import functools
import math
import os
import re
import subprocess
from pathlib import Path

from PIL import Image

from .. import catalog, config
from .textrender import render_caption

W, H, FPS = 1080, 1920, 30


@functools.lru_cache(maxsize=1)
def _video_codec() -> list[str]:
    ff = catalog.ffmpeg_exe()
    enc = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True,
                         creationflags=config.NO_WINDOW).stdout
    if "h264_nvenc" in enc:
        # verify the GPU encoder actually works on this machine (driver / GPU support)
        test = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                               "color=black:s=256x256:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"],
                              capture_output=True, creationflags=config.NO_WINDOW)
        if test.returncode == 0:
            return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21", "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=config.NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError("FFmpeg failed:\n" + "\n".join(r.stderr.strip().splitlines()[-10:]))


def caption_chunks(text: str, max_words: int = 4) -> list[str]:
    """Split narration into short punchy caption chunks, breaking at punctuation when possible."""
    chunks: list[str] = []
    for part in re.split(r"(?<=[.!?,;:।])\s+", text.strip()):
        words = part.split()
        while words:
            n = len(words) if len(words) <= max_words + 1 else max_words
            chunks.append(" ".join(words[:n]))
            words = words[n:]
    return chunks


# Slow camera moves over a still picture: (zoom, x, y) at the start and at the end.
# Zoom 1 shows the whole picture; ZOOM_MAX is the closest. x and y say where the view sits within the
# room it has to move at that zoom: 0 = left/top edge, 0.5 = centred, 1 = right/bottom edge.
ZOOM_MAX = 1.15
MOVES = [
    ((1.00, 0.50, 0.50), (1.12, 0.50, 0.35)),   # push in, drifting up a little
    ((1.14, 0.50, 0.30), (1.00, 0.50, 0.50)),   # pull out
    ((1.14, 0.10, 0.50), (1.14, 0.90, 0.50)),   # pan left → right
    ((1.03, 0.25, 0.75), (1.14, 0.70, 0.30)),   # diagonal push in
    ((1.14, 0.50, 0.90), (1.14, 0.50, 0.10)),   # drift up
    ((1.10, 0.90, 0.45), (1.14, 0.15, 0.50)),   # pan right → left, closing in
]


def _ease(u: float) -> float:
    """Mostly steady speed, with a soft start and end (no jolt where scenes meet)."""
    return 0.7 * u + 0.3 * (0.5 - 0.5 * math.cos(math.pi * u))


def camera(idx: int, frames: int) -> list[tuple[float, float, float]]:
    """(zoom, x, y) for every frame of scene `idx`."""
    (z0, x0, y0), (z1, x1, y1) = MOVES[idx % len(MOVES)]
    out = []
    for k in range(frames):
        e = _ease(k / max(frames - 1, 1))
        out.append((z0 * (z1 / z0) ** e, x0 + (x1 - x0) * e, y0 + (y1 - y0) * e))   # zoom eased in log space
    return out


def _still_source(image: Path) -> Image.Image:
    """The picture cropped to 9:16 and enlarged once (Lanczos, lightly sharpened) to the closest zoom's size."""
    from PIL import ImageFilter, ImageOps
    with Image.open(image) as im:
        big = ImageOps.fit(im.convert("RGB"), (round(W * ZOOM_MAX), round(H * ZOOM_MAX)), Image.LANCZOS)
    return big.filter(ImageFilter.UnsharpMask(radius=2, percent=45, threshold=3))


def view(size: tuple[int, int], zoom: float, x: float, y: float) -> tuple[float, float, float]:
    """(source pixels per output pixel, left, top) of the visible window, always inside the picture."""
    sw, sh = size
    s = sw / zoom / W
    return s, x * (sw - W * s), y * (sh - H * s)


def _frame(src: Image.Image, zoom: float, x: float, y: float) -> bytes:
    """One output frame. Sub-pixel accurate (an affine resample, not a whole-pixel crop), so slow moves
    glide instead of stepping every other frame as FFmpeg's zoompan did."""
    s, left, top = view(src.size, zoom, x, y)
    return src.transform((W, H), Image.AFFINE, (s, 0, left, 0, s, top), resample=Image.BILINEAR).tobytes()


def _write_frames(proc: subprocess.Popen, src: Image.Image, cams: list[tuple[float, float, float]]) -> None:
    """Render frames on all cores (Pillow releases the GIL) and stream them to FFmpeg in order."""
    from concurrent.futures import ThreadPoolExecutor
    workers = max(2, min(8, (os.cpu_count() or 4) - 1))
    with ThreadPoolExecutor(workers) as ex:
        for start in range(0, len(cams), 32):           # a few batches in flight keeps memory small
            for data in ex.map(lambda c: _frame(src, *c), cams[start:start + 32]):
                proc.stdin.write(data)


def media_duration(path: Path) -> float:
    """Length in seconds, read from FFmpeg's own report (no ffprobe needed)."""
    r = subprocess.run([catalog.ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=config.NO_WINDOW)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if not m:
        raise RuntimeError(f"Couldn't read the length of {path.name}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def loop_plan(length: float) -> tuple[float, float]:
    """(blend, offset) for a forward-only seamless loop of a clip `length` seconds long.

    The loop plays the clip from `blend` seconds to its end while the clip's first `blend` seconds fade in
    over that end, so the last frame leads straight back into the first: the loop is `length - blend` long.
    """
    blend = round(min(0.5, length / 4), 3)
    return blend, round(length - 2 * blend, 3)


def make_loop(raw: Path, out: Path) -> Path:
    """Turn a short AI clip (Wan: ~2 s at 16 fps, 480x832) into a seamless 1080x1920 30 fps loop.

    Frames are interpolated to 30 fps at the small size (cheap), enlarged and lightly sharpened. The loop
    only ever plays forwards: its end crossfades into its start. (It used to play forwards then backwards,
    which made rivers flow uphill and people walk backwards every couple of seconds.)
    """
    ff = catalog.ffmpeg_exe()
    tmp = out.with_name(out.stem + ".30fps.mp4")
    _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw), "-vf",
          "minterpolate=fps=30:mi_mode=mci:mc_mode=aobmc:vsbmc=1,"
          f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1,"
          "unsharp=5:5:0.6:5:5:0,format=yuv420p", "-an", *_video_codec(), "-pix_fmt", "yuv420p", str(tmp)])
    try:
        blend, offset = loop_plan(media_duration(tmp))
        graph = (f"[0:v]split[a][b];[a]trim=start={blend},setpts=PTS-STARTPTS[body];"
                 f"[b]trim=end={blend},setpts=PTS-STARTPTS[head];"
                 f"[body][head]xfade=transition=fade:duration={blend}:offset={offset},format=yuv420p[v]")
        _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(tmp), "-filter_complex", graph,
              "-map", "[v]", "-an", *_video_codec(), "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)])
    finally:
        tmp.unlink(missing_ok=True)
    return out


def clip_to_mp4(raw: Path, out: Path) -> Path:
    """A standalone AI clip (Wan: 16 fps) as a shareable MP4: frames interpolated to 30 fps, size kept."""
    ff = catalog.ffmpeg_exe()
    _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw),
          "-vf", "minterpolate=fps=30:mi_mode=mci:mc_mode=aobmc:vsbmc=1,format=yuv420p", "-an",
          *_video_codec(), "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)])
    return out


def build_segment(image: Path, audio: Path, duration: float, narration: str, out: Path,
                  idx: int, captions: bool = True, clip: Path | None = None) -> Path:
    """One scene: picture (slow zoom/pan) or AI clip loop, plus voice line and burned-in captions."""
    ff = catalog.ffmpeg_exe()
    frames = max(1, math.ceil(duration * FPS))
    if clip is not None:
        # AI motion: repeat the seamless loop, slightly slowed so motion feels calm under narration.
        inputs = ["-stream_loop", "-1", "-i", str(clip), "-i", str(audio)]
        graph = [f"[0:v]setpts=1.25*PTS,fps={FPS},setsar=1,format=yuv420p[v0]"]
    else:
        # A still picture with a slow camera move: frames rendered here (smooth, sub-pixel) and piped in.
        inputs = ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0",
                  "-i", str(audio)]
        graph = ["[0:v]setsar=1,format=yuv420p[v0]"]
    last = "v0"
    if captions and narration.strip():
        chunks = caption_chunks(narration)
        weights = [max(len(c), 1) for c in chunks]
        speech = max(duration - 0.35, 0.5)           # trailing pause has no caption
        t = 0.0
        for k, (chunk, wgt) in enumerate(zip(chunks, weights)):
            dur = speech * wgt / sum(weights)
            png = out.parent / "captions" / f"{out.stem}_{k:02d}.png"
            render_caption(chunk, png, canvas_w=W)
            inputs += ["-i", str(png)]
            nxt = f"v{k+1}"
            graph.append(f"[{last}][{k+2}:v]overlay=x=(W-w)/2:y=H*0.70-h/2:"
                         f"enable='between(t,{t:.3f},{t+dur:.3f})'[{nxt}]")
            last, t = nxt, t + dur
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(graph), "-map", f"[{last}]", "-map", "1:a",
           *_video_codec(), "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{duration:.3f}", str(out)]
    if clip is not None:
        _run(cmd)
    else:
        _run_piped(cmd, _still_source(image), camera(idx, frames))
    return out


def _run_piped(cmd: list[str], src: Image.Image, cams: list[tuple[float, float, float]]) -> None:
    """Run FFmpeg with the camera-move frames on its stdin."""
    import tempfile
    with tempfile.TemporaryFile() as err:              # a file, not a pipe: FFmpeg can't block on a full buffer
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err,
                                creationflags=config.NO_WINDOW)
        try:
            _write_frames(proc, src, cams)
        except (BrokenPipeError, OSError):
            pass                                        # FFmpeg stopped early: its error is reported below
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
        code = proc.wait()
        if code != 0:
            err.seek(0)
            text = err.read().decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError("FFmpeg failed:\n" + "\n".join(text[-10:]))


FADE = 0.25      # seconds of crossfade between scenes; shorter than the 0.35 s pause after each voice line


def join_plan(durations: list[float], fade: float = FADE) -> tuple[list[float], list[float], float]:
    """(xfade offsets, audio lengths kept per scene, total length) for joining scenes with crossfades.

    Each scene's picture fades into the next over `fade` seconds. The voice isn't crossfaded: each scene
    drops `fade` seconds of the silent pause at its end, so the next voice line starts right as its
    picture fades in, and sound and picture stay in step.
    """
    n = len(durations)
    offsets, t = [], 0.0
    for k in range(n - 1):
        t += durations[k] - fade
        offsets.append(round(t, 3))
    keep = [round(d - fade, 3) for d in durations[:-1]] + [durations[-1]] if n else []
    return offsets, keep, round(sum(durations) - fade * max(n - 1, 0), 3)


def concat(segments: list[Path], out: Path, durations: list[float] | None = None, fade: float = FADE) -> float:
    """Join the scene segments, with short crossfades when their lengths are known. Returns the length."""
    ff = catalog.ffmpeg_exe()
    if not durations or len(segments) < 2 or fade <= 0:
        lst = out.parent / "segments.txt"
        lst.write_text("".join(f"file '{s.as_posix()}'\n" for s in segments), "utf-8")
        _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
              "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)])
        return sum(durations or [])
    offsets, keep, total = join_plan(durations, fade)
    inputs = [x for s in segments for x in ("-i", str(s))]
    graph, last = [], "0:v"
    for k, off in enumerate(offsets, start=1):
        graph.append(f"[{last}][{k}:v]xfade=transition=fade:duration={fade}:offset={off}[x{k}]")
        last = f"x{k}"
    graph.append(f"[{last}]format=yuv420p[v]")
    graph += [f"[{k}:a]atrim=end={keep[k]},asetpts=PTS-STARTPTS[a{k}]" for k in range(len(segments))]
    graph.append("".join(f"[a{k}]" for k in range(len(segments))) + f"concat=n={len(segments)}:v=0:a=1[a]")
    _run([ff, "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", ";".join(graph),
          "-map", "[v]", "-map", "[a]", *_video_codec(), "-pix_fmt", "yuv420p", "-r", str(FPS),
          "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)])
    return total


def thumbnail(video: Path, out: Path, at: float = 0.5) -> Path:
    ff = catalog.ffmpeg_exe()
    _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", str(at), "-i", str(video),
          "-frames:v", "1", "-vf", "scale=360:-2", str(out)])
    return out


def mix_music(video_in: Path, music: Path | None, out: Path, duration: float, volume: float = 0.5) -> Path:
    """Lay a music bed under the narration with automatic ducking (sidechain compression).

    volume 0–1 maps to -19 dB…+5 dB on the bed (built-in moods sit at -20 dBFS RMS); while the voice is
    speaking the bed is pushed down a further ~9 dB (about 22 dB under the voice at 0.5) and comes
    back up in the pauses.
    """
    ff = catalog.ffmpeg_exe()
    if music is None or volume <= 0:
        _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video_in), "-c", "copy",
              "-movflags", "+faststart", str(out)])
        return out
    gain_db = -19 + 24 * max(0.0, min(1.0, volume))
    fade_st = max(0.0, duration - 2.5)
    graph = (
        f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={gain_db:.1f}dB,"
        f"afade=t=in:d=0.8,afade=t=out:st={fade_st:.2f}:d=2.5[m];"
        "[0:a]aresample=48000,aformat=channel_layouts=mono,pan=stereo|c0=c0|c1=c0,asplit=2[v][sc];"
        "[m][sc]sidechaincompress=threshold=0.04:ratio=5:attack=25:release=300:knee=4[md];"
        "[v][md]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=false[a]"
    )
    _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video_in),
          "-stream_loop", "-1", "-i", str(music), "-filter_complex", graph,
          "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
          "-t", f"{duration:.3f}", "-movflags", "+faststart", str(out)])
    return out
