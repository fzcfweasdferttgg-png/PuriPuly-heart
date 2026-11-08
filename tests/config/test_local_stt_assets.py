from __future__ import annotations

import codecs
import json
import zlib
from pathlib import Path

import pytest

from puripuly_heart.core.local_asr import local_stt_assets as local_stt_assets_module
from puripuly_heart.core.local_asr.local_stt_assets import (
    LOCAL_STT_MODEL_IDS,
    InstalledLocalSTTManifest,
    LocalSTTAssetFile,
    LocalSTTAssetManifest,
    LocalSTTAssetSource,
    LocalSTTInstallState,
    LocalSTTManifestInvalidError,
    LocalSTTModelMissingError,
    default_local_stt_installed_manifest_path,
    default_local_stt_model_dir,
    default_local_stt_model_root,
    default_local_stt_source_for_locale,
    inspect_local_stt_install_state,
    load_local_stt_asset_manifest,
    validate_local_stt_install,
)
from puripuly_heart.runtime_layout import current_runtime_layout


def _crc32_bytes(payload: bytes) -> str:
    return f"{zlib.crc32(payload) & 0xFFFFFFFF:08x}"


def _test_manifest() -> LocalSTTAssetManifest:
    return LocalSTTAssetManifest(
        manifest_version=2,
        model_id="test-model",
        engine="transcribe.cpp-vulkan",
        install_dirname="test-model",
        sources={
            "huggingface": LocalSTTAssetSource(
                name="huggingface",
                revision="hf-rev-1",
            ),
            "modelscope": LocalSTTAssetSource(
                name="modelscope",
                revision="ms-rev-1",
            ),
        },
        files=(
            LocalSTTAssetFile(
                relative_path="model.int8.onnx",
                size_bytes=len(b"model-bytes"),
                crc32=_crc32_bytes(b"model-bytes"),
            ),
            LocalSTTAssetFile(
                relative_path="tokens.txt",
                size_bytes=len(b"token-bytes"),
                crc32=_crc32_bytes(b"token-bytes"),
            ),
        ),
    )


def _write_valid_install(root: Path, manifest: LocalSTTAssetManifest) -> None:
    (root / "model.int8.onnx").write_bytes(b"model-bytes")
    (root / "tokens.txt").write_bytes(b"token-bytes")
    installed = InstalledLocalSTTManifest(
        manifest_version=1,
        model_id=manifest.model_id,
        engine=manifest.engine,
        install_dirname=manifest.install_dirname,
        selected_source="huggingface",
        selected_revision=manifest.sources["huggingface"].revision,
    )
    default_local_stt_installed_manifest_path(root).write_text(
        json.dumps(installed.to_dict(), indent=2),
        encoding="utf-8",
    )


def test_default_local_stt_paths_use_runtime_layout_model_root() -> None:
    layout = current_runtime_layout()

    assert default_local_stt_model_root() == layout.model_cache_root / "transcribe"
    model_dir = default_local_stt_model_dir("test-model")
    assert model_dir == layout.model_cache_root / "transcribe" / "test-model"
    assert default_local_stt_installed_manifest_path(model_dir) == (
        model_dir / "installed-manifest.json"
    )


@pytest.mark.parametrize(
    "retired_model_id",
    ["qwen3-asr-0.6b-int8-sherpa", "parakeet-tdt-0.6b-v3-int8-sherpa"],
)
def test_retired_models_are_not_discoverable_or_downloadable(retired_model_id: str) -> None:
    assert retired_model_id not in LOCAL_STT_MODEL_IDS
    with pytest.raises(LocalSTTModelMissingError, match="model manifest not found"):
        load_local_stt_asset_manifest(retired_model_id)


def test_validate_local_stt_install_accepts_matching_manifest_and_checksums(
    tmp_path: Path,
) -> None:
    manifest = _test_manifest()
    install_dir = tmp_path / manifest.install_dirname
    install_dir.mkdir()
    _write_valid_install(install_dir, manifest)

    installed = validate_local_stt_install(install_dir, manifest=manifest)

    assert installed.selected_source == "huggingface"
    assert installed.selected_revision == "hf-rev-1"


def test_validate_local_stt_install_can_skip_checksum_verification(
    tmp_path: Path,
) -> None:
    manifest = _test_manifest()
    install_dir = tmp_path / manifest.install_dirname
    install_dir.mkdir()
    _write_valid_install(install_dir, manifest)
    (install_dir / "model.int8.onnx").write_bytes(b"model-bytez")

    installed = validate_local_stt_install(
        install_dir,
        manifest=manifest,
        verify_checksums=False,
    )

    assert installed.selected_source == "huggingface"


def test_validate_local_stt_install_rejects_missing_required_file(tmp_path: Path) -> None:
    manifest = _test_manifest()
    install_dir = tmp_path / manifest.install_dirname
    install_dir.mkdir()
    _write_valid_install(install_dir, manifest)
    (install_dir / "tokens.txt").unlink()

    with pytest.raises(LocalSTTManifestInvalidError, match="missing model file"):
        validate_local_stt_install(install_dir, manifest=manifest)


def test_validate_local_stt_install_rejects_size_mismatch(tmp_path: Path) -> None:
    manifest = _test_manifest()
    install_dir = tmp_path / manifest.install_dirname
    install_dir.mkdir()
    _write_valid_install(install_dir, manifest)
    (install_dir / "model.int8.onnx").write_bytes(b"model-bytes-longer")

    with pytest.raises(LocalSTTManifestInvalidError, match="size mismatch"):
        validate_local_stt_install(install_dir, manifest=manifest)


def test_validate_local_stt_install_rejects_checksum_mismatch(tmp_path: Path) -> None:
    manifest = _test_manifest()
    install_dir = tmp_path / manifest.install_dirname
    install_dir.mkdir()
    _write_valid_install(install_dir, manifest)
    (install_dir / "model.int8.onnx").write_bytes(b"Model-Bytes")

    with pytest.raises(LocalSTTManifestInvalidError, match="checksum mismatch"):
        validate_local_stt_install(install_dir, manifest=manifest)


def test_validate_local_stt_install_accepts_bom_prefixed_installed_manifest(
    tmp_path: Path,
) -> None:
    manifest = _test_manifest()
    install_dir = tmp_path / manifest.install_dirname
    install_dir.mkdir()
    _write_valid_install(install_dir, manifest)

    installed_manifest_path = default_local_stt_installed_manifest_path(install_dir)
    payload = installed_manifest_path.read_text(encoding="utf-8")
    installed_manifest_path.write_bytes(codecs.BOM_UTF8 + payload.encode("utf-8"))

    installed = validate_local_stt_install(install_dir, manifest=manifest)

    assert installed.selected_source == "huggingface"


def test_validate_local_stt_install_wraps_invalid_json_manifest(tmp_path: Path) -> None:
    manifest = _test_manifest()
    install_dir = tmp_path / manifest.install_dirname
    install_dir.mkdir()
    (install_dir / "model.int8.onnx").write_bytes(b"model-bytes")
    (install_dir / "tokens.txt").write_bytes(b"token-bytes")
    default_local_stt_installed_manifest_path(install_dir).write_text("{invalid", encoding="utf-8")

    with pytest.raises(LocalSTTManifestInvalidError, match="invalid installed manifest"):
        validate_local_stt_install(install_dir, manifest=manifest)


def test_validate_local_stt_install_wraps_missing_manifest_fields(tmp_path: Path) -> None:
    manifest = _test_manifest()
    install_dir = tmp_path / manifest.install_dirname
    install_dir.mkdir()
    (install_dir / "model.int8.onnx").write_bytes(b"model-bytes")
    (install_dir / "tokens.txt").write_bytes(b"token-bytes")
    default_local_stt_installed_manifest_path(install_dir).write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "engine": manifest.engine,
                "install_dirname": manifest.install_dirname,
                "selected_source": "huggingface",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(LocalSTTManifestInvalidError, match="invalid installed manifest"):
        validate_local_stt_install(install_dir, manifest=manifest)


def test_validate_local_stt_install_rejects_model_id_mismatch(tmp_path: Path) -> None:
    manifest = _test_manifest()
    install_dir = tmp_path / manifest.install_dirname
    install_dir.mkdir()
    _write_valid_install(install_dir, manifest)
    installed_manifest_path = default_local_stt_installed_manifest_path(install_dir)
    payload = json.loads(installed_manifest_path.read_text(encoding="utf-8"))
    payload["model_id"] = "other-model"
    installed_manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LocalSTTManifestInvalidError, match="model_id mismatch"):
        validate_local_stt_install(install_dir, manifest=manifest)


def test_inspect_local_stt_install_state_returns_missing_for_absent_install(tmp_path: Path) -> None:
    manifest = _test_manifest()

    state = inspect_local_stt_install_state(
        tmp_path / manifest.install_dirname,
        manifest=manifest,
    )

    assert state == LocalSTTInstallState(status="missing", installed_manifest=None)


def test_inspect_local_stt_install_state_returns_invalid_for_broken_manifest(
    tmp_path: Path,
) -> None:
    manifest = _test_manifest()
    install_dir = tmp_path / manifest.install_dirname
    install_dir.mkdir()
    (install_dir / "model.int8.onnx").write_bytes(b"model-bytes")
    (install_dir / "tokens.txt").write_bytes(b"token-bytes")
    default_local_stt_installed_manifest_path(install_dir).write_text("{invalid", encoding="utf-8")

    state = inspect_local_stt_install_state(install_dir, manifest=manifest)

    assert state.status == "invalid"
    assert state.installed_manifest is None


def test_inspect_local_stt_install_state_returns_ready_for_valid_install(tmp_path: Path) -> None:
    manifest = _test_manifest()
    install_dir = tmp_path / manifest.install_dirname
    install_dir.mkdir()
    _write_valid_install(install_dir, manifest)

    state = inspect_local_stt_install_state(install_dir, manifest=manifest)

    assert state.status == "ready"
    assert state.installed_manifest is not None
    assert state.installed_manifest.selected_source == "huggingface"


@pytest.mark.parametrize("locale", ["zh-CN", "zh-cn", "zh-HK", "zh_HK", "zh-Hant-HK", "ru_RU", None])
def test_default_local_stt_source_for_locale_is_huggingface(locale) -> None:
    assert default_local_stt_source_for_locale(locale) == "huggingface"


def test_local_stt_asset_file_round_trips_integrity_fields() -> None:
    payload = {
        "relative_path": "conv_frontend.onnx",
        "size_bytes": 44148281,
        "crc32": "d22dc442",
    }

    asset = LocalSTTAssetFile.from_dict(payload)

    assert asset.to_dict() == payload
    assert asset.remote_path_for_source("huggingface") == "conv_frontend.onnx"
    assert asset.remote_path_for_source("modelscope") == "conv_frontend.onnx"


def test_normalize_crc32_accepts_prefixed_and_normalizes_case() -> None:
    assert local_stt_assets_module.normalize_crc32("0xD22DC442") == "d22dc442"

    with pytest.raises(LocalSTTManifestInvalidError, match="invalid crc32"):
        local_stt_assets_module.normalize_crc32("xyz")
