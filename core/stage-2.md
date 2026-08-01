# Этап 2: Настройки

Разбить config/settings.py (1842 строки, 8 enum + 18 dataclass).

## Реальная структура

Enum-ы (8): STTProviderName, LLMProviderName, SecretsBackend, QwenRegion, LocalLLMBackend, TranslationFallbackSelectionAlias, TranslationModel, TranslationConnection

Dataclass-ы (18): TranslationSettings, LanguageSettings, AudioSettings, DesktopAudioSettings, STTSettings, LLMSettings, OSCSettings, OpenAICompatibleSettings, ProviderSettings, SecretsSettings, QwenSettings, LocalLLMSettings, UiSettings, DesktopFletOverlayBounds, DesktopFletOverlayPosition, DesktopFletOverlayVisualSettings, DesktopFletOverlaySettings, OverlaySettings, ApiKeyVerificationSettings, AppSettings

Константа: STT_INTERNAL_SAMPLE_RATE_HZ = 16000 (line 24)

## Неочевидное

**1. settings.py — это dataclass-ы с JSON-сериализацией.**
Классы settings используют `@dataclass` или Pydantic. При разбиении на файлы — каждый dataclass должен сохранить свои field defaults, validators, `__post_init__`. Не копировать "голый" класс без контекста.

**2. Миграции схемы — fragile code.**
Функции типа `_migrate_v29_to_v30` обращаются к полям по строковым ключам. При разбиении — миграции остаются в base.py, но они импортируют enum-ы. Если enum переименован или перенесён — миграция сломается неочевидно (при загрузке старого settings.json).

**3. Значения по умолчанию зависят от платформы.**
Некоторые defaults вычисляются (путь к аудиоустройству, путь к моделям). Эти вычисления содержат `os.path`, `platform` вызовы. При переносе в отдельный файл — проверить что импорты на месте.

**4. Enum-ы содержат migration shim-ы.**
`STTProviderName` содержит старые значения (LOCAL_QWEN_SHERPA_ONNX → LOCAL_QWEN_SHERPA) для обратной совместимости. Эти shim-ы должны быть рядом с enum. Переносим shim-ы вместе с enum в enums.py.

**5. `settings/` как пакет заменяет `settings.py`.**
Нужно удалить settings.py и создать settings/. Если git не отслеживает rename — будет два коммита: удаление + создание. Убедиться что .py файл удалён, иначе Python будет импортировать settings.py вместо settings/.

## Порядок разбиения

1. Enums (8) → первыми, без зависимостей друг от друга
2. Простые dataclass-ы (зависят только от enum): AudioSettings, DesktopAudioSettings, LanguageSettings, STTSettings, LLMSettings, OSCSettings, SecretsSettings, QwenSettings, LocalLLMSettings, UiSettings, DesktopFletOverlayBounds/Position/VisualSettings/Settings, OverlaySettings, ApiKeyVerificationSettings
3. TranslationSettings (зависит от TranslationModel, TranslationConnection, TranslationFallbackSelectionAlias + логика валидации)
4. OpenAICompatibleSettings (зависит от LLMProviderName)
5. ProviderSettings (зависит от STTProviderName, LLMProviderName, OpenAICompatibleSettings)
6. AppSettings (зависит от всех) + сериализация/миграция → base.py
7. STT_INTERNAL_SAMPLE_RATE_HZ константа → base.py или stt.py

## Могу проверить (CLI)

- `python -m py_compile` на каждом файле в settings/
- `python -c "from puripuly_heart.config.settings import AppSettings"` — не упадёт
- `python -c "from puripuly_heart.config.settings import STTProviderName"` — не упадёт
- `python -c "from puripuly_heart.config.settings import LLMProviderName"` — не упадёт
- Проверить что config/settings.py удалён
