"""Deprecated — re-exports for backward compatibility.

SettingsManagerMixin has been decomposed into:
- SettingsService (app/services/settings_service.py) — pure settings operations
- GuiController (app/services/gui_controller.py) — apply_settings orchestrator

This module re-exports load_providers and load_settings so existing
ui/ callers continue to work without changes.
"""

from puripuly_heart.adapters.storage.providers_persistence import load_providers  # noqa: F401
from puripuly_heart.adapters.storage.settings_persistence import load_settings, save_settings  # noqa: F401
