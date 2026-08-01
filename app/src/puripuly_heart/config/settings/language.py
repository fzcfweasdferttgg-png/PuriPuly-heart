from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class LanguageSettings:
    source_language: str = "ko"
    target_language: str = "en"
    second_target_language: str = ""
    peer_source_language: str = "en"
    peer_target_language: str = "ko"
    recent_source_languages: list[str] = field(default_factory=lambda: ["en", "zh-CN", "ja"])
    recent_target_languages: list[str] = field(default_factory=lambda: ["en", "zh-CN", "ja"])

    def validate(self) -> None:
        if not self.source_language:
            raise ValueError("source_language must be non-empty")
        if not self.target_language:
            raise ValueError("target_language must be non-empty")

    @property
    def effective_peer_source(self) -> str:
        return self.peer_source_language or self.source_language

    @property
    def effective_peer_target(self) -> str:
        return self.peer_target_language or self.target_language
