# Translation providers

## Current selection

The primary and backup translation pickers currently expose the same choices:

- **Not selected** (or **Off** for the backup) leaves the corresponding channel unused.
- **OpenAI-compatible server** uses a provider preset or a custom base URL and model.
- **Anthropic-compatible providers** use the Anthropic Messages API with a custom base URL and model.
- **Own server** uses a custom base URL, model, and additional JSON request-body fields.

Each form has three independently saved configuration slots. Primary and backup
settings are separate. When the backup is enabled and configured, it is used
sequentially after the primary provider fails or exceeds the primary wait time.

The Custom HTTP backend exists in the runtime, but is not currently offered by
these two pickers.

After a provider is selected, its card shows a gear button and quick-switch
buttons `1–3`. The gear opens the settings window owned by that specific card
and provider variant; it does not select a provider. There are six independent
window instances: OpenAI-compatible, Own server, and Anthropic for each of the
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
| Own server | Direct `httpx` client | Non-streaming OpenAI Chat Completions with extra body fields |

The OpenAI-compatible variant supports provider presets, custom endpoints,
model discovery, and connection checks. The Anthropic runtime adapter also
supports custom endpoints and SDK model discovery. An API key is optional for
local servers that do not require one.

"Own server" currently means an OpenAI-compatible HTTP endpoint. It does not
yet provide separate native adapters for the KoboldCpp, llama.cpp,
text-generation-webui, or TabbyAPI text-completion protocols. The default
address points to Ollama's OpenAI-compatible API rather than its native API.

## Evaluated expansion candidate

[LLM-lib](https://github.com/Tejasvedagiri/LLM-lib) was evaluated as a possible
shared client for broader own-server connectivity. It covers KoboldCpp,
llama.cpp, Ollama, text-generation-webui, and TabbyAPI, and also has presets
for vLLM, SGLang, LM Studio, mlx-lm, LocalAI, Jan, and colibri.

Ollama uses its native `/api/chat` protocol. The other named servers use their
OpenAI-compatible interfaces. Generic OpenAI-compatible configuration can
cover additional servers, including Aphrodite, when that interface is enabled.

LLM-lib is **not integrated** and is not a project dependency. Evaluation used
commit `9784a0805c8bd6776b78a335e18237212d01368c` on Python 3.14.7: its 101
tests and Ruff checks passed, and loopback protocol checks succeeded for all
five primary server types. It requires `httpx2>=2.13.1` and is licensed under
Apache-2.0. The project is very new, has no PyPI release, and would require a
commit-pinned Git dependency and further acceptance testing before integration.
