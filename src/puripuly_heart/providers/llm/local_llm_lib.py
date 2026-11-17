from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import UUID

from llm_lib import LLM, Completion, Event, Settings

from puripuly_heart.config.provider_values import (
    LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS,
    LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS,
)
from puripuly_heart.core.llm.latency import current_attempt
from puripuly_heart.domain.models import Translation
from puripuly_heart.providers.llm.error_details import LLMResponseError
from puripuly_heart.providers.llm.messages import build_translation_user_message

_SAMPLING_FIELDS = (
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "max_tokens",
    "frequency_penalty",
    "repetition_penalty",
)


def _assert_extra_body_is_safe(extra_body: Mapping[str, object]) -> None:
    for key, value in extra_body.items():
        if not isinstance(key, str):
            raise ValueError("local LLM extra_body keys must be strings")
        normalized = key.lower()
        if normalized in LOCAL_LLM_RESERVED_EXTRA_BODY_KEYS:
            raise ValueError(f"reserved local LLM extra_body key: {key}")
        if normalized in LOCAL_LLM_SENSITIVE_EXTRA_BODY_KEYS:
            raise ValueError(f"sensitive local LLM extra_body key: {key}")
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"local LLM extra_body value for {key!r} is not JSON serializable"
            ) from exc


def _settings_options(
    backend: str,
    extra_body: Mapping[str, object],
) -> dict[str, Any]:
    _assert_extra_body_is_safe(extra_body)
    extra = dict(extra_body)
    options: dict[str, Any] = {}
    for name in _SAMPLING_FIELDS:
        if name in extra:
            options[name] = extra.pop(name)
    if "reasoning_effort" in extra:
        options["effort"] = str(extra.pop("reasoning_effort"))
    if "num_ctx" in extra:
        options["context"] = extra.pop("num_ctx")
    elif "context" in extra:
        options["context"] = extra.pop("context")
    if backend == "ollama" and options.get("effort") == "none":
        options["no_think"] = True
    options["extra"] = extra
    return options


def _normalized_base_url(backend: str, base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if backend == "ollama" and normalized.endswith("/v1"):
        return normalized[:-3]
    return normalized


def _build_system_prompt(*, system_prompt: str, source_language: str, target_language: str) -> str:
    return (
        system_prompt.format(source_language=source_language, target_language=target_language)
        if "{source_language}" in system_prompt
        else system_prompt
    )


def _messages(
    *,
    text: str,
    system_prompt: str,
    source_language: str,
    target_language: str,
    context: str,
    scene_participant_count: int | None,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": _build_system_prompt(
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
    ]


@dataclass(slots=True)
class LLMLibLocalLLMProvider:
    backend: str
    base_url: str
    model: str
    api_key: str = ""
    extra_body: Mapping[str, object] = field(default_factory=dict)
    max_tokens: int | None = None
    timeout: float = 30.0
    client: Any | None = None
    _internal_client: LLM | None = field(init=False, default=None, repr=False)
    _external_client_closed: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self.backend = self.backend.strip()
        self.base_url = _normalized_base_url(self.backend, self.base_url)
        self.model = self.model.strip()
        self.api_key = self.api_key.strip()
        if not self.backend or not self.base_url or not self.model:
            raise ValueError("local LLM backend, base URL, and model are required")
        _assert_extra_body_is_safe(self.extra_body)

    def _settings(self) -> Settings:
        options = _settings_options(self.backend, self.extra_body)
        headers = (
            {"Authorization": f"Bearer {self.api_key}"}
            if self.backend == "ollama" and self.api_key
            else {}
        )
        return Settings(
            provider=self.backend,
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key or None,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
            headers=headers,
            **options,
        )

    def _client_for_call(self) -> Any:
        if self.client is not None:
            return self.client
        if self._internal_client is None:
            self._internal_client = LLM(self._settings())
        return self._internal_client

    @staticmethod
    def _record_completion(observation: Any, completion: Completion) -> None:
        if observation is None:
            return
        if completion.usage is not None:
            observation.record_usage(
                input_tokens=completion.usage.prompt_tokens,
                output_tokens=completion.usage.completion_tokens,
            )

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
        messages = _messages(
            text=text,
            system_prompt=system_prompt,
            source_language=source_language,
            target_language=target_language,
            context=context,
            scene_participant_count=scene_participant_count,
        )
        observation = current_attempt()
        if observation is not None:
            observation.transport = "llm_lib"
            observation.mark_sent()

        def on_event(event: Event) -> None:
            if observation is not None and event.type == "content" and event.text:
                observation.mark_first_text()

        params: dict[str, object] = {"on_event": on_event}
        if max_output_tokens is not None:
            params["max_tokens"] = max_output_tokens
        completion = await client.achat(messages, **params)
        if observation is not None:
            observation.actual_model = str(getattr(client, "model", self.model))
        self._record_completion(observation, completion)
        if completion.truncated:
            raise LLMResponseError(
                "Local LLM response was truncated by max_tokens limit",
                provider="local_llm",
            )
        result = completion.text.strip()
        if not result:
            raise LLMResponseError(
                "Local LLM response contained empty message content",
                provider="local_llm",
            )
        return Translation(
            utterance_id=utterance_id,
            text=result,
            source_text=text,
            source_language=source_language,
            target_language=target_language,
        )

    async def close(self) -> None:
        internal = self._internal_client
        self._internal_client = None
        if internal is not None:
            await internal.aclose()
        if self.client is not None and not self._external_client_closed:
            self._external_client_closed = True
            await self.client.aclose()


__all__ = ["LLMLibLocalLLMProvider"]
