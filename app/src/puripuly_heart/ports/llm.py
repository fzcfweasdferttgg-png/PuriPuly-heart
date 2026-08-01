from __future__ import annotations

from typing import Protocol
from uuid import UUID

from puripuly_heart.domain.models import Translation


class LLMProvider(Protocol):
    async def translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation: ...

    async def close(self) -> None: ...
