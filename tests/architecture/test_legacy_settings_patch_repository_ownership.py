from __future__ import annotations

from tests.helpers.paths import REPO_ROOT as ROOT

COMPOSITION_PATH = ROOT / "src" / "puripuly_heart" / "composition" / "application_runtime.py"
UI_RUNTIME_PATH = ROOT / "src" / "puripuly_heart" / "app" / "adapters" / "ui_runtime.py"
PROVIDER_SETTINGS_PATH = (
    ROOT / "src" / "puripuly_heart" / "app" / "services" / "provider" / "provider_settings.py"
)
SETTINGS_APPLICATION_PATH = (
    ROOT / "src" / "puripuly_heart" / "app" / "services" / "settings" / "settings_application.py"
)
OPENROUTER_PKCE_PATH = (
    ROOT / "src" / "puripuly_heart" / "app" / "services" / "openrouter_pkce_flow.py"
)
MANAGED_ACCOUNT_WIRING_PATH = (
    ROOT / "src" / "puripuly_heart" / "app" / "wiring" / "wiring_managed_account.py"
)
