from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from puripuly_heart.config import providers_registry
from puripuly_heart.ui.views import settings as settings_view

PROVIDER_WINDOW_KINDS = (
    "translation_openai",
    "translation_anthropic",
    "translation_local",
    "backup_openai",
    "backup_anthropic",
    "backup_local",
)


class ProviderWindow:
    def __init__(self) -> None:
        self.is_open = False
        self.accepted = False
        self.on_accept = None
        self.on_close_request = None
        self.opened_with: dict = {}

    def open(self, schedule, *, title, content, on_accept, on_close_request, height=450) -> None:
        self.is_open = True
        self.accepted = False
        self.on_accept = on_accept
        self.on_close_request = on_close_request
        self.opened_with = dict(title=title, content=content, height=height)

    def accept(self) -> None:
        self.accepted = True
        if self.on_accept is not None:
            self.on_accept()
        self.is_open = False

    async def close(self) -> None:
        if self.is_open and not self.accepted and self.on_close_request is not None:
            self.on_close_request()
        self.is_open = False


class WindowSchedule:
    def __init__(self) -> None:
        self.scheduled = []

    def __call__(self, factory):
        self.scheduled.append(factory)
        return factory

    def run_all(self) -> None:
        while self.scheduled:
            factory = self.scheduled.pop(0)
            result = factory()
            if asyncio.iscoroutine(result):
                asyncio.run(result)


def create_settings_view(monkeypatch: pytest.MonkeyPatch) -> settings_view.SettingsView:
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)
    return settings_view.SettingsView()


def attach_provider_windows(view: settings_view.SettingsView):
    windows = {kind: ProviderWindow() for kind in PROVIDER_WINDOW_KINDS}
    schedule = WindowSchedule()
    view.attach_provider_settings_window(windows, schedule)
    return windows, schedule


def install_provider_presets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    documents = {
        "openai": {
            "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1"},
        },
        "anthropic": {
            "anthropic": {"label": "Anthropic", "base_url": "https://api.anthropic.com"},
        },
        "local": {
            "llamacpp": {
                "label": "llama.cpp",
                "base_url": "http://127.0.0.1:8080/v1",
                "backend": "llamacpp",
            },
        },
    }
    paths = {protocol: tmp_path / f"{protocol}_providers.json" for protocol in documents}
    for protocol, providers in documents.items():
        paths[protocol].write_text(
            json.dumps({"version": "1.0", "providers": providers}), encoding="utf-8"
        )
    monkeypatch.setattr(
        providers_registry,
        "ensure_providers_file",
        lambda protocol="openai": paths[protocol],
    )
    monkeypatch.setattr(providers_registry, "_PROVIDER_PRESETS_CACHE", None)
