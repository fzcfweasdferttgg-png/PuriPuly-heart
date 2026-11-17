"""How many requests a model server really runs at once, so an app doesn't send N "parallel" requests
to a server with one slot -- where they'd queue and each would also wait out the others' turns.

- Anthropic, or an OpenAI-compatible API on a non-local host: hosted, no limit to respect (None).
- LM Studio: the loaded model's `parallel` (`lms load <model> --parallel N` or the load dialog).
- llama.cpp (or any local server answering llama.cpp's /props): its `total_slots`.
- colibri: its KV slots (`coli serve --kv-slots N`), from /health.
- Ollama and other local servers: 1 -- none reports it (OLLAMA_NUM_PARALLEL is server-side).

Parallel slots share one context, so when `window` (the tokens one request may grow to) is given the
answer is also capped at how many whole windows the loaded context holds. Observed on LM Studio
(-c 40960 --parallel 4): two 11.5k-token requests at once ran, four failed with "Context size has
been exceeded".
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

import httpx2

from llm_lib.servers.lmstudio import LMStudio, server_root
from llm_lib.settings import Settings

PROBE_TIMEOUT_S = 5.0


def is_local_host(host: str | None) -> bool:
    if not host or host == "localhost" or "." not in host:
        return True  # a bare machine name ("gpu-box") is on the LAN, not a hosted API
    if host.endswith((".local", ".lan", ".internal", ".home")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _fits(slots: int, context: int | None, window: int | None) -> tuple[int, str]:
    if not window or not isinstance(context, int) or context <= 0 or context // window >= slots:
        return slots, ""
    whole = max(1, context // window)
    return whole, (
        f"; its {context}-token context holds {whole} request(s) of {window} tokens -- load it with "
        f"a context of {window * slots} for {slots}"
    )


def llamacpp_slots(base_url: str, http: httpx2.Client | None = None) -> tuple[int, int | None] | None:
    """(total_slots, n_ctx) from llama.cpp's /props, or None when the server doesn't answer it."""
    getter = http.get if http is not None else httpx2.get
    try:
        resp = getter(f"{server_root(base_url)}/props", timeout=PROBE_TIMEOUT_S)
        props = resp.json() if resp.status_code == 200 else {}
        slots = props.get("total_slots")
        context = (props.get("default_generation_settings") or {}).get("n_ctx")
    except (httpx2.HTTPError, ValueError, AttributeError):
        return None
    if not isinstance(slots, int) or slots < 1:
        return None
    return slots, context if isinstance(context, int) else None


def colibri_slots(settings: Settings, http: httpx2.Client | None = None) -> tuple[int, str]:
    """colibri runs one generation per KV slot (`--kv-slots`); /health says how many when authed."""
    headers = {"Authorization": f"Bearer {key}"} if (key := settings.resolved_api_key()) else {}
    getter = http.get if http is not None else httpx2.get
    try:
        resp = getter(
            f"{server_root(settings.resolved_base_url() or '')}/health", headers=headers, timeout=PROBE_TIMEOUT_S
        )
        health = resp.json() if resp.status_code == 200 else {}
    except (httpx2.HTTPError, ValueError):
        return 1, "colibri's /health didn't answer"
    slots = (health.get("scheduler") or {}).get("capacity") or health.get("kv_slots")
    if not isinstance(slots, int) or slots < 1:
        return 1, "colibri's /health didn't report its slots (is the API key right?)"
    return slots, f"colibri serves {slots} KV slot(s)"


def server_parallel(
    settings: Settings,
    *,
    window: int | None = None,
    lmstudio: LMStudio | None = None,
    http: httpx2.Client | None = None,
) -> tuple[int | None, str]:
    """(how many requests the server runs at once -- None: no limit to respect, a hosted API; why)."""
    if settings.kind == "anthropic":
        return None, "the Anthropic API serves concurrent requests"
    if settings.kind == "mock":
        return None, "the mock provider has no limit"
    url = settings.resolved_base_url() or ""
    if settings.provider == "lmstudio":
        lms = lmstudio or LMStudio(url, api_key=settings.resolved_api_key(), http=http)
        match = next((i for i in lms.loaded_instances() if i.get("key") == settings.model), None)
        if match is not None:
            parallel, context = match.get("parallel") or 1, match.get("context_length")
        else:
            try:
                loaded = lms.ps() if lms.cli_available() else []
            except Exception as exc:
                return 1, f"`lms ps` failed ({exc})"
            cli = next((m for m in loaded if settings.model in (m.get("identifier"), m.get("modelKey"))), None)
            if cli is None:
                return 1, f"{settings.model} isn't loaded in LM Studio"
            parallel, context = cli.get("parallel") or 1, cli.get("contextLength")
        workers, short = _fits(int(parallel), context, window)
        return workers, f"LM Studio serves {settings.model} with parallel={parallel}{short}"
    if settings.provider == "colibri":
        return colibri_slots(settings, http)
    if settings.kind == "ollama":
        return 1, "Ollama doesn't report how many requests it serves at once"
    host = urlparse(url).hostname
    if not settings.preset.local and settings.provider != "openai-compatible":
        return None, f"{settings.label} is a hosted API"
    if url and is_local_host(host) and (probe := llamacpp_slots(url, http)):
        slots, context = probe
        workers, short = _fits(slots, context, window)
        return workers, f"the server reports {slots} slot(s){short}"
    if settings.provider == "llamacpp":
        return 1, "llama.cpp's /props didn't answer with total_slots"
    if not is_local_host(host):
        return None, f"{host} is a hosted API"
    return 1, f"the local server at {url} doesn't report how many requests it serves at once"


def effective_parallel(
    wanted: int, settings: Settings, *, window: int | None = None, **kwargs: object
) -> tuple[int, str]:
    """min(wanted, what the server runs at once), and why."""
    if wanted <= 1:
        return 1, "parallel requests are off"
    slots, why = server_parallel(settings, window=window, **kwargs)  # type: ignore[arg-type]
    return (wanted if slots is None else min(wanted, slots)), why
