"""Registry of official OpenAI-compatible translation providers.

providers.json ships bundled and is copied to the user config directory on
first use; the user copy is authoritative and may be edited or extended
(own endpoints are ordinary entries). Format:
{"version": "1.0", "providers": {key: {"label", "base_url"}}}.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from puripuly_heart.config.paths import user_config_dir

logger = logging.getLogger(__name__)

PROVIDERS_FILENAME = "providers.json"


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    key: str
    label: str
    base_url: str


def _bundled_providers_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "providers" / PROVIDERS_FILENAME


def user_providers_path() -> Path:
    return user_config_dir() / PROVIDERS_FILENAME


def ensure_providers_file() -> Path:
    dest = user_providers_path()
    if dest.exists():
        return dest
    src = _bundled_providers_path()
    if not src.exists():
        logger.warning("[Providers] Bundled providers.json not found at %s", src)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    logger.info("[Providers] Copied default providers.json to %s", dest)
    return dest


def load_provider_presets() -> dict[str, ProviderPreset]:
    path = ensure_providers_file()
    if not path.exists():
        logger.warning("[Providers] providers.json not found, returning empty")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("providers", {})
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("[Providers] Failed to load providers.json: %s", exc)
        return {}
    presets: dict[str, ProviderPreset] = {}
    if not isinstance(raw, dict):
        return presets
    for key, info in raw.items():
        if not isinstance(key, str) or not isinstance(info, dict):
            continue
        label = str(info.get("label") or key).strip() or key
        base_url = str(info.get("base_url") or "").strip()
        if not base_url:
            continue
        presets[key] = ProviderPreset(key=key, label=label, base_url=base_url)
    return presets
