"""Text-to-speech with Kokoro (ONNX, runs on CPU)."""
from __future__ import annotations

import io
import threading

import numpy as np
import soundfile as sf

from .. import catalog, config

# Friendly labels for the best Kokoro voices; any other installed voice still works.
VOICE_LABELS = {
    "af_heart": "Heart — US female (warm)",
    "af_bella": "Bella — US female (bright)",
    "af_nicole": "Nicole — US female (soft)",
    "am_michael": "Michael — US male",
    "am_fenrir": "Fenrir — US male (deep)",
    "am_puck": "Puck — US male (lively)",
    "bf_emma": "Emma — UK female",
    "bm_george": "George — UK male",
    "bm_fable": "Fable — UK male (storyteller)",
    "hf_alpha": "Alpha — Hindi female",
    "hf_beta": "Beta — Hindi female",
    "hm_omega": "Omega — Hindi male",
    "hm_psi": "Psi — Hindi male",
}

_LANG_BY_PREFIX = {"a": "en-us", "b": "en-gb", "h": "hi", "e": "es", "f": "fr-fr",
                   "i": "it", "p": "pt-br", "j": "ja", "z": "cmn"}


def lang_for(voice: str) -> str:
    return _LANG_BY_PREFIX.get(voice[:1], "en-us")


class TTSEngine:
    def __init__(self) -> None:
        self._k = None
        self._lock = threading.Lock()

    def _model(self):
        with self._lock:
            if self._k is None:
                comp = catalog.get("tts-kokoro")
                if not comp.is_installed():
                    raise RuntimeError("Voice model not installed. Open Models and run One-click setup.")
                from kokoro_onnx import Kokoro
                files = [config.DATA / f.dest for f in comp.files]
                self._k = Kokoro(str(files[0]), str(files[1]))
            return self._k

    def voices(self) -> list[dict]:
        names = sorted(self._model().get_voices())
        ordered = [v for v in VOICE_LABELS if v in names] + [v for v in names if v not in VOICE_LABELS]
        return [{"id": v, "label": VOICE_LABELS.get(v, v), "lang": lang_for(v)} for v in ordered]

    def synth(self, text: str, voice: str = "af_heart", speed: float = 1.0) -> tuple[np.ndarray, int]:
        k = self._model()
        with self._lock:   # onnxruntime session: one request at a time
            samples, sr = k.create(text, voice=voice, speed=speed, lang=lang_for(voice))
        return np.asarray(samples, dtype=np.float32), sr

    def wav_bytes(self, text: str, voice: str, speed: float = 1.0) -> bytes:
        samples, sr = self.synth(text, voice, speed)
        buf = io.BytesIO()
        sf.write(buf, samples, sr, format="WAV")
        return buf.getvalue()


tts = TTSEngine()
