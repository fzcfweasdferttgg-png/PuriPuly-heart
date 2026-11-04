"""Local STT model assets: manifest catalog (v2, simplified).

Models are described by small JSON manifests in ``data/models`` and installed
into the user model root (``<user data>/models/<install_dirname>/``). The
integrity contract is lightweight: byte size + CRC32 per file, checked at
install time only. The installed marker (``installed-manifest.json``) records
a completed install and is never re-hashed at startup.
"""

from __future__ import annotations

import json
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from puripuly_heart.runtime_layout import current_runtime_layout

LOCAL_STT_INSTALLED_MANIFEST_FILENAME = "installed-manifest.json"

LOCAL_STT_MODEL_FAMILIES = (
    "gigaam-v3",
    "parakeet-tdt-0.6b-v3",
    "qwen3-asr-0.6b",
    "qwen3-asr-1.7b",
)
LOCAL_STT_QUANTIZATIONS = ("q8_0", "q6_k", "f16")
LOCAL_STT_MODEL_IDS = tuple(
    f"{family}-gguf-{quant}"
    for quant in LOCAL_STT_QUANTIZATIONS
    for family in LOCAL_STT_MODEL_FAMILIES
)
DEFAULT_LOCAL_STT_MODEL_ID = "qwen3-asr-1.7b-gguf-q6_k"

LOCAL_QWEN_GPU_MODEL_ID = DEFAULT_LOCAL_STT_MODEL_ID
LOCAL_QWEN_GPU_MODEL_FILENAME = "Qwen3-ASR-1.7B-Q6_K.gguf"


def local_stt_manifest_relative_path(model_id: str) -> str:
    return f"data/models/{model_id}.manifest.json"


LOCAL_STT_MANIFEST_RELATIVE_PATHS = {
    model_id: local_stt_manifest_relative_path(model_id) for model_id in LOCAL_STT_MODEL_IDS
}


class LocalSTTAssetError(RuntimeError):
    """Base error for local STT asset contract failures."""


class LocalSTTModelMissingError(LocalSTTAssetError):
    """Raised when the local STT model is not installed."""


class LocalSTTManifestInvalidError(LocalSTTAssetError):
    """Raised when the installed local STT manifest or files are invalid."""


def normalize_crc32(value: object) -> str:
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if len(text) != 8 or any(char not in "0123456789abcdef" for char in text):
        raise LocalSTTManifestInvalidError(f"invalid crc32 value: {value!r}")
    return text


def file_crc32(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest = zlib.crc32(chunk, digest)
    return f"{digest & 0xFFFFFFFF:08x}"


@dataclass(frozen=True, slots=True)
class LocalSTTAssetSource:
    name: str
    repo_id: str = ""
    revision: str = ""
    download_url_template: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "repo_id": self.repo_id,
            "revision": self.revision,
            "download_url_template": self.download_url_template,
        }

    @classmethod
    def from_dict(cls, name: str, data: Mapping[str, object]) -> "LocalSTTAssetSource":
        return cls(
            name=name,
            repo_id=str(data.get("repo_id", "")),
            revision=str(data.get("revision", "")),
            download_url_template=str(data.get("download_url_template", "")),
        )


@dataclass(frozen=True, slots=True)
class LocalSTTAssetFile:
    relative_path: str
    size_bytes: int
    crc32: str

    def remote_path_for_source(self, source_name: str) -> str:
        _ = source_name
        return self.relative_path

    def to_dict(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "crc32": self.crc32,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "LocalSTTAssetFile":
        return cls(
            relative_path=str(data["relative_path"]),
            size_bytes=int(data["size_bytes"]),
            crc32=normalize_crc32(data["crc32"]),
        )


@dataclass(frozen=True, slots=True)
class InstalledLocalSTTManifest:
    manifest_version: int
    model_id: str
    engine: str
    install_dirname: str
    selected_source: str
    selected_revision: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "manifest_version": self.manifest_version,
            "model_id": self.model_id,
            "engine": self.engine,
            "install_dirname": self.install_dirname,
            "selected_source": self.selected_source,
            "selected_revision": self.selected_revision,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "InstalledLocalSTTManifest":
        return cls(
            manifest_version=int(data["manifest_version"]),
            model_id=str(data["model_id"]),
            engine=str(data["engine"]),
            install_dirname=str(data["install_dirname"]),
            selected_source=str(data.get("selected_source", "")),
            selected_revision=(
                str(data["selected_revision"]) if data.get("selected_revision") else None
            ),
        )


@dataclass(frozen=True, slots=True)
class LocalSTTInstallState:
    status: str
    installed_manifest: InstalledLocalSTTManifest | None = None


@dataclass(frozen=True, slots=True)
class LocalSTTAssetManifest:
    model_id: str
    engine: str
    install_dirname: str
    sources: Mapping[str, LocalSTTAssetSource]
    files: tuple[LocalSTTAssetFile, ...]
    manifest_version: int = 2
    installed_manifest_filename: str = LOCAL_STT_INSTALLED_MANIFEST_FILENAME

    def to_dict(self) -> dict[str, object]:
        return {
            "manifest_version": self.manifest_version,
            "model_id": self.model_id,
            "engine": self.engine,
            "install_dirname": self.install_dirname,
            "sources": {name: source.to_dict() for name, source in self.sources.items()},
            "files": [item.to_dict() for item in self.files],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "LocalSTTAssetManifest":
        version = int(data.get("manifest_version", 0))
        if version != 2:
            raise LocalSTTManifestInvalidError(
                f"unsupported manifest_version {version!r}; only v2 is supported"
            )
        raw_sources = data.get("sources")
        if not isinstance(raw_sources, Mapping) or not raw_sources:
            raise LocalSTTManifestInvalidError("manifest sources must be a non-empty object")
        raw_files = data.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise LocalSTTManifestInvalidError("manifest files must be a non-empty list")
        return cls(
            manifest_version=version,
            model_id=str(data["model_id"]),
            engine=str(data.get("engine", "transcribe.cpp-vulkan")),
            install_dirname=str(data["install_dirname"]),
            sources={
                str(name): LocalSTTAssetSource.from_dict(str(name), source)
                for name, source in raw_sources.items()
            },
            files=tuple(LocalSTTAssetFile.from_dict(item) for item in raw_files),
        )


def default_local_stt_model_root() -> Path:
    return current_runtime_layout().model_cache_root


def default_local_stt_model_dir(model_id: str = DEFAULT_LOCAL_STT_MODEL_ID) -> Path:
    return default_local_stt_model_root() / model_id


def default_local_stt_installed_manifest_path(model_dir: Path | None = None) -> Path:
    resolved_model_dir = model_dir or default_local_stt_model_dir()
    return resolved_model_dir / LOCAL_STT_INSTALLED_MANIFEST_FILENAME


def default_local_stt_source_for_locale(locale: str | None) -> str:
    _ = locale
    return "huggingface"


def load_local_stt_asset_manifest(
    model_id: str = DEFAULT_LOCAL_STT_MODEL_ID,
) -> LocalSTTAssetManifest:
    manifest_path = current_runtime_layout().package_resource(
        *local_stt_manifest_relative_path(model_id).split("/")
    )
    if not manifest_path.is_file():
        raise LocalSTTModelMissingError(f"model manifest not found: {model_id}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    manifest = LocalSTTAssetManifest.from_dict(payload)
    if manifest.model_id != model_id:
        raise LocalSTTManifestInvalidError(
            f"manifest model_id mismatch: {manifest.model_id!r} != {model_id!r}"
        )
    return manifest


def load_installed_local_stt_manifest(
    model_dir: Path | None = None,
    *,
    manifest: LocalSTTAssetManifest | None = None,
) -> InstalledLocalSTTManifest | None:
    resolved_manifest = manifest or load_local_stt_asset_manifest()
    resolved_model_dir = model_dir or default_local_stt_model_dir(resolved_manifest.model_id)
    installed_path = resolved_model_dir / resolved_manifest.installed_manifest_filename
    if not installed_path.is_file():
        return None
    try:
        installed = InstalledLocalSTTManifest.from_dict(
            json.loads(installed_path.read_text(encoding="utf-8-sig"))
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise LocalSTTManifestInvalidError(
            f"invalid installed manifest: {installed_path}"
        ) from exc
    if installed.model_id != resolved_manifest.model_id:
        raise LocalSTTManifestInvalidError(
            f"installed manifest model_id mismatch: {installed.model_id!r}"
        )
    return installed


def validate_local_stt_install(
    model_dir: Path,
    *,
    manifest: LocalSTTAssetManifest,
    verify_checksums: bool = True,
) -> InstalledLocalSTTManifest:
    installed = load_installed_local_stt_manifest(model_dir, manifest=manifest)
    if installed is None:
        raise LocalSTTModelMissingError(f"model is not installed: {manifest.model_id}")
    for asset in manifest.files:
        asset_path = model_dir / asset.relative_path
        if not asset_path.is_file():
            raise LocalSTTManifestInvalidError(
                f"missing model file: {asset.relative_path}"
            )
        actual_size = asset_path.stat().st_size
        if actual_size != asset.size_bytes:
            raise LocalSTTManifestInvalidError(
                f"size mismatch for model file: {asset.relative_path} "
                f"({actual_size} != {asset.size_bytes})"
            )
        if verify_checksums and file_crc32(asset_path) != asset.crc32:
            raise LocalSTTManifestInvalidError(
                f"checksum mismatch for model file: {asset.relative_path}"
            )
    return installed


def inspect_local_stt_install_state(
    model_dir: Path,
    *,
    manifest: LocalSTTAssetManifest | None = None,
    verify_checksums: bool = False,
) -> LocalSTTInstallState:
    resolved_manifest = manifest or load_local_stt_asset_manifest()
    try:
        installed = validate_local_stt_install(
            model_dir,
            manifest=resolved_manifest,
            verify_checksums=verify_checksums,
        )
    except LocalSTTModelMissingError:
        return LocalSTTInstallState(status="missing")
    except LocalSTTManifestInvalidError:
        return LocalSTTInstallState(status="invalid")
    return LocalSTTInstallState(status="ready", installed_manifest=installed)


__all__ = [
    "DEFAULT_LOCAL_STT_MODEL_ID",
    "InstalledLocalSTTManifest",
    "LOCAL_STT_MODEL_FAMILIES",
    "LOCAL_STT_INSTALLED_MANIFEST_FILENAME",
    "LOCAL_STT_MANIFEST_RELATIVE_PATHS",
    "LOCAL_STT_MODEL_IDS",
    "LOCAL_STT_QUANTIZATIONS",
    "LocalSTTAssetError",
    "LocalSTTAssetFile",
    "LocalSTTAssetManifest",
    "LocalSTTAssetSource",
    "LocalSTTInstallState",
    "LocalSTTManifestInvalidError",
    "LocalSTTModelMissingError",
    "default_local_stt_installed_manifest_path",
    "default_local_stt_model_dir",
    "default_local_stt_model_root",
    "default_local_stt_source_for_locale",
    "file_crc32",
    "inspect_local_stt_install_state",
    "load_installed_local_stt_manifest",
    "load_local_stt_asset_manifest",
    "local_stt_manifest_relative_path",
    "normalize_crc32",
    "validate_local_stt_install",
]
