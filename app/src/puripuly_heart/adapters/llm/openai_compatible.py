from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from uuid import UUID

import openai
from openai import AsyncOpenAI

from puripuly_heart.ports.logging import SessionLogger
from puripuly_heart.domain.models import Translation
from puripuly_heart.adapters.llm.messages import build_translation_user_message

logger = logging.getLogger(__name__)


def _log_basic_request(
    *,
    runtime_logging: SessionLogger | None,
    operation: str,
    text: str,
    source_language: str,
    target_language: str,
    context: str,
) -> None:
    message = "[Basic][LLM] OpenAI-compatible request [%s][context=%s] %s -> %s: %r" % (
        operation,
        "yes" if context else "no",
        source_language,
        target_language,
        text,
    )
    if runtime_logging is not None:
        runtime_logging.emit_basic(message)
        return
    logger.info(message)


def _log_basic_response(
    *, runtime_logging: SessionLogger | None, operation: str, text: str
) -> None:
    message = "[Basic][LLM] OpenAI-compatible response [%s]: %r" % (operation, text)
    if runtime_logging is not None:
        runtime_logging.emit_basic(message)
        return
    logger.info(message)


def _log_basic_request_failure(
    *,
    runtime_logging: SessionLogger | None,
    operation: str,
    message: str,
) -> None:
    rendered = "[Basic][LLM] OpenAI-compatible request failed [%s]: %s" % (
        operation,
        message,
    )
    if runtime_logging is not None:
        runtime_logging.emit_basic(rendered, level=logging.ERROR)
        return
    logger.error(rendered)


def _build_system_prompt(
    *,
    system_prompt: str,
    source_language: str,
    target_language: str,
) -> str:
    return (
        system_prompt.format(
            source_language=source_language,
            target_language=target_language,
        )
        if "{source_language}" in system_prompt
        else system_prompt
    )


def _build_user_message(*, text: str, context: str) -> str:
    return build_translation_user_message(text=text, context=context)


@dataclass
class ProviderVerificationResult:
    endpoint_reachable: bool = False
    api_key_valid: bool = False
    model_exists: bool = False
    error_message: str = ""


@dataclass(slots=True)
class OpenAICompatibleLLMProvider:
    api_key: str
    base_url: str
    model: str
    timeout: float = 30.0
    runtime_logging: SessionLogger | None = None
    _client: AsyncOpenAI | None = field(init=False, default=None, repr=False)
    _client_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)

    async def _get_client(self) -> AsyncOpenAI:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                self._client = AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    timeout=self.timeout,
                )
            return self._client

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
        if not self.api_key:
            raise RuntimeError("OpenAI-compatible API key is empty")

        _log_basic_request(
            runtime_logging=self.runtime_logging,
            operation="translate",
            text=text,
            source_language=source_language,
            target_language=target_language,
            context=context,
        )

        system_content = _build_system_prompt(
            system_prompt=system_prompt,
            source_language=source_language,
            target_language=target_language,
        )
        user_message = _build_user_message(text=text, context=context)

        try:
            client = await self._get_client()
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_message},
                ],
                timeout=self.timeout,
            )

            if not response.choices:
                raise RuntimeError("OpenAI-compatible response did not contain choices")

            choice = response.choices[0]
            if choice.finish_reason == "length":
                logger.warning(
                    "[Basic][LLM] OpenAI-compatible response truncated by max_tokens (finish_reason=length)"
                )

            result = choice.message.content
            if not result or not result.strip():
                raise RuntimeError("OpenAI-compatible response contained empty message content")

            result = result.strip()
            _log_basic_response(
                runtime_logging=self.runtime_logging,
                operation="translate",
                text=result,
            )
            return Translation(utterance_id=utterance_id, text=result, origin_wall_clock_ms=int(time.time() * 1000))

        except openai.AuthenticationError as exc:
            _log_basic_request_failure(
                runtime_logging=self.runtime_logging,
                operation="translate",
                message=f"Authentication failed (401): {exc}",
            )
            raise RuntimeError(f"API key invalid: {exc}") from exc

        except openai.NotFoundError as exc:
            _log_basic_request_failure(
                runtime_logging=self.runtime_logging,
                operation="translate",
                message=f"Model not found (404): {exc}",
            )
            raise RuntimeError(
                f"Model '{self.model}' not found at {self.base_url}: {exc}"
            ) from exc

        except openai.RateLimitError as exc:
            _log_basic_request_failure(
                runtime_logging=self.runtime_logging,
                operation="translate",
                message=f"Rate limited (429): {exc}",
            )
            raise RuntimeError(f"Rate limited by provider: {exc}") from exc

        except openai.APIConnectionError as exc:
            _log_basic_request_failure(
                runtime_logging=self.runtime_logging,
                operation="translate",
                message=f"Connection failed: {exc}",
            )
            raise RuntimeError(
                f"Cannot connect to {self.base_url}: {exc}"
            ) from exc

        except Exception as exc:
            _log_basic_request_failure(
                runtime_logging=self.runtime_logging,
                operation="translate",
                message=str(exc),
            )
            raise

    async def close(self) -> None:
        async with self._client_lock:
            client = self._client
            self._client = None
        if client is not None:
            await client.close()
            logger.debug("[Basic][LLM] OpenAI-compatible client closed")

    async def verify_connection(self) -> ProviderVerificationResult:
        if not self.api_key:
            return ProviderVerificationResult(error_message="API key is empty")

        try:
            client = await self._get_client()
            models = await client.models.list()

            available_models = [m.id for m in models.data]
            if self.model not in available_models:
                return ProviderVerificationResult(
                    endpoint_reachable=True,
                    api_key_valid=True,
                    error_message=f"Model '{self.model}' not found. Available: {available_models[:5]}..."
                )

            return ProviderVerificationResult(
                endpoint_reachable=True,
                api_key_valid=True,
                model_exists=True,
            )

        except openai.AuthenticationError as exc:
            return ProviderVerificationResult(
                endpoint_reachable=True,
                error_message=f"API key invalid: {exc}",
            )

        except openai.APIConnectionError as exc:
            return ProviderVerificationResult(
                error_message=f"Cannot reach endpoint {self.base_url}: {exc}",
            )

        except openai.NotFoundError:
            # Some providers don't support /models endpoint.
            # Endpoint is reachable, but we can't verify the model.
            logger.info(
                "[Basic][LLM] Provider does not support /models endpoint, "
                "skipping model verification"
            )
            return ProviderVerificationResult(
                endpoint_reachable=True,
                api_key_valid=True,
                model_exists=False,
                error_message="Provider does not support /models listing; model not verified",
            )

        except openai.PermissionDeniedError as exc:
            return ProviderVerificationResult(
                endpoint_reachable=True,
                error_message=f"Permission denied (403): {exc}",
            )

        except Exception as exc:
            return ProviderVerificationResult(
                error_message=f"Verification failed: {exc}",
            )
