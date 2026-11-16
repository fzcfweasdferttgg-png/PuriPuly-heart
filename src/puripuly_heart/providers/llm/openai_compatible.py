from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx2
import openai

from puripuly_heart.core.llm.latency import current_attempt
from puripuly_heart.domain.models import Translation
from puripuly_heart.providers.llm.error_details import LLMResponseError
from puripuly_heart.providers.llm.messages import (
    build_translation_user_message,
    format_translation_system_prompt,
)


def _content_text(content: object) -> str:
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        if parts:
            return "\n".join(parts)
    raise LLMResponseError(
        "OpenAI-compatible response contained no text", provider="openai_compatible"
    )


@dataclass(slots=True)
class OpenAICompatibleLLMProvider:
    base_url: str
    model: str
    api_key: str = ""
    max_tokens: int | None = None
    timeout: float = 30.0
    client: Any | None = None
    _owned_client: Any | None = field(init=False, default=None, repr=False)
    _client_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)
    _active_tasks: set[asyncio.Task[object]] = field(init=False, default_factory=set, repr=False)
    _external_client_closed: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.strip().rstrip("/")
        self.model = self.model.strip()
        self.api_key = self.api_key.strip()
        if not self.base_url or not self.model:
            raise ValueError("OpenAI-compatible provider needs an address and a model")

    def _client_for_call(self) -> Any:
        if self.client is not None:
            return self.client
        if self._owned_client is None:
            http_client = httpx2.AsyncClient(
                timeout=self.timeout,
                trust_env=False,
                follow_redirects=False,
            )
            self._owned_client = openai.AsyncOpenAI(
                api_key=self.api_key or "not-required",
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
                observation.transport = "openai_sdk"
                observation.mark_sent()
            request: dict[str, object] = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": format_translation_system_prompt(
                            system_prompt=system_prompt,
                            source_language=source_language,
                            target_language=target_language,
                        ),
                    },
                    {
                        "role": "user",
                        "content": build_translation_user_message(
                            text=text,
                            context=context,
                            scene_participant_count=scene_participant_count,
                        ),
                    },
                ],
                "stream": False,
            }
            effective_max_tokens = max_output_tokens or self.max_tokens
            if effective_max_tokens is not None:
                request["max_tokens"] = effective_max_tokens
            response = await client.chat.completions.create(**request)
        except asyncio.CancelledError:
            raise
        except openai.APIError as exc:
            raise LLMResponseError(
                "OpenAI-compatible request failed",
                provider="openai_compatible",
                status_code=getattr(exc, "status_code", None),
            ) from exc
        finally:
            if operation is not None:
                self._active_tasks.discard(operation)

        if observation is not None:
            observation.record_openai_response(response.model_dump())
        if not response.choices:
            raise LLMResponseError(
                "OpenAI-compatible response did not contain choices",
                provider="openai_compatible",
            )
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMResponseError(
                "OpenAI-compatible response was truncated by max_tokens limit",
                provider="openai_compatible",
            )
        result = _content_text(choice.message.content)
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


__all__ = ["OpenAICompatibleLLMProvider"]
