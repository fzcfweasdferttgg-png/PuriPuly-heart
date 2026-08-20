"""Local STT lifecycle manager — install state, download, warmup.

Decoupled from UI layer. Manages the state machine:
  ready → missing → downloading → ready/failed
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from puripuly_heart.domain.providers import STTProviderName
from puripuly_heart.domain.stt_events import STTError
from puripuly_heart.core.local_stt_assets import (
    LocalSTTInstallState,
    LocalSTTManifestInvalidError,
    LocalSTTModelMissingError,
    inspect_local_stt_install_state,
)

if TYPE_CHECKING:
    from puripuly_heart.config.settings import AppSettings
    from puripuly_heart.core.pipeline.pipeline import Pipeline

logger = logging.getLogger(__name__)

LOCAL_STT_PROVIDERS = frozenset({
    STTProviderName.LOCAL_QWEN,
    STTProviderName.LOCAL_QWEN_17B,
    STTProviderName.LOCAL_GIGAAM_RNNT,
    STTProviderName.LOCAL_PARAKEET_TDT,
    STTProviderName.LOCAL_GIGAAM_RNNT_GGUF,
    STTProviderName.LOCAL_PARAKEET_TDT_GGUF,
    STTProviderName.LOCAL_QWEN3_ASR_GGUF,
    STTProviderName.LOCAL_QWEN_17B_GGUF,
})


@dataclass
class LocalSTTManager:
    """Manages local STT install/download/warmup lifecycle.

    State machine:
        ready → (model missing) → missing
        missing → (download start) → downloading
        downloading → (success) → ready
        downloading → (fail/cancel) → download_failed

    Does NOT know about Flet/UI. Uses callbacks for UI side-effects.
    """

    hub: Pipeline
    config_path: object  # Path
    models_dir: Path = field(default_factory=lambda: Path("."))
    peer_stt_backend_factory: Callable[..., object] | None = None

    # Callbacks
    on_status_change: Callable[[str], None] | None = None
    on_notice_update: Callable[[], None] | None = None
    on_error: Callable[[str], None] | None = None
    on_stt_toggle_update: Callable[[bool], None] | None = None

    # Callback: controller passes self._peer_translation_activation_requested_for
    # to preserve EULA + multi-condition check (not just ui.peer_translation_enabled).
    peer_translation_requested_resolver: Callable[[AppSettings], bool] | None = None

    # State
    _install_state: LocalSTTInstallState = field(
        default_factory=lambda: LocalSTTInstallState(status="ready")
    )
    _runtime_status: str = field(default="ready")
    _download_origin: str | None = field(default=None)
    _download_percent: int | None = field(default=None)
    _download_task: asyncio.Task[object] | None = field(default=None, repr=False)
    _download_cancel_event: threading.Event | None = field(default=None, repr=False)
    _pending_enable_after_install: bool = field(default=False)
    _pending_peer_enable_after_install: bool = field(default=False)

    @property
    def install_state(self) -> LocalSTTInstallState:
        return self._install_state

    @property
    def runtime_status(self) -> str:
        return self._runtime_status

    @property
    def download_percent(self) -> int | None:
        return self._download_percent

    @property
    def is_downloading(self) -> bool:
        return self._runtime_status == "downloading"

    def is_local_provider(self, provider: STTProviderName) -> bool:
        return provider in LOCAL_STT_PROVIDERS

    def current_status(self) -> str:
        """Resolve effective status considering download state."""
        if self._runtime_status in ("downloading", "download_failed"):
            return self._runtime_status
        return self._install_state.status

    def peer_local_stt_requested(self, settings: AppSettings) -> bool:
        """Check if peer STT uses a local provider.

        Uses peer_translation_requested_resolver callback to preserve
        EULA + multi-condition check from PeerFlagsMixin.
        """
        if self.peer_translation_requested_resolver is None:
            return bool(
                settings.provider.peer_stt in LOCAL_STT_PROVIDERS
                and settings.ui.peer_translation_enabled
            )
        return bool(
            settings.provider.peer_stt in LOCAL_STT_PROVIDERS
            and self.peer_translation_requested_resolver(settings)
        )

    def reset_pending_enable(self) -> None:
        self._pending_enable_after_install = False

    def set_pending_enable(self, value: bool = True) -> None:
        self._pending_enable_after_install = value

    def reset_pending_peer_enable(self) -> None:
        self._pending_peer_enable_after_install = False

    def clear_pending_if_provider_switched_away(self, settings: AppSettings) -> None:
        if settings.provider.stt not in LOCAL_STT_PROVIDERS:
            self.reset_pending_enable()
        if not self.peer_local_stt_requested(settings):
            self.reset_pending_peer_enable()

    def refresh_runtime_state(self) -> None:
        """Reset install state, sync notice — called on startup."""
        self._install_state = LocalSTTInstallState(status="ready")
        if self._runtime_status not in ("downloading", "download_failed"):
            self._runtime_status = "ready"
        self._sync_notice(None)

    def handle_unavailable(
        self,
        status: str,
        *,
        resume_self: bool,
        resume_peer: bool,
        settings: AppSettings | None = None,
    ) -> None:
        """Handle missing/invalid local STT model.

        Returns void — controller must apply UI side-effects in its wrapper.
        """
        if status in ("missing", "invalid"):
            self._install_state = LocalSTTInstallState(status=status)
        if self._runtime_status != "downloading":
            self._runtime_status = status
            self._download_percent = None
        if resume_self:
            self._pending_enable_after_install = True
        if resume_peer:
            self._pending_peer_enable_after_install = True
        self._sync_notice(settings)
        if self.on_stt_toggle_update is not None and resume_self:
            self.on_stt_toggle_update(False)

    async def ensure_ready(self, settings: AppSettings) -> bool:
        """Check + warmup local STT. Returns True if ready.

        Returns False on any failure — controller must apply
        UI side-effects (_stt_desired, dashboard toggle, snackbar).
        """
        if settings.provider.stt not in LOCAL_STT_PROVIDERS:
            return True
        if self.hub is None or self.hub.stt is None:
            self._install_state = LocalSTTInstallState(status="missing")
            self._runtime_status = "missing"
            self._sync_notice(settings)
            return False

        try:
            from puripuly_heart.core.local_stt_assets import (
                default_local_stt_model_dir,
                load_local_stt_asset_manifest,
                resolve_model_id,
            )

            model_id = resolve_model_id(
                settings.provider.stt.value, settings.provider.stt_quant
            )
            if model_id is None:
                self._install_state = LocalSTTInstallState(status="missing")
                self._runtime_status = "missing"
                self._sync_notice(settings)
                return False

            model_dir = default_local_stt_model_dir(model_id, data_dir=self.models_dir)
            manifest = load_local_stt_asset_manifest(model_id)
            install_state = inspect_local_stt_install_state(model_dir, manifest=manifest)
            if install_state.status != "ready":
                raise LocalSTTModelMissingError(f"model status: {install_state.status} (path: {model_dir})")
        except (LocalSTTModelMissingError, LocalSTTManifestInvalidError) as exc:
            logger.error("Local STT model not available: %s", exc)
            self._install_state = LocalSTTInstallState(status="missing")
            self._runtime_status = "missing"
            self._sync_notice(settings)
            return False

        try:
            if not await self.hub.stt.warmup():
                raise STTError("STT warmup returned False")
            self._install_state = LocalSTTInstallState(status="ready")
            if self._runtime_status != "downloading":
                self._runtime_status = "ready"
            self._sync_notice(settings)
            return True
        except (LocalSTTModelMissingError, LocalSTTManifestInvalidError, STTError) as exc:
            logger.error("Local STT warmup failed: %s", exc)
            return False

    async def ensure_peer_ready(self, settings: AppSettings) -> bool:
        """Check + warmup peer local STT. Returns True if ready."""
        if settings.provider.peer_stt not in LOCAL_STT_PROVIDERS:
            return True

        try:
            from puripuly_heart.core.local_stt_assets import (
                default_local_stt_model_dir,
                load_local_stt_asset_manifest,
                resolve_model_id,
            )

            model_id = resolve_model_id(
                settings.provider.peer_stt.value,
                settings.provider.peer_stt_quant,
            )
            if model_id is None:
                logger.error("Peer local STT: quant not selected")
                return False

            model_dir = default_local_stt_model_dir(model_id, data_dir=self.models_dir)
            manifest = load_local_stt_asset_manifest(model_id)
            install_state = inspect_local_stt_install_state(model_dir, manifest=manifest)
            if install_state.status != "ready":
                logger.error("Peer local STT model not available: %s", install_state.status)
                return False
        except (LocalSTTModelMissingError, LocalSTTManifestInvalidError) as exc:
            logger.error("Peer local STT model check failed: %s", exc)
            return False

        try:
            await self._probe_peer_local_stt_backend()
            self._install_state = LocalSTTInstallState(status="ready")
            if self._runtime_status != "downloading":
                self._runtime_status = "ready"
            self._sync_notice(settings)
            return True
        except (LocalSTTModelMissingError, LocalSTTManifestInvalidError, STTError) as exc:
            logger.error("Peer local STT warmup failed: %s", exc)
            return False

    async def _probe_peer_local_stt_backend(self) -> None:
        """Probe peer STT backend — open+close session to verify load."""
        if self.peer_stt_backend_factory is None:
            raise STTError("peer_stt_backend_factory not configured")
        peer_backend = self.peer_stt_backend_factory()
        session = None
        try:
            session = await peer_backend.open_session()
        finally:
            if session is not None:
                with contextlib.suppress(Exception):
                    await session.close()
            close_backend = getattr(peer_backend, "close", None)
            if callable(close_backend):
                with contextlib.suppress(Exception):
                    await close_backend()

    async def cancel_download(self) -> None:
        """Cancel any in-progress download."""
        task = self._download_task
        cancel_event = self._download_cancel_event
        self.reset_pending_enable()
        self.reset_pending_peer_enable()
        if cancel_event is not None:
            cancel_event.set()
        if task is None:
            self._download_cancel_event = None
            return
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._download_task = None
        self._download_cancel_event = None

    def _sync_notice(self, settings: AppSettings | None) -> None:
        status = self.current_status()
        should_show = status == "downloading" or (
            settings is not None
            and (
                settings.provider.stt in LOCAL_STT_PROVIDERS
                or self.peer_local_stt_requested(settings)
            )
            and status != "ready"
        )
        if self.on_notice_update is not None:
            self.on_notice_update()
