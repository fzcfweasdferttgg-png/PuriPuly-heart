from __future__ import annotations

import asyncio
from uuid import uuid4

from puripuly_heart.app.wiring.wiring_translation_variants import (
    SECRET_BACKUP_OPENAI_COMPATIBLE_API_KEY,
    SECRET_OPENAI_COMPATIBLE_API_KEY,
    build_backup_provider,
    build_variant_provider,
    wrap_backup_chain,
)
from puripuly_heart.config.runtime_resolution import (
    UserBackupTranslationRuntimeIntent,
    UserLocalLLMRuntimeIntent,
    UserOpenAICompatibleRuntimeIntent,
)
from puripuly_heart.core.llm.fallback_chain import SequentialFallbackLLMProvider
from puripuly_heart.core.llm.provider import LLMProvider
from puripuly_heart.core.storage.secrets import InMemorySecretStore
from puripuly_heart.domain.models import Translation


class FakeProvider(LLMProvider):
    def __init__(self, *, error: Exception | None = None, delay_s: float = 0.0) -> None:
        self.error = error
        self.delay_s = delay_s
        self.calls = 0
        self.closed = False

    async def translate(self, *, utterance_id, **_kwargs) -> Translation:
        self.calls += 1
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return Translation(
            utterance_id=utterance_id,
            text="ok",
            source_text="src",
            source_language="en",
            target_language="ko",
        )

    async def close(self) -> None:
        self.closed = True


def _chain(primary: LLMProvider, fallback: LLMProvider, **kwargs) -> SequentialFallbackLLMProvider:
    return SequentialFallbackLLMProvider(primary=primary, fallback=fallback, **kwargs)


async def _translate(provider: LLMProvider) -> Translation:
    return await provider.translate(
        utterance_id=uuid4(),
        text="src",
        system_prompt="prompt",
        source_language="en",
        target_language="ko",
    )


async def test_primary_success_does_not_touch_backup() -> None:
    primary = FakeProvider()
    backup = FakeProvider()
    used: list[bool] = []

    result = await _translate(_chain(primary, backup, on_fallback_used=lambda: used.append(True)))

    assert result.text == "ok"
    assert primary.calls == 1
    assert backup.calls == 0
    assert used == []


async def test_primary_error_falls_back_to_backup() -> None:
    primary = FakeProvider(error=RuntimeError("boom"))
    backup = FakeProvider()
    used: list[bool] = []

    result = await _translate(_chain(primary, backup, on_fallback_used=lambda: used.append(True)))

    assert result.text == "ok"
    assert primary.calls == 1
    assert backup.calls == 1
    assert used == [True]


async def test_primary_stall_falls_back_after_wait_threshold() -> None:
    primary = FakeProvider(delay_s=1.0)
    backup = FakeProvider()

    result = await _translate(_chain(primary, backup, primary_wait_s=0.05))

    assert result.text == "ok"
    assert backup.calls == 1


async def test_backup_failure_raises() -> None:
    primary = FakeProvider(error=RuntimeError("primary down"))
    backup = FakeProvider(error=RuntimeError("backup down"))

    try:
        await _translate(_chain(primary, backup))
    except RuntimeError as exc:
        assert str(exc) == "backup down"
    else:
        raise AssertionError("backup failure must propagate")


async def test_close_closes_both_providers() -> None:
    primary = FakeProvider()
    backup = FakeProvider()

    await _chain(primary, backup).close()

    assert primary.closed is True
    assert backup.closed is True


def test_wrap_backup_chain_without_backup_returns_primary() -> None:
    primary = FakeProvider()

    assert wrap_backup_chain(primary, None) is primary


def test_build_network_variant_resolves_preset_and_key_slot() -> None:
    secrets = InMemorySecretStore()
    secrets.set(SECRET_OPENAI_COMPATIBLE_API_KEY, "net-key")

    provider = build_variant_provider(
        "openai_compatible",
        local_llm=UserLocalLLMRuntimeIntent(),
        openai_compatible=UserOpenAICompatibleRuntimeIntent(
            provider_key="deepseek", model="deepseek-chat"
        ),
        secrets=secrets,
    )

    assert provider is not None
    assert provider.base_url == "https://api.deepseek.com/v1"
    assert provider.model == "deepseek-chat"
    assert provider.api_key == "net-key"


def test_build_network_variant_prefers_explicit_base_url() -> None:
    provider = build_variant_provider(
        "openai_compatible",
        local_llm=UserLocalLLMRuntimeIntent(),
        openai_compatible=UserOpenAICompatibleRuntimeIntent(
            provider_key="deepseek", base_url="http://127.0.0.1:9999/v1", model="m1"
        ),
        secrets=InMemorySecretStore(),
    )

    assert provider is not None
    assert provider.base_url == "http://127.0.0.1:9999/v1"


def test_build_local_variant_requires_address_and_model() -> None:
    secrets = InMemorySecretStore()

    assert (
        build_variant_provider(
            "local_llm",
            local_llm=UserLocalLLMRuntimeIntent(base_url="", model=""),
            openai_compatible=UserOpenAICompatibleRuntimeIntent(),
            secrets=secrets,
        )
        is None
    )

    provider = build_variant_provider(
        "local_llm",
        local_llm=UserLocalLLMRuntimeIntent(
            base_url="http://127.0.0.1:11434/v1", model="llama3.1:8b"
        ),
        openai_compatible=UserOpenAICompatibleRuntimeIntent(),
        secrets=secrets,
    )
    assert provider is not None
    assert provider.model == "llama3.1:8b"


def test_backup_provider_uses_backup_slots_with_primary_key_fallback() -> None:
    secrets = InMemorySecretStore()
    secrets.set(SECRET_OPENAI_COMPATIBLE_API_KEY, "primary-key")
    backup = UserBackupTranslationRuntimeIntent(
        enabled=True,
        model="openai_compatible",
        openai_compatible=UserOpenAICompatibleRuntimeIntent(
            provider_key="openai", model="gpt-test"
        ),
    )

    provider = build_backup_provider(backup, secrets=secrets)

    assert provider is not None
    assert provider.api_key == "primary-key"
    secrets.set(SECRET_BACKUP_OPENAI_COMPATIBLE_API_KEY, "backup-key")
    provider = build_backup_provider(backup, secrets=secrets)
    assert provider is not None
    assert provider.api_key == "backup-key"


def test_backup_provider_disabled_or_incomplete_returns_none() -> None:
    secrets = InMemorySecretStore()

    assert build_backup_provider(UserBackupTranslationRuntimeIntent(), secrets=secrets) is None
    assert (
        build_backup_provider(
            UserBackupTranslationRuntimeIntent(
                enabled=True,
                model="openai_compatible",
                openai_compatible=UserOpenAICompatibleRuntimeIntent(model="m"),
            ),
            secrets=secrets,
        )
        is None
    )
