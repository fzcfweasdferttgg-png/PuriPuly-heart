from __future__ import annotations

from asyncio import Future
from types import SimpleNamespace

from puripuly_heart.ui.app import TranslatorApp
from tests.helpers.ui_application import compose_test_ui_application_boundary


class DummyPage:
    def __init__(self) -> None:
        async def destroy_window() -> None:
            self.window.destroy_calls += 1

        self.opened: list[object] = []

        async def wait_until_ready_to_show() -> None:
            return None

        async def center_window() -> None:
            self.window.center_calls += 1

        self.closed: list[object] = []
        self.tasks: list[object] = []
        self.title: str = ""
        self.theme = None
        self.updated = 0
        self.theme_mode = None
        self.bgcolor = None
        self.padding = None
        self.added: list[object] = []
        self.visibility_updates: list[bool] = []
        self.window = SimpleNamespace(
            frameless=False,
            resizable=False,
            maximizable=True,
            width=0,
            height=0,
            min_width=0,
            max_width=0,
            min_height=0,
            max_height=0,
            prevent_close=False,
            on_event=None,
            destroy_calls=0,
            destroy=destroy_window,
            icon="",
            center_calls=0,
            visible=False,
            center=center_window,
            wait_until_ready_to_show=wait_until_ready_to_show,
        )
        self.dialog = None

    def show_dialog(self, control) -> None:
        if hasattr(control, "open"):
            control.open = True
        self.opened.append(control)
        self.dialog = control

    def pop_dialog(self):
        control = self.dialog
        if control is None:
            return None
        if hasattr(control, "open"):
            control.open = False
        self.closed.append(control)
        self.dialog = None
        return control

    def run_task(self, coro_fn) -> Future:
        handle = Future()

        async def run():
            try:
                return await coro_fn()
            finally:
                if not handle.done():
                    handle.set_result(None)

        self.tasks.append(run)
        return handle

    def update(self) -> None:
        self.updated += 1
        self.visibility_updates.append(self.window.visible)

    def add(self, control) -> None:
        self.added.append(control)


class DummyContent:
    def __init__(self, content=None) -> None:
        self.content = content
        self.update_calls = 0

    def update(self) -> None:
        self.update_calls += 1


class InlineAudioTestSettingsView:
    def __init__(self) -> None:
        self.applied: list[tuple[str, float]] = []

    def vad_thresholds(self) -> dict[str, float]:
        return {"self": 0.4, "peer": 0.55}

    def apply_vad_threshold(self, channel: str, value: float) -> None:
        self.applied.append((channel, value))


def _audio_test_app(controller: SimpleNamespace) -> TranslatorApp:
    app = TranslatorApp.__new__(TranslatorApp)
    app.page = DummyPage()
    app.view_settings = InlineAudioTestSettingsView()
    app._run_page_task = lambda coroutine, *args: app.page.tasks.append(coroutine)
    app._ui_application = compose_test_ui_application_boundary(controller)
    return app
