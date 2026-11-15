from __future__ import annotations

import asyncio

import pytest

from puripuly_heart.core.llm.fallback_chain import SequentialFallbackLLMProvider
from puripuly_heart.core.runtime.provider_handle import ProviderRuntimeHandle


class CloseProvider:
    def __init__(self, *, failures: int = 0, label: str) -> None:
        self.failures = failures
        self.label = label
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_calls <= self.failures:
            raise RuntimeError(f"{self.label} close failed")


@pytest.mark.asyncio
async def test_primary_close_failure_does_not_skip_backup_cleanup() -> None:
    primary = CloseProvider(failures=1, label="primary")
    backup = CloseProvider(label="backup")
    chain = SequentialFallbackLLMProvider(primary=primary, fallback=backup)
    handle = ProviderRuntimeHandle(name="translation", provider=chain)

    with pytest.raises(RuntimeError, match="primary close failed"):
        await handle.close()

    assert primary.close_calls == 1
    assert backup.close_calls == 1
    assert handle.provider is chain

    await handle.close()

    assert primary.close_calls == 2
    assert backup.close_calls == 2
    assert handle.provider is None


@pytest.mark.asyncio
async def test_close_reports_both_failures_after_closing_both_providers() -> None:
    primary = CloseProvider(failures=1, label="primary")
    backup = CloseProvider(failures=1, label="backup")
    chain = SequentialFallbackLLMProvider(primary=primary, fallback=backup)

    with pytest.raises(ExceptionGroup) as caught:
        await chain.close()

    assert primary.close_calls == 1
    assert backup.close_calls == 1
    assert [str(error) for error in caught.value.exceptions] == [
        "primary close failed",
        "backup close failed",
    ]


class CancelledCloseProvider:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        raise asyncio.CancelledError


@pytest.mark.asyncio
async def test_primary_close_cancellation_closes_backup_and_propagates() -> None:
    primary = CancelledCloseProvider()
    backup = CloseProvider(label="backup")
    chain = SequentialFallbackLLMProvider(primary=primary, fallback=backup)

    with pytest.raises(asyncio.CancelledError):
        await chain.close()

    assert primary.close_calls == 1
    assert backup.close_calls == 1
