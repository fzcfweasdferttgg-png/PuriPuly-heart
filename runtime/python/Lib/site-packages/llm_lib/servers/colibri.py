"""colibri's own endpoints, beside the chat API every OpenAI-protocol client already uses.

    col = Colibri.from_settings(settings)          # or Colibri("http://127.0.0.1:8000", api_key=...)
    col.health()                                   # status, arch, scheduler (active/queued/capacity), kv_slots
    d = col.decide(ticket_text, {
        "urgent": noul("Does this message express urgency?"),
        "team": choice("Which team should handle this?", {"billing": "payments", "technical": "bugs"}),
        "severity": score("How severe is the impact?", ["none", "minor", "blocked", "losing money"]),
    })
    d["team"].choice, d["team"].confidence, d["urgent"].noul, d["severity"].score
    image = col.generate_image("a red fox in the snow", size="1024x576", steps=8, seed=42)
    Path("fox.png").write_bytes(image.png)

`decide` is System One mode (POST /v1/systemone): closed questions answered with a probability per
allowed option, by scoring rather than writing -- so the answer is always one of the options, and
`confidence` says whether the model knows. The state is read once and kept in the engine, so every
question after the first on the same text is cheap.

These calls can take minutes on a CPU box and block; async apps run them with asyncio.to_thread.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

import httpx2

from llm_lib.errors import LLMError, LLMTimeout, LLMUnavailable, status_error
from llm_lib.servers.lmstudio import server_root
from llm_lib.settings import COLIBRI_TIMEOUT_S, Settings

DEFAULT_URL = "http://127.0.0.1:8000"
MAX_QUESTIONS = 64


def noul(instructions: Any = None, *, true: str | None = None, false: str | None = None) -> dict[str, Any]:
    """A yes/no question; the answer is the probability of yes."""
    q: dict[str, Any] = {"type": "noul"}
    if instructions is not None:
        q["instructions"] = instructions
    if true is not None or false is not None:
        q["criteria"] = {k: v for k, v in (("true", true), ("false", false)) if v is not None}
    return q


def choice(instructions: Any, options: dict[str, str | None] | list[str]) -> dict[str, Any]:
    """One of 1-255 labels (a dict gives each a description); the answer has a probability per label."""
    criteria = dict.fromkeys(options) if isinstance(options, list) else dict(options)
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: Any, levels: list[str]) -> dict[str, Any]:
    """A level on an ordered scale of 1-255; the answer is the expected level (a float)."""
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


@dataclass(slots=True)
class Answer:
    type: str
    noul: float | None = None  # noul: probability of yes
    choice: str | None = None  # choice: the most likely label
    score: float | None = None  # score: the expected level, 0..n-1
    probabilities: dict[str, float] = field(default_factory=dict)  # by label, or "0".."n-1" for a score
    confidence: float | None = None  # 1 when one option holds the mass, 0 when it is flat
    legend: dict[str, str] = field(default_factory=dict)  # score: level index -> description
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def value(self) -> float | str | None:
        return {"noul": self.noul, "choice": self.choice, "score": self.score}.get(self.type)

    @property
    def level(self) -> str | None:
        """score: the description of the most likely level."""
        if not self.probabilities:
            return None
        best = max(self.probabilities, key=self.probabilities.__getitem__)
        return self.legend.get(best)

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Answer:
        return cls(
            type=raw.get("type", ""),
            noul=raw.get("noul"),
            choice=raw.get("choice"),
            score=raw.get("score"),
            probabilities=dict(raw.get("probabilities") or {}),
            confidence=raw.get("confidence"),
            legend=dict(raw.get("legend") or {}),
            raw=raw,
        )


@dataclass(slots=True)
class Decision:
    answers: dict[str, Answer]
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, question_id: str) -> Answer:
        return self.answers[question_id]


@dataclass(slots=True)
class Image:
    png: bytes
    width: int | None = None
    height: int | None = None
    seed: int | None = None  # the seed actually used (drawn by the server when none was given)
    info: dict[str, Any] = field(default_factory=dict)  # steps, timings {encode, denoise, decode}


class Colibri:
    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        *,
        api_key: str | None = None,
        http: httpx2.Client | None = None,
        timeout: float = COLIBRI_TIMEOUT_S,
    ) -> None:
        self.root = server_root(base_url)
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        self._http = http or httpx2.Client(timeout=httpx2.Timeout(timeout, connect=15.0, write=30.0, pool=15.0))

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> Colibri:
        return cls(
            settings.resolved_base_url() or DEFAULT_URL,
            api_key=settings.resolved_api_key(),
            timeout=settings.resolved_timeout,
            **kwargs,
        )

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> httpx2.Response:
        url = f"{self.root}{path}"
        try:
            resp = self._http.request(method, url, json=body, headers=self.headers)
        except httpx2.TimeoutException as exc:
            raise LLMTimeout(f"colibri didn't reply in time ({path})") from exc
        except httpx2.TransportError as exc:
            raise LLMUnavailable(f"cannot reach colibri at {self.root}: {exc}") from exc
        if resp.status_code >= 400:
            raise status_error("colibri", resp.status_code, resp.text)
        return resp

    def health(self) -> dict[str, Any]:
        """{"status": "ok"} always; with a valid key (or none configured) also arch, scheduler
        (active, queued, capacity, max_queue, ...), kv_slots, input_modalities, capabilities."""
        return self._request("GET", "/health").json()

    def metrics(self) -> str:
        """Prometheus text exposition of the scheduler (colibri_scheduler_*)."""
        return self._request("GET", "/metrics").text

    def model_id(self) -> str:
        """The id of the one model this server serves -- what every request's `model` must say."""
        data = self._request("GET", "/v1/models").json().get("data") or []
        if not data:
            raise LLMError("colibri lists no model")
        return data[0]["id"]

    def decide(
        self,
        state: Any,
        questions: dict[str, dict[str, Any]],
        *,
        model: str | None = None,
        normalize: bool | None = None,
        pin_state: bool | None = None,
        prefix: Any = None,
        cache_slot: int | None = None,
    ) -> Decision:
        """System One: answer `questions` ({id: noul(...) | choice(...) | score(...)}, 1-64 of them)
        about `state` (text, or an object/array read as JSON text) with a probability per option."""
        if not 1 <= len(questions) <= MAX_QUESTIONS:
            raise ValueError(f"decide takes 1 to {MAX_QUESTIONS} questions, got {len(questions)}")
        body: dict[str, Any] = {"state": state, "questions": questions}
        for key, value in (
            ("model", model),
            ("normalize", normalize),
            ("pin_state", pin_state),
            ("prefix", prefix),
            ("cache_slot", cache_slot),
        ):
            if value is not None:
                body[key] = value
        data = self._request("POST", "/v1/systemone", body).json()
        answers = {qid: Answer.parse(a) for qid, a in (data.get("answers") or {}).items()}
        return Decision(answers=answers, model=data.get("model", ""), usage=data.get("usage") or {}, raw=data)

    def generate_image(
        self,
        prompt: str,
        *,
        size: str | None = None,
        width: int | None = None,
        height: int | None = None,
        steps: int | None = None,
        seed: int | None = None,
        model: str | None = None,
        **extra: Any,
    ) -> Image:
        """POST /v1/images/generations on a colibri serving an image model (Qwen-Image): one PNG."""
        body: dict[str, Any] = {"prompt": prompt, "model": model or self.model_id(), "response_format": "b64_json"}
        for key, value in (("size", size), ("width", width), ("height", height), ("steps", steps), ("seed", seed)):
            if value is not None:
                body[key] = value
        body.update(extra)
        data = self._request("POST", "/v1/images/generations", body).json()
        try:
            png = base64.b64decode(data["data"][0]["b64_json"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError(f"colibri returned no image: {json.dumps(data)[:300]}") from exc
        info = dict(data.get("colibri") or {})
        return Image(png=png, width=info.get("width"), height=info.get("height"), seed=info.get("seed"), info=info)

    def close(self) -> None:
        self._http.close()
