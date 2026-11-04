from __future__ import annotations

from pathlib import Path

from puripuly_heart.config import paths


def test_user_config_dir_is_portable_data_user_dir() -> None:
    assert paths.user_config_dir() == paths.app_root() / "data_user"


def test_default_paths_use_user_config_dir(monkeypatch, tmp_path):
    def _fake_user_config_dir(*, app_dir_name: str = paths.APP_DIR_NAME) -> Path:
        return tmp_path / app_dir_name

    monkeypatch.setattr(paths, "user_config_dir", _fake_user_config_dir)

    assert paths.default_settings_path() == tmp_path / paths.APP_DIR_NAME / paths.SETTINGS_FILENAME
    assert (
        paths.default_settings_path()
        == tmp_path / paths.STABLE_APP_DIR_NAME / paths.SETTINGS_FILENAME
    )
    assert (
        paths.default_http_extensions_dir()
        == tmp_path / paths.APP_DIR_NAME / paths.HTTP_EXTENSIONS_DIRNAME
    )
    assert paths.default_models_dir() == tmp_path / paths.APP_DIR_NAME / paths.MODELS_DIRNAME
