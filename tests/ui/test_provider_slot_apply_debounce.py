from __future__ import annotations

import pytest

from puripuly_heart.app.ports.settings_view import ProviderApplyIntent, SettingsApplyOutcome
from puripuly_heart.ui.app import TranslatorApp
from puripuly_heart.ui.settings_queue import SettingsQueue


async def _apply_success(_intent: object) -> SettingsApplyOutcome:
    return SettingsApplyOutcome(saved=True, applied=True)


@pytest.mark.asyncio
async def test_provider_apply_waits_for_slot_switching_to_settle() -> None:
    app = TranslatorApp.__new__(TranslatorApp)
    app._settings_queue = SettingsQueue(_apply_success, lambda _intent: None)
    tasks = []
    app._queue_settings_mutation_task = tasks.append

    app._queue_settings_apply(ProviderApplyIntent(()))

    assert len(tasks) == 1
    delays = []

    async def instant_sleep(seconds: float) -> None:
        delays.append(seconds)

    original_sleep = __import__("asyncio").sleep
    __import__("asyncio").sleep = instant_sleep
    try:
        await tasks.pop(0)()
    finally:
        __import__("asyncio").sleep = original_sleep

    assert delays == [0.15]
