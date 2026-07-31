from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Literal

import httpx

from puripuly_heart.core.local_stt_assets import (
    InstalledLocalSTTManifest,
    LocalSTTAssetError,
    LocalSTTAssetManifest,
    default_local_stt_source_for_locale,
    validate_local_stt_install,
)

RuntimeLocalSTTStatus = Literal["downloading", "ready", "download_failed"]


@dataclass(slots=True, frozen=True)
class RuntimeLocalSTTStatusUpdate:
    status: RuntimeLocalSTTStatus
    percent: int | None = None


StatusCallback = Callable[[RuntimeLocalSTTStatusUpdate], Awaitable[None] | None]


class LocalSTTRuntimeInstallError(LocalSTTAssetError):
    """Raised when runtime local STT provisioning fails."""


class LocalSTTRuntimeInstallCancelled(LocalSTTAssetError):
    """Raised when runtime local STT provisioning is cancelled."""


async def _emit_status(
    on_status: StatusCallback | None,
    status: RuntimeLocalSTTStatus,
    *,
    percent: int | None = None,
) -> None:
    if on_status is None:
        return
    result = on_status(RuntimeLocalSTTStatusUpdate(status=status, percent=percent))
    if inspect.isawaitable(result):
        await result


class _DownloadProgress:
    def __init__(self, total_bytes: int) -> None:
        self._total_bytes = max(total_bytes, 0)
        self._downloaded_bytes = 0
        self._lock = threading.Lock()

    def add(self, size_bytes: int) -> None:
        if size_bytes <= 0:
            return
        with self._lock:
            self._downloaded_bytes += size_bytes

    def percent(self) -> int:
        if self._total_bytes <= 0:
            return 0
        with self._lock:
            downloaded_bytes = self._downloaded_bytes
        return min(99, int(downloaded_bytes * 100 / self._total_bytes))


def _source_order(
    manifest: LocalSTTAssetManifest,
    *,
    preferred_source: str | None,
    locale: str | None,
) -> tuple[str, ...]:
    selected = preferred_source or default_local_stt_source_for_locale(locale)
    names: list[str] = []
    if selected in manifest.sources:
        names.append(selected)
    for name in manifest.sources:
        if name not in names:
            names.append(name)
    return tuple(names[:2])


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise LocalSTTRuntimeInstallCancelled("runtime local STT install cancelled")


def _download_source_into_staging(
    *,
    source_name: str,
    staging_dir: Path,
    manifest: LocalSTTAssetManifest,
    cancel_event: threading.Event | None = None,
    progress: _DownloadProgress | None = None,
) -> InstalledLocalSTTManifest:
    try:
        _raise_if_cancelled(cancel_event)
        source = manifest.sources[source_name]
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for asset in manifest.files:
                _raise_if_cancelled(cancel_event)
                asset_path = staging_dir / asset.relative_path
                asset_path.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size_bytes = 0
                url = source.download_url_template.format(
                    path=asset.remote_path_for_source(source_name)
                )
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with asset_path.open("wb") as handle:
                        for chunk in response.iter_bytes():
                            _raise_if_cancelled(cancel_event)
                            if not chunk:
                                continue
                            handle.write(chunk)
                            digest.update(chunk)
                            size_bytes += len(chunk)
                            if progress is not None:
                                progress.add(len(chunk))
                if digest.hexdigest() != asset.sha256:
                    raise LocalSTTRuntimeInstallError(
                        f"checksum mismatch for required model file: {asset.relative_path}"
                    )
                if asset.size_bytes is not None and size_bytes != asset.size_bytes:
                    raise LocalSTTRuntimeInstallError(
                        f"size mismatch for required model file: {asset.relative_path}"
                    )

        installed = InstalledLocalSTTManifest(
            manifest_version=manifest.installed_manifest_version,
            model_id=manifest.model_id,
            engine=manifest.engine,
            install_dirname=manifest.install_dirname,
            selected_source=source_name,
            selected_revision=source.revision,
        )
        (staging_dir / manifest.installed_manifest_filename).write_text(
            json.dumps(installed.to_dict(), indent=2),
            encoding="utf-8",
        )
        validate_local_stt_install(staging_dir, manifest=manifest)
        return installed
    except LocalSTTRuntimeInstallCancelled:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def _promote_staging_install(
    *,
    staging_dir: Path,
    install_dir: Path,
    cancel_event: threading.Event | None = None,
) -> None:
    _raise_if_cancelled(cancel_event)
    backup_dir = install_dir.with_name(f"{install_dir.name}.backup")
    install_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(backup_dir, ignore_errors=True)

    had_existing_install = install_dir.exists()
    if had_existing_install:
        install_dir.rename(backup_dir)

    try:
        staging_dir.rename(install_dir)
    except Exception:
        if install_dir.exists():
            shutil.rmtree(install_dir, ignore_errors=True)
        if had_existing_install and backup_dir.exists():
            backup_dir.rename(install_dir)
        raise
    else:
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
