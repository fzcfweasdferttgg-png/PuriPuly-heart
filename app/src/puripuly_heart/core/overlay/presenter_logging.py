"""Overlay presenter logging — diagnostics recording and lazy emit.

Mixin for OverlayPresenter.  Provides:
- _emit_detailed / _emit_detailed_lazy: log messages (lazy = only build
  string if logging is actually enabled)
- _emit_turn_decision: structured decision logging for overlay turn lifecycle
- _record_removed_entry: bridge between entry removal and tombstoning +
  diagnostics recording.  This is where _remember_tombstone is called.
- _record_visible_window_selection: logs which entries are visible/evicted

Called by PresenterEntryMgmtMixin (via _record_removed_entry) and
presenter_refresh_burst.py.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from .protocol import OverlayPresentationBlock
    from .state import (
        OverlayEntryRemovalRecord,
        OverlayLogicalTurnEntry,
        OverlayReductionResult,
        OverlayTurnDecisionRecord,
    )


class PresenterLoggingMixin:
    """Logging, emit, and diagnostic recording methods for OverlayPresenter."""

    def _emit_detailed(self, message: str, *, level: int = logging.INFO) -> bool:
        if self.runtime_log_detailed is None:
            return False
        try:
            return self.runtime_log_detailed(message, level=level)
        except Exception:
            return False

    def _emit_detailed_lazy(
        self,
        build_message: Callable[[], str],
        *,
        level: int = logging.INFO,
    ) -> bool:
        # Lazy emit: only call build_message() if logging is actually enabled.
        # Uses reflection to find emit_detailed_lazy/log_detailed_lazy on the
        # owner object (if the callback is a bound method).  Falls back to
        # eager call if no lazy variant found.
        runtime_log_detailed = self.runtime_log_detailed
        if runtime_log_detailed is None:
            return False

        owner = getattr(runtime_log_detailed, "__self__", None)
        try:
            if owner is not None:
                emit_detailed_lazy = getattr(owner, "emit_detailed_lazy", None)
                if callable(emit_detailed_lazy):
                    return emit_detailed_lazy(build_message, level=level)
                log_detailed_lazy = getattr(owner, "log_detailed_lazy", None)
                if callable(log_detailed_lazy):
                    return log_detailed_lazy(build_message, level=level)
            return runtime_log_detailed(build_message(), level=level)
        except Exception:
            return False

    def _emit_turn_decision(
        self,
        decision: str,
        *,
        disposition: str | None = None,
        key: tuple[str, UUID] | None = None,
        entry: OverlayLogicalTurnEntry | None = None,
        block: OverlayPresentationBlock | None = None,
        extras: dict[str, object] | None = None,
    ) -> bool:
        def build_message() -> str:
            resolved_key = key
            if resolved_key is None and entry is not None:
                resolved_key = (entry.channel, entry.utterance_id)
            parts = [f"decision={decision}"]
            if disposition is not None:
                parts.append(f"disposition={disposition}")
            if resolved_key is not None:
                parts.append(f"entry={self._format_entry_key(resolved_key)}")
            if entry is not None:
                publishable = self._presentation_state.entry_is_publishable(
                    entry,
                    show_peer_original=self.show_peer_original,
                )
                parts.extend(
                    [
                        f"channel={entry.channel}",
                        f"publishable={publishable}",
                        f"ever_visible={entry.ever_visible}",
                        "ever_visible_with_translation="
                        f"{entry.translation_observed_visible_since is not None}",
                        f"retained_hidden={entry.retained_hidden}",
                    ]
                )
            if block is not None:
                parts.extend(
                    [
                        f"block_variant={block.block_variant}",
                        f"primary_len={len(block.primary_text)}",
                        f"secondary_len={len(block.secondary_text)}",
                    ]
                )
            if extras is not None:
                for field_name, value in extras.items():
                    parts.append(f"{field_name}={value}")
            return f"[OverlayPresenter][Decision] {' '.join(parts)}"

        return self._emit_detailed_lazy(build_message)

    def _emit_pair_state(
        self,
        key: tuple[str, UUID],
        entry: OverlayLogicalTurnEntry,
        block: OverlayPresentationBlock,
        *,
        publish_kind: str,
    ) -> bool:
        _ = (key, entry, block, publish_kind)
        return False

    def _emit_skip_disposition(
        self,
        *,
        decision: str,
        disposition: str,
        key: tuple[str, UUID] | None = None,
        entry: OverlayLogicalTurnEntry | None = None,
        extras: dict[str, object] | None = None,
    ) -> bool:
        return self._emit_turn_decision(
            decision,
            disposition=disposition,
            key=key,
            entry=entry,
            extras=extras,
        )

    def _emit_reduction_decisions(
        self,
        decisions: tuple[OverlayTurnDecisionRecord, ...],
    ) -> None:
        for decision in decisions:
            self._emit_turn_decision(
                decision.decision,
                disposition=decision.disposition,
                key=decision.key,
                entry=decision.entry,
                block=decision.block,
                extras=decision.extras,
            )

    def _finish_reduction_result(self, result: OverlayReductionResult) -> bool:
        self._emit_reduction_decisions(result.decisions)
        self._drain_presentation_state_removals()
        return result.changed

    def _record_removed_entry(self, record: OverlayEntryRemovalRecord) -> None:
        # Bridge between entry removal and diagnostics/tombstoning.
        # Records diagnostics, emits turn decisions for expired/evicted entries,
        # and calls _remember_tombstone to prevent late-arrival re-creation.
        key = record.key
        entry = record.entry
        effective_deadline, visible_deadline, translation_deadline = (
            self._entry_expiration_components(entry)
        )
        removal_time = record.now if record.now is not None else self.clock.now()
        extra_fields: dict[str, object] = {}
        if entry.channel == "self":
            lifetime_ms = 0.0
            if entry.visible_since is not None:
                lifetime_ms = max(0.0, (removal_time - entry.visible_since) * 1000.0)
            translated_lifetime_ms = 0.0
            if entry.translation_observed_visible_since is not None:
                translated_lifetime_ms = max(
                    0.0,
                    (removal_time - entry.translation_observed_visible_since) * 1000.0,
                )
            extra_fields = {
                "lifetime_ms": lifetime_ms,
                "translated_lifetime_ms": translated_lifetime_ms,
                "had_translation": bool(entry.translation_text.strip()),
                "ever_visible_with_translation": entry.translation_observed_visible_since
                is not None,
                "translation_observed_visible_since": entry.translation_observed_visible_since,
            }
        if self.diagnostics is not None:
            self.diagnostics.record_presenter_removal(
                reason=record.reason,
                entry_key=self._format_entry_key(key),
                appearance_seq=entry.appearance_seq,
                channel=entry.channel,
                primary_len=len(entry.original_text.strip()),
                secondary_len=len(entry.translation_text.strip()),
                visible_since=entry.visible_since,
                translation_visible_since=entry.translation_visible_since,
                closed_at=entry.closed_at,
                now=removal_time,
                visible_deadline=visible_deadline,
                translation_deadline=translation_deadline,
                effective_deadline=effective_deadline,
                **extra_fields,
            )
        seq = record.tombstone_seq if record.tombstone_seq is not None else entry.closed_seq
        if record.reason == "expired" and entry.ever_visible:
            self._remember_scene_terminal_reason(key, reason=record.reason)
            self._emit_turn_decision(
                "overlay_turn_hidden_idle_ttl",
                disposition="hidden_idle_ttl",
                key=key,
                entry=entry,
                extras={"deadline": effective_deadline},
            )
        if record.reason == "evicted_by_newer_turn":
            self._remember_scene_terminal_reason(key, reason=record.reason)
            self._emit_turn_decision(
                "overlay_turn_evicted_by_newer_turn",
                disposition="evicted",
                key=key,
                entry=entry,
            )
        if seq is not None:
            self._remember_tombstone(key, seq)

    def _record_visible_window_selection(
        self,
        *,
        active_self_present: bool,
        finalized_limit: int,
        candidate_keys: list[tuple[str, UUID]],
        selected_keys: list[tuple[str, UUID]],
        protected_selected: list[tuple[str, UUID]],
        retained_hidden: list[tuple[str, UUID]],
    ) -> None:
        if self.diagnostics is None:
            return
        candidate_labels = [self._format_entry_key(key) for key in candidate_keys]
        selected_labels = [self._format_entry_key(key) for key in selected_keys]
        dropped_labels = [label for label in candidate_labels if label not in selected_labels]
        protected_labels = [self._format_entry_key(key) for key in protected_selected]
        retained_hidden_labels = [self._format_entry_key(key) for key in retained_hidden]
        signature = (
            active_self_present,
            finalized_limit,
            tuple(candidate_labels),
            tuple(selected_labels),
            tuple(dropped_labels),
            tuple(protected_labels),
            tuple(retained_hidden_labels),
        )
        if signature == self._last_visible_window_signature:
            return
        self._last_visible_window_signature = signature
        self.diagnostics.record_presenter(
            "visible_window",
            active_self_present=active_self_present,
            finalized_limit=finalized_limit,
            candidate_keys=candidate_labels,
            selected_keys=selected_labels,
            dropped_keys=dropped_labels,
            protected_selected=protected_labels,
            retained_hidden=retained_hidden_labels,
        )

    def _record_deadline(self, entry: OverlayLogicalTurnEntry) -> None:
        if self.diagnostics is None:
            return
        effective_deadline, visible_deadline, translation_deadline = (
            self._entry_expiration_components(entry)
        )
        self.diagnostics.record_presenter(
            "deadline_scheduled",
            entry_key=self._format_entry_key((entry.channel, entry.utterance_id)),
            channel=entry.channel,
            visible_since=entry.visible_since,
            translation_visible_since=entry.translation_visible_since,
            closed_at=entry.closed_at,
            visible_deadline=visible_deadline,
            translation_deadline=translation_deadline,
            effective_deadline=effective_deadline,
        )

    def _record_self_presentation_refresh_burst_start(
        self,
        key: tuple[str, UUID],
        *,
        reason: str,
    ) -> None:
        target_key = self._format_entry_key(key)
        self._emit_detailed_lazy(
            lambda: "[OverlayPresenter][SelfPresentationRefresh] start reason=%s target_key=%s"
            % (reason, target_key)
        )
        if self.diagnostics is not None:
            self.diagnostics.record_presenter(
                "self_presentation_refresh_burst_start",
                reason=reason,
                target_key=target_key,
            )

    def _record_self_presentation_refresh_burst_end(
        self,
        key: tuple[str, UUID],
        *,
        reason: str,
        tick_count: int,
        cleanup_publish_count: int,
    ) -> None:
        target_key = self._format_entry_key(key)
        self._emit_detailed_lazy(
            lambda: "[OverlayPresenter][SelfPresentationRefresh] end "
            "reason=%s target_key=%s tick_count=%s cleanup_publish_count=%s"
            % (reason, target_key, tick_count, cleanup_publish_count)
        )
        if self.diagnostics is not None:
            self.diagnostics.record_presenter(
                "self_presentation_refresh_burst_end",
                reason=reason,
                target_key=target_key,
                tick_count=tick_count,
                cleanup_publish_count=cleanup_publish_count,
            )

    def _format_entry_key(self, key: tuple[str, UUID]) -> str:
        return f"{key[0]}:{key[1]}"
