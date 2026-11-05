from __future__ import annotations

import pytest

from tests.helpers.ast_sources import imported_modules
from tests.helpers.paths import SOURCE_ROOT

CASES = [
    {
        "id": "translation_enable",
        "owner_paths": (
            "app/services/translation_enable.py",
        ),
        "forbidden_prefixes": ("puripuly_heart.ui",),
        "forbidden_exact": (),
    },
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_owner_does_not_import_ui_or_controller(case) -> None:
    for relative_path in case["owner_paths"]:
        imports = imported_modules(SOURCE_ROOT / relative_path)
        assert not any(
            name.startswith(prefix) for name in imports for prefix in case["forbidden_prefixes"]
        )
        assert not set(case["forbidden_exact"]) & imports
