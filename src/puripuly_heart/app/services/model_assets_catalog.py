from dataclasses import dataclass

from puripuly_heart.core.local_asr.local_stt_assets import (
    LOCAL_STT_MODEL_IDS,
    LocalSTTAssetError,
    load_local_stt_asset_manifest,
)
from puripuly_heart.core.local_translation.assets import e4b_gemma_spec, gemma_26b_spec


@dataclass(frozen=True, slots=True)
class ModelAssetChoice:
    model_id: str
    name: str
    description_key: str
    purpose: str
    size_bytes: int
    importable: bool = False
    required_files: tuple[str, ...] = ()


def model_asset_choices() -> tuple[ModelAssetChoice, ...]:
    choices = []
    for model_id in LOCAL_STT_MODEL_IDS:
        try:
            manifest = load_local_stt_asset_manifest(model_id)
        except LocalSTTAssetError:
            continue
        choices.append(
            ModelAssetChoice(
                model_id,
                model_id,
                "model_assets.description." + model_id,
                "asr",
                sum(a.size_bytes for a in manifest.files),
            )
        )
    for spec, name, key in (
        (e4b_gemma_spec(), "Gemma 4 E4B QAT UD Q4 K XL", "gemma_e4b"),
        (gemma_26b_spec(), "Gemma 4 26B A4B QAT UD Q4 K XL", "gemma_26b"),
    ):
        choices.append(
            ModelAssetChoice(
                spec.model_id,
                name,
                "model_assets.description." + key,
                "translation",
                sum(a.size_bytes for a in spec.assets),
                importable=True,
                required_files=tuple(asset.filename for asset in spec.assets),
            )
        )
    return tuple(choices)
