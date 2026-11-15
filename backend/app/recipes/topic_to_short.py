"""Recipe: Topic → script → voiceover → images → vertical video with captions.

Two stages so the creator can edit the script before spending time on rendering:
  1. write_script(): the text model writes title, scenes, description and hashtags.
  2. render(): voice (CPU) and images (GPU) run in parallel, then FFmpeg assembles.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
import unicodedata
from pathlib import Path

import soundfile as sf
import numpy as np

from PIL import Image, ImageOps

from .. import catalog, config
from ..engines.image import image_engine, video_engine
from ..engines.llm import llm
from ..engines.tts import tts
from ..engines import music, video
from ..jobs import Job

STYLES = {
    "cinematic": "cinematic film still, dramatic lighting, shallow depth of field, rich colors, highly detailed",
    "photoreal": "professional photograph, natural light, sharp focus, realistic, 35mm, highly detailed",
    "anime": "anime illustration, vibrant colors, clean line art, studio quality, detailed background",
    "3d": "3d render, pixar style, soft global illumination, cute, vibrant, octane render",
    "watercolor": "watercolor painting, soft textured paper, delicate brush strokes, pastel palette",
    "comic": "comic book art, bold ink outlines, halftone shading, dynamic composition, vivid colors",
}
NEGATIVE = ("text, letters, watermark, signature, logo, blurry, lowres, jpeg artifacts, deformed, "
            "disfigured, bad anatomy, extra limbs, extra fingers, mutated hands, cropped, worst quality")

# Motion for AI clips: calm movement suits narration better than fast action.
MOTION_PROMPT = "subtle natural motion, gentle slow camera movement, smooth, cinematic"
MOTION_MODES = ("still", "hook", "all")    # still pictures · AI motion on the first scene · on every scene

TONES = ["educational", "storytelling", "motivational", "funny", "mystery", "news"]
LANGUAGES = {"english": "English", "hindi": "Hindi (Devanagari script)"}

# Image prompts must be English (the image model only understands English), so the grammar
# restricts them to printable ASCII. For other languages each scene first states its fact in
# English: a small "think first" step that keeps a 4B model's facts and story consistent.
ASCII_TEXT = {"type": "string", "pattern": "^[ -~]+$"}


def script_schema(language: str) -> dict:
    scene: dict = {"narration": {"type": "string"}, "image_prompt": ASCII_TEXT}
    if language != "english":
        scene = {"fact": ASCII_TEXT, "image_prompt": ASCII_TEXT, "narration": {"type": "string"}}
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "scenes": {
                "type": "array", "minItems": 3, "maxItems": 12,
                "items": {"type": "object", "properties": scene, "required": list(scene)},
            },
            "description": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        },
        "required": ["title", "scenes", "description", "hashtags"],
    }


def _clean_tag(tag: str) -> str:
    # Keep letters, digits AND combining marks (Devanagari matras are category M, not \w).
    out = "".join(c for c in tag if c == "_" or unicodedata.category(c)[0] in "LMN")
    return out.strip("_")


def _slug(text: str, fallback: str = "project") -> str:
    """ASCII folder-name slug; returns `fallback` when the text has no ASCII letters (e.g. Hindi)."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")[:40]
    return s or fallback.strip("-")


def write_script(job: Job, topic: str, tone: str = "educational", language: str = "english",
                 seconds: int = 45, model_id: str | None = None) -> dict:
    scenes = max(4, min(10, round(seconds / 7)))
    words = int(seconds * (2.4 if language == "english" else 2.1))
    lang = LANGUAGES.get(language, "English")
    job.update(0.05, "Loading the text model")
    llm.ensure(model_id)
    job.update(0.25, "Writing the script")
    system = (
        "You are an expert short-form video scriptwriter for YouTube Shorts, Instagram Reels and TikTok. "
        "You write scripts that hook viewers in the first two seconds, keep them watching, and end with a "
        "satisfying payoff or call to action. Facts must be accurate; never invent statistics."
    )
    fact_rule = lang_rule = ""
    if language != "english":
        fact_rule = ('\n- Each scene starts with "fact": the one true point of that scene, written in plain '
                     "English. Then write the image_prompt and the narration from that fact.")
        lang_rule = (" Use natural, simple spoken Hindi (Hindustani) as people actually talk; common "
                     "English words are fine. Write numbers as digits.") if language == "hindi" else ""
    user = f"""Write a {seconds}-second vertical video script.

Topic: {topic}
Tone: {tone}
Narration language: {lang}

Rules:
- Exactly {scenes} scenes. Total narration about {words} words, so each scene's narration is
  {max(12, words // scenes - 3)}–{words // scenes + 4} words. Do not write shorter scenes.
- Scene 1 is a strong hook (a surprising fact, question or bold claim).
- The scenes tell ONE connected story in order; never contradict an earlier scene.
- Only state facts you are certain are true. Prefer well-known, verifiable facts over dramatic ones.{fact_rule}
- Each scene's "narration" is 2 spoken sentences in {lang}. No emojis, no stage directions.{lang_rule}
- Each scene's "image_prompt" is in ENGLISH ONLY, even when the narration is not: a vivid, concrete
  visual description of ONE still image for that scene (subject, setting, composition, lighting).
  Never ask for text, words, logos or captions in the image. Keep characters and setting consistent.
- "title": catchy, under 60 characters, in {lang}.
- "description": 1–2 sentences for the upload description, in {lang}.
- "hashtags": 5–8 relevant hashtags without the # sign."""
    data = llm.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}],
                         schema=script_schema(language), temperature=0.6, max_tokens=2000)
    tags = (_clean_tag(h) for h in data.get("hashtags", []))
    data["hashtags"] = list(dict.fromkeys(t for t in tags if t))
    for sc in data.get("scenes", []):
        sc.pop("fact", None)
    data["topic"], data["tone"], data["language"] = topic, tone, language
    job.update(1.0, "Script ready")
    return data


# ---------------------------------------------------------------- project helpers
def project_dir(pid: str) -> Path:
    d = (config.OUTPUTS / pid).resolve()
    if d.parent != config.OUTPUTS.resolve() or not (d / "project.json").exists():
        raise RuntimeError("Unknown project")
    return d


def load_project(pid: str) -> dict:
    d = project_dir(pid)
    meta = json.loads((d / "project.json").read_text("utf-8"))
    if "scenes" not in meta:   # projects made before per-scene data existed
        sc = [s for s in meta.get("script", {}).get("scenes", []) if s.get("narration", "").strip()]
        meta["scenes"] = []
        for i, s in enumerate(sc):
            wav = d / f"scene_{i+1:02d}.wav"
            meta["scenes"].append({"narration": s["narration"], "image_prompt": s["image_prompt"], "seed": -1,
                                   "duration": sf.info(str(wav)).duration if wav.exists() else 0.0, "rev": 0})
    meta.setdefault("music", "none")
    meta.setdefault("music_volume", 0.5)
    meta.setdefault("rev", 0)
    meta.setdefault("motion", "still")
    return meta


def _save(d: Path, meta: dict) -> None:
    tmp = d / "project.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(d / "project.json")


def _new_seed() -> int:
    return random.randint(0, 2**31 - 1)


def _voice(d: Path, i: int, text: str, voice: str, speed: float) -> float:
    samples, sr = tts.synth(text, voice, speed)
    samples = np.concatenate([samples, np.zeros(int(sr * 0.35), dtype=np.float32)])
    tmp = d / f"scene_{i+1:02d}.tmp.wav"
    sf.write(tmp, samples, sr)
    tmp.replace(d / f"scene_{i+1:02d}.wav")
    return len(samples) / sr


def _paint(job: Job, d: Path, i: int, prompt: str, style: str, seed: int, on_progress) -> None:
    style_text = STYLES.get(style, STYLES["cinematic"])
    tmp = d / f"scene_{i+1:02d}.tmp.png"
    image_engine.generate(f"{prompt.strip().rstrip('.')}, {style_text}", tmp, negative=NEGATIVE, seed=seed,
                          on_progress=on_progress, is_cancelled=lambda: job.cancelled)
    tmp.replace(d / f"scene_{i+1:02d}.png")


def _clip_path(d: Path, i: int) -> Path:
    return d / f"scene_{i+1:02d}.loop.mp4"


def _animate(job: Job, d: Path, i: int, sc: dict, style: str, on_progress) -> None:
    """AI motion for one scene: Wan animates the painted picture (image-to-video models) or makes a
    clip from the prompt (text-to-video models); the clip becomes a seamless loop for the segment."""
    comp = catalog.get(video_engine.default_model())
    init = None
    prompt = f"{sc['image_prompt'].strip().rstrip('.')}. {MOTION_PROMPT}"
    if comp.meta.get("i2v"):
        init = d / f"scene_{i+1:02d}.wan.png"
        with Image.open(d / f"scene_{i+1:02d}.png") as im:
            ImageOps.fit(im.convert("RGB"), (comp.meta["width"], comp.meta["height"]), Image.LANCZOS).save(init)
    else:
        prompt += f", {STYLES.get(style, STYLES['cinematic'])}"
    raw = d / f"scene_{i+1:02d}.raw.avi"
    raw.unlink(missing_ok=True)
    video_engine.generate(prompt, raw, init_image=init, seed=sc.get("motion_seed", sc["seed"]),
                          on_progress=on_progress, is_cancelled=lambda: job.cancelled)
    tmp = d / f"scene_{i+1:02d}.loop.tmp.mp4"
    video.make_loop(raw, tmp)
    tmp.replace(_clip_path(d, i))
    raw.unlink(missing_ok=True)
    if init is not None:
        init.unlink(missing_ok=True)
    sc["motion"], sc["video_model"] = "ai", comp.id


def _segment(d: Path, i: int, meta: dict) -> Path:
    sc = meta["scenes"][i]
    (d / "segments").mkdir(exist_ok=True)
    clip = _clip_path(d, i) if sc.get("motion") == "ai" and _clip_path(d, i).exists() else None
    return video.build_segment(d / f"scene_{i+1:02d}.png", d / f"scene_{i+1:02d}.wav", sc["duration"],
                               sc["narration"], d / "segments" / f"seg_{i+1:02d}.mp4",
                               idx=i, captions=meta.get("captions", True), clip=clip)


def _result(pid: str, meta: dict) -> dict:
    v = meta.get("rev", 0)
    return {"project": pid, "video": f"/outputs/{pid}/final.mp4?v={v}",
            "thumb": f"/outputs/{pid}/thumb.jpg?v={v}", "duration": meta.get("duration")}


def _assemble(job: Job, d: Path, meta: dict) -> dict:
    n = len(meta["scenes"])
    segs = [d / "segments" / f"seg_{i+1:02d}.mp4" for i in range(n)]
    duration = sum(s["duration"] for s in meta["scenes"])
    job.update(0.95, "Joining scenes")
    video.concat(segs, d / "voice.mp4")
    job.update(0.97, "Mixing music")
    track = music.resolve(meta["music"], meta.get("script", {}).get("tone"), duration, d,
                          seed=meta.get("music_seed", 1))
    video.mix_music(d / "voice.mp4", track, d / "final.mp4", duration, meta["music_volume"])
    video.thumbnail(d / "final.mp4", d / "thumb.jpg")
    meta.setdefault("script", {})["scenes"] = [
        {"narration": s["narration"], "image_prompt": s["image_prompt"]} for s in meta["scenes"]]
    meta.update({"status": "done", "duration": round(duration, 1), "video": "final.mp4", "thumb": "thumb.jpg",
                 "scenes_count": n, "rev": meta.get("rev", 0) + 1, "updated": time.time()})
    _save(d, meta)
    return _result(d.name, meta)


def project_detail(pid: str) -> dict:
    meta = load_project(pid)
    d = project_dir(pid)
    scenes = []
    for i, s in enumerate(meta["scenes"]):
        v = s.get("rev", 0)
        ai = s.get("motion") == "ai" and _clip_path(d, i).exists()
        scenes.append({**s, "index": i, "motion": "ai" if ai else "still",
                       "image": f"/outputs/{pid}/scene_{i+1:02d}.png?v={v}",
                       "audio": f"/outputs/{pid}/scene_{i+1:02d}.wav?v={v}",
                       "clip": f"/outputs/{pid}/scene_{i+1:02d}.loop.mp4?v={v}" if ai else None})
    return {"id": pid, "script": meta.get("script", {}), "voice": meta.get("voice"), "style": meta.get("style"),
            "speed": meta.get("speed", 1.0), "captions": meta.get("captions", True), "music": meta["music"],
            "music_volume": meta["music_volume"], "motion": meta.get("motion", "still"),
            "can_animate": bool(video_engine.installed()), "status": meta.get("status"), "scenes": scenes,
            **_result(pid, meta)}


# ---------------------------------------------------------------- full render
def render(job: Job, script: dict, voice: str = "af_heart", style: str = "cinematic",
           speed: float = 1.0, captions: bool = True, seed: int = -1,
           music_track: str = "auto", music_volume: float = 0.5, motion: str = "still") -> dict:
    scenes = [s for s in script.get("scenes", []) if s.get("narration", "").strip()]
    if not scenes:
        raise RuntimeError("The script has no scenes with narration.")
    motion = motion if motion in MOTION_MODES else "still"
    if motion != "still" and not video_engine.installed():
        raise RuntimeError("AI motion needs a video model. Open Models → Video to download one, "
                           "or choose 'Still pictures'.")
    # title first; Hindi titles have no ASCII, so fall back to the topic, then to "<language>-short"
    slug = _slug(script.get("title") or "", "") or _slug(script.get("topic") or "", f"{script.get('language', '')}-short")
    pid = time.strftime("%Y%m%d-%H%M%S") + "-" + slug
    d = config.OUTPUTS / pid
    d.mkdir(parents=True, exist_ok=True)
    n = len(scenes)
    base = seed if seed >= 0 else _new_seed()
    meta = {"id": pid, "script": {**script, "scenes": scenes}, "voice": voice, "style": style, "speed": speed,
            "captions": captions, "seed": base, "music": music_track, "music_volume": music_volume,
            "music_seed": base % 1000, "status": "rendering", "created": time.time(), "rev": 0,
            "motion": motion,
            "scenes": [{"narration": s["narration"].strip(), "image_prompt": s["image_prompt"].strip(),
                        "seed": base + i, "duration": 0.0, "rev": 0,
                        "motion": "ai" if motion == "all" or (motion == "hook" and i == 0) else "still"}
                       for i, s in enumerate(scenes)]}
    _save(d, meta)

    # ---- voiceover on CPU, in parallel with images on GPU ----
    tts_done = [0]
    tts_error: list[BaseException] = []

    def voice_worker() -> None:
        try:
            for i, sc in enumerate(meta["scenes"]):
                if job.cancelled:
                    return
                sc["duration"] = _voice(d, i, sc["narration"], voice, speed)
                tts_done[0] = i + 1
        except BaseException as exc:   # re-raised on the job thread
            tts_error.append(exc)

    vt = threading.Thread(target=voice_worker, daemon=True)
    vt.start()

    def stage(i: int) -> str:
        return f"Painting scene {i+1} of {n}  ·  voice {tts_done[0]}/{n}"

    moving = [i for i, sc in enumerate(meta["scenes"]) if sc["motion"] == "ai"]
    paint_end = 0.4 if moving else 0.8           # AI clips take most of the time when they're on
    for i, sc in enumerate(meta["scenes"]):
        job.update(0.05 + (paint_end - 0.05) * i / n, stage(i))
        _paint(job, d, i, sc["image_prompt"], style, sc["seed"],
               on_progress=lambda p, i=i: job.update(0.05 + (paint_end - 0.05) * (i + p) / n, stage(i)))

    for k, i in enumerate(moving):
        label = f"Animating scene {i+1}  ·  clip {k+1} of {len(moving)}"
        job.update(paint_end + (0.8 - paint_end) * k / len(moving), label)
        _animate(job, d, i, meta["scenes"][i], style,
                 on_progress=lambda p, k=k, label=label: job.update(
                     paint_end + (0.8 - paint_end) * (k + p) / len(moving), label))
        _save(d, meta)

    job.update(0.8, "Finishing voiceover")
    vt.join()
    if tts_error:
        raise tts_error[0]
    job.check_cancel()

    for i in range(n):
        job.update(0.82 + 0.12 * i / n, f"Editing scene {i+1} of {n}")
        _segment(d, i, meta)
    return _assemble(job, d, meta)


# ---------------------------------------------------------------- touch-ups
def regenerate_scene(job: Job, pid: str, index: int, narration: str | None = None,
                     image_prompt: str | None = None, new_image: bool = False, new_voice: bool = False,
                     motion: str | None = None) -> dict:
    """Redo one scene's image, voice line and/or motion, then rebuild only that segment.

    motion="ai" animates the scene (or makes a fresh take if it already moves); motion="still" goes
    back to the zoom-and-pan picture. A new picture on a moving scene is animated again.
    """
    d = project_dir(pid)
    meta = load_project(pid)
    if not 0 <= index < len(meta["scenes"]):
        raise RuntimeError("That scene doesn't exist.")
    sc = meta["scenes"][index]
    if narration and narration.strip() and narration.strip() != sc["narration"]:
        sc["narration"], new_voice = narration.strip(), True
    if image_prompt and image_prompt.strip() and image_prompt.strip() != sc["image_prompt"]:
        sc["image_prompt"], new_image = image_prompt.strip(), True
    if motion not in (None, "ai", "still"):
        raise RuntimeError("Unknown motion setting.")
    if not (new_image or new_voice or motion):
        raise RuntimeError("Nothing to change for this scene.")
    if motion == "ai" and not video_engine.installed():
        raise RuntimeError("AI motion needs a video model. Open Models → Video to download one.")
    if motion == "still":
        sc["motion"] = "still"
    animate = motion == "ai" or (new_image and sc.get("motion") == "ai")
    paint_end = 0.4 if animate else 0.72
    if new_image:
        sc["seed"] = _new_seed()
        job.update(0.02, f"Painting scene {index+1}")
        _paint(job, d, index, sc["image_prompt"], meta.get("style", "cinematic"), sc["seed"],
               on_progress=lambda p: job.update(0.02 + (paint_end - 0.02) * p, f"Painting scene {index+1}"))
    if animate:
        sc["motion_seed"] = _new_seed()
        job.update(paint_end, f"Animating scene {index+1}")
        _animate(job, d, index, sc, meta.get("style", "cinematic"),
                 on_progress=lambda p: job.update(paint_end + (0.74 - paint_end) * p, f"Animating scene {index+1}"))
    if new_voice:
        job.update(0.75, f"Recording voice for scene {index+1}")
        sc["duration"] = _voice(d, index, sc["narration"], meta.get("voice", "af_heart"), meta.get("speed", 1.0))
    sc["rev"] = sc.get("rev", 0) + 1
    job.update(0.85, f"Editing scene {index+1}")
    _segment(d, index, meta)
    return _assemble(job, d, meta)


def set_music(job: Job, pid: str, track: str, volume: float) -> dict:
    d = project_dir(pid)
    meta = load_project(pid)
    meta["music"], meta["music_volume"] = track, volume
    for i in range(len(meta["scenes"])):
        if not (d / "segments" / f"seg_{i+1:02d}.mp4").exists():
            _segment(d, i, meta)
    return _assemble(job, d, meta)
