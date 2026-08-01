# Этап 18: Верификация после рефакторинга

После этапов 12-17. Запуск приложения — не в scope. Проверки статические и логические.

Все команды — через Python (Windows совместимость).

## 1. Статическая компиляция

Полная компиляция всех .py файлов проекта:

```python
import py_compile, pathlib
errors = []
for f in pathlib.Path("app/src/puripuly_heart").rglob("*.py"):
    try:
        py_compile.compile(str(f), doraise=True)
    except py_compile.PyCompileError as e:
        errors.append(str(e))
if errors:
    for e in errors: print(f"FAIL: {e}")
else:
    print("PASS: all .py files compile")
```

## 2. Dependency direction

```python
import pathlib, re

SRC = pathlib.Path("app/src/puripuly_heart")
rules = {
    "ports/":    {"must_not_import_from": ["core.", "adapters."]},
    "core/":     {"must_not_import_from": ["adapters."]},
    "domain/":   {"must_not_import_from": ["core.", "adapters.", "ports."]},
    "application/": {"must_not_import_from": ["core.", "adapters."]},
}
import_re = re.compile(r"from\s+puripuly_heart\.(\S+)")
violations = []
for layer, rule in rules.items():
    for f in (SRC / layer.replace("/", "")).rglob("*.py"):
        for line in f.read_text(encoding="utf-8").splitlines():
            m = import_re.search(line)
            if m:
                target = m.group(1)
                for forbidden in rule["must_not_import_from"]:
                    if target.startswith(forbidden):
                        violations.append(f"{f}: imports from {target}")
for v in violations:
    print(f"FAIL: {v}")
if not violations:
    print("PASS: dependency direction clean")
```

## 3. Проверка целостности портов

```python
import pathlib, re

ports_dir = pathlib.Path("app/src/puripuly_heart/ports")

# Все порты — Protocol
protocol_ok = True
for f in ports_dir.glob("*.py"):
    if f.name == "__init__.py":
        continue
    text = f.read_text(encoding="utf-8")
    if "Protocol" not in text:
        print(f"FAIL: {f} does not contain Protocol")
        protocol_ok = False

# Нет NotImplementedError
not_impl_ok = True
for f in ports_dir.rglob("*.py"):
    text = f.read_text(encoding="utf-8")
    if "NotImplementedError" in text:
        print(f"FAIL: {f} contains NotImplementedError")
        not_impl_ok = False

# LLMProvider — Protocol
llm_port = ports_dir / "llm.py"
llm_text = llm_port.read_text(encoding="utf-8")
if "class LLMProvider(Protocol)" not in llm_text:
    print("FAIL: LLMProvider is not Protocol")
else:
    print("PASS: LLMProvider is Protocol")

if protocol_ok and not_impl_ok:
    print("PASS: all ports are Protocol, no NotImplementedError")
```

## 4. Проверка перемещённых файлов

```python
import pathlib

SRC = pathlib.Path("app/src/puripuly_heart")
old_files = [
    "core/osc/chatbox_paginator.py",
    "core/osc/udp_sender.py",
    "core/storage/secrets.py",
    "core/overlay/sink.py",
]
new_files = [
    "adapters/osc/chatbox_paginator.py",
    "adapters/osc/udp_sender.py",
    "adapters/storage/secrets.py",
    "adapters/overlay/sink.py",
]

for f in old_files:
    if (SRC / f).exists():
        print(f"FAIL: old file still exists: {f}")
    else:
        print(f"PASS: old file removed: {f}")

for f in new_files:
    if (SRC / f).exists():
        print(f"PASS: new file exists: {f}")
    else:
        print(f"FAIL: new file missing: {f}")

# Нет dangling импортов на старые пути
import re
old_imports = [
    "from puripuly_heart.core.osc.chatbox_paginator",
    "from puripuly_heart.core.osc.udp_sender",
    "from puripuly_heart.core.storage.secrets",
    "from puripuly_heart.core.overlay.sink",
]
for f in SRC.rglob("*.py"):
    text = f.read_text(encoding="utf-8")
    for old_imp in old_imports:
        if old_imp in text:
            print(f"FAIL: {f} still imports {old_imp}")

# core/storage/ удалена
if (SRC / "core" / "storage").exists():
    remaining = list((SRC / "core" / "storage").iterdir())
    real_files = [f for f in remaining if f.name != "__init__.py"]
    if real_files:
        print(f"FAIL: core/storage/ still has files: {[f.name for f in real_files]}")

# core/osc/sender.py удалён
if (SRC / "core/osc/sender.py").exists():
    print("FAIL: core/osc/sender.py still exists (should be deleted in stage 13)")

# __init__.py файлы обновлены
adapters_init = (SRC / "adapters/__init__.py").read_text(encoding="utf-8")
for module in ["osc", "storage", "overlay"]:
    if f'"{module}"' not in adapters_init:
        print(f"FAIL: adapters/__init__.py missing '{module}' in __all__")

osc_init = (SRC / "core/osc/__init__.py").read_text(encoding="utf-8")
if "chatbox_paginator" in osc_init:
    print("FAIL: core/osc/__init__.py still references chatbox_paginator")
if "udp_sender" in osc_init:
    print("FAIL: core/osc/__init__.py still references udp_sender")

ports_init = (SRC / "ports/__init__.py").read_text(encoding="utf-8")
if "OscSink" not in ports_init:
    print("FAIL: ports/__init__.py missing OscSink re-export")
```

## 5. Проверка стадии 12 (LLMProvider → Protocol)

```python
import pathlib

SRC = pathlib.Path("app/src/puripuly_heart")

# SemaphoreLLMProvider — concrete class, не Protocol
provider = (SRC / "core/llm/provider.py").read_text(encoding="utf-8")
if "class SemaphoreLLMProvider(LLMProvider)" in provider:
    print("FAIL: SemaphoreLLMProvider still inherits from LLMProvider")
elif "class SemaphoreLLMProvider:" in provider:
    print("PASS: SemaphoreLLMProvider is standalone class")
else:
    print("WARN: SemaphoreLLMProvider definition not found as expected")

# isinstance check в controller.py должен работать
controller = (SRC / "ui/controller.py").read_text(encoding="utf-8")
if "isinstance(llm, SemaphoreLLMProvider)" in controller:
    print("PASS: isinstance check for SemaphoreLLMProvider preserved")
```

## 6. Проверка стадии 13 (OscSink port)

```python
import pathlib

SRC = pathlib.Path("app/src/puripuly_heart")

# OscSink Protocol определён в ports/osc.py
osc_port = (SRC / "ports/osc.py").read_text(encoding="utf-8")
if "class OscSink(Protocol)" not in osc_port:
    print("FAIL: OscSink Protocol not in ports/osc.py")
else:
    print("PASS: OscSink Protocol defined")

# Pipeline использует OscSink тип
pipeline = (SRC / "core/pipeline/pipeline.py").read_text(encoding="utf-8")
if "osc: OscSink" in pipeline or "osc: OscSink |" in pipeline:
    print("PASS: Pipeline uses OscSink type")
elif "osc: ChatboxPaginator" in pipeline:
    print("FAIL: Pipeline still uses ChatboxPaginator type")
else:
    print("WARN: Pipeline osc type not found as expected")

# controller.py импортирует из adapters/osc/
if "from puripuly_heart.adapters.osc" in controller or "from puripuly_heart.adapters.osc." in controller:
    print("PASS: controller.py imports from adapters/osc/")
elif "from puripuly_heart.core.osc.chatbox_paginator" in controller:
    print("FAIL: controller.py still imports from core/osc/chatbox_paginator")

# OscSink имеет все 6 методов
expected = ["enqueue", "send_immediate", "send_typing", "set_typing_reason", "clear_typing_reasons", "process_due"]
missing = [m for m in expected if f"def {m}" not in osc_port]
if missing:
    print(f"FAIL: OscSink missing methods: {missing}")
else:
    print("PASS: OscSink has all 6 methods")
```

## 7. Проверка стадии 14 (Storage)

```python
import pathlib

SRC = pathlib.Path("app/src/puripuly_heart")

# wiring.py импортирует из adapters/storage/
wiring = (SRC / "app/wiring.py").read_text(encoding="utf-8")
if "from puripuly_heart.adapters.storage.secrets" in wiring:
    print("PASS: wiring.py imports from adapters/storage/")
elif "from puripuly_heart.core.storage.secrets" in wiring:
    print("FAIL: wiring.py still imports from core/storage/")
```

## 8. Проверка стадии 15 (Overlay)

```python
import pathlib

SRC = pathlib.Path("app/src/puripuly_heart")

# state.py импортирует event types из ports/overlay.py (не из adapters/overlay/sink.py)
state = (SRC / "core/overlay/state.py").read_text(encoding="utf-8")
if "from puripuly_heart.ports.overlay import" in state:
    print("PASS: state.py imports from ports/overlay.py")
elif "from puripuly_heart.core.overlay.sink import" in state:
    print("FAIL: state.py still imports from core/overlay/sink.py")
elif "from puripuly_heart.adapters.overlay.sink import" in state:
    print("FAIL: state.py imports event types from adapters/overlay/sink.py (should be ports/overlay.py)")

# pipeline.py импортирует OverlaySink из ports/overlay.py
if "from puripuly_heart.ports.overlay import" in pipeline or "from puripuly_heart.ports.overlay import OverlaySink" in pipeline:
    print("PASS: pipeline.py imports OverlaySink from ports/overlay.py")
elif "from puripuly_heart.core.overlay.sink import" in pipeline:
    print("FAIL: pipeline.py still imports OverlaySink from core/overlay/sink.py")

# OverlayEventAdapter импортируется из adapters/
if "from puripuly_heart.adapters.overlay.sink import OverlayEventAdapter" in pipeline:
    print("PASS: pipeline.py imports OverlayEventAdapter from adapters/")
elif "OverlayEventAdapter" in pipeline and "from puripuly_heart.core.overlay.sink" in pipeline:
    print("FAIL: pipeline.py imports OverlayEventAdapter from old path")
```

## 9. Проверка стадии 16 (State cleanup)

```python
import pathlib

SRC = pathlib.Path("app/src/puripuly_heart")

# Pipeline: нет forwarding-методов latency
latency_methods = [
    "_emit_latency_trace_if_ready",
    "_emit_latency_summary_if_ready",
    "_emit_latency_contract_if_ready",
    "_latency_hangover_ms",
    "_get_latency_timeline",
    "_elapsed_latency_ms",
]
for method in latency_methods:
    # Проверяем что метод НЕ определён в pipeline.py (def method_name)
    if f"def {method}" in pipeline:
        print(f"FAIL: pipeline.py still defines forwarding method: {method}")

# Pipeline: нет __setattr__
if "def __setattr__" in pipeline:
    print("FAIL: pipeline.py still has __setattr__ override")
else:
    print("PASS: __setattr__ removed from Pipeline")

# Pipeline: нет _sync_self_runtime_aliases
if "def _sync_self_runtime_aliases" in pipeline:
    print("FAIL: pipeline.py still has _sync_self_runtime_aliases")
else:
    print("PASS: _sync_self_runtime_aliases removed")

# Pipeline: нет _sync_self_runtime_aliases вызова в __post_init__
if "_sync_self_runtime_aliases" in pipeline:
    print("FAIL: _sync_self_runtime_aliases still called")

# Mixin'ы используют self._latency напрямую
stages_dir = SRC / "core/pipeline/stages"
old_latency_calls = []
for f in stages_dir.glob("*.py"):
    text = f.read_text(encoding="utf-8")
    for line_num, line in enumerate(text.splitlines(), 1):
        if "self._record_latency_stage" in line and "self._latency" not in line:
            old_latency_calls.append(f"{f}:{line_num}")
        if "self._emit_latency_" in line and "self._latency" not in line:
            old_latency_calls.append(f"{f}:{line_num}")
        if "self._clear_latency_" in line and "self._latency" not in line:
            old_latency_calls.append(f"{f}:{line_num}")
        if "self._finalize_latency_" in line and "self._latency" not in line:
            old_latency_calls.append(f"{f}:{line_num}")
if old_latency_calls:
    for loc in old_latency_calls:
        print(f"FAIL: mixin still uses Pipeline forwarding: {loc}")
else:
    print("PASS: all mixin latency calls go through self._latency")
```

## 10. Проверка стадии 17 (Application Services)

```python
import pathlib

SRC = pathlib.Path("app/src/puripuly_heart")

# application/__init__.py существует
if not (SRC / "application/__init__.py").exists():
    print("FAIL: application/__init__.py missing")
else:
    print("PASS: application/__init__.py exists")

# TranslationService существует и имеет translate метод
ts = (SRC / "application/translation_service.py").read_text(encoding="utf-8")
if "class TranslationService" not in ts:
    print("FAIL: TranslationService class not found")
elif "def translate" not in ts and "async def translate" not in ts:
    print("FAIL: TranslationService.translate not found")
else:
    print("PASS: TranslationService.translate exists")

# OutputDispatcher существует и имеет dispatch методы
od = (SRC / "application/output_dispatcher.py").read_text(encoding="utf-8")
if "class OutputDispatcher" not in od:
    print("FAIL: OutputDispatcher class not found")
else:
    print("PASS: OutputDispatcher exists")

# Pipeline содержит translation_service и output_dispatcher поля
if "translation_service" not in pipeline:
    print("FAIL: pipeline.py missing translation_service field")
if "output_dispatcher" not in pipeline:
    print("FAIL: pipeline.py missing output_dispatcher field")

# Pipeline НЕ содержит _translate_text (moved to TranslationService)
if "def _translate_text" in pipeline:
    print("FAIL: pipeline.py still defines _translate_text (should be in TranslationService)")

# Pipeline НЕ содержит _enqueue_osc (moved to OutputDispatcher)
if "def _enqueue_osc" in pipeline:
    print("FAIL: pipeline.py still defines _enqueue_osc (should be in OutputDispatcher)")

# Pipeline НЕ содержит _format_system_prompt (moved to TranslationService)
if "def _format_system_prompt" in pipeline:
    print("FAIL: pipeline.py still defines _format_system_prompt (should be in TranslationService)")

# Pipeline СОДЕРЖИТ публичные методы
public_methods = ["async def start", "async def stop", "async def replace_stt_provider",
                   "async def replace_peer_stt_provider", "def clear_context", "def mark_promo_eligible"]
for method in public_methods:
    if method not in pipeline:
        print(f"FAIL: pipeline.py missing public method: {method}")

# wiring.py создаёт сервисы
if "TranslationService" not in wiring:
    print("FAIL: wiring.py doesn't create TranslationService")
if "OutputDispatcher" not in wiring:
    print("FAIL: wiring.py doesn't create OutputDispatcher")
```

## 11. Проверка circular imports

```python
import importlib, sys, pathlib

# Добавить src в path
sys.path.insert(0, str(pathlib.Path("app/src")))

modules = [
    "puripuly_heart.ports.llm",
    "puripuly_heart.ports.osc",
    "puripuly_heart.ports.overlay",
    "puripuly_heart.ports.secrets",
    "puripuly_heart.adapters.llm.openai_compatible",
    "puripuly_heart.adapters.osc.chatbox_paginator",
    "puripuly_heart.adapters.storage.secrets",
    "puripuly_heart.adapters.overlay.sink",
    "puripuly_heart.application.translation_service",
    "puripuly_heart.application.output_dispatcher",
    "puripuly_heart.core.pipeline.pipeline",
    "puripuly_heart.core.overlay.state",
]
for m in modules:
    try:
        importlib.import_module(m)
        print(f"OK: {m}")
    except Exception as e:
        print(f"FAIL: {m} -> {e}")
```

## 12. Проверка что ничего не потерялось

```python
import pathlib

SRC = pathlib.Path("app/src/puripuly_heart")

# i18n не затронут
i18n_dir = SRC / "data/i18n"
# Проверка через git: git diff --name-only app/src/puripuly_heart/data/i18n/

# controller.py импорт count
controller_imports = sum(1 for line in controller.splitlines() if "from puripuly_heart" in line)
print(f"INFO: controller.py has {controller_imports} puripuly_heart imports (compare with pre-refactor)")

# core/osc/receiver.py остался на месте (не перемещался)
if (SRC / "core/osc/receiver.py").exists():
    print("PASS: core/osc/receiver.py preserved (not part of stage 13)")
else:
    print("WARN: core/osc/receiver.py missing — was it moved?")

# core/llm/provider.py переэкспортирует LLMProvider из ports
provider_text = (SRC / "core/llm/provider.py").read_text(encoding="utf-8")
if "from puripuly_heart.ports.llm import LLMProvider" in provider_text:
    print("PASS: core/llm/provider.py re-exports LLMProvider from ports")
```

## Затронутые файлы

Этап не редактирует код. Только проверки.

## Формат результата

По каждой проверке: PASS или FAIL с указанием файла и строки. Если FAIL — описать что не так и в каком этапе это нужно исправить.
