from __future__ import annotations

from dataclasses import replace

from tests.app.test_translation_runtime_switching import _openai_primary, _runtime_owner, _signature


def test_disabled_backup_model_change_does_not_require_rebuild() -> None:
    base = _openai_primary()
    disabled = replace(
        base,
        intent=replace(
            base.intent,
            backup_translation=replace(base.intent.backup_translation, enabled=False, model=""),
        ),
    )
    changed = replace(
        disabled,
        intent=replace(
            disabled.intent,
            backup_translation=replace(
                disabled.intent.backup_translation,
                enabled=False,
                model="local_llm",
            ),
        ),
    )

    assert _signature(disabled) == _signature(changed)
    plan = _runtime_owner(disabled).build_plan(changed, force_rebuild_llm=False)
    assert plan.should_rebuild_llm is False
