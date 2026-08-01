from __future__ import annotations

_CLOSED_TOMBSTONE_LIMIT = 64
LATE_ARRIVAL_WINDOW_SECONDS = 5.0
VISIBLE_TTL_SECONDS = 8.0
SELF_TRANSLATION_MIN_VISIBLE_SECONDS = 4.0
# LOAD-BEARING: The peer presentation refresh burst is product-permanent unless
# Stage 2 HMD QA proves an alternative. The 2026-04-28 submit-only resubmit
# regression showed repeated stored-frame SetOverlayTexture calls are not
# equivalent; each cadence tick must drive fresh snapshot/render/GPU work.
PEER_PRESENTATION_REFRESH_BURST_SECONDS = 2.0
PEER_PRESENTATION_REFRESH_BURST_INTERVAL_SECONDS = 0.1
