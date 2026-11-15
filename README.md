# Featherbox

**Local AI, light on your PC.** One app that downloads and runs text, image, video and voice AI models
entirely on your own computer: no cloud, no account, no subscription. It's built for ordinary laptops
with 4–6 GB graphics cards (and works on CPU-only PCs, more slowly).

- **Models**: a catalog of text, image, video and voice models, each with plain "will it run on this PC?"
  advice. Download any of them, pick which one is used, or add your own Hugging Face links.
- **Chat** with a local text model.
- **Studio** (the first ready-made creation): type a topic and get a finished vertical video with a
  script, voiceover, AI pictures or AI motion, captions and music.
- Standalone image, video and voice tools are next on the roadmap.

## Install (for users)

Run `Featherbox-Setup-<version>.exe` (about 80 MB). There's no admin prompt and nothing else to
install first: Python and the interface are bundled. Setup adds Start-menu and desktop shortcuts and
an entry in Windows "Installed apps".

- **Featherbox** opens in its own window (Microsoft Edge app mode). A tray icon by the clock keeps it
  running; right-click it → **Quit Featherbox**. Opening the shortcut again just brings the window back.
- First start: **Models → One-click setup** downloads the AI engines and models for that PC (once).
- Everything lives in the install folder: the program in `app\` and `python\`, and models, videos and
  settings in `data\`. Updating (running a newer Setup) replaces the program and keeps `data\`.
  Uninstalling asks whether to keep `data\` so a reinstall doesn't need to download again.
- Unsigned build: Windows SmartScreen shows "Windows protected your PC" → **More info → Run anyway**
  until the installer is code-signed.

Silent install for IT/testing: `Featherbox-Setup.exe /S /D=C:\Featherbox`; uninstall with
`uninstall.exe /S` (add `/DELETEDATA` to remove models and videos too).

### Building the installer

```bash
backend\.venv\Scripts\python installer\build.py        # -> installer\dist\Featherbox-Setup-<version>.exe
```

No extra tools needed. It copies the Python runtime this venv was made from (trimmed) plus the venv's
packages and the Microsoft C++ runtime DLLs, builds the UI, compiles the launcher, setup and uninstaller
with the C# compiler that ships with Windows (`installer\Launcher.cs`, `installer\Setup.cs`), and embeds
everything in one Setup.exe. Bump `APP_VERSION` in `backend/app/config.py` for a new release.

## Quick start (developers)

Double-click `start.bat`. The first run creates the Python environment and builds the UI. After that
it opens `http://127.0.0.1:8420` in your browser.

1. **Models** → **One-click setup** installs the best engines and models for the detected hardware
   (about 6.5 GB on an NVIDIA GPU). Downloads resume if interrupted.
2. **Studio** → type a topic, pick language, length and tone → **Write script**.
3. Edit the scenes if you like, pick a visual style, voice and background music → **Make video**.
4. On the finished video, fix any single scene: **New image** repaints it (about 30 s) and
   **Update voice** re-records an edited line (about 15 s). Only that scene is rebuilt. Background
   music can be changed in about 10 s.
5. Finished videos are listed in **Library** (**Edit scenes & music** reopens them) and saved under
   `data/outputs/<project>/final.mp4`.

Developer requirements: Windows 10/11, Python 3.11+, Node 18+ (only to build the UI), and an FFmpeg on
PATH, or install it from the Models page.

## How it works

```
React UI (frontend/)  ──HTTP──▶  FastAPI backend (backend/app)
                                   ├─ hardware.py   picks CUDA / Vulkan / CPU and model sizes
                                   ├─ catalog.py    pinned engine builds + curated models
                                   ├─ downloads.py  resumable installer
                                   ├─ jobs.py       one GPU job at a time
                                   ├─ engines/
                                   │   ├─ llm.py        llama.cpp `llama-server` (script writing, chat)
                                   │   ├─ image.py      stable-diffusion.cpp CLI (scene images)
                                   │   ├─ tts.py        Kokoro ONNX on CPU (voiceover, EN + HI)
                                   │   ├─ textrender.py HarfBuzz-shaped captions (Hindi renders correctly)
                                   │   ├─ music.py      built-in music moods (synthesised, no download) + your tracks
                                   │   └─ video.py      FFmpeg: Ken Burns motion, captions, concat, music ducking
                                   └─ recipes/topic_to_short.py
```

Each project folder keeps its scene images, voice lines and video segments, so a touch-up rebuilds
one segment, re-joins the segments (a stream copy) and re-mixes the music.

**Music.** Five built-in moods (Calm, Uplifting, Playful, Mysterious, Epic) are synthesised with numpy
(pads, bass, plucks, bells, FFT reverb), so they're royalty-free and need no download. "Auto" picks
a mood from the script's tone. Your own tracks (mp3/wav/m4a/ogg/flac) go to `data/music/`. FFmpeg
`sidechaincompress` ducks the music about 9 dB while the voice is speaking (about 22 dB under the
voice at the default 50% volume).

Low-VRAM design: the text model is unloaded before images are generated. Voiceover runs on the CPU
**in parallel** with image generation on the GPU. If the text model doesn't fit fully, GPU layers are
calculated from free VRAM.

## Model library

The **Models** page lists every model the app knows about, in tabs: Text, Image, Video, Voice, Music and
Engines. Each model gets advice for *this* PC, but nothing is ever blocked: you can download any model.

| Badge | Meaning |
|---|---|
| Runs well | Fits in the graphics memory (or runs fast on the CPU, for voice) |
| Runs slowly | Part of the model is kept in RAM / runs on the CPU |
| Very slow | CPU only, or far more graphics memory needed than this PC has |
| Probably won't run | Not enough RAM; you get a "download anyway?" check first |

- **Use this** picks which installed text and image model the Studio uses.
- **Add your own model**: paste a Hugging Face file link (`.gguf` text models, or SD 1.5 / SDXL
  `.safetensors` checkpoints). The link is checked and the size read before it is added. Gated models
  (which need a Hugging Face login) are not supported yet. User-added text models are placed on the
  GPU automatically by llama.cpp (`--fit on`).
- Files shared by several models (the Flux VAE, Qwen3 4B used by Z-Image, the umt5 video encoder) are
  downloaded once and only deleted when no installed model needs them.
- Image architectures supported by the engine: SD 1.5, SDXL, FLUX.1 and Z-Image. Piper and MusicGen
  are listed as coming soon.

## AI motion (optional video models)

With a Wan video model installed (Models → Video), scenes can move instead of using a slow zoom and pan:

- **Studio → Motion**: *Still pictures*, *AI motion on the first scene* (a moving hook) or
  *AI motion on every scene*.
- **Finished video → scene card**: **Animate** a still scene, **New motion** for a different take, or
  **Make still** to go back. Only that scene is rebuilt.
- **Wan 2.2 TI2V 5B** animates the picture Z-Image painted (image-to-video), so the look stays the same.
  **Wan 2.1 T2V 1.3B** is lighter but makes the clip from the text prompt only.
- How a clip is made: 33 frames at 480×832 (about 2 s), interpolated to 30 fps, upscaled to 1080×1920,
  then played forwards and backwards (a seamless "ping-pong" loop), slowed 1.25× and repeated to fill the
  voice line. Captions are burned in on top as usual.
- **Clip quality** (Studio → Motion, a global setting):
  - **Fast clips** (default) decode with the 22 MB TAEHV tiny decoder (MIT, `taew2_2` / `taew2_1`).
    Slightly softer detail; the first frame still matches the painted picture.
  - **Best quality** uses the full Wan VAE.
- Speed on an RTX 3050 4 GB (Wan 2.2 5B, 20 steps, ~2 min of animating per clip):

  | | Fast clips | Best quality |
  |---|---|---|
  | Decoding one clip | 1.4 s | ~220 s |
  | Animating one scene of a finished video | **2.6 min** | 7.1 min |
  | 4-scene Short with a moving first scene | ~5 min | 10 min |

  For Best on cards under 10 GB the decoder is told to use 128 px tiles with less overlap up front (the
  default tiles run out of memory and retry, which took twice as long; decoding on the CPU took 37 min).

## Development

```bash
# backend (port 8420)
cd backend && .venv/Scripts/python run.py --no-browser
# frontend with hot reload (port 5173, proxies /api to 8420)
cd frontend && npm run dev
```

The data folder can be moved with the `FEATHERBOX_DATA` environment variable (the old `SRIJAN_DATA`
still works).

## Licenses of bundled/downloaded components

| Component | License |
|---|---|
| llama.cpp, stable-diffusion.cpp | MIT |
| Qwen2.5 / Qwen3 models, Mistral Nemo, Sarvam-M | Apache-2.0 |
| Llama 3.1 / 3.2 | Llama Community License |
| Gemma 3 | Gemma Terms of Use |
| DreamShaper 8, Realistic Vision 6, Counterfeit 2.5, Juggernaut XL | CreativeML OpenRAIL-M |
| DreamShaper XL Lightning | OpenRAIL++ |
| Z-Image Turbo, FLUX.1 schnell, Wan 2.1 / 2.2 | Apache-2.0 |
| Kokoro TTS | Apache-2.0 |
| FFmpeg (BtbN GPL build) | GPL |

Check each license before selling the app or using generated content commercially.

## Roadmap

- [x] Regenerate a single scene image or voice line without re-rendering everything
- [x] Background music with auto-ducking (built-in moods + your own tracks)
- [ ] Optional MusicGen model for AI-composed music (needs PyTorch, about 4 GB)
- [ ] Fact-check pass for scripts (small models still slip, especially in Hindi)
- [ ] More recipes: lecture → notes + quiz, product photo → ad, podcast → clips
- [x] One-click Windows installer (no admin, bundled Python, tray launcher, uninstaller)
- [ ] Code-sign the installer (removes the SmartScreen warning)
- [ ] Mac version
- [x] Bigger model library with "will it run here?" advice, model choice and your own Hugging Face links
- [x] AI motion with Wan video models (whole video, first scene, or single scenes)
- [ ] Piper voices for more Indian languages
- [ ] Standalone tools: image generator, video generator, text-to-speech, speech-to-text
- [ ] Hugging Face model search inside the Models page
