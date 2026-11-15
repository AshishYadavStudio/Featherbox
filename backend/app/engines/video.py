"""Assemble scene images + narration + captions into a vertical MP4 with FFmpeg."""
from __future__ import annotations

import functools
import math
import re
import subprocess
from pathlib import Path

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


def _motion(idx: int, frames: int) -> str:
    f = max(frames - 1, 1)
    center_x, center_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    moves = [
        (f"1+0.14*on/{f}", center_x, center_y),                         # slow zoom in
        (f"1.14-0.14*on/{f}", center_x, center_y),                      # slow zoom out
        ("1.12", f"(iw-iw/zoom)*on/{f}", center_y),                     # pan left → right
        ("1.12", center_x, f"(ih-ih/zoom)*(1-on/{f})"),                 # pan bottom → top
    ]
    z, x, y = moves[idx % len(moves)]
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={FPS}"


def make_loop(raw: Path, out: Path) -> Path:
    """Turn a short AI clip (Wan: ~2 s at 16 fps, 480x832) into a seamless 1080x1920 30 fps loop.

    Frames are interpolated to 30 fps at the small size (cheap), then the clip is played forwards and
    backwards ("ping-pong"), so it can repeat for any length of voice line without a visible jump.
    """
    ff = catalog.ffmpeg_exe()
    graph = ("[0:v]minterpolate=fps=30:mi_mode=mci:mc_mode=aobmc:vsbmc=1,"
             f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1,"
             "split[f][b];[b]reverse,trim=start_frame=1,setpts=PTS-STARTPTS[r];"
             "[f][r]concat=n=2:v=1:a=0,format=yuv420p[v]")
    _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw), "-filter_complex", graph,
          "-map", "[v]", "-an", *_video_codec(), "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)])
    return out


def build_segment(image: Path, audio: Path, duration: float, narration: str, out: Path,
                  idx: int, captions: bool = True, clip: Path | None = None) -> Path:
    """One scene: picture (slow zoom/pan) or AI clip loop, plus voice line and burned-in captions."""
    ff = catalog.ffmpeg_exe()
    frames = max(1, math.ceil(duration * FPS))
    if clip is not None:
        # AI motion: loop the ping-pong clip, slightly slowed so motion feels calm under narration.
        inputs = ["-stream_loop", "-1", "-i", str(clip), "-i", str(audio)]
        graph = [f"[0:v]setpts=1.25*PTS,fps={FPS},setsar=1,format=yuv420p[v0]"]
    else:
        inputs = ["-i", str(image), "-i", str(audio)]
        graph = [f"[0:v]scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
                 f"{_motion(idx, frames)},setsar=1,format=yuv420p[v0]"]
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
    _run(cmd)
    return out


def concat(segments: list[Path], out: Path) -> Path:
    ff = catalog.ffmpeg_exe()
    lst = out.parent / "segments.txt"
    lst.write_text("".join(f"file '{s.as_posix()}'\n" for s in segments), "utf-8")
    _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
          "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)])
    return out


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
