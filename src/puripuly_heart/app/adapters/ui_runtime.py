from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from puripuly_heart.app.language_selection import LanguageSelectionChange
from puripuly_heart.app.ports.settings_view import (
    AlibabaConnectionApplyResult,
    AlibabaConnectionDraftSnapshot,
    GeneralSettingsSnapshot,
    ImmediateSettingsIntent,
    OverlaySettingsSnapshot,
    PromptApplyIntent,
    ProviderApplyIntent,
    TranslationSelectionEdit,
)
from puripuly_heart.app.ports.ui_models import (
    GpuDeviceOption,
    GpuNoticeAction,
    ManagedGemmaNoticeAction,
    OverlayPeerPresentationState,
)
from puripuly_heart.app.services.application_after_launch import (
    ApplicationAfterLaunchOwner,
)
from puripuly_heart.app.services.canonical_settings_persistence import SettingsOwner
from puripuly_heart.app.services.capture.self_capture_application import (
    SelfCaptureApplicationOwner,
)
from puripuly_heart.app.services.desktop_overlay_application import (
    DesktopOverlayApplicationOwner,
)
from puripuly_heart.app.services.github_star_prompt import GithubStarPromptOwner
from puripuly_heart.app.services.gpu_runtime_interaction import GpuRuntimeInteractionOwner
from puripuly_heart.app.services.llm_connection_readiness import LlmConnectionReadinessOwner
from puripuly_heart.app.services.managed_gemma_translation import (
    ManagedGemmaTranslationOwner,
)
from puripuly_heart.app.services.manual_typing import ManualTypingOwner
from puripuly_heart.app.services.model_assets import ModelAssetsApplication
from puripuly_heart.app.services.overlay.overlay_application import OverlayApplicationOwner
from puripuly_heart.app.services.overlay.overlay_calibration_application import (
    OverlayCalibrationApplicationOwner,
)
from puripuly_heart.app.services.provider.alibaba_workspace import AlibabaWorkspaceOwner
from puripuly_heart.app.services.provider.provider_credential_verification import (
    ProviderCredentialVerificationInteractionOwner,
)
from puripuly_heart.app.services.provider.provider_settings import (
    ProviderApplicationOwner,
    ProviderSettingsOwner,
)
from puripuly_heart.app.services.settings.settings_application import (
    SettingsApplicationOwner,
    materialize_immediate_settings_intent,
    materialize_prompt_apply_intent,
    materialize_provider_apply_intent,
    settings_view_surface_snapshots,
)
from puripuly_heart.app.services.settings.settings_projection import (
    SettingsProjectionOwner,
)
from puripuly_heart.app.services.translation_enable import TranslationEnableOwner
from puripuly_heart.app.wiring.wiring_audio_test import AudioTestRuntime
from puripuly_heart.app.wiring.wiring_peer_application import PeerApplicationRuntime
from puripuly_heart.app.wiring.wiring_runtime_pipeline import RuntimePipelineHandle
from puripuly_heart.config.alibaba_connection import AlibabaEndpointMode, AlibabaRegion
from puripuly_heart.config.prompts import resolve_system_prompt
from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import provider_llm_for_translation
from puripuly_heart.core.http_extensions import (
    http_extension_secret_key_prefix,
)
from puripuly_heart.core.local_translation.devices import list_llama_vulkan_devices
from puripuly_heart.core.orchestrator.self_translation_channel import TextSubmissionAdmission


def _llama_gpu_device_options() -> tuple[GpuDeviceOption, ...]:
    return tuple(
        GpuDeviceOption(
            device_id=device.device_id,
            display_name=device.display_name,
            backend_name=device.device_id,
        )
        for device in list_llama_vulkan_devices()
    )


@dataclass(slots=True)
class UiInputRuntimeAdapter:
    pipeline: RuntimePipelineHandle
    manual_typing: ManualTypingOwner
    translation: TranslationEnableOwner
    self_capture: SelfCaptureApplicationOwner
    connection_readiness: LlmConnectionReadinessOwner | None = None

    async def submit_text(self, text: str) -> TextSubmissionAdmission | None:
        owner = self.pipeline.self_translation_channel
        submit = None if owner is None else lambda: owner.submit_text(text, source="You")
        return await self.manual_typing.submit(submit)

    def set_manual_input_activity(self, has_text: bool) -> None:
        self.manual_typing.set_input_activity(has_text)

    async def set_translation_enabled(
        self, enabled: bool, *, allow_authorization: bool = True
    ) -> object:
        result = await self.translation.set_enabled(
            enabled, allow_authorization=allow_authorization
        )
        self._sync_connection_readiness()
        return result

    async def set_stt_enabled(self, enabled: bool) -> object:
        result = await self.self_capture.set_enabled(enabled)
        self._sync_connection_readiness()
        return result

    def _sync_connection_readiness(self) -> None:
        if self.connection_readiness is not None:
            self.connection_readiness.sync()


@dataclass(slots=True)
class UiPeerCaptureRuntimeAdapter:
    peer: PeerApplicationRuntime
    overlay: OverlayApplicationOwner
    connection_readiness: LlmConnectionReadinessOwner | None = None

    async def set_peer_translation_enabled(self, enabled: bool) -> object:
        result = await self.peer.owner.set_enabled(enabled)
        if self.connection_readiness is not None:
            self.connection_readiness.sync()
        return result

    async def retry_peer_process_capture(self) -> bool:
        return await self.peer.owner.retry_process_capture()

    async def apply_loopback_capture_option(self, value: str) -> None:
        await self.peer.target.apply(value)

    def list_loopback_capture_options(self) -> object:
        return self.peer.target.options()

    def list_loopback_process_options(self) -> object:
        return self.peer.target.process_options()

    def list_loopback_device_options(self) -> object:
        return self.peer.target.device_options()

    def current_loopback_capture_option_value(self) -> object:
        return self.peer.target.current_value()

    def loopback_capture_summary(self) -> object:
        return self.peer.target.summary()

    def overlay_peer_presentation_state(self) -> OverlayPeerPresentationState | None:
        return self.overlay.presentation_state()


@dataclass(slots=True)
class UiAudioTestRuntimeAdapter:
    audio_test: AudioTestRuntime

    async def start_audio_test(self, *, observation_callback=None) -> bool:
        return await self.audio_test.start(observation_callback=observation_callback)

    def audio_test_snapshot(self) -> dict[str, object]:
        return self.audio_test.snapshot

    async def wait_audio_test_ready(self) -> bool:
        return await self.audio_test.wait_ready()

    async def stop_audio_test(self) -> None:
        await self.audio_test.stop()

    def release_audio_test_capture_restore(self, channel: str) -> None:
        self.audio_test.release_capture_restore(channel)


@dataclass(slots=True)
class UiOverlayRuntimeAdapter:
    overlay: OverlayApplicationOwner
    desktop: DesktopOverlayApplicationOwner
    calibration: OverlayCalibrationApplicationOwner

    async def set_overlay_enabled(self, enabled: bool) -> object:
        return await self.overlay.set_enabled(enabled)

    async def wait_overlay_transition(self) -> dict[str, object]:
        return {
            **await self.overlay.wait_start_outcome(),
            "calibration_draft_active": self.calibration.draft is not None,
        }

    async def set_desktop_overlay_captions_locked(self, locked: bool) -> dict[str, str]:
        return await self.desktop.set_captions_locked(locked)

    async def set_desktop_overlay_size_preset(self, size_preset: str) -> dict[str, str]:
        return await self.desktop.set_size_preset(size_preset)

    async def reset_desktop_overlay_position(self) -> dict[str, str]:
        return await self.desktop.reset_position()

    def begin_overlay_calibration(self) -> object:
        return self.calibration.begin()

    def set_overlay_calibration_field(
        self,
        field_name: object,
        value: object,
    ) -> object:
        return self.calibration.set_field(str(field_name), value)

    def apply_overlay_calibration(self) -> object:
        return self.calibration.apply()

    def cancel_overlay_calibration(self) -> object:
        return self.calibration.cancel()


@dataclass(slots=True)
class UiSettingsRuntimeAdapter:
    settings: SettingsOwner
    projection: SettingsProjectionOwner
    application: SettingsApplicationOwner
    merge_provider_settings: Callable[[AppSettingsVNext], AppSettingsVNext]

    async def on_dashboard_language_change(
        self,
        change: LanguageSelectionChange,
    ) -> None:
        await self.application.apply_language_selection(change)

    def capture_settings_view_change(self, settings: AppSettingsVNext) -> object:
        return self.projection.capture(settings)

    def merge_settings_view_change_with_current(self, captured: object) -> AppSettingsVNext:
        return self.projection.merge_with_current(captured)

    def refresh_settings_projection(
        self,
        *,
        preserve_custom_vocab_draft: bool = False,
    ) -> bool:
        settings = self.settings.canonical
        if settings is None:
            return False
        provider, general, prompt, overlay = settings_view_surface_snapshots(settings)
        return bool(
            self.projection.render_surfaces(
                provider=provider,
                general=general,
                prompt=prompt,
                overlay=overlay,
                compatibility_settings=settings,
                preserve_custom_vocab_draft=preserve_custom_vocab_draft,
            )
        )

    def refresh_settings_after_openrouter_pkce_success(self) -> bool:
        settings = self.settings.canonical
        if settings is None:
            return False
        provider, _general, prompt, _overlay = settings_view_surface_snapshots(settings)
        return bool(
            self.projection.refresh_after_openrouter_pkce_success(
                provider=provider,
                prompt=prompt,
                compatibility_settings=settings,
            )
        )

    def merge_settings_tab_apply_with_current_languages(
        self,
        settings: AppSettingsVNext,
    ) -> AppSettingsVNext:
        return self.merge_provider_settings(settings)

    async def apply_settings(
        self,
        settings: AppSettingsVNext,
        *,
        provider_requested: bool = False,
    ) -> object:
        return await self.application.apply(settings, provider_requested=provider_requested)


    async def apply_settings_intent(self, intent: ImmediateSettingsIntent) -> object:
        settings = self.settings.canonical
        if settings is None:
            return False
        return await self.application.apply(materialize_immediate_settings_intent(settings, intent))

    async def retry_settings_runtime(self) -> bool:
        settings = self.settings.require_committed()
        runtime = self.application.provider_runtime
        plan = runtime.build_plan(settings, force_rebuild_llm=False) if runtime else None
        return await self.application.apply_direct(
            settings,
            persist=False,
            provider_plan=plan,
        )

    async def apply_prompt_intent(self, intent: PromptApplyIntent) -> object:
        settings = self.settings.canonical
        if settings is None:
            return False
        return await self.application.apply(materialize_prompt_apply_intent(settings, intent))

    def materialize_provider_intent(self, intent: ProviderApplyIntent) -> object:
        settings = self.settings.canonical
        if settings is None:
            raise RuntimeError("settings owner has no compatibility settings")
        return materialize_provider_apply_intent(
            settings,
            intent,
            materialize_translation=self.settings.materialize_translation,
        )

    def overlay_snapshot(self) -> OverlaySettingsSnapshot | None:
        settings = self.settings.canonical
        if settings is None:
            return None
        return settings_view_surface_snapshots(settings)[3]

    def general_snapshot(self) -> GeneralSettingsSnapshot | None:
        settings = self.settings.canonical
        if settings is None:
            return None
        return settings_view_surface_snapshots(settings)[1]


@dataclass(slots=True)
class UiProviderRuntimeAdapter:
    settings: SettingsOwner
    provider_application: ProviderApplicationOwner
    gpu: GpuRuntimeInteractionOwner
    credential_verification: ProviderCredentialVerificationInteractionOwner
    provider_settings: ProviderSettingsOwner
    managed_gemma: ManagedGemmaTranslationOwner | None = None
    llm_devices_sink: Callable[[tuple[GpuDeviceOption, ...]], None] | None = None
    connection_readiness: LlmConnectionReadinessOwner | None = None
    model_assets: ModelAssetsApplication | None = None
    _alibaba_workspace: AlibabaWorkspaceOwner | None = field(default=None, init=False)

    def alibaba_workspace(self) -> AlibabaWorkspaceOwner:
        if self._alibaba_workspace is None:
            self._alibaba_workspace = AlibabaWorkspaceOwner(
                self.settings,
                self.provider_settings,
                self.credential_verification.verification_owner.verifier,
            )
        return self._alibaba_workspace

    async def begin_alibaba_connection_draft(self) -> AlibabaConnectionDraftSnapshot:
        return await self.alibaba_workspace().begin()

    async def alibaba_connection_draft(self) -> AlibabaConnectionDraftSnapshot:
        return await self.alibaba_workspace().read()

    async def alibaba_active_connection(self) -> AlibabaConnectionDraftSnapshot:
        return await self.alibaba_workspace().active()

    async def edit_alibaba_connection_draft(
        self,
        *,
        token: str,
        region: AlibabaRegion | None = None,
        endpoint_mode: AlibabaEndpointMode | None = None,
        api_host: str | None = None,
    ) -> AlibabaConnectionDraftSnapshot:
        return await self.alibaba_workspace().edit(
            token=token,
            region=region,
            endpoint_mode=endpoint_mode,
            api_host=api_host,
        )

    async def verify_alibaba_connection_draft(
        self,
        *,
        token: str,
        capability: Literal["asr", "translation", "both"],
        api_key: str | None = None,
    ) -> AlibabaConnectionDraftSnapshot:
        return await self.alibaba_workspace().verify(
            token=token,
            capability=capability,
            api_key=api_key,
        )

    async def apply_alibaba_connection_draft(self, *, token: str) -> AlibabaConnectionApplyResult:
        affected = (await self.alibaba_workspace().read()).affected_consumers
        committed = await self.alibaba_workspace().apply(
            token=token, apply_settings=self.apply_providers
        )
        return AlibabaConnectionApplyResult(
            committed=bool(committed),
            affected_consumers=affected,
            transaction=self.provider_application.results.current,
        )

    def cancel_alibaba_connection_draft(self, *, token: str) -> None:
        self.alibaba_workspace().cancel(token=token)

    async def apply_providers(
        self,
        settings: AppSettingsVNext | None = None,
        *,
        force_rebuild_llm: bool = False,
        persist_settings: bool = True,
        refresh_ui: bool = True,
    ) -> object:
        if persist_settings:
            return await self.provider_application.apply(
                settings,
                force_rebuild_llm=force_rebuild_llm,
                refresh_ui=refresh_ui,
            )
        return await self.provider_application.apply(
            settings,
            force_rebuild_llm=force_rebuild_llm,
            persist_settings=persist_settings,
            refresh_ui=refresh_ui,
        )

    async def install_selected_gpu_model_if_needed(self) -> None:
        await self.gpu.install_selected_model_if_needed()

    async def ensure_gpu_device_discovery(self) -> None:
        await self.gpu.ensure_device_discovery(
            force=False,
            origin="settings",
        )
        sink = self.llm_devices_sink
        if sink is None:
            return
        sink(await asyncio.to_thread(_llama_gpu_device_options))

    async def verify_api_key(self, provider: str, key: str) -> tuple[bool, str]:
        return await self.credential_verification.verify(provider, key)

    def persist_api_key_verification(
        self,
        provider: str,
        key: str,
        success: bool,
    ) -> None:
        self.provider_settings.persist_verification(provider, key, success)

    async def persist_provider_secret_change(self, key: str, value: str) -> bool:
        succeeded = await self.provider_settings.change_secret(key, value)
        if succeeded:
            rebind = getattr(self.provider_application, "rebind_rolling_stt_secret", None)
            if callable(rebind):
                rebind(key, value)
        if succeeded and key in {"alibaba_api_key_beijing", "alibaba_api_key_singapore"}:
            await self.apply_providers(persist_settings=False)
        current = self.settings.canonical
        if succeeded and current is not None:
            translation = current.intent.translation
            active_secret = None
            if translation.connection == "official_byok":
                active_secret = {
                    "gemini": "google_api_key",
                    "openai": "openai_api_key",
                    "deepseek": "deepseek_api_key",
                }.get(provider_llm_for_translation(translation.model, translation.connection))
            elif (
                translation.connection == "openrouter"
                and translation.openrouter_selected_source == "byok"
            ):
                active_secret = "openrouter_api_key"
            if key == active_secret:
                await self.apply_providers(
                    force_rebuild_llm=True,
                    persist_settings=False,
                )
                return succeeded
        http_extension_id = (
            None if current is None else current.intent.translation.http_extension_id
        )
        if (
            succeeded
            and key.startswith("http_extension.")
            and current is not None
            and current.intent.translation.model == "custom_http"
            and http_extension_id is not None
            and key.startswith(http_extension_secret_key_prefix(http_extension_id))
        ):
            await self.apply_providers(
                force_rebuild_llm=True,
                persist_settings=False,
            )
        return succeeded

    def clear_provider_verification(self, provider: str) -> None:
        self.provider_settings.persist_verification(provider, "", False)

    def handle_gpu_notice_action(self, action: GpuNoticeAction) -> object:
        return self.gpu.handle_notice_action(action)

    async def handle_managed_gemma_notice_action(
        self,
        action: ManagedGemmaNoticeAction,
    ) -> object:
        if action == "cancel":
            return False if self.managed_gemma is None else self.managed_gemma.cancel()
        raise ValueError(f"unsupported managed Gemma notice action: {action}")

    def model_asset_entries(self) -> tuple[object, ...]:
        return self.model_assets.entries() if self.model_assets is not None else ()

    async def refresh_model_assets(self) -> tuple[object, ...]:
        if self.model_assets is None:
            return ()
        return await self.model_assets.refresh()

    async def download_model_asset(self, model_id: str, *, source: str | None = None) -> object:
        return await self._require_model_assets().download(model_id, source=source)

    async def import_model_asset(self, model_id: str, directory: Path) -> object:
        return await self._require_model_assets().import_directory(model_id, directory)

    async def cancel_model_asset(self, model_id: str) -> bool:
        return await self._require_model_assets().cancel(model_id)

    async def delete_model_asset(self, model_id: str) -> object:
        return await self._require_model_assets().delete(model_id)

    async def search_model_repos(self, query: str, source: str) -> tuple[object, ...]:
        return await self._require_model_assets().search_hub(query, source)

    async def list_model_repos(self, owner: str, source: str) -> tuple[object, ...]:
        return await self._require_model_assets().list_model_repos(owner, source)

    async def install_catalog_model(self, model_id: str, quant: str) -> object:
        return await self._require_model_assets().install_catalog_model(model_id, quant)

    async def model_repo_files(self, repo: object) -> tuple[object, ...]:
        return await self._require_model_assets().hub_files(repo)

    async def install_model_file(self, repo: object, file: object) -> object:
        return await self._require_model_assets().install_hub_file(repo, file)

    async def add_model_file(self, path: Path) -> object:
        return await self._require_model_assets().add_local_file(Path(path))

    @property
    def model_operation_progress(self) -> tuple[str, str, int | None] | None:
        return (
            self.model_assets.operation_progress
            if self.model_assets is not None
            else None
        )

    def _require_model_assets(self) -> ModelAssetsApplication:
        if self.model_assets is None:
            raise RuntimeError("Model assets are unavailable")
        return self.model_assets


@dataclass(slots=True)
class UiEngagementRuntimeAdapter:
    settings: SettingsOwner
    settings_application: SettingsApplicationOwner
    github_prompt: GithubStarPromptOwner
    after_launch: ApplicationAfterLaunchOwner

    def get_event_language_codes(self) -> tuple[str | None, str | None]:
        settings = self.settings.canonical
        if settings is None:
            return None, None
        return (
            settings.intent.languages.source_language,
            settings.intent.languages.target_language,
        )

    def schedule_github_star_prompt_translation_success_observed(self) -> None:
        self.github_prompt.schedule_translation_success_observed()


    def should_show_github_star_prompt(self) -> bool:
        return self.github_prompt.should_show()

    async def persist_github_star_prompt_eligible_launch(self) -> bool:
        return await self.github_prompt.persist_eligible_launch()

    async def prepare_runtime_after_launch(self) -> None:
        await self.after_launch.prepare()

    async def persist_github_star_prompt_opened(
        self,
        *,
        should_open: Callable[[], bool] | None = None,
    ) -> bool:
        return await self.github_prompt.persist_opened(should_open=should_open)

    async def persist_github_star_prompt_clicked(self) -> None:
        await self.github_prompt.persist_clicked()


@dataclass(slots=True)
class UiDiagnosticsRuntimeAdapter:
    cycle_capture_fault: Callable[[], str]
    cycle_stt_fault: Callable[[], str]
    clear_audio_faults: Callable[[], None]

    def cycle_debug_capture_fault_profile(self) -> str:
        return self.cycle_capture_fault()

    def cycle_debug_stt_fault_profile(self) -> str:
        return self.cycle_stt_fault()

    def clear_debug_audio_fault_profiles(self) -> None:
        self.clear_audio_faults()
