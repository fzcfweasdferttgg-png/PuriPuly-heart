from __future__ import annotations

import hashlib
import json

import pytest

from puripuly_heart.core.local_translation import assets


def _asset(filename: str, content: bytes) -> assets.GemmaAsset:
    return assets.GemmaAsset(
        filename=filename,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _write_install(tmp_path, monkeypatch, *, corrupt: bool = False):
    target = b"target"
    draft = b"draft"
    pinned = (_asset("target.gguf", target), _asset("draft.gguf", draft))
    monkeypatch.setattr(assets, "GEMMA_ASSETS", pinned)
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "target.gguf").write_bytes(b"broken" if corrupt else target)
    (tmp_path / "draft.gguf").write_bytes(draft)
    (tmp_path / assets.GEMMA_INSTALLED_MANIFEST_FILENAME).write_text(
        json.dumps(assets.InstalledGemmaManifest.expected().to_dict()),
        encoding="utf-8",
    )
    return pinned


def test_pinned_gemma_contract_has_exact_target_and_drafter() -> None:
    assert assets.GEMMA_REPO_ID == "unsloth/gemma-4-E4B-it-qat-GGUF"
    assert assets.GEMMA_REVISION == "8c5a9e4fd5482e2be20fe0bf013b4c262a8f4265"
    assert assets.GEMMA_UPSTREAM_REPO_ID == "google/gemma-4-E4B-it"
    assert assets.GEMMA_LICENSE == "Apache-2.0"
    assert assets.GEMMA_LICENSE_URL == "https://www.apache.org/licenses/LICENSE-2.0"
    assert assets.InstalledGemmaManifest.expected().upstream_repo_id == "google/gemma-4-E4B-it"
    assert assets.InstalledGemmaManifest.expected().license == "Apache-2.0"
    assert assets.InstalledGemmaManifest.expected().license_url == (
        "https://www.apache.org/licenses/LICENSE-2.0"
    )
    assert [(item.filename, item.size_bytes, item.sha256) for item in assets.GEMMA_ASSETS] == [
        (
            "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf",
            4_215_695_776,
            "df0fd4ee07072c607c29a0a1cb4f98918426cca12f45a2776bdd6ee6d09a4de3",
        ),
        (
            "mtp-gemma-4-E4B-it.gguf",
            59_678_016,
            "423074e537504b4f9ec5eafed5c639fac82c96631626efccacdd3c4039b20605",
        ),
    ]


def test_full_validation_rejects_checksum_mismatch(tmp_path, monkeypatch) -> None:
    _write_install(tmp_path, monkeypatch, corrupt=True)

    with pytest.raises(assets.GemmaInstallInvalidError, match="checksum mismatch"):
        assets.validate_gemma_install(tmp_path)


def test_inspection_reports_ready_without_hashing_valid_sized_assets(tmp_path, monkeypatch) -> None:
    _write_install(tmp_path, monkeypatch)

    state = assets.inspect_gemma_install(tmp_path)

    assert state.status == "ready"
    assert state.manifest == assets.InstalledGemmaManifest.expected()


def test_26b_spec_is_distinct_pinned_text_only_artifact():
    spec = assets.resolve_gemma_spec(assets.GEMMA_26B_MODEL_ID)
    assert spec == assets.gemma_26b_spec()
    assert spec.repo_id == "unsloth/gemma-4-26B-A4B-it-qat-GGUF"
    assert spec.revision == "7b92b5b28818151e8669af2e45e88d6086f490dd"
    assert spec.model_filename == "gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf"
    assert spec.draft_filename is None
    assert spec.assets == (
        assets.GemmaAsset(
            filename=spec.model_filename,
            size_bytes=14_249_047_104,
            sha256="a7c5bc715f5ff8e99a3e8901ce7d2b42b402c669bf24f7c5250747633d0f5891",
        ),
    )
    assert tuple(spec.sources) == ("huggingface", "modelscope")
    assert spec.sources["huggingface"].repo_id == spec.repo_id
    assert spec.sources["huggingface"].revision == spec.revision
    mirror = spec.sources["modelscope"]
    assert mirror.repo_id == spec.repo_id
    assert mirror.revision == assets.GEMMA_26B_MODELSCOPE_REVISION
    assert mirror.download_url_template == (
        "https://www.modelscope.cn/api/v1/models/unsloth/gemma-4-26B-A4B-it-qat-GGUF/repo"
        "?Revision=f5f82bed79bc0bc49b01e0e577bb3dc949ebc564&FilePath={path}"
    )
    assert assets.resolve_gemma_spec().model_id == assets.GEMMA_MODEL_ID
    assert assets.default_gemma_install_dir(spec) != assets.default_gemma_install_dir()
    assert assets.InstalledGemmaManifest.expected(spec).files == (spec.model_filename,)
