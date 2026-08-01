# Этап 25: Верификация после hex-исправлений (stages 19-24)

## 1. Dependency direction

```python
import pathlib, re

SRC = pathlib.Path("app/src/puripuly_heart")
rules = {
    "ports/":    {"must_not_import_from": ["core.", "adapters."]},
    "core/":     {"must_not_import_from": ["adapters."]},
    "domain/":   {"must_not_import_from": ["core.", "adapters.", "ports."]},
    "application/": {"must_not_import_from": ["core.", "adapters.", "config."]},
}
import_re = re.compile(r"from\s+puripuly_heart\.(\S+)")
violations = []
for layer, rule in rules.items():
    layer_dir = SRC / layer.replace("/", "")
    if not layer_dir.exists():
        continue
    for f in layer_dir.rglob("*.py"):
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

## 2. Статическая компиляция

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

## 3. Проверка stage 19 (STTProviderName → domain/)

```python
import pathlib
SRC = pathlib.Path("app/src/puripuly_heart")

providers = (SRC / "domain/providers.py").read_text(encoding="utf-8")
assert "class STTProviderName" in providers, "FAIL: STTProviderName not in domain/providers.py"
assert "class LLMProviderName" in providers, "FAIL: LLMProviderName not in domain/providers.py"
print("PASS: domain/providers.py has enums")

peer = (SRC / "domain/peer_types.py").read_text(encoding="utf-8")
assert "from puripuly_heart.domain.providers import STTProviderName" in peer, "FAIL: peer_types.py wrong import"
print("PASS: peer_types.py imports from domain/providers")

enums = (SRC / "config/settings/enums.py").read_text(encoding="utf-8")
assert "from puripuly_heart.domain.providers" in enums, "FAIL: enums.py missing re-import"
print("PASS: enums.py re-imports from domain/")
```

## 4. Проверка stage 20 (OverlayEventAdapter → wiring)

```python
import pathlib
SRC = pathlib.Path("app/src/puripuly_heart")
pipeline = (SRC / "core/pipeline/pipeline.py").read_text(encoding="utf-8")

assert "from puripuly_heart.adapters.overlay.sink import OverlayEventAdapter" not in pipeline, \
    "FAIL: pipeline.py still imports OverlayEventAdapter"
print("PASS: pipeline.py no OverlayEventAdapter import")

assert "overlay_event_adapter: OverlayEventFactory = field(init=False)" not in pipeline, \
    "FAIL: field still init=False"
print("PASS: overlay_event_adapter is init parameter")

assert "OverlayEventAdapter(clock=self.clock)" not in pipeline, \
    "FAIL: still creating OverlayEventAdapter in pipeline"
print("PASS: OverlayEventAdapter not created in pipeline")

controller = (SRC / "ui/controller.py").read_text(encoding="utf-8")
headless = (SRC / "app/headless_mic.py").read_text(encoding="utf-8")
assert "OverlayEventAdapter" in controller, "FAIL: controller missing OverlayEventAdapter"
assert "OverlayEventAdapter" in headless, "FAIL: headless_mic missing OverlayEventAdapter"
print("PASS: wiring sites create OverlayEventAdapter")
```

## 5. Проверка stage 21 (SessionLogger Protocol)

```python
import pathlib
SRC = pathlib.Path("app/src/puripuly_heart")

logging_port = (SRC / "ports/logging.py").read_text(encoding="utf-8")
assert "class SessionLogger(Protocol)" in logging_port, "FAIL: SessionLogger not defined"
print("PASS: SessionLogger Protocol defined")

# ports/__init__.py re-export
ports_init = (SRC / "ports/__init__.py").read_text(encoding="utf-8")
assert "SessionLogger" in ports_init, "FAIL: ports/__init__.py missing SessionLogger"
print("PASS: ports/__init__.py re-exports SessionLogger")

# application и adapters импортируют из ports/logging (не из core.runtime_logging)
ts = (SRC / "application/translation_service.py").read_text(encoding="utf-8")
assert "from puripuly_heart.core.runtime_logging" not in ts, \
    "FAIL: translation_service still imports from core.runtime_logging"
print("PASS: translation_service imports from ports")

paginator = (SRC / "adapters/osc/chatbox_paginator.py").read_text(encoding="utf-8")
assert "from puripuly_heart.core.runtime_logging" not in paginator, \
    "FAIL: chatbox_paginator still imports from core.runtime_logging"
print("PASS: chatbox_paginator imports from ports")

openai = (SRC / "adapters/llm/openai_compatible.py").read_text(encoding="utf-8")
assert "from puripuly_heart.core.runtime_logging" not in openai, \
    "FAIL: openai_compatible still imports from core.runtime_logging"
print("PASS: openai_compatible imports from ports")

local_openai = (SRC / "adapters/llm/local_openai.py").read_text(encoding="utf-8")
assert "from puripuly_heart.core.runtime_logging" not in local_openai, \
    "FAIL: local_openai still imports from core.runtime_logging"
print("PASS: local_openai imports from ports")
```

## 6. Проверка stage 22 (config.prompts injection)

```python
import pathlib
SRC = pathlib.Path("app/src/puripuly_heart")

ts = (SRC / "application/translation_service.py").read_text(encoding="utf-8")
assert "from puripuly_heart.config.prompts" not in ts, \
    "FAIL: translation_service still imports from config.prompts"
print("PASS: translation_service no config.prompts import")
```

## 7. Проверка stage 23 (STT error types → domain/)

```python
import pathlib
SRC = pathlib.Path("app/src/puripuly_heart")

assert (SRC / "domain/stt_errors.py").exists(), "FAIL: domain/stt_errors.py missing"
print("PASS: domain/stt_errors.py exists")

controller = (SRC / "ui/controller.py").read_text(encoding="utf-8")
assert "from puripuly_heart.domain.stt_errors" in controller, \
    "FAIL: controller imports STT errors from adapters"
print("PASS: controller imports STT errors from domain/")
```

## 8. Проверка stage 24 (OSC → wiring)

```python
import pathlib
SRC = pathlib.Path("app/src/puripuly_heart")

controller = (SRC / "ui/controller.py").read_text(encoding="utf-8")
assert "from puripuly_heart.adapters.osc" not in controller, \
    "FAIL: controller still imports from adapters.osc"
print("PASS: controller no direct adapters.osc import")

wiring = (SRC / "app/wiring.py").read_text(encoding="utf-8")
assert "def create_osc_sink" in wiring, "FAIL: create_osc_sink not in wiring.py"
print("PASS: create_osc_sink in wiring.py")
```

## Затронутые файлы

Этап не редактирует код. Только проверки.
