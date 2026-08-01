from __future__ import annotations

from uuid import UUID

from puripuly_heart.domain.models import Translation


class LLMProvider:
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
        _ = utterance_id, text, system_prompt, source_language, target_language, context
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError
