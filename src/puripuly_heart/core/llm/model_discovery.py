"""Discovery of model identifiers from provider APIs."""

from __future__ import annotations

import asyncio
import logging

import httpx
from llm_lib import LLM, Settings

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT_S = 10.0
_TEST_TIMEOUT_S = 5.0


def _models_url(base_url: str) -> str:
    return base_url.strip().rstrip("/") + "/models"


def _auth_headers(api_key: str) -> dict[str, str]:
    key = api_key.strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


async def fetch_models(base_url: str, api_key: str = "") -> list[str]:
    """Return the sorted model ids the endpoint exposes via GET /models."""
    url = _models_url(base_url)
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S) as client:
            resp = await client.get(url, headers=_auth_headers(api_key))
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("[ModelDiscovery] Failed to fetch models from %s: %s", base_url, exc)
        return []
    if not isinstance(data, dict):
        return []
    entries = data.get("data")
    if not isinstance(entries, list):
        return []
    return sorted({str(m.get("id")) for m in entries if isinstance(m, dict) and m.get("id")})


async def test_connection(base_url: str, api_key: str = "") -> tuple[int, str]:
    """Ping GET /models and return (status_code, short body or error text)."""
    url = _models_url(base_url)
    try:
        async with httpx.AsyncClient(timeout=_TEST_TIMEOUT_S) as client:
            resp = await client.get(url, headers=_auth_headers(api_key))
            return resp.status_code, resp.text[:200]
    except Exception as exc:
        return 0, str(exc)


def _anthropic_client(base_url: str, api_key: str):
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(
        api_key=api_key.strip() or "not-configured",
        base_url=base_url.strip() or "https://api.anthropic.com",
        max_retries=0,
        timeout=_FETCH_TIMEOUT_S,
    )


async def fetch_anthropic_models(base_url: str, api_key: str = "") -> list[str]:
    client = _anthropic_client(base_url, api_key)
    try:
        model_ids: set[str] = set()
        async for model in client.models.list(limit=1000):
            model_id = getattr(model, "id", None)
            if model_id and getattr(model, "lifecycle", "active") != "retired":
                model_ids.add(str(model_id))
        return sorted(model_ids)
    except Exception as exc:
        logger.warning(
            "[ModelDiscovery] Failed to fetch Anthropic models from %s: %s", base_url, exc
        )
        return []
    finally:
        await client.close()


async def test_anthropic_connection(base_url: str, api_key: str = "") -> tuple[int, str]:
    client = _anthropic_client(base_url, api_key)
    try:
        async for _ in client.models.list(limit=1):
            break
        return 200, "ok"
    except Exception as exc:
        return getattr(exc, "status_code", 0) or 0, str(exc)[:200]
    finally:
        await client.close()


def _llm_lib_base_url(backend: str, base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if backend == "ollama" and normalized.endswith("/v1"):
        return normalized[:-3]
    return normalized


def _llm_lib_discovery_client(backend: str, base_url: str, api_key: str) -> LLM:
    key = api_key.strip()
    headers = {"Authorization": f"Bearer {key}"} if backend == "ollama" and key else {}
    return LLM(
        Settings(
            provider=backend,
            base_url=_llm_lib_base_url(backend, base_url),
            api_key=key or None,
            timeout=15.0,
            headers=headers,
        )
    )


async def fetch_llm_lib_models(
    backend: str,
    base_url: str,
    api_key: str = "",
) -> list[str]:
    client = _llm_lib_discovery_client(backend, base_url, api_key)
    try:
        return await asyncio.to_thread(client.list_models)
    except Exception as exc:
        logger.warning("[ModelDiscovery] LLM-lib failed to fetch models from %s: %s", base_url, exc)
        return []
    finally:
        await client.aclose()


async def test_llm_lib_connection(
    backend: str,
    base_url: str,
    api_key: str = "",
) -> tuple[int, str]:
    client = _llm_lib_discovery_client(backend, base_url, api_key)
    try:
        await asyncio.to_thread(client.list_models)
        return 200, "ok"
    except Exception as exc:
        return getattr(exc, "status_code", 0) or 0, str(exc)[:200]
    finally:
        await client.aclose()
