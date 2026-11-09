"""ModelScope download adapter for local STT model assets.

Implements the same download port as the Hugging Face adapter by bridging to
the official ModelScope SDK (``modelscope.hub.file_download``).
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from puripuly_heart.core.local_asr.local_stt_download_port import (
    HuggingFaceDownloadProgress,
    HuggingFaceDownloadRequest,
    LocalSTTDownloadPortCancelled,
    LocalSTTDownloadPortError,
)


class _ModelScopeCancelled(BaseException):
    pass


def _download_blocking(
    request: HuggingFaceDownloadRequest,
    local_dir: Path,
    cancel_event: threading.Event | None,
    on_progress,
) -> Path:
    from modelscope.hub.callback import ProgressCallback
    from modelscope.hub.file_download import get_file_download_url, http_get_model_file

    class _Bridge(ProgressCallback):
        def __init__(self) -> None:
            super().__init__(request.remote_path, request.expected_size_bytes or 0)
            self.transferred = 0

        def update(self, size: int) -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise _ModelScopeCancelled()
            self.transferred += size
            if on_progress is not None:
                on_progress(
                    HuggingFaceDownloadProgress(
                        downloaded_bytes=self.transferred,
                        total_bytes=self.file_size or None,
                    )
                )

        def end(self) -> None:
            pass

    url = get_file_download_url(
        request.repo_id,
        request.remote_path,
        request.revision or "master",
        endpoint=request.endpoint or None,
    )
    http_get_model_file(
        url,
        str(local_dir),
        request.remote_path,
        request.expected_size_bytes or 0,
        None,
        disable_tqdm=True,
        progress_callbacks=[_Bridge],
    )
    return local_dir / request.remote_path


class ModelScopeDownloadAdapter:
    """Downloads pinned ModelScope repo files into a staging directory."""

    async def download(
        self,
        request: HuggingFaceDownloadRequest,
        *,
        cancel_event: threading.Event | None = None,
        on_progress=None,
    ) -> Path:
        if cancel_event is not None and cancel_event.is_set():
            raise LocalSTTDownloadPortCancelled("ModelScope download cancelled")
        local_dir = Path(request.local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        try:
            completed_path = await asyncio.to_thread(
                _download_blocking,
                request,
                local_dir,
                cancel_event,
                on_progress,
            )
        except _ModelScopeCancelled as exc:
            raise LocalSTTDownloadPortCancelled("ModelScope download cancelled") from exc
        except LocalSTTDownloadPortCancelled:
            raise
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise LocalSTTDownloadPortCancelled("ModelScope download cancelled") from exc
            raise LocalSTTDownloadPortError(
                f"ModelScope download failed: {exc}",
                failure_code="download_failed",
                cause_type=type(exc).__name__,
            ) from exc
        if cancel_event is not None and cancel_event.is_set():
            raise LocalSTTDownloadPortCancelled("ModelScope download cancelled")
        resolved = Path(completed_path)
        if on_progress is not None:
            total = request.expected_size_bytes or resolved.stat().st_size
            on_progress(
                HuggingFaceDownloadProgress(
                    downloaded_bytes=resolved.stat().st_size,
                    total_bytes=total,
                )
            )
        return resolved


__all__ = ["ModelScopeDownloadAdapter"]
