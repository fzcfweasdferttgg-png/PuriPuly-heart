# PuriPuly Heart — Portable Core

PuriPuly Heart is an LLM-based real-time translator for VRChat. This repository
contains the Windows portable source distribution, including the application,
its Python runtime, native ASR components, tests, and build scripts.

The portable folder is self-contained and relocatable. Running the application
does not require a system Python installation or changes to system settings.

## Launch

Run `start.exe` and choose one of the following actions:

- **Launch** starts the application without an additional console window.
- **Launch with debug** starts it with the Python console and debug UI controls.
- **Maintenance** clears user data, Python bytecode, or regenerable caches
  without starting the application.

The application runs directly from `src/` using `runtime/python/`. To start it
from a console instead, run this command from the project root:

```bat
runtime\python\python.exe -m puripuly_heart.main
```

For launcher details, maintenance behavior, package management, and rebuild
instructions, see the [portable guide](docs/portable.md).

## Project layout

| Path | Purpose |
| --- | --- |
| `src/` | Application source code |
| `runtime/python/` | Embedded Python and application dependencies |
| `runtime/.venv/` | Optional development and test environment; not committed |
| `native/` | Native component sources |
| `native/build/` | ASR worker, transcribe.cpp, and ggml runtime files |
| `native/third_party/` | Vendored third-party resources and license materials |
| `docs/` | Portable guides, feature logs, and runtime prompt templates |
| `scripts/` | Build and CI scripts |
| `tests/` | Automated tests |
| `var/data_user/` | Settings, models, logs, and secrets; not committed |
| `var/cache/` | Regenerable caches and temporary files; not committed |
| `start.cs` | Portable launcher source |
| `start.exe` | Compiled portable launcher |

## Documentation

- [Portable guide — English](docs/portable.md)
- [Portable guide — Russian](docs/portable-ru.md)
- [Portable guide — Korean](docs/portable-ko.md)

Prompt templates used by the application are located in `docs/prompts/`.

## Development

The project requires Python 3.14 and uses `pyproject.toml` and `uv.lock` for
dependency management. The development environment is intentionally excluded
from Git. To create it inside the portable folder with `uv`, use PowerShell:

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$PWD/runtime/.venv"
uv sync --extra dev
```

The environment variable applies only to the current shell.

Run the automated tests and style checks from the project root:

```bat
runtime\.venv\Scripts\python.exe -m pytest
runtime\.venv\Scripts\python.exe -m ruff check src tests
runtime\.venv\Scripts\python.exe -m black --check src tests
```

See `scripts/` and `native/` for native build tooling. Launcher rebuild
instructions are included in the [portable guide](docs/portable.md#launcher-rebuild).

## User data and secrets

Settings, downloaded models, logs, API keys, and other user data are stored in
`var/data_user/`. Secrets are stored in plain text by design. Do not publish
this directory or include it in archives, commits, logs, or bug reports.

`var/cache/` contains only regenerable data and can be removed when the
application is closed. Both directories are excluded by `.gitignore`.

## TODO

This section tracks differences and remaining work. The comparison baseline is
the original author's `kap/dev@4c3324d4a` snapshot from 2026-10-31. It was a
development tree marked as version 2.10.0, not the author's official 2.10.0 release.

### Added

- A portable launcher and embedded Python runtime, with user data in
  `var/data_user/` and regenerable files in `var/cache/`.
- CUDA support for local speech recognition.
- A generic OpenAI-compatible translation provider with editable presets for
  local or hosted endpoints, plus independent primary and backup configuration.
- Explicit not-selected states for speech recognition and translation providers.
- Portable documentation in English, Russian, and Korean.

### Removed

- The broker service, its deployment files and migrations, and app-active-day telemetry.
- Dedicated cloud STT adapters and the Sherpa/ONNX recognition path.
- Dedicated LLM adapters for Gemini, OpenAI, OpenRouter, DeepSeek, Qwen,
  ChatGPT Plan, and index-translate.
- Account and managed-account cards in the settings UI.
- Inno Setup scripts and the author's GitHub workflows.
- Fernet and keyring secret-store implementations.

### Changed

- The bundled ASR data includes twelve user manifests with SHA-256-pinned GGUF
  files and a 77-entry transcribe.cpp catalog, with VAD hidden in the GUI,
  instead of the author's five manifests. The model manager also supports local
  GGUF imports and GGUF downloads from Hugging Face and ModelScope, with
  role/language filters, quantization selection, and install lifecycle handling.
- Model and GPU selection was reworked, and model management moved to a separate
  window with search, local inventory, and Hugging Face or ModelScope sources.
- Local and hosted translation endpoints are configured through the generic
  OpenAI-compatible provider instead of dedicated hosted adapters.
- Managed Gemma remains in the code but is not a selectable translation model.
- OpenRouter and managed-identity modules, native packaging and release-evidence
  checks remain in the tree. The GitHub Star prompt remains active.

### Work plan

- [ ] Stabilize and refine the model manager, OpenAI-compatible server, and local server concepts.
- [ ] Add native Anthropic Messages API support, provider addresses, and required dependencies.
- [ ] Add configurable workload-to-backend assignment for self and peer speech recognition, allowing each channel to run on CPU, Vulkan, or CUDA and either share one model instance or use separate model instances.
- [ ] ... ???

## License

PuriPuly Heart is distributed under the GNU Affero General Public License,
version 3 or later. See [LICENSE](LICENSE) and the
[third-party notices](src/puripuly_heart/data/THIRD_PARTY_NOTICES.txt).
