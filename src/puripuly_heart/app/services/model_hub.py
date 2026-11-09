"""Model source providers: Hugging Face (HTTP API) and ModelScope (SDK).

The two hubs are separate providers with their own catalogs, not mirrors of
each other. Hugging Face supports free-text search; ModelScope is addressed by
repository id, link or owner (its public search API does not exist), and its
side runs through the installed ``modelscope`` SDK.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

logger = logging.getLogger(__name__)

HF_API = "https://huggingface.co"
SEARCH_LIMIT = 20
_REQUEST_TIMEOUT = httpx.Timeout(30.0, read=300.0)
_CHUNK_SIZE = 1024 * 256

PROVIDER_HF = "hf"
PROVIDER_MS = "ms"


class ModelRepoError(RuntimeError):
    def __init__(self, message: str, *, failure_code: str = "download_failed") -> None:
        super().__init__(message)
        self.failure_code = failure_code


class _CancelDownload(BaseException):
    """Escapes the SDK retry loop (which only catches Exception) on cancel."""


@dataclass(frozen=True, slots=True)
class ModelRepo:
    repo_id: str
    source: str
    name: str
    downloads: int | None = None

    @property
    def source_key(self) -> str:
        return "huggingface" if self.source == PROVIDER_HF else "modelscope"


@dataclass(frozen=True, slots=True)
class RepoFile:
    path: str
    size_bytes: int
    sha256: str | None = None

    @property
    def name(self) -> str:
        name = self.path.rsplit("/", 1)[-1]
        if not name or name in {".", ".."} or "\\" in name or Path(name).name != name:
            raise ModelRepoError("Unsafe repository file name", failure_code="invalid_file")
        return name


RepoProgressCallback = Callable[[int, int | None], None]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------- ModelScope SDK seams (lazy import: the dev venv has no modelscope) ----------


def _ms_list_models(owner: str, *, limit: int) -> list[dict]:
    from modelscope.hub.api import HubApi as ModelScopeApi

    result = ModelScopeApi().list_models(owner_or_group=owner, page_number=1, page_size=limit)
    return list((result or {}).get("Models") or ())


def _ms_get_model_files(repo_id: str) -> list[dict]:
    from modelscope.hub.api import HubApi as ModelScopeApi

    return list(ModelScopeApi().get_model_files(repo_id, recursive=True) or [])


def _ms_download_file(
    repo_id: str,
    file_path: str,
    *,
    file_name: str,
    file_size: int,
    local_dir: Path,
    on_update: Callable[[int, int | None], None],
    cancel_event: asyncio.Event | None,
) -> None:
    from modelscope.hub.callback import ProgressCallback
    from modelscope.hub.file_download import get_file_download_url, http_get_model_file

    class _Bridge(ProgressCallback):
        def __init__(self, filename: str, size: int) -> None:
            super().__init__(filename, size)
            self.transferred = 0

        def update(self, size: int) -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise _CancelDownload()
            self.transferred += size
            on_update(self.transferred, self.file_size or None)

        def end(self) -> None:
            pass

    url = get_file_download_url(repo_id, file_path, "master")
    http_get_model_file(
        url,
        str(local_dir),
        file_name,
        file_size,
        None,
        disable_tqdm=True,
        progress_callbacks=[_Bridge],
    )


def _ms_repo_id_from_query(query: str) -> str | None:
    """Accept a ModelScope link or a bare owner/name pair."""
    text = query.strip()
    if "modelscope.cn" in text:
        parts = [part for part in urlparse(text).path.split("/") if part]
        for marker in ("models", "datasets"):
            if marker in parts:
                index = parts.index(marker)
                if len(parts) >= index + 3:
                    return f"{parts[index + 1]}/{parts[index + 2]}"
        return None
    if text.count("/") == 1 and " " not in text:
        return text
    return None


# ---------- public API ----------


async def search_repos(
    query: str,
    *,
    source: str = PROVIDER_HF,
    limit: int = SEARCH_LIMIT,
    client: httpx.AsyncClient | None = None,
) -> tuple[ModelRepo, ...]:
    """Search one provider: free text on HF, repo id / owner on ModelScope."""
    query = query.strip()
    if not query:
        return ()
    if source == PROVIDER_MS:
        return await asyncio.to_thread(_ms_search, query, limit)
    return await _hf_search(query, limit=limit, client=client)


def _ms_search(query: str, limit: int) -> tuple[ModelRepo, ...]:
    try:
        repo_id = _ms_repo_id_from_query(query)
        if repo_id is not None:
            return (
                ModelRepo(repo_id=repo_id, source=PROVIDER_MS, name=repo_id.rsplit("/", 1)[-1]),
            )
        items = _ms_list_models(query, limit=limit)
    except Exception as error:
        raise ModelRepoError(str(error), failure_code="connection_failed") from error
    repos = [
        ModelRepo(
            repo_id=str(item.get("Id") or ""),
            source=PROVIDER_MS,
            name=str(item.get("Name") or ""),
            downloads=int(item["Downloads"]) if isinstance(item.get("Downloads"), int) else None,
        )
        for item in items
        if str(item.get("Id") or "").count("/") == 1
    ]
    repos.sort(key=lambda repo: repo.downloads or 0, reverse=True)
    return tuple(repos)


async def _hf_search(
    query: str,
    *,
    limit: int,
    client: httpx.AsyncClient | None = None,
) -> tuple[ModelRepo, ...]:
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
                source=PROVIDER_HF,
                name=repo_id.rsplit("/", 1)[-1],
                downloads=int(downloads) if isinstance(downloads, int) else None,
            )
        )
    repos.sort(key=lambda repo: repo.downloads or 0, reverse=True)
    return tuple(repos)


async def list_repos(
    owner: str,
    *,
    source: str = PROVIDER_HF,
    limit: int = SEARCH_LIMIT,
    client: httpx.AsyncClient | None = None,
) -> tuple[ModelRepo, ...]:
    """List one owner's repositories on a provider."""
    owner = owner.strip()
    if not owner:
        return ()
    if source == PROVIDER_MS:
        return await asyncio.to_thread(_ms_search, owner, limit)
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, follow_redirects=True)
    try:
        response = await client.get(
            f"{HF_API}/api/models",
            params={"author": owner, "filter": "gguf", "limit": limit},
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
    repos: list[ModelRepo] = []
    for item in payload if isinstance(payload, list) else ():
        if not isinstance(item, dict):
            continue
        repo_id = str(item.get("modelId") or item.get("id") or "")
        if not repo_id:
            continue
        downloads = item.get("downloads")
        repos.append(
            ModelRepo(
                repo_id=repo_id,
                source=PROVIDER_HF,
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
    """List the GGUF files of a repository on its provider."""
    if repo.source == PROVIDER_MS:
        files = await asyncio.to_thread(_ms_list_gguf_files, repo.repo_id)
    else:
        files = await _hf_list_gguf_files(repo.repo_id, client=client)
    return tuple(file for file in files if file.path.lower().endswith(".gguf"))


def _ms_list_gguf_files(repo_id: str) -> tuple[RepoFile, ...]:
    try:
        entries = _ms_get_model_files(repo_id)
    except Exception as error:
        raise ModelRepoError(str(error), failure_code="connection_failed") from error
    return tuple(
        RepoFile(
            path=str(item["Path"]),
            size_bytes=int(item.get("Size") or 0),
            sha256=(str(item["Sha256"]).lower() if item.get("Sha256") else None),
        )
        for item in entries
        if str(item.get("Path") or "") and int(item.get("Size") or 0) > 0
    )


async def _hf_list_gguf_files(
    repo_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[RepoFile, ...]:
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, follow_redirects=True)
    try:
        response = await client.get(
            f"{HF_API}/api/models/{quote(repo_id, safe='/')}/tree/main",
            params={"recursive": "true"},
        )
        response.raise_for_status()
        payload = response.json()
        entries = payload if isinstance(payload, list) else ()
        files = [
            RepoFile(
                path=str(item["path"]),
                size_bytes=int(item.get("size") or 0),
                sha256=(
                    str(
                        (item.get("lfs") or {}).get("sha256") or (item.get("lfs") or {}).get("oid")
                    ).lower()
                    if (item.get("lfs") or {}).get("sha256") or (item.get("lfs") or {}).get("oid")
                    else None
                ),
            )
            for item in entries
            if isinstance(item, dict) and item.get("type") == "file"
        ]
    except httpx.HTTPError as error:
        raise ModelRepoError(str(error), failure_code="connection_failed") from error
    except (KeyError, TypeError, ValueError) as error:
        raise ModelRepoError(str(error), failure_code="download_failed") from error
    finally:
        if owns_client:
            await client.aclose()
    return tuple(files)


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
    if repo.source == PROVIDER_MS:
        return await _ms_download(
            repo,
            file,
            target=target,
            target_dir=target_dir,
            cancel_event=cancel_event,
            on_progress=on_progress,
        )
    return await _hf_download(
        repo,
        file,
        target=target,
        target_dir=target_dir,
        cancel_event=cancel_event,
        on_progress=on_progress,
        client=client,
    )


async def _ms_download(
    repo: ModelRepo,
    file: RepoFile,
    *,
    target: Path,
    target_dir: Path,
    cancel_event: asyncio.Event | None,
    on_progress: RepoProgressCallback | None,
) -> Path:
    def on_update(transferred: int, total: int | None) -> None:
        if on_progress is not None:
            on_progress(transferred, total)

    try:
        await asyncio.to_thread(
            _ms_download_file,
            repo.repo_id,
            file.path,
            file_name=file.name,
            file_size=file.size_bytes,
            local_dir=target_dir,
            on_update=on_update,
            cancel_event=cancel_event,
        )
    except _CancelDownload:
        target.unlink(missing_ok=True)
        raise ModelRepoError("cancelled", failure_code="cancelled")
    except Exception as error:
        target.unlink(missing_ok=True)
        raise ModelRepoError(str(error), failure_code="connection_failed") from error
    if file.size_bytes and (not target.is_file() or target.stat().st_size != file.size_bytes):
        target.unlink(missing_ok=True)
        raise ModelRepoError("file size mismatch", failure_code="file_size_mismatch")
    if file.sha256 and await asyncio.to_thread(_file_sha256, target) != file.sha256.lower():
        target.unlink(missing_ok=True)
        raise ModelRepoError("checksum mismatch", failure_code="checksum_failed")
    return target


async def _hf_download(
    repo: ModelRepo,
    file: RepoFile,
    *,
    target: Path,
    target_dir: Path,
    cancel_event: asyncio.Event | None,
    on_progress: RepoProgressCallback | None,
    client: httpx.AsyncClient | None = None,
) -> Path:
    part = target_dir / (file.name + ".part")
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, follow_redirects=True)
    downloaded = 0
    quoted = quote(file.path, safe="/")
    url = f"{HF_API}/{quote(repo.repo_id, safe='/')}/resolve/main/{quoted}"
    try:
        async with client.stream("GET", url) as response:
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
    if file.sha256 and await asyncio.to_thread(_file_sha256, part) != file.sha256.lower():
        part.unlink(missing_ok=True)
        raise ModelRepoError("checksum mismatch", failure_code="checksum_failed")
    part.replace(target)
    return target


__all__ = [
    "PROVIDER_HF",
    "PROVIDER_MS",
    "ModelRepo",
    "ModelRepoError",
    "RepoFile",
    "download_file",
    "list_gguf_files",
    "list_repos",
    "search_repos",
]
