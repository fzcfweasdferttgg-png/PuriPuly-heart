# Added functionality

Functionality added to the portable version relative to the author's
original, with new package dependencies.

## Portable launcher

`start.exe` and its source: normal start, debug start, maintenance (clear
user data, clear runtime temp files, clear cache), 35 languages, hover
tooltips, exit-code dialog.
Dependencies: none.

## Portable Python runtime

`python/`: CPython 3.14.7 with all program dependencies installed inside,
pip package manager, `python/INSTALLED_PACKAGES.txt` manifest.
Dependencies: bundled set of 62 packages.

## User data folder

`data_user/`: settings, models, secrets and logs stored inside the program
folder.
Dependencies: none.

## Cache folder

`cache/`: regenerable caches and temporary files kept inside the program
folder (pip, uv, Hugging Face, client home, temp).
Dependencies: none.

## Documentation set

`portable.md` with language versions; this log and the deleted log in three
languages.
Dependencies: none.

## Local ASR model catalog (user's set)

Twelve transcribe.cpp Vulkan GGUF models (gigaam-v3, parakeet-tdt-0.6b-v3,
qwen3-asr-0.6b, qwen3-asr-1.7b; q8_0/q6_k/f16) from the user's Hugging Face
and ModelScope repositories. Simplified v2 manifests describe sources and
files with size + CRC32, verified at install time only.
Dependencies: modelscope, modelscope-hub.

## Local model selection

The active local recognition model (including the quantization variant) is
chosen in the Settings model picker: all twelve catalog models in every
quantization plus a not-selected item; every catalog model is installable on
demand.
Dependencies: none.

## Not-selected settings state

Every settings choice that can be empty has a separate not-selected item (or
its local equivalent) and defaults to it after a fresh load. While a channel
setting (speech recognition, peer recognition, translation, ASR model) is not
selected, the channel is not ready: its dashboard button is disabled, pressing
it does nothing and no error or prompt appears. The OSC mirror publishes the
not-selected state as id 255.
Dependencies: none.

## ASR device selection

The "ASR device" setting chooses the video card that runs the recognition
model, or CPU; CPU activates the transcribe.cpp CPU backend for the self and
the peer channel alike. The same device list is offered for the translation
runtime.
Dependencies: none.
