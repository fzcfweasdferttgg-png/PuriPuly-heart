"""Overlay infrastructure protocol — file I/O and OS-level operations for overlay process management.

Defines the interface for infrastructure operations that OverlayProcessManager
needs but cannot perform itself (hexagonal architecture: core/ does not do I/O).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class OverlayInfrastructureProtocol(Protocol):
    """Infrastructure operations needed by OverlayProcessManager.

    Implementations live in adapters/overlay/infrastructure.py.
    Injected into core/overlay/process.py through DI.
    """

    def write_manifest(self, manifest_dict: dict[str, Any]) -> Path:
        """Write overlay launch manifest to a temporary JSON file.

        Returns path to the created manifest file.
        Caller is responsible for cleanup.
        """
        ...

    def assign_process_to_job(self, pid: int, job_handle: int | None) -> None:
        """Assign a Windows process to a Job Object for cleanup on exit."""
        ...

    def copy_file(self, source: Path, destination: Path) -> None:
        """Copy file from source to destination, creating parent directories if needed."""
        ...
