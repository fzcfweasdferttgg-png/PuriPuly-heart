"""Translation context for LLM prompts.

Gathers recent (source → translated) pairs from channel history and formats
them into a string injected into the translation prompt.  Two modes:

- "local"   — context from the requesting channel only (self).
- "integrated" — merged self + peer context, sorted by timestamp, capped at
  ``integrated_max_entries``.  Used when peer translation is enabled so the LLM
  sees cross-speaker context.

Called by ``TranslationService.prepare_request``; the formatted string becomes
part of the system prompt sent to the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from puripuly_heart.core.clock import Clock, SystemClock
from puripuly_heart.core.pipeline.channel_runtime import ChannelRuntime, ContextEntry

ContextMode = Literal["local", "integrated"]


@dataclass(slots=True)
class ContextResolver:
    clock: Clock = SystemClock()
    # Local: tighter limits (self-channel only, less context needed).
    local_time_window_s: float = 30.0
    local_max_entries: int = 3
    # Integrated: wider window + more entries (cross-speaker context helps LLM).
    integrated_time_window_s: float = 40.0
    integrated_max_entries: int = 4

    def get_local_entries(
        self,
        *,
        runtime: ChannelRuntime,
        source_language: str,
        target_language: str,
    ) -> list[ContextEntry]:
        return runtime.get_valid_context(
            now=self.clock.now(),
            source_language=source_language,
            target_language=target_language,
            time_window_s=self.local_time_window_s,
            max_entries=self.local_max_entries,
        )

    def format_local(self, entries: list[ContextEntry]) -> str:
        return self._format_entries(entries)

    def resolve_local(
        self,
        *,
        runtime: ChannelRuntime,
        source_language: str,
        target_language: str,
    ) -> tuple[str, ContextMode]:
        entries = self.get_local_entries(
            runtime=runtime,
            source_language=source_language,
            target_language=target_language,
        )
        return self.format_local(entries), "local"

    def resolve_for_request(
        self,
        *,
        runtime: ChannelRuntime,
        other_runtime: ChannelRuntime,
        requested_mode: ContextMode,
        peer_translation_enabled: bool,
        source_language: str,
        target_language: str,
        other_source_language: str | None = None,
        other_target_language: str | None = None,
    ) -> tuple[str, ContextMode]:
        # Decision tree: integrated mode requires BOTH requested AND peer_translation_enabled.
        # If peer_translation_enabled=False, integrated mode falls back to local even if
        # requested_mode="integrated". This prevents cross-channel context when peer translation
        # is off (avoids leaking peer context into self-channel prompts).
        # Decision tree: integrated mode requires both requested AND enabled.
        # Falls back to local when peer translation is off or mode is "local".
        if requested_mode != "integrated" or not peer_translation_enabled:
            return self.resolve_local(
                runtime=runtime,
                source_language=source_language,
                target_language=target_language,
            )
        integrated_entries = self._get_integrated_entries(
            runtime=runtime,
            other_runtime=other_runtime,
            source_language=source_language,
            target_language=target_language,
            other_source_language=other_source_language,
            other_target_language=other_target_language,
        )
        return self.format_integrated(integrated_entries), "integrated"

    def _format_entries(self, entries: list[ContextEntry]) -> str:
        if not entries:
            return ""
        return "\n".join(self._format_entry(entry) for entry in entries)

    def _format_entry(self, entry: ContextEntry) -> str:
        label = "peer" if entry.channel == "peer" else "self"
        return f'- [{label}, {self._relative_age(entry.timestamp)}s ago] "{entry.text}"'

    def format_integrated(self, entries: list[tuple[ChannelRuntime, ContextEntry]]) -> str:
        if not entries:
            return ""
        return "\n".join(self._format_entry(entry) for _, entry in entries)

    def _get_integrated_entries(
        self,
        *,
        runtime: ChannelRuntime,
        other_runtime: ChannelRuntime,
        source_language: str,
        target_language: str,
        other_source_language: str | None = None,
        other_target_language: str | None = None,
    ) -> list[tuple[ChannelRuntime, ContextEntry]]:
        # Cross-channel merge: collect entries from self + peer runtimes,
        # sort by timestamp, keep most recent integrated_max_entries.
        # Note: get_valid_context already slices to max_entries internally,
        # so combined may have up to 2×max_entries before the final slice.
        # other_source/target_language default to self languages if not provided.
        combined: list[tuple[ChannelRuntime, ContextEntry]] = []
        other_source_language = (
            source_language if other_source_language is None else other_source_language
        )
        other_target_language = (
            target_language if other_target_language is None else other_target_language
        )
        for channel_runtime, entry_source_language, entry_target_language in (
            (runtime, source_language, target_language),
            (other_runtime, other_source_language, other_target_language),
        ):
            for entry in channel_runtime.get_valid_context(
                now=self.clock.now(),
                source_language=entry_source_language,
                target_language=entry_target_language,
                time_window_s=self.integrated_time_window_s,
                max_entries=self.integrated_max_entries,
            ):
                combined.append((channel_runtime, entry))
        combined.sort(key=lambda item: item[1].timestamp)
        if self.integrated_max_entries > 0:
            return combined[-self.integrated_max_entries :]
        return combined

    def _relative_age(self, timestamp: float) -> int:
        return max(0, int(self.clock.now() - timestamp))
