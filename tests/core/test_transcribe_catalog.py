from __future__ import annotations

import re

from puripuly_heart.core.transcribe_catalog import load_transcribe_catalog


def test_catalog_loads_with_all_roles_and_published_repos() -> None:
    models = load_transcribe_catalog()
    assert len(models) > 50

    roles = {model.role for model in models}
    assert {"asr", "diarize", "langid", "vad"} <= roles
    for model in models:
        assert model.repo.count("/") == 1
        assert model.downloads
        assert all(item.size_bytes > 0 for item in model.downloads)


def test_catalog_keeps_whisper_models_listed() -> None:
    models = load_transcribe_catalog()
    whisper = [model for model in models if model.model_id.startswith("whisper")]
    assert whisper, "whisper GGUF models stay listed (user decision 2026-11-08)"


def test_catalog_accuracy_cells_are_formatted_per_language() -> None:
    models = load_transcribe_catalog()
    russian = [model for model in models if "ru" in model.accuracy]
    assert russian

    cell = russian[0].accuracy_for("ru", None)
    assert re.fullmatch(r"(WER|CER|ACC|DER|CPWER) \d+\.\d%", cell)
    assert russian[0].accuracy_for("zz", None) == ""


def test_catalog_download_lookup_and_params_label() -> None:
    model = next(
        item for item in load_transcribe_catalog() if item.download("Q6_K") is not None
    )
    download = model.download("Q6_K")
    assert download is not None
    assert download.filename.endswith(".gguf")
    assert model.download("NOPE") is None
    assert re.fullmatch(r"\d+(\.\d)?[BMK]", model.params_label)
