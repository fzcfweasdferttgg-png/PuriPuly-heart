"""Infrastructure utilities for overlay process management — file I/O and OS-level operations."""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def write_overlay_manifest(manifest_dict: dict[str, Any]) -> Path:
    """Write overlay launch manifest to a temporary JSON file.

    Returns path to the created manifest file.
    Caller is responsible for cleanup.
    """
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".json",
        prefix="puripuly-overlay-", delete=False,
    )
    try:
        json.dump(manifest_dict, handle)
        handle.flush()
        return Path(handle.name)
    finally:
        handle.close()


def assign_process_to_job(pid: int, job_handle: int | None) -> None:
    """Assign a Windows process to a Job Object for cleanup on exit."""
    if job_handle is None:
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        proc = kernel32.OpenProcess(0x1F0FFF, False, pid)
        if proc:
            kernel32.AssignProcessToJobObject(job_handle, proc)
            kernel32.CloseHandle(proc)
    except Exception:
        logger.debug("Failed to assign PID %s to job handle", pid, exc_info=True)


def copy_file(source: Path, destination: Path) -> None:
    """Copy file from source to destination, creating parent directories if needed."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
