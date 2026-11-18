"""Discovery of model identifiers from provider APIs."""

from __future__ import annotations

import logging

import httpx
from llm_lib import LLM
from llm_lib.errors import status_error

from puripuly_heart.core.llm.llm_lib_settings import (
    ExplicitLLMLibSettings,
    llm_lib_backend_capabilities,
    normalized_llm_lib_base_url,
)
from puripuly_heart.providers.llm.error_details import (
    record_probe_cleanup_failure,
    record_probe_failure,
)

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
    client = None
    try:
        client = httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S)
        resp = await client.get(url, headers=_auth_headers(api_key))
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            return []
        entries = data.get("data")
        if not isinstance(entries, list):
            return []
        return sorted({str(m.get("id")) for m in entries if isinstance(m, dict) and m.get("id")})
    except Exception as exc:
        logger.warning("[ModelDiscovery] Failed to fetch models: error_type=%s", type(exc).__name__)
        return []
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception as exc:
                logger.warning(
                    "[ModelDiscovery] Model fetch cleanup failed: error_type=%s",
                    type(exc).__name__,
                )


async def test_connection(base_url: str, api_key: str = "") -> tuple[int, str]:
    """Ping GET /models and return (status_code, short body or error text)."""
    url = _models_url(base_url)
    client = None
    primary_failure = None
    status = 0
    detail = ""
    try:
        client = httpx.AsyncClient(timeout=_TEST_TIMEOUT_S)
        resp = await client.get(url, headers=_auth_headers(api_key))
        status, detail = resp.status_code, resp.text[:200]
        if resp.status_code >= 400:
            primary_failure = record_probe_failure(
                None, provider="openai_compatible", status_code=resp.status_code
            )
    except Exception as exc:
        primary_failure = record_probe_failure(exc, provider="openai_compatible")
        status, detail = 0, str(exc)
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception as exc:
                record_probe_cleanup_failure(
                    exc, provider="openai_compatible", primary_failure=primary_failure
                )
    return status, detail


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
            "[ModelDiscovery] Failed to fetch Anthropic models: error_type=%s",
            type(exc).__name__,
        )
        return []
    finally:
        try:
            await client.close()
        except Exception as exc:
            logger.warning(
                "[ModelDiscovery] Anthropic model fetch cleanup failed: error_type=%s",
                type(exc).__name__,
            )


async def test_anthropic_connection(base_url: str, api_key: str = "") -> tuple[int, str]:
    client = _anthropic_client(base_url, api_key)
    primary_failure = None
    status = 0
    detail = ""
    try:
        async for _ in client.models.list(limit=1):
            break
        status, detail = 200, "ok"
    except Exception as exc:
        primary_failure = record_probe_failure(exc, provider="anthropic")
        status, detail = getattr(exc, "status_code", 0) or 0, str(exc)[:200]
    finally:
        try:
            await client.close()
        except Exception as exc:
            record_probe_cleanup_failure(exc, provider="anthropic", primary_failure=primary_failure)
    return status, detail


def _llm_lib_discovery_client(backend: str, base_url: str, api_key: str) -> LLM:
    key = api_key.strip()
    headers = {"Authorization": f"Bearer {key}"} if backend == "ollama" and key else {}
    return LLM(
        ExplicitLLMLibSettings(
            provider=backend,
            base_url=normalized_llm_lib_base_url(backend, base_url),
            api_key=key,
            timeout=15.0,
            headers=headers,
        )
    )


async def _list_llm_lib_models(client: LLM) -> list[str]:
    settings = client.settings
    base = settings.resolved_base_url()
    headers = dict(settings.headers)
    if key := settings.resolved_api_key():
        headers["Authorization"] = f"Bearer {key}"
    if llm_lib_backend_capabilities(settings.provider).discovery_api == "ollama":
        response = await client.async_http.get(f"{base}/api/tags", headers=headers)
        if response.status_code >= 400:
            raise status_error(client.label, response.status_code, response.text)
        data = response.json()
        return sorted(
            str(model["name"])
            for model in data.get("models", [])
            if isinstance(model, dict) and model.get("name")
        )
    response = await client.async_http.get(f"{base}/models", headers=headers)
    if response.status_code >= 400:
        raise status_error(client.label, response.status_code, response.text)
    data = response.json()
    items = data.get("data", data) if isinstance(data, dict) else data
    return sorted(str(model["id"]) for model in items if isinstance(model, dict) and "id" in model)


async def fetch_llm_lib_models(
    backend: str,
    base_url: str,
    api_key: str = "",
) -> list[str]:
    client = _llm_lib_discovery_client(backend, base_url, api_key)
    try:
        return await _list_llm_lib_models(client)
    except Exception as exc:
        logger.warning(
            "[ModelDiscovery] LLM-lib failed to fetch models: error_type=%s",
            type(exc).__name__,
        )
        return []
    finally:
        try:
            await client.aclose()
        except Exception as exc:
            logger.warning(
                "[ModelDiscovery] LLM-lib model fetch cleanup failed: error_type=%s",
                type(exc).__name__,
            )


async def test_llm_lib_connection(
    backend: str,
    base_url: str,
    api_key: str = "",
) -> tuple[int, str]:
    client = _llm_lib_discovery_client(backend, base_url, api_key)
    primary_failure = None
    status = 0
    detail = ""
    try:
        await _list_llm_lib_models(client)
        status, detail = 200, "ok"
    except Exception as exc:
        primary_failure = record_probe_failure(exc, provider="local_llm")
        status, detail = getattr(exc, "status_code", 0) or 0, str(exc)[:200]
    finally:
        try:
            await client.aclose()
        except Exception as exc:
            record_probe_cleanup_failure(exc, provider="local_llm", primary_failure=primary_failure)
    return status, detail
