from __future__ import annotations

"""Block renderer for overlay presentation entries.

Builds OverlayPresentationBlock objects from logical turn entries and
computes dedup signatures for rendered blocks.

Extracted from OverlayPresentationState (Phase 8).
Dependencies: RefreshManager, SelectionEngine (via constructor).
"""

from typing import TYPE_CHECKING

from puripuly_heart.domain.overlay_types import (
    OverlayEntryKey,
    OverlayPresentationBlock,
)
from puripuly_heart.core.overlay.refresh_manager import RefreshManager

from puripuly_heart.core.overlay.types_common import content_language_or_none, line_language

if TYPE_CHECKING:
    from puripuly_heart.core.overlay.selection_engine import SelectionEngine

    from puripuly_heart.core.overlay.types_common import (
        OverlayPresentationEntry,
    )


class BlockRenderer:
    """Builds presentation blocks and computes dedup signatures.

    Extracted from OverlayPresentationState (Phase 8).

    ``selection`` is wired after construction (chicken-and-egg: SelectionEngine
    needs build_presentation_block as callback, so BlockRenderer must exist first).
    """

    def __init__(self, refresh: RefreshManager, selection: SelectionEngine | None = None) -> None:
        self._refresh = refresh
        self._selection = selection

    # --- Orchestrator ---

    def build_presentation_block(
        self,
        entry: OverlayPresentationEntry,
        *,
        prefer_live_self: bool = False,
        show_translation: bool,
        show_peer_original: bool,
        peer_presentation_refresh_burst: bool,
        self_presentation_refresh_burst: bool = True,
    ) -> OverlayPresentationBlock | None:
        # build_presentation_block: dispatches to 3 sub-methods based on channel+live state — NOT based on block_variant field
        if prefer_live_self and entry.channel == "self":
            return self._build_active_self_block(
                entry, show_translation=show_translation,
            )
        if entry.channel == "peer":
            return self._build_peer_block(
                entry,
                show_peer_original=show_peer_original,
                peer_presentation_refresh_burst=peer_presentation_refresh_burst,
            )
        return self._build_finalized_block(
            entry,
            show_translation=show_translation,
            self_presentation_refresh_burst=self_presentation_refresh_burst,
        )

    # --- Sub-methods (3 branches) ---

    def _build_active_self_block(
        self,
        entry: OverlayPresentationEntry,
        *,
        show_translation: bool,
    ) -> OverlayPresentationBlock | None:
        primary_text = entry.live_text.strip()
        if not primary_text:
            return None
        live_secondary_text = entry.live_secondary_text.strip()
        secondary_text = live_secondary_text or entry.translation_text.strip()
        secondary_language = (
            entry.live_secondary_language if live_secondary_text else entry.translation_language
        )
        if live_secondary_text:
            update_id = entry.live_update_id
            origin_wall_clock_ms = entry.live_origin_wall_clock_ms
            session_scope = entry.live_session_scope
            source_text_hash = entry.live_source_text_hash
            source_text_len = entry.live_source_text_len
            logical_turn_key = entry.live_logical_turn_key
        else:
            update_id = entry.translation_update_id
            origin_wall_clock_ms = entry.translation_origin_wall_clock_ms
            session_scope = entry.translation_session_scope
            source_text_hash = entry.translation_source_text_hash
            source_text_len = entry.translation_source_text_len
            logical_turn_key = entry.translation_logical_turn_key
        return OverlayPresentationBlock(
            id=entry.block_id,
            occupant_key=entry.occupant_key,
            appearance_seq=RefreshManager.block_appearance_seq(
                entry.appearance_seq,
                entry.first_input_seq,
                entry.last_updated_seq,
            ),
            channel="self",
            block_variant="active_self",
            primary_text=primary_text,
            secondary_text=secondary_text,
            secondary_enabled=show_translation,
            primary_language=line_language(entry.live_primary_language, primary_text),
            secondary_language=line_language(
                secondary_language,
                secondary_text,
                enabled=show_translation,
            ),
            update_id=update_id,
            origin_wall_clock_ms=origin_wall_clock_ms,
            session_scope=session_scope,
            source_text_hash=source_text_hash,
            source_text_len=source_text_len,
            logical_turn_key=logical_turn_key,
        )

    def _build_peer_block(
        self,
        entry: OverlayPresentationEntry,
        *,
        show_peer_original: bool,
        peer_presentation_refresh_burst: bool,
    ) -> OverlayPresentationBlock | None:
        # _build_peer_block: priority order — translated_text > active_text > original_text — each branch has different block_variant
        translated_text = entry.translation_text.strip()
        original_text = entry.original_text.strip() or entry.live_text.strip()
        if translated_text:
            return OverlayPresentationBlock(
                id=entry.block_id,
                occupant_key=entry.occupant_key,
                appearance_seq=(
                    entry.appearance_seq
                    if entry.appearance_seq is not None
                    else RefreshManager.block_appearance_seq(
                        entry.appearance_seq,
                        entry.first_input_seq,
                        entry.last_updated_seq,
                    )
                ),
                channel="peer",
                block_variant="finalized",
                primary_text=translated_text,
                secondary_text=original_text,
                secondary_enabled=show_peer_original and bool(original_text),
                primary_language=line_language(
                    entry.translation_language,
                    translated_text,
                ),
                secondary_language=line_language(
                    entry.original_language,
                    original_text,
                    enabled=show_peer_original,
                ),
                update_id=entry.translation_update_id,
                origin_wall_clock_ms=entry.translation_origin_wall_clock_ms,
                session_scope=self._refresh.peer_session_scope_with_presentation_refresh(
                    entry.channel,
                    entry.utterance_id,
                    entry.translation_session_scope,
                    peer_presentation_refresh_burst=peer_presentation_refresh_burst,
                ),
                source_text_hash=entry.translation_source_text_hash,
                source_text_len=entry.translation_source_text_len,
                logical_turn_key=entry.translation_logical_turn_key,
            )
        active_text = entry.live_text.strip()
        if active_text:
            if not show_peer_original:
                return None
            return OverlayPresentationBlock(
                id=entry.block_id,
                occupant_key=entry.occupant_key,
                appearance_seq=RefreshManager.block_appearance_seq(
                    entry.appearance_seq,
                    entry.first_input_seq,
                    entry.last_updated_seq,
                ),
                channel="peer",
                block_variant="active_peer",
                primary_text="",
                secondary_text=active_text,
                secondary_enabled=True,
                primary_language=None,
                secondary_language=line_language(entry.original_language, active_text),
                session_scope=self._refresh.peer_session_scope_with_presentation_refresh(
                    entry.channel,
                    entry.utterance_id,
                    None,
                    peer_presentation_refresh_burst=peer_presentation_refresh_burst,
                ),
            )
        if original_text:
            if not show_peer_original:
                return None
            return OverlayPresentationBlock(
                id=entry.block_id,
                occupant_key=entry.occupant_key,
                appearance_seq=(
                    entry.appearance_seq
                    if entry.appearance_seq is not None
                    else RefreshManager.block_appearance_seq(
                        entry.appearance_seq,
                        entry.first_input_seq,
                        entry.last_updated_seq,
                    )
                ),
                channel="peer",
                block_variant="finalized",
                primary_text="",
                secondary_text=original_text,
                secondary_enabled=True,
                primary_language=None,
                secondary_language=line_language(entry.original_language, original_text),
                session_scope=self._refresh.peer_session_scope_with_presentation_refresh(
                    entry.channel,
                    entry.utterance_id,
                    None,
                    peer_presentation_refresh_burst=peer_presentation_refresh_burst,
                ),
            )
        return None

    def _build_finalized_block(
        self,
        entry: OverlayPresentationEntry,
        *,
        show_translation: bool,
        self_presentation_refresh_burst: bool,
    ) -> OverlayPresentationBlock | None:
        primary_text = entry.original_text.strip()
        if not primary_text:
            return None
        secondary_text = entry.translation_text.strip()
        secondary_enabled = show_translation

        return OverlayPresentationBlock(
            id=entry.block_id,
            occupant_key=entry.occupant_key,
            appearance_seq=entry.appearance_seq,
            channel=entry.channel,  # type: ignore[arg-type]
            block_variant="finalized",
            primary_text=primary_text,
            secondary_text=secondary_text,
            secondary_enabled=secondary_enabled,
            primary_language=line_language(entry.original_language, primary_text),
            secondary_language=line_language(
                entry.translation_language,
                secondary_text,
                enabled=secondary_enabled,
            ),
            update_id=entry.translation_update_id,
            origin_wall_clock_ms=entry.translation_origin_wall_clock_ms,
            session_scope=self._refresh.self_session_scope_with_presentation_refresh(
                entry.channel,
                entry.utterance_id,
                entry.translation_session_scope,
                primary_text=primary_text,
                block_variant="finalized",
                self_presentation_refresh_burst=self_presentation_refresh_burst,
            ),
            source_text_hash=entry.translation_source_text_hash,
            source_text_len=entry.translation_source_text_len,
            logical_turn_key=entry.translation_logical_turn_key,
        )

    # --- Signatures ---

    @staticmethod
    def _session_scope_has_presentation_refresh_marker(
        session_scope: str | None,
        *,
        marker_prefix: str,
    ) -> bool:
        if session_scope is None:
            return False
        return any(part.startswith(marker_prefix) for part in session_scope.split("|"))

    def rendered_block_signature(
        self,
        block: OverlayPresentationBlock,
    ) -> tuple[object, ...]:
        secondary_text = block.secondary_text if block.secondary_enabled else ""
        include_translation_metadata = block.channel == "peer" or bool(secondary_text)
        # rendered_block_signature: includes session_scope ONLY for self+finalized+refresh — this forces re-render during burst
        include_self_refresh_metadata = (
            block.channel == "self"
            and block.block_variant == "finalized"
            and self._session_scope_has_presentation_refresh_marker(
                block.session_scope,
                marker_prefix="self_presentation_refresh=",
            )
        )
        return (
            block.id,
            block.occupant_key,
            block.appearance_seq,
            block.channel,
            block.block_variant,
            block.primary_text,
            secondary_text,
            block.secondary_enabled,
            block.primary_language,
            block.secondary_language if block.secondary_enabled else None,
            block.update_id if include_translation_metadata else None,
            block.origin_wall_clock_ms if include_translation_metadata else None,
            (
                block.session_scope
                if include_translation_metadata or include_self_refresh_metadata
                else None
            ),
            block.source_text_hash if include_translation_metadata else None,
            block.source_text_len if include_translation_metadata else None,
            block.logical_turn_key if include_translation_metadata else None,
        )

    def rendered_blocks_signature(
        self,
        blocks: list[OverlayPresentationBlock],
    ) -> tuple[object, ...]:
        return tuple(self.rendered_block_signature(block) for block in blocks)

    def visible_block_content_signature(
        self,
        block: OverlayPresentationBlock,
    ) -> tuple[str, str, str, bool]:
        # visible_block_content_signature: lighter-weight than rendered_block_signature — used for self-burst dedup and TTL refresh
        secondary_text = block.secondary_text if block.secondary_enabled else ""
        return (
            block.block_variant,
            block.primary_text,
            secondary_text,
            block.secondary_enabled,
        )
