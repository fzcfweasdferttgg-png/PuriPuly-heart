from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

from puripuly_heart.core.local_asr.model_registry import REGISTRY_FILENAME


class ModelInstallInUseError(RuntimeError):
    failure_code = "model_in_use"


def remove_model_install_dir(install_dir: Path) -> bool:
    for residue in install_dir.parent.glob(f"{install_dir.name}.deleting-*"):
        shutil.rmtree(residue, ignore_errors=True)
    if not install_dir.exists():
        return False
    trash = install_dir.with_name(f"{install_dir.name}.deleting-{uuid4().hex}")
    try:
        install_dir.rename(trash)
    except OSError as error:
        raise ModelInstallInUseError("Model files are in use") from error
    try:
        shutil.rmtree(trash)
    except OSError as error:
        try:
            trash.rename(install_dir)
        except OSError:
            pass
        raise ModelInstallInUseError("Model files could not be deleted") from error
    return True


def remove_interrupted_install_residue(
    model_root: Path, *, keep: Iterable[str] = ()
) -> tuple[Path, ...]:
    """Remove leftovers of interrupted manager installs under one model root.

    Only artifacts the manager itself produces on the way to a registry record
    are removed: ``*.part`` download temporaries, unregistered directories left
    empty by them and registry ``.tmp`` files. Everything else is kept: the
    directories named in ``keep`` (registered models, catalog installs), their
    contents, ``.staging-*`` / ``.deleting-*`` / ``.backup`` directories owned
    by other reconcilers, symlinks and any unknown files.
    """
    root = Path(model_root)
    if not root.is_dir():
        return ()
    protected = {str(name) for name in keep}
    removed: list[Path] = []
    for entry in sorted(root.iterdir()):
        name = entry.name
        if entry.is_symlink():
            continue
        if entry.is_file():
            if name.startswith(f".{REGISTRY_FILENAME}.") and name.endswith(".tmp"):
                try:
                    entry.unlink()
                except OSError:
                    continue
                removed.append(entry)
            continue
        if not entry.is_dir():
            continue
        if (
            name in protected
            or ".staging-" in name
            or ".deleting-" in name
            or name.endswith(".backup")
        ):
            continue
        try:
            children = tuple(entry.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_symlink() or not child.is_file() or not child.name.endswith(".part"):
                continue
            try:
                child.unlink()
            except OSError:
                continue
            removed.append(child)
        try:
            if not any(entry.iterdir()):
                remove_model_install_dir(entry)
                removed.append(entry)
        except OSError, ModelInstallInUseError:
            continue
    return tuple(removed)


__all__ = [
    "ModelInstallInUseError",
    "remove_interrupted_install_residue",
    "remove_model_install_dir",
]
