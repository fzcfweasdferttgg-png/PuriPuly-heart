"""Sequential backup translation chain.

The primary provider serves every request. When it fails — or does not answer
within the wait threshold — the backup provider serves the request instead.
Both speech directions share the chain, so the backup covers the self and the
peer channel alike.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from puripuly_heart.core.llm.provider import LLMProvider

if TYPE_CHECKING:
    from puripuly_heart.domain.models import Translation

logger = logging.getLogger(__name__)

DEFAULT_PRIMARY_WAIT_S = 30.0


@dataclass(slots=True)
class SequentialFallbackLLMProvider(LLMProvider):
    primary: LLMProvider
    fallback: LLMProvider
    primary_wait_s: float = DEFAULT_PRIMARY_WAIT_S
    on_fallback_used: Callable[[], None] | None = None

    async def translate(
        self,
        *,
        utterance_id: UUID,
        text: str,
        system_prompt: str,
        source_language: str,
        target_language: str,
        context: str = "",
        scene_participant_count: int | None = None,
        max_output_tokens: int | None = None,
    ) -> Translation:
        params = {
            "utterance_id": utterance_id,
            "text": text,
            "system_prompt": system_prompt,
            "source_language": source_language,
            "target_language": target_language,
            "context": context,
            "scene_participant_count": scene_participant_count,
        }
        if max_output_tokens is not None:
            params["max_output_tokens"] = max_output_tokens
        try:
            return await asyncio.wait_for(
                self.primary.translate(**params),
                timeout=self.primary_wait_s,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[LLM] Primary provider failed (%s) \u2014 trying fallback",
                exc,
            )
        if self.on_fallback_used is not None:
            try:
                self.on_fallback_used()
            except Exception:
                logger.exception("[LLM] on_fallback_used callback failed")
        return await self.fallback.translate(**params)

    async def close(self) -> None:
        await self.primary.close()
        await self.fallback.close()
