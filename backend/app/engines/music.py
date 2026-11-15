"""Background music.

Built-in moods are synthesised on the fly with numpy (pads, bass, plucks, bells, a soft pulse,
FFT reverb), so they need no download and are royalty-free by construction. Creators can also add
their own tracks (mp3/wav/…), which are stored under data/music.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import soundfile as sf

from .. import config

SR = 44100
USER_DIR = config.DATA / "music"

# chords: MIDI notes (mid register); bass: MIDI root for each chord
MOODS: dict[str, dict] = {
    "calm": {"label": "Calm", "bpm": 70, "beats": 8, "pad": 1.0, "bright": 4, "arp": "bells", "pulse": False,
             "chords": [[60, 64, 67, 71], [57, 60, 64, 67], [53, 57, 60, 64], [55, 59, 62, 64]],
             "bass": [36, 33, 41, 43]},
    "uplifting": {"label": "Uplifting", "bpm": 100, "beats": 4, "pad": 0.8, "bright": 5, "arp": "eighths", "pulse": False,
                  "chords": [[62, 66, 69], [61, 64, 69], [62, 66, 71], [62, 67, 71]],
                  "bass": [38, 33, 35, 43]},
    "playful": {"label": "Playful", "bpm": 112, "beats": 4, "pad": 0.45, "bright": 3, "arp": "bounce", "pulse": False,
                "chords": [[65, 69, 72], [62, 65, 69], [62, 67, 70], [64, 67, 72]],
                "bass": [41, 38, 43, 36]},
    "mysterious": {"label": "Mysterious", "bpm": 60, "beats": 8, "pad": 1.0, "bright": 3, "arp": "sparse", "pulse": False,
                   "chords": [[57, 60, 64], [57, 60, 65], [57, 62, 65], [56, 59, 64]],
                   "bass": [33, 41, 38, 40]},
    "epic": {"label": "Epic", "bpm": 90, "beats": 4, "pad": 0.9, "bright": 6, "arp": "ostinato", "pulse": True,
             "chords": [[62, 65, 69], [62, 65, 70], [60, 65, 69], [60, 64, 67]],
             "bass": [38, 34, 41, 36]},
}

TONE_MOOD = {"educational": "calm", "storytelling": "mysterious", "motivational": "uplifting",
             "funny": "playful", "mystery": "mysterious", "news": "epic"}

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}


def _hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def _pad_note(freq: float, n: int, bright: int, rng: np.random.Generator) -> np.ndarray:
    """Warm detuned additive 'saw' pad, stereo (n, 2)."""
    t = np.arange(n) / SR
    out = np.zeros((n, 2), np.float32)
    for det, pan in ((-0.0035, 0.3), (0.0035, 0.7), (0.0, 0.5)):
        f = freq * (1 + det)
        sig = np.zeros(n, np.float32)
        for k in range(1, bright + 1):
            if f * k > 9000:
                break
            sig += (np.sin(2 * np.pi * f * k * t + rng.uniform(0, 6.28)) / k ** 1.6).astype(np.float32)
        out[:, 0] += sig * (1 - pan)
        out[:, 1] += sig * pan
    # slow "breathing" so long chords don't sound static
    lfo = 1 + 0.12 * np.sin(2 * np.pi * rng.uniform(0.08, 0.2) * t + rng.uniform(0, 6.28))
    return out * lfo[:, None].astype(np.float32)


def _pluck(freq: float, length: float, tau: float, bell: bool = False) -> np.ndarray:
    n = int(length * SR)
    t = np.arange(n) / SR
    env = np.exp(-t / tau) * np.minimum(1, t / 0.004)
    if bell:   # inharmonic partial gives a soft bell / celesta colour
        sig = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(2 * np.pi * freq * 2.76 * t) * np.exp(-t / (tau * 0.4))
    else:
        sig = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * freq * 2 * t) + 0.1 * np.sin(2 * np.pi * freq * 3 * t)
    return (sig * env).astype(np.float32)


def _boom(length: float = 1.2) -> np.ndarray:
    n = int(length * SR)
    t = np.arange(n) / SR
    f = 45 + 50 * np.exp(-t / 0.08)
    phase = 2 * np.pi * np.cumsum(f) / SR
    return (np.sin(phase) * np.exp(-t / 0.35) * np.minimum(1, t / 0.003)).astype(np.float32)


def _add(buf: np.ndarray, sig: np.ndarray, start: int, gain: float, pan: float = 0.5) -> None:
    if start >= len(buf):
        return
    end = min(len(buf), start + len(sig))
    s = sig[: end - start] * gain
    if s.ndim == 1:
        buf[start:end, 0] += s * (1 - pan) * 2 ** 0.5
        buf[start:end, 1] += s * pan * 2 ** 0.5
    else:
        buf[start:end] += s


def _fft_lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    spec = np.fft.rfft(x, axis=0)
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    spec *= (1 / np.sqrt(1 + (freqs / cutoff) ** 4))[:, None]
    return np.fft.irfft(spec, n=len(x), axis=0).astype(np.float32)


def _reverb(x: np.ndarray, seconds: float, wet: float, rng: np.random.Generator) -> np.ndarray:
    n_ir = int(seconds * SR)
    t = np.arange(n_ir) / SR
    ir = rng.standard_normal((n_ir, 2)).astype(np.float32) * np.exp(-t / (seconds / 6.9))[:, None]
    ir[: int(0.012 * SR)] = 0                              # pre-delay
    ir /= np.sqrt((ir ** 2).sum(axis=0))
    size = 1 << int(np.ceil(np.log2(len(x) + n_ir)))
    y = np.fft.irfft(np.fft.rfft(x, size, axis=0) * np.fft.rfft(ir, size, axis=0), size, axis=0)[: len(x)]
    return (x * (1 - wet) + y.astype(np.float32) * wet * 1.4).astype(np.float32)


def synth(mood: str, seconds: float, seed: int = 1) -> np.ndarray:
    """Return a stereo float32 track (n, 2) for a built-in mood."""
    m = MOODS.get(mood) or MOODS["calm"]
    rng = np.random.default_rng(seed)
    beat = 60 / m["bpm"]
    chord_len = beat * m["beats"]
    n = int((seconds + 0.5) * SR)
    pad = np.zeros((n, 2), np.float32)
    lead = np.zeros((n, 2), np.float32)
    n_chords = int(np.ceil(seconds / chord_len)) + 1

    for c in range(n_chords):
        idx = c % len(m["chords"])
        chord, root = m["chords"][idx], m["bass"][idx]
        start = int(c * chord_len * SR)
        # pad block with a little overlap and smooth attack/release for crossfades
        blk = int((chord_len + 1.2) * SR)
        tt = np.arange(blk) / SR
        env = np.minimum(1, tt / 0.9) * np.clip((chord_len + 1.2 - tt) / 1.2, 0, 1)
        block = np.zeros((blk, 2), np.float32)
        for note in chord:
            block += _pad_note(_hz(note), blk, m["bright"], rng)
        block += _pad_note(_hz(root), blk, 2, rng) * 1.3            # bass
        _add(pad, block * env[:, None].astype(np.float32) / (len(chord) + 1.3), start, m["pad"])

        # melodic layer
        steps = {"eighths": 2, "bounce": 2, "ostinato": 4, "bells": 0.5, "sparse": 0.25}[m["arp"]]
        count = int(m["beats"] * steps)
        notes = chord + [x + 12 for x in chord]
        for k in range(count):
            t0 = start + int(k * beat / steps * SR)
            if m["arp"] == "sparse":
                if rng.random() < 0.55:
                    _add(lead, _pluck(_hz(rng.choice(notes) + 12), 3.0, 1.2, bell=True), t0, 0.16, rng.uniform(0.2, 0.8))
            elif m["arp"] == "bells":
                _add(lead, _pluck(_hz(notes[(k * 2 + c) % len(notes)] + 12), 3.0, 1.1, bell=True), t0, 0.14,
                     0.35 + 0.3 * (k % 2))
            elif m["arp"] == "bounce":
                note = notes[[0, 2, 1, 3, 0, 2, 4, 3][k % 8] % len(notes)]
                _add(lead, _pluck(_hz(note), 0.4, 0.09), t0, 0.32, 0.4 + 0.2 * (k % 2))
                if k % 2 == 0:
                    _add(lead, _pluck(_hz(root + 12), 0.3, 0.08), t0, 0.25)
            elif m["arp"] == "eighths":
                note = notes[[0, 1, 2, 3, 2, 1, 0, 2][k % 8] % len(notes)]
                _add(lead, _pluck(_hz(note), 0.9, 0.28), t0, 0.22, 0.3 + 0.4 * ((k // 2) % 2))
            else:  # ostinato
                note = chord[[0, 1, 2, 1][k % 4]] - 12 + (12 if k % 8 >= 4 else 0)
                _add(lead, _pluck(_hz(note), 0.35, 0.1), t0, 0.2, 0.5)
        if m["pulse"]:
            for b in range(m["beats"]):
                if b % 2 == 0:
                    _add(lead, _boom(), start + int(b * beat * SR), 0.55)

    mix = _fft_lowpass(pad, 2200 if m["bright"] < 5 else 3500) + _fft_lowpass(lead, 6000)
    mix = _reverb(mix, 2.6 if m["bpm"] < 80 else 1.8, 0.32, rng)
    mix = mix[: int(seconds * SR)]
    fade = int(min(1.5, seconds / 4) * SR)
    mix[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)[:, None]
    rms = float(np.sqrt(np.mean(mix ** 2))) or 1.0
    mix *= 0.1 / rms                                   # about -20 dBFS RMS
    peak = float(np.abs(mix).max()) or 1.0
    if peak > 0.89:
        mix *= 0.89 / peak
    return mix


def write_mood(mood: str, seconds: float, out: Path, seed: int = 1) -> Path:
    sf.write(out, synth(mood, seconds, seed), SR, subtype="PCM_16")
    return out


def preview(mood: str) -> Path:
    cache = config.DATA / "cache" / "music"
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"preview_{mood}.wav"
    if not out.exists():
        write_mood(mood, 14, out)
    return out


# ---------------------------------------------------------------- user tracks
def safe_name(name: str) -> str:
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    if ext not in AUDIO_EXT:
        raise ValueError(f"Unsupported audio type {ext or '(none)'}. Use mp3, wav, m4a, ogg or flac.")
    stem = re.sub(r"[^\w\- ]+", "", stem, flags=re.UNICODE).strip()[:60] or "track"
    return stem + ext


def user_tracks() -> list[dict]:
    if not USER_DIR.exists():
        return []
    return [{"id": f"file:{p.name}", "label": p.stem} for p in sorted(USER_DIR.iterdir())
            if p.suffix.lower() in AUDIO_EXT]


def options() -> dict:
    return {"moods": [{"id": f"mood:{k}", "label": v["label"]} for k, v in MOODS.items()],
            "tracks": user_tracks(), "tone_mood": TONE_MOOD}


def resolve(track: str, tone: str | None, seconds: float, out_dir: Path, seed: int = 1) -> Path | None:
    """Turn a track id ("none", "auto", "mood:x", "file:y") into an audio file for mixing."""
    if not track or track == "none":
        return None
    if track == "auto":
        track = "mood:" + TONE_MOOD.get(tone or "", "calm")
    if track.startswith("mood:"):
        return write_mood(track[5:], seconds + 1, out_dir / "music.wav", seed)
    if track.startswith("file:"):
        p = (USER_DIR / track[5:]).resolve()
        if p.parent != USER_DIR.resolve() or not p.exists():
            raise RuntimeError(f"Music track not found: {track[5:]}")
        return p
    raise RuntimeError(f"Unknown music option: {track}")
