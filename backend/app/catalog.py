"""Curated catalog of engines and models the app can download.

Engine builds are pinned for reproducibility; bump the tags to update.
"""
from __future__ import annotations

import dataclasses
import json
import re
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from . import config

LLAMA_TAG = "b12383"
SD_TAG = "master-1104-5dcfa79"
SD_HASH = SD_TAG.rsplit("-", 1)[1]
WHISPER_TAG = "b5454"

_GH_LLAMA = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/"
_GH_SD = f"https://github.com/leejet/stable-diffusion.cpp/releases/download/{SD_TAG}/"
_GH_WHISPER = f"https://github.com/ggml-org/whisper.cpp/releases/download/{WHISPER_TAG}/"
_GH_KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
_FFMPEG = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"


def _hf(repo: str, file: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/main/{file}"


@dataclass
class FileSpec:
    url: str
    dest: str                    # relative to DATA; for zips, the archive's temp name
    size_mb: int
    extract_to: str | None = None  # relative dir under DATA to unzip into
    role: str = "model"          # how the engine loads it: model | diffusion | vae | clip_l | t5xxl | llm | tae | vad
    members: tuple[str, ...] = ()  # zips only: fetch just these files (by name or "prefix*") with range requests


@dataclass
class Component:
    id: str
    kind: str                    # engine | model
    category: str                # llm | image | video | tts | stt | music | tool
    name: str
    description: str
    files: list[FileSpec]
    license: str
    backend: str | None = None   # engines only: cuda | vulkan | cpu
    meta: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    studio: bool = True          # False: listed and downloadable, but the Studio does not use it yet
    soon: bool = False           # shown as "coming soon", nothing to download
    custom: bool = False         # added by the user from a Hugging Face link

    @property
    def size_mb(self) -> int:
        return sum(f.size_mb for f in self.files)

    def download_mb(self) -> int:
        """What is still left to download (shared files already on disk are skipped)."""
        have = shared_paths_in_use(self)
        return sum(f.size_mb for f in self.files
                   if f.dest not in have and not (config.DATA / (f.extract_to or f.dest)).exists())

    @property
    def marker(self) -> Path:
        return config.DATA / ".installed" / f"{self.id}.ok"

    def main_path(self) -> Path:
        """The file or folder engines load from."""
        f = self.files[0]
        return config.DATA / (f.extract_to or f.dest)

    def is_installed(self) -> bool:
        if self.soon:
            return False
        if self.id == "ffmpeg" and shutil.which("ffmpeg"):
            return True
        # Every file must be on disk too, so a file added to a model later (e.g. a fast decoder) shows the
        # model as needing a (small) download instead of silently being missing.
        return self.marker.exists() and all((config.DATA / (f.extract_to or f.dest)).exists() for f in self.files)


def _engine_files(prefix: str, names: list[tuple[str, int]], folder: str) -> list[FileSpec]:
    return [FileSpec(prefix + n, f"bin/_dl/{n}", mb, extract_to=f"bin/{folder}") for n, mb in names]


def _llm(cid: str, name: str, repo: str, file: str, mb: int, desc: str, lic: str,
         layers: int, kv_mb_per_1k: int, tags: list[str]) -> Component:
    return Component(cid, "model", "llm", name, desc, [FileSpec(_hf(repo, file), f"models/llm/{file}", mb)],
                     lic, tags=tags, meta={"layers": layers, "ctx": 4096, "kv_mb_per_1k": kv_mb_per_1k})


def _whisper(cid: str, name: str, file: str, mb: int, desc: str, tags: list[str], rtf: float,
             gpu_rtf: float) -> Component:
    """rtf / gpu_rtf: seconds of work per second of speech (8-core laptop CPU / RTX 3050 4 GB, measured)."""
    return Component(cid, "model", "stt", name, desc,
                     [FileSpec(_hf("ggerganov/whisper.cpp", file), f"models/stt/{file}", mb), _VAD],
                     "MIT", tags=tags, meta={"rtf": rtf, "gpu_rtf": gpu_rtf})


def stt_rtf(c: "Component", hw: dict, gpu: bool) -> float:
    """Expected seconds of work per second of speech on this PC."""
    if gpu:
        return c.meta.get("gpu_rtf", 0.1)
    return c.meta.get("rtf", 0.5) * 8 / max(2, hw.get("cores", 4))


# The CUDA runtime (cudart + cuBLAS) that llama.cpp ships; whisper.cpp's CUDA build uses the same 12.4 libraries.
_LLAMA_CUDART = FileSpec(_GH_LLAMA + "cudart-llama-bin-win-cuda-12.4-x64.zip",
                         "bin/_dl/cudart-llama-bin-win-cuda-12.4-x64.zip", 391, extract_to="bin/llama-cuda")


# Files shared by several models (downloaded once; kept while any model that uses them is installed).
_QWEN3_4B = FileSpec(_hf("unsloth/Qwen3-4B-Instruct-2507-GGUF", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"),
                     "models/llm/Qwen3-4B-Instruct-2507-Q4_K_M.gguf", 2497)
_FLUX_VAE = FileSpec(_hf("Comfy-Org/Z-Image-Turbo", "split_files/vae/ae.safetensors"),
                     "models/encoders/flux_ae.safetensors", 320, role="vae")
_SD15_VAE = FileSpec(_hf("stabilityai/sd-vae-ft-mse-original", "vae-ft-mse-840000-ema-pruned.safetensors"),
                     "models/encoders/vae-ft-mse-840000-ema-pruned.safetensors", 319, role="vae")
_SDXL_VAE = FileSpec(_hf("madebyollin/sdxl-vae-fp16-fix", "sdxl.vae.safetensors"),
                     "models/encoders/sdxl-vae-fp16-fix.safetensors", 319, role="vae")
_VAD = FileSpec(_hf("ggml-org/whisper-vad", "ggml-silero-v6.2.0.bin"),
                "models/stt/ggml-silero-v6.2.0.bin", 1, role="vad")   # skips silence (stops "phantom" text)
_UMT5 = FileSpec(_hf("city96/umt5-xxl-encoder-gguf", "umt5-xxl-encoder-Q4_K_M.gguf"),
                 "models/encoders/umt5-xxl-encoder-Q4_K_M.gguf", 3486, role="t5xxl")


CATALOG: list[Component] = [
    # ---------------- engines ----------------
    Component("llama-cuda", "engine", "llm", "llama.cpp (NVIDIA CUDA)",
              "Text engine for NVIDIA GPUs. Fastest option for chat and script writing.",
              _engine_files(_GH_LLAMA, [(f"llama-{LLAMA_TAG}-bin-win-cuda-12.4-x64.zip", 319)], "llama-cuda")
              + [_LLAMA_CUDART],
              "MIT", backend="cuda"),
    Component("llama-vulkan", "engine", "llm", "llama.cpp (Vulkan)",
              "Text engine for AMD / Intel / NVIDIA GPUs via Vulkan. Small download.",
              _engine_files(_GH_LLAMA, [(f"llama-{LLAMA_TAG}-bin-win-vulkan-x64.zip", 38)], "llama-vulkan"),
              "MIT", backend="vulkan"),
    Component("llama-cpu", "engine", "llm", "llama.cpp (CPU)",
              "Text engine that runs on any PC without a GPU.",
              _engine_files(_GH_LLAMA, [(f"llama-{LLAMA_TAG}-bin-win-cpu-x64.zip", 25)], "llama-cpu"),
              "MIT", backend="cpu"),
    Component("sd-cuda", "engine", "image", "stable-diffusion.cpp (NVIDIA CUDA)",
              "Image engine for NVIDIA GPUs.",
              _engine_files(_GH_SD, [(f"sd-master-{SD_HASH}-bin-win-cuda12-x64.zip", 412),
                                     ("cudart-sd-bin-win-cu12-x64.zip", 563)], "sd-cuda"),
              "MIT", backend="cuda"),
    Component("sd-vulkan", "engine", "image", "stable-diffusion.cpp (Vulkan)",
              "Image engine for AMD / Intel / NVIDIA GPUs via Vulkan.",
              _engine_files(_GH_SD, [(f"sd-master-{SD_HASH}-bin-win-vulkan-x64.zip", 30)], "sd-vulkan"),
              "MIT", backend="vulkan"),
    Component("sd-cpu", "engine", "image", "stable-diffusion.cpp (CPU)",
              "Image engine for PCs without a GPU (slow: minutes per image).",
              _engine_files(_GH_SD, [(f"sd-master-{SD_HASH}-bin-win-cpu-x64.zip", 18)], "sd-cpu"),
              "MIT", backend="cpu"),
    Component("whisper-cpu", "engine", "stt", "whisper.cpp (CPU)",
              "Speech-to-text engine. Runs on the processor of any PC, so it never competes with image or "
              "video jobs for the graphics card.",
              _engine_files(_GH_WHISPER, [("whisper-bin-x64.zip", 9)], "whisper-cpu"),
              "MIT"),
    Component("whisper-cuda", "engine", "stt", "whisper.cpp (NVIDIA CUDA)",
              "Speech-to-text on the graphics card: about 20x faster than the CPU engine. Shares the CUDA "
              "libraries of the text engine, so only the speech parts are downloaded.",
              [FileSpec(_GH_WHISPER + "whisper-bin-win-cuda-12.4.0-x64.zip", "bin/_dl/whisper-cuda.zip", 258,
                        extract_to="bin/whisper-cuda",
                        members=("whisper-cli.exe", "whisper.dll", "ggml.dll", "ggml-base.dll", "ggml-cuda.dll",
                                 "ggml-cpu-*")),
               _LLAMA_CUDART],
              "MIT", backend="cuda"),
    Component("ffmpeg", "engine", "tool", "FFmpeg",
              "Video encoder used to assemble the final video. Skipped if already on your PATH.",
              [FileSpec(_FFMPEG, "bin/_dl/ffmpeg.zip", 200, extract_to="bin/ffmpeg")],
              "GPL"),
    # ================= models =================
    # ---- text (write scripts, chat) ----
    *[_llm(*a) for a in [
        ("llm-gemma3-1b", "Gemma 3 1B", "unsloth/gemma-3-1b-it-GGUF", "gemma-3-1b-it-Q4_K_M.gguf", 769,
         "Tiny and quick. Works on almost any PC, but scripts are simple.", "Gemma", 26, 26, ["Tiny"]),
        ("llm-llama32-1b", "Llama 3.2 1B Instruct", "bartowski/Llama-3.2-1B-Instruct-GGUF",
         "Llama-3.2-1B-Instruct-Q4_K_M.gguf", 808,
         "Tiny model for low-end PCs without a GPU. Lower quality scripts.", "Llama 3.2 Community", 16, 32, ["Tiny"]),
        ("llm-qwen25-1.5b", "Qwen2.5 1.5B Instruct", "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
         "qwen2.5-1.5b-instruct-q4_k_m.gguf", 1066,
         "Small and fast, follows instructions well for its size.", "Apache-2.0", 28, 28, ["Fast"]),
        ("llm-llama32-3b", "Llama 3.2 3B Instruct", "bartowski/Llama-3.2-3B-Instruct-GGUF",
         "Llama-3.2-3B-Instruct-Q4_K_M.gguf", 2019,
         "Smaller and faster; fits fully on 4 GB GPUs.", "Llama 3.2 Community", 28, 112, ["Fast"]),
        ("llm-gemma3-4b", "Gemma 3 4B", "unsloth/gemma-3-4b-it-GGUF", "gemma-3-4b-it-Q4_K_M.gguf", 2375,
         "Google's small model. Strong in Hindi and other Indian languages.", "Gemma", 34, 136,
         ["Indian languages"]),
        ("llm-qwen3-4b", "Qwen3 4B Instruct", "unsloth/Qwen3-4B-Instruct-2507-GGUF",
         "Qwen3-4B-Instruct-2507-Q4_K_M.gguf", 2497,
         "Best all-round script writer for 4–8 GB GPUs. Good at English and Hindi.", "Apache-2.0", 36, 144,
         ["Best for 4 GB"]),
        ("llm-qwen25-7b", "Qwen2.5 7B Instruct", "bartowski/Qwen2.5-7B-Instruct-GGUF",
         "Qwen2.5-7B-Instruct-Q4_K_M.gguf", 4466,
         "Noticeably smarter scripts. Best with a 6–8 GB GPU.", "Apache-2.0", 28, 56, []),
        ("llm-llama31-8b", "Llama 3.1 8B Instruct", "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
         "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf", 4693,
         "Popular all-rounder with natural English. Best with an 8 GB GPU.", "Llama 3.1 Community", 32, 128, []),
        ("llm-gemma3-12b", "Gemma 3 12B", "unsloth/gemma-3-12b-it-GGUF", "gemma-3-12b-it-Q4_K_M.gguf", 6963,
         "High-quality writing and the best Indian-language skills here. Needs a 10–12 GB GPU.", "Gemma",
         48, 384, ["Indian languages", "High quality"]),
        ("llm-mistral-nemo", "Mistral Nemo 12B", "bartowski/Mistral-Nemo-Instruct-2407-GGUF",
         "Mistral-Nemo-Instruct-2407-Q4_K_M.gguf", 7131,
         "Creative storyteller with a long memory. Needs a 10–12 GB GPU.", "Apache-2.0", 40, 160,
         ["High quality"]),
        ("llm-sarvam-m", "Sarvam-M 24B", "bartowski/sarvamai_sarvam-m-GGUF", "sarvamai_sarvam-m-Q4_K_M.gguf",
         13670, "Made in India for Indian languages (Hindi, Bengali, Tamil, Telugu and more). Very large.",
         "Apache-2.0", 40, 160, ["Indian languages", "Made in India"]),
        ("llm-qwen3-30b-a3b", "Qwen3 30B-A3B Instruct", "unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF",
         "Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf", 17697,
         "Very smart and surprisingly fast for its size. Needs about 32 GB of RAM.", "Apache-2.0", 48, 96,
         ["High quality"]),
    ]],
    # ---- image (paint scenes) ----
    Component("img-dreamshaper8", "model", "image", "DreamShaper 8 (SD 1.5)",
              "Versatile image model: cinematic, anime, illustration, photo. Runs on 4 GB GPUs.",
              [FileSpec(_hf("Lykon/DreamShaper", "DreamShaper_8_pruned.safetensors"),
                        "models/image/DreamShaper_8_pruned.safetensors", 2133)],
              "CreativeML OpenRAIL-M", tags=["Best for 4 GB"],
              meta={"arch": "sd15", "width": 512, "height": 896, "steps": 24, "cfg": 7.0,
                    "sampler": "euler_a", "vram_mb": 2800}),
    Component("img-realvis6", "model", "image", "Realistic Vision 6 (SD 1.5)",
              "Photo-realistic people, places and products. Runs on 4 GB GPUs.",
              [FileSpec(_hf("SG161222/Realistic_Vision_V6.0_B1_noVAE", "Realistic_Vision_V6.0_NV_B1_fp16.safetensors"),
                        "models/image/Realistic_Vision_V6.0_NV_B1_fp16.safetensors", 2034),
               _SD15_VAE],
              "CreativeML OpenRAIL-M", tags=["Photo-real"],
              meta={"arch": "sd15", "width": 512, "height": 896, "steps": 25, "cfg": 5.0,
                    "sampler": "dpm++2m", "scheduler": "karras", "vram_mb": 2800}),
    Component("img-counterfeit25", "model", "image", "Counterfeit 2.5 (SD 1.5)",
              "Anime and manga style. Runs on 4 GB GPUs.",
              [FileSpec(_hf("gsdf/Counterfeit-V2.5", "Counterfeit-V2.5_fp16.safetensors"),
                        "models/image/Counterfeit-V2.5_fp16.safetensors", 2034),
               _SD15_VAE],
              "CreativeML OpenRAIL-M", tags=["Anime"],
              meta={"arch": "sd15", "width": 512, "height": 896, "steps": 24, "cfg": 7.0,
                    "sampler": "euler_a", "vram_mb": 2800}),
    Component("img-zimage-turbo", "model", "image", "Z-Image Turbo",
              "Newest and sharpest model here: detailed, realistic scenes that follow the prompt closely. "
              "About twice as slow as SD 1.5 models (~40 s per image on a 4 GB GPU). "
              "Shares the Qwen3 4B text model, so it is a smaller download if you already have that.",
              [FileSpec(_hf("leejet/Z-Image-Turbo-GGUF", "z_image_turbo-Q3_K.gguf"),
                        "models/image/z_image_turbo-Q3_K.gguf", 2998, role="diffusion"),
               _FLUX_VAE, dataclasses.replace(_QWEN3_4B, role="llm")],
              "Apache-2.0", tags=["Best quality"],
              meta={"arch": "zimage", "width": 576, "height": 1024, "steps": 8, "cfg": 1.0,
                    "sampler": "euler", "vram_mb": 4000}),
    Component("img-dreamshaper-xl", "model", "image", "DreamShaper XL Lightning (SDXL)",
              "Sharper and more detailed than SD 1.5, in only 6 steps. Best with a 6–8 GB GPU.",
              [FileSpec(_hf("Lykon/dreamshaper-xl-lightning", "DreamShaperXL_Lightning.safetensors"),
                        "models/image/DreamShaperXL_Lightning.safetensors", 6618),
               _SDXL_VAE],
              "OpenRAIL++", tags=["High quality"],
              meta={"arch": "sdxl", "width": 768, "height": 1344, "steps": 6, "cfg": 2.0,
                    "sampler": "dpm++2m", "scheduler": "karras", "vram_mb": 6500}),
    Component("img-juggernaut-xl", "model", "image", "Juggernaut XL Lightning (SDXL)",
              "Cinematic, photo-real SDXL model in 6 steps. Best with a 6–8 GB GPU.",
              [FileSpec(_hf("RunDiffusion/Juggernaut-XL-Lightning",
                            "Juggernaut_RunDiffusionPhoto2_Lightning_4Steps.safetensors"),
                        "models/image/Juggernaut_RunDiffusionPhoto2_Lightning_4Steps.safetensors", 6776),
               _SDXL_VAE],
              "CreativeML OpenRAIL-M", tags=["Photo-real", "High quality"],
              meta={"arch": "sdxl", "width": 768, "height": 1344, "steps": 6, "cfg": 1.5,
                    "sampler": "dpm++2m", "scheduler": "karras", "vram_mb": 6500}),
    Component("img-flux-schnell", "model", "image", "FLUX.1 schnell",
              "Excellent prompt following and composition in 4 steps. Large: best with an 8 GB+ GPU.",
              [FileSpec(_hf("leejet/FLUX.1-schnell-gguf", "flux1-schnell-q4_k.gguf"),
                        "models/image/flux1-schnell-q4_k.gguf", 6566, role="diffusion"),
               _FLUX_VAE,
               FileSpec(_hf("comfyanonymous/flux_text_encoders", "clip_l.safetensors"),
                        "models/encoders/clip_l.safetensors", 235, role="clip_l"),
               FileSpec(_hf("city96/t5-v1_1-xxl-encoder-gguf", "t5-v1_1-xxl-encoder-Q4_K_M.gguf"),
                        "models/encoders/t5-v1_1-xxl-encoder-Q4_K_M.gguf", 2762, role="t5xxl")],
              "Apache-2.0", tags=["High quality"],
              meta={"arch": "flux", "width": 576, "height": 1024, "steps": 4, "cfg": 1.0,
                    "sampler": "euler", "vram_mb": 8000}),
    # ---- video (AI motion clips) ----
    Component("vid-wan21-1.3b", "model", "video", "Wan 2.1 Text-to-Video 1.3B",
              "Makes short AI clips from each scene's text prompt (it can't animate the painted picture). Lighter.",
              [FileSpec(_hf("samuelchristlie/Wan2.1-T2V-1.3B-GGUF", "Wan2.1-T2V-1.3B-Q8_0.gguf"),
                        "models/video/Wan2.1-T2V-1.3B-Q8_0.gguf", 1465, role="diffusion"),
               _UMT5,
               FileSpec(_hf("Comfy-Org/Wan-2.1", "split_files/vae/wan_2.1_vae.safetensors"),
                        "models/video/wan_2.1_vae.safetensors", 242, role="vae"),
               FileSpec("https://github.com/madebyollin/taehv/raw/refs/heads/main/safetensors/taew2_1.safetensors",
                        "models/encoders/taew2_1.safetensors", 22, role="tae")],
              "Apache-2.0", tags=["Lightest video model", "Text-to-video"],
              meta={"arch": "wan", "i2v": False, "width": 480, "height": 832, "frames": 33, "fps": 16,
                    "steps": 20, "cfg": 6.0, "flow_shift": 3.0, "vram_mb": 6000}),
    Component("vid-wan22-5b", "model", "video", "Wan 2.2 Text/Image-to-Video 5B",
              "Brings each painted scene to life (image-to-video), so the look stays the same. On a 4 GB GPU a clip takes about 2–3 min (Fast) or 6–7 min (Best).",
              [FileSpec(_hf("QuantStack/Wan2.2-TI2V-5B-GGUF", "Wan2.2-TI2V-5B-Q4_K_M.gguf"),
                        "models/video/Wan2.2-TI2V-5B-Q4_K_M.gguf", 3274, role="diffusion"),
               _UMT5,
               FileSpec(_hf("Comfy-Org/Wan-2.2", "split_files/vae/wan2.2_vae.safetensors"),
                        "models/video/wan2.2_vae.safetensors", 1344, role="vae"),
               FileSpec("https://github.com/madebyollin/taehv/raw/refs/heads/main/safetensors/taew2_2.safetensors",
                        "models/encoders/taew2_2.safetensors", 22, role="tae")],
              "Apache-2.0", tags=["Animates your scene pictures"],
              meta={"arch": "wan", "i2v": True, "width": 480, "height": 832, "frames": 33, "fps": 16,
                    "steps": 20, "cfg": 6.0, "flow_shift": 3.0, "vram_mb": 10000}),
    # ---- voice ----
    Component("tts-kokoro", "model", "tts", "Kokoro TTS v1.0",
              "Natural voices in English (US/UK), Hindi and more. Runs fast on CPU.",
              [FileSpec(_GH_KOKORO + "kokoro-v1.0.onnx", "models/tts/kokoro-v1.0.onnx", 326),
               FileSpec(_GH_KOKORO + "voices-v1.0.bin", "models/tts/voices-v1.0.bin", 28)],
              "Apache-2.0", tags=["English", "Hindi"]),
    Component("tts-piper", "model", "tts", "Piper voices",
              "Extra voices for Malayalam, Telugu, Nepali and 30+ more languages.",
              [], "MIT", soon=True),
    # ---- speech to text (whisper.cpp)
    _whisper("stt-whisper-base", "Whisper Base", "ggml-base-q5_1.bin", 57,
             "Smallest and quickest. Fine for clear English; weaker on accents and other languages.",
             tags=["English", "Fastest"], rtf=0.1, gpu_rtf=0.02),
    _whisper("stt-whisper-small", "Whisper Small", "ggml-small-q5_1.bin", 181,
             "A good balance for English and the bigger languages. Can translate speech to English.",
             tags=["99 languages", "Translates"], rtf=0.25, gpu_rtf=0.03),
    _whisper("stt-whisper-turbo", "Whisper Large v3 Turbo", "ggml-large-v3-turbo-q5_0.bin", 547,
             "Near-best accuracy at a fraction of the cost. Recommended, including Hindi and other Indian "
             "languages. (It writes what was said; for translation use Small or Large v3.)",
             tags=["Recommended", "Hindi", "99 languages"], rtf=0.6, gpu_rtf=0.07),
    _whisper("stt-whisper-large", "Whisper Large v3", "ggml-large-v3-q5_0.bin", 1031,
             "The most accurate Whisper, and the best at translating to English. Several times slower than Turbo.",
             tags=["Most accurate", "Translates"], rtf=1.8, gpu_rtf=0.2),
    # ---- music ----
    Component("music-musicgen", "model", "music", "MusicGen (Meta)",
              "AI-composed background music from a text description. Needs an extra ~4 GB runtime.",
              [], "CC-BY-NC 4.0", soon=True),
]

BY_ID = {c.id: c for c in CATALOG}   # built-in components (engines are looked up here)


# ---------------------------------------------------------------- custom models (user-added links)
_CUSTOM_FILE = config.DATA / "custom_models.json"
_custom_lock = threading.Lock()
CUSTOM_KINDS = {
    "llm": ("Text model (.gguf)", ".gguf"),
    "sd15": ("Image model: SD 1.5 checkpoint (.safetensors)", ".safetensors"),
    "sdxl": ("Image model: SDXL checkpoint (.safetensors)", ".safetensors"),
}


def _load_custom() -> list[dict]:
    try:
        return json.loads(_CUSTOM_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return []


def _custom_component(e: dict) -> Component:
    kind, file = e["kind"], e["file"]
    if kind == "llm":
        return Component(e["id"], "model", "llm", e["name"], "Added by you from Hugging Face.",
                         [FileSpec(e["url"], f"models/llm/custom/{file}", e["size_mb"])],
                         "See model page", custom=True, meta={"ctx": 4096, "url": e["url"]})
    files = [FileSpec(e["url"], f"models/image/custom/{file}", e["size_mb"])]
    if kind == "sdxl":
        files.append(_SDXL_VAE)
        meta = {"arch": "sdxl", "width": 768, "height": 1344, "steps": 25, "cfg": 6.0, "sampler": "euler_a",
                "vram_mb": 6500}
    else:
        meta = {"arch": "sd15", "width": 512, "height": 896, "steps": 24, "cfg": 7.0, "sampler": "euler_a",
                "vram_mb": 2800}
    meta["url"] = e["url"]
    return Component(e["id"], "model", "image", e["name"], "Added by you from Hugging Face.", files,
                     "See model page", custom=True, meta=meta)


def components() -> list[Component]:
    return CATALOG + [_custom_component(e) for e in _load_custom()]


def normalize_hf_url(url: str) -> tuple[str, str]:
    """Return (download_url, file_name) for a Hugging Face file link, or raise ValueError."""
    u = urlparse(url.strip())
    if u.scheme != "https" or u.netloc not in ("huggingface.co", "www.huggingface.co", "hf.co"):
        raise ValueError("Paste a link to a file on huggingface.co.")
    parts = [p for p in u.path.split("/") if p]
    # /{owner}/{repo}/(blob|resolve)/{revision}/{path...}
    if len(parts) < 5 or parts[2] not in ("blob", "resolve"):
        raise ValueError("That looks like a model page. Open the 'Files' tab and copy the link of one file.")
    parts[2] = "resolve"
    name = parts[-1]
    return f"https://huggingface.co/{'/'.join(parts)}", name


def add_custom(url: str, kind: str, size_mb: int) -> Component:
    dl, file = normalize_hf_url(url)
    if kind not in CUSTOM_KINDS:
        raise ValueError("Unknown model type.")
    ext = CUSTOM_KINDS[kind][1]
    if not file.lower().endswith(ext):
        raise ValueError(f"This type needs a {ext} file.")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", file).strip("-.") or f"model{ext}"
    cid = "custom-" + re.sub(r"[^a-z0-9]+", "-", Path(safe).stem.lower()).strip("-")
    with _custom_lock:
        entries = [e for e in _load_custom() if e["id"] != cid]
        entries.append({"id": cid, "name": Path(file).stem, "kind": kind, "url": dl, "file": safe,
                        "size_mb": size_mb})
        _CUSTOM_FILE.write_text(json.dumps(entries, indent=2), "utf-8")
    return get(cid)


def remove_custom(cid: str) -> None:
    with _custom_lock:
        entries = [e for e in _load_custom() if e["id"] != cid]
        _CUSTOM_FILE.write_text(json.dumps(entries, indent=2), "utf-8")


def get(cid: str) -> Component:
    for c in components():
        if c.id == cid:
            return c
    raise KeyError(cid)


def installed(category: str, kind: str = "model") -> list[Component]:
    return [c for c in components() if c.kind == kind and c.category == category and c.is_installed()]


def shared_paths_in_use(comp: Component) -> set[str]:
    """Files of `comp` that another installed component also needs (must not be deleted)."""
    mine = {f.dest for f in comp.files}
    keep: set[str] = set()
    for c in components():
        if c.id != comp.id and c.is_installed():
            keep |= mine & {f.dest for f in c.files}
    return keep


# ---------------------------------------------------------------- "will it run here?" advice
def _gb(mb: float) -> str:
    gb = round(mb / 1024 * 2) / 2          # nearest half GB reads better than 5.9 GB
    return f"{gb:g} GB"


def fit_label(c: Component, hw: dict) -> tuple[str, str]:
    """(level, reason) — advice only; the user can always download anything.

    level: great | slow | very-slow | too-big | recommended | other-backend | soon
    """
    gpu = hw.get("backend") != "cpu"
    vram = hw.get("vram_mb", 0) if gpu else 0
    ram_mb = hw.get("ram_gb", 0) * 1024
    if c.soon:
        return "soon", "Coming in a future update."
    if c.kind == "engine":
        if c.backend and c.backend != hw.get("backend"):
            return "other-backend", "Made for different hardware than this PC uses."
        return "recommended", "Matches this PC."
    if c.category == "tts":
        return "great", "Runs on the processor (CPU) and is fast."
    if c.category == "llm":
        kv = c.meta.get("kv_mb_per_1k", c.size_mb / 25) * c.meta.get("ctx", 4096) / 1024
        need = c.size_mb + kv + 500
        if vram >= need:
            return "great", f"Fits fully in your {_gb(vram)} of graphics memory."
        if vram >= c.size_mb * 0.4 and ram_mb >= need + 4096:
            pct = max(10, min(95, int(100 * (vram - kv - 500) / c.size_mb)))
            return "slow", (f"About {pct}% fits in your {_gb(vram)} graphics memory; the rest runs on the CPU, "
                            f"so it writes more slowly.")
        if ram_mb >= need + 4096:
            where = "No suitable graphics card, so it" if not vram else "Too big for your graphics memory, so it"
            level = "slow" if c.size_mb <= 1200 else "very-slow"
            return level, f"{where} runs on the CPU. Expect slow writing."
        return "too-big", f"Needs about {_gb(need + 4096)} of memory; this PC has {_gb(ram_mb)} RAM."
    if c.category == "stt":
        need_ram = c.size_mb * 2 + 1024
        if ram_mb < need_ram:
            return "too-big", f"Needs about {_gb(need_ram)} of RAM; this PC has {_gb(ram_mb)}."

        def took(rtf: float) -> str:
            secs = rtf * 600
            return "under a minute" if secs < 60 else f"about {secs / 60:.0f} min"
        cpu = took(stt_rtf(c, hw, False))
        if hw.get("backend") == "cuda":
            return "great", (f"{took(stt_rtf(c, hw, True)).capitalize()} for 10 minutes of speech with the NVIDIA "
                             f"engine ({cpu} on the processor).")
        level = "great" if stt_rtf(c, hw, False) <= 0.5 else "slow"
        return level, f"Runs on the processor (CPU): {cpu} for 10 minutes of speech on this PC."
    # image / video
    need_v =c.meta.get("vram_mb", 3000)
    need_ram = c.size_mb + 6144
    what = "per clip" if c.category == "video" else "per image"
    if vram >= need_v:
        extra = " AI video is still heavy: expect several minutes per clip." if c.category == "video" else ""
        return "great", f"Fits in your {_gb(vram)} of graphics memory.{extra}"
    if vram >= min(3000, need_v * 0.5) and ram_mb >= need_ram:
        level = "very-slow" if c.category == "video" or need_v >= vram * 2 else "slow"
        return level, (f"Works best with {_gb(need_v)} graphics memory (you have {_gb(vram)}). Part of it is kept "
                       f"in RAM, so it is slower {what}.")
    if ram_mb >= need_ram:
        return "very-slow", f"No suitable graphics card, so it runs on the CPU: several minutes {what}."
    return "too-big", f"Needs about {_gb(need_ram)} of RAM; this PC has {_gb(ram_mb)}."


def recommended_set(hw: dict) -> list[str]:
    backend = hw["backend"]
    ids = [f"llama-{backend}", f"sd-{backend}", "tts-kokoro", "img-dreamshaper8"]
    vram = hw.get("vram_mb", 0) if backend != "cpu" else 0
    if vram >= 3500 or hw.get("ram_gb", 0) >= 16:
        ids.append("llm-qwen3-4b")
    else:
        ids.append("llm-llama32-1b")
    if not shutil.which("ffmpeg"):
        ids.append("ffmpeg")
    return ids


def find_exe(folder: str, names: tuple[str, ...]) -> Path | None:
    root = config.BIN / folder
    if not root.exists():
        return None
    for name in names:
        hits = sorted(root.rglob(name), key=lambda p: len(p.parts))
        if hits:
            return hits[0]
    return None


def engine_exe(category: str, backend: str) -> tuple[Path, str]:
    """Find an installed engine executable, preferring the chosen backend."""
    if category == "stt":
        for b in ("cuda", "cpu") if backend == "cuda" else ("cpu",):
            comp = BY_ID[f"whisper-{b}"]
            exe = find_exe(f"whisper-{b}", ("whisper-cli.exe",)) if comp.is_installed() else None
            if exe:
                return exe, b
        raise RuntimeError("Speech-to-text engine not installed. Open Models → Speech to text.")
    names = {"llm": ("llama-server.exe",), "image": ("sd-cli.exe", "sd.exe")}[category]
    prefix = {"llm": "llama", "image": "sd"}[category]
    order = [backend] + [b for b in ("cuda", "vulkan", "cpu") if b != backend]
    for b in order:
        comp = BY_ID.get(f"{prefix}-{b}")
        if comp and comp.is_installed():
            exe = find_exe(f"{prefix}-{b}", names)
            if exe:
                return exe, b
    raise RuntimeError(f"No {category} engine installed. Open Models and run One-click setup.")


def ffmpeg_exe() -> str:
    sys_ff = shutil.which("ffmpeg")
    if sys_ff:
        return sys_ff
    exe = find_exe("ffmpeg", ("ffmpeg.exe",))
    if not exe:
        raise RuntimeError("FFmpeg not installed. Open Models and install FFmpeg.")
    return str(exe)
