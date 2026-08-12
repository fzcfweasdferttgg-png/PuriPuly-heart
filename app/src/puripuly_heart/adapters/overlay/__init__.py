"""Adapters for overlay subsystem — infrastructure implementations."""
from puripuly_heart.adapters.overlay.process_utils import assign_process_to_job, write_overlay_manifest

__all__ = [
    "assign_process_to_job",
    "write_overlay_manifest",
]
