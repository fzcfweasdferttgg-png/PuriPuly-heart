from __future__ import annotations

from typing import Protocol


class ModelDiscovery(Protocol):
    async def fetch_models(self, base_url: str, api_key: str = "") -> list[str]: ...
    async def test_connection(self, base_url: str, api_key: str = "") -> tuple[int, str]: ...
