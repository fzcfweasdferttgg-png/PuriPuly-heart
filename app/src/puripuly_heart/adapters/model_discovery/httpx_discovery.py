from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class HttpxModelDiscovery:
    async def fetch_models(self, base_url: str, api_key: str = "") -> list[str]:
        url = base_url.rstrip("/") + "/models"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return sorted(
                m.get("id", "") for m in data.get("data", []) if m.get("id")
            )

    async def test_connection(self, base_url: str, api_key: str = "") -> tuple[int, str]:
        url = base_url.rstrip("/") + "/models"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url, headers=headers)
                return resp.status_code, resp.text[:200]
        except Exception as exc:
            return 0, str(exc)
