from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums import (
    TranslationConnection,
    TranslationFallbackSelectionAlias,
    TranslationModel,
    _default_translation_connection,
    _parse_translation_connection,
    _parse_translation_connection_history,
    _parse_translation_fallback_selection_alias,
    _parse_translation_model,
    _supported_translation_connections,
    supported_translation_connections,
)


@dataclass(slots=True)
class TranslationSettings:
    model: TranslationModel = TranslationModel.OPENAI_COMPATIBLE
    connection: TranslationConnection = TranslationConnection.OPENAI_COMPATIBLE
    fallback_selection_alias: TranslationFallbackSelectionAlias = (
        TranslationFallbackSelectionAlias.NONE
    )
    connection_history: dict[str, TranslationConnection] = field(
        default_factory=lambda: _default_translation_connection_history()
    )

    def validate(self) -> None:
        if not isinstance(self.model, TranslationModel):
            raise ValueError("invalid translation model")
        if not isinstance(self.connection, TranslationConnection):
            raise ValueError("invalid translation connection")
        if not isinstance(self.fallback_selection_alias, TranslationFallbackSelectionAlias):
            raise ValueError("invalid translation fallback selection")
        if self.connection not in _supported_translation_connections(self.model):
            raise ValueError("translation connection is not supported for model")
        if not isinstance(self.connection_history, dict):
            raise ValueError("translation connection_history must be a dict")
        for model_value, connection in self.connection_history.items():
            model = _parse_translation_model(model_value)
            if model is None:
                raise ValueError("invalid translation connection_history model")
            if not isinstance(connection, TranslationConnection):
                raise ValueError("invalid translation connection_history connection")
            if connection not in _supported_translation_connections(model):
                raise ValueError("translation connection_history connection is not supported")


def _default_translation_connection_history() -> dict[str, TranslationConnection]:
    return {TranslationModel.OPENAI_COMPATIBLE.value: TranslationConnection.OPENAI_COMPATIBLE}


def _normalize_translation_settings(
    *,
    model: TranslationModel | None,
    connection: TranslationConnection | None,
    fallback_selection_alias: object = None,
    history: object = None,
) -> TranslationSettings:
    normalized_model = model or TranslationModel.OPENAI_COMPATIBLE
    normalized_history = _parse_translation_connection_history(history)
    if connection not in _supported_translation_connections(normalized_model):
        connection = _default_translation_connection(normalized_model)
    normalized_history[normalized_model.value] = connection
    return TranslationSettings(
        model=normalized_model,
        connection=connection,
        fallback_selection_alias=_parse_translation_fallback_selection_alias(
            fallback_selection_alias
        ),
        connection_history=normalized_history,
    )


def _translation_data_has_valid_model(value: object) -> bool:
    return isinstance(value, dict) and _parse_translation_model(value.get("model")) is not None


def _translation_settings_to_dict(settings: TranslationSettings) -> dict[str, Any]:
    return {
        "model": settings.model.value,
        "connection": settings.connection.value,
        "fallback_selection_alias": settings.fallback_selection_alias.value,
        "connection_history": {
            model: connection.value for model, connection in settings.connection_history.items()
        },
    }


def _default_translation_settings_dict() -> dict[str, Any]:
    return {
        "model": TranslationModel.OPENAI_COMPATIBLE.value,
        "connection": TranslationConnection.OPENAI_COMPATIBLE.value,
        "fallback_selection_alias": TranslationFallbackSelectionAlias.NONE.value,
        "connection_history": {
            TranslationModel.OPENAI_COMPATIBLE.value: TranslationConnection.OPENAI_COMPATIBLE.value,
        },
    }


def _translation_settings_is_exact_default(settings: TranslationSettings) -> bool:
    return _translation_settings_to_dict(settings) == _default_translation_settings_dict()
