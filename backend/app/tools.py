"""Standalone creation tools: image generator, video generator and voice (text-to-speech).

Every result is saved under data/creations/<kind>/ as the file plus a small JSON sidecar with the
settings used, so each tool can show a gallery and "use these settings again".
"""
from __future__ import annotations

import json
import math
import random
import re
import time
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageOps

from . import catalog, config
from .engines import video
from .engines.image import image_engine, video_engine
from .engines.tts import VOICE_LABELS, tts
from .jobs import Job
from .recipes.topic_to_short import NEGATIVE, STYLES

CREATIONS = config.DATA / "creations"
EXT = {"image": ".png", "video": ".mp4", "voice": ".wav"}
ASPECTS = ("portrait", "square", "landscape")
_ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def _dir(kind: str) -> Path:
    d = CREATIONS / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def _new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


def _check(kind: str, cid: str) -> None:
    if kind not in EXT and kind != "uploads":
        raise ValueError("Unknown kind")
    if not _ID.match(cid):
        raise ValueError("Bad id")


def _public(meta: dict) -> dict:
    kind, cid = meta["kind"], meta["id"]
    out = {**meta, "url": f"/creations/{kind}/{cid}{EXT[kind]}"}
    if kind == "video" and (_dir("video") / f"{cid}.jpg").exists():
        out["thumb"] = f"/creations/video/{cid}.jpg"
    return out


def _save(meta: dict) -> dict:
    p = _dir(meta["kind"]) / f"{meta['id']}.json"
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    return _public(meta)


def list_creations(kind: str, limit: int = 300) -> list[dict]:
    if kind not in EXT:
        raise ValueError("Unknown kind")
    items = []
    for p in _dir(kind).glob("*.json"):
        try:
            meta = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if (_dir(kind) / f"{meta['id']}{EXT[kind]}").exists():
            items.append(meta)
    items.sort(key=lambda m: m.get("created", 0), reverse=True)
    return [_public(m) for m in items[:limit]]


def delete_creation(kind: str, cid: str) -> None:
    _check(kind, cid)
    for p in _dir(kind).glob(f"{cid}.*"):
        p.unlink(missing_ok=True)


def save_upload(data: bytes) -> dict:
    """A picture the user brings in (to animate). Re-encoded as PNG, which also proves it's an image."""
    import io
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            cid = _new_id()
            im.save(_dir("uploads") / f"{cid}.png")
    except Exception:
        raise ValueError("That file isn't a picture Featherbox can read (use PNG, JPG or WebP).")
    return {"ref": f"upload:{cid}", "url": f"/creations/uploads/{cid}.png", "width": im.width, "height": im.height}


def _resolve_image(ref: str) -> Path:
    kind, _, cid = ref.partition(":")
    folder = {"image": "image", "upload": "uploads"}.get(kind)
    if not folder:
        raise ValueError("Unknown picture reference")
    _check("uploads" if folder == "uploads" else "image", cid)
    p = _dir(folder) / f"{cid}.png"
    if not p.exists():
        raise ValueError("That picture no longer exists.")
    return p


def _dims(w: int, h: int, aspect: str, step: int) -> tuple[int, int]:
    """Model's native portrait size -> the chosen aspect at about the same pixel count."""
    if aspect == "landscape":
        return h, w
    if aspect == "square":
        s = int(round(math.sqrt(w * h) / step) * step)
        return s, s
    return w, h


# ---------------------------------------------------------------- image
def make_images(job: Job, prompt: str, negative: str = "", model_id: str | None = None,
                aspect: str = "portrait", style: str = "none", count: int = 1, seed: int = -1) -> dict:
    comp = catalog.get(model_id or image_engine.default_model())
    if comp.category != "image" or not comp.is_installed():
        raise RuntimeError("That image model isn't installed. Open Models → Image.")
    w, h = _dims(comp.meta["width"], comp.meta["height"], aspect if aspect in ASPECTS else "portrait", 64)
    full = prompt.strip().rstrip(".") + (f", {STYLES[style]}" if style in STYLES else "")
    base = seed if seed >= 0 else random.randint(0, 2**31 - 1)
    items = []
    for k in range(count):
        cid, t0 = _new_id(), time.time()
        label = f"Painting image {k + 1} of {count}" if count > 1 else "Painting your image"
        job.update(k / count, label)
        tmp = _dir("image") / f"{cid}.tmp.png"
        image_engine.generate(full, tmp, negative=negative.strip() or NEGATIVE, width=w, height=h,
                              seed=base + k, model_id=comp.id,
                              on_progress=lambda p, k=k, label=label: job.update((k + p) / count, label),
                              is_cancelled=lambda: job.cancelled)
        tmp.replace(_dir("image") / f"{cid}.png")
        items.append(_save({"id": cid, "kind": "image", "prompt": prompt.strip(), "negative": negative.strip(),
                            "style": style, "aspect": aspect, "model": comp.id, "model_name": comp.name,
                            "seed": base + k, "width": w, "height": h, "created": time.time(),
                            "seconds": round(time.time() - t0, 1)}))
    return {"items": items}


# ---------------------------------------------------------------- video
def make_video(job: Job, prompt: str, image_ref: str | None = None, model_id: str | None = None,
               aspect: str = "portrait", seed: int = -1) -> dict:
    comp = catalog.get(model_id or video_engine.default_model())
    if comp.category != "video" or not comp.is_installed():
        raise RuntimeError("That video model isn't installed. Open Models → Video.")
    m = comp.meta
    w, h = _dims(m["width"], m["height"], aspect if aspect in ASPECTS else "portrait", 32)
    init = None
    cid, t0 = _new_id(), time.time()
    if image_ref:
        if not m.get("i2v"):
            raise RuntimeError(f"{comp.name} can only make clips from text. Remove the picture, or use a model "
                               "that animates pictures (Wan 2.2).")
        init = _dir("video") / f"{cid}.start.png"
        with Image.open(_resolve_image(image_ref)) as im:
            ImageOps.fit(im.convert("RGB"), (w, h), Image.LANCZOS).save(init)
    seed = seed if seed >= 0 else random.randint(0, 2**31 - 1)
    raw = _dir("video") / f"{cid}.raw.avi"
    job.update(0.0, "Animating" if init else "Making your clip")
    try:
        video_engine.generate(prompt.strip(), raw, init_image=init, seed=seed, model_id=comp.id, width=w, height=h,
                              on_progress=lambda p: job.update(0.95 * p, "Animating" if init else "Making your clip"),
                              is_cancelled=lambda: job.cancelled)
        job.update(0.96, "Saving the clip")
        out = _dir("video") / f"{cid}.mp4"
        video.clip_to_mp4(raw, out)
        video.thumbnail(out, _dir("video") / f"{cid}.jpg", at=0.0)
    finally:
        raw.unlink(missing_ok=True)
        if init is not None:
            init.unlink(missing_ok=True)
    seconds = m["frames"] / m["fps"]
    return {"items": [_save({"id": cid, "kind": "video", "prompt": prompt.strip(), "image": image_ref, "aspect": aspect,
                             "model": comp.id, "model_name": comp.name, "seed": seed, "width": w, "height": h,
                             "duration": round(seconds, 1), "quality": config.load_settings().get("video_quality", "fast"),
                             "created": time.time(), "seconds": round(time.time() - t0, 1)})]}


# ---------------------------------------------------------------- voice
def _chunks(text: str, limit: int = 300) -> list[str]:
    """Sentences grouped into chunks the voice model handles comfortably."""
    parts = [p for p in re.split(r"(?<=[.!?।])\s+|\n+", text.strip()) if p.strip()]
    out, cur = [], ""
    for p in parts:
        if cur and len(cur) + len(p) + 1 > limit:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        out.append(cur)
    return out


def make_voice(text: str, voice: str = "af_heart", speed: float = 1.0) -> dict:
    t0 = time.time()
    pieces, sr = [], 24000
    for chunk in _chunks(text):
        samples, sr = tts.synth(chunk, voice, speed)
        pieces += [samples, np.zeros(int(sr * 0.25), dtype=np.float32)]
    if not pieces:
        raise ValueError("Type some text first.")
    audio = np.concatenate(pieces[:-1])
    cid = _new_id()
    sf.write(_dir("voice") / f"{cid}.wav", audio, sr)
    return _save({"id": cid, "kind": "voice", "text": text.strip(), "voice": voice,
                  "voice_name": VOICE_LABELS.get(voice, voice), "speed": speed,
                  "duration": round(len(audio) / sr, 1), "created": time.time(),
                  "seconds": round(time.time() - t0, 1)})
