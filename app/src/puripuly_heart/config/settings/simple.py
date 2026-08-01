from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class LLMSettings:
    concurrency_limit: int = 5

    def validate(self) -> None:
        if self.concurrency_limit <= 0:
            raise ValueError("concurrency_limit must be > 0")


@dataclass(slots=True)
class OSCSettings:
    host: str = "127.0.0.1"
    port: int = 9000
    chatbox_address: str = "/chatbox/input"
    chatbox_send: bool = True
    chatbox_clear: bool = False
    chatbox_max_chars: int = 144
    vrc_mic_intercept: bool = False
    chatbox_include_source: bool = False

    def validate(self) -> None:
        if not self.host:
            raise ValueError("host must be non-empty")
        if not (0 < self.port <= 65535):
            raise ValueError("port must be in 1..65535")
        if not self.chatbox_address or not self.chatbox_address.startswith("/"):
            raise ValueError("chatbox_address must start with '/'")
        if self.chatbox_max_chars <= 0:
            raise ValueError("chatbox_max_chars must be > 0")


@dataclass(slots=True)
class UiSettings:
    locale: str = "en"
    overlay_enabled: bool = False
    peer_translation_enabled: bool = False
    peer_translation_eula_accepted: bool = False
    integrated_context_enabled: bool = True
    integrated_context_bootstrapped: bool = False
    clipboard_auto_translate_enabled: bool = False

    def validate(self) -> None:
        if not self.locale:
            raise ValueError("locale must be non-empty")
        if not isinstance(self.clipboard_auto_translate_enabled, bool):
            raise ValueError("clipboard_auto_translate_enabled must be a bool")


@dataclass(slots=True)
class ApiKeyVerificationSettings:
    openai_compatible: bool = False

    def validate(self) -> None:
        pass
