"""Manages a llama.cpp `llama-server` process and talks to its OpenAI-style API."""
from __future__ import annotations

import atexit
import json
import re
import subprocess
import threading
import time
from typing import Iterator

import httpx

from .. import catalog, config, hardware


class LLMEngine:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.model_id: str | None = None
        self.backend: str | None = None
        self._lock = threading.RLock()
        self.base = f"http://127.0.0.1:{config.LLM_PORT}"
        atexit.register(self.stop)

    # ---------------- lifecycle ----------------
    def default_model(self) -> str:
        wanted = config.load_settings().get("llm_model")
        have = catalog.installed("llm")
        if wanted and any(c.id == wanted for c in have):
            return wanted
        if not have:
            raise RuntimeError("No text model installed. Open Models and run One-click setup.")
        return have[0].id

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def ensure(self, model_id: str | None = None) -> None:
        with self._lock:
            model_id = model_id or self.default_model()
            if self.running() and self.model_id == model_id:
                return
            self.stop()
            hw = hardware.detect()
            exe, backend = catalog.engine_exe("llm", hw["backend"])
            comp = catalog.get(model_id)
            ctx = comp.meta["ctx"]
            ngl = self._gpu_layers(comp, backend)
            log_path = config.LOGS / "llama-server.log"
            log = open(log_path, "w", encoding="utf-8", errors="replace")
            cmd = [str(exe), "-m", str(comp.main_path()), "--host", "127.0.0.1",
                   "--port", str(config.LLM_PORT), "-c", str(ctx),
                   *(["-ngl", str(ngl)] if ngl is not None else ["--fit", "on"]),
                   "--jinja", "-t", str(max(1, hw["cores"]))]
            self.proc = subprocess.Popen(cmd, cwd=exe.parent, stdout=log, stderr=subprocess.STDOUT,
                                         creationflags=config.NO_WINDOW)
            self.model_id, self.backend = model_id, backend
            deadline = time.time() + 300
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    log.close()
                    tail = log_path.read_text("utf-8", errors="replace")[-1500:]
                    self.proc = None
                    raise RuntimeError(f"Text engine exited while loading the model:\n{tail}")
                try:
                    if httpx.get(self.base + "/health", timeout=2).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            self.stop()
            raise RuntimeError("Text engine did not become ready within 5 minutes.")

    def _gpu_layers(self, comp: catalog.Component, backend: str) -> int | None:
        if backend == "cpu":
            return 0
        if "layers" not in comp.meta:
            return None         # user-added model: llama.cpp measures and fits it
        free = hardware.free_vram_mb() or hardware.detect().get("vram_mb", 0)
        if backend == "vulkan" and not free:
            return 999          # unknown VRAM on non-NVIDIA: let llama.cpp try everything
        kv = comp.meta["kv_mb_per_1k"] * comp.meta["ctx"] / 1024
        budget = free - kv - 450     # compute buffers + safety margin
        if budget >= comp.size_mb:
            return 999
        return max(0, int(comp.meta["layers"] * budget / comp.size_mb))

    def stop(self) -> None:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            self.proc = None
            self.model_id = None

    # ---------------- inference ----------------
    def _payload(self, messages: list[dict], **kw) -> dict:
        p = {"messages": messages, "temperature": kw.get("temperature", 0.7), "top_p": 0.8,
             "top_k": 20, "max_tokens": kw.get("max_tokens", 1500)}
        if kw.get("schema"):
            p["response_format"] = {"type": "json_schema",
                                    "json_schema": {"name": "output", "schema": kw["schema"]}}
        return p

    def chat(self, messages: list[dict], model_id: str | None = None, **kw) -> str:
        self.ensure(model_id)
        r = httpx.post(self.base + "/v1/chat/completions", json=self._payload(messages, **kw),
                       timeout=httpx.Timeout(600.0, connect=10.0))
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def chat_json(self, messages: list[dict], schema: dict, **kw) -> dict:
        text = self.chat(messages, schema=schema, **kw)
        try:
            return json.loads(text)
        except ValueError:
            m = re.search(r"\{.*\}", text, re.S)
            if not m:
                raise RuntimeError("The model did not return valid JSON. Try again.")
            return json.loads(m.group(0))

    def stream(self, messages: list[dict], model_id: str | None = None, **kw) -> Iterator[str]:
        self.ensure(model_id)
        payload = self._payload(messages, **kw) | {"stream": True}
        with httpx.stream("POST", self.base + "/v1/chat/completions", json=payload,
                          timeout=httpx.Timeout(600.0, connect=10.0)) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content")
                except (ValueError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta


llm = LLMEngine()
