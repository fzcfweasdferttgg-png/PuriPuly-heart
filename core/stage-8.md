# Этап 8: Data types → domain/

Перенести чистые data types из core/ в domain/, чтобы ports/ не импортировал из core/.

## Нарушения dependency inversion

| Файл ports/ | Импортирует из | Тип |
|-------------|---------------|-----|
| overlay_transport.py | core/overlay/protocol.py | OverlayPresentationSnapshot |
| ui.py | core/overlay/protocol.py | OverlayPresentationSnapshot |
| overlay_process.py | core/overlay/manifest.py | OverlayLaunchManifest |
| peer.py | app/wiring.py | ResolvedPeerSTTConfig |

## Что переносим

### 1. core/overlay/protocol.py → domain/overlay_types.py

В protocol.py 7 dataclass'ов + 2 Literal alias + 6 приватных хелпера. Все 7 dataclass'ов связаны между собой — `OverlayPresentationSnapshot` зависит от `OverlayPresentationBlock`, `OverlayPresentationCalibration`, `NativeFreshRender*`, `NativeQuietTail*`. Переносим **все 7 dataclass'ов** целиком, иначе domain будет импортировать из core (циклическая зависимость).

Переносим:
- `OverlayPresentationCalibration`
- `OverlayPresentationBlock`
- `NativeFreshRenderGenerations`
- `NativeFreshRenderTargets`
- `NativeQuietTailEpisode`
- `NativeQuietTailEpisodes`
- `OverlayPresentationSnapshot`

Также переносим:
- `BlockVariant` (Literal alias)
- `ChannelId` (Literal alias) — в domain/models.py уже есть, можно переиспользовать
- `U64_MAX` (константа)
- 6 приватных `_require_*` / `_optional_*` / `_validate_*` хелперов

### 2. core/overlay/manifest.py → domain/overlay_types.py

- `OverlayLaunchManifest`
- `OVERLAY_CONTRACT_VERSION` (константа)
- `_MANIFEST_FIELDS` (приватная)
- `normalize_overlay_logging_mode()` (функция)

**Проблема:** `OverlayLaunchManifest` зависит от `core.runtime_logging.SessionLoggingMode`. Решение: `SessionLoggingMode` — это enum из config/settings. Можно импортировать из config/ (domain ← config допустимо).

### 3. app/wiring.py → domain/peer_types.py

- `ResolvedPeerSTTConfig` — зависит только от `STTProviderName` из config/settings. Чисто.

## Не трогаем

- `PeerChannelRuntimeState`, `PeerRuntimeConfig` — уже в ports/peer.py
- Реализации (OverlayPresenter, OverlayBridge) остаются в core/
- `AppliedContextMode` — остаётся в ports/overlay.py

## Порядок

1. Создать `domain/overlay_types.py` — перенести ВСЕ 7 dataclass'ов + типы + хелперы из protocol.py + OverlayLaunchManifest + константы из manifest.py
2. Создать `domain/peer_types.py` — перенести ResolvedPeerSTTConfig из wiring.py
3. Обновить `core/overlay/protocol.py` — оставить только re-export из domain/
4. Обновить `core/overlay/manifest.py` — оставить только re-export из domain/
5. Обновить `app/wiring.py` — импорт ResolvedPeerSTTConfig из domain/
6. Обновить `ports/overlay_transport.py`, `ports/ui.py`, `ports/overlay_process.py`, `ports/peer.py` — импорты из domain/
7. Обновить остальных потребителей (7+ файлов для overlay types, 2 файла для manifest)

## Могу проверить

- `python -m py_compile` на каждом файле
- `grep "from puripuly_heart.core" app/src/puripuly_heart/ports/` — 0 совпадений
- `grep "from puripuly_heart.app" app/src/puripuly_heart/ports/` — 0 совпадений
- Весь проект компилируется

## Риск

Средний. Перенос 7 dataclass'ов + manifest вместо 3. Зависимость manifest от SessionLoggingMode требует проверки.
