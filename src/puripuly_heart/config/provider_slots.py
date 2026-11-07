"""Saved provider configuration slots for the translation settings cards.

Each card (translation and backup translation) keeps three independent
slots. A slot remembers a full provider configuration so the user can
switch between saved setups with one click. The state is stored in a
small JSON file next to the user settings file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

PROVIDER_SLOTS_FILENAME = "provider_slots.json"
SLOT_INDEXES = (1, 2, 3)


@dataclass(frozen=True, slots=True)
class ProviderSlotConfig:
    variant: str = ""
    provider_key: str = ""
    base_url: str = ""
    model: str = ""
    extra_body_json: str = ""

    @property
    def is_empty(self) -> bool:
        return not (
            self.variant
            or self.provider_key
            or self.base_url
            or self.model
            or self.extra_body_json
        )


@dataclass(frozen=True, slots=True)
class ProviderCardSlots:
    active: int = 1
    slots: tuple[ProviderSlotConfig | None, ...] = field(default=(None, None, None))

    def slot(self, index: int) -> ProviderSlotConfig | None:
        if index in SLOT_INDEXES:
            return self.slots[index - 1]
        return None

    def with_slot(self, index: int, config: ProviderSlotConfig) -> "ProviderCardSlots":
        if index not in SLOT_INDEXES:
            return self
        slots = list(self.slots)
        slots[index - 1] = config
        return replace(self, slots=tuple(slots))


def _config_to_dict(config: ProviderSlotConfig | None) -> dict[str, str] | None:
    if config is None or config.is_empty:
        return None
    return {
        "variant": config.variant,
        "provider_key": config.provider_key,
        "base_url": config.base_url,
        "model": config.model,
        "extra_body_json": config.extra_body_json,
    }


def _config_from_dict(raw: object) -> ProviderSlotConfig | None:
    if not isinstance(raw, dict):
        return None
    config = ProviderSlotConfig(
        variant=str(raw.get("variant", "")),
        provider_key=str(raw.get("provider_key", "")),
        base_url=str(raw.get("base_url", "")),
        model=str(raw.get("model", "")),
        extra_body_json=str(raw.get("extra_body_json", "")),
    )
    return None if config.is_empty else config


def load_provider_slots(path: Path) -> dict[str, ProviderCardSlots]:
    """Load card slot states from ``path``; missing or broken files give empty state."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    state: dict[str, ProviderCardSlots] = {}
    for card, card_raw in raw.items():
        if not isinstance(card_raw, dict):
            continue
        slots_raw = card_raw.get("slots")
        slots: list[ProviderSlotConfig | None] = [None, None, None]
        if isinstance(slots_raw, list):
            for position in range(3):
                slots[position] = _config_from_dict(
                    slots_raw[position] if position < len(slots_raw) else None
                )
        active = card_raw.get("active", 1)
        state[str(card)] = ProviderCardSlots(
            active=active if active in SLOT_INDEXES else 1,
            slots=tuple(slots),
        )
    return state


def save_provider_slots(path: Path, state: dict[str, ProviderCardSlots]) -> None:
    """Persist card slot states to ``path`` (best effort)."""
    payload = {
        card: {
            "active": card_slots.active,
            "slots": [_config_to_dict(cfg) for cfg in card_slots.slots],
        }
        for card, card_slots in state.items()
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


__all__ = [
    "PROVIDER_SLOTS_FILENAME",
    "SLOT_INDEXES",
    "ProviderCardSlots",
    "ProviderSlotConfig",
    "load_provider_slots",
    "save_provider_slots",
]
