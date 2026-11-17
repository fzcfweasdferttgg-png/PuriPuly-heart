"""Which server, which model, and how to talk to it -- from code or from the environment.

Every provider name maps onto one of four wire protocols (`kind`):

    openai     /v1/chat/completions -- local servers (LM Studio, llama.cpp, vLLM, SGLang, colibri,
               mlx-lm, KoboldCpp, text-generation-webui, TabbyAPI, LocalAI, Jan) and hosted APIs
               (OpenAI, OpenRouter, Groq, Together, DeepSeek, Mistral, Gemini, xAI, Fireworks,
               Cerebras, NVIDIA, Hugging Face)
    ollama     Ollama's native /api/chat (the only API that takes num_ctx -- without it Ollama
               silently truncates prompts to its small default window)
    anthropic  Claude's Messages API, through the `anthropic` SDK (pip install llm-lib[anthropic])
    mock       a canned local reply, for tests and UI work with no server
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_S = 600.0  # longest wait for the next piece of a reply
COLIBRI_TIMEOUT_S = 3 * 24 * 3600.0  # colibri runs GLM-5.x on the CPU: a story batch takes hours
DEFAULT_RETRY_WAITS = (2.0, 5.0, 10.0)
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"
TEMPLATE_KWARGS = "chat_template_kwargs"
PENALTIES = frozenset({"frequency_penalty", "presence_penalty", "repeat_penalty", "repetition_penalty"})


@dataclass(frozen=True, slots=True)
class Preset:
    kind: str
    label: str
    base_url: str | None = None
    api_key: str | None = None  # a placeholder for local servers that don't check it
    url_env: tuple[str, ...] = ()  # env vars that override base_url, first set wins
    key_env: tuple[str, ...] = ()
    key_required: bool = False
    # Whether the server enforces response_format=json_schema (others get the schema in the prompt only)
    structured_output: bool = False
    timeout: float = DEFAULT_TIMEOUT_S
    retry_waits: tuple[float, ...] = DEFAULT_RETRY_WAITS  # resends after a 5xx
    rate_limit_retries: int = 3  # resends after a 429, each after its Retry-After
    drop_params: frozenset[str] = frozenset()  # body fields the server refuses with a 400
    # How Settings.thinking reaches the server: "enable_thinking" (colibri's extension),
    # "chat_template_kwargs" (vLLM, SGLang, llama.cpp: the template's own enable_thinking), or None
    thinking_param: str | None = None
    local: bool = False


def _local(label: str, port: int, key: str, env: str, **kw: Any) -> Preset:
    return Preset("openai", label, f"http://127.0.0.1:{port}/v1", key, url_env=(env,), local=True, **kw)


def _hosted(label: str, url: str, *key_env: str, **kw: Any) -> Preset:
    return Preset("openai", label, url, key_env=key_env, key_required=True, **kw)


PRESETS: dict[str, Preset] = {
    # ---- local servers
    "lmstudio": Preset(
        "openai",
        "LM Studio",
        "http://127.0.0.1:1234/v1",
        "lm-studio",
        url_env=("LMSTUDIO_BASE_URL", "LMSTUDIO_URL"),
        structured_output=True,
        local=True,
    ),
    "llamacpp": _local(
        "llama.cpp",
        8080,
        "llamacpp",
        "LLAMACPP_BASE_URL",
        structured_output=True,
        thinking_param=TEMPLATE_KWARGS,
    ),
    "vllm": _local("vLLM", 8000, "vllm", "VLLM_BASE_URL", structured_output=True, thinking_param=TEMPLATE_KWARGS),
    "sglang": _local(
        "SGLang", 30000, "sglang", "SGLANG_BASE_URL", structured_output=True, thinking_param=TEMPLATE_KWARGS
    ),
    "colibri": Preset(
        "openai",
        "colibri",
        "http://127.0.0.1:8000/v1",
        "colibri",  # only checked when the server runs with COLI_API_KEY
        url_env=("COLIBRI_BASE_URL", "COLI_BASE_URL"),
        key_env=("COLI_API_KEY", "COLIBRI_API_KEY"),
        structured_output=True,
        timeout=COLIBRI_TIMEOUT_S,
        # A 5xx may come hours into a generation: resending would start it over. A 429 (its queue is
        # full, or the wait for a slot timed out) comes before generation starts, so it's safe to resend.
        retry_waits=(),
        rate_limit_retries=10,
        drop_params=PENALTIES,  # "Token penalties are not supported yet." -- HTTP 400
        thinking_param="enable_thinking",
        local=True,
    ),
    "mlx": _local("mlx-lm", 8080, "mlx", "MLX_BASE_URL"),
    "koboldcpp": _local("KoboldCpp", 5001, "koboldcpp", "KOBOLDCPP_BASE_URL"),
    "textgen": _local("text-generation-webui", 5000, "textgen", "TEXTGEN_BASE_URL"),
    "tabbyapi": _local("TabbyAPI", 5000, "tabby", "TABBY_BASE_URL", structured_output=True),
    "localai": _local("LocalAI", 8080, "localai", "LOCALAI_BASE_URL"),
    "jan": _local("Jan", 1337, "jan", "JAN_BASE_URL"),
    "openai-compatible": Preset("openai", "OpenAI-compatible", None, "not-needed", key_env=("OPENAI_API_KEY",)),
    "ollama": Preset("ollama", "Ollama", "http://127.0.0.1:11434", url_env=("OLLAMA_HOST", "OLLAMA_URL"), local=True),
    # ---- hosted APIs (default URLs; each can be overridden with base_url or LLM_BASE_URL)
    "openai": _hosted("OpenAI", "https://api.openai.com/v1", "OPENAI_API_KEY", structured_output=True),
    "openrouter": _hosted("OpenRouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", structured_output=True),
    "groq": _hosted("Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", structured_output=True),
    "together": _hosted("Together", "https://api.together.xyz/v1", "TOGETHER_API_KEY", structured_output=True),
    "deepseek": _hosted("DeepSeek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),  # json_object only
    "mistral": _hosted("Mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY", structured_output=True),
    "gemini": _hosted(
        "Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        structured_output=True,
    ),
    "xai": _hosted("xAI", "https://api.x.ai/v1", "XAI_API_KEY", structured_output=True),
    "fireworks": _hosted(
        "Fireworks", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY", structured_output=True
    ),
    "cerebras": _hosted("Cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", structured_output=True),
    "nvidia": _hosted("NVIDIA", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY"),
    "huggingface": _hosted("Hugging Face", "https://router.huggingface.co/v1", "HF_TOKEN", "HUGGINGFACE_API_KEY"),
    "anthropic": Preset(
        "anthropic", "Anthropic", None, url_env=("ANTHROPIC_BASE_URL",), key_env=("ANTHROPIC_API_KEY",)
    ),
    "mock": Preset("mock", "mock", retry_waits=(), rate_limit_retries=0, local=True),
}

ALIASES = {
    "": "openai-compatible",
    "custom": "openai-compatible",
    "openai_compatible": "openai-compatible",
    "lm-studio": "lmstudio",
    "lm studio": "lmstudio",
    "lm_studio": "lmstudio",
    "llama.cpp": "llamacpp",
    "llama-cpp": "llamacpp",
    "llama_cpp": "llamacpp",
    "coli": "colibri",
    "mlx-lm": "mlx",
    "mlx_lm": "mlx",
    "kobold": "koboldcpp",
    "kobold.cpp": "koboldcpp",
    "text-generation-webui": "textgen",
    "oobabooga": "textgen",
    "tabby": "tabbyapi",
    "groqcloud": "groq",
    "together.ai": "together",
    "togetherai": "together",
    "google": "gemini",
    "x.ai": "xai",
    "grok": "xai",
    "nim": "nvidia",
    "hf": "huggingface",
    "claude": "anthropic",
}

PROVIDERS = tuple(PRESETS)


def normalize_provider(name: str | None) -> str:
    key = (name or "").strip().lower()
    key = ALIASES.get(key, key)
    if key not in PRESETS:
        raise ValueError(f"unknown LLM provider {name!r}; choose from {', '.join(PROVIDERS)}")
    return key


@dataclass(frozen=True, slots=True)
class Settings:
    provider: str = "openai-compatible"
    model: str = ""
    base_url: str | None = None  # None: the preset's (or its env var's)
    api_key: str | None = None
    # Defaults for every request; a Request's own values win
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    max_tokens: int | None = None  # longest reply, reasoning included; unset lets a lost model run on
    frequency_penalty: float | None = None
    repetition_penalty: float | None = None
    # Context window: Ollama's num_ctx; LM Studio's load context when load_context is unset
    context: int | None = None
    no_think: bool = False  # Qwen3's /no_think on the last user message; implies thinking=False
    # Reasoning on or off where the server has a switch for it (Ollama `think`, colibri
    # `enable_thinking`, vLLM/SGLang/llama.cpp chat_template_kwargs); None: the server's default
    thinking: bool | None = None
    strip_think: bool = True  # inline <think> blocks come out as reasoning, not content
    structured_output: bool | None = None  # None: the preset's
    timeout: float | None = None  # None: the preset's
    retry_waits: tuple[float, ...] | None = None  # pauses before resending after a 5xx; None: the preset's
    # LM Studio: load the model with this context (and `parallel` slots, `load_args` for `lms load`)
    # before the first request when it isn't loaded, instead of LM Studio's small JIT default
    load_context: int | None = None
    load_args: tuple[str, ...] = ()
    parallel: int = 1
    # Reasoning effort: OpenAI-style `reasoning_effort` (OpenAI, OpenRouter, Groq, xAI, colibri, ...),
    # Anthropic output_config.effort, Ollama `think` levels. "none" turns colibri's reasoning off.
    effort: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    extra: Mapping[str, Any] = field(default_factory=dict)  # merged into every request body

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", normalize_provider(self.provider))
        if self.provider == "anthropic" and not self.model:
            object.__setattr__(self, "model", DEFAULT_ANTHROPIC_MODEL)

    @property
    def preset(self) -> Preset:
        return PRESETS[self.provider]

    @property
    def kind(self) -> str:
        return self.preset.kind

    @property
    def label(self) -> str:
        return self.preset.label if self.provider != "openai-compatible" else (self.base_url or "OpenAI-compatible")

    def resolved_base_url(self, env: Mapping[str, str] | None = None) -> str | None:
        env = os.environ if env is None else env
        url = self.base_url or next((env[k] for k in self.preset.url_env if env.get(k)), None) or self.preset.base_url
        if not url:
            return None
        url = url.strip().rstrip("/")
        return url if "://" in url or url == "mock" else f"http://{url}"  # OLLAMA_HOST is often bare host:port

    def resolved_api_key(self, env: Mapping[str, str] | None = None) -> str | None:
        env = os.environ if env is None else env
        return self.api_key or next((env[k] for k in self.preset.key_env if env.get(k)), None) or self.preset.api_key

    @property
    def thinking_off(self) -> bool:
        return self.no_think or self.thinking is False

    @property
    def resolved_timeout(self) -> float:
        return self.timeout or self.preset.timeout

    @property
    def resolved_retry_waits(self) -> tuple[float, ...]:
        return self.preset.retry_waits if self.retry_waits is None else self.retry_waits

    @property
    def resolved_structured_output(self) -> bool:
        return self.preset.structured_output if self.structured_output is None else self.structured_output

    def with_(self, **changes: Any) -> Settings:
        return replace(self, **changes)

    @classmethod
    def from_env(
        cls,
        prefix: str | tuple[str, ...] | list[str] = (),
        *,
        env: Mapping[str, str] | None = None,
        env_file: str | os.PathLike[str] | None = None,
        **overrides: Any,
    ) -> Settings:
        """Settings from environment variables (see ENV_KEYS; each app's older names are read too).

        `prefix` gives per-role overrides, tried in order before the shared name:
        prefix=("ARCHITECT", "PLANNER") reads ARCHITECT_LLM_MODEL / ARCHITECT_MODEL, then
        PLANNER_..., then LLM_MODEL / MODEL. `env_file` (or LLM_ENV_FILE) is a .env file read
        underneath the process environment, which wins. `overrides` win over both.
        """
        base = dict(os.environ if env is None else env)
        path = env_file or base.get("LLM_ENV_FILE")
        merged = {**(read_env_file(path) if path else {}), **base}
        lookup = EnvLookup(merged, prefix)

        values: dict[str, Any] = {}
        for name, (keys, convert) in ENV_KEYS.items():
            raw = lookup.get(*keys)
            if raw is not None:
                try:
                    values[name] = convert(raw)
                except ValueError as exc:
                    raise ValueError(f"{lookup.found_as(*keys)}={raw!r}: {exc}") from exc
        values.update(overrides)
        settings = cls(**values)
        # Provider-specific URL/key variables honour the prefix chain too (PLANNER_ANTHROPIC_API_KEY)
        if settings.base_url is None and (url := lookup.get(*settings.preset.url_env)):
            settings = settings.with_(base_url=url)
        if settings.api_key is None and (key := lookup.get(*settings.preset.key_env)):
            settings = settings.with_(api_key=key)
        return settings


def _bool(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int_or_none(raw: str) -> int | None:
    return int(raw) if raw.strip() else None


def _float_or_none(raw: str) -> float | None:
    return float(raw) if raw.strip() else None


def _args(raw: str) -> tuple[str, ...]:
    return tuple(shlex.split(raw))


# Settings field -> (env var names, first found wins; converter). The first name is the canonical one;
# the rest are what Just-Finish-It (MODEL, OPENAI_URL, LLM_BACKEND, ...) and the STORY_* apps used.
ENV_KEYS: dict[str, tuple[tuple[str, ...], Any]] = {
    "provider": (("LLM_PROVIDER", "LLM_BACKEND", "STORY_PROVIDER"), str),
    "model": (("LLM_MODEL", "MODEL", "STORY_MODEL"), str),
    "base_url": (("LLM_BASE_URL", "OPENAI_URL", "OPENAI_BASE_URL", "STORY_BASE_URL"), str),
    "api_key": (("LLM_API_KEY",), str),
    "temperature": (("LLM_TEMPERATURE", "TEMPERATURE", "STORY_TEMPERATURE"), _float_or_none),
    "top_p": (("LLM_TOP_P",), _float_or_none),
    "top_k": (("LLM_TOP_K",), _int_or_none),
    "min_p": (("LLM_MIN_P",), _float_or_none),
    "max_tokens": (("LLM_MAX_TOKENS", "STORY_MAX_TOKENS", "ANTHROPIC_MAX_TOKENS"), _int_or_none),
    "frequency_penalty": (("LLM_FREQUENCY_PENALTY", "FREQUENCY_PENALTY"), _float_or_none),
    "repetition_penalty": (("LLM_REPETITION_PENALTY",), _float_or_none),
    "context": (("LLM_CONTEXT", "STORY_CONTEXT", "CONTEXT_SIZE"), _int_or_none),
    "no_think": (("LLM_NO_THINK", "STORY_NO_THINK"), _bool),
    "timeout": (("LLM_TIMEOUT", "LLM_REQUEST_TIMEOUT", "STORY_TIMEOUT"), _float_or_none),
    "load_context": (("LLM_LOAD_CONTEXT", "STORY_LOAD_CONTEXT"), _int_or_none),
    "load_args": (("LLM_LOAD_ARGS", "STORY_LOAD_ARGS"), _args),
    "parallel": (("LLM_PARALLEL", "STORY_PARALLEL"), lambda raw: max(1, int(raw or 1))),
    "thinking": (("LLM_THINKING",), lambda raw: _bool(raw) if raw.strip() else None),
    "effort": (("LLM_EFFORT", "LLM_REASONING_EFFORT", "REASONING_EFFORT"), lambda raw: raw.strip() or None),
}


class EnvLookup:
    """Reads `{prefix}_{KEY}` for each prefix in a chain, then the bare `KEY`; empty values don't count."""

    def __init__(self, env: Mapping[str, str], prefix: str | tuple[str, ...] | list[str] = ()) -> None:
        self.env = env
        self.prefixes = tuple(p for p in ((prefix,) if isinstance(prefix, str) else prefix) if p)

    def _names(self, *keys: str) -> list[str]:
        return [f"{p}_{k}" for p in self.prefixes for k in keys] + list(keys)

    def get(self, *keys: str) -> str | None:
        for name in self._names(*keys):
            value = self.env.get(name)
            if value is not None and value.strip():
                return value.strip()
        return None

    def found_as(self, *keys: str) -> str:
        return next((n for n in self._names(*keys) if (self.env.get(n) or "").strip()), keys[0])


def read_env_file(path: str | os.PathLike[str]) -> dict[str, str]:
    """KEY=value lines of a .env file (comments, blank lines and `export ` skipped; quotes removed).
    A missing file is empty."""
    out: dict[str, str] = {}
    try:
        lines = Path(path).expanduser().read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        out[key] = value
    return out
