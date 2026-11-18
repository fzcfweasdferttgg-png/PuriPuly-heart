# Translation providers

## Current selection

The primary and backup translation pickers currently expose the same choices:

- **Not selected** (or **Off** for the backup) leaves the corresponding channel unused.
- **OpenAI-compatible server** uses a provider preset or a custom base URL and model.
- **Anthropic-compatible providers** use the Anthropic Messages API with a custom base URL and model.
- **Other servers** uses a local-server preset or custom base URL, a model, and additional JSON request-body fields.

Each form has three independently saved configuration slots. Primary and backup
settings are separate. When the backup is enabled and configured, it is used
sequentially after the primary provider fails or exceeds the primary wait time.

The Custom HTTP backend exists in the runtime, but is not currently offered by
these two pickers.

After a provider is selected, its card shows a gear button and quick-switch
buttons `1–3`. The gear opens the settings window owned by that specific card
and provider variant; it does not select a provider. There are six independent
window instances: OpenAI-compatible, Other servers, and Anthropic for each of the
primary and backup cards. Opening another variant of the same card closes its
previous window without replacing that window's mounted content. Primary and
backup windows remain independent. Each settings window repeats the three slot
buttons and has its own Save all button. Quick-switching applies the whole saved
slot, including its variant. Selecting Anthropic from either picker activates
its form, gear, slots, and dedicated window in the same way as the other variants.

## Transports

| Variant | Client | Request protocol |
| --- | --- | --- |
| OpenAI-compatible server | Official `openai` SDK | Non-streaming OpenAI Chat Completions |
| Anthropic-compatible providers | Official `anthropic` SDK | Non-streaming Anthropic Messages API |
| Other servers | LLM-lib | Server-specific streaming protocol with completed-text output |

The OpenAI-compatible variant supports provider presets, custom endpoints,
model discovery, and connection checks. The Anthropic runtime adapter also
supports custom endpoints and SDK model discovery. An API key is optional for
local servers that do not require one.

"Other servers" is the only variant that uses LLM-lib. Ollama uses its native
`/api/chat` NDJSON protocol and `/api/tags` discovery. The other bundled local
servers use LLM-lib's OpenAI Chat Completions transport, not the official
OpenAI SDK. This does not add separate native adapters for the KoboldCpp,
llama.cpp, text-generation-webui, or TabbyAPI text-completion protocols.
Requests stream internally and the adapter returns the completed translation.
The default model is empty. Backend capabilities control whether it may stay
empty: Ollama requires an explicit model, while the other bundled backends let
LLM-lib resolve a single offered model or omit the field. The GUI shows the
corresponding model hint and validation. colibri penalty fields are rejected in
the form and omitted at the runtime boundary.

## Provider registry

The OpenAI-compatible dropdown has eight bundled presets: OpenAI, OpenRouter,
Google Gemini, DeepSeek, Qwen, Groq, Mistral AI, and Together AI. OpenAI and its
official `https://api.openai.com/v1` endpoint are the default. The user copy of
`providers.json` remains authoritative and is not destructively migrated.
Primary and backup forms provide add, edit, and delete buttons next to the
provider dropdown. Entries have editable custom names and base URLs; the
registry accepts at most 50 records. Deleting a selected entry moves the form to
the first remaining preset, or clears the selection when none remain.

Anthropic-compatible primary and backup forms use the separate
`anthropic_providers.json` registry with six bundled presets: Anthropic,
OpenRouter, DeepSeek, Moonshot AI, Zhipu AI, and MiniMax. Anthropic and its
official `https://api.anthropic.com` endpoint are the default. It has the same
CRUD controls and 50-entry limit, while its `provider_key` is persisted in
settings, provider slots, runtime signatures, and provider construction.
Deleting a selected preset applies the same fallback behavior.

Other-server primary and backup forms use the independent
`local_providers.json` registry with twelve bundled presets: KoboldCpp, colibri,
LM Studio, llama.cpp, vLLM, SGLang, mlx-lm, text-generation-webui, TabbyAPI,
LocalAI, Jan, and Ollama. KoboldCpp, its `koboldcpp` backend, and
`http://127.0.0.1:5001/v1` are the default. Their addresses and backend
identifiers follow LLM-lib's local-server presets. Both forms provide the same add/edit/delete
workflow, custom names, URL validation, and 50-entry limit. Custom entries also
allow selecting the LLM-lib backend, including native Ollama or generic
OpenAI-compatible transport. The selected `provider_key` and `backend` are
persisted in settings, slots, runtime signatures, and provider construction.
Changing the selected server, backend, or URL resets the primary model to its
built-in default and clears the backup model. A non-empty model is required
only for backends that require one (Ollama); for the other bundled backends an
empty primary or backup model is applied and resolved by LLM-lib at request
time. Slot restoration preserves the
slot's saved model and backend. Renaming a preset or reselecting it without
changing its backend or URL preserves the model. Across all three registries,
the Base URL field in the settings form is read-only; custom addresses are
changed only through Add/Edit server. Opening a form resolves an empty saved URL
from its selected preset without writing that display value back by itself.
Selecting a profile inside a form refreshes its controls immediately and stages
the values for Save all; quick-switching from a settings card applies the slot
immediately. A restored address that no longer matches the selected preset
clears the preset selection instead of displaying a misleading server name. Legacy Ollama addresses ending in `/v1`
are normalized to the native API root by the runtime adapter. Cross-variant
Accept switches only after an explicit slot pick. Invalid `extra_body` JSON is
rejected without saving or applying it. First-use registry creation and later
writes use process-wide locking and unique temporary files. Key-only apply
batches fill the preset URL and local backend only for empty or other preset
addresses, while a custom address is preserved.

## Integrated dependency

[LLM-lib](https://github.com/Tejasvedagiri/LLM-lib) is pinned to commit
`9784a0805c8bd6776b78a335e18237212d01368c` as a direct Git dependency in
`pyproject.toml` and `uv.lock`. It requires `httpx2>=2.13.1` and is licensed
under Apache-2.0. It is used only by the Other servers runtime adapter and its
model discovery. The OpenAI and Anthropic variants continue to use their
official SDKs. Only explicitly saved API keys are sent; environment and preset
placeholder keys are not used. Closing a provider cancels active translations,
and cancelled discovery closes its client only after the worker finishes.

The OpenAI-compatible label in LLM-lib describes only the wire protocols it
implements. It does not imply compatibility with every official OpenAI API
method or with the official OpenAI SDK. Generic OpenAI-compatible configuration
can cover additional servers, including Aphrodite, when that interface is
enabled. Live per-server acceptance testing remains necessary rather than
relying on that label. Project loopback tests cover native Ollama NDJSON and
OpenAI SSE requests, Ollama `/api/tags` discovery, extra-body mapping, and
provider delegation.
