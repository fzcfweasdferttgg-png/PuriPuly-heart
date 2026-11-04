# Deleted functionality

Functionality deleted from the portable version relative to the author's
original, with the package dependencies that left with it.

## Managed control-plane (broker)

Broker service (Cloudflare Worker), its deploy, migrations and tests.
Dependencies: none (the Node/pnpm toolchain left together with it).

## Installer and packaging

Inno Setup installer, installer model provisioning, build spec.
Dependencies: none.

## Build and CI tooling

GitHub CI, scripts, pnpm manifests, tsconfig, vitest, Rust toolchain config.
Dependencies: none.

## Author documentation

AGENTS.md, CODE_SIGNING.md, PRIVACY.md, README.md.
Dependencies: none.

## External package caches

pip and uv caches outside the program folder.
Dependencies: none.

## Cloud speech recognition

Deepgram, Gemini Transcribe and ElevenLabs Scribe STT providers with their
API-key verification, language mapping and the rolling free-tier rotation
between them (provider adapter modules removed).
Dependencies: deepgram-sdk, elevenlabs, google-genai, google-auth, pyasn1,
pyasn1-modules, distro, tenacity.

## Cloud translation provider (Gemini)

Gemini LLM provider with its API-key verification (provider adapter module
removed).
Dependencies: google-genai (also removed with cloud speech recognition).

## Secret encryption

Fernet-encrypted secret store, Windows Credential Manager (keyring) store and
RSA ID-token verification for the account sign-in; secrets are stored in plain
text now.
Dependencies: cryptography, keyring, pywin32-ctypes, jaraco.classes,
jaraco.context, jaraco.functools, more-itertools.

## Cloud translation providers

DeepSeek, Qwen (DashScope), OpenAI, OpenRouter, the ChatGPT plan backend and
the public index-translate API: provider adapter modules removed. Translation
works through the local OpenAI-compatible endpoint or the local Gemma runtime.
Dependencies: none (code only).

## Paid cloud speech recognition

Soniox realtime, Qwen Audio (DashScope) and the custom STT connection with
their API-key verification. The local CPU providers are deleted as well;
recognition stays on the single local Qwen3 ASR provider (transcribe.cpp).
Dependencies: none (code only).

## Account logins and managed keys

QQ login, Discord OAuth, ChatGPT OAuth and OpenRouter PKCE OAuth together with
the managed key delivery through the author's broker and the managed identity
signing; the account and login cards were removed from the Settings interface.
Dependencies: none (code only).

## App-active-day telemetry

Daily activity ping to the broker and the legacy translation-success date
tracking in the settings migration.
Dependencies: none (code only).

## Local ASR model catalog (author's set)

The author's ASR models (orukeet, fun-asr nano/mlt, japanese parakeet sherpa,
qwen3-asr-1.7b Q6_K) and their sha256-pinned manifests replaced by the user's
GGUF catalog with simplified manifests.
Dependencies: none (code only).

## Sherpa/ONNX recognition path

The sherpa-onnx recognition backend (japanese parakeet), its Windows runtime
bootstrap and the runtime check command.
Dependencies: sherpa-onnx, sherpa-onnx-core.

## Settings interface cleanup

Cards and controls of the removed features deleted from the Settings
interface: the ChatGPT account card, the managed key card with the trial
usage bar, the custom STT connection card, the API Keys section with its ten
cloud key fields, the Qwen region and API-host controls, the telemetry toggle
and the OpenRouter PKCE button. The remaining cards (Local LLM, GPU devices,
ASR model, HTTP extension) appear only when their setting is selected.
Dependencies: none (interface only).

## Promotional surfaces

The GitHub star snackbar and its after-launch prompt, the founder letter
dialog with the warm document dialog triggers, the API guide button and the
translation-success promotion hook in the event pipeline.
Dependencies: none (interface only).
