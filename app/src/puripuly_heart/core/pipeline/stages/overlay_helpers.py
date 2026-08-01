"""Overlay helper methods extracted from Pipeline into a mixin."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from puripuly_heart.core.runtime_logging import SessionLoggingMode
from puripuly_heart.domain.models import Translation

if TYPE_CHECKING:
    from uuid import UUID

    from puripuly_heart.core.pipeline.channel_runtime import (
        ChannelRuntime,
        _MergeBuffer,
    )
    from puripuly_heart.core.pipeline.context import ContextMode
    from puripuly_heart.domain.models import ChannelId, Transcript


logger = logging.getLogger(__name__)


class OverlayHelpersMixin:
    """Mixin providing overlay-related helper methods for Pipeline.

    All methods reference ``self.xxx`` attributes that live on the host
    class (overlay_sink, overlay_event_adapter, clock, etc.).  The host
    class must define those attributes — this mixin deliberately has no
    ``__init__``.
    """

    # ------------------------------------------------------------------
    # Overlay event emission
    # ------------------------------------------------------------------

    async def _emit_final_transcript_to_overlay(self, transcript: Transcript) -> None:
        if self.overlay_sink is None:
            return
        source_language, target_language = self._self_overlay_languages_for_utterance(
            transcript.utterance_id
        )
        await self._emit_overlay_event(
            self.overlay_event_adapter.transcript_final(
                transcript,
                source_language=source_language,
                target_language=target_language,
            )
        )

    async def _finalize_peer_source_only(
        self,
        transcript: Transcript,
        *,
        close_is_final: bool,
        finalize_latency: bool,
        preserve_parent_speech_end_time: bool = False,
    ) -> None:
        if self.overlay_sink is not None:
            self._record_overlay_emit(
                event_kind="peer_transcript_final",
                utterance_id=transcript.utterance_id,
                channel="peer",
                secondary_len=len(transcript.text.strip()),
            )
            self._latency._record_latency_stage(
                channel="peer",
                utterance_id=transcript.utterance_id,
                stage="peer_overlay_first_emit",
                overwrite=False,
            )
            await self._emit_overlay_event(
                self.overlay_event_adapter.transcript_final(
                    transcript,
                    source_language=self._source_language_for(self.peer_runtime),
                    target_language=self._target_language_for(self.peer_runtime),
                )
            )
        await self._emit_overlay_utterance_closed(
            utterance_id=transcript.utterance_id,
            channel="peer",
            is_final=close_is_final,
            finalize_latency=finalize_latency,
        )
        self._complete_peer_logical_turn(
            transcript.utterance_id,
            preserve_parent_speech_end_time=preserve_parent_speech_end_time,
        )

    async def _emit_overlay_utterance_closed(
        self,
        *,
        utterance_id: UUID,
        channel: ChannelId,
        is_final: bool,
        finalize_latency: bool | None = None,
    ) -> None:
        if self.overlay_sink is None:
            if finalize_latency is True or (finalize_latency is None and channel == "peer"):
                self._latency._finalize_latency_timeline(
                    runtime=self._runtime_for_channel(channel), channel=channel, utterance_id=utterance_id,
                )
            return
        await self._emit_overlay_event(
            self.overlay_event_adapter.utterance_closed(
                utterance_id=utterance_id,
                channel=channel,
                is_final=is_final,
            )
        )
        if finalize_latency is True or (finalize_latency is None and channel == "peer"):
            self._latency._finalize_latency_timeline(
                runtime=self._runtime_for_channel(channel), channel=channel, utterance_id=utterance_id,
            )

    async def _emit_translation_to_overlay(
        self,
        *,
        translation: Translation,
        applied_context_mode: ContextMode | None,
    ) -> None:
        if self.overlay_sink is None:
            return

        self._record_overlay_emit(
            event_kind="translation_final",
            utterance_id=translation.utterance_id,
            channel=translation.channel,
            secondary_len=len(translation.text.strip()),
        )
        await self._emit_overlay_event(
            self.overlay_event_adapter.translation_final(
                utterance_id=translation.utterance_id,
                channel=translation.channel,
                text=translation.text,
                source_language=self._language_or_fallback(
                    translation.source_language,
                    self.source_language,
                ),
                target_language=self._language_or_fallback(
                    translation.target_language,
                    self.target_language,
                ),
                applied_context_mode=applied_context_mode,
                created_at=translation.created_at,
                **self._translation_overlay_metadata(translation),
            )
        )

    async def _emit_peer_translation_to_overlay(
        self,
        *,
        translation: Translation,
        runtime: ChannelRuntime,
        applied_context_mode: ContextMode | None,
    ) -> None:
        if self.overlay_sink is None:
            return

        self._record_overlay_emit(
            event_kind="translation_final",
            utterance_id=translation.utterance_id,
            channel=translation.channel,
            secondary_len=len(translation.text.strip()),
        )
        self._latency._record_latency_stage(
            channel=runtime.channel,
            utterance_id=translation.utterance_id,
            stage="peer_overlay_first_emit",
            overwrite=False,
        )
        await self._emit_overlay_event(
            self.overlay_event_adapter.translation_final(
                utterance_id=translation.utterance_id,
                channel=translation.channel,
                text=translation.text,
                source_text=translation.source_text,
                source_language=self._language_or_fallback(
                    translation.source_language,
                    self._source_language_for(runtime),
                ),
                target_language=self._language_or_fallback(
                    translation.target_language,
                    self._target_language_for(runtime),
                ),
                applied_context_mode=applied_context_mode,
                created_at=translation.created_at,
                **self._translation_overlay_metadata(translation),
            )
        )

    async def _emit_overlay_event(self, event: object) -> None:
        if self.overlay_sink is None:
            return
        detailed_mode = (
            self.runtime_logging is not None
            and self.runtime_logging.mode is SessionLoggingMode.DETAILED
        )
        start = time.perf_counter() if detailed_mode else 0.0
        try:
            await self.overlay_sink.emit(event)  # type: ignore[arg-type]
        except Exception as exc:
            self.last_error_source = "overlay_sink"
            self._emit_exception_summary(
                "[Hub] Overlay sink emit failed: %s",
                exc,
                level=logging.ERROR,
            )
            return
        if detailed_mode:
            elapsed_ms = max(0, int((time.perf_counter() - start) * 1000))
            event_type = type(event).__name__
            channel = getattr(event, "channel", None)
            utterance_id = getattr(event, "utterance_id", None)
            update_id = getattr(event, "update_id", None)
            self.runtime_logging.emit_detailed_lazy(
                lambda: (
                    "[Detailed][Hub] overlay_sink_emit_duration "
                    f"event_type={event_type} "
                    f"channel={channel} "
                    f"utterance_id={utterance_id} "
                    f"update_id={update_id} "
                    f"elapsed_ms={elapsed_ms}"
                )
            )

    async def _emit_self_active_overlay_event(self, event: object) -> None:
        await self._emit_overlay_event(event)

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def _overlay_translation_will_follow(self, runtime: ChannelRuntime) -> bool:
        return (
            self.overlay_sink is not None
            and self.llm is not None
            and self._translation_enabled_for_runtime(runtime)
        )

    def _peer_terminal_work_will_follow(self, runtime: ChannelRuntime) -> bool:
        if runtime.channel != "peer":
            return False
        return (self.llm is not None and self._translation_enabled_for_runtime(runtime)) or (
            self._should_publish_to_chatbox(runtime)
        )

    # ------------------------------------------------------------------
    # Static / pure helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _translation_overlay_metadata(translation: Translation) -> dict[str, object]:
        return {
            "update_id": translation.update_id,
            "origin_wall_clock_ms": translation.origin_wall_clock_ms,
            "session_scope": translation.session_scope,
            "source_text_hash": translation.source_text_hash,
            "source_text_len": translation.source_text_len,
            "logical_turn_key": translation.logical_turn_key,
        }

    @staticmethod
    def _language_or_fallback(language: str | None, fallback: str) -> str:
        if language is not None and language.strip():
            return language
        return fallback

    @staticmethod
    def _metadata_language(metadata: object | None, field_name: str) -> str | None:
        value = getattr(metadata, field_name, None)
        if not isinstance(value, str):
            return None
        return value

    # ------------------------------------------------------------------
    # Language resolution
    # ------------------------------------------------------------------

    def _active_self_display_languages_for_utterance(
        self,
        utterance_id: UUID,
    ) -> tuple[str | None, str | None]:
        metadata = self._current_active_self_metadata()
        if metadata is None:
            return None, None
        if getattr(metadata, "utterance_id", None) != utterance_id:
            return None, None
        if getattr(metadata, "occupant_key", None) != f"self:{utterance_id}":
            return None, None
        return (
            self._metadata_language(metadata, "primary_language"),
            self._metadata_language(metadata, "secondary_language"),
        )

    def _self_overlay_languages_for_utterance(self, utterance_id: UUID) -> tuple[str, str]:
        primary_language, secondary_language = self._active_self_display_languages_for_utterance(
            utterance_id
        )
        return (
            self._language_or_fallback(primary_language, self.source_language),
            self._language_or_fallback(secondary_language, self.target_language),
        )

    def _active_self_overlay_languages(
        self,
        *,
        buffer: _MergeBuffer,
        source: str,
        secondary_text: str,
        current_metadata: object | None,
    ) -> tuple[str, str]:
        source_language = self._source_language_for(self.self_runtime)
        target_language = self._target_language_for(self.self_runtime)
        if source == "spec" and isinstance(buffer.spec_translation, Translation):
            return (
                self._language_or_fallback(
                    buffer.spec_translation.source_language,
                    source_language,
                ),
                self._language_or_fallback(
                    buffer.spec_translation.target_language,
                    target_language,
                ),
            )
        metadata_matches_active_self = (
            current_metadata is not None
            and getattr(current_metadata, "utterance_id", None) == buffer.merge_id
            and getattr(current_metadata, "occupant_key", None)
            == self._active_self_occupant_key(buffer)
        )
        if secondary_text and source == "sticky_cache" and metadata_matches_active_self:
            return (
                self._language_or_fallback(
                    self._metadata_language(current_metadata, "primary_language"),
                    source_language,
                ),
                self._language_or_fallback(
                    self._metadata_language(current_metadata, "secondary_language"),
                    target_language,
                ),
            )
        if not secondary_text and metadata_matches_active_self:
            return (
                self._language_or_fallback(
                    self._metadata_language(current_metadata, "primary_language"),
                    source_language,
                ),
                target_language,
            )
        return source_language, target_language

    # ------------------------------------------------------------------
    # Active-self metadata
    # ------------------------------------------------------------------

    def _current_active_self_metadata(self) -> object | None:
        provider = getattr(self.overlay_sink, "active_self_overlay_metadata", None)
        if not callable(provider):
            return None
        return provider()

    @staticmethod
    def _active_self_translation_metadata(metadata: object | None) -> dict[str, object]:
        if metadata is None:
            return {
                "update_id": None,
                "origin_wall_clock_ms": None,
                "session_scope": None,
                "source_text_hash": None,
                "source_text_len": None,
                "logical_turn_key": None,
            }
        return {
            "update_id": getattr(metadata, "update_id", None),
            "origin_wall_clock_ms": getattr(metadata, "origin_wall_clock_ms", None),
            "session_scope": getattr(metadata, "session_scope", None),
            "source_text_hash": getattr(metadata, "source_text_hash", None),
            "source_text_len": getattr(metadata, "source_text_len", None),
            "logical_turn_key": getattr(metadata, "logical_turn_key", None),
        }

    def _cached_active_self_secondary_text(self) -> str:
        metadata = self._current_active_self_metadata()
        if metadata is None:
            return ""
        return str(getattr(metadata, "secondary_text", "") or "")

    def _overlay_secondary_translation_metadata(
        self,
        *,
        buffer: _MergeBuffer,
        source: str,
        secondary_text: str,
    ) -> dict[str, object]:
        if not secondary_text:
            return self._active_self_translation_metadata(None)
        if source == "spec" and isinstance(buffer.spec_translation, Translation):
            return self._translation_overlay_metadata(buffer.spec_translation)
        metadata = self._current_active_self_metadata()
        if (
            source == "sticky_cache"
            and metadata is not None
            and getattr(metadata, "utterance_id", None) == buffer.merge_id
        ):
            return self._active_self_translation_metadata(metadata)
        return self._active_self_translation_metadata(None)

    # ------------------------------------------------------------------
    # Active-self secondary decision & sync
    # ------------------------------------------------------------------

    def _active_self_secondary_decision(
        self,
        buffer: _MergeBuffer,
    ) -> tuple[str, str, str | None]:
        translation = buffer.spec_translation
        active_text = self._merge_text(buffer.parts)
        if not active_text:
            return "", "blank", None
        reuse_mode = None
        if isinstance(translation, Translation):
            reuse_mode = self._soft_reuse_mode(buffer.spec_text, active_text)
            if reuse_mode is not None:
                return translation.text.strip(), "spec", reuse_mode
        sticky_secondary = self._cached_active_self_secondary_text().strip()
        if sticky_secondary:
            return sticky_secondary, "sticky_cache", reuse_mode
        return "", "blank", reuse_mode

    def _active_self_occupant_key(self, buffer: _MergeBuffer) -> str:
        return f"self:{buffer.merge_id}"

    async def _sync_overlay_active_self(
        self, buffer: _MergeBuffer | None, *, created_at: float | None = None
    ) -> None:
        if self.overlay_sink is None or buffer is None:
            return

        active_text = self._merge_text(buffer.parts)
        if not active_text:
            return
        secondary_text, source, reuse_mode = self._active_self_secondary_decision(buffer)
        self._record_active_self_secondary_decision(
            buffer=buffer,
            active_text=active_text,
            secondary_text=secondary_text,
            source=source,
            reuse_mode=reuse_mode,
        )
        current_metadata = self._current_active_self_metadata()
        translation_metadata = self._overlay_secondary_translation_metadata(
            buffer=buffer,
            source=source,
            secondary_text=secondary_text,
        )
        current_translation_metadata = self._active_self_translation_metadata(current_metadata)
        occupant_key = self._active_self_occupant_key(buffer)
        source_language, target_language = self._active_self_overlay_languages(
            buffer=buffer,
            source=source,
            secondary_text=secondary_text,
            current_metadata=current_metadata,
        )
        primary_language = source_language.strip() or None
        secondary_language = (target_language.strip() or None) if secondary_text.strip() else None
        if (
            current_metadata is not None
            and buffer.merge_id == getattr(current_metadata, "utterance_id", None)
            and occupant_key == getattr(current_metadata, "occupant_key", None)
            and active_text == getattr(current_metadata, "text", None)
            and secondary_text == getattr(current_metadata, "secondary_text", "")
            and primary_language == getattr(current_metadata, "primary_language", None)
            and secondary_language == getattr(current_metadata, "secondary_language", None)
            and translation_metadata == current_translation_metadata
        ):
            return

        self._record_overlay_emit(
            event_kind="active_self",
            utterance_id=buffer.merge_id,
            channel="self",
            secondary_len=len(secondary_text),
        )
        await self._emit_self_active_overlay_event(
            self.overlay_event_adapter.self_active_update(
                text=active_text,
                utterance_id=buffer.merge_id,
                secondary_text=secondary_text,
                occupant_key=occupant_key,
                source_language=source_language,
                target_language=target_language,
                created_at=created_at,
                **translation_metadata,
            )
        )

    async def reset_overlay_preview(self) -> None:
        if self._current_active_self_metadata() is None:
            return
        if self.overlay_sink is None:
            return
        await self._emit_self_active_overlay_event(self.overlay_event_adapter.self_active_clear())

    # ------------------------------------------------------------------
    # Diagnostics & runtime logging
    # ------------------------------------------------------------------

    def _record_active_self_secondary_decision(
        self,
        *,
        buffer: _MergeBuffer,
        active_text: str,
        secondary_text: str,
        source: str,
        reuse_mode: str | None,
    ) -> None:
        signature = (
            buffer.merge_id,
            active_text,
            secondary_text,
            source,
            reuse_mode,
            buffer.resume_pending,
            buffer.resume_confirmed,
        )
        self._maybe_emit_active_self_secondary_runtime_log(
            buffer=buffer,
            active_text=active_text,
            secondary_text=secondary_text,
            source=source,
            reuse_mode=reuse_mode,
            signature=signature,
        )
        if self.overlay_diagnostics is None:
            return
        if signature == self._last_overlay_secondary_diagnostics_signature:
            return
        self._last_overlay_secondary_diagnostics_signature = signature
        spec_translation_len = 0
        if isinstance(buffer.spec_translation, Translation):
            spec_translation_len = len(buffer.spec_translation.text.strip())
        self.overlay_diagnostics.record_hub(
            "active_self_secondary",
            merge_id=str(buffer.merge_id),
            source=source,
            active_text_len=len(active_text),
            secondary_len=len(secondary_text),
            spec_text_len=len((buffer.spec_text or "").strip()),
            spec_translation_len=spec_translation_len,
            cached_secondary_len=len(self._cached_active_self_secondary_text().strip()),
            reuse_mode=reuse_mode,
            resume_pending=buffer.resume_pending,
            resume_confirmed=buffer.resume_confirmed,
        )

    def _maybe_emit_active_self_secondary_runtime_log(
        self,
        *,
        buffer: _MergeBuffer,
        active_text: str,
        secondary_text: str,
        source: str,
        reuse_mode: str | None,
        signature: tuple[object, ...],
    ) -> None:
        if signature == self._last_overlay_secondary_runtime_signature:
            return
        spec_translation_len = 0
        if isinstance(buffer.spec_translation, Translation):
            spec_translation_len = len(buffer.spec_translation.text.strip())
        emitted = self._emit_detailed(
            "[Hub] active_self_secondary merge_id=%s source=%s active_len=%s secondary_len=%s spec_text_len=%s spec_translation_len=%s cached_secondary_len=%s reuse_mode=%s resume_pending=%s resume_confirmed=%s",
            str(buffer.merge_id)[:8],
            source,
            len(active_text),
            len(secondary_text),
            len((buffer.spec_text or "").strip()),
            spec_translation_len,
            len(self._cached_active_self_secondary_text().strip()),
            reuse_mode,
            buffer.resume_pending,
            buffer.resume_confirmed,
            fallback_level=logging.INFO,
        )
        if emitted:
            self._last_overlay_secondary_runtime_signature = signature

    def _should_blank_stale_active_secondary_before_finalizing(
        self,
        *,
        final_text: str,
        reuse_mode: str | None,
    ) -> bool:
        # Presenter promotion preserves active secondary text for the same occupant.
        # Blank the active row first when speculative reuse is unsafe so stale
        # secondary text cannot be promoted into the finalized row.
        metadata = self._current_active_self_metadata()
        return (
            reuse_mode is None
            and self.overlay_sink is not None
            and metadata is not None
            and getattr(metadata, "text", None) == final_text
            and str(getattr(metadata, "secondary_text", "") or "").strip() != ""
        )

    def _record_overlay_emit(
        self,
        *,
        event_kind: str,
        utterance_id: UUID,
        channel: ChannelId,
        secondary_len: int,
    ) -> None:
        if self.overlay_diagnostics is None:
            return
        self.overlay_diagnostics.record_hub(
            "overlay_emit",
            event_kind=event_kind,
            utterance_id=str(utterance_id),
            channel=channel,
            secondary_len=secondary_len,
            sink_type=type(self.overlay_sink).__name__ if self.overlay_sink is not None else None,
        )
