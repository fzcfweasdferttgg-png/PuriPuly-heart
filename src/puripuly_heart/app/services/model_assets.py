from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from puripuly_heart.app.services.model_assets_catalog import ModelAssetChoice, model_asset_choices
from puripuly_heart.app.services.model_hub import (
    ModelRepo,
    ModelRepoError,
    RepoFile,
)
from puripuly_heart.app.services.model_hub import (
    download_file as hub_download_file,
)
from puripuly_heart.app.services.model_hub import (
    list_gguf_files as hub_list_gguf_files,
)
from puripuly_heart.app.services.model_hub import (
    list_repos as hub_list_repos,
)
from puripuly_heart.app.services.model_hub import (
    search_repos as hub_search_repos,
)
from puripuly_heart.core.feature_diagnostics import log_feature
from puripuly_heart.core.local_asr.local_asr_provisioning import LocalASRInstallRequest
from puripuly_heart.core.local_asr.local_stt_assets import (
    LOCAL_QWEN_GPU_MODEL_ID,
    default_local_stt_model_root,
    inspect_local_stt_install_state,
    load_local_stt_asset_manifest,
)
from puripuly_heart.core.local_asr.model_registry import (
    REGISTRY_STATUS_INSTALLED,
    ModelRegistry,
    RegistryModelFile,
    RegistryModelRecord,
    file_sha256,
)
from puripuly_heart.core.model_download_sources import MODEL_DOWNLOAD_SOURCES
from puripuly_heart.core.model_install_removal import remove_model_install_dir

MODEL_ASSETS_ORIGIN = "model_assets"
_INTEGRITY_STATUS = {"ready": "installed", "missing": "missing", "invalid": "invalid"}
_COPY_CHUNK = 1024 * 1024


class ModelAssetsError(RuntimeError):
    def __init__(self, message: str, *, failure_code: str) -> None:
        super().__init__(message)
        self.failure_code = failure_code


@dataclass(frozen=True, slots=True)
class ModelAssetEntry:
    model_id: str
    name: str
    description_key: str
    purpose: str
    size_bytes: int
    status: str
    progress_percent: int | None = None
    failure_code: str | None = None
    importable: bool = False
    required_files: tuple[str, ...] = ()
    origin: str = "catalog"
    files: tuple[str, ...] = ()
    source: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "name": self.name,
            "purpose": self.purpose,
            "size_bytes": self.size_bytes,
            "status": self.status,
            "progress_percent": self.progress_percent,
            "failure_code": self.failure_code,
            "importable": self.importable,
            "required_files": list(self.required_files),
            "origin": self.origin,
            "files": list(self.files),
            "source": self.source,
        }


@dataclass(slots=True)
class _Operation:
    model_id: str
    kind: str
    progress_percent: int | None = None


@lru_cache(maxsize=None)
def _choice_files(choice: ModelAssetChoice) -> tuple[str, ...]:
    """File names behind one catalog choice, for cross-source dedupe."""
    if choice.purpose == "translation":
        return tuple(choice.required_files)
    try:
        manifest = load_local_stt_asset_manifest(choice.model_id)
    except Exception:
        return ()
    return tuple(Path(item.relative_path).name for item in manifest.files)


def _failure_code(error: BaseException) -> str:
    code = getattr(error, "failure_code", None)
    if isinstance(code, str) and code.isascii() and code.replace("_", "").isalnum():
        return code
    return "unknown"


@dataclass(slots=True)
class ModelAssetsApplication:
    provisioning: Callable[[], object | None]
    gemma: Callable[[], object | None]
    locale: Callable[[], str | None]
    active_model_ids: Callable[[], frozenset[str]] = field(default=lambda: frozenset())
    model_root: Path | None = None
    choices: tuple[ModelAssetChoice, ...] = field(default_factory=model_asset_choices)
    _integrity: dict[str, str] = field(init=False, default_factory=dict)
    _failures: dict[str, str] = field(init=False, default_factory=dict)
    _operation: _Operation | None = field(init=False, default=None)
    _cancel_requested: bool = field(init=False, default=False)
    _cancel_event: asyncio.Event | None = field(init=False, default=None)

    def entries(self) -> tuple[ModelAssetEntry, ...]:
        entries: list[ModelAssetEntry] = []
        for choice in self.choices:
            if choice.purpose == "translation":
                continue
            status = self._integrity.get(choice.model_id, "missing")
            progress = None
            operation = self._operation
            if operation is not None and operation.model_id == choice.model_id:
                status = {"delete": "deleting", "import": "importing"}.get(
                    operation.kind, "downloading"
                )
                progress = operation.progress_percent
            entries.append(
                ModelAssetEntry(
                    model_id=choice.model_id,
                    name=choice.name,
                    description_key=choice.description_key,
                    purpose=choice.purpose,
                    size_bytes=choice.size_bytes,
                    status=status,
                    progress_percent=progress,
                    failure_code=self._failures.get(choice.model_id),
                    importable=choice.importable,
                    required_files=choice.required_files,
                    files=_choice_files(choice),
                )
            )
        for record in self._registry().load():
            status = self._integrity.get(record.model_id) or self._registry().status(record)
            progress = None
            operation = self._operation
            if operation is not None and operation.model_id == record.model_id:
                status = {"delete": "deleting", "import": "importing"}.get(
                    operation.kind, "downloading"
                )
                progress = operation.progress_percent
            entries.append(
                ModelAssetEntry(
                    model_id=record.model_id,
                    name=record.name,
                    description_key="",
                    purpose="asr",
                    size_bytes=record.size_bytes,
                    status=status,
                    progress_percent=progress,
                    failure_code=self._failures.get(record.model_id),
                    origin=record.origin,
                    files=tuple(file.name for file in record.files),
                    source=record.source,
                )
            )
        return tuple(entries)

    def entry(self, model_id: str) -> ModelAssetEntry:
        for item in self.entries():
            if item.model_id == model_id:
                return item
        choice = self._choice(model_id)
        status = self._integrity.get(model_id) or self._inspect_one(choice)
        progress = None
        operation = self._operation
        if operation is not None and operation.model_id == model_id:
            status = {"delete": "deleting", "import": "importing"}.get(
                operation.kind, "downloading"
            )
            progress = operation.progress_percent
        return ModelAssetEntry(
            model_id=choice.model_id,
            name=choice.name,
            description_key=choice.description_key,
            purpose=choice.purpose,
            size_bytes=choice.size_bytes,
            status=status,
            progress_percent=progress,
            failure_code=self._failures.get(model_id),
            importable=choice.importable,
            required_files=choice.required_files,
            files=_choice_files(choice),
        )

    @property
    def busy(self) -> bool:
        return self._operation is not None

    @property
    def operation_progress(self) -> tuple[str, str, int | None] | None:
        operation = self._operation
        if operation is None:
            return None
        return (operation.model_id, operation.kind, operation.progress_percent)

    async def refresh(self) -> tuple[ModelAssetEntry, ...]:
        self._integrity = await asyncio.to_thread(self._inspect_all)
        return self.entries()

    async def download(self, model_id: str, *, source: str | None = None) -> ModelAssetEntry:
        choice = self._choice(model_id)
        if source is not None and source not in MODEL_DOWNLOAD_SOURCES:
            raise ModelAssetsError("Unsupported model source", failure_code="unsupported_source")
        return await self._run(choice, "download", source=source)

    async def import_directory(self, model_id: str, directory: Path) -> ModelAssetEntry:
        choice = self._choice(model_id)
        if not choice.importable:
            raise ModelAssetsError(
                "Model does not support import", failure_code="unsupported_source"
            )
        files = tuple(directory / name for name in choice.required_files)
        missing = [path.name for path in files if not path.is_file()]
        if missing:
            self._failures[model_id] = "missing_file"
            log_feature("ModelDownload", "preflight", "missing_file")
            raise ModelAssetsError(
                "Missing model files: " + ", ".join(missing), failure_code="missing_file"
            )
        return await self._run(choice, "import", source="local", files=files)

    async def search_hub(self, query: str, source: str = "hf") -> tuple[ModelRepo, ...]:
        try:
            return await hub_search_repos(query, source=source)
        except ModelRepoError as error:
            raise ModelAssetsError(str(error), failure_code=error.failure_code) from error

    async def list_model_repos(self, owner: str, source: str) -> tuple[ModelRepo, ...]:
        try:
            return await hub_list_repos(owner, source=source)
        except ModelRepoError as error:
            raise ModelAssetsError(str(error), failure_code=error.failure_code) from error

    async def install_catalog_model(self, model_id: str, quant: str) -> ModelAssetEntry:
        from puripuly_heart.core.transcribe_catalog import load_transcribe_catalog

        model = next(
            (item for item in load_transcribe_catalog() if item.model_id == model_id),
            None,
        )
        if model is None:
            raise ModelAssetsError("Unknown model", failure_code="unknown_model")
        download = model.download(quant)
        if download is None:
            raise ModelAssetsError("Unknown quant", failure_code="unknown_model")
        repo = ModelRepo(repo_id=model.repo, source="hf", name=model.name)
        remote_files = await self.hub_files(repo)
        remote = next((item for item in remote_files if item.name == download.filename), None)
        return await self.install_hub_file(
            repo,
            remote or RepoFile(path=download.filename, size_bytes=download.size_bytes),
        )

    async def hub_files(self, repo: ModelRepo) -> tuple[RepoFile, ...]:
        try:
            return await hub_list_gguf_files(repo)
        except ModelRepoError as error:
            raise ModelAssetsError(str(error), failure_code=error.failure_code) from error

    async def install_hub_file(self, repo: ModelRepo, file: RepoFile) -> ModelAssetEntry:
        registry = self._registry()
        root = self.model_root or default_local_stt_model_root()
        stem = Path(file.name).stem
        self._require_file_not_installed(file.name)
        model_id = registry.allocate_model_id(stem, reserved=self._reserved_model_ids())

        async def action() -> None:
            target_dir = root / model_id

            def on_progress(done: int, total: int | None) -> None:
                if self._operation is not None and total:
                    self._operation.progress_percent = min(100, int(done * 100 / total))

            try:
                try:
                    path = await hub_download_file(
                        repo,
                        file,
                        target_dir=target_dir,
                        cancel_event=self._cancel_event,
                        on_progress=on_progress,
                    )
                except ModelRepoError as error:
                    raise ModelAssetsError(str(error), failure_code=error.failure_code) from error
                digest = file.sha256 or await asyncio.to_thread(file_sha256, path)
                registry.add(
                    RegistryModelRecord(
                        model_id=model_id,
                        name=stem,
                        files=(
                            RegistryModelFile(
                                name=path.name,
                                size_bytes=file.size_bytes,
                                sha256=digest,
                            ),
                        ),
                        origin="search",
                        source=f"{repo.source}:{repo.repo_id}",
                    )
                )
            except BaseException:
                shutil.rmtree(target_dir, ignore_errors=True)
                raise

        await self._run_operation(model_id, "download", action)
        return self.entry(model_id)

    async def add_local_file(self, path: Path) -> ModelAssetEntry:
        source = Path(path)
        if not source.is_file():
            raise ModelAssetsError("Missing model file", failure_code="missing_file")
        if source.suffix.lower() != ".gguf":
            raise ModelAssetsError("Not a GGUF model file", failure_code="invalid_file")
        self._require_file_not_installed(source.name)
        registry = self._registry()
        root = self.model_root or default_local_stt_model_root()
        model_id = registry.allocate_model_id(source.stem, reserved=self._reserved_model_ids())
        total = source.stat().st_size

        async def action() -> None:
            target_dir = root / model_id
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / source.name

            def copy() -> int:
                copied = 0
                with open(source, "rb") as reader, open(target, "wb") as writer:
                    while True:
                        if self._cancel_requested:
                            raise ModelAssetsError("cancelled", failure_code="cancelled")
                        chunk = reader.read(_COPY_CHUNK)
                        if not chunk:
                            break
                        writer.write(chunk)
                        copied += len(chunk)
                        if self._operation is not None and total:
                            self._operation.progress_percent = min(100, int(copied * 100 / total))
                return copied

            try:
                copied = await asyncio.to_thread(copy)
                digest = await asyncio.to_thread(file_sha256, target)
                registry.add(
                    RegistryModelRecord(
                        model_id=model_id,
                        name=source.stem,
                        files=(
                            RegistryModelFile(
                                name=source.name,
                                size_bytes=copied,
                                sha256=digest,
                            ),
                        ),
                        origin="local",
                        source=str(source),
                    )
                )
            except BaseException:
                target.unlink(missing_ok=True)
                shutil.rmtree(target_dir, ignore_errors=True)
                raise

        await self._run_operation(model_id, "import", action)
        return self.entry(model_id)

    async def cancel(self, model_id: str) -> bool:
        operation = self._operation
        if operation is None or operation.model_id != model_id or operation.kind == "delete":
            return False
        self._cancel_requested = True
        if self._cancel_event is not None:
            self._cancel_event.set()
        choice = self._find_choice(model_id)
        if choice is None:
            return True
        if choice.purpose == "translation":
            owner = self.gemma()
            if owner is not None:
                owner.cancel_assets(model_id)
        else:
            owner = self.provisioning()
            if owner is not None:
                await owner.cancel_install(self._backend(model_id), origin=MODEL_ASSETS_ORIGIN)
        return True

    async def delete(self, model_id: str) -> ModelAssetEntry:
        choice = self._find_choice(model_id)
        record = None if choice is not None else self._registry().get(model_id)
        if choice is None and record is None:
            raise ModelAssetsError("Unknown model", failure_code="unknown_model")
        if model_id in self.active_model_ids():
            self._failures[model_id] = "model_in_use"
            raise ModelAssetsError("Model is in use", failure_code="model_in_use")
        if choice is not None:
            return await self._run(choice, "delete")
        root = self.model_root or default_local_stt_model_root()
        registry = self._registry()

        async def action() -> None:
            await asyncio.to_thread(remove_model_install_dir, root / record.model_id)
            registry.remove(record.model_id)

        await self._run_operation(model_id, "delete", action)
        self._integrity.pop(model_id, None)
        return ModelAssetEntry(
            model_id=record.model_id,
            name=record.name,
            description_key="",
            purpose="asr",
            size_bytes=record.size_bytes,
            status="missing",
            origin=record.origin,
        )

    async def _run(
        self,
        choice: ModelAssetChoice,
        kind: str,
        *,
        source: str | None = None,
        files: tuple[Path, ...] = (),
    ) -> ModelAssetEntry:
        async def action() -> None:
            if kind == "delete":
                await self._delete(choice)
            elif choice.purpose == "translation":
                await self._install_gemma(choice, source=source, files=files)
            else:
                await self._install_asr(choice, source=source)

        await self._run_operation(choice.model_id, kind, action)
        return self.entry(choice.model_id)

    async def _run_operation(self, model_id: str, kind: str, action) -> None:
        if self._operation is not None:
            raise ModelAssetsError("Another model task is active", failure_code="task_busy")
        self._operation = _Operation(model_id, kind, 0 if kind != "delete" else None)
        self._cancel_requested = False
        self._cancel_event = asyncio.Event()
        self._failures.pop(model_id, None)
        try:
            await action()
        except ModelAssetsError as error:
            if error.failure_code != "cancelled":
                self._failures[model_id] = error.failure_code
            raise
        except Exception as error:
            code = "cancelled" if self._cancel_requested else _failure_code(error)
            if code != "cancelled":
                self._failures[model_id] = code
                log_feature("ModelDownload", kind, code, error=error)
            raise ModelAssetsError(str(error) or code, failure_code=code) from error
        finally:
            self._operation = None
            self._cancel_requested = False
            self._cancel_event = None
            try:
                self._integrity[model_id] = await asyncio.to_thread(
                    self._inspect_one_by_id, model_id
                )
            except Exception as error:
                log_feature(
                    "ModelDownload",
                    "inspection",
                    "inspect_failed",
                    error=error,
                    level=logging.WARNING,
                )

    async def _install_asr(self, choice: ModelAssetChoice, *, source: str | None) -> None:
        owner = self.provisioning()
        if owner is None:
            raise ModelAssetsError(
                "Model owner unavailable", failure_code="model_owner_unavailable"
            )
        backend = self._backend(choice.model_id)
        if owner.snapshot.activity_for(backend) is not None:
            raise ModelAssetsError("Another model task is active", failure_code="task_busy")

        def on_status(update) -> None:
            percent = getattr(update, "percent", None)
            if self._operation is not None and isinstance(percent, int):
                self._operation.progress_percent = percent

        result = await owner.start_install(
            LocalASRInstallRequest(
                backend=backend,
                model_ids=(choice.model_id,),
                locale=self.locale(),
                origin=MODEL_ASSETS_ORIGIN,
                explicit_gpu_intent=backend == "gpu",
                preferred_source=source,
            ),
            on_status=on_status,
        )
        if result.cancelled:
            raise ModelAssetsError("Model task cancelled", failure_code="cancelled")
        if result.failed_model_ids:
            code = result.failure_codes[0] if result.failure_codes else "unknown"
            raise ModelAssetsError("Model installation failed", failure_code=code)

    async def _install_gemma(
        self,
        choice: ModelAssetChoice,
        *,
        source: str | None,
        files: tuple[Path, ...],
    ) -> None:
        owner = self.gemma()
        if owner is None:
            raise ModelAssetsError(
                "Model owner unavailable", failure_code="model_owner_unavailable"
            )

        def on_status(update) -> None:
            if self._operation is None:
                return
            total = getattr(update, "total_bytes", 0) or 0
            done = getattr(update, "downloaded_bytes", 0) or 0
            if total:
                self._operation.progress_percent = min(100, int(done * 100 / total))

        await owner.install_assets(
            model_id=choice.model_id,
            source=source,
            files=files,
            on_status=on_status,
            locale=self.locale(),
        )

    async def _delete(self, choice: ModelAssetChoice) -> None:
        if choice.purpose == "translation":
            owner = self.gemma()
            if owner is None:
                raise ModelAssetsError(
                    "Model owner unavailable", failure_code="model_owner_unavailable"
                )
            await owner.delete_assets(choice.model_id)
            return
        owner = self.provisioning()
        if owner is None:
            raise ModelAssetsError(
                "Model owner unavailable", failure_code="model_owner_unavailable"
            )
        await owner.delete_model(choice.model_id)

    def _inspect_all(self) -> dict[str, str]:
        statuses = {choice.model_id: self._inspect_one(choice) for choice in self.choices}
        registry = self._registry()
        statuses.update({record.model_id: registry.status(record) for record in registry.load()})
        return statuses

    def _inspect_one_by_id(self, model_id: str) -> str:
        choice = self._find_choice(model_id)
        if choice is not None:
            return self._inspect_one(choice)
        record = self._registry().get(model_id)
        if record is None:
            return "missing"
        return self._registry().status(record)

    def _inspect_one(self, choice: ModelAssetChoice) -> str:
        if choice.purpose == "translation":
            owner = self.gemma()
            if owner is None:
                return "missing"
            status = owner.install_status(choice.model_id)
        else:
            manifest = load_local_stt_asset_manifest(choice.model_id)
            root = self.model_root or default_local_stt_model_root()
            status = inspect_local_stt_install_state(
                root / manifest.install_dirname,
                manifest=manifest,
            ).status
        return _INTEGRITY_STATUS.get(status, "invalid")

    def _registry(self) -> ModelRegistry:
        return ModelRegistry(self.model_root or default_local_stt_model_root())

    def _find_choice(self, model_id: str) -> ModelAssetChoice | None:
        for choice in self.choices:
            if choice.model_id == model_id:
                return choice
        return None

    def _reserved_model_ids(self) -> frozenset[str]:
        return frozenset(choice.model_id for choice in self.choices)

    def _require_file_not_installed(self, name: str) -> None:
        if name in self._installed_file_names():
            raise ModelAssetsError(
                "Model file is already in the library", failure_code="already_installed"
            )

    def _installed_file_names(self) -> frozenset[str]:
        """File names behind installed models — the invariant the UI applies to installs."""
        names: set[str] = set()
        registry = self._registry()
        for choice in self.choices:
            if choice.purpose != "translation" and self._inspect_one(choice) == "installed":
                names.update(_choice_files(choice))
        for record in registry.load():
            if registry.status(record) == REGISTRY_STATUS_INSTALLED:
                names.update(file.name for file in record.files)
        return frozenset(names)

    def _choice(self, model_id: str) -> ModelAssetChoice:
        choice = self._find_choice(model_id)
        if choice is None:
            raise ModelAssetsError("Unknown model", failure_code="unknown_model")
        return choice

    @staticmethod
    def _backend(model_id: str) -> str:
        return "gpu" if model_id == LOCAL_QWEN_GPU_MODEL_ID else "cpu"


__all__ = [
    "MODEL_ASSETS_ORIGIN",
    "ModelAssetEntry",
    "ModelAssetsApplication",
    "ModelAssetsError",
]
