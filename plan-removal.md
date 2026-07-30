# План удаления: Managed Auth + Telemetry + Fingerprint

> Цель: удалить Discord OAuth, Managed OpenRouter Auth, Hardware Fingerprint и Telemetry из portable форка.
> Причина: эти системы — dead code для portable форка (не используются).

---

## Этап 1: Telemetry (независима, тёплый старт)

**Удалить:**
- `core/telemetry.py`
- `ui/components/telemetry_consent_dialog.py`

**Почистить:**
- `config/settings.py` — `TelemetryConsent`, `TelemetrySettings`, `_parse_telemetry_*`, `telemetry_settings_to_dict/from_dict`, поле `telemetry` из `AppSettings`
- `ui/controller.py` — импорт, поле, 6 методов lifecycle
- `ui/app.py` — импорты, consent dialog, startup вызов
- `ui/event_bridge.py` — `_schedule_translation_success_telemetry`, вызов в `_on_translation_success`
- `ui/views/settings.py` — telemetry card UI (~39 строк)
- `ui/components/debug_preview_panel.py` — `on_telemetry_consent_modal`
- 35 i18n файлов — ключи `telemetry.*`

**Верификация после этапа:**
- [ ] `grep -r "telemetry" --include="*.py"` — 0 совпадений (кроме комментариев)
- [ ] `settings.py` — `AppSettings` не содержит поле `telemetry`, `validate()` не вызывает `_parse_telemetry_*`
- [ ] i18n — нет ключей `telemetry.*` в ни одном JSON
- [ ] Загрузка `settings.json` без telemetry-блоков не вызывает ошибок
- [ ] LLM провайдеры не затронуты

---

## Этап 2: Hardware Fingerprint (малый, contained)

**Удалить:**
- `core/hardware_fingerprint.py`

**Почистить:**
- `ui/controller.py` — импорт `get_raw_hardware_fingerprint`, передача в managed release
- `app/wiring.py` — `raw_hardware_fingerprint_provider` wiring

**Верификация после этапа:**
- [ ] `grep -r "hardware_fingerprint\|get_raw_hardware_fingerprint\|compute_hardware_hash\|HardwareFingerprint"` — 0 совпадений
- [ ] `wiring.py` — нет упоминаний fingerprint
- [ ] `controller.py` — нет импорта fingerprint

---

## Этап 3: Discord OAuth + QQ Auth Dialog (UI компоненты)

**Удалить:**
- `core/discord_oauth_loopback.py`
- `core/discord_managed_oauth.py`
- `ui/components/discord_managed_auth_dialog.py`
- `ui/components/qq_managed_auth_dialog.py`

**Почистить:**
- `ui/controller.py` — импорты, `DISCORD_AUTH_ERROR_KEY_BY_SUBCODE`, `_discord_*` поля, методы `start_discord_managed_auth_from_dialog`, `_discord_auth_message_key`, `reopen_discord_managed_auth_browser`, `_local_managed_auth_blocking_source`
- `ui/app.py` — импорты, `_discord_*` поля/generation/cancel, все `_start_discord_*`, `_cancel_discord_*`, `_reopen_discord_*`, `_close_discord_*`, `show_discord_managed_auth_dialog`, `_preview_discord_*`, `mark_discord_managed_auth_callback_received`, `_write_discord_callback_preview_page`, `_on_discord_managed_auth_byok`
- `ui/components/debug_preview_panel.py` — `on_discord_auth`, `on_discord_callback_page`

**Верификация после этапа:**
- [ ] `grep -r "discord\|Discord"` в `*.py` — 0 совпадений (кроме комментариев)
- [ ] `controller.py` — нет `DISCORD_*` констант, нет `_discord_*` полей
- [ ] `app.py` — нет импортов Discord, нет диалогов
- [ ] `debug_preview_panel.py` — нет discord callbacks
- [ ] Запуск UI не падает (нет отсутствующих импортов)

---

## Этап 4: Managed OpenRouter Auth (главное удаление)

**Удалить:**
- `core/managed_openrouter_release.py` (1856 строк)
- `core/managed_openrouter_broker_client.py` (678 строк)
- `core/managed_identity.py`
- `core/managed_auth_claims.py`
- `core/openrouter_handoff.py`

**Почистить:**
- `config/settings.py` — `ManagedIdentitySettings`, `managed_identity` поле, `_parse_managed_*`, `_migrate_settings_dict` ветки (~122 ссылки)
- `app/wiring.py` — managed release service, broker client, identity construction (~60 ссылок)
- `ui/controller.py` — `_managed_release_service`, `dashboard_managed_auth_action`, managed auth lifecycle (~163 ссылок)
- `ui/app.py` — managed auth UI (~133 ссылки)
- `ui/event_bridge.py` — `ManagedOpenRouterUserFacingError`, `clear_managed_auth_pending_state`
- `ui/views/settings.py` — `TalkTogetherPassStatus`, managed identity fields
- `ui/views/dashboard.py` — managed auth pending state
- `core/orchestrator/hub.py` — `ManagedOpenRouterReleaseDiagnostics`, `ManagedOpenRouterUserFacingError`
- `providers/llm/openrouter.py` — `normalize_managed_openrouter_user_identifier`

**Верификация после этапа:**
- [ ] `grep -r "managed_openrouter\|managed_identity\|managed_auth\|ManagedOpenRouter\|ManagedIdentity"` — 0 совпадений
- [ ] `settings.py` — нет ManagedIdentitySettings, нет managed_identity serialization
- [ ] `wiring.py` — нет managed release/broker/identity construction
- [ ] `controller.py` — нет managed auth methods
- [ ] `app.py` — нет managed auth UI
- [ ] `openrouter.py` — работает без managed auth (BYOK путь остаётся)
- [ ] `providers/llm/openrouter.py` — импорт/инициализация не падают
- [ ] i18n — нет ключей `managed_auth.*`

---

## Этап 5: Финальная очистка

- `ui/components/debug_preview_panel.py` — удалить все managed/discord/telemetry preview actions
- `ui/views/dashboard.py` — удалить managed auth pending UI
- `settings.json` — удалить `managed_identity` блок если есть
- Проверить что `openrouter_credentials.py` остался (используется BYOK)
- Проверить i18n файлы на оставшиеся ключи `managed_auth.*`, `discord_auth.*`, `qq_auth.*`
- Проверить `pyproject.toml` на удалённые зависимости

**Верификация после этапа:**
- [ ] `openrouter_credentials.py` существует и не содержит managed auth импортов
- [ ] `settings.json` — нет managed_identity, deepgram_stt, soniox_stt, qwen_asr_stt блоков
- [ ] `pyproject.toml` — нет deepgram-sdk, google-genai только если нужен
- [ ] Все i18n JSON — нет ключей удалённых систем

---

## Этап 6: Широкая верификация

### 6.1 Проверка удалённых импортов
- [ ] `grep -r "from puripuly_heart.core.telemetry"` — 0
- [ ] `grep -r "from puripuly_heart.core.hardware_fingerprint"` — 0
- [ ] `grep -r "from puripuly_heart.core.discord_"` — 0
- [ ] `grep -r "from puripuly_heart.core.managed_"` — 0
- [ ] `grep -r "from puripuly_heart.ui.components.telemetry_"` — 0
- [ ] `grep -r "from puripuly_heart.ui.components.discord_"` — 0
- [ ] `grep -r "from puripuly_heart.ui.components.qq_"` — 0

### 6.2 Проверка что удалённых файлов нет на диске
- [ ] `glob: puripuly_heart/core/telemetry.py` — не найден
- [ ] `glob: puripuly_heart/core/hardware_fingerprint.py` — не найден
- [ ] `glob: puripuly_heart/core/discord_oauth_loopback.py` — не найден
- [ ] `glob: puripuly_heart/core/discord_managed_oauth.py` — не найден
- [ ] `glob: puripuly_heart/core/managed_openrouter_release.py` — не найден
- [ ] `glob: puripuly_heart/core/managed_openrouter_broker_client.py` — не найден
- [ ] `glob: puripuly_heart/core/managed_identity.py` — не найден
- [ ] `glob: puripuly_heart/core/managed_auth_claims.py` — не найден
- [ ] `glob: puripuly_heart/core/openrouter_handoff.py` — не найден
- [ ] `glob: puripuly_heart/ui/components/telemetry_consent_dialog.py` — не найден
- [ ] `glob: puripuly_heart/ui/components/discord_managed_auth_dialog.py` — не найден
- [ ] `glob: puripuly_heart/ui/components/qq_managed_auth_dialog.py` — не найден

### 6.3 Проверка что Python-цепочки импортов не падают
- [ ] `python -c "from puripuly_heart.config.settings import AppSettings"` — OK
- [ ] `python -c "from puripuly_heart.app.wiring import create_wiring"` — OK
- [ ] `python -c "from puripuly_heart.ui.controller import Controller"` — OK
- [ ] `python -c "from puripuly_heart.ui.app import App"` — OK
- [ ] `python -c "from puripuly_heart.providers.llm.openrouter import OpenRouterLLMProvider"` — OK
- [ ] `python -c "from puripuly_heart.providers.llm.gemini import GeminiLLMProvider"` — OK
- [ ] `python -c "from puripuly_heart.providers.llm.deepseek import DeepSeekLLMProvider"` — OK
- [ ] `python -c "from puripuly_heart.providers.llm.cerebras import CerebrasLLMProvider"` — OK
- [ ] `python -c "from puripuly_heart.providers.llm.qwen import QwenLLMProvider"` — OK
- [ ] `python -c "from puripuly_heart.providers.llm.local_openai import LocalOpenAILLMProvider"` — OK
- [ ] `python -c "from puripuly_heart.ui.views.settings import SettingsView"` — OK
- [ ] `python -c "from puripuly_heart.ui.views.dashboard import DashboardView"` — OK
- [ ] `python -c "from puripuly_heart.core.orchestrator.hub import OrchestratorHub"` — OK
- [ ] `python -c "from puripuly_heart.core.language import get_stt_compatibility_warning"` — OK
- [ ] `python -c "from puripuly_heart.ui.event_bridge import EventBridge"` — OK

### 6.4 Проверка Settings без ошибок
- [ ] Загрузка `settings.json` без `telemetry` блока — AppSettings создаётся
- [ ] Загрузка `settings.json` без `managed_identity` блока — AppSettings создаётся
- [ ] Загрузка `settings.json` с legacy `deepgram_stt` — fallback работает
- [ ] `AppSettings().validate()` — без ошибок
- [ ] `settings_to_dict(AppSettings())` — без ключей удалённых систем

### 6.5 Проверка LLM провайдеров
- [ ] OpenRouter — BYOK путь работает (без managed auth)
- [ ] Gemini — прямой API key путь работает
- [ ] DeepSeek — работает
- [ ] Cerebras — работает
- [ ] Qwen — работает
- [ ] Local OpenAI — работает

### 6.6 Проверка STT (не тронуты, но на всякий)
- [ ] Все 8 LOCAL_* провайдеров — импорты работают
- [ ] `providers/stt/__init__.py` — нет удалённых провайдеров

### 6.7 Проверка i18n
- [ ] Все 35 JSON файлов — нет ключей `telemetry.*`
- [ ] Все 35 JSON файлов — нет ключей `discord_auth.*`
- [ ] Все 35 JSON файлов — нет ключей `qq_auth.*`
- [ ] Все 35 JSON файлов — нет ключей `managed_auth.*`
- [ ] Нет дублирующихся ключей между файлами

### 6.8 Проверка pyproject.toml
- [ ] Нет `deepgram-sdk` зависимости
- [ ] Есть `google-genai` если нужен Gemini
- [ ] Есть `websockets` если нужен overlay bridge
- [ ] Нет зависимостей удалённых систем

### 6.9 Проверка settings.json
- [ ] Нет `deepgram_stt` блока
- [ ] Нет `qwen_asr_stt` блока
- [ ] Нет `soniox_stt` блока
- [ ] Нет `managed_identity` блока
- [ ] Нет `api_key_verified.deepgram`
- [ ] Нет `api_key_verified.soniox`

### 6.10 Проверка на orphaned references
- [ ] `grep -r "MANAGED_AUTH_CLAIM_SOURCE"` — 0
- [ ] `grep -r "MANAGED_OPENROUTER"` — 0
- [ ] `grep -r "ManagedOpenRouter"` — 0
- [ ] `grep -r "ManagedIdentity"` — 0
- [ ] `grep -r "DiscordOAuth"` — 0
- [ ] `grep -r "TELEMETRY_"` — 0 (кроме settings.py enum если остался)
- [ ] `grep -r "telemetry_consent"` — 0
- [ ] `grep -r "translation_success_day"` — 0
- [ ] `grep -r "handoff"` — 0
- [ ] `grep -r "fingerprint_salt"` — 0

### 6.11 Проверка на неиспользуемые импорты в затронутых файлах
- [ ] `controller.py` — все импорты используются
- [ ] `app.py` — все импорты используются
- [ ] `event_bridge.py` — все импорты используются
- [ ] `wiring.py` — все импорты используются
- [ ] `settings.py` — все импорты используются
- [ ] `debug_preview_panel.py` — все импорты используются
- [ ] `dashboard.py` — все импорты используются
- [ ] `hub.py` — все импорты используются

### 6.12 Проверка на мёртвый код после удаления
- [ ] Нет методов которые вызываются только удалёнными вызывающими
- [ ] Нет констант которые используются только удалёнными потребителями
- [ ] Нет enum значений которые нигде не проверяются

---

## Этап 7: Оценка оптимизации после изменений

После удаления всех 12 файлов и ~730 ссылок оценить:

**Структурная оптимизация:**
- [ ] `controller.py` — упростить методы которые теперь вызываются реже без managed auth проверок
- [ ] `app.py` — удалить dead callbacks которые больше никуда не вызываются
- [ ] `wiring.py` — упростить цепочку инициализации (меньше зависимостей = проще wiring)
- [ ] `settings.py` — упростить `_migrate_settings_dict` удалив ветки для удалённых провайдеров
- [ ] `dashboard.py` — упростить conditional rendering без managed auth pending state

**Возможная оптимизация:**
- [ ] Удалить `openrouter_handoff.py` если он стал мёртвым (проверить что никто не импортирует)
- [ ] Упростить `openrouter.py` удаля managed user identifier logic
- [ ] Проверить можно ли уменьшить размер `ui/app.py` (сейчас ~2000+ строк, много managed auth)

**Не трогать:**
- `openrouter_credentials.py` — нужен для BYOK
- `providers/llm/openrouter.py` — нужен для BYOK OpenRouter
- `providers/llm/gemini.py` — нужен для Google Gemini

---

## Сводка

| Этап | Файлов удалено | Ссылок почищено | Сложность |
|---|---|---|---|
| 1. Telemetry | 2 | ~80 | Низкая |
| 2. Fingerprint | 1 | ~3 | Низкая |
| 3. Discord + QQ Auth | 4 | ~100 | Средняя |
| 4. Managed OpenRouter | 5 | ~500 | Высокая |
| 5. Финальная очистка | 0 | ~50 | Средняя |
| 6. Широкая верификация | 0 | 0 | Проверка |
| 7. Оценка оптимизации | 0 | 0 | Анализ |

**Общий итог:** 12 файлов удалены, ~730+ ссылок почищены, ~2500 строк кода удалено.
