from __future__ import annotations

import asyncio
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
from puripuly_heart.core.llm.llm_lib_settings import (
    ExplicitLLMLibSettings,
    llm_lib_backend_capabilities,
    normalized_llm_lib_base_url,
)
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
    unsupported = llm_lib_backend_capabilities(backend).unsupported_extra_body_keys
    extra = {key: value for key, value in extra_body.items() if key.lower() not in unsupported}
    options: dict[str, Any] = {}
    for name in _SAMPLING_FIELDS:
        if name in extra:
            options[name] = extra.pop(name)
    extra.pop("seed", None)
    extra.pop("stop", None)
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


def _request_options(extra_body: Mapping[str, object]) -> dict[str, object]:
    options: dict[str, object] = {}
    if "seed" in extra_body:
        options["seed"] = extra_body["seed"]
    if "stop" in extra_body:
        options["stop"] = extra_body["stop"]
    return options


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
    _client_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)
    _active_tasks: set[asyncio.Task[object]] = field(init=False, default_factory=set, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self.backend = self.backend.strip()
        self.base_url = normalized_llm_lib_base_url(self.backend, self.base_url)
        self.model = self.model.strip()
        self.api_key = self.api_key.strip()
        if not self.backend or not self.base_url:
            raise ValueError("local LLM backend and base URL are required")
        if llm_lib_backend_capabilities(self.backend).model_required and not self.model:
            raise ValueError(f"model is required for {self.backend}")
        _assert_extra_body_is_safe(self.extra_body)

    def _settings(self) -> Settings:
        options = _settings_options(self.backend, self.extra_body)
        headers = (
            {"Authorization": f"Bearer {self.api_key}"}
            if self.backend == "ollama" and self.api_key
            else {}
        )
        return ExplicitLLMLibSettings(
            provider=self.backend,
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
            headers=headers,
            **options,
        )

    async def _client_for_call(self, operation: asyncio.Task[object]) -> Any:
        async with self._client_lock:
            if self._closed:
                raise asyncio.CancelledError
            if self.client is not None:
                client = self.client
            else:
                if self._internal_client is None:
                    self._internal_client = LLM(self._settings())
                client = self._internal_client
            self._active_tasks.add(operation)
            return client

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
        operation = asyncio.current_task()
        assert operation is not None
        client = await self._client_for_call(operation)
        try:
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

            params: dict[str, object] = {
                "on_event": on_event,
                **_request_options(self.extra_body),
            }
            if max_output_tokens is not None:
                params["max_tokens"] = max_output_tokens
            try:
                completion = await client.achat(messages, **params)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise LLMResponseError(
                    "Local LLM request failed",
                    provider="local_llm",
                    diagnostic_category="unknown",
                ) from exc
        finally:
            self._active_tasks.discard(operation)
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
        current = asyncio.current_task()
        async with self._client_lock:
            self._closed = True
            internal = self._internal_client
            self._internal_client = None
            operations = tuple(
                operation for operation in self._active_tasks if operation is not current
            )
        for operation in operations:
            operation.cancel()
        if operations:
            await asyncio.gather(*operations, return_exceptions=True)
        if internal is not None:
            await internal.aclose()
        if self.client is not None and not self._external_client_closed:
            self._external_client_closed = True
            await self.client.aclose()


__all__ = ["LLMLibLocalLLMProvider"]
