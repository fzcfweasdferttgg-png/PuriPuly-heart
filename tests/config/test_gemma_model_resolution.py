from __future__ import annotations

from puripuly_heart.config import llm_profiles, runtime_resolution


def _runtime_input(
    *,
    model: str,
    connection: str = runtime_resolution.TRANSLATION_CONNECTION_OPENROUTER,
) -> runtime_resolution.RuntimeResolutionInput:
    return runtime_resolution.RuntimeResolutionInput(
        translation=runtime_resolution.TranslationRuntimeIntent(
            model=model,
            connection=connection,
        )
    )


def test_gemma_catalog_exposes_combined_profiles_and_ignores_retired_aliases() -> None:
    models = (
        llm_profiles.OPENROUTER_MODEL_GEMMA_4_26B_A4B_IT,
        llm_profiles.OPENROUTER_MODEL_GEMMA_4_31B_IT,
    )
    assert {alias for alias in llm_profiles.PROFILE_BY_ALIAS if alias.startswith("gemma4")} == {
        llm_profiles.OPENROUTER_SELECTION_ALIAS_GEMMA4_26B_31B_MANAGED,
        llm_profiles.OPENROUTER_SELECTION_ALIAS_GEMMA4_26B_31B_BYOK,
    }
    for source in ("managed", "byok"):
        canonical = f"gemma4_26b_31b_{source}"
        profile = llm_profiles.get_openrouter_llm_profile(canonical)
        assert profile is not None
        assert profile.alias == canonical
        assert profile.openrouter_source == source
        assert profile.openrouter_models == models
        for retired in (f"gemma4_{source}", f"gemma4_31b_{source}"):
            assert llm_profiles.get_openrouter_llm_profile(retired) is None
    assert not {"gemma4", "gemma4_31b"} & set(runtime_resolution.TRANSLATION_MODELS)


def test_retired_gemma_product_intents_resolve_to_the_combined_pool() -> None:
    for model in ("gemma4", "gemma4_31b", "gemma4_26b_31b"):
        for connection, credential_source in (
            ("managed", "managed"),
            ("openrouter", "secret_store"),
        ):
            intent = runtime_resolution.TranslationRuntimeIntent(
                model=model,
                connection=connection,
            )
            config = runtime_resolution.resolve_llm_config(
                runtime_resolution.RuntimeResolutionInput(translation=intent)
            )
            assert intent.model == "gemma4_26b_31b"
            assert config.primary.models == (
                llm_profiles.OPENROUTER_MODEL_GEMMA_4_26B_A4B_IT,
                llm_profiles.OPENROUTER_MODEL_GEMMA_4_31B_IT,
            )
            assert config.primary.provider_routing == "gemma4_26b_31b_latency"
            assert config.primary.credential.source == credential_source
            assert config.attempts[1].target == config.primary
            assert config.attempts[2].target.models == (
                llm_profiles.OPENROUTER_MODEL_GEMMA_4_31B_IT,
            )
            assert config.attempts[2].target.model == llm_profiles.OPENROUTER_MODEL_GEMMA_4_31B_IT
            assert config.attempts[2].target.credential.source == credential_source


def test_dormant_gemma_alias_cannot_override_active_non_gemma_product() -> None:
    router = runtime_resolution.normalize_openrouter_runtime_intent(
        provider_llm="openrouter",
        selected_source="byok",
        selection_alias="gemma4_31b_byok",
    )
    config = runtime_resolution.resolve_llm_config(
        runtime_resolution.RuntimeResolutionInput(
            translation=runtime_resolution.TranslationRuntimeIntent(
                model="deepseek_v4_flash_41",
                connection="managed",
            ),
            openrouter=router,
        )
    )
    assert config.primary.model == llm_profiles.OPENROUTER_MODEL_DEEPSEEK_V4_FLASH_41
    assert config.primary.credential.reference == "openrouter:managed"
    assert config.primary.provider_routing == "deepseek_v4_flash_41_strict"
    assert config.attempts[2].target.model == llm_profiles.OPENROUTER_MODEL_GEMMA_4_31B_IT
    assert config.attempts[2].target.provider_routing == "gemma4_31b_modelrun_only"


def test_runtime_resolves_three_stage_plan_without_deduplicating_targets() -> None:
    config = runtime_resolution.resolve_llm_config(
        _runtime_input(
            model=runtime_resolution.TRANSLATION_MODEL_GEMMA4_26B_31B,
        )
    )

    assert len(config.attempts) == 3
    assert config.attempts[0].start_after_ms == 0
    assert config.attempts[1].start_after_ms == 1300
    assert config.attempts[1].start_on_primary_error is True
    assert config.attempts[1].target == config.attempts[0].target
    assert config.attempts[2].start_after_ms == 4400
    assert config.attempts[2].start_on_primary_error is False
    assert config.attempts[2].target.model == llm_profiles.OPENROUTER_MODEL_GEMMA_4_31B_IT
    assert config.attempts[2].target.provider_routing == "gemma4_31b_modelrun_only"
    assert config.loser_grace_ms == 50
