"""API key verification service — tests connectivity to LLM providers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from puripuly_heart.ports.model_discovery import ModelDiscovery

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ApiKeyVerificationResult:
    success: bool
    error_message: str = ""


@dataclass
class ApiKeyVerifier:
    """Verifies API keys against provider endpoints.

    Extracted from GuiController.verify_api_key() to decouple
    verification logic from Flet event loop and UI state.
    """

    model_discovery: ModelDiscovery

    async def verify(
        self,
        provider: str,
        key: str,
        base_url: str | None = None,
        *,
        default_base_url_resolver: Callable[[str], str | None] | None = None,
    ) -> ApiKeyVerificationResult:
        """Verify API key connectivity.

        Args:
            provider: Provider name (e.g. "openai_compatible", "local_llm")
            key: API key string
            base_url: Override base URL. If None, resolved via default_base_url_resolver.
            default_base_url_resolver: Callable(provider_name) -> base_url | None

        Returns:
            ApiKeyVerificationResult with success status and error message.
        """
        if not key:
            logger.info("[VerifyKey] provider=%s result=empty_key", provider)
            return ApiKeyVerificationResult(success=False, error_message="API Key is empty")

        masked = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "***"

        if base_url is None:
            if default_base_url_resolver is not None:
                base_url = default_base_url_resolver(provider)
            if base_url is None:
                logger.info("[VerifyKey] provider=%s result=unknown_provider", provider)
                return ApiKeyVerificationResult(
                    success=False, error_message=f"Unknown provider: {provider}"
                )

        logger.info("[VerifyKey] provider=%s key=%s base_url=%s", provider, masked, base_url)

        try:
            status_code, body = await self.model_discovery.test_connection(base_url, key)

            if status_code == 200:
                logger.info("[VerifyKey] provider=%s result=OK status=%d", provider, status_code)
                return ApiKeyVerificationResult(success=True)
            if status_code == 401:
                logger.info("[VerifyKey] provider=%s result=bad_key status=401", provider)
                return ApiKeyVerificationResult(
                    success=False, error_message="API key invalid (401 Unauthorized)"
                )
            if status_code == 0:
                logger.info("[VerifyKey] provider=%s result=connection_error detail=%s", provider, body)
                return ApiKeyVerificationResult(
                    success=False, error_message=f"Cannot reach endpoint {base_url}: {body}"
                )
            logger.info(
                "[VerifyKey] provider=%s result=unexpected_status=%d body=%s",
                provider, status_code, body,
            )
            return ApiKeyVerificationResult(
                success=False, error_message=f"Server returned {status_code}: {body}"
            )
        except Exception as exc:
            logger.info("[VerifyKey] provider=%s exception=%s", provider, exc)
            logger.error("Verification error for %s: %s", provider, exc)
            return ApiKeyVerificationResult(success=False, error_message=str(exc))
