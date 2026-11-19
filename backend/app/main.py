"""HTTP API for the Featherbox desktop UI."""
from __future__ import annotations

import json
import os
import math
import secrets
import shutil
import subprocess
import threading
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import catalog, config, hardware
from .downloads import downloads
from .engines import music
from .engines.image import image_engine, video_engine
from .engines.llm import llm
from .engines.tts import tts
from .engines.stt import LANGUAGES as STT_LANGUAGES, stt
from .jobs import GPU_LOCK, jobs
from .recipes import topic_to_short as t2s
from . import tools, updates

app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------- system
@app.post("/api/shutdown")
def shutdown(request: Request) -> dict:
    """Quit from the tray icon. The launcher passes a one-time token, so web pages can't trigger this."""
    token = os.environ.get("FEATHERBOX_SHUTDOWN_TOKEN", "")
    if not token or not secrets.compare_digest(request.headers.get("x-featherbox-token", ""), token):
        raise HTTPException(403, "Not allowed")

    def stop() -> None:
        import psutil
        llm.stop()
        for child in psutil.Process().children(recursive=True):   # image engine / ffmpeg still running
            if child.pid == updates.installer_pid:
                continue          # the update installer is what asked us to quit: it must keep going
            try:
                child.kill()
            except psutil.Error:
                pass
        os._exit(0)

    threading.Timer(0.3, stop).start()
    return {"ok": True}


@app.on_event("startup")
def _start_update_checks() -> None:
    updates.start_background()


# ---------------------------------------------------------------- updates
@app.get("/api/update")
def update_status() -> dict:
    return updates.status()


@app.post("/api/update/check")
def update_check() -> dict:
    return updates.check(force=True)


@app.post("/api/update/install")
def update_install() -> dict:
    try:
        return updates.start_install()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/system")
def system() -> dict:
    hw = hardware.detect()
    return {"app": config.APP_NAME, "version": config.APP_VERSION, "hardware": hw,
            "settings": config.load_settings(),
            "ready": {
                "llm": bool(catalog.installed("llm")) and _has_engine("llm", hw),
                "image": bool(catalog.installed("image")) and _has_engine("image", hw),
                "tts": catalog.get("tts-kokoro").is_installed(),
                "ffmpeg": catalog.get("ffmpeg").is_installed(),
            },
            "video_ready": bool(catalog.installed("video")) and _has_engine("image", hw),   # optional extra
            "llm_loaded": llm.model_id if llm.running() else None}


def _has_engine(category: str, hw: dict) -> bool:
    try:
        catalog.engine_exe(category, hw["backend"])
        return True
    except RuntimeError:
        return False


class SettingsPatch(BaseModel):
    backend: str | None = None
    llm_model: str | None = None
    image_model: str | None = None
    video_model: str | None = None
    video_quality: str | None = Field(None, pattern="^(fast|best)$")
    voice: str | None = None
    stt_model: str | None = None
    update_check: bool | None = None


@app.patch("/api/settings")
def patch_settings(p: SettingsPatch) -> dict:
    return config.save_settings({k: v for k, v in p.model_dump().items() if v is not None})


# ---------------------------------------------------------------- models & downloads
def _component(c: catalog.Component, hw: dict) -> dict:
    fit, reason = catalog.fit_label(c, hw)
    return {"id": c.id, "kind": c.kind, "category": c.category, "name": c.name,
            "description": c.description, "size_mb": c.size_mb, "download_mb": c.download_mb(),
            "license": c.license, "backend": c.backend, "installed": c.is_installed(),
            "fit": fit, "reason": reason, "tags": c.tags, "studio": c.studio, "soon": c.soon,
            "custom": c.custom, "url": c.meta.get("url"), "i2v": bool(c.meta.get("i2v")), "download": _task(c.id)}


def _task(cid: str) -> dict | None:
    t = downloads.status(cid)
    return {k: v for k, v in t.items() if k != "cancel"} if t else None


def _active(engine) -> str | None:
    try:
        return engine.default_model()
    except RuntimeError:
        return None


@app.get("/api/catalog")
def get_catalog() -> dict:
    hw = hardware.detect()
    return {"components": [_component(c, hw) for c in catalog.components()],
            "recommended": catalog.recommended_set(hw),
            "active": {"llm": _active(llm), "image": _active(image_engine), "video": _active(video_engine),
                       "stt": _active(stt)},
            "custom_kinds": {k: v[0] for k, v in catalog.CUSTOM_KINDS.items()}}


@app.post("/api/catalog/{cid}/install")
def install(cid: str) -> dict:
    try:
        comp = catalog.get(cid)
    except KeyError:
        raise HTTPException(404, "Unknown component")
    if comp.soon:
        raise HTTPException(400, f"{comp.name} is coming in a future update.")
    if comp.is_installed():
        return {"status": "installed"}
    return _task(downloads.start(comp)["id"]) or {}


class CustomModel(BaseModel):
    url: str
    kind: str


@app.post("/api/catalog/custom")
def add_custom_model(m: CustomModel) -> dict:
    """Add a model file from a Hugging Face link (user's choice; advice only, never blocked)."""
    try:
        dl, _ = catalog.normalize_hf_url(m.url)
        if m.kind not in catalog.CUSTOM_KINDS:
            raise ValueError("Pick what kind of model this is.")
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        r = httpx.head(dl, follow_redirects=True, timeout=20,
                       headers={"User-Agent": f"Featherbox/{config.APP_VERSION}"})
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Could not reach Hugging Face: {e}")
    if r.status_code in (401, 403):
        raise HTTPException(400, "This model is gated: it needs a Hugging Face login and licence acceptance, "
                                 "which Featherbox does not support yet.")
    if r.status_code == 404:
        raise HTTPException(400, "File not found. Check the link.")
    if r.status_code >= 400:
        raise HTTPException(400, f"Hugging Face answered {r.status_code}.")
    size = int(r.headers.get("x-linked-size") or r.headers.get("content-length") or 0)
    for h in r.history:
        size = size or int(h.headers.get("x-linked-size") or 0)
    if size < 50 * 1024 * 1024:
        raise HTTPException(400, "That file is too small to be a model. Link the .gguf or .safetensors file itself.")
    try:
        comp = catalog.add_custom(m.url, m.kind, math.ceil(size / 1048576))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _component(comp, hardware.detect())


@app.post("/api/catalog/{cid}/cancel")
def cancel_install(cid: str) -> dict:
    downloads.cancel(cid)
    return {"ok": True}


@app.delete("/api/catalog/{cid}")
def uninstall(cid: str) -> dict:
    try:
        comp = catalog.get(cid)
    except KeyError:
        raise HTTPException(404, "Unknown component")
    if comp.kind == "model" and llm.model_id == cid:
        llm.stop()
    downloads.cancel(cid)
    downloads.uninstall(comp)
    if comp.custom:
        catalog.remove_custom(cid)
    return {"ok": True}


@app.post("/api/setup")
def one_click_setup() -> dict:
    hw = hardware.detect()
    started = []
    for cid in catalog.recommended_set(hw):
        comp = catalog.get(cid)
        if not comp.is_installed():
            downloads.start(comp)
            started.append(cid)
    return {"started": started}


# ---------------------------------------------------------------- chat
class ChatRequest(BaseModel):
    messages: list[dict]
    model: str | None = None
    temperature: float = 0.7


@app.post("/api/chat")
def chat(req: ChatRequest):
    if GPU_LOCK.locked():
        raise HTTPException(409, "A video is being made right now. Chat will be available when it finishes.")

    def gen():
        # acquired inside the generator so a client that disconnects early never leaks the lock
        with GPU_LOCK:
            try:
                for piece in llm.stream(req.messages, model_id=req.model, temperature=req.temperature,
                                        max_tokens=2048):
                    yield piece
            except Exception as exc:
                yield f"\n\n[Error: {exc}]"

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


@app.post("/api/llm/unload")
def unload_llm() -> dict:
    llm.stop()
    return {"ok": True}


# ---------------------------------------------------------------- voice
@app.get("/api/tts/voices")
def voices() -> list[dict]:
    try:
        return tts.voices()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


class TTSPreview(BaseModel):
    text: str = Field(max_length=500)
    voice: str = "af_heart"
    speed: float = 1.0


@app.post("/api/tts/preview")
def tts_preview(req: TTSPreview) -> Response:
    try:
        return Response(tts.wav_bytes(req.text, req.voice, req.speed), media_type="audio/wav")
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


# ---------------------------------------------------------------- standalone tools
@app.get("/api/tools/options")
def tool_options() -> dict:
    def models(category: str, engine) -> list[dict]:
        return [{"id": c.id, "name": c.name, "i2v": bool(c.meta.get("i2v")), "arch": c.meta.get("arch")}
                for c in catalog.installed(category)]
    return {"image_models": models("image", image_engine), "video_models": models("video", video_engine),
            "active": {"image": _active(image_engine), "video": _active(video_engine)},
            "styles": list(t2s.STYLES), "video_quality": config.load_settings().get("video_quality", "fast")}


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=2, max_length=2000)
    negative: str = Field("", max_length=1000)
    model: str | None = None
    aspect: str = Field("portrait", pattern="^(portrait|square|landscape)$")
    style: str = "none"
    count: int = Field(1, ge=1, le=4)
    seed: int = -1


@app.post("/api/tools/image")
def tool_image(req: ImageRequest) -> dict:
    job = jobs.submit("image", f"Image: {req.prompt[:50]}",
                      lambda j: tools.make_images(j, req.prompt, req.negative, req.model, req.aspect, req.style,
                                                  req.count, req.seed))
    return job.to_dict()


class VideoRequest(BaseModel):
    prompt: str = Field(min_length=2, max_length=2000)
    image: str | None = Field(None, max_length=100)
    model: str | None = None
    aspect: str = Field("portrait", pattern="^(portrait|square|landscape)$")
    seed: int = -1


@app.post("/api/tools/video")
def tool_video(req: VideoRequest) -> dict:
    if not catalog.installed("video"):
        raise HTTPException(409, "No video model installed. Open Models → Video to download one.")
    job = jobs.submit("video", f"Video: {req.prompt[:50]}",
                      lambda j: tools.make_video(j, req.prompt, req.image, req.model, req.aspect, req.seed))
    return job.to_dict()


class VoiceRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice: str = "af_heart"
    speed: float = Field(1.0, ge=0.6, le=1.6)


@app.post("/api/tools/voice")
def tool_voice(req: VoiceRequest) -> dict:
    """Runs right away on the CPU (not queued behind GPU jobs)."""
    try:
        return tools.make_voice(req.text, req.voice, req.speed)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/tools/transcribe/options")
def transcribe_options() -> dict:
    hw = hardware.detect()
    gpu = stt.uses_gpu()
    engine = gpu or catalog.get("whisper-cpu").is_installed()
    return {"engine": engine, "ffmpeg": catalog.get("ffmpeg").is_installed(),
            "models": [{"id": c.id, "name": c.name, "translates": c.id != "stt-whisper-turbo",
                        "rtf": catalog.stt_rtf(c, hw, gpu)} for c in catalog.installed("stt")],
            "active": _active(stt), "languages": STT_LANGUAGES,
            "setup": [c.id for c in (catalog.get("whisper-cuda" if hw["backend"] == "cuda" else "whisper-cpu"),
                                     catalog.get("stt-whisper-turbo")) if not c.is_installed()],
            "gpu": gpu, "gpu_engine_available": hw["backend"] == "cuda" and not gpu}


@app.post("/api/creations/media")
async def upload_media(request: Request, name: str = "Recording") -> dict:
    """An audio/video file (or a microphone recording) to transcribe. Streamed to disk."""
    try:
        return await tools.save_media_upload(request.stream(), name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class TranscribeRequest(BaseModel):
    media: str = Field(max_length=100)
    title: str = Field("", max_length=200)
    model: str | None = None
    language: str = "auto"
    translate: bool = False
    prompt: str = Field("", max_length=500)


@app.post("/api/tools/transcribe")
def tool_transcribe(req: TranscribeRequest) -> dict:
    """On the CPU engine this runs in its own queue, so it never waits behind an image or video job."""
    if not catalog.installed("stt"):
        raise HTTPException(409, "No speech-to-text model installed. Open Models → Speech to text.")
    job = jobs.submit("transcribe", f"Transcribe: {req.title[:50]}",
                      lambda j: tools.make_transcript(j, req.media, req.title, req.model, req.language,
                                                      req.translate, req.prompt), gpu=stt.uses_gpu())
    return job.to_dict()


@app.get("/api/creations/{kind}")
def creations(kind: str) -> list[dict]:
    try:
        return tools.list_creations(kind)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.delete("/api/creations/{kind}/{cid}")
def delete_creation(kind: str, cid: str) -> dict:
    try:
        tools.delete_creation(kind, cid)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.post("/api/creations/upload")
async def upload_picture(request: Request) -> dict:
    data = await request.body()
    if len(data) > 40 * 1024 * 1024:
        raise HTTPException(413, "Pictures up to 40 MB, please.")
    try:
        return tools.save_upload(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/creations/{kind}/reveal")
def reveal_creations(kind: str) -> dict:
    if kind not in tools.EXT:
        raise HTTPException(404, "Unknown kind")
    folder = tools._dir(kind)
    subprocess.Popen(["explorer", str(folder)])
    return {"ok": True}


# ---------------------------------------------------------------- studio recipe
@app.get("/api/studio/options")
def studio_options() -> dict:
    return {"styles": list(t2s.STYLES), "tones": t2s.TONES, "languages": list(t2s.LANGUAGES)}


class ScriptRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=500)
    tone: str = "educational"
    language: str = "english"
    seconds: int = Field(45, ge=15, le=90)
    model: str | None = None


@app.post("/api/studio/script")
def studio_script(req: ScriptRequest) -> dict:
    job = jobs.submit("script", f"Script: {req.topic[:50]}",
                      lambda j: t2s.write_script(j, req.topic, req.tone, req.language, req.seconds, req.model))
    return job.to_dict()


class RenderRequest(BaseModel):
    script: dict
    voice: str = "af_heart"
    style: str = "cinematic"
    speed: float = Field(1.0, ge=0.6, le=1.6)
    captions: bool = True
    seed: int = -1
    music: str = "auto"
    music_volume: float = Field(0.5, ge=0, le=1)
    motion: str = Field("still", pattern="^(still|hook|all)$")


@app.post("/api/studio/render")
def studio_render(req: RenderRequest) -> dict:
    title = req.script.get("title") or req.script.get("topic") or "Video"
    job = jobs.submit("render", f"Video: {title[:50]}",
                      lambda j: t2s.render(j, req.script, req.voice, req.style, req.speed,
                                           req.captions, req.seed, req.music, req.music_volume,
                                           req.motion))
    return job.to_dict()


# ---------------------------------------------------------------- music
@app.get("/api/music")
def music_options() -> dict:
    return music.options()


@app.get("/api/music/preview/{mood}")
def music_preview(mood: str):
    if mood not in music.MOODS:
        raise HTTPException(404, "Unknown mood")
    return FileResponse(music.preview(mood), media_type="audio/wav")


@app.post("/api/music/upload")
async def music_upload(request: Request, name: str) -> dict:
    try:
        fname = music.safe_name(name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    data = await request.body()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > 80 * 1024 * 1024:
        raise HTTPException(413, "Music files must be under 80 MB")
    music.USER_DIR.mkdir(parents=True, exist_ok=True)
    (music.USER_DIR / fname).write_bytes(data)
    return {"id": f"file:{fname}", "label": Path(fname).stem}


# ---------------------------------------------------------------- jobs
@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    return [j.to_dict() for j in sorted(jobs.jobs.values(), key=lambda j: -j.created)][:50]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    if job_id not in jobs.jobs:
        raise HTTPException(404, "Unknown job")
    return jobs.jobs[job_id].to_dict()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    if job_id not in jobs.jobs:
        raise HTTPException(404, "Unknown job")
    jobs.cancel(job_id)
    return {"ok": True}


# ---------------------------------------------------------------- library
@app.get("/api/projects")
def projects() -> list[dict]:
    items = []
    for d in sorted(config.OUTPUTS.iterdir(), reverse=True):
        meta_file = d / "project.json"
        if d.is_dir() and meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text("utf-8"))
            except ValueError:
                continue
            items.append({
                "id": d.name, "title": meta.get("script", {}).get("title", d.name),
                "status": meta.get("status"), "duration": meta.get("duration"),
                "created": meta.get("created"), "style": meta.get("style"),
                "description": meta.get("script", {}).get("description", ""),
                "hashtags": meta.get("script", {}).get("hashtags", []),
                "video": f"/outputs/{d.name}/final.mp4?v={meta.get('rev', 0)}" if (d / "final.mp4").exists() else None,
                "thumb": f"/outputs/{d.name}/thumb.jpg?v={meta.get('rev', 0)}" if (d / "thumb.jpg").exists() else None,
            })
    return items


@app.get("/api/projects/{pid}")
def project_detail(pid: str) -> dict:
    try:
        return t2s.project_detail(pid)
    except RuntimeError:
        raise HTTPException(404, "Unknown project")


class SceneEdit(BaseModel):
    narration: str | None = Field(None, max_length=1000)
    image_prompt: str | None = Field(None, max_length=1000)
    new_image: bool = False
    new_voice: bool = False
    motion: str | None = Field(None, pattern="^(ai|still)$")


@app.post("/api/projects/{pid}/scenes/{index}")
def edit_scene(pid: str, index: int, req: SceneEdit) -> dict:
    meta = _project_or_404(pid)
    if not 0 <= index < len(meta["scenes"]):
        raise HTTPException(404, "Unknown scene")
    job = jobs.submit("scene", f"Scene {index + 1}: {meta['script'].get('title', pid)[:40]}",
                      lambda j: t2s.regenerate_scene(j, pid, index, req.narration, req.image_prompt,
                                                     req.new_image, req.new_voice, req.motion),
                      ref=f"{pid}:{index}")
    return job.to_dict()


class MusicEdit(BaseModel):
    music: str
    music_volume: float = Field(0.5, ge=0, le=1)


@app.post("/api/projects/{pid}/music")
def edit_music(pid: str, req: MusicEdit) -> dict:
    meta = _project_or_404(pid)
    job = jobs.submit("music", f"Music: {meta['script'].get('title', pid)[:40]}",
                      lambda j: t2s.set_music(j, pid, req.music, req.music_volume), ref=f"{pid}:music")
    return job.to_dict()


def _project_or_404(pid: str) -> dict:
    try:
        return t2s.load_project(pid)
    except RuntimeError:
        raise HTTPException(404, "Unknown project")


@app.delete("/api/projects/{pid}")
def delete_project(pid: str) -> dict:
    target = (config.OUTPUTS / pid).resolve()
    if target.parent != config.OUTPUTS.resolve() or not target.is_dir():
        raise HTTPException(404, "Unknown project")
    shutil.rmtree(target)
    return {"ok": True}


@app.post("/api/projects/{pid}/reveal")
def reveal_project(pid: str) -> dict:
    target = (config.OUTPUTS / pid).resolve()
    if target.parent != config.OUTPUTS.resolve() or not target.is_dir():
        raise HTTPException(404, "Unknown project")
    if os.name == "nt":
        subprocess.Popen(["explorer", str(target)])
    return {"ok": True}


# ---------------------------------------------------------------- static files
app.mount("/outputs", StaticFiles(directory=config.OUTPUTS), name="outputs")
tools.CREATIONS.mkdir(parents=True, exist_ok=True)
app.mount("/creations", StaticFiles(directory=tools.CREATIONS), name="creations")

if config.FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = config.FRONTEND_DIST / path
        if path and f.is_file():
            return FileResponse(f)
        # always revalidate the page itself, so an updated app never runs an old cached build
        return FileResponse(config.FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache"})
