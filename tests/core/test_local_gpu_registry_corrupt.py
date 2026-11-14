from __future__ import annotations

import pytest

from puripuly_heart.core.local_asr.local_gpu_assets import local_gpu_model_path
from puripuly_heart.core.local_asr.model_registry import ModelRegistryError


def test_corrupt_registry_is_reported_as_unknown_local_model(tmp_path) -> None:
    (tmp_path / "registry.json").write_text("{ not json", encoding="utf-8")

    with pytest.raises(ValueError, match="unknown local model: custom-model") as error:
        local_gpu_model_path(tmp_path, model_id="custom-model")

    assert isinstance(error.value.__cause__, ModelRegistryError)
