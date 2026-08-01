# Этап 4: GuiController

Разбить ui/controller.py (4374 строки, 123 метода) на менеджеры.

## Реальные зависимости

GuiController импортируется 1 файлом: `ui/app.py:24`.
Также в controller.py определён Protocol `ClipboardWatcherRuntime` (line 223) — при разбиении перенести в ports/.

## Что содержит GuiController (123 метода)

Группы методов по ответственности:

- **Overlay management (~30 методов):** _normalized_overlay_target, _overlay_target_for_settings, _overlay_runtime_is_active, _previous_overlay_overlay_target_for_apply, _overlay_process_runner_for_target, _build_initial_desktop_runtime_controls, _desktop_dimensions_for_size_preset, _desktop_launch_bounds_for_current_launch, _desktop_centered_bounds_for_dimensions, _is_finite_non_bool_number, _desktop_bounds_signature, _desktop_bounds_from_payload, _is_valid_desktop_window_bounds_event_payload, _track_desktop_apply_window_bounds_control, _consume_suppressed_desktop_bounds, _discard_suppressed_desktop_bounds, _is_desktop_user_window_bounds_event, _drain_pending_desktop_user_bounds_events, _set_desktop_overlay_interaction_mode, _notify_desktop_overlay_interaction_mode, _schedule_desktop_bounds_persistence, _persist_desktop_bounds, _desktop_center_bounds_for_current_preset, _desktop_work_area_for_current_launch, _discard_pending_desktop_bounds_persistence, _desktop_runtime_is_running_for_settings_update, _desktop_center_preserving_bounds_for_size_preset_change, _prepare_desktop_runtime_settings_update, _sync_desktop_overlay_interaction_mode_from_settings
- **Peer runtime (5 методов):** _build_peer_runtime_config, _enqueue_peer_translation_disclosure, _create_peer_stt_provider_from_runtime_config, _create_peer_audio_source_from_runtime_config, _create_peer_vad_from_runtime_config
- **Overlay lifecycle callbacks (7 методов):** on_overlay_start_failed, on_overlay_runtime_disconnected, on_overlay_runtime_crashed, _mark_overlay_connected, _normalize_overlay_failure_reason, _notify_overlay_state, _log_overlay_state_transition
- **Overlay calibration (8 методов):** begin_overlay_calibration, set_overlay_calibration_field, apply_overlay_calibration, cancel_overlay_calibration + test-версии
- **Microphone test (12 методов):** microphone_test_meter_level, microphone_test_active, _get_microphone_test_lifecycle_lock, _microphone_test_audio_settings_signature, и др.
- **Settings (4 метода):** _load_or_init_settings, _save_settings, _sync_ui_from_settings, _on_recent_languages_change
- **Diagnostics (8 методов):** debug_capture_fault_profile, debug_stt_fault_profile, _debug_audio_fault_allowed, _detailed_audio_diag_enabled, и др.
- **STT provider signatures (8 методов):** _stt_provider_applies_custom_vocabulary, _llm_provider_requires_secret, _selected_stt_provider, _build_self_stt_runtime_signature, и др.
- **Clipboard/manual input (6 методов):** _get_clipboard_watcher_lock, _on_clipboard_text_from_thread, _schedule_clipboard_submit, note_manual_input_activity, и др.
- **Peer translation flags (5 методов):** _effective_peer_translation_enabled_for, _peer_translation_eula_accepted_for, _peer_translation_activation_requested_for, и др.
- **Misc (остальные):** properties, logging, VRC mic receiver

## Неочевидное

**1. Controller — единственный объект, который знает и про UI и про ядро.**
Он мост между Flet (UI) и ClientHub (ядро). При разбиении — менеджеры НЕ должны знать про Flet. Только controller остаётся связующим звеном.

**2. UI-обновления идут в обе стороны.**
- Из ядра в UI: hub → event_bridge → Flet controls (обновить текст перевода)
- Из UI в ядро: Flet button → controller → hub (старт/стоп)

При разбиении — менеджеры работают только "из UI в ядро". "Из ядра в UI" остаётся в event_bridge.

**3. Controller содержит Flet-специфичный код.**
Методы типа `_update_power_button_color()`, `_show_snackbar()` — чистый Flet. Это не менеджеры, это UI-хелперы. Остаются в controller.

**4. Settings persistence переплетена с UI.**
Сохранение настроек вызывается из UI (кнопка "Сохранить") и из controller (автосохранение при изменении). settings_manager должен уметь и то, и другое.

**5. Overlay calibration — UI + hardware.**
Калибровка оверлея — это UI (кнопка, drag) + аппаратная часть (позиция в VR). overlay_manager не должен содержать Flet-код, только логику. UI-часть остаётся в controller или в отдельном view.

**6. Microphone test — асинхронный UI flow.**
Тест микрофона: запускает захват → показывает уровень → останавливает. Это async flow с UI-обновлениями. Лучше оставить в controller как UI-workflow, не выносить в audio_manager.

## Порядок извлечения

1. Сначала чистая логика (без Flet), потом Flet-специфичное
2. Сначала маленькие группы, потом крупные

Конкретно:
- settings (4) → первыми, простые
- STT provider signatures (8) → вторыми, чистая логика
- diagnostics (8) → третьими, изолированы
- clipboard/manual input (6) → четвёртыми
- peer runtime (5) → пятыми, зависят от hub
- overlay lifecycle callbacks (7) → шестыми
- peer translation flags (5) → седьмыми
- microphone test (12) → восьмыми, UI-flow
- overlay management (~30) → последними, крупнейшая группа

## Могу проверить (CLI)

- `python -m py_compile` на controller и каждом менеджере
- `grep -r "from.*controller import"` — обновить все импорты
- Проверить что менеджеры не импортируют `flet` (dependency inversion)
