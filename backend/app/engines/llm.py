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


class BadReply(RuntimeError):
    """The model answered, but not with the JSON we asked for. `problem` is the short reason."""

    def __init__(self, problem: str, advice: str = "Try again, or pick a larger text model.") -> None:
        super().__init__(f"{problem} {advice}")
        self.problem = problem


def json_example(schema: dict):
    """A filled-in example of what `schema` describes, with "..." for text. Keeps the schema's key
    order: with the short fields first, Gemma 3 1B stopped before writing the scenes."""
    t = schema.get("type")
    if t == "object":
        return {k: json_example(v) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return [json_example(schema.get("items", {}))]
    if t in ("number", "integer"):
        return 0
    if t == "boolean":
        return False
    return "..."


def _loads(text: str):
    """json.loads, plus repairs for slips seen from Gemma 3 1B: text or a stray "}" after the object,
    and a comma before a closing bracket."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    start = text.find("{")
    if start < 0:
        return None
    body = text[start:]
    for candidate in (body, re.sub(r",(\s*[}\]])", r"\1", body)):
        try:
            return json.JSONDecoder().raw_decode(candidate)[0]   # the first complete object; ignore the rest
        except ValueError:
            continue
    return None


def _fit_keys(data: dict, required: list[str]) -> dict:
    """Forgive slips small models make: keys in another case ("Title"), the whole answer wrapped in
    one extra object ({"script": {...}}), or the answer put inside a copy of the schema's
    {"type": "object", "properties": {...}}."""
    if not required or all(k in data for k in required):
        return data
    props = data.get("properties")
    if isinstance(props, dict) and all(k in props for k in required) and not any(
            isinstance(props[k], dict) and "type" in props[k] for k in required):   # real values, not a schema
        return _fit_keys(props, required)
    lower = {k.lower(): k for k in data if isinstance(k, str)}
    fixed = dict(data)
    for k in required:
        if k not in fixed and k.lower() in lower:
            fixed[k] = fixed.pop(lower[k.lower()])
    if all(k in fixed for k in required):
        return fixed
    if len(data) == 1:
        inner = next(iter(data.values()))
        if isinstance(inner, dict) and any(k in inner or k.lower() in {x.lower() for x in inner} for k in required):
            return _fit_keys(inner, required)
    return fixed


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

    PLAIN_TRIES = 2     # tries without the enforced format, after the first enforced one

    def chat_json(self, messages: list[dict], schema: dict, defaults: dict | None = None, **kw) -> dict:
        """Ask for JSON that follows `schema`. First with the format enforced by llama-server; if the
        server rejects the model's output (500) or the answer is unusable, ask again in plain words with
        an example of the shape, and read the JSON out of the reply ourselves.
        `defaults`: required keys that may be filled in when a plain reply leaves them out."""
        first = ""
        try:
            reply = self.chat(messages, schema=schema, **kw)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 500:
                raise
            # Small models (Gemma 3 1B) can break the enforced format (e.g. ```json fences), and
            # llama-server then rejects its own output: "does not match the expected peg-native format".
            first = exc.response.text[:300]
        else:
            try:
                return self._parse_json(reply, schema, defaults)
            except BadReply as exc:
                self._log_bad_reply(reply, exc)
                first = str(exc)

        # Show an example of the shape, not the JSON Schema itself: small models tend to copy a
        # schema back ({"type": "object", "properties": ...}) instead of filling it in.
        msgs = [dict(m) for m in messages]
        msgs[-1]["content"] += ("\n\nReply with only one JSON object shaped like this example: the same keys, "
                                "with each ... replaced by your own content and as many list items as asked "
                                "for above. No code fences and no other text.\n"
                                + json.dumps(json_example(schema), ensure_ascii=False, indent=1))
        last: BadReply | None = None
        for attempt in range(self.PLAIN_TRIES):
            try:
                # a cooler second try: fewer creative detours from the shape
                reply = self.chat(msgs, **((kw | {"temperature": 0.3}) if attempt else kw))
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(f"The text engine failed: {exc.response.text[:300] or first}") from exc
            try:
                return self._parse_json(reply, schema, defaults)
            except BadReply as exc:
                self._log_bad_reply(reply, exc)
                last = exc
        raise BadReply(last.problem, self._advice())

    def _advice(self) -> str:
        """What to do after a model failed the format: try again, or switch to a bigger installed model."""
        try:
            current = catalog.get(self.model_id) if self.model_id else None
            bigger = sorted((c for c in catalog.installed("llm")
                             if current and c.id != current.id and c.size_mb > current.size_mb),
                            key=lambda c: c.size_mb)
        except Exception:     # unknown or removed model: fall back to the general advice
            current, bigger = None, []
        name = current.name if current else "The text model"
        if bigger:
            return (f"{name} is too small to follow this format reliably. Try again, or switch to "
                    f"{bigger[0].name} (already installed): Models → Use this.")
        return f"{name} may be too small to follow this format reliably. Try again, or install a larger text model."

    @staticmethod
    def _log_bad_reply(reply: str, err: Exception) -> None:
        """Keep unusable replies in logs/text-replies.log so a failure can be looked at afterwards."""
        try:
            path = config.LOGS / "text-replies.log"
            old = path.read_text("utf-8", errors="replace")[-200_000:] if path.exists() else ""
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            path.write_text(f"{old}\n===== {stamp} · {getattr(err, 'problem', err)}\n{reply[:20_000]}\n", "utf-8")
        except OSError:
            pass

    @staticmethod
    def _parse_json(text: str, schema: dict | None = None, defaults: dict | None = None) -> dict:
        data = _loads(re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip()))
        if not isinstance(data, dict):
            raise BadReply("The model did not return valid JSON.")
        required = (schema or {}).get("required", [])
        essential = [k for k in required if k not in (defaults or {})]
        data = _fit_keys(_fit_keys(data, essential), required)
        for k, v in (defaults or {}).items():
            if k not in data:
                data[k] = json.loads(json.dumps(v))   # a fresh copy
        missing = [k for k in required if k not in data]
        if missing:
            if data.get("type") == "object" and isinstance(data.get("properties"), dict):
                raise BadReply("The model repeated the format description instead of writing the answer.")
            raise BadReply(f"The model's answer was missing {', '.join(missing)}.")
        return data

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
