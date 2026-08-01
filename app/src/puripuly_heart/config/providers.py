from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from puripuly_heart.config.paths import user_config_dir

logger = logging.getLogger(__name__)

_PROVIDERS_FILENAME = "providers.json"
_BUNDLED_PROVIDERS: Path | None = None


def _bundled_providers_path() -> Path:
    global _BUNDLED_PROVIDERS
    if _BUNDLED_PROVIDERS is None:
        # config/providers.py -> puripuly_heart -> src -> app -> app/providers/providers.json
        _BUNDLED_PROVIDERS = Path(__file__).resolve().parent.parent.parent.parent / "providers" / _PROVIDERS_FILENAME
    return _BUNDLED_PROVIDERS


def user_providers_path() -> Path:
    return user_config_dir() / _PROVIDERS_FILENAME


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


def load_providers() -> dict[str, Any]:
    path = ensure_providers_file()
    if not path.exists():
        logger.warning("[Providers] providers.json not found, returning empty")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("providers", {})
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("[Providers] Failed to load providers.json: %s", exc)
        return {}
