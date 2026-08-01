from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from puripuly_heart.core.clock import Clock
from puripuly_heart.ports.overlay_transport import OverlayPresentationTransport, RuntimeDetailedLogger
from puripuly_heart.domain.overlay_calibration import OverlayCalibration

from .diagnostics import OverlayDiagnosticsRecorder
from .protocol import (
    NativeFreshRenderGenerations,
    NativeFreshRenderTargets,
    NativeQuietTailEpisodes,
    OverlayPresentationBlock,
    OverlayPresentationCalibration,
    OverlayPresentationSnapshot,
)
from .sink import (
    OverlayEventUnion,
    OverlaySink,
    PeerActiveUpdate,
    PeerTranscriptFinal,
    SelfActiveClear,
    SelfActiveUpdate,
    SelfTranscriptFinal,
    TranslationFinal,
    TranslationStreamUpdate,
    UtteranceClosed,
)
from .state import (
    ActiveSelfOverlayMetadata,
    OverlayEntryRemovalRecord,
    OverlayPresentationState,
    OverlayReductionResult,
    OverlayTurnDecisionRecord,
)
from .state import (
    OverlayLogicalTurnEntry as _LogicalTurnEntry,
)

from puripuly_heart.core.overlay.presenter_logging import PresenterLoggingMixin
from puripuly_heart.core.overlay.presenter_entry_mgmt import PresenterEntryMgmtMixin
from puripuly_heart.core.overlay.presenter_retry import PresenterRetryMixin
from puripuly_heart.core.overlay.presenter_refresh_burst import PresenterRefreshBurstMixin

VISIBLE_WINDOW_TARGET_BLOCKS = 2
SleepFn = Callable[[float], Awaitable[None]]


@dataclass(slots=True)
class OverlayPresenter(OverlaySink, PresenterLoggingMixin, PresenterEntryMgmtMixin, PresenterRetryMixin, PresenterRefreshBurstMixin):
    calibration: OverlayCalibration
    bridge: OverlayPresentationTransport | None = None
    diagnostics: OverlayDiagnosticsRecorder | None = None
    runtime_log_detailed: RuntimeDetailedLogger | None = None
    clock: Clock
    sleep: SleepFn = asyncio.sleep
    visible_window_target_blocks: int = VISIBLE_WINDOW_TARGET_BLOCKS
    show_translation: bool = True
    show_peer_original: bool = True
    peer_presentation_refresh_burst: bool = True
    self_presentation_refresh_burst: bool = True
    native_retry_trigger_emission: bool = False
    task_factory: Any | None = None

    _terminal_registry: OrderedDict[tuple[str, UUID], int] = field(
        init=False,
        default_factory=OrderedDict,
    )
    _scene_terminal_keys: set[tuple[str, UUID]] = field(
        init=False,
        default_factory=set,
    )
    _scene_terminal_reasons: dict[tuple[str, UUID], str] = field(
        init=False,
        default_factory=dict,
    )
    _expiration_tasks: dict[tuple[str, UUID], asyncio.Task[None]] = field(
        init=False,
        default_factory=dict,
    )
    _revision: int = field(init=False, default=0)
    _appearance_seq: int = field(init=False, default=0)
    _presentation_state: OverlayPresentationState = field(init=False)
    _last_visible_window_signature: tuple[object, ...] | None = field(init=False, default=None)
    _peer_presentation_refresh_burst_task: asyncio.Task[None] | None = field(
        init=False,
        default=None,
    )
    _self_presentation_refresh_burst_task: asyncio.Task[None] | None = field(
        init=False,
        default=None,
    )
    _self_presentation_refresh_burst_cancel_reasons: dict[asyncio.Task[None], str] = field(
        init=False, default_factory=dict
    )
    _self_presentation_refresh_burst_cancel_cleanup_counts: dict[asyncio.Task[None], int] = field(
        init=False, default_factory=dict
    )
    _native_fresh_render_generations: NativeFreshRenderGenerations = field(
        init=False, default_factory=NativeFreshRenderGenerations
    )
    _native_fresh_render_targets: NativeFreshRenderTargets = field(
        init=False, default_factory=NativeFreshRenderTargets
    )
    _native_quiet_tail_episodes: NativeQuietTailEpisodes = field(
        init=False, default_factory=NativeQuietTailEpisodes
    )
    _native_quiet_tail_self_target: str | None = field(init=False, default=None)
    _native_quiet_tail_peer_target: str | None = field(init=False, default=None)
    _native_quiet_tail_self_generation: int | None = field(init=False, default=None)
    _native_quiet_tail_peer_generation: int | None = field(init=False, default=None)
    _ownership_transition_lock: asyncio.Lock = field(
        init=False,
        default_factory=asyncio.Lock,
    )
    _closing: bool = field(init=False, default=False)
    _closed: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self._presentation_state = OverlayPresentationState()
        self._presentation_state.generate_snapshot(
            revision=0,
            calibration=_calibration_from_overlay(self.calibration),
            rendered_entries=[],
        )

    @property
    def _entries(self) -> dict[tuple[str, UUID], _LogicalTurnEntry]:
        return self._presentation_state.entries

    @property
    def _retired_preview_self_seqs(self) -> OrderedDict[tuple[str, UUID], int]:
        return self._presentation_state.retired_preview_self_seqs

    @property
    def _live_self_turn_key(self) -> tuple[str, UUID] | None:
        return self._presentation_state.live_self_turn_key

    @_live_self_turn_key.setter
    def _live_self_turn_key(self, key: tuple[str, UUID] | None) -> None:
        self._presentation_state.live_self_turn_key = key

    @property
    def _live_peer_turn_key(self) -> tuple[str, UUID] | None:
        return self._presentation_state.live_peer_turn_key

    @_live_peer_turn_key.setter
    def _live_peer_turn_key(self, key: tuple[str, UUID] | None) -> None:
        self._presentation_state.live_peer_turn_key = key

    def active_self_overlay_metadata(self) -> ActiveSelfOverlayMetadata | None:
        return self._presentation_state.active_self_overlay_metadata()

    def _elapsed_from_origin_wall_clock_ms(self, origin_wall_clock_ms: int | None) -> int | None:
        if origin_wall_clock_ms is None:
            return None
        return max(0, int(time.time() * 1000) - origin_wall_clock_ms)

    def _rendered_text_sources(
        self,
        entry: _LogicalTurnEntry,
        block: OverlayPresentationBlock,
    ) -> tuple[str, str]:
        if entry.channel == "peer" and block.block_variant == "active_peer":
            secondary_visible = block.secondary_enabled and bool(block.secondary_text.strip())
            return "blank", "source" if secondary_visible else "blank"
        if entry.channel == "peer" and block.block_variant == "finalized":
            if entry.translation_text.strip():
                secondary_visible = block.secondary_enabled and bool(block.secondary_text.strip())
                return "translation", "source" if secondary_visible else "blank"
            secondary_visible = block.secondary_enabled and bool(block.secondary_text.strip())
            return "blank", "source" if secondary_visible else "blank"

        secondary_source = "none"
        if block.secondary_enabled and block.secondary_text:
            if entry.channel == "peer":
                secondary_source = "original_text"
            elif block.block_variant == "active_self" and entry.live_secondary_text.strip():
                secondary_source = "live_secondary_text"
            else:
                secondary_source = "translation_text"

        if block.block_variant == "active_self":
            return "live_text", secondary_source
        if entry.channel == "peer":
            return "translation_text", secondary_source
        return "original_text", secondary_source

    def _rendered_pair_state(self, primary_source: str, secondary_source: str) -> str:
        if primary_source == "live_text":
            if secondary_source == "live_secondary_text":
                return "live_with_preview_translation"
            if secondary_source == "translation_text":
                return "live_with_translation"
            return "live_only"
        if primary_source == "translation_text":
            if secondary_source == "original_text":
                return "translation_with_original"
            return "translation_only"
        if primary_source == "translation":
            if secondary_source == "source":
                return "translation_with_original"
            return "translation_only"
        if primary_source == "blank" and secondary_source == "source":
            return "source_only"
        if secondary_source in {"translation_text", "live_secondary_text"}:
            return "original_with_translation"
        return "original_only"

    def _terminal_update_reason(
        self,
        channel: str | None,
        utterance_id: UUID | None,
    ) -> str | None:
        key = self._entry_key(channel, utterance_id)
        if key not in self._scene_terminal_keys and key not in self._terminal_registry:
            return None
        return self._scene_terminal_reasons.get(key, "")

    def _remember_scene_terminal_reason(self, key: tuple[str, UUID], *, reason: str) -> None:
        self._scene_terminal_keys.add(key)
        self._scene_terminal_reasons[key] = reason

    def attach_bridge(self, bridge: OverlayPresentationTransport) -> None:
        self.bridge = bridge

    def detach_bridge(self) -> None:
        self.bridge = None

    def snapshot(self) -> OverlayPresentationSnapshot:
        return self._presentation_state.snapshot()

    def reset_scene(self) -> None:
        self._cancel_peer_presentation_refresh_burst_task()
        self._cancel_self_presentation_refresh_burst_task(reason="scene_reset")
        self._cancel_all_expiration_tasks()
        self._clear_entries_for_reason("scene_reset")
        self._terminal_registry.clear()
        self._scene_terminal_keys.clear()
        self._scene_terminal_reasons.clear()
        self._retired_preview_self_seqs.clear()
        self._live_self_turn_key = None
        self._live_peer_turn_key = None
        self._revision = 0
        self._appearance_seq = 0
        self._native_fresh_render_generations = NativeFreshRenderGenerations()
        self._native_fresh_render_targets = NativeFreshRenderTargets()
        self._native_quiet_tail_episodes = NativeQuietTailEpisodes()
        self._native_quiet_tail_self_target = None
        self._native_quiet_tail_peer_target = None
        self._last_visible_window_signature = None
        peer_refresh_key = self._presentation_state.peer_presentation_refresh_target_key
        if peer_refresh_key is not None:
            self._presentation_state.end_peer_presentation_refresh(peer_refresh_key)
        self_refresh_key = self._presentation_state.self_presentation_refresh_target_key
        if self_refresh_key is not None:
            self._presentation_state.end_self_presentation_refresh(self_refresh_key)
        self._presentation_state.generate_snapshot(
            revision=0,
            calibration=_calibration_from_overlay(self.calibration),
            rendered_entries=[],
        )

    async def clear_for_runtime_detach(self) -> None:
        await self._cancel_peer_presentation_refresh_burst_task_and_wait()
        await self._cancel_self_presentation_refresh_burst_task_and_wait(reason="runtime_detach")
        self._cancel_all_expiration_tasks()
        self._clear_entries_for_reason("scene_reset")
        self._terminal_registry.clear()
        self._scene_terminal_keys.clear()
        self._scene_terminal_reasons.clear()
        self._retired_preview_self_seqs.clear()
        self._live_self_turn_key = None
        self._live_peer_turn_key = None
        self._revision += 1
        self._native_fresh_render_generations = NativeFreshRenderGenerations()
        self._native_fresh_render_targets = NativeFreshRenderTargets()
        self._native_quiet_tail_episodes = NativeQuietTailEpisodes()
        self._native_quiet_tail_self_target = None
        self._native_quiet_tail_peer_target = None
        self._last_visible_window_signature = None
        peer_refresh_key = self._presentation_state.peer_presentation_refresh_target_key
        if peer_refresh_key is not None:
            self._presentation_state.end_peer_presentation_refresh(peer_refresh_key)
        self_refresh_key = self._presentation_state.self_presentation_refresh_target_key
        if self_refresh_key is not None:
            self._presentation_state.end_self_presentation_refresh(self_refresh_key)
        snapshot = self._presentation_state.generate_snapshot(
            revision=self._revision,
            calibration=_calibration_from_overlay(self.calibration),
            rendered_entries=[],
        )
        if self.bridge is not None:
            await self.bridge.replace_snapshot(snapshot)

    async def emit(self, event: OverlayEventUnion) -> None:
        async with self._ownership_transition_lock:
            await self._emit_serialized(event)

    async def _emit_serialized(self, event: OverlayEventUnion) -> None:
        previous_snapshot = self.snapshot()
        changed = self._apply_event(event)
        peer_event_is_current = self._peer_presentation_refresh_event_is_current(event)
        peer_event_is_visible = (
            peer_event_is_current
            and event.utterance_id is not None
            and self._snapshot_has_refreshable_peer_key(("peer", event.utterance_id))
        )
        if changed or peer_event_is_visible:
            await self._publish_if_changed(
                fresh_render_event=event,
                event_changed=changed,
            )
        if changed or peer_event_is_current:
            await self._start_peer_presentation_refresh_burst_for_event(event)
        if changed:
            await self._start_self_presentation_refresh_burst_for_event(
                event,
                previous_snapshot=previous_snapshot,
            )

    async def update_native_retry_ownership(self, confirmed: bool) -> None:
        async with self._ownership_transition_lock:
            await self._update_native_retry_ownership_serialized(confirmed)

    async def _update_native_retry_ownership_serialized(self, confirmed: bool) -> None:
        confirmed = bool(confirmed)
        if self._closing or self._closed:
            self.native_retry_trigger_emission = False
            self._native_fresh_render_generations = NativeFreshRenderGenerations()
            self._native_fresh_render_targets = NativeFreshRenderTargets()
            return
        if confirmed:
            if (
                self.native_retry_trigger_emission
                and not self.peer_presentation_refresh_burst
                and not self.self_presentation_refresh_burst
            ):
                return
            active_targets = self._active_python_retry_targets()
            if not active_targets:
                active_targets = self._active_native_retry_targets()
            await self.update_peer_presentation_refresh_burst(False)
            await self.update_self_presentation_refresh_burst(False)
            self.native_retry_trigger_emission = True
            self._synchronize_native_retry_targets(active_targets)
            return
        if not confirmed:
            if not self.native_retry_trigger_emission:
                return
            active_targets = self._active_native_retry_targets()
            self.native_retry_trigger_emission = False
            await self._restart_python_retry_targets(active_targets)

    async def _restart_python_retry_targets(self, targets: dict[str, str]) -> None:
        for channel, target in targets.items():
            if channel == "peer":
                await self.update_peer_presentation_refresh_burst(True)
            elif channel == "self":
                await self.update_self_presentation_refresh_burst(True)

    async def update_calibration(self, calibration: OverlayCalibration) -> None:
        if calibration == self.calibration:
            return
        self.calibration = calibration.copy()
        await self._publish_if_changed()

    async def update_display_preferences(
        self,
        *,
        show_translation: bool,
        show_peer_original: bool,
    ) -> None:
        next_show_translation = bool(show_translation)
        next_show_peer_original = bool(show_peer_original)
        if (
            next_show_translation == self.show_translation
            and next_show_peer_original == self.show_peer_original
        ):
            return
        self.show_translation = next_show_translation
        self.show_peer_original = next_show_peer_original
        await self._publish_if_changed()

    async def update_peer_presentation_refresh_burst(self, enabled: bool) -> None:
        next_enabled = bool(enabled)
        if next_enabled == self.peer_presentation_refresh_burst:
            return
        self.peer_presentation_refresh_burst = next_enabled
        if not next_enabled:
            await self._cancel_peer_presentation_refresh_burst_task_and_wait()
            peer_refresh_key = self._presentation_state.peer_presentation_refresh_target_key
            if (
                peer_refresh_key is not None
                and self._presentation_state.end_peer_presentation_refresh(peer_refresh_key)
            ):
                await self._publish_if_changed()

    async def update_self_presentation_refresh_burst(self, enabled: bool) -> None:
        next_enabled = bool(enabled)
        if next_enabled == self.self_presentation_refresh_burst:
            return
        self.self_presentation_refresh_burst = next_enabled
        if not next_enabled:
            await self._cancel_self_presentation_refresh_burst_task_and_wait(
                reason="disabled",
                allow_task_cleanup=True,
            )
            self_refresh_key = self._presentation_state.self_presentation_refresh_target_key
            if (
                self_refresh_key is not None
                and self._presentation_state.end_self_presentation_refresh(self_refresh_key)
            ):
                await self._publish_if_changed()

    async def broadcast_shutdown(self) -> None:
        if self.bridge is None:
            return
        await self.bridge.broadcast_shutdown()

    def _apply_event(self, event: OverlayEventUnion) -> bool:
        now = self.clock.now()
        self._expire_closed_entries(now=now)

        if event.channel == "self":
            return self._apply_self_event(event, now=now)
        if event.channel == "peer":
            return self._apply_peer_event(event, now=now)

        return False

    def _apply_self_event(self, event: OverlayEventUnion, *, now: float) -> bool:
        if isinstance(event, SelfActiveUpdate):
            return self._apply_self_active_update(event, now=now)
        if isinstance(event, SelfActiveClear):
            return self._apply_self_active_clear(event, now=now)
        if isinstance(event, SelfTranscriptFinal):
            return self._apply_self_finalized_update(event, now=now)
        if isinstance(event, (TranslationStreamUpdate, TranslationFinal)):
            return self._apply_self_translation_update(event, now=now)
        if isinstance(event, UtteranceClosed):
            return self._apply_self_utterance_closed(event, now=now)
        return False

    def _apply_peer_event(self, event: OverlayEventUnion, *, now: float) -> bool:
        if isinstance(event, PeerActiveUpdate):
            # Reserved compatibility/fallback path. Normal product peer overlay
            # rows become primary-visible when translation arrives, not from
            # source-only active speech.
            return self._apply_peer_active_update(event, now=now)
        if isinstance(event, PeerTranscriptFinal):
            return self._apply_peer_finalized_update(event, now=now)
        if isinstance(event, (TranslationStreamUpdate, TranslationFinal)):
            return self._apply_peer_translation_update(event, now=now)
        if isinstance(event, UtteranceClosed):
            return self._apply_peer_utterance_closed(event, now=now)
        return False

    def _apply_self_active_update(self, event: SelfActiveUpdate, *, now: float) -> bool:
        result = self._presentation_state.apply_self_active_update(
            event,
            now=now,
            show_translation=self.show_translation,
            terminal_update_reason=self._terminal_update_reason,
        )
        return self._finish_reduction_result(result)

    def _apply_peer_active_update(self, event: PeerActiveUpdate, *, now: float) -> bool:
        result = self._presentation_state.apply_peer_active_update(
            event,
            now=now,
            show_peer_original=self.show_peer_original,
            next_appearance_seq=self._next_appearance_seq,
            terminal_update_reason=self._terminal_update_reason,
        )
        return self._finish_peer_reduction_result(result, event)

    def _apply_peer_finalized_update(
        self,
        event: PeerTranscriptFinal,
        *,
        now: float,
    ) -> bool:
        result = self._presentation_state.apply_peer_finalized_update(
            event,
            now=now,
            show_peer_original=self.show_peer_original,
            next_appearance_seq=self._next_appearance_seq,
            terminal_update_reason=self._terminal_update_reason,
        )
        return self._finish_peer_reduction_result(result, event)

    def _apply_peer_translation_update(
        self,
        event: TranslationStreamUpdate | TranslationFinal,
        *,
        now: float,
    ) -> bool:
        result = self._presentation_state.apply_peer_translation_update(
            event,
            now=now,
            show_peer_original=self.show_peer_original,
            next_appearance_seq=self._next_appearance_seq,
            terminal_update_reason=self._terminal_update_reason,
        )
        return self._finish_peer_reduction_result(result, event)

    def _apply_peer_utterance_closed(self, event: UtteranceClosed, *, now: float) -> bool:
        result = self._presentation_state.apply_peer_utterance_closed(
            event,
            now=now,
            is_tombstoned=self._is_tombstoned,
        )
        return self._finish_peer_reduction_result(result, event)

    def _finish_peer_reduction_result(
        self,
        result: OverlayReductionResult,
        event: OverlayEventUnion,
    ) -> bool:
        changed = self._finish_reduction_result(result)
        if changed:
            key = self._entry_key(event.channel, event.utterance_id)
            entry = self._entries.get(key)
            if entry is not None and (entry.closed_seq is not None or entry.retained_hidden):
                self._schedule_expiration(key, entry)
        return changed

    def _apply_self_active_clear(self, event: SelfActiveClear, *, now: float) -> bool:
        result = self._presentation_state.apply_self_active_clear(
            event,
            now=now,
            show_translation=self.show_translation,
        )
        return self._finish_reduction_result(result)

    def _apply_self_finalized_update(
        self,
        event: SelfTranscriptFinal,
        *,
        now: float,
    ) -> bool:
        result = self._presentation_state.apply_self_finalized_update(
            event,
            now=now,
            show_translation=self.show_translation,
            next_appearance_seq=self._next_appearance_seq,
            terminal_update_reason=self._terminal_update_reason,
        )
        changed = self._finish_reduction_result(result)
        if changed:
            key = self._entry_key(event.channel, event.utterance_id)
            entry = self._entries.get(key)
            if entry is not None and (entry.closed_seq is not None or entry.retained_hidden):
                self._schedule_expiration(key, entry)
        return changed

    def _apply_self_translation_update(
        self,
        event: TranslationStreamUpdate | TranslationFinal,
        *,
        now: float,
    ) -> bool:
        result = self._presentation_state.apply_self_translation_update(
            event,
            now=now,
            show_translation=self.show_translation,
            next_appearance_seq=self._next_appearance_seq,
            terminal_update_reason=self._terminal_update_reason,
        )
        changed = self._finish_reduction_result(result)
        if changed:
            key = self._entry_key(event.channel, event.utterance_id)
            entry = self._entries.get(key)
            if entry is not None and (entry.closed_seq is not None or entry.retained_hidden):
                self._schedule_expiration(key, entry)
        return changed

    def _apply_self_utterance_closed(self, event: UtteranceClosed, *, now: float) -> bool:
        result = self._presentation_state.apply_self_utterance_closed(
            event,
            now=now,
            is_tombstoned=self._is_tombstoned,
        )
        changed = self._finish_reduction_result(result)
        if changed:
            key = self._entry_key(event.channel, event.utterance_id)
            entry = self._entries.get(key)
            if entry is not None:
                self._schedule_expiration(key, entry)
        return changed

    async def _publish_if_changed(
        self,
        fresh_render_event: object | None = None,
        event_changed: bool = True,
    ) -> None:
        now = self.clock.now()
        self._expire_closed_entries(now=now)
        previous_snapshot = self.snapshot()
        selection = self._presentation_state.visible_block_selection(
            entries=self._entries,
            live_self_entry=self._live_self_entry(),
            live_peer_entry=self._live_peer_entry(),
            visible_window_target_blocks=self.visible_window_target_blocks,
            show_translation=self.show_translation,
            show_peer_original=self.show_peer_original,
            peer_presentation_refresh_burst=self.peer_presentation_refresh_burst,
            self_presentation_refresh_burst=self.self_presentation_refresh_burst,
            next_appearance_seq=self._next_appearance_seq,
        )
        self._mark_entries_visible(selection.selected_keys)
        self._prune_displaced_finalized_entries(
            set(selection.selected_keys),
            candidate_keys=selection.candidate_keys,
        )
        for protected_key in selection.protected_keys:
            active_entry = self._entries.get(protected_key)
            if active_entry is not None:
                active_entry.ever_visible = True
        self._record_visible_window_selection(
            active_self_present=selection.active_self_present,
            finalized_limit=selection.finalized_limit,
            candidate_keys=selection.candidate_keys,
            selected_keys=selection.selected_keys,
            protected_selected=selection.protected_keys,
            retained_hidden=selection.retained_hidden,
        )
        rendered_entries = selection.rendered_entries
        next_blocks = [block for _, block in rendered_entries]
        next_calibration = _calibration_from_overlay(self.calibration)
        previous_rendered_signature = self._presentation_state.rendered_blocks_signature(
            previous_snapshot.blocks
        )
        next_rendered_signature = self._presentation_state.rendered_blocks_signature(next_blocks)
        previous_signatures = {
            block.id: self._presentation_state.rendered_block_signature(block)
            for block in previous_snapshot.blocks
        }
        self._refresh_visible_expiration_deadlines(
            rendered_entries,
            previous_blocks=previous_snapshot.blocks,
            now=now,
        )
        if (
            next_rendered_signature == previous_rendered_signature
            and next_calibration == previous_snapshot.calibration
        ):
            self._emit_turn_decision(
                "overlay_turn_no_visible_change",
                disposition="rendered_signature_unchanged",
                extras={"block_count": len(next_blocks)},
            )
            return

        for key, block in rendered_entries:
            entry = self._entries.get(key)
            if entry is None:
                continue
            previous_signature = previous_signatures.get(block.id)
            if previous_signature is None:
                self._emit_turn_decision(
                    "overlay_turn_first_visible",
                    key=key,
                    entry=entry,
                    block=block,
                )
                self._emit_pair_state(key, entry, block, publish_kind="first_visible")
                continue
            if previous_signature != self._presentation_state.rendered_block_signature(block):
                self._emit_turn_decision(
                    "overlay_turn_updated",
                    key=key,
                    entry=entry,
                    block=block,
                )
                self._emit_pair_state(key, entry, block, publish_kind="visible_update")

        self._revision += 1
        snapshot = self._presentation_state.generate_snapshot(
            revision=self._revision,
            calibration=next_calibration,
            rendered_entries=rendered_entries,
        )
        blocks_summary = [
            {
                "id": block.id,
                "variant": block.block_variant,
                "update_id": block.update_id,
                "origin_wall_clock_ms": block.origin_wall_clock_ms,
                "session_scope": block.session_scope,
                "primary_len": len(block.primary_text),
                "secondary_len": len(block.secondary_text),
            }
            for block in next_blocks
        ]
        compact_update_ids = [block.update_id for block in next_blocks if block.update_id]
        compact_scopes = [block.session_scope for block in next_blocks if block.session_scope]
        self._emit_detailed_lazy(
            lambda: "[OverlayPresenter] Snapshot publish: revision=%s block_count=%s bridge_attached=%s update_ids=%s scopes=%s"
            % (
                snapshot.revision,
                len(next_blocks),
                self.bridge is not None,
                compact_update_ids,
                compact_scopes,
            )
        )
        if self.diagnostics is not None:
            self.diagnostics.record_presenter(
                "snapshot_publish",
                revision=snapshot.revision,
                block_count=len(next_blocks),
                bridge_attached=self.bridge is not None,
                blocks=blocks_summary,
            )
        if self.bridge is not None:
            await self.bridge.replace_snapshot(snapshot)


def _calibration_from_overlay(
    calibration: OverlayCalibration,
) -> OverlayPresentationCalibration:
    return OverlayPresentationCalibration(
        anchor=calibration.anchor,
        offset_x=calibration.offset_x,
        offset_y=calibration.offset_y,
        distance=calibration.distance,
        text_scale=calibration.text_scale,
        background_alpha=calibration.background_alpha,
    )
