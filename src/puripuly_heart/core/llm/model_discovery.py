"""Discovery of model identifiers from OpenAI-compatible provider APIs."""

from __future__ import annotations

import logging

import httpx

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
