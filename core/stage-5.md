# Этап 5: Settings view

Разбить ui/views/settings.py (4687 строк) на секции.

## Неочевидное

**1. Settings view — это не просто формы.**
Она содержит логику: "если провайдер локальный — скрыть API-ключ", "если модель Qwen — показать hallucination settings". Эта логика остаётся в секциях, не в координаторе.

**2. Dropdown-ы зависят друг от друга.**
STT dropdown провайдеров → при изменении → обновляет dropdown моделей. LLM dropdown → при изменении → обновляет API-ключ поле. Это каскадные обновления между секциями.

Решение: каждая секция принимает callback `on_change`. Координатор вызывает обновление зависимых секций.

**3. Custom vocabulary editor — сложный компонент.**
Tag editor для кастомного словаря — это отдельный компонент в ui/components/settings/. Секция его только использует, не содержит. Не трогаем компонент.

**4. i18n ключи — не трогать.**
Все тексты берутся из i18n JSON. При разбиении секций — не менять ключи, не добавлять новые. Только переносить код.

**5. Секции используют controller напрямую.**
Секции вызывают controller.settings_manager для чтения/записи. При разбиении — секции должны принимать settings_manager через конструктор, не хардкодить ссылку на controller.

## Порядок извлечения

1. Helper-функции (8 штук: _make_text_button, _set_text_button_label, _reject_json_constant, _update_control_if_mounted, _make_overlay_anchor_dropdown, _load_secret_value, _weighted_len, _setting_action_text_size) → utils.py
2. _build_ui helper-методы (_build_clickable_text, _build_setting_action_text, _build_overlay_step_hit_lane, _build_overlay_step_visual_lane, _build_overlay_step_split_layout, _build_settings_subtab_shell, _build_setting_action_row, _build_action_button, _build_integrated_context_unit_card, _build_overlay_calibration_field, _build_numeric_setting_field, _build_overlay_calibration_column) → utils.py или оставить в SettingsView
3. Секции _build_ui(): разбить на отдельные методы, каждый → свой файл
4. _build_locale_options → i18n_section.py
5. _build_settings_with_provider_draft → settings_view_logic.py
6. SettingsView координатор → settings.py ≤200 строк

## Могу проверить (CLI)

- `python -m py_compile` на каждой секции
- `python -m py_compile` на координаторе
- `grep -r "from.*settings import"` — обновить импорты в app.py
