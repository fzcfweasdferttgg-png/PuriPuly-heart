"""Overlay timing constants for SteamVR subtitle presentation.

Used by presenter_entry_mgmt.py and presenter_refresh_burst.py
(mixins of OverlayPresenter).
"""

from __future__ import annotations

CLOSED_TOMBSTONE_LIMIT = 64
LATE_ARRIVAL_WINDOW_SECONDS = 5.0
VISIBLE_TTL_SECONDS = 8.0
# Must be ≤ VISIBLE_TTL_SECONDS — self-translation entries must not outlive
# their parent entry's TTL in presenter_entry_mgmt.
SELF_TRANSLATION_MIN_VISIBLE_SECONDS = 4.0
# LOAD-BEARING: The peer presentation refresh burst is product-permanent unless
# Stage 2 HMD QA proves an alternative. A submit-only resubmit regression
# showed repeated stored-frame SetOverlayTexture calls are not equivalent;
# each cadence tick must drive fresh snapshot/render/GPU work.
PEER_PRESENTATION_REFRESH_BURST_SECONDS = 2.0
PEER_PRESENTATION_REFRESH_BURST_INTERVAL_SECONDS = 0.1
