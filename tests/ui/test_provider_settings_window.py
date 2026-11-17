from __future__ import annotations

import asyncio
import types
from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.settings_vnext.schema import AppSettingsVNext
from puripuly_heart.config.translation_values import TranslationModel
from puripuly_heart.ui.components.provider_settings_window import (
    PROVIDER_SETTINGS_WINDOW_KINDS,
    ProviderSettingsWindow,
    create_provider_settings_windows,
)
from puripuly_heart.ui.views import settings as settings_view


def _view(monkeypatch: pytest.MonkeyPatch) -> settings_view.SettingsView:
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)
    return settings_view.SettingsView()


class FakeWindow:
    def __init__(self) -> None:
        self.closed = False
        self.minimized = False
        self.visible = False

    def close(self):
        self.closed = True


class FakePage:
    def __init__(self) -> None:
        self.window = FakeWindow()
        self.updates = 0
        self.controls: list = []

    def add(self, *controls) -> None:
        self.controls.extend(controls)

    def update(self) -> None:
        self.updates += 1


def _fake_schedule(record: list) -> object:
    def schedule(coro):
        task = asyncio.get_running_loop().create_task(coro())
        record.append(task)
        return task

    return schedule


async def _hanging_app_runner(target) -> None:
    """Session-like runner: mounts the content and stays alive until cancelled."""
    page = FakePage()
    await target(page)
    await asyncio.Event().wait()


async def test_provider_settings_window_mounts_form_and_accept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accepted: list[str] = []
    closed: list[str] = []
    window = ProviderSettingsWindow(app_runner=_hanging_app_runner)
    content = object()

    def schedule(coro):
        task = asyncio.get_running_loop().create_task(coro())
        return task

    window.open(
        schedule,
        title="Настройки перевода",
        content=content,
        on_accept=lambda: accepted.append("ok"),
        on_close_request=lambda: closed.append("reverted"),
        height=695,
    )
    await asyncio.sleep(0)
    assert window.is_open
    assert window._page.window.height == 695

    window._on_accept_click(None)

    assert accepted == ["ok"]
    assert closed == []
    await window.close()
    assert not window.is_open


async def test_provider_settings_window_close_without_accept_reverts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []
    window = ProviderSettingsWindow(app_runner=_hanging_app_runner)

    def schedule(coro):
        task = asyncio.get_running_loop().create_task(coro())
        return task

    window.open(
        schedule,
        title="t",
        content=object(),
        on_accept=lambda: None,
        on_close_request=lambda: closed.append("reverted"),
    )
    await asyncio.sleep(0)
    await window.close()
    await asyncio.sleep(0)

    assert closed == ["reverted"]


def test_provider_settings_window_configures_resizable_frameless_defaults() -> None:
    window = ProviderSettingsWindow()
    page = FakePage()

    window._title = "Настройки перевода"
    window.configure_page(page)

    assert page.window.frameless is True
    assert page.window.resizable is True
    assert page.window.width > 0
    assert page.window.height > 0
    assert page.bgcolor == "transparent"
    assert page.window.bgcolor == "transparent"


def _fake_settings_window() -> types.SimpleNamespace:
    opened: dict[str, object] = {}
    accepted: list[str] = []
    reverted: list[str] = []

    class _Window:
        is_open = False

        def open(self, schedule, **kwargs) -> None:
            opened.update(kwargs)
            self.is_open = True

        def replace_content(
            self, content, *, title: str | None = None, height: int | None = None
        ) -> None:
            opened["content"] = content
            if title is not None:
                opened["title"] = title
            if height is not None:
                opened["height"] = height

        def close(self) -> None:
            self.is_open = False

    return types.SimpleNamespace(
        window=_Window(), opened=opened, accepted=accepted, reverted=reverted
    )


def test_gear_opens_dialog_with_form_and_titles(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    fake = _fake_settings_window()
    view.attach_provider_settings_window(
        {"translation_openai": fake.window, "backup_openai": fake.window}, lambda coro: coro
    )

    from puripuly_heart.ui.i18n import t

    view._on_openai_gear_click(None)

    assert fake.opened["title"] == "{}: {}".format(
        t("settings.translation.config_title"),
        t("settings.translation_variant.openai_compatible"),
    )
    assert fake.opened["content"] is view._openai_form
    assert fake.opened["height"] == 550

    fake2 = _fake_settings_window()
    view.attach_provider_settings_window(
        {"translation_openai": fake.window, "backup_openai": fake2.window}, lambda coro: coro
    )
    view._on_backup_variant_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    view._on_backup_gear_click(None)

    assert fake2.opened["title"] == "{}: {}".format(
        t("settings.backup_translation.config_title"),
        t("settings.translation_variant.openai_compatible"),
    )
    assert fake2.opened["content"] is view._backup_network_group
    assert fake2.opened["height"] == 550


def test_local_server_dialogs_use_765_pixel_height(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    fake_t = _fake_settings_window()
    fake_b = _fake_settings_window()
    view.attach_provider_settings_window(
        {"translation_local": fake_t.window, "backup_local": fake_b.window}, lambda coro: coro
    )

    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
    view._on_local_llm_gear_click(None)
    assert fake_t.opened["height"] == 765

    view._on_backup_variant_selected(TranslationModel.LOCAL_LLM.value)
    view._on_backup_gear_click(None)
    assert fake_b.opened["height"] == 765


def test_dialog_accept_applies_staged_edits(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    applied: list[str] = []
    monkeypatch.setattr(view, "on_providers_changed", lambda: applied.append("apply"))
    fake = _fake_settings_window()
    view.attach_provider_settings_window(
        {"translation_openai": fake.window, "backup_openai": fake.window}, lambda coro: coro
    )

    view._on_openai_gear_click(None)
    view._openai_model.value = "gpt-test"
    view._on_openai_model_change_end(types.SimpleNamespace(control=view._openai_model))
    assert view.has_provider_changes is True

    view._accept_provider_settings_dialog("translation")

    assert applied == ["apply"]
    assert view.has_provider_changes is True


def test_dialog_close_without_accept_discards_staged_edits(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    fake = _fake_settings_window()
    view.attach_provider_settings_window(
        {"translation_openai": fake.window, "backup_openai": fake.window}, lambda coro: coro
    )

    view._on_openai_gear_click(None)
    view._openai_model.value = "gpt-test"
    view._on_openai_model_change_end(types.SimpleNamespace(control=view._openai_model))
    assert view.has_provider_changes is True

    view._revert_provider_settings_dialog("translation")

    assert view.has_provider_changes is False
    assert view._provider_draft is None


def test_translation_dialog_contains_the_slot_row(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    fake = _fake_settings_window()
    view.attach_provider_settings_window(
        {"translation_openai": fake.window, "backup_openai": fake.window}, lambda coro: coro
    )

    view._on_openai_gear_click(None)

    assert view._openai_form.controls[0] is view._openai_slots_row


def test_translation_variants_use_distinct_windows_without_live_replacement(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    fake_openai = _fake_settings_window()
    fake_local = _fake_settings_window()
    view.attach_provider_settings_window(
        {
            "translation_openai": fake_openai.window,
            "translation_local": fake_local.window,
        },
        lambda coro: coro(),
    )
    view._on_openai_gear_click(None)
    original_content = fake_openai.opened["content"]

    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)

    assert view._provider_dialog_kinds["translation"] == "translation_openai"
    assert fake_openai.opened["content"] is original_content
    assert fake_openai.opened["height"] == 550

    view._on_local_llm_gear_click(None)

    assert fake_openai.window.is_open is False
    assert fake_local.window.is_open is True
    assert fake_local.opened["content"] is view._local_llm_form
    assert view._local_llm_form.controls[0] is view._local_llm_slots_row
    assert fake_local.opened["height"] == 765


def test_backup_dialog_shows_only_the_selected_variant_form(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    fake = _fake_settings_window()
    view.attach_provider_settings_window(
        {"translation_openai": fake.window, "backup_openai": fake.window}, lambda coro: coro
    )
    view._on_backup_variant_selected(TranslationModel.OPENAI_COMPATIBLE.value)

    view._on_backup_gear_click(None)

    assert view._backup_network_group.visible is True
    assert view._backup_local_group.visible is False


def test_both_settings_windows_open_independently(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    fake_t = _fake_settings_window()
    fake_b = _fake_settings_window()
    view.attach_provider_settings_window(
        {"translation_openai": fake_t.window, "backup_openai": fake_b.window}, lambda coro: coro
    )

    view._on_openai_gear_click(None)
    view._on_backup_variant_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    view._on_backup_gear_click(None)

    assert fake_t.window.is_open is True
    assert fake_b.window.is_open is True

    view._on_openai_gear_click(None)
    assert fake_b.window.is_open is True


def test_gear_cards_visible_follow_variant_selection(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")

    view._on_llm_selected(TranslationModel.OPENAI_COMPATIBLE.value)
    assert view._openai_compatible_card.visible is True
    assert view._openai_gear is view._openai_compatible_card
    translation_row = view._translation_provider_card.content.content.controls[-1]
    backup_row = view._backup_translation_card.content.content.controls[-1]
    assert view._openai_compatible_card in translation_row.controls
    assert view._backup_config_card in backup_row.controls

    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
    assert view._local_llm_connection_card.visible is True
    assert view._openai_compatible_card.visible is False


async def test_replace_content_updates_the_mounted_window_state() -> None:
    window = ProviderSettingsWindow(app_runner=_hanging_app_runner)
    first_content = object()
    window.open(
        lambda coro: asyncio.get_running_loop().create_task(coro()),
        title="OpenAI-compatible",
        content=first_content,
        on_accept=lambda: None,
        on_close_request=lambda: None,
        height=450,
    )
    await asyncio.sleep(0)
    assert window._form_host.content is first_content

    for index in range(12):
        content = object()
        title = f"Variant {index}"
        height = 695 if index % 2 else 450
        window.replace_content(content, title=title, height=height)

        assert window._form_host.content is content
        assert window._title == title
        assert window._title_bar._about_label.value == title
        assert window._page.title == title
        assert window._page.window.height == height

    await window.close()


async def test_live_variant_switches_do_not_replace_the_real_mounted_form(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    from puripuly_heart.ui.i18n import t

    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    window = ProviderSettingsWindow(app_runner=_hanging_app_runner)
    backup = _fake_settings_window()
    view.attach_provider_settings_window(
        {"translation_openai": window, "backup_openai": backup.window},
        lambda coro: asyncio.get_running_loop().create_task(coro()),
    )
    view._on_openai_gear_click(None)
    await asyncio.sleep(0)
    expected_form = window._form_host.content
    expected_title = "{}: {}".format(
        t("settings.translation.config_title"),
        t("settings.translation_variant.openai_compatible"),
    )

    for variant in (
        TranslationModel.LOCAL_LLM,
        TranslationModel.OPENAI_COMPATIBLE,
        TranslationModel.ANTHROPIC,
        TranslationModel.OPENAI_COMPATIBLE,
    ):
        view._on_llm_selected(variant.value)

        assert window._form_host.content is expected_form
        assert window._page.title == expected_title
        assert window._title_bar._about_label.value == expected_title
        assert window._page.window.height == 550

    await window.close()


def test_all_six_provider_windows_route_to_independent_instances(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    kinds = (
        "translation_openai",
        "translation_anthropic",
        "translation_local",
        "backup_openai",
        "backup_anthropic",
        "backup_local",
    )
    fakes = {kind: _fake_settings_window() for kind in kinds}
    assert len({id(fake.window) for fake in fakes.values()}) == 6
    view.attach_provider_settings_window(
        {kind: fake.window for kind, fake in fakes.items()}, lambda coro: coro()
    )

    primary = (
        (TranslationModel.OPENAI_COMPATIBLE, "openai", view._on_openai_gear_click),
        (TranslationModel.ANTHROPIC, "anthropic", view._on_anthropic_gear_click),
        (TranslationModel.LOCAL_LLM, "local", view._on_local_llm_gear_click),
    )
    for variant, suffix, handler in primary:
        view._on_llm_selected(variant.value)
        handler(None)
        kind = f"translation_{suffix}"
        expected_form = {
            "openai": view._openai_form,
            "anthropic": view._anthropic_form,
            "local": view._local_llm_form,
        }[suffix]
        assert fakes[kind].window.is_open is True
        assert fakes[kind].opened["content"] is expected_form

    backup = (
        (TranslationModel.OPENAI_COMPATIBLE, "openai"),
        (TranslationModel.ANTHROPIC, "anthropic"),
        (TranslationModel.LOCAL_LLM, "local"),
    )
    for variant, suffix in backup:
        view._on_backup_variant_selected(variant.value)
        view._on_backup_gear_click(None)
        kind = f"backup_{suffix}"
        expected_group = {
            "openai": view._backup_network_group,
            "anthropic": view._backup_anthropic_group,
            "local": view._backup_local_group,
        }[suffix]
        assert fakes[kind].window.is_open is True
        assert fakes[kind].opened["content"] is expected_group
        expected_height = 765 if suffix == "local" else 550
        assert fakes[kind].opened["height"] == expected_height


def test_first_open_loads_base_urls_for_all_provider_variants(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    from puripuly_heart.config.providers_registry import load_provider_presets

    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    kinds = (
        "translation_openai",
        "translation_anthropic",
        "translation_local",
        "backup_openai",
        "backup_anthropic",
        "backup_local",
    )
    fakes = {kind: _fake_settings_window() for kind in kinds}
    view.attach_provider_settings_window(
        {kind: fake.window for kind, fake in fakes.items()}, lambda coro: coro()
    )
    cases = (
        ("translation_openai", "openai", view._openai_provider, view._openai_base_url),
        (
            "translation_anthropic",
            "anthropic",
            view._anthropic_provider,
            view._anthropic_base_url,
        ),
        ("translation_local", "local", view._local_llm_provider, view._local_llm_base_url),
        ("backup_openai", "openai", view._backup_provider, view._backup_base_url),
        (
            "backup_anthropic",
            "anthropic",
            view._backup_anthropic_provider,
            view._backup_anthropic_base_url,
        ),
        ("backup_local", "local", view._backup_local_provider, view._backup_local_base_url),
    )

    expected_keys = {
        "translation_openai": "openai",
        "translation_anthropic": "anthropic",
        "translation_local": "koboldcpp",
        "backup_openai": "openai",
        "backup_anthropic": "anthropic",
        "backup_local": "koboldcpp",
    }
    for kind, protocol, provider, base_url in cases:
        view._open_provider_settings_dialog(kind)
        assert provider.value == expected_keys[kind]
        preset = load_provider_presets(protocol)[expected_keys[kind]]
        assert base_url.value == preset.base_url


def test_stale_window_close_does_not_revert_the_new_variant(
    monkeypatch: pytest.MonkeyPatch, settings_events, tmp_path: Path
) -> None:
    view = _view(monkeypatch)
    settings_events.load(view, AppSettingsVNext(), config_path=tmp_path / "settings.json")
    fake_openai = _fake_settings_window()
    fake_local = _fake_settings_window()
    view.attach_provider_settings_window(
        {
            "translation_openai": fake_openai.window,
            "translation_local": fake_local.window,
        },
        lambda coro: coro(),
    )
    view._on_openai_gear_click(None)
    stale_close = fake_openai.opened["on_close_request"]

    view._on_llm_selected(TranslationModel.LOCAL_LLM.value)
    view._on_local_llm_gear_click(None)
    stale_close()

    display = view._provider_draft or view._provider_snapshot
    assert display.translation.model == TranslationModel.LOCAL_LLM
    assert view._provider_dialog_kinds["translation"] == "translation_local"
    assert fake_local.window.is_open is True


def test_window_factory_creates_all_six_independent_native_window_owners() -> None:
    windows = create_provider_settings_windows()

    assert tuple(windows) == PROVIDER_SETTINGS_WINDOW_KINDS
    assert set(windows) == {
        "translation_openai",
        "translation_anthropic",
        "translation_local",
        "backup_openai",
        "backup_anthropic",
        "backup_local",
    }
    assert len({id(window) for window in windows.values()}) == 6


def test_slot_row_registry_stays_bounded_across_form_rebuilds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _view(monkeypatch)
    expected_keys = {
        "translation_openai",
        "translation_anthropic",
        "translation_local",
        "translation_quick",
        "backup_openai",
        "backup_anthropic",
        "backup_local",
        "backup_quick",
    }
    assert set(view._slot_rows) == expected_keys

    for _ in range(25):
        view._build_openai_form_controls()
        view._build_anthropic_form_controls()
        view._build_local_form_controls()
        view._build_backup_form_controls()

    assert len(view._slot_rows) == len(expected_keys)
    assert set(view._slot_rows) == expected_keys


def test_backup_form_builds_only_the_requested_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _view(monkeypatch)
    calls = []
    monkeypatch.setattr(
        view,
        "_build_provider_dropdown",
        lambda _on_select: pytest.fail("network form must not be rebuilt"),
    )
    monkeypatch.setattr(
        view,
        "_build_backup_anthropic_controls",
        lambda: calls.append("anthropic"),
    )
    monkeypatch.setattr(
        view,
        "_build_backup_local_controls",
        lambda: calls.append("local"),
    )

    view._build_backup_form_controls("backup_anthropic")
    view._build_backup_form_controls("backup_local")

    assert calls == ["anthropic", "local"]


async def test_stale_session_finish_does_not_clear_the_replacement_page() -> None:
    release_old_close = asyncio.Event()
    pages = []
    closed = []

    class SlowCloseWindow(FakeWindow):
        async def close(self):
            await release_old_close.wait()
            self.closed = True

    async def runner(target) -> None:
        page = FakePage()
        if not pages:
            page.window = SlowCloseWindow()
        pages.append(page)
        await target(page)
        await asyncio.Event().wait()

    async def wait_for_pages(count: int) -> None:
        for _ in range(20):
            if len(pages) >= count:
                return
            await asyncio.sleep(0)
        raise AssertionError(f"only {len(pages)} pages mounted")

    window = ProviderSettingsWindow(app_runner=runner)

    def schedule(coro):
        return asyncio.get_running_loop().create_task(coro())

    window.open(
        schedule,
        title="old",
        content=object(),
        on_accept=lambda: None,
        on_close_request=lambda: closed.append("old"),
    )
    await wait_for_pages(1)

    close_task = asyncio.create_task(window.close())
    await asyncio.sleep(0)
    window.open(
        schedule,
        title="new",
        content=object(),
        on_accept=lambda: None,
        on_close_request=lambda: closed.append("new"),
    )
    await wait_for_pages(2)
    replacement_page = pages[1]

    release_old_close.set()
    await close_task

    assert closed == ["old"]
    assert window._page is replacement_page
    assert window._form_host is not None

    await window.close()
    assert closed == ["old", "new"]


async def test_open_schedules_a_flet_compatible_coroutine_function() -> None:
    import inspect

    handlers = []
    window = ProviderSettingsWindow(app_runner=_hanging_app_runner)

    def schedule(handler):
        assert inspect.iscoroutinefunction(handler)
        handlers.append(handler)
        return asyncio.get_running_loop().create_task(handler())

    window.open(
        schedule,
        title="test",
        content=object(),
        on_accept=lambda: None,
        on_close_request=lambda: None,
    )
    await asyncio.sleep(0)

    assert len(handlers) == 1
    assert window.is_open
    await window.close()
