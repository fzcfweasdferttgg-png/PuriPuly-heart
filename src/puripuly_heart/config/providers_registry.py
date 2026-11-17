"""Editable provider registries for each translation transport family.

Each registry ships bundled and is copied to the user config directory on
first use; the user copy is authoritative and may be edited or extended.
The settings UI can add, rename, edit, and delete entries, with at most 50
records. Format:
{"version": "1.0", "providers": {key: {"label", "base_url", "backend"?}}}.
Writes are serialized across threads and processes and use unique temporary files.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from filelock import FileLock

from puripuly_heart.config.paths import user_config_dir
from puripuly_heart.config.provider_values import normalize_local_llm_base_url

logger = logging.getLogger(__name__)

PROVIDERS_FILENAME = "providers.json"
ANTHROPIC_PROVIDERS_FILENAME = "anthropic_providers.json"
LOCAL_PROVIDERS_FILENAME = "local_providers.json"
MAX_PROVIDER_PRESETS = 50
DEFAULT_LOCAL_PROVIDER_BACKEND = "openai-compatible"
LOCAL_PROVIDER_BACKENDS = (
    "colibri",
    "lmstudio",
    "llamacpp",
    "vllm",
    "sglang",
    "mlx",
    "koboldcpp",
    "textgen",
    "tabbyapi",
    "localai",
    "jan",
    "ollama",
    DEFAULT_LOCAL_PROVIDER_BACKEND,
)


class ProviderPresetError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    key: str
    label: str
    base_url: str
    backend: str = ""


def _providers_filename(protocol: str) -> str:
    if protocol == "openai":
        return PROVIDERS_FILENAME
    if protocol == "anthropic":
        return ANTHROPIC_PROVIDERS_FILENAME
    if protocol == "local":
        return LOCAL_PROVIDERS_FILENAME
    raise ValueError(f"unsupported provider protocol: {protocol}")


def _bundled_providers_path(protocol: str = "openai") -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "data"
        / "providers"
        / _providers_filename(protocol)
    )


def user_providers_path(protocol: str = "openai") -> Path:
    return user_config_dir() / _providers_filename(protocol)


def ensure_providers_file(protocol: str = "openai") -> Path:
    dest = user_providers_path(protocol)
    if dest.exists():
        return dest
    src = _bundled_providers_path(protocol)
    if not src.exists():
        logger.warning("[Providers] Bundled providers.json not found at %s", src)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(f"{dest.name}.{uuid4().hex}.tmp")
    shutil.copy2(src, temporary)
    temporary.replace(dest)
    logger.info("[Providers] Copied default providers.json to %s", dest)
    return dest


_PROVIDER_PRESETS_CACHE: tuple[Path, int, int, dict[str, ProviderPreset]] | None = None


def _loaded_backend(key: str, info: dict, protocol: str) -> str:
    if protocol != "local":
        return ""
    candidate = str(info.get("backend") or key).strip()
    if candidate in LOCAL_PROVIDER_BACKENDS:
        return candidate
    return DEFAULT_LOCAL_PROVIDER_BACKEND


def load_provider_presets(protocol: str = "openai") -> dict[str, ProviderPreset]:
    global _PROVIDER_PRESETS_CACHE

    path = ensure_providers_file(protocol)
    if not path.exists():
        logger.warning("[Providers] providers.json not found, returning empty")
        return {}
    try:
        stat = path.stat()
        cached = _PROVIDER_PRESETS_CACHE
        if (
            cached is not None
            and cached[0] == path
            and cached[1] == stat.st_mtime_ns
            and cached[2] == stat.st_size
        ):
            return dict(cached[3])
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
        presets[key] = ProviderPreset(
            key=key,
            label=label,
            base_url=base_url,
            backend=_loaded_backend(key, info, protocol),
        )
    _PROVIDER_PRESETS_CACHE = (path, stat.st_mtime_ns, stat.st_size, presets)
    return dict(presets)


def _provider_document_for_write(protocol: str = "openai") -> tuple[Path, dict, dict]:
    path = ensure_providers_file(protocol)
    if not path.exists():
        return path, {"version": "1.0"}, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderPresetError("invalid_file") from exc
    if not isinstance(data, dict):
        raise ProviderPresetError("invalid_file")
    providers = data.get("providers", {})
    if not isinstance(providers, dict):
        raise ProviderPresetError("invalid_file")
    return path, data, providers


def _write_provider_document(path: Path, data: dict, providers: dict) -> None:
    global _PROVIDER_PRESETS_CACHE

    data["providers"] = providers
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
    except OSError as exc:
        raise ProviderPresetError("write_failed") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("[Providers] Could not remove temporary file: %s", temporary)
    _PROVIDER_PRESETS_CACHE = None


def _validated_preset_fields(
    label: str,
    base_url: str,
    providers: dict,
    *,
    current_key: str = "",
) -> tuple[str, str]:
    normalized_label = label.strip()
    if not normalized_label:
        raise ProviderPresetError("empty_name")
    if len(normalized_label) > 80:
        raise ProviderPresetError("name_too_long")
    try:
        normalized_url = normalize_local_llm_base_url(base_url)
    except ValueError as exc:
        raise ProviderPresetError("invalid_url") from exc
    duplicate = any(
        str(info.get("label", "")).strip().casefold() == normalized_label.casefold()
        and key != current_key
        for key, info in providers.items()
        if isinstance(info, dict)
    )
    if duplicate:
        raise ProviderPresetError("duplicate_name")
    return normalized_label, normalized_url


def _new_provider_preset_key(label: str, providers: dict) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-") or "custom"
    key = base
    suffix = 2
    while key in providers:
        key = f"{base}-{suffix}"
        suffix += 1
    return key


def _validated_backend(
    protocol: str,
    backend: str | None,
    providers: dict,
    current_key: str = "",
) -> str:
    if protocol != "local":
        return ""
    candidate = (backend or "").strip()
    if candidate:
        if candidate not in LOCAL_PROVIDER_BACKENDS:
            raise ProviderPresetError("invalid_backend")
        return candidate
    current = providers.get(current_key, {})
    current_backend = str(current.get("backend") or "").strip()
    if current_backend in LOCAL_PROVIDER_BACKENDS:
        return current_backend
    if current_key in LOCAL_PROVIDER_BACKENDS:
        return current_key
    return DEFAULT_LOCAL_PROVIDER_BACKEND


@contextmanager
def _provider_write_transaction(protocol: str):
    path = user_providers_path(protocol)
    with FileLock(str(path) + ".lock"):
        yield _provider_document_for_write(protocol)


def add_provider_preset(
    label: str,
    base_url: str,
    *,
    protocol: str = "openai",
    backend: str | None = None,
) -> ProviderPreset:
    with _provider_write_transaction(protocol) as (path, data, providers):
        if len(providers) >= MAX_PROVIDER_PRESETS:
            raise ProviderPresetError("limit")
        normalized_label, normalized_url = _validated_preset_fields(label, base_url, providers)
        key = _new_provider_preset_key(normalized_label, providers)
        normalized_backend = _validated_backend(protocol, backend, providers, current_key=key)
        record = {"label": normalized_label, "base_url": normalized_url}
        if normalized_backend:
            record["backend"] = normalized_backend
        providers[key] = record
        _write_provider_document(path, data, providers)
        return ProviderPreset(
            key=key,
            label=normalized_label,
            base_url=normalized_url,
            backend=normalized_backend,
        )


def update_provider_preset(
    key: str,
    label: str,
    base_url: str,
    *,
    protocol: str = "openai",
    backend: str | None = None,
) -> ProviderPreset:
    with _provider_write_transaction(protocol) as (path, data, providers):
        if key not in providers:
            raise ProviderPresetError("not_found")
        normalized_label, normalized_url = _validated_preset_fields(
            label, base_url, providers, current_key=key
        )
        normalized_backend = _validated_backend(protocol, backend, providers, current_key=key)
        record = {"label": normalized_label, "base_url": normalized_url}
        if normalized_backend:
            record["backend"] = normalized_backend
        providers[key] = record
        _write_provider_document(path, data, providers)
        return ProviderPreset(
            key=key,
            label=normalized_label,
            base_url=normalized_url,
            backend=normalized_backend,
        )


def delete_provider_preset(key: str, *, protocol: str = "openai") -> ProviderPreset:
    with _provider_write_transaction(protocol) as (path, data, providers):
        if key not in providers:
            raise ProviderPresetError("not_found")
        info = providers.pop(key)
        _write_provider_document(path, data, providers)
        return ProviderPreset(
            key=key,
            label=str(info.get("label", key)),
            base_url=str(info.get("base_url", "")),
            backend=_loaded_backend(key, info, protocol),
        )
