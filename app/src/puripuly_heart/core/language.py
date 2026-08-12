from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class _HasChannel(Protocol):
    channel: str


@runtime_checkable
class _HasLanguageSettings(Protocol):
    peer_source_language: str
    peer_target_language: str
    source_language: str
    target_language: str


def source_language_for(settings: _HasLanguageSettings, runtime: _HasChannel) -> str:
    if runtime.channel == "peer" and settings.peer_source_language:
        return settings.peer_source_language
    return settings.source_language


def target_language_for(settings: _HasLanguageSettings, runtime: _HasChannel) -> str:
    if runtime.channel == "peer" and settings.peer_target_language:
        return settings.peer_target_language
    return settings.target_language
