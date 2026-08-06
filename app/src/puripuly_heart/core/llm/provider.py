"""LLM provider with concurrency limiter.

SemaphoreLLMProvider wraps any LLMProvider and gates translate() calls
through an asyncio.Semaphore — limits concurrent LLM requests to avoid
API rate limits or local GPU memory exhaustion.

Re-exports LLMProvider Protocol from ports.llm.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from puripuly_heart.domain.models import Translation
from puripuly_heart.ports.llm import LLMProvider

__all__ = ["LLMProvider", "SemaphoreLLMProvider"]


@dataclass(slots=True)
class SemaphoreLLMProvider:
    inner: LLMProvider
    semaphore: asyncio.Semaphore

    async def translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
    ) -> Translation:
        async with self.semaphore:
            return await self.inner.translate(
                utterance_id=utterance_id,
                text=text,
                system_prompt=system_prompt,
                source_language=source_language,
                target_language=target_language,
                context=context,
            )

    async def close(self) -> None:
        await self.inner.close()
