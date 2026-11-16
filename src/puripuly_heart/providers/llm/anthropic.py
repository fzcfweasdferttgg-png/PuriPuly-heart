from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import anthropic
import httpx2

from puripuly_heart.core.llm.latency import current_attempt
from puripuly_heart.domain.models import Translation
from puripuly_heart.providers.llm.error_details import LLMResponseError
from puripuly_heart.providers.llm.messages import (
    build_translation_user_message,
    format_translation_system_prompt,
)

ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS = 2048


def _content_text(content: object) -> str:
    if not isinstance(content, (list, tuple)):
        raise LLMResponseError(
            "Anthropic response contained no content blocks", provider="anthropic"
        )
    parts: list[str] = []
    for block in content:
        block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if block_type == "text" and isinstance(text, str) and text.strip():
            parts.append(text.strip())
    if not parts:
        raise LLMResponseError("Anthropic response contained no text", provider="anthropic")
    return "\n".join(parts)


@dataclass(slots=True)
class AnthropicLLMProvider:
    model: str
    base_url: str = ANTHROPIC_DEFAULT_BASE_URL
    api_key: str = ""
    max_tokens: int | None = None
    timeout: float = 30.0
    client: Any | None = None
    _owned_client: Any | None = field(init=False, default=None, repr=False)
    _client_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)
    _active_tasks: set[asyncio.Task[object]] = field(init=False, default_factory=set, repr=False)
    _external_client_closed: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.strip().rstrip("/") or ANTHROPIC_DEFAULT_BASE_URL
        self.model = self.model.strip()
        self.api_key = self.api_key.strip()
        if not self.model:
            raise ValueError("Anthropic provider needs a model")

    def _client_for_call(self) -> Any:
        if self.client is not None:
            return self.client
        if self._owned_client is None:
            http_client = httpx2.AsyncClient(
                timeout=self.timeout,
                trust_env=False,
                follow_redirects=False,
            )
            self._owned_client = anthropic.AsyncAnthropic(
                api_key=self.api_key or "not-configured",
                base_url=self.base_url,
                max_retries=0,
                http_client=http_client,
            )
        return self._owned_client

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
        client = self._client_for_call()
        operation = asyncio.current_task()
        if operation is not None:
            async with self._client_lock:
                self._active_tasks.add(operation)
        observation = current_attempt()
        try:
            if observation is not None:
                observation.transport = "anthropic_sdk"
                observation.mark_sent()
            response = await client.messages.create(
                model=self.model,
                max_tokens=(
                    max_output_tokens or self.max_tokens or ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS
                ),
                system=format_translation_system_prompt(
                    system_prompt=system_prompt,
                    source_language=source_language,
                    target_language=target_language,
                ),
                messages=[
                    {
                        "role": "user",
                        "content": build_translation_user_message(
                            text=text,
                            context=context,
                            scene_participant_count=scene_participant_count,
                        ),
                    }
                ],
                stream=False,
            )
        except asyncio.CancelledError:
            raise
        except anthropic.APIError as exc:
            raise LLMResponseError(
                "Anthropic request failed",
                provider="anthropic",
                status_code=getattr(exc, "status_code", None),
            ) from exc
        finally:
            if operation is not None:
                self._active_tasks.discard(operation)

        if observation is not None:
            observation.record_openai_response(response.model_dump())
        if response.stop_reason == "max_tokens":
            raise LLMResponseError(
                "Anthropic response was truncated by max_tokens limit",
                provider="anthropic",
            )
        result = _content_text(response.content)
        return Translation(
            utterance_id=utterance_id,
            text=result,
            source_text=text,
            source_language=source_language,
            target_language=target_language,
        )

    async def close(self) -> None:
        current = asyncio.current_task()
        async with self._client_lock:
            operations = tuple(task for task in self._active_tasks if task is not current)
            self._active_tasks.clear()
        for operation in operations:
            operation.cancel()
        if operations:
            await asyncio.gather(*operations, return_exceptions=True)
        owned = self._owned_client
        self._owned_client = None
        if owned is not None:
            await owned.close()
        if self.client is not None and not self._external_client_closed:
            self._external_client_closed = True
            await self.client.close()


__all__ = [
    "ANTHROPIC_DEFAULT_BASE_URL",
    "ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS",
    "AnthropicLLMProvider",
]
