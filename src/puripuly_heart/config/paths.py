from __future__ import annotations

from pathlib import Path

STABLE_APP_DIR_NAME = "puripuly-heart"
APP_DIR_NAME = STABLE_APP_DIR_NAME
SETTINGS_FILENAME = "settings.json"
MODELS_DIRNAME = "models"
HTTP_EXTENSIONS_DIRNAME = "http_extensions"
USER_DATA_DIRNAME = "var/data_user"


def app_root() -> Path:
    here = Path(__file__).resolve()
    parent = here.parents[2]
    if parent.name == "src":
        return here.parents[3]
    return parent


def user_config_dir(*, app_dir_name: str = APP_DIR_NAME) -> Path:
    return app_root() / USER_DATA_DIRNAME


def default_settings_path() -> Path:
    return user_config_dir() / SETTINGS_FILENAME


def default_models_dir() -> Path:
    return user_config_dir() / MODELS_DIRNAME


def default_http_extensions_dir() -> Path:
    return user_config_dir() / HTTP_EXTENSIONS_DIRNAME
