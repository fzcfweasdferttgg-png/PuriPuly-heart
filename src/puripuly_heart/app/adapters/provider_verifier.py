from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from puripuly_heart.app.ports.provider_verifier import (
    PROVIDER_VERIFICATION_STATUS_FAILED,
    PROVIDER_VERIFICATION_STATUS_VERIFIED,
    ProviderVerificationRequest,
    ProviderVerificationResult,
    ProviderVerifierPort,
)
from puripuly_heart.config.alibaba_connection import (
    AlibabaRegionalSettings,
    resolve_alibaba_connection,
    validated_native_url,
    validated_websocket_url,
)
from puripuly_heart.core.diagnostics import emit_failure
from puripuly_heart.core.error_messages import (
    openrouter_auth_failure_report,
    provider_failure_report,
    stt_failure_report,
)
from puripuly_heart.core.openrouter.openrouter_metadata import OpenRouterKeyMetadata

logger = logging.getLogger(__name__)


def _optional_context_str(request: ProviderVerificationRequest, key: str) -> str | None:
    value = request.context.get(key)
    if isinstance(value, str) and value:
        return value
    return None


def _optional_context_bool(request: ProviderVerificationRequest, key: str) -> bool:
    value = request.context.get(key)
    return bool(value) if isinstance(value, bool) else False


@dataclass(frozen=True, slots=True)
class ProviderVerifierAdapter(ProviderVerifierPort):
    async def verify_api_key(
        self,
        provider: str,
        api_key: str,
        *,
        model: str | None = None,
        base_url: str | None = None,
        low_latency: bool = False,
    ) -> bool:
        _ = (provider, api_key, model, base_url, low_latency)
        return False

    async def verify_qwen_llm_api_key(
        self,
        api_key: str,
        *,
        base_url: str,
        model: str | None,
        low_latency: bool,
    ) -> bool:
        _ = (api_key, base_url, model, low_latency)
        return False

    async def probe_qwen_llm_api_key(self, api_key: str, *, base_url: str, model: str) -> bool:
        _ = (api_key, base_url, model)
        return False

    async def verify_qwen_audio_api_key(self, api_key: str, *, endpoint: str, model: str) -> bool:
        _ = (api_key, endpoint, model)
        return False

    async def fetch_openrouter_key_metadata(
        self,
        api_key: str,
    ) -> OpenRouterKeyMetadata | None:
        _ = api_key
        return None

    async def verify_provider_secret(
        self,
        request: ProviderVerificationRequest,
    ) -> ProviderVerificationResult:
        try:
            verified = await self.verify_api_key(
                request.provider,
                request.secret_value,
                model=_optional_context_str(request, "model"),
                base_url=_optional_context_str(request, "base_url"),
                low_latency=_optional_context_bool(request, "low_latency"),
            )
        except Exception as exc:
            if request.provider == "openrouter":
                report = openrouter_auth_failure_report(exc, stage="key_verification")
            elif request.provider in {
                "deepgram",
                "gemini_transcribe",
                "elevenlabs_scribe",
                "soniox",
            }:
                report = stt_failure_report(
                    exc, provider=request.provider, operation="key_verification", channel=""
                )
            else:
                report = provider_failure_report(
                    exc, provider=request.provider, operation="key_verification"
                )
            diagnostics = replace(
                report.diagnostics,
                channel=None,
                fields={
                    **{
                        key: value
                        for key, value in report.diagnostics.fields.items()
                        if key != "channel"
                    },
                    "verification_scope": "key_probe",
                    "recognition_readiness": "unobserved",
                },
            )
            emit_failure(logger, diagnostics, stage="key_verification")
            return ProviderVerificationResult(
                status=PROVIDER_VERIFICATION_STATUS_FAILED,
                provider=request.provider,
                secret_key=request.secret_key,
                secret_revision=request.secret_revision,
                evidence={
                    "verification_scope": "key_probe",
                    "recognition_readiness": "unobserved",
                },
                message=report.message,
                diagnostics=diagnostics,
            )

        if verified:
            return ProviderVerificationResult(
                status=PROVIDER_VERIFICATION_STATUS_VERIFIED,
                provider=request.provider,
                secret_key=request.secret_key,
                secret_revision=request.secret_revision,
                evidence={
                    "verifier": "provider_adapter",
                    "provider": request.provider,
                    "context_count": len(request.context),
                    "verification_scope": "key_probe",
                    "recognition_readiness": "unobserved",
                },
                message=None,
                diagnostics=None,
            )

        return ProviderVerificationResult(
            status=PROVIDER_VERIFICATION_STATUS_FAILED,
            provider=request.provider,
            secret_key=request.secret_key,
            secret_revision=request.secret_revision,
            evidence={
                "verifier": "provider_adapter",
                "provider": request.provider,
                "verification_scope": "key_probe",
                "recognition_readiness": "unobserved",
                "context_count": len(request.context),
            },
            message=None,
            diagnostics=None,
        )


__all__ = ["ProviderVerifierAdapter"]
