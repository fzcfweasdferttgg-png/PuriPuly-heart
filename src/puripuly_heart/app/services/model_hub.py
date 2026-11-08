"""Model source access: search on Hugging Face, file listing and download on HF/MS."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

HF_API = "https://huggingface.co"
MS_API = "https://modelscope.cn"

SEARCH_LIMIT = 20
_REQUEST_TIMEOUT = httpx.Timeout(30.0, read=300.0)
_CHUNK_SIZE = 1024 * 256


class ModelRepoError(RuntimeError):
    def __init__(self, message: str, *, failure_code: str = "download_failed") -> None:
        super().__init__(message)
        self.failure_code = failure_code


@dataclass(frozen=True, slots=True)
class ModelRepo:
    repo_id: str
    source: str
    name: str
    downloads: int | None = None

    @property
    def source_key(self) -> str:
        return "huggingface" if self.source == "hf" else "modelscope"


@dataclass(frozen=True, slots=True)
class RepoFile:
    path: str
    size_bytes: int

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


RepoProgressCallback = Callable[[int, int | None], None]


def _repo_url(repo: ModelRepo, path: str) -> str:
    quoted = path.replace(" ", "%20")
    if repo.source == "hf":
        return f"{HF_API}/{repo.repo_id}/resolve/main/{quoted}"
    return f"{MS_API}/api/v1/models/{repo.repo_id}/repo?Revision=master&FilePath={quoted}"


async def search_repos(
    query: str,
    *,
    limit: int = SEARCH_LIMIT,
    client: httpx.AsyncClient | None = None,
) -> tuple[ModelRepo, ...]:
    """Search Hugging Face for GGUF model repositories."""
    query = query.strip()
    if not query:
        return ()
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, follow_redirects=True)
    try:
        response = await client.get(
            f"{HF_API}/api/models",
            params={"search": query, "filter": "gguf", "limit": limit},
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as error:
        raise ModelRepoError(str(error), failure_code="connection_failed") from error
    except ValueError as error:
        raise ModelRepoError(str(error), failure_code="download_failed") from error
    finally:
        if owns_client:
            await client.aclose()
    if not isinstance(payload, list):
        return ()
    repos: list[ModelRepo] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        repo_id = str(item.get("modelId") or item.get("id") or "")
        if not repo_id:
            continue
        downloads = item.get("downloads")
        repos.append(
            ModelRepo(
                repo_id=repo_id,
                source="hf",
                name=repo_id.rsplit("/", 1)[-1],
                downloads=int(downloads) if isinstance(downloads, int) else None,
            )
        )
    repos.sort(key=lambda repo: repo.downloads or 0, reverse=True)
    return tuple(repos)


async def list_gguf_files(
    repo: ModelRepo,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[RepoFile, ...]:
    """List the GGUF files of a repository."""
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, follow_redirects=True)
    try:
        if repo.source == "hf":
            response = await client.get(
                f"{HF_API}/api/models/{repo.repo_id}/tree/main",
                params={"recursive": "true"},
            )
            response.raise_for_status()
            payload = response.json()
            entries = payload if isinstance(payload, list) else ()
            files = [
                RepoFile(path=str(item["path"]), size_bytes=int(item.get("size") or 0))
                for item in entries
                if isinstance(item, dict) and item.get("type") == "file"
            ]
        else:
            response = await client.get(
                f"{MS_API}/api/v1/models/{repo.repo_id}/repo/files",
                params={"Recursive": "true"},
            )
            response.raise_for_status()
            payload = response.json()
            entries = ((payload or {}).get("Data") or {}).get("Files") or ()
            files = [
                RepoFile(path=str(item["Path"]), size_bytes=int(item.get("Size") or 0))
                for item in entries
                if isinstance(item, dict) and item.get("Type") == "blob"
            ]
    except httpx.HTTPError as error:
        raise ModelRepoError(str(error), failure_code="connection_failed") from error
    except (KeyError, TypeError, ValueError) as error:
        raise ModelRepoError(str(error), failure_code="download_failed") from error
    finally:
        if owns_client:
            await client.aclose()
    return tuple(file for file in files if file.path.lower().endswith(".gguf"))


async def download_file(
    repo: ModelRepo,
    file: RepoFile,
    *,
    target_dir: Path,
    cancel_event: asyncio.Event | None = None,
    on_progress: RepoProgressCallback | None = None,
    client: httpx.AsyncClient | None = None,
) -> Path:
    """Download one repository file into target_dir and return its path."""
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / file.name
    part = target_dir / (file.name + ".part")
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, follow_redirects=True)
    downloaded = 0
    try:
        async with client.stream("GET", _repo_url(repo, file.path)) as response:
            response.raise_for_status()
            total = file.size_bytes or None
            header_total = response.headers.get("content-length")
            if header_total and header_total.isdigit():
                total = int(header_total)
            with open(part, "wb") as handle:
                async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                    if cancel_event is not None and cancel_event.is_set():
                        raise ModelRepoError("cancelled", failure_code="cancelled")
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if on_progress is not None:
                        on_progress(downloaded, total)
    except ModelRepoError:
        part.unlink(missing_ok=True)
        raise
    except httpx.HTTPError as error:
        part.unlink(missing_ok=True)
        raise ModelRepoError(str(error), failure_code="connection_failed") from error
    except OSError as error:
        part.unlink(missing_ok=True)
        code = "disk_full" if getattr(error, "errno", None) == 28 else "download_failed"
        raise ModelRepoError(str(error), failure_code=code) from error
    finally:
        if owns_client:
            await client.aclose()
    if file.size_bytes and downloaded != file.size_bytes:
        part.unlink(missing_ok=True)
        raise ModelRepoError("file size mismatch", failure_code="file_size_mismatch")
    part.replace(target)
    return target


__all__ = [
    "RepoFile",
    "ModelRepo",
    "ModelRepoError",
    "download_file",
    "list_gguf_files",
    "search_repos",
]
