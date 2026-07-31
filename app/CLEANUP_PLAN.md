# План чистки мёртвого кода PuriPuly-heart

## Верификация: что НЕ трогаем

| Элемент | Статус | Почему не трогаем |
|---------|--------|-------------------|
| `LocalLLMSettings` + `LocalLLMBackend` | ✅ ЖИВОЙ | Фича "свой OpenAI-совместимый сервер" (Ollama, llama.cpp, vLLM) |
| `LocalOpenAICompatibleLLMProvider` | ✅ ЖИВОЙ | Используется wiring.py для LOCAL_LLM |
| `LocalOpenAICompatibleLLMProvider.verify_connection()` | ⚠️ Не вызывается из UI | Баг — UI не проверяет local_llm. Не удалять, а починить отдельно |
| `core/overlay/diagnostics.py` stubs | ✅ ЖИВОЙ | Intentional no-op protocol implementations. Класс используется |
| `OverlayPresenter._emit_pair_state()` | ✅ ЖИВОЙ | Intentional stub |
| `LLMProviderName` старые значения | ✅ Миграционный shim | Нужны для парсинга old settings файлов |
| `ApiKeyVerificationSettings` старые поля | ✅ Harmless | Default False, не мешают. Сериализация backward compat |
| All STT `create_*` factory functions | ✅ ЖИВОЙ | Используются worker.py |
| All STT `LoadError` exceptions | ✅ ЖИВОЙ | Используются controller.py |

## Верификация: что нужно решить

| Элемент | Верификация | Рекомендация |
|---------|-------------|--------------|
| `FallbackRacingLLMProvider` | ✅ Класс generic, не зависит от удалённых провайдеров. Агент рекомендует ОСТАВИТЬ | **Оставить класс**, удалить мёртвую обвязку (dropdown, alias значения, no-op handlers) |
| GitHub Star Prompt | Автоматический триггер мёртв (eligibility всегда False) | **Удалить** автоматический триггер, оставить preview через debug panel |
| 5× `Local*STTBackend` + Session classes | ✅ Агент подтвердил: wiring.py всегда создаёт SubprocessSTTBackend, нет флагов переключения | **Удалить** Backend+Session классы, оставить `create_*` функции и `LoadError` |
| `_validate_channel()` дублирование | Не мёртвый код, а дублирование | **Не трогать** — отдельная задача |

---

## Фаза 1: Удаление мёртвых файлов (~580 строк, 0 риска)

Просто `git rm`. Никаких побочных эффектов — файлы нигде не импортируются.

| # | Файл | Строк | Примечание |
|---|------|-------|------------|
| 1 | `core/oauth_callback_page.py` | 70 | |
| 2 | `core/inference/protocol.py` | 155 | |
| 3 | `ui/desktop_overlay_readiness.py` | 254 | |
| 4 | `ui/components/bento_card.py` | 40 | |
| 5 | `core/osc/encoding.py` | 60 | |
| 6 | `data/providers.json` | — | |
| 7 | `data/fonts/NotoSansCJK-Medium.ttc` | — | |
| 8 | `data/licenses/COPYING.LGPL-2.1.txt` | — | |
| 9 | `data/models/__init__.py` | 1 | |

**НЕ удаляем**: `core/llm/fallback_racing.py` — класс generic, агент рекомендует оставить.

После удаления: проверить `__init__.py` re-exports (если ссылаются на удалённые файлы).

### ⚡ Оценка последствий Фазы 1
- **Риск**: Нулевой. Все файлы проверены — 0 импортов из любого места проекта.
- **Возможные баги**: None. Файлы не участвуют в runtime.
- **Проверка после**: `python -c "import puripuly_heart"` — должен импортироваться без ошибок.

---

## Фаза 2: Мёртвые классы в живых файлах (~700 строк)

### 2a. 5× in-process STT Backend + Session classes
Удалить из каждого файла только Backend + Session классы. Оставить `create_*` функции, `LoadError`, константы.

| Файл | Удалить | Оставить |
|------|---------|----------|
| `providers/stt/local_gigaam_rnnt.py` | `LocalGigaamRnntSTTBackend`, `_LocalGigaamRnntSession` | `create_local_gigaam_rnnt_recognizer`, `LocalGigaamRnntLoadError` |
| `providers/stt/local_parakeet_ctc.py` | `LocalParakeetCtcSTTBackend`, `_LocalParakeetCtcSession` | `create_local_parakeet_ctc_recognizer`, `LocalParakeetCtcLoadError` |
| `providers/stt/local_parakeet_tdt.py` | `LocalParakeetTdtSTTBackend`, `_LocalParakeetTdtSession` | `create_local_parakeet_tdt_recognizer`, `LocalParakeetTdtLoadError` |
| `providers/stt/local_qwen_sherpa.py` | `LocalQwenSherpaSTTBackend`, `_LocalQwenSherpaSession` | `create_local_qwen_sherpa_recognizer`, `LocalQwenSherpaLoadError` |
| `providers/stt/local_transcribecpp.py` | `LocalTranscribecppSTTBackend`, `_LocalTranscribecppSession` | `create_transcribecpp_recognizer`, `LocalTranscribecppLoadError` |

### 2b. Overlay dead classes
| Файл | Удалить |
|------|---------|
| `core/overlay/sink.py` | `NullOverlaySink`, `OverlayStreamCoalescer` |

### 2c. Storage dead classes
| Файл | Удалить |
|------|---------|
| `core/storage/secrets.py` | `InMemorySecretStore`, `mask_secret()` |

### ⚡ Оценка последствий Фазы 2
- **Риск**: Низкий. Backend+Session классы никогда не инстанцируются.
- **Возможные баги**: Если в `create_*` функциях есть вызовы методов удалённых классов — будет ImportError. Проверить что `create_*` возвращают свои собственные типы, а не Backend/Session.
- **Проверка после**: grep удалённых имён на остатки ссылок. Проверить что `worker.py` и `controller.py` компилируются.

---

## Фаза 3: Мёртвые методы/функции в живых файлах (~300 строк)

### 3a. `config/settings.py`
| Что | Строки |
|-----|--------|
| `REFERRAL_ID_LENGTH`, `REFERRAL_ID_ALPHABET` | 30-31 |
| `normalize_owned_referral_id()` | 88-98 |
| `DesktopFletOverlayBounds` class + `_parse_desktop_flet_bounds` + `_normalize_desktop_flet_bounds_position` | 643-656, 869-875, 956-968 |
| `AppSettings.overlay_calibration` property | 804-810 |
| `_loaded_llm_provider()` | 1303-1311 |
| `LEGACY_LOW_LATENCY_VAD_HANGOVER_MS` import | 21 |

### 3b. `config/prompts.py`
| Что | Строки |
|-----|--------|
| `list_prompts()` | 106-115 |
| `get_default_prompt()` | 327-329 |
| `_reset_prompt_cache_for_tests()` | 201-204 |

### 3c. `ui/app.py`
| Что | Строки |
|-----|--------|
| `import tempfile` | 5 |
| `AppSettings` import | 12 |
| `FOUNDER_CONTACT_URL` | 51 |
| `_open_settings_tab()` | 577 |
| `_revert_dashboard_translation_toggle()` | 734 |
| `_set_dashboard_translation_visual_state()` | 737 |
| `_translation_enable_succeeded()` | 993 |
| `_on_founder_letter_contact()` | 1003 |
| `show_founder_letter_dialog()` | 1009 |

### 3d. `ui/i18n.py`
| Что | Строки |
|-----|--------|
| `translated_source_label()` | 181 |

### 3e. `ui/views/logs.py`
| Что | Строки |
|-----|--------|
| `attach_log_handler()` | 306 |

### 3f. `ui/event_bridge.py`
| Что | Строки |
|-----|--------|
| 3× `add_history_entry` ghost getattr | 229, 265, 277 |

### 3g. `core/orchestrator/hub.py`
| Что | Строки |
|-----|--------|
| `_get_valid_context()` | 772-778 |
| `_format_context_for_llm()` | 780-783 |
| `translate_peer_text_for_test()` | 2930-2960 |
| `handle_peer_transcript_final_for_test()` | 2965-2990 |

### 3h. `core/overlay/`
| Файл | Что |
|------|-----|
| `openvr_vendor.py` | `collect_vendored_openvr_runtime_binaries()` |
| `diagnostics.py` | `_sorted_events()`, `_iter_all_events()` |
| `presenter.py` | `update_native_retry_ownership()` + 5 helpers |

### 3i. `core/audio/format.py`
| Что | Строки |
|-----|--------|
| `resample_f32_linear()` | 43-60 |
| `normalize_audio_f32()` | 63-76 |
| `normalize_audio_frame_f32()` | 78-84 |

### 3j. `core/local_stt_runtime_installer.py`
| Что | Строки |
|-----|--------|
| `ensure_local_stt_installed()` | 190-250 |

### 3k. `core/language.py`
| Что | Строки |
|-----|--------|
| `is_supported_language()` | ~5 |

### 3l. `core/inference/worker.py`
| Что | Строки |
|-----|--------|
| `local_parakeet_ctc` branch | 126-145 (unreachable — нет такого STTProviderName) |

### ⚡ Оценка последствий Фазы 3
- **Риск**: Средний. Удаление методов в живых файлах — больше шанс задеть что-то.
- **Возможные баги**:
  - `DesktopFletOverlayBounds` — если `from_dict()` парсит старые settings с `bounds` ключом, удаление класса сломает парсинг. **Проверить** что `_parse_desktop_flet_position` не зависит от `_parse_desktop_flet_bounds`.
  - `event_bridge.py` ghost calls — удаление `getattr` блоков может изменить порядок выполнения. Проверить что `if add_history is not None:` блок не содержит важного кода после него.
  - `hub.py` test helpers — если тесты снаружи `src/` их вызывают, сломаются. **Проверить** `tests/` директорию.
  - `ensure_local_stt_installed()` — если UI планирует auto-install, удаление закроет эту возможность.
- **Проверка после**: `python -c "from puripuly_heart.config.settings import AppSettings; AppSettings()"` — должен работать. Проверить что settings файлы парсятся.

---

## Фаза 4: Мёртвые UI компоненты

| Файл | Что |
|------|-----|
| `ui/components/settings/settings_section.py` | Весь класс `SettingsSection` |
| `ui/components/settings/provider_selector.py` | Весь класс `ProviderSelector` |
| `ui/components/settings/__init__.py` | Удалить re-exports этих двух классов |

### ⚡ Оценка последствий Фазы 4
- **Риск**: Низкий. Компоненты никогда не инстанцируются.
- **Возможные баги**: Если `__init__.py` re-exports используются где-то через wildcard import (`from ...settings import *`). Проверить что нет `from puripuly_heart.ui.components.settings import *`.
- **Проверка после**: grep `SettingsSection` и `ProviderSelector` на остатки.

---

## Фаза 5: Мёртвое поведение (нужно решение)

### 5a. GitHub Star Prompt
Автоматический триггер мёртв (eligibility всегда False). Два варианта:
- **Вариант A**: Удалить всю логику автоматического триггера из controller.py + app.py, оставить только debug preview
- **Вариант B**: Обновить eligibility для OPENAI_COMPATIBLE

**Рекомендация**: Вариант A — проще, меньше кода. Debug preview оставить.

### 5b. Fallback Selection Alias + Dropdown
- `TranslationFallbackSelectionAlias` enum значения кроме NONE — удалить
- Fallback dropdown в `views/settings.py` — удалить
- `_on_openrouter_fallback_click` no-op handler — удалить
- `_on_qwen_region_click` no-op handler + `QwenSettings` + `QwenRegion` — **оставить** (GUI, не трогаем)

### 5c. TranslationModel/Connection старые значения
- `TranslationModel` старые значения (GEMMA4, DEEPSEEK_V4_*, etc.)
- `TranslationConnection.OPENROUTER/OFFICIAL_BYOK`
- `TRANSLATION_CONNECTIONS_BY_MODEL` мёртвые маппинги
- `TRANSLATION_CONNECTION_PRIORITY`

**Рекомендация**: Оставить как миграционный shim — старые settings файлы могут содержать эти значения.

### ⚡ Оценка последствий Фазы 5
- **Риск**: Средний-высокий. Затрагивает UI и controller логику.
- **Возможные баги**:
  - GitHub Star: удаление `_github_star_prompt_*` методов из controller может сломать `_github_star_prompt_state_snapshot` / `_restore_github_star_prompt_state_snapshot` которые вызываются из `_persist_github_star_prompt_mutation`. Удалять нужно каскадно — все методы вместе.
  - Fallback dropdown: удаление `_TRANSLATION_FALLBACK_SELECTION_ORDER` и label maps из views/settings.py может сломать `apply_locale()` если она ссылается на fallback i18n ключи.
  - `_on_openrouter_fallback_click` удаление — проверить что callback нигде не передаётся кроме как в UI виджет.
- **Проверка после**: Запустить GUI, проверить что settings view открывается без ошибок.

---

## Фаза 6: __init__.py cleanup

Удалить мёртвые re-exports из `__init__.py` файлов. Все потребители импортируют напрямую из подмодулей.

### ⚡ Оценка последствий Фазы 6
- **Риск**: Низкий. Все потребители импортируют напрямую.
- **Возможные баги**: Если есть внешние скрипты или тесты, которые импортируют через `__init__.py`. Проверить `tests/` директорию.
- **Проверка после**: `python -c "import puripuly_heart"` + все подпакеты.

---

## Фаза 7: Оптимизация и оценка функциональности

После удаления всего мёртвого кода — провести финальную оценку.

### 7a. Проверка целостности
- [ ] `python -c "import puripuly_heart"` — без ошибок
- [ ] `python -c "from puripuly_heart.config.settings import AppSettings; s = AppSettings(); print(s.to_dict())"` — settings сериализуются
- [ ] Проверить что старый settings файл (если есть) парсится через `from_dict()`
- [ ] `grep -r "ImportError\|ModuleNotFoundError" src/` — нет битых импортов

### 7b. Оценка оставшегося мёртвого кода (harmless, не трогаем)
- [ ] `LLMProviderName` старые значения (миграционный shim) — подтвердить что `_parse_llm_provider()` их обрабатывает
- [ ] `ApiKeyVerificationSettings` старые поля — подтвердить что default False не мешает
- [ ] `QwenSettings` + `QwenRegion` — подтвердить что UI не ломается
- [ ] `TranslationModel/Connection` старые значения — подтвердить что `materialize_translation_settings()` их обрабатывает
- [ ] ~70 мёртвых i18n ключей — подтвердить что не мешают

### 7c. Оптимизация (не обязательно, но полезно)
- [ ] Проверить что `_looks_repetitive()` дублирование в 4 STT файлах можно вынести в общий модуль (отдельная задача)
- [ ] Проверить что `_validate_channel()` дублирование в 3 файлах можно вынести в общий модуль (отдельная задача)
- [ ] Проверить что `_format_log_message()` дублирование в 2 файлах можно вынести в общий модуль (отдельная задача)
- [ ] Проверить что `_HubVadSink` дублирование в 2 файлах можно вынести в общий модуль (отдельная задача)
- [ ] Оценить можно ли объединить `OpenAICompatibleSettings` и `LocalLLMSettings` (оба имеют base_url + model)

### 7d. Известные баги (не связанные с чисткой, но обнаруженные)
- [ ] `LocalOpenAICompatibleLLMProvider.verify_connection()` — не вызывается из UI для local_llm
- [ ] `_iter_all_events()` в diagnostics.py — пропускает 5 из 7 deque полей (баг, но поля никогда не заполняются)
- [ ] `worker.py` branch для `local_parakeet_ctc` — unreachable (нет такого STTProviderName)

### 7e. Итоговая оценка
- Подсчитать итого удалённых строк
- Подсчитать итого удалённых файлов
- Оценить улучшение читаемости кодовой базы
- Зафиксировать что осталось как "technical debt" для будущих задач

---

## Порядок执行

1. Фаза 1 (файлы) → commit → ⚡ оценка
2. Фаза 2a (STT backends) → commit → ⚡ оценка
3. Фаза 2b-2c (overlay/storage) → commit → ⚡ оценка
4. Фаза 3 (методы/функции) → commit → ⚡ оценка
5. Фаза 4 (UI компоненты) → commit → ⚡ оценка
6. Фаза 5 (мёртвое поведение) → commit → ⚡ оценка (после решения)
7. Фаза 6 (__init__.py) → commit → ⚡ оценка
8. Фаза 7 (оптимизация + итоговая оценка)

Каждая фаза: сделать изменения → проверить что файл синтаксически валиден → ⚡ оценка последствий → commit.
