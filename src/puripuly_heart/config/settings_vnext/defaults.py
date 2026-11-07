from __future__ import annotations

import locale
from dataclasses import replace

from puripuly_heart.config.settings_vnext.schema import (
    AppSettingsVNext,
    UiIntent,
)


def detect_system_locale() -> str | None:
    try:
        return locale.getlocale()[0]
    except ValueError, locale.Error:
        return None


def resolve_first_run_ui_locale(system_locale: str | None) -> str:
    if system_locale is None:
        return "en"
    normalized = system_locale.strip()
    if not normalized:
        return "en"
    normalized = normalized.split(".", maxsplit=1)[0]
    normalized = normalized.split("@", maxsplit=1)[0]
    normalized = normalized.replace("_", "-").casefold()
    if normalized == "ko" or normalized.startswith(("ko-", "korean")):
        return "ko"
    if normalized == "ja" or normalized.startswith(("ja-", "japanese")):
        return "ja"
    if normalized == "zh" or normalized.startswith(("zh-", "chinese")):
        return "zh-CN"
    if normalized == "ru" or normalized.startswith(("ru-", "russian")):
        return "ru"
    return "en"


def _first_run_translation_defaults(settings: AppSettingsVNext) -> AppSettingsVNext:
    translation = settings.intent.translation
    return replace(
        settings,
        intent=replace(
            settings.intent,
            translation=replace(
                translation,
                model="",
                connection="",
                openrouter_selected_source="none",
                openrouter_selection_alias=None,
                openrouter_provider_routing="default",
                connection_history={},
            ),
        ),
    )


def new_settings_for_reset(system_locale: str | None = None) -> AppSettingsVNext:
    locale_value = resolve_first_run_ui_locale(
        detect_system_locale() if system_locale is None else system_locale
    )
    settings = _first_run_translation_defaults(AppSettingsVNext())
    settings = replace(
        settings,
        intent=replace(settings.intent, ui=UiIntent(locale=locale_value)),
    )
    return settings


def new_settings_for_first_run(system_locale: str | None = None) -> AppSettingsVNext:
    locale_value = resolve_first_run_ui_locale(
        detect_system_locale() if system_locale is None else system_locale
    )
    settings = _first_run_translation_defaults(AppSettingsVNext())
    settings = replace(
        settings,
        intent=replace(settings.intent, ui=UiIntent(locale=locale_value)),
    )
    return settings


__all__ = [
    "detect_system_locale",
    "new_settings_for_first_run",
    "new_settings_for_reset",
    "resolve_first_run_ui_locale",
]
