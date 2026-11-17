"""Controlling LM Studio's loaded models -- over its REST API, or its `lms` CLI when REST can't do it.

LM Studio loads a model just in time on the first request, with its own (often small) default
context; `ensure_loaded` loads it with the context the app's prompts are sized for instead.
`unload_all` frees the GPU for another job (image or speech generation); `models_unloaded` does that
for the length of a block and then puts every model back exactly as it was.

Async apps: these calls are short and blocking; run them with `await asyncio.to_thread(...)`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx2

from llm_lib.errors import LLMError

DEFAULT_URL = "http://127.0.0.1:1234/v1"
LOAD_TIMEOUT_S = 900.0  # a 35B model can take minutes to load from disk
Runner = Callable[..., subprocess.CompletedProcess]
Log = Callable[[str], None]


def find_lms() -> str | None:
    """`lms` on PATH, else where LM Studio installs it (~/.lmstudio/bin) -- it only adds itself to
    PATH when the user runs its bootstrap step."""
    if found := shutil.which("lms"):
        return found
    candidate = Path.home() / ".lmstudio" / "bin" / ("lms.exe" if os.name == "nt" else "lms")
    return str(candidate) if candidate.exists() else None


def server_root(base_url: str) -> str:
    """http://host:1234/v1 -> http://host:1234 (LM Studio's own API lives beside /v1)."""
    return re.sub(r"/v1/?$", "", base_url.rstrip("/"))


class LMStudio:
    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        *,
        api_key: str | None = None,
        http: httpx2.Client | None = None,
        lms_path: str | None = None,
        runner: Runner = subprocess.run,
        log: Log | None = None,
    ) -> None:
        self.root = server_root(base_url)
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = http or httpx2.Client(timeout=httpx2.Timeout(15.0, read=LOAD_TIMEOUT_S))
        self._lms = lms_path
        self._run = runner
        self.log = log or (lambda _: None)

    # ------------------------------------------------------------------ REST

    def _get(self, path: str) -> Any:
        """JSON of a GET, or None when the server isn't LM Studio, is unreachable, or errors."""
        try:
            resp = self._http.get(f"{self.root}{path}", headers=self.headers, timeout=10.0)
            return resp.json() if resp.status_code < 400 else None
        except (httpx2.HTTPError, ValueError):
            return None

    def models(self) -> list[dict[str, Any]]:
        """Every downloaded model: id, type (llm / vlm / embeddings), state (loaded / not-loaded),
        arch, quantization, max_context_length, loaded_context_length. Loaded ones first."""
        data = self._get("/api/v0/models")
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        out = [m for m in items if isinstance(m, dict) and "id" in m]
        return sorted(out, key=lambda m: (m.get("state") != "loaded", str(m["id"]).lower()))

    def model_state(self, model: str) -> dict[str, Any] | None:
        """LM Studio's view of one model (state, loaded_context_length, ...), None if unknown."""
        data = self._get(f"/api/v0/models/{quote(model, safe='')}")
        return data if isinstance(data, dict) else None

    def loaded_instances(self) -> list[dict[str, Any]]:
        """Each loaded instance: key, type, instance_id, context_length, parallel."""
        data = self._get("/api/v1/models")
        listed = data.get("models") if isinstance(data, dict) else None
        out = []
        for m in listed if isinstance(listed, list) else []:
            for inst in m.get("loaded_instances") or []:
                config = inst.get("config") or {}
                out.append(
                    {
                        "key": m.get("key"),
                        "type": m.get("type"),
                        "instance_id": inst.get("id"),
                        "context_length": config.get("context_length"),
                        "parallel": config.get("parallel") or 1,
                    }
                )
        return out

    def loaded_context(self, model: str) -> int | None:
        """The context length `model` is loaded with, or None: not loaded, not LM Studio, unreachable."""
        for inst in self.loaded_instances():
            n = inst.get("context_length")
            if inst.get("key") == model and isinstance(n, int) and n > 0:
                return n
        state = self.model_state(model) or {}
        n = state.get("loaded_context_length") if state.get("state") == "loaded" else None
        return n if isinstance(n, int) and n > 0 else None

    def load(self, model: str, context_length: int, *, parallel: int = 1, unload_others: bool = True) -> dict[str, Any]:
        """Load `model` over REST with this context and `parallel` slots (which share the context).
        `unload_others` frees the GPU first. A model already loaded exactly so is left as it is."""
        try:
            resp = self._http.get(f"{self.root}/api/v1/models", headers=self.headers)
        except httpx2.HTTPError as exc:
            raise LLMError(f"cannot reach LM Studio at {self.root}: {exc}") from exc
        if resp.status_code >= 400:
            raise LLMError("this server can't load models on request (LM Studio's REST API only)")
        listed = resp.json().get("models", [])
        if not any(m.get("key") == model for m in listed):
            raise LLMError(f"LM Studio has no model {model!r}")
        for m in listed:
            for inst in m.get("loaded_instances") or []:
                config = inst.get("config") or {}
                if (
                    m.get("key") == model
                    and config.get("context_length") == context_length
                    and (config.get("parallel") or 1) == parallel
                ):
                    return {"status": "loaded", "model": model, "already": True}
                if unload_others and m.get("type") in ("llm", "vlm"):
                    self._http.post(
                        f"{self.root}/api/v1/models/unload", headers=self.headers, json={"instance_id": inst["id"]}
                    )
        resp = self._http.post(
            f"{self.root}/api/v1/models/load",
            headers=self.headers,
            json={"model": model, "context_length": context_length, "parallel": parallel},
        )
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code >= 400:
            raise LLMError(f"LM Studio couldn't load {model}: {body.get('error', resp.text[:300])}")
        return {"status": "loaded", "model": model, "load_time_seconds": body.get("load_time_seconds")}

    def unload_all(self) -> list[str]:
        """Unload every LLM to free the GPU; returns the keys that were loaded. Falls back to
        `lms unload --all` when the REST API isn't there."""
        instances = [i for i in self.loaded_instances() if i.get("type") in ("llm", "vlm")]
        if not instances and self._get("/api/v1/models") is None:
            if self.cli_available():
                keys = [m.get("identifier") or m.get("modelKey") for m in self.ps()]
                self.cli_unload_all()
                return keys
            return []
        unloaded = []
        for inst in instances:
            r = self._http.post(
                f"{self.root}/api/v1/models/unload", headers=self.headers, json={"instance_id": inst["instance_id"]}
            )
            if r.status_code >= 400:
                raise LLMError(f"LM Studio couldn't unload {inst.get('key')}: {r.text[:300]}")
            unloaded.append(inst["key"])
        return unloaded

    def ensure_loaded(
        self, model: str, context_length: int, *, parallel: int = 1, cli_args: tuple[str, ...] | list[str] = ()
    ) -> None:
        """Load `model` with `context_length` unless LM Studio has it loaded already. Best effort:
        when it can't (not LM Studio, no REST API and no CLI, the load fails) it logs why and LM
        Studio loads the model itself on the request, as it would have anyway."""
        state = self.model_state(model)
        if state is None or state.get("state") == "loaded":
            loaded = (state or {}).get("loaded_context_length")
            if loaded and loaded < context_length:
                self.log(
                    f"{model} is already loaded with a {loaded:,}-token context "
                    f"(wanted {context_length:,}); kept as it is"
                )
            return
        self.log(
            f"loading {model} in LM Studio: {context_length:,}-token context, {parallel} prediction(s) at once"
            + (f", {' '.join(cli_args)}" if cli_args else "")
        )
        started = time.perf_counter()
        if not cli_args:  # REST can't take CLI flags such as --gpu max
            try:
                self.load(model, context_length, parallel=parallel, unload_others=False)
                self.log(f"loaded {model} in {time.perf_counter() - started:.1f}s")
                return
            except (LLMError, httpx2.HTTPError, ValueError) as exc:
                self.log(f"REST load failed ({exc}); trying the `lms` CLI")
        if not self.cli_available():
            self.log("LM Studio's `lms` CLI not found; it will load the model with its default")
            return
        cmd = [self.lms, "load", model, "--context-length", str(context_length), "--yes"]
        if parallel > 1:
            cmd += ["--parallel", str(parallel)]
        cmd += list(cli_args)
        try:
            result = self._run(cmd, capture_output=True, text=True, check=False, timeout=LOAD_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as exc:
            self.log(f"`lms load` failed ({exc}); LM Studio will load it with its default")
            return
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()[-1:]
            self.log(
                f"`lms load` failed ({' '.join(detail) or result.returncode}); LM Studio will load it with its default"
            )
            return
        self.log(f"loaded {model} in {time.perf_counter() - started:.1f}s")

    # ------------------------------------------------------------------ `lms` CLI

    @property
    def lms(self) -> str | None:
        if self._lms is None:
            self._lms = find_lms() or ""
        return self._lms or None

    def cli_available(self) -> bool:
        return self.lms is not None

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        if not self.lms:
            raise LLMError("LM Studio's `lms` CLI wasn't found")
        return self._run([self.lms, *args], capture_output=True, text=True, check=False, timeout=LOAD_TIMEOUT_S)

    def ps(self) -> list[dict[str, Any]]:
        """`lms ps --json`: the loaded LLMs with modelKey, identifier, contextLength, parallel, ttlMs."""
        result = self._cli("ps", "--json")
        if result.returncode != 0:
            raise LLMError(f"lms ps failed: {(result.stderr or result.stdout).strip()}")
        return [m for m in json.loads(result.stdout or "[]") if m.get("type", "llm") == "llm"]

    def cli_unload_all(self) -> None:
        result = self._cli("unload", "--all")
        if result.returncode != 0:
            raise LLMError(f"lms unload --all failed: {(result.stderr or result.stdout).strip()}")

    def cli_reload(self, model: dict[str, Any]) -> None:
        """Load a model back as `lms ps` described it. Uses modelKey, never selectedVariant:
        `lms load qwen/qwen3.8-27b@4bit` fails to resolve where `lms load qwen/qwen3.8-27b` works."""
        args = ["load", model["modelKey"], "-y"]
        if model.get("contextLength"):
            args += ["--context-length", str(model["contextLength"])]
        if model.get("parallel"):
            args += ["--parallel", str(model["parallel"])]
        if model.get("identifier") and model["identifier"] != model["modelKey"]:
            args += ["--identifier", model["identifier"]]
        if model.get("ttlMs"):
            args += ["--ttl", str(model["ttlMs"] // 1000)]
        result = self._cli(*args)
        if result.returncode != 0:
            raise LLMError(f"lms load {model['modelKey']} failed: {(result.stderr or result.stdout).strip()}")

    @contextmanager
    def models_unloaded(self, log: Log | None = None) -> Iterator[list[dict[str, Any]]]:
        """Unload every loaded LLM for the block, then reload each with the same key, context,
        parallelism, identifier and TTL -- also when the block raises, so a failed step never leaves
        the user without their LLM. A model reloaded at LM Studio's default context would silently
        shrink every prompt budget sized against the old one. Yields the snapshot."""
        log = log or self.log
        if not self.cli_available():
            log("LM Studio's `lms` CLI wasn't found; continuing without unloading")
            yield []
            return
        snapshot = self.ps()
        if snapshot:
            log(
                f"Unloading {len(snapshot)} LM Studio model(s): "
                + ", ".join(m.get("identifier") or m["modelKey"] for m in snapshot)
            )
            self.cli_unload_all()
        try:
            yield snapshot
        finally:
            for model in snapshot:
                try:
                    self.cli_reload(model)
                    log(
                        f"Reloaded {model.get('identifier') or model['modelKey']} "
                        f"(context {model.get('contextLength')})."
                    )
                except Exception as exc:
                    log(f"Error: could not reload {model['modelKey']} into LM Studio: {exc}")

    def close(self) -> None:
        self._http.close()
