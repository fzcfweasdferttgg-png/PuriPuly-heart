from __future__ import annotations

"""Refresh manager for overlay presentation refresh bursts.

Extracted from OverlayPresentationState. Owns the four refresh fields
(target_key + nonce for peer/self) and the 11 methods that manipulate them.

RefreshManager forces the native overlay to re-render blocks even when the
text hasn't changed by injecting per-frame nonces into session_scope metadata.
Without this, revision-based dedup would suppress the GPU render and the HMD
would show stale frames.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID

from puripuly_heart.domain.overlay_types import OverlayEntryKey

if TYPE_CHECKING:
    from puripuly_heart.domain.overlay_types import OverlayPresentationSnapshot


class RefreshManager:
    """Manages presentation refresh burst lifecycle and nonce injection."""

    def __init__(
        self,
        *,
        on_snapshot: Callable[[], OverlayPresentationSnapshot],
    ) -> None:
        self._on_snapshot = on_snapshot
        self.peer_presentation_refresh_target_key: OverlayEntryKey | None = None
        self.peer_presentation_refresh_nonce: int = 0
        self.self_presentation_refresh_target_key: OverlayEntryKey | None = None
        self.self_presentation_refresh_nonce: int = 0

    # --- PEER REFRESH LIFECYCLE -----------------------------------------

    def begin_peer_presentation_refresh(self, key: OverlayEntryKey) -> bool:
        """Select the peer refresh target and reset any previous refresh nonce."""
        had_visible_marker = self._snapshot_has_peer_presentation_refresh_marker()
        self.peer_presentation_refresh_target_key = key
        self.peer_presentation_refresh_nonce = 0
        return had_visible_marker

    def tick_peer_presentation_refresh(self, key: OverlayEntryKey) -> bool:
        """Advance the load-bearing peer refresh nonce for the active target."""
        if self.peer_presentation_refresh_target_key != key:
            return False
        # LOAD-BEARING: peer_presentation_refresh=<n> prevents revision/dedup
        # coalescing during the product-permanent burst. Do not normalize this
        # away unless Stage 2 HMD QA proves an alternative fresh-render path.
        self.peer_presentation_refresh_nonce += 1
        return True

    def end_peer_presentation_refresh(self, key: OverlayEntryKey) -> bool:
        """Clear the peer refresh nonce and request cleanup publish if needed."""
        if self.peer_presentation_refresh_target_key != key:
            return False
        had_refresh_metadata = self._snapshot_has_peer_presentation_refresh_marker()
        self.peer_presentation_refresh_target_key = None
        self.peer_presentation_refresh_nonce = 0
        return had_refresh_metadata

    def _snapshot_has_peer_presentation_refresh_marker(self) -> bool:
        for block in self._on_snapshot().blocks:
            session_scope = block.session_scope
            if session_scope is None:
                continue
            if any(
                part.startswith("peer_presentation_refresh=")
                for part in session_scope.split("|")
            ):
                return True
        return False

    # --- SELF REFRESH LIFECYCLE ------------------------------------------

    def begin_self_presentation_refresh(self, key: OverlayEntryKey) -> bool:
        """Select the finalized self refresh target and reset any previous nonce."""
        had_visible_marker = self._snapshot_has_self_presentation_refresh_marker()
        self.self_presentation_refresh_target_key = key
        self.self_presentation_refresh_nonce = 0
        return had_visible_marker

    def tick_self_presentation_refresh(self, key: OverlayEntryKey) -> bool:
        """Advance the load-bearing self refresh nonce for the active target."""
        if self.self_presentation_refresh_target_key != key:
            return False
        # LOAD-BEARING: self_presentation_refresh=<n> must be revision-worthy
        # for finalized self rows, including source-only captions with no
        # secondary text, so the local overlay path receives fresh snapshots.
        self.self_presentation_refresh_nonce += 1
        return True

    def end_self_presentation_refresh(self, key: OverlayEntryKey) -> bool:
        """Clear the self refresh nonce and request cleanup publish if needed."""
        if self.self_presentation_refresh_target_key != key:
            return False
        had_refresh_metadata = self._snapshot_has_self_presentation_refresh_marker()
        self.self_presentation_refresh_target_key = None
        self.self_presentation_refresh_nonce = 0
        return had_refresh_metadata

    def _snapshot_has_self_presentation_refresh_marker(self) -> bool:
        for block in self._on_snapshot().blocks:
            if block.channel != "self":
                continue
            session_scope = block.session_scope
            if session_scope is None:
                continue
            if any(
                part.startswith("self_presentation_refresh=")
                for part in session_scope.split("|")
            ):
                return True
        return False

    # --- SESSION_SCOPE INJECTION -----------------------------------------

    def peer_session_scope_with_presentation_refresh(
        self,
        entry_channel: str,
        entry_utterance_id: UUID,
        session_scope: str | None,
        *,
        peer_presentation_refresh_burst: bool,
    ) -> str | None:
        if (
            not peer_presentation_refresh_burst
            or self.peer_presentation_refresh_nonce <= 0
            or self.peer_presentation_refresh_target_key
            != (entry_channel, entry_utterance_id)
        ):
            return session_scope
        # LOAD-BEARING: this marker is not cosmetic metadata. A submit-only
        # resubmit regression showed stored-frame resubmits are not equivalent
        # to fresh snapshot/render/GPU work, so each nonce value must produce
        # revision-worthy session_scope metadata for native to render.
        marker = f"peer_presentation_refresh={self.peer_presentation_refresh_nonce}"
        if session_scope:
            return f"{session_scope}|{marker}"
        return marker

    def self_session_scope_with_presentation_refresh(
        self,
        entry_channel: str,
        entry_utterance_id: UUID,
        session_scope: str | None,
        *,
        primary_text: str,
        block_variant: str,
        self_presentation_refresh_burst: bool,
    ) -> str | None:
        if (
            not self_presentation_refresh_burst
            or self.self_presentation_refresh_nonce <= 0
            or self.self_presentation_refresh_target_key
            != (entry_channel, entry_utterance_id)
            or entry_channel != "self"
            # block_variant == 'finalized' guard: self refresh only applies to finalized rows, not active rows.
            # Active self rows use different refresh path (live_text updates).
            or block_variant != "finalized"
            or not primary_text.strip()
        ):
            return session_scope
        marker = f"self_presentation_refresh={self.self_presentation_refresh_nonce}"
        if session_scope:
            return f"{session_scope}|{marker}"
        return marker

    # --- PURE UTILITY ----------------------------------------------------

    @staticmethod
    def block_appearance_seq(
        appearance_seq: int | None,
        first_input_seq: int | None,
        last_updated_seq: int,
    ) -> int:
        if appearance_seq is not None:
            return appearance_seq
        if first_input_seq is not None:
            return first_input_seq
        if last_updated_seq > 0:
            return last_updated_seq
        return 0
